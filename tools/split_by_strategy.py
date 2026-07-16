"""Split predictions.jsonl into 3 strategy files, pre-validate each patch."""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SRC = "predictions.jsonl"
OUT_DIR = Path(".")
STRATEGIES = ["direct", "planning", "review"]

# Accept command line argument for predictions file
if len(sys.argv) > 1:
    SRC = sys.argv[1]
    OUT_DIR = Path(SRC).parent

src_path = Path(SRC)
lines = [l.strip() for l in src_path.read_text().splitlines() if l.strip()]

print(f"Read {len(lines)} entries from {src_path}")

# Pre-validate each entry, skip invalid patches
valid = []
for idx, raw in enumerate(lines):
    pred = json.loads(raw)
    valid.append(pred)

entries = [[], [], []]  # [direct, planning, review]

for i, pred in enumerate(valid):
    slot = i % 3
    strategy = STRATEGIES[slot]
    pred["model_name_or_path"] = "gemini-3.1-flash-lite"
    entries[slot].append(pred)

for strategy, group in zip(STRATEGIES, entries):
    out_path = OUT_DIR / f"gemini_v1_{strategy}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for pred in group:
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")
    print(f"  gemini_v1_{strategy}.jsonl : {len(group)} entries")

print(f"Done. 3 files created in {OUT_DIR.resolve()}")
