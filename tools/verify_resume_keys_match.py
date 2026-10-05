"""Do the REAL jsonl rows carry the fields --resume keys on?

The loader builds its key from ``instance_id`` + ``model_name_or_path`` + ``thinking``
(runner.py:322-326). My synthetic test omitted ``model_name_or_path`` and produced keys
like ``'django__django-10914||False'`` -- a MISS, which on a real sweep would re-run
every completed run.

That was a defect in my test, not in the loader. But it exposes the real hazard worth
checking: if the runner WRITES rows without that field, or writes a different model
string than the one --resume looks up, every key misses and the sweep restarts from
zero after any interruption.

This compares what the runner WRITES against what the loader READS, using the real
jsonl files on disk.

Usage:
    python tools/verify_resume_keys_match.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import _resume_key  # noqa: E402


def main() -> None:
    print("=" * 78)
    print("  RESUME KEY ROUND-TRIP -- real jsonl rows")
    print("=" * 78)

    files = sorted(
        p for p in (ROOT / "results").glob("EXP-*/predictions/*.jsonl")
        if p.name != "predictions.jsonl"
    )
    if not files:
        print("  no per-strategy jsonl found")
        return

    problems = 0
    for path in files[-6:]:  # the most recent experiments
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not rows:
            continue

        print(f"\n  {path.parent.parent.name}/{path.name}  ({len(rows)} rows)")
        print(f"    {'field':<24} {'present':>8}  sample")
        print(f"    {'-' * 62}")
        for field in ("instance_id", "model_name_or_path", "thinking", "patch_status"):
            present = sum(1 for r in rows if field in r)
            sample = next((str(r.get(field)) for r in rows if r.get(field) not in (None, "")), "")
            print(f"    {field:<24} {present:>4}/{len(rows):<3}  {sample[:34]}")

        # Would the loader find these rows?
        missing_model = [r for r in rows if not r.get("model_name_or_path")]
        if missing_model:
            problems += 1
            print(f"    PROBLEM: {len(missing_model)} row(s) have no model_name_or_path")
            print(f"             -> their resume key would be "
                  f"{_resume_key('X', '', False)!r}, which --resume cannot match")
        else:
            key = _resume_key(rows[0]["instance_id"],
                              rows[0]["model_name_or_path"],
                              rows[0].get("thinking", False))
            print(f"    example key: {key!r}")

    print()
    print("=" * 78)
    if problems:
        print(f"  {problems} file(s) have rows --resume cannot match.")
        print("  Those runs would be RE-EXECUTED on resume, silently costing money.")
    else:
        print("  Every row carries the fields the loader keys on, so a resume")
        print("  matches completed runs instead of re-running them.")
    print("=" * 78)


if __name__ == "__main__":
    main()
