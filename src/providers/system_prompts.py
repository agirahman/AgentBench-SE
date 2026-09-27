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
EDITING_ROLES = frozenset({"direct", "executor"})

# Editing roles (direct, executor): the agent must change real files.
TOOL_SYSTEM_PROMPT = (
    "You are a software engineering agent fixing a bug in a real repository.\n"
    "\n"
    "Work like this:\n"
    "1. Explore with read_file / grep / list_files to locate the root cause.\n"
    "2. Apply the fix with edit_file (or write_file for a new file). "
    "Read the file first and copy the exact existing text into old_string.\n"
    "3. Call git_diff to confirm the change is what you intend.\n"
    "4. When the fix is complete, reply with a ONE-LINE summary and no tool call.\n"
    "\n"
    "Do NOT print a unified diff as text — the patch is taken from your file "
    "edits automatically. Do not describe what you are about to do; do it."
)

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
