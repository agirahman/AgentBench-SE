# 🧠 AI Agent Memory — AgentBench-SE

**Last Updated:** 2026-09-30 18:55 WIB
**Status:** **Persiapan run 50 issue.** Audit kesiapan oleh 2 partner menemukan **2 BLOCKER** — keduanya sudah diperbaiki + teruji. **BLOCKER kritis ketiga ditemukan sendiri: act revisi kelaparan budget** (0 edit selama 3 eksperimen). 323 test lulus.
**Active Branch:** `19/toolcall-commandcode`
**Detail sesi terakhir:** [`HANDOFF_20260929.md`](HANDOFF_20260929.md) · [`AUDIT_RUN_READINESS.md`](AUDIT_RUN_READINESS.md) · [`AUDIT_DATA_INTEGRITY.md`](AUDIT_DATA_INTEGRITY.md) · [`AUDIT_PIPELINE_20260930.md`](AUDIT_PIPELINE_20260930.md)

> ⛔ **GATE — WAJIB KONFIRMASI USER:** Jangan jalankan run besar (50 issue / multi-jam)
> tanpa persetujuan eksplisit dari user. Boleh tanpa konfirmasi: unit test, smoke test
> kecil (≤3 issue, 1 strategi), dan pekerjaan kode/dokumentasi.

> ⚠️ **KOREKSI PENTING (2026-09-29 malam):** klaim lama di dokumen ini — *"perbaikan act
> revisi BEKERJA, 3× edit_file"* — **SALAH**. Analisis ulang (`tools/analyze_run_anatomy.py`)
> membuktikan act revisi membuat **0 edit di SEMUA run** (level 40 dan 100). Tiga edit yang
> dulu kuklaim itu milik **act pertama (base)**, bukan act revisi. Lihat §"Koreksi act revisi".

---

## 📋 Current Project State

| Aspect | Status | Notes |
|--------|--------|-------|
| **Provider** | ✅ Done | OpenCode via 9router (`oc/space-bunny-free`), model gratis untuk testing |
| **Dataset** | ✅ Done | 50 issues: django(10)+sympy(10)+scikit(10)+matplotlib(10)+requests(6)+seaborn(4) |
| **Repo cache** | ✅ Done | 50 instance pristine di `datasets/repos/` |
| **Mekanisme patch** | ✅ Done | edit-then-diff: agen mengedit file, patch diambil dari `git diff` |
| **Tool calling** | ✅ Done | Loop bersama di `providers/tool_loop.py` (3 provider berbagi) |
| **Budget tool-turn** | ✅ Done | `agents/budget.py` — total sama per strategi, sekarang **40** |
| **Pre-flight validator** | ✅ Done | `tools/preflight_modal.py` — replikasi kontrak Modal secara lokal |
| **Rate-limit handling** | ✅ Done | Backoff 429 + circuit breaker |
| **Test suite** | ✅ Done | **299 lulus** (dari 288) |
| **Retry vs budget** | ✅ **FIXED** | Retry per-request di dalam tool loop (commit `dfc9fa8`) |
| **Konteks per turn** | ✅ Done | `TOOL_OUTPUT_MAX_CHARS=2000`, head+tail (dari 8000 head-only) |
| **Korupsi patch** | ✅ **FIXED** | `_normalize_newlines` merusak diff yang mengandung literal `\n` (commit `3cbd9d1`) |
| **Evaluasi EXP-003** | ✅ Done | Modal SWE-bench harness: **30/30 patch applied**, hasil di `EVAL_NOTE.md` |
| **Reviewer oracle** | ⚠️ Terbatas | `run_tests` selalu gagal; reviewer hanya bisa menalar. Spike 2026-09-29: test pre-existing lokal **murah (2–3 s) tapi tidak mendiskriminasi** — lihat `docs/SPIKE_ORACLE_20260929.md` |
| **Reserve act revisi** | ⚠️ **BELUM TERBUKTI** | Plumbing jalan (act dipanggil, executor yang eksekusi), tapi **0 edit di semua run**. Akar: `share()` di per_task tidak mereservasi untuk act revisi |
| **Verdict parsing** | ✅ **FIXED** | `_extract_verdict` tahan prosa + JSON rusak + negasi; 94 respons diaudit, 0 mismatch |
| **Klasifikasi patch kosong** | ✅ **FIXED** | Patch kosong ≠ kegagalan strategi; `EMPTY_PATCH` dilaporkan terpisah (bug wrapper evaluasi) |
| **Verifikasi eval** | ✅ Done | `tools/verify_eval_consistency.py` — bandingkan wrapper vs harness resmi; sepakat di semua level |
| **`--resume` mengulang kegagalan** | ✅ **FIXED** | Baris error dulu dihitung "selesai" → instance yang mati tidak pernah diulang. Sekarang hanya run ber-patch yang dianggap selesai |
| **Label kegagalan** | ✅ **FIXED** | Semua exception dulu distempel `TIMEOUT`; sekarang `RATE_LIMIT`/`PROVIDER_ERROR`/`ERROR` |
| **Akurasi vs budget** | ✅ **FIXED** | Patch kosong tidak lagi masuk hitungan (bucket 0-15: 67% → 100%) |
| **⚠️ Validitas review** | ❌ **BELUM** | **78% penolakan reviewer bersandar file test yang tidak pernah dinilai harness** — lihat §"Temuan kritis audit" |
| **Race EXP-ID** | ✅ **FIXED** | Lock `O_CREAT\|O_EXCL` + deteksi lock basi; di Windows errno `EACCES`, bukan `EEXIST` |
| **Split paralel** | ✅ Terverifikasi | `tools/verify_split.py`: 26 + 24 = 50, overlap 0 |
| **`--resume`** | ✅ **FIXED** | Dulu selalu membuat direktori baru (inert); sekarang `--exp-id` + `--resume` benar-benar melanjutkan |
| **Config awal run** | ✅ **FIXED** | `experiment.yaml` dulu ditulis setelah run selesai → crash = config hilang; sekarang `on_experiment_start` |
| **Kurva budget** | ✅ **DATAR** | Level 40 = level 100 = **2/3 ketiga strategi**. Level 200 tidak jalan (health check) |

---

## 🔑 Keputusan Kunci

| Keputusan | Tanggal | Alasan | Trade-off |
|-----------|---------|--------|-----------|
| Opsi A: edit-then-diff | 2026-09-27 | Model menulis diff sebagai teks sering cacat; mengedit file lalu `git diff` jauh lebih andal | Butuh tool calling |
| Pindah ke OpenCode/9router | 2026-09-27 | OpenRouter kena rate limit | Bergantung 9router lokal harus hidup |
| Bounded re-review (bukan auto-approve) | 2026-09-27 | Revisi tanpa re-review terbukti merugikan (EXP-007) | Menambah biaya token |
| Budget setara per strategi | 2026-09-28 | Cap per-`act()` membuat total jadi kecelakaan arsitektur; `direct` selalu terpotong | `review` tidak berubah, `direct` naik 3× |
| Reviewer tanpa oracle didokumentasikan sebagai temuan | 2026-09-28 | Lebih jujur daripada memoles angka | `review` tidak bisa lebih baik dari `planning` |
| Retry per-request, bukan per-loop | 2026-09-28 | `@with_retry` me-restart seluruh loop → eksplorasi hilang + budget ter-reset (99 call untuk budget 60) | Retry sekarang di dalam `tool_loop.py`, bukan dekorator |
| Budget 60 → 40 | 2026-09-28 | `direct` eksplorasi sampai dihentikan, bukan konvergen; 60 memakai ~4× token/waktu tanpa bukti akurasi naik (2/3 resolved di 20 maupun 60) | Angka **tidak komparabel** dengan EXP-20260927-010 |
| Potong konteks tool ke 2000 char | 2026-09-28 | 8000/turn × 60 turn ≈ 480 KB → request akhir melewati timeout | Detail menengah hilang; head+tail dipertahankan |
| `_normalize_newlines` hanya untuk patch JSON yang kolaps | 2026-09-29 | Diff asli boleh mengandung literal `\n` di dalam kode sumber; meng-unescape-nya membelah baris konteks | Patch lama di `results/` sudah di-derive ulang dari artefak mentah |
| `BAD_BODY` untuk baris body tanpa prefix | 2026-09-29 | Sebelumnya `pass` → korupsi dilabeli VALID/NORMALIZE, merusak 2 metrik sekaligus | Patch model yang benar-benar cacat sekarang ditolak, bukan "diperbaiki" palsu |
| Evaluasi via Modal SWE-bench harness | 2026-09-29 | Verdict lokal (`apply_status`) hanyalah prediksi `git apply --check`; harness punya fallback `patch --fuzz=5` yang tidak reproducible di Windows | Butuh Modal token + WSL untuk pre-flight |

---

## 📊 Hasil Eksperimen Terakhir

### `EXP-20260927-010` — 3 issue, budget 20/40/60

| Strategi | Resolved | Apply fail |
|---|---|---|
| direct | 2/3 | 0 |
| planning | **3/3** | 0 |
| review | 2/3 | 0 |

**9/9 patch lolos `git apply` ketat, 0 `APPLY_PATCH_FAIL`** — kegagalan murni semantik.

Reviewer menyetujui **9/9** patch, tapi hanya ~5/9 resolved → `APPROVED` tidak punya daya beda.

### `EXP-20260928-001` — 10 issue, DIHENTIKAN di 10/30

Budget 60/60/60 (setara). Loop revisi **terpicu 1×** di 10924/review — pertama kali sejak perbaikan prompt, dan bekerja dengan benar.

Dihentikan karena bug retry-reset-budget (lihat handoff). **1 patch-nya (`11019/direct`) juga kena korupsi `\n`; sudah di-derive ulang.**

### `EXP-20260928-003` — 10 issue × 3 strategi, SELESAI 30/30 + **DIEVALUASI**

Budget 40/40/40, retry per-request, konteks 2000 char. Durasi total ~2,5 jam.
Evaluasi via Modal SWE-bench harness. Detail: `results/EXP-20260928-003/EVAL_NOTE.md`.

| Strategi | Resolved | Rate | Median turn | Median waktu | Median token |
|---|---|---|---|---|---|
| direct | **8/10** | 80% | 25,0 | 187 s | 238 K |
| planning | **8/10** | 80% | 21,5 | 111 s | 140 K |
| review | **6/10** | 60% | 36,5 | 271 s | 291 K |

**30/30 patch terapply di harness** (`patch_successfully_applied: true` semua) — nol `APPLY_PATCH_FAIL`, semua kegagalan murni semantik.

**Dua kegagalan `review` — mekanisme berbeda, keduanya soal pembagian budget:**

| Instance | direct | planning | review | Penyebab kekalahan review |
|---|---|---|---|---|
| 10924 | ✓ | ✓ | ✗ | Executor kena cap **15 turn** → dipotong; reviewer **APPROVED** patch yang belum selesai (callable tidak pernah dipanggil → `TypeError` di hidden test) |
| 11001 | ✓ | ✓ | ✗ | Reviewer **benar** mendiagnosis (`re.DOTALL`), tapi act revisi dapat **1 turn** → hanya sempat 2× `read_file`, **nol edit** |

**Reviewer tidak punya daya beda (terkonfirmasi di sampel lebih besar):** APPROVED 9/10 run review, resolved hanya 6/9. Satu-satunya `NEEDS_REVISION` (11001) juga gagal. Korelasi verdict ↔ outcome nol.

**Pembagian budget 40 ke 3 act = akar masalah review.** Split-nya dinamis (`total//3` lalu separuh sisanya), jadi act yang overspend mengurangi jatah act berikutnya; act revisi hanya dapat floor 1 turn. Planning dengan 2 act (20+20) memberi executor ruang lebih → menang di dua instance yang sama. **Review membayar 2× token planning (291 K vs 140 K) untuk hasil lebih buruk.**

**Catatan:** angka lama `EXP-20260927-010` (2/3, 3/3, 2/3) tidak komparabel — budget dan model berbeda.

**`django-11019/direct` — perbandingan sebelum/sesudah fix retry:**

| | EXP-001 (budget 60) | EXP-003 (budget 40) |
|---|---|---|
| Tool call | 107 (restart 2×) | 42 |
| Waktu | 152 menit | 42,9 menit |
| Restart loop | 2 | **0** |

**Temuan metodologis penting — tool call ≠ turn:**
Model bisa mengeluarkan beberapa tool call paralel dalam satu turn HTTP. `total_tool_calls` (mis. 52) bisa melebihi budget (40) tanpa pelanggaran, karena budget dihitung per **turn**. Pakai `api_turns`/`total_turns` untuk klaim budget, **jangan** `total_tool_calls`. `total_turns=41` untuk direct = 40 turn tool + 1 turn jawaban final (di luar loop, by design).

**⚠️ Temuan lanjutan (2026-09-29) — 5 dari 8 kegagalan EXP-003 terkonfound budget:**
Pemetaan peringatan `MAX-TURNS` ke instance (`tools/analyze_cap_hits.py`, `tools/analyze_budget_confounds.py`) menunjukkan **6 dari 30 run kena cap, dan 5 di antaranya gagal**:

| Instance | direct | planning | review | Cap hits (turn yang diberikan) |
|---|---|---|---|---|
| 10914 | OK | OK | OK | review:1 (revisi) |
| 10924 | OK | OK | **FAIL** | review:**15** (executor dipotong) |
| 11001 | OK | OK | **FAIL** | review:**1** (revisi) |
| 11019 | **FAIL** | **FAIL** | **FAIL** | direct:**40**, planning:**33**, review:**3** |
| 11283 | **FAIL** | **FAIL** | **FAIL** | — (kegagalan model murni) |
| 5 lainnya | OK | OK | OK | — |

**Implikasi:** angka 8/8/6 **bukan** perbandingan bersih. `11019` gagal di ketiganya dan ketiganya kena cap (termasuk `direct` yang memakai pool penuh 40) — jadi instance itu mengukur batas turn, bukan strategi. `11283` gagal di ketiganya **tanpa** cap-hit → itu kegagalan model yang sah (nested quoting, jebakan #8). Untuk klaim RQ1, laporkan 11019 sebagai instance terkonfound atau ulangi dengan budget lebih besar.

**Bukti tambahan 10924:** executor membuat 3 edit terakhir di posisi 25–27 dari 28 tool call, lalu **dipotong**. Patch yang dinilai adalah pekerjaan yang belum selesai — dan reviewer tetap APPROVED (false approval). Terlihat di `tools/analyze_act_trace.py`.

### ✅ Smoke test reserve (2026-09-29) — `EXP-20260929-001`, `django-11001` review

Dijalankan dengan `REVISION_TOOL_TURNS=8`, `--instance-ids django__django-11001 --strategies review`. **Act revisi dikerjakan EXECUTOR, bukan reviewer** (reviewer hanya mendiagnosis).

| | Baseline EXP-003 | Smoke EXP-001 |
|---|---|---|
| `inference_count` | 3 | **5** (planner, executor, reviewer, **executor(revisi)**, reviewer(re-review)) |
| Verdict | NEEDS_REVISION → NEEDS_REVISION | NEEDS_REVISION → **APPROVED** |
| Fix di patch | `r'(.*)[^\S\n](ASC\|DESC)(.*)'` ❌ | `r'(.*)\s(ASC\|DESC)(.*)', re.DOTALL` ✅ |
| Turn act revisi | **1** (kelaparan) | **4** (`8//2`) |
| Edit act revisi | 0 edit (2 read) | **1 edit** (ke file TEST — di-strip harness, lihat catatan) |
| Preflight `git apply` | — | **PASS** |

> ⚠️ **Nuansa (ditemukan 2026-09-29 malam):** satu edit act revisi di smoke test ini
> menyentuh `tests/ordering/tests.py` — file **test**, yang **di-strip harness** sebelum
> evaluasi. Jadi edit itu **tidak mungkin** mempengaruhi hasil resolved. Klaim
> *"tanpa reserve, temuan reviewer tidak akan pernah dieksekusi"* tetap benar sebagai
> pernyataan tentang mekanisme, tapi **bukan** penjelasan kenaikan skor.
> Verifikasi: `python tools/analyze_run_anatomy.py --exp EXP-20260929-001`.

**Rantai revisi terbukti utuh** (urutan pesan via `tools/analyze_review_sequence.py`):
1. `orchestrator → reviewer [get_plan,get_patch]` → MSG 7: `NEEDS_REVISION`. Temuan nyata: *"The added test imports RawSQL from `django.db.models`, but `RawSQL` is not re-exported there… The test module will fail at import/collection time, so the regression test does not run."*
2. `orchestrator → executor [get_feedback]` → MSG 10: *"Fixed the test import: `RawSQL` now comes from `django.db.models.expressions`… (the compiler fix with `re.DOTALL` … was already in place)."*
3. `orchestrator → reviewer [get_plan,get_patch]` → MSG 13: `"verdict": "APPROVED"`, `"issues_found": ["None"]`

**Catatan penting (jangan salah simpulkan):** perbaikan `re.DOTALL` **sudah ada di act pertama** (base act, 4 edit). Reviewer menemukan bug *kedua* yang berbeda — impor test salah — dan act revisi memperbaiki itu. Jadi reserve bukan yang "menemukan" DOTALL; tapi tanpa reserve, temuan reviewer tidak akan pernah dieksekusi, dan patch dikirim dengan test yang gagal saat collection. Itu persis mekanisme yang menghilangkan 11001 dari review di EXP-003 (di sana verdict kedua tetap NEEDS_REVISION dan patch ditolak).

Cap-hit `executor=4` di smoke test **milik act revisi** (act berjalan 11:24:57→11:25:07, cap di 11:25:05) — habis terpakai, tapi cukup untuk 1 edit. Bukti pemisahan act: `tools/analyze_act_edits.py`; atribusi cap: `tools/analyze_cap_attribution.py`.

### 🔬 Kurva budget 40/100/200 — SEDANG BERJALAN (2026-09-29)

**Keputusan user:** per-task budget (bukan per-act), cost cap sebagai pengaman, kurva 3 issue × 3 level, **model free dulu** dengan token dihargai rate card DeepSeek, **sekuensial** (bukan paralel) supaya 9router dan model free melayani satu request pada satu waktu.

**Driver:** `tools/run_budget_curve.py` (27 run, 3 level × 3 issue × 3 strategi).
**Monitor:** `tools/watch_curve_loop.py` (baris progres per interval) dan `tools/watch_budget_curve.py` (tabel lengkap).

**Issue dipilih secara adversarial** — bukan yang sudah sukses semua:

| Issue | Alasan dipilih |
|---|---|
| 11019 | gagal di **ketiga** strategi, dan ketiganya kena cap |
| 11001 | review gagal: act revisi kelaparan 1 turn padahal reviewer sudah benar |
| 10914 | kontrol yang sukses di ketiganya |

**Konfigurasi tiap level:** `BUDGET_MODE=per_task`, `BUDGET_FLOOR_PER_ACT=10`, `REVISION_TOOL_TURNS=0` (di per-task tidak ada reserve terpisah), `COST_LIMIT_USD=3.0`, `PRICING_MODEL_OVERRIDE=deepseek-v4-flash`.

#### ✅ HASIL LEVEL 40 (per_task, floor 10) — `EXP-20260929-003`, dievaluasi Modal

| Instance | direct | planning | review |
|---|---|---|---|
| 10914 | ✅ resolved | ✅ resolved | ✅ resolved |
| **11001** | ✅ resolved | ✅ resolved | **✅ resolved** ← sebelumnya GAGAL di review |
| 11019 | ❌ TESTS_ERROR | ❌ TESTS_ERROR | ❌ TESTS_ERROR |

**2/3 resolved untuk KETIGA strategi — seri untuk pertama kalinya.** 9/9 patch lolos kontrak `git apply` Modal; 9/9 `patch_applied: true`. Biaya 9 run: **$0.5724** ($0.0636/run; termahal $0.1584 vs cap $3 → guard tidak pernah menyala).

#### ⚠️ KOREKSI act revisi (2026-09-29 malam) — klaim lama SALAH

Klaim lama: *"perbaikan act revisi BEKERJA: 3× `edit_file` di act revisi"*. **Itu salah**, dan analisis ulang membuktikannya.

**Metode yang benar** (`tools/analyze_run_anatomy.py`): act berjalan dalam urutan tetap (planner → executor → reviewer → executor → reviewer), jadi **panggilan reviewer pertama menandai akhir act executor BASE**, dan setiap panggilan executor setelahnya adalah act REVISI.

| Bukti | Level 40 | Level 100 |
|---|---|---|
| Run review yang benar-benar menjalankan revisi | 2 dari 3 | 1 dari 3 |
| **Edit oleh act revisi** | **0** | **0** |
| Edit oleh act BASE | 3 (11001) / 2 (11019) | 4 (11001) |
| Turn yang diberikan ke act revisi | **1** (cap) | cukup, tapi tetap 0 edit |

Tiga edit `edit_file` di `11001/review` (call #13, #19, #20) semuanya terjadi **sebelum** panggilan reviewer pertama → itu act **BASE**. Yang resolved adalah patch act base; act revisi hanya sempat 2× `grep` (level 40) atau 6 call read/test/diff tanpa edit (level 100).

**Kenapa 11001 resolved padahal klaimnya salah:** act BASE kebetulan menghasilkan regex yang benar (`re.DOTALL`) di run ini, sedangkan di EXP-003 act base menghasilkan yang salah. **Revisi bukan penyebabnya** — jadi kenaikan 6/10 → 2/3 **bukan bukti** bahwa perbaikan reserve bekerja.

**Akar masalahnya ada di `budget.py`:** di mode `per_task`, `share()` hanya menyisakan floor untuk **act base** yang belum jalan. Act revisi bukan bagian base flow, jadi **tidak ada yang direservasi untuknya**. Di pool 40 act base bisa menghabiskan semuanya → revisi jatuh ke floor 1 turn. Di pool 100 masih ada sisa, tapi tetap tidak mengedit.

**Status jujur:** perbaikan reserve **belum terbukti bekerja**. Yang terbukti hanya plumbing-nya (act revisi benar-benar dipanggil, `inference_count` naik, executor yang menjalankannya — bukan reviewer).

**11019 masih gagal di ketiganya (`TESTS_ERROR`)** — konsisten dengan temuan lama bahwa instance ini mengukur batas budget, bukan strategi. Di level 40 review masih `truncated` di 11001 **dan** 11019 (pool 40 harus menutup 5 act review: plan, exec, review, revisi, re-review). Level 100/200 menguji apakah pool lebih besar mengubahnya.

**Perbandingan aturan pembagian pada pool yang sama** (`tools/compare_curve_vs_baseline.py`):

| Instance | Strategi | per_act → per_task |
|---|---|---|
| 11001 | direct | 27 → **12** turn |
| 11001 | planning | 14 → 27 turn |
| 10914 | planning | 28 → 39 turn |
| 11019 | review | 39 → 42 turn (masih truncated) |

**Preflight (1 run, 10914 direct, level 40) membuktikan plumbing-nya:**
- `cost_usd_actual = $0.0731` — **bukan** $0.0000 → override pricing sampai ke kalkulator biaya, RQ3 punya data dolar meski modelnya gratis
- `truncated = True`, `truncated_acts = 1` → field truncation merekam dirinya sendiri (adopsi dari harness SWE-bench)
- `patch_status = VALID`, `experiment.yaml` mencatat semua knob kurva

**Koreksi estimasi waktu:** laju terukur **10,9–13,7 s/turn** (bukan asumsi 3,5 menit/run). Kurva jadi **1–6 jam** dengan level 200 mendominasi, bukan ~2,5 jam.

#### 🚨 TEMUAN KRITIS AUDIT (2026-09-30) — reviewer menolak atas dasar file test yang tidak dinilai

> ⚠️ **KOREKSI (2026-09-30, sebelum run model berbayar).** Klaim awal **78%** itu
> **SALAH** dan berasal dari pengukuran yang terlalu lemah. Angka itu dihitung dengan
> mencari *"teks review menyebut file test"* — dan kata "test" itu bahasa Inggris
> biasa dalam review kode. Setelah setiap penolakan dibaca **utuh**
> (`tools/analyze_rejection_basis.py`), angkanya:
>
> | Ukuran | Jumlah |
> |---|---|
> | Penolakan `NEEDS_REVISION` yang dibaca | 8 |
> | Menyebut file test | 6 |
> | **Eksplisit MENYANGKAL** file test sebagai alasan | 1 |
> | **Seluruh `issues_found`-nya HANYA soal file test** | **1** |
>
> **Hanya 1 dari 8 (12,5%)** yang bisa berubah kalau reviewer diberi tahu file test
> di-strip. Sisanya menolak karena alasan sah yang terlihat di kode: patch tidak
> menyentuh mekanisme, memanggil method yang tidak ada (`_css_lists_paths`),
> `merge()` masih dua-argumen, `OrderedDict()` tanpa import.
>
> Contoh yang paling jelas — `10914/review`, `issues_found` satu-satunya soal
> **label dokumentasi** (`:ref:`collectstatic`` tidak ada), dan `11001/review`
> menolak karena **fix-nya no-op**: pola lama dan baru sama-sama menghasilkan
> `group(1)` yang sama.
>
> **Kesimpulan yang berubah:** masalah ini nyata tapi **kecil**, bukan kritis.
> `review` tetap bisa diklaim mengukur review+revisi. Yang perlu dicatat hanya
> bahwa **reviewer sesekali menyebut cacat file test yang tidak dinilai harness** —
> dan itu **tidak** mengubah verdict di hampir semua kasus.

Klaim awal (untuk catatan, **JANGAN dipakai**): "7 dari 9 penolakan (78%)" — dihitung
dengan pencocokan frasa, bukan dengan membaca apakah file test itu **menentukan**
verdict.

`review_strategy.py:163` memicu ronde revisi saat reviewer bilang `NEEDS_REVISION`. Reviewer sering menolak karena **test yang ditambahkan agen rusak**. Tapi itu **tidak mungkin** mengubah grade:

```python
# swebench/harness/test_spec/utils.py:66-69, 87, 93
test_files = get_modified_files(test_patch)   # HANYA test_patch gold
reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
```

Harness **me-reset file test** ke base commit lalu menjalankan **test-nya sendiri**. File test yang ditulis agen tidak pernah masuk `FAIL_TO_PASS`/`PASS_TO_PASS`, jadi tidak pernah dinilai.

**Bukti terukur — `11001/review` level 40:**

| Fakta | Nilai |
|---|---|
| Penolakan reviewer | *"test mengimpor `RawSQL` dari `django.db.models`, tidak diekspor … modul test gagal diimpor"* |
| File yang dimaksud | `tests/ordering/tests.py` |
| Test yang BENAR-BENAR dinilai | `expressions.tests.BasicExpressionsTests` (2 test) |
| Patch yang dikirim | memuat file test "rusak" itu |
| **Hasil** | **resolved = True** |

**7 dari 9 verdict `NEEDS_REVISION` (78%) menyebut file test.** Tool: `tools/analyze_test_based_rejections.py`.

**Implikasi untuk tesis:** kalau mayoritas penolakan tidak bisa mempengaruhi grade, maka `review` **bukan** mengukur "review + revisi" — ia mengukur *satu act executor* plus ronde sia-sia. **Klaim apa pun tentang review harus menyebut ini.** Ini juga menjelaskan kenapa act revisi "0 edit": sering kali **memang tidak ada yang perlu diperbaiki**.

**Belum diperbaiki** karena ini keputusan desain, bukan bug: melarang reviewer menolak atas dasar file test akan mengubah perilaku eksperimen. **Butuh keputusan user.**

#### 🐞 Tiga bug lain dari audit — SUDAH DIPERBAIKI

| Bug | Bukti | Perbaikan |
|---|---|---|
| **`--resume` tidak pernah mengulang run gagal** | Baris error ditulis dengan kunci resume sama seperti sukses, dan `_load_existing_ids` membaca semua baris tanpa filter status → instance mati dianggap "selesai" selamanya. Ini kali **ketiga** `--resume` bermasalah | `_is_finished_entry()`: hanya run ber-patch non-kosong yang dianggap selesai. 4 test (2 gagal sebelum) |
| **Semua error distempel `TIMEOUT`** | `runner.py` hardcode `TIMEOUT` di 3 tempat untuk setiap exception. 502 gateway, 429, dan error git jadi tak terbedakan. Terukur: EXP-022 mencatat 502 sebagai TIMEOUT; EXP-20260824-005 mencatat 11× 429 + error git dengan cara sama | `is_provider_error()` di `retry.py`; status jadi `RATE_LIMIT`/`PROVIDER_ERROR`/`ERROR`. 7 test |
| **Patch kosong menyeret akurasi** | `analyze_accuracy_vs_budget.py` menghitung patch kosong sebagai "converged"; `total_turns=0` → selalu jatuh ke bucket terendah. Bucket 0-15: 67% → **100%** | Non-attempt dikecualikan |

**Catatan:** marker `is_provider_error` sengaja **tidak** memakai substring angka telanjang (`"502"`) karena cocok dengan nomor baris/jumlah token — ada test yang menjaganya.

**Dua temuan partner TIDAK dikonfirmasi** (kode benar, tidak ada insiden nyata) dan **tidak** dijadikan dasar perbaikan: `COST_LIMIT_USD` bisa dilewati pada model ber-rate nol / jalur non-tool; kegagalan mid-act membuang edit parsial. Relevan hanya untuk run berbayar — dicatat di `docs/AUDIT_PIPELINE_20260930.md`.

#### 💰 RUN BERBAYAR (RQ3) — model `cbai/deepseek-v4.1-flash` — `EXP-20260930-030`

**Model berbayar pertama.** Bukan estimasi: 9router mencatat setiap request di database
SQLite-nya (`%APPDATA%/9router/db/data.sqlite`, tabel `usageHistory`), jadi biaya bisa
**dibaca dari bill nyata** dengan `tools/read_actual_bill.py`.

**Kredensial:** route `cbai/` **HANYA** menerima `OPENCODE_API_KEY` (NINEROUTER).
`COMMANDCODE_API_KEY` → 401. Diuji ke semua key di `.env`.

**Cara pakai:**
```bash
python tools/probe_route.py --model cbai/deepseek-v4.1-flash --all   # cek sebelum belanja
python tools/run_rq3_paid.py --smoke                                  # 1 issue, 1 strategi
python tools/run_rq3_paid.py                                          # 3 issue × 3 strategi
python tools/read_actual_bill.py --since <ISO> --until <ISO> --compare results/<EXP>
```

##### ⚠️ TEMUAN PENTING: cache hit TIDAK didiskon di route ini

**Ini nyaris membuat angka RQ3 salah 2×.** Urutan temuannya:

1. Rate card awal kuderivasi dari 5.698 baris historis: `$0.14 / $0.002833 / $0.28`
   per 1M (regular/cached/output). 371 baris tanpa cache terprediksi **tepat 0.000%**.
2. Smoke test: bill menagih **$0.003438**, akuntansi kita bilang **$0.001438** —
   **kita under-report 2,05×**.
3. Ternyata API **melaporkan** cache hit (256 & 896 token via
   `prompt_tokens_details.cached_tokens`) — pipeline kita benar membacanya — tapi
   9router menagih **harga penuh**: `charged/full-price = 1.0000`.
4. Jadi diskon cache historis **tidak berlaku untuk request kita**. Kalau kupakai,
   setiap angka biaya di tesis jadi **setengah dari kenyataan** — dan itu ke arah yang
   **mempercantik klaim biaya**, arah yang paling berbahaya.

**Perbaikan:** `cached_input_per_million` disetel **sama dengan** `input_per_million`
(0,14). Card diberi label `...-no-cache-discount`. **Diverifikasi ulang:** bill
$0.003438 vs akuntansi $0.003435 → **selisih 0,09%**.

**Pelajaran metodologis:** rate card historis **bukan** jaminan harga yang berlaku
sekarang. `read_actual_bill.py` adalah otoritasnya; kalau bill berbeda dengan card,
**bill yang menang**.

##### Hasil RQ3 (`EXP-20260930-030`) — **SELESAI**

**Model:** `cbai/deepseek-v4.1-flash` (berbayar, biaya nyata dari bill 9router)
**Durasi:** 14,9 menit untuk 9 run · **9/9 patch VALID** (tidak ada yang kosong)

| | direct | planning | review |
|---|---|---|---|
| 10914 | ✅ | ✅ | ✅ |
| 11001 | ✅ | ✅ | ✅ |
| 11019 | ❌ | ❌ | ❌ |

**2/3 di ketiganya.** Kurva tetap **DATAR** — sekarang di tiga titik (40, 100, berbayar).
Wrapper dan harness resmi **sepakat** (`verify_eval_consistency.py`).

**Biaya nyata: $0,298440** untuk 230 request (74 direct + 65 planning + 91 review).

**Verifikasi bill:** bill mentah $0,325726 untuk 245 request. Selisihnya 15 request =
**sesi agent-ku sendiri** (model sama, route sama). Setelah dipisah: $0,295003 vs
akuntansi kita $0,298440 → **selisih 1,2%**. Rate card terverifikasi di skala run penuh.

**11019 tetap gagal di semua strategi, di semua level, di semua model.** Tapi sekarang
**patch-nya ada dan besar** (7.252 / 5.481 / 3.106 byte) — bukan lagi "tidak ada patch".
Ketiganya `patch_applied=True`, `failure_reason=TESTS_ERROR`.

##### ✅ AUDIT KESIAPAN RUN 50 (2026-09-30) — 3 BLOCKER, semua diperbaiki

Dua partner di-delegasikan audit paralel (read-only): **p7** = jalur RUN, **p8** =
integritas data/evaluasi. Laporan: `AUDIT_RUN_READINESS.md`, `AUDIT_DATA_INTEGRITY.md`.

**Konvergensi kuat:** kedua partner **independen** menemukan BLOCKER `--resume`
yang sama. Itu bukan noise — itu bukti.

| # | Temuan | Tingkat | Bukti | Status |
|---|---|---|---|---|
| 1 | `--resume` **menghapus** baris lama dari CSV + manifest | **BLOCKER** | Dua partner independen; 3 issue → resume 5 issue → CSV tinggal 2 baris | ✅ **FIXED** |
| 2 | Backoff rate-limit tak terbatas → satu act bisa menggantung ~2 jam | **BLOCKER** | Run nyata 5.992 s (100 menit); `RATE_LIMIT_CONSECUTIVE_LIMIT` tidak menangkap karena backoff di **dalam** tool loop | ✅ **FIXED** |
| 3 | **Act revisi kelaparan budget** — 0 edit di 3 eksperimen | **BLOCKER** | `max_tool_turns=1 for role=executor` di log; ditemukan sendiri | ✅ **FIXED** |
| 4 | Semua kegagalan non-TIMEOUT dilabeli `EMPTY_PATCH`; run mati terbaca `COMPLETED` | SERIUS | `observability.py:11-16` | ✅ **FIXED** |
| 5 | Retry instance dihitung **dua kali** → rate bisa >100% | **BLOCKER** | p8 eksekusi: 150% terukur | ✅ **FIXED** |
| 6 | Tidak ada pemeriksaan kelengkapan 150 | MINOR | `runner.py` tidak pernah bandingkan jumlah | ✅ **FIXED** |
| 7 | 10 instance cache kotor sebelum run | MINOR | `reset_working_tree()` dipanggil per strategi, jadi aman selama run | ⚠️ perlu dibersihkan |

**BLOCKER #3 — temuan terpenting, dan aku menemukannya sendiri:**

`share_revision()` di mode `per_task` **mengabaikan reserve** dengan alasan "pool cukup
besar". Alasan itu **salah secara desain**: act base **terakhir** mendapat `share(1)` =
seluruh sisa, jadi pool **kosong tepat saat revisi mulai** → revisi dapat floor **1 turn**
→ tidak bisa edit.

Terukur: `11019/review` level 40 → `max_tool_turns=1 for role=executor`, revisi 0 edit.
Ini menjelaskan kenapa "review" tidak pernah merevisi apa pun di **tiga eksperimen** —
dan kenapa diagnosis awalku ("patch-nya sudah benar") hanya sebagian benar.

**Ini berarti arm `review` belum pernah mengukur review+revisi.** Prasyarat untuk
klaim apa pun tentang review sebelum run 50.

**Perbaikan:** reserve dihormati di kedua mode + **warning** kalau `per_task` tanpa
reserve (supaya kelalaian setelan jadi berisik, bukan senyap).

##### ⚠️ KEPUTUSAN TERBUKA: reserve revisi membuat total budget TIDAK SAMA

Perbaikan reserve (#3) memunculkan konsekuensi desain yang **harus diputuskan sadar**,
bukan ditemukan belakangan di hasil. Diukur dengan `tools/check_budget_fairness.py`:

| Strategi | Act base | Base | Revisi | **TOTAL** |
|---|---|---|---|---|
| direct | 40 | 40 | 0 | **40** |
| planning | 30+10 | 40 | 0 | **40** |
| review | 20+10+10 | 40 | **8** | **48** |

**Base flow tetap setara (40/40/40)** — itu yang dijamin desain. Tapi **total** review
bisa 48 (20% lebih), karena reserve adalah allowance **tambahan**, bukan potongan pool.

**Konsekuensi untuk tesis:**

- **RQ1 (efektivitas):** kalau `review` menang, kemenangan itu **tidak bisa diatribusikan
  ke strategi saja** — dia punya budget lebih besar. Harus dilaporkan bersama totalnya.
  Kalau `review` **kalah**, itu tetap informatif: gagal **dengan** ruang lebih.
- **RQ2 (efisiensi):** terdampak langsung — turn/token review diukur terhadap allowance
  yang lebih besar.
- **RQ3 (biaya):** biaya review naik saat revisi jalan. Itu **nyata** dan boleh dilaporkan
  sebagai biaya struktural strategi.

**Opsi (semua defensible kalau dinyatakan):**

| Opsi | Isi | Trade-off |
|---|---|---|
| **(a)** Terima | Laporkan base=40 + 8 tambahan review sebagai biaya struktural | Paling jujur soal realita; tapi RQ1 perlu kualifikasi |
| **(b)** Samakan total | Naikkan `--total` direct/planning jadi 48 | Perbandingan bersih; tapi angka "40" di kurva tidak lagi sama |
| **(c)** reserve=0 | Tetap seperti 3 eksperimen lalu | Jujur, tapi **review tidak diukur sama sekali** |

**Catatan penting:** hanya opsi (c) yang dilakukan tiga eksperimen di disk. **Setiap angka
`review` yang ada sekarang berasal dari run yang act revisinya dapat 1 turn** — jadi tidak
ada satu pun yang benar-benar mengukur review.

---

##### Perbaikan yang sudah diverifikasi

| Perbaikan | Verifikasi |
|---|---|
| CSV **merge** sebelum tulis (bukan overwrite) | Test regresi **gagal di kode lama** (4 dari 6) → membuktikan test sah |
| Manifest dibangun dari CSV hasil merge | `total_issues_processed` sekarang benar |
| Dedupe prediksi + guard `resolved <= total` | Gagal keras kalau aritmatika salah |
| `INCOMPLETE.json` kalau ada run hilang | Run yang kehilangan data **tidak** keluar seolah sukses |
| `ACT_TIMEOUT_SECONDS` (default 1800 s) | Guard di **atas** loop; act yang dipotong ditandai `truncated` |
| Manifest: semua bucket kegagalan + `failure_counts` | Run mati rate-limit tidak lagi `COMPLETED` |
| `_PATCH_STATUS_FAILED` memuat `RATE_LIMIT`/`PROVIDER_ERROR` | Eksplisit, tidak bergantung kebetulan patch kosong |
| Reserve revisi dihormati di `per_task` | Revisi dapat 4 turn (bukan 1) |

**323 test lulus** (dari 305). Test baru: `test_resume_data_integrity.py`,
`test_tool_loop_wall_clock.py`, + kasus dedup di `test_eval_empty_patch.py`,
+ regresi reserve di `test_budget_modes.py`.

**Jebakan metodologis yang tercatat:** dua hipotesisku sendiri **terbantah saat diuji**
— "78% penolakan karena file test" (asli: **12,5%**) dan "file scratch penyebab 11019"
(**salah**). Keduanya kucatat supaya tidak diulang.

---

**Pertanyaan:** 8 dari 9 run 11019 gagal dengan `TESTS_ERROR` — bukan kegagalan test
biasa, tapi **test run-nya sendiri rusak**. Itu pola yang tidak dihasilkan patch salah.
Kecurigaan: instance ini tidak bisa dinilai di setup kita, jadi semua "kegagalan" itu
artefak pengukuran.

**Diuji:** gold patch resmi dari dataset (4.929 byte, hanya `django/forms/widgets.py`)
dikirim sebagai prediksi (`EXP-20260930-098-gold-check`) → **resolved = 1/1**.

**Kesimpulan: instance ini BISA dinilai, dan kegagalan agen itu NYATA.**
`TESTS_ERROR` bukan artefak harness — patch agen memang merusak test run. Ini
menutup pertanyaan terbuka terakhir tentang 11019.

**Implikasi untuk tesis:** 11019 adalah **kegagalan kapabilitas yang valid**, bukan
terkonfound budget. Tiga strategi, tiga level budget, dua model (gratis & berbayar),
semuanya gagal — sementara gold patch lulus. Ini temuan yang bisa diklaim.

##### ⚠️ 11019: hipotesis file scratch TERBUKTI SALAH

`direct` menyertakan `_check_merge.py` — script 36 baris yang agen tulis untuk menguji
logikanya sendiri (`settings.configure()` + `django.setup()` di level import), di luar
`django/`. planning & review tidak menyertakannya.

**Hipotesis:** file itu merusak koleksi test → TESTS_ERROR.

**Diuji:** patch yang sama **tanpa** file scratch dievaluasi terpisah
(`EXP-20260930-099-scratch-test`) → **tetap `TESTS_ERROR`**.

**Kesimpulan: hipotesis salah.** Ketiga strategi gagal dengan cara identik, jadi file
scratch bukan penyebabnya.

**Yang tetap perlu dicatat:** `runner.py:343-364` men-*strip* file test **gold** dari
patch, tapi tidak ada yang men-*strip* file scratch buatan agen. Belum terbukti
merusak grade di sini, tapi celahnya nyata.

#### ✅ HASIL LEVEL 100 (per_task, floor 10) — `EXP-20260929-022` — **SUDAH DIEVALUASI**

Sweep selesai **240,8 menit** untuk 9 run. **8 dari 9 run usable**; satu mati karena provider.

| Instance | direct | planning | review |
|---|---|---|---|
| 10914 | ✅ resolved | ✅ resolved | ✅ resolved |
| 11001 | ✅ resolved | ✅ resolved | ✅ resolved |
| 11019 | ❌ TESTS_ERROR | ❌ TESTS_ERROR | — **(patch kosong, bukan kegagalan strategi)** |

**2/3 untuk ketiga strategi — identik dengan level 40.** Detail: `results/EXP-20260929-022/EVAL_NOTE.md`.

**KURVA DATAR antara 40 dan 100 turn.** Menaikkan pool 2,5× tidak mengubah satu pun hasil pada ketiga instance ini. Truncation turun dari 2 run (level 40) → **0** (level 100), jadi pool 100 memang menghilangkan cap-hit — tapi menghilangkan cap tidak mengubah verdict apa pun.

**`11019` gagal di semua strategi di semua level.** Dua tafsir yang belum bisa dipisahkan datanya: (1) butuh >100 turn, atau (2) kegagalan kapabilitas murni. Level 200 akan memisahkannya. Sampai itu ada, laporkan `11019` sebagai **terkonfound budget**, bukan sebagai kekalahan strategi.

#### 🐞 Bug evaluasi: patch kosong dihitung sebagai kegagalan strategi — DIPERBAIKI

`tools/eval_modal.py` membaca `resolved_ids`/`error_ids`/`unresolved_ids` dari summary harness, lalu **jatuh ke cabang `else`** untuk instance apa pun yang tidak ada di ketiganya — dan mencatatnya `resolved=False`. Padahal harness **sengaja tidak menjalankan** patch kosong: `reporting.py:47-60` menaruhnya di `empty_patch_ids` ("Instances with empty patches"), dan `run_evaluation.py:458` mengeluarkannya dari dataset.

Akibatnya `11019/review` (mati karena 502) dilaporkan `patch_applied=True reason=TESTS_ERROR` — seolah-olah strateginya menjawab salah, padahal **tidak ada patch sama sekali**.

**Dua lapis masalah, keduanya diperbaiki:**
1. **Klasifikasi salah** → sekarang `failure_reason="EMPTY_PATCH"`, `patch_applied=False`. Nilai `applied=True` yang lama datang dari `report.json` basi: harness mengaplikasikan diff kosong sebagai no-op, lalu melaporkan `applied=True` — benar secara teknis, menyesatkan secara praktis.
2. **Logikanya tidak bisa ditest** karena inline di `main()`. Diekstrak jadi `classify_summary()` murni, sekarang 5 test menutupinya. Bug `UnboundLocalError` di perbaikan pertamaku **lolos test** karena alasan yang sama — itulah kenapa ekstraksi ini bukan sekadar kerapian.

**Dua angka dilaporkan, dan tidak boleh tertukar:**

| Bacaan | Nilai | Arti |
|---|---|---|
| resolved / **submitted** | **66,7%** | headline yang komparabel antar-level |
| resolved / **graded** (n=2) | 100,0% | hanya run yang menghasilkan patch |

Pakai **66,7%** untuk membandingkan level. Angka 100% akan membuat review tampak unggul justru karena **kehilangan** satu data point.

**Tool verifikasi baru:** `tools/verify_eval_consistency.py` — membandingkan summary resmi harness dengan `*_results.json` kita per level/strategi, dan gagal kalau ada ketidaksepakatan. Hasil sekarang: **sepakat di semua level.** Ini pemeriksaan yang menemukan bug di atas; sebelumnya tidak ada yang membandingkan kedua catatan itu.

#### ❌ LEVEL 200 — TIDAK PERNAH JALAN

```
level 40  finished rc=0 in  80.1 min
level 100 finished rc=0 in 240.8 min
Health check failed — aborting
level 200 finished rc=0 in   1.9 min   ← nol data
```

`main.py:298-302` memanggil `provider.health_check()` dan **return lebih awal** kalau gagal. Health check (`opencode_provider.py:67`) menembak `models.list()` lalu satu completion; provider sedang tidak sehat saat itu. **Tidak ada direktori eksperimen yang dibuat** → 0 patch, 0 data.

**Total sweep: 322,8 menit (5,4 jam).** Bukan 2,5 jam seperti estimasi awal.

**Cara menjalankan ulang hanya level 200** (driver mendukungnya):
```bash
python tools/run_budget_curve.py --levels 200
# atau untuk melanjutkan sweep yang terputus:
python tools/run_budget_curve.py --skip 40 100
```
**Sebelum itu, verifikasi provider sehat** — kalau tidak, abort lagi dalam 2 menit.

#### 🐞 Bug verdict parsing — DIPERBAIKI (satu-satunya bug yang mengubah hasil eksperimen)

`_extract_verdict` lama melakukan `json.loads(feedback)` pada **seluruh string**, dan kalau gagal memakai `"APPROVED" in feedback.upper()[:50]`. Reviewer tidak selalu menuruti instruksi "akhiri dengan JSON": kadang **prosa dulu, JSON belakangan**, kadang JSON-nya sedikit rusak.

| Mode gagal | Terjadi di | Efek |
|---|---|---|
| **False revision** — prosa dulu, JSON belakangan | EXP-20260927-005, **EXP-20260929-022 (11001/review)** | Reviewer bilang APPROVED, dibaca NEEDS_REVISION → ronde revisi sia-sia |
| **False approval** — penolakan yang 50 karakter pertamanya memuat "APPROVED" | Belum teramati, tapi kodenya mengizinkan | Patch belum-terverifikasi ikut terkirim |

**Perbaikan:** regex `"verdict"\s*:\s*"([A-Z_]+)"` mengambil verdict **terakhir** di teks (tahan prosa & JSON rusak), plus regex negasi (`not APPROVED`) supaya fallback kata kunci tidak menyetujui penolakan. Default tetap **NEEDS_REVISION** — menolak aman, mengirim patch belum-terverifikasi tidak.

**Verifikasi:** `python tools/analyze_run_anatomy.py --verdicts` → **94 respons diaudit, 0 mismatch** (sebelumnya 3). 7 test baru di `tests/test_review_strategy.py`, memakai teks asli dari data.

#### 🧹 Konsolidasi tooling

22 skrip diagnostik sekali-pakai dihapus, diganti **2 tool permanen**:

| Tool | Fungsi |
|---|---|
| `tools/analyze_run_anatomy.py` | Anatomi per-run: urutan act, edit base vs revisi, verdict + mismatch, retry per jenis. `--verdicts` untuk audit parser saja, `--all` untuk semua eksperimen |
| `tools/check_sweep_state.py` | Status tiap level: predictions, patch kosong, error provider, timing sweep (membuat abort 1,9 menit terlihat) |

#### ⏱️ Retry — angka sebenarnya

Audit pertamaku salah (regex `rate.?limit` cocok dengan baris **`Rate limit delay`**, yaitu jeda sengaja antar-run, **bukan** retry). Angka benar dari log sweep: **10 baris retry**, **2 di antaranya 502 bad gateway sungguhan**. Level 100: 6 model-stall + 5 provider-error.

**3 bug ditemukan saat memantau** (commit `90effea`, semua ada test):
1. `experiment.yaml` ditulis **setelah** run selesai → crash = konfigurasi hilang, tidak reproducible. Diperbaiki dengan `on_experiment_start` callback (config ditulis sebelum run pertama).
2. Watcher membaca `generation_result.csv` yang ditulis sekali di akhir level → melaporkan 0/9 selama satu jam. Diganti ke `predictions/predictions.jsonl` (savepoint per run).
3. Log sweep ditulis PowerShell sebagai **UTF-16LE** → dibaca sebagai UTF-8, regex `LEVEL` tidak pernah cocok. Ditambah `read_text_tolerant()`.

**Dasar riset (terverifikasi dari sumber primer):**

| Sistem | Limit | Per apa |
|---|---|---|
| mini-SWE-agent (`step_limit`) | 250 step + $3 | per **task** |
| SWE-agent | $3/instance, `per_instance_call_limit=0` | per **instance** |
| OpenHands (`max_iterations`) | 500 | per **task** |
| SWE-bench Pro | 200 turn | per **task** |

**Tidak satu pun** membagi budget per agen — kita satu-satunya. Karena 1 turn kita ≈ 1,34 call, 250 step ≈ 186 turn kita (tetap 4,6× pool 40).

**Harness SWE-bench melaporkan `resolved`/`unresolved`/`empty patch`/`error` sebagai hitungan terpisah** dan menyatakan eksplisit bahwa kegagalan "never remove anything from the total". EXP-003 kita meruntuhkan semuanya jadi satu angka 8/8/6 — itu kelemahan terbesar write-up saat itu, dan alasan field `truncated` sekarang ada.

**METR:** jangan pilih satu angka budget, **ukur akurasi sebagai fungsi budget** dan laporkan titik 50%. Itu yang menggantikan "kenapa 40?" dengan pengukuran.

⚠️ **Satu sitasi partner TERVERIFIKASI SALAH:** brief mengutip Olausson et al. (2023) sebagai "67% → 82% dengan execution feedback". Teks lengkap paper (ar5iv, 2,2 MB) **tidak memuat string `82%`**; keuntungan yang dilaporkan adalah **"up to 8%"**. Sudah dikoreksi di `docs/RESEARCH_VERIFIER_20260929.md` — **jangan kutip 67/82**. Tiga sitasi lain benar (Self-Debug +2–3%/+12%, Reflexion 91%, Self-Refine ~20%). `tools/verify_citations.py --self-test` mem-pin kasus ini.

**Biaya terukur:** rata-rata **$0.0696/run** worst-case, **$0.0242** cache-aware — 2,3% / 0,8% dari cap $3. Jadi klaim tesis harus *"budget kita dipecah per-act"*, **bukan** "kita tidak punya cost cap".

⚠️ **Semua run kita (termasuk EXP-003) memakai model GRATIS** (`oc/space-bunny-free`). Biaya $0 di CSV itu **akurat, bukan bug** — rate card $0 yang disengaja. Run final harus pakai model berbayar untuk RQ3.

⚠️ **Jebakan median vs mean:** MEMORY mencatat review 291 K vs planning 140 K (2,08×, **median**); data yang sama memberi 276 K vs 211 K (1,31×, **mean**). Keduanya benar — tulis statistik mana yang dipakai di tabel tesis, atau dua dokumen sendiri akan tampak bertentangan.

---

## ⚙️ Konfigurasi Aktif (`.env`)

| Key | Nilai | Catatan |
|---|---|---|
| `OPENCODE_MODEL` | `oc/space-bunny-free` | via 9router |
| `OPENCODE_BASE_URL` | `http://localhost:20128/v1` | 9router harus hidup |
| `TOOLCALL_ENABLED` | `true` | edit-then-diff |
| `TOTAL_TOOL_TURNS` | `40` | pool per strategi: direct 40; planning 20+20; review 13+13+14 |
| `MAX_TOOL_TURNS` | `20` | fallback per-act, hanya jika TOTAL=0 |
| `BUDGET_MODE` | `per_act` | `per_task` = mode referensi; kurva memakai per_task |
| `BUDGET_FLOOR_PER_ACT` | `0` | hanya berlaku di per_task; kurva memakai 10 |
| `COST_LIMIT_USD` | `3.0` | pengaman dolar per task (referensi SWE-agent $3) |
| `PRICING_MODEL_OVERRIDE` | *(kosong)* | isi `deepseek-v4-flash` → token model gratis dihargai rate card berbayar (**estimasi**) |
| `TOOL_OUTPUT_MAX_CHARS` | `2000` | cap per hasil tool, head+tail |
| `MAX_REVISION_TURNS` | `1` | batas ronde revisi |
| `API_TIMEOUT` | `600` | dinaikkan dari 180 |
| `PROMPT_CACHE_LAYOUT` | `true` | prefix caching terbukti nyata (~90% hit) |
| `SOURCE_CONTEXT_ENABLED` | `false` | agen eksplorasi pakai tool |

> **Catatan:** `.env` saat ini `BUDGET_MODE=per_act`, `BUDGET_FLOOR_PER_ACT=0`,
> `REVISION_TOOL_TURNS=8`. Driver kurva **menimpa** nilai-nilai ini per level lewat
> `run_with_env.py --set`, jadi `.env` tidak berubah saat sweep.

---

## 🚨 Jebakan yang Sudah Memakan Waktu

1. **Env drift** — shell mengekspor `OPENCODE_API_KEY` berisi literal `${NINEROUTER_API_KEY}` (21 char) yang menimpa key asli di `.env` (35 char) → `401`. **Selalu** jalankan lewat `tools/run_with_env.py`, jangan `main.py` langsung.
   - Shell juga mengekspor `TOTAL_TOOL_TURNS=60` dan `API_TIMEOUT=180`, menimpa `.env` (40/600). Ini **pernah membuat test lulus palsu**: `test_strategies` assert literal `60` dan kebetulan cocok dengan nilai shell, bukan `.env`.
2. **Reload modul `config` di test** — `tests/test_response_utils.py` memanggil `importlib.reload(config)`, yang membuat `config.Config` jadi kelas **baru** sementara modul yang sudah `from config import Config` tetap memegang kelas **lama**. `monkeypatch.setattr("config.Config", ...)` akan **meleset**. Patch setiap modul yang memegang binding sendiri (`agents.budget`, `agents.base`, `agents.registry`, `agents.tools`, `strategies.review_strategy`).
3. **9router harus hidup** di `localhost:20128` sebelum run.
4. **Jangan `.strip()` sebuah diff** — baris konteks terakhir bisa berupa spasi; strip membuat hunk corrupt.
5. **Jangan unescape `\n` pada diff yang sudah punya struktur baris** — kode sumber di dalam diff boleh mengandung literal `\n` (mis. `mark_safe('\n'.join(...))` di `django/forms/widgets.py`). Menggantinya jadi newline asli **membelah baris konteks**, dan potongan keduanya kehilangan prefix → `git apply` bilang "corrupt patch at line N". Terukur di EXP-20260928-003: 4 patch, semuanya artefak mentahnya applyable.
   - Korupsi ini dulu **tak terlihat** karena `_check_patch_syntax` menerima baris tanpa prefix, lalu `normalize_patch_headers` menulis ulang `@@` agar cocok dengan body yang sudah rusak → statusnya `VALID`/`NORMALIZE`, yang **dihitung sebagai patch valid**. Satu bug merusak dua metrik.
   - Kalau menemukan patch corrupt: bandingkan `artifacts/<id>/<strategy>/patch.txt` (diff mentah git) dengan `patches/<id>_<strategy>.txt` (yang disubmit). Kalau mentahnya applyable, yang disubmit yang rusak.
6. **Hash repo harus 40 karakter** — cache di `datasets/repos/<owner>/<name>/<commit>`.
7. **Working tree kotor saat run itu normal** — strategi berbagi satu repo per issue, jadi `git apply --check` gagal di tengah run.
8. **PowerShell 5.1** tidak mendukung `&&`; kutip bersarang sering gagal parse (tulis ke `.ps1` lalu `-File`). Output `python` yang di-redirect sering jadi UTF-16 — baca filenya, jangan andalkan stdout.
9. **`Tee-Object`/redirect PowerShell menulis UTF-16LE** — Python yang membacanya sebagai UTF-8 mendapat byte null di setiap karakter lain (`B U D G E T`), sehingga **regex tidak pernah cocok**. Deteksi BOM `\xff\xfe` sebelum decode. Ini pernah membuat watcher melaporkan "1/9" saat 7 run sudah selesai.
10. **Jangan percaya `patch_status=VALID` sebagai "resolved"** — VALID hanya berarti patch well-formed dan lolos `git apply`. Resolved hanya bisa ditentukan Modal.
11. **Verifikasi atribusi act sebelum mengklaim penyebab.** Sempat kuklaim "act revisi mengedit 3×" padahal itu act base — karena `tool_calls.jsonl` tidak memisahkan act. Batas act harus direkonstruksi dari urutan role (reviewer pertama = akhir act executor base). Lihat `tools/analyze_run_anatomy.py`.
12. **`json.loads()` pada seluruh respons model rapuh** — model membungkus JSON dengan prosa atau menghasilkan JSON sedikit rusak. Ekstraksi harus tahan terhadap keduanya, dan default-nya harus **menolak** (aman), bukan menyetujui.
13. **Health check bisa membatalkan run berjam-jam dalam 2 menit** — dan tetap keluar `rc=0`, jadi sweep menganggapnya sukses. Selalu cek `predictions/*.jsonl` per level, jangan percaya exit code saja.

---

## 📂 Path Penting

| Path | Fungsi |
|------|--------|
| `src/agents/budget.py` | Pool tool-turn per strategi |
| `src/providers/tool_loop.py` | Loop tool bersama 3 provider |
| `src/strategies/review_strategy.py` | Loop review + re-review revisi |
| `src/agents/tools.py` | Definisi tool + guard (termasuk `[tests unavailable]`) |
| `tools/run_with_env.py` | **Wrapper wajib** — membuat `.env` menang |
| `tools/preflight_modal.py` | Replikasi kontrak `git apply` Modal secara lokal |
| `docs/HANDOFF_20260929.md` | **Detail sesi terakhir + plan berikutnya** |
| `docs/HANDOFF_20260928.md` | Sesi sebelumnya (latar bug retry-reset-budget) |
| `docs/RUNBOOK.md` | Prosedur menjalankan eksperimen |
| `tools/analyze_run_anatomy.py` | Anatomi per-run: act, edit base vs revisi, verdict, retry |
| `tools/check_sweep_state.py` | Status per level kurva; mendeteksi level yang abort |

---

## 💡 Tips untuk Sesi Berikutnya

1. **Baca `docs/HANDOFF_20260928.md` dulu** — berisi plan lengkap dan bug yang harus diperbaiki.
2. **Perbaiki bug retry sebelum run apa pun** — kalau tidak, budget tidak benar-benar ditegakkan.
3. **Selalu** lewat `tools/run_with_env.py`.
4. **Cek cap warning** di `results/EXP-*/logs/experiment.log` — kalau ada, ada yang terpotong.
5. **Verifikasi dengan `--resume`** bila run terputus.

---

## 📝 Catatan

- **Commit sudah di-push:** `4d89434`, `8e6db35`, `97a71f7`, `dfc9fa8`, `973172a`, `3cbd9d1` (branch `19/toolcall-commandcode`).
- **Temuan untuk skripsi:** reviewer tanpa execution feedback tidak menambah kemampuan verifikasi; test tersembunyi adalah oracle yang tidak bisa digantikan penalaran.
- **Temuan tambahan (2026-09-28):** retry yang membungkus loop — bukan request — membatalkan batas budget. Satu act bisa memakai 180 turn alih-alih 60. Ini kelas bug yang mudah terlewat karena tidak muncul sampai timeout benar-benar terjadi.
- **Temuan tambahan (2026-09-29):** bug di jalur patch dapat **memanipulasi hasil penelitian secara diam-diam**. `_normalize_newlines` merusak 4 patch di EXP-003, dan pelabelannya salah **dua kali** — patch rusak disebut "VALID" *dan* "NOT_APPLYABLE", sehingga angkanya tampak masuk akal (patch tidak apply = model salah), padahal pipeline-nya yang merusak. **Pelajaran metodologis:** verdict yang dihasilkan pipeline yang sama yang memproduksi artefak tidak boleh dipercaya begitu saja; verifikasi silang dengan `git apply` pada checkout bersih.
- **Sudah dievaluasi (2026-09-29):** `EXP-20260928-003` dijalankan di Modal SWE-bench harness. Hasil: direct 8/10, planning 8/10, review 6/10.
- **Temuan tambahan (2026-09-29, evaluasi):** kegagalan `review` di 10924 & 11001 **bukan** kegagalan penalaran — keduanya artefak pembagian budget. Di 11001 reviewer mendiagnosis dengan benar tapi act revisi hanya dapat 1 turn (cukup untuk *membaca*, tidak untuk *mengedit*). Di 10924 executor dipotong di cap 15 turn lalu patch setengah jadi disetujui reviewer (false approval). **Implikasi untuk skripsi:** dengan budget 40, strategi 3-act (`review`) berada di bawah strategi 2-act (`planning`) bukan karena review tidak berguna, tapi karena split `total//n` menghukum strategi dengan lebih banyak act. Kalau `review` ingin diuji secara adil, act revisi butuh jatah minimum yang terjamin (mis. reservasi eksplisit), atau total budget per strategi dinaikkan proporsional terhadap jumlah act.
- **Implikasi metodologis:** `APPROVED` tidak berkorelasi dengan `resolved` (9 approved → 6 resolved). Reviewer tanpa oracle tidak bisa memverifikasi; verdict-nya tidak boleh dipakai sebagai sinyal kualitas patch di analisis.

---

**Last working state:** commit `c2e71d7` — **299 test lulus**. Kurva budget **datar** (40 = 100 = 2/3 ketiga strategi). Audit pipeline: 3 bug diperbaiki, 1 masalah validitas belum.

**Langkah berikutnya (prioritas):**

1. **KEPUTUSAN USER — masalah validitas review.** 78% penolakan reviewer bersandar pada file test yang tidak pernah dinilai harness. Ini menjelaskan kenapa act revisi 0 edit (sering memang tak ada yang perlu diperbaiki). Pilihannya:
   - **(a) Larang reviewer menolak atas dasar file test** — perubahan perilaku eksperimen, perlu run ulang untuk mengukur efeknya.
   - **(b) Biarkan, tapi laporkan sebagai temuan** — `review` diakui sebagai "executor + ronde review yang sebagian sia-sia", bukan "review + revisi".
   - **(c) Ukur dulu**: jalankan 3 issue review dengan reviewer diberi tahu file test di-strip harness, lihat apakah verdict berubah.
   Rekomendasi: **(c)** — murah, dan memberi data sebelum mengubah desain.

2. **Perbaiki `budget.py`** agar act revisi direservasi di mode `per_task`. Tapi lihat temuan kritis dulu: kalau penolakan sering salah, reservasi saja tidak akan menolong — revisi perlu **alasan yang sah** untuk mengedit.

3. **Level 200** hanya kalau keputusan user ingin memisahkan "`11019` butuh >100 turn" dari "kegagalan kapabilitas". ~4 jam.

4. **Model berbayar untuk run final** (RQ3). Sebelum itu, selesaikan dua bug laten yang relevan hanya saat berbayar: `COST_LIMIT_USD` bisa dilewati pada model ber-rate nol / jalur non-tool, dan kegagalan mid-act membuang biaya yang sudah terpakai (keduanya di `docs/AUDIT_PIPELINE_20260930.md`).

5. **Sebelum run besar apa pun:** `verify_eval_consistency.py` + `check_sweep_state.py`.
