# AUDIT — Is the pipeline READY for a 50-issue sweep?

**Task:** `docs/TASK_SCALE_AUDIT.md`
**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Date:** 2026-10-01
**Mode:** READ-ONLY. No source file modified. Scratch scripts used the `.scale_*` prefix and were removed.
**Sweep under audit:** 50 instances × 3 strategies = **150 runs**, `tools/run_final_sweep.py`.

## Important: the repo moved while this audit ran

The audit started at HEAD `af0f16b`. A partner committed **`d3d7ffe`** ("audit: verify the
pipeline survives a 150-run sweep (my own pass)") at **02:20:55**, and 8 commits landed in
between (`c7b9324` … `d3d7ffe`). Two consequences I must state up front:

1. **The working tree changed under me.** My first `preflight_repos.py` run reported **5 FAIL
   (dirty django checkouts)**; a second run 25 minutes later reported **50/50 OK**. I never ran
   `clean_repos.py`. The five checkouts' `.git/index` mtimes are all **01:59:2x**, i.e. a
   concurrent process cleaned them mid-audit. **Repo-state findings below are therefore
   time-stamped and must be re-checked immediately before the run.**
2. **The partner produced overlapping tooling** (`tools/verify_resume_logic.py`,
   `tools/check_disk_budget.py`, `tools/check_trajectory_md_cost.py`,
   `tools/verify_resume_keys_match.py`). Where their tool agrees with my measurement I say so;
   where I found something their pass did not surface, that is called out as unique.

Every number below is measured on this machine. Where I could not measure, I say "not measured".

---

## Verdict summary

| # | Area | Verdict |
|---|---|---|
| 1 | Resume and re-run safety at scale | **READY** |
| 2 | Disk space and artifact size at 50 issues | **READY WITH CAVEAT** |
| 3 | The 45 instances the pilot never touched | **NOT READY** (1 blocker) |
| 4 | Cost guard under the new budget | **READY WITH CAVEAT** |

**Blockers found:** 2 (one in area 3, one in area 4). Neither is in the resume path, which is
the one the task worried about most — that path is clean.

---

## Area 1 — Resume and re-run safety at scale

### Verdict: READY

I tested this by driving the real functions in `src/experiments/runner.py` against synthetic
jsonl in a temp directory (no real results touched).

**Q: sweep dies at run 87, resumed — does it skip the 86 done and redo only the rest?**

```
50 instances x 3 strategies = 150 runs, in order
pass 1 wrote 86 rows; CSV exists = False          <- crash before any export
done per strategy: direct=29, planning=29, review=28   (86 total)
resume would SKIP 86 (expected 86), RE-RUN 64 (expected 64)
first 3 to re-run: [('repo__repo-029','review'), ('repo__repo-030','direct'), ('repo__repo-030','planning')]
```

**Yes — 86 skipped, 64 re-run, exactly right.** Note the crash left **no CSV at all**; the
resume recovered entirely from the jsonl savepoints. That is the documented main case and it
works.

**Q: does a resumed run DUPLICATE rows in the CSV or jsonl?**

```
after resume: merged rows = 150 (expected 150)
unique (instance_id, strategy) keys = 150 (no duplicates)
  direct.jsonl lines = 50   planning.jsonl lines = 50   review.jsonl lines = 50
```

**No duplication in the CSV** — `_merge_csv_rows` keys by `(instance_id, strategy)`
(`runner.py:452-513`).

The **jsonl does keep duplicate rows**, by design: it is an append-only savepoint, so a retry
of a failed instance appends a second row. I verified **every consumer collapses it**:

| Consumer | Behaviour on a retried instance (2 jsonl rows) |
|---|---|
| `_load_existing_ids` (resume skip set) | 1 key — a `set`, duplicates collapse |
| completeness check | 1 entry — a `set` comprehension |
| `_merge_csv_rows` | 1 row — keyed by `(instance_id, strategy)` |
| `eval_modal.classify_summary` | deduped, last wins (`eval_modal.py:166-169`) |

One counter is *not* deduped: **`tools/check_sweep_state.py:48` counts raw
`predictions.jsonl` lines**, so after a retry it can print `predictions=10/9`. It is a progress
counter, not a result, and the task did not name it — recorded here so the number is not
mistaken for a data defect.

**Q: does `--resume` re-run a FAILED run without corrupting completed ones?**

```
done keys = ['i-good|m|False']
-> good skipped:        True
-> timeout RETRIED:     True
-> ratelimit RETRIED:   True
  i-good:      finished=True  completed=True
  i-timeout:   finished=False completed=False
  i-ratelimit: finished=False completed=False
```

**Correct.** Only a run with a non-empty patch is "finished" (`_is_finished_entry`,
`runner.py:253-272`); failures are retried. The completed run is untouched — its row is
preserved by the keyed merge.

A subtlety worth stating: a **`NO_DIFF` run** (finished normally, produced no patch) is
`finished=False` (so resume **retries** it) but `completed=True` (so the completeness check
**counts it**). That is the deliberate split between `_is_finished_entry` and
`_is_completed_entry` (`runner.py:275-295`), and the consequence is benign: a re-run can only
**improve** coverage, never lose it. The partner's `tools/verify_resume_logic.py` exercises the
same matrix and reaches the same result (all 19 checks OK).

**Q: sweep dies MID-RUN (act 3 of 5) — dirty checkout that corrupts the next strategy?**

I reproduced this against a real temp git repo:

```
MID-RUN DEATH: act edits the file, process dies
  git status -> M code.py
  capture_diff -> 'diff --git a/code.py ... -x = 1 +x = 2'

NEXT strategy's run() calls reset_working_tree() FIRST
  reset_working_tree -> True
  git status after -> (clean)
  capture_diff now -> ''
```

**Clean.** Each strategy calls `reset_working_tree()` at the top of `run()`
(`direct_strategy.py:36`, `planning_strategy.py:33`, `review_strategy.py:88`) and **raises** if
it returns False, so the interrupted edits cannot leak into the next strategy's captured diff.

The one path worth checking: a **skipped** run never calls `reset` (the skip is in
`runner.run_experiments`, before `strategy.run`). But a run is skipped only when it is
*finished*, and a finished run did not die mid-act — so no leak path exists via resume.

**Not measured:** the actual behaviour under a hard `SIGKILL` during `to_csv` (I read
`_write_csv_atomically`, `runner.py:516-536`, which writes to `.tmp` then `os.replace`, and
confirmed the savepoint fallback — but I did not kill a live run to prove it).

---

## Area 2 — Disk space and artifact size at 50 issues

### Verdict: READY WITH CAVEAT

**Measured bytes per run** (pilot `EXP-20260930-415`, 15 runs, real artifacts):

```
EXP-20260930-415: 7.34 MB / 15 runs = 478 KB per run
  artifacts: 6.99 MB (120 files)
  predictions: 0.07 MB   patches: 0.03 MB   logs: 0.20 MB
largest single run: 1,440 KB (django__django-11019/review)
```

**Extrapolation to 150 runs:**

| Basis | Total |
|---|---|
| at the pilot mean (478 KB/run) | **~70 MB** |
| at the pilot max (1,440 KB/run) | **~211 MB** |
| × 7.3 for the 200-turn budget (pilot mean was 27.3 turns) | **~536 MB** |

**Free disk:** `results/` and `datasets/` are on the same volume — **403.5 GB total, 98.3 GB
free (76% used)**. Even the 536 MB worst case is **~0.5% of free space**. The partner's
`tools/check_disk_budget.py` computes the same numbers independently (455 KB/run mean, 0.22 GB
worst case, "room to spare"). **Disk is not a risk.**

**CAVEAT — memory, not disk.** The task asks "is there ANY cap on `trajectory.jsonl`? does the
writer stream or buffer?" **It buffers.**

`tool_loop.py:265` `trajectory: list[dict] = []` accumulates every turn and **every full tool
result** in RAM for the whole act; `result.trajectory = list(trajectory)` copies it
(`tool_loop.py:339`); `_save_artifacts` then holds it again in `traj_lines`
(`runner.py:121-134`) before writing. **There is no cap on the trajectory list** — I read the
whole write path.

The reason this matters: **`read_file` is the one tool with no output cap.** `tools.py:212-229`
returns the entire file (the `end_line=0` default means "to EOF"). Measured against the actual
50-issue checkouts:

```
largest .py in the cached repos: 1.13 MB  (sympy/integrals/rubi/rules/sine.py)
a read_file() of it -> 1.13 MB in trajectory.jsonl AND 1.13 MB resident in RAM
```

In the pilot, agents used line ranges (`read_file(path, start_line=300, end_line=315)`), so the
observed max was only ~12–17 KB and the heaviest act collected ~166 KB. But the model chooses
the arguments: **one `read_file` with no range on a sympy rubi rules file puts 1.1 MB into RAM
and into the trajectory, and a run that does this repeatedly at 200 turns has no bound.** The
model only *sees* 2000 chars (`_truncate_tool_output`), so the cost is paid for nothing.

**Usul:** cap `read_file`'s return (e.g. 100 KB with a head+tail marker and a "use a line range"
hint), or cap `traj_lines` per entry. This is a hardening item, not a blocker — the pilot shows
agents self-limit — but it is the only unbounded growth path I found.

---

## Area 3 — The 45 instances the pilot never touched

### Verdict: NOT READY

**Q: do the checkouts exist and are they pristine? Run `preflight_repos.py`.**

Exact output, **first run (01:55)**:

```
FAIL  django__django-10914  (django/django)   - working tree dirty (4 path(s))
FAIL  django__django-10924  (django/django)   - working tree dirty (1 path(s))
FAIL  django__django-11001  (django/django)   - working tree dirty (1 path(s))
FAIL  django__django-11019  (django/django)   - working tree dirty (2 path(s))
FAIL  django__django-11039  (django/django)   - working tree dirty (2 path(s))
OK    ... (45 others)
  45/50 usable, 5 with problems
```

**Second run (02:20)** — after the concurrent cleanup described at the top:

```
  50/50 usable, 0 with problems
  Every repo is pristine at its base_commit -- safe to run.
```

So: **all 50 checkouts exist and are now pristine.** The blocker is not the current state — it
is that **the state changed underneath me without either of us running the cleaner in this
session**, which means repo state is not under control. `preflight_repos.py` exits non-zero on
failure (`preflight_repos.py:195`) and `run_final_sweep.py` **only prints a reminder to run
it** (`run_final_sweep.py:224`) — it does **not gate on it**. The preflight is advisory, so a
sweep can start against dirty checkouts. The consequence is the one the tool's own docstring
names: the captured diff is relative to the wrong tree, `git apply` still succeeds, and the
failure only appears at evaluation — looking like the model's fault.

**Blocker:** run `python tools/preflight_repos.py` immediately before the sweep and **refuse to
start unless it exits 0**, or make `run_final_sweep.py` call it and abort on failure.

**Q: does anything assume a django-style layout?**

Grepped `src/agents/tools.py` for `django`, `runtests`, `tests/`. Findings:

* **No hardcoded django path is used for logic.** The django mentions at `tools.py:83-84`,
  `138`, `178`, `386` are all in docstrings/comments explaining path normalisation, and `207`
  uses a `requests` example. None is a branch condition.
* **`_guess_test_command` (`tools.py:542-559`) detects layout, it does not assume:**

```python
py = sys.executable or "python"
if (root / "tests" / "runtests.py").exists():
    return f'cd tests && "{py}" runtests.py'
if (root / "pytest.ini").exists() or (root / "setup.cfg").exists() or (root / "tox.ini").exists():
    return f'"{py}" -m pytest -x -q'
if (root / "tests").is_dir():
    return f'"{py}" -m pytest tests -x -q'
return f'"{py}" -m pytest -x -q'
```

**Q: for a non-django repo, what does `_guess_test_command` return? Plausible for sympy and scikit-learn?**

Measured against each family's actual checkout:

| Repo | Markers found | `_guess_test_command` returns |
|---|---|---|
| django | `tests/runtests.py`, `setup.cfg`, `tox.ini`, `tests/` | `cd tests && "<py>" runtests.py` |
| **sympy** | **(none)** | `"<py>" -m pytest -x -q` |
| scikit-learn | `setup.cfg` | `"<py>" -m pytest -x -q` |
| matplotlib | `pytest.ini`, `setup.cfg`, `tox.ini` | `"<py>" -m pytest -x -q` |
| requests | `setup.cfg` | `"<py>" -m pytest -x -q` |
| seaborn | `setup.cfg`, `tests/` | `"<py>" -m pytest -x -q` |

**scikit-learn: plausible** — it genuinely uses pytest, and the command is the standard one.

**sympy: the command is not the repo's own runner, but the outcome is benign.** sympy ships its
runner at `sympy/utilities/runtests.py` and a `bin/test` script — **not** `tests/runtests.py`,
which is why the django branch does not fire. The comment at `tools.py:546-547` claims
*"Django and sympy ship a `tests/runtests.py` runner"* — **that is wrong for sympy** (it is
`sympy/utilities/runtests.py`). The docstring is inaccurate; the code is not.

Does it matter? sympy's dependencies are **not installed in this sandbox** (the SWE-bench
harness supplies them in a conda env), so *any* test command fails environmentally. The tool
already detects this: `_looks_like_env_failure` (`tools.py:311-314`) matches "no module named" /
"modulenotfounderror" and returns the explicit `[tests unavailable] … Do NOT call run_tests
again` message (`tools.py:527-536`). So a sympy run gets a clear "tests unavailable" signal and
is told to stop — the mis-guessed command costs at most one turn. **Minor, not a blocker.**

---

## Area 4 — Cost guard under the new budget

### Verdict: READY WITH CAVEAT

**Q: is `COST_LIMIT_USD=3.00` enforced PER TASK or can each act spend $3?**

**Per task.** `ToolTurnBudget.__post_init__` creates **one** `cost_remaining = COST_LIMIT_USD`
(`budget.py:117`); `cost_share(acts_remaining)` hands each act a *share* of that single pool
(`budget.py:244-268`); `spend_cost()` draws it down (`budget.py:270-272`). Measured:

```
cost_remaining starts at 3.0 (ONE pool for the whole task)
  acts_remaining=3 -> $1.0000     review planner
  acts_remaining=2 -> $1.4500     review executor
  acts_remaining=1 -> $2.8000     review reviewer
after 3 acts spending $0.10 each: cost_remaining = 2.7000
```

So a later act cannot be left uncapped: the guard's own comment at `tool_loop.py:227-229` warns
that an *exhausted* cap arrives as `0.0` and must still arm the guard (`is not None`, not
truthiness). Verified: `cost_rates is not None` gates the check (`tool_loop.py:364`).

**Q: the review arm can now run 4 revision rounds — does the cost budget account for it? Could review spend more than direct?**

The *turn* budget is fair — I verified with the partner's tool at the sweep's own numbers:

```
python tools/check_budget_fairness.py --total 200 --reserve 32 --floor 10
  direct   200            -> 200
  planning 190+10         -> 200
  review   148+10+10 (168) + 32 revision = 200
  all three spend exactly 200 turns -> FAIR
```

The reserve is **carved out of** the pool, not added — so review cannot buy a win with a bigger
budget. **But the cost budget is NOT carved out.** `cost_share()` is called with the *base* act
counts only; the revision acts pass `budget.cost_share(2 * rounds_left)`
(`review_strategy.py:194`, `:219`), which is just another slice of the same $3. Because the cap
is a **task-wide backstop** that does not bind in normal operation (pilot: **$0.037/run mean,
$0.55 for 15 runs**), this is fine in practice: review cannot *exceed* $3. It can, however,
*consume more of the shared $3* than direct, which is expected — it does more acts. Since the
cap is non-binding, no confound arises. **Documented, not a defect.**

### BLOCKER — the sweep runs ONE revision round, not the four the reserve is sized for

This is the most consequential finding of the audit and I did not see it in the partner's pass.

`.env:33` sets **`MAX_REVISION_TURNS=1`**. The sweep command **does not override it** — I read
`run_final_sweep.py:build_cmd()` and the dry-run output:

```
run_final_sweep.py --set: OPENCODE_MODEL, TOTAL_TOOL_TURNS, BUDGET_MODE,
                          BUDGET_FLOOR_PER_ACT, REVISION_TOOL_TURNS,
                          COST_LIMIT_USD, ACT_TIMEOUT_SECONDS
                          -- MAX_REVISION_TURNS is NOT set
```

`review_strategy.py:178` loops `while not approved and bb.revision < Config.MAX_REVISION_TURNS`,
so **the loop body runs at most once**. Meanwhile the reserve is **32**, sized for four rounds —
stated in three places:

* `config.py:193` — `32 = 4 rounds x 8 turns, so each revision and re-review gets 8.`
* `.env:80` — `# 32 = 4 rounds x 8 turns. Sized by measurement…`
* `tools/analyze_revision_budget.py:90` — `Set REVISION_TOOL_TURNS >= 8 * MAX_REVISION_TURNS`

Measured effect with the sweep's own config:

```
INTENT (config.py:193): '32 = 4 rounds x 8 turns'
ACTUAL: 1 round, each act granted 16 turns   (share_revision(2) = 32//2 = 16)
```

**Failure this causes during the sweep:** the review arm's numbers would be measured against a
*one-shot* review — the exact "one executor act plus a decorative round" failure that
`run_final_sweep.py:11-17` was written to eliminate. The reserve is 4× larger than the rounds
that can use it, so **16 of the 32 revision turns are unreachable** (they are carved out of
review's pool and never spent). Two consequences for the thesis:

1. **Validity:** any claim that "review can revise until the fix is correct" is unsupported —
   it revises at most once, no matter how many rounds the reserve pays for.
2. **Fairness (the other direction):** review's *base* flow is 168 turns vs direct's 200, and
   it can only ever claw back 32 of them in one round. If the intent was 4 rounds, review is
   silently under-resourced relative to its own design.

`tools/check_budget_fairness.py` **cannot catch this**: it simulates one revision round
(`check_budget_fairness.py:67`) and never reads `MAX_REVISION_TURNS`. The tests that *do* set
`MAX_REVISION_TURNS=3` (`tests/test_strategies.py:259`) do it via `monkeypatch` — the shipped
`.env` value is 1, so the tests pass while the sweep runs a different configuration.

**Usul:** either set `MAX_REVISION_TURNS=4` in the sweep's `--set` list (matching the reserve),
or set `REVISION_TOOL_TURNS=8` (matching one round). Leaving them mismatched means the run
measures a configuration nobody chose. **This is a one-line fix, but it must land before the
sweep** — after 150 runs it is unrecoverable.

---

## Minor finding (not a blocker) — a spurious warning fires on 100 of 150 runs

`budget.py:124-133` warns when `mode == "per_task" and revision_reserve <= 0`. `direct` and
`planning` call `from_config()` **without** `with_revisions`, so their reserve is 0 **by design**
(`budget.py:146-165` explicitly says passing `with_revisions=True` for them would be wrong).
Measured on the real call path with the sweep's config:

```
direct    from_config()                    -> 1 warning(s)
    BUDGET_MODE=per_task with REVISION_TOOL_TURNS=0: the base acts consume the whole pool…
planning  from_config()                    -> 1 warning(s)
review    from_config(with_revisions=True) -> 0 warning(s)
```

The warning is **wrong twice**: it fires for the two strategies that have no revision act, and
its text asserts `REVISION_TOOL_TURNS=0` when the configured value is 32. It is emitted once per
`run()`, so **100 of 150 runs** log a scary-looking budget warning. This is the "alarm that
cries wolf" failure mode the same code warns about elsewhere. It does not affect results, but it
will pollute the per-experiment logs and train the reader to ignore the one warning that matters.

**Usul:** gate the warning on the strategy actually requesting revisions (pass a flag), or check
`Config.REVISION_TOOL_TURNS` instead of the per-call `revision_reserve`.

---

## Additional verification (beyond the four areas)

The task listed 7 recent changes to treat as suspect. I verified the ones in scope:

| Change | Status | Evidence |
|---|---|---|
| `TOTAL_TOOL_TURNS=200`, `REVISION_TOOL_TURNS=32` | **suspect** | Totals fair (200/200/200) but `MAX_REVISION_TURNS=1` makes 16 reserve turns unreachable — see Area 4 blocker |
| Full trajectory recording | **works** | `trajectory.jsonl` written per act (`runner.py:121-134`); verified 15 files / 1.88 MB in the pilot |
| Context-window guard | **works** | `_is_context_overflow` (`tool_loop.py:135-159`) is fatal-on, not retried; `_trim_messages_for_context` (`:87-115`) trims oldest tool outputs before the final answer |
| `planner_tools.md` (new), `shared_static.md` rewritten | **works now** | `planner_tools.md` exists and resolves; the dead `SOURCE CODE` refs are gone from `shared_static.md` (grep → 0 hits) |
| `execute_tool(name, args, role=...)` role enforcement | **works** | `tools.py:1058-1066` refuses a tool not in `AGENT_TOOLS[role]` |
| `run_tests` refuses a repo write from a read-only role | **works** | `tools.py:481-493` + `_command_looks_like_a_write`; `_READONLY_ROLES = {"planner","reviewer"}` (`:333`) |
| `reset_working_tree` returns bool, strategies raise on False | **works** | Verified in the temp-repo test above; all three strategies raise |

Test suite: **408 passed** in 133 s. (The `PROMPT VARIANT MISSING` warnings during the run come
from `tests/test_strategies.py:260-263`, which monkeypatches the loader to return the default —
they are the test asserting the warning fires, not a live defect.)

---

## Could not verify / explicitly unresolved

1. **Repo cleanliness at run time.** It changed mid-audit (5 dirty → 0) with no cleaner invoked
   by me. I cannot assert the checkouts will be pristine when the sweep starts. **Re-run
   `preflight_repos.py` immediately before, and gate on it.**
2. **The actual wall-clock duration at 200 turns.** The pilot averaged 88 s/run at 27.3 turns.
   A 200-turn run is longer, but the relationship is not linear (later turns re-send a growing
   conversation). **Not measured** — I did not run a paid 200-turn act. The 14–17 h estimate in
   the task is unverified by me.
3. **The real dollar cost of 150 runs.** Pilot: $0.037/run mean, $0.55/15 runs. Extrapolating
   naively gives ~$5.5, but a run that *actually uses* 200 turns costs far more than one that
   used 27. **Not measured** — I did not spend money.
4. **Whether `MAX_REVISION_TURNS=1` is intentional.** It may be a deliberate choice for this
   sweep; if so, the reserve of 32 is the error. Either way the two values disagree with the
   three places that document "4 rounds". I report the mismatch, not the intent.
5. **Peak RSS of a real 200-turn run.** I proved the trajectory is buffered and uncapped and
   measured the worst-case single tool result (1.13 MB), but I did not observe a live run's
   memory. **Not measured.**
6. **Line-number accuracy of planner plans** (carried over from the prompt audit) — paths
   verified, line scopes not.

---

## Reproduce

```bash
# Area 1 -- resume semantics (temp dir, no real results touched)
#   synthetic jsonl: 86 done -> skip 86, rerun 64; retry replaces; no CSV dupes
#   (script .scale_resume.py, removed after the audit; logic in runner.py:253-513)

# Area 2 -- disk and per-run size
python tools/check_disk_budget.py                 # partner's tool, agrees with mine

# Area 3 -- repo preflight (RUN THIS BEFORE THE SWEEP)
python tools/preflight_repos.py

# Area 4 -- budget arithmetic at the sweep's numbers
python tools/check_budget_fairness.py --total 200 --reserve 32 --floor 10
python tools/analyze_revision_budget.py 4
grep MAX_REVISION_TURNS .env                       # -> 1, while the reserve is sized for 4
python tools/run_final_sweep.py --dry-run          # -> MAX_REVISION_TURNS is not set

# Resume verification (partner's tool)
python tools/verify_resume_logic.py
```

Scratch scripts (`.scale_*`) were removed after the audit. **No file in `src/`, `tests/`,
`tools/`, or `results/` was modified.** The 8 commits that landed during this audit
(`c7b9324` … `d3d7ffe`) are the partner's, not mine.
