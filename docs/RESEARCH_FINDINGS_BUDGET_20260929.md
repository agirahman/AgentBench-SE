# Research Findings — How SWE-bench-style systems handle the step/turn budget

**Date:** 2026-09-29
**Author:** main agent (WS-lead), with partner research in
`docs/RESEARCH_BUDGET_20260929.md` and `docs/RESEARCH_VERIFIER_20260929.md`
**Question (user):** how do established SWE benchmarks handle the budget problem,
and can we apply it here?

---

## TL;DR — the three findings that matter

1. **Nobody uses a per-act turn cap like ours.** SWE-agent and mini-SWE-agent
   bound a run by **cost in dollars** (`cost_limit: 3.0`) and, when they do use
   steps, use a **very large** per-task number (mini-SWE-agent on SWE-bench:
   **`step_limit: 250`**). Our 40-turn strategy pool is ~6× tighter than the
   reference implementation and is expressed per-act, which no reference does.
2. **SWE-bench's own harness already solves the reporting half of our problem.**
   It reports `resolved` / `unresolved` / `errors` as **separate counts** and
   states explicitly that the failure lines are "counted inside unresolved and
   errors as well, so they never remove anything from the total." We have been
   collapsing everything into a single resolution rate.
3. **METR's methodology is the principled answer to "what number?".** Instead of
   picking one budget and reporting accuracy at it, measure **accuracy as a
   function of budget** and report the point where it crosses 50%
   ("50%-task-completion time horizon"). That converts an arbitrary constant
   into a measured property of the system — which is a defensible thesis result.

---

## 1. What the reference implementations actually do

| System | Mechanism | Parameter | Default | Per what | Verified from |
|---|---|---|---|---|---|
| mini-SWE-agent (SWE-bench config) | step cap **and** cost cap | `step_limit` / `cost_limit` | **250** / **$3** | per task (whole run) | `src/minisweagent/config/benchmarks/swebench.yaml` L112-113 |
| mini-SWE-agent (library default) | same, both disabled | `step_limit` / `cost_limit` | 0 / 0.0 | per task | `src/minisweagent/config/default.yaml` |
| mini-SWE-agent (agent class) | dataclass defaults | `step_limit` / `cost_limit` / `wall_time_limit_seconds` | 0 / **3.0** / 0 | per task | `src/minisweagent/agents/default.py` L26-30 |
| SWE-agent | **cost** cap, no step cap | `per_instance_cost_limit` / `total_cost_limit` / `per_instance_call_limit` | **$3.0** / 0.0 / **0** | per instance / per run / per instance | `sweagent/agent/models.py` L73-76 |
| SWE-agent (check) | enforcement point | — | — | — | `sweagent/agent/agents.py`: `if 0 < self.config.per_instance_cost_limit < self.stats.instance_cost` |
| OpenHands | `max_iterations` | see partner report | (partner verifying — repo restructured to `OpenHands/OpenHands`, old path 404) | per task | partner deliverable |

**Key detail — `step_limit: 0` means NO LIMIT.** In mini-SWE-agent the step cap
is *disabled* by default and only the SWE-bench config turns it on, at 250. The
cost cap ($3) is the real bound. This is the opposite of our design: we bound
turns tightly and don't bound cost at all.

**Our numbers for comparison:**

| | Ours (AgentBench-SE) | mini-SWE-agent (SWE-bench) | SWE-agent |
|---|---|---|---|
| Turn/step budget | 40 total, split 40 / 20+20 / 13+13+14 | 250 per task | none (cost only) |
| Cost budget | none | $3 | $3 |
| Expressed per | **act** (agent) | task | task |

Our budget is **6.25× smaller** than the reference and — more importantly — it is
split **per act**, so a strategy with more agents gets less room per agent. No
reference implementation does this. That is the structural bug we found by
measurement (review's executor got 13 turns vs direct's 40).

**Sources (all fetched and cached in `.research_cache/`):**
- `https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/benchmarks/swebench.yaml`
- `https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/default.yaml`
- `https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/agents/default.py`
- `https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/models.py`

---

## 2. How SWE-bench reports runs that fail for non-patch reasons

This is the direct answer to "what do we do with a run we cut off?" The official
evaluation guide defines **distinct** counters:

| Field | Meaning |
|---|---|
| Instances resolved | The patch made the required tests pass |
| Instances unresolved | The tests ran, but did not pass |
| Instances with empty patches | The prediction was empty, so nothing ran |
| Instances with errors | **No result could be produced at all** |
| Instances with likely infrastructure failures | Failed for an environment reason |
| Instances with ambiguous failures | Could be environment or patch |

And the rule that settles our question:

> "Resolved plus unresolved is how many were actually graded. The two failure
> lines are counted inside unresolved and errors as well, **so they never remove
> anything from the total**."

**Interpretation for us:** the convention is *report the failure class separately,
never drop it from the denominator*. A run cut off by a turn cap is exactly the
"ambiguous failure" category — the harness cannot tell whether the patch was bad
or the budget was. So the correct treatment is:

- keep it in the denominator (do not silently exclude it),
- report it under its own heading (e.g. "budget-truncated: 6 of 30"),
- and state that its outcome cannot be attributed to the strategy.

**This is what our EXP-003 write-up was missing.** We reported 8/8/6 as if all 30
runs were clean measurements. Under the SWE-bench convention we must add a
truncation count and split the failures.

**Source:** `https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/evaluation.md`
(§"What the counts mean"), and the FAQ's metric list:
`https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/faq.md`

---

## 3. The principled alternative to a magic number (METR)

METR's paper *Measuring AI Ability to Complete Long Software Tasks* introduces the
**50%-task-completion time horizon**: "the time humans typically take to complete
tasks that AI models can complete with 50% success rate."

The methodological move is what matters for us: **do not report one accuracy at
one arbitrary budget. Report accuracy as a function of task length/budget, and
characterise the system by where it crosses 50%.**

Applied to AgentBench-SE, that becomes: instead of arguing whether 40 or 250 is
"the right" turn budget, measure **resolution rate as a function of turn budget**
(e.g. 20 / 40 / 80 / 160 turns) and report the curve, plus the budget at which
each strategy crosses 50% on our instance set. That turns an arbitrary constant
into a measured property, and it is exactly the ablation the thesis needs.

Reported headline from the paper (for context, not our number): Claude 3.7 Sonnet
had a 50% time horizon of ~50 minutes on their task suite, with the horizon
doubling roughly every seven months since 2019.

**Source:** `https://arxiv.org/abs/2503.14499` (abstract, cached)

---

## 4. What our own data says about the budget (measured, not assumed)

I built `tools/analyze_accuracy_vs_budget.py` and `tools/analyze_budget_binding.py`
to answer this from our own runs rather than by analogy.

**Binding rate (EXP-20260928-003, 30 runs, 68 acts):**

| Strategy | Acts | Capped | Binding rate |
|---|---|---|---|
| direct | 10 | 1 | 10.0% |
| planning | 20 | 1 | 5.0% |
| review | 38 | 4 | 10.5% |
| **Total** | **68** | **6** | **8.8%** |

**62 of 68 acts (91%) stopped on their own** — the cap did not shape them.

**Turns used by converged runs:** min 5, median 22, p90 34, **max 40**.
The max sits exactly at the pool size, which means the pool is binding for the
tail — it is not comfortably above what runs need.

**Accuracy by turns used (converged runs only):**

| Turns used | n | Resolved | Rate |
|---|---|---|---|
| 0-15 | 7 | 7 | 100% |
| 16-25 | 7 | 7 | 100% |
| 26-35 | 7 | 4 | **57%** |
| 36-45 | 3 | 3 | 100% |

**This does not show "more turns → better".** The 26-35 bucket is *worse* than
the cheaper buckets. Consistent with the earlier 60-vs-40 ablation in MEMORY.md
(direct resolved 2/3 at both budgets, at ~4× the cost for 60). So for our current
model and harness, **raising the budget is not obviously the fix** — but the
6 truncated runs still cannot be scored as clean measurements.

**Runs cut off, with outcome:**

| Strategy | Instance | Turns | Resolved |
|---|---|---|---|
| direct | django-11019 | 41 | ✗ |
| planning | django-11019 | 41 | ✗ |
| review | django-10914 | 42 | ✓ |
| review | django-10924 | 39 | ✗ |
| review | django-11001 | 40 | ✗ |
| review | django-11019 | 39 | ✗ |

1 of 6 truncated runs resolved anyway — so truncation is not automatically
failure, which is another reason to report it as a class rather than delete it.

---

## 5. What AgentBench-SE should adopt

**A. Report truncation as a first-class count (adopt now, cheap, no re-run).**
Add a "budget-truncated" row to every result table, per SWE-bench's convention:
keep the run in the denominator, label it, and state that its outcome is not
attributable to the strategy. Concretely: add `truncated` to the CSV export (we
already detect it in `tools/analyze_budget_binding.py` — the detection exists,
it is just not recorded in the artefact). This fixes the weakest part of the
EXP-003 write-up.

**B. Move the budget from per-act to per-task (adopt now).**
No reference implementation splits the budget per agent. Our split is what
starved review's revision act (1 turn) and gave direct 40 vs review's executor
13. Keep the strategy-wide pool (it fixed the direct-vs-review inequality) but
stop treating the per-act split as a scientific parameter — it is an
implementation detail that should be generous enough never to bind for a
converged act. Justification for the number: set it above the observed
converged max (40) with headroom, e.g. 80-100, and rely on the *cost* cap to
bound runaway runs the way the references do.

**C. Add a cost cap (adopt now).**
This is the biggest structural gap. SWE-agent and mini-SWE-agent both bound a run
by dollars ($3), not turns. We bound turns and don't bound cost — so a strategy
that spends 2× tokens for the same turns is unconstrained, and review already
cost 2× planning's tokens (291K vs 140K). Add `COST_LIMIT_USD` alongside the turn
pool. It also makes the RQ1 comparison fair, which is a thesis requirement.

**D. Produce the accuracy-vs-budget curve (adopt for the thesis write-up).**
Run the 10 django instances at 2-3 budget levels and plot resolution rate against
budget. That is the METR-style answer to "why this number?" and it converts the
weakest part of the methodology (an arbitrary constant) into a measured result.
Do this *after* the reserve fix is validated, and report the 50%-crossing point
per strategy if the data supports it.

**E. Do NOT give the reviewer the hidden test (not applicable — leakage).**
`FAIL_TO_PASS` is withheld by design; using it would invalidate the benchmark.
The correct move is what we already did: report the reviewer's lack of
discriminative power as a *finding* (see partner report on the verifier), not
patch it with the oracle. The SWE-bench counter convention in (A) is the
sanctioned way to report a component that cannot be verified.

---

## 6. Open items / what I could not verify

- **OpenHands' current default iteration cap.** The repo moved to
  `OpenHands/OpenHands` and is now largely TypeScript; the historical Python path
  404s. Delegated to a partner with instructions to pin a tag or report "not
  verified" rather than guess. Historical value (older releases) was
  `max_iterations = 500`, but I have **not** verified that against a current
  source, so it is excluded from the table above.
- **A published accuracy-vs-step-budget ablation curve for SWE-bench itself.**
  Not found in the sources I fetched. The METR paper is the closest rigorous
  treatment of "capability as a function of budget", and it measures human time
  rather than agent steps.
- **AutoCodeRover / Agentless limits.** Not yet checked in this pass; Agentless
  is largely non-agentic (it does not run a long tool loop), so its "budget" is
  not comparable. Partner may cover this.

---

## Appendix — tools added for this analysis

| Tool | Purpose |
|---|---|
| `tools/analyze_budget_binding.py` | capped acts / total acts per strategy; the binding rate |
| `tools/analyze_accuracy_vs_budget.py` | turns used by converged runs; accuracy by turn bucket; truncated-run table |
| `tools/analyze_revision_budget.py` | how the revision reserve splits across revision rounds |
| `tools/analyze_turns_per_act.py` | per-agent tool-call counts, to see where turns went |
| `tools/analyze_stopping_reason.py` | lists cap-hit acts (cut off) vs acts that ended by themselves |
| `tools/research_fetch.py` / `research_probe.py` / `research_extract.py` | fetch + cache + grep primary sources (web_search was out of credits) |
