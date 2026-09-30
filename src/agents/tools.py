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
import re
import subprocess
from pathlib import Path

from config import Config

# Models frequently emit absolute paths in a shell-native dialect instead of
# repo-relative ones: Git-Bash/MSYS style ("/d/dev/repo/file.py") or a Windows
# drive path ("D:\dev\repo\file.py"). Both were observed in EXP-20260823-010,
# where every such call was rejected as "outside the allowed repo directory" and
# the agent burned turns retrying. They are normalised to repo-relative below.
_MSYS_PATH_RE = re.compile(r"^/([a-zA-Z])/(.*)$")

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


def repo_root_resolved() -> bool:
    """True when tools are bound to a real instance checkout.

    Used to refuse tool execution when the root could not be resolved: falling
    back to the shared sandbox base would let an agent read a DIFFERENT
    instance's files, silently contaminating the run's patch.
    """
    return _CURRENT_REPO_ROOT is not None and _CURRENT_REPO_ROOT.is_dir()


def ensure_repo_root(repo: str, base_commit: str) -> Path | None:
    """Preferred repo-root resolution for tool-calling agents.

    1. source_context.get_repo_at_commit — authoritative: shallow-fetches the
       exact base_commit into cache if missing (works even with
       SOURCE_CONTEXT_ENABLED=false), verifies HEAD == base_commit.
    2. Fallback to the heuristic resolve_repo_root if that fails.
    """
    try:
        from source_context import get_repo_at_commit

        path = get_repo_at_commit(repo, base_commit)
        if path is not None:
            return Path(path).resolve()
    except Exception:  # noqa: BLE001
        pass
    return resolve_repo_root(repo, base_commit)


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
    # Loud, greppable marker: without a resolved root, tool calls would fall
    # back to the shared sandbox base and agents could read the WRONG
    # instance's files. Surface it so contaminated runs are identifiable.
    from utils.logger import logger

    logger.warning(
        f"[repo_root] FAILED to resolve repo root for {repo}@{base_commit} — "
        f"tool calls will operate on the shared sandbox base "
        f"({_repo_root()}). Run tools/prepare_repos.py and re-run this instance."
    )
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


def _normalize_tool_path(path: str) -> str:
    """Rewrite shell-native absolute paths into repo-relative ones.

    Observed in real runs (EXP-20260823-010): the model asked for
    ``/d/development/Skripsi2/AgantBech-SE/datasets/repos/django/django/<sha>``
    and the sandbox rejected it, so the agent retried the same shape and wasted
    turns. Three shapes are handled, all reduced to a path relative to the
    active repo root:

      * Git-Bash / MSYS:  ``/d/dev/repo/file.py``   -> ``file.py``
      * Windows drive:    ``D:\\dev\\repo\\file.py`` -> ``file.py``
      * Absolute under root: ``<root>/file.py``     -> ``file.py``

    A path that cannot be mapped is returned unchanged so the sandbox still
    rejects it with an explicit error instead of silently reading elsewhere.
    """
    if not path:
        return path

    raw = path.strip().strip('"').strip("'")
    root = _repo_root()

    # Already relative: leave it alone (the common, correct case).
    if not Path(raw).is_absolute() and not _MSYS_PATH_RE.match(raw):
        return raw

    candidates: list[Path] = []

    m = _MSYS_PATH_RE.match(raw)
    if m:
        # /d/dev/repo/file.py -> D:\dev\repo\file.py
        candidates.append(Path(f"{m.group(1).upper()}:/{m.group(2)}"))

    candidates.append(Path(raw))

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except (OSError, ValueError):
            continue
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            # Not under this instance's root. Fall back to matching the path
            # *suffix* against the repo (e.g. ".../django/conf/x.py" inside a
            # differently-rooted checkout) so a valid file is still reachable.
            parts = resolved.parts
            for i in range(len(parts)):
                sub = Path(*parts[i:])
                if (root / sub).exists():
                    return str(sub)
            continue

    return raw


def _safe_path(path: str) -> Path:
    """Resolve `path` and ensure it stays within the repo root."""
    if not repo_root_resolved():
        raise ValueError(
            "No repository is bound to this run (the instance checkout could not "
            "be resolved). Refusing to read files: falling back to the shared "
            "sandbox would expose a DIFFERENT instance's source. "
            "Run tools/prepare_repos.py for this instance and re-run."
        )
    root = _repo_root()
    path = _normalize_tool_path(path)
    candidate = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path '{path}' is outside the allowed repo directory. "
            f"Use a path RELATIVE to the repository root, e.g. 'requests/sessions.py'."
        )
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


# Environment failures look like a broken sandbox, not a failing test. Retrying
# cannot help, so the tool tells the agent to stop instead of letting it burn
# turns. Measured on EXP-20260927-004: one planning step spent >33 minutes and 49
# tool calls, 15 of them run_tests with 8 different command spellings, because
# nothing told the agent the test environment simply was not installed.
_ENV_FAILURE_MARKERS = (
    "no module named",
    "modulenotfounderror",
    "importerror",
    "is not recognized as an internal or external command",
    "command not found",
    "cannot find module",
    "no such file or directory",
)


def _looks_like_env_failure(output: str) -> bool:
    """True when the command failed because the environment is missing, not the test."""
    low = output.lower()
    return any(m in low for m in _ENV_FAILURE_MARKERS)


# Repeat-call guard: an agent that retries the same failing command is wasting
# its budget. Track the last few commands and refuse exact repeats.
#
# SCOPE: one ACT, not one strategy. This matters because a strategy has several
# agents -- review runs planner, executor, reviewer, and possibly a revision --
# and they do not share a turn budget or a conversation. Counting the planner's
# test command against the executor's quota means a later agent is refused a
# command it has never run, and the refusal reads as "the harness blocked me"
# rather than as the agent's own choice. Each act starts fresh (see
# providers/tool_loop.py), which is the same rule the turn budget follows.
_RECENT_TEST_COMMANDS: list[str] = []
_MAX_REPEATS = 2

#: Roles whose mandate is to inspect, never to author. The review strategy's whole
#: premise is that the reviewer does not write the code it judges, so a read-only
#: role must not be able to write through the shell either.
_READONLY_ROLES = frozenset({"planner", "reviewer"})

#: Shell constructs that MUTATE the repository. Checked only for read-only roles,
#: because `run_tests` takes an arbitrary command and those roles have no other
#: route to a shell.
#:
#: Deliberately narrow. `2>&1`, `>/dev/null` and a pipe into `tail` are NOT writes --
#: every test command here uses them, and a first version of this pattern treated
#: `2>&1` as a redirect and flagged every pytest call. An audit of both pilots
#: (docs/AUDIT_RUN_TESTS_PARTNER.md) also found 13 false "write" classifications
#: from `->` inside a printed string, so a redirect must be preceded by something
#: that can actually be a command.
_WRITE_COMMAND_PATTERNS = (
    # open('f','w') / open('f', 'a') / open('f','x') -- a MODE ARGUMENT, not merely
    # the word "open". The first version used [^)]* between the parens and the
    # quote, which matched the READ-ONLY `open('x.py').read()`: the class skipped
    # the closing paren and reached the quote. Matching the closing paren prevents
    # it, so a read is not mistaken for a write.
    re.compile(r"\bopen\s*\([^)]*,\s*['\"][wax]", re.I),
    re.compile(r"\bsed\s+-i\b", re.I),                       # in-place edit
    re.compile(r"\bgit\s+(checkout|apply|reset|clean|stash|rm|mv)\b", re.I),
    re.compile(r"\b(rm|mv|cp|truncate|tee|dd)\s", re.I),
    re.compile(r"\b(shutil|os)\.(remove|unlink|rmtree|rename|replace|chmod)\b", re.I),
    # patch with a file argument, in either direction: `patch -p1 < x` and
    # `patch -p1 x.diff`. The first version required `-p` followed by nothing
    # else, so a real invocation was missed.
    re.compile(r"\bpatch\s+(-p\s*\d+|\S+\.(diff|patch))\b", re.I),
    re.compile(r"(?<![-=<>0-9])>{1,2}\s*(?!/dev/null)\S"),   # > file, >> file
    re.compile(r"\bpython[0-9.]*\s+-\s*$", re.I),            # python reading a script that may write
)


def _command_looks_like_a_write(command: str) -> str:
    """Return the pattern that matched, or "" when the command is not a write.

    Used only to refuse MUTATION of the REPOSITORY by a read-only role.

    Scope matters, and a first version of this got it wrong. It refused
    ``cd /tmp && cat > t.py <<'EOF' ... EOF`` -- a reviewer writing a scratch probe
    script OUTSIDE the repo, which is a legitimate way to establish behaviour and
    one the model reached for on its very first real run after the guard went in
    (EXP-20260930-398). Blocking that would cripple verification: the reviewer must
    be able to test a hypothesis, it just must not rewrite the code it judges.

    So the question is not "does this write" but "does this write INTO the repo".
    ``run_tests`` runs with ``cwd=repo_root``, so:

    * a RELATIVE write target lands inside the repo -> refuse;
    * an ABSOLUTE target outside the repo -> allow;
    * a command that first ``cd``-s outside the repo -> its relative writes land
      outside -> allow.

    A read-only role running ``python -c "print(x)"`` is fine and common; one
    running ``python -c "open('django/conf/global_settings.py','w')..."`` is not.
    """
    if not command:
        return ""

    # A cd to somewhere outside the repo makes the rest of the command's relative
    # paths land outside too.
    for target in re.findall(r"\bcd\s+([^\s;&|]+)", command):
        if _is_outside_repo_path(target.strip("'\"")):
            return ""

    # A write whose TARGET is an absolute path outside the repo is scratch work,
    # even when the command looks like a write. This covers the two shapes the
    # model actually used: a shell redirect (`> /tmp/out.txt`) and a Python open()
    # (`open('/tmp/probe.py','w')`).
    targets = re.findall(r">{1,2}\s*(\S+)", command)
    targets += re.findall(r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]", command)
    write_like_targets = [t.strip("'\"") for t in targets]
    if write_like_targets and all(
        _is_outside_repo_path(t) or t == "/dev/null" for t in write_like_targets
    ):
        return ""

    for pattern in _WRITE_COMMAND_PATTERNS:
        if pattern.search(command):
            return pattern.pattern
    return ""


def _is_outside_repo_path(raw: str) -> bool:
    """True when ``raw`` names a location outside the active repo checkout.

    Used to let a read-only role keep scratch work (probe scripts, temp output)
    while refusing writes that land in the repository it is judging.
    """
    if not raw:
        return False
    candidate = raw.strip().strip("'\"")
    if candidate.startswith(("/tmp", "/var/tmp", "/dev/shm", "/dev/null")):
        return True
    if candidate.startswith("$TMPDIR") or candidate.startswith("%TEMP%"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", candidate):
        try:
            return not candidate.lower().startswith(str(_repo_root()).lower())
        except Exception:  # noqa: BLE001
            return False
    if candidate.startswith("/") and not candidate.startswith("//"):
        # Any other absolute POSIX path is outside a Windows-style repo root.
        return True
    return False


def run_tests(command: str = "", role: str = "") -> str:
    """Run a test command inside the instance repo (sandboxed, capped).

    SWE-bench checkouts are raw clones: their per-repo dependencies are NOT
    installed (the official harness supplies those in a conda env). So most test
    commands fail for environmental reasons. That is not the agent's fault, and
    retrying cannot fix it — but an agent that does not know this will retry
    anyway, with new spellings, until its budget is gone.

    Therefore this tool distinguishes two outcomes:

    * **test failure** — tests ran and reported failures. Useful signal.
    * **environment failure** — the interpreter or a module is missing. The tool
      says so explicitly and asks the agent to stop retrying.

    The interpreter is ``sys.executable`` (the project venv), not a bare
    ``python``: on this machine a bare ``python`` resolves to the system
    interpreter, where the venv's packages are absent.

    ``role`` REFUSES a mutating command from a read-only role. The parameter name
    is ``command`` and it is executed with ``shell=True``, so the tool is a general
    shell, not a test runner -- which means a reviewer, whose mandate is to judge
    the code rather than author it, could rewrite the very file it is reviewing.
    Audited on 2026-10-01 (docs/AUDIT_RUN_TESTS_PARTNER.md §2): a reviewer could
    ``open('core.py','w').write(...)`` and the change reached the working tree, and
    in the revision path it reached the SHIPPED patch. No guard prevented it.

    The audit also measured that this never actually happened in either pilot (0 of
    20 reviewer calls), so this guard closes an exploitable hole rather than fixing
    an observed failure. It is deliberately narrow: read-only roles keep the shell
    for probing, which they do use (10 of 13 reviewer calls were read-only probes).
    """
    root = _repo_root()
    if not root.exists():
        return f"[error] repo dir not found: {root}"

    command = (command or "").strip()
    if not command:
        command = _guess_test_command(root)

    # A read-only role must not mutate the repository through the shell. See the
    # docstring: the grant is structural, but this tool is arbitrary shell.
    if role in _READONLY_ROLES:
        matched = _command_looks_like_a_write(command)
        if matched:
            _warn(
                f"[tool-guard] read-only role={role} tried a mutating run_tests "
                f"command; refused. command={command[:160]!r}"
            )
            return (
                f"[error] role '{role}' is read-only and may not modify the "
                f"repository. This command looks like a write. Use read_file, grep, "
                f"git_diff or a read-only shell command to inspect the code instead. "
                f"To change the code, report it in your verdict."
            )

    # Refuse to spin on an identical command.
    repeats = sum(1 for c in _RECENT_TEST_COMMANDS if c == command)
    if repeats >= _MAX_REPEATS:
        return (
            f"[stop] You have already run this exact command {repeats} times and it "
            "did not help. Do NOT run it again. Verify your fix by reading the code "
            "instead, then finish."
        )
    _RECENT_TEST_COMMANDS.append(command)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return "[error] command timed out (180s limit)"
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot run command: {e}"

    out = (proc.stdout or "") + (proc.stderr or "")
    limit = 4000
    truncated = len(out) > limit
    if truncated:
        out = out[-limit:]

    if _looks_like_env_failure(out):
        return (
            "[tests unavailable] The test environment for this repository is not "
            "installed in this sandbox (its dependencies come from the evaluation "
            "harness, not from a raw clone). This is NOT a problem with your fix, "
            "and retrying with a different command will not help.\n"
            "Do NOT call run_tests again. Verify your change by reasoning about the "
            "code, then produce your final patch.\n\n"
            f"Command: {command}\n[exit code {proc.returncode}]\n{out}"
        )

    suffix = "\n[... output truncated ...]" if truncated else ""
    return f"[exit code {proc.returncode}]\n{out}{suffix}"


def _guess_test_command(root: Path) -> str:
    """Pick a plausible test command for this checkout.

    Uses the project interpreter (``sys.executable``) so the venv's packages are
    visible. Layout is detected rather than assumed: Django and sympy ship a
    ``tests/runtests.py`` runner, most other repos use pytest.
    """
    import sys

    py = sys.executable or "python"
    if (root / "tests" / "runtests.py").exists():
        # Django-style runner; the repo root must be importable.
        return f'cd tests && "{py}" runtests.py'
    if (root / "pytest.ini").exists() or (root / "setup.cfg").exists() or (root / "tox.ini").exists():
        return f'"{py}" -m pytest -x -q'
    if (root / "tests").is_dir():
        return f'"{py}" -m pytest tests -x -q'
    return f'"{py}" -m pytest -x -q'


def reset_test_guard() -> None:
    """Clear the repeat-call guard (called per strategy run)."""
    _RECENT_TEST_COMMANDS.clear()


def _run_git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run git inside the active repo root, returning the raw completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=str(_repo_root()),
        capture_output=True,
        timeout=timeout,
    )


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact substring in a repo file.

    This is the primary way an agent applies a fix. Producing a unified diff as
    *text* was the previous mechanism and it failed structurally: measured on
    EXP-20260824-005, 56 of 150 patches carried status NORMALIZE (well-formed
    arithmetic, wrong content) and 31 of 66 sampled patches could never apply
    because the removed lines did not exist in the target. Editing the real file
    and letting ``git diff`` derive the patch removes that whole failure class.

    ``old_string`` must match exactly once: an ambiguous edit is rejected with an
    explicit error rather than applied to the wrong occurrence, because a
    mis-placed edit produces a plausible-looking but wrong patch.
    """
    try:
        p = _safe_path(path)
    except ValueError as e:
        return f"[error] {e}"
    if not p.exists() or not p.is_file():
        return f"[error] file not found: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot read {path}: {e}"

    if old_string == new_string:
        return "[error] old_string and new_string are identical — nothing to change"
    if old_string == "":
        return "[error] old_string is empty; use write_file to create a file"

    count = text.count(old_string)
    if count == 0:
        return (
            f"[error] old_string not found in {path}. "
            f"Read the file first and copy the exact text, including indentation."
        )
    if count > 1:
        return (
            f"[error] old_string appears {count} times in {path} — ambiguous. "
            f"Include more surrounding context so it matches exactly once."
        )

    updated = text.replace(old_string, new_string, 1)
    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot write {path}: {e}"

    # Report the resulting diff for this file so the model sees exactly what
    # changed, without having to guess whether the edit landed.
    return f"[ok] edited {path}\n{_diff_for_path(p)}"


def write_file(path: str, content: str) -> str:
    """Create a repo file, or replace one wholesale.

    Overwriting is guarded: a model that emits a whole file can silently truncate
    it (token limits, or a "rewrite" that drops half the content). Measured on
    EXP-20260927-004, an agent rewrote ``django/forms/widgets.py`` wholesale and
    then had to ``reset_repo`` to undo it. A large shrink is refused so the
    damage cannot reach the captured patch. Prefer ``edit_file`` for changes to
    existing code — it fails without modifying anything when it cannot match.
    """
    try:
        p = _safe_path(path)
    except ValueError as e:
        return f"[error] {e}"

    existed = p.exists()
    if existed:
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            old = ""
        # Refuse a drastic shrink of an existing file: that is a truncated
        # rewrite, not an edit, and it would silently delete working code.
        if len(old) > 2000 and len(content) < len(old) * 0.5:
            return (
                f"[refused] writing {path} would shrink it from {len(old)} to "
                f"{len(content)} chars, which looks like a truncated rewrite. Use "
                "edit_file to change the specific lines you need, or resend the "
                "complete file content."
            )

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot write {path}: {e}"
    verb = "overwrote" if existed else "created"
    return f"[ok] {verb} {path} ({len(content)} chars)"


def _diff_for_path(p: Path) -> str:
    """Return the working-tree diff for one file (bounded)."""
    try:
        rel = p.relative_to(_repo_root())
    except ValueError:
        rel = p
    try:
        proc = _run_git("diff", "--no-color", "--", str(rel))
    except Exception as e:  # noqa: BLE001
        return f"[diff unavailable: {e}]"
    out = (proc.stdout or b"").decode("utf-8", "replace")
    if not out.strip():
        return "[no diff — file content unchanged vs HEAD]"
    limit = 3000
    return out[:limit] + ("\n[... diff truncated ...]" if len(out) > limit else "")


def git_diff() -> str:
    """Return the full working-tree diff of the repo (this becomes the patch).

    The pipeline extracts the submitted patch with this, so whatever the agent
    changed on disk is exactly what gets evaluated — no diff re-typing, no hunk
    arithmetic, no hallucinated context lines.
    """
    try:
        proc = _run_git("diff", "--no-color")
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot run git diff: {e}"
    out = (proc.stdout or b"").decode("utf-8", "replace")
    if not out.strip():
        return "[empty] no changes have been made to the repository yet"
    return out


def reset_repo() -> str:
    """Discard all working-tree changes (clean slate before a new attempt)."""
    try:
        proc = _run_git("checkout", "--", ".")
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            return f"[error] git checkout failed: {err}"
        proc2 = _run_git("clean", "-fd", "--", ".")
        err2 = (proc2.stderr or b"").decode("utf-8", "replace").strip()
        return f"[ok] repository reset to HEAD{(' | ' + err2) if err2.strip() else ''}"
    except Exception as e:  # noqa: BLE001
        return f"[error] cannot reset repo: {e}"


# ---------------------------------------------------------------------------
# Patch capture (edit-then-diff)
# ---------------------------------------------------------------------------
#
# Under tool calling the agent edits real files, so the authoritative patch is
# the repository's working-tree diff — not whatever text the model typed. This
# is the whole point of the change: a diff produced by git is applyable by
# construction, whereas a diff re-typed by the model can carry invented context
# lines (31 of 66 sampled patches on EXP-20260824-005 could never apply).


def _git_in(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        timeout=timeout,
    )


def _warn(message: str) -> None:
    """Log a warning without a hard import-time dependency on the logger."""
    try:
        from utils.logger import logger

        logger.warning(message)
    except Exception:  # noqa: BLE001
        pass


def capture_diff(repo_root: str | Path | None = None) -> str:
    """Return the working-tree diff for ``repo_root`` as a unified patch.

    Newly created files are included: ``git diff`` alone omits untracked files,
    so a fix that adds a file would silently vanish. ``git add -N`` marks them
    intent-to-add (content is NOT staged), which makes them appear in the diff
    while keeping the working tree unchanged.

    CRLF is normalised to LF because the diff is later piped to ``git apply``,
    and a stray ``\\r`` on every line makes a patch fail to apply.
    """
    root = Path(repo_root) if repo_root else _repo_root()
    if not root.is_dir():
        return ""
    try:
        _git_in(root, "add", "-N", ".")
        proc = _git_in(root, "diff", "--no-color", "--no-ext-diff")
    except Exception as e:  # noqa: BLE001
        _warn(f"[capture_diff] failed for {root}: {e}")
        return ""
    out = (proc.stdout or b"").decode("utf-8", "replace")
    return out.replace("\r\n", "\n").replace("\r", "\n")


def reset_working_tree(repo_root: str | Path | None = None) -> bool:
    """Return ``repo_root`` to a pristine checkout (no edits, no new files).

    Called before each strategy so the three strategies on the same issue each
    start from the same base commit. Without it, strategy N+1 would inherit
    strategy N's edits and its captured diff would contain both.

    Returns True when the tree is verified clean afterwards. A caller that ignores
    the result is no worse off than before, but one that checks it can refuse to
    run rather than diff against a dirty tree.

    WHY IT VERIFIES. ``_git_in`` returns a CompletedProcess and does NOT raise on a
    non-zero exit code, so the old ``try/except`` here only ever caught a timeout or
    an OSError -- a git command that FAILED (a stale index.lock, a permission error,
    a corrupted index) returned normally and the function reported success. The
    consequence is silent and severe: the captured diff would then include whatever
    was already in the tree, which for this repo was a leftover GOLD PATCH from
    EXP-20260930-098-gold-check. An agent run against that tree produces a patch
    containing the reference solution, which reads as the agent solving the issue.

    Measured before this fix: 5 of 50 checkouts held the gold patch (or its test
    patch) with no indication anywhere in the results.
    """
    root = Path(repo_root) if repo_root else _repo_root()
    if not root.is_dir():
        return False
    # A new strategy run is a new conversation: forget the previous run's
    # repeated-command history so the guard only fires within one run.
    reset_test_guard()
    try:
        _git_in(root, "reset", "-q")            # drop intent-to-add marks
        _git_in(root, "checkout", "--", ".")
        _git_in(root, "clean", "-fdq", "--", ".")
    except Exception as e:  # noqa: BLE001
        _warn(f"[reset_working_tree] command failed for {root}: {e}")
        return False

    # Verify, because a git command can fail without raising.
    try:
        status = _git_in(root, "status", "--porcelain")
        leftover = (status.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:  # noqa: BLE001
        _warn(f"[reset_working_tree] could not verify {root}: {e}")
        return False

    if leftover:
        paths = [l for l in leftover.splitlines() if l.strip()][:5]
        _warn(
            f"[reset_working_tree] {root} is STILL DIRTY after reset "
            f"({len(leftover.splitlines())} path(s)): {paths}. The captured diff "
            f"would include these, so the run should not be trusted."
        )
        return False
    return True


def finalize_patch(repo_root: str | Path | None, fallback_response: str) -> str:
    """Choose the patch to evaluate.

    Tool-calling path: the working-tree diff, which is applyable by construction.
    Fallback: the model's own text (legacy path, and the case where the agent
    explored but never edited anything — in which case there is genuinely no
    patch and the text-based extractor should report NO_DIFF honestly).
    """
    if not Config.TOOLCALL_ENABLED or repo_root is None:
        return fallback_response
    diff = capture_diff(repo_root)
    if diff.strip():
        return diff
    return fallback_response


TOOL_FUNCTIONS = {
    "read_file": read_file,
    "grep": grep,
    "list_files": list_files,
    "run_tests": run_tests,
    "edit_file": edit_file,
    "write_file": write_file,
    "git_diff": git_diff,
    "reset_repo": reset_repo,
}

# Per-role tool assignment: tools match each agent's function so the
# orchestration comparison stays meaningful (planner analyses, executor
# builds+verifies, reviewer checks with evidence, direct fixes in one act).
#
# Editing tools are granted only to roles that are supposed to change code:
# planner is read-only by design (it produces a plan, not a patch), and the
# reviewer inspects rather than authors (it may run tests to gather evidence).
#
# VERIFICATION PARITY (run_tests). Direct used to lack run_tests, on the theory
# that it is "a cheap one-shot" that should not spend turns verifying. That made
# the comparison uninterpretable: planning and review could verify a fix and
# direct could not, so a planning/review win could not be separated from their
# having a capability direct was denied. The pilot made the gap concrete --
# run_tests was called 8x by planning and 13x by review, and 0x by direct, which
# measured the GRANT, not the strategy's choice.
#
# Every strategy can now verify: direct through its own act, planning and review
# through the executor. A strategy may still choose not to, and that choice is
# then observable in the trajectory rather than imposed by the harness.
#
# reset_repo is granted to direct as well, on evidence. The earlier reasoning --
# "direct has one act, so discarding the tree wastes its only attempt" -- was
# contradicted by the pilot: on django__django-11019 the planning executor used
# reset_repo at call 29 (after two edits) to abandon a bad approach and rewrite
# from clean, and that run RESOLVED the instance while direct's did not. Abandoning
# a wrong path is a legitimate capability, not a hazard, and denying it to direct
# would repeat exactly the mistake run_tests was denied on: it would make direct's
# results measure the harness rather than the strategy.
#
# The hazard is real but belongs to the STRATEGY, not the tool: a direct run that
# resets and never edits again ships no patch. That is an outcome to record, not a
# capability to withhold -- the same reasoning that lets an agent choose to stop
# early.
AGENT_TOOLS: dict[str, list[str]] = {
    "direct": [
        "read_file", "grep", "list_files", "run_tests",
        "edit_file", "write_file", "git_diff", "reset_repo",
    ],
    "planner": ["read_file", "grep", "list_files"],
    "executor": [
        "read_file", "grep", "list_files", "run_tests",
        "edit_file", "write_file", "git_diff", "reset_repo",
    ],
    "reviewer": ["read_file", "grep", "run_tests", "git_diff"],
}


def get_tools_for_agent(agent_name: str) -> list[dict]:
    """Return OpenAI-compatible tool schemas for one agent role."""
    allowed = AGENT_TOOLS.get(agent_name, list(TOOL_FUNCTIONS.keys()))
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] in allowed]

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
            "description": (
                "Run a test command inside the instance repo to verify your fix. "
                "Omit the command to auto-select one for this repo's layout "
                "(recommended). Use a targeted path, e.g. "
                "'python -m pytest tests/test_x.py -x -q', rather than the whole suite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run. Omit to auto-detect.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Apply a fix by replacing an exact block of text in a repository file. "
                "PREFER THIS over writing a diff by hand. Read the file first, then copy "
                "the exact text (including indentation) into old_string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path."},
                    "old_string": {
                        "type": "string",
                        "description": "Exact existing text to replace. Must appear exactly once.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text. Use the same indentation as the file.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one with full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": (
                "Show the unified diff of everything you have changed so far. "
                "Call this to verify your fix before finishing."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_repo",
            "description": "Discard ALL working-tree changes and start over from a clean checkout.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def execute_tool(name: str, arguments: dict, role: str = "") -> str:
    """Dispatch a tool call by name with parsed arguments.

    ``role`` ENFORCES the per-role grant. Without it this function executed whatever
    name the model returned, because the provider is only sent the schemas in
    ``AGENT_TOOLS`` -- a filter on what the model is OFFERED, not on what can run.
    Audited on 2026-10-01 (docs/AUDIT_RUN_TESTS_PARTNER.md §2): calling
    ``execute_tool("write_file", ...)`` and ``execute_tool("edit_file", ...)`` both
    succeeded for a reviewer, with no role check anywhere.

    That mattered because the review strategy's premise is that the reviewer does
    not author code. The premise was held up by three soft layers -- the schema
    filter, the system prompt ("You may NOT modify files"), and the absence of
    editing tools -- and none of them is enforcement.

    An empty ``role`` keeps the legacy behaviour so existing callers and tests do
    not break, but the tool loop always passes the real role.
    """
    if role and role in AGENT_TOOLS and name not in AGENT_TOOLS[role]:
        _warn(
            f"[tool-guard] role={role} called '{name}', which is not granted to it "
            f"(granted: {AGENT_TOOLS[role]}). Refused."
        )
        return (
            f"[error] tool '{name}' is not available to role '{role}'. "
            f"You may use: {', '.join(AGENT_TOOLS[role])}."
        )

    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"[error] unknown tool: {name}"
    try:
        # run_tests needs the role so it can refuse a mutating command from a
        # read-only role; it is the only tool that takes an arbitrary shell string.
        if name == "run_tests":
            return fn(**(arguments or {}), role=role)
        return fn(**(arguments or {}))
    except TypeError as e:
        return f"[error] bad arguments for {name}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"[error] {name} failed: {e}"
