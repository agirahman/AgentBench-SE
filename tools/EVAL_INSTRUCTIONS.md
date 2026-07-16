# Panduan Eval SWE-bench di Ubuntu/WSL2 (2GB RAM)

> **Warning**: SWE-bench Docker eval butuh 4GB+ RAM. WSL2 default 2GB → OOM risk.
> Sebelum mulai, edit `C:\Users\<user>\.wslconfig` (Windows side):
> ```ini
> [wsl2]
> memory=4GB
> swap=4GB
> ```
> Lalu `wsl --shutdown` di PowerShell, start ulang WSL2.

## Step 0: Copy project ke Ubuntu

Dari Windows:
```powershell
# Copy folder predictions
Copy-Item -Recurse results/EXP-20260716-006/predictions -Destination \\wsl$\Ubuntu\home\<user>\AgentBench-SE\results\
```

Atau clone repo di Ubuntu:
```bash
git clone <repo-url> ~/AgentBench-SE
cd ~/AgentBench-SE
git checkout 16/feat/eval-toolchain
```

## Step 1: Jalankan setup + eval otomatis

```bash
cd ~/AgentBench-SE
source .venv/bin/activate  # jika venv sudah ada
chmod +x tools/setup_and_eval.sh
./tools/setup_and_eval.sh results/EXP-20260716-006/predictions
```

Script akan:
1. Setup Python venv (avoid PEP 668)
2. Install Docker (jika belum)
3. Install swebench + dependencies
4. Build Docker image Django (30-60 menit pertama)
5. Test 1 issue (`django__django-10914`)
6. Jika test OK → eval 3 strategi (direct, planning, review)
7. Print ringkasan hasil

## Step 2: Manual eval (kalau script gagal)

```bash
# Setup venv dulu
python3 -m venv .venv
source .venv/bin/activate
pip install swebench datasets

# Test 1 issue
python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path results/EXP-20260716-006/predictions/gemini_v1_direct.jsonl \
    --instance_ids django__django-10914 \
    --max_workers 1 \
    --run_id test-1issue

# Atau 3 runs sekaligus
./tools/run_eval.sh results/EXP-20260716-006/predictions

# Check hasil
python3 tools/check_eval.py
```

## Step 3: Cek hasil

Output:
- `*.json` — ringkasan resolved/unresolved per run
- `logs/run_evaluation/<run_id>/` — log per issue

```bash
cat *.json | python -m json.tool
```

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Cannot connect to Docker daemon` | `sudo systemctl start docker` |
| OOM / freeze | Naikkan RAM di `.wslconfig`, atau eval 1 issue |
| Build image gagal | Cek disk space (`df -h`), butuh 10GB+ |
| `No module named swebench` | `pip install swebench` |
