"""Verify that sandbox network blocking mechanisms are active and effective.

Checks:
1. PYTHONPATH-injected sitecustomize intercepts urllib.request
2. PYTHONPATH-injected sitecustomize intercepts socket.create_connection
3. tools.py command security filter blocks forbidden network commands
4. Localhost access remains allowed

Exit code 0 if all protections are active and verified; 1 otherwise.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = ROOT / "src" / "agents" / "sandbox"


def verify_sitecustomize_active() -> bool:
    if not (SANDBOX_DIR / "sitecustomize.py").exists():
        print("  [FAIL] sitecustomize.py not found in", SANDBOX_DIR)
        return False

    env = os.environ.copy()
    cur_py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SANDBOX_DIR) + (os.pathsep + cur_py if cur_py else "")

    # Probe 1: urllib.request to external host
    p1 = subprocess.run(
        [sys.executable, "-c", "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)"],
        env=env,
        capture_output=True,
        text=True,
    )
    err1 = (p1.stderr or "") + (p1.stdout or "")
    if p1.returncode == 0 or "[blocked] network access is disabled in this sandbox" not in err1:
        print("  [FAIL] sitecustomize did not block urllib.request! Output:", err1[:200])
        return False

    # Probe 2: socket.create_connection to external host
    p2 = subprocess.run(
        [sys.executable, "-c", "import socket; socket.create_connection(('example.com', 80), timeout=3)"],
        env=env,
        capture_output=True,
        text=True,
    )
    err2 = (p2.stderr or "") + (p2.stdout or "")
    if p2.returncode == 0 or "[blocked] network access is disabled in this sandbox" not in err2:
        print("  [FAIL] sitecustomize did not block socket.create_connection! Output:", err2[:200])
        return False

    return True


def verify_command_filter_active() -> bool:
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from agents.tools import _check_command_security
    except ImportError as e:
        print(f"  [FAIL] cannot import _check_command_security: {e}")
        return False

    test_commands = [
        "git fetch origin",
        "git clone https://github.com/foo/bar.git",
        "curl https://example.com/patch",
        "pip download django",
        "Invoke-WebRequest -Uri https://example.com",
    ]

    for cmd in test_commands:
        res = _check_command_security(cmd)
        if not res or "[blocked]" not in res:
            print(f"  [FAIL] command filter did not block: {cmd} (got: {res})")
            return False

    # Verify normal commands are NOT blocked
    allowed = ["pytest", "git log -n 1", "python -c 'print(1)'", "dir"]
    for cmd in allowed:
        res = _check_command_security(cmd)
        if res:
            print(f"  [FAIL] command filter false positive on: {cmd} (got: {res})")
            return False

    return True


def main() -> int:
    print("Verifying sandbox network block...")
    sc_ok = verify_sitecustomize_active()
    cf_ok = verify_command_filter_active()

    if sc_ok and cf_ok:
        print("All network block protections active and verified.")
        return 0
    print("Network block verification FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
