"""
binding.py - Anchor Binding Gate (Phase 2).

Implements alpha(c) : every claim c must be tied to a NodeId.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import List, Optional

from .nodes import NodeId, lookup_containing


@dataclass
class Claim:
    claim_id: str
    source_line: int
    expr_source: str
    expr_node: ast.expr
    anchor: Optional[NodeId]
    status: str  # "ANCHORED" or "UNSUPPORTED"


def extract_asserts(
    tree: ast.Module, ids: dict[ast.AST, NodeId]
) -> List[Claim]:
    """Find every `assert <expr>` in the source, attach the enclosing NodeId."""
    out: List[Claim] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            anchor = lookup_containing(node, ids)
            cid = f"assert_L{node.lineno}"
            out.append(
                Claim(
                    claim_id=cid,
                    source_line=node.lineno,
                    expr_source=ast.unparse(node.test),
                    expr_node=node.test,
                    anchor=anchor,
                    status="ANCHORED" if anchor is not None else "UNSUPPORTED",
                )
            )
    return out


def extract_anchored_ids(claims: List[Claim]) -> List[NodeId]:
    return [c.anchor for c in claims if c.anchor is not None]


__all__ = ["Claim", "extract_asserts", "extract_anchored_ids"]