"""Will 150 runs at the 200-turn budget fit on disk, and how big is a run?

The trajectory now stores FULL tool outputs (not the 2000-char previews), and the
budget is 5x the old one, so the artifact size grew in two independent ways. A
14-hour sweep that fills the disk would fail near the end -- the worst possible
time, because most of the work would already be done.

Everything here is MEASURED from the pilot on disk, not estimated.

Usage:
    python tools/check_disk_budget.py
    python tools/check_disk_budget.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    ap.add_argument("--runs", type=int, default=150)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  DISK BUDGET -- measured from {exp.name}")
    print("=" * 78)

    art = exp / "artifacts"
    if not art.is_dir():
        print("  no artifacts directory")
        return

    # Per-run size, and the size of each artifact KIND so the growth source is clear.
    run_sizes: list[tuple[str, int]] = []
    by_kind: dict[str, int] = {}
    for run_dir in sorted(art.glob("*/*")):
        if not run_dir.is_dir():
            continue
        total = 0
        for f in run_dir.rglob("*"):
            if not f.is_file():
                continue
            size = f.stat().st_size
            total += size
            by_kind[f.name] = by_kind.get(f.name, 0) + size
        run_sizes.append((f"{run_dir.parent.name}/{run_dir.name}", total))

    if not run_sizes:
        print("  no runs found")
        return

    sizes = [s for _, s in run_sizes]
    mean = sum(sizes) / len(sizes)
    biggest = max(run_sizes, key=lambda kv: kv[1])

    print(f"\n  runs measured : {len(run_sizes)}")
    print(f"  mean per run  : {mean / 1024:,.0f} KB")
    print(f"  largest run   : {biggest[1] / 1024:,.0f} KB  ({biggest[0]})")
    print(f"  total for {len(run_sizes)} : {sum(sizes) / 1024 / 1024:,.1f} MB")

    print(f"\n  projected for {args.runs} runs:")
    print(f"    at the mean  : {mean * args.runs / 1024 / 1024:,.1f} MB")
    print(f"    at the max   : {biggest[1] * args.runs / 1024 / 1024:,.1f} MB")

    print("\n  bytes by artifact kind (all runs):")
    for name, size in sorted(by_kind.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {name:<24} {size / 1024:>10,.0f} KB")

    # Free space on the drive holding results/.
    usage = shutil.disk_usage(ROOT)
    print(f"\n  disk holding the repo:")
    print(f"    total : {usage.total / 1e9:,.1f} GB")
    print(f"    free  : {usage.free / 1e9:,.1f} GB")
    print(f"    used  : {usage.used / 1e9:,.1f} GB ({usage.used / usage.total * 100:.0f}%)")

    needed = biggest[1] * args.runs
    print(f"\n  worst-case need for {args.runs} runs: {needed / 1e9:,.2f} GB")
    if usage.free > needed * 2:
        print(f"  VERDICT: room to spare ({usage.free / 1e9:.1f} GB free vs "
              f"{needed / 1e9:.2f} GB needed)")
    elif usage.free > needed:
        print(f"  VERDICT: fits, but with less than 2x headroom. Watch it.")
    else:
        print(f"  VERDICT: NOT ENOUGH SPACE -- the sweep would fail part-way.")

    print()
    print("=" * 78)
    print("  NOTE ON MEMORY")
    print("=" * 78)
    print("""
  Disk is only half the question. The trajectory is accumulated IN MEMORY for the
  duration of a run (InferenceResult.trajectory) before being written, so a run whose
  tool outputs are very large holds them in RAM. At 200 turns the pilot's heaviest
  act collected ~166k chars of tool output; a pathological run could be several times
  that. There is no cap on the trajectory list.
""")


if __name__ == "__main__":
    main()
