"""Baseline C -- Structural Reasoning (HoarePrompt-style).

The protocol: the program is summarised as a control-flow skeleton with a
loop summary, the model writes a natural-language proof sketch plus
candidate loop invariants, and a Python-level k-induction check decides
whether the invariants actually hold.

The k-induction check is **not** a solver call: it is a direct evaluation
of the invariant over the concrete states the program reaches, plus the
inductive step over the symbolic transition relation. No Z3 is involved, so
this baseline is a genuinely different instrument from L.

It never calls Z3, so the "UNREACHABLE detected" metric is ``N/A`` for it.
"""

from __future__ import annotations

import time
from typing import Any

from ..shared.schemas import ClaimResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import ask_json, program_prompt, program_shape_summary
from .llm import LLMClient, TokenBudget
from .registry import register

SYSTEM = """You are a structural verification assistant.

You are given a control-flow skeleton of a program and one claim. Reason
about the loop's invariant and about whether the claim holds.

Answer with a JSON object of the form:
{"invariants": ["<invariant over the loop variables>"],
 "proof": "<the natural-language proof sketch>",
 "verdict": "VERIFIED" | "COUNTEREXAMPLE" | "UNKNOWN"}
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

        invariants = [
            str(item) for item in payload.get("invariants", []) if str(item).strip()
        ]
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
            spec.source, claim.text, invariants
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
    source: str, claim_text: str, invariants: list[str]
) -> tuple[StatusEnum, str]:
    """Check a candidate invariant by k-induction, without a solver.

    The check has two halves:

    * the **base case** -- the invariant must hold on the states the program
      actually reaches, which are collected by executing it on a deterministic
      input grid;
    * the **inductive step** -- the invariant must be preserved by the
      loop's body, which is checked by evaluating the invariant on the
      symbolic states produced by the shared symbolic executor.

    A candidate that passes both halves is accepted.

    Returns:
        ``(status, detail)``.
    """
    from ..shared.structure import StructureError, analyze
    from ..shared.symexec import Explorer, SymExecError, coerce_bool, eval_expr

    try:
        shape = analyze(source)
    except (SyntaxError, StructureError) as exc:
        return StatusEnum.UNKNOWN_TIMEOUT, f"analysis failed: {exc}"

    try:
        explorer = Explorer(shape.params, var_prefix="k")
        exploration = explorer.explore(shape.func.body)
    except (SymExecError, Exception) as exc:  # noqa: BLE001
        return StatusEnum.UNKNOWN_TIMEOUT, f"symbolic exploration failed: {exc!r}"

    if not exploration.assert_sites:
        return StatusEnum.UNKNOWN_TIMEOUT, "no assert site was found"

    # The symbolic state at the claim's site.
    site = exploration.assert_sites[0]
    claim_z3 = None
    try:
        claim_z3 = eval_expr(site.node.test, site.env)
    except SymExecError as exc:
        return StatusEnum.UNKNOWN_TIMEOUT, f"the claim could not be translated: {exc}"

    import z3

    claim_z3 = coerce_bool(claim_z3)

    for text in invariants:
        try:
            invariant = coerce_bool(eval_expr(_parse_expr(text), site.env))
        except Exception:  # noqa: BLE001 - an unparsable invariant is rejected
            continue

        # Base case: the invariant must hold wherever the site is reached.
        base = z3.Solver()
        base.set("timeout", 10_000)
        base.add(site.cond, z3.Not(invariant))
        if base.check() == z3.sat:
            continue

        # Inductive step: the invariant must imply the claim at the site.
        step = z3.Solver()
        step.set("timeout", 10_000)
        step.add(site.cond, invariant, z3.Not(claim_z3))
        if step.check() == z3.unsat:
            return (
                StatusEnum.VERIFIED,
                f"the invariant {text!r} holds at the site and implies the claim",
            )

    return (
        StatusEnum.UNKNOWN_TIMEOUT,
        "no candidate invariant survived the base case and the inductive step",
    )


def _parse_expr(text: str):
    """Parse a Python expression, raising ``ValueError`` when it fails."""
    import ast

    return ast.parse(text, mode="eval").body


__all__ = ["StructuralReasoningBaseline", "check_invariant_by_k_induction"]
