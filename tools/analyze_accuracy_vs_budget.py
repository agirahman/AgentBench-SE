"""Is there a turn budget at which our accuracy saturates? (accuracy vs budget)

This is the empirical answer to "what number should the budget be?". Instead of
guessing, it looks at every graded run we have and asks: among runs that DID NOT
hit their cap, how many turns did they actually need? If the distribution has a
short tail, a budget above that tail is safe and anything below it truncates.

The reasoning:
  * a run that hit its cap tells us nothing about how many turns it NEEDED --
    only that it wanted more than it got. Those are excluded from the "needed"
    distribution and reported separately (they are the confounded ones).
  * a run that finished on its own reveals its true requirement: the turns it
    actually used. That is a lower bound on its need, and an exact value for
    "turns it chose to spend".

So the safe budget is around the maximum of "turns used by runs that converged".

Usage: python tools/analyze_accuracy_vs_budget.py [EXP ...]
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")


def cap_hits(exp: str) -> dict[tuple[str, str], int]:
    log = ROOT / f"results/{exp}/logs/experiment.log"
    if not log.exists():
        return {}
    ctx: list[str] = []
    out: dict[tuple[str, str], int] = defaultdict(int)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            ctx = [m.group(1), m.group(2)]
            continue
        if CAP_RE.search(line) and len(ctx) == 2:
            out[(ctx[0], ctx[1])] += 1
    return out


def resolved_map(exp: str) -> dict[tuple[str, str], bool]:
    """resolved per (strategy, instance) from the Modal eval reports.

    The CSV records what the AGENT did (turns, tokens) but not the grade: the
    verdict comes from the separate harness run in predictions/<strategy>_results.json.
    Joining the two is what makes an accuracy-vs-budget analysis possible at all.
    """
    out: dict[tuple[str, str], bool] = {}
    for strat in ("direct", "planning", "review"):
        p = ROOT / f"results/{exp}/predictions/{strat}_results.json"
        if not p.exists():
            continue
        try:
            import json

            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        for rec in data.get("results", []):
            out[(strat, rec["instance_id"])] = bool(rec.get("resolved"))
    return out


def main() -> None:
    exps = sys.argv[1:] or ["EXP-20260928-003"]
    rows: list[dict] = []
    for exp in exps:
        p = ROOT / f"results/{exp}/generation_result.csv"
        if not p.exists():
            print(f"skip {exp}: no CSV")
            continue
        hits = cap_hits(exp)
        grades = resolved_map(exp)
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["_exp"] = exp
                r["_caps"] = hits.get((r.get("strategy"), r.get("instance_id")), 0)
                key = (r.get("strategy"), r.get("instance_id"))
                r["_resolved"] = grades.get(key)
                rows.append(r)

    graded = [r for r in rows if r["_resolved"] is not None]
    ungraded = len(rows) - len(graded)
    print(f"{len(graded)} graded runs from {len(exps)} experiment(s)")
    if ungraded:
        print(f"  ({ungraded} run(s) have no eval report and are excluded)")
    print()

    # "turns used" = total_turns (all acts summed), which is what the pool bounds.
    def turns(r: dict) -> int:
        try:
            return int(float(r.get("total_turns") or 0))
        except ValueError:
            return 0

    def resolved(r: dict) -> bool:
        return bool(r.get("_resolved"))

    converged = [r for r in graded if not r["_caps"]]
    truncated = [r for r in graded if r["_caps"]]

    print(f"converged (stopped on their own): {len(converged)}")
    print(f"truncated (hit a cap):            {len(truncated)}\n")

    # Distribution of turns used, for runs that were NOT cut off.
    used = sorted(turns(r) for r in converged)
    if used:
        import statistics

        print("Turns USED by converged runs (their revealed requirement):")
        print(f"  min={used[0]}  median={statistics.median(used):.0f}  "
              f"p90={used[int(0.9 * (len(used) - 1))]}  max={used[-1]}")
        print(f"  all values: {used}\n")

    # Accuracy by turns-used bucket: is more turns associated with success?
    print("Accuracy by turns used (converged runs only):")
    buckets = [(0, 15), (16, 25), (26, 35), (36, 45), (46, 999)]
    print(f"  {'bucket':>10s} {'n':>4s} {'resolved':>9s} {'rate':>7s}")
    for lo, hi in buckets:
        grp = [r for r in converged if lo <= turns(r) <= hi]
        if not grp:
            continue
        ok = sum(1 for r in grp if resolved(r))
        print(f"  {f'{lo}-{hi if hi < 999 else "+"}':>10s} {len(grp):4d} {ok:9d} {ok / len(grp):6.0%}")

    # The confounded set: what happened to runs that were cut off?
    print("\nRuns CUT OFF by a cap (their result measures the budget):")
    print(f"  {'strategy':9s} {'instance':24s} {'turns':>6s} {'resolved':>9s}")
    for r in sorted(truncated, key=lambda x: (x.get("strategy", ""), x.get("instance_id", ""))):
        print(
            f"  {r.get('strategy',''):9s} {r.get('instance_id',''):24s} "
            f"{turns(r):6d} {str(resolved(r)):>9s}"
        )
    if truncated:
        ok = sum(1 for r in truncated if resolved(r))
        print(f"\n  resolved among truncated: {ok}/{len(truncated)}")

    print("\n--- interpretation ---")
    if used:
        print(
            f"  A run that converged used at most {used[-1]} turns. A budget above that\n"
            f"  cannot truncate a run of this shape; the observed max is the natural\n"
            f"  'safe' level, and anything below it truncates the tail (which is what\n"
            f"  happened to the {len(truncated)} cut-off runs above)."
        )
    print(
        "  NOTE: this is a LOWER bound on what a truncated run needed. It does not\n"
        "  prove a bigger budget would have fixed those runs -- only that the current\n"
        "  number cannot be shown to be sufficient for them."
    )


if __name__ == "__main__":
    main()
