"""Gemini adapter for the Core Proposed Method.

The LLM is used **only** to propose claims and their source lines. It never
decides a verdict: the anchored claim pool is re-derived from the AST and
every claim is checked by the trusted symbolic executor and Z3.

This module intentionally exposes no "direct formalization" surface. That
baseline (``UNANCHORED``) is installed separately during the Baseline phase
and must not leak into the Core Proposed Method.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

REQUEST_TIMEOUT_MS = 120_000
MAX_ATTEMPTS = 2


class SuggestedClaim(BaseModel):
    """One claim proposed by the LLM, tied to a source line."""

    line: int = Field(
        description="The source code line number where this claim holds."
    )
    expression: str = Field(
        description="The Python boolean expression for the claim, e.g. 'x >= 0'."
    )


class LLMVerificationPlan(BaseModel):
    """The JSON envelope the model must return."""

    cot_trace: str = Field(
        description="Step-by-step natural language Chain-of-Thought reasoning trace."
    )
    claims: list[SuggestedClaim] = Field(
        description="List of verification claims / assertions."
    )


@dataclass
class LLMResult:
    """The outcome of one claim-generation call."""

    cot_trace: str
    claims: list[SuggestedClaim]
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""


SYSTEM_PROMPT = """You are an expert formal program verification assistant \
specializing in Python programs with integer arithmetic (QF-LIA).
Your task is to analyze the given Python function, produce a clear \
Chain-of-Thought (CoT) reasoning trace about loop invariants and variable \
bounds, and suggest formal verification claims (assertions) at specific \
source lines.

Rules:
1. Every claim must be a valid Python boolean expression over integer \
variables (e.g., 'x >= 0', 'y == x + 1', 'i <= n').
2. Do not use string, list, float, or library calls in claims. Only use \
basic arithmetic (+, -, *, //, %) and comparisons (==, !=, <, <=, >, >=).
3. Specify the exact line number where each claim should hold.
4. Output valid JSON matching the requested schema.
"""


def make_client(api_key: str):
    """Create a Gemini client with a bounded request timeout.

    The fallback keeps test doubles and older ``google-genai`` versions that
    do not expose ``http_options`` usable without changing semantics.
    """
    try:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
    except TypeError:
        return genai.Client(api_key=api_key)


def resolve_api_key(explicit_key: str | None = None) -> str | None:
    """Retrieve the Gemini API key from the argument, the environment, or a
    local ``.env`` file."""
    if explicit_key:
        return explicit_key
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    candidates = (
        Path(".env"),
        Path("../.env"),
        Path(__file__).resolve().parent.parent.parent.parent / ".env",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def generate_cot_and_claims(
    source_code: str,
    *,
    api_key: str | None = None,
    model: str = "gemini-3.5-flash-lite",
) -> LLMResult:
    """Invoke Gemini to generate a Chain-of-Thought trace and formal claims.

    Args:
        source_code: The Python source code to analyse.
        api_key: Gemini API key. Optional when ``GEMINI_API_KEY`` is set.
        model: Gemini model identifier.

    Returns:
        An :class:`LLMResult` with the CoT trace, the proposed claims and the
        token usage of this call.

    Raises:
        ValueError: No API key could be resolved.
        RuntimeError: The API call failed or returned malformed JSON.
    """
    key = resolve_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment "
            "variable or create a .env file with GEMINI_API_KEY=your_key."
        )

    numbered_source = "\n".join(
        f"{index + 1:3d} | {line}"
        for index, line in enumerate(source_code.splitlines())
    )
    prompt = f"""Here is the Python program to verify:

```python
{numbered_source}
```

Please analyze the function, provide your Chain-of-Thought reasoning trace about \
its state transitions and invariants, and suggest verification claims tied to \
the correct line numbers."""

    client = make_client(key)
    response = None
    last_err: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=LLMVerificationPlan,
                    temperature=0.1,
                ),
            )
            break
        except Exception as exc:  # noqa: BLE001 - retried and re-raised below
            last_err = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(1.0)

    if response is None:
        raise RuntimeError(f"Gemini API request failed: {last_err}")

    tokens_in = 0
    tokens_out = 0
    if response.usage_metadata:
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    raw_text = response.text or ""
    try:
        plan = LLMVerificationPlan.model_validate(json.loads(raw_text))
    except Exception as exc:
        raise RuntimeError(f"Gemini returned invalid anchored JSON: {exc}") from exc

    return LLMResult(
        cot_trace=plan.cot_trace,
        claims=plan.claims,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw_text=raw_text,
        model_name=model,
    )


__all__ = [
    "LLMResult",
    "LLMVerificationPlan",
    "SuggestedClaim",
    "generate_cot_and_claims",
    "make_client",
    "resolve_api_key",
]
