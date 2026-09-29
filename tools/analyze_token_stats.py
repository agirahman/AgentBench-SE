"""Token and cost statistics for an experiment, with the median/mean distinction made explicit.

Why this exists: two of our own documents appeared to disagree. MEMORY.md reports
planning 140K vs review 291K tokens (a 2.08x ratio); an earlier cost analysis
reported 211K vs 276K (1.31x). Both were correct -- the first is the MEDIAN, the
second the MEAN, and the distributions are skewed (direct ranges 22K..860K). A
thesis table that says just "tokens" invites exactly that confusion, so this tool
prints both and states the ratio for each.

It also reports cost, which for most of our runs is $0.00 because they used a
deliberately-free testing model (oc/space-bunny-free, stealth/space-bunny-alpha).
Use --price MODEL to apply a real rate card to those tokens and get an RQ3
estimate; that is an estimate, not a measurement, and is labelled as such.

Usage:
    python tools/analyze_token_stats.py                       # default experiment
    python tools/analyze_token_stats.py --exp EXP-20260928-003
    python tools/analyze_token_stats.py --price deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import statistics
import sys

sys.path.insert(0, "src")
from evaluation.cost import PricingTable  # noqa: E402

RESULTS = pathlib.Path("results")


def load(exp: str) -> list[dict]:
    path = RESULTS / exp / "generation_result.csv"
    if not path.exists():
        sys.exit(f"missing {path}")
    with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
        return [r for r in csv.DictReader(fh) if r.get("instance_id")]


def num(row: dict, *keys: str) -> float:
    """First present, parseable value among keys (schemas changed over time)."""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", default="EXP-20260928-003")
    ap.add_argument("--price", default="", help="model name to price the tokens with")
    args = ap.parse_args()

    rows = load(args.exp)
    if not rows:
        sys.exit("no data rows")

    models = sorted({(r.get("model") or "?").strip() for r in rows})
    print("=" * 78)
    print(f"{args.exp}   n={len(rows)}   model={models}")
    print("=" * 78)

    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("strategy", "?"), []).append(r)

    def tokens(r):
        return num(r, "total_tokens", "input_tokens_total")

    print("\nTOKENS PER RUN — median vs mean (they differ; always state which)")
    print(f"{'strategy':<11}{'n':>3}{'median':>12}{'mean':>12}{'min':>12}{'max':>12}{'mean/med':>10}")
    for strat in sorted(by):
        vals = [tokens(r) for r in by[strat]]
        med, mean = statistics.median(vals), statistics.mean(vals)
        print(f"{strat:<11}{len(vals):>3}{med:>12,.0f}{mean:>12,.0f}"
              f"{min(vals):>12,.0f}{max(vals):>12,.0f}{mean / med:>10.2f}x")

    if "planning" in by and "review" in by:
        p, v = [tokens(r) for r in by["planning"]], [tokens(r) for r in by["review"]]
        print(f"\nreview vs planning — median {statistics.median(v)/statistics.median(p):.2f}x"
              f"   mean {statistics.mean(v)/statistics.mean(p):.2f}x")
        print("  (the '2x' claim is a MEDIAN claim; the mean ratio is lower)")

    # Cost: recorded value first, then an optional priced estimate.
    print("\nCOST AS RECORDED")
    recorded = [num(r, "cost_usd_offpeak", "cost_usd") for r in rows]
    if any(recorded):
        print(f"  total ${sum(recorded):.4f}   mean ${statistics.mean(recorded):.4f}"
              f"   max ${max(recorded):.4f}")
    else:
        print("  all zero. Not a bug: these runs used a free model whose rate card")
        print("  is genuinely $0 (see pricing_version in the CSV).")

    if args.price:
        card = PricingTable.rates_for(args.price, "off_peak")
        if not any(card.get(k) for k in ("input_per_million", "output_per_million")):
            print(f"\n  {args.price!r} has no non-zero rate card; nothing to price.")
            return 0
        print(f"\nPRICED ESTIMATE at {args.price} "
              f"(input ${card['input_per_million']}/M, cached "
              f"${card['cached_input_per_million']}/M, output ${card['output_per_million']}/M)")
        print("  ESTIMATE, not a measurement — the runs used a different (free) model.")
        print(f"\n{'strategy':<11}{'n':>3}{'worst$/run':>12}{'cache$/run':>12}"
              f"{'worst tot$':>12}{'cache tot$':>12}")
        tw = tc = 0.0
        for strat in sorted(by):
            w_list, c_list = [], []
            for r in by[strat]:
                cached = num(r, "input_tokens_cached", "cached_input_tokens")
                regular = num(r, "input_tokens_regular", "regular_input_tokens")
                out = num(r, "output_tokens", "completion_tokens")
                tot_in = num(r, "input_tokens_total", "prompt_tokens") or (cached + regular)
                w_list.append(tot_in / 1e6 * card["input_per_million"]
                              + out / 1e6 * card["output_per_million"])
                c_list.append(regular / 1e6 * card["input_per_million"]
                              + cached / 1e6 * card["cached_input_per_million"]
                              + out / 1e6 * card["output_per_million"])
            tw += sum(w_list)
            tc += sum(c_list)
            print(f"{strat:<11}{len(w_list):>3}{statistics.mean(w_list):>12.4f}"
                  f"{statistics.mean(c_list):>12.4f}{sum(w_list):>12.4f}{sum(c_list):>12.4f}")
        n = len(rows)
        print(f"\n  mean per run: worst ${tw/n:.4f}  cache-aware ${tc/n:.4f}")
        print(f"  reference cap: $3.00/task -> we use {tw/n/3:.1%} (worst) "
              f"of it, so a cost cap would not bind.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
