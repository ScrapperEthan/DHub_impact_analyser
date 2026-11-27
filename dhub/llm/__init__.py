"""Model provider registry and public client interface."""

from ..config.model import LOCAL_RESPONSES_BASE_URL as LOCAL_RESPONSES_BASE_URL
from ..config.model import LLMConfig as LLMConfig
from .local_responses import LocalResponses
from .openai_chat import OpenAIChat
from .types import ChatClient as ChatClient, ChatResult as ChatResult, LLMError as LLMError


PROVIDERS = {"openai_chat": OpenAIChat, "local_responses": LocalResponses}


def create_client(config: LLMConfig) -> ChatClient:
    factory = PROVIDERS.get(config.provider)
    if factory is None:
        raise LLMError(f"Unknown model provider. Supported providers are {', '.join(sorted(PROVIDERS))}.")
    return factory(config)
