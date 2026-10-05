"""Does test_response_utils.test_config_from_env poison later tests?

`importlib.reload(config_module)` REPLACES config.Config with a NEW class object.
Any module that already did `from config import Config` keeps a reference to the OLD
class, so a later `monkeypatch.setattr(mod.Config, ...)` patches a class nothing reads
any more -- and the test still passes, against stale values.

MEMORY records this exact trap. This script proves whether it is live: it captures the
identity of Config as seen by several modules BEFORE and AFTER the reload test.

Usage:
    python tools/check_config_reload_poison.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Run each probe in a FRESH process so one cannot affect the next.
PROBE = """
import sys
sys.path.insert(0, "src")
import config as config_module
from agents import budget as budget_mod
from agents import base as base_mod
from strategies import review_strategy as rev_mod
import importlib

before = {
    "config": id(config_module.Config),
    "agents.budget": id(budget_mod.Config),
    "agents.base": id(base_mod.Config),
    "review_strategy": id(rev_mod.Config),
}
same_before = len(set(before.values())) == 1

importlib.reload(config_module)

after = {
    "config": id(config_module.Config),
    "agents.budget": id(budget_mod.Config),
    "agents.base": id(base_mod.Config),
    "review_strategy": id(rev_mod.Config),
}
same_after = len(set(after.values())) == 1

# Would a monkeypatch on agents.budget.Config be seen by review_strategy?
budget_mod.Config.MAX_REVISION_TURNS = 999
seen = rev_mod.Config.MAX_REVISION_TURNS

print(f"all modules share one Config BEFORE reload : {same_before}")
print(f"all modules share one Config AFTER  reload : {same_after}")
print(f"patch on agents.budget seen by review      : {seen == 999}")
print(f"  (review_strategy reads {seen})")
"""


def main() -> int:
    print("=" * 78)
    print("  CONFIG RELOAD POISONING -- does importlib.reload split the Config class?")
    print("=" * 78)
    print()
    out = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=str(ROOT),
        capture_output=True, text=True,
    )
    print(out.stdout.rstrip())
    if out.stderr.strip():
        print("\n  stderr:")
        print(out.stderr.rstrip())

    print()
    print("=" * 78)
    poisoned = "AFTER  reload : False" in out.stdout
    if poisoned:
        print("  POISONED: after the reload, modules no longer share one Config.")
        print("  A later monkeypatch.setattr(mod.Config, ...) can miss, and the test")
        print("  still passes against stale values. The reload test must restore the")
        print("  original module, or every later test that patches Config is suspect.")
    else:
        print("  SAFE: the reload does not split the Config identity.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
