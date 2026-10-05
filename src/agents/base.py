import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import Config
from models.inference import InferenceResult
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.tools import get_tools_for_agent
from providers.system_prompts import (
    TOOL_SYSTEM_PROMPT,
    READONLY_TOOL_SYSTEM_PROMPT,
    EDITING_ROLES as _EDITING_ROLES,
)
from utils.prompt_loader import load_prompt_or_default, load_prompt, prompt_exists

# Loaded once; identical long prefix prepended to every request when the
# cache-friendly layout is enabled. Kept out of per-instance template so the
# top of the prompt is byte-identical across issues in a strategy.
_SHARED_STATIC_HEADER = load_prompt_or_default("shared_static.md", "")

# _EDITING_ROLES (roles that may modify the repository) is imported from
# providers.system_prompts so the system-prompt choice here and the tool-loop
# wrap-up nudge in providers.tool_loop cannot drift apart. The others get a
# read-only system prompt and a read-only tool set, so the model is never told to
# edit code it cannot (or must not) touch.


@dataclass
class AgentResponse:
    message: AgentMessage
    inference: InferenceResult


class BaseAgent(ABC):
    name: str = ""
    prompt_file: str = ""
    default_template: str = ""

    def __init__(self, provider):
        self.provider = provider
        self.template = load_prompt_or_default(self.prompt_file, self.default_template)
        # Tool calling is opt-in per agent; enabled by the registry when the
        # provider supports it and Config.TOOLCALL_ENABLED is on.
        self.use_tools = False
        # Active instance repo root for tool exploration (set per strategy run).
        self.repo_root: str | None = None
        # Cached tool-mode template (loaded lazily, once).
        self._tool_template_cache: str | None = None

    def _tool_template(self) -> str:
        """Prompt variant for tool-calling mode.

        The non-tool prompts instruct the model to emit a unified diff as JSON.
        That instruction is actively wrong under tool calling, where the patch is
        captured from the working tree — a model following it would type a diff
        instead of editing files, and the edit would never happen. Each role that
        has a tool variant loads ``<prompt_file>`` with a ``_tools`` suffix.

        A MISSING variant used to fall back to the base template silently, and that
        fallback was a real defect rather than a safety net: ``planner`` had no
        ``planner_tools.md``, so in tool mode it received the text-diff prompt
        telling it to "Output ONLY valid JSON". The planner then made ZERO tool
        calls in 6 of 9 pilot runs (EXP-20260930-332) -- not because it chose not to
        explore, but because nothing asked it to. The results carried no mark of
        the difference, so it read as agent behaviour.

        Now the fallback WARNS and names the missing file, so the next such gap
        surfaces at the first run instead of in a post-hoc audit. The base template
        is still returned (a missing file must not crash a 150-run sweep), but the
        warning makes it visible.
        """
        if self._tool_template_cache is None:
            stem = (
                self.prompt_file[:-3]
                if self.prompt_file.endswith(".md")
                else self.prompt_file
            )
            variant = f"{stem}_tools.md"
            loaded = load_prompt_or_default(variant, "")
            # Ask the filesystem, not the loader: the loader also returns "" when a
            # test has stubbed it out, and reporting that as "file missing" sends
            # the reader after a file that is present.
            if not prompt_exists(variant):
                warnings.warn(
                    f"PROMPT VARIANT MISSING: agent '{self.name}' is running with "
                    f"tools but '{variant}' does not exist, so it falls back to "
                    f"'{self.prompt_file}' -- a NON-TOOL prompt that tells the model "
                    f"to emit JSON instead of calling tools. The agent will not "
                    f"explore, and the run will look like a strategy that chose not "
                    f"to read code. Create src/prompts/{variant}.",
                    UserWarning,
                    stacklevel=2,
                )
            self._tool_template_cache = loaded or self.template
        return self._tool_template_cache

    def _static_header(self) -> str:
        """Long, identical prefix for automatic prefix caching.

        Returns the shared static header only when PROMPT_CACHE_LAYOUT is on and
        the header file is present; otherwise empty (legacy layout untouched).
        """
        if not Config.PROMPT_CACHE_LAYOUT or not _SHARED_STATIC_HEADER:
            return ""
        return _SHARED_STATIC_HEADER

    def _wrap(self, dynamic_part: str) -> str:
        """Prepend the static header when cache layout is enabled."""
        header = self._static_header()
        if not header:
            return dynamic_part
        return f"{header}\n\n{dynamic_part}"

    def act(
        self,
        task: AgentMessage,
        context: Blackboard,
        max_tool_turns: int | None = None,
        max_cost_usd: float | None = None,
    ) -> AgentResponse:
        context.bb_ops = []
        # Select the template for this mode without mutating instance state, so
        # the non-tool template is still intact if the mode ever changes.
        template = self._tool_template() if self.use_tools else self.template
        prompt = self._render(task, context, template)
        ops = list(context.bb_ops)
        context.bb_ops = []

        if self.use_tools and hasattr(self.provider, "generate_with_tools"):
            system_prompt = (
                TOOL_SYSTEM_PROMPT
                if self.name in _EDITING_ROLES
                else READONLY_TOOL_SYSTEM_PROMPT
            )
            inference = self.provider.generate_with_tools(
                prompt,
                role=self.name,
                tools=get_tools_for_agent(self.name),
                repo_root=self.repo_root,
                system_prompt=system_prompt,
                max_tool_turns=max_tool_turns,
                # Dollar guard for the whole task, handed down per act so the
                # loop can stop before a request that would exceed it. None
                # (or 0) means uncapped.
                max_cost_usd=max_cost_usd,
            )
        else:
            inference = self.provider.generate(prompt, role=self.name)

        response = AgentMessage(
            sender=self.name,
            receiver=task.sender,
            content=inference.response,
            kind="result",
            bb_ops=ops,
            tool_calls=getattr(inference, "tool_calls", []),
            # Carry the act's full turn-by-turn record onto the blackboard message,
            # so messages.jsonl is the trajectory rather than a summary of it. The
            # InferenceResult is consumed here and never serialised, so anything not
            # copied onto the message is lost when the run ends.
            trajectory=getattr(inference, "trajectory", []) or [],
            truncated=bool(getattr(inference, "truncated", False)),
        )
        context.log(response)
        return AgentResponse(message=response, inference=inference)

    @abstractmethod
    def _render(self, task: AgentMessage, context: Blackboard, template: str) -> str:
        ...
