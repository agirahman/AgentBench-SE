"""Compare level 40 (per_task) against EXP-003 (per_act) on the same issues.

Both used pool 40 and the same three issues, so this isolates the RULE change:
per_act capped the first act at 13 and starved the tail, per_task lets each act
draw the remainder minus a floor for those still to come.
"""
import csv
import pathlib

def load(exp: str) -> dict[tuple[str, str], dict]:
    p = pathlib.Path("results") / exp / "generation_result.csv"
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8", errors="ignore") as fh:
        out = {}
        for r in csv.DictReader(fh):
            if not r.get("instance_id"):
                continue
            key = (r["instance_id"].replace("django__django-", ""), r.get("strategy", ""))
            out[key] = r
        return out

old = load("EXP-20260928-003")   # per_act, pool 40, no floor, no reserve
new = load("EXP-20260929-003")   # per_task, pool 40, floor 10

def f(r, k):
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0

print("=" * 92)
print("SAME POOL (40), SAME ISSUES -- only the division rule changed")
print("=" * 92)
print(f"{'issue':<8}{'strat':<10}{'per_act turns':>14}{'per_task turns':>15}"
      f"{'old trunc':>11}{'new trunc':>11}{'delta':>8}")
for inst in ("10914", "11001", "11019"):
    for strat in ("direct", "planning", "review"):
        o = old.get((inst, strat))
        n = new.get((inst, strat))
        if not o or not n:
            print(f"{inst:<8}{strat:<10}  MISSING (old={bool(o)} new={bool(n)})")
            continue
        ot, nt = f(o, "total_turns"), f(n, "total_turns")
        print(f"{inst:<8}{strat:<10}{ot:>14.0f}{nt:>15.0f}"
              f"{str(o.get('truncated')):>11}{str(n.get('truncated')):>11}"
              f"{nt - ot:>+8.0f}")

print()
print("NOTE: EXP-003 has no 'truncated' column -- it predates that field. Its")
print("truncation is known only from MAX-TURNS log warnings (recorded in")
print("docs/MEMORY.md): 11001/review was granted 1 turn for the revision act,")
print("11019 hit the cap in all three strategies, 10924/review hit 15.")
print()
print("Cost comparison (level 40 only, priced with the DeepSeek card):")
tot = sum(f(r, "cost_usd_actual") for r in new.values())
print(f"  9 runs: ${tot:.4f}   avg ${tot/len(new):.4f}/run")
print(f"  worst single run: ${max(f(r, 'cost_usd_actual') for r in new.values()):.4f}"
      f"  (cap is $3.00, so the guard never bound)")
