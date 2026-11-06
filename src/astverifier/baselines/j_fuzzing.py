"""Baseline J -- Deterministic Fuzzing (offline, 0 token).

The floor baseline: no LLM, no SMT. It generates a deterministic grid of
inputs, runs the program on real CPython, and compares the observed values
against the claim.

What it can establish:

* a *concrete counterexample* -- an input on which the claim is false;
* nothing else. "No violation found" is **not** a proof, so the baseline
  reports ``UNKNOWN_TIMEOUT`` rather than ``VERIFIED``.

It has no AST-anchor gate and no reachability query, so the Hallucination
Rate and the "UNREACHABLE detected" metric are ``N/A`` for it.
"""

from __future__ import annotations

import ast
import time
from typing import Any

from ..shared.oracle import (
    OBS_COUNTEREXAMPLE,
    OBS_NO_VIOLATION,
    OBS_TIMEOUT,
    OBS_UNREACHABLE,
    OracleOutcome,
    build_input_grid,
    observe,
)
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .registry import register

#: How many inputs the fuzzer tries per program.
N_INPUTS = 256

#: The seed that makes the input grid reproducible.
SEED = 42


@register
class DeterministicFuzzingBaseline(BaseBaseline):
    """J -- Deterministic Fuzzing."""

    id = "J"
    name = "Deterministic Fuzzing"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = True
    requires_llm = False

    def run(self, spec: ProgramSpec) -> Any:
        """Fuzz the program and report the outcome."""
        started = time.perf_counter()
        outcome = self._fuzz(spec)
        claim_results = [self._verdict_for(spec, claim, outcome) for claim in spec.claims]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            cot_trace=(
                f"fuzzing tried {outcome.inputs_tried} deterministic inputs; "
                f"observation={outcome.observation}"
            ),
        )

    # -- internals ------------------------------------------------------------

    def _fuzz(self, spec: ProgramSpec) -> OracleOutcome:
        """Run the program over a deterministic input grid."""
        params = _parameters(spec.source)
        grid = build_input_grid(params, seed=SEED)[:N_INPUTS] or [{}]
        return observe(
            spec.source,
            spec.function_name,
            params,
            grid=grid,
            program_timeout=15.0,
        )

    def _verdict_for(self, spec: ProgramSpec, claim, outcome: OracleOutcome) -> ClaimResult:
        """Turn one oracle observation into a claim result."""
        if outcome.observation == OBS_COUNTEREXAMPLE and outcome.witness:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, claim, outcome),
                reason=(
                    f"fuzzing found a violating input in {outcome.inputs_tried} tries"
                ),
            )
        if outcome.observation == OBS_UNREACHABLE:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason=(
                    "the assert never executed on the fuzzing grid; a finite "
                    "grid cannot prove unreachability"
                ),
            )
        if outcome.observation == OBS_TIMEOUT:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason=outcome.error or "the program timed out during fuzzing",
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=(
                f"{OBS_NO_VIOLATION} on {outcome.inputs_tried} inputs; "
                "fuzzing cannot prove a claim"
            ),
        )

    def _counterexample(
        self, spec: ProgramSpec, claim, outcome: OracleOutcome
    ) -> CounterexampleInfo:
        """Build the counterexample record, replayed on real CPython."""
        witness = dict(outcome.witness or {})
        replay = replay_counterexample(
            spec.source,
            spec.function_name,
            witness,
        )
        return CounterexampleInfo(
            z3_model=witness,
            replay_verdict=replay.success,
            replay_output=replay.output_repr,
            replay_diff=replay.error,
        )


def _parameters(source: str) -> list[str]:
    """Return the parameter names of the function under verification."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return [arg.arg for arg in node.args.args]
    return []


__all__ = ["DeterministicFuzzingBaseline"]
