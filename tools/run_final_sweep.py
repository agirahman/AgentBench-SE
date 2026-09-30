"""The 50-issue sweep: 3 strategies x 50 instances, one paid model.

This is the final run the thesis rests on, so the config is fixed here rather than
left to .env -- a stray shell variable silently changing the budget is the exact
class of problem that made earlier results non-comparable (see MEMORY, config
drift). Every knob is passed explicitly through tools/run_with_env.py.

What is deliberately different from the earlier 3-issue runs
-----------------------------------------------------------

1. REVISION_TOOL_TURNS=8 (was 0).

   With 0, review's revision act was granted the floor of 1 turn, so it could make
   one tool call and never edit. Measured across EXP-20260929-003 and
   EXP-20260929-022: "Tool loop hit max_tool_turns=1 for role=executor" on the
   revision act, 0 edits. That means every "review" number produced so far measures
   ONE executor act plus a decorative round, not review+revision.

   The reserve is CARVED OUT of TOTAL_TOOL_TURNS, so the totals stay equal:
   direct 40, planning 40, review 32+8=40. An earlier version added it on top and
   gave review 48 turns, which would confound any claim that review is better.

2. COST_LIMIT_USD=3.00 (the reference value).

   mini-SWE-agent and SWE-agent both cap a task at $3. The measured cost of a run
   on this route is ~$0.03, so it does not bind -- it exists so a pathological loop
   cannot spend without bound on a paid route. The 3-issue RQ3 run used $1.00,
   which was also non-binding; $3 matches the reference.

3. ACT_TIMEOUT_SECONDS=3600 per act.

   A turn budget does not bound duration: with the rate-limit backoff schedule one
   request can sleep 180s before failing, and a 200-turn act is 201 requests. A
   single review run was measured at 5,992 s (100 minutes, a 502). This bounds the
   pathological case while leaving room for an honest long act -- at pool 100 the
   worst healthy act ran long enough that a 1800s bound would have truncated it.

   NOTE: this is PER ACT, and a review run has up to 3 + 2 x MAX_REVISION_ROUNDS
   acts (11 with 4 rounds), so a single pathological review run can occupy hours.
   There is no overall time budget; the savepoint per run means an interrupted sweep
   can be continued with --resume instead of being restarted.

Cost and time
-------------

Measured on the 3-issue paid run: $0.298 for 9 runs, 14.9 minutes. Extrapolated:
150 runs ~= $5 and ~4-6 hours, depending on how often review revises. The review
arm will cost MORE per run than before because revisions now actually happen --
that is the point, and it is a real finding rather than a defect.

Usage
-----
    python tools/run_final_sweep.py --dry-run     # show the command, run nothing
    python tools/run_final_sweep.py --smoke       # 1 issue x 1 strategy
    python tools/run_final_sweep.py --limit 5     # first 5 issues (15 runs)
    python tools/run_final_sweep.py               # the full 150 runs

The start timestamp is written to logs/sweep_started.json so the bill window is
recoverable even if the shell that launched the run is gone, and so
tools/read_actual_bill.py can be pointed at it afterwards.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODEL = "cbai/deepseek-v4.1-flash"

#: The whole task's turn allowance, identical for all three strategies.
#:
#: 200, from the reference implementations rather than from tuning:
#:
#:   SWE-bench Pro (2025)   "maximum of 200 turns" per task  (arxiv 2509.16941v1)
#:   mini-SWE-agent         step_limit 250 per task          (swebench.yaml L112)
#:   OpenHands              max_iterations 500 per task      (config_utils.py)
#:   SWE-agent              no step cap at all, $3 per task   (models.py)
#:
#: The earlier pool of 40 was 5-12x tighter than every published cap and caused
#: the problem this replaces: acts ran out of turns mid-exploration, so their
#: results measured the budget rather than the strategy. Measured cost of that:
#: 3 truncated acts at pool 40 (EXP-20260929-003) and 5 in the 15-run pilot,
#: including the revision act that then made 0 edits.
#:
#: 200 is chosen from the two turn-based references (200, 250) as the more
#: conservative of the pair. At 200 a truncation is now informative -- it means
#: the agent genuinely needed more than a reference-scale allowance, not that we
#: picked a number 5x below everyone else's.
TOTAL_TURNS = 200
#: Turns review sets aside for revising, carved out of TOTAL_TURNS.
#:
#: 48 = 4 rounds x 2 acts x 6 turns. The 6 is MEASURED, not chosen: in pilot
#: EXP-20260930-415 the revision act that actually changed the patch used 6 turns
#: (2 edits); acts that edited successfully elsewhere used 6-17. A smaller grant
#: reads files and runs out before editing, which is how the "revision" was
#: decorative for two earlier experiments.
#:
#: An earlier value of 32 was documented as "4 rounds x 8" -- which is arithmetically
#: impossible: 4 rounds x 2 acts x 8 turns = 64, not 32. At 32 the split for 4 rounds
#: is 4+4 per round, below the measured 6, so every round would have run out of turns
#: mid-edit. tools/verify_revision_rounds.py checks this before a sweep.
REVISION_TURNS = 48
#: How many revise-and-re-review rounds the reserve above is sized for.
#:
#: This MUST be passed explicitly. It was not, and `.env` had MAX_REVISION_TURNS=1,
#: so a sweep would have run ONE round while the reserve was documented as "4 rounds
#: x 8" -- measuring a configuration nobody chose, with the arm under-revising.
#: check_budget_fairness.py could not catch it because it simulates one round, and
#: the round count is not part of the turn total it verifies. Found by a partner
#: audit (docs/AUDIT_SCALE_PARTNER.md, area 4 blocker).
MAX_REVISION_ROUNDS = 4
#: Guaranteed turns for each act still to come, in per_task mode.
FLOOR = 10
#: Dollar backstop per task -- the reference implementations' value ($3).
COST_LIMIT_USD = 3.00
#: Wall-clock bound per act, so retry backoff cannot hang a run for hours.
#:
#: Raised from 1800: at a 200-turn allowance an act legitimately runs longer, and
#: a bound that fires on a HEALTHY act converts a good run into a truncated one.
#: Measured worst case at pool 100 was 5,992 s for a single review run (a 502,
#: not a slow success), so 3600 still bounds the pathological case while leaving
#: room for an honest long act.
ACT_TIMEOUT_SECONDS = 3600


def select_issues(limit: int | None) -> list[str]:
    """Pick the instances that have a local checkout, deterministically.

    Ordered by instance id so the selection is reproducible and a `--limit` run
    covers the same instances every time; an arbitrary order would make two
    partial runs incomparable.

    Resolution checks the two documented layouts DIRECTLY rather than calling
    agents.tools.resolve_repo_root. That function's last resort is an os.walk over
    the owner directory -- 1.35 GB across 51 checkouts -- and calling it once per
    instance (300 times, mostly for instances that are not cached) does not finish
    in a reasonable time. Only instances with a checkout are runnable anyway, and
    without one the runner falls back to the shared sandbox base where the agent
    reads ANOTHER instance's files.
    """
    from datasets import load_dataset

    sys.path.insert(0, str(ROOT / "src"))
    from config import Config

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")

    usable: list[str] = []
    for row in sorted(ds, key=lambda r: r["instance_id"]):
        owner, _, name = row["repo"].partition("/")
        commit = row["base_commit"]
        if (base / owner / name / commit).is_dir() or (base / owner / commit).is_dir():
            usable.append(row["instance_id"])

    if limit is not None:
        return usable[:limit]
    return usable


def build_cmd(
    issues: list[str],
    strategies: list[str],
    dry_run: bool,
    resume: bool = False,
    exp_id: str | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "run_with_env.py"),
        "--set", f"OPENCODE_MODEL={MODEL}",
        "--set", f"TOTAL_TOOL_TURNS={TOTAL_TURNS}",
        "--set", "BUDGET_MODE=per_task",
        "--set", f"BUDGET_FLOOR_PER_ACT={FLOOR}",
        # The reserve that makes the review arm measure review+revision. Carved
        # out of TOTAL_TOOL_TURNS, so every strategy still gets 200 turns.
        "--set", f"REVISION_TOOL_TURNS={REVISION_TURNS}",
        # MUST be explicit. Left to .env this was 1, so review ran a single revision
        # round while the reserve was documented as 4 -- the arm under-revised and
        # nobody could tell from the turn totals. See MAX_REVISION_ROUNDS above.
        "--set", f"MAX_REVISION_TURNS={MAX_REVISION_ROUNDS}",
        "--set", f"COST_LIMIT_USD={COST_LIMIT_USD}",
        "--set", f"ACT_TIMEOUT_SECONDS={ACT_TIMEOUT_SECONDS}",
        "--provider", "opencode",
        "--strategies", *strategies,
        "--instance-ids", *issues,
        "--rate-limit", "2.0",
    ]
    # Resume support. Without these two flags a restart creates a NEW experiment
    # directory and re-runs all 150 runs -- the completed work is on disk but never
    # reused, which at ~14 hours and ~$7-11 is the most expensive possible failure
    # mode. The runner already implements the skip (runner.py:742-750) and main.py
    # already exposes the flags (main.py:64-80); this wrapper simply was not passing
    # them through. Found by a partner audit (docs/AUDIT_OPS_PARTNER.md, blocker B1).
    #
    # --exp-id is REQUIRED for --resume to mean anything: without it there is no
    # existing directory to continue.
    if resume:
        if not exp_id:
            raise ValueError("--resume needs --exp-id: there is no directory to resume")
        cmd += ["--resume", "--exp-id", exp_id]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Final 50-issue sweep (sequential).")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the command and exit")
    ap.add_argument("--smoke", action="store_true",
                    help="1 issue x 1 strategy -- verify the path end to end first")
    ap.add_argument("--limit", type=int, default=None,
                    help="use only the first N usable issues (for a pilot run)")
    ap.add_argument("--strategies", nargs="*", default=["direct", "planning", "review"])
    ap.add_argument("--issues", nargs="*", default=None,
                    help="explicit instance ids (overrides --limit)")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted sweep instead of starting a new one; "
                         "requires --exp-id")
    ap.add_argument("--exp-id", default=None,
                    help="the experiment directory to continue (e.g. EXP-20261001-001)")
    args = ap.parse_args()

    if args.resume and not args.exp_id:
        print("ERROR: --resume needs --exp-id, otherwise there is nothing to continue.",
              file=sys.stderr)
        return 2

    if args.issues:
        issues = args.issues
    elif args.smoke:
        issues = select_issues(1)
    else:
        issues = select_issues(args.limit)

    strategies = ["direct"] if args.smoke else args.strategies
    runs = len(issues) * len(strategies)

    print("=" * 78)
    print("  FINAL SWEEP")
    print("=" * 78)
    print(f"  model      : {MODEL}")
    print(f"  issues     : {len(issues)}")
    if len(issues) <= 8:
        for i in issues:
            print(f"               {i}")
    else:
        print(f"               {issues[0]} ... {issues[-1]}")
    print(f"  strategies : {', '.join(strategies)}")
    print(f"  runs       : {runs}")
    print(f"  budget     : per_task, total {TOTAL_TURNS} turns per strategy")
    print(f"               direct {TOTAL_TURNS} | planning {TOTAL_TURNS} | "
          f"review {TOTAL_TURNS - REVISION_TURNS}+{REVISION_TURNS} = {TOTAL_TURNS}")
    print(f"  revision   : up to {MAX_REVISION_ROUNDS} rounds, "
          f"{REVISION_TURNS} turns reserved "
          f"({REVISION_TURNS // (2 * MAX_REVISION_ROUNDS)} per act)")
    print(f"  reference  : SWE-bench Pro 200 / mini-SWE-agent 250 / OpenHands 500")
    print(f"               (per task; ours was 40 = 5-12x tighter than all of them)")
    print(f"  cost cap   : ${COST_LIMIT_USD:.2f} per task (backstop, reference value)")
    print(f"  act timeout: {ACT_TIMEOUT_SECONDS}s per act (bounds retry backoff)")
    print()
    print(f"  Fairness   : every strategy's task budget is {TOTAL_TURNS} turns, so a")
    print(f"               review win cannot be explained by a larger budget.")
    print(f"               Review sets {REVISION_TURNS} of its own turns aside to revise.")
    print()
    print(f"  Trajectory : per turn -- assistant text + reasoning + tool calls, then")
    print(f"               each tool result. Written to artifacts/<instance>/<strategy>/")
    print(f"               trajectory.jsonl (and trajectory.md to read directly).")
    print()

    # Gate on the preflight instead of printing a reminder. A dirty checkout makes
    # `git diff` capture changes the agent did not make, which reads in the results
    # as the agent solving the issue. That is not a visible failure -- it inflates
    # the scores -- so it has to be blocked before the sweep, not noticed after.
    # (A partner audit found the state changing between two preflights 25 minutes
    # apart while the script only printed a reminder.)
    if not args.dry_run:
        preflight = ROOT / "tools" / "preflight_repos.py"
        print("  Running preflight (dirty checkouts would inflate the scores)...")
        rc = subprocess.call(
            [sys.executable, str(preflight), "--quiet"],
            cwd=str(ROOT),
        )
        if rc != 0:
            print()
            print("  PREFLIGHT FAILED -- not starting the sweep.")
            print("  Fix with: python tools/clean_repos.py")
            print("  (Skipping this check would let an agent's patch be credited with")
            print("   changes that were already in the working tree.)")
            return 1
        print("  Preflight OK.")
        print()

    cmd = build_cmd(issues, strategies, args.dry_run, args.resume, args.exp_id)
    if args.dry_run:
        print("Command:")
        print("  " + " ".join(cmd[1:]))
        return 0

    started = datetime.now(timezone.utc)
    state = {
        "model": MODEL,
        "issues": issues,
        "strategies": strategies,
        "runs_planned": runs,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_iso": started.isoformat(),
        "total_tool_turns": TOTAL_TURNS,
        "revision_tool_turns": REVISION_TURNS,
        "budget_floor_per_act": FLOOR,
        "cost_limit_usd": COST_LIMIT_USD,
        "act_timeout_seconds": ACT_TIMEOUT_SECONDS,
        "resumed_from": args.exp_id if args.resume else None,
    }
    state_path = ROOT / "logs" / "sweep_started.json"

    # NEVER overwrite a previous attempt's bill window. The window is what lets
    # `read_actual_bill.py --compare` reconcile our accounting against the real
    # 9router bill, and an overwritten window means the money already spent cannot
    # be checked. A resumed attempt therefore APPENDS, and the earlier attempt's
    # window stays readable. Found by a partner audit
    # (docs/AUDIT_OPS_PARTNER.md, blocker B2): the write was unconditional.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() and args.resume:
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}
        history = previous.get("attempts") or []
        # Fold the previous top-level attempt into the history if it is not there.
        if previous.get("started_utc") and not any(
            a.get("started_utc") == previous.get("started_utc") for a in history
        ):
            history.append({
                k: previous.get(k) for k in
                ("started_utc", "finished_utc", "elapsed_minutes", "rc", "resumed_from")
            })
        state["attempts"] = history
    elif state_path.exists():
        # A fresh (non-resume) sweep still must not silently destroy the record of a
        # previous one; keep it under a dated backup.
        backup = state_path.with_name(
            f"sweep_started.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        try:
            backup.write_text(state_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  previous bill window archived: {backup.name}")
        except OSError as exc:
            print(f"  WARNING: could not archive the previous window: {exc}")

    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"  bill window starts: {state['started_utc']}")
    print()
    print("=" * 78, flush=True)

    t0 = time.time()
    rc = subprocess.call(cmd, cwd=str(ROOT))
    dt = time.time() - t0

    state["finished_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["elapsed_minutes"] = round(dt / 60, 1)
    state["rc"] = rc
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print()
    print("=" * 78)
    print(f"  SWEEP DONE  rc={rc}  in {dt / 60:.1f} min")
    print(f"  bill window: {state['started_utc']} .. {state['finished_utc']}")
    print("=" * 78)
    print()
    print("  Verify before analysing:")
    print("    python tools/check_sweep_state.py --exp <EXP-id>     # every run present?")
    print("    python tools/verify_eval_consistency.py             # after evaluating")
    print("    python tools/read_actual_bill.py \\")
    print(f"      --since {state['started_utc']} --until {state['finished_utc']} \\")
    print(f"      --model {MODEL} --compare results/<EXP-id>")
    return rc


if __name__ == "__main__":
    sys.exit(main())
