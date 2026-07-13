# Setup Guide — Komputer Abang

## Prasyarat (install sekali)
1. **Git** — https://git-scm.com/downloads/win
2. **Python 3.10+** — https://www.python.org/downloads/ (centang "Add Python to PATH")
3. **Docker Desktop** — https://www.docker.com/products/docker-desktop/
4. **VS Code** (opsional) — https://code.visualstudio.com/

## Setup WSL2 & Docker
Jalankan PowerShell sebagai Administrator:
```powershell
wsl --install -d Ubuntu
```
Restart PC, buka Docker Desktop → Settings → Resources → WSL Integration → enable Ubuntu → Apply & Restart.

Di Docker Desktop Settings → Resources → Advanced, set:
- **CPUs**: 4
- **Memory**: 8 GB (minimum)
- **Disk image size**: 120 GB

## Clone & Dependencies
Buka terminal (cmd/powershell):
```bash
git clone <URL-repo> AgantBech-SE
cd AgantBech-SE
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Copy file dari laptop
Copy 2 file ini dari laptop ke PC via USB/cloud:
| File | Tujuan |
|---|---|
| `.env` (GEMINI_API_KEY, GROQ_API_KEY) | `AgantBech-SE\.env` |
| `results/predictions.jsonl` | `AgantBech-SE\results\predictions.jsonl` |

## Generate patches (jika belum punya predictions.jsonl)
```bash
python src/main.py --n-issues 15
```

## Jalankan Evaluasi SWE-bench
```bash
python -m swebench.harness.run_evaluation ^
    --predictions_path results/predictions.jsonl ^
    --max_workers 1 ^
    --run_id experiment-1
```

## Setelah selesai
Hasil evaluasi ada di folder `results/` — file CSV dengan kolom `resolved` (True/False) untuk tiap task.
