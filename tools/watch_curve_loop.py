"""Loop the curve watcher, printing one compact progress line per interval.

Existed because the first monitor used a nested PowerShell loop whose quoting
broke it (it exited in under a second, having printed nothing). A Python loop
has no shell quoting to get wrong.

Reads predictions/*.jsonl, NOT generation_result.csv. The CSV is written once,
after the whole level finishes, so a watcher reading it reports 0/9 for the
entire hour a level takes -- which is exactly what happened on the first run.
The jsonl files are the runner's real savepoints: one line appended per
completed run. Counts differ per strategy because the runner writes
<strategy>.jsonl and an aggregate predictions.jsonl, so the aggregate is used
and the per-strategy files are ignored to avoid double counting.

Usage:
    python tools/watch_curve_loop.py --interval 300
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent

EXPECTED_PER_LEVEL = 9  # 3 issues x 3 strategies


def curve_experiments() -> dict[int, pathlib.Path]:
    """Map budget level -> experiment directory for every per_task run.

    experiment.yaml is written by main.py only AFTER a run finishes, so during
    a level the directory has no config to read. The level is therefore taken
    from the sweep log, which prints it as the level starts, and the newest
    experiment directory is attributed to it. That is sound because the sweep
    is sequential by construction: only one level is ever in flight.
    """
    levels: dict[int, pathlib.Path] = {}
    for d in sorted((ROOT / "results").glob("EXP-*")):
        yml = d / "experiment.yaml"
        if not yml.exists():
            continue
        text = yml.read_text(encoding="utf-8", errors="ignore")
        if "budget_mode: per_task" not in text:
            continue
        for line in text.splitlines():
            if line.strip().startswith("total_tool_turns:"):
                try:
                    levels[int(line.split(":", 1)[1].strip())] = d
                except ValueError:
                    pass
                break

    running = running_level()
    if running is not None:
        # Override, do not merely fill a gap. A level can legitimately be
        # claimed by an EARLIER directory too: the preflight run used level 40
        # and wrote its config, so "40 already in levels" was true while the
        # real level-40 sweep was mid-flight in a newer directory -- and the
        # watcher reported the preflight's 1 run as the whole level's progress.
        levels[running[0]] = running[1]
    return levels


def read_text_tolerant(path: pathlib.Path) -> str:
    """Read a log file regardless of the encoding it was written with.

    PowerShell's Tee-Object writes UTF-16LE by default on Windows, so the sweep
    log is UTF-16 while every other file here is UTF-8. Reading a UTF-16 file as
    UTF-8 yields a null byte after every character, so a regex for "LEVEL 40"
    simply never matches and the level appears to be missing -- which is exactly
    what happened: the watcher reported the preflight's single run as the whole
    level's progress. Detect the BOM/null density rather than assuming.
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore")
    # No BOM: a UTF-16 file still shows nulls at a high rate.
    if raw and raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def running_level() -> tuple[int, pathlib.Path] | None:
    """(level, experiment dir) for the level currently in flight, if any.

    Prefers the state file the sweep driver writes as each level starts; falls
    back to parsing the sweep log for sweeps started before that file existed.
    Returns None once the level's config has been written, so the caller reads
    it from disk instead.
    """
    level: int | None = None

    state = ROOT / "logs" / "budget_curve_state.json"
    if state.exists():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            if not data.get("finished"):
                level = int(data["level"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            level = None

    if level is None:
        log = ROOT / "logs" / "budget_curve_sweep.log"
        if not log.exists():
            return None
        for line in read_text_tolerant(log).splitlines():
            m = re.search(r"LEVEL (\d+)\s+\(", line)
            if m:
                level = int(m.group(1))
    if level is None:
        return None

    # Only attribute the newest directory if it has no config yet -- otherwise
    # the level already finished and curve_experiments() found it by itself.
    dirs = sorted((ROOT / "results").glob("EXP-*"), key=lambda d: d.name)
    if not dirs:
        return None
    newest = dirs[-1]
    if (newest / "experiment.yaml").exists():
        return None
    return level, newest


def progress(exp_dir: pathlib.Path) -> dict:
    """Count completed runs and their strategies from the savepoints."""
    agg = exp_dir / "predictions" / "predictions.jsonl"
    if not agg.exists():
        return {"done": 0, "by_strategy": {}, "last": "", "empty": 0, "errors": 0}
    done = 0
    by_strategy: dict[str, int] = {}
    last = ""
    empty = 0
    errors = 0
    for line in agg.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        done += 1
        strat = entry.get("strategy", "?")
        by_strategy[strat] = by_strategy.get(strat, 0) + 1
        last = f"{entry.get('instance_id', '?')}/{strat}"
        if not (entry.get("model_patch") or "").strip():
            empty += 1
        if entry.get("error_type"):
            errors += 1
    return {
        "done": done,
        "by_strategy": by_strategy,
        "last": last,
        "empty": empty,
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    while True:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        levels = curve_experiments()
        if not levels:
            print(f"[{stamp}] no curve experiments found", flush=True)
        else:
            parts = []
            for lv in sorted(levels):
                p = progress(levels[lv])
                strat = " ".join(f"{k[:4]}{v}" for k, v in sorted(p["by_strategy"].items()))
                tail = f" last {p['last']}" if p["last"] else ""
                warn = ""
                if p["empty"]:
                    warn += f" empty-patch {p['empty']}"
                if p["errors"]:
                    warn += f" errors {p['errors']}"
                parts.append(
                    f"L{lv}: {p['done']}/{EXPECTED_PER_LEVEL} [{strat}]{tail}{warn}"
                )
            print(f"[{stamp}] " + " | ".join(parts), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
