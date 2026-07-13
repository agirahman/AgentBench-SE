# Appendix A

# Experimental Protocol & Execution Guidelines

---

# A1. Purpose

Dokumen ini mendefinisikan prosedur operasional standar (*Standard Operating Procedure / SOP*) yang digunakan selama pelaksanaan eksperimen.

Tujuan utama dokumen ini adalah:

- memastikan seluruh eksperimen dilakukan secara konsisten;
- menjaga validitas penelitian;
- meningkatkan reproduktibilitas;
- menjadi panduan implementasi Experimental Framework.

Seluruh eksperimen yang dilakukan pada penelitian ini harus mengikuti prosedur yang dijelaskan pada dokumen ini.

---

# A2. Experiment Preparation

Sebelum eksperimen dimulai, seluruh lingkungan eksperimen harus dipersiapkan menggunakan konfigurasi yang identik.

Tahapan persiapan meliputi:

1. Menyiapkan benchmark dataset.
2. Menyiapkan repository target.
3. Menyiapkan Docker Environment.
4. Menyiapkan AI Model.
5. Menyiapkan Prompt Template.
6. Menyiapkan Evaluation Engine.
7. Menyiapkan Metrics Collector.

Eksperimen tidak boleh dijalankan apabila salah satu komponen belum siap.

---

# A3. Benchmark Selection Protocol

## Objective

Menjamin seluruh strategi memperoleh issue dengan tingkat kesulitan yang sama.

## Procedure

1. Memilih benchmark yang telah ditentukan.
2. Memilih issue sesuai kriteria penelitian.
3. Menyimpan Issue ID.
4. Mengunci repository version.
5. Mengunci dependency version.

Issue yang telah dipilih harus digunakan oleh seluruh strategi.

Pergantian issue selama eksperimen tidak diperbolehkan.

---

# A4. Experiment Execution Protocol

## Objective

Menjamin seluruh strategi dijalankan menggunakan prosedur yang sama.

## Standard Workflow

```text
Load Issue

↓

Load Repository

↓

Load Strategy

↓

Run Strategy

↓

Generate Patch

↓

Evaluation Engine

↓

Metrics Collector

↓

Save Result
```

Seluruh strategi wajib mengikuti workflow tersebut.

Perbedaan hanya diperbolehkan pada workflow internal strategi.

---

# A5. Failure Handling Protocol

Apabila eksperimen mengalami kegagalan, status kegagalan harus dicatat.

Kategori kegagalan meliputi:

- Planning Failure
- Execution Failure
- Review Failure
- Build Failure
- Evaluation Failure
- Infrastructure Failure

Eksperimen tidak diperbolehkan mengubah hasil evaluasi secara manual.

---

# A6. Logging Protocol

Setiap eksperimen menghasilkan satu log.

Log minimal berisi:

- Timestamp
- Experiment ID
- Strategy
- Issue ID
- Repository
- AI Model
- Execution Time
- Token Usage
- Evaluation Status
- Error Message (jika ada)

Seluruh log disimpan menggunakan format yang konsisten.

---

# A7. Data Collection Protocol

Metrics Collector hanya mengumpulkan data.

Tidak diperbolehkan:

- mengubah hasil eksperimen;
- menghapus eksperimen yang gagal;
- memperbaiki data secara manual.

Seluruh data mentah (*raw data*) harus disimpan sebelum proses analisis statistik dilakukan.

---

# A8. Statistical Analysis Protocol

Tahapan analisis dilakukan setelah seluruh eksperimen selesai.

Urutan analisis sebagai berikut.

1. Data Cleaning.
2. Descriptive Statistics.
3. Distribution Analysis.
4. Comparative Analysis.
5. Trade-off Analysis.
6. Visualization.
7. Conclusion.

Analisis dilakukan terhadap tiga variabel utama:

- Effectiveness
- Efficiency
- Inference Cost

---

# A9. Threats to Validity Mitigation

Untuk menjaga validitas penelitian, beberapa potensi ancaman diidentifikasi beserta strategi mitigasinya.

| Threat | Mitigation |
|---------|------------|
| Perbedaan Model AI | Menggunakan model yang sama untuk seluruh strategi |
| Perbedaan Prompt | Menggunakan Prompt Template yang sama |
| Perbedaan Dataset | Menggunakan benchmark yang sama |
| Perbedaan Repository | Menggunakan repository dan commit yang sama |
| Variasi Lingkungan | Menggunakan Docker Environment yang identik |
| Bias Evaluasi | Menggunakan Evaluation Engine deterministik |

---

# A10. Reproducibility Checklist

Sebelum eksperimen dijalankan, seluruh item berikut harus dipastikan telah terpenuhi.

| Checklist | Status |
|-----------|--------|
| Benchmark telah dikunci | ☐ |
| Repository telah dikunci | ☐ |
| Commit telah dikunci | ☐ |
| AI Model telah ditentukan | ☐ |
| Prompt Template telah dikunci | ☐ |
| Parameter Inferensi telah dikunci | ☐ |
| Docker Environment telah siap | ☐ |
| Evaluation Engine telah siap | ☐ |
| Metrics Collector telah siap | ☐ |
| Logging aktif | ☐ |

Eksperimen hanya dapat dimulai apabila seluruh checklist telah terpenuhi.

---