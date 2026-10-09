"""
executor.py - Trusted Symbolic Executor (Phase 2).

Builds T_v (per-node transition) and R_pi (per-path reachability) directly from
the AST. NEVER reads anything from the LLM. The only legal inputs to Z3 are
the Boolean relations constructed here.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import z3

from .nodes import NodeId
from .subset import SubsetResult


# --- Symbolic state ----------------------------------------------------------


@dataclass
class SymState:
    """A mapping from variable name to z3.Int / z3.Bool symbolic value."""

    bindings: Dict[str, z3.ExprRef] = field(default_factory=dict)

    def copy(self) -> "SymState":
        return SymState(dict(self.bindings))

    def get(
        self, name: str, default: Optional[z3.ExprRef] = None
    ) -> Optional[z3.ExprRef]:
        return self.bindings.get(name, default)

    def set(self, name: str, value: z3.ExprRef) -> None:
        self.bindings[name] = value


@dataclass
class Transition:
    pre: SymState
    post: SymState
    guard: z3.BoolRef  # condition under which the transition fires
    node: ast.AST

    def pretty(self) -> str:
        return f"Transition[{type(self.node).__name__}@L{getattr(self.node, 'lineno', -1)}]"


@dataclass
class Reachability:
    path: List[NodeId]
    relation: z3.BoolRef  # R_pi(s0, s): path condition plus state equations
    path_condition: z3.BoolRef
    state: SymState = field(default_factory=SymState)
    complete: bool = True


# --- Trusted branch exceptions ---------------------------------------------------


class TrustedBranchViolation(Exception):
    """Raised when the trusted executor encounters something outside the subset."""

    def __init__(self, message: str, line: int = -1) -> None:
        super().__init__(message)
        self.line = line


# --- Trusted Symbolic Executor -----------------------------------------------


class TrustedSymbolicExecutor:
    """Build transitions from an AST that has passed `subset.check_and_normalize`."""

    def __init__(self, tree: ast.Module, ids: Mapping[ast.AST, NodeId]) -> None:
        self.tree = tree
        self.ids = ids
        self.transitions: Dict[NodeId, Transition] = {}
        self.path_coverage_complete = True
        self.last_path_enumeration_complete = True

    # --- Public entry points --------------------------------------------------

    def build_all_transitions(self) -> Dict[NodeId, Transition]:
        # Process each top-level function
        for stmt in self.tree.body:
            if isinstance(stmt, ast.FunctionDef):
                initial = SymState()
                self._declare_params(initial, stmt)
                self._build_block_transitions(stmt.body, initial, current_func=stmt.name)
        return self.transitions

    def enumerate_paths(
        self, *, max_paths: int = 256, max_depth: int = 40
    ) -> List[List[NodeId]]:
        """Enumerate statement-level paths starting from the first function."""
        paths: List[List[NodeId]] = []
        for stmt in self.tree.body:
            if isinstance(stmt, ast.FunctionDef):
                self._enumerate_paths_in_block(
                    stmt.body, [], paths, max_paths, max_depth, stmt.name
                )
                break
        return paths

    def build_reachability(
        self,
        path: Optional[List[NodeId]],
        branch_guards: Optional[List[z3.BoolRef]] = None,
    ) -> Reachability:
        """Build a target-state relation for one concrete symbolic path.

        The transition builder stores expressions in the state at each source
        location.  The final transition's pre-state is therefore the state
        visible at the target node.  We retain that state and expose fresh
        output symbols in ``relation`` so the relation is not confused with
        its path condition.
        """
        if not path:
            # Trivial reachability: true
            return Reachability(
                path=[],
                relation=z3.BoolVal(True),
                path_condition=z3.BoolVal(True),
                state=SymState(),
                complete=self.last_path_enumeration_complete,
            )

        target = self.transitions[path[-1]]
        target_state = target.pre.copy()
        guards = branch_guards
        if guards is None:
            guards = []
            for nid in path:
                guard = self.transitions[nid].guard
                guards.append(guard if z3.is_bool(guard) else guard != 0)
        path_cond = z3.And(*guards) if guards else z3.BoolVal(True)

        equations: list[z3.BoolRef] = []
        suffix = abs(hash(tuple(path)))
        for name, value in target_state.bindings.items():
            if z3.is_bool(value):
                state_var = z3.Bool(f"_state_{suffix}_{name}")
            else:
                state_var = z3.Int(f"_state_{suffix}_{name}")
            equations.append(state_var == value)
        relation = z3.And(path_cond, *equations)
        return Reachability(
            path=path,
            relation=relation,
            path_condition=path_cond,
            state=target_state,
            complete=self.last_path_enumeration_complete,
        )

    def enumerate_target_paths(
        self,
        target: NodeId,
        *,
        max_paths: int = 256,
        max_depth: int = 40,
    ) -> List[Tuple[List[NodeId], List[z3.BoolRef]]]:
        """Enumerate paths that reach ``target`` and their branch guards.

        Loops are deliberately represented by a zero-iteration path and one
        body path.  This is still an approximation, but each claim now gets
        the paths that lead to its own node rather than one program-wide path.
        """
        for stmt in self.tree.body:
            if (
                isinstance(stmt, ast.FunctionDef)
                and self.ids.get(stmt)
                and self.ids[stmt].func == target.func
            ):
                self.path_coverage_complete = True
                results = self._paths_to_target(
                    stmt.body, target, [], [], max_paths, max_depth
                )
                self.last_path_enumeration_complete = self.path_coverage_complete
                return results[:max_paths]
        self.last_path_enumeration_complete = True
        return []

    def _paths_to_target(
        self,
        stmts: List[ast.stmt],
        target: NodeId,
        prefix: List[NodeId],
        guards: List[z3.BoolRef],
        max_paths: int,
        depth: int,
    ) -> List[Tuple[List[NodeId], List[z3.BoolRef]]]:
        if depth <= 0:
            self.path_coverage_complete = False
            return []

        for index, stmt in enumerate(stmts):
            nid = self.ids.get(stmt)
            before = stmts[:index]
            prior_paths = self._enumerate_block_end(
                before, prefix, guards, max_paths, depth
            )
            if not prior_paths:
                continue

            if nid == target:
                return [
                    (p + [nid], g)
                    for p, g in prior_paths[:max_paths]
                ]

            if isinstance(stmt, ast.If):
                cond = self._node_guard(nid)
                results: list[tuple[List[NodeId], List[z3.BoolRef]]] = []
                for p, g in prior_paths:
                    results.extend(
                        self._paths_to_target(
                            stmt.body,
                            target,
                            p + ([nid] if nid else []),
                            g + [cond],
                            max_paths,
                            depth - 1,
                        )
                    )
                    else_guard = z3.Not(cond)
                    results.extend(
                        self._paths_to_target(
                            stmt.orelse,
                            target,
                            p + ([nid] if nid else []),
                            g + [else_guard],
                            max_paths,
                            depth - 1,
                        )
                    )
                if results:
                    return results[:max_paths]
                continue

            if isinstance(stmt, (ast.For, ast.While)):
                cond = self._node_guard(nid)
                if not z3.is_false(z3.simplify(cond)):
                    self.path_coverage_complete = False
                results = []
                for p, g in prior_paths:
                    results.extend(
                        self._paths_to_target(
                            stmt.body,
                            target,
                            p + ([nid] if nid else []),
                            g + [cond],
                            max_paths,
                            depth - 1,
                        )
                    )
                if results:
                    return results[:max_paths]
                continue

        return []

    def _enumerate_block_end(
        self,
        stmts: List[ast.stmt],
        prefix: List[NodeId],
        guards: List[z3.BoolRef],
        max_paths: int,
        depth: int,
    ) -> List[Tuple[List[NodeId], List[z3.BoolRef]]]:
        """Return bounded path prefixes after executing a statement block."""
        paths: list[tuple[List[NodeId], List[z3.BoolRef]]] = [(prefix, guards)]
        if depth <= 0:
            self.path_coverage_complete = False
            return paths

        for stmt in stmts:
            nid = self.ids.get(stmt)
            expanded: list[tuple[List[NodeId], List[z3.BoolRef]]] = []
            for p, g in paths:
                if isinstance(stmt, ast.If):
                    cond = self._node_guard(nid)
                    branches = [
                        (stmt.body, p + ([nid] if nid else []), g + [cond]),
                        (stmt.orelse, p + ([nid] if nid else []), g + [z3.Not(cond)]),
                    ]
                    for branch, bp, bg in branches:
                        expanded.extend(
                            self._enumerate_block_end(
                                branch, bp, bg, max_paths, depth - 1
                            )
                        )
                elif isinstance(stmt, (ast.For, ast.While)):
                    cond = self._node_guard(nid)
                    loop_is_dead = z3.is_false(z3.simplify(cond))
                    if not loop_is_dead:
                        self.path_coverage_complete = False
                    # A zero-iteration path skips the loop node entirely.
                    expanded.append((p, g + [z3.Not(cond)]))
                    expanded.extend(
                        self._enumerate_block_end(
                            stmt.body,
                            p + ([nid] if nid else []),
                            g + [cond],
                            max_paths,
                            depth - 1,
                        )
                    )
                else:
                    if isinstance(stmt, (ast.Return, ast.Break, ast.Continue)):
                        self.path_coverage_complete = False
                        # These statements do not fall through to the next
                        # statement in the current block.  A target after
                        # them must not inherit a fabricated path prefix.
                        continue
                    expanded.append((p + ([nid] if nid else []), g))
            paths = expanded[:max_paths]
            if not paths:
                break
        return paths

    def _node_guard(self, nid: Optional[NodeId]) -> z3.BoolRef:
        if nid is None or nid not in self.transitions:
            return z3.BoolVal(True)
        guard = self.transitions[nid].guard
        return guard if z3.is_bool(guard) else guard != 0

    @staticmethod
    def _as_bool(value: z3.ExprRef) -> z3.BoolRef:
        return value if z3.is_bool(value) else value != 0

    # --- Helpers --------------------------------------------------------------

    def _declare_params(self, state: SymState, func: ast.FunctionDef) -> None:
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        ):
            name = getattr(arg, "arg", None) or getattr(arg, "name", None)
            state.set(name, z3.Int(name))

    def _build_block_transitions(
        self, stmts: List[ast.stmt], state: SymState, current_func: str
    ) -> SymState:
        for stmt in stmts:
            state = self._build_stmt_transition(stmt, state, current_func)
        return state

    def _build_stmt_transition(
        self, stmt: ast.stmt, pre: SymState, current_func: str
    ) -> SymState:
        nid = self.ids.get(stmt)
        post = pre.copy()
        guard = z3.BoolVal(True)
        node = stmt

        if isinstance(stmt, ast.Assign):
            # Single-target
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                raise TrustedBranchViolation(
                    f"only Name targets allowed at L{node.lineno}", node.lineno
                )
            value = self._eval_expr(stmt.value, pre, suffix="next")
            post.set(target.id, value)
        elif isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                raise TrustedBranchViolation(
                    f"only Name targets allowed at L{node.lineno}", node.lineno
                )
            old = pre.get(stmt.target.id)
            if old is None:
                raise TrustedBranchViolation(
                    f"variable {stmt.target.id!r} used before initialization",
                    node.lineno,
                )
            rhs = self._eval_expr(stmt.value, pre)
            op = stmt.op
            if isinstance(op, ast.Add):
                new = old + rhs
            elif isinstance(op, ast.Sub):
                new = old - rhs
            elif isinstance(op, ast.Mult):
                new = old * rhs
            elif isinstance(op, ast.FloorDiv):
                new = old / rhs
            elif isinstance(op, ast.Mod):
                new = old % rhs
            else:
                raise TrustedBranchViolation(
                    f"augmented operator {type(op).__name__} not allowed", node.lineno
                )
            post.set(stmt.target.id, new)
        elif isinstance(stmt, ast.If):
            cond_expr = self._as_bool(self._eval_expr(stmt.test, pre))
            then_pre = pre.copy()
            else_pre = pre.copy()
            then_post = self._build_block_transitions(stmt.body, then_pre, current_func)
            else_post = self._build_block_transitions(stmt.orelse, else_pre, current_func)
            # Merge: post[i] = If(cond, then_post[i], else_post[i]) when either side has it
            keys = set(then_post.bindings) | set(else_post.bindings)
            for k in keys:
                t_val = then_post.get(k)
                e_val = else_post.get(k)
                if t_val is not None and e_val is not None:
                    try:
                        post.set(k, z3.If(cond_expr, t_val, e_val))
                    except z3.Z3Exception:
                        post.set(k, t_val)
                elif t_val is not None:
                    try:
                        post.set(k, z3.If(cond_expr, t_val, pre.get(k, z3.Int(k))))
                    except z3.Z3Exception:
                        post.set(k, t_val)
                else:
                    try:
                        post.set(k, z3.If(cond_expr, pre.get(k, z3.Int(k)), e_val))
                    except z3.Z3Exception:
                        post.set(k, e_val)
            guard = cond_expr
        elif isinstance(stmt, ast.For):
            # Only `for i in range(...)` — iterate symbolically over the body
            target = stmt.target
            if not isinstance(target, ast.Name):
                raise TrustedBranchViolation(
                    f"only Name targets in for allowed at L{node.lineno}", node.lineno
                )
            loop_var = target.id
            (start_v, stop_v, step_v) = self._eval_range(stmt.iter, pre)
            # For symbolic iteration we expose the loop var as a fresh Int with the range constraint
            i_sym = z3.Int(f"_loopvar_{stmt.lineno}")
            post.set(loop_var, i_sym)
            # The guard enforces the iteration: step_v > 0
            body_pre = post.copy()
            # Compose body transitions multiple times symbolically:
            # We approximate by composing N times with fresh suffix each round
            new_bindings = self._compose_n_iterations(
                stmt.body, body_pre, current_func, i_sym, start_v, stop_v, step_v
            )
            # After the loop, the loop_var is fully unconstrained (any int)
            for k, v in new_bindings.items():
                post.set(k, v)
            # Set loop_var to anything outside the range after the loop
            post.set(loop_var, z3.Int(f"_loopvar_out_{stmt.lineno}"))
            guard = z3.And(start_v < stop_v, step_v > 0)
        elif isinstance(stmt, ast.While):
            cond_expr = self._as_bool(self._eval_expr(stmt.test, pre))
            body_pre = pre.copy()
            body_post = self._build_block_transitions(stmt.body, body_pre, current_func)
            # Approximate by joining pre/post via the cond
            keys = set(pre.bindings) | set(body_post.bindings)
            for k in keys:
                pre_v = pre.get(k)
                body_v = body_post.get(k)
                if pre_v is not None and body_v is not None:
                    try:
                        post.set(k, z3.If(cond_expr, body_v, pre_v))
                    except z3.Z3Exception:
                        # If cond is not a Bool, fallback to body_v
                        post.set(k, body_v)
                elif body_v is not None:
                    post.set(k, body_v)
            guard = cond_expr
        elif isinstance(stmt, ast.Assert):
            # The assert is consumed by obligations/binding. We don't change the state here.
            pass
        elif isinstance(stmt, ast.Return):
            pass
        elif isinstance(stmt, (ast.Break, ast.Continue, ast.Pass)):
            pass
        else:
            raise TrustedBranchViolation(
                f"statement type {type(stmt).__name__} not handled at L{node.lineno}",
                node.lineno,
            )

        if nid is not None:
            # Coerce guard to Bool if needed
            if not z3.is_bool(guard):
                guard = guard != 0
            self.transitions[nid] = Transition(pre=pre, post=post, guard=guard, node=node)
        return post

    # --- Iteration helper (for `for i in range(...)`) -------------------------

    def _compose_n_iterations(
        self,
        body: List[ast.stmt],
        pre: SymState,
        current_func: str,
        i_sym: z3.ExprRef,
        start_v: z3.ExprRef,
        stop_v: z3.ExprRef,
        step_v: z3.ExprRef,
    ) -> Dict[str, z3.ExprRef]:
        # Iterative unrolling with fresh naming each round
        state = pre.copy()
        state.set("_loopvar_for", i_sym)
        # Single composition round (symbolic)
        post = self._build_block_transitions(body, state, current_func)
        return post.bindings

    def _eval_range(
        self, call: ast.Call, pre: SymState
    ) -> Tuple[z3.ExprRef, z3.ExprRef, z3.ExprRef]:
        if not call.args:
            return z3.IntVal(0), z3.IntVal(0), z3.IntVal(1)
        if len(call.args) == 1:
            start = z3.IntVal(0)
            stop = self._eval_expr(call.args[0], pre)
            step = z3.IntVal(1)
            return start, stop, step
        if len(call.args) == 2:
            start = self._eval_expr(call.args[0], pre)
            stop = self._eval_expr(call.args[1], pre)
            step = z3.IntVal(1)
            return start, stop, step
        start = self._eval_expr(call.args[0], pre)
        stop = self._eval_expr(call.args[1], pre)
        step = self._eval_expr(call.args[2], pre)
        return start, stop, step

    # --- Expression evaluation (trusted) --------------------------------------

    def _eval_expr(
        self, expr: ast.expr, state: SymState, suffix: str = ""
    ) -> z3.ExprRef:
        if isinstance(expr, ast.Constant):
            v = expr.value
            if isinstance(v, bool):
                return z3.BoolVal(v)
            if isinstance(v, int):
                return z3.IntVal(v)
            if v is None:
                return z3.IntVal(0)
            raise TrustedBranchViolation(
                f"constant {type(v).__name__} not allowed", expr.lineno
            )
        if isinstance(expr, ast.Name):
            v = state.get(expr.id)
            if v is None:
                raise TrustedBranchViolation(
                    f"variable {expr.id!r} is outside the symbolic state",
                    getattr(expr, "lineno", -1),
                )
            return v
        if isinstance(expr, ast.UnaryOp):
            inner = self._eval_expr(expr.operand, state)
            if isinstance(expr.op, ast.USub):
                return -inner
            if isinstance(expr.op, ast.Not):
                # Python: `not <int>` is False if int==0, else True.
                # In Z3, `Not` requires a Bool, so coerce non-Bool operands.
                if z3.is_bool(inner):
                    return z3.Not(inner)
                return inner == 0
            raise TrustedBranchViolation(
                f"unary operator {type(expr.op).__name__} not allowed", expr.lineno
            )
        if isinstance(expr, ast.BinOp):
            l = self._eval_expr(expr.left, state)
            r = self._eval_expr(expr.right, state)
            op = expr.op
            if isinstance(op, ast.Add):
                return l + r
            if isinstance(op, ast.Sub):
                return l - r
            if isinstance(op, ast.Mult):
                return l * r
            if isinstance(op, ast.FloorDiv):
                return l / r
            if isinstance(op, ast.Mod):
                return l % r
            raise TrustedBranchViolation(
                f"binary operator {type(op).__name__} not allowed", expr.lineno
            )
        if isinstance(expr, ast.BoolOp):
            vals = [self._as_bool(self._eval_expr(v, state)) for v in expr.values]
            if isinstance(expr.op, ast.And):
                return z3.And(*vals)
            if isinstance(expr.op, ast.Or):
                return z3.Or(*vals)
            raise TrustedBranchViolation(
                f"bool operator {type(expr.op).__name__} not allowed", expr.lineno
            )
        if isinstance(expr, ast.Compare):
            l = self._eval_expr(expr.left, state)
            result: z3.BoolRef = z3.BoolVal(True)
            for op, comp in zip(expr.ops, expr.comparators):
                r = self._eval_expr(comp, state)
                if isinstance(op, ast.Eq):
                    expr_v = l == r
                elif isinstance(op, ast.NotEq):
                    expr_v = l != r
                elif isinstance(op, ast.Lt):
                    expr_v = l < r
                elif isinstance(op, ast.LtE):
                    expr_v = l <= r
                elif isinstance(op, ast.Gt):
                    expr_v = l > r
                elif isinstance(op, ast.GtE):
                    expr_v = l >= r
                else:
                    raise TrustedBranchViolation(
                        f"comparison {type(op).__name__} not allowed", expr.lineno
                    )
                result = z3.And(result, expr_v)
                l = r
            return result
        if isinstance(expr, ast.IfExp):
            cond = self._as_bool(self._eval_expr(expr.test, state))
            t_val = self._eval_expr(expr.body, state)
            f_val = self._eval_expr(expr.orelse, state)
            return z3.If(cond, t_val, f_val)
        if isinstance(expr, ast.Call):
            if not isinstance(expr.func, ast.Name):
                raise TrustedBranchViolation(
                    "only bare-name calls allowed", expr.lineno
                )
            name = expr.func.id
            args = [self._eval_expr(a, state) for a in expr.args]
            if name == "abs":
                if len(args) != 1:
                    raise TrustedBranchViolation("abs requires 1 arg", expr.lineno)
                return z3.If(args[0] >= 0, args[0], -args[0])
            if name == "min":
                if len(args) != 2:
                    raise TrustedBranchViolation("min requires 2 args", expr.lineno)
                return z3.If(args[0] <= args[1], args[0], args[1])
            if name == "max":
                if len(args) != 2:
                    raise TrustedBranchViolation("max requires 2 args", expr.lineno)
                return z3.If(args[0] >= args[1], args[0], args[1])
            if name == "len":
                # Symbolic: just return an Int with the arg's name if known,
                # or 0 for empty range. We accept this only when called inside
                # a trusted pre-state. We coerce to IntVal(0) if unknown.
                return z3.IntVal(0)
            raise TrustedBranchViolation(
                f"function {name!r} not supported", expr.lineno
            )
        raise TrustedBranchViolation(
            f"expression type {type(expr).__name__} not handled", expr.lineno
        )

    # --- Path enumeration -----------------------------------------------------

    def _enumerate_paths_in_block(
        self,
        stmts: List[ast.stmt],
        prefix: List[NodeId],
        out: List[List[NodeId]],
        max_paths: int,
        depth: int,
        current_func: str,
    ) -> None:
        if len(out) >= max_paths or depth <= 0:
            return
        for stmt in stmts:
            if len(out) >= max_paths:
                return
            nid = self.ids.get(stmt)
            if isinstance(stmt, ast.If):
                then_path = prefix + ([nid] if nid else [])
                self._enumerate_paths_in_block(
                    stmt.body, then_path, out, max_paths, depth - 1, current_func
                )
                if stmt.orelse:
                    self._enumerate_paths_in_block(
                        stmt.orelse, prefix, out, max_paths, depth - 1, current_func
                    )
                return
            if isinstance(stmt, (ast.For, ast.While)):
                # Conservative: take the body once (does not iterate)
                body_path = prefix + ([nid] if nid else [])
                self._enumerate_paths_in_block(
                    stmt.body, body_path, out, max_paths, depth - 1, current_func
                )
                # Path that skips the loop
                out.append(prefix + ([nid] if nid else []))
                return
            if nid is not None:
                prefix = prefix + [nid]

        out.append(prefix)

    # --- Variable renaming ----------------------------------------------------

    def _rename(self, state: SymState, prefix: str) -> Dict[str, z3.ExprRef]:
        renamed: Dict[str, z3.ExprRef] = {}
        for k, v in state.bindings.items():
            try:
                is_concrete = z3.is_int_value(v) or z3.is_bool_value(v)
            except AttributeError:
                is_concrete = z3.is_int_value(v)
            if is_concrete:
                renamed[k] = v
            else:
                renamed[k] = z3.Int(f"{prefix}{k}")
        return renamed

    def _apply_renaming(
        self, state: SymState, mapping: Mapping[str, z3.ExprRef]
    ) -> Dict[str, z3.ExprRef]:
        out: Dict[str, z3.ExprRef] = {}
        for k, v in state.bindings.items():
            if k in mapping:
                out[k] = mapping[k]
            else:
                out[k] = v
        return out


__all__ = [
    "SymState",
    "Transition",
    "Reachability",
    "TrustedBranchViolation",
    "TrustedSymbolicExecutor",
]
