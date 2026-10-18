"""Regression probes for sandbox escape vectors.

Validates that NO bypass can leak solutions, network data, host filesystem files,
or pre-installed oracle packages when running under Docker sandbox isolation.

Covers the vectors identified in docs/VALIDATION_SANDBOX_20261005.md:
- B1-B10: network bypasses (python -E, python -S, powershell, cmd, curl, nested
  subprocess, git ls-remote / remote show / fetch URL, node)
- F1-F7: filesystem traversal bypasses (results patches, predictions, _hits.txt,
  .env, logs, other checkouts, old predictions)
- ORA: host site-packages oracle (target libraries must not be importable)
- GIT / PACK: `.git` history must be masked (binary absence is NOT enough -- the
  packfile is readable with plain Python)

Design notes (why this file looks the way it does)
--------------------------------------------------
An earlier version of this file was a TAUTOLOGY, proven by mutation testing:

1. **Wrong leak marker.** B1/B2 looked for ``b'#`` in the downloaded file. The real
   file starts with ``b'\"\"\"``, so a successful 36,983-byte download was reported as
   "BLOCKED". Measured: ``repr(urlopen(...).read()[:20])`` == ``b'\"\"\"\\nHTML Widget clas'``.
   The markers below are the ones measured to actually appear.

2. **"Blocked" because a binary was missing, not because isolation worked.** B3-B10
   ran tools (powershell, curl, git, node) that are simply not installed in the
   image, so they "passed" while proving nothing. Each such probe now checks the
   binary FIRST and reports **N/A - binary absent** when it cannot test isolation.

3. **Probes looked for the ABSENCE of a marker**, which treats any unrelated failure
   (typo, missing interpreter) as "blocked". Probes now look for **evidence of
   SUCCESS**: a pure-Python probe exits ``LEAK_EXIT`` only after it actually reaches
   the network, and any other unexpected exit code is reported INCONCLUSIVE -- which
   fails the run (fail-closed) instead of quietly passing.

4. **Quoting fragility.** Probe source is base64-encoded and ``exec``'d inside the
   container, so no shell layer has to survive nested quotes, backslashes, or
   newlines. The payload alphabet is ``[A-Za-z0-9+/=]``, which sh and cmd.exe both
   pass through untouched.

Usage:
    python tools/probe_sandbox_escape.py [--mode {docker,local}]

Exit codes:
    0 - every applicable probe was blocked (and nothing was inconclusive)
    1 - a leak was detected, or a probe could not be evaluated (fail-closed)
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.tools import run_tests, set_repo_root

#: Exit code a probe uses to signal "I reached the thing I was told to reach".
#: Distinct from 0 (blocked) and from any interpreter/runtime error, so a probe
#: that never ran cannot be mistaken for a probe that was blocked.
LEAK_EXIT = 3

#: The Django 3.0 file used as the download target for network probes. The exact
#: bytes matter: the previous marker was wrong and hid a real 36,983-byte download.
TARGET_URL = (
    "https://raw.githubusercontent.com/django/django/3.0/django/forms/widgets.py"
)
#: Measured on a real download (network deliberately unblocked). Either marker
#: alone would have caught the old bug; both are checked so a change to the file's
#: header cannot silently re-introduce a tautology.
DOWNLOAD_MARKERS = ('b\'"""', "HTML Widget")

STATUS_BLOCKED = "BLOCKED"
STATUS_LEAK = "LEAK"
STATUS_NA = "N/A"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"


def get_django_checkout() -> Path:
    """Find a REAL, usable checkout for testing.

    Picking any directory whose name is 40 hex chars was a bug: the three dummy
    checkouts (``0000...``, ``1111...``, ``2222...``) are exactly 40 chars and sort
    first, so the probe mounted a broken checkout with an EMPTY
    ``.git/objects/pack``. That made the PACK probe unable to ever go RED — it was
    reporting "no packfile readable" because the dummy has no packfile at all,
    which is a tautology of exactly the kind this file exists to prevent.

    A usable checkout must be a real base_commit AND actually contain a packfile,
    so that the GIT/PACK probes have something to hide.
    """
    dummies = {"0" * 40, "1" * 40, "2" * 40}
    repos_dir = ROOT / "datasets" / "repos" / "django" / "django"
    if repos_dir.exists():
        for item in sorted(repos_dir.iterdir()):
            if not item.is_dir() or item.name in dummies:
                continue
            if len(item.name) != 40:
                continue
            pack_dir = item / ".git" / "objects" / "pack"
            if pack_dir.is_dir() and any(pack_dir.glob("*.pack")):
                return item
    return ROOT


#: Binaries confirmed present in the sandbox image that can reach the network.
#: Recorded here so the report states what was actually available, rather than
#: implying the image was minimal when it was not.
KNOWN_BINARIES = (
    "openssl", "perl", "awk", "sed", "base64", "tar", "gzip", "find", "xargs",
    "env", "nohup", "timeout", "getent", "pip", "pip3", "python3.9",
)


def _wrap_python(source: str, flags: str = "") -> str:
    """Build a shell command that runs ``source`` under the sandbox interpreter.

    The source is base64-encoded so that no shell has to parse it: the payload
    alphabet is alphanumeric plus ``+/=``, which survives sh, cmd.exe, and
    PowerShell without escaping. This replaces the old hand-quoted ``python -c
    "..."`` form that broke whenever a nested quote appeared.
    """
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    boot = "import base64;" + "exec(base64.b64decode('" + payload + "'))"
    parts = ["python"]
    if flags:
        parts.append(flags)
    parts.extend(["-c", '"' + boot + '"'])
    return " ".join(parts)


def _parse_exit_code(out: str) -> int | None:
    """Extract the code from run_tests' ``[exit code N]`` header."""
    m = re.search(r"\[exit code (\d+)\]", out)
    return int(m.group(1)) if m else None


def _looks_like_leak(out: str) -> str | None:
    """Return the marker that came back, if any payload reached the probe."""
    for marker in DOWNLOAD_MARKERS:
        if marker.lower() in out.lower():
            return marker
    return None


def run_exit_code_probe(
    source: str,
    leak_desc: str,
    ok_desc: str,
    flags: str = "",
) -> tuple[str, str]:
    """Run a pure-Python probe whose verdict is carried by its exit code.

    ``LEAK_EXIT`` means the probe reached protected data; ``0`` means it was
    blocked. Anything else (interpreter error, syntax error, dead daemon) is
    INCONCLUSIVE -- never silently "blocked".
    """
    out = run_tests(_wrap_python(source, flags))
    code = _parse_exit_code(out)

    marker = _looks_like_leak(out)
    if marker is not None:
        return STATUS_LEAK, f"payload reached the probe: found {marker!r}"

    if code == LEAK_EXIT:
        return STATUS_LEAK, leak_desc
    if code == 0:
        return STATUS_BLOCKED, ok_desc
    if code is None:
        return STATUS_INCONCLUSIVE, f"no exit code in output: {out[:110]!r}"
    if "[blocked]" in out:
        return STATUS_BLOCKED, "refused by the command filter (layer-1)"
    return STATUS_INCONCLUSIVE, f"unexpected exit {code}: {out[:110]!r}"


def binary_present(binary: str, cache: dict[str, bool] | None = None) -> bool:
    """Is ``binary`` on PATH inside the sandbox?

    Results are cached: several probes ask about the same binary (B5/B6 both need
    curl, B7/B8/B9 all need git), and run_tests refuses an identical command after
    ``_MAX_REPEATS`` attempts. Without the cache the third git probe receives a
    ``[stop]`` message with no exit code and would be misreported as inconclusive.
    """
    if cache is not None and binary in cache:
        return cache[binary]

    # exit 3 == found (the LEAK_EXIT convention: "I reached the thing I looked for")
    source = (
        "import shutil, sys\n"
        f"sys.exit(3 if shutil.which({binary!r}) else 0)\n"
    )
    out = run_tests(_wrap_python(source))
    found = _parse_exit_code(out) == LEAK_EXIT

    if cache is not None:
        cache[binary] = found
    return found


def run_binary_probe(
    binary: str,
    cmd: str,
    desc: str,
    cache: dict[str, bool] | None = None,
    leak_markers: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    """Test a vector that depends on an external binary.

    Two facts are checked and reported separately, because only one of them is
    evidence of isolation:

    * binary absent  -> N/A. Nothing was tested; calling this "blocked" is exactly
      what made the old probes tautological.
    * binary present -> actually run it. A leak marker then means the network is
      open, and we learn that regardless of what the image ships later.

    ``leak_markers`` overrides the default download markers for probes whose
    evidence of success looks different (e.g. an openssl handshake banner).
    """
    if not binary_present(binary, cache):
        return STATUS_NA, f"{binary} not in image - isolation NOT tested by this probe"

    out = run_tests(cmd)
    if "[blocked]" in out:
        return STATUS_BLOCKED, f"{binary} present, refused by the command filter (layer-1)"

    markers = leak_markers if leak_markers is not None else DOWNLOAD_MARKERS
    for marker in markers:
        if marker.lower() in out.lower():
            return STATUS_LEAK, f"{binary} present AND reached the network ({marker!r})"

    code = _parse_exit_code(out)
    if code == 0:
        return STATUS_BLOCKED, f"{binary} present, but the request failed (network=none)"
    if code == LEAK_EXIT:
        return STATUS_LEAK, f"{binary} present and exited {LEAK_EXIT} (reached the network)"
    return STATUS_INCONCLUSIVE, f"{binary} present, unexpected result: {out[:110]!r}"


def run_file_probe(cmd: str, leak_marker: str, desc: str) -> tuple[str, str]:
    """Filesystem probe: leak_marker must NOT come back."""
    out = run_tests(cmd)
    if leak_marker and leak_marker.lower() in out.lower():
        return STATUS_LEAK, f"found {leak_marker!r}: {out[:120]!r}"
    return STATUS_BLOCKED, desc


# --- Probe sources -----------------------------------------------------------
# These exit 0 when the network is unreachable and LEAK_EXIT when a connection
# actually succeeds.

SOCKET_PROBE = """\
import socket, sys
try:
    s = socket.create_connection(("example.com", 80), timeout=5)
    s.close()
except Exception:
    sys.exit(0)
sys.exit(3)
"""

URLLIB_PROBE = """\
import sys, urllib.request
try:
    data = urllib.request.urlopen(%r, timeout=5).read()
except Exception:
    sys.exit(0)
sys.stdout.write("DOWNLOADED " + repr(data[:20]) + chr(10))
sys.exit(3)
""" % TARGET_URL

ORACLE_PROBE = """\
import importlib.util, os, sys
# The repo under test is mounted at /work, so the checkout's OWN source is
# importable from there -- that is the code being evaluated, not an oracle.
# Measured: from cwd=/work, `django` resolves to /work/django/__init__.py; from
# /tmp it is None. Only a copy OUTSIDE the mount (site-packages, system dirs)
# would mean a pre-installed fixed version leaked in.
def outside_mount(origin):
    if not origin:
        return False
    return not os.path.abspath(origin).startswith("/work")

for name in ["django", "sympy", "matplotlib", "sklearn", "seaborn", "requests", "numpy"]:
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        continue
    if spec is not None and outside_mount(spec.origin):
        sys.stdout.write("ORACLE:" + name + ":" + str(spec.origin) + chr(10))
        sys.exit(3)
sys.exit(0)
"""

GIT_MASK_PROBE = """\
import os, sys
hit = os.path.exists("/work/.git/packed-refs") or os.path.exists("/work/.git/objects")
sys.exit(3 if hit else 0)
"""

PACK_PROBE = """\
import glob, sys
sys.exit(3 if glob.glob("/work/.git/objects/pack/*.pack") else 0)
"""

#: openssl is PRESENT in the image and can complete a TLS handshake when the
#: network is available. Measured with --network=none removed:
#:   "Connecting to 20.205.243.166 / CONNECTION ESTABLISHED / Protocol version:
#:    TLSv1.3 / Peer certificate: CN=github.com / Verification: OK"
#: The orchestrator suggested "Verify return code" / "BEGIN CERTIFICATE" as
#: markers, but neither appears with `-brief` (measured: both False). The markers
#: below are the ones that DO appear, so this probe can actually go RED.
OPENSSL_CMD = "echo | openssl s_client -connect github.com:443 -brief 2>&1 | head -20"
OPENSSL_LEAK_MARKERS = ("CONNECTION ESTABLISHED", "Protocol version:", "Verification: OK")

#: perl is PRESENT with IO::Socket::INET (verified). It is run from a file written
#: inside the container rather than `perl -e`, because the quoting for a one-liner
#: is fragile across shells — the same lesson as the base64 wrapper above.
PERL_PROBE_SRC = """\
use IO::Socket::INET;
my $sock = IO::Socket::INET->new(
    PeerHost => "github.com", PeerPort => 443, Proto => "tcp", Timeout => 5);
if ($sock) { print "PERL_CONNECTED\\n"; exit 3 }
print "PERL_BLOCKED\\n"; exit 0;
"""
PERL_LEAK_MARKER = "PERL_CONNECTED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default=None,
        help="Override SANDBOX_MODE environment variable",
    )
    args = parser.parse_args()

    if args.mode:
        os.environ["SANDBOX_MODE"] = args.mode

    mode = os.environ.get("SANDBOX_MODE", "docker").strip().lower()
    repo = get_django_checkout()
    set_repo_root(repo)

    print("=" * 88)
    print(f"  SANDBOX ESCAPE REGRESSION PROBES (mode={mode})")
    print(f"  Test checkout: {repo.name}")
    print("=" * 88)

    results: list[tuple[str, str, str, str]] = []

    def record(pid: str, name: str, outcome: tuple[str, str]) -> None:
        results.append((pid, name, outcome[0], outcome[1]))

    # --- B1/B2: `-E` and `-S` defeat the PYTHONPATH-injected sitecustomize, so
    # they are the flags an agent would use against layer-1. Under docker the
    # isolation is the network namespace, which those flags cannot touch -- and
    # that is precisely what these two probes now verify.
    record("B1", "python -E socket", run_exit_code_probe(
        SOCKET_PROBE,
        "socket.connect() SUCCEEDED - the network is reachable",
        "network unreachable (python -E cannot escape the namespace)",
        flags="-E",
    ))
    record("B2", "python -S urllib", run_exit_code_probe(
        URLLIB_PROBE,
        "urllib DOWNLOADED the target file",
        "download failed (python -S cannot escape the namespace)",
        flags="-S",
    ))

    # --- B3-B10: binary-dependent vectors. N/A when the binary is absent.
    # One shared cache: repeated binary lookups would otherwise be refused by the
    # run_tests repeat guard and misreported as inconclusive.
    bin_cache: dict[str, bool] = {}
    record("B3", "powershell WebClient", run_binary_probe(
        "powershell",
        'powershell -Command "(New-Object System.Net.WebClient).DownloadString(\''
        + TARGET_URL + '\')"',
        "powershell absent from image",
        bin_cache,
    ))
    record("B4", "cmd /c curl", run_binary_probe(
        "cmd",
        'cmd /c "curl -s ' + TARGET_URL + '"',
        "cmd.exe absent from image",
        bin_cache,
    ))
    record("B5", "bash -c curl", run_binary_probe(
        "curl",
        'bash -c "curl -s ' + TARGET_URL + '"',
        "curl absent from image",
        bin_cache,
    ))
    record("B6", "nested subprocess curl", run_binary_probe(
        "curl",
        'python -c "import subprocess; subprocess.run([\'curl\', \''
        + TARGET_URL + '\'])"',
        "curl absent from image",
        bin_cache,
    ))
    record("B7", "git ls-remote URL", run_binary_probe(
        "git",
        "git ls-remote --heads https://github.com/psf/requests",
        "git absent from image",
        bin_cache,
    ))
    record("B8", "git remote show", run_binary_probe(
        "git",
        "git remote show origin",
        "git absent from image",
        bin_cache,
    ))
    record("B9", "git fetch URL", run_binary_probe(
        "git",
        "git fetch https://github.com/psf/requests",
        "git absent from image",
        bin_cache,
    ))
    record("B10", "node fetch", run_binary_probe(
        "node",
        'node -e "fetch(\'' + TARGET_URL + '\').then(r=>r.text()).then(console.log)"',
        "node absent from image",
        bin_cache,
    ))

    # --- B11/B12: binaries that ARE in the image and can reach the network.
    # These matter because they are the vectors that would actually work if
    # --network=none were dropped — the B3-B10 group cannot, since those binaries
    # are simply not installed. Both were verified reachable without the flag.
    record("B11", "openssl s_client", run_binary_probe(
        "openssl",
        OPENSSL_CMD,
        "openssl absent from image",
        bin_cache,
        leak_markers=OPENSSL_LEAK_MARKERS,
    ))
    record("B12", "perl IO::Socket::INET", run_binary_probe(
        "perl",
        # Write the script inside the container first: `perl -e` quoting is
        # fragile, and a broken quote would look like a blocked network. `echo |
        # base64 -d` is used instead of a herestring, which sh does not support.
        "echo '" + base64.b64encode(PERL_PROBE_SRC.encode()).decode()
        + "' | base64 -d > /tmp/p.pl && perl /tmp/p.pl",
        "perl absent from image",
        bin_cache,
        leak_markers=(PERL_LEAK_MARKER,),
    ))

    # --- F1-F7: filesystem traversal (verified correct by the orchestrator; the
    # command strings and markers are unchanged).
    record("F1", "read patches/ dir", run_file_probe(
        "cat ../../../../results/EXP-20261004-034/patches/django__django-10914_direct.txt 2>/dev/null",
        "diff --git", "host results/ unreachable from container"))
    record("F2", "read predictions jsonl", run_file_probe(
        "cat ../../../../results/EXP-20261004-034/predictions/direct.jsonl 2>/dev/null",
        "model_patch", "predictions unreachable from container"))
    record("F3", "read _hits.txt", run_file_probe(
        "cat ../../../../_hits.txt 2>/dev/null",
        "EXP-", "scratch files unreachable from container"))
    record("F4", "read .env", run_file_probe(
        "cat ../../../../.env 2>/dev/null",
        "API_KEY", ".env unreachable from container"))
    record("F5", "read eval log", run_file_probe(
        "cat ../../../../logs/eval_level100.log 2>/dev/null",
        "diff --git", "logs with past patches unreachable from container"))
    record("F6", "list other checkouts", run_file_probe(
        "ls -la ../../../../datasets/repos/django 2>/dev/null",
        "08a4ee06", "sibling checkouts unreachable from container"))
    record("F7", "read old predictions", run_file_probe(
        "cat ../../../../cbai__deepseek-v4.1-flash.modal-direct-EXP-20261001-799.json 2>/dev/null",
        "model_patch", "root prediction files unreachable from container"))

    # --- ORA / GIT / PACK: base64-wrapped exit-code probes (no nested quoting).
    record("ORA", "site-packages oracle", run_exit_code_probe(
        ORACLE_PROBE,
        "a target package IS importable (oracle leak)",
        "no target package importable in the sandbox",
    ))
    record("GIT", "git history masking", run_exit_code_probe(
        GIT_MASK_PROBE,
        "packed-refs or objects/ readable (history leak)",
        ".git masked (packed-refs and objects absent)",
    ))
    record("PACK", "pure-python .git read", run_exit_code_probe(
        PACK_PROBE,
        "a packfile is readable with pure Python (history leak)",
        "no packfile readable via pure Python",
    ))

    # --- Report.
    width = max(len(name) for _, name, _, _ in results)
    leaks = [r for r in results if r[2] == STATUS_LEAK]
    inconclusive = [r for r in results if r[2] == STATUS_INCONCLUSIVE]
    na = [r for r in results if r[2] == STATUS_NA]
    tested = len(results) - len(na)

    for pid, name, status, detail in results:
        print(f"  [{status:<12}] {pid:<4} {name:<{width}} | {detail}")

    print("=" * 88)
    print(
        f"  {tested} probe(s) actually tested isolation | "
        f"{len(na)} N/A (binary absent - NOT evidence of isolation) | "
        f"{len(inconclusive)} inconclusive"
    )
    if na:
        absent = ", ".join(r[0] for r in na)
        print(f"  N/A probes: {absent}")
    if leaks:
        print(f"RESULT: LEAK DETECTED - {len(leaks)} probe(s) reached protected data (FAIL)")
        return 1
    if inconclusive:
        print(
            f"RESULT: {len(inconclusive)} probe(s) COULD NOT BE EVALUATED - "
            "treated as failure (fail-closed)"
        )
        return 1
    print("RESULT: every applicable probe was blocked (PASS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
