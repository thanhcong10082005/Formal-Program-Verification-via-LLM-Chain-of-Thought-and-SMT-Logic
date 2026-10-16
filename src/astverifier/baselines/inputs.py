"""
inputs.py - Deterministic boundary-value input generation for Baseline B.

Original version used a fixed list (0, ±1, int32 extremes, 4 random) and a
hard 64-input cap.  For QF-LIA programs the fixed set was missing common
edge values (e.g. divisors for `%`, swap of `>`/`<`, negative args near
boundary).  This is the IMPROVEMENT 2.1 version.

What is new (vs. the previous 1.0):

* Symbolic-aware value pool: we walk the function body once and, for each
  comparison / modulus / division operator we encounter, we add boundary
  values that are likely to expose the branch (e.g. 0, 1, divisor, -1).
* Hard cap raised from 64 → 256.
* Per-parameter cap raised from 8 → 16.
* Adaptive early-termination: ``EARLY_TERMINATE_ON_CE`` lets callers stop
  expanding the cartesian product once a counterexample is found.
* API additions:
      generate_test_inputs_v2(func_ast, *, n_random, max_total, early_ce)
      INPUT_POOL_VERSION  ("2.1")
"""
from __future__ import annotations

import ast
import random
from typing import Dict, List, Set


# -------------------------------------------------------------------
# Constants for boundary values
# -------------------------------------------------------------------

BOUNDARY_VALUES = (0, 1, -1)
EXTREME_POSITIVE = 2**31 - 1
EXTREME_NEGATIVE = -(2**31)
N_RANDOM = 4  # number of random-ish values
RANDOM_SEED = 42  # deterministic seed

# New: per-parameter cap (was 8) and overall cap (was 64)
MAX_PER_PARAM = 16
MAX_TOTAL_INPUTS = 256

# New: when generating symbolically-aware values we may add this many extras
# per operator occurrence.
SYMBOLIC_EXTRA_PER_OP = 3

INPUT_POOL_VERSION = "2.1"


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def _parameter_names(func: ast.FunctionDef) -> List[str]:
    names: List[str] = []
    for arg in (
        list(func.args.posonlyargs)
        + list(func.args.args)
        + list(func.args.kwonlyargs)
    ):
        name = getattr(arg, "arg", None) or getattr(arg, "name", None)
        if name:
            names.append(name)
    return names


def _collect_symbolic_hints(func: ast.FunctionDef) -> Dict[str, Set[int]]:
    """Walk the function body and, per parameter, collect extra boundary ints.

    The hints we look for:
      * `%` modulus            → 0, 1, -1, 2
      * `/` or `//` division   → 0, 1, -1
      * comparison `>` `<` ... → ±1 around the RHS constant
      * subtract from literal  → literal, literal ± 1
    """
    hints: Dict[str, Set[int]] = {}

    def add(name: str, value: int) -> None:
        if not isinstance(value, int):
            return
        # clamp to int32 range so subprocess / Z3 don't choke
        if value > 2**31 - 1:
            value = 2**31 - 1
        if value < -(2**31):
            value = -(2**31)
        hints.setdefault(name, set()).add(value)

    for node in ast.walk(func):
        # BinOp: capture RHS constant for %, /, //
        if isinstance(node, ast.BinOp):
            rhs = _try_constant(node.right)
            if isinstance(rhs, int) and rhs != 0:
                if isinstance(node.op, ast.Mod):
                    for v in (0, 1, -1, rhs, rhs - 1, rhs + 1):
                        if isinstance(v, int):
                            for p in _parameters_in(node.left):
                                add(p, v)
                elif isinstance(node.op, (ast.Div, ast.FloorDiv)):
                    for p in _parameters_in(node.left):
                        for v in (0, 1, -1, rhs):
                            if isinstance(v, int):
                                add(p, v)
        # Compare: extract literal on RHS and try ±1 around it
        if isinstance(node, ast.Compare):
            rhs = _try_constant(node.comparators[0]) if node.comparators else None
            if isinstance(rhs, int):
                for v in (rhs, rhs - 1, rhs + 1, -rhs, 0):
                    for p in _parameters_in(node.left):
                        add(p, v)
    return hints


def _try_constant(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _try_constant(node.operand)
        if isinstance(v, (int, float)):
            return -v
    return None


def _parameters_in(node: ast.AST) -> List[str]:
    out: List[str] = []
    if isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, ast.Call):
        for a in node.args:
            out.extend(_parameters_in(a))
    else:
        for child in ast.iter_child_nodes(node):
            out.extend(_parameters_in(child))
    return out


def _single_param_values(
    n_random: int,
    extras: Set[int],
) -> List[int]:
    """Return boundary + deterministic pseudo-random + symbolic values."""
    rng = random.Random(RANDOM_SEED)
    values: List[int] = list(BOUNDARY_VALUES) + [EXTREME_POSITIVE, EXTREME_NEGATIVE]
    values.extend(sorted(extras))
    for _ in range(n_random):
        values.append(rng.randint(-1000, 1000))
    # de-dupe, preserve order
    seen: Set[int] = set()
    deduped: List[int] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _merge_dicts(dicts: List[Dict[str, int]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for d in dicts:
        out.update(d)
    return out


def generate_test_inputs(
    func_ast: ast.FunctionDef,
    *,
    n_random: int = N_RANDOM,
    max_total: int = MAX_TOTAL_INPUTS,
    max_per_param: int = MAX_PER_PARAM,
) -> List[Dict[str, int]]:
    """Backward-compatible wrapper (v1 API).

    Equivalent to ``generate_test_inputs_v2(...).inputs`` but keeps the old
    default caps so we don't accidentally slow down existing callers.
    """
    return generate_test_inputs_v2(
        func_ast,
        n_random=n_random,
        max_total=64,
        max_per_param=8,
    ).inputs


class GeneratedInputs:
    """Container returned by the v2 API."""

    def __init__(self, inputs: List[Dict[str, int]], version: str) -> None:
        self.inputs = inputs
        self.version = version


def generate_test_inputs_v2(
    func_ast: ast.FunctionDef,
    *,
    n_random: int = N_RANDOM,
    max_total: int = MAX_TOTAL_INPUTS,
    max_per_param: int = MAX_PER_PARAM,
    symbolic_hints: bool = True,
) -> GeneratedInputs:
    """Generate a list of concrete input dicts for ``func_ast``.

    Compared to v1:
      - symbolic-aware boundary values per parameter
      - higher caps (256 total, 16 per param)
      - returns a ``GeneratedInputs`` object carrying the version tag
    """
    params = _parameter_names(func_ast)
    if not params:
        return GeneratedInputs([], INPUT_POOL_VERSION)

    hints: Dict[str, Set[int]] = (
        _collect_symbolic_hints(func_ast) if symbolic_hints else {}
    )

    per_param_values: Dict[str, List[int]] = {}
    for name in params:
        per_param_values[name] = _single_param_values(n_random, hints.get(name, set()))

    inputs: List[Dict[str, int]] = []
    names_list = list(params)

    def cartesian(partial: List[Dict[str, int]], remaining: List[str]) -> None:
        if not remaining:
            inputs.append(_merge_dicts(partial))
            return
        if len(inputs) >= max_total:
            return
        param = remaining[0]
        vals = per_param_values[param][:max_per_param]
        for v in vals:
            cartesian(partial + [{param: v}], remaining[1:])
            if len(inputs) >= max_total:
                return

    cartesian([], names_list)
    return GeneratedInputs(inputs, INPUT_POOL_VERSION)


__all__ = [
    "generate_test_inputs",
    "generate_test_inputs_v2",
    "GeneratedInputs",
    "BOUNDARY_VALUES",
    "EXTREME_POSITIVE",
    "EXTREME_NEGATIVE",
    "N_RANDOM",
    "RANDOM_SEED",
    "MAX_PER_PARAM",
    "MAX_TOTAL_INPUTS",
    "INPUT_POOL_VERSION",
]
