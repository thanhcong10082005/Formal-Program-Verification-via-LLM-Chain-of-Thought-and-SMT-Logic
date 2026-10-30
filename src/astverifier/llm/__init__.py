"""LLM adapters.

The LLM is used **only** to propose claims, loop invariants and refined
claims. Every decision that the framework reports is taken by the trusted
side (the AST-derived symbolic executor and Z3), never by the model.

The Gemini adapter is isolated in this package so that a Baseline which does
not use an LLM can import the Core Proposed Method without pulling in the
``google-genai`` dependency.
"""

from .gemini import (
    LLMResult,
    LLMVerificationPlan,
    SuggestedClaim,
    generate_cot_and_claims,
    make_client,
    resolve_api_key,
)

__all__ = [
    "LLMResult",
    "LLMVerificationPlan",
    "SuggestedClaim",
    "generate_cot_and_claims",
    "make_client",
    "resolve_api_key",
]
