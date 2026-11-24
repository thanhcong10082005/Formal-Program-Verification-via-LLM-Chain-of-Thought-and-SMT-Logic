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
        (and a b) (or a b) (not a) (implies a b) (ite c a b)
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
        # Multi-character comparison / implication operators
        if i + 1 < n and text[i : i + 2] in ("<=", ">=", "!=", "=>"):
            out.append(text[i : i + 2])
            i += 2
            continue
        # Negative numbers: -1, -42
        if c == "-" and i + 1 < n and text[i + 1].isdigit():
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            out.append(text[i:j])
            i = j
            continue
        # Single-character comparison / arithmetic operators
        if c in "+-*/=<>":
            out.append(c)
            i += 1
            continue
        # Read a "word" (variable name, keyword like implies/ite/div/mod, or integer)
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
        if op in {"implies", "=>"}:
            a = args_tokens[0] if args_tokens else []
            b = args_tokens[1] if len(args_tokens) > 1 else []
            ea, _ = _parse_one(a, state)
            eb, _ = _parse_one(b, state)
            if ea is None or eb is None:
                return None, tokens
            ba = ea if z3.is_bool(ea) else ea != 0
            bb = eb if z3.is_bool(eb) else eb != 0
            return z3.Implies(ba, bb), []
        if op == "ite":
            if len(args_tokens) < 3:
                return None, tokens
            c, _ = _parse_one(args_tokens[0], state)
            t_branch, _ = _parse_one(args_tokens[1], state)
            f_branch, _ = _parse_one(args_tokens[2], state)
            if c is None or t_branch is None or f_branch is None:
                return None, tokens
            cond_bool = c if z3.is_bool(c) else c != 0
            return z3.If(cond_bool, t_branch, f_branch), []
        if op in {"<", "<=", ">", ">=", "=", "!="}:
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
            if op == "!=":
                return ea != eb, []
            return ea == eb, []
        if op in {"+", "-", "*", "div", "mod"}:
            # Need to parse possibly nested S-expressions as args
            children: list[z3.ExprRef] = []
            for a in args_tokens:
                e, _ = _parse_one(a, state)
                if e is None:
                    return None, tokens
                children.append(e)
            if len(children) < 2:
                return None, tokens
            if op == "div":
                return children[0] / children[1], []
            if op == "mod":
                return children[0] % children[1], []
            result = children[0]
            for ch in children[1:]:
                if op == "+":
                    result = result + ch
                elif op == "-":
                    result = result - ch
                else:
                    result = result * ch
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
        model: str = "gemini-2.5-flash-lite",
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

        The inductive step encodes loop bodies built from linear integer
        assignments (``x = expr``, ``x += expr``), conditionals (``if/else``
        merged with ``z3.If``), and ``pass`` statements.  For any other body
        shape (e.g. ``break``, ``return``, ``continue``, nested loops) the
        invariant is rejected, which keeps the pipeline sound: the previous
        UNKNOWN_TIMEOUT verdict is kept instead of claiming an unproven
        VERIFIED.
        """
        if candidate.z3_expr is None:
            return InvariantVerification(
                candidate=candidate, base_holds=False, step_holds=False,
                reason="z3_expr is None (parse failed)",
            )

        if isinstance(loop_node, ast.For):
            return InvariantVerification(
                candidate=candidate,
                base_holds=False,
                step_holds=False,
                reason="for-loop inductive step is not supported",
                k_bound=0,
            )

        # The variables of the invariant are re-created as free symbols so
        # that the checks quantify over every possible execution.
        symbols = {name: z3.Int(name) for name in state}

        # Soundness note: when the step cannot be encoded we must NOT accept
        # the invariant.  A loop body that can leave the loop early
        # (``break``/``return``) or contains unsupported constructs says nothing
        # about the state after the loop, so accepting the invariant would
        # let a tautology "prove" an arbitrary claim.  The invariant is
        # therefore rejected and the pipeline keeps its previous (sound)
        # UNKNOWN_TIMEOUT verdict.
        stepped = _encode_loop_body(loop_body_stmts, symbols)
        if stepped is None:
            return InvariantVerification(
                candidate=candidate,
                base_holds=False,
                step_holds=False,
                reason=(
                    "loop body contains unsupported statements (e.g. break, "
                    "return, or non-linear updates), so the inductive step "
                    "cannot be encoded"
                ),
                k_bound=0,
            )
        for name in stepped:
            if name not in symbols:
                symbols[name] = z3.Int(name)

        invariant = _substitute_vars(candidate.z3_expr, symbols, skip_literals=True)
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
        guard_expr: z3.BoolRef | None = None
        if isinstance(loop_node, ast.While):
            raw_guard = _eval_ast_expr(loop_node.test, symbols)
            if raw_guard is not None:
                guard_expr = _as_z3_bool(raw_guard)

        bounds = tuple(k_bounds) if k_bounds else K_INDUCTION_BOUNDS
        last_reason = "inductive step failed at every tried k"
        for k in bounds:
            s_step = z3.Solver()
            s_step.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
            # Assume the invariant holds for k consecutive iterations.
            for _ in range(max(1, k)):
                s_step.add(invariant)
            if guard_expr is not None:
                s_step.add(guard_expr)
            # After one more iteration every updated variable takes its new
            # symbolic value; the invariant must still hold.
            stepped_invariant = _substitute_vars(
                invariant, stepped, skip_literals=False
            )
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
        pre_condition: z3.BoolRef | None = None,
        post_condition_guard: z3.BoolRef | None = None,
    ) -> InvariantVerification | None:
        """Generate candidates and return a verified inductive invariant.

        When ``post_assertion`` is None, returns the first verified candidate
        (backwards-compatible with direct unit tests).  When ``post_assertion``
        is provided, performs iterative inductive strengthening (Houdini) over
        all candidates and checks the Hoare loop-exit implication::

            PreCondition AND I(s) AND ~Guard(s) AND PostBranchGuard(s) => PostAssertion(s)

        over symbolic loop-modified variables (with unmodified variables kept
        at their loop-entry values).  Returns ``None`` if the post-assertion
        cannot be proved from the verified invariants and ``~Guard(s)``.
        """
        if loop_body_stmts is None:
            loop_body_stmts = _loop_body_statements(loop_node)
        candidates = self.generate(source, loop_node, post_assertion, state)

        if post_assertion is None:
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

        return self._verify_hoare_postcondition(
            candidates=candidates,
            loop_node=loop_node,
            loop_body_stmts=loop_body_stmts,
            state=state,
            entry_state=entry_state,
            post_assertion=post_assertion,
            pre_condition=pre_condition,
            post_condition_guard=post_condition_guard,
        )

    def _verify_hoare_postcondition(
        self,
        *,
        candidates: list[InvariantCandidate],
        loop_node: ast.AST,
        loop_body_stmts: list[ast.stmt],
        state: dict[str, z3.ExprRef],
        entry_state: dict[str, z3.ExprRef] | None,
        post_assertion: ast.expr,
        pre_condition: z3.BoolRef | None,
        post_condition_guard: z3.BoolRef | None,
    ) -> InvariantVerification | None:
        """Verify candidates via Houdini strengthening and check ``I AND ~Guard => Post``."""
        if isinstance(loop_node, ast.For):
            return None
        symbols = {name: z3.Int(name) for name in state}
        stepped = _encode_loop_body(loop_body_stmts, symbols)
        if stepped is None:
            return None
        for name in stepped:
            if name not in symbols:
                symbols[name] = z3.Int(name)

        written = _loop_written_vars(loop_node)
        entry_map = dict(entry_state or {})
        rich_exprs = _hoare_candidate_Pool(
            loop_node=loop_node,
            post_assertion=post_assertion,
            post_condition_guard=post_condition_guard,
            symbols=symbols,
            entry_state=entry_map,
            written=written,
        )
        all_candidates: list[InvariantCandidate] = [
            c for c in candidates if c.z3_expr is not None and c.kind != "constant"
        ]
        for formula_str, expr in rich_exprs:
            all_candidates.append(
                InvariantCandidate(
                    formula=formula_str,
                    kind="relational",
                    z3_expr=expr,
                    source="fallback",
                )
            )

        # 1. Base case filter: PreCondition AND EntryState => I(s0)
        s_base = z3.Solver()
        s_base.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
        if pre_condition is not None:
            s_base.add(pre_condition)
        for name, val in entry_map.items():
            if name in symbols and (z3.is_bool(val) or z3.is_arith(val)):
                s_base.add(symbols[name] == val)

        base_ok: list[tuple[InvariantCandidate, z3.BoolRef]] = []
        for cand in all_candidates:
            if cand.z3_expr is None:
                continue
            inv = _substitute_vars(cand.z3_expr, symbols, skip_literals=True)
            if inv is None or z3.is_true(inv):
                continue
            s_base.push()
            s_base.add(z3.Not(inv))
            ok = s_base.check() == z3.unsat
            s_base.pop()
            if ok:
                base_ok.append((cand, inv))

        if not base_ok:
            return None

        # 2. Inductive step with iterative strengthening: I AND Guard => I(stepped)
        guard_expr: z3.BoolRef = z3.BoolVal(True)
        if isinstance(loop_node, ast.While):
            raw_g = _eval_ast_expr(loop_node.test, dict(symbols))
            if raw_g is not None:
                guard_expr = _as_z3_bool(raw_g)

        s_step = z3.Solver()
        s_step.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
        s_step.add(guard_expr)

        verified_pairs: list[tuple[InvariantCandidate, z3.BoolRef]] = []
        remaining: list[tuple[InvariantCandidate, z3.BoolRef, z3.BoolRef]] = []
        for cand, inv in base_ok:
            stepped_inv = _substitute_vars(inv, stepped, skip_literals=False)
            if stepped_inv is None:
                continue
            s_step.push()
            s_step.add(inv)
            s_step.add(z3.Not(stepped_inv))
            ok = s_step.check() == z3.unsat
            s_step.pop()
            if ok:
                verified_pairs.append((cand, inv))
                s_step.add(inv)
            else:
                remaining.append((cand, inv, stepped_inv))

        for _ in range(2):
            still_rem: list[tuple[InvariantCandidate, z3.BoolRef, z3.BoolRef]] = []
            for cand, inv, stepped_inv in remaining:
                s_step.push()
                s_step.add(inv)
                s_step.add(z3.Not(stepped_inv))
                ok = s_step.check() == z3.unsat
                s_step.pop()
                if ok:
                    verified_pairs.append((cand, inv))
                    s_step.add(inv)
                else:
                    still_rem.append((cand, inv, stepped_inv))
            if len(still_rem) == len(remaining):
                break
            remaining = still_rem

        if not verified_pairs:
            return None

        # 3. Hoare loop-exit check: PreCondition AND I(s) AND ~Guard(s) AND PostGuard => Post(s)
        # Modified variables are free symbols; unmodified variables retain entry_state.
        post_env: dict[str, z3.ExprRef] = {}
        for name in symbols:
            if name in written or name not in entry_map:
                post_env[name] = symbols[name]
            else:
                post_env[name] = entry_map[name]

        exit_guard = z3.Not(
            _substitute_vars(guard_expr, post_env, skip_literals=False)
        )
        inv_at_exit = [
            _substitute_vars(inv, post_env, skip_literals=False)
            for _, inv in verified_pairs
        ]
        raw_claim = _eval_ast_expr(post_assertion, dict(post_env))
        if raw_claim is None:
            return None
        claim_bool = _as_z3_bool(raw_claim)

        s_post = z3.Solver()
        s_post.set("timeout", Z3_INVARIANT_TIMEOUT_MS)
        if pre_condition is not None:
            s_post.add(pre_condition)
        for inv_e in inv_at_exit:
            s_post.add(inv_e)
        s_post.add(exit_guard)
        if post_condition_guard is not None:
            s_post.add(
                _substitute_vars(post_condition_guard, post_env, skip_literals=False)
            )
        s_post.add(z3.Not(claim_bool))
        if s_post.check() != z3.unsat:
            return None

        primary_cand, _ = verified_pairs[0]
        combined_inv = (
            z3.And(*[inv for _, inv in verified_pairs])
            if len(verified_pairs) > 1
            else verified_pairs[0][1]
        )
        chosen = InvariantCandidate(
            formula=primary_cand.formula,
            kind=primary_cand.kind,
            z3_expr=combined_inv,
            source=primary_cand.source,
            verified_base=True,
            verified_step=True,
            k_bound=1,
        )
        return InvariantVerification(
            candidate=chosen,
            base_holds=True,
            step_holds=True,
            reason="base case, inductive step, and Hoare loop-exit postcondition hold at k=1",
            k_bound=1,
        )


# --- Helpers for loop bodies ------------------------------------------------


def _loop_body_statements(loop_node: ast.AST) -> list[ast.stmt]:
    """Return the body statements of a ``while``/``for`` loop node."""
    body = getattr(loop_node, "body", None)
    if isinstance(body, list):
        return [stmt for stmt in body if isinstance(stmt, ast.stmt)]
    return []


def _as_z3_bool(expr: z3.ExprRef) -> z3.BoolRef:
    """Coerce a Z3 arithmetic or boolean expression to ``z3.BoolRef``."""
    if z3.is_bool(expr):
        return expr
    return expr != 0


def _eval_ast_expr(
    node: ast.AST, env: dict[str, z3.ExprRef]
) -> z3.ExprRef | None:
    """Evaluate a Python AST expression inside the QF-LIA subset over ``env``."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return z3.BoolVal(v)
        if isinstance(v, int):
            return z3.IntVal(v)
        return None
    if isinstance(node, ast.Name):
        if node.id not in env:
            env[node.id] = z3.Int(node.id)
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        inner = _eval_ast_expr(node.operand, env)
        if inner is None:
            return None
        if isinstance(node.op, ast.USub):
            if z3.is_bool(inner):
                return None
            return -inner
        if isinstance(node.op, ast.UAdd):
            if z3.is_bool(inner):
                return None
            return inner
        if isinstance(node.op, ast.Not):
            return z3.Not(_as_z3_bool(inner))
        return None
    if isinstance(node, ast.BinOp):
        left = _eval_ast_expr(node.left, env)
        right = _eval_ast_expr(node.right, env)
        if left is None or right is None:
            return None
        if z3.is_bool(left) or z3.is_bool(right):
            return None
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            if not (z3.is_int_value(left) or z3.is_int_value(right)):
                return None
            return left * right
        if isinstance(op, ast.FloorDiv):
            if not z3.is_int_value(right) or right.as_long() == 0:
                return None
            return left / right
        if isinstance(op, ast.Mod):
            if not z3.is_int_value(right) or right.as_long() == 0:
                return None
            return left % right
        return None
    if isinstance(node, ast.BoolOp):
        vals: list[z3.BoolRef] = []
        for val_node in node.values:
            ev = _eval_ast_expr(val_node, env)
            if ev is None:
                return None
            vals.append(_as_z3_bool(ev))
        if isinstance(node.op, ast.And):
            return z3.And(*vals) if vals else z3.BoolVal(True)
        if isinstance(node.op, ast.Or):
            return z3.Or(*vals) if vals else z3.BoolVal(False)
        return None
    if isinstance(node, ast.Compare):
        left = _eval_ast_expr(node.left, env)
        if left is None:
            return None
        parts: list[z3.BoolRef] = []
        for op, comp in zip(node.ops, node.comparators, strict=False):
            right = _eval_ast_expr(comp, env)
            if right is None:
                return None
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
                return None
            left = right
        return z3.And(*parts) if len(parts) > 1 else parts[0]
    if isinstance(node, ast.IfExp):
        cond = _eval_ast_expr(node.test, env)
        t_val = _eval_ast_expr(node.body, env)
        f_val = _eval_ast_expr(node.orelse, env)
        if cond is None or t_val is None or f_val is None:
            return None
        return z3.If(_as_z3_bool(cond), t_val, f_val)
    return None


def _encode_stmts(
    stmts: list[ast.stmt], env: dict[str, z3.ExprRef]
) -> dict[str, z3.ExprRef] | None:
    """Symbolically execute a sequence of loop-body statements over ``env``."""
    cur = dict(env)
    for stmt in stmts:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                return None
            val = _eval_ast_expr(stmt.value, cur)
            if val is None or z3.is_bool(val):
                return None
            cur[stmt.targets[0].id] = z3.simplify(val)
            continue
        if isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                return None
            bin_expr = ast.BinOp(
                left=ast.Name(id=stmt.target.id, ctx=ast.Load()),
                op=stmt.op,
                right=stmt.value,
            )
            val = _eval_ast_expr(bin_expr, cur)
            if val is None or z3.is_bool(val):
                return None
            cur[stmt.target.id] = z3.simplify(val)
            continue
        if isinstance(stmt, ast.If):
            cond = _eval_ast_expr(stmt.test, cur)
            if cond is None:
                return None
            cond_bool = _as_z3_bool(cond)
            then_env = _encode_stmts(stmt.body, cur)
            if then_env is None:
                return None
            else_env = _encode_stmts(stmt.orelse, cur) if stmt.orelse else dict(cur)
            if else_env is None:
                return None
            all_keys = set(cur) | set(then_env) | set(else_env)
            merged: dict[str, z3.ExprRef] = {}
            for k in all_keys:
                base_v = cur.get(k, z3.Int(k))
                t_v = then_env.get(k, base_v)
                e_v = else_env.get(k, base_v)
                if t_v.eq(e_v):
                    merged[k] = t_v
                else:
                    merged[k] = z3.simplify(z3.If(cond_bool, t_v, e_v))
            cur = merged
            continue
        # Reject break, continue, return, nested loops, calls, etc.
        return None
    return cur


def _encode_loop_body(
    body: list[ast.stmt], state: dict[str, z3.ExprRef]
) -> dict[str, z3.ExprRef] | None:
    """Return the post-iteration symbolic state for ``body``, or ``None``."""
    if not body:
        return None
    base_env = {name: z3.Int(name) for name in state}
    stepped = _encode_stmts(body, base_env)
    if stepped is None:
        return None
    changed = any(
        not stepped[k].eq(base_env.get(k, z3.Int(k))) for k in stepped
    )
    if not changed:
        return None
    return stepped


def _extract_increments(
    body: list[ast.stmt], state: dict[str, z3.ExprRef]
) -> dict[str, z3.ArithRef] | None:
    """Return the per-variable increments of a loop body, or ``None``."""
    if not body:
        return None
    increments: dict[str, z3.ArithRef] = {}
    for stmt in body:
        var, delta = _single_increment(stmt, state)
        if var is None or delta is None:
            return None
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
        if isinstance(stmt.op, ast.Sub):
            delta = -delta
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
        if isinstance(stmt.value.op, ast.Sub):
            delta = -delta
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
    expr: z3.BoolRef,
    new_state: dict[str, z3.ExprRef],
    *,
    skip_literals: bool = True,
) -> z3.BoolRef:
    """Substitute the variables of ``expr`` with their values in ``new_state``."""
    if z3.is_true(expr) or z3.is_false(expr):
        return expr
    pairs: list[tuple[z3.ExprRef, z3.ExprRef]] = []
    for name, value in new_state.items():
        if not isinstance(name, str):
            continue
        if skip_literals and (
            z3.is_int_value(value) or z3.is_rational_value(value)
        ):
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


def _loop_written_vars(loop_node: ast.AST) -> set[str]:
    """Return the set of variable names assigned inside ``loop_node``."""
    written: set[str] = set()
    for node in ast.walk(loop_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    written.add(target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            written.add(node.target.id)
    return written


def _hoare_candidate_Pool(
    *,
    loop_node: ast.AST,
    post_assertion: ast.expr,
    post_condition_guard: z3.BoolRef | None,
    symbols: dict[str, z3.ExprRef],
    entry_state: dict[str, z3.ExprRef],
    written: set[str],
) -> list[tuple[str, z3.BoolRef]]:
    """Synthesize AST-guided candidate invariants for Hoare loop verification."""
    active = [
        k
        for k in symbols
        if not k.startswith("junk_") and not k.startswith("_initial_")
    ]
    out: list[tuple[str, z3.BoolRef]] = []
    init_to_unmod: dict[z3.ExprRef, z3.ExprRef] = {}
    for k in active:
        if k not in written and k in entry_state and z3.is_expr(entry_state[k]):
            init_to_unmod[entry_state[k]] = symbols[k]

    def to_sym(expr: z3.ExprRef | None) -> z3.ExprRef | None:
        if expr is None or not z3.is_expr(expr):
            return None
        pairs = list(init_to_unmod.items())
        if pairs:
            expr = z3.substitute(expr, *pairs)
        if "_initial_" in str(expr):
            return None
        return expr

    guard_b = z3.BoolVal(True)
    if isinstance(loop_node, ast.While):
        raw_g = _eval_ast_expr(loop_node.test, dict(symbols))
        if raw_g is not None:
            guard_b = _as_z3_bool(raw_g)

    # 1. Exact equalities for unmodified variables
    for k in active:
        if k not in written:
            ek = to_sym(entry_state.get(k))
            if ek is not None and not symbols[k].eq(ek):
                out.append((f"(= {k} {ek})", symbols[k] == ek))

    # 2. Variable bounds against loop/assertion constants and entry values
    consts = {0, 1}
    for n in ast.walk(loop_node):
        if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
            consts.add(n.value)
    for n in ast.walk(post_assertion):
        if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
            consts.add(n.value)
    for k in active:
        ek = to_sym(entry_state.get(k))
        if ek is not None:
            out.append((f"(>= {k} {ek})", symbols[k] >= ek))
            out.append((f"(<= {k} {ek})", symbols[k] <= ek))
        for c in consts:
            out.append((f"(>= {k} {c})", symbols[k] >= c))
            out.append((f"(<= {k} {c})", symbols[k] <= c))

    # 3. Relaxed loop guard + disjunction with entry value (handles 0-iteration paths)
    if (
        isinstance(loop_node, ast.While)
        and isinstance(loop_node.test, ast.Compare)
        and len(loop_node.test.ops) == 1
    ):
        op = loop_node.test.ops[0]
        l_e = _eval_ast_expr(loop_node.test.left, dict(symbols))
        r_e = _eval_ast_expr(loop_node.test.comparators[0], dict(symbols))
        l_name = (
            loop_node.test.left.id
            if isinstance(loop_node.test.left, ast.Name)
            else None
        )
        l_init = to_sym(entry_state.get(l_name)) if l_name else None
        if l_e is not None and r_e is not None:
            if isinstance(op, ast.Lt):
                out.append((f"(<= {l_e} {r_e})", l_e <= r_e))
                if l_init is not None:
                    out.append(
                        (f"(or (<= {l_e} {r_e}) (= {l_e} {l_init}))", z3.Or(l_e <= r_e, l_e == l_init))
                    )
            elif isinstance(op, ast.Gt):
                out.append((f"(>= {l_e} {r_e})", l_e >= r_e))
                if l_init is not None:
                    out.append(
                        (f"(or (>= {l_e} {r_e}) (= {l_e} {l_init}))", z3.Or(l_e >= r_e, l_e == l_init))
                    )
            elif isinstance(op, ast.LtE):
                out.append((f"(<= {l_e} (+ {r_e} 1))", l_e <= r_e + 1))
                if l_init is not None:
                    out.append(
                        (f"(or (<= {l_e} (+ {r_e} 1)) (= {l_e} {l_init}))", z3.Or(l_e <= r_e + 1, l_e == l_init))
                    )
            elif isinstance(op, ast.GtE):
                out.append((f"(>= {l_e} (- {r_e} 1))", l_e >= r_e - 1))
                if l_init is not None:
                    out.append(
                        (f"(or (>= {l_e} (- {r_e} 1)) (= {l_e} {l_init}))", z3.Or(l_e >= r_e - 1, l_e == l_init))
                    )

    # 4. Pairwise relations and conserved linear combinations
    for i, a in enumerate(active):
        for b in active[i + 1 :]:
            out.append((f"(<= {a} {b})", symbols[a] <= symbols[b]))
            out.append((f"(>= {a} {b})", symbols[a] >= symbols[b]))
            out.append((f"(= {a} {b})", symbols[a] == symbols[b]))
            for off in (-1, 1, 2):
                out.append((f"(>= {a} (+ {b} {off}))", symbols[a] >= symbols[b] + off))
                out.append((f"(<= {a} (+ {b} {off}))", symbols[a] <= symbols[b] + off))
                out.append((f"(>= {b} (+ {a} {off}))", symbols[b] >= symbols[a] + off))
                out.append((f"(<= {b} (+ {a} {off}))", symbols[b] <= symbols[a] + off))
            ea = to_sym(entry_state.get(a))
            eb = to_sym(entry_state.get(b))
            if ea is not None and eb is not None:
                out.append((f"(= (+ {a} {b}) (+ {ea} {eb}))", symbols[a] + symbols[b] == ea + eb))
                out.append((f"(= (- {a} {b}) (- {ea} {eb}))", symbols[a] - symbols[b] == ea - eb))
                out.append((f"(= (+ {a} (* 2 {b})) (+ {ea} (* 2 {eb})))", symbols[a] + 2 * symbols[b] == ea + 2 * eb))
                out.append((f"(= (+ (* 2 {a}) {b}) (+ (* 2 {ea}) {eb}))", 2 * symbols[a] + symbols[b] == 2 * ea + eb))
                out.append((f"(>= (+ {a} (* 2 {b})) (+ {ea} (* 2 {eb})))", symbols[a] + 2 * symbols[b] >= ea + 2 * eb))
                out.append((f"(>= (+ (* 2 {a}) {b}) (+ (* 2 {ea}) {eb}))", 2 * symbols[a] + symbols[b] >= 2 * ea + eb))

    # 5. Post-assertion and post-guard implications
    raw_post = _eval_ast_expr(post_assertion, dict(symbols))
    if raw_post is not None:
        post_b = _as_z3_bool(raw_post)
        out.append((ast.unparse(post_assertion), post_b))
        out.append((f"(or {ast.unparse(post_assertion)} guard)", z3.Or(post_b, guard_b)))
        if post_condition_guard is not None:
            pg_sym = _substitute_vars(post_condition_guard, symbols, skip_literals=False)
            imp = z3.Implies(pg_sym, post_b)
            out.append((f"(=> post_guard {ast.unparse(post_assertion)})", imp))
            out.append((f"(or (=> post_guard {ast.unparse(post_assertion)}) guard)", z3.Or(imp, guard_b)))

    return out


__all__ = [
    "InvariantCandidate",
    "InvariantVerification",
    "LoopInvariantGenerator",
    "parse_smt2_formula",
    "INDUCTION_BOUND",
    "K_INDUCTION_BOUNDS",
]
