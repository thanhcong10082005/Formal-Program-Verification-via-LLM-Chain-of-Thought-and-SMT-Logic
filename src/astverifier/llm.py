"""
llm.py - Gemini adapters for the two independently generated baselines.

The AST baseline keeps the original Python-expression/line-number contract.
The compatibility name ``UNANCHORED`` uses a separate Direct Formalization
contract: Gemini returns SMT-LIB2 Boolean terms and integer declarations, with
short rationales instead of a Chain-of-Thought trace.
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


class DirectSpecification(BaseModel):
    """One direct LLM-to-SMT specification."""

    formula: str = Field(description="Exactly one SMT-LIB2 Boolean term.")
    variables: List[str] = Field(
        default_factory=list,
        description="Integer symbols declared and used by the formula.",
    )
    rationale: str = Field(
        default="",
        description="A brief explanation of the formalized property.",
    )


class DirectFormalizationPlan(BaseModel):
    """Gemini response envelope for the Direct Formalization baseline."""

    specifications: List[DirectSpecification] = Field(
        default_factory=list,
        description="Independent SMT-LIB2 specifications inferred from the source.",
    )


@dataclass
class LLMResult:
    cot_trace: str
    claims: List[SuggestedClaim]
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""


@dataclass
class DirectLLMResult:
    specifications: List[DirectSpecification]
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


DIRECT_SYSTEM_PROMPT = """You are an expert formal methods assistant.
Your task is to formalize properties of the supplied Python program directly
as quantifier-free linear integer arithmetic (QF-LIA) in SMT-LIB2.

Rules:
1. Return one or more specifications. Each `formula` must be exactly one
   SMT-LIB2 Boolean term, not a command, declaration, Python expression, or
   Markdown code block.
2. Use only integer variables listed in that specification's `variables`
   array. Every listed variable is an SMT-LIB2 Int symbol.
3. Use QF-LIA operators only: Boolean connectives, integer comparisons,
   addition, subtraction, unary negation, multiplication by an integer
   constant, and division/modulo by a nonzero integer constant.
4. Include source assertions and useful properties inferred from the source,
   but do not mention or invent line numbers, anchors, NodeIds, AST nodes,
   program locations, reachability, path conditions, or replay.
5. Give a brief rationale for each formula. Do not provide a private or
   step-by-step Chain-of-Thought trace.
6. Return valid JSON matching the requested schema.
"""


DIRECT_TIMEOUT_MS = 120_000


def _make_client(api_key: str):
    """Create a Gemini client with a bounded request timeout.

    The small fallback keeps test doubles and older ``google-genai`` versions
    that do not expose ``http_options`` usable without changing semantics.
    """
    try:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=DIRECT_TIMEOUT_MS),
        )
    except TypeError:
        return genai.Client(api_key=api_key)


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

    client = _make_client(key)

    # Keep a recorded baseline tied to the configured model.  In particular,
    # do not silently mix another model into benchmark token totals.
    models_to_try = [model]

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
        raise RuntimeError(f"Gemini API request failed: {last_err}")

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
    except Exception as exc:
        raise RuntimeError(f"Gemini returned invalid anchored JSON: {exc}") from exc

    return LLMResult(
        cot_trace=cot_trace,
        claims=claims,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw_text=raw_text,
        model_name=used_model,
    )


def generate_direct_formalization(
    source_code: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash",
) -> DirectLLMResult:
    """Invoke Gemini for the Direct Formalization baseline.

    This response is independent from :func:`generate_cot_and_claims`: it has
    no line-number field and cannot be passed through the legacy Python claim
    binder.
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable "
            "or create a .env file with GEMINI_API_KEY=your_key."
        )

    prompt = f"""Formalize the following Python program directly as QF-LIA SMT-LIB2 specifications.

```python
{source_code}
```

Return independent source assertions and useful inferred properties. The
formula text must be SMT-LIB2, and each specification must declare the Int
symbols it uses in `variables`."""

    client = _make_client(key)

    last_err = None
    response = None
    import time

    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=DIRECT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=DirectFormalizationPlan,
                    temperature=0.1,
                ),
            )
            break
        except Exception as exc:
            last_err = exc
            if attempt == 0:
                time.sleep(1.0)

    if response is None:
        raise RuntimeError(f"Gemini direct-formalization request failed: {last_err}")

    tokens_in = 0
    tokens_out = 0
    if response.usage_metadata:
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    raw_text = response.text or ""
    try:
        data = json.loads(raw_text)
        plan = DirectFormalizationPlan.model_validate(data)
        specifications = plan.specifications
    except Exception as exc:
        # Surface malformed JSON to the pipeline as a translation failure.
        # Treating it as a successful empty pool would hide an LLM failure.
        raise RuntimeError(
            f"Gemini returned invalid direct-formalization JSON: {exc}"
        ) from exc

    return DirectLLMResult(
        specifications=specifications,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw_text=raw_text,
        model_name=model,
    )


__all__ = [
    "SuggestedClaim",
    "LLMVerificationPlan",
    "LLMResult",
    "DirectSpecification",
    "DirectFormalizationPlan",
    "DirectLLMResult",
    "generate_cot_and_claims",
    "generate_direct_formalization",
]
