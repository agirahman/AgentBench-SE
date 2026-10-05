"""What will the 200-turn sweep actually cost?

Not a guess: measured from the paid runs already on disk, scaled to the number of
turns the new budget allows. The user is about to authorise 150 runs, so the number
has to come from data.
"""
import json
from pathlib import Path

RESULTS = Path("results")

#: Measured card for cbai/deepseek-v4.1-flash, derived from the real bill.
INPUT = 0.14 / 1_000_000
OUTPUT = 0.28 / 1_000_000

print("=" * 78)
print("  PAID RUNS ON DISK (cbai/deepseek-v4.1-flash)")
print("=" * 78)

rows = []
for exp in sorted(RESULTS.glob("EXP-*")):
    y = exp / "experiment.yaml"
    if not y.exists():
        continue
    text = y.read_text(encoding="utf-8", errors="replace")
    if "cbai/deepseek-v4.1-flash" not in text:
        continue
    total = rev = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("total_tool_turns:"):
            total = int(s.split(":")[1])
        elif s.startswith("revision_tool_turns:"):
            rev = int(s.split(":")[1])

    runs = []
    for sf in sorted(exp.glob("artifacts/*/*/summary.json")):
        try:
            runs.append(json.loads(sf.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    if not runs:
        continue

    toks = sum(r.get("total_tokens") or 0 for r in runs)
    secs = sum(r.get("elapsed_seconds") or 0 for r in runs)
    rows.append((exp.name, total, rev, len(runs), toks, secs))

for name, total, rev, n, toks, secs in rows:
    avg_tok = toks / n
    avg_s = secs / n
    print(f"  {name:<24} total={str(total):<5} rev={str(rev):<4} n={n:<3} "
          f"tokens/run={avg_tok:>9,.0f}  {avg_s:>6.0f}s/run")

print()
print("=" * 78)
print("  EXTRAPOLATION TO 150 RUNS AT 200 TURNS")
print("=" * 78)

# The pool-40 pilot is the best-matched baseline (same model, same harness).
pilot = [r for r in rows if r[1] == 40 and r[3] == 15]
if not pilot:
    print("  no pool-40 paid pilot found; cannot extrapolate honestly")
    raise SystemExit(0)

_, _, _, n, toks, secs = pilot[0]
tok_per_run_40 = toks / n
sec_per_run_40 = secs / n

# Scaling. Tokens grow SUBLINEARLY with the turn budget because the model stops when
# the task is done: the pool is a CAP, not a target. Measured on the free model:
# pool 40 -> 421,634 tokens/run (EXP-20260929-003), pool 100 -> 724,108 (022),
# i.e. 2.5x the pool for 1.72x the tokens (exponent ~0.59).
import math
ratio = 100 / 40
measured_ratio = 724_108 / 421_634
exponent = math.log(measured_ratio) / math.log(ratio)

print(f"\n  Measured scaling exponent (free model, pool 40 -> 100): {exponent:.2f}")
print(f"  (tokens grow sublinearly with the cap: the model stops when finished)")

for cap, label in ((200, "if the cap is reached"), (100, "if runs stop early, as at 100")):
    factor = (cap / 40) ** exponent
    tok = tok_per_run_40 * factor
    cost_run = tok * 0.9 * INPUT + tok * 0.1 * OUTPUT  # ~90% input, measured
    print(f"\n  At pool {cap} ({label}):")
    print(f"    tokens/run  : {tok:>10,.0f}  (x{factor:.2f} vs pool 40)")
    print(f"    cost/run    : ${cost_run:>9.4f}")
    print(f"    cost/150    : ${cost_run * 150:>9.2f}")

# Wall clock: turns scale more directly, since each turn is one request.
print(f"\n  Wall clock:")
print(f"    measured at pool 40: {sec_per_run_40:.0f}s/run ({sec_per_run_40 * 150 / 3600:.1f}h for 150)")
for cap in (100, 200):
    # Turns scale ~linearly, so time does too, but capped by the act timeout.
    factor = cap / 40
    est = sec_per_run_40 * min(factor, 3.0)  # 3x = the act timeout ceiling
    print(f"    at pool {cap}: ~{est:.0f}s/run -> ~{est * 150 / 3600:.1f}h for 150 runs")

print()
print("=" * 78)
print("  CAVEATS -- read before treating these as a quote")
print("=" * 78)
print("""
  * Tokens grow sublinearly with the CAP but the exponent is measured on a DIFFERENT
    model (the free one) at a different pool range, so it is an approximation.
  * The pool is a cap, not a target: a run that finishes in 30 turns costs the same
    at pool 200 as at pool 40. Most runs on these instances finished well inside 40.
  * Review will cost MORE than before because revisions now actually run (32 turns
    reserved instead of 8), which is the point of the change.
  * Cache discounts are NOT applied: 9router billed full price even on API-reported
    cache hits (measured), so charging every input token at the regular rate is the
    honest estimate.
""")
