from abc import ABC, abstractmethod
from dataclasses import dataclass

from agentbench.core.models.inference import InferenceResult
from agentbench.core.agents.messages import AgentMessage
from agentbench.core.agents.blackboard import Blackboard
from agentbench.core.utils.prompt_loader import load_prompt_or_default


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

    def act(self, task: AgentMessage, context: Blackboard) -> AgentResponse:
        prompt = self._render(task, context)
        inference = self.provider.generate(prompt, role=self.name)
        response = AgentMessage(
            sender=self.name,
            receiver=task.sender,
            content=inference.response,
            kind="result",
        )
        context.log(response)
        return AgentResponse(message=response, inference=inference)

    @abstractmethod
    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        ...
