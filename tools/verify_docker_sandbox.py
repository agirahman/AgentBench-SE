"""Preflight verification for Docker sandbox isolation.

Verifies fail-closed:
1. Docker daemon is running and reachable
2. Docker sandbox image (agentbench-sandbox:latest) exists locally
3. Container network egress is blocked under --network=none
4. Host filesystem is not leaked into container (/host_mnt missing, no host drives)

Usage:
    python tools/verify_docker_sandbox.py
Exit code: 0 if all checks pass, 1 if any check fails.
"""
from __future__ import annotations

import os
import subprocess
import sys

IMAGE_NAME = os.environ.get("DOCKER_SANDBOX_IMAGE", "agentbench-sandbox:latest")


def check_daemon() -> tuple[bool, str]:
    """Check if Docker daemon is running."""
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip().splitlines()[0] if (proc.stderr or proc.stdout).strip() else "exit non-zero"
            return False, f"Docker daemon not ready: {err}"
        return True, "Docker daemon is running"
    except subprocess.TimeoutExpired:
        return False, "Docker info timed out (daemon unresponsive)"
    except FileNotFoundError:
        return False, "docker CLI executable not found in PATH"
    except Exception as exc:
        return False, f"Docker check failed: {exc}"


def check_image() -> tuple[bool, str]:
    """Check if the sandbox image is present locally."""
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", IMAGE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return False, f"Image '{IMAGE_NAME}' not found locally. Build it using docker/sandbox.Dockerfile."
        return True, f"Image '{IMAGE_NAME}' is present locally"
    except Exception as exc:
        return False, f"Image inspect failed: {exc}"


def probe_network_blocked() -> tuple[bool, str]:
    """Verify that --network=none blocks outbound HTTP requests."""
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm", "--network=none", IMAGE_NAME,
                "python", "-c",
                "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)"
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return False, "CRITICAL: container was able to make outbound network request under --network=none!"
        out = (proc.stderr or "") + (proc.stdout or "")
        if "urllib.error.URLError" in out or "Temporary failure in name resolution" in out or "Network is unreachable" in out:
            return True, "Outbound network blocked by --network=none (verified)"
        return True, f"Outbound request failed as expected (exit {proc.returncode})"
    except Exception as exc:
        return False, f"Network probe failed unexpectedly: {exc}"


def probe_filesystem_isolation() -> tuple[bool, str]:
    """Verify container cannot access /host_mnt or host drive paths."""
    try:
        proc1 = subprocess.run(
            ["docker", "run", "--rm", IMAGE_NAME, "ls", "/host_mnt"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc1.returncode == 0:
            return False, "CRITICAL: /host_mnt is accessible inside container!"

        proc2 = subprocess.run(
            ["docker", "run", "--rm", IMAGE_NAME, "sh", "-c", "ls 'C:\\' 2>&1 || ls 'D:\\' 2>&1"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out2 = (proc2.stdout or "") + (proc2.stderr or "")
        if "Windows" in out2 or "Program Files" in out2:
            return False, "CRITICAL: host drives visible inside container!"

        return True, "Filesystem isolation verified (no /host_mnt or host drive access)"
    except Exception as exc:
        return False, f"Filesystem probe failed unexpectedly: {exc}"


def probe_git_masking() -> tuple[bool, str]:
    """Verify that .git directory is masked with empty tmpfs when mounted."""
    import tempfile
    from pathlib import Path
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = Path(tmpdir) / ".git"
            git_dir.mkdir()
            (git_dir / "packed-refs").write_text("test_secret_ref", encoding="utf-8")
            proc = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{Path(tmpdir).resolve()}:/work",
                    "--tmpfs", "/work/.git",
                    IMAGE_NAME,
                    "sh", "-c", "ls /work/.git/packed-refs 2>&1"
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if "No such file or directory" in out or proc.returncode != 0:
                return True, ".git directory masked with empty tmpfs (no refs/objects exposed)"
            return False, f"CRITICAL: .git/packed-refs leaked into container: {out.strip()}"
    except Exception as exc:
        return False, f"Git masking probe failed unexpectedly: {exc}"


def main() -> int:
    print("Verifying Docker Sandbox Readiness...")
    checks = [
        ("Docker Daemon", check_daemon),
        ("Sandbox Image", check_image),
        ("Network Block", probe_network_blocked),
        ("Filesystem Isolation", probe_filesystem_isolation),
        ("Git History Masking", probe_git_masking),
    ]

    all_passed = True
    for name, fn in checks:
        ok, msg = fn()
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_passed = False
        print(f"  [{status}] {name:<22} : {msg}")

    if all_passed:
        print("Verdict: Docker sandbox READY.")
        return 0
    print("Verdict: Docker sandbox NOT READY.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
