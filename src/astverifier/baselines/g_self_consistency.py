"""Baseline G -- Self-Consistency Ensemble.

The model is sampled ``n`` times at a non-zero temperature and the verdicts
are combined by majority vote. A tie is reported as ``UNKNOWN_TIMEOUT``
rather than broken arbitrarily.

The **agreement rate** -- the share of samples that agreed with the
majority -- is recorded in the reasoning trace. It is a secondary
indicator: a high agreement rate means the model was sure, not that it was
right.

G is the fairness check on D: if G beats D significantly, then the report
was under-rating D and the framing has to be corrected before submission.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a program verification assistant. Decide whether the
given claim holds for the given program.

Answer with a JSON object of the form:
{"verdict": "VERIFIED" | "COUNTEREXAMPLE" | "UNKNOWN", "reason": "<why>"}
"""

#: How many samples are drawn.
N_SAMPLES = 5

#: The temperature used for the samples.
TEMPERATURE = 0.7


@register
class SelfConsistencyBaseline(BaseBaseline):
    """G -- Self-Consistency Ensemble."""

    id = "G"
    name = "Self-Consistency Ensemble"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Sample ``n`` verdicts and take the majority."""
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

        samples: list[StatusEnum] = []
        reasons: list[str] = []
        for _ in range(N_SAMPLES):
            payload = ask_json(
                self.llm,
                prompt,
                system=SYSTEM,
                temperature=TEMPERATURE,
                budget=self.budget,
            )
            if isinstance(payload, dict):
                samples.append(normalize_verdict(payload.get("verdict")))
                reasons.append(str(payload.get("reason", "")))
            else:
                samples.append(StatusEnum.UNKNOWN_TIMEOUT)

        counts = Counter(samples)
        verdict, agreement = _majority(counts)

        trace = (
            f"samples: {[s.value for s in samples]}; "
            f"agreement rate: {agreement:.0%}; "
            f"reasons: {' | '.join(r for r in reasons if r)[:400]}"
        )

        claim_results = [
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=verdict,
                anchor=self.anchor_info(claim),
                reason=(
                    f"majority vote over {N_SAMPLES} samples "
                    f"(agreement {agreement:.0%})"
                ),
                explanation=trace,
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
            cot_trace=trace,
        )


def _majority(counts: Counter) -> tuple[StatusEnum, float]:
    """Return the majority verdict and the agreement rate.

    A tie is reported as ``UNKNOWN_TIMEOUT``: the ensemble genuinely does
    not know, and the metric layer must see that rather than a coin flip.
    """
    if not counts:
        return StatusEnum.UNKNOWN_TIMEOUT, 0.0
    top = counts.most_common()
    best_count = top[0][1]
    winners = [status for status, count in top if count == best_count]
    total = sum(counts.values())
    if len(winners) > 1:
        return StatusEnum.UNKNOWN_TIMEOUT, best_count / total
    return winners[0], best_count / total


__all__ = ["SelfConsistencyBaseline"]
