# Use Case Specification
## AgentBench-SE

**Peneliti:** Agi Rahman Setiadi
**Institusi:** Universitas Negeri Jakarta — Sistem dan Teknologi Informasi

---

## Actors

| Actor | Tipe | Deskripsi |
|-------|------|-----------|
| **Researcher** | Primary | Pengguna utama — menjalankan eksperimen, menganalisis hasil |
| **Experiment Runner** | System | Mengorkestrasi loop issue × strategy, savepoint, artifact |
| **AI Agent** | System | Planner/Executor/Reviewer — LLM role yang menghasilkan output |
| **LLM Provider** | External | OpenRouter (tencent/hy3) — menyediakan layanan inference |

---

## Use Case Diagram

```
                    ┌───────────────────────────────┐
                    │     AgentBench-SE System       │
                    │                               │
  ┌─────────┐      │  ┌─────────────────────┐      │
  │         │      │  │                     │      │
  │Researcher├─────┼──► UC-01 Configure Exp │      │
  │         │      │  ├─────────────────────┤      │
  │         ├─────┼──► UC-02 Load Dataset  │      │
  │         │      │  ├─────────────────────┤      │
  │         ├─────┼──► UC-09 Resume Exp    │      │
  │         │      │  ├─────────────────────┤      │
  │         ├─────┼──► UC-10 View Results  │      │
  │         │      │  ├─────────────────────┤      │
  │         ├─────┼──► UC-15 Generate Report│      │
  └─────────┘      │  └─────────────────────┘      │
                    │                               │
                    │  ┌─────────────────────┐      │
                    │  │ Experiment Runner    │      │
                    │  ├─────────────────────┤      │
                    │  │ UC-03 Execute S1    │      │
                    │  │ UC-04 Execute S2    │      │
                    │  │ UC-05 Execute S3    │      │
                    │  │ UC-06 Save Artifacts│      │
                    │  │ UC-07 Export Results│      │
                    │  │ UC-08 Compute Cost  │      │
                    │  │ UC-14 Generate ID   │      │
                    │  └─────────────────────┘      │
                    │                               │
                    │  ┌─────────────────────┐      │
                    │  │ AI Agent            │      │
                    │  ├─────────────────────┤      │
                    │  │ UC-04a Generate Plan │      │
                    │  │ UC-04b Generate Patch│      │
                    │  │ UC-05a Review Patch  │      │
                    │  │ UC-05b Revise Patch  │      │
                    │  └─────────────────────┘      │
                    │                               │
                    │  ┌─────────────────────┐      │
                    │  │ LLM Provider        │      │
                    │  ├─────────────────────┤      │
                    │  │ UC-11 Health Check  │      │
                    │  │ UC-12 Execute Inference│    │
                    │  │ UC-13 Retry on Error│      │
                    │  └─────────────────────┘      │
                    └───────────────────────────────┘
```

---

## Use Case Specifications

### UC-01: Configure Experiment

| Aspek | Detail |
|-------|--------|
| **ID** | UC-01 |
| **Nama** | Configure Experiment |
| **Actor** | Researcher |
| **Trigger** | Researcher menjalankan `python src/main.py --provider openrouter ...` |
| **Precondition** | File `.env` dengan `OPENROUTER_API_KEY` tersedia |
| **Postcondition** | Provider initialized, Config siap |

**Flow Utama:**
1. Researcher menjalankan CLI dengan args: `--provider`, `--output`, `--issues`, `--resume`
2. System membaca `.env` → load API key, model, temperature
3. System inisialisasi provider sesuai pilihan
4. System lakukan health check → success
5. System simpan `experiment.yaml`

**Alternative Flow:**
- 4a. Health check gagal → System log error, eksperimen dibatalkan
- 4b. `.env` tidak ada → raise ValueError

---

### UC-02: Load Dataset

| Aspek | Detail |
|-------|--------|
| **ID** | UC-02 |
| **Nama** | Load Dataset |
| **Actor** | Experiment Runner |
| **Trigger** | Setelah UC-01 selesai |
| **Precondition** | Koneksi internet tersedia |
| **Postcondition** | `list[Issue]` (50 issues) siap diproses |

**Flow Utama:**
1. Runner panggil `load_swe_bench_lite()` → download dataset dari HuggingFace
2. Runner filter per `DEFAULT_REPO_SPECS` (6 repos)
3. Runner buat `Issue` objects dengan difficulty mapping
4. Runner log jumlah issues yang berhasil dimuat

**Alternative Flow:**
- 1a. Download gagal → raise, eksperimen dihentikan

---

### UC-03: Execute Direct Strategy (S1)

| Aspek | Detail |
|-------|--------|
| **ID** | UC-03 |
| **Nama** | Execute Direct Strategy |
| **Actor** | AI Agent + LLM Provider |
| **Trigger** | Per issue, saat loop eksperimen |
| **Input** | Issue |
| **Output** | Patch + ExperimentResult |
| **Inferences** | 1× |

**Flow Utama:**
1. Runner panggil `DirectStrategy.run(issue)`
2. Executor load prompt template `direct_prompt.md`
3. Executor substitusi `{{issue}}` → prompt final
4. Provider `generate(prompt)` → InferenceResult
5. Hitung CostSummary dari InferenceResult
6. Return (Patch, ExperimentResult)

**Alternative Flow:**
- 4a. API error → Retry (UC-13) → 3× gagal → log error, skip issue

---

### UC-04: Execute Planning Strategy (S2)

| Aspek | Detail |
|-------|--------|
| **ID** | UC-04 |
| **Nama** | Execute Planning Strategy |
| **Actor** | AI Agent + LLM Provider |
| **Trigger** | Per issue |
| **Input** | Issue |
| **Output** | Plan + Patch + ExperimentResult |
| **Inferences** | 2× |

**Flow Utama:**
1. Runner panggil `PlanningStrategy.run(issue)`
2. **Planner:** load `planner.md` → generate → InferenceResult (plan)
3. **Executor:** load `executor.md` → substitusi `{{issue}}` + `{{plan}}` → generate → InferenceResult (patch)
4. Hitung CostSummary dari semua InferenceResult
5. Return (Patch, ExperimentResult)

**Include:**
- UC-04a: Generate Plan
- UC-04b: Generate Patch

---

### UC-04a: Generate Plan (Planner)

| Aspek | Detail |
|-------|--------|
| **Input** | Issue |
| **Output** | Planning Document (teks) |
| **Content** | `summary`, `root_cause_hypothesis`, `affected_files`, `repair_strategy`, `confidence` |

---

### UC-04b: Generate Patch (Executor)

| Aspek | Detail |
|-------|--------|
| **Input** | Issue + Plan |
| **Output** | Patch (diff format) |

---

### UC-05: Execute Review Strategy (S3)

| Aspek | Detail |
|-------|--------|
| **ID** | UC-05 |
| **Nama** | Execute Review Strategy |
| **Actor** | AI Agent + LLM Provider |
| **Trigger** | Per issue |
| **Input** | Issue |
| **Output** | Plan + Initial Patch + Review Feedback + Final Patch + ExperimentResult |
| **Inferences** | 3–4× |

**Flow Utama:**
1. Runner panggil `ReviewStrategy.run(issue)`
2. **Planner:** generate plan → InferenceResult
3. **Executor:** generate initial patch → InferenceResult
4. **Reviewer:** load `reviewer.md` → generate feedback → InferenceResult
5. Jika verdict `NEEDS_REVISION` → **Executor:** revise patch → InferenceResult
6. Hitung CostSummary dari semua InferenceResult
7. Return (Final Patch, ExperimentResult)

**Include:**
- UC-04a: Generate Plan
- UC-04b: Generate Patch
- UC-05a: Review Patch
- UC-05b: Revise Patch

---

### UC-05a: Review Patch (Reviewer)

| Aspek | Detail |
|-------|--------|
| **Input** | Issue + Plan + Initial Patch |
| **Output** | Review Feedback (teks + verdict) |
| **Verdict** | `APPROVED` atau `NEEDS_REVISION` |

---

### UC-05b: Revise Patch (Executor)

| Aspek | Detail |
|-------|--------|
| **Input** | Issue + Plan + Review Feedback |
| **Output** | Revised Patch |
| **Condition** | Hanya jika verdict = `NEEDS_REVISION` |

---

### UC-06: Save Artifacts

| Aspek | Detail |
|-------|--------|
| **ID** | UC-06 |
| **Nama** | Save Artifacts |
| **Actor** | Experiment Runner |
| **Trigger** | Setelah setiap issue-strategy run berhasil |
| **Input** | instance_id, list[InferenceResult], final_patch |
| **Output** | File: `planner.md`, `executor.md`, `reviewer.md`, `patch.txt` |

**Flow Utama:**
1. Runner buat directory `artifacts/<instance_id>/`
2. Untuk setiap InferenceResult: simpan berdasarkan role
3. Simpan final patch ke `patch.txt`

---

### UC-07: Export Results

| Aspek | Detail |
|-------|--------|
| **ID** | UC-07 |
| **Nama** | Export Results |
| **Actor** | Experiment Runner |
| **Trigger** | Setelah semua issue selesai diproses |
| **Output** | `results.csv`, `statistics.json`, `summary.md`, `predictions/*.jsonl` |

**Flow Utama:**
1. Flatten semua ExperimentResult → rows
2. Export ke CSV via pandas
3. Hitung aggregate statistics → JSON
4. Generate human-readable summary → Markdown
5. Export predictions per strategy + aggregated → JSONL

---

### UC-08: Compute Cost

| Aspek | Detail |
|-------|--------|
| **ID** | UC-08 |
| **Nama** | Compute Cost |
| **Actor** | CostCalculator |
| **Trigger** | Setiap selesai 1 run (per issue per strategi) |
| **Input** | list[InferenceResult] (semua inferensi dalam 1 run) |
| **Output** | CostSummary (total USD + IDR) |

**Flow Utama:**
1. Untuk setiap InferenceResult: hitung cost dari token × pricing
2. Aggregate ke CostSummary
3. Return ke ExperimentResult

---

### UC-09: Resume Experiment

| Aspek | Detail |
|-------|--------|
| **ID** | UC-09 |
| **Nama** | Resume Experiment |
| **Actor** | Researcher |
| **Trigger** | `--resume` flag |
| **Precondition** | File `predictions.jsonl` dari run sebelumnya tersedia |
| **Postcondition** | Eksperimen lanjut, skip issues yang sudah selesai |

**Flow Utama:**
1. Researcher jalankan dengan `--resume`
2. Runner baca existing `predictions.jsonl` per strategi
3. Collect `instance_id` yang sudah done
4. Saat loop: skip issue yang sudah done
5. Lanjut dari issue berikutnya

---

### UC-10: View Results

| Aspek | Detail |
|-------|--------|
| **ID** | UC-10 |
| **Nama** | View Results |
| **Actor** | Researcher |
| **Trigger** | `python src/view_results.py <command>` |
| **Input** | Command: `list`, `summary`, `compare`, `errors`, `patch`, `cost_per_success` |
| **Output** | Tables ke stdout |

**Flow Utama:**
1. Researcher pilih command
2. System baca `results.csv`
3. System proses dan tampilkan output dalam format tabel

---

### UC-11: Health Check Provider

| Aspek | Detail |
|-------|--------|
| **ID** | UC-11 |
| **Nama** | Health Check Provider |
| **Actor** | LLM Provider |
| **Trigger** | Sebelum eksperimen dimulai |
| **Input** | Prompt: "Reply with only: OK" |
| **Output** | `bool` |

**Flow Utama:**
1. Provider generate prompt sederhana
2. Jika response diterima → return True
3. Jika error → return False

---

### UC-12: Execute Inference

| Aspek | Detail |
|-------|--------|
| **ID** | UC-12 |
| **Nama** | Execute Inference |
| **Actor** | LLM Provider |
| **Trigger** | Dipanggil oleh Strategy |
| **Input** | Prompt string + role |
| **Output** | InferenceResult |

**Flow Utama:**
1. Provider kirim prompt ke LLM API
2. Terima response → buat InferenceResult
3. Return InferenceResult

---

### UC-13: Retry on Error

| Aspek | Detail |
|-------|--------|
| **ID** | UC-13 |
| **Nama** | Retry on Error |
| **Actor** | LLM Provider |
| **Trigger** | Network error atau API error |
| **Input** | Failed request |
| **Output** | InferenceResult (jika berhasil) atau raise (jika 3× gagal) |

**Flow Utama:**
1. Provider dapat error
2. Hitung delay: 2^attempt detik (exponential backoff)
3. Wait delay → retry
4. Jika berhasil → return InferenceResult
5. Jika 3× gagal → raise exception

---

### UC-14: Generate Experiment ID

| Aspek | Detail |
|-------|--------|
| **ID** | UC-14 |
| **Nama** | Generate Experiment ID |
| **Actor** | Experiment Runner |
| **Trigger** | Awal eksperimen |
| **Output** | String ID: `EXP-YYYYMMDD-NNN` |

**Flow Utama:**
1. Generate ID berdasarkan tanggal + counter
2. Buat directory `results/<EXP-ID>/`

---

### UC-15: Generate Report

| Aspek | Detail |
|-------|--------|
| **ID** | UC-15 |
| **Nama** | Generate Report |
| **Actor** | Researcher |
| **Trigger** | Post-analysis |
| **Input** | experiment results + evaluation results |
| **Output** | Report CSVs + matplotlib figures |

**Flow Utama:**
1. Load eval results + experiment results
2. Merge → per-repo, per-strategy, tradeoff CSVs
3. Generate matplotlib figures
4. Generate summary Markdown

---

## Include & Extend Relationships

| Use Case | Relationship | Target |
|----------|-------------|--------|
| UC-03 (S1) | include | UC-12 (Execute Inference) |
| UC-04 (S2) | include | UC-04a (Generate Plan) |
| UC-04 (S2) | include | UC-04b (Generate Patch) |
| UC-05 (S3) | include | UC-04a (Generate Plan) |
| UC-05 (S3) | include | UC-04b (Generate Patch) |
| UC-05 (S3) | include | UC-05a (Review Patch) |
| UC-05 (S3) | extend | UC-05b (Revise Patch) — conditional |
| UC-03, UC-04, UC-05 | extend | UC-13 (Retry on Error) — on failure |
| UC-07 (Export) | include | UC-08 (Compute Cost) |
| UC-01 (Configure) | include | UC-11 (Health Check) |
| UC-01 (Configure) | include | UC-02 (Load Dataset) |
| UC-01 (Configure) | include | UC-14 (Generate ID) |

---

## Use Case Count Summary

| Actor | Use Cases |
|-------|-----------|
| Researcher | UC-01, UC-09, UC-10, UC-15 |
| Experiment Runner | UC-02, UC-03, UC-04, UC-05, UC-06, UC-07, UC-14 |
| AI Agent | UC-04a, UC-04b, UC-05a, UC-05b |
| LLM Provider | UC-08, UC-11, UC-12, UC-13 |

**Total: 15 Use Cases, 4 Actors**
