"""Baseline E -- Neuro-Symbolic Verifier (AutoSpec-style).

The model proposes a *specification* -- a precondition, a postcondition and
an invariant -- and the checker validates it. When the check fails, the
failure is fed back to the model, which refines its spec; this happens up to
``k`` times.

There is no AST-anchor gate: the spec is a free-standing artefact, and a
spec that turns out to be wrong is a *translation* problem, not a
hallucination of a program fact. The Hallucination Rate is therefore
``N/A``.

There is no replay either: the checker reasons symbolically, so a
counterexample is a model, not a witnessed input. The Counterexample Replay
Validity Rate is ``N/A``.

This baseline isolates ablation AB-4: the Proposed Method's feedback loop
is grounded in a replayed counterexample, E's is grounded in a solver
rejection.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """Propose a specification with Python Boolean expressions.
Use actual variable names. The invariant must hold at entry and survive each
iteration. The postcondition must hold at the claim's line. Return one JSON
object with a one-sentence reason; use JSON null for an unavailable field.
Example: {"pre":"n >= 0","post":"i == n","inv":"0 <= i <= n","reason":"The loop advances i by one."}
"""

#: How many refinement rounds are allowed.
K_ROUNDS = 3


@register
class NeuroSymbolicBaseline(BaseBaseline):
    """E -- Neuro-Symbolic Verifier (AutoSpec-style)."""

    id = "E"
    name = "Neuro-Symbolic Verifier"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Propose a spec, check it, refine it on failure."""
        started = time.perf_counter()
        self.budget = TokenBudget()

        if self.llm is None:
            return self.roll_up(
                spec,
                self.id,
                [
                    ClaimResult(
                        claim_id=claim.claim_id,
                        claim_text=claim.text,
                        status=StatusEnum.UNKNOWN_TIMEOUT,
                        anchor=self.anchor_info(claim),
                        reason="no LLM client was supplied",
                    )
                    for claim in spec.claims
                ],
                started=started,
            )

        prompt = program_prompt(
            spec,
            task=(
                "Propose a specification for this program: a precondition, "
                "a postcondition and a loop invariant."
            ),
        )

        history: list[str] = []
        accepted: dict[int, str] = {}
        next_prompt = prompt

        for round_index in range(K_ROUNDS):
            payload = ask_json(self.llm, next_prompt, system=SYSTEM, budget=self.budget)
            if not isinstance(payload, dict):
                next_prompt = prompt + "\nReturn exactly one JSON object with pre, post, inv, and reason."
                continue
            candidate = _extract_spec(payload)
            history.append(f"round {round_index + 1}: {candidate}")
            rejected = []
            for index, claim in enumerate(spec.claims):
                if index in accepted:
                    continue
                verdict, detail = check_spec(
                    spec.source, candidate, claim_text=claim.text, source_line=claim.source_line
                )
                if verdict == StatusEnum.VERIFIED:
                    accepted[index] = detail
                else:
                    rejected.append(f"Line {claim.source_line}: {detail}")
            if not rejected or round_index + 1 == K_ROUNDS:
                break
            next_prompt = (
                prompt
                + "\n\nYour previous specification was rejected:\n"
                + "\n".join(rejected)
                + "\n\nRefine it and return one JSON object."
            )

        trace = " | ".join(history)
        claim_results = [
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED if index in accepted else StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason=accepted.get(index, f"no acceptable specification after {len(history)} rounds"),
                explanation=trace,
            )
            for index, claim in enumerate(spec.claims)
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=trace,
        )


def _extract_spec(payload: dict) -> dict[str, str | None]:
    """Pull the three spec components out of the model's answer."""
    return {
        "pre": _as_text(payload.get("pre") or payload.get("precondition")),
        "post": _as_text(payload.get("post") or payload.get("postcondition")),
        "inv": _as_text(payload.get("inv") or payload.get("invariant")),
    }


def _as_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip().lower() != "none":
        return value.strip()
    return None


def check_spec(
    source: str, candidate: dict[str, str | None], *, claim_text: str, source_line: int
) -> tuple[StatusEnum, str]:
    """Check the requested claim and any proposed postcondition at its site."""
    from .c_structural import check_invariant_by_k_induction

    invariant = candidate.get("inv")
    post = candidate.get("post")
    if not invariant and not post:
        return StatusEnum.UNKNOWN_TIMEOUT, "the specification is empty"

    if invariant:
        if post and post != claim_text:
            verdict, detail = check_invariant_by_k_induction(
                source, post, [invariant], source_line=source_line
            )
            if verdict != StatusEnum.VERIFIED:
                return verdict, detail
        return check_invariant_by_k_induction(
            source, claim_text, [invariant], source_line=source_line
        )

    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        "no loop invariant was proposed, so the postcondition cannot be shown",
    )


__all__ = ["NeuroSymbolicBaseline"]
