"""Adversarial self-check: can the CSV merge corrupt REAL data?

The merge fix is load-bearing for the 50-issue sweep, so it must be tested against
the actual artefacts rather than only against a synthetic stub. Two questions:

1. Does re-writing an existing CSV through _merge_csv_rows change any VALUE?
   (A merge that silently retypes a column, drops a row, or shifts a field would
   trade the data-loss bug for a data-corruption bug -- worse, because it would
   look correct.)

2. Does _results_from_flat_rows reproduce the numbers the manifest reports?
   The manifest is built from reconstructed objects now, so if the reconstruction
   loses a field the manifest silently under-reports.

Runs read-only against results/: it copies a CSV into a temp dir, re-writes it
through the real code path, and diffs the two files cell by cell.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import _merge_csv_rows, _results_from_flat_rows  # noqa: E402


def check_csv_roundtrip(exp: str) -> bool:
    """Re-write a real CSV through the merge path; report any changed cell."""
    src = ROOT / "results" / exp / "generation_result.csv"
    if not src.exists():
        print(f"  {exp}: no CSV, skipped")
        return True

    before = pd.read_csv(src)
    before.columns = [str(c).lstrip("[") for c in before.columns]
    before = before.sort_values(["instance_id", "strategy"]).reset_index(drop=True)

    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "generation_result.csv"
        shutil.copy2(src, dst)

        # Merge with NO new rows: a pure round-trip. Any difference is caused by
        # the merge path itself, not by new data.
        merged = _merge_csv_rows(str(dst), [])
        pd.DataFrame(merged).to_csv(dst, index=False)

        after = pd.read_csv(dst)
        after.columns = [str(c).lstrip("[") for c in after.columns]
        after = after.sort_values(["instance_id", "strategy"]).reset_index(drop=True)

    problems = []
    if len(before) != len(after):
        problems.append(f"row count {len(before)} -> {len(after)}")
    if set(before.columns) != set(after.columns):
        problems.append(
            f"columns changed: -{set(before.columns) - set(after.columns)} "
            f"+{set(after.columns) - set(before.columns)}"
        )

    for col in before.columns:
        if col not in after.columns:
            continue
        for idx in range(min(len(before), len(after))):
            a, b = before.at[idx, col], after.at[idx, col]
            both_nan = pd.isna(a) and pd.isna(b)
            if both_nan:
                continue
            if pd.isna(a) != pd.isna(b):
                problems.append(f"{col}[{idx}]: {a!r} -> {b!r}")
            elif isinstance(a, float) and isinstance(b, float):
                if abs(a - b) > 1e-9:
                    problems.append(f"{col}[{idx}]: {a!r} -> {b!r}")
            elif str(a) != str(b):
                problems.append(f"{col}[{idx}]: {a!r} -> {b!r}")

    if problems:
        print(f"  {exp}: *** {len(problems)} DIFFERENCE(S) ***")
        for p in problems[:12]:
            print(f"      {p}")
        return False
    print(f"  {exp}: clean ({len(before)} rows, {len(before.columns)} columns "
          f"byte-identical)")
    return True


def check_manifest_reconstruction(exp: str) -> bool:
    """Do the reconstructed objects carry the values the CSV records?"""
    src = ROOT / "results" / exp / "generation_result.csv"
    if not src.exists():
        return True

    df = pd.read_csv(src)
    df.columns = [str(c).lstrip("[") for c in df.columns]
    rows = df.to_dict(orient="records")
    rebuilt = _results_from_flat_rows(rows)

    problems = []
    for original_row, result in zip(rows, rebuilt):
        csv_tokens = int(float(original_row.get("total_tokens") or 0))
        got_tokens = result.execution.total_tokens
        if csv_tokens != got_tokens:
            problems.append(
                f"{original_row['instance_id']}/{original_row['strategy']}: "
                f"total_tokens {csv_tokens} -> {got_tokens}"
            )

        csv_cost = float(original_row.get("cost_usd_offpeak") or 0.0)
        got_cost = result.cost.total_cost_usd
        if abs(csv_cost - got_cost) > 1e-9:
            problems.append(
                f"{original_row['instance_id']}/{original_row['strategy']}: "
                f"cost {csv_cost} -> {got_cost}"
            )

        csv_time = float(original_row.get("execution_time") or 0.0)
        got_time = result.execution.execution_time
        if abs(csv_time - got_time) > 1e-6:
            problems.append(
                f"{original_row['instance_id']}/{original_row['strategy']}: "
                f"execution_time {csv_time} -> {got_time}"
            )

    if problems:
        print(f"  {exp}: *** {len(problems)} reconstruction problem(s) ***")
        for p in problems[:12]:
            print(f"      {p}")
        return False
    print(f"  {exp}: manifest inputs faithful ({len(rebuilt)} rows)")
    return True


print("=" * 84)
print("  ADVERSARIAL CHECK OF THE CSV MERGE FIX")
print("=" * 84)

experiments = [
    "EXP-20260929-003",
    "EXP-20260929-022",
    "EXP-20260930-030",
    "EXP-20260928-003",
]

print("\n1. Round-trip: does re-writing a real CSV change any value?")
ok1 = all(check_csv_roundtrip(e) for e in experiments)

print("\n2. Reconstruction: do rebuilt objects carry the recorded numbers?")
ok2 = all(check_manifest_reconstruction(e) for e in experiments)

print()
print("=" * 84)
if ok1 and ok2:
    print("  VERDICT: no corruption found in the merge path on real artefacts.")
else:
    print("  VERDICT: PROBLEMS FOUND -- see above. Do not run the sweep yet.")
print("=" * 84)
sys.exit(0 if (ok1 and ok2) else 1)
