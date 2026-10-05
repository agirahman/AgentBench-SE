"""Are the planner's cited file paths REAL, or invented?

The planner prompt requires every path in ``affected_files`` to be one it opened.
That is the property that matters: a plausible-sounding plan naming files that do
not exist is worse than no plan, because the executor trusts it.

This checks each cited path against the instance's own checkout at its base commit.

Usage:
    python tools/check_plan_paths.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config  # noqa: E402


def checkout_for(instance_id: str) -> Path | None:
    from datasets import load_dataset

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base
    for row in load_dataset("SWE-bench/SWE-bench_Lite", split="test"):
        if row["instance_id"] != instance_id:
            continue
        owner, _, name = row["repo"].partition("/")
        for candidate in (base / owner / name / row["base_commit"],
                          base / owner / row["base_commit"]):
            if candidate.is_dir():
                return candidate
        return None
    return None


def cited_paths(plan_text: str) -> tuple[list[str], list[str]]:
    """Split a plan's paths into CLAIMED (affected_files) and MENTIONED (prose).

    The distinction matters and a first version of this tool conflated them, which
    produced a false alarm. The planner writes prose like "the default in
    ``global_settings.py``" -- a bare BASENAME used to refer to a file it already
    named in full. Checking those against the checkout root reports them as
    invented, when the real claim (`django/conf/global_settings.py`) is present and
    correct.

    So: ``affected_files`` is the CLAIM, and that is what the prompt requires to be
    a path the planner actually opened. Prose mentions are informational and are
    reported separately, never as failures.
    """
    claimed: list[str] = []
    try:
        payload = json.loads(plan_text)
        for entry in payload.get("affected_files") or []:
            if isinstance(entry, str):
                # Entries look like "path/to/file.py — reason".
                candidate = re.split(r"\s+[—\-–]\s+", entry.strip())[0].strip()
                if candidate:
                    claimed.append(candidate)
    except (json.JSONDecodeError, AttributeError):
        pass

    mentioned: list[str] = []
    for match in re.finditer(r"\b([\w./-]+\.(?:py|txt|rst|md|cfg|toml))\b", plan_text):
        mentioned.append(match.group(1))

    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for p in items:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    claimed_unique = _unique(claimed)
    # A prose mention that repeats a claimed path is not a separate mention.
    mentioned_unique = [p for p in _unique(mentioned) if p not in claimed_unique]
    return claimed_unique, mentioned_unique


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  PLANNER PATHS vs THE REAL CHECKOUT -- {exp.name}")
    print("=" * 78)

    grand_total = grand_missing = 0
    for m in sorted(exp.glob("artifacts/*/*/messages.jsonl")):
        instance = m.parent.parent.name
        strategy = m.parent.name
        checkout = checkout_for(instance)
        if checkout is None:
            continue

        for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("sender") != "planner" or d.get("kind") != "result":
                continue

            claimed, mentioned = cited_paths(d.get("content") or "")
            missing = [p for p in claimed if not (checkout / p).exists()]
            grand_total += len(claimed)
            grand_missing += len(missing)

            status = "OK" if not missing else "INVENTED"
            print(f"\n  {status:<9} {instance} / {strategy}")
            print(f"    claimed {len(claimed)} path(s) in affected_files, "
                  f"{len(missing)} not in the checkout")
            for p in claimed[:6]:
                mark = " " if (checkout / p).exists() else "?"
                print(f"      [{mark}] {p}")
            if missing:
                print(f"    MISSING: {missing}")
            if mentioned:
                # Informational only: prose may use a bare basename for a file it
                # already named in full, which is not a claim about a path.
                print(f"    prose mentions (not claims): {mentioned[:5]}")

    print()
    print("=" * 78)
    if grand_total:
        pct = grand_missing / grand_total * 100
        print(f"  {grand_missing} of {grand_total} cited paths are not in the checkout "
              f"({pct:.0f}%)")
        if grand_missing == 0:
            print("  Every path the planner cited exists -- the plans are grounded.")
        else:
            print("  An invented path makes the executor chase a file that is not there.")
    else:
        print("  no planner plans found")
    print("=" * 78)


if __name__ == "__main__":
    main()
