"""
llm.py - LLM Chain-of-Thought (CoT) and Claim Generation using Google Gemini API.

Sends the Python program to Gemini to generate:
1. Chain-of-Thought (CoT) reasoning traces analyzing invariants & program properties.
2. Structured verification claims tied to specific line numbers.
3. Token usage metadata (tokens in, tokens out) for metric accounting.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class SuggestedClaim(BaseModel):
    line: int = Field(description="The source code line number where this claim / invariant holds.")
    expression: str = Field(description="The Python boolean expression for the claim, e.g. 'x >= 0' or 'i <= n'.")


class LLMVerificationPlan(BaseModel):
    cot_trace: str = Field(description="Step-by-step natural language Chain-of-Thought reasoning trace.")
    claims: List[SuggestedClaim] = Field(description="List of verification claims / assertions.")


@dataclass
class LLMResult:
    cot_trace: str
    claims: List[SuggestedClaim]
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""


SYSTEM_PROMPT = """You are an expert formal program verification assistant specializing in Python programs with integer arithmetic (QF-LIA).
Your task is to analyze the given Python function, produce a clear Chain-of-Thought (CoT) reasoning trace about loop invariants and variable bounds, and suggest formal verification claims (assertions) at specific source lines.

Rules:
1. Every claim must be a valid Python boolean expression over integer variables (e.g., 'x >= 0', 'y == x + 1', 'i <= n').
2. Do not use string, list, float, or library calls in claims. Only use basic arithmetic (+, -, *, //, %) and comparisons (==, !=, <, <=, >, >=).
3. Specify the exact line number where each claim should hold.
4. Output valid JSON matching the requested schema.
"""


def _get_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Retrieve Gemini API key from parameter, environment, or .env file."""
    if explicit_key:
        return explicit_key
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    # Try reading from .env in project root or parent
    for candidate in (Path(".env"), Path("../.env"), Path(__file__).resolve().parent.parent.parent / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return None


def generate_cot_and_claims(
    source_code: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash",
) -> LLMResult:
    """Invoke Gemini API to generate Chain-of-Thought reasoning and formal claims.

    Args:
        source_code: The Python source code.
        api_key: Gemini API key (optional if set in GEMINI_API_KEY environment variable).
        model: Gemini model identifier (default: 'gemini-3.5-flash').

    Returns:
        LLMResult containing CoT trace, claims, and token usage counts.
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable "
            "or create a .env file with GEMINI_API_KEY=your_key."
        )

    # Number the lines to help Gemini accurately anchor line numbers
    lines = source_code.splitlines()
    numbered_source = "\n".join(f"{idx + 1:3d} | {line}" for idx, line in enumerate(lines))

    prompt = f"""Here is the Python program to verify:

```python
{numbered_source}
```

Please analyze the function, provide your Chain-of-Thought reasoning trace about its state transitions and invariants, and suggest verification claims tied to the correct line numbers."""

    client = genai.Client(api_key=key)

    candidate_models = [model, "gemini-3.5-flash", "gemini-3.7-flash"]
    # De-duplicate while preserving order
    seen = set()
    models_to_try = [m for m in candidate_models if not (m in seen or seen.add(m))]

    last_err = None
    response = None
    used_model = model

    import time
    for m in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=LLMVerificationPlan,
                        temperature=0.1,
                    ),
                )
                used_model = m
                break
            except Exception as e:
                last_err = e
                time.sleep(1.0)
        if response is not None:
            break

    if response is None:
        raise RuntimeError(f"Gemini API request failed across models: {last_err}")

    # Extract token usage
    tokens_in = 0
    tokens_out = 0
    if response.usage_metadata:
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    raw_text = response.text or ""

    try:
        data = json.loads(raw_text)
        plan = LLMVerificationPlan.model_validate(data)
        cot_trace = plan.cot_trace
        claims = plan.claims
    except Exception:
        # Fallback regex parsing if schema coercion fails
        cot_trace = raw_text
        claims = []

    return LLMResult(
        cot_trace=cot_trace,
        claims=claims,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw_text=raw_text,
        model_name=model,
    )


__all__ = [
    "SuggestedClaim",
    "LLMVerificationPlan",
    "LLMResult",
    "generate_cot_and_claims",
]

