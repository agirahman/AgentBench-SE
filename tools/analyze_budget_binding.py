"""Measure how often the turn cap actually BINDS, per act, across an experiment.

The question this answers: "how do we know the budget number is right?"
You cannot know the ideal number a priori, but you CAN measure whether the cap
was binding or irrelevant:

* an act that stops BEFORE its cap returned no tool call on its own -- the model
  decided it was done, so the cap did not shape that result;
* an act that hits its cap was cut off mid-work, so its result measures the cap.

binding_rate = capped acts / total acts. A low rate means the results are mostly
robust to the exact number; the capped acts are exactly where the number matters,
and they must be reported (or the instance re-run with more room).

Usage: python tools/analyze_budget_binding.py [EXP]
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")


def cap_hits(exp: str) -> dict[tuple[str, str], list[tuple[int, str]]]:
    log = ROOT / f"results/{exp}/logs/experiment.log"
    ctx: list[str] = []
    out: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            ctx = [m.group(1), m.group(2)]
            continue
        c = CAP_RE.search(line)
        if c and len(ctx) == 2:
            out[(ctx[0], ctx[1])].append((int(c.group(1)), c.group(2)))
    return out


def rows(exp: str) -> list[dict]:
    p = ROOT / f"results/{exp}/generation_result.csv"
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260928-003"
    hits = cap_hits(exp)
    data = rows(exp)

    print(f"{exp}: {len(data)} runs\n")
    print(f"{'strategy':10s} {'runs':>5s} {'acts':>5s} {'capped':>7s} {'binding':>8s}  turns used")
    print("-" * 68)

    per_strategy = defaultdict(lambda: {"runs": 0, "acts": 0, "capped": 0, "turns": 0})
    for r in data:
        strat = r["strategy"]
        inst = r["instance_id"]
        acts = int(r.get("inference_count") or 0)
        turns = int(r.get("total_turns") or 0)
        capped = len(hits.get((strat, inst), []))
        s = per_strategy[strat]
        s["runs"] += 1
        s["acts"] += acts
        s["capped"] += capped
        s["turns"] += turns

    total_acts = total_capped = 0
    for strat, s in per_strategy.items():
        rate = s["capped"] / s["acts"] if s["acts"] else 0
        print(
            f"{strat:10s} {s['runs']:5d} {s['acts']:5d} {s['capped']:7d} "
            f"{rate:7.1%}  {s['turns']:5d}"
        )
        total_acts += s["acts"]
        total_capped += s["capped"]

    print("-" * 68)
    print(
        f"{'TOTAL':10s} {len(data):5d} {total_acts:5d} {total_capped:7d} "
        f"{total_capped / total_acts:7.1%}"
    )

    print("\nCapped acts in detail (the cap shaped these results):")
    for (strat, inst), lst in sorted(hits.items()):
        for granted, role in lst:
            print(f"  {strat:9s} {inst:24s} {role:9s} granted={granted}")

    converged = total_acts - total_capped
    print(
        f"\n{converged}/{total_acts} acts ({converged / total_acts:.0%}) stopped on their own "
        f"-- the cap did not shape them."
    )
    print(
        f"{total_capped}/{total_acts} acts ({total_capped / total_acts:.0%}) were cut off "
        f"-- for these, the number IS the result."
    )


if __name__ == "__main__":
    main()
