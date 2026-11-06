"""Structural analysis of the dataset's programs.

Every program in ``Dataset/filtered`` has the same shape::

    def main(<params>):
        <parameter bindings>
        <early-return guards>      # `if not <cond>: return`
        <setup assignments>
        while <test>:
            <loop body>
        <assert>                    # or `if <cond>: assert ...`

This module extracts that shape **once**, so that the prover (Phase 0.5.1)
and the precondition extractor (Phase 0.5.2) cannot disagree about what the
control flow of a program is.

The analysis is purely syntactic. It never evaluates a program and never
consults an LLM.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Statements that make an ``if`` branch terminate the current path.
_EXIT_STATEMENTS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


class StructureError(Exception):
    """Raised when a program does not have the expected shape."""


# --- Small AST helpers --------------------------------------------------------


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return the parent of every node in ``tree``."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def negate(node: ast.expr) -> ast.expr:
    """Return an expression equivalent to ``not node``.

    The negation is *simplified* rather than wrapped, so a guard written as
    ``if not a <= m: return`` yields the precondition ``a <= m`` instead of
    the unreadable ``not (not a <= m)``.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return node.operand

    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        flipped = {
            ast.Eq: ast.NotEq,
            ast.NotEq: ast.Eq,
            ast.Lt: ast.GtE,
            ast.LtE: ast.Gt,
            ast.Gt: ast.LtE,
            ast.GtE: ast.Lt,
        }[type(node.ops[0])]
        return ast.Compare(
            left=node.left,
            ops=[flipped()],
            comparators=list(node.comparators),
        )

    if isinstance(node, ast.BoolOp):
        # De Morgan.
        operands = [negate(v) for v in node.values]
        if isinstance(node.op, ast.And):
            return ast.BoolOp(op=ast.Or(), values=operands)
        return ast.BoolOp(op=ast.And(), values=operands)

    # Anything else is a value whose truthiness is tested; for integers
    # ``not x`` is ``x == 0``.
    return ast.Compare(left=node, ops=[ast.Eq()], comparators=[ast.Constant(0)])


def unparse(node: ast.AST) -> str:
    """Return the canonical source text of ``node``."""
    return ast.unparse(node)


# --- Program shape ------------------------------------------------------------


@dataclass
class Guard:
    """One early-return guard in the program's prologue."""

    node: ast.If
    """The ``if`` statement."""

    test: ast.expr
    """The guard's test expression."""

    fall_through: ast.expr
    """The condition under which execution *continues* past the guard."""

    line: int
    """The line of the ``if`` statement."""


@dataclass
class AssertSite:
    """One ``assert`` statement together with its path context."""

    node: ast.Assert
    """The ``assert`` node."""

    text: str
    """The canonical source text of the asserted expression."""

    line: int
    """The line of the ``assert`` statement."""

    in_branch: ast.If | None
    """The enclosing ``if`` whose body holds this assert, if any."""

    branch_is_else: bool
    """True when the assert sits in the ``else`` branch of :attr:`in_branch`."""

    guards: list[Guard] = field(default_factory=list)
    """The early-return guards that precede the assert."""


@dataclass
class ProgramShape:
    """The structural summary of one program."""

    func: ast.FunctionDef
    """The function under analysis."""

    params: list[str]
    """The parameter names, in order."""

    guards: list[Guard]
    """The early-return guards of the prologue, in order."""

    loop: ast.While | None
    """The single ``while`` loop, if any."""

    sites: list[AssertSite]
    """Every ``assert`` site, in source order."""


def find_function(tree: ast.Module) -> ast.FunctionDef:
    """Return the first top-level function of ``tree``."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node
    raise StructureError("no top-level function")


def is_exit_guard(node: ast.If) -> bool:
    """True iff ``node`` is an early-return guard.

    A guard is an ``if`` whose body only terminates the path (a ``return``,
    ``raise``, ``break`` or ``continue``) and whose ``else`` branch is
    absent or empty.
    """
    if not node.body:
        return False
    for stmt in node.body:
        if not isinstance(stmt, _EXIT_STATEMENTS):
            return False
    return all(isinstance(stmt, ast.Pass) for stmt in node.orelse)


def _collect_guards(func: ast.FunctionDef) -> list[Guard]:
    """Return the early-return guards, in program order."""
    guards: list[Guard] = []
    for stmt in func.body:
        if isinstance(stmt, ast.If) and is_exit_guard(stmt):
            guards.append(
                Guard(
                    node=stmt,
                    test=stmt.test,
                    fall_through=negate(stmt.test),
                    line=stmt.lineno,
                )
            )
    return guards


def analyze(source: str) -> ProgramShape:
    """Analyze one program and return its structural summary.

    Raises:
        StructureError: The source does not parse or has no top-level
            function.
        SyntaxError: The source does not parse.
    """
    tree = ast.parse(source)
    func = find_function(tree)
    parents = parent_map(func)
    guards = _collect_guards(func)

    loops = [n for n in ast.walk(func) if isinstance(n, ast.While)]
    if len(loops) > 1:
        raise StructureError(f"{len(loops)} loops; the dataset has at most one")

    sites: list[AssertSite] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert):
            continue
        branch: ast.If | None = None
        is_else = False
        current = parents.get(node)
        while current is not None and current is not func:
            if isinstance(current, ast.If):
                branch = current
                is_else = node in current.orelse
                break
            current = parents.get(current)

        # The guards that precede this assert: every guard of the prologue
        # that is textually before the assert.
        line = node.lineno
        preceding = [g for g in guards if g.line < line]

        sites.append(
            AssertSite(
                node=node,
                text=unparse(node.test),
                line=node.lineno,
                in_branch=branch,
                branch_is_else=is_else,
                guards=preceding,
            )
        )

    sites.sort(key=lambda s: s.line)
    return ProgramShape(
        func=func,
        params=[a.arg for a in func.args.args],
        guards=guards,
        loop=loops[0] if loops else None,
        sites=sites,
    )


def site_precondition(site: AssertSite) -> str | None:
    """Return the path condition under which ``site`` is reached.

    The precondition is the conjunction of

    * every early-return guard that precedes the assert (the execution must
      continue past the guard), and
    * the branch condition of the ``if`` that contains the assert, if any.

    It is ``None`` when the assert is reached unconditionally.
    """
    parts: list[ast.expr] = [g.fall_through for g in site.guards]
    if site.in_branch is not None:
        if site.branch_is_else:
            parts.append(negate(site.in_branch.test))
        else:
            parts.append(site.in_branch.test)

    if not parts:
        return None
    if len(parts) == 1:
        return unparse(parts[0])
    return " and ".join(unparse(p) for p in parts)


__all__ = [
    "AssertSite",
    "Guard",
    "ProgramShape",
    "StructureError",
    "analyze",
    "find_function",
    "is_exit_guard",
    "negate",
    "parent_map",
    "site_precondition",
    "unparse",
]
