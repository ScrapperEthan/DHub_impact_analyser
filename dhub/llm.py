"""Model facade inspired by MDC Assistant's configurable provider dispatch."""

import json
import os
import ssl
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Protocol

import httpx

from .config import LOCAL_RESPONSES_BASE_URL, LLMConfig


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


def _endpoint(configured_url: str, suffix: str, protocol: str) -> httpx.URL:
    error = f"LLM_BASE_URL must be an HTTP(S) API base URL or a full {protocol} URL."
    try:
        url = httpx.URL(configured_url)
    except httpx.InvalidURL:
        raise LLMError(error) from None
    if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment:
        raise LLMError(error)
    base = str(url).rstrip("/")
    return httpx.URL(base if base.endswith(suffix) else f"{base}{suffix}")


def _post_json(client: httpx.Client, endpoint: httpx.URL, api_key: str, payload: dict) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except httpx.TimeoutException:
        raise LLMError("The model request timed out.") from None
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"The model gateway returned HTTP {exc.response.status_code}.") from None
    except httpx.HTTPError:
        raise LLMError("Unable to connect to the model gateway.") from None
    except ValueError:
        raise LLMError("The model gateway returned an invalid JSON response.") from None


class OpenAIChat:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.endpoint = _endpoint(config.base_url, "/chat/completions", "Chat Completions")
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
            data = _post_json(client, self.endpoint, self.config.api_key, payload)
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


class LocalResponses:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.endpoint = _endpoint(config.base_url or LOCAL_RESPONSES_BASE_URL, "/responses", "Responses")
        self.transport = transport
        self.verify: ssl.SSLContext | bool = True
        host = self.endpoint.host
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback and not (config.allow_nonloopback and self.endpoint.scheme == "https" and config.api_key):
            raise LLMError("A remote Responses endpoint requires HTTPS, an API key and LLM_ALLOW_NONLOOPBACK=true.")
        if self.endpoint.scheme == "https" and (ca_file := os.environ.get("SSL_CERT_FILE")):
            try:
                self.verify = ssl.create_default_context(cafile=ca_file)
            except (OSError, ValueError):
                raise LLMError("Unable to load the CA file in SSL_CERT_FILE.") from None

    @staticmethod
    def _input(messages: list[dict]) -> list[dict]:
        items = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                items.append({
                    "type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"],
                })
            elif "_responses_output" in message:
                items.extend(message["_responses_output"])
            else:
                items.append({"role": role, "content": message["content"]})
        return items

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0, *, timeout: float,
    ) -> ChatResult:
        payload = {
            "model": self.config.model, "input": self._input(messages), "store": False, "stream": False,
            "include": ["reasoning.encrypted_content"],
        }
        if self.config.max_tokens is not None:
            payload["max_output_tokens"] = self.config.max_tokens
        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        if tools:
            payload["tools"] = [{"type": "function", **tool["function"], "strict": False} for tool in tools]
            payload["tool_choice"] = "auto"
        with httpx.Client(
            transport=self.transport, timeout=min(self.config.timeout, timeout), trust_env=False, verify=self.verify,
        ) as client:
            data = _post_json(client, self.endpoint, self.config.api_key, payload)
        if data.get("status") not in (None, "completed"):
            raise LLMError("The model gateway returned an incomplete Responses result.")
        try:
            output = data["output"]
            if not isinstance(output, list):
                raise TypeError
            calls = []
            used_ids = set()
            text = []
            for item in output:
                if item["type"] == "function_call":
                    call_id, name, arguments = item["call_id"], item["name"], item["arguments"]
                    if not isinstance(call_id, str) or not call_id or call_id in used_ids or not isinstance(name, str) or not name:
                        raise TypeError
                    used_ids.add(call_id)
                    calls.append({
                        "id": call_id, "type": "function",
                        "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)},
                    })
                elif item["type"] == "message":
                    text.extend(part["text"] for part in item["content"] if part["type"] == "output_text")
            message = {"role": "assistant", "content": "\n".join(text) or None}
            if calls:
                message.update(tool_calls=calls, _responses_output=output)
            usage = data.get("usage") or {}
            return ChatResult(message, usage if isinstance(usage, dict) else {})
        except (KeyError, TypeError, ValueError):
            raise LLMError("The model gateway returned an invalid Responses result.") from None


PROVIDERS = {"openai_chat": OpenAIChat, "local_responses": LocalResponses}


def create_client(config: LLMConfig) -> ChatClient:
    factory = PROVIDERS.get(config.provider)
    if factory is None:
        raise LLMError(f"Unknown model provider. Supported providers are {', '.join(sorted(PROVIDERS))}.")
    return factory(config)
