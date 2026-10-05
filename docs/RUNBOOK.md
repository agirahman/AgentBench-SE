# Runbook — AgentBench-SE

Panduan menjalankan eksperimen: fase **testing** (model gratis) lalu **final run**
(DeepSeek). Ikuti urutannya.

---

## 0. Peringatan penting: environment drift

`load_dotenv()` **tidak menimpa** environment variable yang sudah ada di shell.
Kalau shell Anda sudah punya `OPENROUTER_MODEL` atau `TOOLCALL_ENABLED`, nilai
shell itu yang menang — **bukan** `.env` yang Anda edit.

Ini pernah terjadi: `.env` berisi `stealth/space-bunny-alpha` + `TOOLCALL_ENABLED=true`,
tapi shell punya `poolside/laguna-s-2.1:free` + `false`. Run akan memakai model dan
mekanisme patch yang berbeda dari yang tampak di `experiment.yaml`.

`config.py` sekarang **mendeteksi dan memperingatkan** ini saat startup:

```
CONFIG DRIFT: environment variables override .env
    OPENROUTER_MODEL: shell='poolside/laguna-s-2.1:free'  .env='stealth/space-bunny-alpha'
    TOOLCALL_ENABLED: shell='false'  .env='true'
```

**Selalu baca peringatan ini.** Kalau muncul, bersihkan dulu:

```powershell
Remove-Item Env:OPENROUTER_MODEL, Env:TOOLCALL_ENABLED, Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
```

---

## 1. Setup

```powershell
cd D:\development\Skripsi2\AgantBech-SE
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$env:NO_COLOR = "1"
```

Verifikasi konfigurasi yang **benar-benar terbaca** (bukan yang Anda kira):

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from config import Config; print('model   =', Config.OPENROUTER_MODEL); print('toolcall=', Config.TOOLCALL_ENABLED); print('turns   =', Config.MAX_TOOL_TURNS)"
```

Harus keluar: `model = stealth/space-bunny-alpha`, `toolcall= True`.

---

## 2. Fase testing (model gratis)

`.env` sudah diarahkan ke `stealth/space-bunny-alpha` (gratis di OpenRouter).
Jalankan bertahap — jangan langsung 50 issue.

### 2a. Smoke test: 1 issue, 1 strategi

```powershell
.venv\Scripts\python.exe src/main.py --provider openrouter --strategies direct --issues 1
```

Yang harus terlihat:
- `[toolcall] role=direct tool=read_file`
- `[toolcall] role=direct tool=edit_file`
- `[toolcall] role=direct tool=git_diff`
- `✅ ... inferences | model=stealth/space-bunny-alpha`

### 2b. Cek patch-nya benar

```powershell
# Patch harus ada isinya dan berstatus VALID, bukan NORMALIZE/NO_DIFF
Get-Content results/EXP-*\generation_result.csv | Select-String -Pattern 'VALID|NORMALIZE|NO_DIFF'
```

Untuk patch terbaru, lihat `results/EXP-*/patches/`. Isi file harus berupa diff
yang bisa di-apply. Verifikasi langsung:

```powershell
git -C datasets/repos/psf/requests/<hash> apply --check -v results/EXP-*/patches/<file>.txt
```

### 2c. Skala naik: 5 issue, 3 strategi

```powershell
.venv\Scripts\python.exe src/main.py --provider openrouter --issues 5
```

Cek di akhir:

```powershell
Get-Content results/EXP-*\generation_result.csv | Select-String -Pattern 'patch_status' -Context 0,1
```

**Target:** `VALID` dominan, `NO_DIFF` kecil, `NORMALIZE` mendekati nol.
Kalau `NORMALIZE` masih tinggi, mekanisme edit-then-diff belum aktif — cek
`TOOLCALL_ENABLED` (langkah 0).

### 2d. Verifikasi applyability

Kolom `apply_status` (baru) harus muncul. Ringkasannya:

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); import pandas as pd, glob; f=sorted(glob.glob('results/EXP-*/generation_result.csv'))[-1]; df=pd.read_csv(f); print(df['apply_status'].value_counts() if 'apply_status' in df else 'kolom apply_status belum ada')"
```

Yang diharapkan: `APPLYABLE` tinggi. `NOT_APPLYABLE` harus kecil — kalau besar,
agent masih menghasilkan patch yang tidak apply (lapor).

---

## 3. Fase final run (DeepSeek)

Setelah testing **berjalan sempurna**, ganti model. Edit `.env`:

```ini
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
```

Alternatif yang lebih defensible untuk RQ3 (biaya): pakai **DeepSeek official**
(`--provider deepseek`), karena `PricingTable` memakai harga resmi DeepSeek —
sehingga angka biaya bisa dipertanggungjawabkan, bukan estimasi.

Jalankan lengkap, dengan `--resume` supaya aman kalau rate limit:

```powershell
.venv\Scripts\python.exe src/main.py --provider openrouter --resume --rate-limit 3
```

Kalau kena rate limit, run berhenti rapi (circuit breaker) dan data yang sudah
dikumpulkan tetap valid. Jalankan ulang perintah yang sama setelah limit reset.

---

## 4. Setelah run

```powershell
# 1. Laporan generasi (pre-eval)
Get-Content results/EXP-*\generation_report.md

# 2. Evaluasi resolved rate (OTORITATIF) — butuh Modal
.venv\Scripts\python.exe tools/eval_modal.py --experiment results/EXP-<id>
```

**Penting:** `generation_report.md` = "patch berhasil **dihasilkan**", **bukan**
resolved rate. Angka resolved yang sah hanya dari `eval/summary.md` (Modal).

---

## 5. Kalau ada masalah

| Gejala | Penyebab | Tindakan |
|---|---|---|
| Peringatan `CONFIG DRIFT` | env var shell menimpa `.env` | Bersihkan (langkah 0) |
| `NO_DIFF` banyak | tool calling tidak aktif | Cek `TOOLCALL_ENABLED=true` |
| `NORMALIZE` tinggi | patch di-*type* sebagai teks, bukan edit file | Cek prompt `_tools.md` terpakai |
| `400 Reasoning is mandatory` | endpoint menolak reasoning-off | Pastikan `OPENROUTER_DISABLE_REASONING` tidak `true` |
| `unknown tool: shell` | model berhalusinasi nama tool | Normal — error dikembalikan, agent harus lanjut |
| Tool loop habis tanpa patch | agent terlalu banyak eksplorasi | Turunkan `MAX_TOOL_TURNS` atau perkuat prompt |
