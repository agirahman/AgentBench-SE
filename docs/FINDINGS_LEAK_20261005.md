# FINDINGS — Vektor Kebocoran Solusi & Rencana Mitigasi (2026-10-05)

**Status:** VERIFIKASI ULANG SELESAI — beberapa klaim awal **DIKOREKSI**.
**Konteks:** ditemukan saat memantau batch-5 sweep `EXP-20261004-034`.
**Eksperimen terdampak:** `EXP-20261004-034` (batch 1–4 selesai, 120 run; batch-5 dihentikan).

---

## 1. Ringkasan yang SUDAH diverifikasi

| # | Vektor | Status | Bukti |
|---|---|---|---|
| 1 | **Git refs masa depan** | ✅ **TERBUKTI DIEKSPLOITASI** | `psf__requests-2674`: 159 tag, **64 tag masa depan**; `git show v2.8.0:requests/adapters.py` mengembalikan konten; commit list memuat `5ec7576f Catch and wrap ClosedPoolError` = fix issue itu; patch kita memuat `ClosedPoolError` |
| 2 | **Unduh dari internet (GitHub)** | ✅ **TERBUKTI DIEKSPLOITASI** | `matplotlib-23563`: patch kita memuat **verbatim** `zs = cbook._to_unmasked_float_array(zs).ravel()` = baris fix upstream; `seaborn-3190` mengunduh `pull/3190.diff`; `django-11019` mengunduh `raw.githubusercontent.com/django/django/3.0/...` |
| 3 | **Artefak eksperimen lama** | ⚠️ **DICOBA, run dihentikan** | `sympy-11870` menjalankan `print(d['model_patch'])` dari `results/EXP-20260824-005/predictions/review.jsonl` → file `_hits.txt` di root proyek memuat `model_patch`. Run **tidak selesai** (dihentikan), jadi tidak ada run lengkap yang terdampak — tapi vektornya nyata |
| 4 | ~~site-packages (paket versi fixed)~~ | ❌ **BUKAN KEBOCORAN** (klaim awal saya SALAH) | `read_file` ke path site-packages **dialihkan ke repo** oleh `_normalize_tool_path` (suffix-matching) atau **ditolak**. Diuji langsung: `.../site-packages/django/forms/widgets.py` → `django\forms\widgets.py` (file repo sendiri). `run_tests` yang membuka site-packages mengembalikan **output KOSONG** |
| 5 | ~~Baca checkout instance lain~~ | ❌ **BUKAN KEBOCORAN** (klaim awal saya berlebihan) | Hanya **1** pembacaan lintas-checkout berhasil, hasilnya **kosong**. Tidak ada konten fix yang kembali |

**Koreksi penting:** `django` **tidak terpasang** di sistem maupun venv (diverifikasi). Klaim awal saya "agent membaca Django 3.0 dari site-packages" **tidak benar** — pembacaan itu dialihkan ke file repo sendiri.

---

## 2. Mengapa `--network=none` saja TIDAK cukup (temuan riset)

Dari OpenHands ProgramBench (`benchmarks/programbench/README.md`):

> `--network none` memblokir SDK dari menjangkau agent-server karena port mapping butuh network interface. `docker network create --internal` juga memblokir `-p`. Jawaban yang benar = **filtering egress di dalam container (iptables + `CAP_NET_ADMIN`)** — masih future work.

Dari SWE-bench PR #662 (belum di-merge) — proposal isolasi jaringan resmi.

**Implikasi untuk kita:** harness tetap di host (butuh jaringan untuk LLM API), hanya `run_tests` yang perlu diisolasi. Jadi arsitektur kita berbeda dari OpenHands dan `--network=none` tetap bisa dipakai **untuk container `run_tests`**.

---

## 3. Referensi industri (yang bisa dikutip di skripsi)

| Sumber | Isi | Relevansi |
|---|---|---|
| [SWE-bench #465](https://github.com/SWE-bench/SWE-bench/issues/465) | "Repo State Loopholes During Agentic Evaluation" — 4 trajektori nyata (Claude 4 Sonnet, Qwen3-Coder 480B) memakai `git log --all` | Persis kasus kita |
| [SWE-bench PR #471](https://github.com/SWE-bench/SWE-bench/pull/471) | **Implementasi referensi** `git_clone_timesafe` — solusi resmi | Adopsi |
| [Harbor PR #263](https://github.com/harbor-framework/harbor-datasets/pull/263) | **Dampak terukur:** sebelum perbaikan skor 0,93–0,97 (92–95% pakai internet/git); sesudah 0,61–0,64 | Bukti bahwa kebocoran ini menggelembungkan skor ~30 poin |
| [SWE-bench Pro OSS #93](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93) | "100% success rate exploiting these scenarios on all images" — **tidak butuh internet** | Menegaskan git sanitization wajib |
| [SWE-bench #640](https://github.com/SWE-bench/SWE-bench/issues/640) | Kebocoran lewat cache conda (masih terbuka di SWE-bench) | Perlu dicek untuk kita |
| [SWE-bench #669](https://github.com/SWE-bench/SWE-bench/issues/669) | 9 instance Verified memuat solusinya di `problem_statement` | **Perlu audit soal kita sendiri** |
| [OpenAI Feb 2026](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) | OpenAI **berhenti** melaporkan skor SWE-bench Verified karena kontaminasi | Limitasi yang harus ditulis |

**Catatan penting:** SWE-bench/SWE-agent/OpenHands **tidak** memblokir jaringan secara default. Yang mereka perbaiki adalah **git history**. Jadi git sanitization = standar minimum yang diterima; pemblokiran jaringan = kita melampaui standar.

---

## 4. Implementasi referensi: `git_clone_timesafe` (SWE-bench PR #471)

```bash
git clone -o origin <branch> --single-branch <url> <dir>
git reset --hard <base_commit>
git remote remove origin
# hapus HANYA tag yang commit-date > base_commit
TARGET_TIMESTAMP=$(git show -s --format=%ct <base_commit>)
git tag -l | while read tag; do
  TAG_TIME=$(git show -s --format=%ct "$(git rev-list -n 1 "$tag")")
  if [[ $TAG_TIME -gt $TARGET_TIMESTAMP ]]; then git tag -d "$tag"; fi
done
git reflog expire --expire=now --all
git gc --prune=now --aggressive
# ASSERT: nol commit setelah base_commit (fail-closed)
AFTER_TIMESTAMP=$((TARGET_TIMESTAMP + 1))
COMMIT_COUNT=$(git log --oneline --all --after="@$AFTER_TIMESTAMP" | wc -l)
[ "$COMMIT_COUNT" -eq 0 ] || exit 1
```

**Dua detail krusial:**
1. **JANGAN hapus semua tag** — instance `pytest-5840` hanya bisa diselesaikan lewat tag **masa lalu**. Pakai **batas waktu**, bukan hapus-semua.
2. **Assertion di akhir** — pola fail-closed; kalau masih bocor → gagal, bukan lanjut diam-diam.

---

## 5. State checkout kita (terverifikasi)

| Kategori | Jumlah | Detail |
|---|---|---|
| Shallow | 49 | 0 tag, 0 refs/remotes; **punya remote** (perlu dihapus) |
| **Non-shallow** | 4 | 3 = HEAD dummy (`0000…`/`1111…`/`2222…`, tidak berbahaya); **1 = `psf/requests @0be38a0`** → 2.781 commit masa depan, **159 tag (64 masa depan)**, 7 branch remote |

**Titik perbaikan utama: `psf/requests @0be38a0c37c59c4b66ce908731da15b401655113`**

---

## 6. Analisis biaya/manfaat opsi mitigasi

| Opsi | Menutup | Biaya | Pengaruh ke komparabilitas |
|---|---|---|---|
| **A. Sanitasi git saja** | Vektor #1 (git) | ~10 menit, gratis | Tidak ada |
| **B. A. + blok jaringan Python-level + filter perintah** | #1 + #2 (semua kasus teramati) | ~1–2 jam | Tidak ada (semantik Windows tetap) |
| **C. Full Docker Linux (`--network=none`)** | #1 + #2 + isolasi FS penuh | ~1–2 hari + re-run 50 issue (~$10) | ❌ **RUSAK** — batch 1–4 (Windows) vs 5+ (Linux) tidak sebanding; perintah `cd C:\`, `findstr`, `dir` gagal semua |
| **D. C + re-run SEMUA 50 issue di Docker** | Semuanya | ~$10 + 3–5 hari | Tidak ada (semua Linux) tapi mahal |

**Catatan teknis penting untuk Opsi C:** agent menjalankan perintah **Windows-style** (`cd C:\ & python -c ...`, `findstr`, `dir`). Container Linux akan menggagalkan hampir semua perintah ini → perilaku agent berubah fundamental → hasil tidak sebanding dengan batch 1–4 yang sudah dibayar ($7,37).

**Rekomendasi: Opsi B** — menutup kedua vektor yang **terbukti dieksploitasi**, tanpa merusak komparabilitas, dan sejalan dengan standar industri (SWE-bench melakukan sanitasi git; kita menambahkan blok jaringan).

---

## 7. Cakupan dampak (perlu re-run)

**Isu dengan bukti konten luar yang kembali:**
- `matplotlib__matplotlib-23563` (unduh patch upstream → patch kita 80% identik)
- `mwaskom__seaborn-3190` (unduh PR diff)
- `django__django-11019` (unduh file Django 3.0)
- `psf__requests-2674` (git history masa depan)

**Kandidat tambahan (git log listing — membocorkan pesan commit/pendekatan):**
`django-11001`, `django-11039`, `django-11283`, `matplotlib-18869`, `matplotlib-22711`,
`matplotlib-22835`, `matplotlib-23299`, `matplotlib-23964`, `psf-requests-2148`,
`scikit-learn-10949`

**Estimasi:** ~14 issue × 3 strategi = **42 run** perlu re-run (~$2–3).

---

## 8. Yang BELUM terverifikasi (jujur)

1. **Audit `problem_statement` kita sendiri** — apakah ada soal yang memuat solusinya (seperti SWE-bench #669)? **Belum diperiksa.**
2. **Cache paket** (SWE-bench #640) — apakah image/paket lokal memuat source pasca-base_commit? **Belum diperiksa.**
3. **Kontaminasi training data** — di luar kendali; ini **limitasi** yang harus ditulis, bukan bug yang bisa diperbaiki.
4. **Apakah ada vektor lain yang belum ditemukan** — probe regresi diperlukan untuk menaikkan keyakinan.

---

## 9. Rencana kerja (Opsi B)

| # | Langkah | Verifikasi |
|---|---|---|
| 1 | Sanitasi git semua 53 checkout (`git_clone_timesafe`-style) | `git rev-list --all --not HEAD` = **0** untuk semua; `git tag` future = 0 |
| 2 | Blok jaringan Python-level di `run_tests` (inject `sitecustomize.py` via PYTHONPATH) | Probe: `python -c "import urllib.request; urllib.request.urlopen(...)"` → **harus gagal** |
| 3 | Filter perintah: tolak `git fetch/clone/pull`, `pip download/install`, `curl`, `wget`, `Invoke-WebRequest` | Test unit |
| 4 | Preflight gate: sweep menolak start kalau sanitasi gagal | Test unit + jalankan gate |
| 5 | Probe regresi: uji semua vektor escape → harus gagal | Skrip probe |
| 6 | Re-run 14 issue terdampak | Verifikasi hasil |

**Catatan:** langkah 2 & 3 adalah **pertahanan berlapis**, bukan sandbox sejati. Batasnya harus didokumentasikan (mis. `certutil`, `bitsadmin` tidak tercakup filter). Ini konsisten dengan praktik industri — SWE-bench sendiri tidak memblokir jaringan sama sekali.
