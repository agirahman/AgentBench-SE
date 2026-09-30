"""Does a rejection actually REST on a test file, or merely MENTION one?

The first version of this analysis counted "review text contains a test-related
phrase" and reported 78%. That is too weak a signal to spend money on: the word
"test" is ordinary English in a code review, and one of the flagged texts says
outright that the test defect is "not the fix's behaviour" -- i.e. the reviewer
NOTICED a test problem and rejected on other grounds.

What matters for the experiment is the causal question: if the test file had
never existed, would this verdict have been NEEDS_REVISION anyway? So this tool
prints each rejection in full, with its verdict, and flags the decisive cases --
where the ONLY stated correctness problem is the test file.

Reading the verdicts is the point. A count cannot answer "why did it reject".
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

# (experiment, issue, strategy, msg index) from the earlier scan.
TARGETS = [
    ("EXP-20260927-007", "django__django-10924", "review"),
    ("EXP-20260928-003", "django__django-10914", "review"),
    ("EXP-20260928-003", "django__django-11001", "review"),
    ("EXP-20260928-003", "django__django-11019", "review"),
    ("EXP-20260929-003", "django__django-11001", "review"),
    ("EXP-20260929-003", "django__django-11019", "review"),
]

VERDICT_RE = re.compile(r'"verdict"\s*:\s*"([A-Z_]+)"')

# Phrases that indicate the reviewer is treating the TEST FILE as the problem.
TEST_AS_PROBLEM = [
    r"test file",
    r"added test",
    r"new test",
    r"the test .{0,40}(fails?|raises|breaks|errors?)",
    r"(fails?|raises|breaks|errors?).{0,40}test",
    r"tests?/[\w/]+\.py",
    r"regression test",
    r"test module",
    r"test suite",
]
TEST_AS_PROBLEM_RE = re.compile("|".join(TEST_AS_PROBLEM), re.I)

# Phrases that explicitly DISCLAIM the test file as a reason to reject.
DISCLAIMERS = [
    r"not the fix'?s? behaviour",
    r"test[- ]file defect",
    r"test[- ]only",
    r"does not affect the (fix|behaviour|behavior)",
    r"irrespective of the test",
    r"separate from the fix",
]
DISCLAIMER_RE = re.compile("|".join(DISCLAIMERS), re.I)


def load_messages(exp: str, issue: str, strategy: str) -> list[dict]:
    """Read the recorded blackboard messages for one run.

    The artefact is ``messages.jsonl`` (one JSON object per line), not a JSON
    array: the runner appends as it goes so an interrupted run still has the
    messages up to the point it died.
    """
    art = RESULTS / exp / "artifacts" / issue / strategy
    p = art / "messages.jsonl"
    if p.exists():
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    # Fall back to the per-run result JSON.
    for p in (RESULTS / exp).glob("*.json"):
        if issue.replace("__", "-") in p.name or issue in p.name:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "messages" in data:
                return data["messages"]
    return []


def lenient_json(content: str) -> dict:
    """Parse the reviewer's JSON, tolerating the invalid escapes it writes.

    Measured: django-11001's reviewer quotes a regex ``[^\\S\\n]`` inside a JSON
    string. ``\\S`` is not a legal JSON escape, so ``json.loads`` refuses the whole
    object even though every field is present and readable. Repairing the escapes
    is safe here because this tool only reads fields to print them -- it never
    acts on the values.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return {}
    text = m.group(0)
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return {}


print("=" * 100)
print("  EACH REJECTION, IN FULL -- does the test file CARRY the verdict or just appear in it?")
print("=" * 100)

summary = []
for exp, issue, strategy in TARGETS:
    msgs = load_messages(exp, issue, strategy)
    if not msgs:
        print(f"\n{exp}  {issue}/{strategy}: no messages artifact found")
        continue

    # Reviewer turns are the ones carrying a verdict.
    for i, m in enumerate(msgs):
        content = m.get("content") or ""
        if m.get("sender") != "reviewer" and "verdict" not in content.lower():
            continue
        verdicts = VERDICT_RE.findall(content)
        if not verdicts:
            continue
        verdict = verdicts[-1]
        if verdict != "NEEDS_REVISION":
            continue

        mentions = bool(TEST_AS_PROBLEM_RE.search(content))
        disclaims = bool(DISCLAIMER_RE.search(content))
        obj = lenient_json(content)

        issues_found = obj.get("issues_found") or []
        if isinstance(issues_found, str):
            issues_found = [issues_found]

        print(f"\n{'-' * 100}")
        print(f"{exp}  {issue}/{strategy}  msg[{i}]  verdict={verdict}")
        print(f"  mentions a test file : {mentions}")
        print(f"  disclaims it         : {disclaims}")
        print(f"  issues_found         : {json.dumps(issues_found, indent=4)[:1200]}")
        s = obj.get("review_summary", "")
        print(f"  review_summary       : {str(s)[:700]}")
        summary.append({
            "exp": exp, "issue": issue, "msg": i,
            "mentions": mentions, "disclaims": disclaims,
            "issues": issues_found,
        })

print()
print("=" * 100)
print("  VERDICT ON THE PREMISE")
print("=" * 100)
n = len(summary)
if n:
    only_test = [s for s in summary
                 if s["mentions"] and not s["disclaims"]
                 and all(TEST_AS_PROBLEM_RE.search(str(x)) for x in s["issues"])]
    mentions = [s for s in summary if s["mentions"]]
    disclaim = [s for s in summary if s["disclaims"]]
    print(f"  NEEDS_REVISION reviewed        : {n}")
    print(f"  mention a test file            : {len(mentions)}")
    print(f"  explicitly disclaim the test   : {len(disclaim)}")
    print(f"  issues_found are ALL test-only : {len(only_test)}")
    print()
    print("  The claim to test in the ablation is the LAST number, not the second:")
    print("  only a rejection whose every stated problem is a test file could have")
    print("  been avoided by telling the reviewer the tests are stripped.")
    for s in only_test:
        print(f"    -> {s['exp']} {s['issue']} msg[{s['msg']}]")
