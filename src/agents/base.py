from abc import ABC, abstractmethod
from dataclasses import dataclass

from models.inference import InferenceResult
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.tools import get_tools_for_agent
from utils.prompt_loader import load_prompt_or_default


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
        # Tool calling is opt-in per agent; only the commandcode provider
        # actually exercises it. Other providers ignore use_tools.
        self.use_tools = False
        # Active instance repo root for tool exploration (set per strategy run).
        self.repo_root: str | None = None

    def act(self, task: AgentMessage, context: Blackboard) -> AgentResponse:
        context.bb_ops = []
        prompt = self._render(task, context)
        ops = list(context.bb_ops)
        context.bb_ops = []

        if self.use_tools and hasattr(self.provider, "generate_with_tools"):
            inference = self.provider.generate_with_tools(
                prompt,
                role=self.name,
                tools=get_tools_for_agent(self.name),
                repo_root=self.repo_root,
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
        )
        context.log(response)
        return AgentResponse(message=response, inference=inference)

    @abstractmethod
    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        ...
