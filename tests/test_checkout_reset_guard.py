"""A failed checkout reset must STOP the run, not be shrugged off.

The failure mode this guards is silent and severe. The submitted patch is the
working-tree diff, so if the tree could not be cleaned, whatever was already there
is included in the diff -- and is then recorded as the strategy's answer.

Measured on this repository: 5 of 50 checkouts held a leftover GOLD PATCH (and its
test patch) from EXP-20260930-098-gold-check, a gold-patch validation run that
applied patches to real checkouts and did not clean up. Nothing in the results
indicated it. A run against one of those trees would have produced a patch
containing the reference solution, and every summary would have reported it as a
strategy success.

The old code could not catch this: ``_git_in`` returns a CompletedProcess and does
NOT raise on a non-zero exit code, so the ``try/except`` around the git commands
only ever caught a timeout or an OSError. A git command that genuinely FAILED -- a
stale index.lock, a permission error, a corrupted index -- returned normally and the
function reported success.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one committed file."""
    root = tmp_path / "repo"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    env = {**os.environ, **env}

    def run(*args):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, env=env,
                       check=True)

    run("init", "-q")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "app.py")
    run("commit", "-qm", "init")
    T.set_repo_root(root)
    yield root
    T.set_repo_root(None)


def test_reset_returns_true_when_the_tree_is_clean(repo):
    """The happy path: no changes, so the reset succeeds and says so."""
    assert T.reset_working_tree(repo) is True


def test_reset_cleans_a_modified_file_and_reports_success(repo):
    """A leftover edit must be reverted, and the reset must confirm it."""
    (repo / "app.py").write_text("x = 999  # agent leftover\n", encoding="utf-8")
    assert T.reset_working_tree(repo) is True
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def test_reset_removes_an_untracked_file(repo):
    """An untracked file would otherwise appear in the next diff."""
    (repo / "leftover.py").write_text("junk\n", encoding="utf-8")
    assert T.reset_working_tree(repo) is True
    assert not (repo / "leftover.py").exists()


def test_reset_returns_false_when_the_tree_stays_dirty(repo, monkeypatch):
    """THE case the old code missed: a git command that fails without raising.

    ``_git_in`` returns a CompletedProcess, so a failing git command is not an
    exception. Here the CLEANING commands are made no-ops while ``status`` still
    runs for real -- which is what a blocked checkout looks like to this code (a
    read-only file, a partially applied clean, a checkout that could not complete).
    The function must report the tree as NOT reset rather than claim success.

    Patching status too would prove nothing: an empty status IS a clean tree, so the
    function would be right to return True.
    """
    (repo / "app.py").write_text("x = 999  # cannot be cleaned\n", encoding="utf-8")

    real_git_in = T._git_in

    class _NoOp:
        stdout = b""
        stderr = b""
        returncode = 1

    def _fake(root, *args, **kwargs):
        # The cleaning commands silently do nothing; status still tells the truth.
        if args and args[0] == "status":
            return real_git_in(root, *args, **kwargs)
        return _NoOp()

    monkeypatch.setattr(T, "_git_in", _fake)

    assert T.reset_working_tree(repo) is False, (
        "a tree that could not be cleaned must be reported as such; the captured "
        "diff would otherwise include its existing content"
    )


def test_reset_returns_false_when_git_raises(repo, monkeypatch):
    """A hard failure (timeout, missing git) must also report False."""
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(T, "_git_in", _boom)
    assert T.reset_working_tree(repo) is False


def test_reset_returns_false_for_a_missing_directory(tmp_path):
    """A checkout that does not exist cannot be reset."""
    assert T.reset_working_tree(tmp_path / "nope") is False


# ----------------------------------------------------------- the strategies refuse
def test_direct_strategy_refuses_to_run_on_an_uncleanable_checkout(monkeypatch):
    """A run that cannot be trusted must not be recorded as an outcome.

    Raising is the correct response: recording it would put a patch into the
    results that contains pre-existing changes, and a leftover gold patch would
    read as the agent solving the instance.
    """
    from strategies import direct_strategy

    monkeypatch.setattr(direct_strategy, "ensure_repo_root", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(direct_strategy, "reset_working_tree", lambda *a, **k: False)

    class _Issue:
        instance_id = "django__django-99999"
        repo = "django/django"
        base_commit = "deadbeef"
        difficulty = "hard"

        def to_agent_prompt(self):
            return "fix it"

    class _Provider:
        model = "fake"

    with pytest.raises(RuntimeError, match="could not be reset"):
        direct_strategy.DirectStrategy(_Provider()).run(_Issue())


def test_planning_and_review_refuse_too(monkeypatch):
    """All three strategies share the checkout, so all three must check."""
    from strategies import planning_strategy, review_strategy

    class _Issue:
        instance_id = "django__django-99999"
        repo = "django/django"
        base_commit = "deadbeef"
        difficulty = "hard"

        def to_agent_prompt(self):
            return "fix it"

    class _Provider:
        model = "fake"

    for mod, cls in ((planning_strategy, "PlanningStrategy"),
                     (review_strategy, "ReviewStrategy")):
        monkeypatch.setattr(mod, "ensure_repo_root", lambda *a, **k: Path("/tmp/x"))
        monkeypatch.setattr(mod, "reset_working_tree", lambda *a, **k: False)
        with pytest.raises(RuntimeError, match="could not be reset"):
            getattr(mod, cls)(_Provider()).run(_Issue())
