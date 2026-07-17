# 🧠 AI Agent Memory — AgentBench-SE

**Last Updated:** 2026-07-17 14:59 UTC  
**Status:** Ready for 50-issue full run  
**Active Branch:** `16/feat/eval-toolchain`

---

## 📋 Current Project State

| Aspect | Status | Notes |
|--------|--------|-------|
| **Provider** | ✅ Done | OpenCode (deepseek-v4-flash) at https://opencode.ai/zen/v1 + Gemini + Groq |
| **Dataset** | ✅ Done | 50 issues balanced: django(10)+sympy(10)+scikit(10)+matplotlib(10)+requests(6)+seaborn(4) |
| **Difficulty** | ✅ Done | Domain-based: hard/medium/easy mapped in `models/issue.py` |
| **Auto-fix hunk** | ✅ Done | `_auto_fix_hunk_headers()` in swebench_adapter.py — recount @@ headers |
| **Enhanced logging** | ✅ Done | Phase-level detail: issue loaded → strategy init → API call → result |
| **Pricing** | ✅ Done | DeepSeek v4 flash: $0.14/M input (cache miss), $0.28/M output |
| **Per-experiment logs** | ✅ Done | Dual log: `logs/agentbench.log` (global) + `results/EXP-*/logs/experiment.log` (per-run) |

---

## 🔑 Key Decisions Made

| Decision | Date | Reason | Trade-off |
|----------|------|--------|-----------|
| OpenCode provider (not DeepSeek direct) | 2026-07-17 | User has OPENCODE_API_KEY + response_format=json_object support | Less direct control, forward to DeepSeek Zen |
| Random 5-10s delay per issue | 2026-07-17 | Rate limit safety for 150 executions | Slower total runtime (~12-20 min for 50 issues) |
| Auto-fix hunk headers instead of empty string | 2026-07-17 | Improve valid patch rate (~60% → ~85%), strong skripsi contribution | Extra processing, may mask actual AI failures |
| 50 issues balanced vs 50 from single repo | 2026-07-17 | Generalizability across domains for sidang | Less deep analysis per repo |
| Domain-based difficulty vs empirics-based | 2026-07-17 | Dosen will ask; objective criteria (framework vs lib) | May not reflect actual complexity |

---

## 🚀 Latest Commits (Branch: 16/feat/eval-toolchain)

```
8b8d695 feat: auto-fix hunk headers, 50 issues balanced, difficulty field, enhanced logging
fa8fdac fix: normalize double-escaped newlines properly regardless of real newlines present
494939e feat: random rate-limit delay 5-10s between issue runs
2dd6af0 feat: add deepseek-v4-flash pricing (.14/M input, .28/M output)
4350ffb feat: add --issues flag for testing subset of issues
cb590da fix: replace DeepSeek provider with OpenCode provider (zen/v1 + json_object)
690c415 feat: add per-experiment log sink to runner for reproducibility
abcd724 feat: deepseek provider + multi-repo sampling + savepoint/resume + per-strategy jsonl
```

---

## 📊 Test Results (Last Run)

**Test:** 1 issue, 3 strategies (2026-07-17, OpenCode)
- **Result:** ✅ Success
- **Output:** `results/EXP-20260717-004/`
- **Token cost:** planning strategy generated valid patch
- **Direct strategy:** patch empty (truncated/invalid)
- **Review strategy:** patch empty (failed reasoning)
- **Cost:** $0.0 (OpenCode Zen is free tier)

**Auto-fix hunk test:**
- Input: hunk with mismatch `-1,3/+1,3` (actual `-1,2/+1,3`)
- Output: ✅ Fixed header correctly

---

## 🎯 What's Ready

✅ **All 4 major items from PLAN.md:**
1. Auto-fix hunk headers in `swebench_adapter.py`
2. 50 issues (balanced repo distribution)
3. Difficulty field + domain mapping
4. Enhanced phase-level logging (logs include difficulty, tokens, cost, steps)

✅ **Per-experiment structure:**
- `results/EXP-{date}-{num}/`
  - `logs/experiment.log` — execution trace
  - `predictions/{direct,planning,review}.jsonl` — per-strategy predictions
  - `results.csv` — flattened metrics with `difficulty` column
  - `artifacts/{instance_id}/` — planner.md, executor.md, reviewer.md, patch.txt

✅ **CLI flags:**
- `python src/main.py --provider opencode` — full 50 issues
- `python src/main.py --provider opencode --issues 2` — testing mode
- `python src/main.py --provider opencode --resume` — continue from interruption

---

## ⚠️ Known Issues & Workarounds

| Issue | Impact | Workaround | Status |
|-------|--------|-----------|--------|
| DeepSeek JSON Mode requires "JSON" keyword in prompt | Medium | Prompts already have "Output ONLY valid JSON" | ✅ Done |
| Double-escaped newlines in JSON values | Low | `_normalize_newlines()` fixed to process all escapes | ✅ Fixed |
| Hunk count mismatch from AI hallucination | High | Auto-fix headers + recount lines | ✅ Done |
| OpenCode base_url (v1 vs zen/v1) | Medium | Zen/v1 tested & working | ✅ Verified |
| 50 issues is less than original 150 (docker/modal limitation) | Medium | Acceptable for skripsi scope | Accepted |

---

## 🔧 Configuration

**`.env` (commit-safe, secrets masked):**
```
GEMINI_API_KEY=***
GEMINI_MODEL=gemini-3.5-flash
OPENCODE_API_KEY=***
OPENCODE_MODEL=deepseek-v4-flash
TEMPERATURE=0.2
MAX_RETRIES=3
USD_IDR_RATE=17914.0
```

**`src/dataset_loader.py` — DEFAULT_REPO_SPECS:**
```python
[
    ("django/django", 10),              # hard
    ("sympy/sympy", 10),                # hard
    ("scikit-learn/scikit-learn", 10),  # medium
    ("matplotlib/matplotlib", 10),      # medium
    ("psf/requests", 6),                # easy
    ("mwaskom/seaborn", 4),             # easy
]
```

---

## 📈 Next Steps (Immediate)

1. **Run full 50-issue experiment:**
   ```bash
   python src/main.py --provider opencode
   # ~12-20 min runtime (150 executions × 5-10s random delay + API call time)
   ```

2. **Verify output structure:**
   - Check `results/EXP-{date}-001/predictions/` has 3 `.jsonl` files (direct, planning, review)
   - Check `results/EXP-{date}-001/results.csv` has `difficulty` column
   - Check `logs/experiment.log` has enhanced phase-level logs

3. **Analysis (for Bab 4 skripsi):**
   - Compare success rate by difficulty (easy vs medium vs hard)
   - Token usage per strategy × difficulty
   - Cost breakdown: input/output tokens vs total_cost_usd
   - AI auto-fix hunk impact: patches fixed % before vs after

---

## 🚨 Blockers / Questions for Researcher

| Blocker | Impact | Status |
|---------|--------|--------|
| None currently | — | Ready to execute |

---

## 📂 Important Paths

| Path | Purpose |
|------|---------|
| `src/experiments/runner.py` | Core loop (issue × strategy) + savepoint append |
| `src/experiments/swebench_adapter.py` | `_auto_fix_hunk_headers()` + `extract_diff()` |
| `src/models/issue.py` | `DIFFICULTY_MAP` + difficulty property |
| `src/main.py` | CLI entry, loads difficulty breakdown at start |
| `PLAN.md` | Full technical plan for this sprint |
| `.env` | Config (API keys, model names, rate) |

---

## 💡 Tips for Next Agent Session

1. **Before running:** Always check `.env` has valid `OPENCODE_API_KEY` (URL is https://opencode.ai/zen/v1)
2. **Resume mode:** If interrupted, run with `--resume` to skip completed issues
3. **Testing:** Use `--issues 2` to test pipeline before full 50
4. **Logs:** Dual logs at `logs/agentbench.log` + `results/EXP-*/logs/experiment.log`
5. **CSV analysis:** `results.csv` has `difficulty` column for stratification
6. **Skripsi contribution:** Auto-fix hunk impact is strong data point for Bab 4

---

## 📝 Notes

- **Commit convention:** All recent commits to branch `16/feat/eval-toolchain` follow pattern `feat:`, `fix:`, `refactor:`
- **No breaking changes:** All changes backward-compatible with existing results
- **Pricing update:** DeepSeek v4 flash added to `PricingTable` in `cost.py` — applies automatically
- **Prompts:** Tightened hunk-count instructions in `direct_prompt.md` + `executor.md` to reduce AI hallucination

---

**Last working state:** Commit `8b8d695` — all 4 items ready, tested on 1-issue dry run, ready for 50-issue full run.
