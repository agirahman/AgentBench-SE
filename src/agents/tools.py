"""Active repository tools for tool-calling agents.

These replace the passive SOURCE_CONTEXT injection: instead of feeding a fixed
snapshot of files into the prompt, tool-calling agents (commandcode provider)
can actively explore the repository. All tools are sandboxed to TOOLCALL_REPO_DIR
so they cannot read or execute outside the repo cache.

Tools provided:
  * read_file   - read a file (bounded line range)
  * grep        - search file contents (ripgrep-style, via python)
  * list_files  - list files under a directory
  * run_tests   - run a test command inside the repo (sandboxed, capped)

Each tool returns a plain string the model can read.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config import Config

# Current repo root for the active issue (set per strategy run). When None,
# tools fall back to the global TOOLCALL_REPO_DIR (still sandboxed).
_CURRENT_REPO_ROOT: Path | None = None


def set_repo_root(path: str | Path | None) -> None:
    """Set the active instance's repo root (e.g. datasets/repos/psf/requests/<hash>)."""
    global _CURRENT_REPO_ROOT
    if path is None:
        _CURRENT_REPO_ROOT = None
        return
    p = Path(path)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    _CURRENT_REPO_ROOT = p.resolve()


def resolve_repo_root(repo: str, base_commit: str) -> Path | None:
    """Find the on-disk repo root for an instance.

    Layouts observed in datasets/repos:
      * psf/requests  -> datasets/repos/psf/requests/<hash>
      * django/django -> datasets/repos/django/<hash>
    Heuristic, then recursive fallback to a folder named <base_commit>.
    Returns None if not found.
    """
    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = Path(os.getcwd()) / base
    owner, _, name = repo.partition("/")

    candidates = [
        base / owner / name / base_commit,
        base / owner / base_commit,
    ]
    for c in candidates:
        if c.is_dir():
            return c.resolve()
    # Recursive fallback: find a directory named exactly base_commit.
    try:
        for root, dirs, _files in os.walk(base / owner):
            if base_commit in dirs:
                return (Path(root) / base_commit).resolve()
    except Exception:  # noqa: BLE001
        pass
    return None


def _repo_root() -> Path:
    """Resolve the sandbox root for tool operations.

    Prefers the active instance repo root (set per run) so agents explore the
    correct checked-out repo; falls back to the global TOOLCALL_REPO_DIR.
    """
    if _CURRENT_REPO_ROOT is not None:
        return _CURRENT_REPO_ROOT
    root = Path(Config.TOOLCALL_REPO_DIR)
    if not root.is_absolute():
        root = Path(os.getcwd()) / root
    return root.resolve()


def _safe_path(path: str) -> Path:
    """Resolve `path` and ensure it stays within the repo root."""
    root = _repo_root()
    candidate = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"Path '{path}' is outside the allowed repo directory.")
    return candidate


def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file's content (optionally a line range)."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return f"[error] {e}"
    if not p.exists() or not p.is_file():
        return f"[error] file not found: {path}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot read {path}: {e}"
    if end_line <= 0:
        end_line = len(lines)
    start_line = max(1, start_line)
    end_line = min(len(lines), end_line)
    out = [f"{i + 1}: {lines[i]}" for i in range(start_line - 1, end_line)]
    return "\n".join(out) if out else f"[empty file] {path}"


def grep(pattern: str, path: str = ".", case_sensitive: bool = False) -> str:
    """Search file contents for a regex pattern under `path`."""
    try:
        base = _safe_path(path)
    except ValueError as e:
        return f"[error] {e}"
    if not base.exists():
        return f"[error] path not found: {path}"
    import re

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"[error] invalid regex: {e}"
    matches = []
    count = 0
    search_root = base if base.is_dir() else base.parent
    for root, _dirs, files in os.walk(search_root):
        if count > 500:
            break
        for fname in files:
            if fname.endswith((".pyc", ".git", ".png", ".jpg", ".pdf")):
                continue
            fp = Path(root) / fname
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            for ln, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{fp.relative_to(_repo_root())}:{ln}: {line}")
                    count += 1
                    if count > 200:
                        break
        if count > 500:
            break
    return "\n".join(matches) if matches else f"[no matches for '{pattern}']"


def list_files(path: str = ".", max_entries: int = 100) -> str:
    """List files and directories under `path`."""
    try:
        base = _safe_path(path)
    except ValueError as e:
        return f"[error] {e}"
    if not base.exists():
        return f"[error] path not found: {path}"
    if base.is_file():
        return f"[file] {path}"
    entries = []
    try:
        for i, p in enumerate(sorted(base.iterdir())):
            if i >= max_entries:
                entries.append("... (truncated)")
                break
            kind = "dir" if p.is_dir() else "file"
            entries.append(f"[{kind}] {p.relative_to(_repo_root())}")
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot list {path}: {e}"
    return "\n".join(entries) if entries else f"[empty] {path}"


def run_tests(command: str = "python -m pytest -q") -> str:
    """Run a test command inside the repo directory (sandboxed, capped)."""
    root = _repo_root()
    if not root.exists():
        return f"[error] repo dir not found: {root}"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "[error] command timed out (120s limit)"
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot run command: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    limit = 4000
    if len(out) > limit:
        out = out[-limit:] + "\n[... output truncated ...]"
    return f"[exit code {proc.returncode}]\n{out}"


TOOL_FUNCTIONS = {
    "read_file": read_file,
    "grep": grep,
    "list_files": list_files,
    "run_tests": run_tests,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository. Optionally a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path."},
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {"type": "integer", "default": 0, "description": "0 = to end."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regex pattern under a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern."},
                    "path": {"type": "string", "default": "."},
                    "case_sensitive": {"type": "boolean", "default": False},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files/directories under a path in the repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "max_entries": {"type": "integer", "default": 100},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run a test command inside the repo (sandboxed, 120s cap).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "default": "python -m pytest -q"},
                },
                "required": [],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """Dispatch a tool call by name with parsed arguments."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"[error] unknown tool: {name}"
    try:
        return fn(**(arguments or {}))
    except TypeError as e:
        return f"[error] bad arguments for {name}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"[error] {name} failed: {e}"
