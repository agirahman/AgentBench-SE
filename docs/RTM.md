# Requirement Traceability Matrix (RTM)
## AgentBench-SE

**Peneliti:** Agi Rahman Setiadi
**Institusi:** Universitas Negeri Jakarta — Sistem dan Teknologi Informasi

---

## FR → Design → Implementation → Test

| Req ID | Requirement | Use Case | Design Component | Implementation | Test / Validation |
|--------|-------------|----------|-----------------|----------------|-------------------|
| FR-01 | Load Dataset | UC-02 | Dataset Loader | `src/dataset_loader.py:15` | Manual: `len(issues) == 50` |
| FR-02 | Configure Experiment | UC-01 | Config + CLI | `src/main.py:18` | Check `experiment.yaml` output |
| FR-03 | Health Check Provider | UC-11 | Provider Interface | `src/providers/openrouter_provider.py:67` | Manual: `provider.health_check()` |
| FR-04 | Execute Direct (S1) | UC-03 | DirectStrategy | `src/strategies/direct_strategy.py` | Manual: patch non-empty |
| FR-05 | Execute Planning (S2) | UC-04 | PlanningStrategy | `src/strategies/planning_strategy.py` | Manual: plan + patch exist |
| FR-06 | Execute Review (S3) | UC-05 | ReviewStrategy | `src/strategies/review_strategy.py` | Manual: plan + feedback + patch exist |
| FR-07 | Retry on Failure | UC-13 | @with_retry decorator | `src/evaluation/retry.py` | `tests/test_retry.py` |
| FR-08 | Save Artifacts | UC-06 | Runner._save_artifacts | `src/experiments/runner.py:31` | Manual: artifact files per issue |
| FR-09 | Export Results | UC-07 | CSV + statistics export | `src/experiments/runner.py:234` | Manual: CSV file exists |
| FR-10 | Compute Cost | UC-08 | CostCalculator | `src/evaluation/cost.py:77` | `tests/test_cost.py` |
| FR-11 | Resume Experiment | UC-09 | Runner resume logic | `src/experiments/runner.py:110` | Manual: skip done issues |
| FR-12 | Extract Diff | UC-04b | swebench_adapter | `src/experiments/swebench_adapter.py` | `tests/selfcheck_adapter.py` |
| FR-13 | Auto-fix Hunk Headers | UC-04b | swebench_adapter | `src/experiments/swebench_adapter.py` | Manual: fix line count mismatch |
| FR-14 | Generate Experiment ID | UC-14 | experiment_id | `src/experiment_id.py` | `tests/test_experiment_id.py` |
| FR-15 | Generate Report | UC-15 | report_generator | `src/evaluation/report_generator.py` | Manual: report files exist |
| FR-16 | View Results | UC-10 | view_results CLI | `src/view_results.py` | Manual: stdout tables |

---

## FR → Test Case Detail

| Req ID | Test Case | Input | Expected | Actual | Status |
|--------|-----------|-------|----------|--------|--------|
| FR-01 | Load default 50 issues | `select_issues()` | 50 Issue objects | ✅ | ✅ |
| FR-01 | Load with custom limit | `select_issues(n=2)` | 2 Issue objects | ✅ | ✅ |
| FR-03 | Health check passes | API key valid | True | ✅ | ✅ |
| FR-03 | Health check fails | API key invalid | False | ✅ | ✅ |
| FR-04 | Direct strategy run | 1 issue | Patch non-empty | ✅ | ✅ |
| FR-05 | Planning strategy run | 1 issue | Plan + Patch exist | ✅ | ✅ |
| FR-06 | Review strategy run | 1 issue | Plan + Feedback + Patch | ✅ | ✅ |
| FR-07 | Retry on network error | Mock failure 2×, success 3rd | Success after 2 retries | ✅ | `test_retry.py` |
| FR-07 | Max retries exhausted | Mock failure 3× | Exception raised | ✅ | `test_retry.py` |
| FR-10 | Cost calculation | InferenceResult with tokens | Correct USD/IDR | ✅ | `test_cost.py` |
| FR-12 | Extract diff from JSON | `{"patch": "diff..."}` | Clean diff string | ✅ | `selfcheck_adapter.py` |
| FR-12 | Extract diff from markdown | `````diff\n...````` | Clean diff string | ✅ | `selfcheck_adapter.py` |
| FR-12 | Empty response | `""` | `""` | ✅ | `selfcheck_adapter.py` |
| FR-14 | Experiment ID format | Generate 3 IDs | `EXP-YYYYMMDD-NNN` | ✅ | `test_experiment_id.py` |
| FR-14 | ID increment per day | Same day, 2 calls | NNN+1 | ✅ | `test_experiment_id.py` |

---

## NFR → Design → Implementation → Verification

| Req ID | Requirement | Design Approach | Implementation | Verification |
|--------|-------------|-----------------|----------------|--------------|
| NFR-01 | Performance | Rate limiting, no blocking I/O | `random.uniform(5,10)` delay | Timing log per run |
| NFR-02 | Reliability | Savepoint + retry | `_append_jsonl()` per issue, `@with_retry` | Check no data loss on crash |
| NFR-03 | Reproducibility | experiment.yaml | `_save_experiment_config()` | Re-run with same config |
| NFR-04 | Maintainability | Modular package structure | providers/, strategies/, experiments/, evaluation/ | Code review: single responsibility |
| NFR-05 | Portability | Pure Python 3.10+ | `requirements.txt`, no native deps | Cross-platform test |

---

## FR → NFR Mapping

| FR | NFR-01 Performance | NFR-02 Reliability | NFR-03 Reproducibility | NFR-04 Maintainability | NFR-05 Portability |
|----|:---:|:---:|:---:|:---:|:---:|
| FR-01 Load Dataset | | ✅ Savepoint | | | ✅ HuggingFace |
| FR-02 Configure | | | ✅ experiment.yaml | | |
| FR-03 Health Check | | ✅ Abort on fail | | | |
| FR-04 Execute S1 | ✅ < 30s | | | | |
| FR-05 Execute S2 | ✅ < 60s | | | | |
| FR-06 Execute S3 | ✅ < 90s | | | | |
| FR-07 Retry | | ✅ Backoff | | | |
| FR-08 Save Artifacts | | ✅ Per issue | | ✅ Modular | |
| FR-09 Export Results | | | ✅ Reproducible | | |
| FR-10 Compute Cost | | | | ✅ Separated class | |
| FR-11 Resume | | ✅ Savepoint | | | |
| FR-12 Extract Diff | | | | ✅ Utils | |
| FR-13 Auto-fix Hunk | | | | ✅ Utils | |
| FR-14 Experiment ID | | | ✅ Unique ID | | |
| FR-15 Generate Report | | | | ✅ Separated | |
| FR-16 View Results | | | | ✅ CLI tool | |

---

## Change History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-07-19 | Initial RTM for AgentBench-SE |
