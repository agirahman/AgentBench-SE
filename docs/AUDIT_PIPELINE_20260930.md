# AUDIT — Pipeline AgentBench-SE (2026-09-30)

**Metode:** tiga auditor paralel (satu per jalur), temuan **diverifikasi ulang** oleh
auditor utama terhadap kode dan data nyata sebelum diperbaiki. Klaim yang tidak bisa
dikonfirmasi ditandai.

**Status:** 299 test lulus (dari 288). Semua perbaikan di bawah punya test regresi.

---

## Ringkasan

| # | Temuan | Severity | Status |
|---|---|---|---|
| 1 | **Penolakan reviewer bersandar pada file test yang tidak pernah dinilai** | **Kritis (validitas)** | Terdokumentasi, belum diperbaiki — butuh keputusan |
| 2 | `--resume` menganggap run yang MATI sebagai selesai → tidak pernah diulang | Mayor | ✅ **FIXED** |
| 3 | Semua error dilabeli `TIMEOUT` (502, 429, git error jadi sama) | Mayor | ✅ **FIXED** |
| 4 | Patch kosong dihitung sebagai kegagalan strategi di tool akurasi | Mayor | ✅ **FIXED** |
| 5 | `direct_strategy` tidak memanggil `spend_cost` | Minor (laten) | Terdokumentasi |

---

## 1. KRITIS — Reviewer menolak atas dasar file test yang tidak dinilai

**Ini temuan paling penting dari audit, dan bukan bug kode.**

`review_strategy.py:163` memicu ronde revisi saat reviewer menjawab `NEEDS_REVISION`.
Reviewer sering menolak karena **test yang ditambahkan agen rusak** — dan itu
**tidak mungkin** mengubah grade, karena:

```python
# swebench/harness/test_spec/utils.py:66-69, 87, 93
test_files = get_modified_files(test_patch)          # test_patch GOLD saja
reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
...
reset_tests_command      # sebelum test
apply_test_patch_command # test milik harness
...
reset_tests_command      # sesudah test
```

Harness **me-reset file test** ke base commit lalu menjalankan **test-nya sendiri**
(`FAIL_TO_PASS`/`PASS_TO_PASS`). File test yang ditulis agen tidak pernah masuk daftar
itu, jadi tidak pernah dinilai.

**Bukti terukur — django-11001/review level 40:**

| Fakta | Nilai |
|---|---|
| Penolakan reviewer | *"test yang ditambahkan mengimpor `RawSQL` dari `django.db.models`, nama itu tidak diekspor … modul test gagal diimpor"* |
| File yang dimaksud | `tests/ordering/tests.py` |
| Test yang BENAR-BENAR dinilai | `expressions.tests.BasicExpressionsTests` (FAIL_TO_PASS: 2) |
| Patch yang dikirim | memuat `tests/ordering/tests.py` yang "rusak" itu |
| **Hasil** | **resolved = True** |

Jadi penolakan itu soal file yang tidak dinilai, dan patch-nya sebenarnya **benar**.

**Seberapa sering?** 7 dari 9 verdict `NEEDS_REVISION` (78%) menyebut file test.
Lihat `tools/analyze_test_based_rejections.py`.

**Kenapa ini penting untuk tesis:** kalau sebagian besar penolakan reviewer tidak
bisa mempengaruhi grade, maka `review` **bukan** mengukur "review + revisi" — ia
mengukur *satu act executor* plus ronde tambahan yang sia-sia. Klaim apa pun tentang
review harus menyebut ini. Ada dua konsekuensi:

1. **Revisi jadi mahal tanpa manfaat** — token dan waktu terbuang untuk memperbaiki
   file yang dihapus harness.
2. **Bisa merugikan** — `runner.py:343-364` men-*strip* file test dari patch, tapi
   hanya yang ada di `test_patch` gold. Kalau agen menulis test di file LAIN, file itu
   ikut terkirim. Belum ada bukti kasus ini mengubah grade, tapi mekanismenya ada.

**Belum diperbaiki** karena ini keputusan desain, bukan bug: apakah reviewer harus
dilarang menolak atas dasar file test? Itu mengubah perilaku eksperimen, jadi butuh
keputusan user.

---

## 2. MAYOR — `--resume` tidak pernah mengulang run yang gagal ✅ FIXED

`runner.py:452-465` menulis baris error dengan **kunci resume yang sama** seperti run
sukses, dan `_load_existing_ids` membaca **semua** baris tanpa memfilter status.
Akibatnya instance yang mati (502) dianggap selesai dan **tidak akan pernah diulang**
oleh `--resume` — padahal itu satu-satunya alasan `--resume` ada.

Ini kali **ketiga** `--resume` bermasalah: `ee145e7` memperbaiki direktori baru setiap
kali, sekarang logika skip-nya.

**Perbaikan:** `_is_finished_entry()` — baris dihitung selesai hanya kalau punya patch
non-kosong dan tidak berstatus gagal. 4 test di `tests/test_resume_skips_failures.py`
(2 gagal sebelum perbaikan).

---

## 3. MAYOR — Semua error dilabeli `TIMEOUT` ✅ FIXED

`runner.py` menstempel `patch_status: "TIMEOUT"` di **tiga** tempat untuk setiap
exception. Jadi 502 gateway, 429 rate limit, dan kegagalan git **tidak bisa dibedakan**
dan semuanya terbaca "kehabisan waktu".

Terukur: `EXP-20260929-022` mencatat 502 (`ENOTFOUND opencode.ai`) sebagai TIMEOUT;
`EXP-20260824-005` mencatat 11× 429 berturut-turut dan satu error git dengan cara sama.

**Perbaikan:** `is_provider_error()` baru di `evaluation/retry.py`, status menjadi
`RATE_LIMIT` / `PROVIDER_ERROR` / `ERROR`. Marker sengaja **tidak** memakai substring
angka telanjang (`"502"`) karena itu cocok dengan nomor baris atau jumlah token —
dijaga test. 7 test di `tests/test_failure_classification.py`.

---

## 4. MAYOR — Patch kosong masuk hitungan akurasi ✅ FIXED

`tools/analyze_accuracy_vs_budget.py:90` memasukkan patch kosong ke himpunan
"converged" karena `_resolved is not None`. Patch kosong punya `total_turns=0`,
jadi **selalu** jatuh ke bucket turn terendah dan menyeretnya.

Terukur: bucket `0-15` = 67% sebelum, **100%** sesudah (2 run nyata, keduanya resolved).
`min=0` hilang dari distribusi.

---

## 5. MINOR (laten) — `direct_strategy` tidak memanggil `spend_cost`

`direct_strategy.py:42` memanggil `cost_share(1)` tapi tidak pernah `spend_cost`,
tidak seperti `planning` dan `review`.

**Dampak nyata: nol.** `direct` hanya punya satu act dan objek budget dibuat lokal per
run, jadi tidak ada act berikutnya yang bisa salah menerima allowance. Ini laten —
akan jadi bug kalau `direct` pernah punya act kedua. Dicatat, tidak diperbaiki
(perbaikan tanpa dampak hanya menambah kode).

---

## Temuan yang TIDAK terkonfirmasi

Dilaporkan partner, tidak bisa dibuktikan di data, jadi **tidak** dijadikan dasar
perbaikan:

- **`COST_LIMIT_USD` bisa dilewati** pada model ber-rate nol, jalur non-tool, dan satu
  request setelah batas tercapai. Kodenya memang begitu (`tool_loop.py:149-150,238,334`,
  `base.py:120-121`), tapi **tidak ada insiden nyata** — semua run dibatasi turn rendah
  dan model gratis. Relevan hanya untuk run berbayar.
- **Kegagalan mid-act membuang edit parsial dan biaya.** Benar secara kode
  (`tool_loop.py:275`, `runner.py:471-474`), terlihat di CSV (0 token, $0 untuk run
  yang mati), tapi tidak ada bukti edit parsial pernah hilang dari patch final.

Keduanya dicatat untuk run berbayar, bukan untuk kurva budget.

---

## Reproduksi

```bash
python tools/analyze_test_based_rejections.py        # temuan 1
python tools/analyze_accuracy_vs_budget.py EXP-20260929-003 EXP-20260929-022  # temuan 4
python -m pytest tests/test_resume_skips_failures.py tests/test_failure_classification.py -v
```
