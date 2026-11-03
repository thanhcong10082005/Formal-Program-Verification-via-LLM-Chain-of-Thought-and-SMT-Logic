"""Z3 obligations for the AST-anchored protocol.

For each anchored claim ``c`` two Z3 solvers are built:

1. **Reachability** -- is the target path reachable under *any* pre-condition?
2. **Validity** -- under the path condition and the target state equations,
   does the claim *always* hold?

The validity solver is only run when the reachability solver is ``sat``. This
two-query protocol is the core of the Proposed Method's decidability claim:
it distinguishes *unreachable* from *violated* from *provably true*.
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass

import z3

from .binding import Claim
from .executor import Reachability, SymState, TrustedSymbolicExecutor

Z3_TIMEOUT_MS = 5000


@dataclass
class TwoQueryObligations:
    """The two Z3 solvers for one claim, plus the claim itself."""

    claim: Claim
    reachability_solver: z3.Solver
    validity_solver: z3.Solver


def _claim_to_z3(
    expr: ast.expr,
    executor: TrustedSymbolicExecutor | None = None,
    state: SymState | None = None,
):
    """Convert an assert's boolean expression into a ``z3.BoolRef``.

    A fresh :class:`SymState` is used when none is supplied. It is populated
    with every function parameter so that name lookups (e.g. ``y == z``) get
    the same integer sort as elsewhere.
    """
    if state is None:
        state = SymState()
        if executor is not None:
            for stmt in executor.tree.body:
                if isinstance(stmt, ast.FunctionDef):
                    executor._declare_params(state, stmt)
                    break

    evaluator = executor
    if evaluator is None:
        evaluator = TrustedSymbolicExecutor(ast.Module(body=[], type_ignores=[]), {})
    value = evaluator._eval_expr(expr, state)
    # An integer-valued expression is coerced with `value != 0`.
    if not z3.is_bool(value):
        value = value != 0
    return z3.simplify(value)


def build_obligations(
    claim: Claim,
    reachability: Reachability,
    executor: TrustedSymbolicExecutor,
    *,
    precondition: ast.expr | str | None = None,
) -> TwoQueryObligations:
    """Construct the two Z3 solvers around a claim and its path reachability."""
    reach_solver = z3.Solver()
    reach_solver.set("timeout", Z3_TIMEOUT_MS)
    reach_solver.add(reachability.relation)

    valid_solver = z3.Solver()
    valid_solver.set("timeout", Z3_TIMEOUT_MS)
    # The explicit state equations are included alongside the path condition.
    # The claim is translated against `reachability.state`, so Z3 checks the
    # state at the target rather than a fresh unconstrained local.
    valid_solver.add(reachability.relation)
    valid_solver.add(z3.Not(_claim_to_z3(claim.expr_node, executor, state=reachability.state)))

    if precondition is not None:
        if isinstance(precondition, str):
            pre_str = precondition.strip()
            if pre_str and pre_str.lower() != "none":
                try:
                    pre_node = ast.parse(pre_str, mode="eval").body
                except Exception:
                    pre_node = None
            else:
                pre_node = None
        else:
            pre_node = precondition

        if pre_node is not None:
            pre_z3 = _claim_to_z3(pre_node, executor, state=None)
            reach_solver.add(pre_z3)
            valid_solver.add(pre_z3)

    return TwoQueryObligations(claim, reach_solver, valid_solver)


def run_reachability(
    obligation: TwoQueryObligations,
) -> tuple[str, float | None, z3.ModelRef | None]:
    """Run the reachability solver.

    Returns:
        ``("sat"|"unsat"|"unknown", elapsed_seconds, model)``.
    """
    t0 = time.perf_counter()
    result = obligation.reachability_solver.check()
    elapsed = time.perf_counter() - t0
    if result == z3.sat:
        return ("sat", elapsed, obligation.reachability_solver.model())
    if result == z3.unsat:
        return ("unsat", None, None)
    return ("unknown", None, None)


def run_validity(
    obligation: TwoQueryObligations,
) -> tuple[str, float | None, z3.ModelRef | None]:
    """Run the validity solver.

    Returns:
        ``("sat"|"unsat"|"unknown", elapsed_seconds, model)``.
    """
    t0 = time.perf_counter()
    result = obligation.validity_solver.check()
    elapsed = time.perf_counter() - t0
    if result == z3.sat:
        return ("sat", elapsed, obligation.validity_solver.model())
    if result == z3.unsat:
        return ("unsat", None, None)
    return ("unknown", None, None)


__all__ = [
    "TwoQueryObligations",
    "Z3_TIMEOUT_MS",
    "build_obligations",
    "run_reachability",
    "run_validity",
]
