# Design Science Research (DSR) Mapping
## AgentBench-SE

**Peneliti:** Agi Rahman Setiadi
**Institusi:** Universitas Negeri Jakarta — Sistem dan Teknologi Informasi

---

## 1. Problem Identification

**Latar Belakang:**
Bug fixing merupakan salah satu aktivitas paling mahal dalam pemeliharaan perangkat lunak. Penelitian menunjukkan bahwa bug fixing menempati 30–50% dari waktu pengembangan software (Lientz & Swanson, 1980). Pendekatan manual memiliki keterbatasan: rentan human error, waktu pengerjaan lama, dan tidak scalable.

**Gap Penelitian:**
AI Agent menjanjikan solusi untuk bug fixing otomatis. Namun, belum ada framework yang bisa mengevaluasi secara adil perbedaan strategi orkestrasi agent. Pertanyaan mendasar:

- Apakah menambah agent (Planner, Reviewer) benar-benar meningkatkan kualitas?
- Berapa biaya tambahan dari setiap penambahan agent?
- Kapan trade-off antara biaya dan efektivitas sudah optimal?

**Isu Utama:**
1. Evaluasi yang adil membutuhkan model LLM yang identik — jika model berbeda, sulit memisahkan efek model dari efek strategi
2. Metrik harus komprehensif: tidak hanya success rate, tapi juga waktu, biaya, dan jumlah inferensi
3. Artifact (output agent) harus disimpan untuk dokumentasi dan verifikasi

---

## 2. Objectives of Solution

**Tujuan Utama:**
Mengembangkan framework eksperimen (AgentBench-SE) yang:
1. Mengontrol variabel (model sama, strategi berbeda) sehingga perbedaan hasil murni dipengaruhi oleh strategi orkestrasi
2. Mengumpulkan metrik komprehensif: efektivitas (success rate), efisiensi (time/inference), biaya (tokens/cost)
3. Menyimpan artifact untuk dokumentasi penelitian (BAB IV)
4. Bersifat reproducible — siapapun dapat mengulang eksperimen dengan konfigurasi yang sama

**Artifact yang Dihasilkan:**
AgentBench-SE — CLI framework untuk eksperimen orkestrasi AI Agent

**Research Questions:**

| RQ | Pertanyaan | Metrik | Target |
|----|-----------|--------|--------|
| RQ1 | Efektivitas: Mana strategi yang menghasilkan patch valid? | Build Success Rate, Test Pass Rate | Perbandingan 3 strategi |
| RQ2 | Efisiensi: Berapa lama dan berapa banyak inferensi tiap strategi? | Avg Time per Inference, Execution Time | Perbandingan 3 strategi |
| RQ3 | Trade-off: Apakah peningkatan biaya sebanding dengan peningkatan kualitas? | Cost (USD/IDR), Cost per Success | Perbandingan 3 strategi |

---

## 3. Design & Development

### 3.1 Arsitektur

```
┌──────────────────────────────────────────────────────┐
│                    Researcher                         │
│              (CLI: python src/main.py)                │
└──────────────────┬───────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│              Experiment Controller                     │
│     (main.py → run_experiments → CSV + JSONL)         │
└──────────────────┬───────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│                   Strategies                          │
│  ┌─────────┐  ┌─────────────┐  ┌────────────────┐   │
│  │ Direct   │  │ Planning    │  │ Review          │   │
│  │ (1×)    │  │ (2×)       │  │ (3–4×)         │   │
│  └─────────┘  └─────────────┘  └────────────────┘   │
└──────────────────┬───────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│              LLM Provider                             │
│         OpenRouter: tencent/hy3                       │
│         (fallback: Gemini, Groq)                      │
└──────────────────┬───────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│              Models & Evaluation                      │
│  InferenceResult → ExecutionResult → ExperimentResult │
│  CostCalculator → CostSummary                         │
│  Statistics → statistics.json + summary.md            │
└──────────────────────────────────────────────────────┘
```

### 3.2 Data Flow

```
Issue → Strategy.run()
  │
  ├─ [S1] Executor → InferenceResult → Patch
  │
  ├─ [S2] Planner → InferenceResult
  │       Executor → InferenceResult → Patch
  │
  └─ [S3] Planner → InferenceResult
          Executor → InferenceResult → Patch (initial)
          Reviewer → InferenceResult → Feedback
          Executor → InferenceResult → Patch (final, jika revisi)
  │
  ▼
InferenceRun (patch, list[InferenceResult])
  │
  ▼
ExperimentResult (instance_id, strategy, model, ExecutionResult, CostSummary, EvaluationResult)
  │
  ▼
flatten_for_csv() → results.csv
```

### 3.3 Model Data

```
InferenceResult
  role: str
  response: str
  usage: {prompt_tokens, completion_tokens, total_tokens}
  execution_time: float
  finish_reason: str
  model: str
  timestamp: str

InferenceRun
  patch: str
  inferences: list[InferenceResult]

ExecutionResult (wraps InferenceRun)
  → inference_count, execution_time, prompt_tokens, completion_tokens, total_tokens

CostSummary
  input_cost_usd, output_cost_usd, total_cost_usd, total_cost_idr

ExperimentResult (top-level)
  instance_id, strategy, model, ExecutionResult, CostSummary, EvaluationResult, difficulty
```

### 3.4 Spesifikasi Komponen

| Komponen | File | Tanggung Jawab |
|----------|------|----------------|
| Dataset Loader | `src/dataset_loader.py` | Load SWE-bench Lite, filter per repo |
| Provider | `src/providers/openrouter_provider.py` | OpenRouter API → InferenceResult |
| Strategy | `src/strategies/*.py` | Orkestrasi agent per strategi |
| Runner | `src/experiments/runner.py` | Loop eksperimen, savepoint, artifact |
| Cost Calculator | `src/evaluation/cost.py` | Token → USD/IDR |
| Statistics | `src/evaluation/statistics.py` | Aggregate metrics → JSON/MD |
| Report Generator | `src/evaluation/report_generator.py` | Merge eval → CSV + figures |
| Experiment ID | `src/experiment_id.py` | EXP-YYYYMMDD-NNN |

---

## 4. Demonstration

**Dataset:**
SWE-bench Lite — 50 issues, 6 repositori:
- django/django (10 issues) — framework web → difficulty: hard
- sympy/sympy (10 issues) — symbolic computing → difficulty: hard
- scikit-learn/scikit-learn (10 issues) — ML library → difficulty: medium
- matplotlib/matplotlib (10 issues) — plotting library → difficulty: medium
- psf/requests (6 issues) — HTTP library → difficulty: easy
- mwaskom/seaborn (4 issues) — statistical plotting → difficulty: easy

**Model:**
`tencent/hy3` via OpenRouter
- Input: $0.14 per 1M tokens
- Output: $0.58 per 1M tokens
- Temperature: 0.2
- Max tokens: 4096

**Strategi:**

| Kode | Strategi | Agent | Inferensi |
|------|----------|-------|-----------|
| S1 | Direct | Executor | 1× |
| S2 | Planning | Planner → Executor | 2× |
| S3 | Planning+Review | Planner → Executor → Reviewer → (Executor) | 3–4× |

**Eksperimen:**
- Total runs: 50 issues × 3 strategi = 150 runs
- Provider: OpenRouter (tencent/hy3)
- Temperature: 0.2
- Max retries: 3

**Output per Eksperimen:**
- `results.csv` — metrik per issue per strategi
- `statistics.json` — aggregate metrics
- `summary.md` — human-readable summary
- `predictions.jsonl` — siap untuk SWE-bench evaluation
- `artifacts/` — planner/executor/reviewer output per issue
- `experiment.yaml` — reproducibility config

---

## 5. Evaluation

### 5.1 Functional Validation

Setiap FR divalidasi melalui checklist functional test:

| Test Case | Expected | Status |
|-----------|----------|--------|
| FR-01 Load dataset 50 issues | 50 Issue objects | ✅ |
| FR-02 Configure via CLI | Provider + Config inited | ✅ |
| FR-03 Health check passes | True | ✅ |
| FR-04 S1 produces patch | Non-empty patch | ✅ |
| FR-05 S2 produces plan + patch | plan + patch | ✅ |
| FR-06 S3 produces plan + feedback + patch | Full pipeline output | ✅ |
| FR-07 Retry on error | 3× backoff | ✅ |
| FR-08 Artifacts saved | 4 files per issue | ✅ |
| FR-09 CSV exported | results.csv exists | ✅ |
| FR-10 Cost computed | USD/IDR values > 0 | ✅ |
| FR-11 Resume works | Skip done issues | ✅ |
| FR-12 Diff extracted | Valid patch string | ✅ |
| FR-13 Hunk headers fixed | Correct line counts | ✅ |
| FR-14 Experiment ID generated | EXP-YYYYMMDD-NNN | ✅ |
| FR-15 Report generated | Figures + CSVs | ✅ |
| FR-16 View results | Tables printed | ✅ |

### 5.2 Comparative Analysis (RQ)

| RQ | Metrik | Metode |
|----|--------|--------|
| RQ1 | Build Success Rate, Test Pass Rate | Comparative analysis: S1 vs S2 vs S3 |
| RQ2 | Avg Time per Inference, Total Execution Time | Comparative analysis: S1 vs S2 vs S3 |
| RQ3 | Estimated Cost (USD/IDR), Cost per Success | Cost-benefit analysis: token cost vs effectiveness |

### 5.3 Root Cause Analysis

Planner menghasilkan structured output:
- `summary` — ringkasan bug
- `root_cause_hypothesis` — hipotesis penyebab
- `affected_files` — file yang terdampak
- `repair_strategy` — strategi perbaikan

Output ini memungkinkan root cause analysis yang reproducible.

### 5.4 Cost-Benefit Analysis

| Strategy | Expected Token Cost | Expected Inferences | Expected Effectiveness |
|----------|-------------------|--------------------|-----------------------|
| S1 | Rendah | 1× | ? (diharapkan rendah) |
| S2 | Sedang | 2× | ? (diharapkan sedang) |
| S3 | Tinggi | 3–4× | ? (diharapkan tertinggi) |

RQ3 akan menjawab apakah biaya tambahan S2/S3 sebanding dengan peningkatan kualitas.

---

## 6. Communication

### 6.1 Dokumen SA/DD

| Dokumen | Isi |
|---------|-----|
| `sdd.md` | Spec-Driven Development — arsitektur, role spec, trade-off, runbook |
| `docs/SRS.md` | Software Requirements Specification — FR, NFR, constraints |
| `docs/USE_CASE_SPEC.md` | Use Case Specification — 13 use cases, 4 actors |
| `docs/RTM.md` | Requirement Traceability Matrix — FR → Design → Implementation → Test |
| `docs/SYSTEM_VALIDATION.md` | System Validation Report — functional + NFR validation |
| `docs/DSR_MAPPING.md` | DSR Mapping — dokumen ini |

### 6.2 Output Eksperimen

- `results/<EXP-ID>/results.csv` — metrik mentah
- `results/<EXP-ID>/statistics.json` — aggregate
- `results/<EXP-ID>/summary.md` — human-readable
- `results/<EXP-ID>/predictions/predictions.jsonl` — untuk SWE-bench eval
- `results/<EXP-ID>/artifacts/` — output per-agent per issue
- `results/<EXP-ID>/experiment.yaml` — reproducibility

### 6.3 Posisi dalam BAB Skripsi

| Bab | Isi | DSR Phase |
|-----|-----|-----------|
| BAB I | Pendahuluan | Problem Identification |
| BAB II | Tinjauan Pustaka | Literature Review |
| BAB III | Metodologi + SRS + Use Case + RTM | Objectives + Design |
| BAB IV | Implementasi + Artifact + Validation | Design + Demonstration |
| BAB V | Evaluasi + Hasil + Analisis | Evaluation |
| BAB VI | Kesimpulan + Saran | Communication |
