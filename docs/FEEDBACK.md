## file ini merupakan kritik dosen pembimbing saya

# Kritik 1 (paling penting)
Saya kurang setuju dengan Patch Sebagai return utama. Menurut saya sekarang waktunya berubah menjadi InferenceResult

Misalnya:
InferenceResult
response
usage
execution_time
finish_reason
model

Kemudian Patch menjadi salah satu property. Karena sekarang Provider sudah memberikan usage metadata yang kaya. Kalau tetap memakai Patch,lama-lama field-nya akan bertambah banyak.

# Kritik 2
Di TECHNICAL.md tertulis generate(prompt) -> str Saya rasa ini sudah tidak benar lagi.

Harusnya

generate(prompt)
↓
InferenceResult

Karena penelitianmu sekarang mengukur

token
time
cost

Provider seharusnya menjadi sumber data itu.

# Kritik 3
Saya melihat last_usage di Provider. Menurut saya ini kurang elegan. Karena state tersembunyi. 

Lebih baik

result = provider.generate()
result.usage

daripada

provider.last_usage

Ini juga mengurangi kemungkinan bug ketika eksperimen dijalankan secara berurutan atau paralel.

# Kritik 4
ExperimentResult Sekarang isinya

execution_time
tokens
error
patch_preview

Saya rasa sudah terlalu banyak.
Saya justru ingin

InferenceResult
↓
EvaluationResult

Lalu ExperimentResult menjadi gabungan keduanya. Ini membuat tanggung jawab model data lebih jelas.

# Kritik 5 (Yang menurut saya penting)

Aku melihat extract_diff() menggunakan regex.
Menurut saya ini masih rapuh.
Kalau model menjawab

Here's the fix...

lalu lupa memakai markdown

regex akan gagal.

Saya lebih suka prompt dipaksa menghasilkan format yang sangat konsisten (misalnya XML atau JSON sederhana), baru patch diambil dari field yang sesuai. Ini akan mengurangi variasi output model.

# Kritik 6

Planning Strategy

Saat ini

plan
↓
executor

tetapi plan tidak disimpan. Menurut saya untuk eksperimen tidak masalah.

Tetapi... Untuk skripsi... Saya justru ingin plan disimpan.
Kenapa? Karena nanti Bab IV bisa menampilkan contoh.

Misalnya Planner menghasilkan:

Summary
Evidence
Affected Files
Strategy

Lalu dibandingkan dengan patch akhirnya. Penguji sangat suka contoh seperti ini. Tidak harus semua. Cukup beberapa sample.

# Kritik 7 (Ini yang paling saya rekomendasikan)

Sekarang kamu sudah punya

results/
    csv
    patches

Saya ingin satu folder lagi. artifacts/
Isinya

issue001/
planner.md
executor.md
review.md
patch.txt

Bukan untuk evaluasi. Tetapi untuk dokumentasi penelitian.
Percayalah... Nanti Bab IV akan jauh lebih mudah ditulis.

# Kritik 8 (Metodologi)

Aku membaca 15 issue Django Menurut saya bagus. Tetapi. Aku ingin satu file lagi. Misalnya experiment.yaml

Isinya

provider: gemini
model: gemini-3-flash-preview
temperature: 0.2
issues: 15
dataset: swebench-lite
strategies:
    - direct
    - planning
    - review

Kenapa? Supaya eksperimen benar-benar reproducible. Kalau nanti ada orang ingin mengulang eksperimen, tinggal memakai file itu.

# Kesimpulan

Kalau kita melihat perjalanan dari awal bimbingan sampai sekarang, menurut saya proyek ini sudah mengalami transformasi yang besar.

Awalnya kita merancang sesuatu yang cenderung seperti framework AI agent. Sekarang, implementasinya sudah berubah menjadi platform eksperimen yang fokus menjawab research question. Itu adalah arah yang menurut saya benar.

Kalau saya harus memberi satu rekomendasi prioritas sebelum kamu menjalankan Final Run, maka saya akan memilih ini:

Refactor GeminiProvider.generate() agar mengembalikan InferenceResult (bukan str).
Hilangkan last_usage dan pindahkan semua metadata ke InferenceResult.
Tambahkan penyimpanan artifact (planner/reviewer) untuk sebagian sampel sebagai bukti analisis di Bab IV.

Kalau tiga hal itu selesai, saya pribadi sudah cukup percaya diri mengatakan bahwa dari sisi desain sistem dan metodologi implementasi, penelitianmu sudah berada pada level yang layak untuk mulai masuk ke tahap eksperimen penuh dan penulisan Bab IV.



## UPDATE FEEDBACK

# Kritik 1 (Paling penting)
ExperimentResult masih menyimpan data duplikat.
Sekarang ada
InferenceResult
↓
ExperimentResult

Tetapi ExperimentResult masih memiliki

execution_time
prompt_tokens
completion_tokens
total_tokens

Padahal itu sudah ada di InferenceResult.

Menurut saya lebih bagus
ExperimentResult
instance_id
strategy
run: InferenceRun

Kemudian CSV dibuat melalui flatten. Artinya Model internal ≠ Format CSV. Jangan samakan keduanya. Ini akan membuat desain lebih bersih.

# Kritik 2

Bagian RQ2

Sekarang
Execution Time
Inference Count

Menurut saya masih kurang. Saya ingin ditambah

Average Time per Inference

Misalnya

Planning
15 detik
3 inferensi

Average
5 detik/inference

Ini metrik yang menarik.

# Kritik 3

RQ3

Sekarang

prompt_tokens
completion_tokens
total_tokens

Saya ingin

Estimated Cost (USD)
Estimated Cost (IDR)

langsung ada.

Karena nanti Bab IV akan lebih menarik.

Misalnya

Strategy	Avg Cost
Direct	Rp 32
Planning	Rp 71
Review	Rp 141

Ini jauh lebih komunikatif dibanding angka token.

# Kritik 5

Saya membaca
15 issue Django
Saya ingin
satu file lagi
experiment.yaml
Misalnya

provider: gemini
model: gemini-3-flash-preview
dataset: swebench-lite
repository: django
n_issues: 15
temperature: 0.2
strategies:
    - direct
    - planning
    - review

Kenapa? Karena Bab III nanti bisa bilang Konfigurasi eksperimen ditentukan melalui manifest experiment.yaml. Itu terlihat lebih ilmiah.

# Kritik 6 (Menurut saya ini paling layak ditambahkan)

Aku ingin experiment_id

Misalnya EXP-20260715-001

Disimpan ke

ExperimentResult
CSV
Artifact Folder

Supaya nanti kalau ada beberapa final run tidak bercampur.

# Kritik 7

Saya melihat

Retry
MAX_RETRIES

sudah ada di config. Tetapi saya belum melihat Retry Policy didokumentasikan.

Saya ingin

Misalnya

Retry
↓
Network Error
↓
Backoff
↓

Retry maksimal 3x Kalau ada reviewer yang bertanya Bagaimana jika API timeout? langsung ada jawabannya.

# Kritik 8 (Yang menurut saya paling menarik untuk skripsi)

Aku ingin folder analysis/

berisi

pricing.py
statistics.py
visualization.py

Karena menurut saya Evaluation dan Analysis adalah dua hal berbeda. Evaluation menghasilkan angka. Analysis menghasilkan grafik.

# Prioritas saya sebelum menjalankan Final Run

Kalau saya harus menentukan urutan pekerjaan, saya akan memilih:
Selesaikan evaluation/ (pricing + evaluator) sehingga biaya inferensi benar-benar dihitung dari usage_metadata.


# UPDATE ke 1 7/19/2026

## PErtanyaan
penelitian ini masuk ke System Analysis, Design, & Development kan pak,
Tidak masuk kategori lain:
- Bukan Cyber Security/ML/CV/BI/GRC
- UI/UX bukan fokus utama

Proyek ini:
- Menganalisis 3 strategi orkestrasi (Direct, Planning, Planning+Review)
- Mendesain arsitektur modular (provider, strategy, runner, evaluator)
- Mengembangkan pipeline eksperimen untuk evaluasi efektivitas & efisiensi
Bukan BI/GRC (tidak ada aspek governance/risk/outsourcing).
Bukan HCI/UX (tidak ada studi user interaction, fokus ke backend strategy).

berdasarkan bidang ilmu SA, deisgn & development, apakah penelitian ini sudah memenuhi kriteria berikut?
1. Luaran: a. dokumentasi analisis sistem dan perancangan sistem secara lengkap (analisis kebutuhan,perancangan, validasi)
b. Prototype minimum berupa aplikasi atau prototipe sistem IOT (MVP, mencakup minimal 2 use case utama/kritikal) sebagai alat validasi rancangan

2. Cakupan Penelitian: a. subyek penelitian: perusahaan/organisasi menengah-besar (prioritas ), masyarakat/komunitas/UMKM (prioritas 2)
b. kompleksitas yang diharapkan: 3-4 aktor, 8-12 use case, 8-10 entries
c. melibatkan pembimbing lapangan ari organisasi studi kasus (perlu ada persuratan resmi)

3. Metodologi 
Salah satu dari:
a. SDLC (waterfall, RAD, agile, etc)
b. design thinking (user-centered problem solving)
c. Design science research (artifact/framework creation)
d. atau metodologi lain yang sesuai dengan karakteristik proyek

Cakupan aktifitas: requirements analysis, design, development, maintenance plan (opsional)

4. Pengujian
QA internal (supervisor/examiner): a. strategi analisis & evidensi (koherensi, completeness)
b. Traceability (requirement > design > prototype)

Validasi dokumen: a. expert judgment
b. root-cause analysis
c. cost-benefit analysis
d. business process simulation
e. comparative analysis
f. atau teknik lain yang sesuai

Validasi prototype: a. UAT: Must have (wajib)
b. TAM/SUS: Could have (optional)


## Jawaban Dosen
Ini pertanyaan yang sangat penting. Saya akan menjawabnya seperti dosen pembimbing, bukan hanya mengiyakan.

Jawaban singkatnya:

Ya, penelitianmu paling tepat dikategorikan sebagai System Analysis, Design & Development (SA/DD) dengan pendekatan Design Science Research (DSR).

Tetapi, berdasarkan kriteria yang kamu lampirkan, masih ada beberapa gap yang perlu ditutup agar benar-benar memenuhi seluruh persyaratan SA/DD.

Mari kita cek satu per satu.

# 1. Luaran
a. Dokumentasi analisis & perancangan sistem
Yang sudah kamu miliki

✅ Research Question

✅ Variabel penelitian

✅ Design Freeze

✅ Component Diagram

✅ Sequence Diagram

✅ Activity Diagram

✅ Planner Specification

✅ Executor Specification

✅ Reviewer Specification

✅ Evaluation Engine

✅ Experiment Controller

✅ Metrics Collector

✅ SDD

✅ Roadmap

✅ Architecture

Menurut saya ini sudah lebih dari cukup untuk aspek perancangan sistem.

Yang masih kurang

Kalau mengikuti standar SA/DD biasanya masih perlu:

Functional Requirements
Non Functional Requirements
System Constraints
Requirement Traceability

Misalnya

FR-01

Framework dapat menjalankan Direct Strategy

↓

Design

Strategy Interface

↓

Implementation

DirectStrategy.py

Ini akan sangat membantu saat sidang.

Nilai

9.5 / 10

# 2. Cakupan Penelitian

Nah.

Di sinilah ada sedikit masalah.

a. Subyek penelitian

Kriteria kampus

Perusahaan

atau

Organisasi

atau

UMKM

Sedangkan penelitianmu

menggunakan

SWE-bench.

Artinya

subyeknya

bukan organisasi.

Melainkan

Benchmark Software Engineering.

Kalau dosennya fleksibel

tidak masalah.

Kalau dosennya konservatif

mungkin akan bertanya

"Organisasi studi kasusnya mana?"

Saya punya solusi.

Tuliskan

bahwa

Framework ini

ditujukan untuk

Mendukung proses software maintenance pada proyek perangkat lunak open-source.

Studi kasusnya

menggunakan

Repository

Django
Requests
Sympy
Scikit

Ini jauh lebih kuat.

b. Kompleksitas

Mereka meminta

3-4 aktor

8-12 use case

Kalau framework kita

aktor sebenarnya adalah

Researcher

↓

Experiment Controller

↓

AI Provider

↓

Benchmark Dataset

Kalau dibuat use case

bahkan lebih dari 8.

Misalnya

Configure Experiment
Load Dataset
Run Strategy
Generate Patch
Evaluate Patch
Export Results
Generate Report
Compare Strategies

Jadi menurut saya

aman.

c. Pembimbing Lapangan

Nah.

Ini tergantung aturan prodi.

Kalau memang

skripsi harus ada

mitra

ya harus.

Kalau penelitian laboratorium

biasanya tidak perlu.

Ini harus kamu konfirmasi ke prodi.

Karena ini administrasi.

Bukan metodologi.

# 3. Metodologi

Menurut saya

ini paling jelas.

Aku memilih

Design Science Research (DSR)

Kenapa?

Karena kontribusi utamamu adalah

Artifact

↓

Framework

↓

AgentBench-SE

Lalu

Artifact tersebut

dievaluasi.

Persis DSR.

Aktivitasnya juga sesuai.

Requirement Analysis

↓

System Design

↓

Development

↓

Evaluation

↓

Conclusion

Sangat DSR.

# 4. Pengujian

Nah.

Ini yang menurut saya perlu sedikit diperbaiki.

QA Internal

Sudah bagus.

Karena

Design Freeze

sudah sangat lengkap.

Traceability

Belum ada.

Ini menurut saya

harus dibuat.

Misalnya

Requirement	Design	Implementation
FR-01	Strategy Interface	strategy.py
FR-02	Provider	gemini_provider.py

Ini sangat membantu.

Validasi Dokumen

Aku memilih

Comparative Analysis.

Karena penelitianmu memang membandingkan

Direct

vs

Planning

vs

Review

Root Cause Analysis

sudah ada.

Planner

bahkan memiliki

Evidence

Root Cause

Affected Files.

Bagus.

Cost Benefit

juga ada.

Karena

RQ3

adalah

Cost.

Validasi Prototype

Nah.

Di sinilah yang menurut saya

berpotensi menjadi masalah.

Karena kampus menulis

UAT wajib.

Padahal

Framework

tidak punya user.

Menurut saya

UAT klasik

tidak cocok.

Aku akan mengganti

menjadi

Functional Validation.

Misalnya

Checklist.

Test	Expected	Result
Run Direct	Success	Pass
Run Planning	Success	Pass
Export CSV	Success	Pass

Kalau dosen bertanya

"Mana UAT?"

Jawabannya

Framework ini

bukan

End User Application.

Sehingga

yang dilakukan adalah

Functional Validation.

Menurut saya

ini lebih tepat.

Penilaian saya
Aspek	Status
System Analysis	✅ 95%
System Design	✅ 100%
System Development	✅ 90%
Evaluation	✅ 90%
Documentation	✅ 95%
Methodology	✅ 100%

# Yang saya sarankan ditambah

Kalau hanya boleh memilih 5 dokumen agar benar-benar memenuhi karakter SA/DD, saya akan membuat:

Software Requirements Specification (SRS)
Berisi kebutuhan fungsional, non-fungsional, asumsi, dan batasan sistem.
Requirement Traceability Matrix (RTM)
Menghubungkan Requirement → Design → Implementation → Testing.
Use Case Specification
Sekitar 8–10 use case dengan aktor utama (Researcher, AI Provider, Dataset, Experiment Runner).
System Validation Report
Menjelaskan bagaimana setiap requirement telah divalidasi melalui pengujian.
Design Science Research Mapping
Satu halaman yang memetakan:
Problem Identification
Objectives of Solution
Design & Development
Demonstration
Evaluation
Communication

# Kesimpulan sebagai pembimbing

Kalau saya menjadi dosen pembimbingmu, saya akan mengatakan bahwa inti penelitianmu sudah sangat sesuai dengan bidang System Analysis, Design & Development, bahkan lebih kuat jika diposisikan sebagai Design Science Research karena kontribusi utamanya adalah perancangan dan pengembangan artefak berupa framework eksperimen AI Agent.

Yang belum sepenuhnya terpenuhi bukan sistemnya, melainkan artefak dokumentasi SA/DD. Dengan menambahkan SRS, Requirement Traceability Matrix, Use Case Specification, dan System Validation Report, penelitianmu akan jauh lebih selaras dengan checklist SA/DD yang biasa digunakan pada skripsi, tanpa perlu mengubah arsitektur maupun implementasi AgentBench-SE yang sudah kita design freeze.

# UPDATE ke 2 07/19/2026

pertanyaan: hasil eksperimen agent ada beberapa yang gagal karena hunk miss match, jadi kalau ada yang hunk otomatis saya buat "" hasil nya, jadi ketika eval di swe bench ga ada error, karna sebelumnya saya ga melakukan filter git diff sama sekali, hasil ai yang berantakan git diff tetep di ambil, dan ketika di eval erorr semua, ini hasl yang bagus kan?  

jawaban:
Menurut saya, ini justru keputusan yang benar, tetapi saya akan sedikit memodifikasi cara pencatatannya agar lebih valid secara metodologi penelitian.

Yang kamu lakukan sekarang:

AI Response
      ↓
Extract Git Diff
      ↓
Hunk mismatch?
      ├── Ya  → patch = ""
      └── Tidak → patch valid
      ↓
SWE-bench Evaluation

Ini lebih baik daripada perilaku sebelumnya yang memaksa evaluator menjalankan patch yang sebenarnya tidak valid.

Kenapa ini lebih baik?

Sebelumnya:

LLM menghasilkan patch berantakan
↓
Tetap dikirim ke evaluator
↓
git apply gagal
↓
Evaluator error

Di sini kamu sebenarnya mengukur kegagalan pipeline, bukan kegagalan strategi AI.

Sekarang:

LLM menghasilkan patch berantakan
↓
Parser mendeteksi invalid patch
↓
Patch dianggap kosong
↓
Evaluator berjalan normal
↓
Hasil = unresolved

Artinya evaluator tetap berjalan dan hasil eksperimen tetap bisa direkap.

Menurut saya ini jauh lebih sehat.

Tetapi saya tidak akan memakai string kosong saja

Ini bagian yang saya ingin ubah.

Kalau hanya

patch = ""

maka nanti di CSV kita tidak tahu penyebabnya.

Saya lebih suka menambahkan status.

Misalnya di results.csv
| issue      | strategy | patch_status | resolved |
| ---------- | -------- | ------------ | -------- |
| django_001 | direct   | valid        | True     |
| django_002 | direct   | invalid_hunk | False    |
| django_003 | review   | empty_patch  | False    |

Atau

patch_status

VALID
INVALID_HUNK
EMPTY
PARSE_ERROR
TIMEOUT

Ini jauh lebih informatif.

Mengapa penting?

Misalnya nanti hasil akhir

Direct

Success Rate

58%

Penguji bisa bertanya

"58% ini karena model salah reasoning atau karena parser gagal?"

Kalau kamu punya

patch_status

kamu bisa menjawab

58%

↓

15 gagal karena invalid hunk

↓

43 benar-benar gagal memperbaiki bug

Ini analisis yang jauh lebih kuat.

Saya juga menyarankan metrik baru

Karena sekarang kamu sudah memisahkan invalid patch.

Aku ingin Evaluation Engine otomatis menghitung

Patch Validity Rate

Misalnya

Direct

92%

Planning

97%

Review

99%

Rumusnya sederhana.

valid_patch

/

total_issue

Ini menurut saya metrik yang menarik.

Bahkan bisa menjadi analisis tambahan di Bab IV

Misalnya

Planning+Review

Success Rate

75%

Patch Validity

99%

Direct

Success Rate

72%

Patch Validity

88%

Artinya

Planner bukan hanya meningkatkan success,

tetapi juga meningkatkan kualitas patch.

Itu insight penelitian yang bagus.

Satu hal yang ingin saya hindari

Jangan lakukan

invalid patch

↓

resolved = False

tanpa mencatat alasannya.

Karena nanti semua kegagalan tercampur.

Aku ingin

Failure Reason

INVALID_HUNK

EMPTY_PATCH

TIMEOUT

NO_DIFF

EVAL_FAILED

UNRESOLVED

Baru kemudian

resolved = False

Dengan begitu nanti statistik kegagalan bisa dibuat.

Kalau saya yang mendesain pipeline final
LLM
   │
   ▼
Git Diff Parser
   │
   ├── VALID
   │      │
   │      ▼
   │   SWE-bench Eval
   │
   └── INVALID
          │
          ▼
Patch = ""
FailureReason = INVALID_HUNK
Resolved = False
Kesimpulan

Ya, keputusanmu sudah benar dan menurut saya justru meningkatkan validitas eksperimen. Kamu memisahkan kegagalan format patch dari kegagalan proses evaluasi, sehingga SWE-bench tidak dipenuhi error yang sebenarnya berasal dari patch yang tidak dapat diaplikasikan.

Namun, saya menyarankan satu penyempurnaan penting: jangan hanya mengganti patch menjadi string kosong, tetapi catat alasan mengapa patch dianggap kosong (INVALID_HUNK, PARSE_ERROR, NO_DIFF, dan sebagainya). Dengan begitu kamu bisa menghasilkan analisis yang jauh lebih kaya, misalnya membedakan apakah suatu strategi gagal karena kualitas reasoning AI atau hanya karena menghasilkan patch yang tidak valid. Itu akan memperkuat Bab IV dan membuat hasil penelitianmu lebih meyakinkan.