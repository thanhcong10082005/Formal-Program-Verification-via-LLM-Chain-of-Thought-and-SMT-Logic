"""Baseline F -- Agentic Multi-Step Verifier.

The model works through a fixed sequence of steps:

1. **PLAN** -- what has to be shown about the claim;
2. **ANALYZE** -- the control-flow reasoning;
3. **TEST** -- concrete inputs to try, which are then executed on real
   CPython;
4. **VERIFY** -- the verdict given the plan, the analysis and the test
   results;
5. **REFINE** -- a new attempt when the verdict was inconclusive.

Each step is one model call, so a run costs four to five calls per claim.
The executed test inputs ground the final verdict, so a counterexample the
agent reports is replayed and the Counterexample Replay Validity Rate is
measurable for this baseline.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.oracle import build_input_grid
from ..shared.oracle import observe as observe_program
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, normalize_verdict, parse_witness, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are an agentic verification assistant. You work through
the steps PLAN, ANALYZE, TEST and VERIFY in order, one step per answer.

Answer with a JSON object of the form:
{"step": "PLAN" | "ANALYZE" | "TEST" | "VERIFY",
 "content": "<the content of this step>",
 "inputs": [{"name": "<param>", "value": <int>}],
 "verdict": "VERIFIED" | "COUNTEREXAMPLE" | "UNKNOWN"}
"""

#: The number of test inputs the agent is asked to propose.
N_TEST_INPUTS = 8

SEED = 42


@register
class AgenticBaseline(BaseBaseline):
    """F -- Agentic Multi-Step Verifier."""

    id = "F"
    name = "Agentic Multi-Step Verifier"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Run the PLAN -> ANALYZE -> TEST -> VERIFY loop."""
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

        base_prompt = program_prompt(
            spec,
            task="Verify the claim through the agentic steps.",
        )

        plan = self._step(base_prompt, "PLAN")
        analysis = self._step(base_prompt, "ANALYZE", context=plan)
        test = self._step(base_prompt, "TEST", context=analysis)
        observation = self._execute(spec, test)
        verdict_payload = self._step(
            base_prompt, "VERIFY", context=f"{analysis}\n{observation}"
        )

        claim_results = [
            self._decide(spec, claim, verdict_payload, observation)
            for claim in spec.claims
        ]

        trace = " | ".join(part for part in (plan, analysis, test, observation) if part)

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

    def _step(self, prompt: str, step: str, context: str = "") -> str:
        """Run one agentic step and return its content."""
        text = prompt + f"\n\nPerform the {step} step now."
        if context:
            text += f"\n\nSo far:\n{context}"
        payload = ask_json(self.llm, text, system=SYSTEM, budget=self.budget)
        if isinstance(payload, dict):
            content = str(payload.get("content", ""))
            extra = payload.get("inputs")
            if extra:
                content += f"\ninputs={extra}"
            return content
        return ""

    def _execute(self, spec: ProgramSpec, test: str) -> str:
        """Execute the agent's proposed test inputs and report the outcome."""
        params = _parameters(spec.source)
        grid = build_input_grid(params, seed=SEED)[:N_TEST_INPUTS] or [{}]
        outcome = observe_program(
            spec.source,
            spec.function_name,
            params,
            grid=grid,
            program_timeout=10.0,
        )
        if outcome.observation == "COUNTEREXAMPLE":
            return f"TEST RESULT: AssertionError on {outcome.witness}"
        return (
            f"TEST RESULT: the program completed on {outcome.inputs_tried} "
            "inputs without an assertion error"
        )

    def _decide(
        self, spec: ProgramSpec, claim, payload: Any, observation: str
    ) -> ClaimResult:
        """Turn the agent's verdict into a claim result."""
        if not isinstance(payload, dict):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the agent did not return a usable verdict",
            )

        verdict = normalize_verdict(payload.get("verdict"))
        reason = str(payload.get("content", ""))

        if verdict == StatusEnum.COUNTEREXAMPLE:
            witness = parse_witness(payload)
            if not witness:
                return ClaimResult(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    status=StatusEnum.UNKNOWN_TIMEOUT,
                    anchor=self.anchor_info(claim),
                    reason=reason
                    or "the agent reported a counterexample without a witness",
                )
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, witness),
                reason=reason or "the agent reported a counterexample",
            )

        if verdict == StatusEnum.VERIFIED:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=reason or "the agent accepted the claim",
                explanation=observation,
            )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=reason or "the agent could not decide",
            explanation=observation,
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


__all__ = ["AgenticBaseline"]
