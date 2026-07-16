"""Parse SWE-bench eval report and print summary per strategy."""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

REPORT_DIR = Path("logs/run_evaluation")

strategies = ["direct", "planning", "review"]
summary = {}

for strat in strategies:
    # Find report file matching pattern: *gemini-v1-{strat}.json
    pattern = f"*gemini-v1-{strat}.json"
    reports = list(REPORT_DIR.glob(f"**/{pattern}"))
    
    if not reports:
        print(f"{strat}: NO REPORT FOUND")
        continue
    
    report_file = reports[0]
    try:
        report = json.loads(report_file.read_text())
        
        resolved = report.get("instances_resolved", 0)
        unresolved = report.get("instances_unresolved", 0)
        error = report.get("instances_with_errors", 0)
        total = report.get("total_instances", 0)
        
        summary[strat] = {
            "resolved": resolved,
            "unresolved": unresolved,
            "error": error,
            "total": total,
            "success_rate": f"{resolved/total*100:.1f}%" if total > 0 else "N/A"
        }
        
        print(f"\n{strat.upper()}:")
        print(f"  Total: {total}")
        print(f"  Resolved: {resolved}")
        print(f"  Unresolved: {unresolved}")
        print(f"  Errors: {error}")
        print(f"  Success rate: {summary[strat]['success_rate']}")
        
    except Exception as e:
        print(f"{strat}: ERROR reading {report_file} - {e}")

print("\n" + "="*50)
print("SUMMARY")
print("="*50)
for strat in strategies:
    if strat in summary:
        s = summary[strat]
        print(f"{strat}: {s['resolved']}/{s['total']} ({s['success_rate']})")
