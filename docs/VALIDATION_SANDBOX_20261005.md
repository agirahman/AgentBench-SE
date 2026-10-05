# VALIDASI ORCHESTRATOR — Mitigasi Sandbox (2026-10-05)

**Metode:** validasi independen lewat **jalur `run_tests` yang sesungguhnya** (bukan hanya unit test),
dijalankan oleh orchestrator setelah worker & reviewer selesai.

---

## 1. Apakah pekerjaan worker SESUAI brief?

**Ya — tidak melenceng.** Semua item brief A–F dikerjakan:

| Item brief | Status | Bukti |
|---|---|---|
| A. Skrip sanitasi git | ✅ | `tools/sanitize_git_history.py` — 50 checkout disanitasi, 0 remote, 0 future commit |
| B. sitecustomize + filter perintah | ✅ | `src/agents/sandbox/sitecustomize.py`, `tools.py:444-478` |
| C. Preflight gate | ✅ | `readiness_report.py` → 11 check (naik dari 9) |
| D. Probe regresi | ✅ | `tools/probe_sandbox_escape.py` — 6 probe |
| E. Test pytest | ✅ | `tests/test_sanitize_git_history.py` + `tests/test_network_block.py` |
| F. Verifikasi akhir | ✅ | **653 test lulus** (terverifikasi ulang), gate **READY 11/11** |

**Klaim worker terverifikasi:** 653 passed (dijalankan ulang: `653 passed, 2 warnings in 189.72s`),
gate READY 11/11 (dijalankan ulang), sanitasi 50 checkout bersih.

---

## 2. TAPI: tujuan (menutup kebocoran) BELUM tercapai

Brief-ku sendiri yang kurang: aku meminta "blokir jaringan Python-level + filter perintah",
padahal itu **string-matching terhadap shell Turing-complete**. Hasil validasi terukur:

### 2a. Bypass jaringan — 9 vektor LOLOS (terukur lewat `run_tests`)

| # | Perintah | Hasil | Kenapa lolos |
|---|----------|-------|--------------|
| B1 | `python -E -c "urllib..."` | **HTTP 200** | `-E` abaikan PYTHONPATH |
| B2 | `python -S -c "..."` (juga `-I`) | **HTTP 200** | `-S` tidak load site |
| B3 | `powershell (New-Object System.Net.WebClient).DownloadString(...)` | **unduh 36.983 bytes** (file fix Django 3.0!) | `.NET WebClient` tidak ada di filter |
| B4 | `cmd /c "curl ..."` | **HTTP 200** | filter hanya cocok di awal perintah |
| B5 | `bash -c "curl ..."` | **HTTP 200** | sama |
| B6 | `python -c "subprocess.run(['curl',...])"` | **HTTP 200** | nested subprocess |
| B7 | `git ls-remote --heads https://github.com/psf/requests` | **daftar refs bocor** | `ls-remote` tidak ada di filter |
| B8 | `git remote show origin` | kontak jaringan | `remote show` tidak ada di filter |
| B9 | **RANTAI**: nested `git fetch <URL>` → `git show FETCH_HEAD:...` | **kode masa depan terbaca** | sanitasi menghapus refs lama, tapi tidak mencegah **fetch URL baru** |
| B10 | `node -e "fetch(...)"` | **HTTP 200** | node tidak ada di filter |

### 2b. Bypass FILESYSTEM — yang paling merusak validitas (terukur)

| # | Yang dibaca | Hasil | Dampak |
|---|-------------|-------|--------|
| F1 | `results/EXP-20261004-034/patches/django__django-10914_direct.txt` | **PATCH STRATEGI DIRECT TERBACA** | 🔴 **SELF-LEAK**: planning/review bisa baca solusi direct di instance yang sama |
| F2 | `results/EXP-20261004-034/predictions/direct.jsonl` | **model_patch terbaca** | 🔴 sama |
| F3 | `_hits.txt` (sisa batch-5, berisi `model_patch` sympy-11870) | **terbaca** | 🔴 solusi issue dari run yang dihentikan |
| F4 | `.env` | **terbaca** (nama + nilai API key) | 🟠 kebocoran kredensial |
| F5 | `logs/eval_level100.log` | **110× `diff --git` terbaca** | 🔴 patch lama terbaca |
| F6 | checkout instance lain (`datasets/repos/...`) | **terbaca** | 🟠 |
| F7 | `results/` eksperimen lama (436 file predictions) | **terbaca** | 🔴 solusi issue lama |

**Catatan:** `read_file`/`grep` **sudah** menolak path luar repo (terverifikasi). Yang bocor adalah
**`run_tests`** — shell bebas dengan `cwd=repo` tapi **tanpa batas path**.

---

## 3. Apakah kebocoran ini terjadi di 120 run yang sudah selesai?

**Tidak terdeteksi — tapi bukan berarti tidak terjadi.**

| Sumber | Hasil |
|---|---|
| `tool_calls.jsonl` 123 run (semua batch) | **0** pembacaan `results/`, **0** `model_patch` |
| `experiment.log` (sumber lengkap) | Menemukan **sympy-11870** (batch-5, run #124, **dihentikan**) membaca `patch.txt` + `summary.json` + `manifest.json` eksperimen lama `EXP-20260824-005` |

Run #124 adalah yang memicu stop. `tool_calls.jsonl`-nya tidak ada karena dihentikan sebelum flush —
itulah sebabnya scan 123 file bersih. **Bukti kebocoran nyata ada di `experiment.log`.**

---

## 4. Sanitasi git: apa yang BENAR dan apa yang TIDAK ditutup

**Benar dan terverifikasi:**
- 50 checkout: 0 remote, 0 future commit, `git show v2.8.0:...` → rc=128
- Fail-closed assertion bekerja (`sanitize --check` → PASSED)
- Working tree semua checkout bersih (0 dirty)
- Tag masa lalu dipertahankan (95 tag di requests)

**Tidak ditutup:**
- **Fetch URL baru** (B9): sanitasi hanya menghapus refs yang *sudah ada*. Agent bisa
  `git fetch https://github.com/...` lalu baca dari `FETCH_HEAD`. Terbukti terukur.
- **FETCH_HEAD pre-existing di 51 checkout** — terverifikasi menunjuk **base_commit itu sendiri**
  (bukan kebocoran), tapi tetap higiene yang sebaiknya dibersihkan.

---

## 5. Kesimpulan validasi

| Pertanyaan | Jawaban |
|---|---|
| Pekerjaan worker sesuai brief? | ✅ **Ya**, semua item A–F dikerjakan, 653 test + gate 11/11 terverifikasi |
| Tujuan (tutup kebocoran) tercapai? | ❌ **Belum** — 10 bypass jaringan + 7 jalur filesystem terukur masih terbuka |
| Salah siapa? | **Brief orchestrator** — meminta whack-a-mole string-matching, bukan isolasi sejati |
| Apakah batch 1–4 tercemar? | **Tidak terdeteksi** di 123 run; kebocoran nyata hanya di run #124 (dihentikan) |
| Apakah sweep boleh dilanjutkan? | ❌ **Belum** — vektor F1/F2 (self-leak patch direct) merusak validitas RQ1 |

---

## 6. Rekomendasi (belum diputuskan — menunggu user)

Filter berbasis string **tidak akan pernah** menutup shell Turing-complete. Pilihan yang tersisa:

| Opsi | Cara | Trade-off |
|---|---|---|
| **A. Isolasi proses** | Jalankan `run_tests` sebagai user Windows terbatas + ACL (tolak baca `results/`, `.env`, checkout lain) | Butuh admin sekali setup; paling kuat di platform ini |
| **B. Container** | `docker run --network=none` mount **hanya** checkout | Paling bersih, tapi shell jadi Linux → **tidak sebanding** dengan batch 1–4 |
| **C. Pindah lokasi sensitif** | `results/`, `.env`, `logs/` dipindah ke path yang di-deny; agent tetap bisa `cd C:\` | Tidak menutup semua; butuh ACL juga |
| **D. Perluas filter + bersihkan** | Blokir `-E/-S/-I`, `WebClient`, `ls-remote`, `remote show`, nested subprocess, path absolut luar repo; hapus `_hits.txt` | Whack-a-mole; cepat; menutup kasus yang terbukti tapi bukan jaminan |

**Yang jelas wajib apa pun opsinya:**
1. Hapus `_hits.txt` (berisi `model_patch` sympy-11870) dan `_difft.txt`
2. Hapus FETCH_HEAD pre-existing 51 checkout (higiene)
3. `run_tests` harus **tidak bisa** membaca `results/`, `.env`, `logs/`, atau checkout lain
4. Perluas probe regresi agar **setiap bypass di dokumen ini** menjadi test yang bisa MERAH
