# Panduan Setup & Menjalankan SWE-bench Evaluation (Docker)

> **File privat** — tidak di-push ke repo public (sudah masuk `.gitignore`).
> Panduan lengkap untuk 2 device: laptop Agi (8GB) dan laptop Abang (16GB).

---

## Daftar Isi

1. [Gambaran Alur Kerja](#1-gambaran-alur-kerja)
2. [Kebutuhan Storage](#2-kebutuhan-storage)
3. [Pindah WSL2 + Docker Storage ke D:](#3-pindah-wsl2--docker-storage-ke-d)
4. [Panduan Device Agi (8GB) — Coba Dulu](#4-panduan-device-agi-8gb--coba-dulu)
5. [Panduan Device Abang (16GB) — Non-Developer](#5-panduan-device-abang-16gb--non-developer)
6. [Fallback: Tanpa Docker](#6-fallback-tanpa-docker)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Gambaran Alur Kerja

```
[Laptop Agi]                          [Laptop Abang / Agi]
   │                                        │
   ▼                                        ▼
Final Run (Gemini)                   SWE-bench Eval (Docker)
   │                                        │
   ▼                                        ▼
predictions.jsonl  ──── copy ────►   run_evaluation
   │                                        │
   ▼                                        ▼
results.csv                          eval_results.json
summary.md                           (resolved: true/false)
```

- **Final Run** = generate patch pakai Gemini (ringan, tanpa Docker).
- **SWE-bench Eval** = uji patch di Docker (berat, butuh RAM besar).

Kamu mau coba eval Docker di device sendiri (8GB) dulu — panduan di bagian 4. Kalau gagal/berat, pindah ke device Abang (bagian 5) atau pakai fallback (bagian 6).

---

## 2. Kebutuhan Storage

### Storage Profile SWE-bench Eval Django

| Komponen | Ukuran Estimasi |
|:---------|:----------------|
| Docker Desktop installer | ~600 MB |
| WSL2 (Ubuntu distro) | ~1.5 GB |
| Docker base images (python, etc) | 2-3 GB |
| **SWE-bench image per repo** (Django) | **3-5 GB per build** |
| Dataset cache (HuggingFace) | ~100-500 MB |
| Build cache / intermediate layers | 5-10 GB |
| Hasil eval + logs | 1-2 GB |
| Project files + .venv | ~1 GB |
| **Total minimum** | **~15-20 GB** |
| **Recommended** | **30+ GB** untuk buffer |

### Status Drive (Agi — Laptop 8GB)

| Drive | Sisa Awal | Action | Sisa Target |
|:------|:----------|:-------|:------------|
| **C:** | ~7 GB | Tetap, minimal untuk Windows + Docker system | ~7 GB (pas-pasan) |
| **D:** | ~120 GB | Setelah hapus game | ✅ **Legaaaa banget** |

> **Strategi**: Pindahkan **semua Docker data** (image, container, volumes, WSL2 distro) ke D: supaya C: tidak penuh.

---

## 3. Pindah WSL2 + Docker Storage ke D:

> ⚠️ Lakukan sekali saja sebelum installasi. Kalau sudah install Docker di C:, ikuti langkah di bawah untuk migrasi.

### 3.1 Install Docker Desktop (tetap di C: — default)

```powershell
# Download: https://www.docker.com/products/docker-desktop/
# Install → Next semua → RESTART PC
```

Setelah restart:
- Buka Docker Desktop
- Tunggu ikon whale 🐳 di system tray hijau ("Docker Desktop is running")

### 3.2 WSL2 Setup (otomatis, tinggal verify)

```powershell
# Cek WSL2 sudah aktif
wsl --status

# Kalau belum ada distro, install Ubuntu
wsl --install -d Ubuntu
```

### 3.3 Buat Folder Docker di Drive D:

```powershell
mkdir D:\Docker
mkdir D:\Docker\data
mkdir D:\Docker\wsl
```

### 3.4 Config Docker Engine

1. Buka **Docker Desktop** → **Settings** (ikon gear ⚙️)
2. Pilih **Docker Engine** di sidebar kiri
3. Edit JSON config — **tambah baris `"data-root"`**:

```json
{
  "data-root": "D:\\Docker\\data",
  "builder": {
    "gc": {
      "enabled": true
    }
  }
}
```

> ⚠️ Pastikan JSON valid (tidak ada koma di akhir object sebelum tutup kurung). Kalau sudah ada baris lain (default Docker), tinggal tambah `"data-root"` saja.

4. Klik **Apply & Restart** → Docker restart

> ⏳ First restart setelah ubah data-root: Docker akan re-initialize semua. Tunggu sampai hijau (~1-2 menit).

### 3.5 Pindah WSL2 Distro ke D:

WSL2 distro (`ext4.vhdx`) secara default ada di C:. Pindahkan ke D: supaya C: tidak membengkak.

```powershell
# 1. List semua WSL distro
wsl --list -v

# 2. Stop semua WSL
wsl --shutdown

# 3. Export distro Ubuntu ke D:
wsl --export Ubuntu D:\Docker\wsl\ubuntu.tar

# 4. Unregister distro dari C:
wsl --unregister Ubuntu

# 5. Import distro ke D:
wsl --import Ubuntu D:\Docker\wsl\ D:\Docker\wsl\ubuntu.tar --version 2

# 6. Set Ubuntu sebagai default
wsl --set-default Ubuntu

# 7. Export distro pengguna (kalau ada nama lain)
# Ulangi step 3-5 untuk tiap distro
```

### 3.6 (Opsional) Buat .wslconfig — Limit RAM untuk 8GB

Buat file `C:\Users\<username>\.wslconfig` (pakai Notepad, save as all types):

```ini
[wsl2]
memory=4GB
swap=2GB
localhostForwarding=true
```

Ini batasi WSL2 pakai max **4GB RAM**, sisa 4GB untuk Windows + Docker. Simpan, lalu restart WSL:

```powershell
wsl --shutdown
```

### 3.7 Verifikasi

```powershell
# 1. Cek Docker Root Dir → harus D:\Docker\data
docker info | Select-String "Docker Root Dir"

# 2. Cek lokasi WSL distro → harus D:\Docker\wsl
wsl --list -v

# 3. Cek space C: vs D:
Get-PSDrive C, D | Select-Object Name, Used, Free

# 4. Test Docker jalan
docker run hello-world
```

**Hasil yang diharapkan:**
```
 Docker Root Dir: D:\Docker\data
 WSL: Ubuntu running (di D:\Docker\wsl\)
 C: free > 5 GB
 D: free > 110 GB
 "Hello from Docker!" muncul
```

### 3.8 Checklist Selesai

| Step | Status |
|:-----|:-------|
| Install Docker Desktop | ⬜ |
| WSL2 aktif | ⬜ |
| `D:\Docker\data` terbuat | ⬜ |
| Docker Engine config `data-root` ke D: | ⬜ |
| WSL distro pindah ke D: | ⬜ |
| `.wslconfig` RAM 4GB | ⬜ |
| `docker info` → Root Dir = D:\Docker\data | ⬜ |
| `docker run hello-world` | ⬜ |

Kalau semua checklist hijau ✅ → lanjut ke bagian 4.

---

## 4. Panduan Device Agi (8GB) — Coba Eval

> ⚠️ **Peringatan RAM**: 8GB adalah batas minimum. Tutup semua aplikasi lain (browser, VS Code) sebelum eval.
> ✅ **Prasyarat**: Sudah selesai [bagian 3 — Pindah Docker ke D:](#3-pindah-wsl2--docker-storage-ke-d). Docker Desktop berjalan hijau ✅

### 4.1 Alokasi Resource Docker (8GB)

Docker Desktop → **Settings → Resources → Advanced**:

| Setting | Nilai | Alasan |
|:--------|:------|:-------|
| **CPUs** | 4 | i5 11320H punya 4 core / 8 thread |
| **Memory** | 6 GB | Sisakan 2GB untuk Windows |
| **Swap** | 2 GB | Buffer kalau RAM habis |

Apply & Restart.

### 4.2 Siapkan Predictions

```powershell
# Cek file predictions.jsonl hasil final run
Get-ChildItem results/EXP-*/predictions/predictions.jsonl
```

Pastikan ada. Kalau belum final run, jalankan dulu.

### 4.3 —Eval: 1 Issue Dulu (Test)

> Jangan langsung 15 issue di 8GB. Test 1 issue dulu.

```powershell
.venv\Scripts\activate

# Lihat instance_id pertama dari predictions
Get-Content results/EXP-YYYYMMDD-001/predictions/predictions.jsonl -TotalCount 1

# Test 1 issue (ganti XXXX dengan instance_id asli)
.venv\Scripts\python -m swebench.harness.run_evaluation `
    --dataset_name princeton-nlp/SWE-bench_Lite `
    --predictions_path results/EXP-YYYYMMDD-001/predictions/predictions.jsonl `
    --instance_ids django__django-XXXXX `
    --max_workers 1 `
    --run_id test-1issue
```

> ⏳ **First run 30-60 menit** — build Docker image Django. Sabar. Image di-cache jadi run berikutnya cepat.

### 4.4 Kalau Berhasil → Lanjut Semua

```powershell
.venv\Scripts\python -m swebench.harness.run_evaluation `
    --dataset_name princeton-nlp/SWE-bench_Lite `
    --predictions_path results/EXP-YYYYMMDD-001/predictions/predictions.jsonl `
    --max_workers 1 `
    --run_id EXP-YYYYMMDD-001
```

> `--max_workers 1` — WAJIB di 8GB. Jangan naikkan.

### 4.5 Cek Hasil

```
<username>.EXP-YYYYMMDD-001.json    # Ringkasan resolved
logs/run_evaluation/EXP-YYYYMMDD-001/  # Log per issue
```

### 4.6 Kalau Gagal / OOM

Kalau PC freeze, build gagal berulang, atau error OOM → **stop**. Ini normal untuk 8GB. Pindah ke [bagian 5 — Device Abang (16GB)](#5-panduan-device-abang-16gb--non-developer).

---

## 5. Panduan Device Abang (16GB) — Non-Developer

> Untuk laptop Abang yang belum ada tools developer sama sekali. Ikuti urut dari nol.

### 5.1 Install Software (Urut)

| No | Software | Link | Cara Install |
|:--|:---------|:-----|:-------------|
| 1 | Git | https://git-scm.com/download/win | Double-click → Next semua → Finish |
| 2 | Python 3.12 | https://www.python.org/downloads/ | Double-click → **CENTANG "Add Python to PATH"** → Install Now |
| 3 | Docker Desktop | https://www.docker.com/products/docker-desktop/ | Double-click → Next → **RESTART PC** |

> ⚠️ **PALING PENTING**: Saat install Python, centang kotak **"Add Python to PATH"** di layar pertama. Kalau lupa, harus install ulang.

### 5.2 Verifikasi Install

Buka **PowerShell** (klik Start → ketik "PowerShell" → Enter), jalankan satu-satu:
```powershell
git --version
python --version
docker --version
```
Semua harus keluar nomor versi. Kalau ada yang "not recognized", software itu belum keinstall benar.

### 5.3 Aktifkan Docker

1. Buka **Docker Desktop** dari Start Menu.
2. Tunggu ikon whale 🐳 di system tray (pojok kanan bawah) berwarna hijau.
3. Kalau minta setup WSL2, klik OK dan ikuti (mungkin restart 1x).

### 3.4 Alokasi Resource Docker (16GB — Lega)

Docker Desktop → **Settings** (ikon gear) → **Resources → Advanced**:

| Setting | Nilai |
|:--------|:------|
| **CPUs** | 6 |
| **Memory** | 12 GB |
| **Swap** | 2 GB |
| **Disk image size** | 100 GB |

Klik **Apply & Restart**.

### 3.5 Download Project

Buka PowerShell:
```powershell
# Buat folder kerja
mkdir D:\AgantBench
cd D:\AgantBench

# Download project dari GitHub
git clone https://github.com/agirahman/AgentBench-SE.git
cd AgentBench-SE

# Buat lingkungan Python
python -m venv .venv
.venv\Scripts\activate

# Install semua kebutuhan (tunggu selesai)
pip install -r requirements.txt
```

### 3.6 Terima File dari Agi

Agi akan kasih 1 file lewat USB / Google Drive:
```
predictions.jsonl
```

Letakkan di:
```
D:\AgantBench\AgentBench-SE\predictions.jsonl
```
(Taruh langsung di folder AgentBench-SE, jangan di dalam subfolder.)

### 3.7 Jalankan Eval

```powershell
# Pastikan venv aktif (ada tulisan (.venv) di depan)
.venv\Scripts\activate

# Jalankan evaluasi
.venv\Scripts\python -m swebench.harness.run_evaluation `
    --dataset_name princeton-nlp/SWE-bench_Lite `
    --predictions_path predictions.jsonl `
    --max_workers 2 `
    --run_id EXP-YYYYMMDD-001
```

> ⏳ **First run 30-60 menit** untuk build image Docker. Biarkan jalan, jangan tutup PowerShell. Jangan matikan laptop.

### 3.8 Cek & Kirim Hasil ke Agi

Setelah selesai, cari file JSON hasil di folder `AgentBench-SE`:
```powershell
Get-ChildItem *.json
```

File namanya seperti `<nama>.EXP-YYYYMMDD-001.json`. Copy file ini + folder `logs/` balik ke Agi lewat USB / Google Drive.

---

## 6. Fallback: Tanpa Docker

Kalau Docker gagal di kedua device, penelitian **tetap bisa lanjut** pakai metrik internal (tanpa build success dari SWE-bench).

### Yang Tetap Tersedia Tanpa Docker

| Metrik | Sumber | RQ |
|:-------|:-------|:---|
| Execution time | `results.csv` | RQ2 |
| Inference count | `results.csv` | RQ2 |
| Avg time per inference | `summary.md` | RQ2 |
| Token usage | `results.csv` | RQ3 |
| Cost (USD/IDR) | `results.csv` | RQ3 |
| Cost per success | `view_results.py cost_per_success` | RQ3 |
| Success proxy (`error` kosong) | `results.csv` kolom `success` | RQ1 (proxy) |

### Cara Pakai

```powershell
.venv\Scripts\python src/view_results.py --file results/EXP-YYYYMMDD-001/results.csv summary
.venv\Scripts\python src/view_results.py --file results/EXP-YYYYMMDD-001/results.csv cost_per_success
```

> **Catatan untuk skripsi**: `success` di CSV = patch berhasil di-generate (bukan patch benar-benar lolos test). Untuk RQ1 sebenarnya (Build Success Rate), butuh SWE-bench eval Docker. Kalau Docker tidak jalan, jelaskan di Bab III bahwa RQ1 memakai proxy generation success + analisis kualitatif sample patch.

---

## 7. Troubleshooting

| Masalah | Penyebab | Solusi |
|:--------|:---------|:-------|
| `docker: command not found` | Docker belum keinstall / belum jalan | Buka Docker Desktop, tunggu hijau |
| PC freeze saat eval (8GB) | RAM habis | Turunkan Memory Docker ke 5GB, `--max_workers 1`, tutup app lain |
| `Cannot connect to Docker daemon` | Docker Desktop belum running | Buka Docker Desktop |
| Build image lama banget | Normal untuk first run | Tunggu 30-60 menit, image di-cache |
| `No space left on device` | Disk penuh | Naikkan Disk image size di Settings, atau `docker system prune` |
| Build gagal di tengah | Koneksi internet putus | Ulangi command, layer di-cache lanjut |
| `python not recognized` (Abang) | Lupa centang "Add to PATH" | Install ulang Python, centang PATH |
| Eval selesai tapi semua `unresolved` | Format patch salah / model lemah | Cek `predictions.jsonl`, normal kalau sebagian gagal |

### Bersihkan Docker (kalau disk penuh)

```powershell
# Hapus image & container tidak terpakai
docker system prune -a

# Cek pemakaian disk Docker
docker system df
```

---

## Ringkasan Command Cepat

**Device Agi (test 1 issue):**
```powershell
.venv\Scripts\python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Lite --predictions_path results/EXP-YYYYMMDD-001/predictions/predictions.jsonl --instance_ids django__django-XXXXX --max_workers 1 --run_id test-1issue
```

**Device Abang (semua issue):**
```powershell
.venv\Scripts\python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Lite --predictions_path predictions.jsonl --max_workers 2 --run_id EXP-YYYYMMDD-001
```
