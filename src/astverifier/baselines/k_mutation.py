"""Baseline K -- Mutation Testing làm Oracle (offline, 0 token).

K is *not* a verifier of the original program. It is an oracle for the
**claim pool**: it injects mutants into the program and asks whether the
shared claim still holds.

The insight is that a claim pool can be checked without any hand-written
ground truth. A mutant that the claim still accepts is a hole in the pool.

For each mutant:

* the mutant is executed over the deterministic input grid;
* if the mutant violates the claim while the original does not, the claim
  **caught** the mutant;
* if the mutant still satisfies the claim, the claim **missed** it.

It has no AST-anchor gate and no reachability query, so the Hallucination
Rate and the "UNREACHABLE detected" metric are ``N/A`` for it.
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass
from typing import Any

from ..shared.oracle import OBS_COUNTEREXAMPLE, OBS_NO_VIOLATION
from ..shared.oracle import observe as observe_program
from ..shared.replay import replay_counterexample
from ..shared.schemas import ClaimResult, CounterexampleInfo, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .registry import register

#: How many mutants are generated per program.
N_MUTANTS = 12

#: The seed of the deterministic input grid.
SEED = 42


@dataclass
class _Mutant:
    """One mutated copy of the program."""

    source: str
    description: str
    original_line: int


@register
class MutationTestingBaseline(BaseBaseline):
    """K -- Mutation Testing."""

    id = "K"
    name = "Mutation Testing"
    supports_grounding = False
    supports_replay = True
    supports_reachability = False
    supports_precondition = False
    requires_llm = False

    def run(self, spec: ProgramSpec) -> Any:
        """Mutate the program and check what the claim pool catches."""
        started = time.perf_counter()
        claim_results = [self._verdict_for(spec, claim) for claim in spec.claims]

        return self.roll_up(
            spec,
            self.id,
            claim_results,
            started=started,
            cot_trace=(
                "mutation oracle: the claim pool is scored by how many "
                "injected mutants it rejects"
            ),
        )

    # -- internals ------------------------------------------------------------

    def _verdict_for(self, spec: ProgramSpec, claim) -> ClaimResult:
        """Score one claim against the generated mutants."""
        try:
            mutants = generate_mutants(spec.source)
        except Exception as exc:  # noqa: BLE001 - a baseline never crashes
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason=f"mutation generation failed: {exc!r}",
            )

        if not mutants:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=self.anchor_info(claim),
                reason="no mutant could be generated for this program",
            )

        params = _parameters(spec.source)
        grid = _grid(params)

        caught = 0
        survived = 0
        witness: dict[str, int] | None = None

        for mutant in mutants:
            outcome = observe_program(
                mutant.source,
                spec.function_name,
                params,
                grid=grid,
                program_timeout=10.0,
            )
            if outcome.observation == OBS_COUNTEREXAMPLE:
                # The mutant broke the program: the claim catches it.
                caught += 1
                if witness is None and outcome.witness:
                    witness = outcome.witness
            elif outcome.observation == OBS_NO_VIOLATION:
                # The mutant still satisfies the claim: the claim missed it.
                survived += 1

        total = caught + survived
        reason = (
            f"the claim caught {caught}/{total} injected mutants "
            f"({survived} survived)"
        )

        if caught and not survived:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.VERIFIED,
                anchor=self.anchor_info(claim),
                reason=reason,
                explanation="every injected mutant was rejected by the claim",
            )

        if witness is not None and survived:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=self.anchor_info(claim),
                counterexample=self._counterexample(spec, witness),
                reason=reason,
            )

        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=self.anchor_info(claim),
            reason=reason,
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


# -- mutation generation --------------------------------------------------------

#: Integer constants are shifted by these deltas.
_CONST_DELTAS = (1, -1)

#: Comparison operators are swapped with their neighbours.
_COMPARISONS = {
    "Eq": "NotEq",
    "NotEq": "Eq",
    "Lt": "LtE",
    "LtE": "Lt",
    "Gt": "GtE",
    "GtE": "Gt",
}

#: Binary operators are swapped with their neighbours.
_BINOPS = {"Add": "Sub", "Sub": "Add", "Mult": "Add", "FloorDiv": "Mult"}


def generate_mutants(source: str, limit: int = N_MUTANTS) -> list[_Mutant]:
    """Generate up to ``limit`` mutants of ``source``.

    Three mutation operators are used, in this order:

    1. **constant shift** -- an integer constant becomes ``c + 1`` or
       ``c - 1``;
    2. **comparison swap** -- ``<`` becomes ``<=`` and so on;
    3. **binary operator swap** -- ``+`` becomes ``-`` and so on.

    The mutants are *syntactic*: they are re-parsed and re-rendered, so a
    mutant that would not parse is discarded.
    """
    tree = ast.parse(source)
    lines = source.splitlines()

    candidates: list[tuple[int, ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            for delta in _CONST_DELTAS:
                candidates.append(
                    (node.lineno, node, f"constant {node.value} -> {node.value + delta}")
                )
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            current = type(node.ops[0]).__name__
            if current in _COMPARISONS:
                candidates.append(
                    (node.lineno, node, f"comparison {current} -> {_COMPARISONS[current]}")
                )
        elif isinstance(node, ast.BinOp):
            current = type(node.op).__name__
            if current in _BINOPS:
                candidates.append(
                    (node.lineno, node, f"operator {current} -> {_BINOPS[current]}")
                )

    mutants: list[_Mutant] = []
    seen: set[str] = set()
    for lineno, node, description in candidates:
        if len(mutants) >= limit:
            break
        try:
            mutated = _apply_mutation(tree, node, lineno)
        except Exception:  # noqa: BLE001 - an invalid mutant is simply skipped
            continue
        if mutated is None or mutated in seen:
            continue
        seen.add(mutated)
        mutants.append(
            _Mutant(
                source=mutated,
                description=description,
                original_line=lineno,
            )
        )

    del lines
    return mutants


def _apply_mutation(tree: ast.Module, node: ast.AST, lineno: int) -> str | None:
    """Apply one mutation to ``tree`` and return the mutated source."""
    import copy

    mutant_tree = copy.deepcopy(tree)
    target = _find_same_line(mutant_tree, lineno, node)
    if target is None:
        return None

    if isinstance(target, ast.Constant) and isinstance(target.value, int):
        target.value = target.value + 1
    elif isinstance(target, ast.Compare) and len(target.ops) == 1:
        current = type(target.ops[0]).__name__
        replacement = _COMPARISONS.get(current)
        if replacement is None:
            return None
        target.ops = [_OP_BY_NAME[replacement]()]
    elif isinstance(target, ast.BinOp):
        current = type(target.op).__name__
        replacement = _BINOPS.get(current)
        if replacement is None:
            return None
        target.op = _OP_BY_NAME[replacement]()
    else:
        return None

    try:
        return ast.unparse(mutant_tree) + "\n"
    except Exception:  # noqa: BLE001 - an unrenderable mutant is skipped
        return None


_OP_BY_NAME = {
    "Eq": ast.Eq,
    "NotEq": ast.NotEq,
    "Lt": ast.Lt,
    "LtE": ast.LtE,
    "Gt": ast.Gt,
    "GtE": ast.GtE,
    "Add": ast.Add,
    "Sub": ast.Sub,
    "Mult": ast.Mult,
    "FloorDiv": ast.FloorDiv,
}


def _find_same_line(tree: ast.Module, lineno: int, original: ast.AST):
    """Find the node in ``tree`` that corresponds to ``original``."""
    for node in ast.walk(tree):
        if type(node) is type(original) and getattr(node, "lineno", -1) == lineno:
            return node
    return None


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


def _grid(params: list[str]) -> list[dict[str, int]]:
    """Return the deterministic input grid used to execute the mutants."""
    from ..shared.oracle import build_input_grid

    return build_input_grid(params, seed=SEED)[:64] or [{}]


__all__ = ["MutationTestingBaseline", "generate_mutants"]
