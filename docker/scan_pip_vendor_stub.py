#!/usr/bin/env python3
"""Refuse to ship a pip-vendored copy of the requests fix (oracle scan).

WHY THIS FILE EXISTS
====================
pip vendors a full copy of `requests`. A site-packages tree is readable source,
so for any SWE-bench instance whose base commit is an OLDER requests, the
vendored copy carries the fix -- measured on psf__requests-1963 (base commit
2.3.0, vendored 2.28.2): the 2-line gold fix is 100% readable from
`pip/_vendor/requests/sessions.py` and 0% from the checkout.

The fix that closed it replaces that directory with a SOURCE-FREE stub
(docker/pip_vendor_stub/). The stub is what keeps the oracle closed, so its
content is load-bearing and must be checked by something other than trust:

  * the build runs this script (docker/sandbox.Dockerfile), so an image that
    would carry the oracle cannot be produced; and
  * tests/test_docker_sandbox.py runs the SAME script against the stub source
    on the host, so a bad edit fails the suite even without a Docker daemon.

Both call sites share this one implementation on purpose: a gate whose logic is
only asserted by string-matching the Dockerfile is not pinned (a mutation that
empties the check leaves the string intact). Running the real logic is.

WHY IT ALSO OPENS ARCHIVES
==========================
A wheel is a ZIP: its source is readable with `zipfile`, no install, no network
and no checkout. The base image ships a complete pip wheel through ensurepip --

    /usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl

-- and measured, it carried the SAME oracle at
`pip/_vendor/requests/sessions.py:259`. Stripping site-packages alone therefore
did NOT close the finding; the same fix stayed readable one directory over. This
scanner reads .whl/.zip/.egg entries too, so that class is covered rather than
that one file.

WHY A PAIR, NOT A SINGLE LINE
=============================
The gold fix is a comment plus one assignment. Matching either line alone would
false-positive on legitimate stub prose: the stub's own docstring explains the
oracle and may NAME the phrase it is describing. The check is therefore a
CONTIGUITY test -- a line containing the comment phrase, with a line containing
the assignment phrase within NEAR lines of it.

Usage:
    python scan_pip_vendor_stub.py PATH [PATH ...]

PATH may be a directory (scanned recursively for .py), an archive (detected by
ZIP magic bytes, so a renamed wheel is still opened), or a single .py file. Exit
code 0 = clean; 1 = the oracle pair is present, or a path could not be read at
all (which would make the check vacuous).
"""
from __future__ import annotations

import pathlib
import sys
import zipfile

#: The gold fix for psf__requests-1963. Kept here as two fragments of the same
#: fix; this scanner is NOT inside the scanned trees, so naming them is safe.
ORACLE_COMMENT = "Override the original request"
ORACLE_ASSIGNMENT = "req = prepared_request"

#: How close the two lines must be. The real fix is adjacent (comment directly
#: above the assignment); 4 lines tolerates a blank line or a reflow without
#: letting an unrelated mention elsewhere in a file count as a pair.
NEAR = 4

DEFAULT_ROOT = "/usr/local/lib/python3.9/site-packages/pip/_vendor/requests"


def find_oracle_pairs_in_text(text: str, label: str) -> list[str]:
    """Return ``label:line`` for each place the fix appears as a contiguous pair."""
    hits: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if ORACLE_COMMENT not in line:
            continue
        if any(ORACLE_ASSIGNMENT in n for n in lines[i:i + NEAR]):
            hits.append(f"{label}:{i + 1}")
            break
    return hits


def scan_directory(root: pathlib.Path) -> tuple[list[str], int]:
    """Scan every .py under ``root``. Returns (hits, files_scanned)."""
    hits: list[str] = []
    files = sorted(root.rglob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += find_oracle_pairs_in_text(text, str(path))
    return hits, len(files)


def scan_archive(path: pathlib.Path) -> tuple[list[str], int]:
    """Scan readable source entries inside an archive. Returns (hits, entries)."""
    hits: list[str] = []
    scanned = 0
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.endswith((".py", ".pyi")):
                    continue
                try:
                    text = z.read(name).decode("utf-8", "replace")
                except Exception:
                    continue
                scanned += 1
                hits += find_oracle_pairs_in_text(text, f"{path}!{name}")
    except zipfile.BadZipFile:
        # Not a ZIP after all: nothing readable, so nothing can leak. Zero
        # scanned lets the caller's vacuity check decide.
        return [], 0
    return hits, scanned


def looks_like_zip(path: pathlib.Path) -> bool:
    """True when the file starts with the ZIP magic number.

    Detection is by MAGIC BYTES, not by extension: a wheel is a ZIP whatever it is
    called, and a renamed one would otherwise be scanned as text -- where the
    deflated contents are unreadable, so the check would report "clean" while the
    oracle sat inside. Measured: with extension-only dispatch, emptying
    ARCHIVE_SUFFIXES made an oracle-bearing wheel pass.
    """
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def looks_like_text(path: pathlib.Path) -> bool:
    """True when the file decodes as text, i.e. it is safe to scan line by line.

    A NUL byte means binary. Without this check a binary file falls through to the
    text branch, where reading it with ``errors="replace"`` yields garbage that
    cannot contain the pair -- so the scan reports "clean" for a file it never
    actually read. That is the same silent false negative the archive branch
    exists to prevent, and it is why the caller refuses (rather than passes) a
    file that is neither a ZIP nor text.
    """
    try:
        blob = path.read_bytes()
    except OSError:
        return False
    if b"\x00" in blob:
        return False
    try:
        blob.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def main(argv: list[str]) -> int:
    targets = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path(DEFAULT_ROOT)]

    all_hits: list[str] = []
    total_scanned = 0
    for target in targets:
        if target.is_dir():
            hits, n = scan_directory(target)
        elif target.is_file() and looks_like_zip(target):
            hits, n = scan_archive(target)
        elif target.is_file() and looks_like_text(target):
            text = target.read_text(encoding="utf-8", errors="replace")
            hits, n = find_oracle_pairs_in_text(text, str(target)), 1
        elif target.is_file():
            # Fail CLOSED. A file that is neither a readable ZIP nor text cannot be
            # checked, and reporting it clean would be a claim this scanner did not
            # earn -- exactly how an oracle survives a scan.
            print(
                f"FATAL: cannot scan {target}: not a ZIP archive and not text, so "
                f"its contents cannot be checked for the oracle",
                file=sys.stderr,
            )
            return 1
        else:
            print(f"FATAL: not found: {target}", file=sys.stderr)
            return 1
        all_hits += hits
        total_scanned += n

    if total_scanned == 0:
        # An empty scan is a vacuous pass: the gate would be satisfied by a tree
        # or archive that is not there, which is how the oracle would come back.
        print(
            "FATAL: nothing readable was scanned in "
            + ", ".join(str(t) for t in targets),
            file=sys.stderr,
        )
        return 1

    if all_hits:
        print(
            "FATAL: the pip-vendored requests oracle is present: "
            + ", ".join(all_hits)
            + " -- the stub must stay source-free (see docker/pip_vendor_stub/)",
            file=sys.stderr,
        )
        return 1

    print(f"vendored oracle scan clean: {total_scanned} files, no oracle pair")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
