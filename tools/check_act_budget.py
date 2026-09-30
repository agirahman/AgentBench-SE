"""Did the revision act starve? — per-act tool-call accounting from real runs.

Why this exists: review's revision act was granted the floor of ONE turn for three
consecutive experiments, so it could make a single tool call and never edit
anything. The strategy therefore never actually ran review+revision, which makes
any claim about "review" as a strategy unsupported.

The budget arithmetic said this should happen in per_task mode; this tool checks
whether it DID, from the recorded transcripts rather than from the code.

Message format (agents/base.py:123-131): each act writes ONE AgentMessage carrying
`tool_calls` -- the full list for that act -- so an act's call count is the length
of its tool_calls, NOT the number of messages. Counting messages reports 1 for
every act, which is the mistake this docstring exists to prevent: it produced a
confident and completely wrong "STARVED" reading on every act, including base acts
that had made 19 calls.

Usage:
    python tools/check_act_budget.py                 # the three review experiments
    python tools/check_act_budget.py --exp EXP-...   # one experiment
    python tools/check_act_budget.py --all           # every experiment with artifacts
"""
import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EDIT_TOOLS = {"edit_file", "write_file", "create_file", "apply_patch", "str_replace"}

#: Base-flow act counts per strategy. Acts beyond this index are revision rounds.
BASE_ACTS = {"direct": 1, "planning": 2, "review": 3}

DEFAULT_EXPERIMENTS = [
    ("RQ3 paid (total 40, floor 10)", "EXP-20260930-030"),
    ("level 100 (total 100, floor 10)", "EXP-20260929-022"),
    ("level 40 (total 40, floor 10)", "EXP-20260929-003"),
]


def find_transcripts(exp_dir: Path) -> dict:
    """Map (strategy, instance_id) -> messages.jsonl path."""
    out = {}
    for path in exp_dir.rglob("messages.jsonl"):
        parts = path.parts
        strat = next((s for s in ("direct", "planning", "review") if s in parts), None)
        inst = next((q for q in parts if q.startswith("django__django-")), None)
        if strat and inst:
            out[(strat, inst)] = path
    return out


def parse_acts(messages: list) -> list:
    """Return one record per act: agent, tool calls, edits.

    An orchestrator->agent task message with non-empty content opens an act; the
    next message from that agent closes it and carries the tool calls. Bookkeeping
    writes (empty content + bb_ops) are not act boundaries.
    """
    acts = []
    pending = None
    for msg in messages:
        sender = (msg.get("sender") or "").lower()
        receiver = (msg.get("receiver") or "").lower()
        if sender == "orchestrator" and receiver in ("planner", "executor", "reviewer"):
            if (msg.get("content") or "").strip() or not msg.get("bb_ops"):
                pending = {"agent": receiver}
            continue
        if pending and sender == pending["agent"]:
            names: Counter = Counter()
            for call in msg.get("tool_calls") or []:
                if isinstance(call, dict):
                    name = call.get("name") or (call.get("function") or {}).get("name") or ""
                    if name:
                        names[name] += 1
            pending["calls"] = sum(names.values())
            pending["edits"] = sum(c for n, c in names.items() if n in EDIT_TOOLS)
            pending["tools"] = names
            acts.append(pending)
            pending = None
    return acts


def report(exp_dir: Path, label: str) -> int:
    """Print per-act accounting. Returns the number of starved acts found."""
    transcripts = find_transcripts(exp_dir)
    if not transcripts:
        return 0

    print(f"\n{label}   [{exp_dir.name}, {len(transcripts)} transcripts]")
    print(f"  {'instance':<8} {'#':<3} {'agent':<10} {'calls':>6} {'edits':>6}  note")
    print(f"  {'-' * 64}")

    starved = 0
    for (strat, inst), path in sorted(transcripts.items()):
        try:
            messages = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  {inst:<8} could not read: {exc}")
            continue

        acts = parse_acts(messages)
        short = inst.replace("django__django-", "")
        base_count = BASE_ACTS.get(strat, 0)
        for index, act in enumerate(acts):
            is_revision = act["agent"] == "executor" and index >= base_count
            is_rereview = act["agent"] == "reviewer" and index >= base_count
            note = ""
            if is_revision and act["calls"] <= 1:
                note = "*** STARVED: <=1 call cannot edit a file ***"
                starved += 1
            elif is_rereview and act["calls"] <= 1:
                note = "*** STARVED: <=1 call cannot inspect the patch ***"
                starved += 1
            tag = " <- REVISION" if is_revision else (" <- re-review" if is_rereview else "")
            print(
                f"  {short:<8} {index:<3} {act['agent']:<10} "
                f"{act['calls']:>6} {act['edits']:>6}  {note}{tag}"
            )
    return starved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exp", action="append", default=None,
                        help="Experiment id (repeatable). Default: the three review runs.")
    parser.add_argument("--all", action="store_true",
                        help="Every experiment under results/ that has artifacts.")
    args = parser.parse_args()

    if args.all:
        targets = [
            (d.name, d) for d in sorted((ROOT / "results").glob("EXP-*"))
            if any(d.rglob("messages.jsonl"))
        ]
    elif args.exp:
        targets = [(e, ROOT / "results" / e) for e in args.exp]
    else:
        targets = [(label, ROOT / "results" / exp) for label, exp in DEFAULT_EXPERIMENTS]

    print("=" * 96)
    print("  ACT BUDGET CHECK -- tool calls actually made per act")
    print("=" * 96)

    total_starved = 0
    for label, path in targets:
        if not path.exists():
            print(f"\n{label}: MISSING ({path})")
            continue
        total_starved += report(path, label)

    print()
    print("=" * 96)
    if total_starved:
        print(f"  {total_starved} starved act(s) found.")
        print("  A revision granted 1 call can make ONE tool call. If that call is a")
        print("  read, no edit was possible whatever the reviewer diagnosed -- so the")
        print("  review arm did not measure review+revision and cannot be compared.")
        print("  Fix: set REVISION_TOOL_TURNS >= 8 per allowed round.")
    else:
        print("  No starved revision acts found.")
    print("=" * 96)


if __name__ == "__main__":
    main()
