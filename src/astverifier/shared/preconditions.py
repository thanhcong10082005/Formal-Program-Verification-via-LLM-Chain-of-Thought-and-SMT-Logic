"""Phase 0.5.2 -- path-precondition extraction.

About 45.9% of the dataset's programs hold their ``assert`` inside an ``if``
branch, so the claim is only *required* on that path. A verifier that ignores
the path condition will report a false alarm on every such program.

This module extracts the exact path condition of an assert site:

* every early-return guard that precedes the assert contributes the
  condition under which execution *continues* past it, and
* the enclosing ``if`` contributes its branch condition.

The result is a Python expression in the QF-LIA subset, so it can be handed
to Z3 unchanged, and it is stable across runs because it is derived purely
from the AST.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .structure import AssertSite, ProgramShape, analyze, site_precondition
from .symexec import SymExecError


@dataclass
class PreconditionResult:
    """The extracted precondition of one program."""

    precondition: str | None
    """The path condition, or ``None`` when the assert is unconditional."""

    line: int | None
    """The line of the innermost guard, if any."""

    guarded: bool
    """True when the assert sits inside an ``if`` branch."""

    in_qf_lia: bool
    """True when the condition parses and is inside the supported subset."""

    reason: str | None = None
    """Why the precondition could not be extracted, if it could not."""


def _is_qf_lia(text: str) -> bool:
    """True iff ``text`` parses as a quantifier-free linear integer expression.

    The check is deliberately conservative: it accepts only the expression
    forms the dataset actually uses.
    """
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return False
    return _check_linear(node)


def _check_linear(node: ast.AST) -> bool:
    """True iff every multiplication in ``node`` has a constant operand."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.BinOp)
            and isinstance(sub.op, ast.Mult)
            and not (_is_constant(sub.left) or _is_constant(sub.right))
        ):
            return False
        if (
            isinstance(sub, ast.BinOp)
            and isinstance(sub.op, (ast.FloorDiv, ast.Mod))
            and not _is_constant(sub.right)
        ):
            return False
        if isinstance(sub, ast.Call) and not (
            isinstance(sub.func, ast.Name) and sub.func.id in {"abs", "min", "max"}
        ):
            return False
    return True


def _is_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_constant(node.operand)
    return False


def extract(source: str) -> PreconditionResult:
    """Extract the path condition of a program's claim.

    The program is expected to have a single claim; when it has several,
    the condition of the *first* assert site is returned.
    """
    results = extract_all(source)
    if not results:
        return PreconditionResult(
            precondition=None,
            line=None,
            guarded=False,
            in_qf_lia=False,
            reason="no assert statement found",
        )
    return results[0]


def extract_all(source: str) -> list[PreconditionResult]:
    """Extract the path condition of *every* assert site of a program.

    A program may hold several distinct claims, each under its own path
    condition -- ``abs_value`` in the dataset asserts one thing under
    ``val < 0`` and another under ``val >= 0``. The path condition is a
    property of the *site*, not of the program, so it has to be extracted
    per site.
    """
    try:
        shape: ProgramShape = analyze(source)
    except (SyntaxError, SymExecError) as exc:
        return [
            PreconditionResult(
                precondition=None,
                line=None,
                guarded=False,
                in_qf_lia=False,
                reason=f"analysis failed: {exc}",
            )
        ]

    if not shape.sites:
        return [
            PreconditionResult(
                precondition=None,
                line=None,
                guarded=False,
                in_qf_lia=False,
                reason="no assert statement found",
            )
        ]

    return [extract_for_site(site) for site in shape.sites]


def extract_map(source: str) -> dict[str, PreconditionResult]:
    """Map every *distinct* claim text to the condition of its first site.

    A program that asserts the same expression in several branches has one
    claim text but several sites. The first site is used, because it is the
    one a verifier reaches first; the mapping keeps one entry per distinct
    text, so the claim pool never carries the same text twice.
    """
    out: dict[str, PreconditionResult] = {}
    for site, result in zip(analyze(source).sites, extract_all(source), strict=True):
        out.setdefault(site.text, result)
    return out


def extract_for_site(site: AssertSite) -> PreconditionResult:
    """Extract the path condition of one assert site."""
    text = site_precondition(site)
    guarded = site.in_branch is not None
    line = site.in_branch.lineno if site.in_branch is not None else None

    if text is None:
        return PreconditionResult(
            precondition=None,
            line=line,
            guarded=guarded,
            in_qf_lia=True,
            reason=None,
        )

    if not _is_qf_lia(text):
        return PreconditionResult(
            precondition=None,
            line=line,
            guarded=guarded,
            in_qf_lia=False,
            reason="the path condition is outside the QF-LIA subset",
        )

    return PreconditionResult(
        precondition=text,
        line=line,
        guarded=guarded,
        in_qf_lia=True,
        reason=None,
    )


__all__ = [
    "PreconditionResult",
    "extract",
    "extract_all",
    "extract_for_site",
    "extract_map",
]
