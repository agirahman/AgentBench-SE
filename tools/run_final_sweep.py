"""The 50-issue sweep: 3 strategies x 50 instances, one paid model.

This is the final run the thesis rests on, so the config is fixed here rather than
left to .env -- a stray shell variable silently changing the budget is the exact
class of problem that made earlier results non-comparable (see MEMORY, config
drift). Every knob is passed explicitly through tools/run_with_env.py.

The model
---------
Routes are available and `--route` selects one. A route is a BUNDLE --
model, provider, the env var that provider reads, and the rate card the cost
guard prices with -- because the dangerous failure is a MIX of the two
(recording model A while calling model B). The resume key is
`instance|model|thinking`, so a mix also makes every finished run look
outstanding and re-runs it for real money; main() therefore REFUSES a resume
whose route disagrees with the experiment's own experiment.yaml.

`deepseek` (default): `deepseek-flash` on DeepSeek OFFICIAL
(api.deepseek.com/v1), served by the `deepseek` provider. The model name is the
one the official endpoint lists; the 9router spelling `deepseek-v4-flash` is not
a model there. Its rate card is the official DeepSeek card (src/evaluation/cost.py).

`meta`: `oc/muse-spark-1.3-contributor-free` (Meta's muse-spark,
contributor tier), served over the RESPONSES API by the `meta` provider. This
route is NOT Chat-Completions compatible: measured directly against 9router
(2026-10-09), chat.completions.create returns HTTP 200 with
finish_reason="in_progress", empty content and total_tokens=0 -- an empty answer
with no error -- while responses.create completes with working tool calling.
Running a sweep on the Chat Completions path would have produced 150 silent
empty runs.

`opencode`: `cbai/deepseek-v4.1-flash`, served over Chat Completions by the
`opencode` provider through the same 9router proxy. This is the route the earlier
paid runs used (RQ3, EXP-20260930-030); its rate card is MEASURED from the
9router bill (src/evaluation/cost.py).

The meta muse-spark model id does NOT appear in GET /v1/models (9router lists
only part of its catalogue), so the listing is not used as an availability check
anywhere.

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

3. ACT_TIMEOUT_SECONDS=0 (unbounded; no wall-clock bound per act).

   The wall-clock bound was REMOVED on 2026-10-06. Run 11 (planning on
   django__django-11019) produced NO_DIFF — an empty patch — because the
   executor was killed at the 3600s wall clock after 38 healthy turns. That
   truncated patch is not a measurement of the strategy; it is corrupted data.

   A run now ends only when the turn budget (fairness) or the agent itself
   finishes. What still bounds a run: the 200-turn budget per strategy,
   COST_LIMIT_USD per task, retry max_attempts=3, and API_TIMEOUT (per HTTP
   request). See the comment above the constant for the measured worst case.

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
    python tools/run_final_sweep.py --resume --exp-id EXP-X --only 30
                                                  # at most 30 runs; --resume
                                                  # skips the completed ones

Flag contract: `--resume` always travels with `--exp-id` AND `--only`.
`--resume` is the only owner of the "is this run finished?" predicate;
`--only N` is a CAP on this session's runs (counted in RUNS, rounded up to
whole issues), not a quota that has to be filled.

The start timestamp is written to logs/sweep_started.json so the bill window is
recoverable even if the shell that launched the run is gone, and so
tools/read_actual_bill.py can be pointed at it afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The sweep routes, as BUNDLES. Each entry pins everything that must agree:
#: the model, the provider, the env var that provider reads, and the rate card
#: the cost guard prices with. Keeping them in one dict is the point -- an
#: earlier revision hardcoded a single route, and the failure mode of a partial
#: change is SILENT: e.g. setting OPENCODE_MODEL while selecting --provider meta
#: leaves the meta provider reading META_MODEL from .env, so the run calls one
#: model while the manifest records another, and every check still passes.
#:
#: `env_key` MUST be the variable the provider actually reads:
#:   * meta   -> META_MODEL     (src/config.py:Config.META_MODEL)
#:   * opencode -> OPENCODE_MODEL (src/config.py:Config.OPENCODE_MODEL)
#: A wrong key here is invisible without check_effective_model.py.
ROUTES: dict[str, dict[str, str]] = {
    # The default: Meta's muse-spark over the Responses API. See the module
    # docstring for why Chat Completions is not usable for this model.
    "meta": {
        "model": "oc/muse-spark-1.3-contributor-free",
        "provider": "meta",
        "env_key": "META_MODEL",
        # The contributor-tier card this route is billed at ($0.10/$0.002/$0.20).
        "guard_model": "oc/muse-spark-1.3-contributor-free",
    },
    # The paid route the earlier runs used (RQ3, the budget curve): DeepSeek
    # v4.1 Flash over Chat Completions. The guard card is the MEASURED 9router
    # rate ($0.13941/$0.002467/$0.51345), not the published DeepSeek card --
    # 9router billed cache hits at full price on this key (see cost.py).
    "opencode": {
        "model": "cbai/deepseek-v4.1-flash",
        "provider": "opencode",
        "env_key": "OPENCODE_MODEL",
        "guard_model": "cbai/deepseek-v4.1-flash-billed",
    },
    # DeepSeek OFFICIAL (api.deepseek.com/v1), not a proxy. The model name is
    # `deepseek-flash` -- the 9router spelling `deepseek-v4-flash` is not a model
    # the official endpoint lists. The guard card stays keyed on the 9router
    # name because that is the rate card this project reports against.
    "deepseek": {
        "model": "deepseek-flash",
        "provider": "deepseek",
        "env_key": "DEEPSEEK_MODEL",
        "guard_model": "deepseek-v4-flash",
    },
}
#: Route used when --route is not given. The final sweep's measured route.
DEFAULT_ROUTE = "deepseek"
#: The model the DEFAULT route calls, kept as a module-level name because
#: tests and tools (check_effective_model.py) refer to it directly.
MODEL = ROUTES[DEFAULT_ROUTE]["model"]
#: The provider of the default route (same reason as MODEL).
PROVIDER = ROUTES[DEFAULT_ROUTE]["provider"]

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
#: Wall-clock bound per act. 0 = unbounded (no wall-clock bound). The operator
#: removed it on 2026-10-06 after batch 1 was stopped.
#:
#: WHY IT WAS REMOVED (run 11 is the evidence):
#:   planning on django__django-11019 produced `NO_DIFF` -- an EMPTY patch --
#:   because the executor was killed at max_wall_seconds=3600 after 38 turns
#:   (3626s elapsed) while it was still making healthy progress. That is not a
#: measurement of the strategy; it is wall-clock truncation of a healthy run,
#: which corrupts the research data. A run must end because the turn budget
#: (fairness) or the agent stops on its own -- not because a stopwatch fires.
#:
#: WHAT STILL BOUNDS A RUN NOW (explicit, in this file and src/config.py):
#:   * turn budget (200 per strategy, src/config.py) -- the FAIRNESS control;
#:   * COST_LIMIT_USD (3.00 per task) -- a MONEY bound, not a time bound;
#:   * retry max_attempts=3 -- caps retry backoff;
#:   * API_TIMEOUT (180s) -- timeout on ONE HTTP request, not on work; without
#:     it a hung connection would block forever.
#:
#: MEASURED WORST CASE (honest, no stopwatch now): at pool 100 a single review
#:   run can take 5,992 s (100 min, a 502) per act, and a review run has up to
#:   3 + 2 x MAX_REVISION_ROUNDS acts, so one pathological review run can hold
#:   a worker for many hours. That is ACCEPTED -- an unbounded run is honest
#:   data, a truncated run is corrupt data.
ACT_TIMEOUT_SECONDS = 0
#: No-progress bound for one act, in seconds. NOT a wall-clock cap: it bounds how
#: long an act may go WITHOUT PRODUCING ANYTHING, so a healthy run that keeps
#: working runs as long as it needs to. This is what makes the removal of
#: ACT_TIMEOUT_SECONDS above safe.
#:
#: Why it is needed at all (2026-10-07, EXP-20261007-003): httpx's timeout is PER
#: READ, so a server that dribbles bytes resets it forever. With the wall-clock
#: bound at 0, nothing could interrupt a wedged connection and the sweep sat
#: silent for ~3 hours at CPU 0, discovered only by reading the log by hand.
#:
#: Why 900: it must exceed API_TIMEOUT (600) or it would fire on requests the read
#: timeout still permits. Measured across 767 requests in three experiments, the
#: longest single request was 598 s -- so 900 s has headroom and touched nothing
#: healthy. 0 would disable it; that is deliberately not what is pinned here.
STALL_TIMEOUT_SECONDS = 900
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


def select_issues(
    limit: int | None = None, instance_ids: list[str] | None = None
) -> list[str]:
    """Pick the instances that have a local checkout, deterministically.

    Ordered by instance id so the selection is reproducible and a `--limit` run
    covers the same instances every time; an arbitrary order would make two
    partial runs incomparable.

    ``instance_ids`` switches to VALIDATION mode: every id must exist in the
    dataset, and the known ones come back in dataset order. The checkout filter is
    not applied there -- "does this id exist" is a different question from "can
    this id run here", and the caller only wants the unknown-id ValueError.

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

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")

    if instance_ids is not None:
        wanted = list(dict.fromkeys(instance_ids))
        known = {row["instance_id"] for row in ds}
        missing = [i for i in wanted if i not in known]
        if missing:
            raise ValueError(
                f"unknown instance_id(s): {', '.join(missing)}. Pass ids exactly "
                f"as they appear in SWE-bench Lite (e.g. django__django-11001)."
            )
        order = {row["instance_id"]: i for i, row in enumerate(ds)}
        return sorted(wanted, key=lambda i: order[i])

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base

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
    model: str = MODEL,
) -> list[str]:
    """CAP the batch at ``only`` RUNS worth of issues; ``--resume`` does the skip.

    Returns the issues to hand the runner: the experiment's own ids first (in
    savepoint order), then the dataset tail, truncated to
    ``ceil(only / len(strategies))`` issues.

    ``--only`` is deliberately NOT a second implementation of "is this run
    finished?" -- that predicate belongs to ``--resume``
    (``experiments.runner._load_existing_ids``). Duplicating it here is what
    caused F22: with the scope restricted to ids already recorded, an experiment
    whose recorded runs were all complete could never continue into new issues.

    Consequences, intended:

    * a real session may run FEWER than ``only`` runs when ``--resume`` skips
      some of the selected issues -- a cap means "no more than", not a quota;
    * ``--issues`` is an alternative candidate pool (the dataset tail by
      default), not a requirement.

    ``model`` is unused: selection no longer reads resume keys. It stays in the
    signature so callers (and tests) that pass a model keep working.
    """
    del model
    pred_dir = ROOT / "results" / exp_id / "predictions"

    seen: list[str] = []
    if pred_dir.is_dir():
        for s in strategies:
            jsonl = pred_dir / f"{s}.jsonl"
            if not jsonl.exists():
                continue
            # BEST-EFFORT: deciding what to run must never die on a corrupt
            # savepoint. Three shapes used to crash with a raw traceback:
            #   * jsonl is a DIRECTORY -> PermissionError [WinError 5]
            #   * invalid UTF-8 -> UnicodeDecodeError (raised while ITERATING,
            #     so the try must wrap the whole `with`, not just open())
            #   * a line is valid JSON but not an object -> AttributeError
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
    recorded = sorted(seen)

    ordered = recorded + [c for c in candidates if c not in set(recorded)]
    n_issues = min(len(ordered), -(-only // max(1, len(strategies))))
    return ordered[:n_issues]


def validate_instance_ids(ids: list[str]) -> str | None:
    """Return an error message, or None when every id exists in the dataset.

    Uses the dataset's own lookup, whose ``ValueError`` names the unknown ids.
    A dataset that cannot be reached is NOT a failure: ``--dry-run`` must work
    offline, so the caller warns and passes the ids through unverified.
    """
    try:
        select_issues(instance_ids=ids)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a preview must not need the network
        # Any dataset failure (offline, missing cache, an SDK error) degrades to a
        # warning: --dry-run is the read-only preview and must stay usable offline.
        print(
            "WARNING: could not validate --issues offline; ids are passed "
            f"through unverified ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
    return None


def build_cmd(
    issues: list[str],
    strategies: list[str],
    dry_run: bool,
    resume: bool = False,
    exp_id: str | None = None,
    route: str = DEFAULT_ROUTE,
    allow_config_change: bool = False,
) -> list[str]:
    # Resolve the bundle FIRST, so a typo fails here rather than producing a
    # command that silently mixes one route's model with another's provider.
    r = ROUTES.get(route)
    if r is None:
        raise ValueError(
            f"unknown route {route!r}; known routes: {', '.join(sorted(ROUTES))}"
        )
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "run_with_env.py"),
        # The env var comes from the bundle and MUST be the one this provider
        # reads: the meta provider reads META_MODEL, the opencode provider reads
        # OPENCODE_MODEL. Setting the wrong key is silently ignored (the run
        # would use .env's model while every other check passed), so the pair is
        # pinned together in ROUTES rather than chosen independently here.
        "--set", f"{r['env_key']}={r['model']}",
        # The master switch for the whole tool mechanism. Config.TOOLCALL_ENABLED
        # defaults to FALSE, so leaving it to .env meant tools were on only
        # because that file happened to say true: a drifted .env would collapse
        # every strategy to single-shot with no error, and the readiness gate
        # does not check it. Pinned for the same reason as every knob below.
        "--set", "TOOLCALL_ENABLED=true",
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
        # The runaway guard must price the rate the upstream ACTUALLY bills, so
        # the card is part of the route bundle. Left to .env this was the
        # previous route's measured card (cbai/deepseek-v4.1-flash-billed:
        # output $0.51345/M), which for the muse route over-prices output ~2.6x
        # -- a $3.00 cap would then bind after ~$1.17 of real spend and replace
        # the turn-budget fairness invariant with a dollar bound, exactly the
        # class of silent drift the pins above exist to prevent.
        "--set", f"COST_GUARD_MODEL={r['guard_model']}",
        "--set", f"ACT_TIMEOUT_SECONDS={ACT_TIMEOUT_SECONDS}",
        # Explicit for the same reason as ACT_TIMEOUT_SECONDS: left to .env, a
        # stale value could silently disable the only guard against a wedged
        # connection, and nothing in the results would reveal it.
        "--set", f"STALL_TIMEOUT_SECONDS={STALL_TIMEOUT_SECONDS}",
        # Thinking mode. Explicit for the same reason as the knobs above: a sweep
        # must run the regime that was measured, and nothing in its results would
        # reveal a silent fallback to .env.
        "--set", f"DEEPSEEK_THINKING={THINKING}",
        "--set", f"DEEPSEEK_REASONING_EFFORT={REASONING_EFFORT}",
        # Isolation mode. MUST be explicit: the default used to be 'local', so a
        # sweep that did not set this executed agent commands on the HOST with no
        # isolation and no warning. Pinned to docker so the mode cannot depend on
        # an ambient variable, and so a missing daemon fails loudly at the first
        # run_tests instead of silently running unisolated.
        "--set", "SANDBOX_MODE=docker",
        # Reasoning and the answer SHARE this budget, and a measured thinking turn
        # reached 99.1% of 32768. See the constant's note.
        "--set", f"MAX_TOKENS={MAX_TOKENS}",
        # The provider comes from the same bundle as the model: meta talks the
        # Responses dialect, opencode the Chat Completions one.
        "--provider", r["provider"],
        "--strategies", *strategies,
        "--instance-ids", *issues,
        "--rate-limit", "2.0",
    ]
    # Resume support. Without these two flags a restart creates a NEW experiment
    # directory and re-runs all 150 runs -- the completed work is on disk but never
    # reused, which at ~21 hours (thinking ON) and ~$11 is the most expensive possible
    # failure mode. The runner already implements the skip (the resume-skip loop in
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
        # Forwarded, not invented here: main.py owns the guard and the flag. Without
        # this passthrough the wrapper could tell an operator to "re-run with
        # --allow-config-change" for 95 legacy experiments (savepoints, no
        # run_identity block) that the wrapper itself had no way to allow.
        if allow_config_change:
            cmd.append("--allow-config-change")
    if dry_run:
        cmd.append("--dry-run")
    return cmd


#: Marks a state file as a RECONSTRUCTION rather than a live sweep window. Set by
#: tools/... (see logs/sweep_started.RECONSTRUCTED.*.json) when a real window was
#: destroyed and had to be rebuilt from logs. Such a file is EVIDENCE, not a
#: window the sweep may archive as its own predecessor.
_RECONSTRUCTED_KEY = "_RECONSTRUCTED"


def _is_reconstructed(state_path: Path) -> bool:
    """True when the existing state file is a reconstruction, not a live window.

    Why this needs its own check: the sweep archives the previous state file
    before writing its own, so the reconstruction would be filed under a
    timestamp that belongs to a LATER sweep -- corrupting its provenance and
    losing the note that it is reconstructed. The file itself says
    "Do NOT let a real sweep archive this as if it were a live window"
    (``_NOT_A_LIVE_STATE_FILE``); this is that warning made executable.

    A file that cannot be read is NOT treated as reconstructed: the archival path
    is byte-for-byte and already tolerates corruption, and refusing to archive a
    real window because of a transient read error would be worse than the
    provenance problem this guards.
    """
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and bool(data.get(_RECONSTRUCTED_KEY))


def archive_previous_window(state_path: Path) -> str:
    """Preserve the existing bill window under a NON-COLLIDING name.

    Returns the archive filename, or "" when there was nothing to archive.

    Two protections, both from measured failures:

    1. **Reconstructions are not filed as live windows.** A reconstructed state
       file is evidence; filing it under a sweep timestamp would both misdate it
       and drop the marker saying it is reconstructed (K-1/K-2, 2026-10-07).

    2. **The name cannot collide.** The old name was
       ``sweep_started.{now:%Y%m%dT%H%M%SZ}.json`` -- SECOND resolution. Two
       ``main()`` calls inside one second produced the same name, so the second
       write silently destroyed the first. That is exactly how the 10-issue
       window of EXP-20261007-003 was lost: the archive step DID run, and its
       slot was then reused. The fix appends a short content hash, so two
       different windows can never share a filename, however close together they
       are written. Same-second collisions now produce two files, not one.
    """
    if not state_path.exists():
        return ""

    data = state_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:8]
    if _is_reconstructed(state_path):
        # Evidence, not a live window: keep it, but never as a dated live window.
        #
        # The content hash is in the name for the same reason as the live branch
        # (K-2R): a FIXED name is last-write-wins, so archiving two different
        # reconstructions silently kept only the second. The live branch got this
        # protection in K-2; this branch was left inconsistent with it.
        dest = state_path.with_name(
            f"{state_path.stem}.reconstructed_archived.{digest}"
            f"{state_path.suffix}"
        )
        try:
            dest.write_bytes(data)
            print(f"  previous state file is a RECONSTRUCTION -- preserved as "
                  f"{dest.name} (not filed as a live bill window)")
            return dest.name
        except OSError as exc:
            print(f"  WARNING: could not preserve the reconstructed window: {exc}")
            return ""

    backup = state_path.with_name(
        f"sweep_started.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f".{digest}.json"
    )
    try:
        backup.write_bytes(data)
        print(f"  previous bill window archived: {backup.name}")
        return backup.name
    except OSError as exc:
        print(f"  WARNING: could not archive the previous window: {exc}")
        return ""


#: The strategies the sweep runs when ``--strategies`` is not given.
DEFAULT_STRATEGIES = ["direct", "planning", "review"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Final 50-issue sweep (sequential).")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the command and exit")
    ap.add_argument("--smoke", action="store_true",
                    help="1 issue x 1 strategy -- verify the path end to end first")
    ap.add_argument("--limit", type=int, default=None,
                    help="use only the first N usable issues (for a pilot run)")
    ap.add_argument("--only", type=int, default=None,
                    help="CAP on this session's runs (requires --resume --exp-id). "
                         "Counts runs, not issues, and is rounded up to whole "
                         "issues, so it can yield up to len(strategies)-1 extra "
                         "runs; --dry-run prints the exact count. --resume is what "
                         "decides which runs are skipped, so the session may run "
                         "fewer than N.")
    ap.add_argument("--strategies", nargs="*", default=None,
                    choices=list(DEFAULT_STRATEGIES),
                    help="strategies to run (default: all three). A bare flag "
                         "with no value is refused.")
    ap.add_argument("--issues", nargs="*", default=None,
                    help="explicit instance ids (overrides --limit)")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted sweep instead of starting a new one; "
                         "requires --exp-id and --only")
    ap.add_argument("--exp-id", default=None,
                    help="the experiment directory to continue (e.g. EXP-20261001-001); "
                         "requires --resume")
    ap.add_argument("--allow-config-change", action="store_true",
                    help="resume even though the experiment was recorded with a "
                         "different provider/model/thinking, or with none recorded "
                         "(legacy experiments). Passed through to src/main.py.")
    ap.add_argument("--route", default=DEFAULT_ROUTE, choices=sorted(ROUTES),
                    help="which model/provider bundle to run: "
                         f"{', '.join(sorted(ROUTES))} (default: {DEFAULT_ROUTE})")
    args = ap.parse_args()

    # Resolve the route ONCE, here. Every downstream use (build_cmd, the resume
    # key, the banner, the state file) reads this bundle, so the model that gets
    # RECORDED and the model that gets CALLED cannot drift apart.
    route = ROUTES[args.route]
    model = route["model"]

    # --- Guards. All exit 2 and fire under --dry-run too: previewing a typo is
    # what a dry run is for, so a green dry-run must mean the command is runnable.
    if args.exp_id and not args.resume:
        # build_cmd appends --exp-id ONLY inside `if resume:`, so today the id is
        # silently discarded. Refuse instead of pretending it did something.
        print("ERROR: --exp-id needs --resume. Without it the id has no effect; "
              "to START an experiment use --limit N.", file=sys.stderr)
        return 2
    if args.resume and not args.exp_id:
        print("ERROR: --resume needs --exp-id, otherwise there is nothing to continue.",
              file=sys.stderr)
        return 2
    if args.resume and args.only is None:
        # A bare --resume re-runs everything the resume predicate considers
        # unfinished -- up to 150 runs. The batch must be bounded explicitly.
        print("ERROR: --resume needs --only N (a cap on this session's runs).",
              file=sys.stderr)
        return 2
    if args.smoke and args.limit is not None:
        print("ERROR: --smoke already fixes the batch to 1 issue; drop --limit.",
              file=sys.stderr)
        return 2
    if args.smoke and args.strategies is not None:
        print("ERROR: --smoke already fixes the strategy to direct; "
              "drop --strategies.", file=sys.stderr)
        return 2
    if args.smoke and args.only is not None:
        # Same class as `--smoke --limit`: --smoke fixes the batch to 1 issue, so a
        # run cap alongside it would be half-dead -- the strategies would still flip
        # to direct while --only changed nothing observable.
        print("ERROR: --smoke already fixes the batch to 1 issue; drop --only.",
              file=sys.stderr)
        return 2
    if args.strategies is not None and not args.strategies:
        print("ERROR: --strategies needs at least one name (or omit the flag for "
              f"all: {', '.join(DEFAULT_STRATEGIES)}).", file=sys.stderr)
        return 2
    if args.issues is not None and not args.issues:
        # `--issues` with no value parses to [], which is FALSY: every later use is
        # `args.issues or ...`, so the empty list was silently ignored and the
        # selection fell through to select_issues(None) -- the WHOLE dataset. That
        # is F41's failure mode (a flag that looks like it did something while the
        # sweep runs something else entirely).
        print("ERROR: --issues needs at least one instance id (or drop the flag to "
              "select from the dataset).", file=sys.stderr)
        return 2

    # One source of truth for the strategy list. `--strategies=None` means the
    # flag was not given; an empty list is refused above.
    if args.smoke:
        strategies = ["direct"]
    elif args.strategies:
        strategies = args.strategies
    else:
        strategies = list(DEFAULT_STRATEGIES)

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
    # The guard above already REFUSES `--exp-id` without `--resume`, so this branch
    # is only reached with both flags. That is deliberate: `build_cmd` (defined
    # below) appends `--exp-id` ONLY inside `if resume:`, so a bare `--exp-id` was
    # silently DISCARDED -- and "fixing" build_cmd to forward it would re-run
    # everything INTO an existing experiment and overwrite it, which this
    # staged-sweep wrapper does not want. Refusing the combination is the honest
    # outcome: a flag that has no effect must not look like it had one.
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

        # ROUTE CONSISTENCY. Resuming an experiment with a DIFFERENT model is not
        # an error the runner can see: the resume key is `instance|model|thinking`,
        # so with a mismatched model every finished run simply looks outstanding
        # and is run AGAIN -- paying twice and mixing two models into one
        # experiment's numbers, with the manifest still claiming one model.
        #
        # Measured shape of the trap: EXP-20261009-1548 was written by the meta
        # route (oc/muse-spark...). `--resume --exp-id EXP-20261009-1548` on the
        # opencode route would have re-run all 30 recorded runs on cbai and
        # written them into the same predictions/ files.
        #
        # So the route must agree with what the experiment recorded. The recorded
        # value is the ONLY trustworthy source: it was written at run time by the
        # runner from the live config, not copied from a command line.
        exp_yaml = looked_for / "experiment.yaml"
        if exp_yaml.is_file():
            try:
                import yaml

                cfg = yaml.safe_load(exp_yaml.read_text(encoding="utf-8"))
                recorded = ((cfg or {}).get("provider") or {}).get("model")
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                # Unreadable manifest: not evidence of a mismatch, and refusing
                # here would strand a real experiment over a corrupt file. The
                # runner's own resume logic still protects the run keys.
                recorded = None
            if recorded and recorded != model:
                print(
                    f"ERROR: --resume --exp-id {args.exp_id!r} was recorded with "
                    f"model {recorded!r},\n"
                    f"       but --route {args.route} would run {model!r}.\n"
                    f"       Resuming across models re-runs every finished run "
                    f"(the resume key embeds the model)\n"
                    f"       and mixes two models into one experiment. Use "
                    f"--route {next((k for k, v in ROUTES.items() if v['model'] == recorded), '<matching route>')!s}"
                    f" or start a new experiment.",
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

    # --only is a CAP on this session's runs. Without --resume there is no
    # experiment to cap (and no skip predicate), so the combination is refused.
    if args.only is not None and not args.resume:
        print("ERROR: --only needs --resume --exp-id. It caps this session's runs "
              "against an experiment.", file=sys.stderr)
        return 2
    if args.allow_config_change and not args.resume:
        print("ERROR: --allow-config-change only has meaning with --resume "
              "--exp-id: there is no recorded identity to relax.", file=sys.stderr)
        return 2

    if args.only is not None and args.only <= 0:
        print("ERROR: --only must be a positive number of runs.", file=sys.stderr)
        return 2
    if args.only is not None and args.limit is not None:
        print("ERROR: --only and --limit both bound the batch differently "
              "(runs this session vs first N issues). Pick one.", file=sys.stderr)
        return 2

    # Validate --issues BEFORE the banner, so a --dry-run that goes green really
    # means the command can run. Offline it degrades to a warning: a preview must
    # not require the network.
    if args.issues:
        error = validate_instance_ids(args.issues)
        if error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2

    if args.only is not None:
        # The candidate pool is the experiment's own ids plus the dataset tail;
        # --issues, when given, replaces the tail.
        candidates = args.issues or select_issues(None)
        issues = select_batch_for_only(
            args.only, args.exp_id, strategies, candidates, model=model
        )
        if not issues:
            print("=" * 78)
            print("  FINAL SWEEP")
            print("=" * 78)
            print(f"  Nothing to do: the --only {args.only} cap selected no issue "
                  f"for {args.exp_id}.")
            print("=" * 78)
            return 0
    elif args.issues:
        issues = args.issues
    elif args.smoke:
        issues = select_issues(1)
    else:
        issues = select_issues(args.limit)
        if args.limit is not None and args.limit > len(issues):
            print(f"WARNING: --limit {args.limit} exceeds the {len(issues)} usable "
                  f"issues; running all of them.", file=sys.stderr)

    # ``runs`` means THIS session's runs. With --only it is a ceiling: --resume
    # decides how many of the selected runs are actually still outstanding.
    runs = len(issues) * len(strategies)

    print("=" * 78)
    print("  FINAL SWEEP")
    print("=" * 78)
    print(f"  route      : {args.route}")
    print(f"  model      : {model}")
    print(f"  provider   : {route['provider']} (env {route['env_key']})")
    print(f"  guard card : {route['guard_model']}")
    print(f"  issues     : {len(issues)}")
    if len(issues) <= 8:
        for i in issues:
            print(f"               {i}")
    else:
        print(f"               {issues[0]} ... {issues[-1]}")
    print(f"  strategies : {', '.join(strategies)}")
    print(f"  runs       : {runs}  (this session)")
    if args.only is not None:
        print(f"  only       : cap {args.only} run(s); --resume skips completed runs")
    print(f"  budget     : per_task, total {TOTAL_TURNS} turns per strategy")
    print(f"               direct {TOTAL_TURNS} | planning {TOTAL_TURNS} | "
          f"review {TOTAL_TURNS - REVISION_TURNS}+{REVISION_TURNS} = {TOTAL_TURNS}")
    print(f"  revision   : up to {MAX_REVISION_ROUNDS} rounds, "
          f"{REVISION_TURNS} turns reserved "
          f"({REVISION_TURNS // (2 * MAX_REVISION_ROUNDS)} per act)")
    print(f"  reference  : SWE-bench Pro 200 / mini-SWE-agent 250 / OpenHands 500")
    print(f"               (per task; ours was 40 = 5-12x tighter than all of them)")
    print(f"  cost cap   : ${COST_LIMIT_USD:.2f} per task (backstop, reference value)")
    _act_timeout_label = (
        f"{ACT_TIMEOUT_SECONDS}s per act (bounds retry backoff)"
        if ACT_TIMEOUT_SECONDS > 0
        else "unbounded (no wall-clock bound)"
    )
    print(f"  act timeout: {_act_timeout_label}")
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

    cmd = build_cmd(issues, strategies, args.dry_run, args.resume, args.exp_id,
                    route=args.route,
                    allow_config_change=args.allow_config_change)
    if args.dry_run:
        print("Command:")
        print("  " + " ".join(cmd[1:]))
        return 0

    started = datetime.now(timezone.utc)
    state = {
        "model": model,
        # The route is recorded next to the model: the bill window is read back
        # per model, and a reader needs to know WHICH provider/env pair produced
        # this window (the two routes share one 9router endpoint).
        "route": args.route,
        "provider": route["provider"],
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
        # Redundant but unambiguous: 0 is easy to misread in a manifest.
        "act_timeout": "unbounded" if ACT_TIMEOUT_SECONDS <= 0 else f"{ACT_TIMEOUT_SECONDS}s",
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

    # K-1R: a RECONSTRUCTED state file is EVIDENCE and must be preserved BEFORE
    # either branch below can rewrite the file.
    #
    # The --resume branch was the hole. It folds the previous window into
    # `attempts` and then overwrites the file, so on that path the marker and the
    # provenance fields (_SOURCES, _FIELD_CONFIDENCE) were dropped and NO marked
    # copy was written -- the archival call only ran on the fresh-sweep branch.
    # Measured 2026-10-07 with a real main() call: `--resume` turned
    # _RECONSTRUCTED=True into None, replaced the 11:41:13Z..13:11:56Z window with
    # the new one, and created zero archives. This is not a corner case: --resume
    # is the normal way to continue EXP-20261007-003, so it is the path the
    # operator would have taken.
    #
    # Ordering matters: this runs BEFORE the resume branch, and archiving does not
    # modify the original file, so the fold-in below still sees the same bytes.
    # The hash in the archive name makes it idempotent -- resuming twice rewrites
    # the same file with identical content rather than accumulating copies.
    if state_path.exists() and _is_reconstructed(state_path):
        archive_previous_window(state_path)

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
        # previous one; keep it under a NON-COLLIDING dated backup. The read is
        # byte-for-byte archival, so it must tolerate the same corrupt shapes as
        # above (invalid UTF-8 raises UnicodeDecodeError, which is NOT an OSError).
        #
        # Extracted into archive_previous_window() for two measured reasons:
        #   * the old second-resolution name let two main() calls in one second
        #     overwrite each other -- how the EXP-20261007-003 window was lost;
        #   * a RECONSTRUCTED state file must not be filed as a live window.
        # See that function for both.
        archive_previous_window(state_path)

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
    # `model`, NOT the module-level MODEL: MODEL is the DEFAULT route's model, so
    # printing it here told an --route opencode operator to reconcile the bill
    # against oc/muse-spark-1.3-contributor-free -- a model this run never called.
    # Measured on EXP-20261010-062: the hint at the end of an opencode sweep named
    # the meta model. The window was right, the model was wrong, and a wrong model
    # in read_actual_bill.py silently compares against the wrong rate card.
    print(f"      --model {model} --compare results/<EXP-id>")
    # Normalise: the child's rc already records the real outcome (a rate-limit
    # early stop makes main.py return 1). Returning the raw rc let a non-zero
    # value be RECORDED above while the process still exited 0, because only
    # run_with_env turns a return into an exit code. Collapse it to 0/1 so this
    # wrapper's own exit code matches the outcome it printed.
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
