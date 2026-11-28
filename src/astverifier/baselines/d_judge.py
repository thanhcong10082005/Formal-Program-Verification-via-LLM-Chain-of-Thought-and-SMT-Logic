"""Baseline D -- LLM-as-a-Judge.

The model reads the program and the claim and returns a verdict, with a
sanity check on the boundary values as a second stage.

This is the weakest *reasoning* baseline in the set: it reasons from the
source alone, with no execution and no solver. It is the reference point
that G (Self-Consistency) is measured against.

A counterexample reported by the model is replayed on real CPython, so the
Counterexample Replay Validity Rate is measurable for it.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, parse_witness, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a program verification judge. Decide whether the claim holds
for all inputs satisfying the precondition and reaching the claim's line.
Before choosing VERIFIED, check branch conditions and loop boundaries for a
violating input. Choose UNKNOWN if you cannot justify the universal claim or
give a concrete counterexample.

Return exactly one JSON object with keys verdict, witness, and reason. Use
VERIFIED, COUNTEREXAMPLE, or UNKNOWN for verdict. For COUNTEREXAMPLE, witness
must map the exact function parameter names to integers. Check that this input
passes all early returns, reaches the claim's line, and makes it false.
Otherwise, use null for witness. Keep reason to one sentence of at most 20
words. Example:
{"verdict":"UNKNOWN","witness":null,"reason":"The loop exit value is unclear."}
"""

#: The boundary values the sanity check asks about.
EDGE_VALUES = (-1, 0, 1)


@register
class LLMJudgeBaseline(BaseBaseline):
    """D -- LLM-as-a-Judge."""

    id = "D"
    name = "LLM-as-a-Judge"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Ask the model to judge, then sanity-check the boundary values."""
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
            program_prompt(spec, task="Decide whether the claim holds."),
            system=SYSTEM,
            budget=self.budget,
        )

        sanity = self._sanity_check(spec)

        claim_results = [
            self._decide(spec, claim, payload, sanity) for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=str(payload.get("reason", "")) if isinstance(payload, dict) else "",
        )

    # -- internals ------------------------------------------------------------

    def _sanity_check(self, spec: ProgramSpec) -> dict | None:
        """Ask the model about the boundary values as a second stage."""
        prompt = program_prompt(
            spec,
            task=(
                "Sanity check: consider the boundary inputs "
                f"{list(EDGE_VALUES)}. Check which inputs satisfy the precondition "
                "and reach the claim, then look for a concrete violation there."
            ),
        )
        answer = ask_json(self.llm, prompt, system=SYSTEM, budget=self.budget)
        return answer if isinstance(answer, dict) else None

    def _decide(
        self, spec: ProgramSpec, claim, payload: Any, sanity: dict | None
    ) -> ClaimResult:
        """Turn the judge's answer into a claim result."""
        sanity_reason = str(sanity.get("reason") or "") if sanity else ""
        if sanity and normalize_verdict(sanity.get("verdict")) == StatusEnum.COUNTEREXAMPLE:
            boundary_witness = parse_witness(sanity)
            if boundary_witness:
                boundary = self._counterexample(spec, claim, boundary_witness)
                if boundary.replay_verdict:
                    return ClaimResult(
                        claim_id=claim.claim_id,
                        claim_text=claim.text,
                        status=StatusEnum.COUNTEREXAMPLE,
                        anchor=self.anchor_info(claim),
                        counterexample=boundary,
                        reason=sanity_reason or "the boundary witness reproduced the violation",
                    )
        if not isinstance(payload, dict):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the model did not return a usable verdict",
            )

        verdict = normalize_verdict(payload.get("verdict"))
        reason = str(payload.get("reason", ""))
        if sanity_reason:
            reason = f"{reason} | sanity check: {sanity_reason}".strip(" |")

        if verdict == StatusEnum.COUNTEREXAMPLE:
            witness = parse_witness(payload)
            if not witness:
                return ClaimResult(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    status=StatusEnum.UNKNOWN_TIMEOUT,
                    anchor=self.anchor_info(claim),
                    reason=reason or "the judge reported a counterexample without a witness",
                )
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, claim, witness),
                reason=reason or "the judge reported a counterexample",
            )

        if verdict == StatusEnum.VERIFIED:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=reason or "the judge accepted the claim",
            )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=reason or "the judge could not decide",
        )

    def _counterexample(self, spec: ProgramSpec, claim, witness: dict[str, int]):
        """Build the counterexample record, replayed on real CPython."""
        replay = replay_counterexample(
            spec.source,
            spec.function_name,
            witness,
            target_line=claim.source_line or None,
            claim_expression=claim.text or None,
        )
        return CounterexampleInfo(
            z3_model=dict(witness),
            replay_verdict=replay.success,
            replay_output=replay.output_repr,
            replay_diff=replay.error,
        )


__all__ = ["LLMJudgeBaseline"]
