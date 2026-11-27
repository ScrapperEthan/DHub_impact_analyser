"""OpenAI-compatible Chat Completions provider."""

import json

import httpx

from ..config.model import LLMConfig
from .http import model_endpoint, post_json
from .types import ChatResult, LLMError


class OpenAIChat:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.endpoint = model_endpoint(config.base_url, "/chat/completions", "Chat Completions")
        self.transport = transport

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0, *, timeout: float
    ) -> ChatResult:
        payload = {
            "model": self.config.model,
            "messages": [{key: value for key, value in msg.items() if not key.startswith("_")} for msg in messages],
            "temperature": temperature,
            "stream": False,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        with httpx.Client(transport=self.transport, timeout=min(self.config.timeout, timeout)) as client:
            data = post_json(client, self.endpoint, self.config.api_key, payload)
        try:
            raw = data["choices"][0]["message"]
            content = raw.get("content")
            calls = raw.get("tool_calls") or []
            if (content is not None and not isinstance(content, str)) or not isinstance(calls, list):
                raise TypeError
            message = {"role": "assistant", "content": content}
            if calls:
                normalized = []
                used_ids = set()
                for call in calls:
                    call_id, function = call["id"], call["function"]
                    name, arguments = function["name"], function["arguments"]
                    if not isinstance(call_id, str) or not call_id or call_id in used_ids or not isinstance(name, str):
                        raise TypeError
                    used_ids.add(call_id)
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    normalized.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    })
                message["tool_calls"] = normalized
            usage = data.get("usage") or {}
            return ChatResult(message, usage if isinstance(usage, dict) else {})
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            raise LLMError("The model gateway returned an invalid Chat Completions response.") from None
