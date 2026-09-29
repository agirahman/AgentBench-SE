"""Cross-tabulate EXP-003 cap hits against resolved/unresolved per instance.

Answers: are the cap hits concentrated on instances that failed? If a cap hit
and a failure coincide, the failure cannot be attributed to the strategy alone --
the act was cut off before it could finish, so the patch measured is an
interrupted one.

Reads the eval report (resolved per instance) and the cap-hit list from the log.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
EXP = "EXP-20260928-003"

CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")


def cap_hits() -> dict[tuple[str, str], list[tuple[int, str]]]:
    log = ROOT / f"results/{EXP}/logs/experiment.log"
    ctx: list[str] = []
    out: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            ctx = [m.group(1), m.group(2)]
            continue
        c = CAP_RE.search(line)
        if c and len(ctx) == 2:
            out.setdefault((ctx[0], ctx[1]), []).append((int(c.group(1)), c.group(2)))
    return out


def resolved_map() -> dict[tuple[str, str], bool]:
    """resolved per (strategy, instance) from the Modal eval reports."""
    out: dict[tuple[str, str], bool] = {}
    for strat in ("direct", "planning", "review"):
        p = ROOT / f"results/{EXP}/predictions/{strat}_results.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        for rec in data.get("results", []):
            out[(strat, rec["instance_id"])] = bool(rec.get("resolved"))
    return out


def main() -> None:
    hits = cap_hits()
    res = resolved_map()
    instances = sorted({i for _, i in res})

    print(f"{'instance':24s} {'direct':>8s} {'planning':>9s} {'review':>8s}   cap hits (strategy:granted)")
    print("-" * 100)
    for inst in instances:
        row = []
        for strat in ("direct", "planning", "review"):
            v = res.get((strat, inst))
            row.append("-" if v is None else ("OK" if v else "FAIL"))
        caps = []
        for (strat, i), lst in sorted(hits.items()):
            if i == inst:
                caps.append(f"{strat}:{'+'.join(str(g) for g, _ in lst)}")
        print(f"{inst:24s} {row[0]:>8s} {row[1]:>9s} {row[2]:>8s}   {', '.join(caps) or '-'}")

    print()
    total_fail = sum(1 for v in res.values() if v is False)
    cap_fail = sum(
        1 for (s, i), v in res.items() if v is False and (s, i) in hits
    )
    total_cap = sum(1 for k in res if k in hits)
    print(f"failures: {total_fail}   of which cap-hit: {cap_fail}")
    print(f"runs with a cap hit: {total_cap}   of which failed: {sum(1 for k in hits if res.get(k) is False)}")


if __name__ == "__main__":
    main()
