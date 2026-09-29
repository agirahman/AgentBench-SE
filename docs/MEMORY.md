# 🧠 AI Agent Memory — AgentBench-SE

**Last Updated:** 2026-09-29 03:00 WIB
**Status:** `EXP-20260928-003` **sudah dievaluasi** (Modal): direct 8/10, planning 8/10, review 6/10
**Active Branch:** `19/toolcall-commandcode`
**Detail sesi terakhir:** lihat [`HANDOFF_20260928.md`](HANDOFF_20260928.md)

> ⛔ **GATE — WAJIB KONFIRMASI USER:** Jangan jalankan run besar (50 issue / multi-jam)
> tanpa persetujuan eksplisit dari user. Boleh tanpa konfirmasi: unit test, smoke test
> kecil (≤3 issue, 1 strategi), dan pekerjaan kode/dokumentasi.

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
| **Test suite** | ✅ Done | **213 lulus** (dari 199) |
| **Retry vs budget** | ✅ **FIXED** | Retry per-request di dalam loop (commit `dfc9fa8`) |
| **Konteks per turn** | ✅ Done | `TOOL_OUTPUT_MAX_CHARS=2000`, head+tail (dari 8000 head-only) |
| **Korupsi patch** | ✅ **FIXED** | `_normalize_newlines` merusak diff yang mengandung literal `\n` (commit `3cbd9d1`) |
| **Evaluasi EXP-003** | ✅ Done | Modal SWE-bench harness: **30/30 patch applied**, hasil di `EVAL_NOTE.md` |
| **Reviewer oracle** | ⚠️ Terbatas | `run_tests` selalu gagal; reviewer hanya bisa menalar. Spike 2026-09-29: test pre-existing lokal **murah (2–3 s) tapi tidak mendiskriminasi** — lihat `docs/SPIKE_ORACLE_20260929.md` |
| **Reserve act revisi** | ✅ Done | `REVISION_TOOL_TURNS` — pool terpisah, base flow tetap setara 40/40/40 (default 0 = perilaku lama) |
| **Race EXP-ID** | ✅ **FIXED** | Lock `O_CREAT\|O_EXCL` + deteksi lock basi; di Windows errno `EACCES`, bukan `EEXIST` |
| **Split paralel** | ✅ Terverifikasi | `tools/_verify_split.py`: 26 + 24 = 50, overlap 0 |

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
| Edit act revisi | 0 edit (2 read) | **1 edit** |
| Preflight `git apply` | — | **PASS** |

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

**Temuan utama: perbaikan act revisi BEKERJA.** `11001/review` sekarang resolved. Verifikasi rantai (`tools/verify_review_11001_resolved.py`):

| Bukti | EXP-003 (per_act) | Level 40 (per_task) |
|---|---|---|
| Urutan act | plan→exec→review→**revisi 1 turn**→re-review | plan→exec→review→**revisi (executor)**→re-review |
| Edit act revisi | **0** (2× read saja) | **3× `edit_file`** (#13, #19, #20 dari 60 call) |
| Verdict akhir | NEEDS_REVISION | **APPROVED** |
| Hasil | ❌ gagal | **✅ resolved** |

Urutan pesan membuktikan act revisi dieksekusi **executor** (MSG 9: `orchestrator → executor [get_feedback]`), bukan reviewer — sesuai desain. Tool call per agent: planner 9, executor 16 (3 edit), reviewer 35.

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
| `MAX_REVISION_TURNS` | `1` | batas revisi |
| `API_TIMEOUT` | `600` | dinaikkan dari 180 |
| `PROMPT_CACHE_LAYOUT` | `true` | prefix caching terbukti nyata (~90% hit) |
| `SOURCE_CONTEXT_ENABLED` | `false` | agen eksplorasi pakai tool |

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
| `docs/HANDOFF_20260928.md` | **Detail sesi terakhir + plan berikutnya** |
| `docs/RUNBOOK.md` | Prosedur menjalankan eksperimen |

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

**Last working state:** commit `3cbd9d1` + `bca32eb` — 213 test lulus, retry per-request terpasang, budget 40 setara, `TOOL_OUTPUT_MAX_CHARS=2000`, korupsi patch diperbaiki, EXP-20260928-003 30/30 patch APPLYABLE **dan sudah dievaluasi** (direct 8/10, planning 8/10, review 6/10; `results/EXP-20260928-003/EVAL_NOTE.md`).
