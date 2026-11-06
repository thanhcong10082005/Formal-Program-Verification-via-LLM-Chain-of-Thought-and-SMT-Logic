"""Baseline H -- Self-Refine / Critique-and-Revise.

The model produces a verdict, then critiques its own answer, then revises
it -- three rounds in total.

The baseline exists to isolate the contribution of the *retrospective
feedback* loop: the Proposed Method's feedback is grounded in a replayed
counterexample and a refined claim, while H's feedback is the model's own
opinion. Comparing E (AutoSpec, k=3) and H against the Proposed Method
answers ablation AB-4.

It never calls a solver and never executes the program, so both the
Counterexample Replay Validity Rate and the "UNREACHABLE detected" metric
are ``N/A`` for it.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a program verification assistant. You reason about
whether a claim holds for a program, critique your own reasoning, and
revise your answer.

Answer with a JSON object of the form:
{"verdict": "VERIFIED" | "COUNTEREXAMPLE" | "UNKNOWN",
 "reason": "<the reasoning>", "critique": "<what could be wrong>"}
"""

#: How many revise rounds are performed.
N_ROUNDS = 3


@register
class SelfRefineBaseline(BaseBaseline):
    """H -- Self-Refine."""

    id = "H"
    name = "Self-Refine"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Iterate: answer, critique, revise."""
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

        prompt = program_prompt(spec, task="Decide whether the claim holds.")

        payload = ask_json(self.llm, prompt, system=SYSTEM, budget=self.budget)
        history: list[str] = []

        for round_index in range(N_ROUNDS - 1):
            if not isinstance(payload, dict):
                break
            critique = str(payload.get("critique", "")).strip()
            reason = str(payload.get("reason", "")).strip()
            history.append(f"round {round_index + 1}: {reason}")
            if not critique:
                break
            payload = ask_json(
                self.llm,
                prompt
                + "\n\nYour previous answer was:\n"
                + reason
                + "\n\nIts weakness is:\n"
                + critique
                + "\n\nRevise your answer.",
                system=SYSTEM,
                budget=self.budget,
            )

        if not isinstance(payload, dict):
            return self.roll_up(
                spec,
                self.id,
                [
                    ClaimResult(
                        claim_id=claim.claim_id,
                        claim_text=claim.text,
                        status=StatusEnum.UNKNOWN_TIMEOUT,
                        anchor=self.anchor_info(claim),
                        reason="the model did not return a usable verdict",
                    )
                    for claim in spec.claims
                ],
                started=started,
                tokens_in=self.budget.tokens_in,
                tokens_out=self.budget.tokens_out,
            )

        verdict = normalize_verdict(payload.get("verdict"))
        reason = str(payload.get("reason", ""))
        history.append(f"final: {reason}")

        claim_results = [
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=verdict,
                anchor=self.anchor_info(claim),
                reason=reason or "the refined verdict was inconclusive",
                explanation=" | ".join(history),
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


__all__ = ["SelfRefineBaseline"]
