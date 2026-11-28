"""Baseline C -- Structural Reasoning (HoarePrompt-style).

The protocol: the program is summarised as a control-flow skeleton with a
loop summary, the model writes a natural-language proof sketch plus
candidate loop invariants. Z3 checks the candidates and their implication
of the claim over the symbolic states reaching its assertion.
"""

from __future__ import annotations

import ast
import time
from typing import Any

import z3

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt, program_shape_summary
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """Use the control-flow skeleton to propose inductive Python Boolean
expressions over variables in scope at the loop head. Include the facts
needed for the claim when the guard is false. Return one JSON object only.
Keep proof to one sentence; the checker decides the verdict.
Example: {"invariants":["0 <= i <= n"],"proof":"The upper bound holds at exit."}
"""


@register
class StructuralReasoningBaseline(BaseBaseline):
    """C -- Structural Reasoning."""

    id = "C"
    name = "Structural Reasoning"
    supports_grounding = False
    supports_replay = False
    supports_reachability = False
    supports_precondition = True
    requires_llm = True

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.budget = TokenBudget()

    def run(self, spec: ProgramSpec) -> Any:
        """Show the skeleton, ask for an invariant, check it by k-induction."""
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

        skeleton = program_shape_summary(spec.source)
        payload = ask_json(
            self.llm,
            program_prompt(
                spec,
                task=(
                    "Reason about the control-flow skeleton below, propose a "
                    "loop invariant, and decide whether the claim holds."
                ),
                extra=f"Control-flow skeleton:\n{skeleton}",
            ),
            system=SYSTEM,
            budget=self.budget,
        )

        claim_results = [
            self._decide(spec, claim, payload) for claim in spec.claims
        ]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cot_trace=str(payload.get("proof", "")) if isinstance(payload, dict) else "",
        )

    # -- internals ------------------------------------------------------------

    def _decide(self, spec: ProgramSpec, claim, payload: Any) -> ClaimResult:
        """Check the model's invariant by k-induction over the program's states."""
        if not isinstance(payload, dict):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the model did not return a usable answer",
            )

        candidates = payload.get("invariants")
        invariants = (
            [item for item in candidates if isinstance(item, str) and item.strip()]
            if isinstance(candidates, list)
            else []
        )
        reason = str(payload.get("reason", "") or payload.get("proof", ""))

        if not invariants:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="the model proposed no invariant",
            )

        verdict, detail = check_invariant_by_k_induction(
            spec.source, claim.text, invariants, source_line=claim.source_line
        )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=verdict,
            anchor=self.anchor_info(claim),
            reason=reason or detail,
            explanation=detail,
        )


def check_invariant_by_k_induction(
    source: str, claim_text: str, invariants: list[str], *, source_line: int = 0
) -> tuple[StatusEnum, str]:
    """Check that an invariant holds and implies the requested claim on every path."""
    from ..shared.prover import VERDICT_VERIFIED, validate_loop_invariants
    from ..shared.structure import StructureError, analyze
    from ..shared.symexec import Explorer, SymExecError, coerce_bool, eval_expr

    try:
        shape = analyze(source)
    except (SyntaxError, StructureError) as exc:
        return StatusEnum.UNKNOWN_TIMEOUT, f"analysis failed: {exc}"

    if shape.loop is not None:
        proof = validate_loop_invariants(
            source, claim_text, invariants, source_line=source_line
        )
        status = StatusEnum.VERIFIED if proof.verdict == VERDICT_VERIFIED else StatusEnum.UNKNOWN_TIMEOUT
        return status, proof.reason

    try:
        explorer = Explorer(shape.params, var_prefix="k")
        exploration = explorer.explore(shape.func.body)
    except Exception as exc:  # noqa: BLE001
        return StatusEnum.UNKNOWN_TIMEOUT, f"symbolic exploration failed: {exc!r}"

    try:
        claim_node = ast.parse(claim_text.strip(), mode="eval").body
        if source_line > 0:
            sites = [site for site in exploration.assert_sites if site.node.lineno == source_line]
        else:
            claim_key = ast.dump(claim_node)
            sites = [site for site in exploration.assert_sites if ast.dump(site.node.test) == claim_key]
            if len({site.node.lineno for site in sites}) != 1:
                return StatusEnum.UNKNOWN_TIMEOUT, "the claim has no unique assert site"
        if not sites:
            return StatusEnum.UNKNOWN_TIMEOUT, "the claim's assert site was not found"
        conditions = [coerce_bool(eval_expr(claim_node, site.env)) for site in sites]
    except (SyntaxError, SymExecError, z3.Z3Exception) as exc:
        return StatusEnum.UNKNOWN_TIMEOUT, f"the claim could not be translated: {exc}"

    solver = z3.Solver()
    solver.set("timeout", 10_000)
    if solver.check(z3.Or(*(site.cond for site in sites))) != z3.sat:
        return StatusEnum.UNKNOWN_TIMEOUT, "no reachable claim state could be established"

    for text in invariants:
        try:
            invariant_node = ast.parse(text.strip(), mode="eval").body
            candidates = [coerce_bool(eval_expr(invariant_node, site.env)) for site in sites]
        except (SyntaxError, SymExecError, z3.Z3Exception):
            continue

        base_failure = z3.Or(*(
            z3.And(site.cond, z3.Not(invariant))
            for site, invariant in zip(sites, candidates, strict=True)
        ))
        if solver.check(base_failure) != z3.unsat:
            continue

        claim_failure = z3.Or(*(
            z3.And(site.cond, invariant, z3.Not(condition))
            for site, invariant, condition in zip(sites, candidates, conditions, strict=True)
        ))
        if solver.check(claim_failure) == z3.unsat:
            return (
                StatusEnum.VERIFIED,
                f"the invariant {text!r} holds at the site and implies the claim",
            )

    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        "no candidate invariant survived the base case and the inductive step",
    )


__all__ = ["StructuralReasoningBaseline", "check_invariant_by_k_induction"]
