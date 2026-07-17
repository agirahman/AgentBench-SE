import json
from pathlib import Path

pred_dir = Path("results/EXP-20260717-009/predictions")
for jsonl_file in pred_dir.glob("*.jsonl"):
    if jsonl_file.name.endswith("_results.json"):
        continue
    lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if "model_patch" in entry and entry["model_patch"]:
            if not entry["model_patch"].endswith("\n"):
                entry["model_patch"] += "\n"
        updated.append(json.dumps(entry, ensure_ascii=False))
    jsonl_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"Fixed: {jsonl_file.name}")
print("All predictions updated with trailing newlines")
