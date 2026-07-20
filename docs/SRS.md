# Software Requirements Specification (SRS)
## AgentBench-SE — Analisis Trade-off Strategi Orkestrasi AI Agent

| Item | Detail |
|------|--------|
| **Peneliti** | Agi Rahman Setiadi |
| **Institusi** | Universitas Negeri Jakarta — Sistem dan Teknologi Informasi |
| **Kategori** | System Analysis, Design & Development (SA/DD) |
| **Pendekatan** | Design Science Research (DSR) |
| **Model Utama** | `tencent/hy3` via OpenRouter |

---

## 1. Introduction

### 1.1 Purpose

Dokumen ini mendefinisikan persyaratan fungsional dan non-fungsional untuk AgentBench-SE, sebuah framework eksperimen yang membandingkan tiga strategi orkestrasi AI Agent (Direct, Planning, Planning+Review) dalam menyelesaikan tugas bug fixing perangkat lunak pada dataset SWE-bench Lite.

### 1.2 Scope

AgentBench-SE adalah CLI tool Python 3.10+ yang:
- Memuat dataset SWE-bench Lite dari HuggingFace (multi-repo: 6 repositori, 50 issues)
- Menjalankan 3 strategi orkestrasi AI Agent dengan model LLM yang identik
- Mengumpulkan metrik: execution time, inference count, token usage, estimated cost
- Mengekspor hasil ke CSV, JSONL, JSON, dan Markdown
- Menyimpan artifact per-agent (planner, executor, reviewer) untuk dokumentasi

### 1.3 Definitions

| Istilah | Definisi |
|---------|----------|
| **Agent** | AI role (Planner/Executor/Reviewer) yang dijalankan oleh LLM |
| **Artifact** | Output antara dari agent (plan.md, executor.md, review.md, patch.txt) |
| **Eksperimen** | Satu sesi lengkap: 50 issues × 3 strategi = 150 runs |
| **Run** | Satu eksekusi strategi pada satu issue |
| **SWE-bench** | Benchmark untuk evaluasi patch bug fixing |
| **Orkestrasi** | Urutan pemanggilan agent dalam satu strategi |

---

## 2. Overall Description

### 2.1 Product Perspective

AgentBench-SE adalah standalone CLI tool, bukan bagian dari sistem yang lebih besar. Tidak memerlukan web server, database, atau GUI.

Architecture overview:

```
Researcher → main.py → select_issues() → run_experiments()
                ↓
         ┌─ DirectStrategy (S1) ── 1 inference
         ├─ PlanningStrategy (S2) ─┐ 2 inferences
         └─ ReviewStrategy (S3) ───┐ 3–4 inferences
                ↓
         LLM Provider (OpenRouter)
                ↓
         Hasil: CSV + JSONL + artifacts + statistics
```

### 2.2 Product Functions

Ringkasan 6 fungsi utama:
1. **Dataset Management** — Load, filter, dan seleksi issue SWE-bench Lite
2. **Provider Management** — Inisialisasi dan health check OpenRouter (Gemini/Groq sebagai alternatif)
3. **Strategy Execution** — Jalankan Direct/Planning/Review strategy
4. **Result Export** — CSV, statistics JSON, summary Markdown, predictions JSONL
5. **Artifact Management** — Simpan output per-agent (planner, executor, reviewer)
6. **Experiment Analysis** — View results, cost calculation, comparative analysis

### 2.3 User Classes

| User Class | Deskripsi |
|------------|-----------|
| **Researcher** | Pengguna utama. Menjalankan eksperimen, menganalisis hasil. Satu-satunya kelas pengguna. |

### 2.4 Operating Environment

| Komponen | Spesifikasi |
|----------|-------------|
| **OS** | Windows 10+ / Linux / macOS |
| **Python** | 3.10+ |
| **RAM** | 8 GB minimum |
| **Dependencies** | `openai`, `google-genai`, `datasets`, `pandas`, `loguru`, `python-dotenv` |
| **API Key** | `OPENROUTER_API_KEY` (wajib) |
| **Docker** | Opsional — hanya untuk SWE-bench harness evaluation |

### 2.5 Design Constraints

| Constraint | Alasan |
|------------|--------|
| Tanpa abstract class / interface complex | Desain minimal, fokus eksperimen |
| Tanpa database — file-based storage | Sederhana, reproducible, mudah diperiksa |
| Tanpa GUI — CLI only | Fokus ke batch experiment, bukan interaktivitas |
| Provider pattern: `generate(prompt) → InferenceResult` | Transparansi metrik, tidak ada state tersembunyi |
| Data model = Nested (≠ flat CSV) | Separasi concern: internal representation ≠ export format |

### 2.6 Assumptions & Dependencies

| Asumsi | Dampak jika salah |
|--------|-------------------|
| SWE-bench Lite dataset tersedia di HuggingFace | Eksperimen tidak bisa berjalan |
| `OPENROUTER_API_KEY` tersedia | Eksperimen tidak bisa berjalan |
| Koneksi internet stabil | Provider call gagal → retry |
| `tencent/hy3` pricing $0.14/$0.58 per M tokens | Perhitungan biaya tidak akurat |

---

## 3. Specific Requirements

### 3.1 Functional Requirements

#### FR-01: Load Dataset
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Memuat dataset SWE-bench Lite dari HuggingFace |
| **Input** | — |
| **Output** | `list[Issue]` — 50 issues (10×Django, 10×SymPy, 10×Scikit-learn, 10×Matplotlib, 6×Requests, 4×Seaborn) |
| **Sumber** | `src/dataset_loader.py:15` |
| **Exception** | Jika gagal download → raise, eksperimen dihentikan |

#### FR-02: Configure Experiment
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Membaca konfigurasi dari CLI args + `.env` |
| **Input** | `--provider`, `--output`, `--issues`, `--rate-limit`, `--resume` |
| **Output** | Provider + Config + experiment.yaml |
| **Sumber** | `src/main.py:18` |

#### FR-03: Health Check Provider
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Verifikasi API key dan koneksi provider sebelum eksperimen |
| **Input** | — |
| **Output** | `bool` — True jika provider siap |
| **Sumber** | `src/providers/openrouter_provider.py:67` |
| **Exception** | Jika gagal → eksperimen dibatalkan |

#### FR-04: Execute Direct Strategy (S1)
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | 1 inference: Executor → Patch. Agent menerima issue, menganalisis, menghasilkan patch secara langsung. |
| **Inferences** | 1× |
| **Input** | Issue |
| **Output** | Patch + ExperimentResult (InferenceRun + CostSummary) |
| **Sumber** | `src/strategies/direct_strategy.py` |

#### FR-05: Execute Planning Strategy (S2)
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | 2 inferences: Planner → Planning Document → Executor → Patch |
| **Inferences** | 2× |
| **Input** | Issue |
| **Output** | Plan + Patch + ExperimentResult |
| **Sumber** | `src/strategies/planning_strategy.py` |

#### FR-06: Execute Review Strategy (S3)
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | 3–4 inferences: Planner → Executor → Reviewer → (Executor revision) |
| **Inferences** | 3× (APPROVED) atau 4× (NEEDS_REVISION) |
| **Input** | Issue |
| **Output** | Plan + Initial Patch + Review Feedback + Final Patch + ExperimentResult |
| **Sumber** | `src/strategies/review_strategy.py` |

#### FR-07: Retry on Failure
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Exponential backoff pada API failure. Delay: 2s, 4s, 8s. Maksimal 3 retries. |
| **Input** | Prompt + role |
| **Output** | InferenceResult (jika sukses) atau raise (jika 3× gagal) |
| **Sumber** | `src/evaluation/retry.py` |

#### FR-08: Save Artifacts
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Menyimpan output tiap agent ke file terpisah per issue: `planner.md`, `executor.md`, `reviewer.md`, `patch.txt` |
| **Output** | File artifact di `results/<EXP-ID>/artifacts/<instance_id>/` |
| **Sumber** | `src/experiments/runner.py:31` |

#### FR-09: Export Experiment Results
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Export ke CSV, statistics JSON, summary Markdown, predictions JSONL |
| **Output** | `results.csv`, `statistics.json`, `summary.md`, `predictions.jsonl` |
| **Sumber** | `src/experiments/runner.py:234` |

#### FR-10: Compute Cost
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Token × pricing → estimated cost (USD + IDR). Pricing dari `PricingTable`. |
| **Input** | `InferenceResult` (token counts + model name) |
| **Output** | `CostSummary` (total USD + IDR) |
| **Sumber** | `src/evaluation/cost.py:77` |

#### FR-11: Resume Experiment
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Membaca existing predictions.jsonl → skip issue yang sudah selesai |
| **Trigger** | `--resume` flag |
| **Output** | Eksperimen lanjutan (tidak termasuk issue selesai) |
| **Sumber** | `src/experiments/runner.py:110` |

#### FR-12: Extract Diff
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Parse response LLM → extract patch format diff. Support JSON key `patch`, markdown ` ```diff ``` `, atau plain diff. |
| **Input** | Raw LLM response |
| **Output** | Clean diff string, atau `""` jika tidak valid |
| **Sumber** | `src/experiments/swebench_adapter.py` |

#### FR-13: Auto-fix Hunk Headers
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Koreksi `@@ -N,M +P,Q @@` header jika count mismatch dengan baris aktual. |
| **Input** | Patch dengan hunk header salah |
| **Output** | Patch dengan header terkoreksi |
| **Sumber** | `src/experiments/swebench_adapter.py` |

#### FR-14: Generate Experiment ID
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Generate ID format `EXP-YYYYMMDD-NNN` (daily counter). |
| **Output** | String ID + directory `results/<EXP-ID>/` |
| **Sumber** | `src/experiment_id.py` |

#### FR-15: Generate Report
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Merge evaluation results + experiment results → report CSVs + matplotlib figures. |
| **Output** | Report files di `eval/` directory |
| **Sumber** | `src/evaluation/report_generator.py` |

#### FR-16: View Results
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | CLI viewer: list all experiments, summary metrics, compare strategies, show errors, show patch, show cost per success. |
| **Command** | `python src/view_results.py {list|summary|compare|errors|patch|cost_per_success}` |
| **Output** | Tables to stdout |
| **Sumber** | `src/view_results.py` |

### 3.2 Non-Functional Requirements

#### NFR-01: Performance
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Satu issue selesai dalam waktu yang wajar |
| **Target** | S1: < 30s, S2: < 60s, S3: < 90s (per issue) |
| **Sumber** | `src/experiments/runner.py` — timing log |

#### NFR-02: Reliability
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Eksperimen tidak kehilangan data jika gagal di tengah |
| **Mekanisme** | Savepoint per issue (append JSONL), retry on API failure (3×), resume mode |

#### NFR-03: Reproducibility
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Setiap eksperimen dapat direproduksi dengan konfigurasi yang sama |
| **Mekanisme** | `experiment.yaml` disimpan di setiap folder eksperimen |

#### NFR-04: Maintainability
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Kode modular, mudah dimodifikasi |
| **Struktur** | `providers/`, `strategies/`, `experiments/`, `evaluation/`, `models/` — each with single responsibility |

#### NFR-05: Portability
| Aspek | Detail |
|-------|--------|
| **Deskripsi** | Berjalan di Windows dan Linux tanpa perubahan |
| **Dependencies** | Pure Python (pip install), tidak ada dependency native |

### 3.3 System Constraints

| ID | Constraint | Detail |
|----|------------|--------|
| SC-01 | API Key | `OPENROUTER_API_KEY` harus tersedia di `.env` |
| SC-02 | Dataset Access | SWE-bench Lite harus bisa diunduh dari HuggingFace |
| SC-03 | External Eval | SWE-bench harness evaluation opsional (butuh Docker/Modal) |
| SC-04 | Memory | Running di 8GB RAM — SWE-bench Docker build tidak bisa lokal |
| SC-05 | Model Consistency | Semua strategi harus memakai model yang identik (`tencent/hy3`) |
