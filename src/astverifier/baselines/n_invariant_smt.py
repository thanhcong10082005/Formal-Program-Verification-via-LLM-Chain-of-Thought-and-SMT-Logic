"""Baseline N -- Invariant Synthesis (Code2Inv-style).

The model proposes one to three loop invariants; Z3 checks each of them
for the **base case** and the **inductive step**. An invariant that
survives both checks is accepted, and the claim is then verified against
it.

N is the decisive baseline of the whole study. It has **no AST-anchor
gate**: a claim that cannot be anchored to a node can still be verified by
SMT. If N comes out close to the Proposed Method, the hypothesis that
"anchor + reachability is the main contribution" is not supported -- and
that is a *result*, not a failure.

Two metrics are therefore ``N/A`` for N:

* the Hallucination Rate (no anchor gate),
* the "UNREACHABLE detected" metric (no reachability query).
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """Propose up to three inductive Python Boolean expressions over
integer variables in scope at the loop head. Each candidate must hold before
the first iteration, survive the loop update, and imply the claim when the
loop guard is false. Combine needed facts with and. Return one JSON object
only, for example {"invariants":["0 <= i and i <= n"],"reason":"Bounds hold at the head."}.
"""

#: How many candidate invariants are requested.
N_CANDIDATES = 3


@register
class InvariantSynthesisBaseline(BaseBaseline):
    """N -- Invariant Synthesis (Code2Inv-style)."""

    id = "N"
    name = "Invariant Synthesis"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Synthesise invariants and check them with Z3."""
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

        payload = ask_json(
            self.llm,
            program_prompt(
                spec,
                task="Propose candidate loop invariants for this program.",
            ),
            system=SYSTEM,
            budget=self.budget,
        )

        candidates: list[str] = []
        if isinstance(payload, dict) and isinstance(payload.get("invariants"), list):
            candidates = [
                item.strip()
                for item in payload["invariants"]
                if isinstance(item, str) and item.strip()
            ][:N_CANDIDATES]

        claim_results = [
            self._decide(spec, claim, candidates) for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=(
                f"candidates: {candidates}"
                if candidates
                else "the model proposed no invariant"
            ),
        )

    # -- internals ------------------------------------------------------------

    def _decide(self, spec: ProgramSpec, claim, candidates: list[str]) -> ClaimResult:
        """Check every candidate invariant and report the first accepted one."""
        if not candidates:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the model proposed no candidate invariant",
            )

        verdict, detail = check_invariant_with_z3(
            spec.source,
            claim.text,
            candidates,
            precondition=claim.precondition or spec.precondition,
            source_line=claim.source_line,
        )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=verdict,
            anchor=self.anchor_info(claim),
            reason=detail,
            explanation=f"candidates: {candidates}",
        )


def check_invariant_with_z3(
    source: str,
    claim_text: str,
    candidates: list[str],
    *,
    precondition: str | None = None,
    source_line: int = 0,
) -> tuple[StatusEnum, str]:
    """Check the candidate expressions in the loop's entry, step and exit states."""
    from ..shared.prover import VERDICT_VERIFIED, validate_loop_invariants

    proof = validate_loop_invariants(
        source, claim_text, candidates, precondition=precondition, source_line=source_line
    )
    verdict = StatusEnum.VERIFIED if proof.verdict == VERDICT_VERIFIED else StatusEnum.UNKNOWN_TIMEOUT
    return verdict, proof.reason


__all__ = ["InvariantSynthesisBaseline", "check_invariant_with_z3"]
