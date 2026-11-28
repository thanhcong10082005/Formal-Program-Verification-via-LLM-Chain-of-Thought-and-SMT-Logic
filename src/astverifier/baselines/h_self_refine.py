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

SYSTEM = """Judge the claim at its source line. Check branch guards and loop
boundaries before deciding whether the claim holds for all permitted inputs.
Return only one JSON object. Use VERIFIED, COUNTEREXAMPLE, or UNKNOWN as
verdict. Keep reason and critique to one brief sentence each; use an empty
critique when no revision is needed.
Example: {"verdict":"UNKNOWN","reason":"The loop exit is unclear.","critique":"Check zero iterations."}
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

        payload = None
        history: list[str] = []
        next_prompt = prompt
        for round_index in range(N_ROUNDS):
            revised = ask_json(
                self.llm,
                next_prompt,
                system=SYSTEM,
                budget=self.budget,
            )
            value = revised.get("verdict") if isinstance(revised, dict) else None
            if normalize_verdict(value) == StatusEnum.UNKNOWN_TIMEOUT and (
                not isinstance(value, str)
                or value.strip().upper() not in {"UNKNOWN", "UNKNOWN_TIMEOUT"}
            ):
                history.append(f"round {round_index + 1}: no usable verdict")
                if payload is not None:
                    break
                next_prompt = prompt + "\nReturn the JSON verdict and both text fields."
                continue
            payload = revised
            reason = str(payload.get("reason") or "").strip()
            critique = str(payload.get("critique") or "").strip()
            history.append(f"round {round_index + 1}: {reason}")
            if not critique:
                break
            next_prompt = (
                f"{prompt}\n\nPrevious verdict: {value}\nReason: {reason}"
                f"\nCritique: {critique}\nRevise and return one JSON object."
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
