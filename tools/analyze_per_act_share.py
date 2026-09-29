"""Where do the turns actually GO? Per-act usage, to test the per-task proposal.

The user proposes: one pool per task (e.g. 100), each act draws freely. The risk
of a free draw is that an early act hogs the pool and starves the later ones --
which is why a floor was on the table. But that risk is only real if some act
actually behaves greedily, so measure instead of assuming.

Per-act TURNS were never recorded (the CSV keeps a single api_turns value; the
results JSON has no per-act breakdown). Per-act TOOL CALLS are recorded, in each
run's tool_calls.jsonl, so this counts those and converts using the run-level
calls-per-turn ratio. The conversion is an approximation and is labelled as one:
call/turn is not constant, so the numbers below are indicative, not exact.

What this settles: whether the planner is a hog (bad for free draw) or modest
(free draw is safe).
"""
from __future__ import annotations

import csv
import json
import pathlib
import statistics

EXP = pathlib.Path("results/EXP-20260928-003")
ARTIFACTS = EXP / "artifacts"

# Run-level ratio, for converting calls -> approximate turns.
with (EXP / "generation_result.csv").open(newline="", encoding="utf-8", errors="ignore") as fh:
    rows = [r for r in csv.DictReader(fh) if r.get("instance_id")]


def num(r, k):
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


calls_all = sum(num(r, "total_tool_calls") for r in rows)
turns_all = sum(num(r, "total_turns") for r in rows)
RATIO = calls_all / turns_all if turns_all else 1.0

print("=" * 78)
print("PER-ACT TOOL CALLS (measured) — who takes the room?")
print("=" * 78)
print(f"conversion: {RATIO:.2f} calls/turn (run-level; approximate)\n")

per_role: dict[str, dict[str, list[int]]] = {}
for strategy_dir in sorted(p for p in ARTIFACTS.iterdir() if p.is_dir()):
    for strat in ("direct", "planning", "review"):
        path = strategy_dir / strat / "tool_calls.jsonl"
        if not path.exists():
            continue
        counts: dict[str, int] = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            agent = rec.get("agent", "?")
            counts[agent] = counts.get(agent, 0) + 1
        for agent, c in counts.items():
            per_role.setdefault(strat, {}).setdefault(agent, []).append(c)

print(f"{'strategy':<10}{'act':<12}{'n':>3}{'median calls':>14}"
      f"{'~median turns':>15}{'max calls':>11}")
for strat in sorted(per_role):
    for agent in sorted(per_role[strat]):
        vals = per_role[strat][agent]
        med = statistics.median(vals)
        print(f"{strat:<10}{agent:<12}{len(vals):>3}{med:>14.0f}"
              f"{med / RATIO:>15.1f}{max(vals):>11}")

# Does the FIRST act hog? Compare its share against the acts after it.
print("\n" + "=" * 78)
print("IS THE FIRST ACT A HOG?  (this decides whether free draw is safe)")
print("=" * 78)
for strat in sorted(per_role):
    first = strat if strat == "direct" else ("planner" if "planner" in per_role[strat] else None)
    if not first or first not in per_role[strat]:
        continue
    first_med = statistics.median(per_role[strat][first])
    rest = [v for a, vs in per_role[strat].items() if a != first for v in vs]
    rest_med = statistics.median(rest) if rest else 0
    total = first_med + sum(
        statistics.median(vs) for a, vs in per_role[strat].items() if a != first
    )
    share = 100 * first_med / total if total else 0
    print(f"  {strat:<10} first act '{first}': median {first_med:.0f} calls "
          f"= {share:.0f}% of the run's calls")
    print(f"  {'':<10} later acts median {rest_med:.0f} calls each")

print("""
If the first act were a hog, free draw would be dangerous and a floor essential.
If it is modest, the pool itself provides the headroom and a floor is cheap
insurance rather than a load-bearing rule.""")
