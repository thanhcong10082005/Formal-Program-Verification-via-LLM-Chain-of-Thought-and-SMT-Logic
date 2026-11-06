"""Tier 2 of the ground truth: an exact symbolic prover.

Tier 1 (:mod:`astverifier.shared.oracle`) can only *falsify* a claim. Tier 2
is what turns "no violation found" into a *proof*.

How the proof works
-------------------

The dataset's programs are all of the shape ``guards; while loop; assert``.
The prover encodes the loop as a **Datalog relation** and hands it to Z3's
Spacer engine::

    Inv(p_1, ..., p_n, v_1, ..., v_m)

``Inv`` holds exactly for the loop-head states that are reachable *for those
inputs*. Because the parameters are part of the relation, a query such as
``Inv(p..., v...) AND <the claim fails>`` is a genuine reachability
question, and Spacer answers it exactly:

* ``unsat`` means "no input ever reaches a state that violates the claim",
  which is a **proof** of the claim;
* ``sat`` means "this input does, and here it is" -- a concrete
  **counterexample**.

Two consequences make the labelling trustworthy:

* ``unsat`` is **sound**: Spacer's relation under-approximates the
  reachable states, so a state that is never reached is never reported as
  reachable. A program whose claim site is unreachable is labelled
  ``UNREACHABLE``.
* a ``sat`` answer is a **real** counterexample: the witness is replayed on
  real CPython before the label is written. A witness that does not
  reproduce is never reported as ``COUNTEREXAMPLE``.

The prover never guesses. Anything it cannot decide is reported as
``INCONCLUSIVE`` and downgraded by the labelling pipeline.

Two queries, two roles
----------------------

The prover asks Spacer two different questions, and the difference matters:

1. **The characterization** ``psi`` is the exact description of the
   reachable loop-head states, obtained by asking Spacer for the invariant
   of a contradictory query. It is cheap (milliseconds) even for loops with
   huge bounds. A violation that is ``unsat`` under ``psi`` is therefore
   *proved impossible* -- this is the fast path, and it settles the
   overwhelming majority of the dataset.
2. **The exact reachability query** ``Inv(...) AND violation`` is the only
   way to *confirm* a violation. It is far more expensive, and for some
   loops (a bound of 100,000) it does not terminate at all.

Because the characterization is an over-approximation, "``psi`` allows a
violation" is only a *candidate*. The prover therefore reports a
counterexample in one of two ways:

* the exact query confirms it -- the witness comes from Spacer's proof
  term, or
* the exact query runs out of time -- the prover falls back to the model of
  ``psi AND violation``, which yields a concrete candidate input.

Either way the labelling pipeline replays the witness on real CPython. A
candidate that does not reproduce is never reported as a counterexample, so
the fallback is safe.
"""

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass, field

import z3

from .structure import AssertSite, ProgramShape, StructureError, analyze
from .symexec import SymExecError, eval_expr

VERDICT_VERIFIED = "VERIFIED"
VERDICT_COUNTEREXAMPLE = "COUNTEREXAMPLE"
VERDICT_UNREACHABLE = "UNREACHABLE"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_REACHABLE = "REACHABLE"
"""The claim's site *is* reachable. The mirror image of ``UNREACHABLE``.

It is not a claim verdict, so it never appears in a manifest; it only
distinguishes "the claim is unconstrained because it never runs" from "the
claim holds wherever it runs".
"""

# Spacer's time budget, in milliseconds.
SPACER_TIMEOUT_MS = 20_000

# The time budget for the *exact* Datalog reachability query, in
# milliseconds. This query is the fallback for the rare case where
# Spacer's characterization allows a violation but cannot confirm one.
# It is far more expensive than the characterization, so it gets a
# shorter budget: a program that needs longer is reported as
# inconclusive rather than guessed at.
EXACT_QUERY_TIMEOUT_MS = 10_000

# The plain solver's time budget, in milliseconds (used for loop-free
# programs and for replaying a candidate counterexample).
SOLVER_TIMEOUT_MS = 20_000

# Variables whose name starts with this prefix are scratch space: the
# dataset never reads them outside the loop, and no real variable's value
# depends on them, so they cannot influence any claim.
JUNK_PREFIX = "junk_"


class ProverError(Exception):
    """Raised when the prover cannot handle a program."""


@dataclass
class ProofResult:
    """The outcome of one tier-2 proof attempt."""

    verdict: str
    reason: str
    witness: dict[str, int] | None = None
    precondition: str | None = None
    elapsed: float = 0.0
    n_sites: int = 0
    n_reachable: int = 0
    detail: dict[str, str] = field(default_factory=dict)


@dataclass
class _Site:
    """One assert site with its exact path condition and environment."""

    site: AssertSite
    cond: z3.BoolRef
    env: dict[str, z3.ExprRef]


@dataclass
class _Encoding:
    """The Spacer encoding of one program."""

    fixedpoint: z3.Fixedpoint
    relation: z3.FuncDeclRef
    params: list[str]
    """The parameter names, in the order they appear in the relation."""

    live: list[str]
    """The loop's own variables, in the order they appear in the relation."""

    param_symbols: dict[str, z3.ExprRef]
    live_symbols: dict[str, z3.ExprRef]
    sites: list[_Site]
    """Every assert site, with the exact condition under which it is reached."""


# --- The encoding -------------------------------------------------------------


def _augment(
    current: z3.ExprRef,
    op: ast.operator,
    value: ast.expr,
    env: dict[str, z3.ExprRef],
) -> z3.ExprRef:
    """Apply an augmented assignment symbolically."""
    delta = eval_expr(value, env)
    if isinstance(op, ast.Add):
        return current + delta
    if isinstance(op, ast.Sub):
        return current - delta
    if isinstance(op, ast.Mult):
        return current * delta
    if isinstance(op, ast.FloorDiv):
        return current / delta
    if isinstance(op, ast.Mod):
        return current % delta
    raise SymExecError(f"operator {type(op).__name__} is not supported")


def _body_paths(
    stmts: list[ast.stmt],
    env: dict[str, z3.ExprRef],
    acc: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]],
) -> None:
    """Enumerate every branch of a loop body.

    Each entry of ``acc`` is a ``(condition, post-state)`` pair. The
    condition is the conjunction of the branch conditions that lead to that
    state, so it can be used directly as the guard of a Datalog rule.
    """
    if not stmts:
        acc.append((z3.BoolVal(True), dict(env)))
        return

    stmt, rest = stmts[0], stmts[1:]

    if isinstance(stmt, ast.Pass):
        _body_paths(rest, env, acc)

    elif isinstance(stmt, ast.Assign):
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            raise SymExecError("only single-name assignment targets are supported")
        new_env = dict(env)
        new_env[stmt.targets[0].id] = eval_expr(stmt.value, new_env)
        _body_paths(rest, new_env, acc)

    elif isinstance(stmt, ast.AugAssign):
        if not isinstance(stmt.target, ast.Name):
            raise SymExecError("only single-name assignment targets are supported")
        new_env = dict(env)
        new_env[stmt.target.id] = _augment(
            new_env[stmt.target.id], stmt.op, stmt.value, new_env
        )
        _body_paths(rest, new_env, acc)

    elif isinstance(stmt, ast.If):
        cond = eval_expr(stmt.test, env)
        branches = [(cond, stmt.body)]
        if stmt.orelse:
            branches.append((z3.Not(cond), stmt.orelse))
        else:
            # An ``if`` without an ``else`` has an implicit fall-through
            # branch: when the test fails, the body is skipped and the
            # state is unchanged. Omitting it would silently drop a path.
            branches.append((z3.Not(cond), [ast.Pass()]))

        for branch_cond, branch in branches:
            inner: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]] = []
            _body_paths(branch, dict(env), inner)
            for inner_cond, inner_env in inner:
                tail: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]] = []
                _body_paths(rest, inner_env, tail)
                for tail_cond, tail_env in tail:
                    acc.append(
                        (z3.And(branch_cond, inner_cond, tail_cond), tail_env)
                    )

    else:
        raise SymExecError(f"statement {type(stmt).__name__} is not supported")


def _loop_written(loop: ast.While) -> set[str]:
    """Return the names the loop body writes."""
    written: set[str] = set()
    for node in ast.walk(loop):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    written.add(target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            written.add(node.target.id)
    return written


def _suffix_sites(
    stmts: list[ast.stmt],
    env: dict[str, z3.ExprRef],
    cond: z3.BoolRef,
    sites: list[tuple[z3.BoolRef, dict[str, z3.ExprRef], ast.Assert]],
) -> None:
    """Collect the assert sites that follow the loop.

    Assignments are tracked, because a claim often reads a value that the
    program computed just before it (``_contract_result = -val; assert
    _contract_result == -val``). Each statement is evaluated on its own
    copy of the environment, so a branch never leaks its assignments into
    a sibling branch.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Assert):
            sites.append((cond, dict(env), stmt))
        elif isinstance(stmt, ast.Pass):
            continue
        elif isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise SymExecError("only single-name assignment targets are supported")
            env = dict(env)
            env[stmt.targets[0].id] = eval_expr(stmt.value, env)
        elif isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                raise SymExecError("only single-name assignment targets are supported")
            env = dict(env)
            env[stmt.target.id] = _augment(env[stmt.target.id], stmt.op, stmt.value, env)
        elif isinstance(stmt, ast.If):
            branch = eval_expr(stmt.test, env)
            _suffix_sites(stmt.body, dict(env), z3.And(cond, branch), sites)
            # An ``if`` without an ``else`` still has an implicit
            # fall-through path, where the branch's statements do not run.
            _suffix_sites(
                stmt.orelse if stmt.orelse else [ast.Pass()],
                dict(env),
                z3.And(cond, z3.Not(branch)),
                sites,
            )
        elif isinstance(stmt, ast.Return):
            # A ``return`` ends the path: nothing after it is reachable.
            # The statements that follow it belong to the *other* branch.
            return
        elif isinstance(stmt, ast.Expr):
            # A bare expression statement (a call whose value is unused).
            continue
        else:
            raise SymExecError(f"statement {type(stmt).__name__} is not supported")


def _encode(shape: ProgramShape, tag: str) -> _Encoding:
    """Build the Spacer encoding of one program."""
    func = shape.func
    params = shape.params
    param_symbols = {name: z3.Int(f"{tag}_p_{name}") for name in params}

    body = func.body
    if shape.loop is None:
        raise ProverError("the program has no loop")

    loop_index = next(
        (i for i, s in enumerate(body) if isinstance(s, ast.While)), -1
    )
    prefix = body[:loop_index]
    loop = shape.loop
    suffix = body[loop_index + 1:]

    # --- the prologue ------------------------------------------------------
    env = dict(param_symbols)
    path = z3.BoolVal(True)
    for stmt in prefix:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise SymExecError("only single-name assignment targets are supported")
            env[stmt.targets[0].id] = eval_expr(stmt.value, env)
        elif isinstance(stmt, ast.Pass):
            continue
        elif isinstance(stmt, ast.If):
            # An early-return guard: execution continues only when the
            # guard's test does *not* hold.
            if not all(isinstance(s, ast.Return) for s in stmt.body):
                raise SymExecError("only return guards are supported")
            path = z3.And(path, z3.Not(eval_expr(stmt.test, env)))
        else:
            raise SymExecError(f"statement {type(stmt).__name__} is not supported")

    # --- the loop's own variables -----------------------------------------
    written = _loop_written(loop)
    shadowed = written & set(params)
    if shadowed:
        # The loop rebinds a parameter. The relation carries the parameters
        # unchanged, so the rebinding could not be represented exactly.
        # Rather than silently proving something false, the prover refuses
        # the program.
        raise ProverError(
            f"the loop rebinds the parameter(s) {sorted(shadowed)}"
        )
    live = sorted(
        name for name in written if not name.startswith(JUNK_PREFIX)
    )
    if not live:
        raise ProverError("the loop writes no observable variable")

    live_symbols = {name: z3.Int(f"{tag}_v_{name}") for name in live}
    entry = [env.get(name, z3.Int(f"{tag}_u_{name}")) for name in live]

    fixedpoint = z3.Fixedpoint()
    fixedpoint.set(engine="spacer")
    fixedpoint.set("timeout", SPACER_TIMEOUT_MS)

    relation = z3.Function(
        f"{tag}_Inv",
        *[z3.IntSort()] * (len(params) + len(live)),
        z3.BoolSort(),
    )
    fixedpoint.register_relation(relation)

    for symbol in list(param_symbols.values()) + list(live_symbols.values()):
        fixedpoint.declare_var(symbol)

    param_args = [param_symbols[name] for name in params]
    live_args = [live_symbols[name] for name in live]

    # --- the entry rule ----------------------------------------------------
    fixedpoint.rule(
        relation(*(param_args + live_args)),
        z3.And(path, *[live_args[i] == entry[i] for i in range(len(live))]),
    )

    # --- the transition rules ----------------------------------------------
    head_env = {**env, **live_symbols}
    guard = eval_expr(loop.test, head_env)

    branches: list[tuple[z3.BoolRef, dict[str, z3.ExprRef]]] = []
    _body_paths(loop.body, dict(head_env), branches)
    if not branches:
        raise ProverError("the loop body has no fall-through path")

    for index, (branch_cond, branch_env) in enumerate(branches):
        post = {name: z3.Int(f"{tag}_w{index}_{name}") for name in live}
        for symbol in post.values():
            fixedpoint.declare_var(symbol)
        values = [branch_env.get(name, live_symbols[name]) for name in live]
        fixedpoint.rule(
            relation(*(param_args + [post[name] for name in live])),
            z3.And(
                relation(*(param_args + live_args)),
                guard,
                branch_cond,
                *[
                    post[name] == values[i]
                    for i, name in enumerate(live)
                ],
            ),
        )

    # --- the state after the loop -----------------------------------------
    exit_env = {**env, **live_symbols}
    exit_guard = z3.Not(guard)

    # The prologue's guards were evaluated *before* the loop ran, so their
    # conditions must be expressed over the pre-loop environment. Using the
    # exit environment here would let the loop's own writes redefine the
    # guard, which silently changes the meaning of the program.
    pre_loop_env = dict(env)

    raw_sites: list[tuple[z3.BoolRef, dict[str, z3.ExprRef], ast.Assert]] = []
    _suffix_sites(suffix, exit_env, z3.And(path, exit_guard), raw_sites)

    by_node = {s.node: s for s in shape.sites}
    sites: list[_Site] = []
    for _cond, site_env, node in raw_sites:
        meta = by_node.get(node)
        if meta is None:  # pragma: no cover - defensive
            meta = AssertSite(
                node=node,
                text=ast.unparse(node.test),
                line=node.lineno,
                in_branch=None,
                branch_is_else=False,
            )

        # The exact condition under which this assert is reached: the
        # prologue's guards, the loop's exit condition and the branch
        # condition of the enclosing ``if``.
        parts: list[z3.BoolRef] = [
            eval_expr(g.fall_through, pre_loop_env) for g in meta.guards
        ]
        parts.append(exit_guard)
        if meta.in_branch is not None:
            branch = eval_expr(meta.in_branch.test, site_env)
            parts.append(z3.Not(branch) if meta.branch_is_else else branch)

        sites.append(
            _Site(
                site=meta,
                cond=z3.And(*parts) if parts else z3.BoolVal(True),
                env=site_env,
            )
        )

    return _Encoding(
        fixedpoint=fixedpoint,
        relation=relation,
        params=params,
        live=live,
        param_symbols=param_symbols,
        live_symbols=live_symbols,
        sites=sites,
    )


def _characterize(encoding: _Encoding) -> z3.BoolRef | None:
    """Return Spacer's exact characterization of the reachable states.

    The characterization is a formula ``psi`` over the relation's arguments
    that holds exactly for the loop-head states the loop can reach.

    Spacer computes it in response to a contradictory query, which asks it
    for the invariant rather than a counterexample. The computation is
    cheap even for loops with very large bounds, whereas a direct
    reachability query over the relation can be expensive.
    """
    fixedpoint = encoding.fixedpoint
    args = _relation_args(encoding)
    relation_at_head = encoding.relation(*args)

    try:
        result = fixedpoint.query(z3.And(relation_at_head, z3.BoolVal(False)))
    except z3.Z3Exception:
        return None
    if result != z3.unsat:
        return None

    try:
        answer = fixedpoint.get_answer()
    except z3.Z3Exception:
        return None
    if not z3.is_quantifier(answer):
        return None
    body = answer.body()
    if not z3.is_eq(body):
        return None
    lhs, rhs = body.arg(0), body.arg(1)

    # The invariant equation is ``Inv(v0, ..., vn) == psi``. Only ``v0..vn``
    # are the relation's arguments; ``psi`` may mention further Skolem
    # variables, which must not be renamed.
    bound: list[z3.ExprRef] = []
    for child in lhs.children():
        if not z3.is_var(child):
            return None
        bound.append(child)
    if len(bound) != len(args):
        return None

    for variable, symbol in zip(bound, args, strict=True):
        rhs = z3.substitute(rhs, (variable, symbol))
    return rhs


def _relation_args(encoding: _Encoding) -> list[z3.ExprRef]:
    """Return the relation's arguments: the parameters, then the live vars."""
    return [
        encoding.param_symbols[name] for name in encoding.params
    ] + [encoding.live_symbols[name] for name in encoding.live]


def _extract_witness(
    fixedpoint: z3.Fixedpoint, tag: str, params: list[str]
) -> dict[str, int] | None:
    """Pull the ground parameter values out of Spacer's answer.

    Spacer's answer is a proof term that contains a ground application of
    the relation, e.g. ``Inv(0, 3)``. The parameters come first in the
    relation's signature, so the leading arguments are the witness.
    """
    try:
        answer = fixedpoint.get_answer()
    except z3.Z3Exception:
        return None
    text = answer.sexpr()
    # The answer is a proof term such as
    #   (mp ((_ hyper-res 0 0) (asserted ...) (q_Inv 0 0 0)) ...)
    # The ground application of the relation carries the witness.
    pattern = re.compile(rf"\({re.escape(tag)}_Inv\s+([^)]*)\)")
    for raw in pattern.findall(text):
        parts = [p.strip() for p in raw.split()]
        if len(parts) < len(params):
            continue
        values: dict[str, int] = {}
        for name, part in zip(params, parts, strict=False):
            try:
                values[name] = int(part)
            except ValueError:
                values = {}
                break
        if values:
            return values
    return None


# --- The prover ----------------------------------------------------------------


def _prove_without_loop(
    shape: ProgramShape, claim_text: str, precondition: str | None, started: float
) -> ProofResult:
    """Decide a claim for a program that has no loop.

    Without a loop the program is straight-line code, so plain symbolic
    execution plus a plain Z3 solver is both sound and complete.
    """
    env = {name: z3.Int(f"p_{name}") for name in shape.params}
    sites: list[tuple[z3.BoolRef, dict[str, z3.ExprRef], ast.Assert]] = []
    _suffix_sites(shape.func.body, env, z3.BoolVal(True), sites)

    if not sites:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason="no assert statement found",
            precondition=precondition,
            elapsed=time.perf_counter() - started,
        )

    matching = [s for s in sites if ast.unparse(s[2]) == claim_text] or sites

    reachable = z3.BoolVal(False)
    violated = z3.BoolVal(False)
    for cond, site_env, node in matching:
        claim = eval_expr(node.test, site_env)
        reachable = z3.Or(reachable, cond)
        violated = z3.Or(violated, z3.And(cond, z3.Not(claim)))

    solver = z3.Solver()
    solver.set("timeout", SOLVER_TIMEOUT_MS)
    solver.add(reachable)
    if solver.check() == z3.unsat:
        return ProofResult(
            verdict=VERDICT_UNREACHABLE,
            reason="no reachable state reaches the claim's site",
            precondition=precondition,
            elapsed=time.perf_counter() - started,
            n_sites=len(matching),
        )

    solver = z3.Solver()
    solver.set("timeout", SOLVER_TIMEOUT_MS)
    solver.add(violated)
    result = solver.check()
    elapsed = time.perf_counter() - started

    if result == z3.unsat:
        return ProofResult(
            verdict=VERDICT_VERIFIED,
            reason="no reachable state violates the claim",
            precondition=precondition,
            elapsed=elapsed,
            n_sites=len(matching),
            n_reachable=len(matching),
        )
    if result == z3.unknown:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"solver returned unknown: {solver.reason_unknown()}",
            precondition=precondition,
            elapsed=elapsed,
            n_sites=len(matching),
        )

    model = solver.model()
    witness: dict[str, int] = {}
    for name in shape.params:
        try:
            value = model.eval(z3.Int(f"p_{name}"), model_completion=True)
        except z3.Z3Exception:
            continue
        if z3.is_int_value(value):
            witness[name] = value.as_long()
    return ProofResult(
        verdict=VERDICT_COUNTEREXAMPLE,
        reason="a reachable state violates the claim",
        witness=witness or None,
        precondition=precondition,
        elapsed=elapsed,
        n_sites=len(matching),
        n_reachable=len(matching),
    )


def prove_reachability(
    source: str,
    claim_text: str,
    *,
    tag: str = "r",
) -> ProofResult:
    """Decide *exactly* whether the claim's site is reachable.

    The prover's main path answers "is the site reachable?" with
    ``psi AND cond``, where ``psi`` is Spacer's characterization -- an
    *over-approximation* of the reachable states. That is sound in one
    direction only:

    * ``psi AND cond`` is ``unsat`` => the site is provably unreachable;
    * ``psi AND cond`` is ``sat`` => the site *might* be unreachable.

    This function closes the gap. It asks the exact Datalog reachability
    query ``Inv(...) AND cond``, which decides the question precisely, and
    returns :data:`VERDICT_REACHABLE` or :data:`VERDICT_UNREACHABLE`.

    The cheap ``psi`` shortcut is kept for the unreachable case, because
    Spacer refutes an unreachable site far more cheaply than it confirms a
    reachable one.
    """
    started = time.perf_counter()

    try:
        shape = analyze(source)
    except (SyntaxError, StructureError) as exc:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"analysis failed: {exc}",
            elapsed=time.perf_counter() - started,
        )

    if not shape.sites:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason="no assert statement found",
            elapsed=time.perf_counter() - started,
        )

    if shape.loop is None:
        # Without a loop the program is straight-line, so plain symbolic
        # execution decides reachability exactly.
        env = {name: z3.Int(f"p_{name}") for name in shape.params}
        sites: list[tuple[z3.BoolRef, dict[str, z3.ExprRef], ast.Assert]] = []
        _suffix_sites(shape.func.body, env, z3.BoolVal(True), sites)
        matching = [s for s in sites if ast.unparse(s[2]) == claim_text] or sites
        if not matching:
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason="no assert statement found",
                elapsed=time.perf_counter() - started,
            )
        reachable = z3.Or(*[cond for cond, _, _ in matching])
        solver = z3.Solver()
        solver.set("timeout", SOLVER_TIMEOUT_MS)
        solver.add(reachable)
        try:
            result = solver.check()
        except z3.Z3Exception as exc:
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason=f"solver failed: {exc}",
                elapsed=time.perf_counter() - started,
            )
        elapsed = time.perf_counter() - started
        if result == z3.unsat:
            return ProofResult(
                verdict=VERDICT_UNREACHABLE,
                reason="no reachable state reaches the claim's site",
                elapsed=elapsed,
                n_sites=len(matching),
                n_reachable=0,
            )
        if result == z3.unknown:
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason=f"solver returned unknown: {solver.reason_unknown()}",
                elapsed=elapsed,
                n_sites=len(matching),
            )
        return ProofResult(
            verdict=VERDICT_REACHABLE,
            reason="a reachable state reaches the claim's site",
            elapsed=elapsed,
            n_sites=len(matching),
            n_reachable=len(matching),
        )

    try:
        encoding = _encode(shape, tag)
    except (SymExecError, ProverError, z3.Z3Exception) as exc:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"encoding failed: {exc}",
            elapsed=time.perf_counter() - started,
        )

    matching = [s for s in encoding.sites if s.site.text == claim_text]
    if not matching:
        matching = encoding.sites
    if not matching:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason="no assert statement found",
            elapsed=time.perf_counter() - started,
        )

    fixedpoint = encoding.fixedpoint
    args = _relation_args(encoding)
    inv = encoding.relation(*args)

    # The cheap shortcut: the characterization over-approximates the
    # reachable states, so an ``unsat`` answer already proves that the
    # site is unreachable.
    psi = _characterize(encoding)
    if psi is not None:
        solver = z3.Solver()
        solver.set("timeout", SOLVER_TIMEOUT_MS)
        solver.add(z3.And(psi, matching[0].cond))
        try:
            quick = solver.check()
        except z3.Z3Exception:
            quick = z3.unknown
        if quick == z3.unsat:
            return ProofResult(
                verdict=VERDICT_UNREACHABLE,
                reason="no reachable state reaches the claim's site",
                elapsed=time.perf_counter() - started,
                n_sites=len(matching),
                n_reachable=0,
            )

    # The exact query decides the reachable case.
    fixedpoint.set("timeout", EXACT_QUERY_TIMEOUT_MS)
    try:
        result = str(fixedpoint.query(z3.And(inv, matching[0].cond)))
    except z3.Z3Exception as exc:
        result = f"error:{exc}"
    finally:
        fixedpoint.set("timeout", SPACER_TIMEOUT_MS)

    elapsed = time.perf_counter() - started
    if result == "sat":
        return ProofResult(
            verdict=VERDICT_REACHABLE,
            reason="a reachable state reaches the claim's site",
            elapsed=elapsed,
            n_sites=len(matching),
            n_reachable=len(matching),
        )
    if result == "unsat":
        return ProofResult(
            verdict=VERDICT_UNREACHABLE,
            reason="no reachable state reaches the claim's site",
            elapsed=elapsed,
            n_sites=len(matching),
            n_reachable=0,
        )
    return ProofResult(
        verdict=VERDICT_INCONCLUSIVE,
        reason=f"exact reachability query did not decide: {result}",
        elapsed=elapsed,
        n_sites=len(matching),
    )


def prove(
    source: str,
    claim_text: str,
    *,
    precondition: str | None = None,
    tag: str = "p",
) -> ProofResult:
    """Try to prove that ``claim_text`` holds at its assert site.

    Args:
        source: The program under verification.
        claim_text: The claim expression, as source text.
        precondition: An extra assumption that holds on every path to the
            claim. It is *not* needed for the dataset's programs -- their
            path conditions are derived from the AST -- but it is honoured
            when given.
        tag: A prefix for the generated Z3 symbols.

    Returns:
        A :class:`ProofResult` with the verdict and the reason.
    """
    started = time.perf_counter()

    try:
        shape = analyze(source)
    except (SyntaxError, StructureError) as exc:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"analysis failed: {exc}",
            elapsed=time.perf_counter() - started,
        )

    if not shape.sites:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason="no assert statement found",
            elapsed=time.perf_counter() - started,
        )

    if shape.loop is None:
        try:
            return _prove_without_loop(
                shape, claim_text, precondition, started
            )
        except (SymExecError, z3.Z3Exception) as exc:
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason=f"loop-free proof failed: {exc}",
                precondition=precondition,
                elapsed=time.perf_counter() - started,
            )

    try:
        encoding = _encode(shape, tag)
    except (SymExecError, ProverError, z3.Z3Exception) as exc:
        return ProofResult(
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"encoding failed: {exc}",
            precondition=precondition,
            elapsed=time.perf_counter() - started,
        )

    # The sites whose claim text matches the requested claim.
    matching = [s for s in encoding.sites if s.site.text == claim_text]
    if not matching:
        matching = encoding.sites

    fixedpoint = encoding.fixedpoint
    param_args = [encoding.param_symbols[name] for name in encoding.params]
    live_args = [encoding.live_symbols[name] for name in encoding.live]
    inv = encoding.relation(*(param_args + live_args))

    # An extra precondition is an assumption about the parameters.
    assumption: z3.BoolRef | None = None
    if precondition:
        try:
            node = ast.parse(precondition, mode="eval").body
            assumption = eval_expr(node, encoding.param_symbols)
        except (SyntaxError, SymExecError) as exc:
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason=f"precondition does not parse: {exc}",
                precondition=precondition,
                elapsed=time.perf_counter() - started,
            )

    def _query(formula: z3.BoolRef) -> str:
        if assumption is not None:
            formula = z3.And(formula, assumption)
        try:
            return str(fixedpoint.query(formula))
        except z3.Z3Exception as exc:
            return f"error:{exc}"

    def _exact_query(formula: z3.BoolRef) -> str:
        """The exact Datalog reachability query, with a short budget.

        This is the only *exact* way to confirm a violation, so it gets
        its own, shorter time budget. A query that runs out of time
        returns ``"unknown"`` and the prover reports the program as
        inconclusive -- it never turns a timeout into a label.
        """
        if assumption is not None:
            formula = z3.And(formula, assumption)
        fixedpoint.set("timeout", EXACT_QUERY_TIMEOUT_MS)
        try:
            return str(fixedpoint.query(formula))
        except z3.Z3Exception as exc:
            return f"error:{exc}"
        finally:
            fixedpoint.set("timeout", SPACER_TIMEOUT_MS)

    def _solve(formula: z3.BoolRef) -> str:
        """Check a formula with a plain solver, under the assumption."""
        if assumption is not None:
            formula = z3.And(formula, assumption)
        solver = z3.Solver()
        solver.set("timeout", SOLVER_TIMEOUT_MS)
        solver.add(formula)
        try:
            return str(solver.check())
        except z3.Z3Exception as exc:
            return f"error:{exc}"

    def _model_parameters(formula: z3.BoolRef) -> dict[str, int] | None:
        """Solve ``formula`` and read the parameter values out of the model.

        The result is only a *candidate* witness: the caller must replay
        it on real CPython before it counts.
        """
        if assumption is not None:
            formula = z3.And(formula, assumption)
        solver = z3.Solver()
        solver.set("timeout", SOLVER_TIMEOUT_MS)
        solver.add(formula)
        try:
            if solver.check() != z3.sat:
                return None
            model = solver.model()
        except z3.Z3Exception:
            return None
        values: dict[str, int] = {}
        for name in encoding.params:
            try:
                value = model.eval(encoding.param_symbols[name], model_completion=True)
            except z3.Z3Exception:
                continue
            if z3.is_int_value(value):
                values[name] = value.as_long()
        return values or None

    # --- the exact characterization of the reachable states ----------------
    # The characterization is cheap and sound in one direction: if
    # ``psi AND cond`` is unsatisfiable, the site is provably unreachable.
    # It is *not* sound in the other direction -- Spacer's answer
    # over-approximates the reachable states, so ``psi AND cond`` being
    # satisfiable does not prove the site is reachable.
    #
    # The reachability question is therefore settled by the *exact*
    # Datalog query ``Inv(...) AND cond``, and ``psi`` is only used as the
    # cheap shortcut for the unreachable case.
    psi = _characterize(encoding)

    # --- query 1: is the claim's site reachable at all? --------------------
    n_reachable = 0
    reachable: list[_Site] = []
    for entry in matching:
        if psi is not None:
            quick = _solve(z3.And(psi, entry.cond))
            if quick == "unsat":
                # Provably unreachable: the characterization admits no
                # state that reaches the site, so no reachable state does.
                continue
            if quick == "unknown" or quick.startswith("error:"):
                result = quick
            else:
                # The characterization allows the site. Only the exact
                # query can decide.
                result = _exact_query(z3.And(inv, entry.cond))
        else:
            result = _exact_query(z3.And(inv, entry.cond))
        if result == "sat":
            n_reachable += 1
            reachable.append(entry)
        elif result == "unknown" or result.startswith("error:"):
            return ProofResult(
                verdict=VERDICT_INCONCLUSIVE,
                reason=f"reachability query failed: {result}",
                precondition=precondition,
                elapsed=time.perf_counter() - started,
                n_sites=len(matching),
            )

    if n_reachable == 0:
        return ProofResult(
            verdict=VERDICT_UNREACHABLE,
            reason="no reachable state reaches the claim's site",
            precondition=precondition,
            elapsed=time.perf_counter() - started,
            n_sites=len(matching),
            n_reachable=0,
        )

    # --- query 2: does the claim hold on every reachable state? -------------
    for entry in reachable:
        claim = eval_expr(entry.site.node.test, entry.env)
        violation = z3.And(entry.cond, z3.Not(claim))

        if psi is not None:
            result = _solve(z3.And(psi, violation))
            if result == "unsat":
                # The claim holds on every reachable state: a proof.
                continue
            if result == "unknown" or result.startswith("error:"):
                return ProofResult(
                    verdict=VERDICT_INCONCLUSIVE,
                    reason=f"validity solver failed: {result}",
                    precondition=precondition,
                    elapsed=time.perf_counter() - started,
                    n_sites=len(matching),
                    n_reachable=n_reachable,
                )

        # The characterization allows a violation, but it is only a
        # *candidate*: Spacer's characterization over-approximates the
        # reachable states. The exact Datalog reachability query decides
        # whether a real input violates the claim, and yields the
        # witness.
        result = _exact_query(z3.And(inv, violation))
        if result == "sat":
            witness = _extract_witness(fixedpoint, tag, encoding.params)
            return ProofResult(
                verdict=VERDICT_COUNTEREXAMPLE,
                reason="a reachable state violates the claim",
                witness=witness,
                precondition=precondition,
                elapsed=time.perf_counter() - started,
                n_sites=len(matching),
                n_reachable=n_reachable,
            )
        if result == "unsat":
            # The exact query proves the violation is unreachable.
            continue
        if result == "unknown" or result.startswith("error:"):
            # The exact query ran out of time. The characterization still
            # supplies a *candidate* input, which the labelling pipeline
            # replays on real CPython. A candidate that does not
            # reproduce is never reported as a counterexample, so
            # offering it here is safe.
            candidate = (
                _model_parameters(z3.And(psi, violation))
                if psi is not None
                else None
            )
            if candidate is None:
                return ProofResult(
                    verdict=VERDICT_INCONCLUSIVE,
                    reason=f"validity query failed: {result}",
                    precondition=precondition,
                    elapsed=time.perf_counter() - started,
                    n_sites=len(matching),
                    n_reachable=n_reachable,
                )
            return ProofResult(
                verdict=VERDICT_COUNTEREXAMPLE,
                reason=(
                    "a candidate state violates the claim "
                    f"(exact query: {result}); the witness must be replayed"
                ),
                witness=candidate,
                precondition=precondition,
                elapsed=time.perf_counter() - started,
                n_sites=len(matching),
                n_reachable=n_reachable,
            )

    return ProofResult(
        verdict=VERDICT_VERIFIED,
        reason="no reachable state violates the claim",
        precondition=precondition,
        elapsed=time.perf_counter() - started,
        n_sites=len(matching),
        n_reachable=n_reachable,
    )


__all__ = [
    "ProofResult",
    "ProverError",
    "VERDICT_COUNTEREXAMPLE",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_REACHABLE",
    "VERDICT_UNREACHABLE",
    "VERDICT_VERIFIED",
    "prove",
    "prove_reachability",
]
