# AUDIT — Kesiapan Run 50 Issue (READ-ONLY)

**Tanggal:** 2026-09-30
**Scope:** jalur RUN (eksekusi agen) — bukan evaluasi.
**Sifat:** READ-ONLY. Tidak ada kode yang diubah, tidak ada run berbayar dijalankan, tidak ada commit.
**Metode:** pembacaan kode + verifikasi terhadap data nyata di `results/` + satu skrip pembuktian read-only di direktori temp (tidak menyentuh `results/`).

> **Catatan scope:** brief menyebut `src/runner.py`, `src/agents/*_strategy.py`, `src/execution/*.py`, dan `config/*.yaml`. Path-path itu **tidak ada** di repo ini. Yang benar: `src/experiments/runner.py`, `src/strategies/*_strategy.py`, `config.py` (bukan direktori), dan tidak ada `src/execution/` (tool ada di `src/agents/tools.py`). Audit dilakukan pada path yang sebenarnya.

---

## ⚠️ Catatan penting: repo berubah selama audit

Saat audit ini berjalan, **proses lain** memodifikasi dua file di working tree:

| File | mtime | Status |
|---|---|---|
| `src/agents/budget.py` | 18:38:59 | modified (+29/-14) |
| `tests/test_budget_modes.py` | 18:39:13 | modified (+87/-23) |

Perubahan itu menambahkan `warnings.warn` untuk kombinasi `per_task` + `REVISION_TOOL_TURNS=0`, dan membuat `share_revision`/`spend_revision` menghormati reserve di kedua mode. **Bukan** bagian dari audit ini, dan tidak saya sentuh.

**Verifikasi ulang:** semua file yang menjadi dasar temuan di bawah **tidak berubah** (`git status` bersih untuk `runner.py`, `retry.py`, `tool_loop.py`, `observability.py`, `tools.py`, ketiga strategy, dan `config.py`). Bukti eksekusi resume dijalankan ulang setelah perubahan itu dan **hasilnya identik**. Jadi seluruh temuan tetap berlaku.

---

## Ringkasan

| # | Temuan | Tingkat |
|---|---|---|
| 1 | `--resume` menghapus hasil lama dari `generation_result.csv` + `manifest.json` | **BLOCKER** |
| 2 | Rate-limit backoff tidak terbatas per-run: satu act bisa menggantung ~2 jam | **BLOCKER** |
| 3 | Kegagalan mid-act membuang token/biaya yang sudah terpakai dan edit parsial | **SERIUS** |
| 4 | Run mati dicatat `TIMEOUT` walau bukan timeout; token 0 → anggaran tampak murah | **SERIUS** |
| 5 | `COST_LIMIT_USD` tidak menahan runaway pada model ber-rate nol / jalur non-tool | **SERIUS** |
| 6 | 10 instance cache sudah kotor sebelum run | **MINOR** |
| 7 | `os.walk` tanpa batas di `grep` bisa lambat pada repo besar | **MINOR** |

**BLOCKER = 2 temuan (#1, #2).** Keduanya harus ditangani sebelum run 50 issue.

**Catatan tambahan:** perubahan `budget.py` yang terjadi di luar audit ini (lihat di atas) memperbaiki akar yang dilaporkan MEMORY §"Koreksi act revisi" — reserve act revisi sekarang dihormati di `per_task`. Itu **memperkuat** alasan untuk menahan run 50 issue sampai perubahan itu punya test yang lulus dan tidak ada perubahan lain yang menyusul.

---

## BLOCKER #1 — `--resume` menghapus hasil lama dari artefak turunan

### Bukti kode

- `src/experiments/runner.py:247-248` — `all_results` dan `all_predictions` selalu mulai **kosong**:
  ```python
  all_results: list[ExperimentResult] = []
  all_predictions: list[dict] = []
  ```
- `src/experiments/runner.py:269-272` — instance yang di-skip hanya dihitung lalu `continue`, **tidak ada yang dimasukkan ke `all_results`**:
  ```python
  if expected_key in done_ids[name]:
      skipped += 1
      logger.info(f"[{done}/{total}] SKIP (resume) {name} on {issue.instance_id}")
      continue
  ```
- `src/experiments/runner.py:555-558` — CSV ditulis dari `all_results` dengan mode **overwrite**, bukan append/merge:
  ```python
  rows = [flatten_for_csv(r) for r in all_results]
  df = pd.DataFrame(rows)
  csv_path = f"{exp_dir}/generation_result.csv"
  df.to_csv(csv_path, index=False)
  ```
- `src/experiments/runner.py:574-584` — `manifest.json` juga dibangun dari `all_results` dan ditulis ulang.
- `src/experiments/runner.py:564-571` — `generation_statistics.json` dan `generation_report.md` ikut hanya berisi subset yang baru berjalan.

`predictions/<strategy>.jsonl` **tidak** terkena (ditulis append lewat `_append_jsonl`), jadi artefak yang berbeda-beda menyimpan kebenaran yang berbeda.

### Bukti eksekusi (read-only, direktori temp)

Skrip: `%TEMP%\audit_p7\prove_resume2.py` — memakai stub strategy (tanpa panggilan model), hanya menulis ke temp dir.

```
RUN1 exp=EXP-20260930-034 calls=[1,2,3]        df_rows=3
RUN1 csv_rows=3 instances=[django__django-1, -2, -3]
RUN2 exp=EXP-20260930-034 calls=[django__django-4]  df_rows=1
RUN2 csv_rows=1 instances=[django__django-4]          <-- 1,2,3 HILANG
RUN2 jsonl_lines=4 instances=[-1,-2,-3,-4]            <-- jsonl utuh
RUN2 manifest_processed=1 instances=[django__django-4] <-- manifest ikut hilang
VERDICT: DATA LOSS CONFIRMED — CSV went 3 -> 1 rows after resume
```

### Dampak pada run 50

Skenario yang paling mungkin: run mati di issue ke-37 (rate limit, listrik, crash). User menjalankan `--resume` sesuai instruksi runner (`runner.py:609`). Sisa 13 issue selesai, lalu **`generation_result.csv` dan `manifest.json` hanya berisi 13 issue itu**. 37 issue yang sudah selesai **lenyap dari artefak utama** — padahal token sudah dibayar.

Kerugian berlipat:
- Semua alat analisis membaca CSV ini: `analyze_accuracy_vs_budget.py:86`, `analyze_budget_binding.py:47`, `analyze_per_act_share.py:28`, `analyze_pool_vs_reference.py:40`, `analyze_token_stats.py:35`, `compare_apply_status.py:96`, `compare_curve_vs_baseline.py:11`, `aggregate_runs.py:111`.
- `main.py:393-405` menghitung total token/biaya dari `df` → ringkasan run **under-report drastis**.
- `manifest.json` adalah "shipping receipt" (`observability.py:29`) — akan mengklaim `total_issues_processed: 13` untuk eksperimen 50 issue.
- Data bisa dipulihkan dari `predictions/*.jsonl` + `artifacts/`, tapi itu kerja manual dan **tidak otomatis**; tidak ada tool yang melakukan rekonstruksi ini.

### Rekomendasi

1. Saat `resume=True`, muat baris lama dari `predictions/predictions.jsonl` (atau CSV yang ada) ke `all_results` **sebelum** loop, lalu timpa baris yang di-rerun berdasarkan `(instance_id, strategy)`.
2. Ganti `df.to_csv(..., index=False)` menjadi penulisan atomik dari gabungan (lama + baru), atau tulis CSV per-run dan hasilkan CSV final sebagai agregasi.
3. Tambahkan test: jalankan 3 issue, resume dengan 4 issue, **assert CSV berisi 4 baris** dan manifest `total_issues_processed == 4`. Test yang ada (`tests/test_resume_continues_experiment.py:60-90`, `tests/test_resume_skips_failures.py`) hanya memeriksa **berapa kali strategy dipanggil** — tidak pernah memeriksa isi CSV/manifest. Itu sebabnya bug ini lolos.

---

## BLOCKER #2 — Backoff rate-limit tidak dibatasi: satu act bisa menggantung ~2 jam

### Bukti kode

- `src/evaluation/retry.py:104-106` — backoff rate-limit: `min(base * 2^(attempt-1), max)`.
- `.env` / `config.py:146-147` — `RATE_LIMIT_BACKOFF_BASE=60`, `RATE_LIMIT_BACKOFF_MAX=300`.
- `config.py:141` — `MAX_RETRIES=3`.
- `src/providers/tool_loop.py:212-216` — **setiap request** dibungkus `call_with_retry`, dan `time.sleep(delay)` ada di `retry.py:193`.
- `src/providers/tool_loop.py:232` — loop berjalan `max_tool_turns` kali; di `per_task` satu act bisa memakai **seluruh pool 40 turn**.
- `src/agents/tools.py:361-370` — `run_tests` punya `timeout=180`, tapi `run_tests` **bukan** jalur yang menggantung; yang menggantung adalah backoff HTTP.
- `src/providers/tool_loop.py:334` — setelah loop habis, **satu request `final-answer` lagi** (juga dengan retry penuh).

### Aritmetika

```
per _create() worst wait = 60 + 120 = 180 s
satu act dengan N turn = N+1 request
```

| Turn per act | Request | Backoff murni |
|---|---|---|
| 13 | 14 | 42 min |
| 20 | 21 | 63 min |
| **40** | **41** | **123 min** |
| review per_task, 3 act × 40 | 123 | **369 min** |

### Bukti data nyata

Durasi run dari `generation_result.csv` (kolom `execution_time`):

| Eksperimen | Run | Max satu run |
|---|---|---|
| EXP-20260928-003 | 30 | 2.562 s (42,7 min) |
| EXP-20260929-003 | 9 | 1.487 s (24,8 min) |
| **EXP-20260929-022** | 9 | **4.713 s (78,6 min)** |
| EXP-20260929-022 `11019/review` | 1 | **5.992 s (100 min)** |

Dan itu terjadi pada run **9 issue** dengan pool 40. Backoff 78–100 menit sudah nyata, bukan teori.

### Dampak pada run 50

150 run. Jika sebagian kecil saja kena, satu run bisa memakan 2 jam; 5 run seperti itu = 10 jam terbuang dari anggaran waktu. Yang lebih berbahaya: `RATE_LIMIT_CONSECUTIVE_LIMIT=5` (`config.py:151`) hanya menghitung **kegagalan yang naik ke runner**, sedangkan backoff terjadi **di dalam** `tool_loop` dan tidak menaikkan counter itu. Jadi 150 run bisa menghabiskan berjam-jam dalam sleep tanpa circuit breaker pernah trip.

### Rekomendasi

1. Tambahkan **batas waktu per act** (mis. `ACT_TIMEOUT_SECONDS`) yang di-check di dalam loop, atau cap total waktu backoff per act.
2. Turunkan `RATE_LIMIT_BACKOFF_MAX` (mis. 60 s) dan `MAX_RETRIES` untuk 429, karena usage window 5 jam tidak akan sembuh dalam 300 s — sudah dicatat di `retry.py:58-60`, tapi angkanya tidak konsisten dengan alasan itu.
3. Naikkan `RATE_LIMIT_CONSECUTIVE_LIMIT` sebagai breaker **tingkat act**, bukan hanya tingkat run.
4. Ukur sebelum run: jalankan 1 issue dengan `RATE_LIMIT_BACKOFF_MAX` kecil dan lihat apakah retry benar-benar terjadi.

---

## SERIUS #3 — Kegagalan mid-act membuang token, biaya, dan edit parsial

### Bukti kode

- `src/providers/tool_loop.py:275` — `response = _create(...)` tidak dibungkus try/except. Exception dari `_create` (setelah retry habis) naik keluar dari `run_tool_loop`.
- `src/providers/opencode_provider.py:179-181` — `except Exception as e: logger.error(...); raise` (re-raise).
- `src/agents/base.py:108-121` — tanpa penanganan; exception naik ke strategy.
- `src/strategies/direct_strategy.py:38-43`, `planning_strategy.py:40-59`, `review_strategy.py:105-160` — tanpa penanganan.
- `src/experiments/runner.py:457` → `519-532` — runner menangkap, lalu **mengganti seluruh hasil dengan objek kosong**:
  ```python
  empty_run = InferenceRun(patch="", inferences=[])
  empty_exec = ExecutionResult(run=empty_run)
  empty_cost = CostSummary(0.0, 0.0, 0.0, 0.0, "")
  ```
- Tidak ada pemanggilan `finalize_patch`/`capture_diff` di jalur error — kontras dengan SWE-agent yang punya `attempt_autosubmission_after_error`.

### Bukti data nyata

`results/EXP-20260929-022/artifacts/django__django-11019/review/summary.json`:
```json
{ "patch_status": "TIMEOUT", "elapsed_seconds": 5992.482,
  "total_tokens": 0, "success": false,
  "error": "InternalServerError: Error code: 502 - ..." }
```
Dibandingkan run sukses pada eksperimen yang sama:
```json
{ "patch_status": "VALID", "elapsed_seconds": 437.393, "total_tokens": 688190 }
```

**5992 detik** (100 menit) kerja tercatat sebagai **0 token, $0.00**. Semua edit yang mungkin sudah ditulis ke `datasets/repos/...` sebelum 502 juga dibuang tanpa diambil.

### Dampak pada run 50

- Setiap kegagalan provider di tengah act = satu titik data yang **dibayar tapi dilaporkan nol**. Untuk run berbayar (`cbai/deepseek-v4.1-flash`), biaya nyata naik sementara akuntansi kita menunjukkan $0 → klaim biaya RQ3 under-report.
- Edit parsial yang sudah ada di disk **tidak** masuk patch, jadi model yang sebenarnya sudah hampir selesai dihitung sebagai gagal total.
- Di `planning`/`review`, act yang sudah selesai (planner) ikut lenyap dari artefak.

### Rekomendasi

1. Bungkus eksekusi act sehingga `capture_diff(repo_root)` tetap dipanggil di jalur exception, lalu simpan sebagai patch parsial dengan status khusus (mis. `PARTIAL_AFTER_ERROR`).
2. Akumulasi `usage_totals` di level strategy/runner (bukan hanya di dalam loop) agar token yang sudah dibayar tetap tercatat saat act gagal.
3. Pisahkan metrik: `tokens_billed` vs `tokens_in_submitted_patch`.

---

## SERIUS #4 — Run mati dicatat `TIMEOUT`, token 0, sehingga anggaran tampak murah

### Bukti kode

Sudah diperbaiki sebagian (label `RATE_LIMIT`/`PROVIDER_ERROR`/`ERROR` di `runner.py:493-498`), tapi **sisa masalah**:

- `src/experiments/observability.py:11-16` masih hardcode hanya `"TIMEOUT"` sebagai satu-satunya status non-patch:
  ```python
  if result.patch_status == "TIMEOUT":
      return "TIMEOUT"
  ```
  Status baru (`RATE_LIMIT`, `PROVIDER_ERROR`, `ERROR`) **jatuh ke `EMPTY_PATCH`** — jadi 502 provider tetap terlihat seperti "model tidak menghasilkan apa-apa".
- `src/experiments/runner.py:110` — `_PATCH_STATUS_FAILED = {"TIMEOUT", "ERROR", "FAILED"}` **tidak memuat** `RATE_LIMIT` dan `PROVIDER_ERROR`. `_is_finished_entry` masih aman karena `error_type` di-set (`runner.py:509`) dan patch kosong, tapi daftar ini rapuh: jika suatu saat `error_type` tidak ditulis, run rate-limit akan dianggap selesai dan `--resume` melewatinya.
- `src/experiments/runner.py:542` — `total_tokens=0` di-hardcode di jalur error (lihat #3).
- `src/experiments/runner.py:628-630` — breakdown ringkasan hanya mendaftar status lama; `RATE_LIMIT`/`PROVIDER_ERROR` tidak muncul di ringkasan akhir.

### Bukti data nyata

`results/EXP-20260929-022/artifacts/django__django-11019/review/summary.json` → `"patch_status": "TIMEOUT"` untuk error `InternalServerError` 502. Di `generation_result.csv` baris yang sama: `patch_status=TIMEOUT`, `total_tokens=0`.

### Dampak pada run 50

Setiap provider hiccup menjadi baris "TIMEOUT" dengan biaya $0. Tabel biaya dan tabel kegagalan untuk 50 issue akan **salah dua-duanya**, dan penyebabnya tidak bisa dipisahkan lagi setelah run selesai.

### Rekomendasi

1. Perluas `_result_status` untuk memetakan `RATE_LIMIT`/`PROVIDER_ERROR`/`ERROR` ke kategori sendiri.
2. Tambahkan status itu ke `_PATCH_STATUS_FAILED`.
3. Masukkan ke breakdown ringkasan `runner.py:628`.
4. Catat `error_type` + `elapsed_seconds` di CSV (sekarang hanya di summary.json) agar audit bisa dilakukan dari satu berkas.

---

## SERIUS #5 — `COST_LIMIT_USD` tidak menahan runaway pada rate nol / jalur non-tool

### Bukti kode

- `src/providers/tool_loop.py:149-150`:
  ```python
  capped = max_cost_usd is not None
  cost_rates = PricingTable.rates_for(model, "off_peak") if capped else None
  ```
  Untuk model tanpa entri di `PricingTable.PRICING`, `rates_for` mengembalikan **semua nol** (`evaluation/cost.py:219-220`). `_cost_so_far()` lalu selalu 0.0, sehingga `>= max_cost_usd` **tidak pernah benar** → guard tidak pernah menyala. Ini persis kondisi `oc/space-bunny-free` dan `stealth/space-bunny-alpha` (dua-duanya rate 0.0 di `cost.py`).
- `src/agents/base.py:120-121` — jika `use_tools=False` atau provider tidak punya `generate_with_tools` (Gemini/Groq/DeepSeek), eksekusi lewat `self.provider.generate(prompt)`, dan `max_cost_usd` **tidak diteruskan sama sekali**.
- `src/providers/tool_loop.py:334` — setelah guard trip, masih ada satu request `final-answer` tanpa cek biaya lagi.
- `src/agents/budget.py:197-203` — `cost_share()` membagi cap per act; act pertama bisa menghabiskan seluruh `cost_remaining` lalu act berikutnya dapat `0.0`, dan `0.0` masih memicu satu request di `tool_loop.py:238` (kondisi `>=` benar untuk 0.0) — jadi guard "habis" tetap membayar satu request penuh.

### Catatan penting untuk run berbayar

Run RQ3 (`tools/run_rq3_paid.py:40`) memakai `cbai/deepseek-v4.1-flash`, yang **punya** entri rate nyata (`cost.py:182-188`), jadi guard ini **akan** aktif di sana. Jadi temuan ini bukan blocker untuk run berbayar, tapi tetap relevan jika ada run gratis/`--provider` lain di sesi yang sama.

### Rekomendasi

1. Perlakukan "rates semuanya nol" sebagai uncapped **eksplisit** dan log peringatan, jangan diam-diam.
2. Teruskan `max_cost_usd` (atau bentuk lain dari budget) ke jalur `generate()` non-tool.
3. Ubah cek menjadi `>` pada batas yang benar, atau verifikasi bahwa `0.0` memang seharusnya berhenti sebelum request apa pun.

---

## MINOR #6 — 10 instance cache sudah kotor sebelum run

### Bukti

```
instances with .git: 51
DIRTY: 10
  django/django/08a4ee06... -> 2 changed file(s)
  django/django/17455e92... -> 2
  ... (8 lagi)
```

Contoh isi kotor: `M django/contrib/auth/migrations/0011_update_proxy_permissions.py`, `M tests/auth_tests/test_migrations.py`.

### Dampak

`reset_working_tree()` (`tools.py:627-645`) dipanggil di awal setiap strategy (`direct_strategy.py:29`, `planning_strategy.py:29`, `review_strategy.py:29`), jadi **selama run** tidak bocor antar issue. Tapi 10 instance itu **memulai run dalam keadaan kotor**; jika `git checkout -- .` gagal sebagian (mis. file terkunci di Windows), sisa edit bisa masuk patch pertama.

### Rekomendasi

Jalankan `tools/clean_repos.py` (sudah ada) atau `git checkout -- . && git clean -fdq` pada semua instance sebelum run, lalu verifikasi ulang bahwa 51/51 bersih.

---

## MINOR #7 — `os.walk` tanpa batas di `grep`

### Bukti kode

`src/agents/tools.py:250-268` — `grep` berjalan `os.walk(search_root)` dan hanya berhenti setelah `count > 500`; pada repo besar dengan banyak file biner/hasil, ia membaca setiap file dengan `read_text` sebelum batas tercapai. Tidak ada batas jumlah file yang dikunjungi, hanya batas jumlah match.

### Dampak

Bukan hang permanen, tapi bisa memakan waktu puluhan detik per panggilan pada repo besar (matplotlib/scikit-learn) × 150 run.

### Rekomendasi

Tambahkan batas jumlah file yang di-scan dan skip direktori besar (`.git`, `build`, `dist`).

---

## Yang DIPERIKSA dan AMAN

Bagian ini sengaja dicatat supaya tidak diperiksa ulang.

1. **Isolasi repo antar-issue — AMAN.** `reset_working_tree()` dipanggil di awal **setiap** strategy (`direct_strategy.py:29`, `planning_strategy.py:29`, `review_strategy.py:29`) dan melakukan `git reset -q`, `git checkout -- .`, `git clean -fdq` (`tools.py:640-645`). Ketiga strategy berbagi satu checkout per issue, jadi urutan direct→planning→review memang memakai repo yang sama, tapi selalu di-reset. **Diverifikasi:** semua 50 issue punya `.git` dan `HEAD` yang cocok dengan `base_commit`; 0 issue kekurangan checkout.
2. **Kebocoran state budget antar-issue — AMAN.** `ToolTurnBudget.from_config()` dibuat **baru di setiap `run()`** (`direct_strategy.py:35`, `planning_strategy.py:36`, `review_strategy.py:103`). Tidak ada state kelas.
3. **Kebocoran blackboard — AMAN.** `Blackboard(issue=issue)` baru per run. Tidak ada atribut kelas.
4. **Kebocoran `_CURRENT_REPO_ROOT` antar-issue — AMAN.** `tool_loop.py:155` memanggil `set_repo_root(repo_root)` di setiap act, dengan komentar eksplisit bahwa `None` mereset ke base. Tidak ada `set_repo_root` yang tertinggal.
5. **Kebocoran counter retry — AMAN.** `_RECENT_TEST_COMMANDS` direset via `reset_test_guard()` (`tools.py:637-639`), dipanggil dari `reset_working_tree()`.
6. **Kebocoran `GLOBAL_STATS`/`_THREADS_THAT_USED_API_KEYS` — tidak terpakai.** Tidak ada di `src/` (sisa dari SWE-agent, tidak diadopsi).
7. **Resume TIDAK mengulang kegagalan provider — AMAN (sudah diperbaiki).** `_is_finished_entry` (`runner.py:113-126`) menolak baris dengan `patch_status` gagal, `error_type`, atau patch kosong. `tests/test_resume_skips_failures.py` menutupinya (4 test). **Ini memperbaiki bug yang saya laporkan di audit sebelumnya** — tapi lihat BLOCKER #1: resume sekarang me-retry dengan benar, dan justru itu yang memicu kehilangan data.
8. **Lock `experiment_id` — AMAN.** `O_CREAT|O_EXCL` + penanganan `EACCES`/`EPERM` di Windows + deteksi lock basi 60 s (`experiment_id.py:43-77`). Sudah ada test.
9. **Config drift dari shell — TERKENDALI dengan wrapper.** Tanpa `run_with_env.py`, shell mengekspor `OPENCODE_MODEL`, `TOTAL_TOOL_TURNS`, `API_TIMEOUT`, `TOOLCALL_ENABLED`, dan **`OPENCODE_API_KEY` yang rusak** (21 char vs 35 char di `.env`) → 401. **Selalu pakai `tools/run_with_env.py`.** Ini sudah didokumentasikan di MEMORY jebakan #1 dan diverifikasi ulang di sesi ini.
10. **Kunci resume sudah cukup spesifik — AMAN (dengan catatan).** `_resume_key` = `instance_id|model|thinking` (`runner.py:97-103`). **Catatan:** `TOTAL_TOOL_TURNS`, `BUDGET_MODE`, dan `BUDGET_FLOOR_PER_ACT` **tidak** ada di kunci. Jika `--resume` dipakai dengan budget berbeda di direktori yang sama, run lama akan di-skip dan dianggap setara. Untuk run 50 issue: **jangan** ubah knob budget saat melanjutkan eksperimen yang sama.
11. **Artefak tidak menumpuk tanpa batas — AMAN.** Terukur pada eksperimen 9-run: 1,08–1,97 MB total, artefak terbesar `messages.jsonl` 85 KB. Ekstrapolasi ke 150 run: **~20–35 MB**. Log punya `rotation="10 MB"` (`runner.py:231`). Disk **bukan** risiko.
12. **Repo cache tidak akan tumbuh liar — AMAN.** 1,35 GB / 135.403 file untuk 50 instance; sudah lengkap, tidak ada fetch tambahan saat run.
13. **`run_tests` punya timeout — AMAN.** `timeout=180` (`tools.py:369`), plus guard anti-ulang (`tools.py:350-358`).
14. **`git` tool punya timeout — AMAN.** `timeout=60` default (`tools.py:422-429`, `584-590`).
15. **Pricing run berbayar sudah benar — AMAN.** `cbai/deepseek-v4.1-flash` punya rate terukur dengan `cached_input_per_million = input_per_million` (`cost.py:182-188`), konsisten dengan temuan "cache hit tidak didiskon" di MEMORY. Diverifikasi terhadap bill pada sesi sebelumnya (selisih 1,2%).

---

## Yang BELUM BISA DIPASTIKAN + cara memastikannya

1. **Apakah run 50 issue akan benar-benar memicu BLOCKER #2.** Perlu pengukuran: jalankan 1 issue dengan `RATE_LIMIT_BACKOFF_MAX=30` dan hitung berapa kali backoff terjadi. **Belum terbukti** untuk run berbayar — 9router mungkin tidak membalas 429 pada route `cbai/`.
2. **Berapa banyak kegagalan provider yang realistis pada 150 run.** Riwayat: EXP-20260929-022 punya 1/9 run mati provider. Ekstrapolasi 150 run → ~15 run. **Perlu diverifikasi** dengan `tools/check_sweep_state.py` selama run berjalan.
3. **Apakah `--resume` akan benar-benar dipakai pada run 50.** Jika run berjalan mulus tanpa interupsi, BLOCKER #1 tidak terpicu — tapi risikonya tidak bisa diterima begitu saja, karena `--resume` adalah satu-satunya jalur pemulihan yang didokumentasikan (`runner.py:609`, MEMORY).
4. **Apakah 10 instance kotor mempengaruhi patch pertama.** **Belum terbukti** — perlu menjalankan `git stash list`/`git diff` pada satu instance kotor dan membandingkan dengan patch yang dihasilkan.
5. **Batas waktu total run 50 issue.** Estimasi dari data: level 100 = 240,8 min untuk 9 run → ~26,8 min/run. 150 run ≈ **67 jam** (2,8 hari) pada model gratis. Dengan model berbayar, RQ3 9 run = 14,9 min → ~2,5 min/run → 150 run ≈ **6,2 jam**. **Perlu diverifikasi** karena model berbayar lebih cepat dan tidak ada backoff.
6. **Apakah `MAX_TOKENS=32768` cukup** untuk act panjang. **Belum terbukti** — perlu memeriksa berapa banyak `finish_reason == "length"` pada run sebelumnya.
7. **Apakah `apply_status` tetap akurat setelah edit-then-diff** pada 50 repo berbeda. `validate_applicability` (`swebench_adapter.py:442-519`) sudah menangani working tree kotor dengan temp index, tapi **belum diuji** pada semua 6 repo.

---

## Urutan tindakan yang disarankan

**Sebelum run 50 issue:**

1. **Perbaiki BLOCKER #1** (resume data loss) — ini yang paling mahal jika terpicu.
2. **Batasi BLOCKER #2** — set `RATE_LIMIT_BACKOFF_MAX` lebih kecil (mis. 60 s) dan/atau tambahkan batas waktu per act.
3. Bersihkan 10 instance kotor (`tools/clean_repos.py`).
4. Verifikasi konfigurasi: jalankan `python -c "from config import Config; ..."` lewat `run_with_env.py` dan pastikan `BUDGET_MODE`, `TOTAL_TOOL_TURNS`, `COST_LIMIT_USD` sesuai rencana.
5. Smoke test 1 issue lewat jalur yang akan dipakai, lalu **buang** eksperimen smoke itu.

**Setelah run selesai:**

6. Verifikasi `generation_result.csv` punya 150 baris dan `manifest.json` melaporkan 150.
7. Bandingkan biaya akuntansi dengan bill 9router (`tools/read_actual_bill.py`).
