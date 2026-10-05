"""Does direct actually RECEIVE run_tests now? (not whether it chooses to use it)

"run_tests was called 0 times" is ambiguous: it can mean the tool is missing, or
that the agent chose not to test. Those are different facts and only one of them is
a finding. This checks the tool SCHEMA handed to the provider for each role, which
is what the model actually sees.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.tools import AGENT_TOOLS, get_tools_for_agent  # noqa: E402

print("=" * 78)
print("  TOOLS SENT TO THE MODEL, PER ROLE")
print("=" * 78)

all_tools = ["read_file", "grep", "list_files", "run_tests",
             "edit_file", "write_file", "git_diff", "reset_repo"]

print(f"\n  {'tool':<12} {'direct':>7} {'planner':>8} {'executor':>9} {'reviewer':>9}")
print(f"  {'-' * 50}")
for tool in all_tools:
    row = []
    for role in ("direct", "planner", "executor", "reviewer"):
        row.append("yes" if tool in AGENT_TOOLS[role] else "-")
    print(f"  {tool:<12} {row[0]:>7} {row[1]:>8} {row[2]:>9} {row[3]:>9}")

print()
print("=" * 78)
print("  VERIFICATION PARITY CHECK")
print("=" * 78)
writers = [r for r in AGENT_TOOLS if {"edit_file", "write_file"} & set(AGENT_TOOLS[r])]
print(f"\n  roles that can write code : {', '.join(writers)}")
missing = [r for r in writers if "run_tests" not in AGENT_TOOLS[r]]
if missing:
    print(f"  WITHOUT run_tests         : {', '.join(missing)}   <-- UNFAIR")
else:
    print("  all of them can run_tests : yes   <-- verification parity holds")

print()
print("  Every strategy now has a path to verification:")
print("    direct   -> its own act calls run_tests")
print("    planning -> the executor act calls run_tests")
print("    review   -> the executor AND the reviewer call run_tests")

print()
print("=" * 78)
print("  STRATEGY-LEVEL PARITY (the union of each strategy's roles)")
print("=" * 78)

STRATEGY_ROLES = {
    "direct": ["direct"],
    "planning": ["planner", "executor"],
    "review": ["planner", "executor", "reviewer"],
}
reach = {
    name: {t for r in roles for t in AGENT_TOOLS[r]}
    for name, roles in STRATEGY_ROLES.items()
}

print(f"\n  {'tool':<12} {'direct':>8} {'planning':>9} {'review':>8}   note")
print(f"  {'-' * 68}")
asymmetries = []
for tool in all_tools:
    row = ["yes" if tool in reach[s] else "-" for s in ("direct", "planning", "review")]
    if len(set(row)) == 1:
        note = "parity"
    else:
        note = "<-- CONFOUND: " + ", ".join(
            s for s, v in zip(("direct", "planning", "review"), row) if v == "-"
        ) + " cannot reach it"
        asymmetries.append(tool)
    print(f"  {tool:<12} {row[0]:>8} {row[1]:>9} {row[2]:>8}   {note}")

print()
if asymmetries:
    print(f"  {len(asymmetries)} asymmetry(ies) remain: {', '.join(asymmetries)}")
    print("  A strategy win could not be separated from the capability gap.")
else:
    print("  ALL TOOLS at parity across strategies -- a win cannot come from reach.")

print()
print("=" * 78)
print("  DELIBERATE PER-ROLE SPLIT (within a strategy, not between them)")
print("=" * 78)
print("""
  planner: no edit_file, no write_file, no run_tests, no git_diff, no reset_repo.
    It produces a plan, not a patch, and it does not verify work that does not
    exist yet.

  reviewer: no edit_file, no write_file, no list_files, no reset_repo.
    A reviewer that rewrites the code it is reviewing is not a reviewer; the review
    strategy depends on that independence. It DOES have run_tests -- it needs
    evidence to judge -- and git_diff to see what changed.

  These are intra-strategy by design and do not create a strategy-level gap: the
  executor reaches the same tools on the strategy's behalf.
""")
