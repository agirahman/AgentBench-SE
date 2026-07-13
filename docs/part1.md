---
title: Research Design Freeze
subtitle: AI Agent Orchestration Strategy for Software Bug Fixing
document_type: Research Design Document (RDD)
version: 1.0
status: DESIGN FREEZE (LOCKED)
author: Agi Rahman Setiadi
study_program: Sistem dan Teknologi Informasi
institution: Universitas Negeri Jakarta
last_update: July 2026
---

# Research Design Freeze

> [!IMPORTANT]
> Dokumen ini merupakan acuan resmi (Design Freeze) penelitian.
> Seluruh keputusan terkait judul, metodologi, arsitektur eksperimen, variabel penelitian, serta desain sistem telah dikunci (LOCKED) sebelum tahap implementasi dimulai.
> Setiap perubahan setelah dokumen ini hanya dapat dilakukan apabila terdapat kendala teknis yang berpotensi memengaruhi validitas penelitian dan harus didokumentasikan pada riwayat revisi.

---

# Document Information

| Item | Description |
|------|-------------|
| Document Name | Research Design Freeze |
| Version | 1.0 |
| Status | LOCKED |
| Document Type | Research Design Document |
| Author | Agi Rahman Setiadi |
| Study Program | Sistem dan Teknologi Informasi |
| Research Area | Artificial Intelligence Software Engineering |
| Last Update | July 2026 |

---

# Revision History

| Version | Date | Description |
|----------|------|-------------|
| 0.1 | July 2026 | Initial research discussion |
| 0.5 | July 2026 | Research architecture completed |
| 0.8 | July 2026 | Component specification completed |
| 1.0 | July 2026 | Design Freeze |

---

# Design Freeze Declaration

Penelitian ini menggunakan pendekatan **Design Freeze**, yaitu seluruh rancangan penelitian diselesaikan dan dikunci sebelum implementasi dilakukan. Pendekatan ini bertujuan untuk menjaga konsistensi antara permasalahan penelitian, metodologi eksperimen, implementasi sistem, serta proses analisis hasil.

Seluruh komponen yang telah dirancang meliputi:

- Research Question
- Variabel Penelitian
- Arsitektur Eksperimen
- Planner Agent
- Executor Agent
- Reviewer Agent
- Evaluation Engine
- Experiment Controller
- Metrics Collector

Perubahan terhadap desain yang telah dikunci hanya diperbolehkan apabila ditemukan kendala teknis yang dapat memengaruhi validitas penelitian. Setiap perubahan wajib didokumentasikan agar proses penelitian tetap dapat direproduksi.

---

# Table of Contents

1. Research Overview
2. Research Background
3. Research Gap
4. Research Questions
5. Research Objectives
6. Research Scope and Limitations
7. Research Method
8. Research Variables
9. Expected Research Contribution
10. Current Research Progress

---

# 1. Research Overview

## Research Title

**Analisis Trade-off Strategi Orkestrasi AI Agent terhadap Efektivitas, Efisiensi, dan Biaya Inferensi dalam Tugas Bug Fixing Perangkat Lunak**

---

## Research Theme

Artificial Intelligence

↓

AI Software Engineering

↓

AI Coding Agent

↓

AI Agent Orchestration

↓

Software Bug Fixing

---

## Research Domain

Penelitian ini berada pada irisan beberapa bidang ilmu, yaitu:

- Artificial Intelligence
- Large Language Models (LLM)
- AI Software Engineering
- Multi-Agent System
- Software Engineering
- Automated Bug Fixing

---

## Research Motivation

Kemajuan Large Language Models (LLM) telah menghasilkan berbagai AI Coding Assistant yang mampu membantu pengembang perangkat lunak dalam menyelesaikan berbagai tugas rekayasa perangkat lunak, seperti penulisan kode, dokumentasi, refactoring, hingga perbaikan bug.

Perkembangan tersebut melahirkan paradigma baru berupa **AI Software Engineering**, yaitu penggunaan AI Agent sebagai bagian dari proses pengembangan perangkat lunak.

Berbagai penelitian terbaru menunjukkan bahwa peningkatan performa AI Software Engineering tidak hanya dipengaruhi oleh kemampuan model bahasa yang digunakan, tetapi juga oleh strategi orkestrasi agent yang mengatur bagaimana proses analisis, implementasi, dan evaluasi dilakukan.

Sebagian besar penelitian saat ini masih berfokus pada peningkatan performa model AI atau pengembangan framework agent baru. Sementara itu, penelitian yang secara khusus membandingkan strategi orkestrasi AI Agent menggunakan model AI yang sama masih relatif terbatas.

Oleh karena itu, penelitian ini berfokus pada evaluasi berbagai strategi orkestrasi AI Agent dalam menyelesaikan tugas bug fixing perangkat lunak dengan menggunakan model AI yang identik sehingga perbedaan hasil eksperimen diharapkan berasal dari strategi orkestrasi yang diterapkan, bukan dari perbedaan kemampuan model.

---

## Research Focus

Fokus utama penelitian adalah mengevaluasi bagaimana perbedaan strategi orkestrasi AI Agent memengaruhi:

- Efektivitas penyelesaian bug
- Efisiensi proses penyelesaian bug
- Biaya inferensi AI

dalam konteks tugas bug fixing perangkat lunak.

---

## Research Object

Objek penelitian bukan merupakan model AI tertentu maupun framework tertentu, melainkan **strategi orkestrasi AI Agent** yang digunakan untuk menyelesaikan tugas bug fixing.

Tiga strategi yang akan dibandingkan adalah:

### Strategy 1 — Direct Execution Strategy

Executor menerima issue secara langsung tanpa proses perencanaan maupun peninjauan.

Workflow:

Issue

↓

Executor

↓

Final Patch

---

### Strategy 2 — Planning-based Strategy

Planner terlebih dahulu melakukan analisis terhadap issue dan menghasilkan Planning Document yang kemudian digunakan oleh Executor untuk menghasilkan patch.

Workflow:

Issue

↓

Planner

↓

Planning Document

↓

Executor

↓

Final Patch

---

### Strategy 3 — Planning and Review Strategy

Planner menghasilkan Planning Document, Executor membuat Initial Patch, kemudian Reviewer melakukan evaluasi terhadap patch tersebut. Apabila ditemukan perbaikan yang diperlukan, Executor melakukan revisi sebelum menghasilkan Final Patch.

Workflow:

Issue

↓

Planner

↓

Planning Document

↓

Executor

↓

Initial Patch

↓

Reviewer

↓

Review Report

↓

Executor Revision

↓

Final Patch

---

## Keywords

- AI Software Engineering
- AI Agent
- Multi-Agent System
- Agent Orchestration
- Software Engineering
- Automated Bug Fixing
- Large Language Model
- Trade-off Analysis
- SWE-bench
- Experiment

---

# 2. Research Background

## 2.1 Background

Perkembangan Large Language Models (LLMs) dalam beberapa tahun terakhir telah membawa perubahan signifikan terhadap proses pengembangan perangkat lunak. Model bahasa modern tidak lagi hanya digunakan sebagai chatbot, tetapi telah berkembang menjadi AI Coding Assistant yang mampu membantu berbagai aktivitas rekayasa perangkat lunak, seperti menghasilkan kode program, menjelaskan source code, membuat dokumentasi, melakukan refactoring, hingga memperbaiki bug secara otomatis.

Kemampuan tersebut mendorong munculnya paradigma baru yang dikenal sebagai **AI Software Engineering**, yaitu pemanfaatan Artificial Intelligence sebagai bagian aktif dalam siklus pengembangan perangkat lunak. Berbeda dengan alat bantu pemrograman konvensional, AI Software Engineering memungkinkan AI berperan sebagai agen yang mampu melakukan pengambilan keputusan, menyusun rencana, mengeksekusi perubahan kode, serta melakukan evaluasi terhadap hasil pekerjaannya sendiri.

Perkembangan AI Software Engineering juga ditandai dengan munculnya berbagai AI Coding Agent seperti SWE-Agent, OpenHands, Devin, Codex, Jules, Claude Code, Cursor Agent, dan framework agent lainnya. Berbagai sistem tersebut menunjukkan bahwa keberhasilan penyelesaian tugas rekayasa perangkat lunak tidak lagi hanya ditentukan oleh kualitas model bahasa yang digunakan, tetapi juga oleh bagaimana agent diorkestrasi untuk menyelesaikan suatu tugas secara sistematis.

Seiring meningkatnya kompleksitas tugas yang diberikan kepada AI Agent, pendekatan Single-Agent mulai menunjukkan berbagai keterbatasan. Pada tugas yang membutuhkan analisis terhadap banyak file, penalaran lintas modul, maupun proses validasi hasil perubahan kode, satu agent sering kali menghasilkan keputusan yang kurang optimal karena harus menangani seluruh proses secara mandiri.

Untuk mengatasi permasalahan tersebut, berbagai penelitian mulai mengembangkan pendekatan **Multi-Agent Software Engineering**. Dalam pendekatan ini, proses penyelesaian tugas dibagi ke dalam beberapa peran yang memiliki tanggung jawab berbeda, seperti Planner yang bertugas melakukan analisis, Executor yang melakukan implementasi perubahan kode, serta Reviewer yang mengevaluasi hasil implementasi sebelum perubahan diterima.

Pembagian peran tersebut melahirkan konsep **Agent Orchestration**, yaitu mekanisme yang mengatur bagaimana beberapa AI Agent berinteraksi untuk menyelesaikan suatu tugas secara kolaboratif. Berbeda dengan penelitian sebelumnya yang lebih banyak berfokus pada peningkatan kemampuan model AI, penelitian mengenai orkestrasi agent lebih menitikberatkan pada strategi koordinasi antar agent selama proses penyelesaian tugas.

Dalam konteks Software Engineering, salah satu tugas yang paling banyak digunakan sebagai benchmark adalah **software bug fixing**, yaitu proses memperbaiki kesalahan pada perangkat lunak berdasarkan laporan issue yang tersedia. Bug fixing dipilih karena mencerminkan proses pengembangan perangkat lunak yang nyata, melibatkan analisis permasalahan, pemahaman source code, implementasi perubahan, serta validasi hasil melalui proses build dan pengujian otomatis.

Meskipun perkembangan AI Software Engineering berlangsung sangat pesat, sebagian besar penelitian saat ini masih berorientasi pada peningkatan performa model bahasa, pengembangan framework agent baru, maupun pengenalan arsitektur agent tertentu. Perbandingan yang dilakukan umumnya masih berfokus pada perbedaan model AI, seperti GPT, Claude, Gemini, DeepSeek, atau model lainnya.

Pendekatan tersebut menyebabkan sulitnya mengetahui apakah peningkatan performa berasal dari kemampuan model AI atau justru berasal dari strategi orkestrasi agent yang digunakan. Dengan kata lain, variabel model dan variabel strategi sering kali berubah secara bersamaan sehingga kontribusi masing-masing faktor menjadi sulit dipisahkan.

Oleh karena itu, diperlukan penelitian yang mampu mengisolasi variabel strategi orkestrasi dengan menggunakan model AI yang sama pada seluruh skenario eksperimen. Dengan pendekatan tersebut, setiap perbedaan hasil yang diperoleh dapat dianalisis sebagai konsekuensi dari strategi orkestrasi yang diterapkan, bukan akibat perbedaan kemampuan model AI.

Penelitian ini mengusulkan evaluasi komparatif terhadap tiga strategi orkestrasi AI Agent, yaitu Direct Execution Strategy, Planning-based Strategy, dan Planning and Review Strategy pada tugas software bug fixing. Ketiga strategi akan dibandingkan menggunakan model AI, dataset, repository, prompt dasar, serta lingkungan eksperimen yang identik sehingga perbedaan hasil eksperimen diharapkan hanya dipengaruhi oleh mekanisme orkestrasi yang digunakan.

Evaluasi dilakukan berdasarkan tiga aspek utama, yaitu efektivitas penyelesaian bug, efisiensi proses penyelesaian tugas, serta biaya inferensi AI. Ketiga aspek tersebut dipilih karena mencerminkan trade-off yang umum dihadapi ketika mengembangkan sistem AI Software Engineering pada dunia nyata. Strategi yang menghasilkan tingkat keberhasilan tinggi belum tentu memiliki efisiensi yang baik maupun biaya inferensi yang rendah. Sebaliknya, strategi dengan biaya inferensi rendah belum tentu mampu menghasilkan patch yang valid secara konsisten.

Hasil penelitian ini diharapkan dapat memberikan pemahaman yang lebih mendalam mengenai karakteristik masing-masing strategi orkestrasi AI Agent serta menjadi referensi bagi pengembang dalam menentukan strategi yang paling sesuai berdasarkan kebutuhan sistem yang dibangun.

---

# 3. Research Gap

## 3.1 Existing Research Trends

Berdasarkan studi literatur terhadap berbagai publikasi terbaru pada periode 2024–2026, perkembangan penelitian AI Software Engineering dapat dikelompokkan menjadi beberapa tren utama sebagai berikut.

### Trend 1 — Pengembangan AI Coding Agent

Penelitian berfokus pada pengembangan agent yang mampu menyelesaikan berbagai tugas Software Engineering secara end-to-end.

Contoh:

- SWE-Agent
- OpenHands
- Devin
- OpenCode
- OpenHands CodeAct

---

### Trend 2 — Pengembangan Multi-Agent Software Engineering

Penelitian mulai membagi proses Software Engineering ke dalam beberapa agent yang memiliki tanggung jawab berbeda.

Contoh:

- Planner
- Executor
- Reviewer
- Debugger
- Tester

---

### Trend 3 — Benchmark AI Software Engineering

Penelitian mulai menggunakan benchmark standar seperti SWE-bench untuk mengevaluasi kemampuan AI Agent secara objektif.

Benchmark yang banyak digunakan antara lain:

- SWE-bench
- SWE-bench Lite
- SWE-bench Verified

---

### Trend 4 — AI Agent Orchestration

Penelitian mulai mengembangkan berbagai strategi koordinasi antar AI Agent.

Contoh pendekatan:

- Sequential Agent
- Hierarchical Agent
- Planner–Executor
- Planner–Executor–Reviewer
- Reflection-based Agent

---

## 3.2 Identified Research Gap

Meskipun perkembangan AI Software Engineering berlangsung sangat pesat, masih terdapat beberapa kesenjangan penelitian.

### Gap 1

Sebagian besar penelitian membandingkan **model AI**, bukan **strategi orkestrasi AI Agent**.

---

### Gap 2

Penelitian yang membandingkan strategi orkestrasi umumnya menggunakan konfigurasi model AI yang berbeda sehingga sulit mengisolasi pengaruh strategi terhadap hasil eksperimen.

---

### Gap 3

Sebagian besar evaluasi hanya berfokus pada tingkat keberhasilan penyelesaian tugas (task success) tanpa mempertimbangkan trade-off antara efektivitas, efisiensi, dan biaya inferensi secara bersamaan.

---

### Gap 4

Belum banyak penelitian yang mengevaluasi strategi orkestrasi AI Agent menggunakan lingkungan eksperimen yang sepenuhnya terkontrol (controlled experiment), di mana model AI, dataset, repository, prompt dasar, parameter inferensi, dan lingkungan eksekusi dibuat identik.

---

## 3.3 Research Position

Berdasarkan kesenjangan penelitian yang telah diidentifikasi, penelitian ini memposisikan diri sebagai **comparative experimental study** yang berfokus pada evaluasi strategi orkestrasi AI Agent, bukan pada perbandingan model AI maupun pengembangan framework baru.

Penelitian ini mengisolasi variabel strategi dengan menjaga seluruh faktor eksperimen lainnya tetap konstan. Dengan demikian, hasil yang diperoleh diharapkan mampu memberikan gambaran yang lebih objektif mengenai trade-off antara efektivitas, efisiensi, dan biaya inferensi pada masing-masing strategi orkestrasi AI Agent dalam tugas software bug fixing.

---

# 4. Research Questions

## 4.1 Main Research Question

Berdasarkan latar belakang dan kesenjangan penelitian yang telah diidentifikasi, penelitian ini berupaya menjawab pertanyaan utama sebagai berikut:

> **Bagaimana trade-off antar strategi orkestrasi AI Agent dalam menyelesaikan tugas software bug fixing berdasarkan aspek efektivitas, efisiensi, dan biaya inferensi?**

Pertanyaan utama tersebut menjadi dasar dalam perancangan eksperimen serta pemilihan metrik evaluasi yang digunakan pada penelitian ini.

---

## 4.2 Research Questions (RQ)

Untuk menjawab pertanyaan utama penelitian, dirumuskan tiga Research Question sebagai berikut.

### RQ1

Bagaimana perbedaan efektivitas antar strategi orkestrasi AI Agent dalam menyelesaikan tugas software bug fixing?

Efektivitas diukur berdasarkan keberhasilan agent dalam menghasilkan patch yang valid melalui indikator:

- Build Success Rate
- Test Pass Rate

---

### RQ2

Bagaimana perbedaan efisiensi antar strategi orkestrasi AI Agent dalam menyelesaikan tugas software bug fixing?

Efisiensi diukur berdasarkan sumber daya waktu yang dibutuhkan selama proses penyelesaian tugas, meliputi:

- Total Execution Time
- Average Execution Time
- Inference Count

---

### RQ3

Bagaimana trade-off antara efektivitas, efisiensi, dan biaya inferensi pada setiap strategi orkestrasi AI Agent?

Biaya inferensi diukur menggunakan:

- Prompt Tokens
- Completion Tokens
- Total Tokens
- Estimated Cost (opsional)

Hasil RQ3 diharapkan mampu menunjukkan karakteristik masing-masing strategi sehingga dapat digunakan sebagai dasar pemilihan strategi sesuai kebutuhan implementasi.

---

# 5. Research Objectives

## 5.1 General Objective

Menganalisis trade-off strategi orkestrasi AI Agent terhadap efektivitas, efisiensi, dan biaya inferensi pada tugas software bug fixing melalui pendekatan eksperimen terkontrol.

---

## 5.2 Specific Objectives

Untuk mencapai tujuan umum tersebut, penelitian ini memiliki beberapa tujuan khusus sebagai berikut.

### Objective 1

Merancang lingkungan eksperimen yang mampu mengevaluasi berbagai strategi orkestrasi AI Agent secara adil dan dapat direproduksi.

---

### Objective 2

Menganalisis efektivitas setiap strategi orkestrasi AI Agent berdasarkan keberhasilan penyelesaian bug.

---

### Objective 3

Menganalisis efisiensi setiap strategi berdasarkan waktu eksekusi dan jumlah proses inferensi AI yang dilakukan.

---

### Objective 4

Menganalisis biaya inferensi yang dihasilkan oleh masing-masing strategi menggunakan metrik penggunaan token.

---

### Objective 5

Mengidentifikasi trade-off antara efektivitas, efisiensi, dan biaya inferensi sehingga diperoleh karakteristik masing-masing strategi orkestrasi AI Agent.

---

# 6. Research Scope and Limitations

## 6.1 Research Scope

Agar penelitian memiliki ruang lingkup yang jelas, beberapa batasan berikut ditetapkan sejak awal penelitian.

### Scope 1 — Research Domain

Penelitian difokuskan pada bidang AI Software Engineering, khususnya evaluasi strategi orkestrasi AI Agent pada tugas software bug fixing.

---

### Scope 2 — Task

Tugas Software Engineering yang dievaluasi hanya mencakup software bug fixing.

Penelitian ini tidak mencakup:

- Feature Development
- Code Generation
- Refactoring
- Documentation Generation
- Code Translation

---

### Scope 3 — AI Model

Seluruh strategi menggunakan model AI yang sama.

Penelitian ini tidak membandingkan performa antar Large Language Model.

---

### Scope 4 — Strategy

Strategi yang dibandingkan terdiri dari tiga pendekatan.

- Direct Execution Strategy
- Planning-based Strategy
- Planning and Review Strategy

Strategi lain seperti Reflection Agent, Graph-based Agent, maupun Hierarchical Multi-Agent tidak menjadi bagian dari penelitian ini.

---

### Scope 5 — Dataset

Eksperimen menggunakan benchmark software engineering yang sama pada seluruh strategi.

Seluruh issue berasal dari dataset yang identik agar setiap strategi memperoleh tingkat kesulitan yang setara.

---

### Scope 6 — Evaluation

Evaluasi dilakukan berdasarkan tiga aspek utama.

- Efektivitas
- Efisiensi
- Biaya Inferensi

Aspek lain seperti kualitas kode, maintainability, readability, maupun kepuasan pengguna tidak dievaluasi pada penelitian ini.

---

## 6.2 Research Limitations

Selain ruang lingkup penelitian, terdapat beberapa keterbatasan yang perlu diperhatikan dalam menginterpretasikan hasil penelitian.

### Limitation 1

Penelitian hanya menggunakan satu model AI sehingga hasil penelitian tidak secara langsung menggambarkan performa strategi pada model AI lainnya.

---

### Limitation 2

Eksperimen dilakukan menggunakan benchmark software engineering sehingga hasil penelitian mungkin berbeda apabila diterapkan pada proyek perangkat lunak nyata dengan karakteristik yang lebih kompleks.

---

### Limitation 3

Biaya inferensi dihitung berdasarkan penggunaan token dan estimasi biaya API. Nilai tersebut dapat berubah mengikuti kebijakan harga penyedia layanan AI.

---

### Limitation 4

Waktu eksekusi dapat dipengaruhi oleh kondisi jaringan maupun latensi layanan AI. Untuk meminimalkan pengaruh tersebut, seluruh eksperimen dijalankan menggunakan konfigurasi lingkungan yang identik.

---

### Limitation 5

Penelitian berfokus pada evaluasi strategi orkestrasi AI Agent dan tidak mengevaluasi kualitas algoritma internal maupun kemampuan reasoning dari model AI yang digunakan.

---

# 7. Expected Research Contribution

Penelitian ini diharapkan memberikan kontribusi baik pada aspek akademik maupun praktis.

## 7.1 Academic Contribution

1. Menyediakan evaluasi komparatif mengenai strategi orkestrasi AI Agent menggunakan desain eksperimen yang terkontrol.

2. Menawarkan kerangka evaluasi trade-off antara efektivitas, efisiensi, dan biaya inferensi pada tugas software bug fixing.

3. Menjadi referensi bagi penelitian lanjutan pada bidang AI Software Engineering dan Multi-Agent Software Engineering.

---

## 7.2 Practical Contribution

1. Memberikan panduan dalam memilih strategi orkestrasi AI Agent sesuai kebutuhan implementasi.

2. Membantu pengembang memahami konsekuensi penggunaan strategi tertentu terhadap keberhasilan penyelesaian bug, waktu eksekusi, dan biaya inferensi.

3. Menyediakan desain eksperimen yang dapat direproduksi untuk penelitian selanjutnya.

---

# 8. Research Method

## 8.1 Research Paradigm

Penelitian ini menggunakan paradigma **kuantitatif eksperimental** (quantitative experimental research) dengan pendekatan **comparative experimental study**.

Pendekatan ini dipilih karena tujuan utama penelitian bukan untuk mengembangkan model Artificial Intelligence baru, melainkan untuk mengevaluasi dan membandingkan performa beberapa strategi orkestrasi AI Agent pada lingkungan eksperimen yang terkontrol.

Seluruh strategi akan diuji menggunakan dataset, model AI, repository, prompt dasar, parameter inferensi, serta lingkungan eksekusi yang identik sehingga perbedaan hasil eksperimen diharapkan hanya dipengaruhi oleh strategi orkestrasi yang diterapkan.

---

## 8.2 Research Design

Penelitian menggunakan desain **Controlled Experiment**.

Pada desain ini, seluruh variabel yang berpotensi memengaruhi hasil eksperimen dikendalikan sehingga hanya variabel bebas yang berubah selama proses pengujian.

Variabel bebas pada penelitian ini adalah strategi orkestrasi AI Agent.

Setiap strategi akan menjalankan kumpulan issue yang sama secara independen. Hasil dari setiap eksperimen kemudian dievaluasi menggunakan Evaluation Engine dan dianalisis melalui Metrics Collector.

---

## 8.3 Experiment Workflow

Alur penelitian secara umum terdiri atas beberapa tahapan sebagai berikut.

1. Menyiapkan benchmark software engineering.
2. Memilih issue yang akan digunakan pada eksperimen.
3. Menjalankan salah satu strategi orkestrasi AI Agent.
4. Menghasilkan patch perangkat lunak.
5. Melakukan evaluasi menggunakan Evaluation Engine.
6. Menyimpan hasil eksperimen.
7. Mengulangi proses untuk seluruh issue dan seluruh strategi.
8. Mengolah hasil menggunakan Metrics Collector.
9. Melakukan analisis trade-off.
10. Menarik kesimpulan penelitian.

---

## 8.4 Experiment Environment

Seluruh eksperimen dijalankan menggunakan lingkungan yang identik.

Komponen utama lingkungan eksperimen meliputi:

| Component | Description |
|-----------|-------------|
| Dataset | SWE-bench Lite / SWE-bench Verified (tentatif) |
| Programming Language | Python 3.12+ |
| Agent Framework | LangGraph (tentatif) |
| LLM Provider | OpenRouter / OpenAI Compatible API (tentatif) |
| Execution Environment | Docker |
| Evaluation | SWE-bench Harness |
| Data Analysis | Pandas |
| Visualization | Matplotlib |

Pemilihan teknologi bersifat implementatif dan tidak memengaruhi metodologi penelitian. Apabila pada tahap implementasi terdapat perubahan teknologi, perubahan tersebut tidak mengubah desain eksperimen selama variabel penelitian tetap dipertahankan.

---

# 9. Research Variables

## 9.1 Independent Variable

Variabel bebas pada penelitian ini adalah **Strategi Orkestrasi AI Agent**.

Strategi yang dibandingkan terdiri dari tiga perlakuan eksperimen.

| Code | Strategy | Description |
|------|----------|-------------|
| S1 | Direct Execution Strategy | Executor menerima issue secara langsung tanpa Planner maupun Reviewer |
| S2 | Planning-based Strategy | Planner menghasilkan Planning Document yang digunakan Executor |
| S3 | Planning and Review Strategy | Planner, Executor, dan Reviewer bekerja secara berurutan |

---

## 9.2 Dependent Variables

### A. Effectiveness

Variabel efektivitas menunjukkan tingkat keberhasilan strategi dalam menyelesaikan tugas bug fixing.

Indikator pengukuran:

- Build Success Rate
- Test Pass Rate

---

### B. Efficiency

Variabel efisiensi menunjukkan sumber daya yang digunakan selama proses penyelesaian tugas.

Indikator pengukuran:

- Total Execution Time
- Average Execution Time
- Inference Count

---

### C. Inference Cost

Variabel biaya inferensi menunjukkan sumber daya komputasi AI yang digunakan.

Indikator pengukuran:

- Prompt Tokens
- Completion Tokens
- Total Tokens
- Estimated Cost (opsional)

---

## 9.3 Control Variables

Agar eksperimen berlangsung secara adil, seluruh strategi dijalankan menggunakan konfigurasi yang identik.

Variabel kontrol meliputi:

- Model AI
- Dataset
- Repository
- Issue
- Prompt dasar
- Temperature
- Max Tokens
- Docker Environment
- Evaluation Engine
- Hardware
- Operating System

---

## 9.4 Moderating Variable

Penelitian ini tidak menggunakan variabel moderasi.

---

## 9.5 Mediating Variable

Penelitian ini tidak menggunakan variabel mediasi.

---

# 10. Expected Research Contribution

## Academic Contribution

Penelitian ini diharapkan memberikan kontribusi terhadap perkembangan AI Software Engineering melalui penyediaan kerangka evaluasi strategi orkestrasi AI Agent yang dilakukan secara terkontrol dan dapat direproduksi.

Selain itu, penelitian ini memperluas kajian mengenai AI Coding Agent dengan memfokuskan evaluasi pada strategi orkestrasi, bukan pada perbandingan model Artificial Intelligence.

---

## Practical Contribution

Penelitian ini diharapkan dapat membantu pengembang perangkat lunak dalam memilih strategi orkestrasi AI Agent yang sesuai berdasarkan kebutuhan implementasi.

Hasil penelitian juga dapat dijadikan referensi dalam pengembangan AI Coding Assistant maupun Multi-Agent Software Engineering pada lingkungan industri maupun penelitian lanjutan.

---

# 11. Current Research Progress

Seluruh komponen metodologi penelitian telah diselesaikan dan dikunci (LOCKED) sebelum tahap implementasi dimulai.

| Component | Status |
|-----------|--------|
| Research Title | ✅ LOCKED |
| Research Gap | ✅ LOCKED |
| Research Questions | ✅ LOCKED |
| Research Objectives | ✅ LOCKED |
| Research Method | ✅ LOCKED |
| Research Variables | ✅ LOCKED |
| Experiment Architecture | ✅ LOCKED |
| Planner Specification | ✅ LOCKED |
| Executor Specification | ✅ LOCKED |
| Reviewer Specification | ✅ LOCKED |
| Evaluation Engine Specification | ✅ LOCKED |
| Experiment Controller Specification | ✅ LOCKED |
| Metrics Collector Specification | ✅ LOCKED |

Tahap penelitian selanjutnya adalah implementasi arsitektur eksperimen sesuai dengan spesifikasi yang telah ditetapkan pada dokumen ini.

---

# Part 1 Summary

Part 1 mendefinisikan dasar konseptual penelitian, meliputi latar belakang, research gap, research question, tujuan penelitian, ruang lingkup, metodologi, serta variabel penelitian.

Dokumen ini menjadi acuan utama dalam penyusunan proposal penelitian, implementasi sistem, hingga proses analisis hasil eksperimen.

Seluruh keputusan yang tercantum pada Part 1 telah memasuki status **Design Freeze (LOCKED)** dan menjadi dasar bagi penyusunan Part 2 yang akan membahas arsitektur eksperimen beserta spesifikasi setiap komponen sistem secara rinci.

---

