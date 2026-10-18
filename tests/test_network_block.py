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


def _docker_daemon_alive() -> bool:
    """True when the real Docker daemon answers. Used only to SKIP, never to pass."""
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=15
        )
        return proc.returncode == 0
    except Exception:
        return False


def _sandbox_image_present() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "agentbench-sandbox:latest"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0
    except Exception:
        return False


#: These tests execute a real command through run_tests, which in the default
#: (docker) mode needs a live daemon plus the built sandbox image. Without it
#: they FAIL on infrastructure, not on the property they pin -- so they skip.
#: Same pattern as tests/test_docker_sandbox.py::requires_docker.
requires_docker = pytest.mark.skipif(
    not (_docker_daemon_alive() and _sandbox_image_present()),
    reason="requires a running Docker daemon with agentbench-sandbox:latest built",
)


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
def test_command_filter_blocks_forbidden_commands(cmd, monkeypatch):
    """Mode-INDEPENDENT blocks: no legitimate offline use, so never relaxed.

    pip is deliberately NOT in this list any more (2026-10-08). It is blocked only
    where the network is reachable; see
    test_pip_is_blocked_outside_the_docker_sandbox below for that half.
    """
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    res = _check_command_security(cmd)
    assert res is not None
    assert res.startswith("[blocked]")


#: pip install/download is the ONE rule that depends on the mode. It is allowed in
#: docker mode because --network=none plus `no-index = true` in the image mean pip
#: can only reach the local wheelhouse, so a released (oracle) package cannot be
#: fetched. Outside docker the host HAS a network, so the same command could pull
#: one, and it stays blocked.
#:
#: EVERY SPELLING IS LISTED, because the first version of the guard was defeated by
#: ordinary rewrites. Measured against that version: 12 of 17 escaped --
#: `python3 -m pip`, `python3.9`, `pip3.9`, `sudo pip`, `env pip`, `uv pip`, `pipx`,
#: `PIP_NO_INDEX=0 pip`, and a Windows `C:\Python39\python.exe -m pip`. A guard that
#: the obvious spellings walk around is not a guard, so these are pinned by name.
_PIP_COMMANDS = [
    "pip install requests",
    "pip download django",
    "python -m pip install foo",
    # versioned interpreters
    "python3 -m pip install django",
    "python3.9 -m pip install django",
    "python3.9 -m pip download django",
    "python3 -m pip download django",
    # versioned pip
    "pip3 install django",
    "pip3.9 install django",
    # privilege / environment wrappers
    "sudo pip install django",
    "sudo python3 -m pip install django",
    "env pip install django",
    # VAR=value prefix (the reviewer's PIP_NO_INDEX=0 case)
    "PIP_NO_INDEX=0 pip install django",
    "PIP_INDEX_URL=http://evil pip install django",
    # other installers that reach the same index
    "uv pip install django",
    "pipx install django",
    # path prefixes
    "/usr/bin/pip install django",
    r"C:\Python39\python.exe -m pip install django",
    # inside a compound command
    "echo hi && python3 -m pip install django",
    "cd /tmp; pip3 install django",
    # reviewer round 2 (2026-10-08): CLASSES, not spellings. The old pattern
    # required the subcommand directly after pip, so a global option between
    # them (`pip --isolated install`, valid pip) walked around it; and
    # subshell/substitution wrappers were not separators.
    "pip --isolated install django",
    "pip --disable-pip-version-check install django",
    "python -m pip --isolated install django",
    "(pip install django)",
    "`pip install django`",
    "$(pip install django)",
    'bash -c "pip install django"',
    "sh -c 'pip install django'",
    "xargs pip install django",
    "eval pip install django",
    "nice pip install django",
    "pipx runpip django install x",
    # reviewer round 3 (final audit, 2026-10-08): the QUOTING class. The shell
    # removes quotes and escapes BEFORE exec, so these run pip while containing
    # no literal `pip`; enumerating spellings loses that race, which is why the
    # guard normalises the command before matching (_shell_normalizations).
    # Every one of these was measured RUNNING pip 23.0.1 in the container.
    "p'i'p install django",
    'p"i"p install django',
    "pi\\p install django",
    "'pip' install django",
    '"pip" install django',
    "{ pip install django; }",
    "pip --proxy=http://x install django",
    "env -i pip install django",
    "pip \\\n install django",
    # Line continuation INSIDE the tool name: POSIX deletes the backslash-newline
    # pair, joining the token, so this runs pip. Measured in the container:
    # `pi\<newline>p --version` -> pip 23.0.1. The guard therefore REMOVES the
    # pair instead of substituting a space.
    "pi\\\np install django",
    "p\\\ni\\\np install django",
    # reviewer round 4 (final audit, 2026-10-08): shell CONTROL STRUCTURES and
    # interpreter flags. `if true; then pip install django; fi` contains no
    # separator the pattern knew, and this host has C:\Windows\System32\bash.exe
    # with a WSL Ubuntu that HAS INTERNET (measured: urlopen(pypi.org) -> 200).
    # The reviewer used exactly this to download a real django-6.1.2 wheel
    # (8.4 MB). The `-E` forms are the other half: they make the interpreter
    # ignore PYTHONPATH, so the injected sitecustomize never loads.
    "if true; then pip install django; fi",
    "if true; then pip download django; fi",
    "while true; do pip install django; break; done",
    "until false; do pip install django; break; done",
    "for i in 1; do pip install django; done",
    "case x in x) pip install django;; esac",
    "select x in a; do pip install django; done",
    "if true; then if true; then pip install django; fi; fi",
    "bash -c 'if true; then pip install django; fi'",
    "sh -c 'while true; do pip install django; break; done'",
    "python -E -m pip download django",
    "python -I -m pip download django",
    "python -S -m pip download django",
    "python -Es -m pip download django",
    "if true; then python -E -m pip download django; fi",
    # The interpreter flag matters even WITHOUT pip: -E/-I ignore PYTHONPATH and
    # -S skips site, so the injected sitecustomize never loads and direct urllib
    # reaches the network. Measured on this host: with the flag, the sandbox's
    # socket block is simply absent. The env lock does not cover this path (it
    # only configures pip), so the guard is the control here.
    "python -E -c \"import urllib.request; urllib.request.urlretrieve('https://pypi.org/x.whl')\"",
    "python -I -c \"import socket; socket.create_connection(('pypi.org', 443))\"",
    "python -S -c \"import urllib.request\"",
    "python -Es script.py",
    "bash -c 'python -E -c \"import urllib.request\"'",
    "if true; then python -E -c \"import urllib.request\"; fi",
]


@pytest.mark.parametrize("cmd", _PIP_COMMANDS)
def test_pip_is_allowed_in_the_docker_sandbox(cmd, monkeypatch):
    """Operator decision 2026-10-08: the agent may install deps in the sandbox.

    The safety argument is structural, not a matter of trust: the container runs
    --network=none and the image sets `no-index = true` with
    `find-links = /opt/wheelhouse`, so pip resolves ONLY from the local
    wheelhouse. Verified in the image:
        pip install requests  ->  "No matching distribution found for requests"
    """
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    assert _check_command_security(cmd) is None, (
        f"pip was blocked in docker mode, so the agent cannot install a "
        f"dependency it needs: {cmd}"
    )


@pytest.mark.parametrize("cmd", _PIP_COMMANDS)
def test_pip_is_blocked_outside_the_docker_sandbox(cmd, monkeypatch):
    """In local mode the host has a working network, so pip could fetch an oracle.

    This is the half that makes the relaxation safe. Without it, "allow pip" would
    be a blanket hole: a local-mode run could `pip install django` and read a
    released Django -- a later revision than the base commit, i.e. the fix.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    res = _check_command_security(cmd)
    assert res is not None, (
        f"pip was allowed in local mode, where the network is reachable and a "
        f"released package could be downloaded: {cmd}"
    )
    assert res.startswith("[blocked]")
    assert "wheelhouse" in res or "Docker" in res, (
        f"the refusal should say where pip IS allowed, so the agent can act on it: "
        f"{res}"
    )


def test_pip_defaults_to_the_active_mode(monkeypatch):
    """With no explicit mode, the guard reads the ACTIVE one.

    Defaulting to a fixed value would let the guard drift from the execution path:
    the tests could pass while a real run blocked (or allowed) the wrong thing.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    assert _check_command_security("pip install requests") is not None
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    assert _check_command_security("pip install requests") is None


@pytest.mark.parametrize(
    "cmd",
    [
        # The broadened pattern must not swallow ordinary work. Each of these
        # contains a word the pattern looks for (pip/python/uv) but is not an
        # install, so blocking it would cost the agent a turn for nothing.
        "pytest tests/test_a.py",
        "python -m pytest -x -q",
        "python3.9 -m pytest tests/",
        "python -m pytest --collect-only",
        "pip --version",
        "pip list",
        "pip show requests",
        "python -m pip --version",
        "cat pip_notes.txt",
        "ls /opt/wheelhouse",
        "grep -r 'pip install' README.md",
        "echo 'pip install django'",
        "python -m unittest discover",
        "uv --version",
        "pipx list",
        # reviewer round 3 (final audit): normalisation must not create false
        # positives. Stripping quotes and backslashes is aggressive, so these pin
        # that the guard still lets ordinary work through -- each one CONTAINS a
        # token the pattern looks for, but is not a pip install.
        "grep -r 'pip' .",
        "cat recipes.pip",
        "echo hello | grep pip",
        "ls /pip/install",
        # reviewer round 4: treating shell keywords as separators must not fire on
        # legitimate shell idioms that merely MENTION pip. These are ordinary
        # loops and conditionals over text; blocking them would cost the agent
        # turns for nothing, and they are the exact traps the reviewer named.
        "if grep -q pip file; then echo found; fi",
        "for f in *.py; do grep pip $f; done",
        'echo "install: pip install foo"',
        "while read line; do echo $line; done < file",
        "case $1 in start) echo starting;; esac",
        # reviewer round 5 (finding I): the 6 regressions the F2 fix introduced.
        # The keyword separators made text INSIDE QUOTES get scanned as commands,
        # so a quoted argument that merely contains the words was refused. These
        # were ALLOWED before F2 and must stay allowed: the quoted region is DATA
        # (an argument to grep/echo/python), not a command line. The guard now
        # MASKS quoted regions and only unwraps quote-splices, so a spelling trick
        # like p'i'p is still caught while these stay inert.
        "grep -rn 'then pip install' docs/",
        "grep -rn 'if pip install' docs/",
        "grep -rn 'while pip install' docs/",
        "echo 'do pip install x'",
        "echo 'if you run then pip install works'",
        "python -c 'print(\"pip install\")'",
        # and the same property one level up: a whole loop inside a quoted string.
        "grep -rn 'while true; do pip install django; done' file",
        # reviewer round 6 (finding M): a heredoc whose target consumes the body as
        # DATA. `cat` never executes its stdin, so a body that happens to contain a
        # pip line is inert text and must be allowed -- refusing it would punish the
        # ordinary "write a file with cat <<EOF" pattern.
        "cat <<EOF\npip install django\nEOF",
        "grep -n pip <<EOF\npip install\nEOF",
        "tee notes.txt <<EOF\npip install django\nEOF",
        "cat <<-EOF\npip install django\nEOF",
        "cat <<'EOF'\npip install django\nEOF",
    ],
)
def test_the_pip_guard_does_not_block_ordinary_commands(cmd, monkeypatch):
    """A false positive here burns a turn and teaches the agent nothing.

    The pattern is deliberately broad to catch rewrites, so this is the other half:
    it must still let pytest, `pip --version`, `pip list`, and a string that merely
    MENTIONS pip install go through.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    assert _check_command_security(cmd) is None, (
        f"the pip guard falsely blocked a benign command: {cmd}"
    )


#: Heredoc bodies that are CODE, not data: the feeding program executes them, so the
#: guard must read the body. Measured (reviewer, finding M): masking heredocs
#: unconditionally let 14/14 interpreters escape, because "mask unless the target is
#: a shell" is a list -- and the list was incomplete. The fix inverts the default:
#: a body is masked ONLY for a short list of pure data filters, so an interpreter
#: nobody enumerated is covered without needing a new entry.
#:
#: The first group is the reviewer's measured regression set (all were BLOCK before
#: the heredoc mask landed). The second is the class the reviewer generalised to.
_HEREDOC_CODE = [
    # --- reviewer's 7 measured regressions ---
    "python3 <<'EOF'\npip install django\nEOF",
    "python3 - <<EOF\npip install django\nEOF",
    "wsl bash <<EOF\npip install django\nEOF",
    "wsl.exe -e bash <<EOF\npip install django\nEOF",
    "C:\\Windows\\System32\\bash.exe <<EOF\npip install django\nEOF",
    "busybox sh <<EOF\npip install django\nEOF",
    "bash -c 'cat <<EOF\npip install django\nEOF'",
    # --- reviewer's 4 substitution regressions ---
    'eval "$(cat <<EOF\npip install django\nEOF)"',
    "bash <(cat <<EOF\npip install django\nEOF)",
    "source /dev/stdin <<EOF\npip install django\nEOF",
    ". /dev/stdin <<EOF\npip install django\nEOF",
    # --- the class: interpreters that take a script on stdin ---
    "perl <<EOF\nsystem 'pip install django'\nEOF",
    "ruby <<EOF\n`pip install django`\nEOF",
    "node <<EOF\nrequire('child_process').execSync('pip install django')\nEOF",
    "php <<EOF\n<?php system('pip install django'); ?>\nEOF",
    "lua <<EOF\nos.execute('pip install django')\nEOF",
    "tclsh <<EOF\nexec pip install django\nEOF",
    "expect <<EOF\nspawn pip install django\nEOF",
    "Rscript <<EOF\nsystem('pip install django')\nEOF",
    # --- a program NOT on the data list is fail-closed ---
    "mysteryprog <<EOF\npip install django\nEOF",
    # --- a real command AFTER a data heredoc must still be seen (M7) ---
    "cat <<'E1'\nsome text\nE1\npip install django",
]


@pytest.mark.parametrize("cmd", _HEREDOC_CODE)
def test_a_heredoc_body_that_would_execute_is_blocked(cmd, monkeypatch):
    """Fail-closed: only a known data filter may have its heredoc body masked.

    Each case here feeds the body to something that RUNS it -- a shell, an
    interpreter, an unknown program, or a command substitution -- or places a real
    command after the heredoc. Masking any of them would hide the pip install from
    the guard.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    res = _check_command_security(cmd)
    assert res is not None, (
        f"a heredoc whose body would execute was NOT blocked, so the guard cannot "
        f"see the command it becomes: {cmd!r}"
    )


#: The `-c` flag may be COMBINED with others (`bash -lc`, `bash -lxc`), which the
#: shell accepts as equivalent to `-c`. Measured: the earlier `(?:-[A-Za-z]+)*\s+-c`
#: form missed `-lc` because the flag group consumed it and then demanded a separate
#: `-c` -- a regression that let `bash -lc 'pip install ...'` escape.
_REINVOKE_COMBINED_FLAGS = [
    "bash -lc 'pip install django'",
    "bash -lxc 'pip install django'",
    "bash -lc 'pip download django'",
    "bash -lc 'python -m pip install django'",
    "bash -lc 'uv pip install django'",
    "bash -lc \"p'i'p install django\"",
]


@pytest.mark.parametrize("cmd", _REINVOKE_COMBINED_FLAGS)
def test_a_shell_reinvocation_with_combined_flags_is_blocked(cmd, monkeypatch):
    """`bash -lc '...'` runs the string, so the string must be analysed as code."""
    monkeypatch.setenv("SANDBOX_MODE", "local")
    res = _check_command_security(cmd)
    assert res is not None, f"a combined-flag shell re-invocation escaped: {cmd!r}"


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


@requires_docker
def test_run_tests_normal_execution_unaffected(tmp_path, monkeypatch):
    # Pin the mode this test is ABOUT. Without it the test inherits the ambient
    # SANDBOX_MODE, and running the suite with SANDBOX_MODE=local makes it fail
    # on the H refusal instead of on the property it pins.
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    set_repo_root(tmp_path)
    res = run_tests('python -c "print(100 + 23)"')
    assert "[exit code 0]" in res
    assert "123" in res


def test_run_tests_blocks_forbidden_command(tmp_path):
    set_repo_root(tmp_path)
    res = run_tests("git fetch origin")
    assert res.startswith("[blocked]")


def test_the_env_lock_removes_pips_index():
    """The control that a flag CANNOT bypass: pip's own configuration.

    Measured (final reviewer audit): `python -E -m pip download django` bypassed
    the injected sitecustomize and downloaded django-6.1.2 (8.4 MB) from PyPI,
    because -E makes the interpreter ignore PYTHON* variables. Guarding the
    spelling is not enough on its own -- this host also has a WSL bash with
    internet, and an env var does not cross into it.

    So local execution also sets PIP_NO_INDEX=1, which pip reads ITSELF. Measured
    on this host: with it set, `pip download django` refuses even when the command
    passes `--index-url https://pypi.org/simple` or `--find-links ...`, and it
    survives `-E`. This test pins the lock, not the download.
    """
    from agents.tools import _env_without_index

    locked = _env_without_index({"PATH": "/usr/bin"})
    assert locked["PIP_NO_INDEX"] == "1", (
        "the local-mode environment no longer disables pip's index, so a spelling "
        "the guard has never seen could fetch a released (oracle) package"
    )
    assert locked["PIP_FIND_LINKS"] == "", (
        "PIP_FIND_LINKS must be emptied: a leftover value would let pip resolve a "
        "package from a URL even with no-index set"
    )
    # The lock must not clobber the rest of the environment.
    assert locked["PATH"] == "/usr/bin"
    # And it must be a COPY: mutating the caller's dict would leak PIP_NO_INDEX
    # into the parent process (and into docker-mode runs).
    source = {"PATH": "/x"}
    _env_without_index(source)
    assert source == {"PATH": "/x"}, "the helper mutated the environment it was given"


def test_the_env_lock_is_applied_to_local_execution(tmp_path, monkeypatch):
    """The lock must be WIRED IN, not merely defined.

    A helper that nothing calls is decoration. This runs a real command through
    run_tests and reads back what the child process actually saw, which is the only
    way to prove the lock reaches pip.

    Uses ``local-unsafe`` because plain ``local`` now REFUSES to execute anything
    (see the H finding): the mode that reaches the host-execution branch is the
    test-only one.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local-unsafe")
    monkeypatch.delenv("PIP_NO_INDEX", raising=False)
    set_repo_root(tmp_path)
    try:
        probe = (
            f'"{sys.executable}" -c "import os; '
            f"print('SEEN=' + repr(os.environ.get('PIP_NO_INDEX')))"
            f'"'
        )
        res = run_tests(probe)
        assert "SEEN='1'" in res, (
            f"local execution did not pass PIP_NO_INDEX=1 to the command, so pip "
            f"still has an index to fetch from: {res!r}"
        )
    finally:
        set_repo_root(None)


def test_local_mode_refuses_to_execute_anything(tmp_path, monkeypatch):
    """H, structurally: plain ``local`` must not run the command at all.

    The reviewer downloaded a real django-6.1.2 wheel (8.4 MB) through local mode
    with `bash -c "python3 -c '...urllib...'"` -- a command that never mentions
    pip, so the pip guard could not see it, and the PIP_* env lock does not cross
    into WSL. Measured: 6 of 7 spellings escaped. An allowlist of test runners does
    not close it either, because pytest executes agent-authored files that can
    spawn bash themselves (measured: a test file downloaded a wheel).

    So the control is the ABSENCE of execution. This asserts the command has no
    effect: the side effect it would produce must not appear.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    set_repo_root(tmp_path)
    canary = tmp_path / "canary.txt"
    try:
        res = run_tests(f'python -c "open(r\'{canary}\', \'w\').write(\'ran\')"')
        assert res.startswith("[blocked]"), (
            f"local mode executed a command instead of refusing: {res!r}"
        )
        assert not canary.exists(), (
            "local mode refused in text but the command still ran -- the refusal "
            "must happen BEFORE execution, not after"
        )
        # The message must say what to do instead, not just "no".
        assert "docker" in res.lower(), (
            f"the refusal should point at docker mode: {res!r}"
        )
    finally:
        set_repo_root(None)


@pytest.mark.parametrize("cmd", [
    'bash -c "python3 -c \'import urllib.request\'"',
    "wsl.exe -d Ubuntu -- python3 -c \"import urllib.request\"",
    "cmd /c wsl python3 -c \"import urllib.request\"",
    r"C:\Windows\System32\bash.exe -c \"python3 -c 'import urllib.request'\"",
    "powershell -Command Start-Process python3",
    "bash -c 'pip install django'",
    "python3 -c \"import urllib.request; urllib.request.urlretrieve('https://pypi.org/x')\"",
])
def test_local_mode_refuses_the_wsl_vectors(cmd, tmp_path, monkeypatch):
    """The spellings the reviewer used, and the ones a pattern would miss next.

    Each of these is a DIFFERENT way to reach the host's WSL bash or a bare
    interpreter. They are refused by the same structural refusal, which is the
    point: no list of wrappers is consulted, so a spelling not listed here is
    refused too.
    """
    monkeypatch.setenv("SANDBOX_MODE", "local")
    set_repo_root(tmp_path)
    try:
        res = run_tests(cmd)
        assert res.startswith("[blocked]"), (
            f"local mode executed a network-capable command: {cmd!r} -> {res!r}"
        )
    finally:
        set_repo_root(None)


def _active_sandbox_mode() -> str:
    """The mode run_tests will actually use, read the same way production reads it.

    Delegates to the production function rather than re-reading the environment
    with its own default: an earlier copy of this helper hard-coded 'local', so
    once the production default became 'docker' the helper reported the WRONG mode
    and the test asserted the local-mode message while running in a container.
    """
    from agents.tools import get_sandbox_mode

    return get_sandbox_mode()


def test_local_unsafe_is_documented_as_not_isolated(monkeypatch):
    """The unisolated test mode must SAY it is unisolated, where it is defined.

    Finding O (audit M+N): `local-unsafe` runs agent commands on the host and the
    guard does not contain the cross-language subprocess path -- measured, an
    `os.system('curl ...')` downloaded an 8.4 MB oracle there. The mode is for this
    repository's own test suite, so the fix is not another pattern (that loses the
    same race one language later) but making the property impossible to miss.

    This pins BOTH halves of that:
      * the constant exists and names the measurement, so a reader of the mode
        cannot conclude it is safe;
      * `local-unsafe` actually WARNS at runtime, so a misconfigured run shows up
        in the log instead of being silently unisolated.
    """
    from agents import tools as T

    doc = T._LOCAL_UNSAFE_IS_NOT_ISOLATED
    assert "no network isolation" in doc.lower(), (
        f"the constant must state the property plainly: {doc!r}"
    )
    assert "os.system" in doc and "8.4 MB" in doc, (
        f"the constant must cite the measurement, so it reads as a fact rather "
        f"than a caution: {doc!r}"
    )
    assert "test suite" in doc.lower(), (
        f"the constant must say who the mode is for: {doc!r}"
    )
    # The docstring of the mode reader carries the same warning: that is where a
    # developer looks first.
    assert "NO network isolation" in T.get_sandbox_mode.__doc__, (
        "get_sandbox_mode's docstring must warn that local-unsafe is unisolated"
    )

    # The runtime warning fires, once, on the first command in that mode.
    T._LOCAL_UNSAFE_WARNED[0] = False
    warnings: list[str] = []
    original = T._warn
    T._warn = lambda msg: warnings.append(msg)
    try:
        monkeypatch.setenv("SANDBOX_MODE", "local-unsafe")
        set_repo_root(ROOT)
        try:
            run_tests("echo first")
            run_tests("echo second")
        finally:
            set_repo_root(None)
    finally:
        T._warn = original
        T._LOCAL_UNSAFE_WARNED[0] = False

    hits = [w for w in warnings if "local-unsafe" in w]
    assert hits, (
        f"local-unsafe executed a command WITHOUT warning that it is unisolated; "
        f"warnings seen: {warnings}"
    )
    assert len(hits) == 1, (
        f"the warning must fire once per process, not on every call: {hits}"
    )


@requires_docker
def test_run_tests_blocks_python_network_probe(tmp_path, monkeypatch):
    """The probe must FAIL under whichever isolation is active.

    The two modes block the network by different mechanisms, so they fail with
    different messages — asserting only one of them makes this test wrong in the
    other mode:

    * ``local``  — the injected ``sitecustomize`` raises
      ``PermissionError("[blocked] network access is disabled in this sandbox")``.
    * ``docker`` — ``--network=none`` removes the interface, so the socket fails at
      the OS level: "Temporary failure in name resolution" / "Network is unreachable".

    The assertion is split per mode rather than weakened to a single generic check:
    a generic "it failed somehow" would still pass if the probe failed for an
    unrelated reason (a typo, a missing interpreter), and would stop detecting the
    loss of either isolation layer.
    """
    # The decorator requires a live daemon, so the mode under test is docker.
    # Pin it: without this the test inherits the ambient SANDBOX_MODE, and running
    # the suite with SANDBOX_MODE=local fails on the H refusal instead of here.
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    set_repo_root(tmp_path)
    res = run_tests(
        'python -c "import socket; socket.create_connection((\'example.com\', 80), timeout=2)"'
    )

    assert "[tests unavailable]" not in res

    # Mode-independent: the probe must not have reached the network and succeeded.
    # This is the assertion that goes RED if --network=none (docker) or the
    # sitecustomize injection (local) is removed — the connection then succeeds.
    assert "[exit code 0]" not in res, (
        f"the network probe SUCCEEDED, so isolation is not active: {res}"
    )

    if _active_sandbox_mode() == "docker":
        assert (
            "Temporary failure in name resolution" in res
            or "Network is unreachable" in res
        ), f"docker mode must block the network at the OS level; got: {res}"
    else:
        assert "[blocked] network access is disabled in this sandbox" in res
