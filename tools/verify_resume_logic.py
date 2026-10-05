"""Does --resume actually resume, or would a 14-hour sweep restart from zero?

A 150-run sweep WILL be interrupted at least once. If --resume is broken, the
recovery costs hours of re-running and real money. This tests the resume logic
against a SYNTHETIC jsonl, so no real results are touched.

The specific hazard: the resume key is ``instance_id|model|thinking``. If a run is
recorded under a different key than the one --resume looks up, every completed run
is re-run -- silently, and the only symptom is a bill twice the size expected.

Usage:
    python tools/verify_resume_logic.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import (  # noqa: E402
    _is_completed_entry,
    _no_patch_attempts,
    _load_existing_ids,
    _resume_key,
    _MAX_NO_PATCH_ATTEMPTS,
)

MODEL = "cbai/deepseek-v4.1-flash"


def main() -> None:
    print("=" * 78)
    print("  RESUME LOGIC -- verified against a synthetic jsonl")
    print("=" * 78)

    checks: list[tuple[str, bool, str]] = []

    # ---- 1. A successful run must be skipped on resume.
    #
    # The row models a REAL one: it must carry ``model_name_or_path`` because that is
    # the field the loader keys on (runner.py:322). Omitting it here produced keys like
    # 'django__django-10914||False' and a "failed" check against a loader that works.
    success = {
        "instance_id": "django__django-10914",
        "model_name_or_path": MODEL,
        "model_patch": "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b",
        "strategy": "direct",
        "patch_status": "VALID",
        "thinking": False,
    }
    key = _resume_key("django__django-10914", MODEL, False)
    checks.append((
        "a VALID run with a patch counts as completed for the coverage check",
        _is_completed_entry(success),
        f"_is_completed_entry -> {_is_completed_entry(success)}",
    ))

    # ---- 2. A FAILED run must NOT be skipped (that is the point of --resume).
    for status in ("TIMEOUT", "ERROR", "RATE_LIMIT", "PROVIDER_ERROR", "FAILED", "INTERRUPTED"):
        failed = dict(success, patch_status=status, model_patch="", error_type="SomeError")
        checks.append((
            f"a {status} run is NOT covered (it never produced an outcome)",
            not _is_completed_entry(failed),
            f"_is_completed_entry -> {_is_completed_entry(failed)}",
        ))

    # ---- 2b. Infrastructure deaths are NEVER bounded by the retry budget: they
    #          keep retrying however many times they have died.
    checks.append((
        "repeated infrastructure deaths are never treated as finished",
        _no_patch_attempts([
            dict(success, patch_status="RATE_LIMIT", model_patch="", error_type="RateLimitError"),
            dict(success, patch_status="RATE_LIMIT", model_patch="", error_type="RateLimitError"),
            dict(success, patch_status="RATE_LIMIT", model_patch="", error_type="RateLimitError"),
        ]) == 0,
        "3 rate-limited rows count as 0 no-patch attempts",
    ))

    # ---- 3. A run that finished with NO diff is an OUTCOME, not a missing run,
    #         and it gets exactly _MAX_NO_PATCH_ATTEMPTS tries before being final.
    #
    #         This section used to assert the OPPOSITE ("a NO_DIFF run IS retried
    #         by --resume"), which was option "E" -- retry forever. That is what
    #         docs/PLAN_RESUME_FIX_20261002.md replaced: measured on this repo,
    #         299 (strategy x instance) combinations sit in the no-diff state, so
    #         every resume re-paid for all of them.
    no_diff = dict(success, model_patch="", patch_status="NO_DIFF")
    checks.append((
        "a NO_DIFF run is an outcome (counts as covered)",
        _is_completed_entry(no_diff),
        f"_is_completed_entry -> {_is_completed_entry(no_diff)}",
    ))
    checks.append((
        "a NO_DIFF run counts as ONE no-patch attempt",
        _no_patch_attempts([no_diff]) == 1,
        f"_no_patch_attempts -> {_no_patch_attempts([no_diff])}",
    ))

    # A single no-diff row must still be retried: option D is "retry once", not
    # "never retry" (that would be option B, rejected because failures could then
    # be frozen by a single empty answer).
    one_row = tempfile.mkdtemp()
    try:
        p = Path(one_row) / "direct.jsonl"
        p.write_text(json.dumps(no_diff) + "\n", encoding="utf-8")
        ids_one = _load_existing_ids(str(p))
    finally:
        shutil.rmtree(one_row, ignore_errors=True)
    checks.append((
        "after ONE no-diff row the instance is still retried",
        _resume_key("django__django-10914", MODEL, False) not in ids_one,
        f"keys -> {sorted(ids_one)}",
    ))

    # Two no-diff rows exhaust the budget -> the instance becomes final.
    two_rows = tempfile.mkdtemp()
    try:
        p = Path(two_rows) / "direct.jsonl"
        p.write_text(
            json.dumps(no_diff) + "\n" + json.dumps(no_diff) + "\n", encoding="utf-8"
        )
        ids_two = _load_existing_ids(str(p))
    finally:
        shutil.rmtree(two_rows, ignore_errors=True)
    checks.append((
        f"after {_MAX_NO_PATCH_ATTEMPTS} no-diff rows the instance is final",
        _resume_key("django__django-10914", MODEL, False) in ids_two,
        f"keys -> {sorted(ids_two)}",
    ))

    # A row with no patch_status at all must behave like NO_DIFF. This is the MOST
    # COMMON shape in real data (2_318 of 6_125 rows), not an edge case.
    stateless = {k: v for k, v in success.items() if k != "patch_status"}
    stateless["model_patch"] = ""
    checks.append((
        "a row with NO patch_status counts as a no-patch attempt",
        _no_patch_attempts([stateless]) == 1,
        f"_no_patch_attempts -> {_no_patch_attempts([stateless])}",
    ))

    # ---- 4. The key must depend on model and thinking, so a config change
    #         does NOT silently skip runs made under a different config.
    k1 = _resume_key("inst", MODEL, False)
    k2 = _resume_key("inst", MODEL, True)
    k3 = _resume_key("inst", "other-model", False)
    checks.append((
        "the resume key changes with the thinking flag",
        k1 != k2,
        f"{k1!r} vs {k2!r}",
    ))
    checks.append((
        "the resume key changes with the model",
        k1 != k3,
        f"{k1!r} vs {k3!r}",
    ))

    # ---- 5. Round-trip through a real file: does the loader find the keys the
    #         runner writes?
    #
    #         NOTE: the synthetic rows here MUST carry ``model_name_or_path``, because
    #         that is the field the loader keys on (runner.py:322). A first version of
    #         this test omitted it and reported a resume failure that did not exist --
    #         the keys came out as 'django__django-10914||False' and the check "failed"
    #         against a loader that was working correctly. The real jsonl rows carry
    #         the field (verified by tools/verify_resume_keys_match.py), so the test
    #         has to model the real rows, not an easier shape.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "direct.jsonl"
        rows = [
            success,
            dict(success, instance_id="django__django-11001"),
            dict(success, instance_id="django__django-11019", patch_status="PROVIDER_ERROR",
                 model_patch="", error_type="InternalServerError"),
        ]
        path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        ids = _load_existing_ids(str(path))
        expected_key = _resume_key("django__django-10914", MODEL, False)
        checks.append((
            "the loader returns the key for a successful run",
            expected_key in ids,
            f"looked for {expected_key!r}; got {sorted(ids)}",
        ))
        checks.append((
            "the loader does NOT return the failed run's key",
            _resume_key("django__django-11019", MODEL, False) not in ids,
            f"keys: {sorted(ids)}",
        ))
        checks.append((
            "a row WITHOUT model_name_or_path produces a key --resume cannot match",
            # This is the trap the first version of this test fell into: it is a
            # property of the DATA, not of the loader. Pinning it documents why the
            # field must always be written.
            _resume_key("x", "", False) != _resume_key("x", MODEL, False),
            f"empty-model key: {_resume_key('x', '', False)!r}",
        ))

    print()
    bad = 0
    for name, ok, detail in checks:
        flag = "OK  " if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"  [{flag}] {name}")
        print(f"         {detail}")

    print()
    print("=" * 78)
    if bad:
        print(f"  {bad} check(s) FAILED -- --resume is not trustworthy as written")
    else:
        print("  All checks pass: --resume skips completed work, retries failures,")
        print("  and a config change invalidates the keys rather than silently")
        print("  reusing them.")
    print("=" * 78)

    print()
    print("  WHAT THIS DOES NOT COVER")
    print("  -----------------------")
    print("  The runner's own wiring -- that it CALLS the loader with the same path")
    print("  and the same model string it writes with. That is verified separately")
    print("  by inspecting the call site; a mismatch there would make every key miss")
    print("  and re-run the whole sweep.")

    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
