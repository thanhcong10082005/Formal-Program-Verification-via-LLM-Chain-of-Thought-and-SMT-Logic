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


def bind_llm_claims(
    tree: ast.Module,
    ids: dict[ast.AST, NodeId],
    suggested_claims: list,
) -> List[Claim]:
    """Map LLM-proposed claims to AST NodeId anchors, marking errors as UNSUPPORTED/TRANSLATION_ERROR."""
    out: List[Claim] = []
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
        for node, nid in ids.items():
            if nid.lineno == line:
                anchor = nid
                break
        if anchor is None:
            preceding = [nid for nid in ids.values() if 0 < nid.lineno <= line]
            if preceding:
                preceding.sort(key=lambda x: x.lineno, reverse=True)
                anchor = preceding[0]

        status = "ANCHORED"
        if parse_err or expr_node is None:
            status = "TRANSLATION_ERROR"
        elif anchor is None:
            status = "UNSUPPORTED"

        out.append(
            Claim(
                claim_id=cid,
                source_line=line,
                expr_source=expr_str,
                expr_node=expr_node,
                anchor=anchor,
                status=status,
            )
        )
    return out


__all__ = ["Claim", "extract_asserts", "extract_anchored_ids", "bind_llm_claims"]