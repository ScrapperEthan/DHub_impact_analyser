"""HTTP request and response handling shared by the model providers."""

import httpx

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


def post_json(client: httpx.Client, endpoint: httpx.URL, api_key: str, payload: dict) -> dict:
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
