"""Baseline L -- Symbolic Execution thuần (offline, 0 token).

Pure symbolic execution: the program is explored over Z3 symbols, the paths
that reach the claim's site are enumerated, and the claim is checked on each
of them.

Unlike the Core Proposed Method it is an **independent** implementation: it
uses :mod:`astverifier.shared.symexec` and :mod:`astverifier.shared.prover`,
never :mod:`astverifier.core`. The two therefore agree only where the
semantics agree, which is exactly what the comparison is supposed to measure.

It is the only baseline that can answer "is the site reachable?", so the
"UNREACHABLE detected" metric is measurable for it.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.prover import (
    VERDICT_COUNTEREXAMPLE,
    VERDICT_INCONCLUSIVE,
    VERDICT_UNREACHABLE,
    VERDICT_VERIFIED,
    prove,
    prove_reachability,
)
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .registry import register


@register
class SymbolicExecutionBaseline(BaseBaseline):
    """L -- Symbolic Execution."""

    id = "L"
    name = "Symbolic Execution"
    supports_grounding = False
    supports_replay = True
    supports_reachability = True
    supports_precondition = True
    requires_llm = False

    def run(self, spec: ProgramSpec) -> Any:
        """Symbolically explore the program and decide each claim."""
        started = time.perf_counter()
        claim_results = [self._decide(spec, claim) for claim in spec.claims]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            cot_trace=(
                "symbolic execution over the QF-LIA subset; "
                "reachability and validity decided by Z3"
            ),
        )

    # -- internals ------------------------------------------------------------

    def _decide(self, spec: ProgramSpec, claim) -> ClaimResult:
        """Decide one claim with the two-query protocol."""
        try:
            proof = prove(
                spec.source,
                claim.text,
                precondition=claim.precondition or spec.precondition,
            )
        except Exception as exc:  # noqa: BLE001 - a baseline never crashes
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason=f"symbolic execution failed: {exc!r}",
            )

        if proof.verdict == VERDICT_VERIFIED:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=proof.reason,
                explanation=f"proved over {proof.n_reachable} reachable site(s)",
            )

        if proof.verdict == VERDICT_COUNTEREXAMPLE:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, proof.witness or {}),
                reason=proof.reason,
            )

        if proof.verdict == VERDICT_UNREACHABLE:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNREACHABLE,
                anchor=self.anchor_info(claim),
                reason=proof.reason,
            )

        # The prover could not decide. Ask the exact reachability query: it
        # is cheaper than the full proof and settles the UNREACHABLE case.
        try:
            reach = prove_reachability(spec.source, claim.text)
        except Exception:  # noqa: BLE001 - a baseline never crashes
            reach = None

        if reach is not None and reach.verdict == VERDICT_UNREACHABLE:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNREACHABLE,
                anchor=self.anchor_info(claim),
                reason=reach.reason,
            )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=proof.reason or f"prover was {VERDICT_INCONCLUSIVE}",
        )

    def _counterexample(self, spec: ProgramSpec, witness: dict[str, int]):
        """Build the counterexample record, replayed on real CPython."""
        replay = replay_counterexample(
            spec.source,
            spec.function_name,
            witness,
        )
        return CounterexampleInfo(
            z3_model=dict(witness),
            replay_verdict=replay.success,
            replay_output=replay.output_repr,
            replay_diff=replay.error,
        )


__all__ = ["SymbolicExecutionBaseline"]
