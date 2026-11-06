"""Baseline M -- Hoare Triple in Natural Language.

The model writes the triple ``{P} c {Q}`` in prose -- a precondition, the
command, and a postcondition -- and then assesses for itself whether the
triple is valid.

It is the "self-assessed" variant of C: the same informal reasoning, but
without the k-induction check, and with everything expressed in natural
language rather than in a symbolic form.

Because the assessment is entirely the model's opinion, the baseline never
reports a counterexample it cannot witness, so the Counterexample Replay
Validity Rate and the "UNREACHABLE detected" metric are both ``N/A``.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a Hoare-logic assistant.

You are given a program and a claim. Write the Hoare triple
`{P} c {Q}` in natural language, where P is the precondition, c is the
command, and Q is the postcondition. Then assess whether the triple is
valid.

Answer with a JSON object of the form:
{"pre": "<the precondition in prose>", "command": "<the command in prose>",
 "post": "<the postcondition in prose>", "valid": true | false,
 "reason": "<why>"}
"""


@register
class HoareNaturalLanguageBaseline(BaseBaseline):
    """M -- Hoare Triple in Natural Language."""

    id = "M"
    name = "Hoare Triple (Natural Language)"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Ask for the triple and its self-assessment."""
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
                task="Write the Hoare triple for this program in natural language.",
            ),
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
                        reason="the model did not return a usable triple",
                    )
                    for claim in spec.claims
                ],
                started=started,
                tokens_in=self.budget.tokens_in,
                tokens_out=self.budget.tokens_out,
            )

        triple = (
            f"{{{payload.get('pre', '')}}} "
            f"{payload.get('command', '')} "
            f"{{{payload.get('post', '')}}}"
        )
        reason = str(payload.get("reason", ""))

        if payload.get("valid") is True:
            status = StatusEnum.VERIFIED
        elif payload.get("valid") is False:
            status = StatusEnum.COUNTEREXAMPLE
        else:
            status = normalize_verdict(payload.get("verdict"))

        claim_results = [
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=status,
                anchor=self.anchor_info(claim),
                reason=reason or "the self-assessment was inconclusive",
                explanation=triple,
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
            cot_trace=triple,
        )


__all__ = ["HoareNaturalLanguageBaseline"]
