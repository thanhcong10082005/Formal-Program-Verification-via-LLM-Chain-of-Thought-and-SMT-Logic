"""Anchor Binding Gate.

Implements the grounding function ``alpha(c)``: every claim ``c`` must be
tied to a :class:`~astverifier.shared.anchors.NodeId`, i.e. to one exact
statement of the program under verification.

A line number is only an anchor when it identifies exactly one registered
statement. A claim is never attached to the nearest preceding statement,
because doing so silently changes the claim's program location.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass

from ..shared.anchors import NodeId, lookup_containing
from ..shared.subset import ALLOWED_BUILTINS, validate_expression

STATUS_ANCHORED = "ANCHORED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_TRANSLATION_ERROR = "TRANSLATION_ERROR"


@dataclass
class Claim:
    """One verification claim, before and after the anchor gate.

    Attributes:
        claim_id: Stable identifier of the claim.
        source_line: Line the claim refers to.
        expr_source: The Python boolean expression, as source text.
        expr_node: The parsed expression, or ``None`` when parsing failed.
        anchor: The NodeId the claim is bound to, or ``None``.
        status: One of ``ANCHORED``, ``UNSUPPORTED`` or ``TRANSLATION_ERROR``.
        anchor_node: The AST node the anchor refers to.
        explanation: Free-text note, used for rejected claims.
    """

    claim_id: str
    source_line: int
    expr_source: str
    expr_node: ast.expr | None
    anchor: NodeId | None
    status: str
    anchor_node: ast.AST | None = None
    explanation: str = ""


def extract_asserts(
    tree: ast.Module, ids: dict[ast.AST, NodeId]
) -> list[Claim]:
    """Find every ``assert <expr>`` in the source and attach the enclosing NodeId."""
    out: list[Claim] = []
    parent = _parent_map(tree)
    for index, node in enumerate(ast.walk(tree), start=1):
        if not isinstance(node, ast.Assert):
            continue
        anchor = lookup_containing(node, ids)
        function = _enclosing_function(node, parent)
        visible = _names_initialized_before(function, node)
        names = {
            expr.id
            for expr in ast.walk(node.test)
            if isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load)
        }
        unknown = names - visible - set(ALLOWED_BUILTINS)
        if unknown:
            status = STATUS_UNSUPPORTED
        else:
            status = STATUS_ANCHORED if anchor is not None else STATUS_UNSUPPORTED
        out.append(
            Claim(
                claim_id=f"assert_L{node.lineno}_#{index}",
                source_line=node.lineno,
                expr_source=ast.unparse(node.test),
                expr_node=node.test,
                anchor=anchor,
                status=status,
                anchor_node=node,
            )
        )
    return out


def bind_llm_claims(
    tree: ast.Module,
    ids: dict[ast.AST, NodeId],
    suggested_claims: list,
) -> list[Claim]:
    """Map LLM-proposed claims to an exact statement and validate their names.

    Args:
        tree: The parsed program.
        ids: The registered node identifiers.
        suggested_claims: Objects exposing ``line`` and ``expression``.

    Returns:
        One :class:`Claim` per suggestion, already classified.
    """
    out: list[Claim] = []
    nodes_by_line: dict[int, list[tuple[ast.AST, NodeId]]] = {}
    for node, node_id in ids.items():
        nodes_by_line.setdefault(node_id.lineno, []).append((node, node_id))

    parent = _parent_map(tree)
    for index, suggestion in enumerate(suggested_claims):
        line = getattr(suggestion, "line", 0)
        expr_str = getattr(suggestion, "expression", "")
        claim_id = f"llm_claim_{index + 1}_L{line}"

        expr_node = None
        parse_failed = False
        try:
            expr_node = ast.parse(expr_str, mode="eval").body
        except Exception:
            parse_failed = True

        anchor = None
        anchor_node: ast.AST | None = None
        candidates = nodes_by_line.get(line, []) if line > 0 else []
        if len(candidates) == 1 and not isinstance(candidates[0][0], ast.FunctionDef):
            anchor_node, anchor = candidates[0]

        if parse_failed or expr_node is None:
            status = STATUS_TRANSLATION_ERROR
        elif validate_expression(expr_node) is not None or anchor is None:
            status = STATUS_UNSUPPORTED
        else:
            function = _enclosing_function(anchor_node, parent)
            visible = _names_initialized_before(function, anchor_node)
            names = {
                node.id
                for node in ast.walk(expr_node)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            unknown = sorted(names - visible - set(ALLOWED_BUILTINS))
            status = STATUS_UNSUPPORTED if unknown else STATUS_ANCHORED

        out.append(
            Claim(
                claim_id=claim_id,
                source_line=line,
                expr_source=expr_str,
                expr_node=expr_node,
                anchor=anchor,
                status=status,
                anchor_node=anchor_node,
            )
        )
    return out


def _parent_map(tree: ast.AST) -> Mapping[ast.AST, ast.AST]:
    """Map every node to its parent."""
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _enclosing_function(
    node: ast.AST | None, parent: Mapping[ast.AST, ast.AST]
) -> ast.FunctionDef | None:
    """Return the function definition that contains ``node``."""
    current = node
    while current is not None:
        if isinstance(current, ast.FunctionDef):
            return current
        current = parent.get(current)
    return None


def _parameter_names(function: ast.FunctionDef | None) -> set[str]:
    """Return the parameter names of ``function``."""
    if function is None:
        return set()
    return {
        arg.arg
        for arg in (
            list(function.args.posonlyargs)
            + list(function.args.args)
            + list(function.args.kwonlyargs)
        )
    }


def _names_initialized_before(
    function: ast.FunctionDef | None, target: ast.AST | None
) -> set[str]:
    """Return the names definitely initialized immediately before ``target``."""
    if function is None:
        return set()
    names = _parameter_names(function)
    if target is function:
        return names
    found, visible = _definite_before_block(function.body, target, names)
    return visible if found else set()


def _definite_before_block(
    stmts: list[ast.stmt], target: ast.AST, incoming: set[str]
) -> tuple[bool, set[str]]:
    """Walk a block until ``target`` and report the names defined so far."""
    current = set(incoming)
    for stmt in stmts:
        if stmt is target:
            return True, current

        if isinstance(stmt, ast.If) and _contains_node(stmt, target):
            branch_in = set(current)
            if _contains_node(stmt.body, target):
                return _definite_before_block(stmt.body, target, branch_in)
            return _definite_before_block(stmt.orelse, target, branch_in)

        if isinstance(stmt, (ast.For, ast.While)) and _contains_node(stmt, target):
            branch_in = set(current)
            if isinstance(stmt, ast.For) and isinstance(stmt.target, ast.Name):
                branch_in.add(stmt.target.id)
            return _definite_before_block(stmt.body, target, branch_in)

        current = _definite_after_statement(stmt, current)
    return False, current


def _definite_after_statement(stmt: ast.stmt, incoming: set[str]) -> set[str]:
    """Return the names definitely defined after executing ``stmt``."""
    current = set(incoming)
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                current.add(target.id)
        return current
    if isinstance(stmt, ast.If):
        then_names = _definite_after_block(stmt.body, current)
        else_names = (
            _definite_after_block(stmt.orelse, current) if stmt.orelse else current
        )
        return then_names & else_names
    # A loop may execute zero times, so assignments in its body are not
    # definitely available after the loop.
    return current


def _definite_after_block(
    stmts: list[ast.stmt], incoming: set[str]
) -> set[str]:
    """Return the names definitely defined after executing a block."""
    current = set(incoming)
    for stmt in stmts:
        current = _definite_after_statement(stmt, current)
    return current


def _contains_node(container: ast.AST | list[ast.stmt], target: ast.AST) -> bool:
    """True iff ``target`` appears inside ``container``."""
    nodes = container if isinstance(container, list) else [container]
    return any(node is target for root in nodes for node in ast.walk(root))


__all__ = [
    "Claim",
    "STATUS_ANCHORED",
    "STATUS_TRANSLATION_ERROR",
    "STATUS_UNSUPPORTED",
    "bind_llm_claims",
    "extract_asserts",
]
