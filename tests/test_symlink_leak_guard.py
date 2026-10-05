"""Regression tests for the symlink+grep leak and the git-invisible link gate.

Both fixes are covered by tests that were verified to go RED when the fix is
reverted (see the mutation run in the task report).

SAFETY: every test builds its own temp repo and cleans up in the same block. No
real checkout is touched, and only a FAKE secret is used.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from agents.tools import grep, list_files, read_file, set_repo_root
from repo_link_scan import find_reparse_points, is_reparse_point

FAKE_SECRET = "FAKE_KEY_FOR_TESTING_NOT_REAL_12345"


def _make_link(link: Path, target: Path) -> bool:
    """Create a symlink, falling back to a junction (Windows without dev mode)."""
    try:
        os.symlink(target, link)
        return True
    except OSError:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        return r.returncode == 0


def _make_junction(link: Path, target: Path) -> bool:
    """Create a Windows JUNCTION (no admin/dev-mode needed, unlike a symlink).

    This matters for coverage: a junction is NOT caught by ``stat.S_ISLNK``, so it
    is the only way to exercise the ``st_file_attributes`` branch of
    ``is_reparse_point``. A test that only used symlinks would pass even if that
    branch were deleted — which is exactly what a mutation run revealed.
    """
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True)
    return r.returncode == 0 and os.path.lexists(link)


@pytest.fixture
def repo_with_escaping_link(tmp_path: Path):
    """A repo containing a link to a secret OUTSIDE it; cleaned up after."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")

    secret = tmp_path / "outside_secret.txt"
    secret.write_text(f"GEMINI_API_KEY={FAKE_SECRET}\n", encoding="utf-8")

    link = repo / "zz_v_env"
    if not _make_link(link, secret):
        pytest.skip("cannot create symlink or junction on this host")

    set_repo_root(repo)
    try:
        yield repo, link, secret
    finally:
        # Cleanup in the SAME block, so no link is left behind on any outcome.
        try:
            if os.path.lexists(link):
                os.unlink(link)
        except OSError:
            pass
        set_repo_root(None)


class TestGrepDoesNotFollowLinks:
    def test_read_file_already_refuses_the_link(self, repo_with_escaping_link):
        """Control: the resolver was always correct, so this documents the baseline."""
        _repo, _link, _secret = repo_with_escaping_link
        out = read_file("zz_v_env")
        assert "outside the allowed repo directory" in out
        assert FAKE_SECRET not in out

    def test_grep_does_not_leak_through_the_link(self, repo_with_escaping_link):
        """THE REGRESSION: grep must not read a file reachable only via a link.

        Before the fix this returned `zz_v_env:1: GEMINI_API_KEY=...` — the leak
        that made the sweep unsafe.
        """
        _repo, _link, _secret = repo_with_escaping_link
        out = grep("FAKE_KEY_FOR_TESTING", ".")
        assert FAKE_SECRET not in out, f"grep leaked the secret: {out}"
        assert "no matches" in out.lower()

    def test_grep_still_finds_real_files(self, repo_with_escaping_link):
        """The guard must not disable grep: a normal file is still searchable."""
        repo, _link, _secret = repo_with_escaping_link
        (repo / "real.py").write_text("NEEDLE_IN_REPO = 1\n", encoding="utf-8")
        out = grep("NEEDLE_IN_REPO", ".")
        assert "NEEDLE_IN_REPO" in out, f"grep stopped working: {out}"

    def test_grep_reports_skipped_links(self, repo_with_escaping_link):
        """The agent should learn a link was skipped, not silently get nothing."""
        _repo, _link, _secret = repo_with_escaping_link
        out = grep("ZZZ_NO_MATCH_ANYWHERE", ".")
        assert "symlink" in out.lower(), f"skip was not reported: {out}"

    def test_list_files_marks_the_link(self, repo_with_escaping_link):
        """list_files must not present an escaping link as a normal file."""
        _repo, _link, _secret = repo_with_escaping_link
        out = list_files(".")
        assert "[link->outside] zz_v_env" in out, f"link not marked: {out}"


class TestLinkScanner:
    def test_scanner_finds_a_junction_named_pyc(self, tmp_path: Path):
        """A *.pyc-named link is invisible to git but MUST be found by the scanner."""
        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("x\n", encoding="utf-8")
        link = repo / "zz_hidden.pyc"
        if not _make_link(link, secret):
            pytest.skip("cannot create symlink or junction on this host")

        try:
            found = find_reparse_points(repo)
            assert link in found, f"scanner missed the link: {found}"
            assert is_reparse_point(link) is True
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_scanner_finds_a_JUNCTION(self, tmp_path: Path):
        """Junctions must be detected via the reparse ATTRIBUTE, not S_ISLNK.

        This is the branch a mutation run proved was untested: on a host where
        symlinks work, every link takes the ``S_ISLNK`` early-return, so deleting
        the ``st_file_attributes`` check left all tests green. A junction has
        ``S_ISLNK == False`` and is only found through the attribute.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        target_dir = tmp_path / "outside_dir"
        target_dir.mkdir()
        (target_dir / "secret.txt").write_text("secret\n", encoding="utf-8")
        link = repo / "zz_junction"
        if not _make_junction(link, target_dir):
            pytest.skip("cannot create a junction on this host")

        try:
            # Preconditions that make this test meaningful.
            assert os.path.islink(link) is False, "host created a symlink, not a junction"
            assert is_reparse_point(link) is True, "junction not detected via attributes"
            assert link in find_reparse_points(repo)
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_junction_does_not_leak_through_grep(self, tmp_path: Path):
        """End-to-end: a junction must not expose outside content to grep."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        target_dir = tmp_path / "outside_dir"
        target_dir.mkdir()
        (target_dir / "leaked.txt").write_text(
            f"GEMINI_API_KEY={FAKE_SECRET}\n", encoding="utf-8")
        link = repo / "zz_junction"
        if not _make_junction(link, target_dir):
            pytest.skip("cannot create a junction on this host")

        set_repo_root(repo)
        try:
            out = grep("FAKE_KEY_FOR_TESTING", ".")
            assert FAKE_SECRET not in out, f"junction leaked through grep: {out}"
        finally:
            if os.path.lexists(link):
                os.unlink(link)
            set_repo_root(None)

    def test_scanner_ignores_ordinary_files(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "normal.py").write_text("x = 1\n", encoding="utf-8")
        assert find_reparse_points(repo) == []

    def test_scan_gate_exits_nonzero_on_a_link(self, tmp_path: Path):
        """The gate command itself must fail (exit 1) when a link is present."""
        base = tmp_path / "repos"
        repo = base / "owner__repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("x\n", encoding="utf-8")
        link = repo / "zz_hidden.pyc"
        if not _make_link(link, secret):
            pytest.skip("cannot create symlink or junction on this host")

        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "scan_repo_links.py"),
                 "--repo-dir", str(base)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            assert proc.returncode == 1, (
                f"scan did not fail on a planted link; rc={proc.returncode}\n{proc.stdout}"
            )
            assert "zz_hidden.pyc" in proc.stdout
        finally:
            if os.path.lexists(link):
                os.unlink(link)

    def test_scan_gate_passes_on_a_clean_tree(self, tmp_path: Path):
        base = tmp_path / "repos"
        repo = base / "owner__repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "scan_repo_links.py"),
             "--repo-dir", str(base)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0, proc.stdout

    def test_git_status_is_blind_to_a_pyc_link(self, tmp_path: Path):
        """Documents WHY the scanner cannot rely on git status.

        This is the gate-evasion the orchestrator measured: with `*.pyc` in
        .gitignore, `git status --porcelain` reports a clean tree while the link
        sits in the checkout.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("x\n", encoding="utf-8")
        link = repo / "zz_hidden.pyc"
        if not _make_link(link, secret):
            pytest.skip("cannot create symlink or junction on this host")

        try:
            subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True)
            proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            assert "zz_hidden.pyc" not in (proc.stdout or ""), (
                "git status unexpectedly saw the link; the blind spot this test "
                "documents may have changed"
            )
            # ...and the scanner, unlike git, DOES see it.
            assert find_reparse_points(repo) != []
        finally:
            if os.path.lexists(link):
                os.unlink(link)
