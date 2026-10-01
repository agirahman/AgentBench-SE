# AUDIT — Fragile tests: tests that pass for the wrong reason

**Task:** `docs/TASK_TEST_ROBUSTNESS.md`
**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Date:** 2026-10-01
**Mode:** READ-ONLY on source. I wrote test/probe files with the `.tr_*` prefix (removed
after the audit). The only file I added under `tests/` was
`tests/test_zz_probe_config_leak.py`, used to *measure* a leak.

## The tree moved under me — read this first

The task says "I already fixed 3 tests; find the rest." While I worked, a partner fixed
**more of what I was finding**, live:

| Commit | What it fixed |
|---|---|
| `da00708` | three tests + one checker depending on the shell |
| `39314fa` | tests leaking `Config` into every later test |
| `eed55d6` | one command that runs every readiness check |
| `212b2fb` | the readiness gate could say READY with a red suite |

I started at `3d4b055` and finished at `212b2fb`. **Two of my findings were fixed under me
mid-audit**; I report them as *found and confirmed*, with the state at the time I measured
it, and mark what is still live at HEAD `212b2fb`. Where a fix landed, I say so rather than
presenting a repaired defect as open.

Baseline for every measurement: `.env` = `TOTAL_TOOL_TURNS=200`, `REVISION_TOOL_TURNS=48`,
`MAX_REVISION_TURNS=4`.

---

## Summary

| # | Category | Fragile tests found | Still live at HEAD |
|---|---|---|---|
| 1 | Reads ambient `Config` instead of pinning | **2** | **1** (`test_config_defaults`) |
| 2 | Test order changes the result | **0** | 0 |
| 3 | Passes by accident / checker repeats a constant | **1 checker** | **1** (`analyze_revision_budget.py`) |
| 4 | Claim vs real artifact | **4 wrong citations** | **4** |

**Tests that test nothing:** 0. I ran 12 source mutations and **all 12 were detected** — the
suite genuinely exercises the behaviour it claims. The defects below are narrower: a test
whose *outcome depends on the caller's environment*, a *checker that recommends the bug it
should catch*, and *citations that name the wrong run*.

Suite totals measured: **426 passed** clean, **426 passed** with `40/8/1` — the partner's
fixes hold. The fragility is now confined to one config value nobody sets to 0.

---

## Category 1 — tests that read ambient `Config` instead of pinning

### 1.1 FIXED (found live) — `tests/test_budget_modes.py` leaked `REVISION_TOOL_TURNS` into every later test

**State when I measured it:** before `39314fa`.

`test_budget_modes.py:236-239` assigned straight to the class:

```python
Config.TOTAL_TOOL_TURNS = 40
Config.REVISION_TOOL_TURNS = 8      # <-- written, never restored
Config.BUDGET_MODE = "per_task"
Config.BUDGET_FLOOR_PER_ACT = 10
```

The file's autouse `_restore_config` fixture (line 29-44) saved only **four** values —
`TOTAL_TOOL_TURNS`, `BUDGET_MODE`, `BUDGET_FLOOR_PER_ACT`, `COST_LIMIT_USD` — and
`REVISION_TOOL_TURNS` was not one of them. So the assignment survived the fixture.

**Proof.** I wrote a probe that reports the class-level `Config` values at its own point in
the session, then ran it immediately after each test file:

```
--- probe alone (baseline)
      REVISION_TOOL_TURNS    Config=48       .env=48
--- after tests/test_budget_modes.py
      REVISION_TOOL_TURNS    Config=8        .env=48        <-- LEAKED
```

The probe's canary assertion failed with the same message:

```
FAILED tests/test_zz_probe_config_leak.py::test_probe_would_fail_if_config_leaked
  AssertionError: REVISION_TOOL_TURNS: Config=8 but .env='48' — a previous test mutated
  Config without restoring it
```

**Impact if unfixed.** Every test that ran *after* `test_budget_modes.py` read
`REVISION_TOOL_TURNS=8` instead of the configured 48. `tests/test_strategies.py` sorts
after it and drives the real review strategy, so its budget assertions were being evaluated
against a value **no configuration chose**. That is the same class as the "421 passed is
fake" defect the task was written about: the suite was green against a config that existed
only because a test wrote it.

**Fixed in `39314fa`.** Re-ran the same probe after the fix:

```
--- after tests/test_budget_modes.py
      24 passed in 0.21s
      REVISION_TOOL_TURNS    Config=48       .env=48          (no leak)
```

### 1.2 STILL LIVE — `tests/test_response_utils.py:87` depends on ambient `MAX_REVISION_TURNS`

```python
def test_config_defaults():
    assert Config.MAX_TOKENS > 0
    assert Config.API_TIMEOUT > 0
    assert isinstance(Config.DEEPSEEK_THINKING, bool)
    assert Config.DEEPSEEK_REASONING_EFFORT in ("low", "medium", "high")
    assert Config.MAX_REVISION_TURNS >= 1        # <-- line 87
```

**file:line:** `tests/test_response_utils.py:87`
**test name:** `test_config_defaults`

The test asserts a property of the *ambient* configuration with no `monkeypatch.setattr`.
It passes for every value `>= 1` and fails the moment the shell exports `0`:

```
$env:MAX_REVISION_TURNS="0"
.venv\Scripts\python.exe -m pytest tests/test_response_utils.py::test_config_defaults -q
F                                                                        [100%]
tests\test_response_utils.py:87: in test_config_defaults
    assert Config.MAX_REVISION_TURNS >= 1
E   assert 0 >= 1
E    +  where 0 = Config.MAX_REVISION_TURNS
1 failed in 0.25s
```

**Measured across values** (all with the rest of the env clean):

| ambient `MAX_REVISION_TURNS` | outcome |
|---|---|
| unset (→ `.env` 4) | PASS |
| `1` | PASS |
| `2` | PASS |
| `99` | PASS |
| **`0`** | **FAIL** (`assert 0 >= 1`) |

**Whole-suite effect:**

```
ambient TOTAL_TOOL_TURNS=0 REVISION_TOOL_TURNS=0 MAX_REVISION_TURNS=0
  -> 1 failed, 425 passed
     FAILED tests/test_response_utils.py::test_config_defaults - assert 0 >= 1
```

**Impact.** `MAX_REVISION_TURNS=0` is a **legitimate, reachable configuration**: it means
"no revision rounds", which is exactly the legacy behaviour `config.py:154` documents
(`MAX_REVISION_TURNS = _get_int_env("MAX_REVISION_TURNS", 1)`) and which
`tools/verify_revision_rounds.py:83-88` handles deliberately:

```
if rounds < 1:
    print("  No revision rounds are allowed at all -- the review arm would")
    print("  measure review WITHOUT revision, which is not the strategy under test.")
    return 1
```

So the codebase treats 0 as valid and meaningful, while this test declares it impossible.
A researcher running an ablation with revisions disabled gets a **red suite for a correct
configuration**, and — worse in the other direction — the assertion cannot fail for any
value in the normal range, so it does not actually pin anything the run depends on.

**Severity:** low-to-moderate. It cannot mask a real defect the way 1.1 could (it is not a
leak — it reads ambient and asserts, it does not write), but it is exactly the "test whose
outcome depends on the caller's environment" the task asks about, and it is the only
remaining one.

**Suggested fix** (not applied — I may not edit source): pin the value, e.g.
`monkeypatch.setattr(Config, "MAX_REVISION_TURNS", 4)`, or drop the assertion and let
`test_config_from_env` own the "env reaches Config" property.

---

## Category 2 — test order changing the result

### Verdict: no order-dependence found

**Method.** I ran a 13-file group containing every file that touches budget/config/strategy
state in three different orders:

| order | result |
|---|---|
| forward (as collected) | 108 passed |
| reversed | 108 passed |
| interleaved (`GROUP[::2] + GROUP[1::2]`) | 108 passed |

**Identical failure sets in all three orders** (empty). I also ran the probe file *alone*
and *after* each of `test_budget_modes.py`, `test_response_utils.py`, `test_strategies.py`,
`test_tool_budget.py`, `test_revision_reserve_usability.py`, `test_review_strategy.py` —
the only order-sensitivity was the leak in 1.1, fixed.

`pytest-randomly` / `pytest-xdist` are **not installed** (`pip list` shows only
`pytest 9.1.1`), so "with and without `-p no:randomly`" is not a distinction this
environment can make; I ran the orders explicitly instead.

### `importlib.reload(config)` — still present, but now contained

`tests/test_response_utils.py:105-135` still reloads the config module. This is the hazard
MEMORY records (a reload rebinds `config.Config` to a **new class object**, so
`monkeypatch.setattr("config.Config", ...)` aimed at the old class misses). At the time I
started, `test_config_from_env` used `monkeypatch.setenv` and relied on fixture teardown
order to undo the reload — which is exactly what went wrong.

The partner rewrote it to restore the environment and reload in a single `finally`
(`test_response_utils.py:127-135`), and added
`test_config_values_are_restored_after_the_reload` (line 138) which asserts the leak is
gone. I verified independently with my probe: after `test_response_utils.py`, all six
watched `Config` values match `.env`. **Contained.**

---

## Category 3 — tests/checkers that pass by accident

### 3.1 Verdict on the tests: none found (12 mutations, all detected)

The task's hardest category is "a test that cannot fail". I tested that directly by mutating
the **source** in a full temp copy of the repo (the real `src/` was never touched) and
re-running the tests that should notice:

| mutation (source) | detected? | test that caught it |
|---|---|---|
| reserve added on top instead of carved out | **DETECTED** | 6 failures incl. `test_the_reserve_is_carved_out_of_the_total_not_added_on_top` |
| `share_revision` ignores the reserve | **DETECTED** | 9 failures incl. `test_per_task_revision_uses_the_reserve_not_the_exhausted_pool` |
| `cost_share` returns `None` when exhausted | **DETECTED** | `test_cost_share_returns_zero_not_none_when_exhausted` |
| cost guard uses truthiness (`bool()` not `is not None`) | **DETECTED** | `test_a_cost_stop_is_labelled_by_its_cause_not_as_a_turn_limit` |
| `execute_tool` drops the role check | **DETECTED** | `test_execute_tool_refuses_a_tool_outside_the_role_grant` |
| `run_tests` drops the write guard | **DETECTED** | `test_a_read_only_role_cannot_write_through_run_tests` |
| `reset_working_tree` always reports success | **DETECTED** | `test_reset_returns_false_when_the_tree_stays_dirty` |
| completeness check reuses the resume predicate | **DETECTED** | `test_a_completed_run_without_a_patch_is_not_reported_as_missing` |
| `_is_finished_entry` returns True for everything | **DETECTED** | 4 failures in `test_resume_skips_failures.py` |
| trajectory truncates the tool result | **DETECTED** | `test_each_tool_result_is_recorded_in_full_and_paired_with_its_call` |
| prompt-variant warning silenced | **DETECTED** | 2 failures in `test_prompt_variants.py` |
| eval dedupe removed | **DETECTED** | `test_a_retried_instance_is_not_counted_twice` |

**NOT DETECTED: 0.** I also checked the two assertions that *looked* tautological —
`test_main_cli.py:73` (`cfg["revision_tool_turns"] == Config.REVISION_TOOL_TURNS`) and
`test_main_cli.py:105-110` (`_PROVIDER_MODEL_MAP[k] == Config.<K>_MODEL`), both of which
compare a value to the source it was derived from. Mutating the **writer** in a temp copy
made both fail, so they are real checks of the writer, not tautologies.

`test_tool_budget.py::test_disabled_pool_falls_back_to_per_act_cap` compares
`budget.share(n) == Config.MAX_TOOL_TURNS` — same shape. Mutating the fallback to return
`Config.MAX_TOOL_TURNS + 7` made it fail. **Real check.**

I also verified the "never runs the path it claims" case for every revision test by
forcing the revision loop to run zero times (`while ... < Config.MAX_REVISION_TURNS:` →
`while False:`): **all 7 revision tests failed**, so none of them is decorative.

### 3.2 STILL LIVE — `tools/analyze_revision_budget.py` recommends the starved value

This is the exact pattern the task names: *"a checker that repeats a remembered constant
instead of deriving the requirement will reproduce the bug it was written to find."*

`tools/analyze_revision_budget.py`:

```python
# line 82
reserve = 4 * 2 * rounds_wanted          # "4 turns per act"

# line 90-91
print("\n  Set REVISION_TOOL_TURNS >= 8 * MAX_REVISION_TURNS to give every round\n"
      "  the same 4-turn grant the smoke test measured as sufficient.")
```

For `rounds=4` that derives **32** and prints a recommendation of **32**. Measured output:

```
  rounds  reserve   per-act grant
       4       32   [4]  (all rounds equal)
  Set REVISION_TOOL_TURNS >= 8 * MAX_REVISION_TURNS to give every round
  the same 4-turn grant the smoke test measured as sufficient.
```

Meanwhile `tools/verify_revision_rounds.py:125` derives the requirement from the measured
need instead of a remembered constant:

```python
USABLE_GRANT = 6                                   # measured: the revision that edited used 6
expected = 2 * rounds * USABLE_GRANT               # 2 * 4 * 6 = 48
if reserve < expected:
    problems.append(f"REVISION_TOOL_TURNS={reserve} but {rounds} round(s) x 2 acts need "
                    f">= {expected} for a {USABLE_GRANT}-turn grant per act")
```

**The two tools contradict each other, and the shipped value is 48:**

| tool | derives for 4 rounds | verdict on the shipped 48 |
|---|---|---|
| `analyze_revision_budget.py` | **32** | would call 48 over-provisioned; recommends **32** |
| `verify_revision_rounds.py` | **48** | **PASS** (rc=0) |

Following `analyze_revision_budget.py`'s advice — set the reserve to 32 — produces exactly
the shape `verify_revision_rounds.py` **fails**, and exactly the shape
`tests/test_revision_reserve_usability.py::test_a_reserve_sized_for_four_rounds_is_not_enough_for_one`
pins as a defect (4+4 per round, below the measured need of 6):

```
reserve=32, rounds=4  ->  4+4  4+4  4+4  4+4      <-- cannot edit
reserve=48, rounds=4  ->  6+6  6+6  6+6  6+6      <-- shipped
```

**Aggravating factor:** `analyze_revision_budget.py` **always exits 0** — it is
informational and cannot gate anything. Measured: `rc=0` on the starved 32 shape. So a
reader who runs the older tool, sees "all rounds equal", and follows its printed
recommendation gets no warning at all.

**Impact.** This is a documentation/checker defect, not a live run defect (`.env` is 48 and
`verify_revision_rounds.py` passes). But the task named this exact pattern as "the most
valuable" thing to find, and this is a second instance of it. The `4 * 2 * rounds` and the
`8 * MAX_REVISION_TURNS` string are **stale arithmetic from before `USABLE_GRANT` was
raised from 4 to 6** (the docstring at `verify_revision_rounds.py:31-35` records the
change: 4 came from EXP-20260929-001 where a 4-turn revision "hit its cap precisely"; 6
came from EXP-20260930-415 where the editing revision used 6).

**Suggested fix:** have `analyze_revision_budget.py` import `USABLE_GRANT` from
`verify_revision_rounds.py` (or share one module) and print
`2 * rounds * USABLE_GRANT`, so the two tools cannot disagree. Better: delete it, since
`verify_revision_rounds.py` is the gate and it is strictly more correct.

---

## Category 4 — test claims vs real artifacts

The task asks me to check each behavioural claim against `results/EXP-*/artifacts/`, not
just mocks. I verified every citation I could resolve. **Four name the wrong run.**

### 4.1 WRONG INSTANCE — the "revision made 2 edits" citation

**Claim** (`tests/test_revision_reserve_usability.py:16-18`):
> "Measured need: the revision act that actually changed a patch used 6 turns
> (EXP-20260930-415, **django-11019** revision made 2 edits)."

**Also** (`tests/test_strategies.py:351`):
> "Measured need: 6 turns (the revision that changed a patch used 6, made 2 edits)."

**Measured** from `results/EXP-20260930-415/artifacts/*/review/trajectory.jsonl`:

```
django__django-10914/review: acts=['planner','executor','reviewer']                  revision=no
django__django-10924/review: acts=['planner','executor','reviewer','executor','reviewer']
        executor act 1: turns=10 edits=2
        executor act 3: turns=6  edits=2     <-- THE CITATION'S NUMBERS
django__django-11001/review: acts=['planner','executor','reviewer']                  revision=no
django__django-11019/review: acts=['planner','executor','reviewer']                  revision=no
django__django-11039/review: acts=['planner','executor','reviewer']                  revision=no
```

**`django-11019/review` in EXP-20260930-415 has no revision act at all** — three acts,
the third being the reviewer. The run with "6 turns, 2 edits" is **`django-10924`**.

The *numbers* (6 turns, 2 edits) are correct and I could reproduce them exactly; only the
**instance id** is wrong. Across **every** `EXP-*` on disk, only two review runs ever ran a
revision act, and neither is 11019:

```
EXP-20260930-398 django__django-10924  revision: turns=9 edits=1
EXP-20260930-415 django__django-10924  revision: turns=6 edits=2   <-- matches
```

**Impact.** The claim is used to justify `USABLE_GRANT=6`. The number is sound; the
attribution is not. A reader who opens `EXP-20260930-415/django-11019/review/` to check the
foundation of the whole reserve sizing finds no revision act — which reads as "the
measurement was never made" rather than "the label is off by one instance". This is the
kind of citation that, once found wrong, casts doubt on the ones that are right.

### 4.2 WRONG DENOMINATOR — "0 of 20 reviewer calls"

**Claim** (`tests/test_tool_enforcement.py:16`):
> "Neither had actually happened in the two pilots measured (0 of 20 reviewer calls were
> writes)…"

**Measured** reviewer tool calls, from `tool_calls.jsonl`:

| pilot | reviewer calls |
|---|---|
| EXP-20260930-215 | **64** |
| EXP-20260930-332 | **71** |
| EXP-20260930-415 | **91** |

226 across the three pilots, not 20. The **conclusion is true** — I searched every
`EXP-*` on disk for a reviewer call to `edit_file` / `write_file` / `reset_repo` and found
**0** — but "20" is not a denominator any measurement produces.

**Impact.** Low: the conclusion holds and is now *stronger* than claimed (0 of 226, not
0 of 20). But the figure is unverifiable, and an unverifiable number in a docstring is how
the "6 of 9" problem below started.

### 4.3 WRONG PILOT AND WRONG COUNT — "planner made ZERO tool calls in 6 of 9 runs"

**Claim** (`src/agents/base.py:64`, `tests/test_prompt_variants.py:10`):
> "The planner then made ZERO tool calls in **6 of 9** pilot runs (EXP-20260930-332)…"

**Measured** planner tool calls per run, counting every planner-using run (planning +
review, since both use the planner role):

| experiment | planning runs | review runs | zero-call planner runs |
|---|---|---|---|
| EXP-20260930-030 | 3 | 3 | 3 of 6 |
| EXP-20260930-215 | 5 | 5 | **7 of 10** |
| EXP-20260930-332 | 5 | 5 | **7 of 10** |
| EXP-20260930-365 | 1 | 1 | 0 of 2 |
| EXP-20260930-398 | 1 | 1 | 0 of 2 |
| EXP-20260930-415 | 5 | 5 | 0 of 10 |

**EXP-20260930-332 has 5 planning runs, of which 3 made zero planner calls.** There is no
9-run population anywhere in the artifacts, and no 6/9 ratio. The prompt audit
(`docs/AUDIT_PROMPTS_PARTNER.md:309-312`, `:394-397`) reached the same conclusion
independently and also could not reconcile it.

**Impact.** Moderate. This figure is the *stated reason* `planner_tools.md` exists and the
justification for the whole prompt-variant warning machinery. The direction is right (the
zero-call behaviour is real and dramatic — 7 of 10, worse than claimed), but a task
statement, a source comment, and a test docstring all carry a number that cannot be
reproduced. The fix is to write `7 of 10` and cite `EXP-20260930-215` **or**
`EXP-20260930-332` (both measure 7/10).

### 4.4 VERIFIED CORRECT — the citations that hold

I checked these against the artifacts and they reproduce **exactly**:

| claim | location | measured | verdict |
|---|---|---|---|
| "run_tests 8x planning, 13x review, 0x direct" (EXP-20260930-215) | `test_agent_tools.py:306` | planning **8**, review **13**, direct **0** | **EXACT** |
| "planning executor called reset_repo at call 29 of 43" | `test_agent_tools.py:373` | `reset_repo at call 29 of 43 (agent=executor)` | **EXACT** |
| "the heaviest act collected ~166k chars across 108 calls" | `test_context_overflow.py:8` | `django-11019/planning: 108 calls, 166,232 chars` | **EXACT** |
| "10 of the reviewer's 13 run_tests calls were read-only probes" | `test_tool_enforcement.py:187` | EXP-20260930-332: 13 run_tests = **10 probes + 3 pytest** | **EXACT** |
| "the executor removed its own untracked `_t*.py` scratch files" | `test_tool_enforcement.py:203` | `os.remove(f) for f in ['_t.py','_t2.py','_t3.py','_t4.py']` | **EXACT** |

One caveat on the 166k claim, stated rather than silently accepted: it is measured from
`tool_calls.jsonl`, whose `result_preview` is **capped at 2000 chars**, and `EXP-20260929-022`
has **no `trajectory.jsonl`** (it predates full trajectory recording). So the figure is
"preview chars across a whole RUN", not "chars of one act's full outputs" as the docstring's
wording ("the heaviest act") implies. The number is right; the unit description is loose.

### 4.5 Artifacts the tests cite but cannot resolve

`EXP-20260930-215` has **no `trajectory.jsonl`** — its artifact set is
`direct.md, executor.md, messages.jsonl, patch.txt, planner.md, reviewer.md, summary.json,
tool_calls.jsonl`. Every claim citing 215 (4.4 rows 1-2) is verifiable **only** through
`tool_calls.jsonl`, which carries no turn numbers and a 2000-char preview. I verified them
and they hold, but a reader who looks for a trajectory in that pilot will not find one.

---

## What I could not verify

1. **Whether the "6 of 9" figure was ever measured at all.** I could not reproduce it from
   any artifact, and I could not find a third pilot with a 9-run planner population. I
   report the measured numbers (7/10, 3/5) and do not guess which was intended.
2. **The `MAX_REVISION_TURNS=0` failure's real-world reach.** I proved the test fails when
   the shell exports 0. Whether any planned ablation actually uses 0 is a question about
   intent, not about the artifacts — I did not find a run or a script that sets it.
3. **Line-number accuracy of the plans** the prompt tests cite (carried over from the
   prompt audit) — paths verified, line scopes not.
4. **Whether the `.tr_*` leak probe I wrote is the same method the partner used.** I wrote
   `tests/test_zz_probe_config_leak.py` independently to measure the leak; the partner
   committed a file with the same name in `39314fa`. I did not diff the two; my findings
   stand on my own measurements, reproduced above.

---

## Reproduce

```powershell
# Category 1.2 -- the one still-live fragility
$env:MAX_REVISION_TURNS="0"
.venv\Scripts\python.exe -m pytest tests/test_response_utils.py::test_config_defaults -q
#   -> FAILED  assert 0 >= 1
Remove-Item Env:\MAX_REVISION_TURNS
.venv\Scripts\python.exe -m pytest tests/test_response_utils.py::test_config_defaults -q
#   -> 1 passed

# Category 3.2 -- the two checkers disagree on the shipped config
.venv\Scripts\python.exe tools/analyze_revision_budget.py 4      # recommends 32, rc=0
.venv\Scripts\python.exe tools/verify_revision_rounds.py         # requires 48, rc=0

# Category 4 -- the citations
#   11019 vs 10924:  parse results/EXP-20260930-415/artifacts/*/review/trajectory.jsonl
#                    and count distinct executor act_index values
#   reviewer calls:  count agent=="reviewer" rows in results/*/artifacts/*/*/tool_calls.jsonl
#   planner zeros:   count agent=="planner" rows per planning/ and review/ tool_calls.jsonl

# The two-way comparison the task prescribes (both now agree)
#   clean : 426 passed
#   dirty (40/8/1): 426 passed
```

Scratch probes (`.tr_*`) were removed after the audit. **No file in `src/`, `tools/`, or
`results/` was modified.** The one file I added under `tests/`
(`test_zz_probe_config_leak.py`) is a measurement probe, later committed by the partner in
`39314fa`. The 4 commits that landed during this audit (`da00708`, `39314fa`, `eed55d6`,
`212b2fb`) are the partner's, not mine.
