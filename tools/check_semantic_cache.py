"""Gate: did the provider serve any run from its semantic (response) cache?

A semantic-cache hit means the provider replayed an ENTIRE previous response
instead of running the model. A run that hits it did not measure the strategy, so
including it in a strategy comparison silently invalidates that comparison -- and
this sweep decides a thesis. Nothing else in the pipeline reports it, so this is
the check that has to catch it.

This is NOT the same as prefix (prompt) caching. Prefix caching shows up as
``cached_tokens`` / ``input_tokens_cached`` and is normally 76-81% of the prompt
on a shared prefix: expected, harmless, and deliberately NOT flagged here. Only
the explicit ``semantic_cache_hit`` flag counts.

Reads two sources, because they answer different questions:

  * ``generation_result.csv`` -- the per-run verdict (the exported record).
  * ``manifest.json`` -- the headline count, and the per-run entries.

Usage:
    python tools/check_semantic_cache.py --exp EXP-20261004-034
    python tools/check_semantic_cache.py --all
    python tools/check_semantic_cache.py --exp EXP-A --exp EXP-B --quiet

Exit code 0 when no hit was found (or nothing was readable); 1 when at least one
run hit the cache. Missing or corrupt files are reported, never fatal: a gate
that crashes on a half-written artefact is a gate that gets bypassed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The flag as it appears in the exported CSV / manifest entries.
HIT_FIELD = "semantic_cache_hit"
COST_SAVED_FIELD = "semantic_cache_cost_saved_usd"
HIT_TURNS_FIELD = "semantic_cache_hit_turns"


def _is_hit(value) -> bool:
    """Read a truthy flag from a CSV cell or JSON value.

    A blank/None cell means "not recorded" (a pre-fix row, or a savepoint-recovered
    row) -- NOT a hit and NOT a verified clean run. It is reported separately as
    "unknown" so an experiment that predates the flag cannot be mistaken for one
    that was checked and came back clean.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in ("true", "1", "yes")


def _is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def _scan_csv(csv_path: Path) -> dict:
    """Per-run verdicts from an exported generation CSV."""
    result = {"hits": [], "checked": 0, "unknown": 0, "flagged": 0, "errors": [],
              "source": str(csv_path)}
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                result["errors"].append(f"{csv_path.name}: empty file (no header)")
                return result
            # The writer has historically emitted a leading "[" on the first
            # column name; normalise so a renamed header cannot hide the flag.
            fieldmap = {str(name).lstrip("["): name for name in reader.fieldnames}
            if HIT_FIELD not in fieldmap:
                result["errors"].append(
                    f"{csv_path.name}: no '{HIT_FIELD}' column "
                    f"(written before the flag existed)"
                )
                return result
            hit_col = fieldmap[HIT_FIELD]
            cost_col = fieldmap.get(COST_SAVED_FIELD)
            turns_col = fieldmap.get(HIT_TURNS_FIELD)
            for row in reader:
                result["checked"] += 1
                raw = row.get(hit_col)
                if _is_blank(raw):
                    result["unknown"] += 1
                    continue
                result["flagged"] += 1
                if _is_hit(raw):
                    result["hits"].append(
                        {
                            "instance_id": row.get(fieldmap.get("instance_id", ""), "?"),
                            "strategy": row.get(fieldmap.get("strategy", ""), "?"),
                            "cost_saved_usd": (
                                row.get(cost_col) if cost_col is not None else ""
                            ),
                            "hit_turns": (
                                row.get(turns_col) if turns_col is not None else ""
                            ),
                        }
                    )
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        result["errors"].append(f"{csv_path.name}: {type(exc).__name__}: {exc}")
    return result


def _scan_manifest(manifest_path: Path) -> dict:
    """Per-run verdicts plus the headline count from manifest.json."""
    result = {"hits": [], "checked": 0, "unknown": 0, "flagged": 0, "errors": [],
              "headline": None, "source": str(manifest_path)}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["errors"].append(f"{manifest_path.name}: {type(exc).__name__}: {exc}")
        return result
    if not isinstance(data, dict):
        result["errors"].append(f"{manifest_path.name}: top level is not an object")
        return result

    summary = data.get("summary")
    if isinstance(summary, dict):
        result["headline"] = summary.get("semantic_cache_hits")

    entries = data.get("results")
    if not isinstance(entries, list):
        result["errors"].append(f"{manifest_path.name}: 'results' is not a list")
        return result
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result["checked"] += 1
        raw = entry.get(HIT_FIELD)
        if _is_blank(raw):
            result["unknown"] += 1
            continue
        result["flagged"] += 1
        if _is_hit(raw):
            result["hits"].append(
                {
                    "instance_id": entry.get("instance_id", "?"),
                    "strategy": entry.get("strategy", "?"),
                    "cost_saved_usd": entry.get(COST_SAVED_FIELD, ""),
                    "hit_turns": entry.get(HIT_TURNS_FIELD, ""),
                }
            )
    return result


def check_experiment(exp_dir: Path) -> dict:
    """Scan one experiment directory. Never raises."""
    report = {"exp": exp_dir.name, "csv": None, "manifest": None, "errors": []}
    if not exp_dir.exists():
        report["errors"].append(f"{exp_dir}: directory not found")
        return report
    if not exp_dir.is_dir():
        report["errors"].append(f"{exp_dir}: not a directory")
        return report

    csv_path = exp_dir / "generation_result.csv"
    if csv_path.exists():
        report["csv"] = _scan_csv(csv_path)
    else:
        report["errors"].append("generation_result.csv not found")

    manifest_path = exp_dir / "manifest.json"
    if manifest_path.exists():
        report["manifest"] = _scan_manifest(manifest_path)
    return report


def _hits_of(report: dict) -> list[dict]:
    """All hits in one experiment, DEDUPLICATED by ``(instance_id, strategy)``.

    A run appears in BOTH ``generation_result.csv`` and ``manifest.json``, so
    concatenating the two sources counts it twice: 30 experiments report a
    headline roughly double the truth. The exit code stays correct (1 either way),
    which is exactly why this survived -- the GATE was fine, the NUMBER quoted in
    the thesis was not. This project has already been bitten by the same class of
    error (``resolved/total`` > 100% from duplicate rows), so the key here is
    deliberately the same one ``_merge_csv_rows`` uses: one run is one hit.

    Duplicates are merged rather than discarded: the first occurrence wins, but a
    blank field is filled in from a later one, so a value recorded in only one of
    the two files is not lost.
    """
    merged: dict[tuple[str, str], dict] = {}
    for key in ("csv", "manifest"):
        section = report.get(key)
        if not section:
            continue
        for hit in section.get("hits") or []:
            identity = (str(hit.get("instance_id", "?")),
                        str(hit.get("strategy", "?")))
            existing = merged.get(identity)
            if existing is None:
                merged[identity] = dict(hit)
                continue
            for field in ("cost_saved_usd", "hit_turns"):
                if _is_blank(existing.get(field)) and not _is_blank(hit.get(field)):
                    existing[field] = hit[field]
    return list(merged.values())


def _verifiable(report: dict) -> bool:
    """True when at least one source actually carried the flag.

    An artefact written before the flag existed cannot testify about it. Reporting
    "no hits" for such an experiment would be a false assurance -- the same class
    of error as reading a missing cost as 0.00 -- so it is reported as
    UNVERIFIABLE instead, and counted separately from a verified-clean scan.
    """
    for key in ("csv", "manifest"):
        section = report.get(key)
        if section and section.get("checked", 0) > 0 and section.get("flagged", 0) > 0:
            return True
    return False


def _print_report(report: dict, quiet: bool) -> None:
    exp = report["exp"]
    csv_section = report.get("csv")
    manifest_section = report.get("manifest")

    hits = _hits_of(report)
    checked = 0
    if csv_section:
        checked = max(checked, csv_section["checked"])
    verifiable = _verifiable(report)

    if quiet and not hits and verifiable and not report["errors"]:
        print(f"{exp}: clean ({checked} run(s) checked, 0 hits)")
        return
    if quiet and not hits and not verifiable:
        print(f"{exp}: UNVERIFIABLE (no run carries the flag; written pre-fix?)")
        return

    print(f"=== {exp} ===")
    if csv_section:
        print(f"  CSV rows checked        : {csv_section['checked']}")
        print(f"  CSV rows with no flag   : {csv_section['unknown']} (not recorded)")
    if manifest_section:
        headline = manifest_section.get("headline")
        if headline is not None:
            print(f"  manifest headline hits  : {headline}")
        print(f"  manifest entries checked: {manifest_section['checked']}")

    for section in (csv_section, manifest_section):
        if section:
            for err in section["errors"]:
                print(f"  [warn] {err}")
    for err in report["errors"]:
        print(f"  [warn] {err}")

    if hits:
        print(f"  [HIT] SEMANTIC CACHE HITS: {len(hits)}")
        for hit in hits:
            saved = hit.get("cost_saved_usd")
            turns = hit.get("hit_turns")
            detail = []
            if not _is_blank(saved):
                detail.append(f"saved ${saved}")
            if not _is_blank(turns):
                detail.append(f"{turns} request(s) hit")
            suffix = f" ({', '.join(detail)})" if detail else ""
            print(f"       - {hit['instance_id']} / {hit['strategy']}{suffix}")
        print(
            "  -> these runs were served a cached response and did NOT measure the\n"
            "     strategy; exclude them before comparing strategies."
        )
    elif verifiable:
        print("  [ok] no semantic-cache hit detected")
    else:
        print(
            "  [unknown] UNVERIFIABLE: no artefact carries the 'semantic_cache_hit'\n"
            "            flag, so a hit could not have been recorded. This is NOT a\n"
            "            clean verdict -- the run predates the flag."
        )


def main(argv: list[str] | None = None) -> int:
    """Return 0 (clean), 1 (hits found) or 2 (the check itself could not run).

    The three codes are distinct on purpose. A crash that exits 1 is
    indistinguishable from a real hit, so a broken check would be read as a
    dirty sweep -- and, worse, a genuinely clean sweep that crashed would block
    the batch. Exit 2 says "no verdict", which is the honest answer.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Detect semantic (response) cache hits in experiment artifacts. "
            "Exit 1 when any run hit the cache; exit 2 when the check could not run."
        )
    )
    parser.add_argument("--exp", action="append", default=[],
                        help="experiment id (repeatable), e.g. --exp EXP-20261004-034")
    parser.add_argument("--all", action="store_true",
                        help="scan every experiment under results/")
    parser.add_argument("--results-dir", default=str(ROOT / "results"),
                        help="results directory (default: <repo>/results)")
    parser.add_argument("--quiet", action="store_true",
                        help="print one line per clean experiment")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    targets: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        """Add a scan target once. The SAME experiment can be reachable twice --
        ``--all`` plus an explicit ``--exp`` naming something already covered, or
        the same ``--exp`` given twice -- and counting it twice inflates both the
        scan count and the hit total. Deduplicating on the resolved path keeps
        "one experiment = one verdict", the same rule as ``_hits_of``.
        """
        try:
            identity = str(path.resolve())
        except OSError:
            identity = str(path)
        if identity in seen:
            return
        seen.add(identity)
        targets.append(path)

    if args.all:
        if not results_dir.is_dir():
            print(f"⚠ results directory not found: {results_dir}", file=sys.stderr)
            return 0
        for p in sorted(results_dir.iterdir()):
            if p.is_dir():
                _add(p)
    for exp in args.exp:
        _add(results_dir / exp)

    if not targets:
        parser.print_help()
        return 0

    total_hits = 0
    scanned = 0
    unverifiable: list[str] = []
    for target in targets:
        report = check_experiment(target)
        scanned += 1
        total_hits += len(_hits_of(report))
        if not _hits_of(report) and not _verifiable(report):
            unverifiable.append(report["exp"])
        _print_report(report, quiet=args.quiet)
        print()

    print(f"Scanned {scanned} experiment(s); {total_hits} semantic-cache hit(s).")
    if unverifiable:
        print(
            f"NOTE: {len(unverifiable)} experiment(s) UNVERIFIABLE (no flag recorded): "
            f"{', '.join(unverifiable)}"
        )
    if total_hits:
        print("EXIT 1: the sweep is NOT clean -- exclude the listed runs.")
        return 1
    if unverifiable and len(unverifiable) == scanned:
        print(
            "EXIT 0: no hit found, but NO experiment carried the flag -- this is an\n"
            "        absence of evidence, not evidence of absence. Re-run the sweep\n"
            "        with the flag present to get a real verdict."
        )
        return 0
    print("EXIT 0: no semantic-cache hit detected.")
    return 0


def _main_safe(argv: list[str] | None = None) -> int:
    """Run main(), turning an unexpected failure into exit 2 rather than a crash.

    Also forces ASCII-safe output: the default Windows console codepage (cp1252)
    cannot encode the arrows/emoji these messages would otherwise use, and an
    ``UnicodeEncodeError`` mid-report exited 1 -- the SAME code as a real hit.
    A tool whose failure mode looks like its finding is worse than no tool.
    """
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001 - a gate must not crash
        print(
            f"EXIT 2: the check could not complete ({type(exc).__name__}: {exc}). "
            f"This is NOT a verdict about the sweep.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(_main_safe())
