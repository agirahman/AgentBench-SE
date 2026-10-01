# 🧠 AI Agent Memory — AgentBench-SE

**Last Updated:** 2026-10-01 03:40 WIB
**Status:** **SIAP run 50 issue** (setelah 2 audit partner, **5 blocker diperbaiki**). Budget 200 turn, total sama 200/200/200, **revisi 48 turn (4 putaran × 2 act × 6)**. Trajectory penuh + guard context window. **426 test lulus.** Skrip: `tools/run_final_sweep.py`. **Menunggu izin user untuk 150 run.**
**Active Branch:** `19/toolcall-commandcode`
**Handoff sesi terakhir:** [`HANDOFF_20261001.md`](HANDOFF_20261001.md)
**Detail audit sesi terakhir:** [`AUDIT_OPS_PARTNER.md`](AUDIT_OPS_PARTNER.md) · [`AUDIT_SCALE_PARTNER.md`](AUDIT_SCALE_PARTNER.md) · [`AUDIT_PROMPTS_PARTNER.md`](AUDIT_PROMPTS_PARTNER.md) · [`AUDIT_RUN_TESTS_PARTNER.md`](AUDIT_RUN_TESTS_PARTNER.md) · [`RESEARCH_BUDGET_20260929.md`](RESEARCH_BUDGET_20260929.md)

> ⛔ **GATE — WAJIB KONFIRMASI USER:** Jangan jalankan run besar (50 issue / multi-jam)
> tanpa persetujuan eksplisit dari user. Boleh tanpa konfirmasi: unit test, smoke test
> kecil (≤3 issue, 1 strategi), dan pekerjaan kode/dokumentasi.

> ⚠️ **KOREKSI PENTING (2026-09-29 malam):** klaim lama di dokumen ini — *"perbaikan act
> revisi BEKERJA, 3× edit_file"* — **SALAH**. Analisis ulang (`tools/analyze_run_anatomy.py`)
> membuktikan act revisi membuat **0 edit di SEMUA run** (level 40 dan 100). Tiga edit yang
> dulu kuklaim itu milik **act pertama (base)**, bukan act revisi. Lihat §"Koreksi act revisi".

---

## ✅ KESIAPAN RUN 50 (verifikasi terakhir 2026-10-01 05:00)

**Satu perintah memverifikasi semuanya:** `python tools/readiness_report.py` → **READY, 9/9**.
Ia **menjalankan unit test suite juga** (versi pertamanya tidak, dan itu blocker: gate bisa
hijau sementara suite merah). Terbukti **bisa gagal**: `tools/_prove_gate_fails.py`
menyuntikkan test rusak → gate melaporkan **NOT READY**, exit 1.

| Verifikasi | Hasil |
|---|---|
| Unit test | **426 lulus** |
| Preflight repo | **50/50 pristine** |
| Fairness budget | **FAIR** — 200/200/200 |
| Putaran revisi | Setiap act revisi **6+6** (kebutuhan terukur: 6) |
| Model efektif | Sweep memanggil **`cbai/deepseek-v4.1-flash`** (berbayar) |
| Disk | 211 MB worst-case vs **98 GB free** |
| Resume | `--resume` tanpa `--exp-id` → **exit 2**; dengan `--exp-id` diteruskan |
| Sweep command | 150 run, semua flag eksplisit |
| Cross-check biaya | $0,551 vs $0,582 = **-5,28%** |

**Perintah run:**
```
python tools/run_final_sweep.py
```

**Estimasi:** ~$7–11, ~14–17 jam (terukur dari pilot, bukan tebakan — token tumbuh
sublinear terhadap cap karena 200 itu *batas*, bukan target).

**Kalau terputus:**
```
python tools/run_final_sweep.py --resume --exp-id <EXP-id>
```
Sweep **tidak** mengulang dari nol; savepoint per run dibaca dan yang sudah selesai dilewati.

---

## 📋 Current Project State

| Aspect | Status | Notes |
|--------|--------|-------|
| **Provider** | ✅ Done | OpenCode via 9router (`oc/space-bunny-free`), model gratis untuk testing |
| **Dataset** | ✅ Done | 50 issues: django(10)+sympy(10)+scikit(10)+matplotlib(10)+requests(6)+seaborn(4) |
| **Repo cache** | ✅ Done | 50 instance pristine di `datasets/repos/` |
| **Mekanisme patch** | ✅ Done | edit-then-diff: agen mengedit file, patch diambil dari `git diff` |
| **Tool calling** | ✅ Done | Loop bersama di `providers/tool_loop.py` (3 provider berbagi) |
| **Budget tool-turn** | ✅ Done | `agents/budget.py` — total sama per strategi, sekarang **200** (skala referensi) |
| **Pre-flight validator** | ✅ Done | `tools/preflight_modal.py` — replikasi kontrak Modal secara lokal |
| **Rate-limit handling** | ✅ Done | Backoff 429 + circuit breaker |
| **Test suite** | ✅ Done | **426 lulus** (dari 408). **Lulus di env bersih MAUPUN env kotor** — dulu tidak, dan itu bug |
| **Retry vs budget** | ✅ **FIXED** | Retry per-request di dalam tool loop (commit `dfc9fa8`) |
| **Konteks per turn** | ✅ Done | `TOOL_OUTPUT_MAX_CHARS=2000`, head+tail (dari 8000 head-only) |
| **Korupsi patch** | ✅ **FIXED** | `_normalize_newlines` merusak diff yang mengandung literal `\n` (commit `3cbd9d1`) |
| **Evaluasi EXP-003** | ✅ Done | Modal SWE-bench harness: **30/30 patch applied**, hasil di `EVAL_NOTE.md` |
| **Reviewer oracle** | ⚠️ Terbatas | `run_tests` selalu gagal; reviewer hanya bisa menalar. Spike 2026-09-29: test pre-existing lokal **murah (2–3 s) tapi tidak mendiskriminasi** — lihat `docs/SPIKE_ORACLE_20260929.md` |
| **Reserve act revisi** | ✅ **TERBUKTI MENGEDIT** | Pilot `EXP-20260930-415`: revisi **6 turn, 2 edit** (sebelumnya 0 edit). Config sweep kini `48`/4 putaran |
| **Verdict parsing** | ✅ **FIXED** | `_extract_verdict` tahan prosa + JSON rusak + negasi; 94 respons diaudit, 0 mismatch |
| **Klasifikasi patch kosong** | ✅ **FIXED** | Patch kosong ≠ kegagalan strategi; `EMPTY_PATCH` dilaporkan terpisah (bug wrapper evaluasi) |
| **Verifikasi eval** | ✅ Done | `tools/verify_eval_consistency.py` — bandingkan wrapper vs harness resmi; sepakat di semua level |
| **`--resume` mengulang kegagalan** | ✅ **FIXED** | Baris error dulu dihitung "selesai" → instance yang mati tidak pernah diulang. Sekarang hanya run ber-patch yang dianggap selesai |
| **Label kegagalan** | ✅ **FIXED** | Semua exception dulu distempel `TIMEOUT`; sekarang `RATE_LIMIT`/`PROVIDER_ERROR`/`ERROR` |
| **Akurasi vs budget** | ✅ **FIXED** | Patch kosong tidak lagi masuk hitungan (bucket 0-15: 67% → 100%) |
| **Validitas review** | ✅ **FIXED** | Klaim "78% penolakan bersandar file test" **dikoreksi**: angka sebenarnya **12,5%**. Lihat §"Koreksi" |
| **Race EXP-ID** | ✅ **FIXED** | Lock `O_CREAT\|O_EXCL` + deteksi lock basi; di Windows errno `EACCES`, bukan `EEXIST` |
| **Split paralel** | ✅ Terverifikasi | `tools/verify_split.py`: 26 + 24 = 50, overlap 0 |
| **`--resume` sweep** | ✅ **FIXED** | `run_final_sweep.py` dulu **tidak meneruskan** `--resume`/`--exp-id` → restart = 150 run diulang (B1) |
| **Jendela tagihan** | ✅ **FIXED** | `sweep_started.json` dulu ditimpa tanpa syarat → jendela attempt pertama hilang (B2) |
| **Cross-check biaya** | ✅ **FIXED** | `read_actual_bill.py --compare` dulu mencari field yang tidak pernah ada → RQ3 tak bisa divalidasi (B4). Kini: $0,551 vs $0,582 = **-5,28%** |
| **Paritas tool** | ✅ **FIXED** | `direct` dulu **tidak punya `run_tests`** — confound: planning/review boleh verifikasi, direct tidak |
| **Trajectory penuh** | ✅ Done | Setiap turn (teks + reasoning + tool + hasil) → `trajectory.jsonl`/`.md`, dibawa di `AgentMessage` |
| **Guard context window** | ✅ Done | Overflow dideteksi & ditandai **fatal** (tidak di-retry, tidak ditagih ulang) |
| **Config awal run** | ✅ **FIXED** | `experiment.yaml` dulu ditulis setelah run selesai → crash = config hilang; sekarang `on_experiment_start` |
| **Kurva budget** | ✅ **DATAR** | Level 40 = level 100 = **2/3 ketiga strategi**. Level 200 tidak jalan (health check) |
| **Evaluasi Modal (5 issue)** | ✅ Done | Ketiga strategi **4/5 (80%)**, himpunan resolve **sama persis**. `11019` gagal di ketiganya |

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

**Status jujur (per 2026-09-29):** perbaikan reserve **belum terbukti bekerja**. Yang terbukti hanya plumbing-nya (act revisi benar-benar dipanggil, `inference_count` naik, executor yang menjalankannya — bukan reviewer).

> ✅ **UPDATE (2026-10-01): SUDAH TERBUKTI.** Pilot `EXP-20260930-415` menunjukkan act revisi
> **benar-benar mengedit** (6 turn, 2 edit) — diverifikasi `tools/audit_revision_edits.py`.
> Yang salah bukan plumbing-nya, tapi **konfigurasi**: reserve 32 dengan `MAX_REVISION_TURNS=1`
> memberi **4+4** per act untuk 4 putaran, di bawah kebutuhan terukur **6**. Kini
> `REVISION_TOOL_TURNS=48` / 4 putaran. Blok di atas tetap ada sebagai catatan sejarah.

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

**Klaim lama: "7 dari 9 verdict `NEEDS_REVISION` (78%) menyebut file test."** Angka itu
**tidak salah sebagai hitungan penyebutan**, tapi **salah sebagai dasar kesimpulan** — "menyebut"
bukan "menentukan". Diukur dengan `tools/analyze_rejection_basis.py`: **6 dari 8 menyebut**,
tapi hanya **1 dari 8 (12,5%)** yang **seluruh** `issues_found`-nya soal file test
(`EXP-20260928-003 django__django-11001 msg[13]`).

**Implikasi untuk tesis (versi terkoreksi):** `review` **tetap** boleh diklaim mengukur
review+revisi. Yang perlu dicatat hanya bahwa **reviewer sesekali menyebut cacat file test yang
tidak dinilai harness**, dan itu **tidak mengubah verdict** di hampir semua kasus. Alasan act
revisi dulu "0 edit" bukan karena penolakan salah, tapi karena **jatahnya tidak cukup**
(1–4 turn; lihat §blocker budget) — dan itu **sudah diperbaiki** (`REVISION_TOOL_TURNS=48`).

**Status:** **tidak ada keputusan user yang dibutuhkan.** Setelah diukur, masalahnya kecil
(12,5%, bukan 78%). Melarang reviewer menolak atas dasar file test akan mengubah perilaku
eksperimen dan **tidak sepadan** untuk 1 dari 8 kasus. Cukup dicatat sebagai temuan di tesis.

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

##### 🔁 VERIFIKASI ADVERSARIAL (2026-09-30) — 4 KRITIS, semua di perbaikanku SENDIRI

Setelah audit, partner **tidak** diberi tugas mengaudit lagi — mereka diberi tugas
**membongkar perbaikanku**. Ini disengaja: kesalahan pada perbaikan lebih berbahaya
daripada bug aslinya, karena kita sekarang mengandalkannya.

**Hasil: perbaikan BLOCKER-nya benar, tapi 3 dari 4 perbaikan TIDAK menutup kasus
yang jadi alasan keberadaannya.** Laporan: `VERIFY_FIXES_P7.md`, `VERIFY_FIXES_P8.md`.

| # | Cacat pada perbaikanku | Tingkat | Kenapa berbahaya |
|---|---|---|---|
| 1 | `--resume` **masih** kehilangan data kalau crash **sebelum export pertama** | **KRITIS** | CSV ditulis **sekali di akhir** → crash = tidak ada CSV → merge tidak memulihkan apa pun. **Ini kasus UTAMA `--resume` ada.** Dibuktikan dengan `KeyboardInterrupt` nyata: 5 issue, mati di ke-3 → CSV berisi 3, savepoint berisi 5 |
| 2 | CSV **terpotong** → `pd.read_csv` gagal → **semua** baris lama dibuang | **KRITIS** | `to_csv` tidak atomik, dan menulis CSV adalah langkah **terakhir** — jadi ini gangguan **paling mungkin**, bukan paling jarang. Terbukti: 9 baris → 1 |
| 3 | Pemeriksaan kelengkapan **alarm palsu** pada hasil yang sah | **KRITIS** | Run yang selesai **tanpa patch** dihitung "hilang". Di data nyata (EXP-20260824-005): 84 ditandai, **44 di antaranya sah**. Alarm palsu = alarm asli diabaikan |
| 4 | `INCOMPLETE.json` **tidak pernah dihapus** | **KRITIS** | Resume yang berhasil meninggalkan alarm permanen. Alarm basi tak bisa dibedakan dari alarm hidup |

**Perbaikan:**

- Rekonstruksi dari **savepoint jsonl** (bukan hanya CSV) + rekonsiliasi: savepoint adalah
  **otoritas** soal run mana yang ada; CSV hanya lebih kaya per baris
- Tulis CSV **atomik** (`tmp` + `os.replace`) + simpan `.bak`
- Predikat terpisah: `_is_finished_entry` (untuk resume) vs `_is_completed_entry`
  (untuk kelengkapan) — **menggabungkan keduanya itulah bug-nya**
- `INCOMPLETE.json` dihapus saat sukses; laporan pisahkan "ran but FAILED" vs "never ran"

**3 cacat kecil, semuanya terkonfirmasi eksekusi:**

| Cacat | Kenapa berbahaya |
|---|---|
| `bool(NaN)` = **True** | Sel `generated` kosong dibaca sebagai **sukses** — setiap baris hasil pemulihan akan mengaku punya patch |
| `str(NaN)` = `'nan'` | String **tidak kosong** → lolos guard `patch.strip()` yang seharusnya mendeteksi patch kosong |
| Guard rate **raise** | Terjadi **setelah** Modal dijalankan & dibayar → membuang evaluasi yang sudah selesai karena masalah pembukuan. Sekarang: rate ditahan (`null`), baris per-instance tetap ditulis |

**Pelajaran proses:** memperbaiki bug lalu memverifikasi perbaikan itu **wajib**, dan
paling baik dilakukan pihak yang **tidak** menulis perbaikannya. Tiga dari empat
perbaikanku tidak menutup kasus utamanya — dan aku tidak akan menemukannya sendiri.

---

##### ✅ KEPUTUSAN DIAMBIL: reserve revisi **DIPOTONG** dari pool (opsi b)

Keputusan user: **perbandingan turn harus sama di semua strategi**. Diimplementasikan
sebagai **carve-out** — reserve dipotong **dari** `TOTAL_TOOL_TURNS`, bukan ditambahkan.

**Versi terkini (setelah audit partner + keputusan user naikkan budget ke skala referensi):**

| Strategi | Act base | Base | Revisi | **TOTAL** |
|---|---|---|---|---|
| direct | 1 act | 200 | 0 | **200** |
| planning | 190+10 | 200 | 0 | **200** |
| review | 152 (50+51+51) | **152** | **48** (4 putaran × 6+6) | **200** |

**Setiap strategi dapat total 200 turn.** Kemenangan `review` tidak bisa dijelaskan oleh
budget lebih besar — confound-nya hilang.

Angka **200** diambil dari referensi, bukan tuning: SWE-bench Pro **200 turn/task**,
mini-SWE-agent **250 step**, OpenHands 500 iterasi. Lihat
[`RESEARCH_BUDGET_20260929.md`](RESEARCH_BUDGET_20260929.md).

**Trade-off yang disadari:** total sama, **base flow tidak** (200/200/152). Review
menyisihkan 48 turn-nya sendiri untuk merevisi, seperti orang yang menganggarkan 200 aksi.
Review yang tidak perlu merevisi **memakai lebih sedikit** dari jatahnya. Pertanyaan yang
dijawab eksperimen: *"dengan 200 turn yang sama, strategi mana yang terbaik?"*

**Verifikasi:** `tools/check_budget_fairness.py` → **FAIR** di kedua mode, dan
`tools/verify_revision_rounds.py` → setiap act revisi dapat **6+6** (di atas kebutuhan
terukur 6). **426 test lulus.**

**Detail implementasi yang penting:**

- `from_config(with_revisions=True)` wajib untuk memotong reserve, dan **hanya review**
  yang memakainya. Kalau dipotong untuk semua, direct & planning dapat 152 sementara review
  200 — ketidakadilan yang sama, arah berlawanan. Ada test yang mengunci ini.
- Reserve lebih besar dari pool **di-clamp** (bukan dipatuhi): kalau dipatuhi, base act
  dapat pool negatif dan semua act jatuh ke floor 1 → review tidak bisa apa-apa.
- `task_total` adalah **property**, bukan field: nilainya jumlah sisa, jadi melaporkan
  total awal setelah dibelanjakan akan salah.
- **4 test lama** mengunci invarian lama (base sama, reserve ekstra) — **ditulis ulang**
  dengan docstring yang menjelaskan apa yang berubah, supaya pembalikan ini tidak
  disalahartikan sebagai regresi.

**⚠️ Semua angka `review` di disk (3 eksperimen sebelumnya) memakai `REVISION_TOOL_TURNS=0`**
→ act revisinya dapat 1 turn, 0 edit → **tidak ada satu pun yang mengukur review+revisi.**
Angka-angka itu **tidak komparabel** dengan run 50 yang akan datang.

**Catatan:** pilot `EXP-20260930-415` memakai **200/200/200** tapi **revisi 32 dengan 1 putaran**
(16+16). Revisi di sana **benar-benar mengedit** (6 turn, 2 edit) — jadi plumbing-nya terbukti,
tapi konfigurasinya bukan yang akan dipakai sweep (`48`, 4 putaran).

##### ✅ HASIL EVALUASI MODAL (pilot verifikasi, 200 turn) — `EXP-20260930-415`

| Strategi | Resolved | Empty | Harness error | Rate |
|---|---|---|---|---|
| direct | **4/5** | 0 | 0 | **80%** |
| planning | **4/5** | 0 | 0 | **80%** |
| review | **4/5** | 0 | 0 | **80%** |

Per instance — ketiganya resolve himpunan yang **sama persis**:

| Instance | direct | planning | review |
|---|:-:|:-:|:-:|
| django-10914 | PASS | PASS | PASS |
| django-10924 | PASS | PASS | PASS |
| django-11001 | PASS | PASS | PASS |
| **django-11019** | **fail** | **fail** | **fail** |
| django-11039 | PASS | PASS | PASS |

**Baseline naik: 2/3 (67%) → 4/5 (80%)**, dan kini pada **lima** instance, bukan tiga.

##### 🎯 PERTANYAAN LAMA TERJAWAB: `11019` = **BATAS KAPABILITAS**, bukan budget

Sejak `EXP-20260929-003` pertanyaan ini menggantung: apakah 11019 butuh lebih banyak turn, atau
memang di luar kemampuan model? Sekarang terukur — **0 truncation** dan act-nya **berhenti sendiri**:

| Strategi | Turn dipakai | Diberi |
|---|---|---|
| direct | **41** | 200 |
| planning | 11 + **35** | 200 |
| review | 13 + **44** + 18 | 200 |

Ketiganya memakai **~20% anggaran**, menghasilkan patch **VALID** yang menyentuh file yang benar,
tapi **tetap salah secara semantik**. Gold patch lulus 1/1 di instance ini (dibuktikan sebelumnya)
→ **bukan artefak harness**.

**Konsekuensi untuk tesis:** `11019` boleh dilaporkan sebagai **batas kapabilitas** pada model dan
budget ini — tidak perlu lagi dilaporkan sebagai "terkonfound budget".

##### 🔴 AUDIT PARTNER (2 ronde) — 4 BLOCKER OPS + 1 BLOCKER BUDGET

Dua partner di Herdr pane mengaudit pipeline 150-run. **Semuanya sudah diperbaiki** (commit `eae63b3`).

| # | Blocker | Bukti | Perbaikan |
|---|---|---|---|
| **B1** | `run_final_sweep.py` **tidak meneruskan `--resume`/`--exp-id`** → restart = direktori EXP baru + **150 run diulang** | `build_cmd` diperiksa; `main.py:64-80` sudah punya flag-nya | Diteruskan + **gate**: `--resume` tanpa `--exp-id` → exit 2 |
| **B2** | `sweep_started.json` **ditimpa tanpa syarat** → jendela tagihan attempt pertama **hilang** | `run_final_sweep.py:249` | Resume **append** ke `attempts`; sweep baru **mengarsipkan** |
| **B3** | Docstring bilang `ACT_TIMEOUT_SECONDS=1800`, kodenya **3600**; 1 run review = 11 act → bisa **5 jam tanpa batas total** | grep: tidak ada budget keseluruhan | Docstring dikoreksi + jalur recovery dinyatakan |
| **B4** | `read_actual_bill.py --compare` **tidak pernah bisa jalan** — mencari `total_cost_usd` yang tidak pernah ditulis | Diuji pada **8 eksperimen** → 0/8 menemukan biaya | Baca dari CSV `cost_usd_actual`; **menolak** menyajikan rata-rata sebagai total |

**Verifikasi B4 hidup:** model kita **$0,551462** vs tagihan nyata **$0,582197** → **-5,28%**.

##### 🔴 BLOCKER BUDGET: review mengukur konfigurasi yang **tidak ada yang memilih**

| | Nilai | Masalah |
|---|---|---|
| `REVISION_TOOL_TURNS` | 32 | didokumentasikan sebagai *"4 putaran × 8"* — **mustahil secara aritmetika**: 4 × 2 act × 8 = **64** |
| `MAX_REVISION_TURNS` (`.env`) | **1** | sweep **tidak pernah meng-override** → review hanya **1 putaran** |
| Akibatnya | | 32 dibagi 2 act = **16+16** untuk 1 putaran; kalau 4 putaran → **4+4**, **di bawah kebutuhan terukur 6** |

**Pengukuran yang menentukan** (`tools/audit_revision_edits.py`, `EXP-20260930-415`): act revisi yang
**berhasil mengedit** memakai **6 turn, 2 edit**. Jadi `min_grant ≥ 6`.

**Perbaikan:** `REVISION_TOOL_TURNS=48` = **4 × 2 × 6**, dan `MAX_REVISION_TURNS=4` **dipass eksplisit**
oleh sweep supaya `.env` tidak bisa drift lagi.

**Yang juga diperbaiki karena temuan ini:**

- `check_budget_fairness.py` **tidak bisa melihat** masalah di atas — ia mensimulasikan **1 putaran**
  dan default-nya 40/8/1. Sekarang menerima jumlah putaran, membaca config, dan **gagal** kalau
  satu act revisi tidak bisa membaca **dan** mengedit. **Total sama itu perlu, tapi tidak cukup.**
- Warning starvation menyala di **100 dari 150 run** dengan pesan yang **sendiri salah**
  ("`REVISION_TOOL_TURNS=0`" padahal 48) — direct & planning memang tidak punya revisi.
- Warning `PROMPT VARIANT MISSING` menyala untuk file yang **ada**: ia membaca hasil kosong dari
  loader yang di-stub sebagai "file hilang". Sekarang bertanya ke **filesystem**.
- `.env` **tidak menyetel** `BUDGET_MODE`/`BUDGET_FLOOR_PER_ACT` → default `per_act`/`0`, padahal
  sweep memaksa `per_task`/`10`. Kini eksplisit.
- Sweep sekarang **GATE pada preflight**, bukan sekadar mengingatkan. Checkout kotor membuat
  `git diff` menangkap perubahan yang **bukan** buatan agen — terlihat seperti agen memecahkan
  issue, dan itu **meninggikan skor secara tak terlihat**.

**Tool baru:** `verify_revision_rounds.py`, `audit_revision_rounds.py`, `audit_revision_edits.py`.

**426 test lulus** (dari 408).

---

**Gejala:** planner **0 tool call di 6 dari 9 run**. Terlihat seperti planner "memilih tidak membaca
kode". **Sebabnya bukan itu.**

**Akar masalah:** `BaseAgent._tool_template()` memuat `<prompt>_tools.md` dan **jatuh ke prompt dasar
secara diam-diam** kalau file itu tidak ada. `planner_tools.md` **tidak pernah ada**, jadi planner
menerima `planner.md` — yang berbunyi *"Output ONLY valid JSON"* dan **tidak menyebut tool sama sekali**.

**Akibatnya 3 instruksi saling bertabrakan** (dibuktikan partner dengan merender prompt nyata —
planner menerima **7.393 karakter**):

| Sumber | Isi |
|---|---|
| `shared_static.md` | *"produce a correct, minimal code change as a unified diff"* + 10 aturan patch |
| `planner.md` (fallback) | *"Output ONLY valid JSON"* |
| system prompt read-only | *"Explore with read_file / grep / list_files"* |

**Perbaikan:** `planner_tools.md` dibuat — mewajibkan bukti **sebelum** hipotesis (cari dengan
`grep`/`list_files` → baca → telusuri mekanisme → baru jawab). Fallback tetap ada (file hilang tidak
boleh membuat sweep crash) tapi sekarang **memperingatkan dan menyebut nama file**.

**Verifikasi end-to-end:** planner **0 → 7 tool call**, dan rencananya menyitir **nomor baris nyata**
(`global_settings.py:307`, `storage.py:283-284`) alih-alih pengetahuan umum.

##### 🔴 3 cacat prompt lain (semua diperbaiki)

1. **`shared_static.md` menyuruh SEMUA role menulis patch.** Header ini di-prepend ke **keempat role**
   (`PROMPT_CACHE_LAYOUT=true` di `.env`, aktif saat pilot). Planner & reviewer read-only disuruh
   "produce a unified diff". Ditulis ulang: menyatakan role berbeda, aturan patch jadi **bersyarat**,
   plus aturan bukti. Tiga contoh diff dihapus (5.893 → 3.660 char); `direct_prompt.md` &
   `executor.md` tetap punya aturan hunk sendiri — ada test yang menjaganya.
2. **4 prompt menyitir `SOURCE CODE (base commit)`** yang **tidak pernah diinjeksi**
   (`Issue.to_agent_prompt()` selalu mengembalikan problem statement saja). Dihapus dari
   `planner.md`, `reviewer.md`, `executor.md`, `direct_prompt.md`.
3. **`_tool_template()` fallback diam-diam** → sekarang memperingatkan.

##### 🔴 2 LUBANG PENEGAKAN (audit partner, dibuktikan konstruktif)

**Lubang 1 — `execute_tool` tidak memeriksa role.** Provider hanya dikirim *schema* di `AGENT_TOOLS`
— itu filter **apa yang ditawarkan**, bukan **apa yang boleh dijalankan**. Partner membuktikan:
`execute_tool("write_file", ...)` **berhasil** untuk reviewer. Jadi premis *"reviewer tidak mengarang
kode"* bergantung pada model tidak menebak nama tool yang tidak ditampilkan.

**Lubang 2 — `run_tests` adalah shell arbitrer** (`shell=True`). Reviewer bisa menulis ulang file
yang sedang ia nilai — dan di **jalur revisi** (`review_strategy.py:201` mengambil diff **setelah**
act reviewer) tulisan itu **masuk ke patch yang dikirim**. Partner membuktikan **3/3 trial bocor**.

**Tidak pernah terjadi di data** (0 dari 20 call reviewer), jadi ini menutup lubang yang bisa
dieksploitasi, bukan memperbaiki kegagalan yang terukur.

**Guard pertamaku SALAH dan ditangkap run nyata:** ia menolak
`cd /tmp && cat > t.py <<'EOF'` — reviewer menulis **probe scratch di LUAR repo**, yang merupakan
verifikasi sah. Batasnya sekarang **"apakah ini menulis KE DALAM repo"**, bukan "apakah ini menulis":
target absolut di luar repo dan `cd` keluar repo **diizinkan**; target relatif & di dalam repo
**ditolak**. `tools/verify_write_boundary.py` menguji 22 kasus di batas itu — **semua lulus**.

**Kontrol negatif mengunci kedua classifier:** percobaan pertamaku menandai **setiap** panggilan
pytest sebagai write (menganggap `2>&1` sebagai redirect), dan percobaan pertama partner
menghasilkan **13 write palsu** dari `->` di dalam string yang di-print.

**408 test lulus** (dari 363).

---

Keputusan user: berhenti memutar-mutar masalah truncation, pakai angka referensi.
`TOTAL_TOOL_TURNS` **40 → 200**.

| Sistem | Limit | Per apa | Sumber |
|---|---|---|---|
| SWE-bench Pro (2025) | **200 turn** | per task | arxiv 2509.16941v1 |
| mini-SWE-agent | **250 step** | per task | `swebench.yaml` L112 |
| OpenHands | 500 iterasi | per task | `config_utils.py` |
| SWE-agent | tanpa step cap, $3 | per instance | `models.py` |

**Pool 40 sebelumnya 5–12× lebih ketat** dari semua cap yang dipublikasikan — itu sebabnya
act terus kehabisan turn di tengah eksplorasi, sehingga hasilnya mengukur budget, bukan
strategi. 200 dipilih dari dua referensi berbasis turn (200, 250) sebagai yang lebih konservatif.

**`REVISION_TOOL_TURNS` 8 → 32** (4 ronde × 8). Diukur, bukan selera: revisi 1 turn tidak
bisa mengedit (EXP-20260928-003), dan **4 turn masih tidak cukup** (pilot 15 run: act revisi
memakai keempatnya untuk membaca, **0 edit**, lalu re-review menyetujui patch yang tidak
berubah — sementara act yang **berhasil** mengedit memakai 6–17 turn).

**Carve-out dipertahankan:** direct 200, planning 200, review 148+10+10 base + 32 revisi =
**200**. Total tetap sama → kemenangan review tetap tidak bisa dijelaskan oleh budget lebih besar.

**Verifikasi:** `check_budget_fairness.py --total 200 --reserve 32` → **FAIR**.
`ACT_TIMEOUT_SECONDS` 1800 → 3600 untuk act yang kini lebih panjang.

##### ✅ TRAJECTORY PENUH: seluruh aktivitas agen terekam

Sebelum ini, jejak satu act hanya **pesan terakhir** + daftar call datar dengan preview
2000 char. Akibat yang **terukur**, bukan dibayangkan:

- `<role>.md` dikunci per-role → di run review, act executor **kedua menimpa yang pertama**
  → percobaan yang ditolak reviewer **tidak ada di artefak mana pun**.
- Tidak ada nomor turn → tidak bisa tahu call mana dari turn mana.
- Reasoning hanya ada untuk respons **final**.

**Sekarang setiap turn terekam:** teks asisten, **reasoning**, tool yang diminta, lalu
setiap hasil tool **utuh** → `trajectory.jsonl` + `trajectory.md` (versi manusiawi), dan
dibawa di `AgentMessage` sehingga **`messages.jsonl` ADALAH trajectory**-nya.

**Cutoff ditandai di dalam rekaman** (`bound_reached` + `stop_reason`) → "selesai sendiri"
vs "dihentikan bound" bisa dibedakan **tanpa grep log** — pembedaan yang dulu membuat
angka 8/8/6 EXP-003 tidak terbaca.

**Reasoning dibaca dari semua nama field** yang dipakai provider (`reasoning_content` /
`reasoning` / `thinking`, string / list / dict bersarang). Membaca hanya
`reasoning_content` diam-diam membuang reasoning provider yang menamainya lain.

**Diverifikasi end-to-end dengan thinking ON** (`EXP-20260930-250`): **15 turn reasoning
terekam, 10 KB**. Uji itu juga **menemukan bug nyata**: penulis lama menghasilkan **0 file
reasoning** ketika respons final tidak punya reasoning — padahal **12 dari 19 turn punya**.
Jadi artefaknya membuat run thinking **terlihat seperti non-thinking**. Diperbaiki: artefak
dibangun dari trajectory. `tools/check_thinking_mode.py` juga diperbaiki (dulu memeriksa
`messages.jsonl` yang tidak punya field itu → selalu melaporkan 0).

##### ✅ GUARD CONTEXT WINDOW (konsekuensi dari 200 turn — sebelumnya tidak ada sama sekali)

Pada 200 turn, satu act bisa mengumpulkan konteks melebihi window (terukur di pool 100:
**166k char** output tool dalam satu act). Sebelumnya **tidak ada penanganan apa pun**.
Overflow akan di-retry `MAX_RETRIES` kali dengan backoff — gagal identik, **ditagih ulang
tiap kali** — lalu dilaporkan sebagai **kegagalan strategi**.

**Sekarang:** dideteksi dari teks pesan (provider berbeda soal status code), ditandai
**fatal sehingga TIDAK di-retry**, act berhenti dan meminta jawaban final dengan output
tool tertua dipangkas, dan berhentinya diberi label `stop_reason=context_limit`. Kalau itu
pun overflow, hasilnya tetap membawa pekerjaan yang sudah dilakukan (bukan raise).

---

Skrip run final: **50 issue × 3 strategi = 150 run**, semua knob dipass **eksplisit**
(bukan diwarisi dari `.env`). Ini bukan gaya penulisan: variabel shell yang diam-diam
mengubah budget adalah persis penyebab hasil lama tidak komparabel.

| Setelan | Nilai | Alasan |
|---|---|---|
| `REVISION_TOOL_TURNS` | **8** | Tanpa ini, arm review tidak benar-benar merevisi |
| `COST_LIMIT_USD` | **3.00** | Nilai referensi mini-SWE-agent & SWE-agent |
| `ACT_TIMEOUT_SECONDS` | **1800** | Backoff retry tidak bisa menggantung satu act berjam-jam |
| `BUDGET_MODE` | `per_task` | Struktur yang dipakai semua referensi |
| `BUDGET_FLOOR_PER_ACT` | 10 | |

**Verifikasi sebelum run:** `python tools/preflight_repos.py` → **50/50 repo pristine**.

**Estimasi:** ~$7–11 dan **~14–17 jam** untuk 150 run (dihitung `tools/estimate_sweep_cost.py`
dari run berbayar di disk, bukan tebakan). Arm review akan **lebih mahal** dari sebelumnya
karena revisi benar-benar terjadi — itu tujuannya, dan itu temuan nyata, bukan cacat.

**Setelah run:** `check_sweep_state.py` (kelengkapan) → evaluasi Modal →
`verify_eval_consistency.py` → `read_actual_bill.py --compare`.

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
| `OPENCODE_MODEL` | `oc/space-bunny-free` | **model gratis** — untuk smoke test. **Sweep meng-override ke `cbai/deepseek-v4.1-flash` (berbayar) lewat `--set`**; RQ3 butuh yang berbayar |
| `OPENCODE_BASE_URL` | `http://localhost:20128/v1` | 9router harus hidup |
| `TOOLCALL_ENABLED` | `true` | edit-then-diff |
| `TOTAL_TOOL_TURNS` | `200` | pool per strategi: direct 200; planning 190+10; review 152+48 |
| `MAX_TOOL_TURNS` | `20` | fallback per-act, hanya jika TOTAL=0 |
| `BUDGET_MODE` | `per_task` | **eksplisit** (dulu tidak diset → default `per_act`, beda dari sweep) |
| `BUDGET_FLOOR_PER_ACT` | `10` | **eksplisit** (dulu tidak diset → default 0) |
| `REVISION_TOOL_TURNS` | `48` | = 4 putaran × 2 act × 6 turn (kebutuhan terukur: 6) |
| `MAX_REVISION_TURNS` | `4` | batas ronde revisi; **sweep mem-pass ini eksplisit** |
| `COST_LIMIT_USD` | `3.0` | pengaman dolar per task (referensi SWE-agent $3) |
| `ACT_TIMEOUT_SECONDS` | `3600` | **per act**. Dulu tidak ada di `.env` → default 1800, padahal sweep pass 3600 |
| `TOOL_OUTPUT_MAX_CHARS` | `2000` | cap per hasil tool, head+tail |
| `API_TIMEOUT` | `600` | dinaikkan dari 180 |
| `PROMPT_CACHE_LAYOUT` | `true` | prefix caching terbukti nyata (~90% hit) |
| `SOURCE_CONTEXT_ENABLED` | `false` | agen eksplorasi pakai tool |

**Tidak ada di `.env`** (ada di `src/config.py`, ambil default):

| Key | Default | Kenapa tidak diset |
|---|---|---|
| `PRICING_MODEL_OVERRIDE` | `""` | diisi hanya saat mau menghargai token model gratis dengan rate card berbayar (**estimasi**) |

> **Catatan:** `tools/run_final_sweep.py` mem-pass **semua** nilai di atas lewat
> `run_with_env.py --set`, jadi sweep tidak bergantung pada `.env`. Nilai di `.env`
> disamakan agar `python src/main.py` langsung **tidak** mengukur jadwal budget yang
> berbeda — dulu `BUDGET_MODE`/`BUDGET_FLOOR_PER_ACT`/`ACT_TIMEOUT_SECONDS` tidak diset
> dan itu terjadi (audit partner: `docs/AUDIT_FINAL_VERIFY.md` B5).
>
> **Verifikasi:** `tools/readiness_report.py` membandingkan config efektif dan
> **mengabaikan** env shell yang menimpa `.env` (itu pernah menipu dua kali).

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
14. **Checkout kotor membuat skor naik secara tak terlihat.** Strategi berbagi satu repo per issue, dan runner membersihkan **sebelum** tiap strategi tapi **tidak sesudah** — jadi run terakhir meninggalkan jejak. Kalau sweep di-`--resume` dengan tree kotor, `git diff` menangkap perubahan yang **bukan** buatan agen, dan itu terbaca sebagai "agen memecahkan issue". Ditemukan saat preflight: **5 dari 50 repo kotor** setelah pilot. Sweep sekarang **gate pada preflight** (bukan sekadar mengingatkan). Bersihkan: `tools/clean_repos.py`.
15. **Konfigurasi bisa "benar" di total tapi tidak bisa dijalankan.** `REVISION_TOOL_TURNS=32` dengan `MAX_REVISION_TURNS=4` memberi review total yang sama, tapi split per putarannya **4+4** — di bawah 6 turn yang dibutuhkan act revisi untuk membaca **dan** mengedit. **Total sama itu perlu, tapi tidak cukup:** periksa juga apakah tiap act dapat jatah yang cukup. `tools/verify_revision_rounds.py` memeriksa ini; `check_budget_fairness.py` sekarang ikut gagal kalau tidak.
16. **Aritmetika di komentar bisa salah dan bertahan lama.** `32 = 4 putaran × 8` tertulis di **tiga tempat** (`.env`, `.env.example`, `run_final_sweep.py`) — padahal 4 × 2 act × 8 = **64**. Komentar yang salah lebih berbahaya daripada tidak ada komentar, karena ia terdengar seperti sudah diperiksa.
17. **Warning yang salah lebih buruk daripada tidak ada warning.** Dua warning menyala untuk kondisi yang tidak ada: `PROMPT VARIANT MISSING` untuk file yang **ada** (membaca hasil kosong dari loader yang di-stub sebagai "file hilang"), dan starvation `REVISION_TOOL_TURNS=0` untuk direct & planning yang memang tidak punya revisi. Yang pertama membuat aku sendiri hampir mengejar bug hantu; yang kedua menyala di **100 dari 150 run**. Kalau warning berbunyi, periksa apakah **pesannya** benar sebelum mempercayainya.
18. **Test bisa bocor ke test lain lewat `Config`.** `test_budget_modes.py` menugaskan langsung (`Config.REVISION_TOOL_TURNS = 8`) tanpa monkeypatch dan tanpa memulihkan → setiap test **setelahnya** membaca `8` padahal `.env` bilang `48`. `monkeypatch.setattr` memulihkan sendiri; **penugasan langsung tidak**. Ditemukan canary partner (`tests/test_zz_probe_config_leak.py`) + `tools/_find_config_leak.py`. **Pelajaran:** test yang lulus bisa lulus karena nilai yang bocor dari test lain, bukan karena kode benar.
19. **`importlib.reload(config)` memecah identitas `Config`.** Reload memasang class **baru**, sementara modul yang sudah `from config import Config` memegang class **lama** — jadi `monkeypatch.setattr(mod.Config, ...)` bisa **meleset** dan test tetap lulus dengan nilai basi. Dibuktikan `tools/check_config_reload_poison.py`. Workaround yang benar: patch **setiap** modul yang memegang binding sendiri (lihat `tests/test_tool_loop_retry.py`).
20. **Gate yang bisa hijau sementara suite merah lebih buruk daripada tidak ada gate.** `tools/readiness_report.py` versi pertama menjalankan 7 check konfigurasi dan **tidak menjalankan test suite**, lalu mencetak "READY". Ia mengubah "aku belum memeriksa" menjadi "aku sudah periksa dan aman". Sekarang ia menjalankan suite **pertama**, dan `tools/_prove_gate_fails.py` membuktikan gate itu **bisa gagal** (menyuntikkan test rusak → NOT READY).
21. **Test yang tidak bisa gagal adalah dekorasi.** Aku menulis dua versi test penjaga yang **lulus meski perbaikannya dimatikan**: yang pertama mem-assert identitas class modul (ternyata bergantung **urutan import**), yang kedua dijalankan **sendirian** sehingga tidak ada yang bisa dideteksi. Selalu buktikan test baru **bisa gagal** sebelum mempercayainya — `tools/_prove_containment.py` dan `tools/_prove_gate_fails.py` melakukannya secara otomatis.
22. **Fixture teardown pytest berjalan TERBALIK.** Fixture yang dideklarasikan **setelah** `monkeypatch` di signature dibongkar **lebih dulu**, jadi fixture yang me-reload config akan me-reload **sebelum** env dipulihkan → kebocoran tetap ada. Jangan bergantung pada urutan fixture untuk hal yang urutannya penting; lakukan pemulihan di dalam `finally` test itu sendiri.

---

## 📂 Path Penting

| Path | Fungsi |
|------|--------|
| `src/agents/budget.py` | Pool tool-turn per strategi |
| `src/providers/tool_loop.py` | Loop tool bersama 3 provider |
| `src/strategies/review_strategy.py` | Loop review + re-review revisi |
| `src/agents/tools.py` | Definisi tool + guard (termasuk `[tests unavailable]`) |
| `tools/run_with_env.py` | **Wrapper wajib** — membuat `.env` menang |
| `tools/run_final_sweep.py` | **Entry point run 50 issue** — gate preflight, pass semua config eksplisit |
| `tools/preflight_repos.py` | Cek 50 repo pristine; sweep **gagal** kalau tidak |
| `tools/clean_repos.py` | Bersihkan checkout yang kotor |
| `tools/verify_revision_rounds.py` | Apakah reserve membiayai **setiap** putaran revisi? |
| `tools/audit_revision_rounds.py` | Berapa putaran revisi yang **benar-benar** jalan? |
| `tools/audit_revision_edits.py` | Apakah act revisi **mengedit** apa pun? |
| `tools/read_actual_bill.py` | Rekonsiliasi biaya vs tagihan 9router (`--compare`) |
| `tools/preflight_modal.py` | Replikasi kontrak `git apply` Modal secara lokal |
| `docs/HANDOFF_20261001.md` | **Detail sesi terakhir + plan berikutnya** |
| `docs/AUDIT_OPS_PARTNER.md` | Audit operasional 14 jam (4 blocker) |
| `docs/AUDIT_SCALE_PARTNER.md` | Audit integritas data skala 150 run |
| `docs/RUNBOOK.md` | Prosedur menjalankan eksperimen |
| `tools/analyze_run_anatomy.py` | Anatomi per-run: act, edit base vs revisi, verdict, retry |
| `tools/check_sweep_state.py` | Status per level kurva; mendeteksi level yang abort |

---

## 💡 Tips untuk Sesi Berikutnya

1. **Baca `docs/HANDOFF_20261001.md` dulu** — berisi plan lengkap, status kesiapan run, dan blocker yang sudah diperbaiki.
2. **Jangan jalankan run besar tanpa izin user** (gate di atas).
3. **Sebelum run apa pun:** `python tools/run_final_sweep.py --dry-run` — cek 200/200/200 dan semua flag.
4. **Selalu** lewat `tools/run_with_env.py` (sweep sudah melakukannya).
5. **Kalau terputus:** `python tools/run_final_sweep.py --resume --exp-id <EXP-id>` — jangan jalankan ulang dari nol.
6. **Setelah run:** `tools/check_sweep_state.py --exp <EXP-id>` + `tools/read_actual_bill.py --compare`.
7. **Cek cap warning** di `results/EXP-*/logs/experiment.log` — kalau ada, ada yang terpotong.

---

## 📝 Catatan

- **Commit sudah di-push:** `eae63b3` (fix sweep), `1597379` (memory), `d3d7ffe` (audit sendiri), `2349eb1`, `cfaf0ae`, `fb8f6ca` (branch `19/toolcall-commandcode`).
- **Temuan untuk skripsi:** reviewer tanpa execution feedback tidak menambah kemampuan verifikasi; test tersembunyi adalah oracle yang tidak bisa digantikan penalaran.
- **Temuan tambahan (2026-09-28):** retry yang membungkus loop — bukan request — membatalkan batas budget. Satu act bisa memakai 180 turn alih-alih 60. Ini kelas bug yang mudah terlewat karena tidak muncul sampai timeout benar-benar terjadi.
- **Temuan tambahan (2026-09-29):** bug di jalur patch dapat **memanipulasi hasil penelitian secara diam-diam**. `_normalize_newlines` merusak 4 patch di EXP-003, dan pelabelannya salah **dua kali** — patch rusak disebut "VALID" *dan* "NOT_APPLYABLE", sehingga angkanya tampak masuk akal (patch tidak apply = model salah), padahal pipeline-nya yang merusak. **Pelajaran metodologis:** verdict yang dihasilkan pipeline yang sama yang memproduksi artefak tidak boleh dipercaya begitu saja; verifikasi silang dengan `git apply` pada checkout bersih.
- **Sudah dievaluasi (2026-09-29):** `EXP-20260928-003` dijalankan di Modal SWE-bench harness. Hasil: direct 8/10, planning 8/10, review 6/10.
- **Temuan tambahan (2026-09-29, evaluasi):** kegagalan `review` di 10924 & 11001 **bukan** kegagalan penalaran — keduanya artefak pembagian budget. Di 11001 reviewer mendiagnosis dengan benar tapi act revisi hanya dapat 1 turn (cukup untuk *membaca*, tidak untuk *mengedit*). Di 10924 executor dipotong di cap 15 turn lalu patch setengah jadi disetujui reviewer (false approval). **Implikasi untuk skripsi:** dengan budget 40, strategi 3-act (`review`) berada di bawah strategi 2-act (`planning`) bukan karena review tidak berguna, tapi karena split `total//n` menghukum strategi dengan lebih banyak act.
- **Implikasi metodologis:** `APPROVED` tidak berkorelasi dengan `resolved` (9 approved → 6 resolved). Reviewer tanpa oracle tidak bisa memverifikasi; verdict-nya tidak boleh dipakai sebagai sinyal kualitas patch di analisis.
- **Temuan metodologis (2026-10-01, audit 2 partner):** **lima blocker** di pipeline 150-run, semuanya **tak terlihat dari angka hasil**. Yang paling berbahaya bukan bug yang membuat run gagal, tapi yang membuat run **sukses dengan pengukuran yang salah**: (1) `--resume` tidak diteruskan → restart mengulang 150 run; (2) review mengukur **1 putaran** padahal reserve untuk **4**; (3) `--compare` biaya **mati total** sehingga RQ3 tak tervalidasi; (4) checkout kotor → skor naik tanpa jejak; (5) warning yang salah membuat pembaca mengejar bug hantu. **Pola:** semua lolos dari 408 test karena test-nya memeriksa **total**, bukan **apakah konfigurasi itu bisa dijalankan**. Pelajaran: audit pipeline yang akan memakan 14 jam harus menanyakan "apa yang **tidak** diuji", bukan "apakah test lulus".

---

**Last working state:** commit `1597379` — **426 test lulus**. Pipeline **SIAP run 50 issue** setelah 2 audit partner (5 blocker diperbaiki). Budget **200/200/200**, revisi **48 (4 putaran × 6+6)**. Preflight **50/50 pristine**.

**Langkah berikutnya (prioritas):**

1. **KEPUTUSAN USER — jalankan sweep 50 issue?** ~$7–11, ~14–17 jam. Semua verifikasi sudah lulus (lihat tabel "KESIAPAN RUN 50" di atas). **Gate: butuh izin eksplisit user.**
   ```
   python tools/run_final_sweep.py
   ```
   Kalau terputus: `python tools/run_final_sweep.py --resume --exp-id <EXP-id>`.

2. **Setelah sweep selesai:**
   - `python tools/check_sweep_state.py --exp <EXP-id>` — setiap run hadir?
   - Evaluasi Modal → `python tools/eval_modal.py ...`
   - `python tools/read_actual_bill.py --since ... --until ... --model cbai/deepseek-v4.1-flash --compare results/<EXP-id>` — RQ3, cross-check biaya.

3. **Masalah validitas review (78% → 12,5%)** — **sudah dikoreksi**, tidak perlu keputusan lagi. Angka sebenarnya: **1 dari 8 (12,5%)** penolakan yang bergantung pada file test. Lihat §"Koreksi" baris 276-304.

4. **Ablation reviewer (opsional, untuk tesis):** `review` tanpa ronde review = `planning`. Kalau ketiga strategi seri di 50 issue, ablation ini memisahkan "review berguna" dari "review tidak berpengaruh".

5. **`11019`** sudah dijawab: **batas kapabilitas**, bukan budget (0 truncation, act berhenti di ~20% anggaran, patch VALID tapi salah semantik). Tidak perlu run tambahan.
