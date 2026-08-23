import re
import subprocess
from pathlib import Path

from config import Config
from models.issue import Issue
from utils.logger import logger

_HEX_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DOTTED_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_PY_PATH_RE = re.compile(r"\b[\w./-]+\.py\b")

_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was",
    "not", "but", "you", "have", "has", "its", "all", "will", "class",
    "def", "import", "return", "self", "True", "False", "None", "should",
}

_REPO_CACHE: dict[tuple[str, str], Path | None] = {}


def _is_hex_commit(value: str) -> bool:
    return bool(_HEX_COMMIT_RE.match(value))


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def get_repo_at_commit(repo: str, base_commit: str) -> Path | None:
    """Fetch base commit shallowly into cache. Returns repo root or None on failure."""
    key = (repo, base_commit)
    if key in _REPO_CACHE:
        return _REPO_CACHE[key]

    if not _is_hex_commit(base_commit):
        _REPO_CACHE[key] = None
        return None

    cache_root = Path(Config.REPO_CACHE_DIR)
    repo_dir = cache_root / repo / base_commit

    if (repo_dir / ".git").exists():
        _REPO_CACHE[key] = repo_dir
        return repo_dir

    try:
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q", str(repo_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", f"https://github.com/{repo}.git"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", base_commit],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "-q", "FETCH_HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        head = _git(repo_dir, "rev-parse", "HEAD")
        if head.strip() != base_commit:
            raise RuntimeError(f"HEAD {head.strip()} != base_commit {base_commit}")
        _REPO_CACHE[key] = repo_dir
        return repo_dir
    except Exception as e:
        logger.warning(f"Failed to fetch {repo}@{base_commit}: {e}")
        _REPO_CACHE[key] = None
        return None


def _list_py_files(repo_path: Path) -> list[str]:
    out = _git(repo_path, "ls-tree", "-r", "--name-only", "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


def _extract_terms(problem_statement: str) -> tuple[list[str], list[str], set[str]]:
    path_hits = [m for m in _PY_PATH_RE.findall(problem_statement)]
    base_hits = [Path(p).name for p in path_hits]

    identifiers: set[str] = set()
    for m in _DOTTED_RE.findall(problem_statement):
        identifiers.add(m)
        identifiers.add(m.split(".")[-1])
    for m in _IDENTIFIER_RE.findall(problem_statement):
        if len(m) >= 4 and m.lower() not in _STOPWORDS:
            identifiers.add(m)
    return path_hits, base_hits, identifiers


def _score_file(path: str, path_hits: list[str], base_hits: list[str], identifiers: set[str]) -> float:
    score = 0.0
    for hit in path_hits:
        if hit in path:
            score += 100.0
    for hit in base_hits:
        if Path(path).name == hit:
            score += 50.0
    return score


def select_relevant_files(
    repo_path: Path,
    problem_statement: str,
    max_files: int,
    max_file_lines: int,
) -> list[str]:
    path_hits, base_hits, identifiers = _extract_terms(problem_statement)
    files = _list_py_files(repo_path)

    scored: list[tuple[float, str]] = []
    for path in files:
        score = _score_file(path, path_hits, base_hits, identifiers)
        if score <= 0 and not identifiers:
            continue
        if identifiers:
            try:
                content = (repo_path / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            hits = sum(min(content.count(term), 3) for term in identifiers if len(term) >= 4)
            if hits:
                score += hits
        if score > 0:
            scored.append((score, path))

    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return [path for _, path in scored[:max_files]]


def _render_file(repo_path: Path, path: str, max_file_lines: int, budget: int) -> str | None:
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"### {path}\n(not readable)\n"
    lines = content.splitlines()
    total = len(lines)
    if total > max_file_lines:
        lines = lines[:max_file_lines]
    header = f"### {path}\n"
    body_budget = budget - len(header)
    if body_budget < 60:
        return None
    rendered = [f"{i + 1}: {line}" for i, line in enumerate(lines)]
    used = 0
    kept: list[str] = []
    for entry in rendered:
        if used + len(entry) + 1 > body_budget:
            break
        kept.append(entry)
        used += len(entry) + 1
    if len(kept) < len(rendered):
        while kept and used + len("... (truncated)\n") > body_budget:
            used -= len(kept.pop()) + 1
        kept.append("... (truncated)")
    body = "\n".join(kept)
    if total > len(rendered) and len(kept) == len(rendered):
        body += f"\n... ({total - len(lines)} more lines truncated)"
    return header + body + "\n"


def build_source_context(
    issue: Issue,
    max_chars: int | None = None,
    max_files: int | None = None,
    max_file_lines: int | None = None,
) -> str:
    max_chars = max_chars or Config.SOURCE_CONTEXT_MAX_CHARS
    max_files = max_files or Config.SOURCE_CONTEXT_MAX_FILES
    max_file_lines = max_file_lines or Config.SOURCE_CONTEXT_MAX_FILE_LINES

    repo_path = get_repo_at_commit(issue.repo, issue.base_commit)
    if repo_path is None:
        return ""

    selected = select_relevant_files(repo_path, issue.problem_statement, max_files, max_file_lines)

    lines = [f"Repo: {issue.repo}", f"Base commit: {issue.base_commit}", "Instance ID: " + issue.instance_id]

    header = "\n".join(lines) + "\n"
    content_budget = max_chars - len(header)
    tree_budget = int(content_budget * 0.3)

    if selected:
        header += "\nSelected files (contents with line numbers):\n"
        content_budget -= len("\nSelected files (contents with line numbers):\n")
        per_file = max(120, content_budget // max(len(selected), 1))
        chunks: list[str] = []
        for path in selected:
            chunk = _render_file(repo_path, path, max_file_lines, per_file)
            if chunk is None:
                continue
            chunks.append(chunk)
        header += "\n".join(chunks)

    all_py = _list_py_files(repo_path)
    tree = "\nFile tree (tracked .py files):\n" + "\n".join(all_py)
    header += tree[:tree_budget]

    context = header
    if len(context) > max_chars:
        context = context[:max_chars]
        context += "\n... (context truncated)"
    return context