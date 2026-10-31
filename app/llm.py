"""Thin LLM provider clients. Both providers have a free option:

* OllamaProvider  - local open-source model (llama3.2, qwen2.5, ...). No account,
                    no API key, no rate limits, data never leaves your machine.
* GeminiProvider  - Google AI Studio free tier (needs a free API key).

The LLM is used for ONE narrow job: turn a question into a small JSON object
{"intent": ..., "params": {...}}. See interpreter.py for how that output is
validated before it is trusted.
"""
from __future__ import annotations

import httpx

from . import config


class LLMError(Exception):
    """Any failure talking to the LLM (not running, timeout, bad response...)."""


class BaseProvider:
    name = "base"

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:  # pragma: no cover
        raise NotImplementedError


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None, timeout: float | None = None):
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.LLM_TIMEOUT_SECONDS

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",                 # Ollama constrains output to valid JSON
            "options": {"temperature": 0},    # deterministic routing
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            resp = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except httpx.ConnectError as exc:
            raise LLMError(f"Ollama is not reachable at {self.url} (is 'ollama serve' running?)") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama timed out after {self.timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except (KeyError, ValueError) as exc:
            raise LLMError(f"Unexpected response format from Ollama: {exc}") from exc


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.GEMINI_MODEL
        self.timeout = timeout or config.LLM_TIMEOUT_SECONDS
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set (get a free key at https://aistudio.google.com/apikey)")

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        try:
            # key goes in a header, never in the URL, so it cannot leak into logs
            resp = httpx.post(url, json=payload, headers={"x-goog-api-key": self.api_key},
                              timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except httpx.TimeoutException as exc:
            raise LLMError(f"Gemini timed out after {self.timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise LLMError("Gemini free-tier rate limit reached (HTTP 429)") from exc
            raise LLMError(f"Gemini returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach Gemini: {exc.__class__.__name__}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected response format from Gemini: {exc}") from exc


def get_provider(name: str | None = None) -> BaseProvider | None:
    """Return the configured provider, or None when running without an LLM."""
    name = (name or config.LLM_PROVIDER).lower()
    if name in ("none", "", "off", "rules"):
        return None
    if name == "ollama":
        return OllamaProvider()
    if name == "gemini":
        return GeminiProvider()
    raise LLMError(f"Unknown LLM_PROVIDER '{name}'. Use ollama, gemini or none.")
