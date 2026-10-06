#!/usr/bin/env python
"""Convert the USD costs in generation_result.csv to IDR — at REPORTING time.

WHY THIS IS A SEPARATE STEP
---------------------------
The exchange rate is a PRESENTATION choice, not research data. When the pipeline
converted, each batch was frozen to whatever ``USD_IDR_RATE`` was in ``.env`` when
it ran, so a sweep split across 5 batches at different times produced costs that
could not be compared — the same USD amount became a different Rp figure depending
on the day. It also meant a rate change forced a re-run.

So the pipeline reports USD only, and this tool converts afterwards, with ONE rate
the operator supplies. Every batch gets the same rate, and the rate and date are
recorded in the output as an audit trail (needed for Bab IV: "biaya dilaporkan
dalam USD; konversi IDR memakai kurs X pada tanggal Y").

USAGE
-----
    # One experiment
    python tools/convert_to_idr.py --exp EXP-20261004-034 --rate 17828.10

    # Every experiment at once, with a single rate (for Bab IV)
    python tools/convert_to_idr.py --all --rate 17828.10

    # Write the table to a file
    python tools/convert_to_idr.py --all --rate 17828.10 --out bab4_biaya.md

``--rate`` is REQUIRED and has no default. A default would be worse than an error:
it would silently produce Rp figures at a rate nobody chose, which is exactly the
inconsistency this tool exists to remove (the repo previously held three different
rates: .env 17828.10, Config default 16500.0, report_generator default 16500).

NOTES
-----
* The source CSV is only ever READ. Nothing here rewrites an experiment's data.
* Legacy CSVs contain ``*_idr*`` columns from the old pipeline. They are IGNORED —
  the USD column is converted instead — because those figures were computed with a
  rate that may differ from the one requested here. Reading them would silently mix
  two rates in one table.
* ``predictions.jsonl`` is never touched: it is the SWE-bench evaluation input and
  carries no cost columns at all.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_RESULTS_DIR = REPO_ROOT / "results"

#: Cost column generations, newest first. Each entry is a tuple of candidate names
#: for the same quantity, because the schema was renamed across generations
#: (``cost_usd`` -> ``cost_usd_offpeak`` -> ``cost_usd_actual``). The first name
#: present in the CSV wins, so old and new files both work.
TOTAL_COST_COLUMNS = ("cost_usd_actual", "actual_cost_usd", "cost_usd_offpeak", "cost_usd")
PEAK_COST_COLUMNS = ("cost_usd_peak_total", "peak_total_cost_usd", "cost_usd_peak")
OFFPEAK_COST_COLUMNS = ("cost_usd_offpeak", "cost_usd")

#: Columns that identify a run's outcome. ``resolved`` is the SWE-bench verdict
#: produced by the evaluation step; ``generated`` only means "a patch was produced",
#: so it is used solely as a fallback and is labelled as such in the output.
RESOLVED_COLUMNS = ("resolved", "is_resolved", "success")
GENERATED_COLUMNS = ("generated",)
STRATEGY_COLUMNS = ("strategy",)


class ConversionError(Exception):
    """Raised for a malformed input that would make the output misleading."""


def _first_present(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    return next((c for c in candidates if c in columns), None)


def _to_float(value: object) -> float:
    """Parse a CSV cell as a float, treating blanks as 0.0.

    Blank cells occur legitimately: rows recovered from savepoints have no cost
    recorded (see ``_rows_from_savepoints``). They must count as 0.0 here rather
    than abort, but the count is reported so a mostly-empty file is visible.
    """
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise ConversionError(f"not a number: {text!r}") from exc


def _is_truthy(value: object) -> bool:
    """Read a CSV boolean, tolerating the representations pandas/git produce."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def find_experiments(results_dir: Path) -> list[Path]:
    """Return every directory holding a ``generation_result.csv``, sorted by name."""
    return sorted(
        p.parent for p in results_dir.glob("**/generation_result.csv")
    )


def summarise_experiment(exp_dir: Path, rate: float) -> dict:
    """Sum one experiment's USD costs per strategy and convert them to IDR.

    Returns a dict per strategy. ``success_basis`` records WHICH column supplied
    the success count, so a reader can tell a SWE-bench ``resolved`` rate from the
    weaker "a patch was generated" fallback.
    """
    csv_path = exp_dir / "generation_result.csv"
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        raise ConversionError(f"cannot read {csv_path}: {exc}") from exc

    if not rows:
        raise ConversionError(f"{csv_path} has no data rows")

    columns = list(rows[0].keys())
    cost_col = _first_present(columns, TOTAL_COST_COLUMNS)
    if cost_col is None:
        raise ConversionError(
            f"{csv_path} has no USD cost column. Looked for "
            f"{', '.join(TOTAL_COST_COLUMNS)}; found: {', '.join(columns)}"
        )
    strategy_col = _first_present(columns, STRATEGY_COLUMNS)
    if strategy_col is None:
        raise ConversionError(f"{csv_path} has no 'strategy' column")

    peak_col = _first_present(columns, PEAK_COST_COLUMNS)
    offpeak_col = _first_present(columns, OFFPEAK_COST_COLUMNS)
    resolved_col = _first_present(columns, RESOLVED_COLUMNS)
    generated_col = _first_present(columns, GENERATED_COLUMNS)

    per_strategy: dict[str, dict] = {}
    blanks = 0
    for row in rows:
        strategy = str(row.get(strategy_col) or "").strip() or "(blank)"
        entry = per_strategy.setdefault(
            strategy,
            {"runs": 0, "cost_usd": 0.0, "peak_usd": 0.0, "offpeak_usd": 0.0,
             "success": 0, "cost_blank": 0},
        )
        entry["runs"] += 1
        raw = row.get(cost_col)
        if raw is None or not str(raw).strip():
            entry["cost_blank"] += 1
            blanks += 1
        entry["cost_usd"] += _to_float(raw)
        if peak_col:
            entry["peak_usd"] += _to_float(row.get(peak_col))
        if offpeak_col:
            entry["offpeak_usd"] += _to_float(row.get(offpeak_col))
        if resolved_col:
            if _is_truthy(row.get(resolved_col)):
                entry["success"] += 1
        elif generated_col and _is_truthy(row.get(generated_col)):
            entry["success"] += 1

    if resolved_col:
        basis = "resolved"
    elif generated_col:
        basis = "generated (patch produced — NOT the SWE-bench resolved rate)"
    else:
        basis = "none (no success column present)"

    out = {}
    for strategy, entry in sorted(per_strategy.items()):
        success = entry["success"]
        cost_usd = entry["cost_usd"]
        out[strategy] = {
            "runs": entry["runs"],
            "cost_usd": cost_usd,
            "cost_idr": cost_usd * rate,
            "cost_blank": entry["cost_blank"],
            "peak_usd": entry["peak_usd"],
            "peak_idr": entry["peak_usd"] * rate,
            "offpeak_usd": entry["offpeak_usd"],
            "offpeak_idr": entry["offpeak_usd"] * rate,
            "success": success,
            "cost_usd_per_success": (cost_usd / success) if success else None,
            "cost_idr_per_success": (cost_usd * rate / success) if success else None,
            "success_basis": basis,
        }
    return {"experiment": exp_dir.name, "cost_column": cost_col, "strategies": out,
            "rows": len(rows), "rows_without_cost": blanks}


def format_markdown(payloads: list[dict], rate: float, rate_date: str,
                    grand: dict) -> str:
    """Render the Bab IV table: rate + date in the header as an audit trail."""
    lines: list[str] = []
    lines.append("# Konversi Biaya USD → IDR\n")
    lines.append(f"**Kurs yang dipakai: 1 USD = Rp{rate:,.2f}**  ")
    lines.append(f"**Tanggal konversi: {rate_date}**\n")
    lines.append(
        "> Biaya di pipeline dilaporkan dalam **USD**. Konversi ke IDR dilakukan "
        "di tahap pelaporan dengan kurs di atas, sehingga seluruh batch memakai "
        "kurs yang sama dan dapat dibandingkan.\n"
    )
    lines.append("| Eksperimen | Strategi | Runs | Biaya USD | Biaya IDR | "
                 "Sukses | USD / Sukses | IDR / Sukses |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for payload in payloads:
        for strategy, s in payload["strategies"].items():
            usd_ps = (f"${s['cost_usd_per_success']:.6f}"
                      if s["cost_usd_per_success"] is not None else "n/a")
            idr_ps = (f"Rp{s['cost_idr_per_success']:,.0f}"
                      if s["cost_idr_per_success"] is not None else "n/a")
            lines.append(
                f"| {payload['experiment']} | {strategy} | {s['runs']} | "
                f"${s['cost_usd']:.6f} | Rp{s['cost_idr']:,.0f} | {s['success']} | "
                f"{usd_ps} | {idr_ps} |"
            )
    lines.append("")
    lines.append("## Total Seluruh Batch\n")
    lines.append(f"- Total biaya: **${grand['cost_usd']:.6f}** "
                 f"(**Rp{grand['cost_idr']:,.0f}**)")
    lines.append(f"- Total sukses: **{grand['success']}**")
    if grand["success"]:
        lines.append(f"- Rata-rata: **${grand['cost_usd_per_success']:.6f}** / sukses "
                     f"(**Rp{grand['cost_idr_per_success']:,.0f}** / sukses)")
    lines.append("")
    lines.append(
        f"> Kurs Rp{rate:,.2f}/USD pada {rate_date}. Sumber kurs dan tanggalnya "
        f"harus dicatat di Bab IV agar konversi dapat direproduksi.\n"
    )

    bases = {s["success_basis"] for p in payloads for s in p["strategies"].values()}
    if any(b.startswith("generated") for b in bases):
        lines.append(
            "> **Catatan:** sebagian atau seluruh tabel memakai basis sukses "
            "`generated` (patch berhasil dibuat), **bukan** angka `resolved` "
            "SWE-bench. Jangan kutip sebagai pass@k.\n"
        )
    return "\n".join(lines)


def format_csv(payloads: list[dict], rate: float, rate_date: str,
               grand: dict) -> str:
    """Render the same figures as CSV, with the rate on every row."""
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "experiment", "strategy", "runs", "cost_usd", "cost_idr",
        "peak_cost_usd", "peak_cost_idr", "offpeak_cost_usd", "offpeak_cost_idr",
        "success_count", "success_basis", "cost_usd_per_success",
        "cost_idr_per_success", "usd_idr_rate", "rate_date",
    ])
    for payload in payloads:
        for strategy, s in payload["strategies"].items():
            writer.writerow([
                payload["experiment"], strategy, s["runs"],
                f"{s['cost_usd']:.6f}", f"{s['cost_idr']:.2f}",
                f"{s['peak_usd']:.6f}", f"{s['peak_idr']:.2f}",
                f"{s['offpeak_usd']:.6f}", f"{s['offpeak_idr']:.2f}",
                s["success"], s["success_basis"],
                (f"{s['cost_usd_per_success']:.6f}"
                 if s["cost_usd_per_success"] is not None else ""),
                (f"{s['cost_idr_per_success']:.2f}"
                 if s["cost_idr_per_success"] is not None else ""),
                f"{rate:.2f}", rate_date,
            ])
    writer.writerow([
        "TOTAL", "", grand["runs"], f"{grand['cost_usd']:.6f}",
        f"{grand['cost_idr']:.2f}", "", "", "", "", grand["success"], "",
        (f"{grand['cost_usd_per_success']:.6f}"
         if grand["cost_usd_per_success"] is not None else ""),
        (f"{grand['cost_idr_per_success']:.2f}"
         if grand["cost_idr_per_success"] is not None else ""),
        f"{rate:.2f}", rate_date,
    ])
    return buf.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert_to_idr.py",
        description=(
            "Convert USD costs in generation_result.csv to IDR at reporting time. "
            "--rate is required: there is no default, because a default would "
            "silently produce Rp figures at a rate nobody chose."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python tools/convert_to_idr.py --exp EXP-20261004-034 --rate 17828.10\n"
            "  python tools/convert_to_idr.py --all --rate 17828.10\n"
            "  python tools/convert_to_idr.py --all --rate 17828.10 --out bab4.md\n"
        ),
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--exp", metavar="NAME",
                       help="one experiment directory name under --results-dir")
    scope.add_argument("--all", action="store_true",
                       help="every experiment under --results-dir")
    parser.add_argument(
        "--rate", type=float, required=True, metavar="KURS",
        help="IDR per 1 USD. REQUIRED — no default is applied.",
    )
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="directory holding experiment folders "
                             f"(default: {DEFAULT_RESULTS_DIR})")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="also write the table here (.md or .csv by extension)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="date to record in the output (default: today)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Guard rather than trust argparse: a non-positive rate produces Rp0 or a
    # negative total, which would be reported as a real figure.
    if args.rate is None:
        print("ERROR: --rate is required (IDR per 1 USD).", file=sys.stderr)
        return 2
    if args.rate <= 0:
        print(f"ERROR: --rate must be positive, got {args.rate}.", file=sys.stderr)
        return 2

    rate_date = args.date or date.today().isoformat()
    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        print(f"ERROR: results directory not found: {results_dir}", file=sys.stderr)
        return 2

    if args.all:
        exp_dirs = find_experiments(results_dir)
        if not exp_dirs:
            print(f"ERROR: no generation_result.csv under {results_dir}",
                  file=sys.stderr)
            return 2
    else:
        exp_dirs = [results_dir / args.exp]
        if not (exp_dirs[0] / "generation_result.csv").is_file():
            print(f"ERROR: no generation_result.csv in {exp_dirs[0]}", file=sys.stderr)
            return 2

    payloads: list[dict] = []
    for exp_dir in exp_dirs:
        try:
            payloads.append(summarise_experiment(exp_dir, args.rate))
        except ConversionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    grand = {
        "runs": sum(p["rows"] for p in payloads),
        "cost_usd": sum(s["cost_usd"] for p in payloads for s in p["strategies"].values()),
        "cost_idr": 0.0,
        "success": sum(s["success"] for p in payloads for s in p["strategies"].values()),
    }
    grand["cost_idr"] = grand["cost_usd"] * args.rate
    grand["cost_usd_per_success"] = (
        grand["cost_usd"] / grand["success"] if grand["success"] else None
    )
    grand["cost_idr_per_success"] = (
        grand["cost_usd"] * args.rate / grand["success"] if grand["success"] else None
    )

    markdown = format_markdown(payloads, args.rate, rate_date, grand)
    csv_text = format_csv(payloads, args.rate, rate_date, grand)

    print(markdown)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".csv":
            out_path.write_text(csv_text, encoding="utf-8")
        else:
            out_path.write_text(markdown, encoding="utf-8")
            csv_path = out_path.with_suffix(".csv")
            csv_path.write_text(csv_text, encoding="utf-8")
            print(f"\n[written] {out_path}")
            print(f"[written] {csv_path}")
            return 0
        print(f"\n[written] {out_path}")

    blanks = sum(p["rows_without_cost"] for p in payloads)
    if blanks:
        print(f"\n[note] {blanks} row(s) had no cost recorded (counted as 0).",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
