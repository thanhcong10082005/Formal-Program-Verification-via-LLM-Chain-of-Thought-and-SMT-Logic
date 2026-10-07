"""
obligations.py - Two-Query Z3 Protocol (Phase 3).

For each anchored claim c, generate two Z3 solvers:
  (1) Reachability : is the path reachable under ANY pre-conditions?
  (2) Validity     : under the path constraint, is the claim ALWAYS true?
The Validity solver is only run if Reachability is SAT.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

import z3

from .binding import Claim
from .executor import Reachability, TrustedSymbolicExecutor


Z3_TIMEOUT_MS = 5000


@dataclass
class TwoQueryObligations:
    """Holds two Z3 solvers for one claim, plus tracking fields."""

    claim: Claim
    reachability_solver: z3.Solver
    validity_solver: z3.Solver


def _claim_to_z3(expr, executor: TrustedSymbolicExecutor):
    """Convert an assert's boolean expression to a z3.BoolRef via the executor.

    Use a fresh SymState whose bindings are populated for ALL function parameters
    so that name lookups (e.g. `y == z`) get the same Int sort as elsewhere.
    """
    from .executor import SymState  # local import to avoid cycle

    state = SymState()
    # Pre-populate known params from the AST so expressions like `x == y`
    # resolve to Int comparisons (Bool), not Int-Int mismatches.
    for stmt in executor.tree.body:
        if isinstance(stmt, __import__("ast").FunctionDef):
            executor._declare_params(state, stmt)
            break
    val = executor._eval_expr(expr, state)
    # If val is Int, wrap as `val != 0` to coerce to Bool
    if not z3.is_bool(val):
        val = val != 0
    return z3.simplify(val)


def build_obligations(
    claim: Claim,
    reachability: Reachability,
    executor: TrustedSymbolicExecutor,
) -> TwoQueryObligations:
    """Construct two Z3 solvers around the claim and the path's reachability."""
    reach_solv = z3.Solver()
    reach_solv.set("timeout", Z3_TIMEOUT_MS)
    reach_solv.add(reachability.relation)

    valid_solv = z3.Solver()
    valid_solv.set("timeout", Z3_TIMEOUT_MS)
    valid_solv.add(reachability.path_condition)
    # Negate the claim to test whether the path can violate it
    neg_claim = z3.Not(_claim_to_z3(claim.expr_node, executor))
    valid_solv.add(neg_claim)

    return TwoQueryObligations(claim, reach_solv, valid_solv)


def run_reachability(
    obligation: TwoQueryObligations,
) -> Tuple[str, Optional[float], Optional[z3.ModelRef]]:
    """Run reachability solver. Returns ('sat'|'unsat'|'unknown', elapsed, model)."""
    t0 = time.perf_counter()
    res = obligation.reachability_solver.check()
    elapsed = time.perf_counter() - t0
    if res == z3.sat:
        return ("sat", elapsed, obligation.reachability_solver.model())
    if res == z3.unsat:
        return ("unsat", None, None)
    return ("unknown", None, None)


def run_validity(
    obligation: TwoQueryObligations,
) -> Tuple[str, Optional[float], Optional[z3.ModelRef]]:
    """Run validity solver. Returns ('sat'|'unsat'|'unknown', elapsed, model)."""
    t0 = time.perf_counter()
    res = obligation.validity_solver.check()
    elapsed = time.perf_counter() - t0
    if res == z3.sat:
        return ("sat", elapsed, obligation.validity_solver.model())
    if res == z3.unsat:
        return ("unsat", None, None)
    return ("unknown", None, None)


__all__ = [
    "TwoQueryObligations",
    "build_obligations",
    "run_reachability",
    "run_validity",
    "Z3_TIMEOUT_MS",
]