"""Read the REAL bill for a run window from 9router's usageHistory.

RQ3 needs dollars that were actually charged, not a model of them. 9router logs
every request it proxies (model, promptTokens, completionTokens, cost), so the
bill for a run can be read back instead of inferred: sum the rows whose timestamp
falls inside the run window and whose model matches.

This is also the independent check on PricingTable: `--compare` prints our own
computed cost next to the recorded one, so a wrong rate card shows up as a
mismatch rather than silently pricing the thesis.

Usage
-----
    python tools/read_actual_bill.py --since 2026-09-30T10:00:00Z
    python tools/read_actual_bill.py --since ... --until ... --model cbai/deepseek-v4.1-flash
    python tools/read_actual_bill.py --since ... --compare results/EXP-.../generation_statistics.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("APPDATA", "")) / "9router" / "db" / "data.sqlite"


def _iso(s: str) -> str:
    """Normalise a user-supplied timestamp to the DB's format.

    The database stores ISO-8601 with a trailing 'Z' (e.g. 2026-09-30T09:38:08.212Z).
    Lexicographic comparison works for this format, so the filter can stay a
    simple string range -- but only if the query is normalised the same way.
    """
    s = s.strip()
    if s.endswith("Z"):
        return s
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def open_ro() -> sqlite3.Connection:
    """Copy-then-read: the server holds the DB open in WAL mode."""
    tmp = Path(tempfile.gettempdir()) / "9router_bill_ro.sqlite"
    shutil.copy2(DB, tmp)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    ap = argparse.ArgumentParser(description="Read the actual 9router bill for a window.")
    ap.add_argument("--since", required=True, help="ISO timestamp, inclusive")
    ap.add_argument("--until", default=None, help="ISO timestamp, exclusive")
    ap.add_argument("--model", default=None, help="substring match on the model name")
    ap.add_argument("--compare", default=None,
                    help="generation_statistics.json or experiment dir to compare against")
    ap.add_argument("--per-request", action="store_true", help="also list each request")
    args = ap.parse_args()

    if not DB.exists():
        print(f"9router database not found: {DB}", file=sys.stderr)
        return 1

    since = _iso(args.since)
    until = _iso(args.until) if args.until else "9999"

    con = open_ro()
    where = ["timestamp >= ?", "timestamp < ?"]
    params: list = [since, until]
    if args.model:
        # Match on the model NAME, not the route prefix. 9router records the
        # upstream id it called ("deepseek-v4.1-flash") while the configured model
        # is the routed id ("cbai/deepseek-v4.1-flash"), so a LIKE on the full
        # configured string silently returns nothing -- which is how the first
        # bill read came back empty while 9 requests were actually billed.
        needle = args.model.split("/")[-1]
        where.append("model LIKE ?")
        params.append(f"%{needle}%")
    sql = f"""
        SELECT model, COUNT(*) n, SUM(cost) cost,
               SUM(promptTokens) pt, SUM(completionTokens) ct,
               MIN(timestamp) first_ts, MAX(timestamp) last_ts
        FROM usageHistory WHERE {' AND '.join(where)}
        GROUP BY model ORDER BY cost DESC
    """
    rows = con.execute(sql, params).fetchall()

    print("=" * 78)
    print("  ACTUAL 9router BILL")
    print("=" * 78)
    print(f"  window : {since}  ..  {args.until or '(now)'}")
    if args.model:
        print(f"  model  : ~{args.model}")
    print()
    if not rows:
        print("  no requests recorded in this window")
        return 0

    total = 0.0
    print(f"  {'model':44s} {'n':>5s} {'prompt':>12s} {'complt':>10s} {'cost USD':>11s}")
    for r in rows:
        total += r["cost"] or 0.0
        print(f"  {r['model'][:44]:44s} {r['n']:5d} {r['pt'] or 0:12d} "
              f"{r['ct'] or 0:10d} {r['cost'] or 0:11.6f}")
    print(f"  {'TOTAL':44s} {'':5s} {'':12s} {'':10s} {total:11.6f}")

    if args.per_request:
        print()
        print("  per-request detail:")
        for r in con.execute(f"""
            SELECT timestamp, model, promptTokens, completionTokens, cost, status
            FROM usageHistory WHERE {' AND '.join(where)} ORDER BY timestamp
        """, params):
            print(f"    {r['timestamp']}  {r['model'][:34]:34s} "
                  f"pt={r['promptTokens'] or 0:>7} ct={r['completionTokens'] or 0:>6} "
                  f"${r['cost'] or 0:.6f}  {r['status']}")

    # Cross-check against our own accounting.
    if args.compare:
        target = Path(args.compare)
        if target.is_dir():
            target = target / "generation_statistics.json"
        print()
        print("=" * 78)
        print("  OUR COST MODEL vs THE ACTUAL BILL")
        print("=" * 78)
        if not target.exists():
            print(f"  not found: {target}")
        else:
            ours, how = _our_total_cost(target)
            if ours is None:
                print(f"  no cost field found in {target.name}; keys: "
                      f"{list(json.loads(target.read_text(encoding='utf-8')).keys())[:12]}")
            else:
                delta = ours - total
                pct = (delta / total * 100) if total else 0.0
                print(f"  actual bill (9router)  : ${total:.6f}")
                print(f"  our computed cost      : ${ours:.6f}   [{how}]")
                print(f"  difference             : ${delta:+.6f}  ({pct:+.2f}%)")
                print()
                if abs(pct) > 25:
                    print("  >>> LARGE GAP. Check the rate card against the bill before")
                    print("      quoting either number in the thesis.")
                elif abs(pct) > 10:
                    print("  >>> gap is material; the rate card's cached rate is the")
                    print("      weakest link (observed spread $0.0028-$0.0074 per 1M).")
                else:
                    print("  agreement within 10% -- the card reproduces this run.")
    con.close()
    return 0


def _our_total_cost(stats_path: Path) -> tuple[float | None, str]:
    """Our own total cost for an experiment, and where it came from.

    The per-run CSV is authoritative: it has one row per run with
    ``cost_usd_actual``. ``generation_statistics.json`` only carries MEANS
    (``summary.<strategy>.mean_cost_usd_actual``), so a total derived from it needs
    the run count per strategy, which the statistics file does not state.

    Both shapes are tried, cheapest-to-read first, because this used to look only
    for a top-level ``total_cost_usd`` that no artefact has ever written -- so
    ``--compare`` printed "no cost field found" on every experiment (verified on
    8 of them), and the RQ3 cross-check against the real bill could not run at all.
    Found by a partner audit (docs/AUDIT_OPS_PARTNER.md, blocker B4).
    """
    # 1. The per-run CSV: sum the actual-cost column.
    csv_path = stats_path.with_name("generation_result.csv")
    if csv_path.exists():
        import csv

        try:
            with csv_path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, csv.Error):
            rows = []
        for column in ("cost_usd_actual", "cost_usd_offpeak", "cost_usd_peak"):
            if rows and column in rows[0]:
                try:
                    vals = [float(r[column]) for r in rows
                            if r.get(column) not in (None, "")]
                except (TypeError, ValueError):
                    continue
                if vals:
                    return sum(vals), f"{len(vals)} rows from {csv_path.name}:{column}"

    # 2. Fall back to the statistics file: a top-level total, else means x counts.
    if stats_path.name != "generation_statistics.json" or not stats_path.exists():
        return None, ""
    try:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ""

    for key in ("total_cost_usd", "total_cost", "cost_usd"):
        if key in stats:
            try:
                return float(stats[key]), f"{key} (top level)"
            except (TypeError, ValueError):
                pass

    # Means are not totals. Reconstruct with the per-strategy run count when the
    # file states one, and refuse rather than guess a count.
    summary = stats.get("summary") or {}
    counts = stats.get("run_counts") or stats.get("n_runs") or {}
    total_cost = 0.0
    used = 0
    for name, block in summary.items():
        if not isinstance(block, dict):
            continue
        mean = block.get("mean_cost_usd_actual")
        n = counts.get(name) if isinstance(counts, dict) else None
        if mean is None or not n:
            return None, ""
        total_cost += float(mean) * float(n)
        used += 1
    if used:
        return total_cost, f"{used} strategies: mean_cost_usd_actual x run count"
    return None, ""


if __name__ == "__main__":
    sys.exit(main())
