"""Report how prompt caching shows up in a finished experiment.

Reads the generation CSV (column names: input_tokens_total / _regular / _cached)
and the per-inference usage records, so we can tell whether the 9router cache is
actually being credited or silently dropped.

Usage: python tools/cache_report.py <EXP-ID> [<EXP-ID> ...]
"""

import csv
import glob
import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")


def num(row, key):
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def report(exp):
    lines = [f"=== {exp} ==="]
    csvs = sorted(glob.glob(str(ROOT / "results" / exp / "generation_result.csv")))
    if not csvs:
        lines.append("  no generation_result.csv")
        return lines

    with open(csvs[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tot_t = tot_c = tot_r = tot_cost = 0.0
    lines.append(f"  {'instance':26s} {'strat':9s} {'total_in':>10s} {'cached':>10s} {'hit%':>7s} {'cost_usd':>10s}")
    for row in rows:
        t = num(row, "input_tokens_total")
        c = num(row, "input_tokens_cached")
        r = num(row, "input_tokens_regular")
        cost = float(row.get("cost_usd_offpeak") or 0)
        tot_t += t
        tot_c += c
        tot_r += r
        tot_cost += cost
        pct = (100.0 * c / t) if t else 0.0
        lines.append(
            f"  {row['instance_id']:26s} {row['strategy']:9s} {t:10d} {c:10d} {pct:6.1f}% {cost:10.6f}"
        )
    pct = (100.0 * tot_c / tot_t) if tot_t else 0.0
    lines.append(f"  {'TOTAL':26s} {'':9s} {int(tot_t):10d} {int(tot_c):10d} {pct:6.1f}% {tot_cost:10.6f}")
    lines.append(f"  regular(uncached) input tokens: {int(tot_r)}")

    # Per-inference detail: did any single call report cached tokens, and did the
    # provider's response cache ever fire?
    #
    # The glob is 3 levels deep (artifacts/<instance>/<strategy>/<file>.jsonl).
    # A 2-level pattern matched NOTHING, so this whole section silently reported
    # zeros for every experiment -- a report that cannot fail loudly is worse than
    # no report, because its zeros read as "measured, found nothing".
    infs = sorted(glob.glob(str(ROOT / "results" / exp / "artifacts" / "*" / "*" / "*.jsonl")))
    hit_calls = 0
    with_cache = 0
    total_calls = 0
    semantic_hits = 0
    semantic_turns = 0
    for path in infs:
        if "tool_calls" in path or "trajectory" in path:
            continue
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                u = rec.get("usage") or {}
                if not u:
                    continue
                total_calls += 1
                if (u.get("cached_tokens") or 0) > 0:
                    with_cache += 1
                    hit_calls += int(u.get("cached_tokens") or 0)
                # Response-cache hit: read the FLAG only. A high cached_tokens
                # ratio is normal prefix caching (76-81% here) and is NOT a hit.
                if u.get("semantic_cache_hit"):
                    semantic_hits += 1
                semantic_turns += int(u.get("semantic_cache_hit_turns") or 0)
        except OSError:
            continue

    lines.append(f"  artifact jsonl files scanned: {len(infs)}")
    lines.append(f"  inference records with usage: {total_calls}")
    lines.append(f"  calls reporting cached_tokens>0: {with_cache} (prefix cache - normal)")
    lines.append(f"  sum cached_tokens (per-call): {hit_calls}")
    lines.append(f"  calls with semantic_cache_hit=true: {semantic_hits}")
    if semantic_turns:
        lines.append(f"  sum semantic_cache_hit_turns: {semantic_turns}")
    return lines


if __name__ == "__main__":
    out_path = Path(sys.argv[1])
    exps = sys.argv[2:]
    text = "\n".join("\n".join(report(e)) for e in exps)
    out_path.write_text(text + "\n", encoding="utf-8")
    print("ok")
