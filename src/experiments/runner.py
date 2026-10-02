import json
import os
import random
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd

from models.issue import Issue
from models.result import (
    ExperimentResult,
    ExecutionResult,
    CostSummary,
    EvaluationResult,
)
from models.patch import Patch
from models.inference import InferenceRun, InferenceResult
from experiments.csv_exporter import flatten_for_csv
from experiments.swebench_adapter import (
    collect_test_files,
    extract_diff,
    strip_test_files,
    validate_applicability,
)
from agents.tools import ensure_repo_root
from evaluation.statistics import export_statistics_json, generate_summary_md
from evaluation.cost import PricingTable
from evaluation.retry import is_provider_error, is_rate_limit_error
from config import Config
from experiment_id import generate_experiment_id, create_experiment_dir
from experiments.observability import build_experiment_manifest, write_issue_run_summary
from utils.logger import logger


class StrategyProtocol(Protocol):
    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]: ...


def _save_artifacts(
    output_dir: str,
    instance_id: str,
    strategy_name: str,
    inferences,
    final_patch: str,
    messages=None,
) -> None:
    """Simpan artifact per-agent ke ``<exp_dir>/artifacts/<instance_id>/<strategy_name>/``.

    Dipisah per strategi agar ``messages.jsonl``, ``patch.txt``, dan ``<role>.md``
    setiap strategi tidak saling menimpa.

    Berkas yang ditulis:

    * ``trajectory.jsonl`` -- rekaman LENGKAP per turn: setiap turn asisten (teks +
      reasoning + tool yang diminta) dan setiap hasil tool, berurutan. Ini trajectory
      utama; ``messages.jsonl`` menyimpan percakapan antar-agen.
    * ``trajectory.md`` -- versi manusiawi dari berkas di atas, untuk dibaca langsung.
    * ``<role>.md`` -- respons FINAL agen tersebut. Nama berkas per-role berarti act
      berikutnya MENIMPA act sebelumnya (review punya dua act executor), jadi berkas
      ini hanya bertahan untuk act terakhir; trajectory.jsonl menyimpan semuanya.
    """
    art_dir = Path(f"{output_dir}/artifacts/{instance_id}/{strategy_name}")
    art_dir.mkdir(parents=True, exist_ok=True)

    for inf in inferences:
        if not inf.role:
            continue
        (art_dir / f"{inf.role}.md").write_text(inf.response, encoding="utf-8")

    # The reasoning channel, per role. Two sources, and the trajectory comes first
    # because the final-response field alone is badly incomplete: measured on
    # EXP-20260930-249 (thinking ON), 12 of 19 assistant turns carried reasoning
    # while the FINAL turn carried none -- so writing only `inf.reasoning_content`
    # produced no file at all, and a reader (or a check script) would conclude
    # thinking was off while 12 turns of it sat unrecorded.
    #
    # Keeping the filename means existing tooling keeps working; the content is now
    # every turn's reasoning rather than only the last one's.
    for inf in inferences:
        if not inf.role:
            continue
        turns = [
            e for e in (getattr(inf, "trajectory", []) or [])
            if e.get("type") == "assistant" and (e.get("reasoning") or "").strip()
        ]
        if turns:
            blocks = [
                f"## turn {e.get('turn')}\n\n{(e.get('reasoning') or '').strip()}"
                for e in turns
            ]
            header = (
                f"# Reasoning — role `{inf.role}`\n\n"
                f"{len(turns)} turn(s) with a reasoning channel. "
                f"See trajectory.jsonl for the full record.\n\n"
            )
            (art_dir / f"{inf.role}_reasoning.md").write_text(
                header + "\n\n".join(blocks), encoding="utf-8"
            )
        elif (getattr(inf, "reasoning_content", "") or "").strip():
            # Non-tool path: no turns to iterate, so the single response is the record.
            (art_dir / f"{inf.role}_reasoning.md").write_text(
                inf.reasoning_content, encoding="utf-8"
            )

    (art_dir / "patch.txt").write_text(final_patch, encoding="utf-8")

    if messages:
        with (art_dir / "messages.jsonl").open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

    # Full trajectory: one line per event, in order, across every act of the run.
    # Written for ALL acts (not just the last of each role), which is the gap that
    # made a rejected-then-revised run unreadable: <role>.md kept only the final
    # act, so the reasoning behind the FIRST executor attempt -- the one the
    # reviewer rejected -- was not in any artifact.
    traj_lines: list[dict] = []
    for act_index, inf in enumerate(inferences):
        if not inf.role:
            continue
        for entry in getattr(inf, "trajectory", []) or []:
            enriched = dict(entry)
            enriched["act_index"] = act_index
            enriched["act_role"] = inf.role
            traj_lines.append(enriched)

    if traj_lines:
        with (art_dir / "trajectory.jsonl").open("w", encoding="utf-8") as f:
            for entry in traj_lines:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        (art_dir / "trajectory.md").write_text(
            _render_trajectory(traj_lines), encoding="utf-8"
        )

    # Per-agent tool-call log: which tool, how many times, by which agent.
    # Each inference carries tool_calls (empty for non-tool agents).
    tool_lines = []
    for inf in inferences:
        if not inf.role:
            continue
        for call in getattr(inf, "tool_calls", []) or []:
            tool_lines.append(
                {
                    "agent": inf.role,
                    "tool": call.get("name"),
                    "arguments": call.get("arguments"),
                    "result_preview": (call.get("result") or "")[:2000],
                }
            )
    if tool_lines:
        with (art_dir / "tool_calls.jsonl").open("w", encoding="utf-8") as f:
            for line in tool_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _render_trajectory(entries: list[dict]) -> str:
    """Render the trajectory as readable Markdown.

    The JSONL is the machine-readable record; this is the same content a person
    can read without a parser, which matters when the question is "what did the
    agent actually do" and the answer is needed during a supervision session
    rather than from a script.
    """
    out: list[str] = ["# Trajectory", ""]
    for e in entries:
        kind = e.get("type", "?")
        turn = e.get("turn", "?")
        role = e.get("act_role") or e.get("role") or "?"
        if kind == "assistant":
            out.append(f"## [{role}] turn {turn} — assistant")
            out.append("")
            reasoning = (e.get("reasoning") or "").strip()
            if reasoning:
                out.append("**Reasoning**")
                out.append("")
                out.append("```")
                out.append(reasoning)
                out.append("```")
                out.append("")
            content = (e.get("content") or "").strip()
            if content:
                out.append("**Content**")
                out.append("")
                out.append("```")
                out.append(content)
                out.append("```")
                out.append("")
            calls = e.get("tool_calls") or []
            if calls:
                out.append("**Tool calls**")
                out.append("")
                for c in calls:
                    out.append(f"- `{c.get('name')}` `{c.get('arguments')}`")
                out.append("")
            if e.get("is_final_answer_after_bound"):
                out.append(
                    "_This answer was requested AFTER the act hit its bound "
                    "(turns/cost/time), so the act did not finish on its own._"
                )
                out.append("")
        elif kind == "tool":
            out.append(f"### [{role}] turn {turn} — tool result: `{e.get('name')}`")
            out.append("")
            out.append(f"_{e.get('result_chars', 0)} chars_")
            out.append("")
            out.append("```")
            out.append(str(e.get("result", "")))
            out.append("```")
            out.append("")
        elif kind == "bound_reached":
            out.append(
                f"### [{role}] BOUND REACHED — {e.get('stop_reason')} "
                f"(granted {e.get('granted_turns')} turns)"
            )
            out.append("")
    return "\n".join(out)


def _resume_key(instance_id: str, model: str, thinking: bool) -> str:
    """Composite key so resume is safe across configs (model/thinking).

    Item 8c: a bare ``instance_id`` match would wrongly skip an issue when the
    SAME experiment folder is reused with a different model or thinking flag.
    """
    return f"{instance_id}|{model}|{thinking}"


#: Statuses that mean the run died for an INFRASTRUCTURE reason rather than
#: reaching an outcome. Such a row must always be retried by --resume -- the
#: number of retries is NOT bounded (only no-diff outcomes are bounded, see
#: ``_MAX_NO_PATCH_ATTEMPTS``).
#:
#: RATE_LIMIT and PROVIDER_ERROR were added when the error path stopped stamping
#: every exception "TIMEOUT" (runner.py:493-498), but this set was not updated --
#: so those two statuses fell outside it. The list is the explicit statement of
#: intent, so it has to name them.
#:
#: NOTE for the thesis: on this repo's measured history (6_125 rows) only TIMEOUT
#: ever appears (382 rows); RATE_LIMIT / PROVIDER_ERROR / ERROR / FAILED appear
#: zero times, because the error path derives them from the exception type. They
#: are kept as a CONTRACT, not as a reproduction of an observed incident.
_PATCH_STATUS_FAILED = {
    "TIMEOUT",
    "ERROR",
    "FAILED",
    "RATE_LIMIT",
    "PROVIDER_ERROR",
    # Written when the operator interrupts the run (Ctrl-C). ``KeyboardInterrupt``
    # is a BaseException and was previously NOT caught at all, so the instance
    # vanished with no row -- see the ``except KeyboardInterrupt`` branch below.
    "INTERRUPTED",
}

#: How many times a "no diff" outcome is retried before it is accepted as final.
#:
#: One, deliberately. A run can finish normally and produce NO diff -- the model
#: answered with prose. That is an OUTCOME, not an interruption, and retrying it
#: on every resume is a silent cost leak: measured on this repo's own history,
#: 299 (strategy x instance) combinations sit in that state and every resume
#: re-paid for them. But zero retries is also wrong -- a provider that returns an
#: empty completion once may not the next time. One attempt buys the safety net
#: and bounds the leak.
#:
#: This is the number of no-patch ROWS allowed before the key is treated as done.
#: Note what this does NOT bound: infrastructure deaths (see
#: ``_PATCH_STATUS_FAILED``) retry for as long as they keep failing.
_MAX_NO_PATCH_ATTEMPTS = 2


def _is_infrastructure_failure(entry: dict) -> bool:
    """Did this row die for an infrastructure reason (so it must always retry)?

    Two independent signals, because neither alone is sufficient:

    * ``error_type`` -- written ONLY by the error path (runner.py:987, and a
      repo-wide grep finds no other writer). This is the load-bearing signal:
      the retry policy for infrastructure deaths depends on it entirely, so if
      that path ever stops writing it, failures silently become "outcomes".
    * a member of ``_PATCH_STATUS_FAILED`` -- covers the case where a status is
      recorded but ``error_type`` is missing.

    Deliberately NOT the same as "the patch is empty": a no-diff outcome also has
    an empty patch, and treating the two alike is what this whole module's resume
    logic had to be split to avoid.
    """
    if entry.get("error_type"):
        return True
    status = str(entry.get("patch_status") or "").upper()
    return status in _PATCH_STATUS_FAILED


def _is_completed_entry(entry: dict) -> bool:
    """Did this row represent a run that actually RAN to completion?

    A run counts as covered unless it failed for an INFRASTRUCTURE reason. A model
    answering with prose instead of a diff is a valid OUTCOME, not an absent run --
    the runner records it as ``NO_DIFF``/``EMPTY`` with no ``error_type``.

    On real data (EXP-20260824-005, 150 runs) a patch-based predicate flagged 84
    rows as unfinished, of which 44 were legitimate no-diff outcomes and only 40
    were infrastructure deaths. Reporting a false alarm on 44 of 150 runs would
    have discredited the very check meant to catch real losses -- and a control
    that cries wolf is worse than none, because the real alarm is then ignored.

    This answers "did the sweep cover this instance?", which is a DIFFERENT
    question from "should --resume retry it?" -- see ``_load_existing_ids``.
    """
    return not _is_infrastructure_failure(entry)


def _no_patch_attempts(entries: list[dict]) -> int:
    """How many no-patch OUTCOME attempts do these rows represent?

    A "no-patch outcome" row is one that:
      * has NO ``error_type`` and no infrastructure status (else it is a death,
        which is never bounded), AND
      * carries no patch.

    The status-less row counts. It is the SINGLE MOST COMMON case in real data
    (2_318 of 6_125 rows), not an edge case -- verbatim example:

        {"instance_id": "psf__requests-1963", "model_patch": "",
         "model_name_or_path": "deepseek-v4-flash", "strategy": "direct"}

    A row WITH a patch is not counted: once a patch exists the key is finished and
    the loader skips it regardless.
    """
    n = 0
    for entry in entries:
        if _is_infrastructure_failure(entry):
            continue
        if (entry.get("model_patch") or "").strip():
            continue
        n += 1
    return n


def _load_existing_ids(jsonl_path: str) -> set[str]:
    """Baca file jsonl yang sudah ada, return set composite resume keys.

    Each key is ``instance_id|model|thinking`` (see ``_resume_key``).

    A key is returned (= "--resume may skip it") when EITHER:

    1. some row produced a patch -- the run plainly finished; or
    2. the key has already used up its no-diff retries
       (``_MAX_NO_PATCH_ATTEMPTS`` rows with no patch and no ``error_type``).

    Case 2 is option "D" of docs/PLAN_RESUME_FIX_20261002.md. Before it, ANY empty
    patch was retried forever. Measured on this repo: 299 (strategy x instance)
    combinations are no-diff outcomes, so every resume re-ran all of them. One
    retry keeps the recovery chance and bounds that leak.

    A key whose rows are infrastructure deaths is NEVER returned -- it stays
    retryable no matter how many times it has died, which is the entire point of
    --resume. Measured on EXP-20260929-022 django-11019/review, a 502.

    The count is deliberately per FILE (one strategy), matching how the runner
    looks it up: it calls this once per strategy with that strategy's jsonl.
    """
    ids: set[str] = set()
    if not os.path.exists(jsonl_path):
        return ids

    # Group first: the retry budget is per key, so the whole history for a key
    # must be seen before deciding. A previous version decided row by row, which
    # cannot express "two no-patch rows" at all.
    grouped: dict[str, list[dict]] = {}
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = entry.get("instance_id")
            if iid is None:
                continue
            model = entry.get("model_name_or_path", "")
            thinking = entry.get("thinking", False)
            grouped.setdefault(_resume_key(iid, model, thinking), []).append(entry)

    for key, entries in grouped.items():
        if any((e.get("model_patch") or "").strip() for e in entries):
            ids.add(key)                     # finished (1)
            continue
        if any(_is_infrastructure_failure(e) for e in entries):
            continue                         # always retryable
        attempts = _no_patch_attempts(entries)
        if attempts >= _MAX_NO_PATCH_ATTEMPTS:
            ids.add(key)                     # option D: budget used up (2)
            if attempts > _MAX_NO_PATCH_ATTEMPTS:
                logger.warning(
                    f"Resume: {key} has {attempts} no-patch attempts, more than the "
                    f"expected {_MAX_NO_PATCH_ATTEMPTS} -- treating as final. An "
                    "unexpected history is worth a look, not silence."
                )
    return ids


def _append_jsonl(jsonl_path: str, entry: dict) -> None:
    """Append 1 baris ke jsonl (savepoint)."""
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl_entries(jsonl_path: str) -> list[dict]:
    """Read every parseable row from a savepoint, in order."""
    entries: list[dict] = []
    if not os.path.exists(jsonl_path):
        return entries
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _rows_from_savepoints(pred_dir: Path, strategies) -> list[dict]:
    """Reconstruct CSV rows from the jsonl savepoints.

    Used when the CSV cannot be read: missing (a crash before the first export --
    the MAIN case --resume exists for), truncated (a crash DURING to_csv, which is
    the last and most interruptible step), or otherwise unparseable.

    The savepoints are the source of truth: they are appended per run, so a run
    that finished is recorded even if the process died before any export.

    What this CANNOT recover: token counts, cost, and timing are not part of the
    prediction contract the savepoints store. Those columns come back empty and
    ``_recovered_from_savepoint`` is set, so a reader can tell "not recorded" from
    "zero". Reporting a fabricated 0.00 cost for a run that really spent money
    would be worse than reporting nothing -- it is the same class of error as the
    cache-discount bug that nearly halved RQ3's numbers.
    """
    rows: list[dict] = []
    for strat_name in strategies:
        jsonl_path = str(pred_dir / f"{strat_name}.jsonl")
        for entry in _read_jsonl_entries(jsonl_path):
            iid = entry.get("instance_id")
            if not iid:
                continue
            status = str(entry.get("patch_status") or "")
            rows.append({
                "instance_id": iid,
                "strategy": strat_name,
                "model": entry.get("model_name_or_path", ""),
                "patch_status": status,
                "error": entry.get("error") or "",
                "generated": bool((entry.get("model_patch") or "").strip()),
                "patch_preview": "",
                # None, not "": these columns are numeric everywhere else, and a
                # mixed str/float column makes pandas raise on any arithmetic
                # downstream (statistics, cost aggregation). None reads back as
                # NaN, which the _num/_text helpers map to "not recorded" --
                # distinguishable from a real 0, which matters because a
                # fabricated 0.00 cost for a paid run is a wrong number, not a
                # missing one.
                "total_tokens": None,
                "input_tokens_total": None,
                "input_tokens_cached": None,
                "input_tokens_regular": None,
                "output_tokens": None,
                "cost_usd_offpeak": None,
                "cost_idr_offpeak": None,
                "cost_usd_peak_total": None,
                "cost_idr_peak_total": None,
                "cost_usd_actual": None,
                "cost_idr_actual": None,
                "input_cost_usd_total": None,
                "output_cost_usd": None,
                "input_cost_usd_cached": None,
                "input_cost_usd_regular": None,
                "execution_time": None,
                "pricing_version": "",
                "_recovered_from_savepoint": True,
            })
    return rows


def _merge_csv_rows(csv_path: str, new_rows: list[dict],
                    pred_dir: Path | None = None,
                    strategies=None) -> list[dict]:
    """Merge this session's rows with the rows already on disk.

    Why this exists: the CSV was written from ``all_results``, which only ever
    holds runs started in THIS process, and ``to_csv`` overwrites. So a ``--resume``
    after a crash rewrote ``generation_result.csv`` with only the post-interruption
    runs and silently dropped everything before it -- while the jsonl savepoints
    stayed complete. Measured independently by two auditors: a 3-issue first pass
    followed by a 5-issue resume left the CSV with 2 rows out of 5.

    That is the failure a long sweep cannot tolerate. 50 issues x 3 strategies is
    ~6 hours and a mid-run interruption is likely, so the recovery path must
    preserve the paid-for work instead of erasing it.

    Three sources, in order of preference:

    1. The existing CSV -- it carries every column, including tokens and cost.
    2. The jsonl savepoints -- when the CSV is missing or unreadable. Recovers
       WHICH runs happened and their status, but not tokens/cost, because the
       savepoints store only the prediction contract.
    3. This session's rows.

    The csv is read with escalating tolerance. ``pd.read_csv`` is all-or-nothing:
    ONE malformed row raises ParserError and every old row is discarded -- which
    would restore the original bug on the MOST realistic input, a file half-written
    when the process died mid-``to_csv``. So a failed strict parse falls back to
    ``on_bad_lines="skip"``, and then to the savepoints.

    Rows are keyed by ``(instance_id, strategy)`` and the NEWEST wins, so a retry
    after a failure replaces the failed row rather than appearing twice. That
    deduplication is also what keeps the evaluation wrapper from counting one
    instance twice (a duplicate row made ``resolved/total`` exceed 100%).
    """
    merged: dict[tuple[str, str], dict] = {}
    loaded_from_csv = False

    if os.path.exists(csv_path):
        for attempt, kwargs in enumerate(({}, {"on_bad_lines": "skip"})):
            try:
                old = pd.read_csv(csv_path, **kwargs)
                # The header is written with a leading "[" (a pandas artifact of
                # the original writer), so strip it before matching column names.
                old.columns = [str(c).lstrip("[") for c in old.columns]
                for record in old.to_dict(orient="records"):
                    key = (str(record.get("instance_id")),
                           str(record.get("strategy")))
                    merged[key] = record
                loaded_from_csv = True
                if attempt == 1:
                    logger.warning(
                        f"{csv_path} needed on_bad_lines='skip' to parse: some "
                        f"rows were malformed and dropped. Recovered "
                        f"{len(merged)} row(s) from it; the savepoints are the "
                        f"authority if the count looks short."
                    )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Could not read {csv_path} for merging "
                    f"(attempt {attempt + 1}, {type(exc).__name__}: {exc})"
                )

    if not loaded_from_csv and pred_dir is not None and strategies:
        recovered = _rows_from_savepoints(pred_dir, strategies)
        if recovered:
            logger.warning(
                f"No readable CSV at {csv_path}; reconstructed {len(recovered)} "
                f"row(s) from the jsonl savepoints. Token and cost columns are "
                f"empty for those rows -- they were never recorded there."
            )
        for record in recovered:
            key = (str(record.get("instance_id")), str(record.get("strategy")))
            merged.setdefault(key, record)

    # The savepoints are the authority on WHICH runs happened; the CSV is only
    # richer per row. A CSV that parsed but is SHORT -- truncated mid-write, or
    # with rows dropped by on_bad_lines="skip" -- would otherwise pass as complete
    # while quietly missing runs that the savepoints prove were done. So any
    # instance the savepoints record and the CSV does not is added from them.
    if loaded_from_csv and pred_dir is not None and strategies:
        for record in _rows_from_savepoints(pred_dir, strategies):
            key = (str(record.get("instance_id")), str(record.get("strategy")))
            if key not in merged:
                merged[key] = record
                logger.warning(
                    f"  {key[0]}/{key[1]} is in the savepoints but was missing "
                    f"from {Path(csv_path).name}; restored (without token/cost, "
                    f"which the savepoint does not record)."
                )

    for record in new_rows:
        key = (str(record.get("instance_id")), str(record.get("strategy")))
        merged[key] = record

    return list(merged.values())


def _write_csv_atomically(df, csv_path: str) -> None:
    """Write the CSV via a temp file and a rename, keeping a .bak of the old one.

    ``df.to_csv`` writes in place, so a process that dies mid-write leaves a
    TRUNCATED file -- and that is the most likely moment for an interruption,
    because it is the last thing a long run does. A truncated CSV then hits the
    merge path, which is exactly the case the savepoint fallback exists for; the
    atomic write removes the cause instead of relying on the recovery.

    The ``.bak`` copy means a bad merge can never destroy the only record: the
    previous good file is still on disk.
    """
    target = Path(csv_path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    if target.exists():
        try:
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        except OSError as exc:
            logger.warning(f"Could not write the CSV backup: {exc}")
    os.replace(tmp, target)


def _export_interrupted_run(exp_dir, all_results, pred_dir, strategies, issues,
                            strategy_names, provider_name, agents) -> None:
    """Flush every artefact when the operator interrupts the run.

    The exports below live at the END of ``run_experiments``, so a Ctrl-C would
    otherwise discard the accounting for every run that DID finish -- the results
    survive only in the jsonl savepoints, which do not record tokens, cost or
    timing (see ``_rows_from_savepoints``).

    This is not just about the CSV. Measured with a real Ctrl-C mid-sweep, only
    the CSV appeared; manifest.json, generation_statistics.json and
    generation_report.md were all missing. That is the same failure class as the
    experiment.yaml bug (config written after the run finished, so a crash lost
    it), and it degrades a downstream tool: verify_patches.py reads manifest.json
    for instance -> base_commit mapping.

    Best effort by design: an interrupt must not be turned into a crash by
    bookkeeping, so every step is guarded and failures are logged, not raised. The
    savepoints remain the authority either way.
    """
    try:
        csv_path = f"{exp_dir}/generation_result.csv"
        new_rows = [flatten_for_csv(r) for r in all_results]
        merged_rows = _merge_csv_rows(csv_path, new_rows, pred_dir, list(strategies))
        df = pd.DataFrame(merged_rows)
        _write_csv_atomically(df, csv_path)
        logger.info(
            f"  Exported {len(merged_rows)} row(s) for the interrupted run: {csv_path}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not export the CSV after the interrupt: {exc}")
        return

    manifest_results = _results_from_flat_rows(merged_rows)
    model_name = df["model"].iloc[0] if len(df) else ""
    pricing = PricingTable.get(model_name) if model_name else None

    try:
        export_statistics_json(df, f"{exp_dir}/generation_statistics.json",
                               pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
        generate_summary_md(df, f"{exp_dir}/generation_report.md",
                            pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not export the statistics/report after the interrupt: {exc}")

    try:
        manifest = build_experiment_manifest(
            issues=issues,
            strategies=list(strategy_names),
            provider_name=provider_name,
            experiment_id=Path(exp_dir).name,
            output_dir=str(exp_dir),
            results=manifest_results,
            agents=agents,
        )
        Path(f"{exp_dir}/manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not export the manifest after the interrupt: {exc}")

    logger.info(
        "  Interrupted run exported: CSV, statistics, report, manifest. "
        "--resume will retry the instances that did not finish."
    )


def _results_from_flat_rows(rows: list[dict]) -> list[ExperimentResult]:
    """Rebuild manifest inputs from flattened CSV rows.

    The manifest is built from ``ExperimentResult`` objects, but after a merge the
    rows for earlier runs exist only as CSV records. Token and timing properties on
    ``ExperimentResult`` are computed from ``run.inferences``, so the recorded
    totals are carried by one synthetic ``InferenceResult`` -- that keeps
    ``total_tokens``, ``execution_time`` and the cost columns faithful to what was
    originally measured instead of collapsing them to zero.

    Only fields the manifest actually reads are reconstructed; nothing is invented
    for values the CSV does not carry.
    """
    out: list[ExperimentResult] = []

    def _num(value, default=0.0) -> float:
        try:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return default
            if isinstance(value, str) and not value.strip():
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _truthy(value) -> bool:
        """Read a boolean-ish CSV cell, where an empty cell arrives as NaN.

        ``bool(float("nan"))`` is True, so a blank ``generated`` cell was read as
        a SUCCESSFUL generation -- the exact opposite of what an empty cell means.
        A blank cell is written by the savepoint recovery path and by any row that
        lacks the column, so on a resumed experiment every recovered row would have
        claimed to have produced a patch.
        """
        if value is None:
            return False
        if isinstance(value, float) and pd.isna(value):
            return False
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _text(value) -> str:
        """Read a text cell, mapping NaN to "" instead of the string 'nan'.

        ``str(float('nan'))`` is ``'nan'``, a non-empty string. That defeats the
        ``patch.strip()`` guard used to decide whether a patch exists, so a row
        with no patch preview would have passed an emptiness test as if it had
        content.
        """
        if value is None:
            return ""
        if isinstance(value, float) and pd.isna(value):
            return ""
        text = str(value)
        return "" if text.lower() in ("nan", "none") else text

    for row in rows:
        patch_preview = _text(row.get("patch_preview"))
        synth = InferenceResult(
            role="rebuilt",
            response="",
            usage={
                "prompt_tokens": int(_num(row.get("input_tokens_total"))),
                "completion_tokens": int(_num(row.get("output_tokens"))),
                "total_tokens": int(_num(row.get("total_tokens"))),
                "cached_tokens": int(_num(row.get("input_tokens_cached"))),
            },
            execution_time=_num(row.get("execution_time")),
            api_turns=int(_num(row.get("total_turns"), 1)) or 1,
        )
        run = InferenceRun(patch=patch_preview, inferences=[synth])
        cost = CostSummary(
            input_cost_usd=_num(row.get("input_cost_usd_total")),
            output_cost_usd=_num(row.get("output_cost_usd")),
            total_cost_usd=_num(row.get("cost_usd_offpeak")),
            total_cost_idr=_num(row.get("cost_idr_offpeak")),
            pricing_version=_text(row.get("pricing_version")),
            cached_input_tokens=int(_num(row.get("input_tokens_cached"))),
            regular_input_tokens=int(_num(row.get("input_tokens_regular"))),
            cached_input_cost_usd=_num(row.get("input_cost_usd_cached")),
            regular_input_cost_usd=_num(row.get("input_cost_usd_regular")),
            peak_total_cost_usd=_num(row.get("cost_usd_peak_total")),
            peak_total_cost_idr=_num(row.get("cost_idr_peak_total")),
            actual_cost_usd=_num(row.get("cost_usd_actual")),
            actual_cost_idr=_num(row.get("cost_idr_actual")),
        )
        result = ExperimentResult(
            instance_id=_text(row.get("instance_id")),
            strategy=_text(row.get("strategy")),
            model=_text(row.get("model")),
            execution=ExecutionResult(run=run),
            cost=cost,
            evaluation=EvaluationResult(
                # _truthy, not bool(): a blank cell arrives as NaN, and
                # bool(NaN) is True -- so an empty cell read as a success.
                success=_truthy(row.get("generated")),
                error=_text(row.get("error")),
                timestamp=_text(row.get("timestamp")),
            ),
            difficulty=_text(row.get("difficulty")),
            patch_status=_text(row.get("patch_status")),
            apply_status=_text(row.get("apply_status")),
        )
        out.append(result)
    return out


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, StrategyProtocol],
    base_dir: str = "results",
    provider_name: str = "unknown",
    rate_limit_seconds: float = 1.5,
    resume: bool = False,
    agents: list[dict[str, str]] | None = None,
    model: str = "",
    on_experiment_start: Callable[[str, str], None] | None = None,
    experiment_id: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Execute experiments and dump results to a per-experiment folder.

    Args:
        issues: List of Issue objects to process
        strategies: Dict of strategy_name -> strategy_object
        base_dir: Root results directory (default: results/)
        provider_name: Name of provider (gemini/groq/deepseek)
        rate_limit_seconds: Delay between strategies (for API rate limiting)
        resume: If True, skip issues already completed in existing jsonl files
        model: Actual configured model id (e.g. cmd/deepseek/deepseek-v4-flash).
            Used for resume keys and error rows so records never carry a
            provider name in place of a model id.
        on_experiment_start: Called with (exp_dir, exp_id) as soon as the
            directory exists, before the first run. The caller uses it to write
            the configuration record up front. Without it the config was only
            written AFTER every run finished, so a crash or a kill left a
            directory full of patches with no record of the settings that
            produced them -- and a multi-hour sweep is exactly where that
            matters. Failures here are logged, never fatal: losing a metadata
            write must not abort an experiment that is already running.
        experiment_id: Continue an EXISTING experiment instead of creating a new
            one. This is what makes ``resume`` functional: the directory is
            chosen first and the resume scan then reads the savepoints inside
            it. Previously every invocation minted a fresh id, so the scan ran
            against an empty directory and could never skip anything -- the flag
            was accepted and inert. Selecting the directory is separate from
            ``resume``: passing an id without ``resume`` deliberately re-runs
            everything into that directory (e.g. re-measuring after a code
            change), which would otherwise be impossible.

    Returns:
        (DataFrame, experiment_id) where DataFrame is the flattened results.csv
    """
    effective_model = model or provider_name
    exp_id = experiment_id or generate_experiment_id()
    exp_dir = create_experiment_dir(base_dir, exp_id)
    if on_experiment_start is not None:
        try:
            on_experiment_start(exp_dir, exp_id)
        except Exception as exc:  # noqa: BLE001 - metadata must not kill a run
            logger.warning(
                f"Could not write the early config record for {exp_id}: "
                f"{type(exc).__name__}: {exc}"
            )
    Path(f"{exp_dir}/patches").mkdir(parents=True, exist_ok=True)
    pred_dir = Path(f"{exp_dir}/predictions")
    pred_dir.mkdir(parents=True, exist_ok=True)

    # Per-experiment log sink
    exp_log_file = f"{exp_dir}/logs/experiment.log"
    _sink_id = logger.add(exp_log_file, level="INFO", rotation="10 MB")
    logger.info(f"Per-experiment log: {exp_log_file}")

    # --- Resume: collect done ids per strategy ---
    done_ids: dict[str, set[str]] = {}
    if resume:
        for strat_name in strategies:
            jsonl_path = str(pred_dir / f"{strat_name}.jsonl")
            done_ids[strat_name] = _load_existing_ids(jsonl_path)
            if done_ids[strat_name]:
                logger.info(f"Resume: {len(done_ids[strat_name])} already done in {strat_name}")
        logger.info(f"Resume mode enabled — skipping completed issues")
    else:
        for strat_name in strategies:
            done_ids[strat_name] = set()

    all_results: list[ExperimentResult] = []
    all_predictions: list[dict] = []

    total = len(issues) * len(strategies)
    done = 0
    skipped = 0
    # Consecutive rate-limit failures. When the provider's usage window is
    # exhausted, every further call fails the same way — continuing only wastes
    # the remaining quota and produces a run full of 429 rows. Trip a breaker
    # and stop cleanly so --resume can finish later.
    consecutive_rate_limits = 0
    rate_limit_stopped = False

    for issue in issues:
        for name, strategy in strategies.items():
            done += 1

            # Resume skip — composite key (instance_id|model|thinking) so a
            # different config in the same experiment folder is not wrongly skipped.
            expected_key = _resume_key(
                issue.instance_id, effective_model, Config.DEEPSEEK_THINKING
            )
            if expected_key in done_ids[name]:
                skipped += 1
                logger.info(f"[{done}/{total}] SKIP (resume) {name} on {issue.instance_id}")
                continue

            logger.info(
                f"[{done}/{total}] Running {name} on {issue.instance_id} ({issue.difficulty})..."
            )
            logger.info(f"  → Issue loaded: {len(issue.problem_statement)} chars")
            t0 = time.time()
            try:
                logger.info(f"  → Strategy initialized: {name}")
                logger.info(f"  → API call to {provider_name}...")
                patch, result = strategy.run(issue)
                result.difficulty = issue.difficulty
                result.thinking = Config.DEEPSEEK_THINKING
                result.max_tokens = Config.MAX_TOKENS
                elapsed = time.time() - t0
                result.evaluation.timestamp = datetime.now(timezone.utc).isoformat()

                # --- Truncated JSON protection ---
                last_finish = ""
                try:
                    last_finish = (
                        result.execution.inferences[-1].finish_reason
                        if result.execution.inferences else ""
                    )
                    patch_result = extract_diff(patch.response, finish_reason=last_finish)
                    diff = patch_result.patch
                    patch_status = patch_result.status
                except Exception:
                    diff = ""
                    patch_status = "PARSE_ERROR"

                result.patch_status = patch_status
                # A successful call means the provider is healthy again; the
                # breaker only counts *consecutive* failures.
                consecutive_rate_limits = 0
                # Semantic check: patch_status only proves the diff arithmetic is
                # well formed. apply_status records whether the patch can really
                # be applied to the target repo, so a "valid" patch that merely
                # guessed its lines is not mistaken for a working one.
                if Config.APPLY_CHECK_ENABLED and diff.strip():
                    try:
                        result.apply_status = validate_applicability(
                            diff, ensure_repo_root(issue.repo, issue.base_commit)
                        )
                    except Exception as exc:  # noqa: BLE001 - never fail a run
                        logger.warning(
                            f"  ⚠ apply check failed for {issue.instance_id} "
                            f"({name}): {type(exc).__name__}: {exc}"
                        )
                        result.apply_status = "UNKNOWN"
                all_results.append(result)

                if last_finish == "length":
                    logger.warning(
                        f"  ⚠ Response truncated (finish_reason='length') for {issue.instance_id} ({name}) "
                        f"— patch_status={patch_status}, patch_len={len(diff)}, "
                        f"response_len={len(patch.response)}"
                    )

                # --- Savepoint append: per-strategy .jsonl ---
                # Last-resort fallback (Item 8a): if extract_diff failed but the
                # raw response still contains a unified diff, ship the raw text
                # so a valid patch is not silently dropped as "no report".
                model_patch = diff if diff.strip() else ""
                if not model_patch and "diff --git" in patch.response:
                    logger.warning(
                        f"  ⚠ extract_diff failed but raw response has a diff for "
                        f"{issue.instance_id} ({name}) — using raw response as patch"
                    )
                    model_patch = patch.response

                # Keep test files out of the submitted patch. The harness resets
                # test files and applies its own gold test patch; a model patch
                # that creates the same test file makes `git checkout <base_commit>
                # <path>` fail ("did not match any file(s) known to git"), and the
                # gold patch then fails with "already exists in working directory".
                # Evaluation continues regardless (the eval script has no `set -e`),
                # so the wrong tests could run. The harness supplies its own tests.
                if model_patch.strip() and getattr(issue, "test_patch", ""):
                    gold_test_files = collect_test_files(issue.test_patch)
                    if gold_test_files:
                        model_patch, stripped = strip_test_files(model_patch, gold_test_files)
                        if stripped:
                            logger.info(
                                f"  ✂ Removed {len(stripped)} test file(s) from patch "
                                f"for {issue.instance_id} ({name}): {', '.join(stripped)}"
                            )
                            if not model_patch.strip():
                                logger.warning(
                                    f"  ⚠ Patch for {issue.instance_id} ({name}) became "
                                    "empty after removing test files — the model only "
                                    "edited tests, so it will not resolve."
                                )

                # Re-derive status from the patch we ACTUALLY submit. Both checks
                # above ran on the raw captured diff, before test-file stripping,
                # so their verdicts could describe a patch that is never sent.
                # Observed on EXP-20260927-008 (django-10914 review): the raw diff
                # was HUNK_MISMATCH/NORMALIZE because the agent's edit to a gold
                # test file left that hunk one line short, while the submitted
                # patch (that hunk removed) was clean and applied with rc=0.
                # Reporting the raw verdict would have libelled a good patch.
                if model_patch.strip() and model_patch != diff:
                    patch_status = extract_diff(model_patch).status
                    result.patch_status = patch_status
                    if Config.APPLY_CHECK_ENABLED:
                        try:
                            result.apply_status = validate_applicability(
                                model_patch, ensure_repo_root(issue.repo, issue.base_commit)
                            )
                        except Exception as exc:  # noqa: BLE001 - never fail a run
                            logger.warning(
                                f"  ⚠ apply check failed for {issue.instance_id} "
                                f"({name}): {type(exc).__name__}: {exc}"
                            )
                            result.apply_status = "UNKNOWN"

                pred_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": model_patch,
                    "model_name_or_path": result.model,
                    "strategy": name,
                    "patch_status": patch_status,
                    "thinking": result.thinking,
                    "max_tokens": result.max_tokens,
                }
                strategy_jsonl = str(pred_dir / f"{name}.jsonl")
                _append_jsonl(strategy_jsonl, pred_entry)
                all_predictions.append(pred_entry)

                # --- Savepoint append: aggregated predictions.jsonl ---
                agg_jsonl = str(pred_dir / "predictions.jsonl")
                _append_jsonl(agg_jsonl, pred_entry)

                if not model_patch.strip():
                    logger.warning(
                        f"  ⚠ Patch empty/invalid for {issue.instance_id} ({name}) — "
                        f"recorded as empty ({patch_status})"
                    )
                else:
                    # Persist exactly what is sent to the Modal evaluator,
                    # not the raw (possibly JSON-wrapped) response.
                    Path(f"{exp_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                        model_patch, encoding="utf-8"
                    )
                    logger.info(f"  → Patch saved: {issue.instance_id}_{name}.txt")

                # Always persist artifacts (messages, per-role responses,
                # tool_calls) — even when the patch is empty/invalid — so the
                # tool-call trail stays available for debugging and analysis.
                _save_artifacts(
                    str(exp_dir),
                    issue.instance_id,
                    name,
                    result.execution.inferences,
                    patch.response,
                    result.execution.run.messages,
                )

                logger.success(
                    f"  ✅ {elapsed:.1f}s | {result.execution.total_tokens} tokens "
                    f"({result.execution.prompt_tokens} in + {result.execution.completion_tokens} out) | "
                    f"{result.execution.inference_count} inferences | "
                    f"${result.cost.total_cost_usd:.6f} | model={result.model}"
                )

                write_issue_run_summary(
                    output_dir=str(exp_dir),
                    issue=issue,
                    strategy_name=name,
                    patch_status=patch_status,
                    elapsed_seconds=elapsed,
                    total_tokens=result.execution.total_tokens,
                    success=bool(diff.strip()),
                    error="",
                )

                if rate_limit_seconds > 0:
                    # Honour the caller's --rate-limit instead of a fixed 5-10s:
                    # the parameter was previously accepted and then ignored.
                    jitter = random.uniform(0.0, min(2.0, rate_limit_seconds * 0.5))
                    delay = rate_limit_seconds + jitter
                    logger.info(f"Rate limit delay: {delay:.1f}s")
                    time.sleep(delay)

            except Exception as e:
                error_detail = str(e)
                elapsed_err = time.time() - t0
                logger.error(
                    f"  ❌ FAILED: {issue.instance_id} ({name}, {issue.difficulty}) — {type(e).__name__}: {error_detail[:400]}"
                )

                # Rate-limit breaker: a 429 on one instance means the rest will
                # fail too. Count consecutive hits and stop the run cleanly.
                if is_rate_limit_error(e):
                    consecutive_rate_limits += 1
                    limit = Config.RATE_LIMIT_CONSECUTIVE_LIMIT
                    logger.warning(
                        f"  ⏳ Rate limit hit ({consecutive_rate_limits}"
                        f"{f'/{limit}' if limit > 0 else ''} consecutive)"
                    )
                    if limit > 0 and consecutive_rate_limits >= limit:
                        rate_limit_stopped = True
                        logger.error(
                            f"Stopping run after {consecutive_rate_limits} consecutive "
                            f"rate-limit failures — the provider usage window is "
                            f"exhausted. Re-run with --resume once it resets."
                        )
                else:
                    consecutive_rate_limits = 0

                # Label the failure by CAUSE, not by a single catch-all. Every
                # unhandled exception used to be stamped "TIMEOUT", so a provider
                # 502, an HTTP 429 and a git error were indistinguishable in the
                # results and all read as time-outs downstream. Measured:
                # EXP-20260929-022 django-11019/review recorded a 502
                # (ENOTFOUND opencode.ai) as TIMEOUT, and EXP-20260824-005
                # recorded 11 consecutive 429s plus a git failure the same way.
                # The distinction matters because only some of these are worth
                # retrying, and a run that died at the provider must not be read
                # as a strategy that ran out of time.
                if is_rate_limit_error(e):
                    failure_status = "RATE_LIMIT"
                elif is_provider_error(e):
                    failure_status = "PROVIDER_ERROR"
                else:
                    failure_status = "ERROR"

                error_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": "",
                    "model_name_or_path": effective_model,
                    "strategy": name,
                    "patch_status": failure_status,
                    # Keep resume keys symmetric with success rows; otherwise
                    # --resume under thinking mode re-runs every errored instance.
                    "thinking": Config.DEEPSEEK_THINKING,
                    "error_type": type(e).__name__,
                    "error_message": error_detail,
                }
                strategy_jsonl = str(pred_dir / f"{name}.jsonl")
                _append_jsonl(strategy_jsonl, error_entry)
                all_predictions.append(error_entry)

                agg_jsonl = str(pred_dir / "predictions.jsonl")
                _append_jsonl(agg_jsonl, error_entry)

                empty_run = InferenceRun(patch="", inferences=[])
                empty_exec = ExecutionResult(run=empty_run)
                empty_cost = CostSummary(0.0, 0.0, 0.0, 0.0, "")
                empty_eval = EvaluationResult(success=False, error=str(e))
                all_results.append(ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy=name,
                    model=effective_model,
                    difficulty=issue.difficulty,
                    execution=empty_exec,
                    cost=empty_cost,
                    evaluation=empty_eval,
                    patch_status=failure_status,
                ))

                # Persist an issue-level summary even on failure so manifest
                # artifact references never dangle.
                write_issue_run_summary(
                    output_dir=str(exp_dir),
                    issue=issue,
                    strategy_name=name,
                    patch_status=failure_status,
                    elapsed_seconds=elapsed_err,
                    total_tokens=0,
                    success=False,
                    error=f"{type(e).__name__}: {error_detail[:400]}",
                )

            except KeyboardInterrupt:
                # Ctrl-C. ``KeyboardInterrupt`` derives from BaseException, so the
                # ``except Exception`` above does NOT catch it -- the instance used
                # to vanish with no row at all, and the operator's interruption was
                # indistinguishable from an instance that was never planned.
                #
                # This matters most for the real use: a 150-run sweep is ~6-14
                # hours, so Ctrl-C is the likely way it ends. Record the row so the
                # instance stays retryable, then re-raise so the interrupt still
                # stops the run instead of being swallowed.
                #
                # ``error_type`` is set deliberately: it is what makes the resume
                # predicate classify this as an infrastructure death, i.e. always
                # retried, never counted as an outcome.
                logger.warning(
                    f"  ⏹ INTERRUPTED: {issue.instance_id} ({name}) — operator "
                    "pressed Ctrl-C. Recording the row so --resume will retry it."
                )
                interrupted_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": "",
                    "model_name_or_path": effective_model,
                    "strategy": name,
                    "patch_status": "INTERRUPTED",
                    "thinking": Config.DEEPSEEK_THINKING,
                    "error_type": "KeyboardInterrupt",
                    "error_message": "operator interrupted the run",
                }
                _append_jsonl(str(pred_dir / f"{name}.jsonl"), interrupted_entry)
                _append_jsonl(str(pred_dir / "predictions.jsonl"), interrupted_entry)
                all_predictions.append(interrupted_entry)

                # Export what we have before letting the interrupt propagate: the
                # CSV, statistics, report and manifest are written at the end of
                # this function, so without this the interruption would discard the
                # accounting for every run that DID finish, leaving only the
                # savepoints.
                _export_interrupted_run(
                    exp_dir, all_results, pred_dir, list(strategies), issues,
                    list(strategies), provider_name, agents,
                )
                raise

            if rate_limit_stopped:
                break
        if rate_limit_stopped:
            break

    # --- Final exports ---
    # "generation_" prefix disambiguates phase-1 outputs from the eval-phase
    # files that report_generator writes under eval/ (results.csv, statistics.json).
    #
    # MERGE with whatever is already on disk before writing. all_results holds only
    # the runs started in this process, so overwriting here dropped every earlier
    # row when --resume was used -- see _merge_csv_rows. A fresh run has no old CSV
    # and merges to exactly its own rows, so this is a no-op in the normal case.
    csv_path = f"{exp_dir}/generation_result.csv"
    new_rows = [flatten_for_csv(r) for r in all_results]
    merged_rows = _merge_csv_rows(csv_path, new_rows, pred_dir, list(strategies))
    df = pd.DataFrame(merged_rows)
    _write_csv_atomically(df, csv_path)
    logger.success(f"CSV exported: {csv_path} ({len(df)} rows)")

    if len(df) > len(new_rows):
        logger.info(
            f"  (merged {len(df) - len(new_rows)} row(s) recorded by an earlier "
            f"session in this experiment directory)"
        )

    # The manifest and the pre-eval report are built from ExperimentResult objects.
    # After a merge, earlier runs exist only as CSV rows, so rebuild them from the
    # merged frame: otherwise the manifest would claim fewer issues processed than
    # the CSV contains -- the "shipping receipt" disagreeing with the shipment.
    manifest_results = _results_from_flat_rows(merged_rows)

    stats_path = f"{exp_dir}/generation_statistics.json"
    model_name = df["model"].iloc[0] if len(df) else provider_name
    pricing = PricingTable.get(model_name) if model_name else None
    export_statistics_json(df, stats_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Statistics exported: {stats_path}")

    # NOTE: this is the PRE-EVAL generation report (patch generation only),
    # NOT the resolved rate. The authoritative resolved rate is eval/summary.md
    # produced by report_generator from Modal results. See PLAN.md Item 5.
    summary_path = f"{exp_dir}/generation_report.md"
    generate_summary_md(df, summary_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Generation report (PRE-EVAL) exported: {summary_path}")

    manifest = build_experiment_manifest(
        issues=issues,
        strategies=list(strategies.keys()),
        provider_name=provider_name,
        experiment_id=exp_id,
        output_dir=str(exp_dir),
        results=manifest_results,
        agents=agents,
    )
    manifest_path = f"{exp_dir}/manifest.json"
    Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.success(f"Experiment manifest exported: {manifest_path}")

    # Log per-strategy counts
    for strat_name in strategies:
        strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
        count = sum(1 for _ in open(strat_jsonl, "r", encoding="utf-8") if _.strip())
        logger.success(f"Per-strategy predictions: {strat_jsonl} ({count} entries)")

    # --- Completeness check ---
    # Nothing used to compare the number of finished runs against the number
    # planned, so a sweep that silently lost runs still reported success. At 50
    # issues x 3 strategies a missing instance is easy to miss by eye and
    # expensive to discover after the analysis is written.
    #
    # Counts a row as covered unless it died for an infrastructure reason
    # (_is_completed_entry): a run that finished and produced no diff is an
    # outcome, not a gap. Using the resume predicate here flagged 44 of 150
    # legitimate no-diff runs as missing on real data, which would have turned
    # this control into noise.
    #
    # ---- THE TWO NUMBERS MUST SHARE A BASE ----
    # ``expected`` used to be ``len(issues) * len(strategies)`` for THIS session
    # only, while ``completed`` counts every entry in the savepoints -- i.e. all
    # sessions. Resuming a batch smaller than the folder already holds therefore
    # compared a small expected against a large completed, which measured as
    # "Completeness: 3/1 runs completed -- 0 run(s) not covered" and still wrote
    # INCOMPLETE.json. A control that fires on every resume is one nobody reads,
    # which is worse than having none (MEMORY, trap #17).
    #
    # So the denominator is the UNION: this session's batch plus every instance
    # already recorded in the savepoints.
    expected_keys: set[str] = set()
    for strat_name in strategies:
        for issue in issues:
            expected_keys.add(f"{strat_name}:{issue.instance_id}")
    for strat_name in strategies:
        for entry in _read_jsonl_entries(str(pred_dir / f"{strat_name}.jsonl")):
            if entry.get("instance_id"):
                expected_keys.add(f"{strat_name}:{entry.get('instance_id')}")
    expected_total = len(expected_keys)

    covered_total = 0
    missing: list[str] = []
    failed: list[str] = []
    for strat_name in strategies:
        strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
        entries = _read_jsonl_entries(strat_jsonl)
        covered_here = {
            entry.get("instance_id") for entry in entries
            if _is_completed_entry(entry)
        }
        failed_here = {
            entry.get("instance_id") for entry in entries
            if not _is_completed_entry(entry)
        }
        covered_total += len(covered_here)
        for key in sorted(expected_keys):
            k_strat, _, k_iid = key.partition(":")
            if k_strat != strat_name:
                continue
            if k_iid not in covered_here:
                # Distinguish "ran and died" from "never ran": they need different
                # actions (retry vs investigate), and merging them hides which.
                if k_iid in failed_here:
                    failed.append(key)
                else:
                    missing.append(key)

    if covered_total > expected_total:
        # Cannot happen once both sides share a base. If it ever does, the bases
        # have drifted apart again -- say so rather than silently comparing them.
        logger.error(
            f"Completeness arithmetic is incoherent: completed={covered_total} "
            f"exceeds expected={expected_total}. The two figures are being counted "
            "on different bases; treat the coverage result as unknown."
        )

    incomplete_marker = Path(f"{exp_dir}/INCOMPLETE.json")
    if covered_total == expected_total:
        logger.success(
            f"Completeness: {covered_total}/{expected_total} runs completed."
        )
        # Clear a stale marker. It was written only on the failure branch and never
        # removed, so a successful resume left a permanent alarm claiming the
        # experiment was short -- and a stale alarm is indistinguishable from a
        # live one.
        if incomplete_marker.exists():
            try:
                incomplete_marker.unlink()
                logger.info("Cleared the INCOMPLETE.json left by an earlier session.")
            except OSError as exc:
                logger.warning(f"Could not remove the stale INCOMPLETE.json: {exc}")
    else:
        logger.error(
            f"Completeness: {covered_total}/{expected_total} runs completed "
            f"-- {len(missing) + len(failed)} run(s) not covered."
        )
        for entry in failed[:20]:
            logger.error(f"    ran but FAILED: {entry}")
        for entry in missing[:20]:
            logger.error(f"    never ran: {entry}")
        if len(missing) + len(failed) > 20:
            logger.error(f"    ... and {len(missing) + len(failed) - 20} more")
        logger.error(
            "  Re-run with --resume to finish the incomplete runs before analysing."
        )
        # Persist the shortfall so a downstream reader cannot mistake this for a
        # complete sweep just because the process exited 0.
        #
        # ``skipped_already_done`` is named explicitly because a reader must be
        # able to tell "deliberately skipped as already done" from "never ran" --
        # they need opposite actions, and the old payload only reported absences.
        completeness = {
            "expected": expected_total,
            "completed": covered_total,
            "completed_this_session": len(all_predictions),
            "skipped_already_done": skipped,
            "failed": failed,
            "never_ran": missing,
            "missing": failed + missing,  # kept for readers of the old key
            "complete": False,
        }
        try:
            incomplete_marker.write_text(
                json.dumps(completeness, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"Could not write INCOMPLETE.json: {exc}")

    if skipped:
        logger.info(f"Resume: skipped {skipped} already-completed entries")

    if rate_limit_stopped:
        logger.warning("")
        logger.warning("!" * 60)
        logger.warning(
            "  RUN STOPPED EARLY — provider rate limit / usage window exhausted."
        )
        logger.warning(
            f"  Processed {done - 1}/{total} planned runs before stopping."
        )
        logger.warning(
            "  Data collected so far is valid and already flushed to disk."
        )
        logger.warning(
            "  Re-run the same command with --resume once the limit resets."
        )
        logger.warning("!" * 60)

    # --- Failure summary ---
    valid = sum(1 for p in all_predictions if p.get("patch_status") == "VALID")
    total_preds = len(all_predictions)
    failed = total_preds - valid
    from collections import Counter
    status_counts = Counter(p.get("patch_status", "UNKNOWN") for p in all_predictions)

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  Run Summary: {exp_id}")
    logger.info(f"  Total runs : {total_preds}")
    logger.info(f"  Valid      : {valid} ({valid/total_preds*100:.1f}%)" if total_preds else "  Valid: 0")
    logger.info(f"  Failed     : {failed} ({failed/total_preds*100:.1f}%)" if total_preds else "  Failed: 0")
    if failed:
        logger.info("  Breakdown:")
        for status_name in ("MALFORMED_HEADER", "PLACEHOLDER_ONLY", "OFFSET_TOO_LARGE",
                            "BAD_BODY", "HUNK_MISMATCH", "TRUNCATED", "PARSE_ERROR",
                            "NO_DIFF", "EMPTY", "TIMEOUT"):
            cnt = status_counts.get(status_name, 0)
            if cnt:
                logger.info(f"    {status_name}: {cnt}")
    norm = status_counts.get("NORMALIZE", 0)
    if norm:
        logger.info(f"  Normalized: {norm} (hunk header corrected, still valid)")
    logger.info("=" * 60)

    logger.remove(_sink_id)
    return df, exp_id
