"""
nodes.py - Stable AST node identifier (Phase 1, Foundation).

Every statement-level AST node receives a stable NodeId derived from its
(function, lineno, col, kind, discriminator) tuple. Two runs over the same
source MUST produce the same NodeIds.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class NodeId:
    func: str
    lineno: int
    col_offset: int
    kind: str
    discriminator: int = 0

    def as_string(self) -> str:
        return f"{self.func}@{self.kind}:L{self.lineno}:C{self.col_offset}#{self.discriminator}"


# Statement-level nodes that we register with a NodeId
_STATEMENT_NODES = (
    ast.FunctionDef,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Return,
    ast.Break,
    ast.Continue,
    ast.Assert,
    ast.Pass,
    ast.Expr,
)


def assign_ids(tree: ast.Module) -> Mapping[ast.AST, NodeId]:
    """Walk the tree and assign a NodeId to every statement node."""
    result: dict[ast.AST, NodeId] = {}

    def _walk(node: ast.AST, current_func: str) -> None:
        if isinstance(node, ast.FunctionDef):
            f = node.name
            result[node] = NodeId(
                func=f,
                lineno=node.lineno,
                col_offset=node.col_offset,
                kind=type(node).__name__,
                discriminator=0,
            )
            for child in ast.iter_child_nodes(node):
                _walk(child, f)
            return

        if isinstance(node, _STATEMENT_NODES):
            # Discriminator counts same-(func,lineno,kind) nodes in document order.
            disc = sum(
                1
                for k, v in result.items()
                if v.func == current_func
                and v.lineno == getattr(node, "lineno", -1)
                and v.kind == type(node).__name__
            )
            result[node] = NodeId(
                func=current_func,
                lineno=getattr(node, "lineno", -1),
                col_offset=getattr(node, "col_offset", -1),
                kind=type(node).__name__,
                discriminator=disc,
            )

        for child in ast.iter_child_nodes(node):
            _walk(child, current_func)

    for stmt in tree.body:
        _walk(stmt, current_func="<module>")

    return result


def lookup_containing(node: ast.AST, ids: Mapping[ast.AST, NodeId]) -> NodeId | None:
    """Find the nearest enclosing statement-level node id for an arbitrary node."""
    # Walk up parents; we need parent map.
    parent: dict[ast.AST, ast.AST] = getattr(lookup_containing, "_parent", {})
    if id(node) not in parent:
        # Build/extend parent cache
        for k in list(parent.keys()):
            pass  # no-op to avoid stale state
        # Fresh build for this subtree if not cached
        new_parents: dict[ast.AST, ast.AST] = {}
        for p in ast.walk(node):
            for c in ast.iter_child_nodes(p):
                new_parents[c] = p
        parent.update(new_parents)
        lookup_containing._parent = parent  # type: ignore[attr-defined]

    cur: ast.AST | None = node
    while cur is not None:
        if cur in ids:
            return ids[cur]
        cur = parent.get(cur)
    return None


__all__ = ["NodeId", "assign_ids", "lookup_containing"]