"""Gemini adapters for the anchored and simple verification baselines.

``AST_ANCHORED`` keeps its existing anchored formalization contract.  The
``UNANCHORED`` baseline uses a separate structured claim-list contract; its
claim expressions are JSON objects and contain no source locations or joint
constraint section.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field


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


class AnchoredSpecification(DirectFormula):
    """A SatLM-style target property tied to one exact source line."""

    line: int = Field(
        description=(
            "The exact source line whose pre-state is the target location. "
            "It must identify one concrete AST statement."
        )
    )


class AnchoredFormalizationPlan(BaseModel):
    """SatLM-style formalization with AST target anchors."""

    constraints: List[DirectConstraint] = Field(
        default_factory=list,
        description=(
            "Joint SMT-LIB2 QF-LIA relations describing whole-program "
            "arithmetic semantics."
        ),
    )
    specifications: List[AnchoredSpecification] = Field(
        default_factory=list,
        description=(
            "SMT-LIB2 target properties. Every target must include an exact "
            "source line anchor."
        ),
    )


class SimpleClaim(BaseModel):
    """One unanchored structured QF-LIA claim.

    ``expression`` intentionally remains a JSON object at the API boundary.
    The verifier performs the recursive whitelist validation in
    :mod:`astverifier.simple` after the model response is decoded.
    """

    model_config = ConfigDict(extra="forbid")

    expression: Dict[str, Any] = Field(
        description=(
            "A structured Boolean expression using variable, integer, Boolean, "
            "and QF-LIA operator objects."
        )
    )
    rationale: str = Field(
        default="",
        description="A brief rationale for the claim, not a Chain-of-Thought trace.",
    )


class SimpleClaimList(BaseModel):
    """Gemini response envelope for the simple claim-list baseline."""

    model_config = ConfigDict(extra="forbid")

    claims: List[SimpleClaim] = Field(
        default_factory=list,
        description="Claims in the order in which they should be checked.",
    )


# Gemini Developer API does not accept JSON Schema's ``additionalProperties``
# keyword.  The Pydantic models above remain strict for response validation,
# while this provider schema describes the outer shape and a bounded recursive
# expression vocabulary.  The runtime whitelist in ``simple.py`` remains the
# authority for arbitrary-depth validation.
_SIMPLE_OPERATORS = [
    "and",
    "or",
    "not",
    "implies",
    "xor",
    "eq",
    "neq",
    "lt",
    "le",
    "gt",
    "ge",
    "add",
    "sub",
    "neg",
    "mul",
    "div",
    "mod",
]


def _simple_expression_response_schema(depth: int = 4) -> Dict[str, Any]:
    """Build a provider-compatible, bounded recursive expression schema."""
    child_schema: Dict[str, Any]
    if depth <= 0:
        # The runtime validator still checks deeper expressions if a provider
        # returns them; this fallback only keeps the API schema finite.
        child_schema = {"type": "OBJECT"}
    else:
        child_schema = _simple_expression_response_schema(depth - 1)

    return {
        "anyOf": [
            {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["var"]},
                    "name": {"type": "STRING"},
                },
                "required": ["kind", "name"],
            },
            {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["int"]},
                    "value": {"type": "INTEGER"},
                },
                "required": ["kind", "value"],
            },
            {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["bool"]},
                    "value": {"type": "BOOLEAN"},
                },
                "required": ["kind", "value"],
            },
            {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["op"]},
                    "op": {"type": "STRING", "enum": _SIMPLE_OPERATORS},
                    "args": {"type": "ARRAY", "items": child_schema},
                },
                "required": ["kind", "op", "args"],
            },
        ]
    }


_SIMPLE_CLAIM_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "claims": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "expression": {
                        "description": "A structured QF-LIA Boolean expression.",
                        **_simple_expression_response_schema(),
                    },
                    "rationale": {
                        "type": "STRING",
                        "description": "A brief rationale for the claim.",
                    },
                },
                "required": ["expression"],
            },
        }
    },
    "required": ["claims"],
}


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


@dataclass
class AnchoredLLMResult:
    """SatLM-style constraints plus line-anchored target specifications."""

    constraints: List[DirectConstraint]
    specifications: List[AnchoredSpecification]
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""


@dataclass
class SimpleLLMResult:
    """Structured claims returned by the simple-baseline LLM call."""

    claims: List[SimpleClaim]
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""

    @property
    def simple_claims(self) -> List[SimpleClaim]:
        """Compatibility spelling for callers that use the pipeline argument name."""
        return self.claims


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


ANCHORED_SYSTEM_PROMPT = """You are an expert formal methods assistant.
Formalize the supplied Python program using a SatLM-style declarative
specification in quantifier-free linear integer arithmetic (QF-LIA) SMT-LIB2,
while grounding every target specification in the program's AST.

Rules:
1. Return a JSON object with `constraints` and `specifications` arrays.
2. Each formula must be exactly one SMT-LIB2 Boolean term, not a command,
   declaration, Python expression, or Markdown code block.
3. Use only integer variables listed in that entry's `variables` array.
4. Use QF-LIA operators only: Boolean connectives, integer comparisons,
   addition, subtraction, multiplication by an integer constant, and division
   or modulo by a nonzero integer constant.
5. Put whole-program assignment and branch relations in `constraints`.
   Constraints are jointly assumed by the verifier and need no source line.
6. Every `specifications` entry must include the exact source `line` where
   the target is checked. The line must identify one concrete statement, and
   the formula must describe the state immediately before that statement.
7. Include every source `assert` exactly once as a specification at its assert
   line, plus useful inferred properties at valid statement lines.
8. Do not invent NodeIds, AST identifiers, or reachability claims. The line
   field is only a location hint; the verifier independently binds it to the
   parsed AST and rejects ambiguous or invalid anchors.
9. Give brief rationales only. Do not provide a private or step-by-step
   Chain-of-Thought trace.
10. Return valid JSON matching the requested schema.
"""


SIMPLE_SYSTEM_PROMPT = """You are an expert formal methods assistant working in
quantifier-free integer arithmetic (QF-LIA).

Return a JSON object with exactly one `claims` array. Each item has an
`expression` object and an optional short `rationale`. Expressions use this
structured vocabulary:

* `{"kind":"var","name":"x"}` for an integer variable;
* `{"kind":"int","value":2}` for an integer constant;
* `{"kind":"bool","value":true}` or `false` for a Boolean constant;
* `{"kind":"op","op":...,"args":[...]}` for an operator.

Allowed operator names are `and`, `or`, `not`, `implies`, `xor`, `eq`, `neq`,
`lt`, `le`, `gt`, `ge`, `add`, `sub`, `neg`, `mul`, `div`, and `mod`.
Multiplication must have an integer constant operand. Division and modulo must
have a nonzero integer constant divisor. Use only Int variables and Boolean
terms. Return each claim as one Boolean expression. Do not return source
locations, prose outside JSON, or private step-by-step reasoning. Include the
properties expressed by source `assert` statements when possible.
"""


DIRECT_TIMEOUT_MS = 120_000

# The observed Gemini 3.5 Flash-Lite free-tier limit for the benchmark key is
# 15 requests/minute.  A slightly slower interval leaves a safety margin for
# the provider's rolling window.  The throttle is shared by both independent
# baseline calls in a process, so AST_ANCHORED and UNANCHORED consume the same
# request budget.
DEFAULT_REQUEST_INTERVALS = {
    "gemini-3.5-flash-lite": 4.2,
}
REQUEST_INTERVAL_ENV = "GEMINI_REQUEST_INTERVAL_SECONDS"


def _request_interval_seconds(model: str) -> float:
    """Return the minimum gap between Gemini request attempts.

    ``GEMINI_REQUEST_INTERVAL_SECONDS`` can override the model default.  Set
    it to ``0`` only when the project's configured quota makes throttling
    unnecessary.  The interval is deliberately applied to retries as well as
    successful calls because every HTTP attempt counts toward RPM quotas.
    """
    configured = os.environ.get(REQUEST_INTERVAL_ENV)
    if configured is not None:
        try:
            interval = float(configured)
        except ValueError as exc:
            raise ValueError(
                f"{REQUEST_INTERVAL_ENV} must be a non-negative number"
            ) from exc
        if interval < 0:
            raise ValueError(f"{REQUEST_INTERVAL_ENV} must be non-negative")
        return interval

    model_name = model.rsplit("/", 1)[-1].lower()
    return DEFAULT_REQUEST_INTERVALS.get(model_name, 0.0)


class _GeminiRequestThrottle:
    """Reserve request start times for all Gemini calls in this process."""

    def __init__(self, *, clock=time.monotonic, sleeper=time.sleep):
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait_for_slot(self, model: str) -> None:
        interval = _request_interval_seconds(model)
        if interval <= 0:
            return

        # Reserve the next slot while holding the lock, then sleep outside it
        # so independent callers do not serialize around the actual network
        # request.  The benchmark is sequential, but this also behaves safely
        # for callers that use the adapter from multiple threads.
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + interval
        if delay:
            self._sleeper(delay)


_REQUEST_THROTTLE = _GeminiRequestThrottle()


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

    for m in models_to_try:
        for attempt in range(2):
            try:
                _REQUEST_THROTTLE.wait_for_slot(m)
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

    This response is independent from :func:`generate_anchored_formalization`:
    it has no line-number field and cannot be passed through the AST anchor
    binder. Constraints and target specifications are generated together so
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
    for attempt in range(2):
        try:
            _REQUEST_THROTTLE.wait_for_slot(model)
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


def generate_simple_claims(
    source_code: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash",
) -> SimpleLLMResult:
    """Invoke Gemini for the unanchored structured claim-list baseline."""
    key = _get_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment "
            "variable or create a .env file with GEMINI_API_KEY=your_key."
        )

    prompt = f"""Produce a short ordered list of formal Boolean claims for the
following Python program. Encode every expression as structured JSON using the
schema and QF-LIA vocabulary in the system instruction. Include useful source
assertion properties when present. Return only the JSON object.

```python
{source_code}
```
"""

    client = _make_client(key)
    last_err = None
    response = None
    for attempt in range(2):
        try:
            _REQUEST_THROTTLE.wait_for_slot(model)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SIMPLE_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_SIMPLE_CLAIM_RESPONSE_SCHEMA,
                    temperature=0.1,
                ),
            )
            break
        except Exception as exc:
            last_err = exc
            if attempt == 0:
                time.sleep(1.0)

    if response is None:
        raise RuntimeError(f"Gemini simple-claim request failed: {last_err}")

    tokens_in = 0
    tokens_out = 0
    if response.usage_metadata:
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    raw_text = response.text or ""
    try:
        data = json.loads(raw_text)
        plan = SimpleClaimList.model_validate(data)
    except Exception as exc:
        raise RuntimeError(f"Gemini returned invalid simple-claim JSON: {exc}") from exc

    return SimpleLLMResult(
        claims=list(plan.claims),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw_text=raw_text,
        model_name=model,
    )


# A descriptive alias used by a few external callers.  The pipeline uses the
# shorter name, but both names refer to the same schema and request contract.
generate_simple_claim_list = generate_simple_claims


def generate_anchored_formalization(
    source_code: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash",
) -> AnchoredLLMResult:
    """Invoke Gemini for the anchored SatLM-style formalization.

    The response shares the direct baseline's declarative constraints and
    SMT-LIB2 target formulas, but every target also carries a source line.
    The pipeline binds that line to an exact AST statement before using the
    trusted executor's reachability and target-state checks.
    """
    key = _get_api_key(api_key)
    if not key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment "
            "variable or create a .env file with GEMINI_API_KEY=your_key."
        )

    prompt = f"""Formalize the following Python program as an AST-grounded,
SatLM-style declarative specification in QF-LIA SMT-LIB2.

`constraints` are jointly assumed arithmetic relations for the whole program.
`specifications` are target properties checked against the trusted AST-derived
execution state at their exact source `line`. The verifier will first check
that the anchored line is reachable and then check the target against the
anchored state, together with the joint constraints.

Include each source `assert` exactly once as a specification at its assert line.
Add useful inferred properties only when they can be tied to one concrete
statement line. Use the state immediately before the anchored statement. For
example, for:

    y = x + 1
    assert y >= x

    return a constraint such as `(= y (+ x 1))` and a specification at the
    following assertion line such as
`{{"line": 3, "formula": "(>= y x)", "variables": ["y", "x"]}}`.
Do not output Python predicates, line ranges, AST identifiers, or Markdown.
Give only brief rationales, not a private Chain-of-Thought trace.

```python
{source_code}
```

Return valid JSON matching the requested schema."""

    client = _make_client(key)
    last_err = None
    response = None
    for attempt in range(2):
        try:
            _REQUEST_THROTTLE.wait_for_slot(model)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=ANCHORED_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=AnchoredFormalizationPlan,
                    temperature=0.1,
                ),
            )
            break
        except Exception as exc:
            last_err = exc
            if attempt == 0:
                time.sleep(1.0)

    if response is None:
        raise RuntimeError(f"Gemini anchored-formalization request failed: {last_err}")

    tokens_in = 0
    tokens_out = 0
    if response.usage_metadata:
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    raw_text = response.text or ""
    try:
        data = json.loads(raw_text)
        plan = AnchoredFormalizationPlan.model_validate(data)
    except Exception as exc:
        raise RuntimeError(
            f"Gemini returned invalid anchored-formalization JSON: {exc}"
        ) from exc

    return AnchoredLLMResult(
        constraints=list(plan.constraints),
        specifications=list(plan.specifications),
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
    "AnchoredSpecification",
    "AnchoredFormalizationPlan",
    "AnchoredLLMResult",
    "SimpleClaim",
    "SimpleClaimList",
    "SimpleLLMResult",
    "generate_cot_and_claims",
    "generate_direct_formalization",
    "generate_simple_claims",
    "generate_simple_claim_list",
    "generate_anchored_formalization",
]
