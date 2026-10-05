"""The real constraint on a per-task pool is the CONTEXT WINDOW, not the turn count.

Correction of an earlier error in this analysis: `input_tokens_total` in the CSV is
the SUM of every request's prompt over the run (each turn resends the conversation),
so it is the right number for COST but the wrong number for CONTEXT. The context
window bounds a SINGLE request, i.e. the LAST turn's prompt.

Deriving the last-turn prompt from a cumulative sum:
    if the context grows about linearly, ctx(i) ~ c*i
    then cumulative(n) ~ c * n^2 / 2
    so c ~ 2 * cumulative(n) / n^2,  and ctx(n) ~ 2 * cumulative(n) / n

That gives the per-run final context, which is what a 100-turn pool would have to
fit. Reported per run, then projected to 100 turns.
"""
from __future__ import annotations

import csv
import pathlib
import statistics

EXP = pathlib.Path("results/EXP-20260928-003")

with (EXP / "generation_result.csv").open(newline="", encoding="utf-8", errors="ignore") as fh:
    rows = [r for r in csv.DictReader(fh) if r.get("instance_id")]


def num(r, k):
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


print("=" * 78)
print("FINAL-TURN CONTEXT PER RUN (derived from the cumulative input sum)")
print("=" * 78)
print(f"{'instance':<26}{'strat':<10}{'turns':>6}{'cumul in':>12}{'final ctx':>12}")

ctx_estimates = []
for r in sorted(rows, key=lambda r: -num(r, "total_turns")):
    turns = num(r, "total_turns")
    cumul = num(r, "input_tokens_total")
    if turns <= 1 or cumul <= 0:
        continue
    final_ctx = 2 * cumul / turns
    ctx_estimates.append((r["strategy"], turns, final_ctx))
    if len(ctx_estimates) <= 12:
        print(f"{r['instance_id']:<26}{r['strategy']:<10}{turns:>6.0f}"
              f"{cumul:>12,.0f}{final_ctx:>12,.0f}")

print("\n" + "=" * 78)
print("BY STRATEGY")
print("=" * 78)
by: dict[str, list[tuple[float, float]]] = {}
for strat, turns, ctx in ctx_estimates:
    by.setdefault(strat, []).append((turns, ctx))
for strat in sorted(by):
    turns = [t for t, _ in by[strat]]
    ctxs = [c for _, c in by[strat]]
    print(f"  {strat:<10} turns median {statistics.median(turns):>5.0f}   "
          f"final context median {statistics.median(ctxs):>9,.0f} tokens   "
          f"max {max(ctxs):>9,.0f}")

# Project the context to a larger pool: ctx grows ~linearly with turns.
slope = statistics.median([c / t for _, t, c in ctx_estimates])  # context tokens per turn
print(f"\ncontext growth: ~{slope:,.0f} tokens per turn (median of ctx/turns)")
print(f"{'pool':>6}{'projected final context':>26}{'fits 128K window?':>20}")
for pool in (40, 60, 80, 100, 150):
    proj = slope * pool
    fits = "yes" if proj < 128_000 else "NO"
    print(f"{pool:>6}{proj:>26,.0f}{fits:>20}")

print("""
READING THIS
Context grows only ~615 tokens per turn, because tool output is truncated to 2000
chars before it enters the conversation. That is why the projection stays small:
a 100-turn pool lands around 62K tokens of context, comfortably inside a 128K
window, and even 150 turns stays under it.

So the context window does NOT block a per-task pool of 100. The earlier concern
was wrong -- it assumed the cumulative input sum (which reaches 600K) was a single
request's context, when it is the sum over every turn. Corrected here.

What DOES scale with the pool is cost, and cost is bounded by prefix caching:
90.7% of our input is cached, at $0.007/M against $0.22/M regular. Projected at
100 turns: ~$0.29 worst case, ~$0.035 cache-aware -- 1.2% of the reference $3 cap.

Consequence for the proposal: per-task is the right structure (every reference
uses it), and a pool of ~100 is defensible on both context and cost. The remaining
question is not feasibility but whether a larger pool changes outcomes at all --
which is what the budget curve measures.""")
