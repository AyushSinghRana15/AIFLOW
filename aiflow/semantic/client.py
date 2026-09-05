"""Minimal OpenRouter client.

Uses `urllib` rather than `requests` so the semantic analyzer adds no runtime
dependency to a package whose core job is parsing and validating JSON.

The API key is read from the environment and never written to disk, never
included in a cache key, and redacted from any error this module raises.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

__all__ = ["OpenRouterClient", "ClientError", "MissingKey",
           "DEFAULT_MODEL", "ENV_KEY", "ENV_MODEL"]

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
ENV_KEY = "OPENROUTER_API_KEY"
ENV_MODEL = "AIFLOW_MODEL"
DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324:free"
REFERER = "https://github.com/AyushSinghRana15/AIFLOW"

_KEY_PATTERN = re.compile(r"sk-or-[A-Za-z0-9\-_]+")


class ClientError(RuntimeError):
    pass


class MissingKey(ClientError):
    pass


def _redact(text: str) -> str:
    return _KEY_PATTERN.sub("sk-or-***", text)


@dataclass
class OpenRouterClient:
    api_key: str
    model: str = DEFAULT_MODEL
    timeout: int = 120

    @classmethod
    def from_env(cls, model: str | None = None) -> "OpenRouterClient":
        key = os.environ.get(ENV_KEY, "").strip()
        if not key:
            raise MissingKey(
                f"{ENV_KEY} is not set.\n"
                f"  export {ENV_KEY}='sk-or-...'   # from https://openrouter.ai/keys\n"
                f"Keep it in your shell profile or a .env file that is gitignored — "
                f"never in the repository."
            )
        return cls(api_key=key, model=model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL)

    def complete(self, system: str, user: str, *, max_tokens: int = 4000,
                 temperature: float = 0.0) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }).encode()

        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": REFERER,
            "X-Title": "AIFLOW",
        })

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = _redact(exc.read().decode(errors="replace")[:400])
            if exc.code == 401:
                raise ClientError(
                    f"OpenRouter rejected the key (401). Check {ENV_KEY}, and that the "
                    f"key has not been revoked.\n{detail}") from None
            if exc.code == 402:
                raise ClientError(
                    f"OpenRouter reports insufficient credit (402). A ':free' model is "
                    f"required on a free key; {ENV_MODEL} is currently "
                    f"{self.model!r}.\n{detail}") from None
            if exc.code == 429:
                raise ClientError(
                    f"Rate limited by OpenRouter (429). The free tier is capped per day; "
                    f"wait for it to reset.\n{detail}") from None
            if exc.code in (400, 404):
                raise ClientError(
                    f"OpenRouter rejected the request ({exc.code}). The model "
                    f"{self.model!r} may no longer exist — free model ids change. "
                    f"Pick a current one at https://openrouter.ai/models?q=free and set "
                    f"{ENV_MODEL}.\n{detail}") from None
            raise ClientError(f"OpenRouter returned {exc.code}.\n{detail}") from None
        except urllib.error.URLError as exc:
            raise ClientError(f"Could not reach OpenRouter: {_redact(str(exc.reason))}") from None

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ClientError(
                f"Unexpected response shape from OpenRouter: "
                f"{_redact(json.dumps(payload)[:400])}") from None


def extract_json(text: str):
    """Pull a JSON object out of a model response.

    Free models frequently wrap JSON in prose or a markdown fence even when
    told not to, so parsing is defensive: strict parse first, then a fenced
    block, then the outermost braces.
    """
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except ValueError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise ClientError(f"Model did not return parseable JSON. First 300 chars:\n{text[:300]}")
