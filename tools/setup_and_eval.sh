#!/bin/bash
# Setup + Eval SWE-bench di Ubuntu/WSL2 (2GB RAM)
# Usage: ./setup_and_eval.sh [predictions_dir]
set -e

PRED_DIR="${1:-results/EXP-20260716-006/predictions}"
DATASET="princeton-nlp/SWE-bench_Lite"

echo "=== Step 0: Setup venv ==="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "=== Step 1: Install Docker (if not present) ==="
if ! command -v docker &> /dev/null; then
    sudo apt update
    sudo apt install -y docker.io
    sudo systemctl enable --now docker
    sudo usermod -aG docker $USER
    echo "Docker installed. LOGOUT & LOGIN again, then re-run this script."
    exit 0
else
    echo "Docker already installed"
fi

echo "=== Step 2: Install swebench + deps ==="
pip install swebench datasets 2>&1 | tail -5

echo "=== Step 3: Build SWE-bench image (first run 30-60 min) ==="
python3 -c "from swebench.harness.docker_build import build_image; build_image('django__django-10914')" || {
    echo "Build failed. Check Docker daemon + RAM."
    exit 1
}

echo "=== Step 4: Test 1 issue (django__django-10914) ==="
python3 -m swebench.harness.run_evaluation \
    --dataset_name "$DATASET" \
    --predictions_path "$PRED_DIR/gemini_v1_direct.jsonl" \
    --instance_ids django__django-10914 \
    --max_workers 1 \
    --run_id test-1issue

echo "=== Step 5: If test OK, run all 3 strategies ==="
for strat in direct planning review; do
    python3 -m swebench.harness.run_evaluation \
        --dataset_name "$DATASET" \
        --predictions_path "$PRED_DIR/gemini_v1_${strat}.jsonl" \
        --max_workers 1 \
        --run_id "gemini-v1-${strat}"
done

echo "=== Step 6: Check results ==="
python3 tools/check_eval.py

echo "DONE"
