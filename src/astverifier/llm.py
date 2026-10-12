"""
llm.py - Gemini adapters for the two independently generated baselines.

The AST baseline keeps the original Python-expression/line-number contract.
The compatibility name ``UNANCHORED`` uses a separate SatLM-style declarative
contract: Gemini returns jointly assumed SMT-LIB2 constraints and target
specifications, with short rationales instead of a Chain-of-Thought trace.
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


class DirectFormula(BaseModel):
    """One direct LLM-to-SMT QF-LIA formula."""

    formula: str = Field(description="Exactly one SMT-LIB2 Boolean term.")
    variables: List[str] = Field(
        default_factory=list,
        description="Integer symbols declared and used by the formula.",
    )
    rationale: str = Field(
        default="",
        description="A brief explanation, not a Chain-of-Thought trace.",
    )


class DirectConstraint(DirectFormula):
    """A formula jointly assumed as part of the declarative context."""


class DirectSpecification(DirectFormula):
    """A target property checked as a consequence of the constraints."""


class DirectFormalizationPlan(BaseModel):
    """Gemini response envelope for the SatLM-style direct baseline."""

    constraints: List[DirectConstraint] = Field(
        default_factory=list,
        description=(
            "SMT-LIB2 facts and relations jointly assumed for the whole "
            "declarative specification."
        ),
    )

    specifications: List[DirectSpecification] = Field(
        default_factory=list,
        description=(
            "SMT-LIB2 target properties that should follow from the joint "
            "constraints."
        ),
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
    constraints: List[DirectConstraint]
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
Your task is to formalize the supplied Python program directly, using a
SatLM-style declarative specification in quantifier-free linear integer
arithmetic (QF-LIA) SMT-LIB2.  A specification has two parts: constraints are
jointly assumed facts/relations, and specifications are target properties to
check as consequences of those constraints.

Rules:
1. Return a JSON object with `constraints` and `specifications` arrays.
2. Each array entry's `formula` must be exactly one SMT-LIB2 Boolean term, not
   a command, declaration, Python expression, or Markdown code block.
3. Use only integer variables listed in that entry's `variables` array. Every
   listed variable is an SMT-LIB2 Int symbol.
4. Use QF-LIA operators only: Boolean connectives, integer comparisons,
   addition, subtraction, unary negation, multiplication by an integer
   constant, and division/modulo by a nonzero integer constant.
5. Encode whole-program arithmetic facts in `constraints`. For assignments,
   use ordinary or SSA-style integer symbols and equations such as
   `(= y (+ x 1))`; do not use source line numbers or AST identifiers.
6. Put every source `assert` property, plus useful inferred properties, in
   `specifications` at least once. A target is checked against the joint
   constraints; it is not a free-standing predicate.
7. Do not mention or invent line numbers, anchors, NodeIds, AST nodes,
   program locations, reachability queries, path conditions, or replay.
8. Give a brief rationale for each formula. Do not provide a private or
   step-by-step Chain-of-Thought trace.
9. Return valid JSON matching the requested schema.
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
    """Invoke Gemini for the SatLM-style Direct Formalization baseline.

    This response is independent from :func:`generate_cot_and_claims`: it has
    no line-number field and cannot be passed through the legacy Python claim
    binder.  Constraints and target specifications are generated together so
    the solver can check entailment rather than validity of a free predicate.
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable "
            "or create a .env file with GEMINI_API_KEY=your_key."
        )

    prompt = f"""Formalize the following Python program as a joint SatLM-style
declarative specification in QF-LIA SMT-LIB2.

`constraints` are facts and arithmetic relations jointly assumed for the
program. `specifications` are target properties that should follow from those
constraints. The solver will check each target with:

    constraints AND NOT(target)

Use one formula per array entry and declare every Int symbol used by that
entry in its `variables` array. Include each source `assert` as a target
specification exactly once, along with useful inferred properties. For
example, for `y = x + 1; assert y >= x`, a suitable pair is the constraint
`(= y (+ x 1))` and the target `(>= y x)`. Do not use line numbers, AST
identifiers, reachability claims, or Python predicates.

```python
{source_code}
```

Return valid JSON matching the requested schema. Give only brief rationales,
not a private Chain-of-Thought trace."""

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
        constraints = plan.constraints
        specifications = plan.specifications
    except Exception as exc:
        # Surface malformed JSON to the pipeline as a translation failure.
        # Treating it as a successful empty pool would hide an LLM failure.
        raise RuntimeError(
            f"Gemini returned invalid direct-formalization JSON: {exc}"
        ) from exc

    return DirectLLMResult(
        constraints=constraints,
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
    "DirectFormula",
    "DirectConstraint",
    "DirectSpecification",
    "DirectFormalizationPlan",
    "DirectLLMResult",
    "generate_cot_and_claims",
    "generate_direct_formalization",
]
