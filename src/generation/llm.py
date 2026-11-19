"""Large language model clients: Gemini first, Groq when Gemini is unavailable.

Plain HTTPS POST with the standard library; no SDK. Only two hosts are ever
contacted: ``generativelanguage.googleapis.com`` and ``api.groq.com``. API
keys travel in request headers, never in URLs, and no key, prompt, or server
error text is ever put in an exception or log message.

Configuration comes from environment variables (see ``.env.example``):
``GEMINI_API_KEY``, ``GROQ_API_KEY``, ``HRI_GEMINI_MODEL``, ``HRI_GROQ_MODEL``.
A provider without a key is skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
import socket
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
# llama-3.3-70b-versatile is listed by Groq but not available to free keys
# (HTTP 404 model_not_found, 2026-11-19).
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_TOKENS = 1024
# One retry of the same provider for a temporary server failure or timeout;
# never for 429, so a used-up free quota is not hit again.
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})
RETRY_DELAY_SECONDS = 2.0
# Some API front ends reject urllib's default User-Agent.
USER_AGENT = "healthcare-research-intelligence"

# (url, headers, body, timeout) -> response body; raises HTTPError/URLError.
PostTransport = Callable[[str, Mapping[str, str], bytes, float], bytes]


class LLMError(RuntimeError):
    """A provider could not produce an answer. Messages are safe to show."""


class LLMRateLimitedError(LLMError):
    """The provider refused because of a rate limit or quota (HTTP 429)."""


@dataclass(frozen=True)
class LLMReply:
    text: str
    provider: str
    model: str


class LLMClient(Protocol):
    """One provider, or a chain of them, that turns a prompt into text."""

    provider: str
    model: str

    def generate(self, system: str, user: str) -> LLMReply: ...


def default_post(
    url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> bytes:
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class _HttpLLM:
    provider = ""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: PostTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        if not api_key.strip():
            raise LLMError(f"{self.provider} has no API key configured.")
        self._api_key = api_key.strip()
        self.model = model
        self._post = transport or default_post
        self._sleep = sleeper
        self.max_output_tokens = max_output_tokens

    def _call(self, url: str, headers: Mapping[str, str], payload: dict) -> dict:
        body = json.dumps(payload).encode()
        for attempt in (1, 2):
            try:
                raw = self._post(url, headers, body, TIMEOUT_SECONDS)
                break
            except HTTPError as error:
                if error.code == 429:
                    raise LLMRateLimitedError(
                        f"{self.provider} is rate limited or out of quota."
                    ) from None
                if attempt == 2 or error.code not in RETRY_STATUS_CODES:
                    raise LLMError(
                        f"{self.provider} returned HTTP status {error.code}."
                    ) from None
            except (URLError, socket.timeout, TimeoutError, OSError):
                if attempt == 2:
                    raise LLMError(f"{self.provider} could not be reached.") from None
            self._sleep(RETRY_DELAY_SECONDS)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise LLMError(f"{self.provider} returned malformed JSON.") from None

    def _reply(self, text: object) -> LLMReply:
        if not isinstance(text, str) or not text.strip():
            raise LLMError(f"{self.provider} returned no answer text.")
        return LLMReply(text.strip(), self.provider, self.model)


class GeminiClient(_HttpLLM):
    """Google Gemini API ``generateContent``."""

    provider = "Gemini"

    def generate(self, system: str, user: str) -> LLMReply:
        data = self._call(
            GEMINI_URL.format(model=quote(self.model, safe="-._")),
            {"x-goog-api-key": self._api_key},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": 0.1, "maxOutputTokens": self.max_output_tokens,
                },
            },
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        except (KeyError, IndexError, TypeError, AttributeError):
            text = None  # for example, a blocked or empty response
        return self._reply(text)


class GroqClient(_HttpLLM):
    """Groq's OpenAI-compatible chat completions API."""

    provider = "Groq"

    def generate(self, system: str, user: str) -> LLMReply:
        data = self._call(
            GROQ_URL,
            {"Authorization": f"Bearer {self._api_key}"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": self.max_output_tokens,
            },
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        return self._reply(text)


class FallbackLLM:
    """Try each client in order; move on whenever one fails.

    ``before_request`` runs before every attempt (the daily cap); it raises
    ``LLMError`` to stop. If every client fails, the last error is raised with
    a summary of each provider's failure.
    """

    def __init__(
        self,
        clients: Sequence[LLMClient],
        *,
        before_request: Callable[[], None] = lambda: None,
    ) -> None:
        self.clients = tuple(clients)
        self.provider = "+".join(client.provider for client in self.clients)
        self.model = "+".join(client.model for client in self.clients)
        self._before_request = before_request

    def generate(self, system: str, user: str) -> LLMReply:
        failures: list[LLMError] = []
        for client in self.clients:
            self._before_request()
            try:
                return client.generate(system, user)
            except LLMError as error:
                failures.append(error)
        message = " ".join(map(str, failures)) or "No AI provider is configured."
        if failures and all(isinstance(e, LLMRateLimitedError) for e in failures):
            raise LLMRateLimitedError(message)
        raise LLMError(message)


def build_default_llm(
    environment: Mapping[str, str] | None = None,
    *,
    before_request: Callable[[], None] = lambda: None,
    transport: PostTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
) -> FallbackLLM:
    """Gemini, then Groq, each only if its API key is set."""
    env = environment if environment is not None else os.environ
    clients: list[LLMClient] = []
    for key_name, model_name, default_model, client_type in (
        ("GEMINI_API_KEY", "HRI_GEMINI_MODEL", DEFAULT_GEMINI_MODEL, GeminiClient),
        ("GROQ_API_KEY", "HRI_GROQ_MODEL", DEFAULT_GROQ_MODEL, GroqClient),
    ):
        key = env.get(key_name, "").strip()
        if key:
            model = env.get(model_name, "").strip() or default_model
            clients.append(client_type(
                key, model, transport=transport, sleeper=sleeper,
                max_output_tokens=max_output_tokens,
            ))
    return FallbackLLM(clients, before_request=before_request)
