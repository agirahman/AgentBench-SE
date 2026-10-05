"""Tests for sandbox network block and command security filter."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.tools import _check_command_security, run_tests, set_repo_root

SANDBOX_DIR = ROOT / "src" / "agents" / "sandbox"


def test_sitecustomize_blocks_external_socket():
    env = os.environ.copy()
    cur = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SANDBOX_DIR) + (os.pathsep + cur if cur else "")

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; s = socket.socket(); s.connect(('example.com', 80))",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = (proc.stderr or "") + (proc.stdout or "")
    assert "[blocked] network access is disabled in this sandbox" in err


def test_sitecustomize_blocks_socket_create_connection():
    env = os.environ.copy()
    cur = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SANDBOX_DIR) + (os.pathsep + cur if cur else "")

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.create_connection(('example.com', 80), timeout=2)",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = (proc.stderr or "") + (proc.stdout or "")
    assert "[blocked] network access is disabled in this sandbox" in err


def test_sitecustomize_blocks_urllib_external():
    env = os.environ.copy()
    cur = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SANDBOX_DIR) + (os.pathsep + cur if cur else "")

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import urllib.request; urllib.request.urlopen('https://example.com', timeout=2)",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = (proc.stderr or "") + (proc.stdout or "")
    assert "[blocked] network access is disabled in this sandbox" in err


def test_sitecustomize_allows_localhost_socket():
    env = os.environ.copy()
    cur = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SANDBOX_DIR) + (os.pathsep + cur if cur else "")

    # Connecting to localhost on an unbound port should raise ConnectionRefusedError,
    # NOT PermissionError([blocked] ...)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; s = socket.socket(); s.connect(('127.0.0.1', 65432))",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    err = (proc.stderr or "") + (proc.stdout or "")
    assert "[blocked]" not in err, "localhost connection must not be blocked by sandbox guard"


@pytest.mark.parametrize(
    "cmd",
    [
        "git fetch origin",
        "git clone https://github.com/foo/bar.git",
        "git pull",
        "git remote add upstream https://...",
        "pip install requests",
        "pip download django",
        "python -m pip install foo",
        "curl https://example.com/patch",
        "curl.exe -O https://example.com",
        "wget https://example.com",
        "Invoke-WebRequest -Uri https://example.com",
        "Invoke-RestMethod -Uri https://example.com",
        "Start-BitsTransfer -Source https://example.com",
        "certutil -urlcache -split -f http://example.com/file",
        "bitsadmin /transfer job http://example.com/file",
    ],
)
def test_command_filter_blocks_forbidden_commands(cmd):
    res = _check_command_security(cmd)
    assert res is not None
    assert res.startswith("[blocked]")


@pytest.mark.parametrize(
    "cmd",
    [
        "pytest",
        "pytest tests/test_tools.py",
        'python -c "print(42)"',
        "git log --oneline -n 5",
        "git show HEAD:requests/adapters.py",
        "git diff HEAD~1",
        "git status",
        "dir",
        "ls -la",
        "findstr /s 'def ' *.py",
        "type README.md",
        "cd tests && python runtests.py",
    ],
)
def test_command_filter_allows_normal_commands(cmd):
    res = _check_command_security(cmd)
    assert res is None, f"command filter falsely blocked benign command: {cmd}"


def test_run_tests_normal_execution_unaffected(tmp_path):
    set_repo_root(tmp_path)
    res = run_tests('python -c "print(100 + 23)"')
    assert "[exit code 0]" in res
    assert "123" in res


def test_run_tests_blocks_forbidden_command(tmp_path):
    set_repo_root(tmp_path)
    res = run_tests("git fetch origin")
    assert res.startswith("[blocked]")


def test_run_tests_blocks_python_network_probe(tmp_path):
    set_repo_root(tmp_path)
    res = run_tests(
        'python -c "import socket; socket.create_connection((\'example.com\', 80), timeout=2)"'
    )
    assert "[blocked] network access is disabled in this sandbox" in res
    assert "[tests unavailable]" not in res
