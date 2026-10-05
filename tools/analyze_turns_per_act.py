"""How many turns did each act of a run use, and was it capped?

Per-act turn accounting. The CSV only reports totals across a run, so this reads
the tool-call log and the cap warnings together to show WHERE the turns went:
a run can look cheap in total while one act consumed everything.

Usage: python tools/analyze_turns_per_act.py <EXP> [--instance ID]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")


def caps(exp: str) -> dict[tuple[str, str, str], int]:
    log = ROOT / f"results/{exp}/logs/experiment.log"
    ctx: list[str] = []
    out: dict[tuple[str, str, str], int] = {}
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            ctx = [m.group(1), m.group(2)]
            continue
        c = CAP_RE.search(line)
        if c and len(ctx) == 2:
            out[(ctx[0], ctx[1], c.group(2))] = int(c.group(1))
    return out


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260928-003"
    only = None
    if "--instance" in sys.argv:
        only = sys.argv[sys.argv.index("--instance") + 1]

    cap_map = caps(exp)
    art = ROOT / f"results/{exp}/artifacts"
    if not art.exists():
        print(f"missing {art}")
        return

    print(f"{exp} — tool calls per agent (a turn may issue several calls)\n")
    print(f"{'strategy':9s} {'instance':24s} {'agent':9s} {'calls':>6s} {'capped':>7s}")
    print("-" * 62)

    totals: dict[str, int] = defaultdict(int)
    for inst_dir in sorted(art.iterdir()):
        if not inst_dir.is_dir():
            continue
        if only and only not in inst_dir.name:
            continue
        for strat_dir in sorted(inst_dir.iterdir()):
            if not strat_dir.is_dir():
                continue
            tc = strat_dir / "tool_calls.jsonl"
            if not tc.exists():
                continue
            per_agent: dict[str, int] = defaultdict(int)
            for line in tc.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                per_agent[rec.get("agent", "?")] += 1
            for agent, n in sorted(per_agent.items()):
                grant = cap_map.get((strat_dir.name, inst_dir.name, agent))
                mark = f"{grant}" if grant else "-"
                print(
                    f"{strat_dir.name:9s} {inst_dir.name:24s} {agent:9s} "
                    f"{n:6d} {mark:>7s}"
                )
                totals[agent] += n

    print("-" * 62)
    print("total calls by agent:", dict(totals))


if __name__ == "__main__":
    main()
