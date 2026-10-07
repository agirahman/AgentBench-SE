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
import stat
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


#: The git metadata directory. Blocked by NAME (component-wise) rather than as a
#: path prefix, so `.gitignore` / `.gitattributes` keep working. Case-insensitive
#: because Windows filesystems are: `.GIT/config` opens the same file as `.git/config`.
_GIT_DIR_NAME = ".git"


def _safe_path(path: str) -> Path:
    """Resolve `path` and ensure it stays within the repo root.

    Also refuses any path whose components name the ``.git`` directory. ``.git``
    lives INSIDE the checkout, so a root-containment check alone allows it, and
    these tools run on the HOST where ``SANDBOX_MODE`` has no effect. Measured
    before the guard: ``read_file('.git/packed-refs')`` returned tag refs and
    ``read_file('.git/ORIG_HEAD')`` returned a commit SHA.

    Matching is on path COMPONENTS, not a prefix, so ``.gitignore`` and
    ``.gitattributes`` keep working. The comparison is case-insensitive because
    Windows filesystems are: ``.GIT/config`` opens the same file as ``.git/config``.
    """
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
        rel_parts = candidate.relative_to(root).parts
    except ValueError:
        raise ValueError(
            f"Path '{path}' is outside the allowed repo directory. "
            f"Use a path RELATIVE to the repository root, e.g. 'requests/sessions.py'."
        )
    # Checked on the RESOLVED path, so a symlink pointing into .git is caught too
    # (`link -> .git` would otherwise resolve to a readable .git file).
    if any(part.lower() == _GIT_DIR_NAME for part in rel_parts):
        raise ValueError(
            "Direct access to the .git directory is not allowed. Use git_diff to "
            "inspect changes."
        )
    return candidate


#: Windows file attribute marking a reparse point (symlink OR junction).
#: A junction created by a Linux container lands on the host with this bit set,
#: and `Path.is_symlink()` returns False for it — so the attribute, not the
#: portable API, is what a detector must read.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(path: Path) -> bool:
    """True when `path` is a symlink or junction (do NOT follow it)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def _resolves_inside_root(path: Path) -> bool:
    """True when `path` still points inside the repo root after resolving links.

    This is the per-FILE guard. `_safe_path` only validates the path the agent
    named; a directory listing can still contain a link that points anywhere, and
    reading through it returns content from outside the sandbox. Measured on
    2026-10-06: a symlink dropped into the checkout by the container (mount was
    rw, so it landed on the host) let `grep` return the project's `.env`, and
    `read_file` on the same link was correctly refused — the leak was the walker,
    not the resolver.
    """
    try:
        root = _repo_root().resolve()
        resolved = Path(os.path.realpath(path))
        resolved.relative_to(root)
    except (ValueError, OSError):
        return False
    return True


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
    skipped_links = 0
    search_root = base if base.is_dir() else base.parent
    for root, dirs, files in os.walk(search_root):
        if count > 500:
            break
        # Prune .git from the walk. `_safe_path('.')` is legitimate, so the walk
        # starts at the repo root and would otherwise descend into .git and return
        # its contents (measured: `grep('FUTURE-TAG', '.')` returned
        # `.git\packed-refs:3: ... refs/tags/v9.9.9-FUTURE-TAG`). Blocking the
        # requested path alone does NOT close this: the leak is the walker.
        dirs[:] = [d for d in dirs if d.lower() != _GIT_DIR_NAME]
        for fname in files:
            if fname.endswith((".pyc", ".git", ".png", ".jpg", ".pdf")):
                continue
            fp = Path(root) / fname
            # Per-FILE guard: a symlink/junction inside the tree can point outside
            # the repo, and reading through it returns content the agent must not
            # see (measured: this returned the project's .env). Checked BEFORE the
            # read, so the target is never opened.
            if _is_reparse_point(fp) or not _resolves_inside_root(fp):
                skipped_links += 1
                continue
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
    if not matches:
        note = ""
        if skipped_links:
            note = (f" ({skipped_links} symlink(s) skipped: they point outside the "
                    f"repository, which is not allowed)")
        return f"[no matches for '{pattern}']{note}"
    return "\n".join(matches)


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
            # A link inside the tree can point outside the repo. Report it as a
            # link (never as a plain file/dir) so the agent is not invited to read
            # it, and so a stray link left by a container run is visible.
            if _is_reparse_point(p) or not _resolves_inside_root(p):
                entries.append(f"[link->outside] {p.relative_to(_repo_root())}")
                continue
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


_SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"
_DOCKER_SANDBOX_IMAGE = os.environ.get("DOCKER_SANDBOX_IMAGE", "agentbench-sandbox:latest")
_DOCKER_MASK_GIT = os.environ.get("DOCKER_MASK_GIT", "1").strip().lower() in ("1", "true", "yes")

#: Distinctive phrases from the messages `run_tests` returns when the SANDBOX
#: itself is unavailable. Kept at module level, next to those messages, so the
#: detector cannot drift from the text it matches.
#:
#: Deliberately NARROW: no generic words like "error" or "not found". An ordinary
#: test failure must not read as a dead sandbox, or a healthy run is marked
#: SANDBOX_DOWN -- which would discard good data, the opposite of the intent.
_SANDBOX_INFRA_MARKERS = (
    "docker daemon is not running",              # daemon down (the main message)
    "dockerdesktoplinuxengine",                  # the Windows named pipe
    "error during connect",                      # docker CLI's own connect error
    "cannot connect to the docker daemon",       # docker CLI's own message
    "is the docker daemon running",              # docker CLI's own message
    "cannot run docker container",               # run_tests' wrapper message
    "docker cli executable not found",           # docker not installed at all
)

#: A missing image is matched as a PAIR -- the word "docker image" alone could
#: appear in an ordinary test's output, so it only counts together with docker's
#: own phrasing for an absent local image.
_SANDBOX_IMAGE_MARKERS = ("docker image", "not found locally")


def is_sandbox_infrastructure_error(result: str) -> bool:
    """True when a ``run_tests`` result says the SANDBOX was unavailable.

    Distinguishes "the sandbox could not run the tests" from "the tests ran and
    failed". Only the first invalidates a run: an agent that never had a working
    sandbox did not get the same conditions as one that did, so its patch says
    nothing about the strategy.

    This exists because of EXP-20261007-003: the daemon was down for the whole
    session, all 9 runs returned "[error] Docker daemon is not running or
    unreachable." on every call, and every one of them was still recorded VALID.
    The contamination was invisible in the results and had to be found by hand.

    Matched on the returned STRING, not an exception: ``run_tests`` deliberately
    returns errors as text so the agent can read and react to them, which is what
    let the failure pass silently in the first place.

    Deliberately narrow: an ``[error]`` for a bad command, a refused write by a
    read-only role, or a test failure are all NOT sandbox problems, and counting
    them here would mark healthy runs SANDBOX_DOWN.
    """
    text = (result or "").lower()
    if any(marker in text for marker in _SANDBOX_INFRA_MARKERS):
        return True
    # The missing-image message is matched as a PAIR (see _SANDBOX_IMAGE_MARKERS).
    return all(marker in text for marker in _SANDBOX_IMAGE_MARKERS)


def get_sandbox_mode() -> str:
    """Return the active sandbox mode: 'docker' (default) or 'local'.

    The default is **docker**, i.e. fail-CLOSED. It used to be 'local', which
    meant any entry point that did not set SANDBOX_MODE silently executed agent
    commands on the HOST with no isolation and no warning — the sweep and the
    runner both failed to set it. A missing daemon then produced a clear error
    instead of an unisolated run, which is the correct direction for a safety
    default: the failure is loud and cheap rather than silent and expensive.

    'local' is still available for development and for the local-mode tests, but
    it must now be requested EXPLICITLY.
    """
    return os.environ.get("SANDBOX_MODE", "docker").strip().lower()


def _translate_paths_to_container(command: str, root: Path) -> str:
    """Translate absolute host paths pointing inside checkout to /work paths.

    Paths pointing OUTSIDE checkout are deliberately untouched so they fail inside
    the container (preventing filesystem escape).
    """
    root_resolved = root.resolve()
    roots = {str(root_resolved), root_resolved.as_posix(), str(root), root.as_posix()}
    for r in sorted(roots, key=len, reverse=True):
        pattern = re.compile(re.escape(r) + r'((?:[/\\][^\s"\'><;&|]*)?)', re.IGNORECASE)

        def repl(m: re.Match) -> str:
            subpath = m.group(1).replace("\\", "/")
            return "/work" + subpath

        command = pattern.sub(repl, command)
    return command

#: Command patterns attempting network access or package/git retrieval outside
#: the evaluation sandbox. These close the solutions-leak loophole (2026-10-05)
#: where agents downloaded upstream patches via curl/wget or inspected remote git refs.
_SECURITY_COMMAND_PATTERNS = (
    (
        r"(?:^|[;&|\n\r])\s*(?:[\w\\/.-]*[/\\])?git(?:\.exe)?\s+(?:.*?\s+)?(?:fetch|clone|pull|remote\s+add|remote\s+set-url)\b",
        "git network commands (fetch/clone/pull/remote add) are disabled in this sandbox",
    ),
    (
        r"(?:^|[;&|\n\r])\s*(?:[\w\\/.-]*[/\\])?(?:python(?:\.exe)?\s+-m\s+)?pip\d*(?:\.exe)?\s+(?:install|download)\b",
        "pip package installation and download are disabled in this sandbox",
    ),
    (
        r"(?:^|[;&|\n\r])\s*(?:[\w\\/.-]*[/\\])?(?:curl|wget)(?:\.exe)?\b",
        "external web fetch tools (curl/wget) are disabled in this sandbox",
    ),
    (
        r"\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer)\b",
        "network transfer cmdlets are disabled in this sandbox",
    ),
    (
        r"(?:^|[;&|\n\r])\s*(?:[\w\\/.-]*[/\\])?certutil(?:\.exe)?\s+.*-(?:urlcache|f)\b",
        "certutil network download is disabled in this sandbox",
    ),
    (
        r"(?:^|[;&|\n\r])\s*(?:[\w\\/.-]*[/\\])?bitsadmin(?:\.exe)?\b",
        "bitsadmin background transfer is disabled in this sandbox",
    ),
)


def _check_command_security(command: str) -> str | None:
    """Return an error message if the command tries external network access."""
    cmd = (command or "").strip()
    for pattern, reason in _SECURITY_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return f"[blocked] {reason}"
    return None


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

    # Sandbox security guard: block commands attempting network access or package/repo fetch.
    blocked_reason = _check_command_security(command)
    if blocked_reason:
        _warn(f"[tool-guard] network/security command blocked: {command[:160]!r}")
        return blocked_reason

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

    mode = get_sandbox_mode()
    # Fail CLOSED on an unrecognised value. The dispatch below has an `else` branch
    # that runs on the host, so a typo ("dockr", "Docker ") would have silently
    # selected the UNISOLATED path — the same class of bug as the old 'local'
    # default, just triggered by a typo instead of an omission.
    if mode not in ("docker", "local"):
        return (
            f"[error] SANDBOX_MODE={mode!r} is not recognised (expected 'docker' or "
            f"'local'). Refusing to run: an unknown mode would fall through to "
            f"UNISOLATED host execution."
        )
    if mode == "docker":
        cmd_for_container = _translate_paths_to_container(command, root)
        image = _DOCKER_SANDBOX_IMAGE
        docker_args = [
            "docker", "run", "--rm",
            "--network=none",
            "--memory=2g",
            "--cpus=2",
            "--tmpfs", "/tmp",
            "-v", f"{root.resolve()}:/work",
        ]
        if _DOCKER_MASK_GIT:
            docker_args.extend(["--tmpfs", "/work/.git"])
        docker_args.extend([
            "-w", "/work",
            image,
            "sh", "-lc", cmd_for_container,
        ])

        try:
            proc = subprocess.run(
                docker_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "[error] command timed out (180s limit)"
        except FileNotFoundError:
            return "[error] docker CLI executable not found in PATH."
        except Exception as e:  # noqa: BLE001
            return f"[error] cannot run docker container: {e}"

        out = (proc.stdout or "") + (proc.stderr or "")

        # Detect docker daemon connection failure
        daemon_markers = (
            "error during connect",
            "cannot connect to the docker daemon",
            "dockerdesktoplinuxengine",
            "is the docker daemon running",
        )
        if proc.returncode != 0 and any(m in out.lower() for m in daemon_markers):
            return f"[error] Docker daemon is not running or unreachable.\n{out}"

        # Detect missing image failure
        if proc.returncode != 0 and (
            f"unable to find image '{image}' locally" in out.lower()
            or f"repository {image} not found" in out.lower()
        ):
            return f"[error] Docker image '{image}' not found locally. Preflight requires image to be pre-built."

    else:
        # Local execution mode (default / legacy)
        # Inject sandbox sitecustomize via PYTHONPATH so Python processes have
        # outbound socket/network calls blocked at runtime.
        env = os.environ.copy()
        if _SANDBOX_DIR.exists():
            cur_py_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                str(_SANDBOX_DIR) + (os.pathsep + cur_py_path if cur_py_path else "")
            )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(root),
                env=env,
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

    if "[blocked]" not in out and _looks_like_env_failure(out):
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

    Uses POSIX python when running inside Docker container; otherwise uses the
    project interpreter (``sys.executable``) so the venv's packages are visible.
    Layout is detected rather than assumed: Django and sympy ship a
    ``tests/runtests.py`` runner, most other repos use pytest.
    """
    mode = get_sandbox_mode()
    if mode == "docker":
        py = "python"
        qpy = "python"
    else:
        import sys
        py = sys.executable or "python"
        qpy = f'"{py}"'

    if (root / "tests" / "runtests.py").exists():
        # Django-style runner; the repo root must be importable.
        return f"cd tests && {qpy} runtests.py"
    if (root / "pytest.ini").exists() or (root / "setup.cfg").exists() or (root / "tox.ini").exists():
        return f"{qpy} -m pytest -x -q"
    if (root / "tests").is_dir():
        return f"{qpy} -m pytest tests -x -q"
    return f"{qpy} -m pytest -x -q"


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
    """Discard all working-tree changes (clean slate before a new attempt).

    ``-x`` is deliberate: without it this tool reported "[ok] repository reset"
    while leaving every .gitignore'd file in place. That made it a FALSE clean
    slate — the agent's own ``.pyc`` (recoverable with ``marshal.load``) and
    pytest's ``.pytest_cache`` (which records which tests failed) both survived,
    so a later strategy inherited them. See reset_working_tree for the full case.
    """
    try:
        proc = _run_git("checkout", "--", ".")
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            return f"[error] git checkout failed: {err}"
        proc2 = _run_git("clean", "-fdx", "--", ".")
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
    """Run git in `root`, with a NEUTRAL identity.

    The identity matters because `git reset` writes a reflog entry attributed to
    the committer, and git takes that from the ambient config. Measured: our own
    `reset_working_tree` stamped the operator's real name and email
    (``agirahman <agi.rahman.s@gmail.com>``) into a dataset checkout's reflog.

    Setting a neutral identity here keeps that PII out of the dataset at the
    source. It affects only reflog attribution: the commands this helper runs
    (reset/checkout/clean/status/add/diff/cat-file) do not otherwise use an
    identity, and the agent's own shell commands do not go through here.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "agentbench",
        "GIT_AUTHOR_EMAIL": "agentbench@localhost",
        "GIT_COMMITTER_NAME": "agentbench",
        "GIT_COMMITTER_EMAIL": "agentbench@localhost",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        timeout=timeout,
        env=env,
    )


# Matches a per-file section header in a unified diff, capturing the b/ path.
_DIFF_GIT_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")

# Files an agent creates to CHECK its own work, then forgets to delete. They are
# never part of a fix, but ``git diff`` captures them because the agent wrote them
# inside the repo it is patching.
#
# Measured, across three separate experiments, always on the ``direct`` arm (which
# has one act and the whole 200-turn pool, so it iterates most):
#   EXP-20260930-030  django__django-11019  _check_merge.py
#   EXP-20260930-215  django__django-11019  _verify_merge.py
#   EXP-20261001-765  django__django-11001  _tmp_check.py
#
# The last one shipped as an empty file in a submitted patch. It still applied
# (the file was new, so the hunk was trivial), but an unrelated empty file in a
# patch is noise a reviewer has to explain, and each one is another chance for
# ``git apply`` to fail across 150 runs.
#
# The patterns require an explicit throwaway prefix, NOT merely a leading
# underscore. A first attempt used ``^_.*\.py$`` and matched the basename
# ``__init__.py`` -- i.e. it deleted ``django/db/models/fields/__init__.py`` from
# 2 of the 15 pilot patches. Dunder files are load-bearing everywhere in Python,
# so the rule must name what it is removing.
_SCRATCH_FILE_PATTERNS = (
    re.compile(r"^_tmp[_A-Za-z0-9]*\.py$"),      # _tmp_check.py
    re.compile(r"^_verify[_A-Za-z0-9]*\.py$"),   # _verify_merge.py
    re.compile(r"^_check[_A-Za-z0-9]*\.py$"),    # _check_merge.py
    re.compile(r"^_run[_A-Za-z0-9]*\.py$"),      # _run_media_tests.py
    re.compile(r"^_out\.(txt|log)$"),            # _out.txt
    re.compile(r"^tmp_[A-Za-z0-9_]*\.py$"),
    re.compile(r"^scratch[_A-Za-z0-9]*\.py$"),
)

# Names that must NEVER be treated as scratch, whatever the patterns say. Guards
# against exactly the class of mistake described above.
_NEVER_SCRATCH = ("__init__.py", "__main__.py", "__about__.py")


def _is_scratch_file(path: str) -> bool:
    """True when *path* looks like an agent's throwaway check script.

    Basename-only, and the name must carry an explicit throwaway prefix (``_tmp``,
    ``_verify``, ``_check``, ``_run``, ``_out``, ``tmp_``, ``scratch``). Python
    dunders are excluded outright: ``__init__.py`` is a real module, and an
    earlier ``^_.*`` rule deleted it from two patches.
    """
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    if base in _NEVER_SCRATCH:
        return False
    return any(p.match(base) for p in _SCRATCH_FILE_PATTERNS)


def drop_scratch_files(patch: str) -> tuple[str, list[str]]:
    """Remove agent scratch-file sections from *patch*.

    Returns ``(filtered_patch, dropped_paths)``. Splits on ``diff --git`` so a
    multi-file patch is handled; a patch containing ONLY scratch files yields an
    empty string.

    This exists so the submitted patch contains the fix and nothing else. It is a
    deterministic pipeline filter, applied identically to all three strategies,
    rather than a prompt instruction -- changing the prompt would change the
    experiment's conditions mid-study.
    """
    if not patch:
        return patch, []

    chunks: list[tuple[str, list[str]]] = []
    current_path: str | None = None
    current: list[str] = []
    for line in patch.splitlines(keepends=True):
        m = _DIFF_GIT_HEADER_RE.match(line.rstrip("\n"))
        if m:
            if current:
                chunks.append((current_path or "", current))
            current_path = m.group(2)
            current = [line]
        else:
            if not current and line.strip() == "":
                continue
            current.append(line)
    if current:
        chunks.append((current_path or "", current))

    kept: list[str] = []
    dropped: list[str] = []
    for path, lines in chunks:
        if _is_scratch_file(path):
            dropped.append(path)
        else:
            kept.extend(lines)
    return "".join(kept), dropped


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

    Agent scratch files (``_tmp_check.py`` and friends) are dropped here, at the
    single point where the authoritative patch is produced, so every consumer --
    the savepoints, the CSV, the submitted patch -- sees the same clean text.
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
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out, dropped = drop_scratch_files(out)
    if dropped:
        _warn(
            f"[capture_diff] dropped {len(dropped)} agent scratch file(s) from the "
            f"patch: {', '.join(dropped)}"
        )
    return out


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

    # Detect a `.git` write from the act that just finished. The baseline was
    # recorded at the END of the previous reset, when `.git` was known good, so
    # anything that changed since was written by the strategy in between.
    #
    # This comparison MUST happen before the reset commands run: `git reset`
    # rewrites `index`/`ORIG_HEAD`, which would erase the evidence.
    git_dir = root / _GIT_DIR_NAME
    baseline = _GIT_DIR_BASELINES.get(str(root))
    if git_dir.is_dir() and baseline is not None:
        tampered = _git_dir_modified(git_dir, baseline)
        if tampered:
            _warn(
                f"[reset_working_tree] {git_dir} was MODIFIED during the previous "
                f"act: {tampered[:5]}. These names are legitimate, but their CONTENT "
                f"is not — anything written into `.git` survives every reset and is "
                f"readable by the next strategy. The run should not be trusted."
            )
            return False

        # Every reflog is excluded from the hash comparison (git appends to them on
        # commit, checkout, reset, stash, merge), so verify them structurally:
        # the previous text must survive as a prefix and appended lines must be
        # valid entries. Without this, excluding them would leave a hole.
        prev_reflogs = baseline.get(_RELOG_KEY) or {}
        if isinstance(prev_reflogs, dict):
            current_reflogs = _reflogs_of(git_dir)
            for rel, prev_text in prev_reflogs.items():
                ok, why = _reflog_append_is_clean(prev_text, current_reflogs.get(rel, ""))
                if not ok:
                    _warn(
                        f"[reset_working_tree] {git_dir / rel} is not a clean "
                        f"superset of its previous content: {why}. Reflogs are "
                        f"excluded from the hash check because git appends to them "
                        f"legitimately, so this is how a marker written there is "
                        f"caught. The run should not be trusted."
                    )
                    return False

        # `config` is also excluded from hashing (git config set / remote add /
        # submodule add rewrite it), so its EDIT is verified against the previous
        # content. A shape check alone is not enough: a comment is valid INI, so an
        # agent could hide a marker in one (measured: attack B4).
        prev_config = baseline.get(_CONFIG_KEY)
        if isinstance(prev_config, str):
            config_path = git_dir / "config"
            try:
                current_config = (
                    config_path.read_text(encoding="utf-8", errors="replace")
                    if config_path.is_file() else ""
                )
            except OSError:
                current_config = ""
            ok, why = _config_edit_is_clean(prev_config, current_config)
            if not ok:
                _warn(
                    f"[reset_working_tree] {config_path} was not edited like a git "
                    f"config: {why}. config is excluded from the hash check because "
                    f"git rewrites it legitimately, so this is how a marker written "
                    f"there is caught. The run should not be trusted."
                )
                return False

        # Shape checks for the files excluded from hashing. This is what keeps the
        # exclusions from re-opening the hole: a ref that does not hold a valid SHA,
        # a malformed packed-refs line, a reflog line that is not an entry, or an
        # ORIG_HEAD that does not resolve are all reported here.
        shape_problems = _structural_problems(git_dir)
        if shape_problems:
            _warn(
                f"[reset_working_tree] {git_dir} holds {len(shape_problems)} "
                f"malformed ref/reflog/message file(s): {shape_problems[:3]}. These "
                f"paths are excluded from the hash comparison because git rewrites "
                f"them in normal use, so their SHAPE is checked instead. The run "
                f"should not be trusted."
            )
            return False

    try:
        _git_in(root, "reset", "-q")            # drop intent-to-add marks
        _git_in(root, "checkout", "--", ".")
        # -x removes .gitignore'd leftovers. Without it the reset claimed success
        # while leaving exactly the files that leak between strategies:
        #
        #   * `__pycache__/*.pyc` — an agent's compiled module survives the reset
        #     and a later strategy can recover its code objects with marshal.load
        #     (names and constants survive; only comments are lost).
        #   * `.pytest_cache/v/cache/lastfailed` — pytest writes this from INSIDE
        #     the container to the bind-mounted host checkout. It records WHICH
        #     tests failed, and for SWE-bench that is the bug location
        #     (FAIL_TO_PASS). A later strategy does not even have to read it:
        #     `pytest --lf` consults it automatically and re-runs exactly the
        #     tests the previous strategy discovered.
        #
        # Both are git-IGNORED, so plain `clean -fd` never touched them.
        _git_in(root, "clean", "-fdxq", "--", ".")
    except Exception as e:  # noqa: BLE001
        _warn(f"[reset_working_tree] command failed for {root}: {e}")
        return False

    # Verify, because a git command can fail without raising.
    #
    # --ignored is REQUIRED: plain `status --porcelain` hides every .gitignore'd
    # path, so it shared the exact blindness of the operation it was checking and
    # returned True ("verified clean") with __pycache__ and .pytest_cache still
    # on disk.
    try:
        status = _git_in(root, "status", "--porcelain", "--ignored")
        leftover = (status.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:  # noqa: BLE001
        _warn(f"[reset_working_tree] could not verify {root}: {e}")
        return False

    if leftover:
        paths = [l for l in leftover.splitlines() if l.strip()][:5]

        # Distinguish an UNSUPPORTED submodule from ordinary dirt. `git clean`
        # refuses to delete a nested repository, so a submodule created by the
        # agent cannot be removed by the reset — and neither `clean -fdxq` nor
        # `clean -ffdx` removes it (measured). The run genuinely cannot continue
        # with a clean tree, but "STILL DIRTY ... the run should not be trusted"
        # would misreport WHY, and an operator would go looking for a leak.
        #
        # This is a documented LIMITATION, not a hole: no `.git` guard is relaxed,
        # and a submodule's metadata is still shape-checked. Measured relevance:
        # 0 of the 53 checkouts contain a .gitmodules file, and SWE-bench agents
        # fix code rather than adding submodules, so this path is not expected to
        # trigger in a sweep.
        nested = _nested_repos_in_tree(root)
        if nested:
            _warn(
                f"[reset_working_tree] {root} contains a nested git repository "
                f"({nested[:3]}) that `git clean` refuses to remove, so the tree "
                f"cannot be returned to a pristine state. SUBMODULE/NESTED-REPO "
                f"OPERATIONS ARE NOT SUPPORTED in this sandbox. This is a known "
                f"limitation, not detected tampering: no .git guard was relaxed. "
                f"Refusing the run rather than diffing against a dirty tree."
            )
            return False

        _warn(
            f"[reset_working_tree] {root} is STILL DIRTY after reset "
            f"({len(leftover.splitlines())} path(s)): {paths}. The captured diff "
            f"would include these, so the run should not be trusted."
        )
        return False

    # Git cannot see everything that leaks between strategies: it never reports
    # the contents of its own .git directory (so a marker written there survives
    # every reset), and it does not report DANGLING junctions (measured: a
    # junction whose target was deleted is absent from `status --ignored` even
    # though the link is still on disk and still readable).
    links = [p for p in _iter_reparse_points(root)]
    if links:
        names = [str(p.relative_to(root)) for p in links][:5]
        _warn(
            f"[reset_working_tree] {root} still holds {len(links)} symlink/junction "
            f"path(s) after reset: {names}. A link can point outside the checkout, "
            f"so the run should not be trusted."
        )
        return False

    # `.git` needs checks git itself cannot provide: `git clean` never removes
    # anything inside it and `git status` never reports it.
    #
    # A NAME allowlist is NOT enough, and a measured exploit proved it: writing
    # `STRATEGY_A_WAS_HERE` into `.git/gc.log` (or `.git/ORIG_HEAD`) passed,
    # because those names are legitimate. Forgiving a name forgives OVERWRITING
    # it. So this records a CONTENT fingerprint to compare against next time.
    #
    # Hashing every file would produce false positives: git's own reset cycle
    # legitimately rewrites `index` and `logs/HEAD`, and creates `ORIG_HEAD`
    # (measured over 4 cycles). Those are excluded and verified structurally.
    if git_dir.is_dir():
        try:
            unexpected = sorted(
                p.name for p in git_dir.iterdir() if p.name not in _GIT_DIR_ALLOWED
            )
        except OSError as e:
            _warn(f"[reset_working_tree] could not inspect {git_dir}: {e}")
            return False
        if unexpected:
            _warn(
                f"[reset_working_tree] {git_dir} holds unexpected entr(ies) after "
                f"reset: {unexpected[:5]}. `.git` is not cleaned by git and not "
                f"reported by git status, so this is an undetected inter-strategy "
                f"channel. The run should not be trusted."
            )
            return False

        # A poisoned index is NOT rebuilt by reset/checkout/clean (measured), and
        # the failure is silent because `_git_in` does not raise: `git status`
        # returns non-zero with EMPTY stdout, which reads as a clean tree.
        index_check = _git_in(root, "status", "--porcelain")
        if index_check.returncode != 0:
            err = (index_check.stderr or b"").decode("utf-8", "replace").strip()
            _warn(
                f"[reset_working_tree] `git status` FAILED in {root} after reset "
                f"(rc={index_check.returncode}): {err[:160]}. A failed status returns "
                f"empty output, which would otherwise be read as a clean tree. The "
                f"index may be poisoned. The run should not be trusted."
            )
            return False

        # Post-reset shape check. ORIG_HEAD is not compared to HEAD any more: git
        # legitimately points it at the pre-reset HEAD, so that comparison was
        # wrong in general AND missed a tampered value (measured, attack B3).
        # What must hold is that it resolves to a real git object.
        post_problems = _structural_problems(git_dir)
        if post_problems:
            _warn(
                f"[reset_working_tree] {root} has {len(post_problems)} malformed "
                f"ref/reflog/message file(s) after reset: {post_problems[:3]}. The "
                f"run should not be trusted."
            )
            return False

        # Record the post-reset fingerprint, which is the baseline the NEXT call
        # compares against.
        _GIT_DIR_BASELINES[str(root)] = _git_dir_manifest(git_dir)

    return True


#: Per-checkout fingerprint of the hash-compared files under `.git`, recorded at
#: the end of each successful reset and compared at the start of the next one.
#: Keyed by the repo root so several checkouts can be reset in one process. The
#: ``_RELOG_KEY`` entry holds a nested dict of reflog texts, not a hash.
#:
#: Why not compare within a single call: the reset commands themselves rewrite
#: `.git`, so a snapshot taken after them equals the state they produced and the
#: comparison is vacuous. The evidence of an act's write only exists BETWEEN
#: calls, which is exactly when the next reset looks for it.
_GIT_DIR_BASELINES: dict[str, dict] = {}


#: Entries a normal git checkout has at the top level of `.git`. Measured across
#: all 53 local checkouts: these 12 occur and nothing else does. The remaining
#: names are standard git internals that appear as soon as anyone commits or
#: merges in the checkout (a plain `git init && git commit` creates
#: COMMIT_EDITMSG), so they are listed to avoid refusing a legitimate repo.
#:
#: This list only decides which NAMES are structurally acceptable. It is NOT a
#: safety check on its own: forgiving a name forgives OVERWRITING it, and a
#: measured exploit wrote a marker into `.git/gc.log` (an allowlisted name) and
#: passed this check. `_git_dir_modified` supplies the content check.
_GIT_DIR_ALLOWED = frozenset({
    # present in the measured checkouts
    "HEAD", "ORIG_HEAD", "config", "description", "hooks", "index", "info",
    "logs", "objects", "packed-refs", "refs", "shallow",
    # standard internals created by normal git use
    "COMMIT_EDITMSG", "FETCH_HEAD", "MERGE_HEAD", "MERGE_MSG", "MERGE_MODE",
    "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "BISECT_START",
    "branches", "worktrees", "modules", "rr-cache", "svn", "sequencer",
    "rebase-apply", "rebase-merge", "auto-merge", "gc.log", "locked",
})

#: Files under `.git` that git rewrites or creates as part of NORMAL use, and
#: which therefore CANNOT be hash-compared. Measured over the 10 legitimate
#: operations an agent may run (commit, tag, checkout -b, stash, merge, gc,
#: reset --hard, add, capture_diff, checkout <branch>):
#:
#:   index            -> rewritten by `git add`, `capture_diff`, every reset
#:   logs/HEAD        -> reflog, appended on commit/checkout/reset
#:   ORIG_HEAD        -> created/rewritten by `git reset`
#:
#: Every one is verified STRUCTURALLY instead of being ignored (see
#: _git_dir_structural_problems), because simply excluding a name would let an
#: agent write there undetected.
_GIT_DIR_VOLATILE = frozenset({"index", "logs/HEAD", "ORIG_HEAD"})

#: Path prefixes whose files are refs, branch reflogs, or git-written messages.
#: Files matching these are verified structurally rather than by hash: git
#: rewrites them during legitimate work (commit/tag/branch/stash/merge/gc), so
#: hashing them produces false positives that STOP the run — measured on 7 of the
#: 10 legitimate operations before this change.
_GIT_REF_PREFIXES = ("refs/", "logs/refs/", "worktrees/", "modules/")
#: Exact `.git` paths that git rewrites during NORMAL use, and which are therefore
#: shape-checked rather than hash-compared. Deliberately MINIMAL: a file belongs
#: here only if a measured legitimate operation changes it.
#:
#: Measured writes (advanced operations an agent really runs):
#:   rebase / revert            -> AUTO_MERGE (a 40-hex tree/commit id)
#:   rebase / cherry-pick       -> COMMIT_EDITMSG, ORIG_HEAD, refs/**, logs/**
#:   config set / submodule add -> config
#:   worktree add               -> worktrees/<name>/** (a nested git dir)
#:   submodule add              -> modules/<name>/** (a nested git dir)
#:
#: `gc.log` is NOT here even though it is a legitimate git file — `git gc` does not
#: rewrite it in these checkouts, and listing it would let an agent overwrite it
#: undetected (measured: that was attack B2).
_GIT_REF_EXACT = frozenset({
    "HEAD", "ORIG_HEAD", "packed-refs", "info/refs", "config",
    # written by commit / merge / cherry-pick / rebase, which agents may run
    "COMMIT_EDITMSG", "MERGE_MSG", "MERGE_HEAD", "MERGE_MODE", "AUTO_MERGE",
    "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "BISECT_START",
    "FETCH_HEAD",
})

#: A 40-hex object name. Used to validate refs, ORIG_HEAD, and reflog SHAs.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

#: A git config file is INI-shaped. Measured against a real `.git/config` written
#: by `git config user.name` and `git config --add remote.origin.url`:
#:   [core]                              -> section
#:   \trepositoryformatversion = 0       -> TAB-indented key/value
#:   [remote "origin"]                   -> section with a subsection
#:   ; comment                           -> comment
#: NOTE the tab: an earlier version of this regex required the key to start at
#: column 0 and rejected every real line.
_RE_CONFIG_SECTION = re.compile(r'^\[[^\]\n]+\]$')
_RE_CONFIG_KV = re.compile(r'^\s+[A-Za-z][\w.-]*\s*=\s*.*$')
_RE_CONFIG_COMMENT = re.compile(r'^\s*[#;].*$')

#: A file written by git that holds a single 40-hex object name (ORIG_HEAD,
#: AUTO_MERGE, worktree HEAD in detached state, MERGE_HEAD, ...).
_GIT_SINGLE_SHA_FILES = frozenset({
    "ORIG_HEAD", "AUTO_MERGE", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
    "BISECT_START",
})

#: A ref file holds either a 40-hex SHA (detached/loose ref) or a symbolic
#: ``ref: <path>`` pointer. Anything else is not a ref.
_RE_REF_SYMBOLIC = re.compile(r"^ref: \S+$")
#: `packed-refs`: comments, peeled lines (^sha), or "<sha> <refname>".
_RE_PACKED_REF_LINE = re.compile(r"^(#.*|\^[0-9a-f]{40}|[0-9a-f]{40} \S+)$")
#: `info/refs`: "<sha>\t<refname>".
_RE_INFO_REF_LINE = re.compile(r"^[0-9a-f]{40}\s+\S+$")

#: One reflog line: <old-sha> <new-sha> <name> <email> <unix-ts> <tz>\t<message>.
#: The SHAs are all-zeros for the initial commit. Used to tell a real reflog
#: append from a marker an agent wrote into the file.
#:
#: The trailing ``\t<message>`` is OPTIONAL: a worktree's reflog is written without
#: it (measured: `worktrees/<name>/logs/HEAD` holds
#: ``0000... ec7b351... t <t@t> 1791291510 +0700`` and nothing else). Requiring the
#: tab made a legitimate `git worktree add` look like a malformed reflog.
_RE_RELOG_LINE = re.compile(
    r"^[0-9a-f]{40} [0-9a-f]{40} .+ <[^>]*> \d{9,} [-+]\d{4}(\t.*)?$"
)


def _config_edit_is_clean(previous: str, current: str) -> tuple[bool, str]:
    """Check that `current` is `previous` plus well-formed INI edits.

    `config` cannot be hash-compared (`git config set`, `git remote add` and
    `git submodule add` all rewrite it), so it is compared against the PREVIOUS
    content, the same way reflogs are. Measured mutations of a real config:

      * `config user.name`     -> adds a `[user]` section and a key line
      * `config --add`         -> adds a section and a key line
      * `config --unset`       -> REMOVES a line
      * `config --replace-all` -> replaces one key line
      * `remote add`           -> adds a section plus key lines
      * `submodule add`        -> adds a section plus key lines

    So the rule is LINE-based, not prefix-based (a removal breaks a prefix rule).

    Comments are deliberately REJECTED as additions. Measured: 0 of 53 real
    checkouts have a comment in `.git/config`, and no git command above writes one.
    Allowing them let a marker through — that was the measured attack B4, which a
    comment-permitting rule accepted while the hash check no longer applied.

    ============================ ACCEPTED TRADE-OFF ============================
    WHAT IS ACCEPTED, AND WHY. A hand-written INI edit with a well-formed shape is
    accepted. Measured: `git config user.name STRATEGY_A_WAS_HERE` and the
    hand-written equivalent produce BYTE-IDENTICAL configs, so no structural rule
    can accept the legitimate command while rejecting the written one. This is what
    makes `git config set` pass (MATRIX2 D11) instead of aborting a real run.

    THE CONSEQUENCE, MEASURED. Two adversarial channels therefore pass:
      * C5 — a marker hidden in a well-formed INI edit;
      * C7 — a marker used as a remote NAME.
    Both were detected before this rule and are not now. The adversarial harness
    moved from 4 detected / 7 missed to 2 detected / 9 missed, and that regression
    is deliberate rather than unnoticed.

    WHY THAT IS ACCEPTABLE. Both channels require a path the sweep never uses:
      * C7 needs to RUN git, and git is absent from the sandbox image —
        measured: `run_tests("git config user.name X")` returns
        `[exit code 127] sh: 1: git: not found`.
      * C5 needs to WRITE `.git/config`, and `/work/.git` is a tmpfs — measured:
        a container write is visible inside the container and leaves the host
        config byte-identical.
      * The host tool layer refuses `.git/config` for read, write AND edit.
      * The sweep is pinned to docker (`run_final_sweep.py`: SANDBOX_MODE=docker),
        and `get_sandbox_mode()` defaults to docker when unset.
    So C5/C7 are open only in `local` mode, which no sweep uses and which is
    already documented as unsafe.

    THE PRIMARY DEFENCE IS CONTAINER ISOLATION, NOT THIS DETECTOR. Treat this
    check as a backstop for the paths that CAN reach `.git`; the reason a sweep is
    safe is that git is absent and `.git` is masked.

    WHY NOT OPTION C (a stricter rule). Rejecting `[remote "..."]` additions would
    close C7 but break a legitimate `git remote add`, which the advanced-operations
    test supports and which `git fetch`/`git pull` depend on. The only remaining
    discriminator would be pattern-matching marker-like names, i.e. a heuristic that
    false-positives on unusual-but-legitimate names — reintroducing the class of bug
    this rule exists to fix.
    """
    prev_lines = [l for l in previous.splitlines() if l.strip()]
    cur_lines = [l for l in current.splitlines() if l.strip()]

    added = [l for l in cur_lines if l not in prev_lines]
    removed = [l for l in prev_lines if l not in cur_lines]

    for line in added:
        if _RE_CONFIG_COMMENT.match(line):
            return False, (
                f"a comment line was ADDED to config: {line[:40]!r}. No git command "
                f"writes comments here, so this is how a marker is hidden."
            )
        if not (_RE_CONFIG_SECTION.match(line) or _RE_CONFIG_KV.match(line)):
            return False, f"added line is not valid INI syntax: {line[:50]!r}"
    for line in removed:
        if not (_RE_CONFIG_SECTION.match(line) or _RE_CONFIG_KV.match(line)
                or _RE_CONFIG_COMMENT.match(line)):
            return False, f"a non-INI line disappeared from config: {line[:50]!r}"
    return True, ""


def _reflog_append_is_clean(previous: str, current: str) -> tuple[bool, str]:
    """Check that `current` is `previous` plus well-formed reflog entries.

    `logs/HEAD` cannot be hash-compared (git appends to it on commit, checkout,
    and every reset), so it is excluded from the fingerprint — which would leave
    a hole: an agent could APPEND a marker and strategy B could read it. Three
    rules are needed, and each was measured against a real reflog:

    1. the previous text must survive as a PREFIX (catches a wholesale rewrite);
    2. every appended line must match the reflog FORMAT (catches a bare marker);
    3. the SHAs must form a CHAIN and the last one must equal HEAD (catches a
       well-formed forgery, which rule 2 alone accepts — measured: appending
       ``<40 zeros> <40 ones> ... LEAK_MARKER_99`` passed rules 1-2).
    """
    if not current.startswith(previous):
        return False, "the previous reflog content was not preserved as a prefix"
    appended = current[len(previous):]
    for i, line in enumerate(appended.splitlines(), 1):
        if not line.strip():
            continue
        if not _RE_RELOG_LINE.match(line):
            return False, f"appended line {i} is not a valid reflog entry: {line[:60]!r}"
    return True, ""


def _reflog_chain_is_intact(text: str) -> tuple[bool, str]:
    """Verify the reflog is a SHA chain whose tip is the current HEAD.

    Measured to hold for every real shape: fresh init, 1/3 commits,
    commit+checkout -b, commit --amend, checkout -, reset --hard. A forgery that
    continues the chain still fails the tip rule unless it also rewrites HEAD.
    """
    shas: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        if len(parts) < 2 or len(parts[0]) != 40 or len(parts[1]) != 40:
            return False, f"malformed reflog line: {line[:50]!r}"
        shas.append((parts[0], parts[1]))
    for i in range(len(shas) - 1):
        if shas[i][1] != shas[i + 1][0]:
            return False, (
                f"reflog chain broken between line {i + 1} and {i + 2}: "
                f"{shas[i][1][:12]} -> {shas[i + 1][0][:12]}"
            )
    return True, ""


#: Key under which the previous reflog TEXTS are stored in the baseline dict. The
#: value is a dict {relative path -> text}: every reflog (HEAD and all branch
#: reflogs) is verified by prefix+format, because git appends to them during
#: normal commit/checkout/reset/stash/merge work.
_RELOG_KEY = "<reflogs>"

#: Key under which the previous `config` TEXT is stored in the baseline dict. Not
#: a hash: git rewrites config during legitimate work, so the edit is compared
#: against the previous content instead (see _config_edit_is_clean).
_CONFIG_KEY = "<config>"


def _is_ref_like(rel: str) -> bool:
    """True when this `.git` path is a ref, reflog, or git-written message.

    These are excluded from hash comparison and verified structurally instead.
    """
    return rel in _GIT_REF_EXACT or rel.startswith(_GIT_REF_PREFIXES)


def _structural_problems(git_dir: Path) -> list[str]:
    """Verify every ref/reflog/message file has a VALID SHAPE.

    Structural verification is what makes it safe to exclude these files from the
    hash comparison. Merely excluding them by name would re-open the hole: an
    agent could write anything into `refs/heads/main` and go unnoticed. Measured
    against 7 attack shapes, each of which is caught here.
    """
    problems: list[str] = []

    def rel_of(p: Path) -> str:
        return str(p.relative_to(git_dir)).replace("\\", "/")

    def read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # --- loose refs: a 40-hex SHA, or a `ref: <path>` pointer -----------------
    refs_root = git_dir / "refs"
    if refs_root.is_dir():
        for p in sorted(refs_root.rglob("*")):
            if not p.is_file():
                continue
            content = read(p).strip()
            if not (_SHA40_RE.match(content) or _RE_REF_SYMBOLIC.match(content)):
                problems.append(
                    f"{rel_of(p)} is not a valid ref (expected a 40-hex commit SHA "
                    f"or 'ref: <path>', got {content[:40]!r})"
                )

    # --- HEAD: symbolic ref or detached SHA ----------------------------------
    head = git_dir / "HEAD"
    if head.is_file():
        content = read(head).strip()
        if not (_SHA40_RE.match(content) or _RE_REF_SYMBOLIC.match(content)):
            problems.append(
                f"HEAD is not a valid ref (got {content[:40]!r})"
            )

    # --- packed-refs / info/refs --------------------------------------------
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for i, line in enumerate(read(packed).splitlines(), 1):
            if line.strip() and not _RE_PACKED_REF_LINE.match(line):
                problems.append(f"packed-refs line {i} is malformed: {line[:50]!r}")
    info_refs = git_dir / "info" / "refs"
    if info_refs.is_file():
        for i, line in enumerate(read(info_refs).splitlines(), 1):
            if line.strip() and not _RE_INFO_REF_LINE.match(line):
                problems.append(f"info/refs line {i} is malformed: {line[:50]!r}")

    # --- every reflog: valid entry format ------------------------------------
    logs_root = git_dir / "logs"
    if logs_root.is_dir():
        for p in sorted(logs_root.rglob("*")):
            if not p.is_file():
                continue
            for i, line in enumerate(read(p).splitlines(), 1):
                if not line.strip():
                    continue
                if not _RE_RELOG_LINE.match(line):
                    problems.append(
                        f"{rel_of(p)} line {i} is not a valid reflog entry: "
                        f"{line[:50]!r}"
                    )

    # --- ORIG_HEAD: must be a SHA that git can actually resolve --------------
    # Excluded from hashing (git rewrites it on reset), so verified here. A
    # tampered ORIG_HEAD previously went undetected (measured, attack B3): the
    # old check only compared it to HEAD, which a valid-looking SHA can pass.
    orig_head = git_dir / "ORIG_HEAD"
    if orig_head.is_file():
        content = read(orig_head).strip()
        if not _SHA40_RE.match(content):
            problems.append(
                f"ORIG_HEAD is not a 40-hex SHA: {content[:40]!r}"
            )
        else:
            probe = _git_in(git_dir.parent, "cat-file", "-e", content)
            if probe.returncode != 0:
                problems.append(
                    f"ORIG_HEAD ({content[:12]}) does not resolve to a git object"
                )

    # --- other single-SHA files (AUTO_MERGE, MERGE_HEAD, ...) ----------------
    # Written by rebase/revert/merge, which agents legitimately run. Shape-checked
    # so a marker cannot be parked here.
    for name in sorted(_GIT_SINGLE_SHA_FILES - {"ORIG_HEAD"}):
        f = git_dir / name
        if not f.is_file():
            continue
        content = read(f).strip()
        # MERGE_HEAD may list several SHAs (an octopus merge); accept 1..N.
        parts = content.split()
        if not parts or not all(_SHA40_RE.match(p) for p in parts):
            problems.append(
                f"{name} is not one or more 40-hex SHAs: {content[:40]!r}"
            )

    # --- config: must be INI-shaped -----------------------------------------
    # `git config set` and `git submodule add` both rewrite it, so it cannot be
    # hash-compared. Two checks, both needed:
    #   1. every line must be INI-shaped (catches a marker in place of a key);
    #   2. the edit is compared against the previous content
    #      (_config_edit_is_clean), because a COMMENT is valid INI syntax and would
    #      otherwise let an agent hide a marker (measured: that was attack B4).
    config = git_dir / "config"
    if config.is_file():
        for i, line in enumerate(read(config).splitlines(), 1):
            if not line.strip():
                continue
            if (_RE_CONFIG_SECTION.match(line) or _RE_CONFIG_KV.match(line)
                    or _RE_CONFIG_COMMENT.match(line)):
                continue
            problems.append(
                f"config line {i} is not valid INI syntax: {line[:50]!r}"
            )

    # --- nested git dirs: worktrees/<name> and modules/<name> ---------------
    # `git worktree add` and `git submodule add` create a full nested git dir, so
    # its contents cannot be hash-compared. Each one is checked with the SAME
    # rules by recursing, so a marker inside a worktree ref/reflog is still caught.
    for container in ("worktrees", "modules"):
        root = git_dir / container
        if not root.is_dir():
            continue
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            nested = _structural_problems(sub)
            for prob in nested:
                problems.append(f"{container}/{sub.name}/{prob}")
            # A worktree records where it lives; the pointer must be a relative
            # path or an absolute path, never an arbitrary marker.
            gitdir_file = sub / "gitdir"
            if gitdir_file.is_file():
                pointer = read(gitdir_file).strip()
                if not pointer or "\n" in pointer:
                    problems.append(
                        f"{container}/{sub.name}/gitdir is not a single path: "
                        f"{pointer[:40]!r}"
                    )

    return problems


def _reflogs_of(git_dir: Path) -> dict[str, str]:
    """Return {relative path -> reflog text} for every reflog under `.git`."""
    out: dict[str, str] = {}
    logs_root = git_dir / "logs"
    if not logs_root.is_dir():
        return out
    for p in sorted(logs_root.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(git_dir)).replace("\\", "/")
        try:
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            out[rel] = ""
    return out

#: Objects are content-addressed and numerous; hashing them is wasted work and
#: a legitimately-run test cannot add one without also touching refs.
_GIT_DIR_SKIP_PREFIXES = ("objects/",)


def _git_dir_manifest(git_dir: Path) -> dict[str, str]:
    """Fingerprint the files under `.git` that git does NOT rewrite in normal use.

    Values are sha256 hex digests, except the ``_RELOG_KEY`` entry, whose value is
    a dict of reflog texts. Reflogs and refs are excluded from HASHING because git
    rewrites them during legitimate work (commit/tag/branch/stash/merge/gc) and
    hashing them stopped the run — but they are not ignored: every one is checked
    by `_structural_problems`, and reflogs additionally by prefix+format.
    """
    import hashlib

    out: dict[str, str] = {}
    try:
        candidates = list(git_dir.rglob("*"))
    except OSError:
        return out
    for p in candidates:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        rel = str(p.relative_to(git_dir)).replace("\\", "/")
        if rel in _GIT_DIR_VOLATILE or rel.startswith(_GIT_DIR_SKIP_PREFIXES):
            continue
        if _is_ref_like(rel):
            # Verified structurally, not by hash (see _structural_problems).
            continue
        try:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue

    out[_RELOG_KEY] = _reflogs_of(git_dir)  # type: ignore[assignment]
    # `config` is verified as an EDIT, not a hash, so store its text.
    config_path = git_dir / "config"
    try:
        out[_CONFIG_KEY] = (
            config_path.read_text(encoding="utf-8", errors="replace")
            if config_path.is_file() else ""
        )
    except OSError:
        out[_CONFIG_KEY] = ""
    return out


def _git_dir_modified(git_dir: Path, before: dict[str, str]) -> list[str]:
    """Return the hash-compared `.git` files whose content changed since `before`.

    Reflog, config and ref paths are skipped here: they are handled by
    `_structural_problems`, `_reflog_append_is_clean` and `_config_edit_is_clean`,
    because git rewrites them legitimately.
    """
    sentinels = (_RELOG_KEY, _CONFIG_KEY)
    after = _git_dir_manifest(git_dir)
    changed = []
    for rel, digest in before.items():
        if rel in sentinels:
            continue
        if rel not in after:
            changed.append(f"{rel} (removed)")
        elif after[rel] != digest:
            changed.append(rel)
    for rel in after:
        if rel in sentinels or rel in before:
            continue
        if _is_ref_like(rel) or rel in _GIT_DIR_VOLATILE:
            # A ref/branch created by legitimate work (checkout -b, tag, stash,
            # merge) is not tampering; its SHAPE is checked structurally.
            continue
        changed.append(f"{rel} (added)")
    return sorted(changed)


def _nested_repos_in_tree(root: Path, max_depth: int = 6) -> list[str]:
    """Return relative paths of nested git repositories inside `root`.

    A submodule (or any nested clone) puts a `.git` FILE at its root, so `git
    clean` treats the whole directory as a repository and refuses to delete it —
    measured with `-fdxq`, `-fdxq -f`, `-fdx -ff` and `-fdffx`: none removed it.
    Detected here only to produce an accurate error message.
    """
    found: list[str] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        if len(current.parts) - root_depth > max_depth:
            dirnames[:] = []
            continue
        # Do not descend into .git itself.
        dirnames[:] = [d for d in dirnames if d.lower() != _GIT_DIR_NAME]
        if current != root and ".git" in filenames:
            found.append(str(current.relative_to(root)).replace("\\", "/"))
            dirnames[:] = []      # no need to look inside a nested repo
    return sorted(found)


def _iter_reparse_points(root: Path):
    """Yield every symlink/junction under `root`, without following any of them.

    Uses os.walk(followlinks=False) + lstat, so the scan cannot itself be
    redirected out of the checkout by a link it is looking for. Mirrors
    tools/repo_link_scan.py, kept local so the agent-facing tool module has no
    dependency on the tools/ directory.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        keep = []
        for name in dirnames:
            candidate = current / name
            if _is_reparse_point(candidate):
                yield candidate
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            candidate = current / name
            if _is_reparse_point(candidate):
                yield candidate


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
