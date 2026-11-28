"""Baseline A -- SatLM Direct SMT-LIB2.

The SatLM protocol: the model translates the program and the claim into
SMT-LIB2, and the solver checks ``Constraints AND NOT Spec``.

The translation is *not* anchored to the AST. The model is free to
mis-translate, and when it does, the resulting verdict is wrong for reasons
that have nothing to do with the program's semantics. That is precisely the
weakness this baseline is supposed to expose, so it is not "fixed" here.

Because there is no anchor gate, the Hallucination Rate is ``N/A``: a
translation error is reported as ``TRANSLATION_ERROR`` on the claim, not as
a hallucination of a program fact.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """Translate the Python program and its claim to SMT-LIB2.
Return one JSON object with no Markdown.
- smt: an array of single-line strings, one declare-fun or assert command each.
  Encode the states reaching the claim's line. Do not assert the claim itself.
- claim: a single-line Boolean SMT-LIB2 term using the declared names.
Use fresh names for assigned values (SSA). Summarize loops with closed-form
constraints and encode the loop exit. Omit recursive functions and non-SMT-LIB
syntax. If an exact translation is unavailable, use
{"smt":[],"claim":null}.
Example: {"smt":["(declare-fun n () Int)","(assert (= n 0))"],"claim":"(>= n 0)"}
"""

SYSTEM_SSA_V2 = """Translate the Python program and claim to SMT-LIB2.
Return exactly one JSON object with keys smt (an array of single-line
declare-fun or assert commands) and claim (one Boolean term). No Markdown.

Encode only paths reaching the claim. Include their branch and return guards.
Use a distinct declared symbol for each assignment. Read old names on the
right-hand side and use the latest names in the claim. Declare every free
symbol used in the constraints or claim. Keep the claim out of the assertions.

Example: i = n; i = i + 1; assert i > n
{"smt":["(declare-fun n () Int)","(declare-fun i_0 () Int)","(declare-fun i_1 () Int)","(assert (= i_0 n))","(assert (= i_1 (+ i_0 1)))"],"claim":"(> i_1 n)"}

For a loop, encode its exact exit state, including zero iterations. For
i = n; while i < 3: i = i + 1, declare i_exit and constrain it with
(assert (= i_exit (ite (< n 3) 3 n))). Preserve values on branches where
they are unchanged. Before answering, check for undeclared symbols and
self-contradictory assignments such as (= i (+ i 1)). If you cannot encode
the program exactly, return {"smt":[],"claim":null}.
"""


@register
class SatLMDirectBaseline(BaseBaseline):
    """A -- SatLM Direct SMT-LIB2."""

    id = "A"
    name = "SatLM Direct SMT-LIB2"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None, *, system_prompt: str | None = None) -> None:
        self.llm = llm
        self.system_prompt = SYSTEM_SSA_V2 if system_prompt is None else system_prompt
        # Explicit prompt variants retain their original one-shot protocol.
        self.repair_invalid = system_prompt is None
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Translate, solve, and report."""
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

        claim_results = []
        constraints = []
        for claim in spec.claims:
            claim_spec = replace(
                spec, claims=[claim], precondition=claim.precondition or spec.precondition
            )
            prompt = program_prompt(
                claim_spec, task="Translate the program constraints and claim into SMT-LIB2."
            )
            payload = ask_json(self.llm, prompt, system=self.system_prompt, budget=self.budget)
            decision = self._decide(claim, payload)
            if self.repair_invalid and decision.status == StatusEnum.TRANSLATION_ERROR:
                previous = json.dumps(payload, ensure_ascii=False)[:4096]
                repair_prompt = (
                    f"{prompt}\n\nThe previous answer failed validation: {decision.reason[:500]}"
                    f"\nPrevious translation (possibly truncated):\n{previous}"
                    "\nCorrect the reported problem using the Python source. If the constraints "
                    "are inconsistent, check SSA assignment names, path guards, and the loop "
                    "exit including zero iterations. Preserve all paths reaching the assertion."
                    "\nReturn the corrected JSON translation or the specified null claim "
                    "if an exact translation is unavailable."
                )
                payload = ask_json(
                    self.llm, repair_prompt, system=self.system_prompt, budget=self.budget
                )
                decision = self._decide(claim, payload)
            smt_text = self._smt_text(payload)
            claim_results.append(decision)
            if smt_text:
                constraints.append(smt_text)

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace="\n\n".join(constraints),
            extra={"direct_constraints": constraints},
        )

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _smt_text(payload: Any) -> str:
        commands = payload.get("smt") if isinstance(payload, dict) else None
        if isinstance(commands, str):
            return commands.strip()
        if isinstance(commands, list) and all(isinstance(cmd, str) for cmd in commands):
            return "\n".join(commands).strip()
        return ""

    def _decide(self, claim, payload: Any) -> ClaimResult:
        """Check ``Constraints AND NOT Spec`` with Z3."""
        smt_text = self._smt_text(payload)
        if (
            not smt_text
            or not isinstance(payload.get("claim"), str)
            or not payload["claim"].strip()
        ):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=self.anchor_info(claim),
                reason="the model did not return SMT-LIB2 constraints and a claim",
            )

        claim_term = payload["claim"]

        import z3

        try:
            solver = z3.Solver()
            solver.set("timeout", 20_000)
            formulas = list(z3.parse_smt2_string(f"{smt_text}\n(assert (not {claim_term}))"))
            solver.add(*formulas[:-1])
            consistency = solver.check()
            if consistency == z3.sat:
                solver.add(formulas[-1])
                outcome = solver.check()
            else:
                outcome = z3.unknown
        except Exception as exc:  # noqa: BLE001 - a baseline never crashes
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=self.anchor_info(claim),
                reason=f"the SMT-LIB2 translation could not be parsed: {exc!r}",
            )

        if consistency == z3.unsat:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=self.anchor_info(claim),
                reason="the SMT-LIB2 constraints contain no state reaching the claim",
            )
        if outcome == z3.sat:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                reason="the solver found a model violating the claim",
            )
        if outcome == z3.unsat:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason="the solver proved the claim cannot be violated",
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason="the solver returned 'unknown'",
        )


__all__ = ["SatLMDirectBaseline"]
