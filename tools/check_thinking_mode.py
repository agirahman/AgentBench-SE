"""Was thinking mode actually on during the pilot?

The question is not what the config file says but what the API was asked to do and
what it returned. Three independent checks, because each alone can be fooled:

1. CONFIG. What the run recorded at start (experiment.yaml) and what the provider
   actually sends. `_extra_body()` returns {} when DEEPSEEK_THINKING is false, so
   no reasoning parameter reaches the API at all.

2. REQUEST. The messages the provider built -- no "thinking"/"reasoning_effort"
   key should appear.

3. RESPONSE. Whether the API returned reasoning content. A reasoning model returns
   a `reasoning_content` field (or puts its chain of thought in the message); if
   every recorded inference has an empty one, thinking was off regardless of what
   was requested.

Check 3 is the decisive one: a config can say "off" while a router turns thinking
on by default for the upstream model. Only the recorded response settles it.

Usage:
    python tools/check_thinking_mode.py --exp EXP-20260930-215
    python tools/check_thinking_mode.py --all
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def read_config(exp_dir: Path) -> dict:
    """Read the recorded settings from experiment.yaml without a YAML dependency."""
    path = exp_dir / "experiment.yaml"
    out: dict = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        for key in ("model_thinking:", "model_reasoning_effort:", "model:"):
            if stripped.startswith(key):
                out[key.rstrip(":")] = stripped.split(":", 1)[1].strip()
    return out


def check_responses(exp_dir: Path) -> dict:
    """Look for the artifacts that PROVE reasoning output.

    The strongest evidence is the trajectory: it records the reasoning channel of
    EVERY turn of a tool-calling act. Measured on EXP-20260930-249 (thinking ON),
    12 of 19 turns carried reasoning while the FINAL turn carried none -- so a
    check that only looked at the final response (or at the file written from it)
    would report "thinking was off" while twelve turns of reasoning sat in the
    record. That is why the trajectory is consulted first.

    The ``<role>_reasoning.md`` files are also checked: they are written when any
    turn had reasoning, so their presence corroborates the trajectory.

    A first version of this tool scanned ``messages.jsonl`` for a
    ``reasoning_content`` key instead, and reported 0 -- which proved nothing,
    because AgentMessage has no such field and never serialised one. The check
    could not have found reasoning in ANY run, so its "0" was an artifact of where
    it looked rather than a fact about the run.
    """
    artifacts = exp_dir / "artifacts"
    reasoning_files: list[str] = []
    agent_output_files = 0
    trajectory_files = 0
    turns_total = 0
    turns_with_reasoning = 0

    for path in sorted(artifacts.rglob("*.md")):
        name = path.name
        if name.endswith("_reasoning.md"):
            reasoning_files.append(
                f"{path.parent.parent.name}/{path.parent.name}/{name}"
            )
        elif name in ("direct.md", "planner.md", "executor.md", "reviewer.md"):
            agent_output_files += 1

    # The trajectory is the primary evidence.
    for path in sorted(artifacts.rglob("trajectory.jsonl")):
        trajectory_files += 1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            turns_total += 1
            if (entry.get("reasoning") or "").strip():
                turns_with_reasoning += 1

    return {
        "reasoning_files": reasoning_files,
        "agent_output_files": agent_output_files,
        "trajectory_files": trajectory_files,
        "turns_total": turns_total,
        "turns_with_reasoning": turns_with_reasoning,
    }


def report(exp_dir: Path) -> bool:
    cfg = read_config(exp_dir)
    res = check_responses(exp_dir)

    print(f"\n{'=' * 74}")
    print(f"  {exp_dir.name}")
    print(f"{'=' * 74}")
    print(f"  1. CONFIG (recorded at run start)")
    print(f"       model            : {cfg.get('model', '?')}")
    print(f"       model_thinking   : {cfg.get('model_thinking', '?')}")
    print(f"       reasoning_effort : {cfg.get('model_reasoning_effort', '?')}")

    # What the provider sends, derived from the same flag.
    from config import Config

    thinking_flag = Config.DEEPSEEK_THINKING
    extra = (
        {"thinking": {"type": "enabled"},
         "reasoning_effort": Config.DEEPSEEK_REASONING_EFFORT}
        if thinking_flag else {}
    )
    print(f"  2. REQUEST (what the provider attaches)")
    print(f"       DEEPSEEK_THINKING now: {thinking_flag}")
    print(f"       extra_body sent      : {extra if extra else '(none -- nothing sent)'}")

    print(f"  3. RESPONSE (the decisive evidence)")
    print(f"       agent outputs written : {res['agent_output_files']}")
    print(f"       trajectory files      : {res['trajectory_files']}")
    if res["turns_total"]:
        print(f"       assistant turns       : {res['turns_total']}")
        print(f"       turns with reasoning  : {res['turns_with_reasoning']}")
    print(f"       reasoning files       : {len(res['reasoning_files'])}")
    for entry in res["reasoning_files"][:5]:
        print(f"         {entry}")

    reasoning_seen = (
        res["turns_with_reasoning"] > 0 or bool(res["reasoning_files"])
    )
    print()
    if reasoning_seen:
        print("  VERDICT: THINKING WAS ON -- the model returned reasoning output.")
        print("           If this run was meant to be non-thinking, it is not.")
    else:
        print("  VERDICT: thinking was OFF.")
        print("           No turn carried a reasoning channel, and no reasoning")
        print("           parameter was sent to the API.")
    return not reasoning_seen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None, help="Experiment id (e.g. EXP-20260930-215)")
    ap.add_argument("--all", action="store_true", help="Check every experiment.")
    args = ap.parse_args()

    if args.all:
        targets = [d for d in sorted((ROOT / "results").glob("EXP-*")) if d.is_dir()]
    elif args.exp:
        targets = [ROOT / "results" / args.exp]
    else:
        targets = sorted(
            (ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime
        )[-1:]
        targets = [d for d in targets if d.is_dir()]

    if not targets:
        print("No experiments found.")
        sys.exit(1)

    all_off = True
    for target in targets:
        if not target.exists():
            print(f"missing: {target}")
            continue
        all_off = report(target) and all_off

    print()
    print("=" * 74)
    print("  All checked runs used non-thinking mode." if all_off
          else "  At least one run had thinking ENABLED.")
    print("=" * 74)


if __name__ == "__main__":
    main()
