"""Cross-platform clipboard copy with graceful fallbacks (SDD §8.6).

Strategy (first success wins):

1. :mod:`pyperclip` when installed (uses the platform-native mechanism).
2. Platform helper binaries: ``xclip`` / ``xsel`` (Linux), ``pbcopy``
   (macOS), ``clip.exe`` (Windows).
3. Last resort: a temp file ``~/.agentbench_clipboard.txt`` so copying
   still works on headless boxes (WSL without X, CI) — the UI toasts the
   path so the user can still retrieve the text.

``copy_text`` returns the *method* that succeeded (``"pyperclip"``,
``"xclip"``, ``"xsel"``, ``"pbcopy"``, ``"clip"``, ``"tempfile"``) so
screens can confirm what happened; ``"tempfile"`` includes the path in
the returned tuple. Tests monkeypatch :func:`_backends` to force a path.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Last-resort fallback file (kept in the user home, not the repo).
TEMP_CLIPBOARD = Path.home() / ".agentbench_clipboard.txt"


def _backends() -> list[tuple[str, list[str], bool]]:
    """Candidate copy commands as ``(name, argv-prefix, stdin-bool)``.

    The final element is ``True`` when the text is piped to the command's
    stdin, ``False`` when it is passed as the last argument.
    """
    if sys.platform == "win32":
        return [("clip", ["clip.exe"], True)]
    if sys.platform == "darwin":
        return [("pbcopy", ["pbcopy"], True)]
    # Linux / WSL: try xclip then xsel.
    candidates: list[tuple[str, list[str], bool]] = []
    if shutil.which("xclip"):
        candidates.append(("xclip", ["xclip", "-selection", "clipboard"], True))
    if shutil.which("xsel"):
        candidates.append(("xsel", ["xsel", "--clipboard", "--input"], True))
    # WSL interop: clip.exe lives on the Windows side.
    win_clip = Path("/mnt/c/Windows/System32/clip.exe")
    if win_clip.exists():
        candidates.append(("clip", [str(win_clip)], True))
    return candidates


def copy_text(text: str) -> tuple[str, str | None]:
    """Copy ``text`` to the system clipboard.

    Returns ``(method, extra)`` where ``extra`` is a human-readable
    confirmation detail — e.g. ``("tempfile", "/home/u/.agentbench_clipboard.txt")``
    or ``("pyperclip", None)``.
    """
    text = str(text)

    # 1) pyperclip
    try:
        import pyperclip  # noqa: PLC0415

        pyperclip.copy(text)
        return ("pyperclip", None)
    except Exception:  # noqa: BLE001
        pass

    # 2) platform helpers
    for name, argv, via_stdin in _backends():
        try:
            if via_stdin:
                subprocess.run(argv, input=text.encode("utf-8"), check=True, timeout=5)  # noqa: S603
            else:
                subprocess.run([*argv, text], check=True, timeout=5)  # noqa: S603
            return (name, None)
        except Exception:  # noqa: BLE001
            continue

    # 3) temp file fallback
    TEMP_CLIPBOARD.write_text(text, encoding="utf-8")
    return ("tempfile", str(TEMP_CLIPBOARD))


def read_clipboard() -> str:
    """Best-effort paste for tests: try pyperclip, then the temp file."""
    try:
        import pyperclip  # noqa: PLC0415

        return pyperclip.paste()
    except Exception:  # noqa: BLE001
        pass
    if TEMP_CLIPBOARD.exists():
        return TEMP_CLIPBOARD.read_text(encoding="utf-8")
    return ""
