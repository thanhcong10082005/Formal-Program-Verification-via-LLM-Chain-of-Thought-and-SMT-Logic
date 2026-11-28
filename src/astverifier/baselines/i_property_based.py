"""Baseline I -- LLM-Guided Property-Based Testing.

The model reads the program (and its docstring) and proposes an invariant;
the invariant becomes the *strategy* of a property-based search. The search
looks for an input on which the property fails, and any input it finds is
replayed on real CPython before it is reported.

The search itself is deterministic: the strategy is evaluated over a
systematic grid of boundary values and seeded random tuples, so two runs
explore exactly the same inputs.

PBT is the strongest baseline in the "bug hunting" corner of the set -- the
literature reports a bug recall of 42-83% against 31-77% for open-ended
testing. It has no anchor gate, so the Hallucination Rate is ``N/A``.
"""

from __future__ import annotations

import ast
import time
from typing import Any

import z3

from ..shared.oracle import OBS_COUNTEREXAMPLE, OBS_NO_VIOLATION, build_input_grid
from ..shared.oracle import observe as observe_program
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from ..shared.symexec import SymExecError, coerce_bool, eval_expr
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a property-based testing assistant.

Suggest a Python Boolean expression over the function parameters. Inputs
satisfying the expression will be tested first. Return one JSON object with a
short reason, for example {"invariant":"n >= 0 and n <= 2","reason":"Try boundaries."}
"""

#: How many inputs the search explores.
N_INPUTS = 256

SEED = 42


@register
class PropertyBasedTestingBaseline(BaseBaseline):
    """I -- LLM-Guided Property-Based Testing."""

    id = "I"
    name = "LLM-Guided Property-Based Testing"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Derive a strategy, search for a violating input, replay it."""
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
                task="Propose a property-based testing strategy for this program.",
            ),
            system=SYSTEM,
            budget=self.budget,
        )
        invariant = ""
        if isinstance(payload, dict):
            invariant = str(payload.get("invariant", ""))

        params = _parameters(spec.source)
        grid = _prioritize_inputs(build_input_grid(params, seed=SEED), params, invariant)
        outcome = observe_program(
            spec.source,
            spec.function_name,
            params,
            grid=grid[:N_INPUTS],
            program_timeout=15.0,
        )

        claim_results = [
            self._verdict_for(spec, claim, outcome, invariant)
            for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=(
                f"strategy: {invariant or '(none)'}; "
                f"searched {outcome.inputs_tried} inputs"
            ),
        )

    # -- internals ------------------------------------------------------------

    def _verdict_for(self, spec: ProgramSpec, claim, outcome, invariant: str):
        """Turn the search outcome into a claim result."""
        if outcome.observation == OBS_COUNTEREXAMPLE and outcome.witness:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, claim, outcome.witness),
                reason=(
                    f"the property-based search found a violating input "
                    f"in {outcome.inputs_tried} tries"
                ),
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=(
                f"{OBS_NO_VIOLATION} on {outcome.inputs_tried} inputs; "
                "property-based testing cannot prove a claim"
            ),
            explanation=invariant,
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


def _prioritize_inputs(
    grid: list[dict[str, int]], params: list[str], predicate: str
) -> list[dict[str, int]]:
    """Rank inputs by the proposed predicate without executing model text."""
    if not predicate.strip():
        return grid
    symbols = {name: z3.Int(name) for name in params}
    try:
        expression = ast.parse(predicate.strip(), mode="eval").body
        criterion = coerce_bool(eval_expr(expression, symbols))
    except (SyntaxError, SymExecError, z3.Z3Exception):
        return grid

    def preferred(case: dict[str, int]) -> bool:
        substitutions = [(symbol, z3.IntVal(case[name])) for name, symbol in symbols.items()]
        return z3.is_true(z3.simplify(z3.substitute(criterion, *substitutions)))

    return sorted(grid, key=preferred, reverse=True)


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


__all__ = ["PropertyBasedTestingBaseline"]
