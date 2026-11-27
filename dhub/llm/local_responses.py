"""Responses API provider with native tool-call history."""

import json
import os
import ssl
from ipaddress import ip_address

import httpx

from ..config.model import LOCAL_RESPONSES_BASE_URL, LLMConfig
from .http import model_endpoint, post_json
from .types import ChatResult, LLMError


class LocalResponses:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.endpoint = model_endpoint(config.base_url or LOCAL_RESPONSES_BASE_URL, "/responses", "Responses")
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
            data = post_json(client, self.endpoint, self.config.api_key, payload)
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
