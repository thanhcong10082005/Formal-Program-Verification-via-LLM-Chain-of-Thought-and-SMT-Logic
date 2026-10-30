"""Stable AST node identifiers.

Every statement-level AST node receives a stable :class:`NodeId` derived from
its ``(function, lineno, col_offset, kind, discriminator)`` tuple. Two runs
over the same source MUST produce the same NodeIds, so they can be used as
stable keys in reports and in the shared claim pool.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass

# The statement-level nodes that receive a NodeId.
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


@dataclass(frozen=True)
class NodeId:
    """A stable identifier for one statement-level AST node."""

    func: str
    lineno: int
    col_offset: int
    kind: str
    discriminator: int = 0

    def as_string(self) -> str:
        """Return the canonical text form of this identifier."""
        return (
            f"{self.func}@{self.kind}:L{self.lineno}:C{self.col_offset}"
            f"#{self.discriminator}"
        )


def assign_ids(tree: ast.Module) -> Mapping[ast.AST, NodeId]:
    """Walk the tree and assign a :class:`NodeId` to every statement node."""
    result: dict[ast.AST, NodeId] = {}

    def _walk(node: ast.AST, current_func: str) -> None:
        if isinstance(node, ast.FunctionDef):
            result[node] = NodeId(
                func=node.name,
                lineno=node.lineno,
                col_offset=node.col_offset,
                kind=type(node).__name__,
                discriminator=0,
            )
            for child in ast.iter_child_nodes(node):
                _walk(child, node.name)
            return

        if isinstance(node, _STATEMENT_NODES):
            # The discriminator counts same-(func, lineno, kind) nodes in
            # document order.
            discriminator = sum(
                1
                for value in result.values()
                if value.func == current_func
                and value.lineno == getattr(node, "lineno", -1)
                and value.kind == type(node).__name__
            )
            result[node] = NodeId(
                func=current_func,
                lineno=getattr(node, "lineno", -1),
                col_offset=getattr(node, "col_offset", -1),
                kind=type(node).__name__,
                discriminator=discriminator,
            )

        for child in ast.iter_child_nodes(node):
            _walk(child, current_func)

    for stmt in tree.body:
        _walk(stmt, current_func="<module>")

    return result


def lookup_containing(
    node: ast.AST, ids: Mapping[ast.AST, NodeId]
) -> NodeId | None:
    """Find the nearest enclosing statement-level node id for ``node``.

    The parent map is rebuilt on every call from the registered statements,
    so the result never depends on a previous call. Every intermediate node
    between ``node`` and its enclosing statement is a descendant of that
    statement, which is why walking the registered statements is sufficient.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for statement in ids:
        for parent in ast.walk(statement):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

    current: ast.AST | None = node
    while current is not None:
        if current in ids:
            return ids[current]
        current = parents.get(current)
    return None


__all__ = ["NodeId", "assign_ids", "lookup_containing"]
