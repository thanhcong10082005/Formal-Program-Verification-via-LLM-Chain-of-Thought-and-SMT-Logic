"""The LLM port used by the LLM-driven baselines.

Every baseline that needs a language model talks to it through the
:class:`LLMClient` protocol. Three concrete clients ship:

* :class:`GeminiClient` -- backed by ``astverifier.llm.gemini``.
* :class:`OpenAICompatibleClient` -- talks to any OpenAI-compatible
  ``/chat/completions`` endpoint (Ollama, Groq, OpenRouter, vLLM, OpenAI,
  Mistral) using Python's stdlib ``urllib`` with zero extra dependencies.
* :class:`StubClient` -- a deterministic, offline stand-in used by the
  contract tests so that the test suite never spends a token.

:class:`RateLimitedClient` wraps any :class:`LLMClient` to enforce a
requests-per-minute ceiling.

A baseline never imports :mod:`astverifier.llm.gemini` directly; it receives
a client. That is what makes the contract test able to run offline.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMReply:
    """One reply from a language model.

    Attributes:
        text: The raw text the model produced.
        tokens_in: Prompt tokens consumed.
        tokens_out: Completion tokens produced.
        model: The model identifier.
    """

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""

    def json(self) -> Any:
        """Parse the reply as JSON, tolerating Markdown code fences.

        Raises:
            ValueError: The reply contains no parseable JSON.
        """
        return parse_json(self.text)


@runtime_checkable
class LLMClient(Protocol):
    """The interface every baseline uses to talk to a language model."""

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
    ) -> LLMReply:
        """Return the model's reply to ``prompt``."""
        ...


def parse_json(text: str) -> Any:
    """Parse JSON out of a model reply.

    Model replies routinely wrap the JSON in a Markdown fence or add a
    sentence of prose around it. This helper finds the outermost JSON value.

    Raises:
        ValueError: No JSON value could be found.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in the reply")


class StubClient:
    """A deterministic, offline stand-in for a language model.

    The client answers from a fixed table of reply templates, so a contract
    test can exercise every LLM code path -- JSON parsing, verdict
    extraction, token accounting -- without touching the network.

    Attributes:
        replies: Replies returned in order. The last one repeats.
        model: The model identifier reported in every reply.
    """

    def __init__(self, replies: list[str] | str | None = None, *, model: str = "stub"):
        if replies is None:
            replies = [self._default_reply()]
        elif isinstance(replies, str):
            replies = [replies]
        self.replies = list(replies)
        self.model = model
        self.calls: list[str] = []

    @staticmethod
    def _default_reply() -> str:
        return json.dumps(
            {
                "verdict": "VERIFIED",
                "reason": "stub client: no evidence of a violation",
                "claims": [],
                "spec": {},
                "invariants": [],
            }
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
    ) -> LLMReply:
        """Return the next canned reply."""
        self.calls.append(prompt)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        text = self.replies[index]
        return LLMReply(
            text=text,
            tokens_in=max(1, len(prompt) // 4),
            tokens_out=max(1, len(text) // 4),
            model=self.model,
        )


class GeminiClient:
    """The real client, backed by ``astverifier.llm.gemini``.

    Attributes:
        model: The Gemini model identifier.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gemini-3.5-flash-lite",
    ) -> None:
        from ..llm.gemini import make_client, resolve_api_key

        key = resolve_api_key(api_key)
        if not key:
            raise ValueError(
                "GEMINI_API_KEY is not set; cannot construct a GeminiClient"
            )
        self._client = make_client(key)
        self.model = model

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
    ) -> LLMReply:
        """Ask Gemini for a reply and return it with its token usage."""
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            **({"system_instruction": system} if system else {}),
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        tokens_in = 0
        tokens_out = 0
        if response.usage_metadata:
            tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            tokens_out = (
                getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            )
        return LLMReply(
            text=response.text or "",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self.model,
        )


_RETRYABLE_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504})


class OpenAICompatibleClient:
    """Client for any OpenAI-compatible ``/chat/completions`` HTTP endpoint.

    Works with Ollama (``http://localhost:11434/v1``), Groq, OpenRouter,
    Mistral, vLLM, LM Studio, and OpenAI using only the Python standard
    library.

    Attributes:
        base_url: API root ending in ``/v1`` (or without trailing slash).
        model: The model identifier accepted by the server.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen2.5-coder:7b",
        api_key: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
        seed: int | None = 42,
        max_tokens: int | None = 1024,
        reasoning_effort: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_base_delay = max(0.1, retry_base_delay)
        self.seed = seed
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
    ) -> LLMReply:
        """Send a chat completion request and return text + token counts."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return self._parse_response(data)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in _RETRYABLE_HTTP_CODES or attempt >= self.max_retries:
                    detail = ""
                    with contextlib.suppress(Exception):
                        detail = exc.read().decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(
                        f"OpenAI-compatible endpoint HTTP {exc.code}: {detail}"
                    ) from exc
                delay = self._retry_delay(exc, attempt)
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"OpenAI-compatible endpoint connection failed: {exc}"
                    ) from exc
                time.sleep(self.retry_base_delay * (2**attempt))

        raise RuntimeError(f"OpenAI-compatible request failed: {last_exc}")

    def _retry_delay(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            with contextlib.suppress(ValueError):
                return max(0.1, float(retry_after))
        return self.retry_base_delay * (2**attempt)

    def _parse_response(self, data: dict[str, Any]) -> LLMReply:
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                )

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        model = str(data.get("model") or self.model)
        return LLMReply(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
        )


class RateLimitedClient:
    """Thread-safe wrapper that paces calls to an :class:`LLMClient`."""

    def __init__(self, inner: LLMClient, *, rpm: float) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._inner = inner
        self._min_interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
    ) -> LLMReply:
        """Wait if needed to honour ``rpm``, then delegate to the wrapped client."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval
        return self._inner.complete(prompt, system=system, temperature=temperature)


@dataclass
class TokenBudget:
    """Accumulates the token usage of a baseline run.

    Attributes:
        tokens_in: Total prompt tokens.
        tokens_out: Total completion tokens.
        calls: Number of model calls.
    """

    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    replies: list[LLMReply] = field(default_factory=list)

    def add(self, reply: LLMReply) -> None:
        """Account one reply."""
        self.tokens_in += reply.tokens_in
        self.tokens_out += reply.tokens_out
        self.calls += 1
        self.replies.append(reply)


__all__ = [
    "GeminiClient",
    "LLMClient",
    "LLMReply",
    "OpenAICompatibleClient",
    "RateLimitedClient",
    "StubClient",
    "TokenBudget",
    "parse_json",
]
