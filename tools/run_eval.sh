#!/bin/bash
# Run SWE-bench evaluation for 3 strategy files on Ubuntu
# Error-tolerant: one run failure doesn't stop others
# Usage: ./run_eval.sh [predictions_dir] [dataset_name]

set +e

BASE_DIR="${1:-results/EXP-20260716-006/predictions}"
DATASET="${2:-princeton-nlp/SWE-bench_Lite}"

echo "BASE_DIR: $BASE_DIR"
echo "DATASET: $DATASET"

for v in v1 v2 v3; do
  for strat in direct planning review; do
    FILE="$BASE_DIR/gemini_${v}_${strat}.jsonl"
    RUN_ID="gemini-${v}-${strat}"

    if [ ! -f "$FILE" ]; then
      echo "SKIP: $FILE not found"
      continue
    fi

    echo "=== Running eval: $RUN_ID ($FILE) ==="
    python -m swebench.harness.run_evaluation \
      --dataset_name "$DATASET" \
      --predictions_path "$FILE" \
      --max_workers 1 \
      --run_id "$RUN_ID" \
      2>&1 | tee "logs/eval_${v}_${strat}.log"

    echo "=== Done $RUN_ID (exit: $?) ==="
  done
done

echo "All eval runs completed."
