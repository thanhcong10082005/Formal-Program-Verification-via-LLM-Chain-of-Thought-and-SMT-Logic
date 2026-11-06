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

SYSTEM = """You are a neuro-symbolic verification assistant.

You propose a specification for a program: a precondition, a postcondition
and a loop invariant, each a Python boolean expression over the program's
integer variables.

Answer with a JSON object of the form:
{"pre": "<precondition or null>", "post": "<postcondition or null>",
 "inv": "<loop invariant or null>", "reason": "<why>"}
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

        payload = ask_json(self.llm, prompt, system=SYSTEM, budget=self.budget)
        history: list[str] = []
        accepted = None

        for round_index in range(K_ROUNDS):
            if not isinstance(payload, dict):
                break
            candidate = _extract_spec(payload)
            history.append(f"round {round_index + 1}: {candidate}")
            verdict, detail = check_spec(spec.source, candidate)
            if verdict == StatusEnum.VERIFIED:
                accepted = (candidate, detail)
                break
            payload = ask_json(
                self.llm,
                prompt
                + "\n\nYour previous specification was rejected:\n"
                + detail
                + "\n\nRefine it.",
                system=SYSTEM,
                budget=self.budget,
            )

        if accepted is None:
            return self.roll_up(
                spec,
                self.id,
                [
                    ClaimResult(
                        claim_id=claim.claim_id,
                        claim_text=claim.text,
                        status=StatusEnum.UNKNOWN_TIMEOUT,
                        anchor=self.anchor_info(claim),
                        reason=(
                            f"no acceptable specification after {K_ROUNDS} rounds"
                        ),
                        explanation=" | ".join(history),
                    )
                    for claim in spec.claims
                ],
                started=started,
                tokens_in=self.budget.tokens_in,
                tokens_out=self.budget.tokens_out,
                cot_trace=" | ".join(history),
            )

        candidate, detail = accepted
        claim_results = [
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=detail,
                explanation=f"accepted spec: {candidate}",
            )
            for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=" | ".join(history),
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


def check_spec(source: str, candidate: dict[str, str | None]) -> tuple[StatusEnum, str]:
    """Check a proposed specification against the program.

    The check is symbolic but solver-free: the postcondition is evaluated on
    the concrete states the program reaches, and the invariant is checked by
    k-induction over the program's own transition relation.

    Returns:
        ``(status, detail)``.
    """
    from .c_structural import check_invariant_by_k_induction

    invariant = candidate.get("inv")
    post = candidate.get("post")
    if not invariant and not post:
        return StatusEnum.UNKNOWN_TIMEOUT, "the specification is empty"

    if invariant:
        verdict, detail = check_invariant_by_k_induction(source, post or "True", [invariant])
        if verdict == StatusEnum.VERIFIED:
            return verdict, detail
        return StatusEnum.UNKNOWN_TIMEOUT, detail

    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        "no loop invariant was proposed, so the postcondition cannot be shown",
    )


__all__ = ["NeuroSymbolicBaseline"]
