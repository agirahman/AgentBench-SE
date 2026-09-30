# AUDIT — Prompt files: dead references and instruction conflicts

**Task:** `docs/TASK_PROMPT_AUDIT.md`
**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Date:** 2026-10-01
**Mode:** READ-ONLY. No source file was modified. Scratch scripts use the `.audit_` prefix (gitignored).
**Pilot under study:** `results/EXP-20260930-332` (15 runs: 5 instances × 3 strategies), executed `2026-09-30T16:52:55Z` (= 23:52 local).

---

## 0. The finding that reframes everything

**The prompt files in the working tree are NOT the ones the pilot ran with.** They carry
uncommitted edits, and a file the pilot never had now exists.

| Fact | Evidence |
|---|---|
| HEAD is `af0f16b`, committed `2026-09-30 23:32:10 +0700` | `git log -1` |
| The pilot finished `2026-09-30 23:52:55 +0700` — **after** HEAD | `manifest.json` `execution_timestamp`; `logs/experiment.log` mtime `23:52:55` |
| `src/prompts/planner_tools.md` exists now, mtime `2026-10-01 00:09:07` | `os.stat` |
| It was **ABSENT** at HEAD | `git cat-file -e af0f16b:src/prompts/planner_tools.md` → absent; `git ls-tree af0f16b src/prompts/` lists 8 files, no `planner_tools.md` |
| `planner.md`, `reviewer.md`, `executor.md`, `direct_prompt.md` are **modified, uncommitted** | `git status --short`; `git diff --stat` = `4 files changed, 8 deletions(-)` |
| `src/agents/base.py` is **modified, uncommitted** | `git status --short` |

So an in-flight partner fix already addresses the pilot's cause. **This audit therefore
reports two states:** what the pilot actually ran (HEAD `af0f16b`) and what the tree holds
now. Both matter: the tree is not committed, so a re-run today would use it, but the pilot's
results were produced by HEAD.

I did not write to any of these files. Everything below is measurement.

---

## 1. Inventory — every file in `src/prompts/`

Nine files (eight at HEAD, plus the new `planner_tools.md`):

| File | Tracked? | Modified? | Role that uses it |
|---|---|---|---|
| `planner.md` | yes | **modified** | `planner` |
| `planner_tools.md` | **untracked (new)** | — | `planner` (tool mode) |
| `reviewer.md` | yes | **modified** | `reviewer` |
| `reviewer_tools.md` | yes | no | `reviewer` (tool mode) |
| `executor.md` | yes | **modified** | `executor` |
| `executor_tools.md` | yes | no | `executor` (tool mode) |
| `direct_prompt.md` | yes | **modified** | `direct` |
| `direct_prompt_tools.md` | yes | no | `direct` (tool mode) |
| `shared_static.md` | yes | no | **all four** (prepended when `PROMPT_CACHE_LAYOUT=true`) |

### Role → prompt mapping (item 2d)

Source: the agent classes, not `config.py` (there is no `prompt_file` in `config.py` — the
task's hint to grep it finds nothing; the mapping lives on the classes).

```
src/agents/planner_agent.py:8    prompt_file = "planner.md"
src/agents/reviewer_agent.py:8   prompt_file = "reviewer.md"
src/agents/executor_agent.py:8   prompt_file = "executor.md"
src/agents/direct_agent.py:8     prompt_file = "direct_prompt.md"
```

Tool mode appends `_tools` before the extension — `src/agents/base.py:59-67` (HEAD):

```python
stem = self.prompt_file[:-3] if self.prompt_file.endswith(".md") else self.prompt_file
self._tool_template_cache = (
    load_prompt_or_default(f"{stem}_tools.md", "") or self.template
)
```

**`or self.template` is the defect.** A missing `_tools` variant silently falls back to the
base (non-tool) template. Verified by executing the loader:

```
planner    prompt_file=planner.md       -> planner_tools.md    exists=TRUE (now)
reviewer   prompt_file=reviewer.md      -> reviewer_tools.md   exists=True
executor   prompt_file=executor.md      -> executor_tools.md   exists=True
direct     prompt_file=direct_prompt.md -> direct_prompt_tools.md exists=True
```

At HEAD, `planner_tools.md` was absent, so **the planner received `planner.md`** — a prompt
that says `Output ONLY valid JSON` and mentions no tool at all. I confirmed the base prompts
contain zero tool mentions:

```
HEAD planner.md:   lines=17  tool-mentions=[]
HEAD reviewer.md:  lines=22  tool-mentions=[]
HEAD executor.md:  lines=24  tool-mentions=[]
HEAD direct_prompt.md: lines=31 tool-mentions=[]
```

---

## 2. Per-file findings

### 2.1 `planner.md`

**Verdict at HEAD: `HAS CONFLICT` (and `HAS DEAD REFERENCE`).**
**Verdict now: `HAS DEAD REFERENCE` removed, but still the wrong prompt for tool mode if the new variant is ever lost.**

a. **Dead reference — YES, at HEAD line 15:**

> `If source code is provided below under "SOURCE CODE (base commit)", use the exact file paths and line numbers shown there when naming affected files and line scopes. Do NOT invent file paths or line numbers that are not present in the provided source code.`

b. **Emit fixed format immediately — YES, HEAD line 6:**

> `Create a structured analysis plan. Output ONLY valid JSON in this exact format:`

There is no "read the code first" step anywhere in the file — no mention of `grep`,
`read_file`, `list_files`, or tools. The instruction is to answer now.

c. **Contradicts its system prompt — YES, decisively.** The planner is a read-only role, so
`src/agents/base.py:103-107` sends `READONLY_TOOL_SYSTEM_PROMPT`, which says:

> `1. Explore with read_file / grep / list_files to gather evidence from the actual source — do not rely on assumptions about the code.`
> `2. Base every claim on code you have actually read.`

The task prompt says "output JSON now"; the system prompt says "read code first". The task
prompt also supplies a JSON schema with an `affected_files` field whose entries must be
paths — while the only path source named ("SOURCE CODE (base commit)") never appears.

d. **Role:** `planner` (`src/agents/planner_agent.py:8`).

### 2.2 `planner_tools.md` (new, untracked)

**Verdict: `OK`.**

a. No dead reference (grep for `SOURCE CODE|base commit` → 0 hits).
b. No immediate-emit: line 17 says `Then reply with ONLY this JSON` — *after* steps 1-4.
c. Does not contradict `READONLY_TOOL_SYSTEM_PROMPT`; it restates it (line 6: `Your plan
   must be grounded in code you have ACTUALLY read`).
d. Role: `planner`.

It is the correct fix for the pilot's cause. **It is not committed**, and there is no test
or CI gate that would fail if it were deleted — the fallback would go silent again (see §6).

### 2.3 `reviewer.md`

**Verdict at HEAD: `HAS DEAD REFERENCE`.** **Verdict now: same (one dead ref remains).**

a. **Dead reference — YES, HEAD line 20:**

> `If source code is provided below under "SOURCE CODE (base commit)", verify the patch references real files and line numbers present in that source code. Flag patches that reference invented file paths or line numbers.`

b. Immediate-emit — the file does say `Output ONLY valid JSON` (HEAD line 12), **but** it is
   never the prompt the reviewer runs with: `reviewer_tools.md` exists and is selected in
   tool mode. So this is not a live defect.

c. Contradicts system prompt — not in practice: tool mode loads `reviewer_tools.md`.

d. Role: `reviewer` (`src/agents/reviewer_agent.py:8`).

### 2.4 `reviewer_tools.md`

**Verdict: `OK`.** No `SOURCE CODE`/`base commit` reference. It explicitly instructs
exploration (lines 14-24: read the changed code *and its callers*, establish the mechanism,
verify independently of the plan) and emits JSON only at step 5, after reading.

### 2.5 `executor.md`

**Verdict at HEAD: `HAS DEAD REFERENCE`.** **Verdict now: same (one dead ref remains).**

a. **Dead reference — YES, HEAD line 15:**

> `If source code is provided below under "SOURCE CODE (base commit)", base your patch on those exact files and line numbers. Use the exact file paths from the file tree and the line numbers from the selected files when writing hunk headers. Do NOT guess file paths or line numbers — only reference files and lines that are actually present in the provided source code.`

b. Immediate-emit — the file says `Output ONLY valid JSON` (HEAD line 9), but tool mode uses
   `executor_tools.md`, which exists. Not a live defect.

c. Contradicts system prompt — not in practice.

d. Role: `executor` (`src/agents/executor_agent.py:8`).

### 2.6 `executor_tools.md`

**Verdict: `OK`.** No dead reference. Instructs reading before editing (line 12-13: `The plan
is a hypothesis — the code is the truth`).

### 2.7 `direct_prompt.md`

**Verdict at HEAD: `HAS DEAD REFERENCE`.** **Verdict now: same (one dead ref remains).**

a. **Dead reference — YES, HEAD line 9:**

> `If source code is provided below under "SOURCE CODE (base commit)", base your fix on those exact files and line numbers. Use the exact file paths shown in the file tree and the line numbers shown in the selected files when writing your patch hunk headers. Do NOT guess file paths or line numbers — only reference files and lines that are actually present in the provided source code.`

b. Immediate-emit — the file says `Output ONLY valid JSON` (HEAD line 11), but tool mode uses
   `direct_prompt_tools.md`, which exists. Not a live defect.

d. Role: `direct` (`src/agents/direct_agent.py:8`).

### 2.8 `direct_prompt_tools.md`

**Verdict: `OK`.** No dead reference. Full edit-then-diff workflow, explores before editing.

### 2.9 `shared_static.md`

**Verdict: `HAS DEAD REFERENCE` — and this one is LIVE.**

a. **Dead reference — YES, line 27-29:**

> `8. Use the EXACT file paths and line numbers shown in the provided source. Do NOT`
> `   guess file paths or line numbers — only reference files and lines that are`
> `   actually present.`

b. Immediate-emit — line 32: `Output ONLY valid JSON. Do NOT wrap it in markdown code blocks.`
   This is a patch-format preamble. For `direct` and `executor` it is appropriate; for
   `planner` and `reviewer` (read-only roles) it is not.

c. **Contradicts system prompt — YES, for the read-only roles.** The header opens by telling
   the model its job is `to analyze a bug report and produce a correct, minimal code change as
   a unified diff`, then gives a full "Hard rules for the patch field" section — addressed to a
   role that `READONLY_TOOL_SYSTEM_PROMPT` says may not modify files.

d. **Role: ALL FOUR.** `src/agents/base.py:19` loads it once; `base.py:95-107` prepends it to
   every request when `PROMPT_CACHE_LAYOUT` is on. **It is on:**

```
.env:94  PROMPT_CACHE_LAYOUT=true
.env mtime 2026-09-30 22:08:56  <  pilot 23:52:55  -> active during the pilot
```

**Empirical proof it reaches the planner.** I rendered the planner's tool-mode prompt with the
real loader and config:

```
PROMPT_CACHE_LAYOUT = True
shared_static.md loaded: True (5769 chars)
total prompt chars: 7393
Does it contain the dead 'provided source' reference? True
```

So every planner/reviewer/executor/direct call in the pilot carried a 5.7 KB prefix about
patch-hunk counting and "the provided source" — including the read-only planner, which was
told to `produce a correct, minimal code change as a unified diff` while also being told (in
its base template, due to the missing variant) to `Output ONLY valid JSON`, while its system
prompt said to explore first. Three instructions, three different jobs.

---

## 3. Is SOURCE_CONTEXT dead? — **Yes, definitively. It is never injected.**

* `src/config.py:251` — `SOURCE_CONTEXT_ENABLED = _get_env("SOURCE_CONTEXT_ENABLED", "true")…`
  (defaults true, but the flag no longer does anything).
* `src/config.py:260-283` — `_warn_if_source_context_enabled()`. Exact message:

  > `SOURCE_CONTEXT_ENABLED=true has NO EFFECT: agents now gather evidence with tools, and Issue.to_agent_prompt() never injects a source snapshot. Set it to false to avoid confusion, or build the ablation explicitly.`

  Its docstring adds: `Issue.to_agent_prompt() now always returns the bare problem statement: the agent gathers evidence with tools, and a pre-injected snapshot would be a confound.`

* `src/models/issue.py:39-57` — `to_agent_prompt()` returns `self.to_prompt()` (instance id +
  problem statement only). Docstring: `Deliberately carries NO pre-selected source snapshot.`

* `src/source_context.py:184` — `build_source_context(...)` still exists but has **zero
  callers** on the prompt path. Grep for `build_source_context` across `src/` returns only
  this definition plus comments in `config.py` and `models/issue.py`.

* All four strategies pass `issue.to_agent_prompt()` (`direct_strategy.py:48`,
  `planning_strategy.py:47,61`, `review_strategy.py:111,125,145,179,212`). None call
  `build_source_context`.

* `.env:41` sets `SOURCE_CONTEXT_ENABLED=false`, so even the warning is suppressed.

**Conclusion:** no source code is injected into any prompt. Every `"SOURCE CODE (base
commit)"` / "provided source" / "file tree" / "selected files" phrase in the prompts refers to
a feature that cannot deliver. Those instructions are not merely inert — they tell the model
to use paths "shown below" that never arrive, which is an invitation to invent them.

**Contradiction with the task's framing:** `TASK_PROMPT_AUDIT.md` calls the `planner.md`
reference a "**dead feature** … may be". It is dead — confirmed — but it was **also live in
the pilot's prompt** via `shared_static.md`, which the task did not mention.

---

## 4. Why did the reviewer read code but the planner not?

Because in the pilot the reviewer had a tool variant and the planner did not.

| | `planner` | `reviewer` |
|---|---|---|
| `_tools.md` variant at HEAD | **ABSENT** | PRESENT |
| Prompt actually loaded in tool mode | `planner.md` (fallback) | `reviewer_tools.md` |
| Does that prompt mention tools? | no (`tool-mentions=[]`) | yes (`read_file`, `grep`, `git_diff`) |
| Says "Output ONLY valid JSON" as the whole task? | **yes** | only as the final step, after 4 exploration steps |
| Tool calls observed (pilot) | **0 in 7 of 10 planner runs** | 7–23 per run, never 0 |

The reviewer's prompt (`reviewer_tools.md:14-24`) is the counter-example that proves the
mechanism — it contains the instruction the planner was missing:

> `1. Use read_file and grep to read the code the patch changes AND the code that calls it.`
> `2. Establish the MECHANISM: say, from code you have actually read, why the bug happened…`
> `3. Verify the mechanism INDEPENDENTLY of the plan…`
> `5. Finish by replying with ONLY this JSON (no tool call, no markdown fence):`

Same JSON-at-the-end discipline, but exploration is steps 1-4 and JSON is step 5. `planner.md`
has no steps 1-4.

### Measured tool calls (pilot, `messages.jsonl`, `sender==role`, `kind==result`)

```
[planning] planner : 10914=0  10924=9  11001=0  11019=0  11039=2   -> 3 of 5 runs ZERO
[review]   planner : 10914=0  10924=0  11001=0  11019=5  11039=0   -> 4 of 5 runs ZERO
[review]   reviewer: 10914=10 10924=23 11001=22 11019=7  11039=9   -> 0 of 5 runs ZERO
[planning] executor: 14/14/38/55/12                                -> never zero
[review]   executor: 8/13/27/20/8                                   -> never zero
[direct]   direct  : 13/17/9/25/6                                   -> never zero
```

**The task states "the planner agent makes ZERO tool calls in 6 of 9 runs".** The artifacts
contain **10 planner runs across the two strategies that use a planner**, and **7 of 10 are
zero** (3 in `planning` + 4 in `review`). The 9/6 figure does not match the artifact contents.
I report the numbers I measured; I cannot reconcile the task's count from the data on disk.

### The zero-call trajectory is one turn, not a failed loop

```
django__django-10914/planning planner: turn 1 assistant finish='stop' tool_calls=0
```

`finish_reason='stop'` — the model answered immediately and stopped. It was never asked to
call a tool, so it did not decline to; the prompt told it to emit JSON.

### The two prompts, side by side

A planner run that called tools (`10924`) produced output that opens with its evidence:
`Based on my analysis of the source code: - django/db/models/fields/__init__.py lines
1664-1669…`. The zero-call run (`10914`) produced bare JSON with no preamble — exactly what
`planner.md` asks for.

### `affected_files` do exist in the checkout (task's final question)

Resolved `base_commit` per instance via the dataset loader (`datasets.repos` is keyed by
commit), then tested each path against `datasets/repos/django/django/<base_commit>/`:

| Instance | planner calls | `affected_files` | Exists? |
|---|---|---|---|
| django__django-10914 | 0 | `django/conf/global_settings.py` | **OK** |
| django__django-10924 | 9 | `django/forms/fields.py`, `django/db/models/fields/__init__.py` | **OK / OK** |
| django__django-11001 | 0 | `django/db/models/sql/compiler.py` | **OK** |
| django__django-11019 | 0 | `django/forms/widgets.py` | **OK** |
| django__django-11039 | 2 | `django/core/management/commands/sqlmigrate.py` | **OK** |

**Every path is real**, including the zero-call runs. So the zero-call planner did **not**
invent paths — the model knows these repos well enough to name correct files from memory. That
is worth stating plainly: the harm is not hallucinated paths in this pilot. The harm is that
the plan is unverified — `confidence` and `root_cause_hypothesis` are guesses dressed as
findings, and the downstream executor is told the plan is `a hypothesis — the code is the
truth` (`executor_tools.md:13`), so the executor re-derives the truth anyway. The planner's
contribution is decorative in exactly the runs where it skipped reading.

I could not verify whether the *line numbers* in those plans are correct — that requires
per-hunk comparison against each checkout, which is a larger job than this audit. Path
existence is verified; line-number accuracy is not.

---

## 5. Files needing a fix

**At HEAD (what the pilot ran):**

1. `src/prompts/planner.md` — missing tool variant ⇒ planner got a no-tool prompt. **Root
   cause of the pilot finding.**
2. `src/prompts/planner.md:15`, `reviewer.md:20`, `executor.md:15`, `direct_prompt.md:9`,
   `shared_static.md:27` — dead `SOURCE CODE (base commit)` references.

**Still needing a fix in the current tree** (the partner fix removed the dead refs from the
four base prompts but did **not** touch `shared_static.md`):

3. **`src/prompts/shared_static.md:27-29`** — dead reference still live on every request.
4. **`src/prompts/shared_static.md` (whole file, for read-only roles)** — tells `planner` and
   `reviewer` to `produce a correct, minimal code change as a unified diff`, contradicting
   their mandate. This is a live conflict, not a dead reference.
5. **`src/agents/base.py:59-67`** — the `or self.template` silent fallback. The uncommitted
   edit adds a `warnings.warn`, which is a real improvement, but a warning is not a gate.

**Verdict table (both states):**

| File | HEAD (pilot) | Working tree now |
|---|---|---|
| `planner.md` | HAS CONFLICT + HAS DEAD REFERENCE | OK (refs removed) |
| `planner_tools.md` | *absent* | OK (new) |
| `reviewer.md` | HAS DEAD REFERENCE | OK |
| `reviewer_tools.md` | OK | OK |
| `executor.md` | HAS DEAD REFERENCE | OK |
| `executor_tools.md` | OK | OK |
| `direct_prompt.md` | HAS DEAD REFERENCE | OK |
| `direct_prompt_tools.md` | OK | OK |
| `shared_static.md` | **HAS DEAD REFERENCE + HAS CONFLICT** | **HAS DEAD REFERENCE + HAS CONFLICT (unchanged)** |

---

## 6. Could not verify / explicitly unresolved

1. **The task's "6 of 9" zero-call figure.** I measure 7 of 10 planner runs (10 = 5 planning
   + 5 review). I cannot reproduce 6/9 from `results/EXP-20260930-332`. Either the task counted
   only `planning` (3 of 5) or a different denominator; I state the measured numbers and do not
   guess which is intended.

2. **Whether the pilot's prompt actually differed from HEAD's files.** I prove the *files* at
   HEAD lacked `planner_tools.md`, and that the loader therefore falls back. I did **not** find
   a captured request/prompt dump in the pilot artifacts (searched: no `*prompt*`/`*request*`
   files under `results/EXP-20260930-332`). The reasoning is structural — loader + missing
   file + observed zero calls — not a byte-comparison of the sent prompt. A prompt dump would
   close this; there is none.

3. **Whether `shared_static.md` was in the pilot's planner prompt specifically.** I confirm
   `PROMPT_CACHE_LAYOUT=true` in `.env` predates the pilot, that `_wrap()` prepends the header
   for every role, and I reproduced the concatenation. I did not capture the wire request, so
   I infer from code + config, not from the artifact.

4. **Line-number accuracy of the plans.** Path existence verified (§4); line scopes not.

5. **`confidence` values** the planner emitted in the zero-call runs — not checked for
   whether the model self-reported `Low`, which would be a mitigating signal.

6. **Effect on patch quality.** This audit covers prompts only. Whether the zero-call planner
   degraded `planning`/`review` outcomes relative to `direct` is an evaluation question, not
   answerable from prompts. Note the pilot's own manifest reports all 15 runs
   `PATCH_GENERATED / VALID / APPLYABLE` — i.e. the sweep looked healthy while 7 planner runs
   never read code, which is the "plausible-looking output" failure mode.

---

## 7. Reproduce

```bash
# Inventory + mapping + tool-variant resolution
python .audit_templates.py        # loader: which prompt each role gets
python tools/check_prompt_variants.py   # partner's checker (already in tree)

# Dead references at HEAD vs now
python .audit_lines.py

# Measured tool calls from the pilot artifacts
python .audit_toolcalls.py

# Planner messages: zero-call vs calling
python .audit_planner_msgs.py
python .audit_reasoning.py

# Rendered planner prompt (proves shared_static.md reaches it)
python .audit_render.py

# affected_files existence at base_commit
python .audit_affected3.py
python .audit_getcommits2.py

# planner vs reviewer prompt diff
python .audit_diff.py
```

Scratch scripts are `.audit_*` (gitignored). **No file in `src/`, `tests/`, `tools/`, or
`results/` was modified by this audit.** The working-tree changes listed in §0 (`planner.md`,
`reviewer.md`, `executor.md`, `direct_prompt.md`, `base.py`, `planner_tools.md`) were already
present when I started and are not mine.
