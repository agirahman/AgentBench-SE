"""Detect symlinks and junctions (reparse points) inside repo checkouts.

Why this exists
---------------
`git status --porcelain` is the wrong tool for this job. Measured on 2026-10-06:

    zz_hidden.pyc   landed=True  git_status_visible=False   <-- gate BLIND
    zz_build        landed=True  git_status_visible=True
    zz_visible.txt  landed=True  git_status_visible=True

The requests checkout's .gitignore has 21 patterns including `*.pyc` and `build/`,
so a container-created link named `x.pyc` (or placed in `build/`) is invisible to
git status: preflight reports "pristine" and the sweep runs with an active leak
vector. The scan below is independent of git and of ignore rules.

Why reparse attributes rather than `Path.is_symlink()`
------------------------------------------------------
A symlink created inside a Linux container lands on the Windows host as a
junction. For those, `Path.is_symlink()` returns **False** and `Path.exists()`
raises ``OSError WinError 1920`` — both portable APIs misreport it. The reliable
signal is ``os.lstat().st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT``
(measured: 1056 = 0x420 for a junction, 32 = 0x20 for a normal file).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

#: Windows file attribute marking a reparse point (symlink OR junction).
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_reparse_point(path: Path) -> bool:
    """True when `path` is a symlink or junction. Never follows the link."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def find_reparse_points(root: Path, max_depth: int = 40) -> list[Path]:
    """Return every symlink/junction under `root`, without following any of them.

    Uses os.walk(followlinks=False) and lstat, so the scan cannot itself be
    redirected out of the checkout by a malicious link.
    """
    found: list[Path] = []
    if not root.is_dir():
        return found

    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        if len(current.parts) - root_depth > max_depth:
            dirnames[:] = []
            continue

        # A symlinked DIRECTORY appears in dirnames; check and prune it so the
        # walk does not descend through it.
        keep: list[str] = []
        for name in dirnames:
            candidate = current / name
            if is_reparse_point(candidate):
                found.append(candidate)
            else:
                keep.append(name)
        dirnames[:] = keep

        for name in filenames:
            candidate = current / name
            if is_reparse_point(candidate):
                found.append(candidate)

    return found


def describe(path: Path, root: Path) -> str:
    """One-line description with a host-safe relative path."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    try:
        target = os.readlink(path)
    except OSError:
        target = "<junction; target not readable from the host>"
    return f"{rel} -> {target}"


def cleanup_instructions(root: Path, found: list[Path]) -> str:
    """Concrete, copy-pasteable removal commands for the links that were found."""
    lines = [
        "These are symlinks/junctions left inside the checkout (a container run can",
        "create them, because /work is bind-mounted read-write). Remove them with:",
        "",
        "    powershell -Command \"Remove-Item -LiteralPath '<path>' -Force\"",
        "",
        "or, for every one at once:",
        "",
        "    python -c \"import os,stat,pathlib,sys;"
        "root=pathlib.Path(r'" + str(root) + "');"
        "[os.unlink(p) for p in root.rglob('*') if p.is_symlink()]\"",
        "",
        "Paths found:",
    ]
    for p in found:
        lines.append(f"  - {p}")
    return "\n".join(lines)
