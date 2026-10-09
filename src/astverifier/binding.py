"""
binding.py - Anchor Binding Gate (Phase 2).

Implements alpha(c) : every claim c must be tied to a NodeId.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional

from .nodes import NodeId, lookup_containing
from .subset import ALLOWED_BUILTINS, validate_expression


@dataclass
class Claim:
    claim_id: str
    source_line: int
    expr_source: str
    expr_node: Optional[ast.expr]
    anchor: Optional[NodeId]
    status: str  # "ANCHORED", "UNSUPPORTED", or "TRANSLATION_ERROR"
    anchor_node: Optional[ast.AST] = None


def extract_asserts(
    tree: ast.Module, ids: dict[ast.AST, NodeId]
) -> List[Claim]:
    """Find every `assert <expr>` in the source, attach the enclosing NodeId."""
    out: List[Claim] = []
    parent = _parent_map(tree)
    for index, node in enumerate(ast.walk(tree), start=1):
        if isinstance(node, ast.Assert):
            anchor = lookup_containing(node, ids)
            cid = f"assert_L{node.lineno}_#{index}"
            function = _enclosing_function(node, parent)
            visible = _names_initialized_before(function, node)
            names = {
                expr.id
                for expr in ast.walk(node.test)
                if isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load)
            }
            unknown = names - visible - set(ALLOWED_BUILTINS)
            if unknown:
                status = "UNSUPPORTED"
            else:
                status = "ANCHORED" if anchor is not None else "UNSUPPORTED"
            out.append(
                Claim(
                    claim_id=cid,
                    source_line=node.lineno,
                    expr_source=ast.unparse(node.test),
                    expr_node=node.test,
                    anchor=anchor,
                    status=status,
                    anchor_node=node,
                )
            )
    return out


def claims_without_anchors(claims: Iterable[Claim]) -> List[Claim]:
    """Copy claims for the unanchored baseline without retaining AST anchors."""
    return [
        Claim(
            claim_id=claim.claim_id,
            source_line=claim.source_line,
            expr_source=claim.expr_source,
            expr_node=claim.expr_node,
            anchor=None,
            status=claim.status,
            anchor_node=None,
        )
        for claim in claims
    ]


def parse_unanchored_asserts(claim_infos: Iterable) -> List[Claim]:
    """Convert subset-gate assert inputs into free predicates.

    This intentionally does not run anchor lookup or definite-initialization
    checks.  The subset gate has already parsed the expressions; the
    unanchored baseline discards their source locations before solving.
    """
    return [
        Claim(
            claim_id=info.claim_id,
            source_line=info.source_line,
            expr_source=info.expr_source,
            expr_node=info.expr_node,
            anchor=None,
            status="UNANCHORED",
        )
        for info in claim_infos
    ]


def extract_anchored_ids(claims: List[Claim]) -> List[NodeId]:
    return [c.anchor for c in claims if c.anchor is not None]


def bind_llm_claims(
    tree: ast.Module,
    ids: dict[ast.AST, NodeId],
    suggested_claims: list,
) -> List[Claim]:
    """Map LLM claims to an exact statement and validate their names.

    A line number is only an anchor when it identifies exactly one registered
    statement.  In particular, a claim is never attached to the nearest
    preceding statement: doing so changes the claim's program location.
    """
    out: List[Claim] = []
    nodes_by_line: dict[int, list[tuple[ast.AST, NodeId]]] = {}
    for node, nid in ids.items():
        nodes_by_line.setdefault(nid.lineno, []).append((node, nid))

    parent = _parent_map(tree)
    for idx, sc in enumerate(suggested_claims):
        line = getattr(sc, "line", 0)
        expr_str = getattr(sc, "expression", "")
        cid = f"llm_claim_{idx + 1}_L{line}"

        expr_node = None
        parse_err = False
        try:
            parsed = ast.parse(expr_str, mode="eval")
            expr_node = parsed.body
        except Exception:
            parse_err = True

        anchor = None
        anchor_node: Optional[ast.AST] = None
        candidates = nodes_by_line.get(line, []) if line > 0 else []
        if len(candidates) == 1 and not isinstance(candidates[0][0], ast.FunctionDef):
            anchor_node, anchor = candidates[0]

        status = "ANCHORED"
        if parse_err or expr_node is None:
            status = "TRANSLATION_ERROR"
        elif validate_expression(expr_node) is not None:
            status = "UNSUPPORTED"
        elif anchor is None:
            status = "UNSUPPORTED"
        else:
            function = _enclosing_function(anchor_node, parent)
            visible = _names_initialized_before(function, anchor_node)
            names = {
                node.id
                for node in ast.walk(expr_node)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            unknown = sorted(names - visible - set(ALLOWED_BUILTINS))
            if unknown:
                status = "UNSUPPORTED"

        out.append(
            Claim(
                claim_id=cid,
                source_line=line,
                expr_source=expr_str,
                expr_node=expr_node,
                anchor=anchor,
                status=status,
                anchor_node=anchor_node,
            )
        )
    return out


def parse_unanchored_claims(suggested_claims: Iterable) -> List[Claim]:
    """Parse claim predicates without consulting a program AST or location."""
    out: List[Claim] = []
    for idx, sc in enumerate(suggested_claims):
        line = getattr(sc, "line", 0)
        expr_str = getattr(sc, "expression", "")
        expr_node: Optional[ast.expr] = None
        status = "UNANCHORED"
        try:
            expr_node = ast.parse(expr_str, mode="eval").body
        except Exception:
            status = "TRANSLATION_ERROR"
        else:
            if validate_expression(expr_node) is not None:
                status = "UNSUPPORTED"
        out.append(
            Claim(
                claim_id=f"llm_claim_{idx + 1}_L{line}",
                source_line=line,
                expr_source=expr_str,
                expr_node=expr_node,
                anchor=None,
                status=status,
            )
        )
    return out


def _parent_map(tree: ast.AST) -> Mapping[ast.AST, ast.AST]:
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _enclosing_function(
    node: Optional[ast.AST], parent: Mapping[ast.AST, ast.AST]
) -> Optional[ast.FunctionDef]:
    current = node
    while current is not None:
        if isinstance(current, ast.FunctionDef):
            return current
        current = parent.get(current)
    return None


def _parameter_names(function: Optional[ast.FunctionDef]) -> set[str]:
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
    function: Optional[ast.FunctionDef], target: Optional[ast.AST]
) -> set[str]:
    """Return names definitely initialized immediately before ``target``."""
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
    current = set(incoming)
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                current.add(target.id)
        return current
    if isinstance(stmt, ast.If):
        then_names = _definite_after_block(stmt.body, current)
        else_names = _definite_after_block(stmt.orelse, current) if stmt.orelse else current
        return then_names & else_names
    # A loop may execute zero times, so assignments in its body are not
    # definitely available after the loop.
    return current


def _definite_after_block(stmts: list[ast.stmt], incoming: set[str]) -> set[str]:
    current = set(incoming)
    for stmt in stmts:
        current = _definite_after_statement(stmt, current)
    return current


def _contains_node(container: ast.AST | list[ast.stmt], target: ast.AST) -> bool:
    nodes = container if isinstance(container, list) else [container]
    return any(node is target for root in nodes for node in ast.walk(root))


__all__ = [
    "Claim",
    "extract_asserts",
    "extract_anchored_ids",
    "claims_without_anchors",
    "parse_unanchored_asserts",
    "bind_llm_claims",
    "parse_unanchored_claims",
]
