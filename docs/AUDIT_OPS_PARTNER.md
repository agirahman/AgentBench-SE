# AUDIT OPS — akan SELAMATKAH sweep 14–17 jam itu?

**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Sifat:** READ-ONLY. Tidak ada file sumber yang diubah. Skrip scratch pakai prefix `.verify_ops_*`.
**Target:** 50 instance × 3 strategi = **150 run**, sekuensial, model berbayar `cbai/deepseek-v4.1-flash` via 9router, tanpa pengawasan.

---

## Ringkasan verdict

| # | Area | Verdict |
|---|---|---|
| 1 | Rate limit & kuota | **READY WITH CAVEAT** |
| 2 | Wall-clock bound vs sweep 14 jam | **READY WITH CAVEAT** |
| 3 | Crash recovery mid-sweep | **NOT READY** |
| 4 | Checkout hygiene | **READY** |
| 5 | Bill window | **READY WITH CAVEAT** |

**Tidak ada blocker yang "membuang 87 run".** Blocker terburuk (B1, di bawah) membuang **seluruh 150 run dari sisi rekonsiliasi tagihan dan kelanjutan**, bukan dari sisi kerja yang sudah dibayar — savepoint-nya tetap ada. Ini tetap peringkat #1 karena blast radius-nya paling besar.

---

## Blocker diurutkan berdasarkan blast radius

| Rank | Blocker | Blast radius | Terukur? |
|---|---|---|---|
| **B1** | `run_final_sweep.py` **tidak** meneruskan `--resume`/`--exp-id`; restart = direktori EXP baru + 150 run diulang | **150 run** (seluruh sweep dijalankan ulang; kerja lama tidak pernah disambung) | Ya — `build_cmd` diperiksa, lihat §3 |
| **B2** | `sweep_started.json` ditimpa tanpa syarat pada restart | **Jendela tagihan attempt pertama hilang** → tidak bisa rekonsiliasi biaya run yang sudah dibayar | Ya — `run_final_sweep.py:249`, §3/§5 |
| **B3** | Tidak ada budget waktu keseluruhan; `ACT_TIMEOUT_SECONDS` hanya per-act, dan satu run review punya 5 act | **1 run bisa 5 jam**, dan tidak ada yang menghentikan sweep secara keseluruhan | Ya — `run_final_sweep.py:109`, §2 |
| **B4** | `read_actual_bill.py --compare` tidak menemukan field biaya di artefak kita | **RQ3 tidak bisa divalidasi otomatis** terhadap tagihan nyata | Ya — diuji pada 8 eksperimen, §5 |

---

## 1. Rate limit & kehabisan kuota — READY WITH CAVEAT

### 1a. Backoff (terverifikasi)

`src/evaluation/retry.py`:

- `is_rate_limit_error` (`retry.py:54-68`): cek `status_code == 429` dulu, lalu marker teks (`"429"`, `"rate limit"`, `"quota exceeded"`, `"usage limit"`, `"resource_exhausted"`, …).
- `_backoff_delay` (`retry.py:95-106`): rate-limit → `min(60 × 2^(n-1), 300)`; error biasa → `2 × 2^(n-1)`.
- `call_with_retry` (`retry.py:109-210`): retry per-request. `MAX_RETRIES=3` → backoff murni per request = 60 + 120 = **180 s** maksimum sebelum menyerah.

Nilai efektif (dimuat dari `.env`, diverifikasi dengan menjalankan `config.py`):

```
RATE_LIMIT_BACKOFF_BASE      = 60.0
RATE_LIMIT_BACKOFF_MAX       = 300.0
MAX_RETRIES                  = 3
RATE_LIMIT_CONSECUTIVE_LIMIT = 5
```

### 1b. Breaker runner (terverifikasi)

`src/experiments/runner.py`:

- `consecutive_rate_limits` diinisialisasi di `runner.py:735`.
- Pada sukses, counter **di-reset** (`runner.py:784`) — jadi hanya kegagalan **berurutan** yang dihitung.
- Pada exception (`runner.py:935`), jika `is_rate_limit_error(e)` → counter naik (`runner.py:944-945`); ketika `>= RATE_LIMIT_CONSECUTIVE_LIMIT` (5) → `rate_limit_stopped = True` (`runner.py:951-957`).
- Loop luar & dalam break pada `runner.py:1025-1028` → **abort sweep dengan bersih**, lalu `--resume` untuk melanjutkan.

**Jadi: breaker menghentikan sweep, bukan skip instance, bukan retry selamanya.** Ini perilaku yang benar.

### 1c. Peluang 429 / kuota 5 jam

**Yang bisa saya buktikan dari data:** pada rute berbayar yang akan dipakai, **tidak ada satu pun event rate-limit**.

- `results/EXP-20260930-415/logs/experiment.log`: **0** marker rate-limit, **0** `429` nyata (dari 622 baris).
- `logs/pilot200.log`, `logs/pilot_verified.log`, `logs/pilot15.log`: **0** marker.
- `logs/rq3_run.log`: **0** marker.

**Yang TIDAK bisa saya buktikan:** probabilitas 429 pada 14 jam mendatang. Saya tidak punya data kuota 9router, tidak tahu limit per jam, dan tidak bisa mengukurnya dari artefak. **Saya tidak akan menebak angka.**

Yang **bisa** saya laporkan sebagai fakta historis: 429 memang pernah terjadi pada rute lain — `logs/agentbench.log:140-143` menunjukkan `hit a rate limit — backing off 60.0s/120.0s`, dan `logs/agentbench.2026-08-09_02-15-07_833055.log:27130-27134` menunjukkan pola yang sama dengan `200.0s`/`300.0s` (cap). Jadi mekanismenya pernah aktif di repo ini, tetapi **bukan** pada model berbayar yang akan dipakai.

### 1d. Apakah `--rate-limit 2.0` cukup?

`run_final_sweep.py:166` → `--rate-limit 2.0`. Diteruskan `main.py:359` → `run_experiments(rate_limit_seconds=...)` → dipakai di `runner.py:927-933`:

```python
if rate_limit_seconds > 0:
    jitter = random.uniform(0.0, min(2.0, rate_limit_seconds * 0.5))
    delay = rate_limit_seconds + jitter
    time.sleep(delay)
```

**Penting:** delay ini dijalankan **per run** (setelah tiap strategi), **bukan per request**. Satu run berbayar mengirim puluhan request berurutan di dalam tool loop (`runner.py:757-914`, semua request terjadi di dalam `strategy.run(issue)`).

Terukur dari pilot 415: `api_requests` per strategi = direct 91, planning 139, review 180 (dari `generation_statistics.json`) — untuk 5 issue. Total ≈ 410 request untuk 15 run, yaitu **~27 request/run**. Untuk 150 run: **~4.100 request**.

Jadi 2,0 s hanya memberi jeda antar-**run**, bukan antar-**request**. Spacing antar-request efektifnya adalah latensi API itu sendiri (median ~47-171 s per run untuk 6-67 turn). Apakah itu cukup untuk menghindari 429 **tidak dapat saya tetapkan dari data** — tidak ada data kuota.

**CAVEAT:** `--rate-limit` memberi ilusi proteksi. Ia tidak menyebarkan request; ia menyebarkan run.

---

## 2. Wall-clock bound vs sweep 14 jam — READY WITH CAVEAT

### 2a. Angka terukur dari pilot (bukan tebakan)

Sumber: `results/EXP-20260930-415/artifacts/*/*/summary.json` (15 run).

| Metrik | Nilai |
|---|---|
| run terukur | 15 |
| jumlah | 1.336,1 s = **22,3 menit** |
| rata-rata | **89,1 s** |
| min / max | 26,0 s / **172,7 s** |
| run terlama | `django__django-11019` / `review` |

Proyeksi ke 150 run (**terukur**, bukan asumsi):

| Dasar | × 150 | Jam |
|---|---|---|
| rata-rata pilot | 13.361 s + 300 s (rate-limit) = 13.661 s | **3,8 jam** |
| maksimum pilot | 25.899 s + 300 s = 26.199 s | **7,3 jam** |

Pembanding dari pilot sebelumnya, `results/EXP-20260930-215/artifacts/*/*/summary.json` (15 run, budget sama):

| Metrik | Nilai |
|---|---|
| rata-rata | 136,5 s |
| maks | 389,2 s |
| proyeksi rata-rata × 150 | **5,7 jam** |
| proyeksi maks × 150 | **16,2 jam** |

**Kesimpulan:** proyeksi terukur (3,8–5,7 jam rata-rata; 7,3–16,2 jam skenario maksimum) **muat** dalam anggaran 14–17 jam, tetapi skenario maksimum dari pilot 215 **mendekati batas atas**. Perkiraan "14-17 jam" di brief konsisten dengan pilot 215, bukan dengan pilot 415 (yang lebih cepat).

**CAVEAT penting:** kedua pilot hanya 5 instance, dan 5 instance itu adalah 5 termudah/tercepat yang punya checkout lokal (`select_issues` mengurutkan by id). 45 instance sisanya **belum pernah diukur**. Proyeksi ini adalah **batas bawah**.

### 2b. Worst case per run dan apakah ada budget keseluruhan

`run_final_sweep.py:109` menetapkan `ACT_TIMEOUT_SECONDS = 3600`. Diverifikasi bahwa nilai ini benar-benar berlaku: `--set ACT_TIMEOUT_SECONDS=3600` (`run_final_sweep.py:162`) diterapkan `run_with_env.py:88-89` setelah membersihkan variabel `.env`, dan `config.py:219` membacanya — saya jalankan `Config.ACT_TIMEOUT_SECONDS` dengan env di-set dan mendapat **3600** (bukan default 1800).

Jumlah act per strategi (dari `review_strategy.py`):
- `direct`: 1 act → 1 × 3600 = 1,0 jam
- `planning`: 2 act → 2,0 jam
- `review`: planner + executor + reviewer + (revisi + re-review) × `MAX_REVISION_TURNS`. `.env` menetapkan `MAX_REVISION_TURNS=1` → **5 act** → **5,0 jam**

Kalau `MAX_REVISION_TURNS` dinaikkan ke 4 (sesuai ukuran reserve 32 = 4×8): 11 act → **11,0 jam untuk satu run**.

**Apakah ada budget waktu keseluruhan? TIDAK.** Diverifikasi dengan grep pada `src/` untuk `time_budget|max_seconds|overall|deadline|signal|alarm|KeyboardInterrupt|atexit` → tidak ada. Satu-satunya bound adalah per-act.

**Konsekuensi:** satu instance patologis (mis. provider hang berulang) bisa menahan sweep hingga 5 jam tanpa ada yang menghentikannya. Karena setiap act dibatasi 3600 s dan ada 5 act di review, satu run bisa 5 jam; tiga strategi pada satu instance bisa sampai ~8 jam.

Yang **menyelamatkan** dari bencana: savepoint per-run (`_append_jsonl`, `runner.py:332-335`) dan `write_issue_run_summary` dipanggil **per run** (`runner.py:916`, `1014`) — jadi kerja yang sudah selesai tetap tercatat meski sweep dihentikan. Tapi tidak ada yang menghentikannya secara otomatis.

---

## 3. Crash recovery mid-sweep — **NOT READY**

### 3a. State apa yang ada di disk?

Per run, runner menulis secara inkremental:

| Artefak | Kapan | Mekanisme |
|---|---|---|
| `predictions/<strategy>.jsonl` | per run (append) | `_append_jsonl`, `runner.py:332-335` |
| `predictions/predictions.jsonl` | per run (append) | idem |
| `patches/<id>_<strategy>.txt` | per run | `runner.py:892` |
| `artifacts/<id>/<strategy>/{summary.json,tool_calls.jsonl,...}` | per run | `_save_artifacts`, `runner.py:900` |
| `generation_result.csv` | **sekali, di akhir** | `_write_csv_atomically`, `runner.py:1042` |

Jadi **kerja selesai tersimpan per run dan dapat dipulihkan** — inilah alasan `_rows_from_savepoints` ada (`runner.py:355`). Ini benar.

### 3b. Apakah `--resume` ada dan bekerja?

`--resume` ada (`main.py:64-71`), butuh `--exp-id` (`main.py:72-80`), dan diteruskan ke `run_experiments(resume=..., experiment_id=...)` (`main.py:359,363`). Jalur skip ada di `runner.py:744-750`.

**Tetapi `tools/run_final_sweep.py` — satu-satunya entry point yang didokumentasikan untuk sweep 150 run — TIDAK meneruskan keduanya.** Diverifikasi (`build_cmd`, `run_final_sweep.py:150-170`):

```
build_cmd contains --resume        : False
build_cmd contains --exp-id        : False
```

Dan `run_experiments` akan membuat ID baru bila `experiment_id is None` (`runner.py:693`):

```python
exp_id = experiment_id or generate_experiment_id()
```

**Akibatnya:** menjalankan ulang `python tools/run_final_sweep.py` setelah crash:
1. Membuat **direktori EXP baru** (bukan melanjutkan yang lama).
2. Menjalankan **seluruh 150 run dari awal**.
3. Kerja lama tetap ada di direktori lama, tetapi **tidak disambung** dan tidak otomatis direkonsiliasi.

Dokumentasi `run_final_sweep.py:270-275` menyuruh pengguna memeriksa `check_sweep_state.py --exp <EXP-id>` dan `read_actual_bill.py --since ... --until ...` — tetapi skrip itu sendiri tidak memberi cara melanjutkan. **Recovery hanya mungkin jika operator tahu harus memanggil `main.py --resume --exp-id` secara manual dengan `--set` yang sama persis.**

### 3c. Lock file yang bisa memblokir restart?

`src/experiment_id.py` (bukan `src/utils/experiment_id.py` seperti di brief — path itu tidak ada):

- Lock: `results/experiment_index.lock`, dibuat `O_CREAT|O_EXCL` (`experiment_id.py:58-61`).
- Lock basi dipecah setelah `_STALE_LOCK_SECONDS = 60.0` (`experiment_id.py:21`, `_break_stale_lock` di `:24-40`).
- Retry 20× dengan jeda 0,1 s (`experiment_id.py:13-14`), lalu `RuntimeError`.

**Verdict: TIDAK memblokir.** Bahkan lock yang ditinggalkan proses mati dipecah otomatis setelah 60 s. Diverifikasi juga: saat ini **tidak ada** file `.lock` di `results/`.

Kritik kecil: lock basi dipecah **berdasarkan mtime**, dan `_break_stale_lock` dipanggil di dalam loop retry (`experiment_id.py:72`) — jadi dua proses yang benar-benar berjalan bersamaan lebih dari 60 s bisa saling memecah lock. Untuk sweep sekuensial ini tidak relevan.

### 3d. `sweep_started.json`: apakah menimpa jendela tagihan sebelumnya?

**YA — dan ini blocker B2.**

`run_final_sweep.py:233-249`:

```python
started = datetime.now(timezone.utc)
state = {... "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"), ...}
state_path = ROOT / "logs" / "sweep_started.json"
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")   # L249
```

Tidak ada pemeriksaan apakah file sudah ada; tidak ada pembacaan file lama; tidak ada append/arsip. Diverifikasi dengan grep: `sweep_started` muncul **hanya 2 kali** di seluruh repo (`run_final_sweep.py:53` komentar, `:247` path), dan **tidak pernah dibaca** (`read_text` tidak muncul di sekitarnya).

`state_path.write_text` juga dipanggil lagi di akhir (`:262`) untuk menambah `finished_utc`/`elapsed_minutes`/`rc` pada dict yang **sama**.

**Akibatnya:** kalau sweep pertama crash dan dijalankan ulang, `started_utc` **ditimpa** dengan waktu mulai attempt kedua. Jendela tagihan attempt pertama **hilang**. Karena `read_actual_bill.py` bekerja dari rentang `--since/--until`, tidak ada lagi cara mengetahui kapan attempt pertama mulai — jadi **request yang sudah dibayar pada attempt pertama tidak bisa direkonsiliasi dengan andal**.

Saat ini `logs/sweep_started.json` berisi attempt pilot:

```json
"started_utc": "2026-09-30T17:54:27Z",
"finished_utc": "2026-09-30T18:17:44Z",
"elapsed_minutes": 23.3, "rc": 0
```

Itu attempt yang **selesai** (`rc=0`), jadi belum ada kerugian. Kerugiannya terjadi pada crash yang di-restart.

---

## 4. Checkout hygiene across 150 runs — READY

Ini poin yang brief tandai "THIS MATTERS MOST". Jawabannya **tidak abort**.

### 4a. Ketiga strategi melempar

- `direct_strategy.py:36-41` → `raise RuntimeError(...)`
- `planning_strategy.py:33-38` → `raise RuntimeError(...)`
- `review_strategy.py:88-93` → `raise RuntimeError(...)`

Semuanya dipicu oleh `reset_working_tree()` yang mengembalikan `False` (`tools.py:635-688`, yang kini **memverifikasi** dengan `git status --porcelain` dan mengembalikan `False` bila masih kotor).

### 4b. Apakah exception itu mematikan sweep? **TIDAK.**

**Dibuktikan dengan eksekusi** (`.verify_ops_4.py`, temp dir, stub strategy — tanpa panggilan model):

```
TEST: 5 issues, strategy RAISES RuntimeError on issue 3 (dirty-checkout shape)
  run_experiments RETURNED NORMALLY (did not propagate the exception)
  strategies attempted: [1, 2, 3, 4, 5]
  CSV rows: 5
    django__django-1  VALID
    django__django-2  VALID
    django__django-3  ERROR      <-- dicatat sebagai kegagalan, bukan abort
    django__django-4  VALID
    django__django-5  VALID
  manifest execution_status: COMPLETED_WITH_ERRORS
  INCOMPLETE.json exists: True
```

Worst case, raise pada **setiap** instance: 3 run dicoba, ketiganya dicatat gagal, `run_experiments` tetap kembali normal.

**Mekanismenya, dengan `file:line`:**

- `runner.py:757` — `try:` membuka blok per-run.
- `runner.py:760` — `patch, result = strategy.run(issue)` ada di dalam `try` itu.
- `runner.py:935` — `except Exception as e:` menangkapnya.
- `runner.py:938-940` — dicatat `❌ FAILED: <instance> (<strategy>) — RuntimeError: ...`.
- `runner.py:971-976` — status diklasifikasi: `RATE_LIMIT` / `PROVIDER_ERROR` / `ERROR`. `RuntimeError` dari checkout → **`ERROR`**.
- `runner.py:990-995` — baris error di-append ke savepoint (jadi `--resume` tahu instance ini masih perlu dijalankan, karena `_is_finished_entry` menolak baris tanpa patch: `runner.py:267-272`).
- `runner.py:1001-1010` — `ExperimentResult` kosong dicatat.
- `runner.py:1014-1022` — `write_issue_run_summary` dipanggil dengan `success=False`.
- Loop berlanjut ke iterasi berikutnya — **tidak ada `break`** di jalur exception. `break` hanya terjadi pada `rate_limit_stopped` (`runner.py:1025-1028`).

**Blast radius: SATU run.** Instance itu muncul sebagai `ERROR` dan akan dicoba ulang oleh `--resume`. **Bukan blocker.** Usul perbaikan tidak diperlukan — tetapi karena brief memintanya bila abort: tidak ada yang perlu diperbaiki di sini. Yang perlu diperbaiki adalah jalur *recovery*-nya (B1), bukan jalur exception ini.

### 4c. Preflight

`tools/preflight_repos.py` ada dan memeriksa tiga hal per instance (`:112-126`): ada `.git`, HEAD == base_commit, dan working tree bersih. Keluar non-nol bila ada masalah (`:195`). Dokumentasinya menyebut audit menemukan **10 cache kotor** dan **1 repo dengan HEAD all-zeros** (`:9`). Jalankan ini sebelum sweep — `run_final_sweep.py:224` juga mengingatkannya.

`tools/clean_repos.py` ada untuk membersihkan.

---

## 5. Bill window — READY WITH CAVEAT

### 5a. Apa yang dibutuhkan `read_actual_bill.py`

`tools/read_actual_bill.py`:

- Butuh database SQLite 9router: `%APPDATA%/9router/db/data.sqlite` (`:32`). **Ada** — diverifikasi, 12.959.744 byte, mtime 2026-10-01 02:14.
- Membaca read-only dengan copy-then-read (WAL mode, `:51-57`).
- Query `usageHistory` dengan filter `timestamp >= ? AND timestamp < ?` (`:78-95`), format ISO-8601 `...Z` (`_iso`, `:35-48`). Perbandingan leksikografis — valid untuk format itu.
- Filter model opsional: `--model cbai/deepseek-v4.1-flash` → dipotong ke bagian setelah `/` menjadi `deepseek-v4.1-flash` (`:86`), karena 9router mencatat id upstream, bukan id routed. Ini sudah benar (dengan komentar yang menjelaskan bug sebelumnya).

### 5b. Apakah `run_final_sweep.py` merekam semua yang diperlukan?

**Cukup untuk rentang waktu, TIDAK cukup untuk perbandingan otomatis.**

`logs/sweep_started.json` berisi `started_utc`, `finished_utc`, `model`, `issues`, `strategies`, `runs_planned`, `total_tool_turns`, `revision_tool_turns`, `budget_floor_per_act`, `cost_limit_usd`, `act_timeout_seconds` (`run_final_sweep.py:234-246`). Itu **cukup** untuk `--since`/`--until`/`--model`.

**CAVEAT (blocker B4):** `--compare results/<EXP-id>` **tidak menemukan field biaya.** Diverifikasi dengan mereplikasi logika ekstraksi `read_actual_bill.py:140-151` terhadap **8** `generation_statistics.json` terbaru (termasuk pilot 415):

```
EXP-20260930-415: NONE -> --compare prints 'no cost field found'
files where --compare would find a cost: 0/8
```

Penyebabnya: `generation_statistics.json` tidak punya kunci `total_cost_usd` / `total_cost` / `cost_usd` di level atas, dan tidak punya `strategies` / `per_strategy` berisi dict dengan `total_cost_usd`. Kunci sebenarnya adalah:

```
top-level : summary, success_rate, avg_time_per_inference, cost_per_success,
            cache_hit_rate, patch_validity_rate, patch_quality, applyability_rate,
            apply_quality, failure_breakdown, api_requests, pricing
```

Biaya ada di `summary.<strategy>.mean_cost_usd_offpeak` (rata-rata, bukan total) — **bukan** bentuk yang dicari `--compare`. Jadi cross-check otomatis RQ3 terhadap tagihan nyata **tidak akan jalan** apa adanya; biaya nyata tetap bisa dibaca (`--since/--until`), tetapi perbandingan "model kami vs tagihan" harus dihitung manual dari CSV.

### 5c. Melewati tengah malam atau batas bulan

**Tidak masalah.** Window memakai timestamp UTC absolut (`_iso` → `...Z`), bukan tanggal kalender. Tidak ada logika "hari ini" atau "bulan ini" di `read_actual_bill.py`. `generate_experiment_id` memakai tanggal UTC untuk nama (`experiment_id.py:118`), tetapi itu hanya penamaan ID dan tidak mempengaruhi query tagihan. Sweep 14–17 jam yang dimulai sore dan berakhir keesokan paginya akan tercakup oleh satu rentang `--since/--until`.

**CAVEAT:** karena B2 (penimpaan `sweep_started.json`), window bisa hilang pada restart — masalahnya bukan pergantian hari, melainkan penimpaan.

---

## Yang TIDAK bisa saya pastikan

Dinyatakan eksplisit. Tidak ada angka yang saya tebak.

1. **Probabilitas 429 / kuota 5 jam pada 14 jam mendatang.** Tidak ada data kuota 9router di repo, dan saya tidak bisa mengukurnya. Yang bisa dilaporkan: **0 event** pada rute berbayar di pilot (`EXP-20260930-415`, `pilot200.log`, `pilot_verified.log`, `pilot15.log`, `rq3_run.log`), dan 429 **pernah** terjadi pada rute lain (`agentbench.log:140`).

2. **Durasi sebenarnya untuk 45 instance yang belum pernah dijalankan.** Proyeksi saya hanya dari 5 instance yang punya checkout lokal dan diurutkan by id. 45 sisanya **belum terukur**. Proyeksi 3,8–16,2 jam adalah batas bawah.

3. **Apakah `--rate-limit 2.0` cukup untuk menghindari 429.** Tidak bisa ditetapkan tanpa data kuota. Yang **bisa** dipastikan: delay itu per-**run**, bukan per-**request** (~27 request/run terukur dari `api_requests`: direct 91 + planning 139 + review 180 untuk 5 issue = 410 request / 15 run).

4. **Apakah satu run review benar-benar bisa mencapai 5 jam.** Itu batas **teoretis** dari 5 act × 3600 s. Yang terukur: maksimum pilot 415 = 172,7 s, pilot 215 = 389,2 s. Belum ada run yang mendekati bound sejak `ACT_TIMEOUT_SECONDS` diperkenalkan.

5. **Apakah operator akan tahu harus memakai `main.py --resume --exp-id` secara manual.** Itu pertanyaan prosedural, bukan pertanyaan kode. Tidak bisa saya verifikasi dari repo.

6. **Apakah `reset_working_tree` gagal pada instance nyata selama sweep.** Saya membuktikan **bagaimana** kegagalan ditangani (tercatat, sweep lanjut), bukan **apakah** akan terjadi. Preflight (`preflight_repos.py`) mengukur ini sebelum sweep; jalankan dan laporkan hasilnya.

7. **Kebenaran `--compare` setelah `generation_statistics.json` diperbaiki.** Saya menguji kondisi saat ini (0/8 menemukan biaya). Bila formatnya diubah, kesimpulan ini perlu diuji ulang.

---

## Usul perbaikan (tidak diterapkan — tugas ini READ-ONLY)

Diurutkan berdasarkan blast radius:

1. **B1** — Tambahkan `--resume`/`--exp-id` ke `build_cmd` (opsional lewat flag `--resume <EXP-id>` di `run_final_sweep.py`), dan tulis `exp_id` ke `logs/sweep_started.json` agar operator tahu direktori mana yang harus dilanjutkan.
2. **B2** — Sebelum menimpa, **arsipkan** `logs/sweep_started.json` ke `logs/sweep_started.<timestamp>.json`, atau **append** ke daftar attempt. Jangan pernah menimpa tanpa jejak.
3. **B3** — Tambahkan budget waktu keseluruhan (mis. `--max-hours 18`) yang memeriksa waktu sebelum tiap run dan berhenti dengan bersih + pesan `--resume`. Ini mengubah "sweep menggantung 5 jam" menjadi "sweep berhenti dan bisa dilanjutkan".
4. **B4** — Tambahkan `total_cost_usd` di level atas `generation_statistics.json` (atau ajari `read_actual_bill.py` membaca `summary.<strategy>.mean_cost_usd_offpeak` × jumlah run) supaya `--compare` benar-benar membandingkan.

Dua perbaikan operasional yang murah dan mengurangi risiko:

5. Jalankan `python tools/preflight_repos.py` sampai keluar nol **tepat sebelum** sweep (menutup 10 cache kotor).
6. Pastikan mesin tidak sleep selama sweep (power plan), karena tidak ada watchdog dan tidak ada resume otomatis dari entry point sweep.

---

## Lampiran — skrip verifikasi

| Skrip | Yang diuji |
|---|---|
| `.verify_ops_4.py` | Apakah exception satu strategi mematikan sweep (jawaban: tidak) |
| `.verify_ops_time.py` | Proyeksi wall-clock dari kedua pilot |
| `.verify_ops_ratelimit.py` | Event rate-limit nyata per log |
| `.verify_ops_bill.py` | Apakah `--compare` menemukan field biaya |
| `.verify_ops_state.py` | Semantik `sweep_started.json` + `--resume` di `build_cmd` |

**Catatan:** selama audit ini, `git status` menunjukkan `src/agents/tools.py`, `src/agents/base.py`, `src/prompts/*.md` dalam keadaan **modified** oleh **proses lain** (bukan sesi ini; muncul guard `[tool-guard]` baru di `tools.py:481-493` dan `:1058-1066`). Saya tidak mengubah file sumber apa pun. Semua temuan di atas bersumber dari `runner.py`, `retry.py`, `run_final_sweep.py`, `run_with_env.py`, `experiment_id.py`, `read_actual_bill.py`, `preflight_repos.py`, `config.py`, dan artefak `results/EXP-20260930-415` — tidak ada di antaranya yang bergantung pada perubahan konkuren itu.
