"""Should we match the reference's turn budget, and what would a floor of 5 grant?

Q1 — unit conversion. Our "turn" is one assistant message that may batch several
tool calls; the reference limits (mini-SWE-agent step_limit=250, OpenHands
max_iterations=500, SWE-bench Pro 200 turns) count one ACTION each. Convert before
comparing, using the measured calls-per-turn of our own runs.

Q2 — the floor, with the relationship VERIFIED rather than assumed. An earlier
draft of this file claimed "floor = pool // n reproduces the current rule". That
is false (checked in tools/_verify_floor_math.py):

    current rule, pool 40, 3 acts, every act maxing out  -> 13, 13, 14
    floor rule,   floor 13                                -> 14, 13, 13

Same total, different distribution: the current rule recomputes an even split of
the remainder each act, the floor rule subtracts a fixed reserve. They never
coincide. What matters is what each GUARANTEES:

    current rule : act1 is CAPPED at 13; later acts get whatever is left, which
                   can be the floor of 1 when the early acts spend the pool
    floor f      : later acts are GUARANTEED f each; act1 may draw the rest

That difference is the whole point: EXP-20260928-003 shows the current rule
granting a revision act 1 turn, twice.
"""
from __future__ import annotations

import csv
import pathlib

EXP = pathlib.Path("results/EXP-20260928-003")
POOL = 40
N_BASE_ACTS = 3

# ------------------------------------------------------------------ Q1: units
print("=" * 78)
print("Q1. OUR TURN vs THE REFERENCE'S STEP — measured, not assumed")
print("=" * 78)

with (EXP / "generation_result.csv").open(newline="", encoding="utf-8", errors="ignore") as fh:
    rows = [r for r in csv.DictReader(fh) if r.get("instance_id")]


def num(r, k):
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


calls = sum(num(r, "total_tool_calls") for r in rows)
turns = sum(num(r, "total_turns") for r in rows)
cpt = calls / turns if turns else 0

print(f"EXP-003 totals: {calls:,.0f} tool calls over {turns:,.0f} turns")
print(f"=> one of our turns carries {cpt:.2f} tool calls on average")
print(f"=> our 40-turn pool is worth about {POOL * cpt:.0f} actions\n")

print(f"{'reference':<34}{'limit':>7}{'= our turns':>13}{'times our pool':>16}")
for label, limit in (
    ("mini-SWE-agent step_limit", 250),
    ("SWE-bench Pro turn cap", 200),
    ("OpenHands max_iterations", 500),
):
    eq = limit / cpt
    print(f"{label:<34}{limit:>7}{eq:>13.0f}{eq / POOL:>15.1f}x")

print("""
CAVEAT: the reference's 250 is calibrated to a single-loop scaffold where each
step runs one command, so the units are not directly comparable even after
conversion. And the reference's binding limit is the $3 COST cap, not the step
count -- we measured our runs at 2.3% of that cap, so we are far from it in
either unit. Matching 250 would mean a ~6x larger pool.""")

# ----------------------------------------------------------------- Q2: floor
print("\n" + "=" * 78)
print("Q2. WHAT A FLOOR GRANTS — verified against the current rule")
print("=" * 78)
print(f"{'rule':<24}{'act1 may draw':>15}{'min for act2':>14}{'min for act3':>14}")
print(f"{'current (now)':<24}{POOL // N_BASE_ACTS:>15}{'leftover':>14}{'leftover':>14}")
for f in (1, 3, 5, 8, 10, 13):
    print(f"{'floor ' + str(f):<24}{POOL - (N_BASE_ACTS - 1) * f:>15}{f:>14}{f:>14}")

print("""
So floor 5 means: guarantee the 2 later acts 5 turns each, and let the first act
draw up to 30. It is NOT "give every act 5 turns". The current rule gives act1 a
CAP of 13 and the later acts no guarantee at all -- when the early acts spend the
pool, the last act (and any revision act) lands on the floor of 1, which is
exactly what EXP-20260928-003 recorded twice.""")

# ------------------------------------------------------- what we actually know
print("\n" + "=" * 78)
print("WHAT EXP-003 ACTUALLY RECORDED (from the MAX-TURNS log)")
print("=" * 78)
print("""
  django-10924 review : executor granted 15, used all 15 -> CUT OFF mid-work
  django-11001 review : revision act granted 1           -> 2 reads, zero edits
  django-11019 direct : granted the whole 40-turn pool   -> CUT OFF, still failed
  django-11019 review : revision act granted 3
  django-10914 review : revision act granted 1           -> resolved anyway

  NOT recorded anywhere: how many turns each act WANTED. The CSV keeps a single
  api_turns value and the results JSON has no per-act breakdown, so any per-act
  "need" figure would be invented. That gap is worth closing in the artefact --
  the same class of omission as the missing truncation flag.
""")

print("=" * 78)
print("RECOMMENDATION")
print("=" * 78)
print("""
On matching the reference: do NOT jump to 250. It is 6x our pool and we have no
evidence our ceiling is binding at 40 for acts that converge -- 91% of acts stop
on their own. Jumping 6x would multiply runtime and cost for a hypothesis the
literature does not support: METR's method says to MEASURE the curve, not pick the
reference's number. The 3-issue x 3-budget run is exactly that measurement.

On the floor: do NOT pick 5. At floor 5 the reviewer can be squeezed to 5 turns
while the executor takes 19, so review would fail for lack of review -- turning
our measured "reviewer has no discriminative power" into an artefact of the
budget. 8-10 is the defensible band: enough for a read-only act to read the plan
and the patch and return a verdict, while freeing the executor from its 13-turn
cap.

The honest position: we cannot derive the right floor from EXP-003 because per-act
wants were never recorded. Run the curve at 13 (today), 10, and 5 and let the
resolution rate decide.
""")
