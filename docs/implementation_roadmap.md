# Implementation Roadmap

Dokumen ini menjadi panduan implementasi Experimental Framework setelah seluruh desain penelitian dinyatakan **LOCKED**.

Roadmap disusun secara bertahap sehingga setiap fase menghasilkan sistem yang dapat dijalankan (*working increment*).

---

# Phase 0 — Research Preparation

## Objective

Menyiapkan seluruh kebutuhan penelitian sebelum implementasi dimulai.

### Tasks

- [ ] Menentukan benchmark (SWE-bench Lite / Verified).
- [ ] Menentukan repository target.
- [ ] Menentukan model AI.
- [ ] Menentukan Inference Provider.
- [ ] Menentukan framework agent (LangGraph / ADK / custom).
- [ ] Menyiapkan Docker.
- [ ] Membuat repository Git.

### Deliverable

- Environment siap digunakan.

---

# Phase 1 — Framework Foundation

## Objective

Membangun kerangka dasar Experimental Framework.

### Tasks

- [ ] Struktur folder proyek.
- [ ] Konfigurasi project.
- [ ] Strategy Interface.
- [ ] Artifact Model.
- [ ] Logging System.
- [ ] Configuration Loader.

### Deliverable

Framework dasar dapat dijalankan.

---

# Phase 2 — Minimal Experiment (MVP)

## Objective

Membangun eksperimen paling sederhana menggunakan Direct Strategy.

### Tasks

- [ ] Implementasi Direct Strategy.
- [ ] Implementasi Executor.
- [ ] Integrasi Inference Provider.
- [ ] Evaluation Engine (versi awal).
- [ ] Metrics Collector (versi awal).
- [ ] Menjalankan satu issue hingga selesai.

### Deliverable

Pipeline berikut sudah berjalan:

Issue → Direct Strategy → Patch → Evaluation → CSV

---

# Phase 3 — Full Strategy

## Objective

Menambahkan seluruh strategi penelitian.

### Tasks

- [ ] Implementasi Planner.
- [ ] Planning Artifact.
- [ ] Planning Strategy.
- [ ] Implementasi Reviewer.
- [ ] Review Artifact.
- [ ] Planning + Review Strategy.

### Deliverable

Ketiga strategi dapat dijalankan.

---

# Phase 4 — Experiment Automation

## Objective

Mengotomatisasi seluruh proses eksperimen.

### Tasks

- [ ] Experiment Controller.
- [ ] Batch Execution.
- [ ] Retry Policy.
- [ ] Experiment Manifest.
- [ ] Export Result.
- [ ] Resume Experiment.

### Deliverable

Eksperimen dapat berjalan otomatis untuk banyak issue.

---

# Phase 5 — Analysis & Thesis

## Objective

Mengolah hasil eksperimen menjadi temuan penelitian.

### Tasks

- [ ] Data Cleaning.
- [ ] Statistik Deskriptif.
- [ ] Visualisasi.
- [ ] Analisis RQ1.
- [ ] Analisis RQ2.
- [ ] Analisis RQ3.
- [ ] Penyusunan Bab 4.
- [ ] Penyusunan Bab 5.

### Deliverable

Hasil penelitian siap dipublikasikan.

---

# Success Criteria

| Phase | Expected Output |
|--------|-----------------|
| Phase 0 | Environment siap |
| Phase 1 | Framework dasar |
| Phase 2 | Direct Strategy berjalan |
| Phase 3 | Seluruh strategi berjalan |
| Phase 4 | Eksperimen otomatis |
| Phase 5 | Skripsi selesai |
