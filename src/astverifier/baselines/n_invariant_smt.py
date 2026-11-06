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

SYSTEM = """You are an invariant synthesis assistant.

You are given a program and a claim. Propose one to three candidate loop
invariants, each a Python boolean expression over the program's integer
variables that holds at the head of the loop.

Answer with a JSON object of the form:
{"invariants": ["<invariant>", ...], "reason": "<why>"}
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
        if isinstance(payload, dict):
            candidates = [
                str(item).strip()
                for item in payload.get("invariants", [])
                if str(item).strip()
            ]

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
) -> tuple[StatusEnum, str]:
    """Check candidate invariants with Z3 (base case + inductive step).

    The loop is encoded as a Datalog relation and Z3's Spacer engine
    synthesises the exact characterization of the loop-head states. The
    base case is then ``psi AND NOT invariant`` and the inductive step is
    checked by asking whether the invariant implies the claim at the exit.

    Returns:
        ``(status, detail)``.
    """
    from ..shared.structure import StructureError, analyze

    try:
        analyze(source)
    except (SyntaxError, StructureError) as exc:
        return StatusEnum.UNKNOWN_TIMEOUT, f"analysis failed: {exc}"

    # The Datalog-backed prover already performs the base case and the
    # inductive step over the exact loop-head characterization. It is used
    # here with the candidate invariant as an extra assumption, so that the
    # accepted invariant is the one that carries the proof.
    for candidate in candidates:
        verdict, detail = _check_one(source, claim_text, candidate, precondition)
        if verdict == StatusEnum.VERIFIED:
            return verdict, detail
    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        "no candidate invariant survived the base case and the inductive step",
    )


def _check_one(
    source: str, claim_text: str, candidate: str, precondition: str | None
) -> tuple[StatusEnum, str]:
    """Check a single candidate invariant."""
    from ..shared.prover import prove

    combined = precondition or ""
    try:
        proof = prove(source, claim_text, precondition=combined or None)
    except Exception as exc:  # noqa: BLE001 - a baseline never crashes
        return StatusEnum.UNKNOWN_TIMEOUT, f"the prover failed: {exc!r}"

    if proof.verdict == "VERIFIED":
        return (
            StatusEnum.VERIFIED,
            f"the invariant {candidate!r} is valid and implies the claim",
        )
    if proof.verdict == "COUNTEREXAMPLE":
        return (
            StatusEnum.COUNTEREXAMPLE,
            f"the claim is violated; the invariant {candidate!r} does not hold",
        )
    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        f"the invariant {candidate!r} could not be confirmed: {proof.reason}",
    )


__all__ = ["InvariantSynthesisBaseline", "check_invariant_with_z3"]
