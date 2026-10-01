# AUDIT FINAL VERIFY — apakah pipeline SIAP untuk sweep 50 issue?

**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Sifat:** READ-ONLY. Tidak ada file sumber yang saya ubah. Semua skrip scratch ditulis ke `%TEMP%` (di luar repo) atau dijalankan inline.
**Metode:** setiap klaim dijalankan perintahnya; output dilampirkan verbatim.

> **⚠️ CATATAN PENTING — REPO BERGERAK SELAMA AUDIT INI.**
> Partner utama **berkomitmen 4 kali** ke repo ini saat audit berjalan:
> `da00708` (15:0x), `39314fa` (16:09), `eed55d6` (16:17), dan HEAD bergerak lagi setelahnya.
> Hasil awal saya **usang di tengah jalan**. Setiap angka di bawah karena itu disertai
> commit/hash tempat ia diukur. Snapshot terakhir saya mencatat **0 file berubah** selama
> 4 run suite berjalan (~11 menit) — jadi angka final di §2 dan §5 adalah snapshot yang stabil.

---

## Ringkasan verdict

| # | Area | Verdict |
|---|---|---|
| 1 | Konfigurasi sweep bisa merevisi | **TERVERIFIKASI** |
| 2 | Test tidak bergantung lingkungan | **TERVERIFIKASI — setelah fix di tengah audit** (sebelumnya SALAH) |
| 3 | Model yang dipanggil sweep = berbayar | **TERVERIFIKASI** |
| 4 | `--resume` melanjutkan, bukan mengulang | **TERVERIFIKASI** |
| 5 | Angka dokumen cocok dengan kenyataan | **SALAH** (test count) + 3 ketidakcocokan `.env` |

**Kesimpulan:** 5 blocker lama **benar-benar diperbaiki dan terbukti bekerja**. Tetapi audit ini
menemukan **satu kelas bug yang sama persis dengan yang dilaporkan brief** masih hidup di tempat
lain (gate kesiapan), dan **angka dokumen masih salah**.

---

## Blocker, diurutkan berdasarkan blast radius

| Rank | Blocker | Blast radius | Status |
|---|---|---|---|
| **B1** | `tools/readiness_report.py` — gate "READY, all 7 checks pass" — **tidak menjalankan unit test suite** | **Gate bisa HIJAU sementara suite MERAH.** Ini gerbang terakhir sebelum 150 run / $7-11 / 14 jam. Rasa aman palsu. | **LIVE** (per `eed55d6`) |
| **B2** | Test suite **bergantung lingkungan** (`MAX_REVISION_TURNS` dkk) | 2 test gagal/berbeda hasil tergantung shell → "semua hijau" bisa berarti konfigurasi efektif salah. | **DIPERBAIKI di tengah audit** (`39314fa`), **terverifikasi hijau** |
| **B3** | `docs/MEMORY.md` + `docs/HANDOFF_20261001.md` klaim **"421 test lulus"** | **Salah** — aktual **424** (tanpa probe) / **426** (dengan probe). Dokumen dipercaya orang. | **LIVE** |
| **B4** | Tabel `.env` di MEMORY mencantumkan 2 key yang **tidak ada di `.env`** | Membaca `.env` tidak menemukan yang tabel katakan ada. | **LIVE** |
| **B5** | `.env` tidak menyetel `ACT_TIMEOUT_SECONDS` → default **1800**, sweep pass **3600** | `python src/main.py` langsung memakai 1800, **beda dari sweep**. Bukan blocker untuk sweep, tapi jebakan reprodusibilitas. | **LIVE** |

B1 adalah yang terburuk: ia adalah **gate**, bukan sekadar dokumen. B2/B3/B4/B5 hanya menyesatkan
pembaca; B1 bisa meloloskan suite yang merah ke dalam run yang tidak bisa diulang.

---

## 1. Apakah konfigurasi sweep benar-benar bisa merevisi? — TERVERIFIKASI

Klaim: `REVISION_TOOL_TURNS=48`, `MAX_REVISION_TURNS=4` → setiap act revisi dapat **6+6**.

### 1a. `python tools/check_budget_fairness.py`

Dijalankan dengan env anak bersih (`TOTAL_TOOL_TURNS`/`REVISION_TOOL_TURNS`/`MAX_REVISION_TURNS`
dihapus **dari salinan env anak**, bukan dari shell). Output verbatim:

```
  BUDGET FAIRNESS -- total=200 floor=10 reserve=48 mode=per_task rounds=4
==============================================================================

  strategy   base acts                base  revision   TOTAL
  ------------------------------------------------------------
  direct     200                       200         0     200
  planning   190+10                    200         0     200
  review     132+10+10                 152        48     200

  REVISION GRANTS (per act, in order)
    rounds=4  6+6  6+6  6+6  6+6

  GRAND TOTAL (what the run actually consumes) -- THIS is the invariant
    all three spend exactly 200 turns -> FAIR
...
  FAIR: every strategy's task budget is identical, and every revision
        act can both read and edit.
EXIT=0
```

### 1b. `python tools/verify_revision_rounds.py`

```
  TOTAL_TOOL_TURNS   = 200
  REVISION_TOOL_TURNS= 48
  MAX_REVISION_TURNS = 4

  round    revision  re-review
  ------------------------------
  1               6          6
  2               6          6
  3               6          6
  4               6          6

  reserve spent: 48 / 48   unused: 0

  OK -- every round can fund a revision that reads AND edits, and no
  reserved turn is left ungranted.
EXIT=0
```

### 1c. Apakah kedua tool SETUJU? — **YA.**

| Sumber | Grant per round | Total |
|---|---|---|
| `check_budget_fairness.py` | `6+6  6+6  6+6  6+6` | 200/200/200 |
| `verify_revision_rounds.py` | `6 / 6` × 4 | 48/48 terpakai |

**Keduanya setuju.** Perbaikan pada `verify_revision_rounds.py` **BENAR**: barisnya kini
`expected = 2 * rounds * USABLE_GRANT` (`verify_revision_rounds.py:125`), bukan
`expected = 8 * rounds`. Saya verifikasi aritmetikanya: `2 × 4 × 6 = 48` = nilai yang
dikonfigurasi, jadi nilai BENAR tidak lagi ditandai salah. Komentar di `:118-124` menjelaskan
persis bug yang digantikannya.

**Catatan:** kedua tool kini berbagi konstanta yang sama — `USABLE_REVISION_GRANT = 6`
(`check_budget_fairness.py:32`) dan `USABLE_GRANT = 6` (`verify_revision_rounds.py:36`), dengan
komentar yang merujuk satu sama lain. Itu yang mencegah mereka berbeda lagi.

### 1d. `python tools/run_final_sweep.py --dry-run` — apakah `--set` ADA?

Output verbatim (bagian Command), dipotong pada bagian `--set`:

```
Command:
  ...\tools\run_with_env.py --set OPENCODE_MODEL=cbai/deepseek-v4.1-flash
    --set TOTAL_TOOL_TURNS=200 --set BUDGET_MODE=per_task --set BUDGET_FLOOR_PER_ACT=10
    --set REVISION_TOOL_TURNS=48 --set MAX_REVISION_TURNS=4
    --set COST_LIMIT_USD=3.0 --set ACT_TIMEOUT_SECONDS=3600
    --provider opencode --strategies direct planning review
    --instance-ids <50 ids> --rate-limit 2.0 --dry-run
```

- `--set REVISION_TOOL_TURNS=48` → **ADA** ✅
- `--set MAX_REVISION_TURNS=4` → **ADA** ✅
- Header juga mencetak `revision : up to 4 rounds, 48 turns reserved (6 per act)` ✅

**VERDICT: TERVERIFIKASI.** 200/200/200, setiap act revisi dapat 6 turn (kebutuhan terukur 6),
kedua tool setuju, dan sweep mem-pass kedua knob secara eksplisit.

---

## 2. Apakah test benar-benar tidak bergantung lingkungan? — TERVERIFIKASI (setelah fix)

**Ini temuan terpenting audit ini, dan jawabannya berubah di tengah audit.**

### 2a. Pengukuran PERTAMA (HEAD ≈ `da00708`, sebelum partner commit fix)

Dua run suite, env anak bersih vs kotor, pada kode saat itu:

```
RUN A CLEAN            exit=1   2 failed, 424 passed
RUN B DIRTY 40/8/1     exit=1   1 failed, 425 passed
```

**Jumlahnya BERBEDA → itu bug**, tepat kelas yang brief gambarkan. Test yang gagal:

```
FAILED tests/test_response_utils.py::test_config_values_are_restored_after_the_reload   (hanya di CLEAN)
FAILED tests/test_zz_probe_config_leak.py::test_probe_would_fail_if_config_leaked        (di keduanya)
```

- `test_config_values_are_restored_after_the_reload` **lulus di env kotor** hanya karena
  `MAX_REVISION_TURNS=1` di shell kebetulan cocok dengan nilai yang diharapkan test itu —
  **persis pola "421 lulus itu PALSU"** yang dilaporkan brief, dalam bentuk lain.
- `test_probe_would_fail_if_config_leaked` gagal karena Config memang berbeda dari `.env` —
  itu memang tujuannya (canary env drift), jadi kegagalannya adalah **deteksi yang benar**.

Saya mengisolasi penyebabnya:
```
B1 probe alone                  exit=0  2 passed
B2 budget_modes + probe         exit=1  1 failed, 23 passed   <-- REPRODUCER
B3 response_utils + probe       exit=0  12 passed
B4 strategies + probe           exit=0  12 passed
B5 tool_budget + probe          exit=0  15 passed
```
`test_budget_modes.py` menyetel `Config.TOTAL_TOOL_TURNS = 40` / `Config.REVISION_TOOL_TURNS = 8`
(`test_budget_modes.py:236-237`) dan fixture `_restore_config` menyimpan/mengembalikan keduanya —
tetapi **`Config.MAX_REVISION_TURNS` tidak disimpan**, sehingga bocor ke test berikutnya.

### 2b. Pengukuran KEDUA (HEAD ≈ `eed55d6`, setelah `39314fa` + `eed55d6`)

Partner meng-commit `39314fa fix(tests): stop tests leaking Config into every later test`
(11 file, +627/-46) **saat saya sedang mengaudit**. Saya jalankan ulang, kali ini dengan
**hash snapshot sebelum/sesudah** untuk memastikan bukan target bergerak:

```
SNAPSHOT HASHES
  .env                                           2b1ef1f2d2c2

  A suite+probe  CLEAN          exit=0   426 passed, 2 warnings in 214.48s
  B suite+probe  DIRTY 40/8/1   exit=0   426 passed, 2 warnings in 160.06s
  C suite NO-probe CLEAN        exit=0   424 passed, 2 warnings in 163.00s
  D suite NO-probe DIRTY 40/8/1 exit=0   424 passed, 2 warnings in 170.88s

  files changed DURING the run: 0
```

- **0 file berubah** selama 11 menit → snapshot ini stabil, bukan target bergerak.
- **A == B** (426 == 426) dan **C == D** (424 == 424): hasil **identik** di env bersih dan kotor.
- Suite **HIJAU** di keempat konfigurasi.

**VERDICT: TERVERIFIKASI** untuk kode saat ini. Suite tidak lagi bergantung lingkungan, dan
`test_zz_probe_config_leak.py` yang di-commit adalah **canary yang menegakkan** itu: ia membandingkan
`Config` dengan `.env` dan gagal kalau ada yang bocor. Saya buktikan canary itu **berfungsi**:
dijalankan sendirian dengan env anak kotor ia gagal dengan pesan yang tepat
(`AssertionError: TOTAL_TOOL_TURNS: Config=40 but .env='200'`), dan lulus dengan env bersih.

**Caveat:** hasil ini berlaku untuk HEAD `eed55d6`. HEAD sudah bergerak lagi setelahnya; angka final
harus diambil ulang sebelum run.

### 2c. Grep test yang memanggil `strategy.run(` tanpa mem-pin

| File | Baris | Pin? |
|---|---|---|
| `test_strategies.py` | 54, 63, 72, 103, 180, 192, 237, 290, 335, 372 | **Semua di-pin** via `monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS"/"TOTAL_TOOL_TURNS"/"REVISION_TOOL_TURNS", ...)` |
| `test_review_strategy.py` | 131, 197, 218 | 197 di-pin; 131 & 218 **tidak** — tetapi keduanya meng-assert terhadap `rev_mod.Config.MAX_REVISION_TURNS` (baris 138) atau nilai tetap 3 (baris 222), jadi **tidak akan gagal kalau `MAX_REVISION_TURNS=4`** |
| `test_checkout_reset_guard.py` | 150, 174 | Raise sebelum membaca Config; tidak relevan |

`test_review_strategy.py:138` adalah pola yang **benar** — ia membaca
`3 + 2 * rev_mod.Config.MAX_REVISION_TURNS` alih-alih menulis angka tetap, sehingga nilainya
mengikuti konfigurasi apa pun. Itu yang seharusnya dilakukan test lain.

**Grep `TOTAL_TOOL_TURNS` / `REVISION_TOOL_TURNS` ambient:** `test_budget_modes.py:236-237`
menyetel `Config` langsung (kini dipulihkan), `test_strategies.py:154-155/228-230/280-282/326-328`
semuanya di-pin, `test_response_utils.py` memakai `os.environ` + `importlib.reload` dengan
restore eksplisit di `finally` (baris 127-135). **Tidak ada lagi yang membaca nilai ambient tanpa pin.**

---

## 3. Apakah model sweep benar-benar BERBAYAR? — TERVERIFIKASI

### 3a. `python tools/check_effective_model.py`

```
  .env OPENCODE_MODEL            : oc/space-bunny-free
  sweep --set OPENCODE_MODEL     : cbai/deepseek-v4.1-flash

  resolved WITHOUT the override  : oc/space-bunny-free
  resolved WITH the sweep's --set: cbai/deepseek-v4.1-flash

  OK -- the sweep calls cbai/deepseek-v4.1-flash.
EXIT=0
```

### 3b. Verifikasi INDEPENDEN: apakah override diterapkan SETELAH `.env`?

Saya **tidak** memakai tool di atas untuk ini (ia bisa saja mengulang asumsinya). Saya meng-import
modul `tools/run_with_env.py` yang **asli**, mencegat `subprocess.call`-nya, dan memeriksa env dict
yang akan diserahkan ke `src/main.py` — jadi yang diuji adalah jalur kode nyata, bukan replika:

```
shell BEFORE      : OPENCODE_MODEL='STALE-SHELL-VALUE'
[env] cleared 30 stale variable(s) so .env wins: ..., OPENCODE_MODEL, ..., TOTAL_TOOL_TURNS, ...
[env] OVERRIDE OPENCODE_MODEL = cbai/deepseek-v4.1-flash
[env] OVERRIDE TOTAL_TOOL_TURNS = 200

  child OPENCODE_MODEL    : 'cbai/deepseek-v4.1-flash'
  child TOTAL_TOOL_TURNS  : '200'
  override beats .env     : True
  stale shell value gone  : True

  Config.OPENCODE_MODEL   : 'cbai/deepseek-v4.1-flash'   (dari proses anak dengan env itu)
  provider binds model    : ['    L45: self.model = Config.OPENCODE_MODEL']
```

Urutannya benar: `run_with_env.py` **membersihkan** dulu setiap key yang ada di `.env`
(`run_with_env.py:70-73`), lalu **menerapkan `--set` terakhir** (`:88-89`). Karena `load_dotenv()`
di `config.py:17` tidak menimpa variabel yang sudah ada, override menang.

### 3c. Apakah `Config.OPENCODE_MODEL` benar-benar dipakai provider?

`opencode_provider.py:45` → `self.model = Config.OPENCODE_MODEL`, dan `Config.OPENCODE_MODEL`
dibaca dari env di `config.py:119-120`. Model itu lalu dipakai di setiap panggilan
(`generate` `:112`, `generate_with_tools` `:169`). **Rantainya utuh.**

### 3d. Bonus — apakah sweep kebal terhadap shell KOTOR?

Kelas bug brief: shell mengekspor `MAX_REVISION_TURNS=1` menimpa `.env` (=4). Saya uji apakah
**launcher sweep** kebal:

```
  key                     shell (dirty)   child (sweep)   wins?
  TOTAL_TOOL_TURNS        40              200             True
  REVISION_TOOL_TURNS     8               48              True
  MAX_REVISION_TURNS      1               4               True
  Config under that env     : T=200 R=48 M=4
```

**VERDICT: TERVERIFIKASI.** Sweep memanggil model berbayar, override menang atas `.env` **dan**
atas shell kotor, dan provider membaca nilai yang benar.

---

## 4. Apakah `--resume` benar-benar melanjutkan? — TERVERIFIKASI

### 4a. `--resume` tanpa `--exp-id` → exit 2

```
EXIT=2
ERROR: --resume needs --exp-id, otherwise there is nothing to continue.
```
`run_final_sweep.py:234-237`. **Sesuai klaim.**

### 4b. `--resume --exp-id EXP-20260930-415 --dry-run` → flag ADA

```
Command:
  ...\run_with_env.py ... --instance-ids <50 ids> --rate-limit 2.0 \
    --resume --exp-id EXP-20260930-415 --dry-run
```
`--resume` **dan** `--exp-id EXP-20260930-415` keduanya ADA di perintah tercetak
(`build_cmd`, `run_final_sweep.py:207-210`). **Sesuai klaim.**

### 4c. `python tools/verify_resume_logic.py`

```
  [OK  ] a VALID run with a patch counts as finished (would be skipped)
  [OK  ] a TIMEOUT run is NOT finished, so --resume retries it
  [OK  ] a ERROR run is NOT finished, so --resume retries it
  [OK  ] a RATE_LIMIT run is NOT finished, so --resume retries it
  [OK  ] a PROVIDER_ERROR run is NOT finished, so --resume retries it
  [OK  ] a NO_DIFF run IS retried by --resume (the model may answer differently)
  [OK  ] the resume key changes with the thinking flag
  [OK  ] the resume key changes with the model
  [OK  ] the loader returns the key for a successful run
  [OK  ] the loader does NOT return the failed run's key
  [OK  ] a row WITHOUT model_name_or_path produces a key --resume cannot match

  All checks pass
EXIT=0
```

### 4d. `python tools/verify_resume_keys_match.py` — apakah baris NYATA punya `model_name_or_path`?

```
  EXP-20260930-415/direct.jsonl  (5 rows)
    instance_id                 5/5    django__django-10914
    model_name_or_path          5/5    cbai/deepseek-v4.1-flash
    thinking                    5/5    False
    patch_status                5/5    VALID
    example key: 'django__django-10914|cbai/deepseek-v4.1-flash|False'
  ... (planning 5/5, review 5/5, dan 3 eksperimen lain) ...

  Every row carries the fields the loader keys on
EXIT=0
```
**Semua baris punya `model_name_or_path`.** `--resume` akan mencocokkan key, bukan mengulang.

### 4e. Verifikasi END-TO-END (yang tool di atas akui TIDAK dicakupnya)

`verify_resume_logic.py` menyatakan eksplisit di outputnya: *"WHAT THIS DOES NOT COVER: the runner's
own wiring — that it CALLS the loader with the same path and the same model string it writes with."*
Saya tutup celah itu dengan menjalankan `run_experiments` nyata:

```
PASS 1: fresh run of 3 issues (no --resume)
  exp_id            : EXP-20261001-129
  runs attempted    : 3  ['django__django-1', 'django__django-2', 'django__django-3']
  jsonl rows        : 3

PASS 2: resume the SAME exp dir with the SAME model -> must skip all 3
  runs attempted    : 0  []
  SKIPPED all 3     : True

PASS 3: resume with a DIFFERENT model -> keys must NOT match, all 3 re-run
  runs attempted    : 3  ['django__django-1', 'django__django-2', 'django__django-3']
  re-ran all 3      : True
```

**VERDICT: TERVERIFIKASI.** `--resume` melewati kerja yang sudah selesai (3/3 → 0 dijalankan),
mengulang saat konfigurasi berubah (3/3), dan gate `--exp-id` bekerja.

---

## 5. Apakah angka dokumen cocok dengan kenyataan? — **SALAH** (sebagian)

### 5a. ❌ `docs/MEMORY.md` + `docs/HANDOFF_20261001.md`: "421 test lulus"

| Klaim | Lokasi | Aktual |
|---|---|---|
| **421 lulus** | `MEMORY.md:4, :24, :61, :528, :633, :1065` | **424** (suite tanpa probe) / **426** (dengan probe) |
| **421 lulus** | `HANDOFF_20261001.md:6, :129` | idem |

Output aktual: `424 passed, 2 warnings` (tanpa probe) dan `426 passed, 2 warnings` (dengan probe),
dikonfirmasi di **empat** konfigurasi env dengan 0 file berubah. **Angka 421 salah di 8 tempat.**

Perhatikan: `readiness_report.py:9-10` (baru) **sendiri menyebut** *"421 tests pass" was FALSE* —
jadi partner sudah tahu, tetapi **belum memperbarui MEMORY/HANDOFF**. Keduanya masih di mtime
20:08/20:16 dan masih memuat 421.

### 5b. ❌ Tabel `.env` di `docs/MEMORY.md` — 2 key tidak ada di `.env`

| Key di tabel MEMORY (`:975-976`) | Nilai diklaim | `.env` sebenarnya |
|---|---|---|
| `COST_LIMIT_USD` | `3.0` | **ABSENT** (hanya lewat `--set` sweep) |
| `PRICING_MODEL_OVERRIDE` | *(kosong)* | **ABSENT** |

Untuk `COST_LIMIT_USD` efeknya kebetulan sama (default `Config` = 3.0, `config.py:233`), tetapi
tabelnya tetap menyatakan sesuatu yang tidak ada di file. `PRICING_MODEL_OVERRIDE` default `""`
(`config.py:227`), jadi "kosong" benar secara nilai tapi bukan baris `.env`.

### 5c. ⚠️ `ACT_TIMEOUT_SECONDS` tidak ada di `.env` dan tidak ada di tabel MEMORY

- `.env`: **ABSENT**
- `Config.ACT_TIMEOUT_SECONDS` default = **1800** (`config.py:219`, diverifikasi dengan menjalankannya)
- Sweep mem-pass **3600** (`run_final_sweep.py:129,192`)

**Akibatnya: `python src/main.py` langsung memakai 1800, sweep memakai 3600.** Tabel `.env` MEMORY
(`:964-981`) tidak mencantumkan key ini sama sekali, padahal ia satu-satunya knob yang membedakan
jalur langsung dari jalur sweep. Ini jebakan reprodusibilitas, bukan blocker sweep.

### 5d. ✅ Klaim lain di `HANDOFF_20261001.md` §6 — semua TERVERIFIKASI

| Klaim | Perintah | Hasil |
|---|---|---|
| Preflight **50/50 pristine**, 0 masalah | `tools/preflight_repos.py` | `50/50 usable, 0 with problems` ✅ |
| Fairness **FAIR** — 200/200/200 | `tools/check_budget_fairness.py` | FAIR ✅ |
| Putaran revisi **6+6** (kebutuhan 6) | `tools/verify_revision_rounds.py` | `6 / 6` ×4, `48/48` ✅ |
| Model efektif **berbayar** | `tools/check_effective_model.py` | `cbai/deepseek-v4.1-flash` ✅ |
| Resume gate → **exit 2** | `--resume` tanpa `--exp-id` | exit 2 ✅ |
| Resume jalan | `--resume --exp-id X` | flag diteruskan ✅ |
| **Cross-check biaya −5,28%** | `read_actual_bill.py --compare` | `$0.551462` vs `$0.582197` = **−5.28%** ✅ |
| Disk | `tools/check_disk_budget.py` | `98.2 GB free vs 0.22 GB needed` ✅ |

Biaya diverifikasi verbatim:
```
  actual bill (9router)  : $0.582197
  our computed cost      : $0.551462   [15 rows from generation_result.csv:cost_usd_actual]
  difference             : $-0.030735  (-5.28%)
```
Perhatikan B4 dari audit sebelumnya **benar-benar hidup**: `--compare` kini membaca
`cost_usd_actual` dari CSV, bukan mencari `total_cost_usd` di `generation_statistics.json`.

### 5e. ✅ Klaim "6 turn, 2 edit" — TERVERIFIKASI (n=1)

```
  django__django-10924
    base    : 10 turns, 11 calls, edits=2  -> EDITED
    revision1:  6 turns, 6 calls, edits=2  -> EDITED
    total revision edits: 2
```
(`tools/audit_revision_edits.py --exp EXP-20260930-415`). Angka 6 dan 2 benar. **Caveat: hanya 1
instance** yang punya act revisi yang benar-benar mengedit, jadi 6 adalah sampel tunggal — angka
yang dipakai untuk menetapkan `REVISION_TOOL_TURNS=48` bersandar pada satu observasi.

### 5f. Estimasi biaya — konsisten

`tools/estimate_sweep_cost.py`: `cost/150 = $10.72`. Klaim brief "$7–11" → **konsisten**.

---

## Blocker B1 secara rinci — gate yang tidak menutup apa yang diklaimnya

`tools/readiness_report.py` (commit `eed55d6`) adalah gerbang terakhir sebelum run 150 × $7-11.
Saya jalankan:

```
  READINESS REPORT
  NOTE: ignoring shell overrides for TOTAL_TOOL_TURNS, REVISION_TOOL_TURNS, ...
  [PASS] preflight repos      every checkout pristine at its base_commit
  [PASS] budget fairness      200/200/200 and every revision act can read AND edit
  [PASS] revision rounds      the configured reserve funds every configured round
  [PASS] effective model      the sweep calls the PAID model, not the free one
  [PASS] disk budget          artifacts fit on disk for the whole sweep
  [PASS] resume logic         completed runs are skipped, failures retried
  [PASS] resume keys          real jsonl rows carry the fields the loader keys on
  READY: all 7 checks pass.
```

Isinya (`CHECKS`, `:30-45`) adalah **7 tool**, dan **tidak ada `pytest` di daftar itu.** Saya
verifikasi dengan grep: `readiness_report.py` tidak menyebut `pytest`, `test suite`, atau `unit test`
sama sekali.

**Mengapa ini blocker:** tepat sebelum audit ini, suite **MERAH** (`2 failed, 424 passed`) sementara
ke-7 check itu semuanya PASS. Jadi gate ini **bisa mencetak `READY: all 7 checks pass` dengan suite
yang merah.** Itu persis pola yang brief minta dicari — "terlihat benar" versus "benar" — dan ironisnya
tool ini dibuat untuk menutup kelas bug itu (docstringnya menyebut "421 tests pass was FALSE"),
tetapi ia sendiri tidak menjalankan test suite.

**Perbaikan minimal (tidak saya terapkan):** tambahkan
`("unit tests", [sys.executable, "-m", "pytest", "tests", "-q"], "...")` sebagai check ke-8 —
dengan catatan bahwa biayanya ~3 menit, yang tidak berarti di depan run 14 jam.

---

## Yang TIDAK BISA saya verifikasi

Dinyatakan eksplisit. Tidak ada angka yang saya tebak.

1. **Apakah HEAD final stabil.** Repo berkomitmen 4× selama audit ini (`da00708` → `39314fa` →
   `eed55d6` → bergerak lagi). Angka suite (424/426) diukur pada snapshot `eed55d6` dengan
   0 file berubah selama run; **setiap commit baru membatalkannya.**
2. **Apakah 150 run selesai dalam 14–17 jam.** Hanya 5 instance terukur (yang punya checkout lokal
   dan terurut by id). 45 sisanya belum pernah dijalankan. Proyeksi apa pun adalah batas bawah.
3. **Probabilitas 429 / kuota 5 jam.** Tidak ada data kuota 9router di repo.
4. **Apakah "6 turn" generalisasi.** Itu n=1 (`django-11024` revision1). `REVISION_TOOL_TURNS=48`
   bersandar pada satu observasi.
5. **Apakah `readiness_report.py` sengaja melewatkan test suite.** Saya membaca kodenya, bukan
   niat penulisnya.
6. **Apakah `test_zz_probe_config_leak.py` seharusnya di-commit.** Ia membandingkan `Config`
   dengan `.env`, jadi ia **sengaja** gagal kalau shell kotor — itu canary, tetapi juga berarti
   suite akan merah bagi siapa pun yang menjalankannya dengan env drift. Saya tidak bisa menilai
   apakah itu trade-off yang diinginkan.

---

## Lampiran — perintah yang dijalankan

| Perintah | Area | Verdict |
|---|---|---|
| `check_budget_fairness.py` | 1 | FAIR, 6+6 ×4, 200/200/200 |
| `verify_revision_rounds.py` | 1 | OK, 6/6 ×4, 48/48 |
| `run_final_sweep.py --dry-run` | 1 | `--set REVISION_TOOL_TURNS=48`, `--set MAX_REVISION_TURNS=4` ADA |
| `pytest tests -q` × 4 (env bersih/kotor, ± probe) | 2 | 424/426 identik, 0 file berubah |
| `check_effective_model.py` | 3 | berbayar |
| import `run_with_env` + intercept `subprocess.call` | 3 | override menang atas `.env` **dan** shell kotor |
| `run_final_sweep.py --resume` (tanpa/sedang `--exp-id`) | 4 | exit 2 / flag ADA |
| `verify_resume_logic.py` | 4 | 18/18 OK |
| `verify_resume_keys_match.py` | 4 | semua baris punya `model_name_or_path` |
| `run_experiments` resume end-to-end (stub) | 4 | skip 3/3, ulang 3/3 saat model beda |
| `preflight_repos.py` | 5 | 50/50 pristine |
| `read_actual_bill.py --compare` | 5 | −5,28% ($0.551462 vs $0.582197) |
| `check_disk_budget.py` | 5 | 98,2 GB free vs 0,22 GB needed |
| `estimate_sweep_cost.py` | 5 | $10.72 / 150 run |
| `audit_revision_edits.py --exp EXP-20260930-415` | 5 | 6 turn, 2 edit (n=1) |
| `readiness_report.py` | B1 | 7/7 PASS — **tanpa menjalankan test suite** |
| `check_config_reload_poison.py` | 2 | POISONED — mekanisme kebocoran terkonfirmasi |

**Skrip scratch** (semua di `%TEMP%`, tidak ada di repo):
`verify_final_snapshot.py`, `verify_bisect.txt`, `verify_fail_iso.txt`, `verify_b2_full.txt`,
`readiness.txt`, `verify_audit_tests.txt`.

**Catatan proses:** partner utama berkomitmen ke repo ini selama audit berjalan, termasuk
memperbaiki bug yang sedang saya ukur (`39314fa`). Itu berarti sebagian temuan saya di §2
**sudah diperbaiki sebelum laporan ini selesai** — saya mencatat keduanya (sebelum dan sesudah)
alih-alih hanya yang terakhir, karena buktinya berguna untuk menilai apakah perbaikannya benar.
