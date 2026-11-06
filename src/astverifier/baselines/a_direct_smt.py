"""Baseline A -- SatLM Direct SMT-LIB2.

The SatLM protocol: the model translates the program and the claim into
SMT-LIB2, and the solver checks ``Constraints AND NOT Spec``.

The translation is *not* anchored to the AST. The model is free to
mis-translate, and when it does, the resulting verdict is wrong for reasons
that have nothing to do with the program's semantics. That is precisely the
weakness this baseline is supposed to expose, so it is not "fixed" here.

Because there is no anchor gate, the Hallucination Rate is ``N/A``: a
translation error is reported as ``TRANSLATION_ERROR`` on the claim, not as
a hallucination of a program fact.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a formal verification assistant. You translate a small \
Python program and a claim into SMT-LIB2 2.

Rules:
1. Declare one integer constant per function parameter with the same name.
2. Encode the program's straight-line statements as assertions.
3. Encode the claim as a named Boolean `spec`.
4. Do not use any command other than `declare-fun`, `assert`, `check-sat`, \
`push`, `pop`, and `get-model`.

Answer with a JSON object of the form:
{"smt": "<the SMT-LIB2 program>", "claim": "<the claim as a Boolean term>"}
"""


@register
class SatLMDirectBaseline(BaseBaseline):
    """A -- SatLM Direct SMT-LIB2."""

    id = "A"
    name = "SatLM Direct SMT-LIB2"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Translate, solve, and report."""
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
                task=(
                    "Translate the program and the claim into an SMT-LIB2 2 "
                    "program that checks whether the claim can be violated."
                ),
            ),
            system=SYSTEM,
            budget=self.budget,
        )

        claim_results = [
            self._decide(spec, claim, payload) for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=(payload or {}).get("smt", "") if isinstance(payload, dict) else "",
            extra={"direct_constraints": [self._smt_text(payload)]}
            if isinstance(payload, dict)
            else {},
        )

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _smt_text(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("smt", ""))
        return ""

    def _decide(self, spec: ProgramSpec, claim, payload: Any) -> ClaimResult:
        """Check ``Constraints AND NOT Spec`` with Z3."""
        if not isinstance(payload, dict) or not payload.get("smt"):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=self.anchor_info(claim),
                reason="the model did not return an SMT-LIB2 program",
            )

        smt_text = str(payload["smt"])
        claim_term = str(payload.get("claim") or claim.text)

        import z3

        try:
            solver = z3.Solver()
            solver.set("timeout", 20_000)
            solver.from_string(smt_text)
            solver.add(z3.Not(z3.parse_smt2_string(f"(assert {claim_term})")))
            outcome = solver.check()
        except Exception as exc:  # noqa: BLE001 - a baseline never crashes
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=self.anchor_info(claim),
                reason=f"the SMT-LIB2 translation could not be parsed: {exc!r}",
            )

        if outcome == z3.sat:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                reason="the solver found a model violating the claim",
            )
        if outcome == z3.unsat:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason="the solver proved the claim cannot be violated",
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason="the solver returned 'unknown'",
        )


__all__ = ["SatLMDirectBaseline"]
