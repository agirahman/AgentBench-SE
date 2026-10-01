"""PROBE (my own test file, per the task's rules) — proves the Config leak.

This file is named to sort AFTER test_budget_modes.py so pytest runs it later in
the session. It asserts nothing about behaviour; it just REPORTS the class-level
Config values it can observe at its own point in the session. If a previous test
mutated Config without restoring, the value here differs from .env.

Run it in two ways to see the difference:
  1. pytest tests/test_budget_modes.py tests/test_zz_probe_config_leak.py
  2. pytest tests/test_zz_probe_config_leak.py           (alone)
"""
import json
import os
from pathlib import Path

from config import Config

# What .env says, read independently of the Config class.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
dotenv = {}
for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    dotenv[k.strip()] = v.strip()

WATCH = ("TOTAL_TOOL_TURNS", "REVISION_TOOL_TURNS", "MAX_REVISION_TURNS",
         "BUDGET_MODE", "BUDGET_FLOOR_PER_ACT", "COST_LIMIT_USD")


def test_report_config_state():
    observed = {}
    for k in WATCH:
        observed[k] = {
            "Config_attr": getattr(Config, k, "<missing>"),
            "env_file": dotenv.get(k),
            "shell": os.environ.get(k),
        }
    out_path = Path(__file__).resolve().parent.parent / ".tr_probe_observed.json"
    out_path.write_text(json.dumps(observed, indent=2), encoding="utf-8")
    print("\n[PROBE] Config values observed at this point in the session:")
    for k, v in observed.items():
        flag = ""
        if v["env_file"] is not None and str(v["Config_attr"]) != str(v["env_file"]):
            flag = "   <-- DIFFERS from .env"
        print(f"   {k:22s} Config={v['Config_attr']!r:8} .env={v['env_file']!r:8}"
              f" shell={v['shell']!r}{flag}")


def test_probe_would_fail_if_config_leaked():
    """A canary: asserts Config matches its EXPECTED source. Fails if a prior test leaked.

    Compares against the shell value when one is set, falling back to .env. Comparing
    against .env ALONE is a false positive: the shell legitimately overrides .env
    (config.py documents it, and tools/run_with_env.py relies on it), so with
    REVISION_TOOL_TURNS=8 exported, Config=8 is CORRECT and reporting it as a leak
    would send the reader after a test that is doing nothing wrong. Measured: that
    exact false positive fired once this file was added.
    """
    for k in ("TOTAL_TOOL_TURNS", "REVISION_TOOL_TURNS", "MAX_REVISION_TURNS"):
        expected = os.environ.get(k) or dotenv.get(k)
        if expected is None:
            continue
        assert str(getattr(Config, k)) == str(expected), (
            f"{k}: Config={getattr(Config, k)!r} but expected {expected!r} "
            f"(shell={os.environ.get(k)!r}, .env={dotenv.get(k)!r}) — a previous "
            f"test mutated Config without restoring it"
        )
