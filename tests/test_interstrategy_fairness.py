"""Inter-strategy fairness: no artifact may survive from one strategy to the next.

The three strategies (direct/planning/review) run sequentially against the SAME
checkout, so anything left behind is an input the next strategy sees and the
previous one did not. That is contamination of the comparison, not a cosmetic
cleanliness issue.

Measured on this repository before these fixes (2026-10-06):

  * 663 `__pycache__` directories, 3541 `*.pyc` files and 2 `.pytest_cache`
    directories across 53 checkouts — 23 checkouts affected.
  * 2 checkouts carried a NON-EMPTY `.pytest_cache/v/cache/lastfailed` holding
    real test paths from earlier runs. For SWE-bench the failing tests ARE the
    bug location (FAIL_TO_PASS), and a later strategy need not even read the file:
    `pytest --lf` consults it automatically and re-runs exactly the tests the
    previous strategy discovered.
  * A marker written into `.git` survived every reset, because `git clean` never
    removes anything inside `.git` and `git status` never reports it.

Every test here builds its own throwaway git repo and cleans up in the same block.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    """A real git repo with the ignore patterns that caused the leak."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text(
        "*.pyc\n__pycache__/\nbuild/\ndist/\n*.log\n.pytest_cache/\n", encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    # -b main so the branch/checkout tests can name it explicitly; older git
    # versions default to `master`, which would make those tests silently skip.
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    # The `.git` fingerprint is process-global and keyed by repo root, so clear it
    # to keep tests independent (each tmp_path is unique, but a stale entry would
    # survive a re-used path).
    T._GIT_DIR_BASELINES.clear()
    T.set_repo_root(root)
    yield root
    T._GIT_DIR_BASELINES.clear()
    T.set_repo_root(None)


class TestGitignoreLeftoversAreCleaned:
    """F4: `git clean` without -x left every ignored file in place."""

    def test_removes_ignored_pyc(self, repo):
        """A .pyc is recoverable code, not garbage: marshal.load restores the
        code objects, so its names and constants reach the next strategy."""
        (repo / "agent_out.pyc").write_text("SOLUTION: LEAK_MARKER_99\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is True
        assert not (repo / "agent_out.pyc").exists(), (
            "an ignored .pyc survived the reset, so strategy B can read strategy A's "
            "leftovers"
        )

    def test_removes_pycache_directory(self, repo):
        """__pycache__ is the automatic version of the same leak: running python
        creates it without the agent intending to leave anything."""
        cache = repo / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-39.pyc").write_text("LEAK_MARKER_99\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is True
        assert not cache.exists(), "an ignored __pycache__ survived the reset"

    def test_removes_pytest_cache_with_test_results(self, repo):
        """THE MOST DANGEROUS CASE. `.pytest_cache/v/cache/lastfailed` records
        WHICH tests failed — for SWE-bench that is the bug location."""
        cache = repo / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True)
        (cache / "lastfailed").write_text(
            json.dumps({"tests/test_foo.py::test_bar": True}), encoding="utf-8")
        assert T.reset_working_tree(repo) is True
        assert not (repo / ".pytest_cache").exists(), (
            "pytest's test-result cache survived the reset, so the next strategy "
            "can discover the failing tests without exploring"
        )

    def test_removes_other_ignored_patterns(self, repo):
        """build/, dist/ and *.log are all in the checkout's .gitignore."""
        for name in ("build", "dist"):
            d = repo / name
            d.mkdir()
            (d / "artifact.txt").write_text("LEAK_MARKER_99\n", encoding="utf-8")
        (repo / "agent.log").write_text("LEAK_MARKER_99\n", encoding="utf-8")

        assert T.reset_working_tree(repo) is True
        for name in ("build", "dist", "agent.log"):
            assert not (repo / name).exists(), f"ignored {name} survived the reset"

    def test_reset_repo_tool_also_cleans_ignored_files(self, repo):
        """The agent-facing reset_repo tool must not give a false clean slate."""
        (repo / "agent_out.pyc").write_text("LEAK_MARKER_99\n", encoding="utf-8")
        out = T.reset_repo()
        assert "[ok]" in out, out
        assert not (repo / "agent_out.pyc").exists(), (
            "reset_repo reported success while leaving an ignored file behind"
        )


class TestVerificationIsNotBlind:
    """F6: the verification shared the blindness of the operation it checked."""

    def test_reset_reports_false_when_an_ignored_file_cannot_be_removed(
            self, repo, monkeypatch):
        """`status --porcelain` cannot see ignored files, so a verification using
        it returned True with the leak still present.

        The cleaning commands are made no-ops while `status` runs for real, so the
        function must detect the leftover through `--ignored`.
        """
        (repo / "agent_out.pyc").write_text("LEAK_MARKER_99\n", encoding="utf-8")

        real_git_in = T._git_in

        class _NoOp:
            stdout = b""
            stderr = b""
            returncode = 1

        def _fake(root, *args, **kwargs):
            if args and args[0] == "status":
                return real_git_in(root, *args, **kwargs)
            return _NoOp()

        monkeypatch.setattr(T, "_git_in", _fake)
        assert T.reset_working_tree(repo) is False, (
            "a leftover ignored file was not detected: the verification is as blind "
            "as the clean it verifies"
        )

    def test_verification_uses_the_ignored_flag(self, repo, monkeypatch):
        """Pin the flag itself: without --ignored the check cannot see the leak."""
        seen: list[tuple] = []
        real_git_in = T._git_in

        def _spy(root, *args, **kwargs):
            seen.append(args)
            return real_git_in(root, *args, **kwargs)

        monkeypatch.setattr(T, "_git_in", _spy)
        assert T.reset_working_tree(repo) is True
        status_calls = [a for a in seen if a and a[0] == "status"]
        assert status_calls, "no status verification ran"
        assert any("--ignored" in a for a in status_calls), (
            "the verification does not pass --ignored, so it cannot see ignored files"
        )


class TestGitDirIsNotAWritableChannel:
    """F1: `.git` was writable and survived every reset."""

    def test_read_file_refuses_dot_git(self, repo):
        (repo / ".git" / "packed-refs").write_text("refs/tags/v9\n", encoding="utf-8")
        out = T.read_file(".git/packed-refs")
        assert "not allowed" in out, out
        assert "refs/tags" not in out

    def test_write_file_refuses_dot_git(self, repo):
        """The write side is worse than the read side: it PERSISTS."""
        out = T.write_file(".git/zz_marker", "STRATEGY_A_WAS_HERE")
        assert "not allowed" in out, out
        assert not (repo / ".git" / "zz_marker").exists(), (
            "a file was created inside .git, which no reset removes"
        )

    def test_edit_file_refuses_dot_git(self, repo):
        (repo / ".git" / "ORIG_HEAD").write_text("deadbeef\n", encoding="utf-8")
        out = T.edit_file(".git/ORIG_HEAD", "deadbeef", "sabotage")
        assert "not allowed" in out, out
        assert (repo / ".git" / "ORIG_HEAD").read_text(encoding="utf-8") == "deadbeef\n"

    def test_list_files_refuses_dot_git(self, repo):
        out = T.list_files(".git")
        assert "not allowed" in out, out

    def test_grep_from_root_does_not_descend_into_dot_git(self, repo):
        """Blocking the REQUESTED path is not enough: `grep('.')` is a legitimate
        call, and the walk from the repo root reached `.git/packed-refs`.

        Asserts on the FILE PATH, not on the pattern text: the no-match message
        echoes the pattern (`[no matches for 'FUTURE-TAG']`), so checking for the
        pattern would fail even when the guard works.
        """
        (repo / ".git" / "packed-refs").write_text(
            "abc123 refs/tags/v9.9.9-FUTURE-TAG\n", encoding="utf-8")
        out = T.grep("FUTURE-TAG", ".")
        assert "packed-refs" not in out, (
            f"grep from the repo root read a file inside .git: {out}"
        )

    def test_grep_on_dot_git_is_refused(self, repo):
        out = T.grep("refs", ".git")
        assert "not allowed" in out, out

    @pytest.mark.parametrize("path", [".gitignore", ".gitattributes"])
    def test_dot_git_prefixed_files_still_work(self, repo, path):
        """Component matching, NOT prefix matching: these are ordinary files."""
        (repo / path).write_text("# note\n", encoding="utf-8")
        out = T.read_file(path)
        assert "[error]" not in out, f"{path} was wrongly blocked: {out}"

    def test_nested_dot_git_component_is_refused(self, repo):
        (repo / "pkg").mkdir()
        out = T.read_file("pkg/.git/config")
        assert "not allowed" in out, out

    def test_case_variant_is_refused(self, repo):
        """Windows filesystems are case-insensitive, so .GIT is the same file."""
        out = T.read_file(".GIT/config")
        assert "not allowed" in out, out

    def test_marker_in_dot_git_is_detected_by_reset(self, repo):
        """A marker written through the SHELL (which the tool guard cannot stop)
        must still be detected, because git never reports or cleans `.git`."""
        (repo / ".git" / "zz_orch_marker").write_text("STRATEGY_A_WAS_HERE\n",
                                                      encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker inside .git was not detected: it would survive every reset "
            "and be readable by the next strategy"
        )

    @pytest.mark.parametrize("name", ["gc.log", "ORIG_HEAD", "HEAD", "config"])
    def test_overwriting_an_ALLOWLISTED_name_is_detected(self, repo, name):
        """THE HOLE A NAME ALLOWLIST LEAVES OPEN.

        Measured exploit: `echo STRATEGY_A_WAS_HERE > .git/gc.log` landed on the
        host, and reset_working_tree() returned True — because `gc.log` is a
        legitimate name. Forgiving a name forgives OVERWRITING it, so the check
        must be on CONTENT.

        Covers both shapes: a name that already exists (`HEAD`, `config`, and
        `ORIG_HEAD` after a reset) and one the attacker CREATES with a legitimate
        name (`gc.log` in a fresh repo). The creation case is the subtler one: the
        name check passes because the name is allowed.

        Requires two calls: the fingerprint is recorded at the end of one reset
        and compared at the start of the next, which is the only window in which
        an act's write is visible (the reset commands themselves rewrite `.git`).
        """
        assert T.reset_working_tree(repo) is True, "baseline reset must succeed"
        target = repo / ".git" / name
        existed = target.exists()
        if existed:
            original = target.read_text(encoding="utf-8", errors="replace")
        target.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        if existed:
            assert original != "STRATEGY_A_WAS_HERE\n", f"precondition: {name} changed"
        assert T.reset_working_tree(repo) is False, (
            f"writing the legitimate name {name} inside .git was not detected "
            f"(existed before: {existed}), so it survives every reset and reaches "
            f"the next strategy"
        )

    def test_a_poisoned_index_is_detected(self, repo):
        """A corrupt index is NOT rebuilt by reset/checkout/clean (measured), and
        the failure is silent: `git status` returns non-zero with EMPTY stdout,
        which the old code read as a clean tree."""
        assert T.reset_working_tree(repo) is True
        (repo / ".git" / "index").write_bytes(b"GARBAGE_NOT_AN_INDEX")
        assert T.reset_working_tree(repo) is False, (
            "a poisoned index was accepted; git status fails silently and the "
            "resulting empty output looks like a clean tree"
        )

    def test_reflog_marker_append_is_detected(self, repo):
        """`logs/HEAD` is excluded from the hash comparison because git appends to
        it legitimately, which would otherwise leave a hole: an agent can APPEND a
        marker and the next strategy reads it out of the reflog.

        Caught by requiring the previous text to survive as a prefix and every
        appended line to match the reflog format.
        """
        assert T.reset_working_tree(repo) is True
        reflog = repo / ".git" / "logs" / "HEAD"
        reflog.write_text(reflog.read_text(encoding="utf-8") + "STRATEGY_A_WAS_HERE\n",
                          encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker appended to the reflog was not detected"
        )

    def test_reflog_rewrite_is_detected(self, repo):
        """Overwriting the reflog outright (not appending) must also be caught."""
        assert T.reset_working_tree(repo) is True
        reflog = repo / ".git" / "logs" / "HEAD"
        reflog.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "replacing the reflog content was not detected"
        )

    def test_reflog_shaped_forgery_is_detected(self, repo):
        """A well-formed FAKE reflog line must still be caught.

        The format check alone accepts it, so the prefix rule is what catches it:
        the original lines must still be present at the start.
        """
        assert T.reset_working_tree(repo) is True
        reflog = repo / ".git" / "logs" / "HEAD"
        forged = ("0" * 40 + " " + "1" * 40 +
                  " STRATEGY_A <a@b> 1700000000 +0000\tLEAK_MARKER_99\n")
        reflog.write_text(forged, encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a shaped reflog forgery that discarded the original content was accepted"
        )

    def test_normal_git_commit_between_resets_is_not_tampering(self, repo):
        """Git itself appends to the reflog on commit, so a commit must pass.

        Without the format/prefix rule being correct, this would false-positive.
        """
        for i in range(3):
            (repo / "app.py").write_text(f"x = {i + 400}\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", f"c{i}")
            assert T.reset_working_tree(repo) is True, (
                f"cycle {i}: a normal commit's reflog append was misreported"
            )

    @pytest.mark.parametrize("op", ["tag", "checkout_b", "stash", "merge", "gc",
                                    "checkout_branch"])
    def test_legitimate_git_operations_between_resets_pass(self, repo, op):
        """EVERY git operation an agent may legitimately run must survive the reset.

        Measured before this fix: 7 of these 10 operations were REFUSED, which
        would abort every real run that used them (the strategies raise
        RuntimeError when reset_working_tree returns False). The cause was hashing
        refs and branch reflogs, which git rewrites during normal work.

        They are now shape-checked instead: a ref must hold a valid 40-hex SHA or
        a `ref:` pointer, and a reflog line must match the reflog format.

        The BASELINE reset first is essential, not decoration. The tamper check
        only compares against a recorded baseline, so an operation performed before
        the first reset is never examined — an earlier version of this test did
        exactly that and therefore passed even with the ref handling removed
        (found by mutation testing: reverting `_GIT_REF_PREFIXES` left it green).
        Doing a baseline reset first reproduces the production pattern, where the
        operation happens BETWEEN two resets.
        """
        assert T.reset_working_tree(repo) is True, "baseline reset must succeed"

        if op == "tag":
            _git(repo, "tag", "v1")
        elif op == "checkout_b":
            _git(repo, "checkout", "-q", "-b", "feature")
        elif op == "checkout_branch":
            _git(repo, "checkout", "-q", "-b", "other")
            _git(repo, "checkout", "-q", "main")
        elif op == "stash":
            (repo / "app.py").write_text("changed\n", encoding="utf-8")
            _git(repo, "stash")
        elif op == "merge":
            _git(repo, "checkout", "-q", "-b", "feat")
            (repo / "f.py").write_text("f\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "feat")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "merge", "-q", "--ff-only", "feat")
        elif op == "gc":
            _git(repo, "gc", "--quiet")

        assert T.reset_working_tree(repo) is True, (
            f"a legitimate `git {op}` was misreported as .git tampering, which "
            f"would abort the run"
        )

    def test_gc_log_overwrite_is_detected(self, repo):
        """`gc.log` is a FREE-TEXT file, so an agent can write anything there and
        it survives every reset.

        `git gc` does NOT create it in these checkouts (measured with --quiet,
        --auto and --prune=now), so hashing it is safe — and required, because
        listing it as a "normal use" file would let a marker through.
        """
        assert T.reset_working_tree(repo) is True
        (repo / ".git" / "gc.log").write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker written into .git/gc.log was not detected"
        )

    def test_real_git_gc_still_passes(self, repo):
        """No false positive: a real `git gc` must not be flagged.

        This is the counterpart to the test above. gc.log is NOT in the exclusion
        list precisely because git gc never writes it here — verified for three
        flag variants — so hashing it catches an agent's marker without refusing a
        legitimate gc.
        """
        for i in range(3):
            _git(repo, "gc", "--quiet")
            assert T.reset_working_tree(repo) is True, (
                f"cycle {i}: a legitimate `git gc` was misreported as tampering"
            )

    def test_reflog_replaced_with_a_FORMAT_VALID_line_is_detected(self, repo):
        """The PREFIX rule is load-bearing, and this is the case that proves it.

        A replacement whose line matches the reflog format passes the format check
        and passes the structural check (the SHAs are well formed), so only the
        "previous content must survive as a prefix" rule catches it. Measured: with
        the prefix rule disabled this attack is MISSED.
        """
        assert T.reset_working_tree(repo) is True
        reflog = repo / ".git" / "logs" / "HEAD"
        valid_line = ("0" * 40 + " " + "1" * 40 +
                      " A <a@b> 1700000000 +0000\tMARK\n")
        reflog.write_text(valid_line, encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a format-valid reflog replacement that discarded the previous content "
            "was accepted"
        )

    def test_new_reflog_file_with_a_marker_is_detected(self, repo):
        """The STRUCTURAL check is load-bearing for reflog files that did not exist
        when the baseline was recorded.

        There is no previous text for a brand-new reflog, so the prefix rule cannot
        apply; only the format check on the file itself can catch it. Measured: with
        the structural check disabled this attack is MISSED.
        """
        assert T.reset_working_tree(repo) is True
        new_reflog = repo / ".git" / "logs" / "refs" / "heads" / "zz_attack"
        new_reflog.parent.mkdir(parents=True, exist_ok=True)
        new_reflog.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a new reflog file containing a marker was not detected"
        )

    def test_new_ref_file_with_a_marker_is_detected(self, repo):
        """The structural check is also load-bearing for refs.

        A newly created ref is not in the baseline (nothing to compare against) and
        its name is unremarkable, so only the shape check catches a non-SHA value.
        """
        assert T.reset_working_tree(repo) is True
        new_ref = repo / ".git" / "refs" / "heads" / "zz_attack"
        new_ref.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a new ref file holding a non-SHA value was not detected"
        )

    def test_branch_ref_marker_is_detected(self, repo):
        """Refs are shape-checked, not ignored: a marker in a branch ref must be
        caught even though git rewrites refs during legitimate work."""
        assert T.reset_working_tree(repo) is True
        branch = repo / ".git" / "refs" / "heads" / "main"
        if not branch.exists():
            pytest.skip("no loose branch ref in this git version")
        branch.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker written into a branch ref was not detected"
        )

    def test_branch_reflog_marker_is_detected(self, repo):
        """Branch reflogs get the same prefix+format treatment as logs/HEAD."""
        assert T.reset_working_tree(repo) is True
        branch_reflog = repo / ".git" / "logs" / "refs" / "heads" / "main"
        if not branch_reflog.exists():
            pytest.skip("no branch reflog in this git version")
        with open(branch_reflog, "a", encoding="utf-8") as f:
            f.write("STRATEGY_A_WAS_HERE\n")
        assert T.reset_working_tree(repo) is False, (
            "a marker appended to a branch reflog was not detected"
        )

    def test_orig_head_overwrite_is_detected(self, repo):
        """ORIG_HEAD is excluded from hashing (git rewrites it on reset), so it is
        verified structurally: it must resolve to a real git object.

        Measured: replacing it with 40 zeros used to pass undetected, because the
        old check only compared it to HEAD.
        """
        assert T.reset_working_tree(repo) is True
        oh = repo / ".git" / "ORIG_HEAD"
        if not oh.exists():
            pytest.skip("ORIG_HEAD not present in this git version")
        oh.write_text("0" * 40 + "\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "ORIG_HEAD replaced with a non-resolving SHA was not detected"
        )

    def test_repeated_resets_have_no_false_positives(self, repo):
        """The fingerprint must not fire on a HEALTHY checkout.

        Git's own reset cycle rewrites `index` and `logs/HEAD` and creates
        `ORIG_HEAD`, so those are excluded from the hash comparison (measured over
        4 cycles). If they were included, every reset would refuse a good repo.
        """
        for i in range(5):
            assert T.reset_working_tree(repo) is True, (
                f"reset {i} reported tampering on a healthy checkout"
            )

    def test_capture_diff_between_resets_is_not_tampering(self, repo):
        """THE PRODUCTION PATH that makes the volatile exclusions load-bearing.

        ``capture_diff`` runs ``git add -N .`` (tools.py), which REWRITES
        `.git/index` — and it is called at the end of every strategy run, so this
        happens in the real sweep between two resets.

        Without the volatile exclusions, the next reset compares the rewritten
        index against the previous fingerprint and reports tampering on a perfectly
        healthy checkout. Measured: cycles 1 and 2 returned False when the
        exclusions were removed, so every run after the first would be refused.

        An earlier version of this file did NOT exercise capture_diff, which is why
        the mutation "drop the volatile exclusions" stayed green: the test only did
        edit+reset, and those never touch `.git/index`.
        """
        for i in range(3):
            # Content guaranteed to differ from the committed version, so the
            # index really is rewritten every cycle.
            (repo / "app.py").write_text(f"x = {i + 100}\n", encoding="utf-8")
            (repo / "new_file.py").write_text(f"y = {i}\n", encoding="utf-8")
            diff = T.capture_diff(repo)
            assert "app.py" in diff, "precondition: capture_diff must see the edit"
            assert T.reset_working_tree(repo) is True, (
                f"cycle {i}: capture_diff's index rewrite was misreported as "
                f"tampering, so a real sweep would refuse every run after the first"
            )

    def test_plain_git_add_between_resets_is_not_tampering(self, repo):
        """`git add` is shell-legal (the command filter allows benign git) and
        rewrites `.git/index` too. Same reasoning as capture_diff."""
        for i in range(3):
            (repo / "app.py").write_text(f"x = {i + 200}\n", encoding="utf-8")
            _git(repo, "add", "-A")
            assert T.reset_working_tree(repo) is True, (
                f"cycle {i}: a plain `git add` was misreported as .git tampering"
            )

    def test_normal_work_between_resets_is_not_tampering(self, repo):
        """Editing files and producing test artifacts is normal strategy work and
        must not be mistaken for a `.git` write."""
        for i in range(4):
            (repo / "app.py").write_text(f"x = {i + 300}\n", encoding="utf-8")
            cache = repo / "__pycache__"
            cache.mkdir(exist_ok=True)
            (cache / "m.pyc").write_text("c\n", encoding="utf-8")
            assert T.reset_working_tree(repo) is True, (
                f"cycle {i}: normal work was misreported as .git tampering"
            )

    @pytest.mark.parametrize("op", ["rebase", "cherry_pick", "revert", "config_set",
                                    "worktree_add", "remote_add", "config_unset",
                                    "config_replace_all"])
    def test_advanced_git_operations_between_resets_pass(self, repo, op):
        """Agents legitimately rebase, cherry-pick and revert while fixing code.

        Measured before this fix: 7 advanced operations were REFUSED (rebase,
        rebase --onto, cherry-pick, revert, submodule add, worktree add, config
        set), which would abort a real sweep. Each writes `.git` files that were
        being hash-compared: refs, branch reflogs, AUTO_MERGE, config, worktrees/.

        The baseline reset first is essential (see the sibling test above): without
        it there is nothing to compare against and the check never fires.
        """
        assert T.reset_working_tree(repo) is True, "baseline reset must succeed"

        if op == "rebase":
            _git(repo, "checkout", "-q", "-b", "topic")
            (repo / "t.py").write_text("t\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "topic")
            _git(repo, "checkout", "-q", "main")
            (repo / "m.py").write_text("m\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "main2")
            _git(repo, "rebase", "-q", "topic")
        elif op == "cherry_pick":
            _git(repo, "checkout", "-q", "-b", "feat")
            (repo / "f.py").write_text("f\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "feat")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "cherry-pick", "-q", "feat")
        elif op == "revert":
            _git(repo, "checkout", "-q", "-b", "topic2")
            (repo / "r.py").write_text("r\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "r")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "revert", "--no-edit", "topic2")
        elif op == "config_set":
            _git(repo, "config", "user.name", "tester")
        elif op == "config_unset":
            _git(repo, "config", "--unset", "core.bare")
        elif op == "config_replace_all":
            _git(repo, "config", "--replace-all", "core.filemode", "true")
        elif op == "remote_add":
            _git(repo, "remote", "add", "origin", "https://example.com/x.git")
        elif op == "worktree_add":
            _git(repo, "worktree", "add", "-q", str(repo.parent / "wt"), "HEAD")

        assert T.reset_working_tree(repo) is True, (
            f"a legitimate `git {op}` was misreported as .git tampering, which "
            f"would abort the run"
        )

    def test_config_overwrite_is_detected(self, repo):
        """`config` is free text: an agent can write anything there and it survives.

        config is excluded from hashing (git rewrites it during legitimate work),
        so the edit is compared against the previous content. A comment is VALID
        INI syntax, which is exactly how a comment-permitting rule let this through
        — measured as attack B4, and it dropped MATRIX1 from 18/18 to 17/18.
        """
        assert T.reset_working_tree(repo) is True
        (repo / ".git" / "config").write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "overwriting .git/config with free text was not detected"
        )

    def test_config_comment_marker_is_detected(self, repo):
        """A COMMENT is valid INI, so a shape check alone accepts it.

        No git command writes a comment into .git/config (measured across 8
        mutations), and 0 of the 53 real checkouts have one, so added comments are
        rejected as a marker channel.
        """
        assert T.reset_working_tree(repo) is True
        cfg = repo / ".git" / "config"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# MARK\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker hidden in a config comment was not detected"
        )

    def test_git_config_set_is_not_tampering(self, repo):
        """The counterpart: a legitimate `git config set` must still pass."""
        assert T.reset_working_tree(repo) is True
        _git(repo, "config", "user.name", "tester")
        assert T.reset_working_tree(repo) is True, (
            "a legitimate `git config set` was misreported as tampering"
        )

    def test_config_tradeoff_is_pinned(self, repo):
        """DOCUMENTED TRADE-OFF: a well-formed hand-written INI edit is ACCEPTED.

        Measured: `git config user.name STRATEGY_A_WAS_HERE` and the hand-written
        equivalent produce BYTE-IDENTICAL configs, so no structural rule can accept
        the legitimate command while rejecting the written one. Accepting the former
        (so a real run is not aborted) necessarily accepts the latter.

        This test PINS that consequence so it cannot drift unnoticed. It is the C5
        channel: a marker hidden in a well-formed INI edit. It is acceptable because
        it needs a `.git/config` write, and in the sweep's docker mode `/work/.git`
        is a tmpfs (measured: a container write leaves the host config
        byte-identical) and the host tool layer refuses `.git/config` entirely.

        If this test ever fails because the channel became DETECTED, that is an
        improvement — update this test and the `_config_edit_is_clean` docstring.
        If it fails because the legitimate case broke, the sweep will abort runs.
        """
        assert T.reset_working_tree(repo) is True

        # The legitimate command, with a marker-looking value.
        _git(repo, "config", "user.name", "STRATEGY_A_WAS_HERE")
        legit_config = (repo / ".git" / "config").read_text(encoding="utf-8")
        assert T.reset_working_tree(repo) is True, (
            "a legitimate `git config set` must not abort the run"
        )

        # The hand-written equivalent, byte-for-byte the same edit.
        assert T.reset_working_tree(repo) is True
        cfg = repo / ".git" / "config"
        cfg.write_text(cfg.read_text(encoding="utf-8")
                       + "[user]\n\tname = STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        hand_written = cfg.read_text(encoding="utf-8")

        # Same edit shape, so the same verdict — this is the trade-off.
        assert T.reset_working_tree(repo) is True, (
            "the hand-written INI edit is accepted by design (see the "
            "_config_edit_is_clean docstring); if it is now DETECTED, update the "
            "docstring and the C5 note in the module tests"
        )
        assert "[user]" in hand_written and "[user]" in legit_config

    def test_config_tradeoff_does_not_extend_to_free_text(self, repo):
        """The trade-off must NOT extend to non-INI content.

        A free-text marker is not INI at all, so it is still rejected.
        """
        assert T.reset_working_tree(repo) is True
        (repo / ".git" / "config").write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "free text in config must still be detected"
        )

    def test_config_tradeoff_does_not_extend_to_comments(self, repo):
        """A comment IS valid INI syntax, but no git command writes one into
        `.git/config` (measured across 8 mutations), so comments stay rejected.

        Separate test rather than a second case in the one above: the reset does NOT
        repair `.git/config` (git never rewrites that file), so a detected marker
        persists and would make a later call fail for the wrong reason.
        """
        assert T.reset_working_tree(repo) is True
        cfg = repo / ".git" / "config"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# MARK\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a comment in config must still be detected"
        )

    def test_detected_config_tamper_is_not_auto_repaired(self, repo):
        """A detected config tamper PERSISTS: the reset does not restore `.git/config`.

        Documented because it affects how a refusal must be handled — the run is
        refused, and re-running without operator intervention would refuse again.
        The config must be restored (e.g. by tools/clean_repos.py) before the
        checkout is usable.
        """
        assert T.reset_working_tree(repo) is True
        cfg = repo / ".git" / "config"
        cfg.write_text("STRATEGY_A_WAS_HERE\n", encoding="utf-8")
        assert T.reset_working_tree(repo) is False
        assert "STRATEGY_A_WAS_HERE" in cfg.read_text(encoding="utf-8"), (
            "precondition: the tamper is still on disk"
        )
        assert T.reset_working_tree(repo) is False, (
            "a second reset must also refuse while the tamper remains"
        )

    def test_legitimate_remote_add_is_not_tampering(self, repo):
        """Why the trade-off cannot be narrowed to close C7.

        Rejecting `[remote "..."]` additions would close the C7 channel but break
        `git remote add`, which `git fetch`/`git pull` depend on. The only remaining
        discriminator would be pattern-matching marker-like remote NAMES — a
        heuristic that would false-positive on unusual-but-legitimate names.
        """
        assert T.reset_working_tree(repo) is True
        _git(repo, "remote", "add", "origin", "https://example.com/x.git")
        assert T.reset_working_tree(repo) is True, (
            "a legitimate `git remote add` was misreported as tampering"
        )

    def test_worktree_reflog_without_a_message_is_accepted(self, repo):
        """A worktree reflog omits the trailing `\\t<message>`.

        Measured: `worktrees/<name>/logs/HEAD` holds only
        `0000... <sha> name <email> <ts> <tz>`. Requiring the tab made a
        legitimate `git worktree add` look like a malformed reflog (the D9
        regression).
        """
        assert T.reset_working_tree(repo) is True
        _git(repo, "worktree", "add", "-q", str(repo.parent / "wt2"), "HEAD")
        assert T.reset_working_tree(repo) is True, (
            "a legitimate worktree reflog was reported as malformed"
        )

    def test_auto_merge_marker_is_detected(self, repo):
        """AUTO_MERGE is written by rebase/revert, so it cannot be hash-compared.

        It holds a single 40-hex object id, and that SHAPE is what is checked.
        Measured: a marker placed there is detected — partly by the shape check and
        partly because git itself then fails to resolve the ref (rc=0 with
        "cannot lock ref 'AUTO_MERGE'"), which the poisoned-index check catches.
        Both paths are exercised here so neither can rot silently.
        """
        assert T.reset_working_tree(repo) is True
        (repo / ".git" / "AUTO_MERGE").write_text("STRATEGY_A_WAS_HERE\n",
                                                  encoding="utf-8")
        assert T.reset_working_tree(repo) is False, (
            "a marker written into .git/AUTO_MERGE was not detected"
        )

    def test_auto_merge_shape_check_rejects_free_text(self, repo):
        """Pin the SHAPE rule directly, independent of git's own failure.

        Without this, the detection above could pass purely because `git reset`
        happened to fail on the bad ref, and the shape rule could be deleted
        unnoticed.
        """
        (repo / ".git" / "AUTO_MERGE").write_text("STRATEGY_A_WAS_HERE\n",
                                                  encoding="utf-8")
        problems = T._structural_problems(repo / ".git")
        assert any("AUTO_MERGE" in p for p in problems), (
            f"the shape check did not report AUTO_MERGE: {problems}"
        )

    def test_auto_merge_with_a_valid_sha_is_accepted(self, repo):
        """No false positive: a real rebase's AUTO_MERGE is a valid object id."""
        assert T.reset_working_tree(repo) is True
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / ".git" / "AUTO_MERGE").write_text(sha + "\n", encoding="utf-8")
        assert T._structural_problems(repo / ".git") == [], (
            "a valid 40-hex AUTO_MERGE was reported as malformed"
        )

    def test_submodule_reports_an_unsupported_operation_not_tampering(self, repo,
                                                                     monkeypatch):
        """DOCUMENTED LIMITATION: a nested repo cannot be cleaned, and we say so.

        `git clean` refuses to delete a nested repository (measured with -fdxq,
        -fdxq -f, -fdx -ff, -fdffx: none removed it), so the tree cannot be made
        pristine. The run must be refused — but with an accurate reason, so an
        operator does not hunt for a leak that is not there.

        This is a limitation, NOT a hole: no `.git` guard is relaxed, and the
        submodule's metadata is still shape-checked. Relevance is low: 0 of the 53
        checkouts have a .gitmodules file, and SWE-bench agents fix code rather
        than adding submodules.

        Asserts on the MESSAGE, not just the return value. Measured: the diagnostic
        only changes the wording — the reset returns False either way (the tree
        really is dirty) — so a return-value assertion cannot detect the diagnostic
        being removed. `_warn` is spied on because it goes through loguru, which
        holds its own stderr reference from import time and is therefore invisible
        to both `caplog` and `capsys`.
        """
        assert T.reset_working_tree(repo) is True, "baseline reset must succeed"
        p = _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(repo), "sub")
        if p.returncode != 0:
            pytest.skip(f"git submodule add unavailable here: {p.stderr[:60]}")

        assert T._nested_repos_in_tree(repo) == ["sub"], "precondition: nested repo"

        messages: list[str] = []
        monkeypatch.setattr(T, "_warn", lambda msg: messages.append(str(msg)))

        result = T.reset_working_tree(repo)
        joined = "\n".join(messages)

        assert result is False, "a tree with an uncleanable nested repo must be refused"
        assert "SUBMODULE/NESTED-REPO OPERATIONS ARE NOT SUPPORTED" in joined, (
            "the refusal was reported as generic dirt instead of an unsupported "
            f"operation, so an operator would hunt for a leak. warnings: {joined[:300]!r}"
        )
        assert "not detected tampering" in joined, (
            f"the message must distinguish a limitation from tampering: {joined[:300]!r}"
        )

    def test_a_normal_git_dir_passes_reset(self, repo):
        """No false positive: a checkout that has just been committed is fine.

        `git commit` creates COMMIT_EDITMSG, so an allowlist that only covered the
        measured 12 names would refuse every healthy repo.
        """
        _git(repo, "commit", "--allow-empty", "-qm", "another")
        assert (repo / ".git" / "COMMIT_EDITMSG").exists(), "precondition"
        assert T.reset_working_tree(repo) is True


class TestReparsePointsAreDetectedByReset:
    """Junctions must not survive a reset, and must not be deleted THROUGH.

    Two layers, both verified here:
      1. `git clean -fdx` removes the link itself (and, measured with outside
         canaries, never deletes the link's TARGET).
      2. A reparse-point scan in the verification step catches anything clean
         could not remove (e.g. clean failed or was skipped).
    """

    def _junction(self, link: Path, target: Path) -> bool:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True)
        return r.returncode == 0 and os.path.lexists(link)

    def test_reset_removes_a_junction_without_touching_its_target(self, repo, tmp_path):
        """Layer 1: the junction goes, the outside data stays."""
        target = tmp_path / "outside_dir"
        target.mkdir()
        canary = target / "canary.txt"
        canary.write_text("precious\n", encoding="utf-8")
        link = repo / "zz_link"
        if not self._junction(link, target):
            pytest.skip("cannot create a junction on this host")
        try:
            assert T.reset_working_tree(repo) is True
            assert not os.path.lexists(link), "the junction survived the reset"
            assert canary.exists(), (
                "git clean followed the junction and deleted data OUTSIDE the repo"
            )
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_backstop_detects_a_junction_when_clean_cannot_run(self, repo, tmp_path,
                                                              monkeypatch):
        """Layer 2, and proof it is not dead code.

        With the cleaning command disabled, only the reparse scan can notice the
        link — so the reset must return False. Without this test the scan could be
        deleted and every other test here would still pass.
        """
        target = tmp_path / "outside_dir3"
        target.mkdir()
        link = repo / "zz_link3"
        if not self._junction(link, target):
            pytest.skip("cannot create a junction on this host")

        real_git_in = T._git_in

        class _NoOp:
            stdout = b""
            stderr = b""
            returncode = 0

        def _fake(root, *args, **kwargs):
            if args and args[0] == "clean":
                return _NoOp()          # the clean silently does nothing
            return real_git_in(root, *args, **kwargs)

        monkeypatch.setattr(T, "_git_in", _fake)
        try:
            assert T.reset_working_tree(repo) is False, (
                "the reparse scan did not catch a junction that clean failed to "
                "remove — so the backstop is not working"
            )
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_backstop_detects_a_DANGLING_junction(self, repo, tmp_path, monkeypatch):
        """The case `git status --ignored` misses (measured): the target is gone,
        so git omits the link from status entirely, but the link is still on disk
        and still readable."""
        link = repo / "zz_dangling"
        if not self._junction(link, tmp_path / "gone_target"):
            pytest.skip("cannot create a junction on this host")

        real_git_in = T._git_in

        class _NoOp:
            stdout = b""
            stderr = b""
            returncode = 0

        def _fake(root, *args, **kwargs):
            if args and args[0] == "clean":
                return _NoOp()
            return real_git_in(root, *args, **kwargs)

        monkeypatch.setattr(T, "_git_in", _fake)
        try:
            assert T.reset_working_tree(repo) is False, (
                "a dangling junction was not detected; git status does not report it"
            )
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_reset_succeeds_once_the_link_is_gone(self, repo, tmp_path, monkeypatch):
        """No false positive: once the link is removed the reset must pass."""
        target = tmp_path / "outside_dir2"
        target.mkdir()
        link = repo / "zz_link2"
        if not self._junction(link, target):
            pytest.skip("cannot create a junction on this host")

        real_git_in = T._git_in

        class _NoOp:
            stdout = b""
            stderr = b""
            returncode = 0

        def _fake(root, *args, **kwargs):
            if args and args[0] == "clean":
                return _NoOp()
            return real_git_in(root, *args, **kwargs)

        monkeypatch.setattr(T, "_git_in", _fake)
        try:
            assert T.reset_working_tree(repo) is False
            os.unlink(link)
            assert T.reset_working_tree(repo) is True, (
                "the reset must recover once the link is gone"
            )
        finally:
            if os.path.lexists(link):
                os.unlink(link)


class TestCleanReposScript:
    """The DOCUMENTED remedy must actually remedy.

    `tools/clean_repos.py` is what MEMORY.md, preflight_repos.py and the strategy
    error messages all tell the operator to run. Its rules were previously only
    reachable by running it against the real datasets/repos, so nothing tested
    them — and a mutation run showed that reverting `-fdxq` to `-fdq`, or dropping
    `--ignored`, left the whole suite green.
    """

    @pytest.fixture
    def clean_mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "clean_repos_under_test", ROOT / "tools" / "clean_repos.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_removes_ignored_pyc(self, repo, clean_mod):
        (repo / "agent_out.pyc").write_text("LEAK_MARKER_99\n", encoding="utf-8")
        assert clean_mod.clean_one(repo) == "cleaned"
        assert not (repo / "agent_out.pyc").exists()

    def test_removes_pytest_cache(self, repo, clean_mod):
        cache = repo / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True)
        (cache / "lastfailed").write_text('{"t.py::x": true}', encoding="utf-8")
        assert clean_mod.clean_one(repo) == "cleaned"
        assert not (repo / ".pytest_cache").exists(), (
            "the documented remedy left pytest's test-result cache behind"
        )

    def test_reports_pristine_for_a_clean_tree(self, repo, clean_mod):
        assert clean_mod.clean_one(repo) == "pristine"

    def test_detection_uses_the_ignored_flag(self, repo, clean_mod, monkeypatch):
        """Pin the FLAG, not a simulation of its absence.

        An earlier version of this test monkeypatched ``git`` to force the blind
        behaviour and then asserted the blind behaviour — so it passed no matter
        what the source said, and a mutation run proved it: deleting ``--ignored``
        from ``clean_one`` left the test green. Spying on the real calls pins the
        source instead.
        """
        cache = repo / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-39.pyc").write_text("x\n", encoding="utf-8")

        seen: list[tuple] = []
        real_git = clean_mod.git

        def spy(repo_path, *args):
            seen.append(args)
            return real_git(repo_path, *args)

        monkeypatch.setattr(clean_mod, "git", spy)
        assert clean_mod.clean_one(repo) == "cleaned"

        status_calls = [a for a in seen if a and a[0] == "status"]
        assert status_calls, "clean_one ran no status check"
        assert any("--ignored" in a for a in status_calls), (
            "clean_one's detection does not pass --ignored, so a checkout whose only "
            f"problem is an ignored file looks pristine and is skipped. calls: {seen}"
        )

    def test_clean_one_removes_the_ignored_dir_when_verified_properly(
            self, repo, clean_mod):
        cache = repo / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-39.pyc").write_text("x\n", encoding="utf-8")
        assert clean_mod.clean_one(repo) == "cleaned"
        assert not cache.exists()


class TestOperatorPiiIsNotLeftInCheckouts:
    """The reflog records WHO ran git and WHEN, and it is not part of a pristine
    checkout — a fresh clone has no `logs/HEAD`.

    Measured: one real checkout carried `agirahman <agi.rahman.s@gmail.com>` and a
    run timestamp, written by OUR OWN `reset_working_tree` (which runs `git reset`,
    attributed to the ambient git identity). Two fixes, both tested here:
    a neutral identity at the source, and `reflog expire` in clean_repos.
    """

    @pytest.fixture
    def clean_mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "clean_repos_pii_under_test", ROOT / "tools" / "clean_repos.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _reflog_lines(self, repo):
        rf = repo / ".git" / "logs" / "HEAD"
        if not rf.is_file():
            return []
        return [l for l in rf.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_our_reset_does_not_stamp_the_operator_identity(self, repo):
        """SOURCE fix: the entry our reset appends must not carry PII.

        Only the lines OUR code adds are inspected — the fixture's own commit
        legitimately used an operator identity, and that is not what this guards.
        """
        before = len(self._reflog_lines(repo))
        assert T.reset_working_tree(repo) is True
        new_lines = self._reflog_lines(repo)[before:]
        assert new_lines, "precondition: the reset should append a reflog entry"
        joined = "\n".join(new_lines)
        assert "agentbench" in joined, (
            f"the reset did not use the neutral identity: {joined!r}"
        )
        for marker in ("agi.rahman", "@gmail.com"):
            assert marker not in joined, (
                f"our reset stamped operator PII ({marker}) into the checkout: {joined!r}"
            )

    def test_clean_one_expires_the_reflog(self, repo, clean_mod):
        """CLEANUP fix: clean_one must purge the reflog even when the tree is
        otherwise clean, because a reflog is invisible to `git status`."""
        assert T.reset_working_tree(repo) is True
        reflog = repo / ".git" / "logs" / "HEAD"
        if not reflog.exists():
            pytest.skip("no reflog in this git version")
        assert reflog.stat().st_size > 0, "precondition: reset wrote a reflog entry"
        # The tree is otherwise clean, so the old early-return path was taken.
        assert _git(repo, "status", "--porcelain", "--ignored").stdout.strip() == ""

        state = clean_mod.clean_one(repo)
        assert state == "pristine"
        remaining = (reflog.read_text(encoding="utf-8", errors="replace")
                     if reflog.exists() else "")
        assert remaining.strip() == "", (
            "the reflog survived the cleanup, so the operator's identity and the "
            f"run timestamp stay in the dataset: {remaining[:80]!r}"
        )

    def test_reset_still_works_after_the_reflog_was_expired(self, repo, clean_mod):
        """No regression: expiring the reflog outside a run must not make the next
        run's reset report tampering.

        `git reflog expire` shrinks the reflog to empty, and a shrunken reflog is
        indistinguishable from a rewrite to the prefix check — which is exactly why
        the expire lives in clean_repos (outside a run) and NOT in
        reset_working_tree. A fresh baseline is taken when the next run starts.
        """
        assert T.reset_working_tree(repo) is True
        clean_mod.clean_one(repo)
        T._GIT_DIR_BASELINES.clear()      # a new run starts with no baseline
        assert T.reset_working_tree(repo) is True, (
            "the reset refused a checkout whose reflog had just been expired"
        )


class TestCleanReposDoesNotTouchTheProjectRepo:
    """`datasets/repos/.git` is an EMPTY directory, and `rglob('.git')` finds it.

    `git -C datasets/repos` then walks UP the tree and resolves to the PROJECT's
    own `.git`, so an unvalidated loop ran `git reset` / `checkout` / `clean`
    against the research repository. This is why the script reported 54
    "checkouts" when only 53 exist.

    The dangerous command is `git reset -q`: it takes NO pathspec, so it resets the
    whole index of whatever repository git resolves to.
    """

    @pytest.fixture
    def clean_mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "clean_repos_guard_under_test", ROOT / "tools" / "clean_repos.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_real_decoy_is_rejected(self, clean_mod):
        """The actual decoy on disk must not be treated as a checkout."""
        decoy = ROOT / "datasets" / "repos" / ".git"
        if not decoy.exists():
            pytest.skip("the datasets/repos/.git decoy is not present")
        assert clean_mod.is_instance_checkout(decoy) is False, (
            "the empty datasets/repos/.git was accepted as an instance checkout, so "
            "clean_repos would run git reset/clean against the PROJECT repository"
        )

    def test_empty_dot_git_in_a_sha_dir_is_rejected(self, tmp_path, clean_mod):
        """The 40-hex name alone is not enough: HEAD must exist."""
        fake = tmp_path / ("a" * 40) / ".git"
        fake.mkdir(parents=True)
        assert clean_mod.is_instance_checkout(fake) is False

    def test_a_real_checkout_is_accepted(self, tmp_path, clean_mod):
        """No false positive: a genuine checkout must still be cleaned."""
        real = tmp_path / ("b" * 40) / ".git"
        real.mkdir(parents=True)
        (real / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        assert clean_mod.is_instance_checkout(real) is True

    def test_non_sha_named_dir_is_rejected(self, tmp_path, clean_mod):
        """A directory that is not named after a base_commit is not a checkout."""
        fake = tmp_path / "django" / ".git"
        fake.mkdir(parents=True)
        (fake / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        assert clean_mod.is_instance_checkout(fake) is False

    def test_main_skips_the_decoy_and_reports_it(self, clean_mod, monkeypatch,
                                                 capsys, tmp_path):
        """`main` must skip it rather than quietly counting it as a checkout."""
        decoy = tmp_path / "repos" / ".git"
        decoy.mkdir(parents=True)
        real = tmp_path / "repos" / ("c" * 40) / ".git"
        real.mkdir(parents=True)
        (real / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

        monkeypatch.setattr(clean_mod, "REPO_BASE", tmp_path / "repos")
        monkeypatch.setattr(clean_mod, "ROOT", tmp_path)
        monkeypatch.setattr(clean_mod, "clean_one", lambda repo: "pristine")

        rc = clean_mod.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "SKIP" in out, f"the decoy was not reported as skipped: {out}"
        assert "skipped: 1" in out, out


class TestSweepPinsTheSandboxMode:
    """The sweep must not depend on a default for isolation."""

    def test_sweep_command_sets_sandbox_mode_explicitly(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "sweep_under_test", ROOT / "tools" / "run_final_sweep.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        cmd = mod.build_cmd(["inst-1"], ["direct"], dry_run=True)
        assert "SANDBOX_MODE=docker" in cmd, (
            "the sweep does not pin SANDBOX_MODE, so isolation would depend on an "
            f"ambient variable; command was: {cmd}"
        )


class TestSweepHasNoWallClockBound:
    """2026-10-06: the wall-clock bound was removed (run 11 produced NO_DIFF)."""

    def _load_sweep_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "sweep_under_test", ROOT / "tools" / "run_final_sweep.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_zero_act_timeout_means_unbounded(self, monkeypatch):
        """Config.ACT_TIMEOUT_SECONDS=0 must resolve to max_wall_seconds=None.

        tool_loop.run_tool_loop reads the bound from Config when the caller passes
        max_wall_seconds=None. 0 means "disabled", which maps to None. If someone
        changes 0 from None to a real bound, this run dies on a healthy long act.
        """
        from providers import tool_loop

        from test_tool_loop_wall_clock import (
            _FakeClient, _SlowCompletions,
        )

        monkeypatch.setattr(tool_loop.Config, "ACT_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)
        monkeypatch.setattr(
            tool_loop, "execute_tool", lambda n, a, role="": "TOOL_OUT")

        # Three real turns at 0.01 s each = 0.03 s. A zero-second bound would kill
        # this immediately; None (unbounded) must let it finish.
        completions = _SlowCompletions(
            per_call_seconds=0.01, calls_before_final=3)
        client = _FakeClient(completions)
        result = tool_loop.run_tool_loop(
            client, model="fake", prompt="fix", role="direct",
            tools=[{"type": "function",
                    "function": {"name": "read_file", "parameters": {}}}],
            repo_root=None, max_tool_turns=10,  # max_wall_seconds=None default
        )
        assert result.truncated is False, (
            "ACT_TIMEOUT_SECONDS=0 did not map to an unbounded loop; the loop "
            f"stopped early (truncated={result.truncated})"
        )
        assert result.response == "done"

    def test_long_act_is_not_killed_by_wall_clock(self, monkeypatch):
        """A slow but healthy act must NOT be truncated when the bound is off.

        Drives the real loop with max_wall_seconds=None and a client that burns a
        real 0.05 s per call. The loop must run to the turn cap.
        """
        from providers import tool_loop

        from test_tool_loop_wall_clock import (
            _FakeClient, _SlowCompletions,
        )

        monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)
        monkeypatch.setattr(
            tool_loop, "execute_tool", lambda n, a, role="": "TOOL_OUT")

        completions = _SlowCompletions(
            per_call_seconds=0.05, calls_before_final=5)
        client = _FakeClient(completions)

        result = tool_loop.run_tool_loop(
            client, model="fake", prompt="fix", role="direct",
            tools=[{"type": "function",
                    "function": {"name": "read_file", "parameters": {}}}],
            repo_root=None, max_tool_turns=10, max_wall_seconds=None,
        )

        assert result.truncated is False, (
            "max_wall_seconds=None truncated a healthy long act; the unbounded "
            "path is broken"
        )
        assert result.response == "done"

    def test_sweep_pins_act_timeout_explicitly(self):
        """run_final_sweep.py must pass ACT_TIMEOUT_SECONDS=0, not rely on .env."""
        mod = self._load_sweep_module()
        cmd = mod.build_cmd(["inst-1"], ["direct"], dry_run=True)
        joined = " ".join(cmd)
        assert "ACT_TIMEOUT_SECONDS=0" in joined, (
            "the sweep does not pin ACT_TIMEOUT_SECONDS=0; it would silently take "
            f".env's value instead. command: {joined}"
        )

    def test_sweep_banner_says_unbounded(self, capsys):
        """The banner must NOT print '0s' -- an operator could read it as 0 seconds."""
        mod = self._load_sweep_module()
        # The banner reads the module-level constant, so with 0 it must say unbounded.
        assert mod.ACT_TIMEOUT_SECONDS == 0, (
            f"sweep must run unbounded; got {mod.ACT_TIMEOUT_SECONDS}"
        )
        # Render the label the banner uses.
        label = (
            f"{mod.ACT_TIMEOUT_SECONDS}s per act (bounds retry backoff)"
            if mod.ACT_TIMEOUT_SECONDS > 0
            else "unbounded (no wall-clock bound)"
        )
        assert "unbounded" in label, f"banner would mislead the operator: {label}"
        assert "0s" not in label, (
            f"banner must not read '0s' (looks like zero seconds): {label}"
        )

    def test_no_3600_wall_clock_bound_remains_in_sweep_cmd(self):
        """Anti-regression: build_cmd must not carry a 3600s wall-clock bound."""
        mod = self._load_sweep_module()
        cmd = mod.build_cmd(["inst-1"], ["direct"], dry_run=True)
        joined = " ".join(cmd)
        assert "ACT_TIMEOUT_SECONDS=3600" not in joined, (
            f"the sweep still pins 3600s; command: {joined}"
        )
        assert mod.ACT_TIMEOUT_SECONDS == 0, (
            f"sweep constant is {mod.ACT_TIMEOUT_SECONDS}, expected 0"
        )


class TestSandboxModeFailsClosed:
    """CACAT 3: an unset SANDBOX_MODE silently ran agent commands on the host."""

    def test_unset_defaults_to_docker(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        assert T.get_sandbox_mode() == "docker"

    def test_unknown_mode_is_refused_not_executed(self, repo, monkeypatch):
        """A typo must not select the unisolated `else` branch."""
        monkeypatch.setenv("SANDBOX_MODE", "dockr")
        T.reset_test_guard()
        try:
            out = T.run_tests("echo should-not-run")
            assert "[error]" in out, out
            assert "not recognised" in out, out
            assert "should-not-run" not in out, (
                "the command was executed despite an unrecognised SANDBOX_MODE"
            )
        finally:
            T.reset_test_guard()
