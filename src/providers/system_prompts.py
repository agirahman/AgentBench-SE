"""System prompts shared by the tool-calling and single-shot provider paths.

Kept in a dependency-free leaf module on purpose: ``agents.base`` needs these
constants, and ``providers.tool_loop`` needs ``agents.tools``. If the constants
lived in ``tool_loop``, importing them from ``base`` created a cycle
(base -> tool_loop -> agents.tools -> agents/__init__ -> base) that only worked
by accident of import order and broke as soon as a module was imported directly.
"""

# Roles allowed to modify the repository. The others are read-only and must never
# be told to edit code.
#
# Lives in this leaf module on purpose: both ``agents.base`` (to pick the system
# prompt) and ``providers.tool_loop`` (to pick the wrap-up nudge) need it, and
# having ``tool_loop`` import ``agents.base`` would recreate the import cycle this
# module exists to avoid.
import os

EDITING_ROLES = frozenset({"direct", "executor"})


def get_tool_system_prompt(sandbox_mode: str | None = None) -> str:
    """Return the tool system prompt, optionally tailored to the sandbox environment.

    The default mode is ``docker`` to match ``agents.tools.get_sandbox_mode``: if
    these two disagreed, an unconfigured run would EXECUTE in docker while being
    PROMPTED as if it were local (telling the agent to use ``dir``/``type`` and
    that git works), which is worse than either default alone.
    """
    mode = (sandbox_mode or os.environ.get("SANDBOX_MODE", "docker")).strip().lower()
    hint = (
        "The execution sandbox is POSIX (Linux): use grep/ls/cat, not findstr/dir/type. "
        "Absolute Windows paths are not valid there. Git is not installed in the sandbox — "
        "use the git_diff tool (runs on host) to inspect changes. "
        "The sandbox HAS this repo's common dependencies pre-installed, so run the tests. "
        "There is NO network. If a module is missing, look for it in the local wheelhouse "
        "(`ls /opt/wheelhouse`) and install it with "
        "`pip install --no-index --find-links=/opt/wheelhouse <package>`, then retry. "
        "Only DEPENDENCIES are there: a released copy of the project you are fixing is "
        "deliberately absent, because it would contain the answer.\n"
        if mode == "docker"
        else ""
    )
    # Step 4 must match what run_tests will actually DO. In docker mode the sandbox
    # runs the suite, so the agent is told to use it. Outside docker, run_tests
    # REFUSES to execute (see agents.tools._LOCAL_MODE_REFUSAL: this host has a WSL
    # bash with internet, so no local execution can be contained) -- and a prompt
    # that still said "verify with run_tests" would send the agent into a refusal
    # and read as a broken tool rather than a deliberate control.
    verify_step = (
        "4. Verify with run_tests. The sandbox pre-installs this repo's dependencies, "
        "so the tests should actually run — use them as a regression signal for your "
        "change. If run_tests reports a missing module, install it from the wheelhouse "
        "as described below and retry; that is an environment problem you can fix, not "
        "a reason to stop.\n"
        if mode == "docker"
        else
        "4. run_tests is DISABLED outside docker mode: this host has a network and a "
        "WSL bash, so executing commands here could fetch a released copy of the "
        "project (which contains the answer), and no guard can contain that. So do "
        "not call run_tests to execute anything — verify by reading the code and "
        "reasoning about the change, then report.\n"
    )
    return (
        "You are a software engineering agent fixing a bug in a real repository.\n"
        "\n"
        "Work like this:\n"
        "1. Explore with read_file / grep / list_files to locate the root cause.\n"
        "2. Apply the fix with edit_file (or write_file for a new file). "
        "Read the file first and copy the exact existing text into old_string.\n"
        "3. Call git_diff to confirm the change is what you intend.\n"
        + verify_step
        + hint
        + "5. When the fix is complete, reply with a ONE-LINE summary and no tool call.\n"
        "\n"
        "Do NOT print a unified diff as text — the patch is taken from your file "
        "edits automatically. Do not describe what you are about to do; do it."
    )


# Editing roles (direct, executor): the agent must change real files.
TOOL_SYSTEM_PROMPT = get_tool_system_prompt()

# Read-only roles (planner, reviewer) must NOT be told to edit code. Sharing the
# editing prompt with them produced contradictory instructions: their task prompt
# says "do not write code / you may not edit", while the system prompt said to
# apply a fix. Each role now gets a prompt that matches its mandate.
READONLY_TOOL_SYSTEM_PROMPT = (
    "You are a software engineering agent analysing a bug in a real repository.\n"
    "\n"
    "Work like this:\n"
    "1. Explore with read_file / grep / list_files to gather evidence from the "
    "actual source — do not rely on assumptions about the code.\n"
    "2. Base every claim on code you have actually read.\n"
    "3. When you have enough evidence, reply with your final answer and no tool call.\n"
    "\n"
    "You may NOT modify files. Do not describe what you are about to do; do it."
)

# Single-shot path (no tools sent at all). The previous text told the model to
# "use the provided tools" even here, where none existed — so it narrated tool
# use it could not perform and returned no patch (a direct contributor to the 45
# NO_DIFF results in EXP-20260824-005).
NO_TOOL_SYSTEM_PROMPT = (
    "You are a software engineering agent. Produce the requested output "
    "directly and completely. Do not describe a plan of action or narrate what "
    "you are about to do — emit the requested artifact itself."
)
