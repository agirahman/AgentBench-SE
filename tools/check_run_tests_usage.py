"""Is `run_tests` being used as a general SHELL, and does that break read-only roles?

`run_tests` executes its argument with ``shell=True``, so it is not limited to test
runners: any command works. That has a consequence the tool schema does not express:

  * The reviewer is declared read-only -- it has no ``edit_file`` and no
    ``write_file`` -- but ``run_tests`` can write files via
    ``python -c "open('x','w').write(...)"``. The read-only guarantee is enforced by
    the tool LIST, not by the harness.

  * ``direct`` gained ``run_tests`` for verification parity, which also hands it
    arbitrary shell access. That is a bigger grant than "can run the test suite".

This script reports, from the recorded calls, how the tool is ACTUALLY used:
which commands look like test runs and which look like shell escapes, and whether
any read-only role changed the tree during its own act.

Usage:
    python tools/check_run_tests_usage.py --exp EXP-20260930-332
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Commands that are plainly a test run.
_TEST_HINTS = re.compile(
    r"(pytest|runtests\.py|unittest|manage\.py test|\btox\b|\bnosetests\b)", re.I
)
#: Shell constructs that are not "running the test suite".
_SHELL_HINTS = re.compile(
    r"(<<|python\s+-c|python3\s+-c|\bcat\b|\becho\b|\bcp\b|\bmv\b|\brm\b|\bsed\b|"
    r"\bawk\b|\bgrep\b|\bfind\b|\bgit\b|\bmkdir\b|\btouch\b|\bcurl\b|\bwget\b|"
    r"\bopen\()",
    re.I,
)
#: Shell constructs that WRITE a file (the read-only bypass).
#:
#: ``2>&1``, ``2>/dev/null`` and ``>/dev/null`` are deliberately NOT writes: they
#: redirect a stream that is discarded or merged into stdout, which every test
#: command in this project does (``... -q 2>&1 | tail -20``). Treating them as
#: writes made a first version of this check classify EVERY pytest call as a write
#: and report zero test runs -- a false alarm about a bypass that was not happening.
_WRITE_HINTS = re.compile(
    r"(open\([^)]*['\"][wa]|>>?\s*(?!/dev/null)\S|tee\s+\S|sed\s+-i|patch\s+-p|"
    r"git\s+(checkout|apply|reset|clean|commit))",
    re.I,
)


def classify(command: str) -> str:
    """Label one command: test, shell, WRITES, or other.

    Order matters. A command can look like several things at once -- the common
    real shape is ``python -m pytest tests/x.py -q 2>&1 | tail -20``, which is a
    TEST that also uses a pipe and a stream redirect. Checking WRITES first (as a
    first version did) labelled it a write because of the ``2>&1``.
    """
    if not command:
        return "auto (no command given)"
    if _TEST_HINTS.search(command):
        # A test run that ALSO writes (sed -i, git checkout) is still reported as a
        # write: the write is the fact that matters.
        if _WRITE_HINTS.search(command):
            return "WRITES"
        return "test"
    if _WRITE_HINTS.search(command):
        return "WRITES"
    if _SHELL_HINTS.search(command):
        return "shell"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  run_tests USAGE -- {exp.name}")
    print("=" * 78)

    totals: dict[str, dict[str, int]] = {}
    writes_by_role: list[str] = []

    for tc in sorted(exp.glob("artifacts/*/*/tool_calls.jsonl")):
        inst, strategy = tc.parent.parent.name, tc.parent.name
        calls = [json.loads(l) for l in
                 tc.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        for c in calls:
            if c["tool"] != "run_tests":
                continue
            role = c["agent"]
            cmd = (c.get("arguments") or {}).get("command", "") or ""
            kind = classify(cmd)
            totals.setdefault(role, {}).setdefault(kind, 0)
            totals[role][kind] += 1
            if kind == "WRITES":
                writes_by_role.append(f"{inst}/{strategy} {role}: {cmd[:90]}")

    print(f"\n  {'role':<12} {'test':>5} {'shell':>6} {'WRITES':>7} {'other':>6} "
          f"{'auto':>5}")
    print(f"  {'-' * 48}")
    for role in sorted(totals):
        t = totals[role]
        print(f"  {role:<12} {t.get('test', 0):>5} {t.get('shell', 0):>6} "
              f"{t.get('WRITES', 0):>7} {t.get('other', 0):>6} "
              f"{t.get('auto (no command given)', 0):>5}")

    print()
    print("=" * 78)
    print("  READ-ONLY ROLES USING run_tests AS A SHELL")
    print("=" * 78)
    for role in ("planner", "reviewer"):
        t = totals.get(role, {})
        shell = t.get("shell", 0)
        writes = t.get("WRITES", 0)
        if not t:
            print(f"\n  {role}: never called run_tests")
            continue
        print(f"\n  {role}: {shell} shell-style call(s), {writes} that WRITE")
        if writes:
            print("    -> the read-only guarantee is bypassed in practice:")
            for line in writes_by_role[:6]:
                if f" {role}:" in line:
                    print(f"       {line}")

    print()
    print("=" * 78)
    print("  INTERPRETATION")
    print("=" * 78)
    print("""
  run_tests is implemented as `subprocess.run(command, shell=True)`, so it accepts
  ANY command. The per-role tool lists express an INTENT (planner and reviewer are
  read-only; direct and executor write code), but the harness only enforces which
  TOOLS are offered -- not what a shell tool may do with them.

  Two consequences worth stating in the write-up rather than discovering later:

  1. Verification parity required giving `direct` run_tests. That grant is broader
     than "can run the test suite": it includes arbitrary shell commands.

  2. A reviewer that runs `python -c "open('f','w').write(...)"` has edited the
     repository without any editing tool. The review strategy's independence rests
     on the reviewer not authoring changes, and that is currently a convention, not
     a constraint.

  Neither is necessarily a defect -- agents legitimately use a shell to probe the
  environment (the pilot shows `python -c "print('hello')"` to check the
  interpreter). But it must be REPORTED, and the calls above say how often it
  actually happens.
""")


if __name__ == "__main__":
    main()
