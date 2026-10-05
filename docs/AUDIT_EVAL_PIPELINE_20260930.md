# AUDIT REPORT — AgentBench-SE Evaluation/Reporting Pipeline

**Date:** 2026-09-30
**Scope:** `tools/eval_modal.py`, `src/evaluation/report_generator.py`, `src/evaluation/statistics.py`, `src/view_results.py`, `tools/analyze_accuracy_vs_budget.py`, `tools/check_sweep_state.py`, `tools/analyze_run_anatomy.py`, `tools/check_eval.py`
**Change audited:** Empty patch classification (EMPTY_PATCH failure_reason, graded/empty_patches/harness_errors fields)

---

## TOP 5 FINDINGS (Most Severe First)

### 1. `analyze_accuracy_vs_budget.py` INCLUDES EMPTY PATCHES IN ACCURACY MATH

**File:** `tools/analyze_accuracy_vs_budget.py:90-91`
**Severity:** MAJOR

**What is wrong:**
The tool joins `generation_result.csv` with `*_results.json` and computes accuracy by turns-used bucket. An empty patch (django__django-11019/review in EXP-20260929-022) with `total_turns=0` and `resolved=False` is counted as a "converged" run and included in the accuracy denominator.

**Evidence:**
```
$ python tools/analyze_accuracy_vs_budget.py EXP-20260929-022

9 graded runs from 1 experiment(s)
converged (stopped on their own): 9
...
Turns USED by converged runs:
  all values: [0, 8, 27, 28, 31, 54, 60, 68, 89]

Accuracy by turns used:
      bucket    n  resolved    rate
        0-15    2         1    50%   <-- includes the empty patch
```

The `resolved_map()` function (line 49-69) returns `_resolved=False` for the empty patch, and `graded` filter (line 90) includes it because `_resolved is not None`. The tool then treats it as a legitimate failed attempt.

**Why it matters:**
An empty patch is NOT a wrong answer — it's a non-attempt. Including it artificially lowers the accuracy of the 0-15 turn bucket and skews the "safe budget" interpretation. The thesis conclusion about budget adequacy could be affected.

**Should empty patches be excluded?**
Yes. The tool's purpose is to find the budget at which accuracy saturates. A run that never started tells us nothing about budget needs — only about provider reliability.

---

### 2. `report_generator.merge_data()` FLOWS EMPTY_PATCH CORRECTLY — NO DEFECT

**File:** `src/evaluation/report_generator.py:87-111`
**Severity:** NONE (confirmed working)

**What it does:**
`merge_data()` reads per-instance results from `*_results.json` and maps `resolved`, `patch_applied`, `failure_reason` into the merged DataFrame. EMPTY_PATCH instances appear with:
- `resolved=False`
- `patch_applied=False`
- `failure_reason="EMPTY_PATCH"`

**Evidence (test run on EXP-20260929-022):**
```
django__django-11019: resolved=False, patch_applied=False, failure_reason=EMPTY_PATCH
```

**Downstream effect:**
`_build_failure_breakdown()` (line 114-131) categorizes this as a distinct failure reason column. The breakdown correctly shows `EMPTY_PATCH` as its own bucket, NOT averaged into strategy failure rate.

**Conclusion:** The change is schema-compatible with `report_generator`. EMPTY_PATCH instances are reported separately as intended.

---

### 3. `statistics.py` WARNING IS STILL ACCURATE — NO DEFECT

**File:** `src/evaluation/statistics.py:388-390`
**Severity:** NONE (confirmed accurate)

**The warning:**
```python
"> **WARNING — PRE-EVALUATION REPORT.** This report measures "
"**patch generation** only (success = a non-empty patch was "
"produced by the pipeline). It is **NOT** the resolved rate from "
"the Modal evaluation. The authoritative `resolved` rate lives in "
"`eval/summary.md` (produced by `report_generator.merge_data()` "
"from Modal `*_results.json`). Do not cite the success rate below "
"as the SWE-bench pass@k result."
```

**Why it's still correct:**
This warning appears in `generate_summary_md()` which operates on generation-phase CSVs (before Modal eval). The new EMPTY_PATCH classification happens in the eval phase (`*_results.json`), not the generation phase. The warning correctly distinguishes generation-success from resolved-rate.

**No change needed.**

---

### 4. ZERO-DIVISION RISK WHEN ALL RUNS ARE EMPTY PATCHES

**File:** `tools/eval_modal.py:316-317`
**Severity:** MINOR

**What is wrong:**
```python
graded_count = total_count - empty_count - error_count
success_rate_graded = (resolved_count / graded_count * 100) if graded_count > 0 else 0
```

If every instance in a strategy is an empty patch or error, `graded_count = 0` and `success_rate_graded = 0`. This is technically correct (no graded runs → 0% graded success), but the print statement (line 332-333) shows:

```python
if graded_count != total_count:
    print(f"              {success_rate_graded:.1f}%  (resolved/graded, "
          f"n={graded_count}; excludes empty patches and errors)")
```

When `graded_count = 0`, this prints `0.0% (resolved/graded, n=0; ...)`. Not misleading, but edge-case reporting could be clearer.

**Why it's minor:**
The headline rate (`success_rate`) is always reported first and uses `total_count`. A strategy with 100% empty patches would show `0% resolved/submitted`, which is correct. The graded rate is a secondary diagnostic.

---

### 5. `check_eval.py` USES HARNESS SUMMARY FIELDS — NO DEFECT

**File:** `tools/check_eval.py:27-40`
**Severity:** NONE (confirmed compatible)

**What it does:**
Reads Modal harness summary files (`*gemini-v1-{strat}.json`) and prints `instances_resolved`, `instances_unresolved`, `instances_with_errors`, `total_instances`.

**Why it's unaffected:**
The new EMPTY_PATCH classification is in our wrapper (`*_results.json`), not in the raw harness summary. The harness already reports `empty_patch_ids` separately (line 159 in `eval_modal.py`), and our wrapper uses that to build the classification. `check_eval.py` reads a different file format.

---

## OTHER CONSUMERS CHECKED

| Consumer | Reads `*_results.json`? | Affected by EMPTY_PATCH? | Notes |
|----------|------------------------|--------------------------|-------|
| `view_results.py` | No (reads CSV) | No | Uses `resolved` column if present; generation CSV has no eval data |
| `check_sweep_state.py` | No (reads predictions.jsonl) | No | Reports empty patches from JSONL, not from results |
| `analyze_run_anatomy.py` | No (reads logs) | No | Reports empty patches from patch.txt file size |
| `statistics.py` | No (reads CSV) | No | Generation-phase only; eval-phase uses `report_generator` |

---

## RECOMMENDATIONS

### 1. Fix `analyze_accuracy_vs_budget.py` (MAJOR)

**Option A — Exclude empty patches from graded set:**
```python
# In resolved_map(), check for EMPTY_PATCH:
for rec in data.get("results", []):
    if rec.get("failure_reason") == "EMPTY_PATCH":
        continue  # skip non-attempts
    out[(strat, rec["instance_id"])] = bool(rec.get("resolved"))
```

**Option B — Add separate counter:**
Report empty patches separately from "graded" and "ungraded" in the tool's output, similar to how `eval_modal.py` does.

### 2. No other changes required

The schema change is backward-compatible with all other consumers. Tests pass (16/16). The `report_generator` correctly handles EMPTY_PATCH as a distinct failure category.

---

## VERIFICATION

- Tests run: `pytest tests/test_report_generator.py tests/test_eval_empty_patch.py` — **16 passed**
- Real data checked: `results/EXP-20260929-022/predictions/review_results.json` — EMPTY_PATCH present and correct
- Accuracy tool checked: includes empty patch in denominator — **defect confirmed**

---

## FILES TO FIX

| File | Change Required |
|------|-----------------|
| `tools/analyze_accuracy_vs_budget.py` | Exclude EMPTY_PATCH instances from graded set |
| (no other files) | — |
