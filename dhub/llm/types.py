"""The common model interface and result types."""

from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResult:
    message: dict
    usage: dict


class ChatClient(Protocol):
    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0, *, timeout: float
    ) -> ChatResult: ...
