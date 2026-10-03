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
#: Thinking mode for the sweep. MUST be passed explicitly, for the same class of
#: reason MAX_REVISION_ROUNDS is: `.env` is not the only input, and a sweep that
#: silently ran a different regime than the one measured would be undetectable
#: from its results.
#:
#: Enabled on 2026-10-02 from a paired pilot (same 5 issues, same model):
#:   thinking OFF : 12/15 resolved (80%)   $0.3092   ~9 h projected for 150
#:   thinking ON  : 15/15 resolved (100%)  $1.0831   ~21 h projected for 150
#: The whole gain is django__django-11019, which had failed in all 20 prior
#: attempts across every experiment and only the gold patch had ever resolved.
THINKING = "true"
#: Reasoning effort. `medium` is the only level measured end to end.
REASONING_EFFORT = "medium"
#: Output ceiling PER REQUEST. Raised to 65536 for thinking mode: reasoning and
#: the answer share this budget, and one measured review turn reached ~32,465
#: tokens (99.1% of the old 32768) before completing. See .env for the full note.
MAX_TOKENS = 65536


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


def select_batch_for_only(
    only: int, exp_id: str, strategies: list[str], candidates: list[str],
) -> tuple[list[str], int, int]:
    """Take the next ``only`` still-incomplete RUNS, in order, from this experiment.

    Returns ``(issues, runs_this_session, runs_outstanding_in_experiment)``.

    The intended use is a staged sweep: run some issues, then continue with the
    next batch -- "resume, do 5 more issues" -- WITHOUT redoing what is finished
    and WITHOUT pulling in issues that were never part of this experiment.

    ``--limit`` cannot express that, because it counts ISSUES from the start of the
    dataset: after a partial sweep, "the first 20 issues" may already be done, so
    almost nothing runs while the command looks like it asked for 20 runs of work.

    Scope is deliberately restricted to instances ALREADY PRESENT in the
    experiment's savepoints. An earlier version selected from all 50 candidates and
    therefore ADDED new issues to an existing experiment -- measured on a 1-issue
    pilot (`EXP-20261002-279`), where `--only 2` quietly ran two unrelated issues
    (10924, 11001) for real money. Continuing an experiment must continue it, not
    extend it.

    Within that scope the batch is the next ``only`` outstanding runs in order. An
    issue is included whole even if its last strategy overshoots the budget,
    because the runner takes issue ids, not (issue, strategy) pairs.

    The "is it finished?" answer comes from ``experiments.runner._load_existing_ids``,
    the SAME predicate --resume uses. Reimplementing it would let the two disagree,
    so a run that --resume skips could still be counted here (or the reverse).
    """
    sys.path.insert(0, str(ROOT / "src"))
    from experiments.runner import _load_existing_ids, _resume_key

    exp_dir = ROOT / "results" / exp_id
    pred_dir = exp_dir / "predictions"

    def recorded_ids() -> list[str]:
        """Instances this experiment already knows about, in first-seen order."""
        seen: list[str] = []
        if not pred_dir.is_dir():
            return seen
        for s in strategies:
            jsonl = pred_dir / f"{s}.jsonl"
            if not jsonl.exists():
                continue
            # Reading a savepoint is BEST-EFFORT: a jsonl that cannot be read must
            # never kill the tool while it is deciding what to run. Three shapes
            # crashed with a raw traceback before this wrap (measured):
            #   * jsonl is a DIRECTORY -> open() raises PermissionError [WinError 5]
            #   * jsonl has invalid UTF-8 -> the read raises UnicodeDecodeError
            #     (raised during ITERATION, not at open(), so the try must wrap the
            #     whole `with`, not just the open call)
            #   * a line is valid JSON but not an object (e.g. `[1,2,3]` or `"s"`)
            #     -> entry.get raises AttributeError
            # An unreadable file simply contributes no ids (treated as empty); a
            # non-object line is skipped like any other unparseable line.
            try:
                with open(jsonl, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        iid = entry.get("instance_id")
                        if iid and iid not in seen:
                            seen.append(iid)
            except (OSError, UnicodeDecodeError):
                continue
        # Deterministic: savepoint order is the run order, but sort to be safe
        # against a partially written file.
        return sorted(seen)

    in_experiment = recorded_ids()

    if not in_experiment:
        # A brand-new experiment: every candidate is outstanding, so the first
        # ``only`` runs come from the first issues, rounded UP because one issue
        # yields len(strategies) runs.
        per_issue = max(1, len(strategies))
        n_issues = min(len(candidates), -(-only // per_issue))
        chosen = candidates[:n_issues]
        return chosen, len(chosen) * len(strategies), len(candidates) * len(strategies)

    def _safe_existing_ids(path: Path) -> set[str]:
        """``_load_existing_ids`` that never raises on a corrupt jsonl.

        ``_load_existing_ids`` (src/experiments/runner.py:342) reads the file with no
        guard, so the same corrupt shapes that crashed the two functions above crash
        HERE too -- reached once the experiment has at least one savepoint, via this
        call site. Measured: predictions/planning.jsonl as a DIRECTORY -> PermissionError
        at runner.py:373, rc=1; and a valid-JSON-but-non-object line -> AttributeError
        from ``entry.get`` at runner.py:382, rc=1. runner.py must not be touched, so
        the guard lives at the call site. Any of these yields the empty set, the
        conservative reading ("nothing known done" -> the run is retried rather than
        silently skipped). AttributeError is caught narrowly because the only ``.get``
        calls in that loader are on parsed JSON rows, so a non-dict row is its sole
        cause here.
        """
        try:
            return _load_existing_ids(str(path))
        except (OSError, UnicodeDecodeError, AttributeError):
            return set()

    done_per_strategy = {
        s: _safe_existing_ids(pred_dir / f"{s}.jsonl") for s in strategies
    }
    key = _resume_key("", MODEL, THINKING == "true")

    def outstanding(iid: str) -> int:
        # Reuse the same key construction as the loader: strip the leading '|'
        # that the empty instance id leaves behind.
        k = _resume_key(iid, MODEL, THINKING == "true")
        return sum(1 for s in strategies if k not in done_per_strategy[s])

    cumulative = sum(outstanding(i) for i in in_experiment)

    chosen: list[str] = []
    budget = only
    for iid in in_experiment:
        if budget <= 0:
            break
        n = outstanding(iid)
        if n == 0:
            continue                      # fully done; --resume will skip it
        chosen.append(iid)
        budget -= n

    return chosen, sum(outstanding(i) for i in chosen), cumulative


def _experiment_has_savepoints(exp_id: str | None, strategies: list[str]) -> bool:
    """True if the experiment already records at least one instance.

    This mirrors the ``recorded_ids()`` predicate inside ``select_batch_for_only``:
    an experiment "has savepoints" when any of its ``predictions/<strategy>.jsonl``
    files holds a row with an ``instance_id``. It decides whether ``--issues`` still
    does something under ``--only`` (see the warning in ``main``): with no savepoints
    the candidate pool IS ``--issues``, so the flag is used; with savepoints the batch
    comes from the experiment's own records and ``--issues`` is inert.

    Kept as a small standalone check so ``select_batch_for_only`` itself is not
    touched (its batch-sizing logic must not change).
    """
    if not exp_id:
        return False
    pred_dir = ROOT / "results" / exp_id / "predictions"
    if not pred_dir.is_dir():
        return False
    for s in strategies:
        jsonl = pred_dir / f"{s}.jsonl"
        if not jsonl.exists():
            continue
        # Same best-effort read as recorded_ids() above: an unreadable jsonl (a
        # DIRECTORY -> PermissionError, or invalid UTF-8 -> UnicodeDecodeError, which
        # surfaces while ITERATING the handle) must not crash the tool. Such a file
        # simply contributes no savepoint. A valid-JSON-but-non-object line would make
        # entry.get raise AttributeError, so it is skipped too.
        try:
            with open(jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("instance_id"):
                        return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


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
        # Thinking mode. Explicit for the same reason as the knobs above: a sweep
        # must run the regime that was measured, and nothing in its results would
        # reveal a silent fallback to .env.
        "--set", f"DEEPSEEK_THINKING={THINKING}",
        "--set", f"DEEPSEEK_REASONING_EFFORT={REASONING_EFFORT}",
        # Reasoning and the answer SHARE this budget, and a measured thinking turn
        # reached 99.1% of 32768. See the constant's note.
        "--set", f"MAX_TOKENS={MAX_TOKENS}",
        "--provider", "opencode",
        "--strategies", *strategies,
        "--instance-ids", *issues,
        "--rate-limit", "2.0",
    ]
    # Resume support. Without these two flags a restart creates a NEW experiment
    # directory and re-runs all 150 runs -- the completed work is on disk but never
    # reused, which at ~14 hours and ~$7-11 is the most expensive possible failure
    # mode. The runner already implements the skip (the resume-skip loop in
    # src/experiments/runner.py, which consults ``_load_existing_ids``) and main.py
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
    ap.add_argument("--only", type=int, default=None,
                    help="run about N still-INCOMPLETE runs (requires --resume "
                         "--exp-id). Counts runs, not issues, so it can drive a "
                         "staged sweep: 'do 30 runs, then check'. Rounded UP to "
                         "whole issues, so N can yield up to N+len(strategies)-1 "
                         "runs; --dry-run prints the exact count.")
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

    # --resume pointing at something that is not a REAL experiment is a TYPO, and it
    # used to succeed silently. Round 1 only closed the LITERAL case (EXP-TYPO-XXXX);
    # a reviewer then measured that the guard still leaked, because its predicate asked
    # "is this an existing directory" rather than "is this an experiment". Measured
    # (read-only, --dry-run) -- every one of these exited 0 and LAUNCHED a sweep:
    #
    #   --exp-id "   "        rc=0  LAUNCHED=YES   <- whitespace trims to results/
    #   --exp-id .            rc=0  LAUNCHED=YES   <- resolves to results/
    #   --exp-id ..           rc=0  LAUNCHED=YES   <- resolves to ROOT
    #   --exp-id /            rc=0  LAUNCHED=YES   <- absolute path
    #   --exp-id C:\Windows   rc=0  LAUNCHED=YES   <- absolute path (Windows)
    #   --exp-id csv          rc=0  LAUNCHED=YES   <- an existing non-experiment dir
    #
    # Path(ROOT/"results") / ".." is ROOT; a path with a drive/root discards the whole
    # left side on Windows; trailing spaces are trimmed by the OS. Each made is_dir()
    # True, so the guard passed -- and the runner then populated that location and ran
    # PAID jobs into it. The directories came from three places, not one:
    # create_experiment_dir() mkdirs artifacts/ and logs/ (src/experiment_id.py:133-138),
    # then the runner mkdirs patches/ (src/experiments/runner.py:989) and predictions/
    # (runner.py:990-991). That is the ORIGINAL defect again: money spent, results filed
    # where no analysis reads them (a staged sweep is ~21 h and ~$10.8).
    #
    # So the predicate is "is this a REAL experiment", not "does this path exist":
    # a bare NAME (no separators, not absolute, no surrounding space) that resolves
    # strictly INSIDE results/ and carries an experiment marker -- predictions/ or
    # experiment.yaml (see the marker note below) -- so a genuine experiment is never
    # rejected.
    #
    # A name-pattern check (EXP-\d{8}-\d{3}) was tried here and REMOVED: it was wrong
    # in three ways, all measured. `\d` matches Unicode digits, so the fullwidth
    # 'EXP-２０２６１００２-２７９' -- a name generate_experiment_id() can never produce --
    # passed. And it REJECTED real experiments: the counter is `f"{counter:03d}"`, a
    # MINIMUM width, so the tool's own EXP-20261002-1000 fails the pattern. That id
    # is reachable, not hypothetical: the counter increments once per main.py
    # INVOCATION (not per run -- a 150-run sweep is ONE id), and
    # results/experiment_index.json already shows a daily counter of 815 (20261001),
    # so >999 is only ~185 more invocations away on a busy day. After that, a
    # name-pattern guard would refuse to resume the tool's own directories. It also
    # rejected custom names the tool created on purpose
    # (results/dry_run, results/dry_run_groq, results/testing_run all have
    # predictions/), which src/main.py:76-78 documents as valid --exp-id targets. The
    # real-experiment predicate above covers every original leak without a name shape.
    #
    # Deliberately fires under --dry-run too: previewing a typo is what a dry run is
    # for, so refusing here catches the mistake BEFORE money leaves.
    #
    # It does NOT need to fire without --resume, but NOT for the reason an earlier
    # comment gave. That comment said `--exp-id X` alone re-runs into that directory;
    # measured, that path does not exist in this tool -- build_cmd (defined below)
    # appends `--exp-id` ONLY inside `if resume:`, so without --resume the id is
    # DISCARDED and never reaches main.py. So the guard is simply moot there: the
    # flag has no effect on what runs. (Do not "fix" build_cmd to pass it: a bare
    # --exp-id would re-run everything INTO an existing experiment, overwriting it,
    # which this staged-sweep wrapper does not want.)
    if args.resume and args.exp_id:
        exp_id = args.exp_id
        # Un-resolved on purpose: this is the exact path the user would look at, and
        # it is what the "Looked for" line prints (resolve() can normalise case or
        # symlinks on Windows and make the message harder to match against the shell).
        # Built OUTSIDE the try below so it is always defined for the error message;
        # it is a pure path join and cannot raise.
        looked_for = ROOT / "results" / exp_id
        reason = None
        # The WHOLE check runs inside ONE try/except, not just is_dir(). Two different
        # failures live in here and both used to escape as a raw traceback:
        #
        #   * NTFS Alternate Data Stream: 'EXP-20261002-279::$DATA'.is_dir() does not
        #     return False, stat() RAISES PermissionError [WinError 5]. Measured:
        #     --resume --exp-id EXP-20261002-279::$DATA --only 1 --dry-run -> rc=1.
        #   * symlink LOOP: (ROOT/"results"/exp_id).resolve() raises RuntimeError
        #     ("Symlink loop from ..."), which is NOT an OSError, so a try that caught
        #     only OSError still crashed. Measured with results/_loopA <-> _loopB:
        #     --resume --exp-id _loopA --only 1 --dry-run -> rc=1, traceback at the
        #     resolve() line.
        #
        # Wrapping only is_dir() left resolve() outside the guard and reintroduced the
        # exact defect for the fifth time, so the catch is (OSError, RuntimeError) and
        # covers EVERY filesystem call below (resolve, is_relative_to, is_dir, is_file).
        # Any such failure means "this cannot be inspected as a path" -> not an
        # experiment -> refuse (exit 2) with an actionable message, never a traceback.
        try:
            results_root = (ROOT / "results").resolve()
            if not exp_id.strip() or exp_id != exp_id.strip():
                reason = ("the id is blank or has surrounding whitespace; an experiment "
                          "id is a name like EXP-20261002-279 with no spaces")
            elif Path(exp_id).is_absolute() or Path(exp_id).anchor:
                reason = ("the id is an absolute path; --exp-id takes a NAME under "
                          "results/, not a location on disk")
            elif "/" in exp_id or "\\" in exp_id:
                reason = ("the id contains a path separator; an experiment id is a "
                          "single directory name, never a path")
            else:
                resolved = (ROOT / "results" / exp_id).resolve()
                if resolved == results_root or not resolved.is_relative_to(results_root):
                    reason = (f"the id resolves outside results/ (to {resolved}); a typo "
                              f"like '.' or '..' points at the whole project, not at an "
                              f"experiment")
                elif not looked_for.is_dir():
                    reason = "no such experiment directory"
                else:
                    # An experiment is identified by EITHER marker. predictions/ is
                    # the normal one (runner.py:991), but a run that died between
                    # create_experiment_dir (runner.py:980) and the predictions/ mkdir
                    # has experiment.yaml (written by on_experiment_start,
                    # runner.py:981-988) and no predictions/ yet -- it is still a
                    # started experiment and must be resumable. Directories that are
                    # not experiments have neither marker (results/csv, results/verify).
                    has_predictions = (looked_for / "predictions").is_dir()
                    has_yaml = (looked_for / "experiment.yaml").is_file()
                    if not has_predictions and not has_yaml:
                        reason = ("the directory exists but is not an experiment: it "
                                  "has neither a predictions/ directory nor an "
                                  "experiment.yaml file")
        except (OSError, RuntimeError) as exc:
            reason = (f"the id cannot be inspected as a path "
                      f"({type(exc).__name__}: {exc}); it may contain an alternate "
                      f"data stream (name:stream), a symlink loop, or otherwise "
                      f"illegal characters")
        if reason is not None:
            print(
                f"ERROR: --resume --exp-id {args.exp_id!r}: {reason}.\n"
                f"       Looked for: {looked_for}\n"
                f"       Check the id for a typo. To START a new experiment instead, "
                f"drop --resume\n"
                f"       and use --limit N for the first batch (e.g. --limit 5).",
                file=sys.stderr,
            )
            return 2

    # --limit selects the first N usable issues, so N must be >= 1. Measured
    # (read-only, --dry-run): `--limit -1` planned 147 runs and `--limit -5` planned
    # 135, both exit 0 -- `usable[:limit]` with a negative index means "all but the
    # last |limit|", so one stray minus sign launches almost the entire PAID sweep.
    # `--limit 0` is refused too: `usable[:0]` is no issues at all, a no-op that still
    # looks like a successful launch. Preview with --dry-run instead of spending.
    if args.limit is not None and args.limit < 1:
        print("ERROR: --limit must be at least 1 (it selects the first N usable "
              "issues). A negative value would run nearly the whole sweep, and 0 "
              "would run nothing.", file=sys.stderr)
        return 2

    # --only counts runs REMAINING, which is undefined without an experiment to
    # measure against. Running "the first N issues" instead would silently give the
    # flag a second meaning, so refuse rather than guess.
    if args.only is not None and not args.resume:
        print("ERROR: --only needs --resume --exp-id. It counts runs that are still "
              "INCOMPLETE, which cannot be known without an experiment to read.",
              file=sys.stderr)
        return 2
    if args.only is not None and args.only <= 0:
        print("ERROR: --only must be a positive number of runs.", file=sys.stderr)
        return 2
    if args.only is not None and args.limit is not None:
        print("ERROR: --only and --limit both bound the batch differently "
              "(runs remaining vs first N issues). Pick one.", file=sys.stderr)
        return 2

    # --only + --issues is NOT an error -- its meaning depends on whether the
    # experiment already has savepoints, and both readings are legitimate:
    #
    #   * fresh experiment (no savepoints): select_batch_for_only uses `candidates =
    #     args.issues or select_issues(None)`, so `--only 3 --issues X Y Z` means
    #     "work the next 3 runs from THIS set" -- the issues are USED.
    #   * existing experiment (savepoints present): the batch comes from the
    #     experiment's own recorded issues, and `candidates` is not consulted at all
    #     (select_batch_for_only only uses it in its fresh-experiment branch), so
    #     `--issues` has no effect. Measured: `--resume --exp-id EXP-20261002-279
    #     --only 3 --issues django__django-9999 --dry-run` ran the experiment's own
    #     10924/11001, not the named 9999.
    #
    # An earlier round refused the combination outright. That was too strong: it
    # rejected the fresh case, which is meaningful. So warn only where the flag is
    # inert (the existing-experiment case), and stay silent where it does something.
    if args.only is not None and args.issues:
        strategies_for_check = ["direct"] if args.smoke else args.strategies
        if _experiment_has_savepoints(args.exp_id, strategies_for_check):
            print(
                "WARNING: --issues is set, but this experiment already has savepoints. "
                "--only counts the experiment's own still-INCOMPLETE runs, so the "
                "batch comes from those, not from --issues -- the id list has no "
                "effect here. Continuing anyway.",
                file=sys.stderr,
            )

    strategies_preview = ["direct"] if args.smoke else args.strategies

    if args.only is not None:
        # Consider every runnable issue: the still-incomplete ones are not
        # necessarily the first N, so the candidate pool must be the full set.
        candidates = args.issues or select_issues(None)
        issues, runs, runs_cumulative = select_batch_for_only(
            args.only, args.exp_id, strategies_preview, candidates
        )
        if not issues:
            print("=" * 78)
            print("  FINAL SWEEP")
            print("=" * 78)
            print(f"  Nothing to do: all {runs_cumulative} run(s) in {args.exp_id} "
                  f"are already complete.")
            print("  (--only counts runs that are still INCOMPLETE; there are none.)")
            print("=" * 78)
            return 0
    elif args.issues:
        issues = args.issues
        runs_cumulative = None
    elif args.smoke:
        issues = select_issues(1)
        runs_cumulative = None
    else:
        issues = select_issues(args.limit)
        runs_cumulative = None

    strategies = strategies_preview
    # ``runs`` means THIS session's runs. For --only it was computed from the
    # incomplete set; otherwise it is simply the whole batch.
    if args.only is None:
        runs = len(issues) * len(strategies)
        runs_cumulative = runs

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
    print(f"  runs       : {runs}  (this session)")
    if runs_cumulative is not None and runs_cumulative != runs:
        print(f"  experiment : {runs_cumulative} run(s) total; "
              f"{runs_cumulative - runs} already done")
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
    print(f"  thinking   : {'ON' if THINKING == 'true' else 'OFF'}, "
          f"reasoning_effort={REASONING_EFFORT} (measured: +3 resolved, 3.5x cost)")
    print(f"  max tokens : {MAX_TOKENS:,} per request (reasoning + answer share it)")
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
        # Pass the SAME batch through, or the gate checks a different set than the
        # one about to run: preflight defaults to all 50 instances, so a 10-issue
        # pilot was gated on 40 repos it never touches -- green there says nothing
        # about the batch. Passing --limit keeps both selections identical, since
        # select_issues() and load_instances() both take the first N by sorted id.
        preflight_cmd = [sys.executable, str(preflight), "--quiet"]
        if args.limit is not None and not args.issues and not args.smoke:
            preflight_cmd += ["--limit", str(args.limit)]
        elif args.issues or args.smoke:
            # An explicit --issues list cannot be expressed as "first N", so say so
            # rather than silently checking a different set.
            print("  NOTE: --issues/--smoke given; preflight checks the full set")
            print("        (it does not accept an explicit id list).")
        rc = subprocess.call(preflight_cmd, cwd=str(ROOT))
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
        # The experiment's total, which for a staged `--only` sweep is larger than
        # this session's work. Reporting only `runs_planned` would understate the
        # experiment; reporting only this would overstate what is about to happen.
        "runs_planned_cumulative": runs_cumulative,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_iso": started.isoformat(),
        "total_tool_turns": TOTAL_TURNS,
        "revision_tool_turns": REVISION_TURNS,
        "budget_floor_per_act": FLOOR,
        "cost_limit_usd": COST_LIMIT_USD,
        "act_timeout_seconds": ACT_TIMEOUT_SECONDS,
        "thinking": THINKING == "true",
        "reasoning_effort": REASONING_EFFORT,
        "max_tokens": MAX_TOKENS,
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
        # Best-effort read of the previous bill window: it is a bookkeeping file, so
        # a corrupt one must never stop the sweep from starting. Before this wrap the
        # read could crash the tool two ways (measured on a real, non-dry-run start):
        #   * invalid UTF-8 -> UnicodeDecodeError (NOT caught by the old OSError-only
        #     except, so it escaped as a traceback)
        #   * valid JSON that is not an object (e.g. `[1,2,3]`) -> AttributeError from
        #     previous.get below
        # Any of these means "no usable previous window": fall back to {}.
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                previous = {}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            previous = {}
        history = previous.get("attempts")
        if not isinstance(history, list):
            history = []
        # Keep only dict rows: a hand-edited file could put a string in the list, and
        # ``a.get`` below (and the later fold-in) assumes every row is a mapping.
        history = [a for a in history if isinstance(a, dict)]
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
        # previous one; keep it under a dated backup. The read is byte-for-byte
        # archival, so it must tolerate the same corrupt shapes as above (invalid
        # UTF-8 raises UnicodeDecodeError, which is NOT an OSError).
        backup = state_path.with_name(
            f"sweep_started.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        try:
            backup.write_bytes(state_path.read_bytes())
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

    # Fold THIS attempt into the history as well, not only the previous one.
    #
    # The append-on-resume logic above records the attempt that was already in the
    # file, so the list always lagged one behind: the run in progress entered
    # `attempts` only when a LATER resume folded it in. A sweep that then completed
    # with no further resume never recorded its own window at all -- measured on a
    # 1-issue run + resume: the file held 1 attempt (the interrupted first one) and
    # the resume's window was missing, so `read_actual_bill.py` could not reconcile
    # the money that resume spent.
    #
    # Written here rather than at the top so the entry carries its real outcome
    # (elapsed/rc), which is what makes the window auditable after the fact.
    attempts = state.get("attempts") or []
    this_attempt = {
        k: state.get(k) for k in
        ("started_utc", "finished_utc", "elapsed_minutes", "rc", "resumed_from")
    }
    if not any(a.get("started_utc") == this_attempt["started_utc"] for a in attempts):
        attempts.append(this_attempt)
    state["attempts"] = attempts
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
