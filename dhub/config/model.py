"""Model gateway settings shared by the LLM providers."""

from dataclasses import dataclass, field


LOCAL_RESPONSES_BASE_URL = "http://127.0.0.1:4141/v1"


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "offline"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    max_tokens: int | None = None
    timeout: float = 40
    reasoning_effort: str = ""
    allow_nonloopback: bool = False
