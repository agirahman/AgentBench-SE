"""THE HEADLINE FINDING: the reviewer rejects on evidence that cannot affect the grade.

What the pipeline does
----------------------
review_strategy: executor makes a patch -> reviewer inspects it -> if NEEDS_REVISION,
a revision act runs -> the revision is re-reviewed -> the FIRST APPROVED candidate
is shipped (review_strategy.py:223-226).

What the harness does
---------------------
swebench/harness/test_spec/utils.py:66-69,87,93
    test_files = get_modified_files(test_patch)
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    ...
    reset_tests_command          # before the tests
    apply_test_patch_command     # the harness's OWN tests
    ...
    reset_tests_command          # after

Only files from the harness's OWN test_patch are reset. But grading runs ONLY the
harness's FAIL_TO_PASS / PASS_TO_PASS lists. So any file NOT in those lists -- which
includes every test file the AGENT writes -- is never graded at all.

The consequence
---------------
A rejection whose basis is "the test you added is broken" cannot change `resolved`.
The revision it triggers is wasted work, and worse, the rejection can cause the run
to SHIP A DIFFERENT PATCH than the one that was actually fine.

Measured
--------
django-11001/review at level 40:
  * reviewer rejected: "the added test imports RawSQL from django.db.models, but that
    name is not exported ... so the test module fails to import"
  * that file is tests/ordering/tests.py
  * graded tests: expressions.tests.BasicExpressionsTests ONLY (FAIL_TO_PASS: 2)
  * the shipped patch (with the "broken" test) still scored resolved=True

  -> the rejection was about a file the harness never grades, and the patch was fine.

This script counts how often a rejection cites a test file.
"""
import json
import pathlib
import re

VERDICT_RE = re.compile(r'"verdict"\s*:\s*"([A-Z_]+)"')
TEST_MENTION = re.compile(
    r"(tests?/[\w/\.]+\.py|test module|test suite|the added test|added test|"
    r"regression test|test file|pytest|test that)",
    re.I,
)

EXPS = ["EXP-20260927-007", "EXP-20260927-010", "EXP-20260928-003",
        "EXP-20260929-003", "EXP-20260929-022"]


def load_jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def graded_tests(exp, inst):
    """Which tests did the harness actually run for this instance?"""
    run = f"modal-review-{exp}"
    p = pathlib.Path("logs/run_evaluation") / run / "oc__space-bunny-free" / inst / "report.json"
    if not p.exists():
        return None
    try:
        r = json.loads(p.read_text(encoding="utf-8"))[inst]
    except (json.JSONDecodeError, KeyError):
        return None
    ts = r.get("tests_status") or {}
    names = []
    for k in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        g = ts.get(k) or {}
        names += (g.get("success") or []) + (g.get("failure") or [])
    files = set()
    for n in names:
        m = re.search(r"\(([\w\.]+)\)", n)
        if m:
            files.add(m.group(1).replace(".", "/") + ".py")
    return files, len(names)


print("=" * 100)
print("REJECTIONS WHOSE BASIS IS A TEST FILE (cannot affect the grade)")
print("=" * 100)

total_rej = 0
test_rej = 0

for exp in EXPS:
    art = pathlib.Path("results") / exp / "artifacts"
    if not art.exists():
        continue
    for inst_dir in sorted(art.glob("*")):
        sd = inst_dir / "review"
        if not sd.exists():
            continue
        msgs = load_jsonl(sd / "messages.jsonl")
        inst = inst_dir.name
        for i, d in enumerate(msgs):
            if d.get("sender") != "reviewer" or d.get("kind") != "result":
                continue
            text = d.get("content") or ""
            found = VERDICT_RE.findall(text)
            if not found or found[-1] != "NEEDS_REVISION":
                continue
            total_rej += 1
            mentions = TEST_MENTION.findall(text)
            if not mentions:
                continue
            test_rej += 1
            g = graded_tests(exp, inst)
            print(f"\n{exp}  {inst.replace('django__django-','')}/review  MSG {i}")
            print(f"  test-related phrases: {sorted(set(m.lower() for m in mentions))[:6]}")
            if g:
                files, n = g
                print(f"  harness graded {n} tests from: {sorted(files)}")
            else:
                print("  (no harness report for this run)")
            # the first 300 chars of the review
            flat = " ".join(text.split())[:300]
            print(f"  review text: {flat}")

print()
print("=" * 100)
print("SUMMARY")
print("=" * 100)
print(f"  NEEDS_REVISION verdicts recorded : {total_rej}")
print(f"  ...whose basis mentions a test  : {test_rej}")
if total_rej:
    print(f"  share: {test_rej / total_rej:.0%}")
