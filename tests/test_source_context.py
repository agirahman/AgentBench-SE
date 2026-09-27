import subprocess
from pathlib import Path

import pytest

import source_context
from config import Config
from experiments.swebench_adapter import extract_diff
from models.issue import Issue


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _make_fake_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test")
    (repo_dir / "formatter.py").write_text(
        "def format_number(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    (repo_dir / "rounder.py").write_text(
        "def round_currency(value):\n"
        "    return round(value, 2)\n",
        encoding="utf-8",
    )
    (repo_dir / "utils").mkdir()
    (repo_dir / "utils" / "helpers.py").write_text(
        "def helper():\n"
        "    return None\n",
        encoding="utf-8",
    )
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", "init")
    return _git(repo_dir, "rev-parse", "HEAD")


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    cache_root = tmp_path / "repos"
    repo_name = "django/django"
    repo_dir = cache_root / repo_name / "placeholder"
    commit = _make_fake_repo(repo_dir)
    final_dir = cache_root / repo_name / commit
    repo_dir.rename(final_dir)
    monkeypatch.setattr(Config, "REPO_CACHE_DIR", str(cache_root))
    return final_dir, commit


def _make_issue(repo, base_commit, problem_statement="Bug in format_number function"):
    return Issue(
        instance_id="ISSUE-TEST",
        repo=repo,
        base_commit=base_commit,
        problem_statement=problem_statement,
    )


def test_get_repo_at_commit_returns_none_for_invalid_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "REPO_CACHE_DIR", str(tmp_path / "repos"))
    issue = _make_issue("django/django", "abc123")
    assert source_context.get_repo_at_commit(issue.repo, issue.base_commit) is None


def test_get_repo_at_commit_uses_existing_cache(fake_repo):
    repo_dir, commit = fake_repo
    result = source_context.get_repo_at_commit("django/django", commit)
    assert result == repo_dir


def test_select_relevant_files_picks_matching_file(fake_repo):
    repo_dir, commit = fake_repo
    selected = source_context.select_relevant_files(
        repo_dir,
        "format_number misbehaves on negatives in utils/formatter.py",
        max_files=8,
        max_file_lines=600,
    )
    assert "formatter.py" in selected
    assert selected[0] == "formatter.py"


def test_select_relevant_files_respects_max_files(fake_repo):
    repo_dir, commit = fake_repo
    selected = source_context.select_relevant_files(
        repo_dir,
        "format_number round_currency helper",
        max_files=1,
        max_file_lines=600,
    )
    assert len(selected) <= 1


def test_build_source_context_contains_line_numbers_and_tree(fake_repo):
    repo_dir, commit = fake_repo
    issue = _make_issue("django/django", commit, "format_number in utils/formatter.py")
    context = source_context.build_source_context(issue)
    assert "Base commit: " + commit in context
    assert "formatter.py" in context
    assert "1: " in context
    assert "File tree" in context


def test_to_agent_prompt_has_no_source_snapshot(fake_repo):
    """The agent prompt must NOT carry a pre-selected source snapshot.

    Tool calling is the mechanism for gathering evidence: the agent explores the
    checkout with read_file/grep/list_files. Injecting a snapshot as well would
    hand it the answer while also claiming it found the answer itself.
    """
    repo_dir, commit = fake_repo
    issue = _make_issue("django/django", commit, "format_number in utils/formatter.py")
    prompt = issue.to_agent_prompt()
    assert prompt == issue.to_prompt()
    assert "SOURCE CODE (base commit)" not in prompt
    assert "File tree" not in prompt


def test_to_agent_prompt_does_not_touch_the_repo(fake_repo, monkeypatch):
    """Building the prompt must not read the repo at all (no hidden I/O)."""
    repo_dir, commit = fake_repo
    calls = {"n": 0}

    import source_context as sc

    real = sc.build_source_context

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(sc, "build_source_context", spy)
    issue = _make_issue("django/django", commit)
    issue.to_agent_prompt()
    assert calls["n"] == 0


def test_extract_diff_keeps_complete_patch_when_truncated():
    response = (
        '{"root_cause": "x", "patch": "diff --git a/foo.py b/foo.py\\n'
        "index 1111111..2222222 100644\\n"
        "--- a/foo.py\\n+++ b/foo.py\\n"
        "@@ -1 +1 @@\\n-old\\n+new\\n\", \"summary\": \"y\"}"
    )
    result = extract_diff(response, finish_reason="length")
    assert result.status == "VALID"
    assert "diff --git" in result.patch


def test_extract_diff_truncated_when_patch_absent():
    result = extract_diff('{"root_cause": "x", "patch": ""}', finish_reason="length")
    assert result.status == "TRUNCATED"
    assert result.patch == ""