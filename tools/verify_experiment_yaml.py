"""Verify an experiment.yaml loads back to the values it was written from.

A reproducibility record that a YAML loader reads differently than intended is
worse than no record: it looks authoritative. This checks the fields that the
budget work added (revision_tool_turns, instance_ids) plus the ones that must
stay typed (agents as a list, instance_ids as null when not targeted).

Usage: python tools/verify_experiment_yaml.py <EXP>
"""
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TYPES = {
    ("tool_calling", "enabled"): bool,
    ("tool_calling", "total_tool_turns"): int,
    ("tool_calling", "revision_tool_turns"): int,
    ("dataset", "n_issues"): int,
    ("strategies",): list,
    ("agents",): list,
}


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260929-001"
    path = ROOT / f"results/{exp}/experiment.yaml"
    if not path.exists():
        print(f"missing {path}")
        return

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    print(f"{exp} — loaded {len(cfg)} top-level keys\n")

    bad = 0
    for keys, expected in EXPECTED_TYPES.items():
        cur = cfg
        try:
            for k in keys:
                cur = cur[k]
        except (KeyError, TypeError):
            print(f"  MISSING {'.'.join(keys)}")
            bad += 1
            continue
        ok = isinstance(cur, expected)
        if not ok:
            bad += 1
        print(f"  {'OK  ' if ok else 'BAD '} {'.'.join(keys):34s} = {cur!r}")

    ids = cfg.get("dataset", {}).get("instance_ids", "ABSENT")
    print(f"\n  dataset.instance_ids = {ids!r}")
    if ids is None:
        print("    (null = full-suite run, correct)")
    elif isinstance(ids, list):
        print(f"    ({len(ids)} targeted id(s))")
    else:
        print("    BAD: expected null or a list")
        bad += 1

    rev = cfg.get("tool_calling", {}).get("revision_tool_turns", "ABSENT")
    print(f"  tool_calling.revision_tool_turns = {rev!r}")

    print(f"\n{'OK — all typed fields load correctly' if not bad else f'{bad} problem(s)'}")


if __name__ == "__main__":
    main()
