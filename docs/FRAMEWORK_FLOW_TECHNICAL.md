# Framework Flow Teknis — AgentBench-SE

**Tanggal**: 2026-07-31
**Audiens**: Developer, kontributor
**Cabang**: `19/feat/multi-agent-orchestration`

---

## 1. Tujuan

Dokumen ini menjelaskan alur kode end-to-end untuk developer yang ingin memahami, memelihara, atau memperluas framework AgentBench-SE.

## 2. Peta Entry Point

```
src/main.py::main()
  └─> providers/*.py                  # HTTP client ke LLM
      └─> agents/registry.py          # build_agent_team(provider)
          ├─> agents/direct_agent.py
          ├─> agents/planner_agent.py
          ├─> agents/executor_agent.py
          └─> agents/reviewer_agent.py
      └─> strategies/*.py             # komposisi agent
          └─> agents/base.py::act()   # pembungkus 1 LLM call
              └─> providers/*.py::generate()
      └─> experiments/runner.py::run_experiments()
          ├─> experiments/swebench_adapter.py
          ├─> experiments/observability.py
          ├─> experiments/csv_exporter.py
          └─> evaluation/cost.py
```

## 3. CLI Lifecycle

`src/main.py`:

1. **Parse args** — `parse_args()`.
2. **Pilih provider** — `Provider` dipilih dari `--provider`.
3. **Health check** — `provider.health_check()`.
4. **Load dataset** — `select_issues(repo_specs)` dari `dataset_loader.py`.
5. **Bangun agent_team** — `build_agent_team(provider)`.
6. **Bangun strategies** — map `{"direct": DirectStrategy(provider), "planning": ..., "review": ...}`.
7. **Buat experiment dir** — `create_experiment_dir(base, exp_id)` dari `experiment_id.py`.
8. **Panggil runner** — `run_experiments(issues, strategies, base_dir, provider_name, rate_limit, resume, agents)`.
9. **Tulis experiment.yaml** — `_save_experiment_config(...)`.

## 4. Provider Layer

Lokasi: `src/providers/`.

Tiap provider mengimplementasikan interface:
- `__init__()`: setup client + baca model name dari `Config`.
- `health_check() -> bool`: test ping.
- `generate(prompt: str, role: str = "") -> InferenceResult` (di-decorate `@with_retry()`).

Wajib sesuai interface — strategy & agent hanya memanggil `provider.generate(prompt, role=agent.name)`.

## 5. Agent Layer

### 5.1 Base

`src/agents/base.py`:
- `BaseAgent.__init__(provider)`: load prompt template via `load_prompt_or_default`.
- `BaseAgent.act(task, context)`: render → call provider → log ke blackboard → return `AgentResponse`.
- `BaseAgent._render(task, context) -> str`: abstract, diimplementasi per agent konkret.

### 5.2 Concrete Agents

| File | Class | Template | `_render` |
|---|---|---|---|
| `direct_agent.py` | `DirectAgent` | `direct_prompt.md` | `{{issue}}` |
| `planner_agent.py` | `PlannerAgent` | `planner.md` | `{{issue}}` |
| `executor_agent.py` | `ExecutorAgent` | `executor.md` | `{{issue}}` + `{{plan}}` + (revisi) `Reviewer Feedback` |
| `reviewer_agent.py` | `ReviewerAgent` | `reviewer.md` | `{{issue}}` + `{{plan}}` + `{{patch}}` |

### 5.3 Blackboard

`src/agents/blackboard.py`:
- fields: `issue`, `plan`, `patch`, `feedback`, `revision`, `history`.
- method: `log(message: AgentMessage)`.

### 5.4 AgentMessage

`src/agents/messages.py`:
- fields: `sender`, `receiver`, `kind`, `content`, `timestamp`.
- method: `to_dict()` (untuk serialisasi ke `messages.jsonl`).

### 5.5 Registry

`src/agents/registry.py`:
- `build_agent_team(provider) -> dict[str, BaseAgent]`.

## 6. Strategy Layer

`src/strategies/*.py` — supervisor. Tiap strategi:
1. `__init__(provider)`: `self.team = build_agent_team(provider)`.
2. `run(issue) -> (Patch, ExperimentResult)`:
   - Bangun `Blackboard`.
   - Urutkan agent activations via `task` → `agent.act(task, bb)`.
   - Mutasi `bb` sesuai kebutuhan (plan, patch, feedback).
   - Kumpulkan `inferences` list.
   - Bangun `InferenceRun(patch, inferences, messages=list(bb.history))`.
   - Agregasi cost via `CostCalculator`.
   - Bentuk `ExperimentResult`.

## 7. Runner Layer

`src/experiments/runner.py`:
- `run_experiments(issues, strategies, base_dir, provider_name, rate_limit, resume, agents)`:
  - Generate experiment ID.
  - Setup log sink.
  - Loop (issue × strategy).
  - Ekstrak diff via `swebench_adapter.extract_diff`.
  - Append jsonl savepoint.
  - Save artifact per-issue per-strategy.
  - Tulis summary.
  - Export CSV, statistics, summary.md, manifest.json, experiment.yaml.

## 8. Model Layer

`src/models/`:
- `issue.py`: `Issue` + `difficulty` property.
- `inference.py`: `InferenceResult` (per-call) + `InferenceRun` (kumpulan + messages).
- `result.py`: `ExecutionResult`, `CostSummary`, `EvaluationResult`, `ExperimentResult`.
- `patch.py`: `Patch`.

## 9. Observability Pipeline

Tiap strategi selesai → `_save_artifacts(...)`:
- Path: `artifacts/<instance>/<strategy>/`.
- File: `{role}.md` per inference, `patch.txt`, `messages.jsonl`.

Aggregation di akhir run:
- `results.csv` (semua hasil flat).
- `predictions.jsonl` (concat semua strategi).
- `statistics.json` (agregat).
- `summary.md` (markdown report).
- `manifest.json` (dataset + provider + agents).
- `experiment.yaml` (reproducibility).

## 10. Evaluasi SWE-bench

Downstream — di luar codebase ini:
- `tools/EVAL_INSTRUCTIONS.md`: panduan WSL2.
- `tools/setup_and_eval.sh`: setup venv + Docker + eval otomatis.
- `tools/run_eval.sh`: eval 3 strategi sekaligus.

## 11. Test Suite

| File | Cakupan |
|---|---|
| `tests/test_agents.py` | BaseAgent + 4 agent konkret: render + record |
| `tests/test_strategies.py` | S1 = 1 inference, S2 = 2, S3 = 3 (approved), S3 = 4 (revision) |
| `tests/test_review_strategy.py` | `_extract_verdict` + fallback prompt |
| `tests/test_runner_error_reporting.py` | `_save_artifacts` per-strategi + `load_existing_ids` + `append_jsonl` |
| `tests/test_observability.py` | `build_experiment_manifest` + `write_issue_run_summary` |
| `tests/test_cost.py` | Pricing + cost calculator |
| `tests/test_statistics.py` | Statistik agregat |
| Lainnya | Config, retry, dataset, swebench_adapter, dll. |

Total: 52 test.

## 12. Cara Menambah Strategi Baru

Mis. `S4_Debate` (multiple executors + judge).
1. Tambah `src/strategies/debate_strategy.py`.
2. `__init__(provider)`: `self.team = build_agent_team(provider)`.
3. `run(issue)`: bangun blackboard, jalankan agent sesuai urutan baru.
4. Daftarkan di `main.py` map.
5. Tambah test di `tests/test_strategies.py`.

## 13. Cara Menambah Agent Baru

Mis. `CriticAgent`.
1. Tambah `src/agents/critic_agent.py` subclass `BaseAgent`.
2. Implementasi `_render`.
3. Tambah `prompt_file`/`default_template`.
4. Daftarkan di `registry.py:build_agent_team`.
5. Tambah test di `tests/test_agents.py`.
