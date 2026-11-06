"""Baseline B -- Execution Grounding.

The protocol: run the program on a deterministic grid of inputs under a
trace, show the model the observed values, and let it judge the claim
against that evidence.

The trace is the grounding: the model never sees a static description of
the program, only what actually happened when it ran. That is what makes
this baseline different from D (LLM-as-a-Judge), which reasons from the
source alone.

The baseline can produce a concrete counterexample, and that counterexample
is replayed on real CPython, so the Counterexample Replay Validity Rate is
measurable for it.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.oracle import OBS_COUNTEREXAMPLE, OBS_NO_VIOLATION, build_input_grid
from ..shared.oracle import observe as observe_program
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, parse_witness, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are an execution-grounded verification assistant.

You are given the source of a program, the observed behaviour of that
program on a set of concrete inputs, and a claim. Decide whether the claim
holds for the observed behaviour.

Answer with a JSON object of the form:
{"verdict": "VERIFIED" | "COUNTEREXAMPLE" | "UNKNOWN", "reason": "<why>",
 "witness": {"<param>": <int>}}
"""

#: How many inputs are executed to ground the model.
N_INPUTS = 32

SEED = 42


@register
class ExecutionGroundingBaseline(BaseBaseline):
    """B -- Execution Grounding."""

    id = "B"
    name = "Execution Grounding"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Execute the program, then let the model judge the trace."""
        started = time.perf_counter()
        self.budget = TokenBudget()

        params = _parameters(spec.source)
        grid = build_input_grid(params, seed=SEED)[:N_INPUTS] or [{}]
        outcome = observe_program(
            spec.source,
            spec.function_name,
            params,
            grid=grid,
            program_timeout=15.0,
        )

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

        trace = _render_trace(grid, outcome)
        payload = ask_json(
            self.llm,
            program_prompt(
                spec,
                task=(
                    "Decide whether each claim holds, using the observed "
                    "behaviour below as the primary evidence."
                ),
                extra=f"Observed behaviour:\n{trace}",
            ),
            system=SYSTEM,
            budget=self.budget,
        )

        claim_results = [
            self._decide(spec, claim, payload, outcome) for claim in spec.claims
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

    # -- internals ------------------------------------------------------------

    def _decide(
        self, spec: ProgramSpec, claim, payload: Any, outcome
    ) -> ClaimResult:
        """Turn the model's judgement into a claim result."""
        # A concrete violation observed by the oracle outranks the model:
        # it is a fact, not an opinion.
        if outcome.observation == OBS_COUNTEREXAMPLE and outcome.witness:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, outcome.witness),
                reason="the execution trace shows a violating input",
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

        if verdict == StatusEnum.COUNTEREXAMPLE:
            witness = parse_witness(payload) or dict(outcome.witness or {})
            if witness:
                return ClaimResult(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    status=StatusEnum.COUNTEREXAMPLE,
                    anchor=self.anchor_info(claim),
                    counterexample=self._counterexample(spec, witness),
                    reason=reason or "the model reported a counterexample",
                )
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the model reported a counterexample without a witness",
            )

        if verdict == StatusEnum.VERIFIED:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=reason or "the observed behaviour is consistent with the claim",
            )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=reason or "the model could not decide",
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


def _parameters(source: str) -> list[str]:
    """Return the parameter names of the function under verification."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return [arg.arg for arg in node.args.args]
    return []


def _render_trace(grid: list[dict[str, int]], outcome) -> str:
    """Render the observed behaviour as a compact trace."""
    if outcome.observation == OBS_COUNTEREXAMPLE:
        return (
            "The program raised an AssertionError on at least one input.\n"
            f"Violating input: {outcome.witness}"
        )
    if outcome.observation == OBS_NO_VIOLATION:
        return (
            f"The program completed on all {outcome.inputs_tried} inputs "
            "without raising an assertion error.\n"
            f"First inputs: {grid[:5]}"
        )
    return f"The program's behaviour could not be observed: {outcome.error}"


__all__ = ["ExecutionGroundingBaseline"]
