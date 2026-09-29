"""Map every MAX-TURNS warning in an experiment log to its instance/strategy.

The budget warning is the only place that records the turn allowance an act was
actually granted. Mapping them to instances shows whether a cap is a harmless
rollover (an act spent less than its share, the next got more) or a real cutoff
(an act ran out of turns mid-work).
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")
CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260928-003"
    log = ROOT / f"results/{exp}/logs/experiment.log"
    if not log.exists():
        print(f"no log at {log}")
        return

    context: list[str] = []
    hits: list[tuple[str, str, str, int, str]] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            context = [m.group(1), m.group(2)]
            continue
        c = CAP_RE.search(line)
        if c:
            strategy = context[0] if context else "?"
            instance = context[1] if len(context) > 1 else "?"
            ts = line.split("|")[0].strip()[:19]
            hits.append((ts, strategy, instance, int(c.group(1)), c.group(2)))

    print(f"{len(hits)} cap hits in {exp}\n")
    print(f"{'time':19s}  {'strategy':9s} {'instance':24s} {'granted':>7s}  role")
    print("-" * 78)
    for ts, strategy, instance, granted, role in hits:
        print(f"{ts:19s}  {strategy:9s} {instance:24s} {granted:7d}  {role}")


if __name__ == "__main__":
    main()
