import json
import csv
from pathlib import Path

# Check review_results.json
results_file = Path('results/EXP-20260929-022/predictions/review_results.json')
data = json.loads(results_file.read_text())

print('=== review_results.json ===')
print(f'total: {data["total"]}')
print(f'graded: {data["graded"]}')
print(f'empty_patches: {data["empty_patches"]}')
print(f'resolved: {data["resolved"]}')
print()

for r in data['results']:
    print(f'{r["instance_id"]}: resolved={r["resolved"]}, patch_applied={r["patch_applied"]}, failure_reason={r["failure_reason"]}')

print()
print('=== generation_result.csv (review rows) ===')
csv_file = Path('results/EXP-20260929-022/generation_result.csv')
with csv_file.open(newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['strategy'] == 'review':
            err = row['error'][:60] if row['error'] else '(none)'
            print(f'{row["instance_id"]}: total_turns={row["total_turns"]}, generated={row["generated"]}, error={err}')
