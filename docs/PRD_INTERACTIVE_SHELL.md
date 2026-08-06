# Product Requirements Document (PRD)
## AgentBench-SE Interactive Shell

**Version:** 1.0  
**Date:** 2026-08-05  
**Author:** Agi Rahman Setiadi  
**Status:** Draft — Ready for Implementation

---

## 1. Executive Summary

Transform AgentBench-SE from a command-line script (`python src/main.py --flags`) into an **interactive shell framework** with:
- One-time setup wizard (`agentbench setup`)
- Interactive REPL environment (`agentbench` → shell prompt)
- Beautiful terminal UI (tables, progress bars, colors via `rich`)
- Persistent configuration (`~/.agentbench/config.yaml`)

**Goals:**
1. **Usability:** Reduce barrier to entry for researchers (no manual `.env` editing)
2. **Discoverability:** Built-in help system (`help` command)
3. **Professionalism:** Clean, modern CLI like Hermes/Docker/Kubernetes
4. **Open-source ready:** Easy to install (`pip install agentbench-se`), easy to use

---

## 2. User Personas

### Primary: Researcher / PhD Student
- **Goal:** Run experiments to evaluate AI agent strategies
- **Pain Points:** Complex setup, cryptic error messages, manual config editing
- **Needs:** Guided setup, clear feedback, reproducible experiments

### Secondary: Practitioner / Engineer
- **Goal:** Benchmark AI agent orchestration for production use
- **Pain Points:** Lack of documentation, hard to customize
- **Needs:** Fast setup, extensible framework, export results

---

## 3. User Journey

### 3.1 First-Time User (Setup)

```
$ git clone https://github.com/agirahman/agentbench-se.git
$ cd agentbench-se
$ pip install -e .
$ agentbench setup

[Interactive wizard walks through:]
1. Researcher info (name, institution, email)
2. Provider selection (openrouter, gemini, groq, opencode)
3. API key input (hidden, validated)
4. Model selection (default: tencent/hy3:free)
5. Experiment settings (temperature, retries, rate limit, USD/IDR)
6. Provider connection test

[Result: ~/.agentbench/config.yaml created]

$ agentbench
[Enters interactive shell]
```

### 3.2 Returning User (Run Experiment)

```
$ agentbench

[Shell displays:]
- Banner (framework name, version, researcher name)
- Current config summary (provider, model, dataset)
- Prompt: agentbench>

agentbench> help
[Shows command list]

agentbench> run --issues 10
[Interactive confirmation]
[Real-time progress with rich progress bars]
[Success summary with cost/time breakdown]

agentbench> results summary
[Table view of strategy comparison]

agentbench> export --format csv
[Exports to results/latest/export.csv]

agentbench> exit
```

---

## 4. Functional Requirements

### FR-1: Installation & Packaging

**Requirement:**
- Framework must be installable via `pip install -e .` (editable mode for dev)
- Entry point: `agentbench` command available in PATH after install

**Acceptance Criteria:**
- `pip install -e .` succeeds without errors
- `agentbench --version` returns version number
- `which agentbench` shows path to executable

**Implementation Notes:**
- Use `setup.py` with `entry_points` for console script
- Package name: `agentbench-se` (PyPI-friendly)
- Dependencies: `click`, `rich`, `pyyaml`, + existing requirements

---

### FR-2: Setup Wizard (`agentbench setup`)

**Requirement:**
- Interactive wizard for first-time configuration
- Guides user through:
  1. Researcher information (name, institution, email)
  2. Provider selection (dropdown: openrouter, gemini, groq, opencode)
  3. API key input (hidden, validated on-the-fly)
  4. Model selection (default suggestions per provider)
  5. Experiment settings (temperature, max_retries, rate_limit, usd_idr_rate)
  6. Provider health check (test connection before saving)
- Saves config to `~/.agentbench/config.yaml`

**Acceptance Criteria:**
- Setup wizard can be run multiple times (overwrites previous config)
- API key validation: test provider connection before saving
- If health check fails, user can retry or choose different provider
- Config file is human-readable YAML (user can manually edit if needed)
- Security: API keys stored in plain YAML (document security risk in README)

**UI/UX:**
- Use `rich` for beautiful prompts (colored text, panels, spinners)
- Show progress: "Testing connection..." with spinner
- Success/error messages with color coding (green ✓, red ✗)

**Sample Config (`~/.agentbench/config.yaml`):**
```yaml
researcher:
  name: "Agi Rahman Setiadi"
  institution: "Universitas Negeri Jakarta"
  email: "agi.rahman.s@gmail.com"

provider:
  name: "openrouter"
  api_key: "sk-or-v1-***"
  model: "tencent/hy3:free"

experiment:
  temperature: 0.2
  max_retries: 3
  rate_limit: 1.5
  usd_idr_rate: 16500.0
```

---

### FR-3: Interactive Shell Entry (`agentbench`)

**Requirement:**
- Running `agentbench` (no args) launches interactive shell
- Shell displays:
  1. Banner (framework name, version, researcher name)
  2. Current config summary (provider, model, dataset info)
  3. Prompt: `agentbench>` waiting for user input
- Shell uses `cmd.Cmd` (Python standard library) for REPL
- Commands are space-separated: `<command> [args] [flags]`
- Built-in commands: `help`, `exit`, `quit`, `version`, `info`

**Acceptance Criteria:**
- Typing `agentbench` without setup shows friendly error: "No config found. Run 'agentbench setup' first."
- Typing invalid command shows: "Unknown command: xyz. Type 'help' for available commands."
- Typing `exit` or `quit` or `Ctrl+D` exits shell gracefully
- Banner is visually distinct (use `rich.panel` with border)

**Banner Example:**
```
╔══════════════════════════════════════════════════════════════╗
║              🧪 AgentBench-SE Interactive Shell              ║
║                                                              ║
║  Framework for AI Agent Orchestration Strategy Evaluation   ║
║  Version 0.1.0 | Research by Agi Rahman Setiadi             ║
╚══════════════════════════════════════════════════════════════╝

📊 Current Configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Provider: OpenRouter (tencent/hy3:free)
  Dataset: SWE-bench Lite (50 issues)
  Strategies: Direct | Planning | Planning+Review

Type 'help' for available commands or 'exit' to quit.
```

---

### FR-4: Command — `help`

**Requirement:**
- Show list of all available commands with brief description
- Group commands by category (Experiment, Analysis, Configuration, etc.)
- Use `rich.table` for clean layout

**Acceptance Criteria:**
- `help` shows full command list
- `help <command>` shows detailed help for specific command (e.g., `help run`)

**Sample Output:**
```
Available Commands:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 Experiment Commands
  run             Start experiment run
  resume          Resume from previous run
  status          Show current run status
  
📊 Analysis Commands
  results         View experiment results
  compare         Compare strategies
  export          Export results to file
  
⚙️  Configuration
  config          Show/edit configuration
  setup           Re-run setup wizard
  provider        Test provider connection
  
📁 Data Management
  dataset         Manage SWE-bench dataset
  artifacts       Browse saved artifacts
  
❓ Help & Info
  help            Show this help message
  info            Show framework information
  version         Show version
  exit            Exit interactive shell
```

---

### FR-5: Command — `run`

**Requirement:**
- Start experiment run with current config
- Syntax: `run [--issues N] [--strategy S] [--output DIR] [--resume]`
- Flags:
  - `--issues N`: Number of issues to process (default: all 50)
  - `--strategy S`: Which strategy to run (direct, planning, review, all) (default: all)
  - `--output DIR`: Output directory (default: `results/EXP-<timestamp>`)
  - `--resume`: Resume from previous run (skip done issues)
- Shows confirmation before starting (Y/n)
- Real-time progress display:
  - Overall progress bar (X/N issues)
  - Per-issue progress (current strategy, current agent)
  - Live metrics (time elapsed, tokens used, cost so far)
  - Spinner for active inference ("⠋ Generating patch... [Executor]")
- On completion:
  - Summary table (total time, total cost, success rate per strategy)
  - Path to results directory

**Acceptance Criteria:**
- Integration with existing `src/experiments/runner.py`
- Progress updates every inference (not batch at end)
- Handles errors gracefully (log error, continue to next issue)
- Savepoint after each issue (can resume if interrupted)
- CTRL+C during run: confirm "Abort experiment? (Y/n)" → save partial results

**Sample Interaction:**
```
agentbench> run --issues 10

╔══════════════════════════════════════════════════════════════╗
║                     🔬 Experiment Run                         ║
╚══════════════════════════════════════════════════════════════╝

Configuration:
  Issues: 10 (from 50 total)
  Strategies: Direct, Planning, Planning+Review
  Output: results/EXP-20260805-001/
  
Start experiment? [Y/n]: y

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Processing Issue 1/10: django__django-11049
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Strategy: Direct (1/3)
  ⠋ Generating patch... [Executor] 
  ✓ Patch generated (2.3s, 1,234 tokens, $0.021)
  
[... progress continues ...]

Progress: ████████░░ 10/10 issues (100%)
Time: 3m 24s | Avg: 20.4s per issue
Cost: $0.52 USD (Rp 8,580)

✅ Experiment completed!
Results: results/EXP-20260805-001/
```

---

### FR-6: Command — `results`

**Requirement:**
- Subcommands: `summary`, `compare`, `errors`, `patch`
- `results summary`: Show aggregate metrics per strategy (table)
- `results compare`: Side-by-side comparison of strategies (table)
- `results errors`: List failed issues with error messages
- `results patch <issue_id> <strategy>`: Display patch for specific issue

**Acceptance Criteria:**
- Integration with existing `src/view_results.py`
- Tables use `rich.table` with borders & colors
- If no results directory exists, show friendly error

**Sample Output (`results summary`):**
```
╔══════════════════════════════════════════════════════════════╗
║                    📊 Results Summary                         ║
╚══════════════════════════════════════════════════════════════╝

Experiment: EXP-20260805-001
Issues: 10 | Strategies: 3

┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Strategy      ┃ Issues ┃ Avg Time ┃ Avg Cost┃ Success ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ Direct        │ 10     │ 6.2s     │ $0.021  │ 80%     │
│ Planning      │ 10     │ 12.5s    │ $0.034  │ 90%     │
│ Planning+Rev  │ 10     │ 19.8s    │ $0.065  │ 90%     │
└───────────────┴────────┴──────────┴─────────┴─────────┘
```

---

### FR-7: Command — `export`

**Requirement:**
- Export results to file
- Syntax: `export [--format FORMAT] [--output PATH]`
- Formats: `csv`, `json`, `markdown` (default: `csv`)
- Output path: default `results/latest/export.<format>`

**Acceptance Criteria:**
- CSV: One row per (issue, strategy) with all metrics
- JSON: Structured output (easy for programmatic access)
- Markdown: Human-readable report (suitable for appendix)

---

### FR-8: Command — `config`

**Requirement:**
- Subcommands: `show`, `set`, `reset`
- `config show`: Display current config (pretty-printed YAML)
- `config set <key> <value>`: Update specific key (e.g., `config set provider.model llama-3.3-70b`)
- `config reset`: Delete config, prompt to re-run setup

**Acceptance Criteria:**
- API keys masked when displayed (show first 8 chars + "***")
- `config set` validates key path exists before saving
- `config reset` asks confirmation before deleting

---

### FR-9: Command — `provider`

**Requirement:**
- Test current provider connection (health check)
- Syntax: `provider [--test]`
- No args: show provider info (name, model, status)
- `--test`: run health check (send test inference)

**Acceptance Criteria:**
- Health check sends prompt "Reply with only: OK" to provider
- If success: show green ✓ "Provider online"
- If fail: show red ✗ "Provider error: <message>"

---

### FR-10: Command — `dataset`

**Requirement:**
- Show dataset information (SWE-bench Lite)
- Syntax: `dataset [--refresh]`
- No args: show cached dataset info (total issues, repos, difficulty distribution)
- `--refresh`: re-download dataset from HuggingFace

**Acceptance Criteria:**
- Table shows repo breakdown (Django: 10 issues, SymPy: 10, etc.)
- Difficulty distribution (easy: X, medium: Y, hard: Z)
- If dataset not cached, auto-download on first access

---

### FR-11: Command — `artifacts`

**Requirement:**
- Browse saved artifacts (planner.md, executor.md, reviewer.md, patch.txt)
- Syntax: `artifacts <issue_id> <strategy>`
- Opens artifact in pager (less-like viewer) or prints to stdout

**Acceptance Criteria:**
- If artifact not found, show error: "No artifacts for <issue_id> / <strategy>"
- Use `rich.syntax` for syntax highlighting (Markdown)

---

### FR-12: Command — `info`

**Requirement:**
- Show framework metadata
- Display:
  - Framework name & version
  - Researcher info (from config)
  - Installation path
  - Config path
  - Results directory
  - Dataset cache path

**Sample Output:**
```
╔══════════════════════════════════════════════════════════════╗
║                  AgentBench-SE Information                    ║
╚══════════════════════════════════════════════════════════════╝

Framework: AgentBench-SE v0.1.0
Researcher: Agi Rahman Setiadi (agi.rahman.s@gmail.com)
Institution: Universitas Negeri Jakarta

Paths:
  Installation: /home/user/agentbench-se
  Config: ~/.agentbench/config.yaml
  Results: ./results/
  Dataset: ~/.agentbench/cache/swe-bench-lite/

Documentation: https://github.com/agirahman/agentbench-se
```

---

### FR-13: Command — `exit` / `quit`

**Requirement:**
- Exit interactive shell
- Both `exit` and `quit` work (aliases)
- `Ctrl+D` (EOF) also exits
- Show goodbye message: "Goodbye! 👋"

---

## 5. Non-Functional Requirements

### NFR-1: Performance
- Shell startup < 2 seconds (load config, check provider)
- Command response time < 100ms (except `run` which is long-running)
- Progress updates every 0.5s during experiment run

### NFR-2: Usability
- All commands have `--help` flag showing usage
- Error messages are actionable (tell user what to do next)
- Consistent color scheme:
  - Success: green
  - Error: red
  - Info: cyan
  - Warning: yellow
  - Prompt: white/default

### NFR-3: Reliability
- Config validation on load (catch corrupted YAML)
- Graceful handling of missing config (prompt to run setup)
- CTRL+C handling: confirm abort, don't crash
- Auto-save after each issue (resume capability)

### NFR-4: Maintainability
- Modular code structure:
  - `shell.py`: Interactive shell (cmd.Cmd subclass)
  - `config_manager.py`: Config I/O
  - `setup_wizard.py`: Setup wizard logic
  - `commands/`: One file per command (run.py, results.py, etc.)
- Each command is a method: `do_<command>(self, args)`
- Use existing core logic (don't rewrite runner, just wrap it)

### NFR-5: Extensibility
- Easy to add new commands (just add `do_<name>` method)
- Config schema documented (easy for users to add custom fields)
- Plugin-ready architecture (future: load custom strategies)

---

## 6. Technical Stack

### Core Libraries
- **`cmd`**: Python standard library for REPL (like Python shell)
- **`rich`**: Beautiful terminal UI (tables, progress, panels, syntax highlighting)
- **`click`**: CLI argument parsing for `agentbench setup` wizard
- **`pyyaml`**: Config file I/O

### Integration Points
- **Existing `src/` code**: Reuse all existing logic (providers, strategies, runner, evaluator)
- **No breaking changes**: Keep `python src/main.py` working for backward compatibility
- **Config migration**: Read `.env` if exists, convert to YAML config

---

## 7. File Structure

```
AgantBech-SE/
├── agentbench/                        # NEW: Package (rename from src/)
│   ├── __init__.py
│   ├── shell.py                       # NEW: Interactive shell (cmd.Cmd)
│   ├── config_manager.py              # NEW: Config I/O (~/.agentbench/config.yaml)
│   ├── setup_wizard.py                # NEW: agentbench setup (click-based)
│   │
│   ├── commands/                      # NEW: Command implementations
│   │   ├── __init__.py
│   │   ├── run.py                     # run command logic
│   │   ├── results.py                 # results command logic
│   │   ├── export.py                  # export command logic
│   │   ├── config.py                  # config command logic
│   │   └── dataset.py                 # dataset command logic
│   │
│   ├── cli/                           # NEW: Entry points
│   │   ├── __init__.py
│   │   └── main.py                    # Entry: agentbench, agentbench setup
│   │
│   └── core/                          # EXISTING: Rename src/ → core/
│       ├── providers/
│       ├── strategies/
│       ├── agents/
│       ├── experiments/
│       ├── evaluation/
│       ├── models/
│       └── prompts/
│
├── setup.py                           # NEW: Packaging (pip install -e .)
├── pyproject.toml                     # NEW: Modern Python packaging
├── requirements.txt                   # UPDATE: Add rich, click, pyyaml
├── README.md                          # UPDATE: New installation & usage
└── docs/
    ├── PRD_INTERACTIVE_SHELL.md       # This document
    └── SDD_INTERACTIVE_SHELL.md       # Technical design (next step)
```

---

## 8. Implementation Phases

### Phase 1: Setup & Packaging (2-3 hours)
- [x] Install dependencies: `pip install rich click pyyaml`
- [x] Create `setup.py` with entry points
- [x] Create `agentbench/cli/main.py` (entry point dispatcher)
- [x] Create `agentbench/config_manager.py` (load/save YAML)
- [x] Create `agentbench/setup_wizard.py` (interactive wizard with `rich`)
- [x] Test: `pip install -e .` → `agentbench setup` → config created

**Deliverable:** `agentbench setup` works end-to-end

### Phase 2: Interactive Shell Foundation (2-3 hours)
- [x] Create `agentbench/shell.py` (cmd.Cmd subclass)
- [x] Implement basic commands: `help`, `exit`, `info`, `version`
- [x] Implement `config show`, `config set`, `config reset`
- [x] Add banner (rich.panel with framework info)
- [x] Test: `agentbench` → enters shell → `help` works

**Deliverable:** Shell works, basic commands functional

### Phase 3: Core Commands — Run (3-4 hours)
- [x] Create `agentbench/commands/run.py`
- [x] Integrate with existing `src/experiments/runner.py` (via `agentbench.core.experiments.runner`)
- [x] Add progress UI:
  - Overall progress bar (rich.progress)
  - Per-issue status (spinner, time, tokens, cost)
- [x] Add confirmation prompt before starting
- [x] Add CTRL+C handling (confirm abort)
- [x] Test: `run --issues 5` → full experiment with live progress

**Deliverable:** `run` command works with beautiful progress UI

### Phase 4: Analysis Commands (2-3 hours)
- [x] Create `agentbench/commands/results.py`
- [x] Integrate with existing `src/view_results.py` (via `agentbench.core.view_results` + `evaluation.statistics`)
- [x] Implement subcommands: `summary`, `compare`, `errors`, `patch`
- [x] Add rich.table for result tables
- [x] Create `agentbench/commands/export.py` (CSV, JSON, Markdown)
- [x] Test: `results summary` → table displayed

**Deliverable:** Results viewing & export functional

### Phase 5: Data Management Commands (1-2 hours)
- [x] Create `agentbench/commands/dataset.py` (show dataset info)
- [x] Create `agentbench/commands/artifacts.py` (browse artifacts)
- [x] Implement `provider` command (health check)
- [x] Test: `dataset` → shows repo breakdown

**Deliverable:** All commands implemented

### Phase 6: Polish & Documentation (2-3 hours)
- [ ] Update README.md (new installation & usage)
- [ ] Add command help strings (`help <command>`)
- [ ] Add error handling (friendly error messages)
- [ ] Add input validation (e.g., `--issues` must be 1-50)
- [ ] Test full workflow: setup → run → results → export
- [ ] Record demo GIF for README

**Deliverable:** Production-ready, documented framework

**Total Estimated Time:** 12-18 hours (1.5-2 days full-time, or 1 week part-time)

---

## 9. Success Metrics

### Quantitative
- **Setup time:** < 5 minutes from clone to first run
- **Commands learned:** User can run experiment without reading docs (help command sufficient)
- **Error rate:** < 5% of runs fail due to user error (validation catches issues)

### Qualitative
- **User feedback:** "This is so much easier than the old script!"
- **Adoption:** Other researchers can use it without author's help
- **Documentation:** README gets < 3 "how do I..." issues per month

---

## 10. Out of Scope (Future Work)

### Phase 2 Features (Post-Thesis)
- Tab completion (Bash/Zsh)
- Plugin system for custom strategies
- Web UI (Streamlit/Gradio) for non-CLI users
- Docker image (`docker run agentbench/agentbench`)
- Multi-user support (team collaboration)
- Cloud execution (run on remote GPU)

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Breaking existing code** | High | Keep `python src/main.py` working; add new CLI as separate entry point |
| **Config migration issues** | Medium | Auto-convert `.env` to YAML on first `setup`; document manual steps |
| **Rich library compatibility** | Low | Pin `rich>=13.0` in requirements; test on Linux/macOS/Windows |
| **Scope creep** | Medium | Strict adherence to Phase 1-6; defer nice-to-haves to Phase 2 |

---

## 12. Approval & Sign-off

**Stakeholder:** Agi Rahman Setiadi (Researcher)  
**Status:** ✅ Approved for implementation  
**Next Step:** Create SDD (System Design Document) for technical architecture

---

## Appendix A: Command Reference (Quick Lookup)

| Command | Syntax | Description |
|---------|--------|-------------|
| `help` | `help [<command>]` | Show available commands or help for specific command |
| `run` | `run [--issues N] [--strategy S] [--output DIR] [--resume]` | Start experiment run |
| `resume` | `resume` | Resume last interrupted run |
| `status` | `status` | Show current run status |
| `results` | `results <subcommand>` | View/analyze results |
| `  summary` | `results summary` | Aggregate metrics per strategy |
| `  compare` | `results compare` | Side-by-side strategy comparison |
| `  errors` | `results errors` | List failed issues |
| `  patch` | `results patch <issue_id> <strategy>` | Display specific patch |
| `export` | `export [--format FORMAT] [--output PATH]` | Export results to file |
| `config` | `config <subcommand>` | Manage configuration |
| `  show` | `config show` | Display current config |
| `  set` | `config set <key> <value>` | Update config value |
| `  reset` | `config reset` | Delete config (re-run setup) |
| `setup` | `setup` | Re-run setup wizard |
| `provider` | `provider [--test]` | Show/test provider connection |
| `dataset` | `dataset [--refresh]` | Show dataset info |
| `artifacts` | `artifacts <issue_id> <strategy>` | Browse saved artifacts |
| `info` | `info` | Show framework metadata |
| `version` | `version` | Show version number |
| `exit` | `exit` or `quit` or `Ctrl+D` | Exit interactive shell |

---

## Appendix B: Config Schema

```yaml
# ~/.agentbench/config.yaml

researcher:
  name: string              # Required
  institution: string       # Optional
  email: string             # Optional

provider:
  name: string              # Required: openrouter | gemini | groq | opencode
  api_key: string           # Required (provider-specific)
  model: string             # Required (provider-specific)

experiment:
  temperature: float        # Default: 0.2, Range: 0.0-1.0
  max_retries: int          # Default: 3
  rate_limit: float         # Default: 1.5 (seconds between strategy runs)
  usd_idr_rate: float       # Default: 16500.0
```

---

**End of PRD — Ready for SDD & Implementation** 🚀
