"""HTTP request and response handling shared by the model providers."""

import httpx

from ..sources.utils import describe_error
from .types import LLMError


def model_endpoint(configured_url: str, suffix: str, protocol: str) -> httpx.URL:
    error = f"LLM_BASE_URL must be an HTTP(S) API base URL or a full {protocol} URL."
    try:
        url = httpx.URL(configured_url)
    except httpx.InvalidURL:
        raise LLMError(error) from None
    if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment:
        raise LLMError(error)
    base = str(url).rstrip("/")
    return httpx.URL(base if base.endswith(suffix) else f"{base}{suffix}")


def _masked(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return describe_error(text, 300)


def _gateway_detail(response: httpx.Response, api_key: str) -> str:
    """Return the gateway's own error message, such as an unknown model or unsupported parameter."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        for value in (error.get("message") if isinstance(error, dict) else error, body.get("message"), body.get("detail")):
            if isinstance(value, str) and value.strip():
                return _masked(value, api_key)
    try:
        return _masked(response.text, api_key)
    except (UnicodeError, httpx.ResponseNotRead):
        return ""


def post_json(client: httpx.Client, endpoint: httpx.URL, api_key: str, payload: dict) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except httpx.TimeoutException as exc:
        raise LLMError(f"The model request timed out ({type(exc).__name__}).") from None
    except httpx.HTTPStatusError as exc:
        detail = _gateway_detail(exc.response, api_key)
        message = f"The model gateway returned HTTP {exc.response.status_code}."
        raise LLMError(f"{message} {detail}" if detail else message) from None
    except httpx.HTTPError as exc:
        raise LLMError(f"Unable to connect to the model gateway ({_masked(f'{type(exc).__name__}: {exc}', api_key)}).") from None
    except ValueError:
        raise LLMError("The model gateway returned an invalid JSON response.") from None
