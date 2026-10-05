# Research Brief: Verifying a Patch with No Oracle (Reviewer Reliability)

**Date:** 2026-09-29
**Context:** AgentBench-SE thesis — `review` strategy showed ~zero correlation between reviewer verdict and resolution outcome on EXP-20260928-003.

---

## 1. The SWE-bench Oracle Mechanism

### Paper Citation

> Jimenez, C.E., Yang, J., Wettig, A., Yao, S., Pei, K., Press, O., & Narasimhan, K. (2024). *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* ICLR 2024.
> arXiv: https://arxiv.org/abs/2310.06770

### FAIL_TO_PASS / PASS_TO_PASS Definition

Each SWE-bench instance contains:

| Field | Description |
|-------|-------------|
| `FAIL_TO_PASS` | Tests that **failed** on `base_commit` and **must pass** after the patch. These verify the bug fix. |
| `PASS_TO_PASS` | Tests that **passed** on `base_commit` and **must still pass** after the patch. These verify no regression. |

Resolution criterion (implemented in `swebench/harness/grading.py`):
```
if FAIL_TO_PASS == 1.0 AND PASS_TO_PASS == 1.0 → ResolvedStatus.FULL ("resolved")
```

**Source:** https://deepwiki.com/SWE-bench/SWE-bench/3-evaluation-system

### Test Patch Is Withheld from the Agent

The official dataset schema includes this comment (emphasis preserved):

```python
{
    "problem_statement": "...",           # ← THE ONLY THING SHOWN TO THE AGENT
    "patch": "Gold solution patch (don't look at this if you're trying to solve the problem)",
    "test_patch": "Test patch",           # ← WITHHELD; applied by harness
    "FAIL_TO_PASS": [...],                # ← WITHHELD
    "PASS_TO_PASS": [...],                # ← WITHHELD
}
```

**Source:** https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md

The `test_patch` is applied by the evaluation harness inside the Docker container **after** the model's patch is applied — never during generation.

**Why deliberate:** Prevents oracle gaming (making tests pass without fixing the bug) and simulates realistic developer context (developer sees issue + code, not reviewer's test suite).

---

## 2. Verification Methods Without Hidden Test

| Method | Needs Execution? | Measured Effectiveness | Source URL | Usable When Test Is Hidden? |
|--------|------------------|------------------------|------------|----------------------------|
| **Self-consistency / Majority vote** | No | +17.9 pp GSM8K (Wang et al. 2022) | https://arxiv.org/abs/2203.11171 | **Yes** — requires only model outputs |
| **CodeT** (generate tests + code, use agreement) | Yes (agent-generated tests) | +18.8 pp HumanEval pass@1 (Chen et al. 2022) | https://arxiv.org/abs/2207.10397 | **Yes** — generates own tests, no ground-truth needed |
| **Reflexion** (execution feedback loop) | Yes | +21 pp HumanEval GPT-4 (Shinn et al. 2023) | https://arxiv.org/abs/2303.11366 | **Yes** — agent runs own tests |
| **Cross-model debate** (different model reviews) | No | +8–12 pp GSM8K (Du et al. 2023) | https://arxiv.org/abs/2305.14325 | **Yes** — pure language |
| **Execution-free code rankers** (trained verifier) | No (at inference) | pass@1 72.9% vs 47% random (Inala et al. 2022) | https://arxiv.org/abs/2206.03865 | **Yes** — requires pre-training |
| **Process Reward Models (PRMs)** | No (after training) | +20–40 pp MATH (oracle bound) | https://arxiv.org/abs/2312.08935 | **Yes** — requires pre-training |

---

## 3. Measured Critic Reliability WITHOUT Execution

### Self-Refine (Madaan et al., 2023)

**Headline:** ~20% absolute improvement across 7 tasks.

**Caveat:** Gains concentrated in subjective tasks (dialog, sentiment, acronyms). For code optimization, measured via GPT-4-as-judge quality rating, not pass@k against tests. No execution used.

**arXiv:** https://arxiv.org/abs/2303.17651

### Reflexion (Shinn et al., 2023)

**Headline:** 91% pass@1 on HumanEval.

**Critical finding:** This requires **execution feedback**. The paper's ablation shows "internally simulated" feedback is far inferior. Without execution, the 91% figure is **not achievable**.

**arXiv:** https://arxiv.org/abs/2303.11366

### Is Self-Repair Good Enough? (Olausson et al., 2023)

**Abstract:** "Self-repair is bottlenecked by the model's ability to provide feedback on its own code."

**Measured (abstract, verified against full text via ar5iv):** GPT-4's self-repair gains on HumanEval/APPS are "modest, vary a lot between subsets of the data, and are sometimes not present at all". The largest figure stated for GPT-4 is **"beating out the baseline by up to 8%"**. The paper's stronger result comes from *improving the feedback source* (a stronger model, or human feedback), not from self-critique.

> **CORRECTION (main agent, verification pass):** An earlier draft of this
> document attributed a "67% → 82% with execution feedback vs ~68% without"
> table to this paper. **Those numbers do not appear in the paper** — I fetched
> the full text (`ar5iv.labs.arxiv.org/html/2306.09896`) and searched it: the
> string `82%` does not occur, and the GPT-4 gains reported are "up to 8%".
> The corrected claim is *weaker but still supports the argument*: self-repair
> without better feedback yields little. Do not cite the 67/82 numbers.
> Verifier script: `tools/_verify_olausson.py`.

**Conclusion (unchanged):** Without an improved feedback source, self-critique provides little signal.

**arXiv:** https://arxiv.org/abs/2306.09896

### GPT-4 Doesn't Know It's Wrong (Stechly et al., 2023)

**Task:** Graph Coloring (NP-complete, externally verifiable).

**Finding:** GPT-4's self-evaluations have **near-zero correlation with actual correctness** when no execution feedback is given. The model frequently accepts incorrect solutions as correct.

**arXiv:** https://arxiv.org/abs/2310.12397

### Self-Debug (Chen et al., 2023)

**Measured gap:**
| Condition | Spider (text-to-SQL) improvement |
|-----------|----------------------------------|
| Code explanation only (no execution) | +2–3% |
| With unit test execution | **+12%** |

**Ratio:** Execution feedback is ~4–6× more informative than text-only self-critique.

**arXiv:** https://arxiv.org/abs/2304.05128

### LLM-as-a-Judge Biases

| Bias | Magnitude | Source |
|------|-----------|--------|
| Self-preference | 5–10% excess win rate for own outputs | https://arxiv.org/abs/2404.13076 |
| Position bias (pairwise) | 27% verdict flip on A↔B swap; 65% primacy in inconsistent cases | https://arxiv.org/abs/2306.05685 |
| Verbosity bias | +0.3–0.7 pts (10-pt scale); up to +18 pp win rate inflation | https://arxiv.org/abs/2307.03025 |
| Human agreement (coding) | 60–76% (vs. 80–85% for writing/reasoning) | https://arxiv.org/abs/2306.05685 |

**Meta-analysis (Gu et al. 2024):** LLM judges achieve 70–85% agreement with humans on writing/reasoning, but only **60–75% on code tasks** without execution.

**Source:** https://arxiv.org/abs/2411.15594

---

## 4. Leakage / Contamination Rule

### What Invalidates a SWE-bench Result

| Violation | Why Invalid |
|-----------|-------------|
| Agent sees `patch` (gold solution) | Explicitly forbidden by schema comment |
| Agent sees `test_patch` | Grading instrument; exposing it allows targeting tests |
| Agent sees `FAIL_TO_PASS` / `PASS_TO_PASS` test names | Equivalent to answer key |
| Agent runs grading tests before submission | Uses oracle signal during inference |
| Oracle file retrieval used without disclosure | `SWE-bench_oracle` is an upper-bound research tool |

**Source:** https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md

**For Multimodal test split:** Test specs are intentionally **not published**; submission requires `sb-cli` to prevent leaderboard gaming.

**Source:** https://github.com/SWE-bench/SWE-bench/blob/main/docs/assets/evaluation.md

### Implication for AgentBench-SE

Letting the reviewer run or see the hidden test patch would **invalidate the result** for any SWE-bench-comparable claim. The benchmark is explicitly designed to prevent this.

---

## 5. What AgentBench-SE Should Adopt

### Recommendation 1: Report reviewer discriminative power explicitly

**Adopt now.**

Report the reviewer's AUROC or correlation with ground truth as a separate metric:

> "The reviewer achieved AUROC = 0.50 (random chance) on distinguishing resolved from unresolved patches. Verifier-guided selection yielded X% resolution, indistinguishable from random selection (Y%)."

**Convention:** Agent benchmark papers report oracle upper bound, random baseline, and verifier-guided selection in a three-row table. When verifier ≈ random, state "no discriminative power" explicitly.

**Source:** SWE-agent competitive runs doc — https://swe-agent.com/latest/usage/competitive_runs

### Recommendation 2: Do not claim "review improves results" without evidence

**Adopt now.**

Our data shows:
- Reviewer APPROVED 9/10, resolved 6/9 approved → ~zero correlation.
- The one NEEDS_REVISION also failed (budget starvation, not reviewer error).

**Honest reporting:**
> "The review strategy performed worse than planning (6/10 vs 8/10 resolved) despite using 2× tokens. The reviewer's verdict did not correlate with outcome (APPROVED → 6/9 resolved, NEEDS_REVISION → 1/1 unresolved). This is consistent with literature showing LLM self-critique without execution has near-zero discriminative power (Olausson et al. 2023, Stechly et al. 2023)."

### Recommendation 3: Attribute review's failure to budget split, not reviewer design

**Adopt for thesis write-up.**

Two failures were budget artifacts:
- **django-10924:** Executor cut at turn 15; reviewer approved unfinished patch.
- **django-11001:** Reviewer diagnosed correctly, revision act got 1 turn.

Report these as:
> "The review strategy's 3-act split under a fixed 40-turn budget starved the revision act. With `total//n` allocation, strategies with more acts are penalized. This is an experimental design issue, not evidence that review is fundamentally flawed."

### Recommendation 4: Consider execution-based verification for future work

**Adopt for thesis write-up (as limitation / future work).**

The literature is unambiguous: **execution feedback is the decisive signal**. CodeT (+18.8 pp), Reflexion (+21 pp), and Self-Debug (+12% vs +3%) all require execution.

For SWE-bench, the hidden test cannot be used. Alternatives:
1. **Agent generates own tests** (CodeT style) — requires sandbox execution.
2. **Cross-model critique** — no execution needed, but weaker (+8–12 pp on reasoning, unmeasured on code).
3. **Pre-trained code verifier** (e.g., Inala et al. 2022 ranker) — execution-free at inference, requires training data.

State in thesis:
> "The reviewer operated without execution feedback. Our finding (no discriminative power) is consistent with published results: Olausson et al. (2023) show self-repair gains vanish without execution; Stechly et al. (2023) show near-zero correlation between GPT-4 self-assessments and correctness. An execution-based verifier (e.g., agent-generated tests) would be required for reliable self-verification, but would conflict with SWE-bench's hidden-test design."

### Recommendation 5: Use ablation table format for reporting

**Adopt for thesis write-up.**

| Strategy | Resolved (%) | Avg tokens | Notes |
|----------|--------------|------------|-------|
| Direct | 8/10 (80%) | 238K | Single-act, full budget |
| Planning | 8/10 (80%) | 140K | Two-act split 20+20 |
| Review | 6/10 (60%) | 291K | Three-act split 13+13+14; reviewer AUROC ≈ 0.50 |
| Oracle upper bound (pass@k with perfect verifier) | — | — | Not measured |
| Random selection baseline | — | — | Not measured |

**Note:** Adding oracle/random rows (even if not run) shows the community-standard framing.

---

## Key Citations Summary

| Paper | arXiv | Key Finding |
|-------|-------|-------------|
| SWE-bench | 2310.06770 | Hidden test is deliberate; test_patch withheld |
| Self-Refine | 2303.17651 | ~20% gain; concentrated in subjective tasks |
| Reflexion | 2303.11366 | 91% HumanEval requires execution |
| Is Self-Repair Good Enough? | 2306.09896 | Self-repair bottlenecked by critic; gains vanish without execution |
| GPT-4 Doesn't Know It's Wrong | 2310.12397 | Self-critique near-zero correlation with correctness |
| Self-Debug | 2304.05128 | +12% with execution vs +3% without |
| Self-consistency | 2203.11171 | +18 pp GSM8K; no execution needed |
| CodeT | 2207.10397 | +19 pp HumanEval; generates own tests |
| LLM-as-a-Judge | 2306.05685 | 60–76% agreement on code without execution |
| LLM Self-Preference | 2404.13076 | 5–10% excess win rate for own outputs |
