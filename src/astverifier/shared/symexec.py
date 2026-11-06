"""Symbolic execution over the QF-LIA subset.

This module is the *trusted* symbolic core of the dataset ground truth. It
never reads anything produced by an LLM: it evaluates Python expressions over
Z3 symbols and enumerates the control-flow paths of a program.

The subset it supports is exactly the one the dataset uses:

* straight-line assignments and augmented assignments,
* ``if`` / ``elif`` / ``else`` branching,
* early ``return`` guards (``if not <cond>: return``),
* a single ``while`` loop per function.

The loop is *not* unrolled. Instead the explorer reports the loop head state,
and :mod:`astverifier.shared.prover` hands that to Z3's Spacer engine, which
synthesizes an exact loop invariant.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

import z3

JUNK_PREFIX = "junk_"


class SymExecError(Exception):
    """Raised when the program leaves the supported subset."""


# --- Expression evaluation ---------------------------------------------------


def coerce_bool(value: z3.ExprRef) -> z3.BoolRef:
    """Coerce a value to a Z3 boolean the way Python's truthiness does."""
    if z3.is_bool(value):
        return value
    return value != 0


def eval_expr(node: ast.expr, env: dict[str, z3.ExprRef]) -> z3.ExprRef:
    """Evaluate ``node`` symbolically against ``env``.

    Raises:
        SymExecError: The expression uses a construct outside the subset.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return z3.BoolVal(value)
        if isinstance(value, int):
            return z3.IntVal(value)
        if value is None:
            return z3.IntVal(0)
        raise SymExecError(f"constant {type(value).__name__} not supported")

    if isinstance(node, ast.Name):
        if node.id not in env:
            raise SymExecError(f"variable {node.id!r} is not in scope")
        return env[node.id]

    if isinstance(node, ast.UnaryOp):
        inner = eval_expr(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -inner
        if isinstance(node.op, ast.UAdd):
            return inner
        if isinstance(node.op, ast.Not):
            return z3.Not(coerce_bool(inner))
        raise SymExecError(f"unary operator {type(node.op).__name__} not supported")

    if isinstance(node, ast.BinOp):
        left = eval_expr(node.left, env)
        right = eval_expr(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        raise SymExecError(f"binary operator {type(node.op).__name__} not supported")

    if isinstance(node, ast.BoolOp):
        values = [coerce_bool(eval_expr(v, env)) for v in node.values]
        if isinstance(node.op, ast.And):
            return z3.And(*values)
        if isinstance(node.op, ast.Or):
            return z3.Or(*values)
        raise SymExecError(f"boolean operator {type(node.op).__name__} not supported")

    if isinstance(node, ast.Compare):
        left = eval_expr(node.left, env)
        parts: list[z3.BoolRef] = []
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = eval_expr(comparator, env)
            if isinstance(op, ast.Eq):
                parts.append(left == right)
            elif isinstance(op, ast.NotEq):
                parts.append(left != right)
            elif isinstance(op, ast.Lt):
                parts.append(left < right)
            elif isinstance(op, ast.LtE):
                parts.append(left <= right)
            elif isinstance(op, ast.Gt):
                parts.append(left > right)
            elif isinstance(op, ast.GtE):
                parts.append(left >= right)
            else:
                raise SymExecError(f"comparison {type(op).__name__} not supported")
            left = right
        return z3.And(*parts)

    if isinstance(node, ast.IfExp):
        cond = coerce_bool(eval_expr(node.test, env))
        return z3.If(cond, eval_expr(node.body, env), eval_expr(node.orelse, env))

    if isinstance(node, ast.Call):
        return _eval_call(node, env)

    raise SymExecError(f"expression {type(node).__name__} not supported")


def _eval_call(node: ast.Call, env: dict[str, z3.ExprRef]) -> z3.ExprRef:
    if not isinstance(node.func, ast.Name):
        raise SymExecError("only bare-name calls are supported")
    name = node.func.id
    args = [eval_expr(a, env) for a in node.args]
    if name == "abs" and len(args) == 1:
        return z3.If(args[0] >= 0, args[0], -args[0])
    if name == "min" and len(args) == 2:
        return z3.If(args[0] <= args[1], args[0], args[1])
    if name == "max" and len(args) == 2:
        return z3.If(args[0] >= args[1], args[0], args[1])
    raise SymExecError(f"call to {name!r} is not supported")


# --- Paths --------------------------------------------------------------------


@dataclass
class SymPath:
    """One control-flow path with a symbolic state."""

    cond: z3.BoolRef
    env: dict[str, z3.ExprRef] = field(default_factory=dict)


@dataclass
class AssertSite:
    """An ``assert`` statement together with the paths that reach it."""

    node: ast.Assert
    cond: z3.BoolRef
    env: dict[str, z3.ExprRef]


@dataclass
class LoopSpec:
    """The single loop of a function, described symbolically."""

    node: ast.While
    guard: z3.BoolRef
    head_paths: list[SymPath]
    head_vars: list[str]
    body_paths: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]]
    """``(condition, post-state)`` pairs -- one per branch of the loop body."""

    body_var_updates: dict[str, z3.ExprRef] = field(default_factory=dict)


@dataclass
class Exploration:
    """The result of exploring one function."""

    assert_sites: list[AssertSite]
    loop: LoopSpec | None
    param_names: list[str]
    params: dict[str, z3.ExprRef]
    terminated_paths: list[SymPath]
    """Paths that returned before reaching any assert."""


# --- Explorer -----------------------------------------------------------------


class Explorer:
    """Explores one function, producing symbolic paths and assert sites."""

    def __init__(self, param_names: list[str], var_prefix: str = "v") -> None:
        self.param_names = list(param_names)
        self.params = {
            name: z3.Int(f"{var_prefix}_{name}") for name in self.param_names
        }
        self._counter = 0

    # -- helpers ------------------------------------------------------------
    def _fresh(self, tag: str) -> str:
        self._counter += 1
        return f"{tag}_{self._counter}"

    @staticmethod
    def _strip_junk(env: dict[str, z3.ExprRef]) -> dict[str, z3.ExprRef]:
        return {k: v for k, v in env.items() if not k.startswith(JUNK_PREFIX)}

    # -- public -------------------------------------------------------------
    def explore(self, body: list[ast.stmt]) -> Exploration:
        """Explore the function body."""
        paths = [SymPath(z3.BoolVal(True), dict(self.params))]
        sites: list[AssertSite] = []
        terminated: list[SymPath] = []
        loop: LoopSpec | None = None

        for stmt in body:
            if isinstance(stmt, ast.While):
                if loop is not None:
                    raise SymExecError("more than one loop is not supported")
                loop = self._handle_loop(stmt, paths)
                paths = loop.exit_paths
                continue
            if isinstance(stmt, ast.For):
                raise SymExecError("for-loops are not supported by the prover")
            paths, terminated_here, sites_here = self._exec_stmt(stmt, paths)
            terminated.extend(terminated_here)
            sites.extend(sites_here)

        return Exploration(
            assert_sites=sites,
            loop=loop,
            param_names=list(self.param_names),
            params=dict(self.params),
            terminated_paths=terminated,
        )

    # -- loop ---------------------------------------------------------------
    def _handle_loop(self, node: ast.While, paths: list[SymPath]) -> LoopSpec:
        """Analyse the loop and produce the paths that continue past it."""
        head_paths = list(paths)
        if not head_paths:
            raise SymExecError("loop is not reachable")

        env0 = head_paths[0].env
        guard = eval_expr(node.test, env0)

        # The variables the loop can change, plus everything it reads.
        head_vars = set(env0)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                head_vars.add(sub.id)
            if isinstance(sub, (ast.Assign, ast.AugAssign)):
                targets = (
                    sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        head_vars.add(target.id)
        head_vars = sorted(v for v in head_vars if not v.startswith(JUNK_PREFIX))

        # Symbolic body branches, relative to the loop head state.
        body_paths: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]] = []
        body_paths, _, _ = self._exec_block(
            node.body, [SymPath(z3.BoolVal(True), dict(env0))]
        )
        for path in body_paths:
            body_paths_list = [(path.cond, dict(path.env))]
            body_paths = body_paths_list + body_paths

        # The post-loop state: fresh symbols for everything the loop owns.
        exit_vars = {v: z3.Int(self._fresh(f"exit_{v}")) for v in head_vars}
        exit_guard = eval_expr(node.test, {**env0, **exit_vars})

        exit_paths: list[SymPath] = []
        for path in head_paths:
            env = {**path.env, **exit_vars}
            exit_paths.append(
                SymPath(z3.And(path.cond, z3.Not(exit_guard)), env)
            )

        spec = LoopSpec(
            node=node,
            guard=guard,
            head_paths=head_paths,
            head_vars=head_vars,
            body_paths=[
                (cond, {k: v for k, v in env.items() if k not in self.params})
                for cond, env in body_paths
            ],
        )
        # Attach the exit machinery through a private attribute-like field.
        spec.exit_paths = exit_paths  # type: ignore[attr-defined]
        spec.exit_vars = exit_vars  # type: ignore[attr-defined]
        spec.exit_guard = exit_guard  # type: ignore[attr-defined]
        return spec

    # -- statements ---------------------------------------------------------
    def _exec_block(
        self,
        stmts: list[ast.stmt],
        paths: list[SymPath],
    ) -> tuple[list[SymPath], list[SymPath], list[AssertSite]]:
        current = paths
        terminated: list[SymPath] = []
        sites: list[AssertSite] = []
        for stmt in stmts:
            current, terminated_here, sites_here = self._exec_stmt(stmt, current)
            terminated.extend(terminated_here)
            sites.extend(sites_here)
        return current, terminated, sites

    def _exec_stmt(
        self,
        stmt: ast.stmt,
        paths: list[SymPath],
    ) -> tuple[list[SymPath], list[SymPath], list[AssertSite]]:
        """Execute one statement symbolically.

        Returns ``(fall-through paths, terminated paths, assert sites)``.
        """
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                raise SymExecError("multi-target assignment is not supported")
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                raise SymExecError("only simple name targets are supported")
            value = eval_expr(stmt.value, paths[0].env) if paths else None
            out: list[SymPath] = []
            for path in paths:
                env = dict(path.env)
                env[target.id] = eval_expr(stmt.value, env)
                out.append(SymPath(path.cond, env))
            del value
            return out, [], []

        if isinstance(stmt, ast.AugAssign):
            target = stmt.target
            if not isinstance(target, ast.Name):
                raise SymExecError("only simple name targets are supported")
            out = []
            for path in paths:
                env = dict(path.env)
                current = env[target.id]
                delta = eval_expr(stmt.value, env)
                if isinstance(stmt.op, ast.Add):
                    env[target.id] = current + delta
                elif isinstance(stmt.op, ast.Sub):
                    env[target.id] = current - delta
                elif isinstance(stmt.op, ast.Mult):
                    env[target.id] = current * delta
                elif isinstance(stmt.op, ast.FloorDiv):
                    env[target.id] = current / delta
                elif isinstance(stmt.op, ast.Mod):
                    env[target.id] = current % delta
                else:
                    raise SymExecError(
                        f"augmented operator {type(stmt.op).__name__} not supported"
                    )
                out.append(SymPath(path.cond, env))
            return out, [], []

        if isinstance(stmt, ast.If):
            out: list[SymPath] = []
            terminated: list[SymPath] = []
            sites: list[AssertSite] = []
            for path in paths:
                cond = eval_expr(stmt.test, path.env)
                then_paths = [SymPath(z3.And(path.cond, cond), dict(path.env))]
                else_paths = [SymPath(z3.And(path.cond, z3.Not(cond)), dict(path.env))]
                t_out, t_term, t_sites = self._exec_block(stmt.body, then_paths)
                e_out, e_term, e_sites = self._exec_block(stmt.orelse, else_paths)
                out.extend(t_out)
                out.extend(e_out)
                terminated.extend(t_term)
                terminated.extend(e_term)
                sites.extend(t_sites)
                sites.extend(e_sites)
            return out, terminated, sites

        if isinstance(stmt, ast.Assert):
            sites = [
                AssertSite(node=stmt, cond=path.cond, env=dict(path.env))
                for path in paths
            ]
            return paths, [], sites

        if isinstance(stmt, ast.Return):
            return [], list(paths), []

        if isinstance(stmt, (ast.Pass, ast.Expr)):
            return paths, [], []

        raise SymExecError(f"statement {type(stmt).__name__} is not supported")


__all__ = [
    "AssertSite",
    "Exploration",
    "Explorer",
    "LoopSpec",
    "SymExecError",
    "SymPath",
    "coerce_bool",
    "eval_expr",
]
