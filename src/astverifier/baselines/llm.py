"""The LLM port used by the LLM-driven baselines.

Every baseline that needs a language model talks to it through the
:class:`LLMClient` protocol. Two implementations ship:

* :class:`GeminiClient` -- the real one, backed by ``astverifier.llm.gemini``.
* :class:`StubClient` -- a deterministic, offline stand-in used by the
  contract tests so that the test suite never spends a Gemini token.

A baseline never imports :mod:`astverifier.llm.gemini` directly; it receives
a client. That is what makes the contract test able to run offline.
"""

from __future__ import annotations

import json
import re
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
    "StubClient",
    "TokenBudget",
    "parse_json",
]
