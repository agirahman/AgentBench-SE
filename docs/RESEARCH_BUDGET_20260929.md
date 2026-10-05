# RESEARCH — Turn/step budget in agentic SWE benchmarks

**Date:** 2026-09-29
**Task:** `.commandcode/prompts/research-budget.md` (read-only research; no experiments run, nothing under `src/`, `tests/`, `tools/`, `results/` modified)
**Method:** primary sources only — repo source files pinned by tag, paper HTML on arXiv, official docs. Every claim carries a URL. Verified claims are separated from inference. Where a value could not be confirmed, this document says **not found** rather than guessing.

> Note on tooling: `web_search`/`web_fetch` returned HTTP 400 "insufficient credits" for this session, so all sources were retrieved with `curl` into `.research_cache/` and read locally. The URLs are unchanged; only the fetch mechanism differs.

---

## 1. Budget mechanisms across systems

| System | Limit mechanism | Parameter name | Default | Per what | Source URL |
|---|---|---|---|---|---|
| **SWE-agent** | cost cap (primary) | `per_instance_cost_limit` | **$3.0** | per instance (task) | https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/models.py |
| SWE-agent | total-run cost cap | `total_cost_limit` | `0.0` (disabled) | per run | same file |
| SWE-agent | API-call cap | `per_instance_call_limit` | `0` (disabled) | per instance | same file |
| SWE-agent | error-correction requeries | `max_requeries` | `3` | per step | https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/agents.py |
| SWE-agent | **no** step/turn cap found | — | — | — | default config has no `max_steps`: https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/config/default.yaml |
| **mini-SWE-agent** (library default) | step cap + cost cap, both disabled in the template | `step_limit` / `cost_limit` | `0` / `0.` | per task | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/default.yaml |
| **mini-SWE-agent** (agent class) | dataclass defaults | `step_limit` / `cost_limit` / `wall_time_limit_seconds` / `max_consecutive_format_errors` | `0` / **`3.0`** / `0` / `3` | per task | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/agents/default.py |
| **mini-SWE-agent** (SWE-bench config) | step cap + cost cap, both active | `step_limit` / `cost_limit` | **`250`** / **`3`** | per task (whole run) | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/benchmarks/swebench.yaml |
| mini-SWE-agent | per-command timeout | `timeout` (environment) | `60` s | per command | same swebench.yaml |
| **OpenHands** (v0.30.0 and v1.0.0) | iteration cap | `max_iterations` | **`500`** (`OH_MAX_ITERATIONS = 500`) | per task (conversation) | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/1.0.0/openhands/core/config/config_utils.py |
| OpenHands | dollar cap | `max_budget_per_task` | `None` (**no budget limit by default**) | per task | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/1.0.0/openhands/core/config/app_config.py |
| OpenHands | CLI override | `--max-iterations` / `--max-budget-per-task` | falls back to config | per task | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/1.0.0/openhands/core/config/utils.py |
| OpenHands | cap is **extended** when the user speaks (non-headless only) | `state.max_iterations = state.iteration + _initial_max_iterations` | — | per task | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/0.30.0/openhands/controller/agent_controller.py |
| **SWE-bench Pro** (2025) | turn cap, stated in the paper | "maximum of **200 turns**" | 200 | per task | https://arxiv.org/html/2509.16941v1 (§"Evaluation settings") |
| SWE-bench Pro | scaffold used for all results | SWE-Agent | — | — | same section |
| **SWE-Lancer** (2025) | **no turn/step cap found**; single attempt | pass@1 | — | per task | https://arxiv.org/html/2502.12115v1 (§2); §A.5 scaffold details |
| **MLE-bench** (2024/25) | wall-clock runtime | "maximum of **24 hours**" | 24 h | per competition | https://arxiv.org/html/2410.07095v6 (§3); https://raw.githubusercontent.com/openai/mle-bench/main/README.md |
| **AutoCodeRover** (2024) | **no turn cap found**; cost reported per task | — | (reports $0.435–$1.304/task, 48.3–67 iterations/task as *outcomes*, not caps) | per task | https://arxiv.org/html/2404.05427v2 (§5, Fig. 6, §"Detailed Comparison with Swe-agent") |
| AutoCodeRover (baseline comparison) | cost budget used when re-running SWE-agent | "two USD as cost budget … per task instance" | $2 | per task | same source |
| **Agentless** (2024) | **not agentic** — no loop, no step budget | — | $0.70 total on SWE-bench Lite | per task | https://arxiv.org/abs/2407.01489 (abstract) |
| **CodeAct** (2024) | multi-turn interaction, no published cap found | — | — | — | https://arxiv.org/abs/2402.01030 (abstract) |
| **SWE-bench harness** | **no agent budget at all** — evaluates submitted patches | — | — | — | https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/evaluation.md |

### Key observations from the table

1. **The dominant mechanism is a dollar cap, not a turn cap.** Both SWE-agent and mini-SWE-agent bound a task at **$3.0**. This is stated identically in two independent codebases (`sweagent/agent/models.py`, `minisweagent/agents/default.py`).
2. **Where a turn cap exists it is large.** mini-SWE-agent's SWE-bench config uses `250`; OpenHands uses `500`; SWE-bench Pro uses `200`. Our pool of 40 is 5–12× smaller than every published turn cap.
3. **Every one of these is per-task, not per-agent.** No reference implementation splits a turn budget across sub-agents. Our `ToolTurnBudget.share()` (13+13+14 for review) has no analogue in the literature — it is an artifact of our architecture, not a standard practice.
4. **Agentless is not comparable.** It is explicitly non-agentic ("without letting the LLM decide future actions or operate with complex tools"), so its cost figure says nothing about step budgets.
5. **OpenHands' cap is soft.** `_initial_max_iterations` is re-added to the current iteration every time the user sends a message (non-headless mode), i.e. the 500 is a per-turn-of-dialogue allowance, not a hard task budget.

---

## 2. Is the number justified?

**Mostly not — and the few places where it is, the justification is a resource-scaling experiment rather than an ablation of the step cap.**

**Documented facts:**

- **MLE-bench is the only system found that publishes an accuracy-vs-budget curve.** It runs dedicated experiments on "scaling resources for agents, including scaling agent runtime, hardware resources, and pass@k attempts" (https://arxiv.org/html/2410.07095v6 §1, contributions list). Reported results: GPT-4o scores **8.7% given 24 hours** per competition vs **11.8% given 100 hours** — i.e. ~4× more wall-clock buys +3.1 pp. Separately, o1-preview's medal rate **doubles from 16.9% (pass@1) to 34.1% (pass@8)**. Their default of 24 h is a stated benchmark convention, not a tuned optimum, and the README warns that reducing it "risks degrading the performance of your agent."
- **AutoCodeRover reports iterations as an outcome, not a budget.** Its table lists ACR-lite at 48.3 iterations / $0.435 per task and ACR-all at 67 iterations / $1.304 (https://arxiv.org/html/2404.05427v2 Fig. 6). No cap is stated; the numbers describe what the search happened to consume.
- **SWE-bench Pro's 200 turns** is stated as an evaluation setting with no justification or ablation (https://arxiv.org/html/2509.16941v1).
- **No published accuracy-vs-step-budget ablation for SWE-bench itself was found.** Not in the SWE-bench repo docs, not in the SWE-agent docs, not in mini-SWE-agent's docs.

**Inference (clearly labelled):** the $3.0 cost cap in both SWE-agent and mini-SWE-agent looks like a convention propagated between the two projects (same authors, mini-SWE-agent explicitly supersedes SWE-agent), not an empirically derived constant. The cost cap and the turn cap are substitutes: bounding dollars bounds tokens, which bounds turns implicitly at whatever the model's per-step token cost happens to be — so neither project needed to pick a "right" turn number.

**Contradicting signal from our own data** (already measured, not re-run): on EXP-20260928-003 the 26–35-turn bucket resolved 57% vs 100% for the cheaper 0–25 buckets, and the earlier 60-vs-40 ablation showed direct resolving 2/3 at both budgets at ~4× cost. So for our model and harness, *raising the budget is not obviously the fix* — which is precisely why the number cannot be justified a priori and must be measured.

---

## 3. Truncation reporting conventions

**Documented facts (SWE-bench official evaluation guide, https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/evaluation.md):**

The harness defines distinct counters — `resolved`, `unresolved`, `empty patches`, `errors`, `likely infrastructure failures`, `ambiguous failures` — and states:

> "Resolved plus unresolved is how many were actually graded. The two failure lines are counted inside unresolved and errors as well, so they never remove anything from the total."

A run that times out is called out explicitly as a usual cause of `Instances with errors` ("a patch that did not apply, a container that did not start, or a run that timed out"), and the guide notes that a non-zero error count coexists with `0 failed` because "a failed evaluation is not a crash: the harness catches the problem, writes it to the log, and carries on."

**Agent-side truncation is reported via `exit_status`, not a boolean:**

| System | Truncation signal | Values | Source |
|---|---|---|---|
| mini-SWE-agent | exception → `exit_status` in the trajectory | `LimitsExceeded` (step or cost), `TimeExceeded`, `RepeatedFormatError` | `agents/default.py`; serialized via `serialize()` → `info.exit_status`, `info.model_stats.instance_cost`, `api_calls` |
| SWE-agent | `exit_status` string | `exit_cost`, `exit_context`, `exit_command_timeout`, `exit_total_execution_time`, `exit_format`, `exit_api`, `exit_environment_error`, `exit_error`, `exit_forfeit` | `sweagent/agent/agents.py` |
| SWE-bench harness | no `budget_truncated` field | — | evaluation guide |

**The convention, therefore, is: keep the truncated run in the denominator, label it, and never silently drop it.** Neither system excludes or auto-re-runs a truncated task. Notably SWE-agent *autosubmits* the current diff on most errors (`attempt_autosubmission_after_error`), so a cost/time cutoff still produces a gradable patch — "terminated" and "no output" are different states.

**Not found:** any harness that publishes an aggregate "how many runs hit the limit" count as a first-class metric. The information exists per-instance (`exit_status` / `run_instance.log`) but is not surfaced in the headline table. That gap is exactly where our "budget-truncated: N of 30" row would go.

---

## 4. Principled alternatives to a fixed number

### 4.1 METR's 50%-task-completion time horizon

**Documented fact** (https://arxiv.org/abs/2503.14499, *Measuring AI Ability to Complete Long Tasks*, Kwa et al., 2025):

> "We propose tracking AI progress over time using the task completion time horizon: the duration of tasks that models can complete at a certain success probability."

Method, verbatim from the paper: (1) build a 170-task suite, (2) time humans and run AI agents, (3) **"fit a logistic model to find the time horizon at which each AI agent has a 50% chance of success"**, and plot that against model release date. Reported headline: Claude 3.7 Sonnet has a 50% time horizon of ~50 minutes, with the horizon doubling roughly every seven months since 2019 (https://arxiv.org/abs/2503.14499 abstract).

**The transferable move is the reporting shape, not the unit.** METR replaces "score at one budget" with "the budget at which the system crosses 50%", which converts an arbitrary constant into a measured property. The unit is human task duration, not agent steps, so our version has to substitute a turn budget for the time axis.

### 4.2 Accuracy-vs-budget curves (resource scaling)

MLE-bench is the closest published example in an SWE-adjacent benchmark: explicit sections "Varying amount of compute available" and "Increasing time available", with pass@k scaling reported as a first-class result (https://arxiv.org/html/2410.07095v6). Its shape — sub-linear returns (24 h → 100 h buys +3.1 pp) and large gains from *repeated attempts* rather than longer single attempts — is directly relevant to our decision.

### 4.3 Compute-controlled evaluation

**Documented facts:** MLE-bench asks submitters to report agent resources and not stray from defaults ("Agent resources — not a strict requirement of the benchmark but please report if you stray from these defaults!"), and pins a hardware envelope per run (36 vCPUs / 440 GB RAM / A10 GPU). AutoCodeRover compares itself to SWE-agent only at matched cost ($2 per task instance), and Agentless reports $0.70 as part of its headline result. Both SWE-agent and mini-SWE-agent record `instance_cost`, `tokens_sent`, `tokens_received`, `api_calls` per instance.

**Inference:** the field's de-facto fairness rule is *report cost/tokens alongside accuracy, and compare systems at matched budget*. No leaderboard found normalises the score itself by cost.

**Not found:** a leaderboard that ranks by accuracy-per-dollar as the primary metric.

---

## 5. What AgentBench-SE should adopt

### A. Report truncation as a first-class count — **adopt now**

Add a `truncated` column to the CSV export and a "budget-truncated: N of 30" row to every result table, per the SWE-bench convention (keep in the denominator, label, state that the outcome is not attributable to the strategy). Detection already exists in `tools/analyze_budget_binding.py`; it is simply not recorded in the artefact. On EXP-20260928-003 this is 6 of 68 acts, and 1 of those 6 resolved anyway — proof that truncation ≠ failure and must be a class, not a deletion.

### B. Keep the budget per-strategy, stop treating the per-act split as a scientific parameter — **adopt now**

No reference implementation splits a turn budget per agent (§1, observation 3). `Config.TOTAL_TOOL_TURNS` (default 60) is the right knob; the 13+13+14 division in `src/agents/budget.py` must be described as an implementation detail, generous enough never to bind a converged act. Set `TOTAL_TOOL_TURNS` above the observed converged maximum with headroom, and report the split in the appendix, not the method.

### C. Add a cost cap alongside the turn pool — **adopt now**

This is the largest structural gap: SWE-agent and mini-SWE-agent both bound a task by dollars (`cost_limit` / `per_instance_cost_limit` = $3.0), we bound turns and not cost. A strategy that spends 2× tokens for the same turns is unconstrained, and review already cost 2× planning's tokens. Add `COST_LIMIT_USD` to `src/config.py` next to `TOTAL_TOOL_TURNS`, record it per run, and report actual cost per strategy — the token/cost columns already exist in `src/experiments/csv_exporter.py`.

### D. Produce the accuracy-vs-budget curve — **adopt for the thesis write-up**

Run the 10 django instances at 3 budget levels (e.g. 40 / 80 / 160 turns) and plot resolution rate against budget, per strategy. That is the METR-shaped answer to "why this number?" and it converts the weakest part of the methodology into a measured result. Report the 50%-crossing budget per strategy if the data supports it; if it does not, report the curve as flat — that is also a finding, and it is consistent with our current 60-vs-40 ablation showing no gain.

### E. Do not normalise away the difference, and do not use the oracle — **not applicable, because…**

Two things we should explicitly *not* copy. (i) Cost normalisation of the score is not a published leaderboard practice (§4.3) — report cost *beside* accuracy, do not divide. (ii) Using `FAIL_TO_PASS`/`test_patch` to bound or grade the reviewer would invalidate any SWE-bench-comparable claim; the benchmark withholds them by design. The reviewer's lack of discriminative power is a finding to report (see `docs/RESEARCH_VERIFIER_20260929.md`), not a gap to patch with the oracle.

---

## 6. Open items / not verified

- **OpenHands' current (post-restructure) default.** Verified at tags `0.30.0` and `1.0.0` (`OH_MAX_ITERATIONS = 500`). The current `main` tree 404s on all historical Python paths — it is now "Agent Canvas" with the Python agent moved to `OpenHands/software-agent-sdk` (https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md). Treat 500 as "last verified Python release", not "current main".
- **SWE-Lancer's turn cap.** The paper describes a "basic scaffold" and single attempt (pass@1); no turn, token, or time cap was found in the main text or §A.5. Appendix A.5 does pin the VM (2 vCPU / 8 GB) and container (192 GB max memory), which is a compute envelope rather than a step budget.
- **A published accuracy-vs-step-budget ablation for SWE-bench.** Not found. MLE-bench (§3.3–3.4) and METR are the closest treatments.
- **CodeAct's turn limit.** The paper describes multi-turn interaction and reports +20% success over JSON/text action formats, but no numeric cap was found in the abstract or main text.

---

## Appendix — sources fetched (cached in `.research_cache/`)

| Source | URL |
|---|---|
| SWE-bench evaluation guide | https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/evaluation.md |
| SWE-bench datasets guide | https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/datasets.md |
| SWE-agent model limits | https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/models.py |
| SWE-agent termination logic | https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/agents.py |
| SWE-agent default config | https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/config/default.yaml |
| mini-SWE-agent agent class | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/agents/default.py |
| mini-SWE-agent SWE-bench config | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/benchmarks/swebench.yaml |
| mini-SWE-agent default config | https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/default.yaml |
| OpenHands 1.0.0 config utils | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/1.0.0/openhands/core/config/config_utils.py |
| OpenHands 1.0.0 app config | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/1.0.0/openhands/core/config/app_config.py |
| OpenHands 0.30.0 controller | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/0.30.0/openhands/controller/agent_controller.py |
| OpenHands current README | https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md |
| SWE-bench Pro (paper) | https://arxiv.org/html/2509.16941v1 |
| SWE-Lancer (paper) | https://arxiv.org/html/2502.12115v1 |
| MLE-bench (paper) | https://arxiv.org/html/2410.07095v6 |
| MLE-bench README | https://raw.githubusercontent.com/openai/mle-bench/main/README.md |
| AutoCodeRover (paper) | https://arxiv.org/html/2404.05427v2 |
| Agentless (abstract) | https://arxiv.org/abs/2407.01489 |
| CodeAct (abstract) | https://arxiv.org/abs/2402.01030 |
| METR time horizons (abstract) | https://arxiv.org/abs/2503.14499 |
| METR time horizons (full text) | https://arxiv.org/html/2503.14499v1 |
