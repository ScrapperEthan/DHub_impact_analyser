"""Model facade inspired by MDC Assistant's configurable provider dispatch."""

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import LLMConfig


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


class OpenAIChat:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        url = httpx.URL(config.base_url)
        if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment:
            raise LLMError("LLM_BASE_URL 必须是 HTTP(S) API 根地址或完整 Chat Completions 地址")
        base = str(url).rstrip("/")
        self.endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
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
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        try:
            with httpx.Client(transport=self.transport, timeout=min(self.config.timeout, timeout)) as client:
                response = client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
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
        except httpx.TimeoutException:
            raise LLMError("模型请求超时") from None
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"模型网关返回 HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise LLMError("模型网关连接失败") from None
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            raise LLMError("模型网关返回的数据不符合 Chat Completions 约定") from None


PROVIDERS = {"openai_chat": OpenAIChat}


def create_client(config: LLMConfig) -> ChatClient:
    factory = PROVIDERS.get(config.provider)
    if factory is None:
        raise LLMError(f"未配置此模型适配器，可用类型为 {', '.join(sorted(PROVIDERS))}")
    return factory(config)
