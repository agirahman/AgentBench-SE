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

# The sandbox is a different world from the host, so the agent must be told which
# one it is in. Split on purpose: the read-only roles (planner, reviewer) need the
# environment half (POSIX + the deliberately masked .git) but must NOT receive the
# dependency half, which tells the reader to run the tests and install wheels -- an
# instruction they are not allowed to carry out.
#
# The environment half is itself split, because ONE sentence in it names a TOOL
# (git_diff, which runs on the host). Not every read-only role carries that tool:
# planner's tool set is read_file/grep/list_files only (agents.tools.AGENT_TOOLS),
# while reviewer's includes git_diff. Telling a role to use a tool it does not have
# is the same defect class this module avoids elsewhere (see the read-only framing
# below), so the git_diff sentence is a separate piece handed out only to the roles
# whose tool set actually contains it.
_SANDBOX_POSIX_HINT = (
    "The execution sandbox is POSIX (Linux): use grep/ls/cat, not findstr/dir/type. "
    "Absolute Windows paths are not valid there. "
)

_SANDBOX_GIT_DIFF_HINT = (
    "Git is not installed in the sandbox — use the git_diff tool (runs on host) "
    "to inspect changes. "
)

_SANDBOX_MASKED_GIT_HINT = (
    "Git history is intentionally unavailable/masked by design: do NOT search for "
    ".git directories, git repositories, or git history, and do not try to unmount "
    "them — such searches waste turns; solve the issue from the source code alone. "
)

# The editors' env half: POSIX + git_diff (direct/executor all carry it) + the
# masked-git rule.
_SANDBOX_ENV_HINT = _SANDBOX_POSIX_HINT + _SANDBOX_GIT_DIFF_HINT + _SANDBOX_MASKED_GIT_HINT

_SANDBOX_DEPS_HINT = (
    "The sandbox HAS this repo's common dependencies pre-installed, so run the tests. "
    "There is NO network. If a module is missing, look for it in the local wheelhouse "
    "(`ls /opt/wheelhouse`) and install it with "
    "`pip install --no-index --find-links=/opt/wheelhouse <package>`, then retry. "
    "Only DEPENDENCIES are there: a released copy of the project you are fixing is "
    "deliberately absent, because it would contain the answer.\n"
)


def get_tool_system_prompt(sandbox_mode: str | None = None) -> str:
    """Return the tool system prompt, optionally tailored to the sandbox environment.

    The default mode is ``docker`` to match ``agents.tools.get_sandbox_mode``: if
    these two disagreed, an unconfigured run would EXECUTE in docker while being
    PROMPTED as if it were local (telling the agent to use ``dir``/``type`` and
    that git works), which is worse than either default alone.
    """
    mode = (sandbox_mode or os.environ.get("SANDBOX_MODE", "docker")).strip().lower()
    hint = (_SANDBOX_ENV_HINT + _SANDBOX_DEPS_HINT) if mode == "docker" else ""
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
#
# The sandbox env hint is shared with the editors (see _SANDBOX_ENV_HINT) because
# planner and reviewer burn the same turns hunting the masked .git. It is appended
# INSIDE the read-only framing, and only the env half: the dependency half says
# "run the tests / install wheels", which these roles are not allowed to do.
_READONLY_BASE = (
    "You are a software engineering agent analysing a bug in a real repository.\n"
    "\n"
    "Work like this:\n"
    "1. Explore with read_file / grep / list_files to gather evidence from the "
    "actual source — do not rely on assumptions about the code.\n"
    "2. Base every claim on code you have actually read.\n"
    "3. When you have enough evidence, reply with your final answer and no tool call.\n"
    "\n"
)


def get_readonly_system_prompt(
    sandbox_mode: str | None = None, *, has_git_diff: bool = False
) -> str:
    """Read-only variant: same env hint as the editors, no write/test instruction.

    ``has_git_diff`` must reflect the role's REAL tool set. The env hint's git_diff
    sentence names a TOOL, and only direct/executor/reviewer carry it -- planner's
    set is read_file/grep/list_files (agents.tools.AGENT_TOOLS). A role told to use
    a tool it does not have is the defect class this read-only framing exists to
    avoid, so the sentence is handed out only when the tool is actually present.
    """
    mode = (sandbox_mode or os.environ.get("SANDBOX_MODE", "docker")).strip().lower()
    hint = ""
    if mode == "docker":
        hint = _SANDBOX_POSIX_HINT
        if has_git_diff:
            hint += _SANDBOX_GIT_DIFF_HINT
        hint += _SANDBOX_MASKED_GIT_HINT
    return _READONLY_BASE + hint + "You may NOT modify files. Do not describe what you are about to do; do it."


# Read-only role WITHOUT git_diff (planner): POSIX + masked git, no git_diff line.
READONLY_TOOL_SYSTEM_PROMPT = get_readonly_system_prompt()

# Read-only role WITH git_diff (reviewer). Separate constant so the two cannot
# drift into telling a role to use a tool it does not have.
READONLY_GIT_DIFF_TOOL_SYSTEM_PROMPT = get_readonly_system_prompt(has_git_diff=True)

# Single-shot path (no tools sent at all). The previous text told the model to
# "use the provided tools" even here, where none existed — so it narrated tool
# use it could not perform and returned no patch (a direct contributor to the 45
# NO_DIFF results in EXP-20260824-005).
NO_TOOL_SYSTEM_PROMPT = (
    "You are a software engineering agent. Produce the requested output "
    "directly and completely. Do not describe a plan of action or narrate what "
    "you are about to do — emit the requested artifact itself."
)
