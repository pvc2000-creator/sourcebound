"""Optional language-model adapter for any OpenAI-compatible Chat Completions endpoint
(Ollama, LM Studio, llama.cpp server, OpenAI, Groq, OpenRouter, Together, ...).

The model is only ever asked to *plan* and to *write from numbered evidence*. Its
output is treated as untrusted text: plans are schema-validated and every written
sentence is checked against the evidence it cites before it reaches the user.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from .config import LLMSettings


class LLMError(RuntimeError):
    """A provider problem, described without secrets."""


class ChatModel(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 900, json_mode: bool = False) -> str: ...


class OpenAICompatibleModel:
    def __init__(self, settings: LLMSettings, max_attempts: int = 3) -> None:
        if not settings.enabled:
            raise LLMError("No language model is configured.")
        self.settings = settings
        self.name = settings.model
        self.max_attempts = max_attempts

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "User-Agent": "Sourcebound/0.1"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        body = json.dumps(payload).encode("utf-8")
        last_error = "unknown error"
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as resp:
                    raw = resp.read(2_000_000)
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise LLMError("The model provider returned an unexpected response shape.")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise LLMError(f"The model provider rejected the API key (HTTP {exc.code}). Check SOURCEBOUND_LLM_API_KEY.") from None
                if exc.code == 404:
                    raise LLMError(
                        "The model endpoint or model name was not found (HTTP 404). Check SOURCEBOUND_LLM_BASE_URL and SOURCEBOUND_LLM_MODEL."
                    ) from None
                last_error = f"HTTP {exc.code}"
                if exc.code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    break
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                delay = min(float(retry_after), 8.0) if retry_after.replace(".", "", 1).isdigit() else 1.5 * attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = f"connection failed ({type(reason).__name__})"
                delay = 1.5 * attempt
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise LLMError("The model provider returned a response that is not valid JSON.") from None
            if attempt < self.max_attempts:
                time.sleep(delay)
        raise LLMError(f"The model provider could not be reached: {last_error}.")

    def complete(self, system: str, user: str, *, max_tokens: int = 900, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("The model provider returned an unexpected response shape.") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("The model returned an empty answer.")
        return content.strip()


def build_model(settings: LLMSettings) -> ChatModel | None:
    return OpenAICompatibleModel(settings) if settings.enabled else None


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model output (tolerates code fences)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("The model did not return a JSON object.")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise LLMError("The model returned malformed JSON.") from None
    if not isinstance(value, dict):
        raise LLMError("The model did not return a JSON object.")
    return value
