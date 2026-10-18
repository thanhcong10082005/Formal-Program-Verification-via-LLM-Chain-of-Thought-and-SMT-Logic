"""
obligations.py - Z3 obligations for anchored and direct baselines.

For each anchored claim c, generate two Z3 solvers:
  (1) Reachability : is the path reachable under ANY pre-conditions?
  (2) Validity     : under the path constraint, is the claim ALWAYS true?
The Validity solver is only run if Reachability is SAT.  The anchored SMT
formalization path uses a separate declarative entailment obligation: jointly
assumed constraints are conjoined with the negation of one target
specification.  The simple unanchored path uses ``SimpleObligation`` below and
has no context at all.
"""
from __future__ import annotations

import ast
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import z3

from .binding import Claim
from .executor import Reachability, SymState, TrustedSymbolicExecutor


Z3_TIMEOUT_MS = 5000


@dataclass
class TwoQueryObligations:
    """Holds two Z3 solvers for one claim, plus tracking fields."""

    claim: Claim
    reachability_solver: z3.Solver
    validity_solver: z3.Solver


@dataclass
class DirectObligation:
    """A SatLM-style entailment obligation ``constraints AND not target``."""

    constraints: z3.BoolRef
    formula: z3.BoolRef
    context_solver: z3.Solver
    validity_solver: z3.Solver


@dataclass
class DirectContext:
    """The consistency query for a joint direct-formalization context."""

    constraints: z3.BoolRef
    solver: z3.Solver


@dataclass
class SimpleObligation:
    """One simple claim validity query containing only ``Not(formula)``."""

    formula: z3.BoolRef
    validity_solver: z3.Solver


def _claim_to_z3(
    expr,
    executor: Optional[TrustedSymbolicExecutor] = None,
    state: Optional[SymState] = None,
):
    """Convert an assert's boolean expression to a z3.BoolRef via the executor.

    Use a fresh SymState whose bindings are populated for ALL function parameters
    so that name lookups (e.g. `y == z`) get the same Int sort as elsewhere.
    """
    from .executor import SymState  # local import to avoid cycle

    if state is None:
        state = SymState()
        if executor is not None:
            # Pre-populate known params from the AST so expressions like
            # `x == y` resolve to Int comparisons (Bool), not free names.
            for stmt in executor.tree.body:
                if isinstance(stmt, ast.FunctionDef):
                    executor._declare_params(state, stmt)
                    break
        else:
            # UNANCHORED predicates intentionally have no program state. Each
            # name is an independent free integer in the predicate itself.
            for node in ast.walk(expr):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    state.set(node.id, z3.Int(node.id))

    evaluator = executor
    if evaluator is None:
        evaluator = TrustedSymbolicExecutor(
            ast.Module(body=[], type_ignores=[]), {}
        )
    val = evaluator._eval_expr(expr, state)
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
    # Include the explicit state equations as well as the path condition.
    # The claim is translated against reachability.state below, so Z3 checks
    # the state at the target rather than a fresh unconstrained local.
    valid_solv.add(reachability.relation)
    # Negate the claim to test whether the path can violate it
    neg_claim = z3.Not(
        _claim_to_z3(claim.expr_node, executor, state=reachability.state)
    )
    valid_solv.add(neg_claim)

    return TwoQueryObligations(claim, reach_solv, valid_solv)


def build_anchored_direct_obligations(
    claim: Claim,
    reachability: Reachability,
    constraints: z3.BoolRef,
) -> TwoQueryObligations:
    """Build anchored reachability/validity queries for an SMT target.

    This is the hybrid protocol used by the improved AST baseline.  The AST
    supplies the target path and reached state; SatLM-style constraints supply
    additional declarative relations; and the target formula is substituted
    into the reached state before checking its negation.
    """
    if claim.direct_expression is None:
        raise TypeError("anchored direct claim has no validated formula")
    if not z3.is_bool(constraints):
        raise TypeError("anchored direct obligations require Boolean constraints")

    # Ground every context symbol that is available in the AST-derived target
    # state.  This keeps the SatLM relations connected to the trusted state
    # instead of leaving an independent ``x`` beside ``_state_*_x``.  Symbols
    # that describe a different program phase may remain declarative/free; the
    # target itself is still required to bind every one of its variables below.
    context_replacements = []
    for declaration in _integer_constants(constraints):
        state_value = reachability.state.get(declaration)
        if state_value is None:
            continue
        if not z3.is_int(state_value):
            continue
        context_replacements.append((z3.Int(declaration), state_value))
    grounded_constraints = z3.simplify(
        z3.substitute(constraints, *context_replacements)
        if context_replacements
        else constraints
    )

    replacements = []
    for name in claim.direct_variables:
        state_value = reachability.state.get(name)
        if state_value is None:
            raise ValueError(
                f"variable {name!r} is not available at the anchored target state"
            )
        if not z3.is_int(state_value):
            raise ValueError(
                f"anchored direct variable {name!r} does not have Int state"
            )
        replacements.append((z3.Int(name), state_value))

    target = z3.simplify(
        z3.substitute(claim.direct_expression, *replacements)
        if replacements
        else claim.direct_expression
    )
    if not z3.is_bool(target):
        raise TypeError("anchored direct target must remain Boolean after binding")

    reach_solv = z3.Solver()
    reach_solv.set("timeout", Z3_TIMEOUT_MS)
    reach_solv.add(reachability.relation, grounded_constraints)

    valid_solv = z3.Solver()
    valid_solv.set("timeout", Z3_TIMEOUT_MS)
    valid_solv.add(reachability.relation, grounded_constraints, z3.Not(target))
    return TwoQueryObligations(claim, reach_solv, valid_solv)


def _integer_constants(formula: z3.BoolRef) -> set[str]:
    """Return names of uninterpreted Int constants occurring in ``formula``."""
    names: set[str] = set()
    for node in z3.z3util.get_vars(formula):
        if z3.is_int(node):
            names.add(str(node.decl().name()))
    return names


def build_unanchored_obligations(claim: Claim) -> TwoQueryObligations:
    """Legacy helper for the former free-predicate compatibility path.

    The public ``UNANCHORED`` pipeline no longer calls this function; direct
    formalization uses :func:`build_direct_obligation` instead.
    """
    reach_solv = z3.Solver()
    reach_solv.set("timeout", Z3_TIMEOUT_MS)
    reach_solv.add(z3.BoolVal(True))

    valid_solv = z3.Solver()
    valid_solv.set("timeout", Z3_TIMEOUT_MS)
    predicate = _claim_to_z3(claim.expr_node)
    valid_solv.add(z3.Not(predicate))
    return TwoQueryObligations(claim, reach_solv, valid_solv)


def build_direct_context(constraints: z3.BoolRef) -> DirectContext:
    """Build the consistency query for the joint direct context.

    A separate consistency check prevents contradictory generated constraints
    from making every target look verified vacuously.
    """
    if not z3.is_bool(constraints):
        raise TypeError("direct context requires a Boolean formula")
    solver = z3.Solver()
    solver.set("timeout", Z3_TIMEOUT_MS)
    solver.add(constraints)
    return DirectContext(constraints=constraints, solver=solver)


def build_direct_obligation(
    constraints: z3.BoolRef,
    formula: z3.BoolRef,
) -> DirectObligation:
    """Build the direct baseline query ``constraints AND not target``.

    There is intentionally no reachability solver, program state, target
    location, or path relation in this protocol.  The constraint context and
    target are generated as declarative SMT-LIB2 formulas.
    """
    if not z3.is_bool(constraints):
        raise TypeError("direct obligation requires Boolean constraints")
    if not z3.is_bool(formula):
        raise TypeError("direct obligation requires a Boolean formula")
    context_solver = z3.Solver()
    context_solver.set("timeout", Z3_TIMEOUT_MS)
    context_solver.add(constraints)

    validity_solver = z3.Solver()
    validity_solver.set("timeout", Z3_TIMEOUT_MS)
    validity_solver.add(constraints, z3.Not(formula))
    return DirectObligation(
        constraints=constraints,
        formula=formula,
        context_solver=context_solver,
        validity_solver=validity_solver,
    )


def build_simple_obligation(formula: z3.BoolRef) -> SimpleObligation:
    """Build the simple baseline query ``Not(formula)``.

    No context, source state, path condition, or reachability relation is
    added.  This deliberately makes the simple baseline a validity check over
    one translated claim at a time.
    """
    if not z3.is_bool(formula):
        raise TypeError("simple obligation requires a Boolean formula")
    solver = z3.Solver()
    solver.set("timeout", Z3_TIMEOUT_MS)
    solver.add(z3.Not(formula))
    return SimpleObligation(formula=formula, validity_solver=solver)


def run_simple_validity(
    obligation: SimpleObligation,
) -> Tuple[str, Optional[float], Optional[z3.ModelRef]]:
    """Run the one-query simple-baseline validity check."""
    return run_validity(obligation)


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
    obligation,
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


def run_context(
    context,
) -> Tuple[str, Optional[float], Optional[z3.ModelRef]]:
    """Run a direct context-consistency solver."""
    t0 = time.perf_counter()
    res = context.solver.check()
    elapsed = time.perf_counter() - t0
    if res == z3.sat:
        return ("sat", elapsed, context.solver.model())
    if res == z3.unsat:
        return ("unsat", None, None)
    return ("unknown", None, None)


__all__ = [
    "TwoQueryObligations",
    "build_obligations",
    "build_anchored_direct_obligations",
    "build_unanchored_obligations",
    "DirectObligation",
    "DirectContext",
    "SimpleObligation",
    "build_direct_context",
    "build_direct_obligation",
    "build_simple_obligation",
    "run_reachability",
    "run_validity",
    "run_context",
    "run_simple_validity",
    "Z3_TIMEOUT_MS",
]
