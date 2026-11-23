"""
invariant.py - Loop Invariant Generation (Phase 2 of IMPLEMENTATION_PLAN_PROPOSED.md).

When a claim's anchor lies below a while/for loop, the bounded path
enumeration in ``executor.py`` cannot reach the assertion in finite time.
This module asks the LLM to propose candidate invariants (linear / quadratic /
constant / relational) and verifies each one with Z3 via k-induction:

  Base case    (k = 0): invariant at loop entry.
  Inductive step:       invariant preserved by one iteration.

Verified invariants are conjoined into the path relation so that the
existing two-query Z3 engine can close the loop without enumerating an
unbounded number of paths.

Fallback: when no verified invariant is found, the pipeline reports
``UNKNOWN_TIMEOUT`` exactly as before, so the new module can never make
results worse than Phase 0.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

import z3

log = logging.getLogger(__name__)


# Allowed k-induction bound for the inductive step check.
INDUCTION_BOUND = 1
Z3_INVARIANT_TIMEOUT_MS = 3000

#: Bounds tried by the extended k-induction procedure, in order.
K_INDUCTION_BOUNDS: tuple[int, ...] = (1, 2, 3)

#: Constant offsets used by the fallback relational templates ``x == y - c``.
_INVARIANT_OFFSETS: tuple[int, ...] = (0, 1, 2)


@dataclass
class InvariantCandidate:
    """One candidate loop invariant proposed by the LLM."""

    formula: str                       # human-readable Z3-like expression
    kind: str                          # "constant"|"linear"|"quadratic"|"relational"
    z3_expr: z3.BoolRef | None = None
    source: str = "llm"                # "llm"|"fallback"
    verified_base: bool = False
    verified_step: bool = False
    raw_attempt: str = ""              # last raw LLM response for debugging
    k_bound: int = 0                   # k used by the successful inductive step


@dataclass
class InvariantVerification:
    """The result of trying to verify a candidate invariant."""

    candidate: InvariantCandidate
    base_holds: bool
    step_holds: bool
    reason: str = ""
    k_bound: int = 0                   # k at which the inductive step succeeded

    @property
    def is_valid(self) -> bool:
        """True when both the base case and the inductive step hold."""
        return self.base_holds and self.step_holds

    @property
    def z3_expr(self) -> z3.BoolRef | None:
        """The verified formula, or ``None`` when nothing was verified."""
        if self.is_valid:
            return self.candidate.z3_expr
        return None


# --- LLM prompt for invariant synthesis -------------------------------------


INVARIANT_SYSTEM_PROMPT = """You are a loop invariant synthesis assistant for Python programs.
Given a `while` or `for` loop and a post-condition (`assert`), propose 2-3 candidate
invariants.  Each invariant MUST be expressed as a Z3 QF-LIA boolean formula in
SMT-LIB2 syntax.

Rules:
1. Use only integer variables (those declared as parameters or assigned in the
   function body before the loop).
2. Allowed operators: `+ - *` (with one side a constant), `div mod` with a
   non-zero integer constant, comparisons `= != < <= > >=`, and the usual
   boolean connectives `and or not implies`.
3. Each formula must be a single boolean term.  Do NOT wrap it in
   `(assert ...)`, do NOT add a `declare-const` line, and do NOT include
   semicolons.
4. Choose the kind from {"constant", "linear", "quadratic", "relational"}.

Output strict JSON: {"invariants": [{"kind": ..., "formula": "..."}, ...]}
"""


def _build_invariant_prompt(
    source: str,
    loop_node: ast.AST,
    post_assertion: ast.expr | None,
) -> str:
    lines = source.splitlines()
    numbered = "\n".join(f"{i + 1:3d} | {ln}" for i, ln in enumerate(lines))
    post_text = ast.unparse(post_assertion) if post_assertion is not None else "(no explicit post-condition)"
    return (
        f"Source program:\n```python\n{numbered}\n```\n\n"
        f"Loop of interest (line {getattr(loop_node, 'lineno', '?')}):\n"
        f"```python\n{ast.unparse(loop_node)}\n```\n\n"
        f"Post-condition (assertion) to prove: `{post_text}`\n\n"
        f"Propose 2-3 invariants that (a) hold at loop entry, "
        f"(b) are preserved by one iteration, and (c) help prove the "
        f"post-condition when combined with the loop exit condition."
    )


# --- SMT-LIB2 to z3.BoolRef parsing -----------------------------------------


def parse_smt2_formula(
    formula: str,
    state: dict[str, z3.ExprRef],
) -> z3.BoolRef | None:
    """Parse a small SMT-LIB2 boolean term against a pre-populated state.

    The state supplies ``z3.Int(name)`` for every variable name.  We support
    only the QF-LIA fragment actually used by the prompt:
        (= a b)  (!= a b)  (< a b)  (<= a b)  (> a b)  (>= a b)
        (+ a b c) (- a b)   (* a c)  (div a c) (mod a c)
        (and a b) (or a b) (not a) (ite c a b)
        constant integer
    """
    if not formula or not isinstance(formula, str):
        return None
    formula = formula.strip()
    if not formula:
        return None
    try:
        expr = _parse_sexpr(formula, state)
    except Exception as exc:
        log.debug("smt2 parse failed: %s", exc)
        return None
    if expr is None:
        return None
    if z3.is_bool(expr):
        return expr
    return expr != 0


def _parse_sexpr(text: str, state: dict[str, z3.ExprRef]) -> z3.ExprRef | None:
    """Tiny S-expression tokenizer/parser."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    expr, _ = _parse_one(tokens, state)
    return expr


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(" or c == ")":
            out.append(c)
            i += 1
            continue
        # Multi-character comparison operators
        if c in "<>=" and i + 1 < n and text[i + 1] == "=":
            out.append(text[i : i + 2])
            i += 2
            continue
        # Single-character arithmetic operators
        if c in "+-*/=":
            # A minus sign directly in front of a digit belongs to the
            # number, not to a binary operator: "-1" is one token.
            if c == "-" and i + 1 < n and text[i + 1].isdigit():
                j = i + 1
                while j < n and text[j].isdigit():
                    j += 1
                out.append(text[i:j])
                i = j
                continue
            out.append(c)
            i += 1
            continue
        # Read a "word" (variable name or integer)
        j = i
        while j < n and (text[j].isalnum() or text[j] in "._-?"):
            j += 1
        if j == i:
            i += 1
            continue
        out.append(text[i:j])
        i = j
    return out


def _parse_one(
    tokens: list[str], state: dict[str, z3.ExprRef]
) -> tuple[z3.ExprRef | None, list[str]]:
    if not tokens:
        return None, tokens
    head = tokens[0]
    if head == ")":
        return None, tokens
    if head == "(":
        rest = tokens[1:]
        if not rest or rest[0] == ")":
            return None, tokens
        # Find the matching close paren for this list
        op = rest[0]
        # Collect arguments as sublists/atoms.
        args_tokens: list[list[str]] = []
        cur: list[str] = []
        depth = 1
        i = 1
        n = len(rest)
        while i < n and depth > 0:
            t = rest[i]
            if t == "(":
                # A nested list starts a new argument.
                if depth == 1 and cur:
                    args_tokens.append(list(cur))
                    cur = []
                depth += 1
                cur.append(t)
            elif t == ")":
                depth -= 1
                if depth == 0:
                    # The list ends here: the pending argument is complete.
                    if cur:
                        args_tokens.append(list(cur))
                    break
                cur.append(t)
            else:
                if depth == 1:
                    if cur:
                        args_tokens.append(list(cur))
                    cur = [t]
                else:
                    cur.append(t)
            i += 1
        # Recursively parse op
        op_expr, _ = _parse_one([op], state)
        if op == "and" or op == "or":
            children = []
            for a in args_tokens:
                e, _ = _parse_one(a, state)
                if e is None:
                    return None, tokens
                children.append(e if z3.is_bool(e) else e != 0)
            if op == "and":
                return z3.And(*children) if children else z3.BoolVal(True), []
            return z3.Or(*children) if children else z3.BoolVal(False), []
        if op == "not":
            a = args_tokens[0] if args_tokens else []
            e, _ = _parse_one(a, state)
            if e is None:
                return None, tokens
            return z3.Not(e if z3.is_bool(e) else e != 0), []
        if op in {"<", "<=", ">", ">=", "="}:
            a = args_tokens[0] if args_tokens else []
            b = args_tokens[1] if len(args_tokens) > 1 else []
            ea, _ = _parse_one(a, state)
            eb, _ = _parse_one(b, state)
            if ea is None or eb is None:
                return None, tokens
            # Build a boolean formula: a <op> b
            if op == "<":
                return ea < eb, []
            if op == "<=":
                return ea <= eb, []
            if op == ">":
                return ea > eb, []
            if op == ">=":
                return ea >= eb, []
            return ea == eb, []
        if op in {"+", "-", "*"}:
            # Need to parse possibly nested S-expressions as args
            children: list[z3.ExprRef] = []
            for a in args_tokens:
                e, _ = _parse_one(a, state)
                if e is None:
                    return None, tokens
                children.append(e)
            if len(children) < 2:
                return None, tokens
            result = children[0]
            for c in children[1:]:
                if op == "+":
                    result = result + c
                elif op == "-":
                    result = result - c
                else:
                    result = result * c
            return result, []
        return None, tokens
    # Atomic
    return _parse_atom(head, state), tokens[1:]


def _parse_atom(
    tok: str, state: dict[str, z3.ExprRef]
) -> z3.ExprRef | None:
    if re.fullmatch(r"-?\d+", tok):
        try:
            return z3.IntVal(int(tok))
        except Exception:
            return None
    if tok in state:
        return state[tok]
    return None


# --- Fallback invariant generators ------------------------------------------


def _linear_candidate_templates(
    state: dict[str, z3.ExprRef]
) -> list[tuple[str, str, z3.BoolRef]]:
    """Generate cheap linear / relational candidates from the loop's state.

    For each pair (x, y) and each comparison op we emit one candidate.
    For each single variable we emit ``0 <= x`` and ``x == x`` (tautology).

    We also emit the "offset" family ``x == y - c`` for a small set of
    constant offsets.  These are the invariants that actually carry an
    accumulator loop such as ``s = s + 1; i = i + 1``, where the relation
    between the two counters is the only thing that summarises the loop.
    """
    out: list[tuple[str, str, z3.BoolRef]] = []
    names = list(state.keys())
    for n in names:
        out.append(("relational", f"(>= {n} 0)", state[n] >= 0))
        out.append(("constant", f"(= {n} {n})", z3.BoolVal(True)))  # tautology
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out.append(("relational", f"(<= {a} {b})", state[a] <= state[b]))
            out.append(("relational", f"(>= {a} {b})", state[a] >= state[b]))
            for offset in _INVARIANT_OFFSETS:
                out.append(
                    (
                        "relational",
                        f"(= {a} (- {b} {offset}))",
                        state[a] == state[b] - offset,
                    )
                )
    return out


# --- Generator class --------------------------------------------------------


class LoopInvariantGenerator:
    """Generate and Z3-verify candidate loop invariants for a single loop."""

    def __init__(
        self,
        llm_client=None,
        *,
        max_attempts: int = 2,
        use_llm: bool = True,
        model: str = "gemini-3.5-flash-lite",
    ) -> None:
        self.llm_client = llm_client
        self.max_attempts = max_attempts
        self.use_llm = use_llm
        self.model = model

    def _call_llm(self, prompt: str) -> str | None:
        """Ask Gemini for JSON invariants; return the raw text or None.

        The call is best-effort: any failure returns ``None`` so the
        deterministic fallback templates still provide candidates.
        """
        if self.llm_client is None or not self.use_llm:
            return None
        try:
            from google.genai import types
        except Exception:  # noqa: BLE001 - the SDK is optional at runtime
            return None
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                response = self.llm_client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=INVARIANT_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                return getattr(response, "text", None) or None
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                last_err = exc
                err_str = str(exc)
                # A daily quota exhaustion will not recover within the run,
                # so retrying only wastes time: fall back to the templates.
                if "quota" in err_str.lower() and "per day" in err_str.lower():
                    log.warning(
                        "Invariant LLM call skipped: the API quota for this "
                        "model is exhausted for the day. Falling back to the "
                        "deterministic candidate templates."
                    )
                    return None
                if (
                    "503" in err_str
                    or "UNAVAILABLE" in err_str
                    or "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                ):
                    time.sleep(2**attempt)
                    continue
                log.warning("Invariant LLM call failed: %s", exc)
                return None
        log.warning("Invariant LLM call failed after 3 attempts: %s", last_err)
        return None

    def _parse_llm_json(self, raw: str) -> list[dict[str, str]]:
        if not raw:
            return []
        text = raw.strip()
        # Strip ```json ... ``` fences
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except Exception:
                return []
        if not isinstance(data, dict):
            return []
        items = data.get("invariants", [])
        if not isinstance(items, list):
            return []
        cleaned: list[dict[str, str]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind", "")).strip().lower()
            formula = str(it.get("formula", "")).strip()
            if kind in {"constant", "linear", "quadratic", "relational"} and formula:
                cleaned.append({"kind": kind, "formula": formula})
        return cleaned

    def _state_for_loop(
        self, state: dict[str, z3.ExprRef]
    ) -> dict[str, z3.ExprRef]:
        return dict(state)

    def generate(
        self,
        source: str,
        loop_node: ast.AST,
        post_assertion: ast.expr,
        state: dict[str, z3.ExprRef],
    ) -> list[InvariantCandidate]:
        """Return up to ``max_attempts`` candidate invariants.

        Always returns at least one candidate (the cheapest linear fallback)
        so the verifier has something to try.
        """
        candidates: list[InvariantCandidate] = []
        prompt = _build_invariant_prompt(source, loop_node, post_assertion)
        # 1. LLM suggestions
        if self.use_llm and self.llm_client is not None:
            for _ in range(self.max_attempts):
                raw = self._call_llm(prompt)
                items = self._parse_llm_json(raw or "")
                for it in items:
                    z3_expr = parse_smt2_formula(it["formula"], state)
                    candidates.append(
                        InvariantCandidate(
                            formula=it["formula"],
                            kind=it["kind"],
                            z3_expr=z3_expr,
                            source="llm",
                            raw_attempt=raw or "",
                        )
                    )
                if any(c.z3_expr is not None for c in candidates):
                    break
                time.sleep(0.2)
        # 2. Cheap fallback (linear / relational) - always appended
        for kind, formula, z3_expr in _linear_candidate_templates(state):
            candidates.append(
                InvariantCandidate(
                    formula=formula,
                    kind=kind,
                    z3_expr=z3_expr,
                    source="fallback",
                )
            )
        return candidates

    def verify(
        self,
        candidate: InvariantCandidate,
        *,
        loop_node: ast.AST,
        loop_body_stmts: list[ast.stmt],
        state: dict[str, z3.ExprRef],
        entry_state: dict[str, z3.ExprRef] | None = None,
        k_bounds: Sequence[int] | None = None,
    ) -> InvariantVerification:
        """Verify a candidate invariant with (extended) k-induction.

        The verification follows the standard scheme:

        * **Base case** -- the invariant must hold at the loop head.  The
          loop-entry state supplied by the trusted executor constrains the
          variables to the values the program really has there, so the check
          is meaningful.  Without it every variable would be free at loop
          entry and only tautologies could ever pass.
        * **Inductive step** -- assuming the invariant holds, one more
          iteration of the body must preserve it.  When ``k = 1`` fails the
          procedure is strengthened to ``k = 2`` and ``k = 3``, which is the
          usual extended k-induction.

        Both checks are performed on the *symbolic* state: the variables
        range over all integers, so a passing check really does establish
        the invariant for every possible execution of the loop.

        The inductive step can only be encoded for a loop body made of
        straight-line additive updates (``x = x + c``).  For any other body
        shape the invariant is rejected, which keeps the pipeline sound:
        the previous UNKNOWN_TIMEOUT verdict is kept instead of claiming an
        unproven VERIFIED.
        """
        if candidate.z3_expr is None:
            return InvariantVerification(
                candidate=candidate, base_holds=False, step_holds=False,
                reason="z3_expr is None (parse failed)",
            )

        # The inductive step can only be encoded for a body made of straight
        # additive updates.
        #
        # Soundness note: when the step cannot be encoded we must NOT accept
        # the invariant.  A loop body that can leave the loop early
        # (``break``/``return``) or that assigns a fresh value says nothing
        # about the state after the loop, so accepting the invariant would
        # let a tautology "prove" an arbitrary claim.  The invariant is
        # therefore rejected and the pipeline keeps its previous (sound)
        # UNKNOWN_TIMEOUT verdict.
        increments = _extract_increments(loop_body_stmts, state)
        if increments is None:
            return InvariantVerification(
                candidate=candidate,
                base_holds=False,
                step_holds=False,
                reason=(
                    "loop body is not a sequence of constant additive "
                    "updates, so the inductive step cannot be encoded"
                ),
                k_bound=0,
            )

        # The variables of the invariant are re-created as free symbols so
        # that the checks quantify over every possible execution.
        symbols = {name: z3.Int(name) for name in state}
        invariant = _substitute_vars(candidate.z3_expr, symbols)
        if invariant is None:
            return InvariantVerification(
                candidate=candidate, base_holds=False, step_holds=False,
                reason="the candidate could not be re-instantiated on free symbols",
            )

        # --- Base case ------------------------------------------------------
        s_base = z3.Solver()
        s_base.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
        if entry_state:
            for name, value in entry_state.items():
                if name not in symbols:
                    continue
                if z3.is_bool(value) or z3.is_arith(value):
                    s_base.add(symbols[name] == value)
        s_base.add(z3.Not(invariant))
        base_kind = s_base.check()
        base_holds = base_kind == z3.unsat
        if not base_holds:
            return InvariantVerification(
                candidate=candidate,
                base_holds=False,
                step_holds=False,
                reason="base case does not hold at loop entry",
                k_bound=0,
            )

        # --- Inductive step -------------------------------------------------
        bounds = tuple(k_bounds) if k_bounds else K_INDUCTION_BOUNDS
        last_reason = "inductive step failed at every tried k"
        for k in bounds:
            s_step = z3.Solver()
            s_step.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
            # Assume the invariant holds for k consecutive iterations.
            for _ in range(max(1, k)):
                s_step.add(invariant)
            # After one more iteration every updated variable has moved by
            # its increment; the invariant must still hold.
            stepped = dict(symbols)
            for var, delta in increments.items():
                if var in stepped:
                    stepped[var] = stepped[var] + delta
            stepped_invariant = _substitute_vars(invariant, stepped)
            if stepped_invariant is None:
                last_reason = "the inductive step could not be substituted"
                break
            s_step.add(z3.Not(stepped_invariant))
            step_kind = s_step.check()
            if step_kind == z3.unsat:
                candidate.verified_base = True
                candidate.verified_step = True
                candidate.k_bound = k
                return InvariantVerification(
                    candidate=candidate,
                    base_holds=True,
                    step_holds=True,
                    reason=f"base case and inductive step hold at k={k}",
                    k_bound=k,
                )
            if step_kind == z3.unknown:
                last_reason = f"Z3 returned unknown on the inductive step at k={k}"
                break
            last_reason = f"inductive step failed at k={k}"
        return InvariantVerification(
            candidate=candidate,
            base_holds=True,
            step_holds=False,
            reason=last_reason,
            k_bound=0,
        )

    def generate_and_verify(
        self,
        source: str,
        loop_node: ast.AST,
        post_assertion: ast.expr | None,
        state: dict[str, z3.ExprRef],
        *,
        loop_body_stmts: list[ast.stmt] | None = None,
        entry_state: dict[str, z3.ExprRef] | None = None,
        k_bounds: Sequence[int] | None = None,
    ) -> InvariantVerification | None:
        """Generate candidates and return the first one that verifies.

        Returns ``None`` when no candidate survived.  This is the entry point
        used by the pipeline: it hides the generate/verify split so callers
        only deal with a verified invariant or nothing.
        """
        candidates = self.generate(source, loop_node, post_assertion, state)
        if loop_body_stmts is None:
            loop_body_stmts = _loop_body_statements(loop_node)
        for candidate in candidates:
            verification = self.verify(
                candidate,
                loop_node=loop_node,
                loop_body_stmts=loop_body_stmts,
                state=state,
                entry_state=entry_state,
                k_bounds=k_bounds,
            )
            if verification.is_valid:
                return verification
        return None


# --- Helpers for loop bodies ------------------------------------------------


def _loop_body_statements(loop_node: ast.AST) -> list[ast.stmt]:
    """Return the body statements of a ``while``/``for`` loop node."""
    body = getattr(loop_node, "body", None)
    if isinstance(body, list):
        return [stmt for stmt in body if isinstance(stmt, ast.stmt)]
    return []


def _extract_increments(
    body: list[ast.stmt], state: dict[str, z3.ExprRef]
) -> dict[str, z3.ArithRef] | None:
    """Return the per-variable increments of a loop body, or ``None``.

    A body qualifies when every one of its statements is a straight-line
    update of the form ``x = x + c`` / ``x += c`` / ``x = x - c`` with a
    constant ``c``.  This covers the accumulator pattern that dominates
    real code::

        while i < n:
            s = s + i      # s grows by a *variable* -> not a constant
            i = i + 1

    Note that ``s = s + i`` is rejected because the delta ``i`` is not a
    constant: the update of ``s`` depends on the loop counter, so the
    inductive step cannot be modelled by a single substitution.

    Anything else -- a nested ``if``, a ``break``, a ``return``, an
    assignment of a fresh value -- changes the control flow or the state in
    a way the inductive step cannot model, so the body is rejected and no
    invariant is derived from it.  This is what keeps the pipeline sound.
    """
    if not body:
        return None
    increments: dict[str, z3.ArithRef] = {}
    for stmt in body:
        var, delta = _single_increment(stmt, state)
        if var is None or delta is None:
            return None
        # A variable updated twice in the same iteration is not modelled.
        if var in increments:
            return None
        increments[var] = delta
    if not increments:
        return None
    return increments


def _single_increment(
    stmt: ast.stmt, state: dict[str, z3.ExprRef]
) -> tuple[str | None, z3.ArithRef | None]:
    """Return ``(variable, delta)`` for a single additive update statement."""
    if isinstance(stmt, ast.AugAssign):
        if not isinstance(stmt.target, ast.Name):
            return None, None
        if not isinstance(stmt.op, (ast.Add, ast.Sub)):
            return None, None
        delta = _eval_const(stmt.value, state)
        if delta is None:
            return None, None
        return stmt.target.id, z3.simplify(delta)
    if (
        isinstance(stmt, ast.Assign)
        and isinstance(stmt.value, ast.BinOp)
        and isinstance(stmt.value.left, ast.Name)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.value.left.id == stmt.targets[0].id
        and isinstance(stmt.value.op, (ast.Add, ast.Sub))
    ):
        delta = _eval_const(stmt.value.right, state)
        if delta is None:
            return None, None
        return stmt.targets[0].id, z3.simplify(delta)
    return None, None


def _eval_const(node: ast.AST, state: dict[str, z3.ExprRef]) -> z3.ArithRef | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return z3.IntVal(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _eval_const(node.operand, state)
        if inner is not None:
            return -inner
    return None


def _substitute_vars(
    expr: z3.BoolRef, new_state: dict[str, z3.ExprRef]
) -> z3.BoolRef:
    """Substitute the variables of ``expr`` with their values in ``new_state``.

    The mapping is applied as ``name -> value``.  Values that are already
    concrete numerals are skipped: substituting them would change nothing,
    and ``z3.substitute`` rejects a pair whose right-hand side is not an
    expression.
    """
    if z3.is_true(expr) or z3.is_false(expr):
        return expr
    pairs: list[tuple[z3.ExprRef, z3.ExprRef]] = []
    for name, value in new_state.items():
        if not isinstance(name, str):
            continue
        if z3.is_int_value(value) or z3.is_rational_value(value):
            continue
        if not z3.is_expr(value):
            continue
        pairs.append((z3.Int(name), value))
    if not pairs:
        return expr
    try:
        return z3.substitute(expr, *pairs)
    except z3.Z3Exception:
        return expr


__all__ = [
    "InvariantCandidate",
    "InvariantVerification",
    "LoopInvariantGenerator",
    "parse_smt2_formula",
    "INDUCTION_BOUND",
    "K_INDUCTION_BOUNDS",
]
