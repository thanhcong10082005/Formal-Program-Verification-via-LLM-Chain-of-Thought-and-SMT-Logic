"""Direct Formalization baseline helpers.

This module is deliberately independent from the trusted symbolic executor.
It parses one SMT-LIB2 Boolean term, checks a small QF-LIA whitelist, and
returns the formula that can be checked with the single obligation ``not C``.
It also contains the deterministic conversion used for source ``assert``
statements in the compatibility ``UNANCHORED`` result directory.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Sequence

import z3


class DirectFormulaError(ValueError):
    """Base class for invalid direct specifications."""


class DirectTranslationError(DirectFormulaError):
    """The text is malformed or cannot be translated to one Boolean term."""


class DirectUnsupportedError(DirectFormulaError):
    """The term parses but is outside the supported QF-LIA fragment."""


@dataclass(frozen=True)
class ValidatedDirectFormula:
    """A parsed and whitelisted direct formula."""

    formula: str
    variables: tuple[str, ...]
    expression: z3.BoolRef
    rationale: str = ""


_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_SYMBOLS = {
    "true",
    "false",
    "let",
    "forall",
    "exists",
    "Int",
    "Bool",
}
_BUILTIN_NAMES = {"abs", "min", "max", "range", "len"}


def validate_direct_formula(
    formula: str,
    variables: Sequence[str] | None = None,
    *,
    rationale: str = "",
) -> ValidatedDirectFormula:
    """Parse and validate one SMT-LIB2 QF-LIA Boolean term.

    The formula is wrapped in one ``assert`` command solely for parsing.  The
    returned expression is the asserted Boolean term; no source state,
    location, or reachability relation is introduced.
    """
    if not isinstance(formula, str) or not formula.strip():
        raise DirectTranslationError("direct formula is empty")

    declared = _normalize_variables(variables)
    declarations = " ".join(f"(declare-const {name} Int)" for name in declared)
    script = f"{declarations} (assert {formula.strip()})".strip()
    try:
        assertions = z3.parse_smt2_string(script)
    except Exception as exc:
        raise DirectTranslationError(f"malformed SMT-LIB2 formula: {exc}") from exc

    if len(assertions) != 1:
        raise DirectTranslationError(
            f"expected exactly one SMT-LIB2 Boolean term, got {len(assertions)} assertions"
        )
    expression = assertions[0]
    if not z3.is_bool(expression):
        raise DirectTranslationError("direct specification must be a Boolean term")

    try:
        _validate_term(expression, set(declared), expected="Bool")
    except DirectFormulaError:
        raise
    except Exception as exc:
        raise DirectUnsupportedError(f"unsupported SMT-LIB2 term: {exc}") from exc

    return ValidatedDirectFormula(
        formula=formula.strip(),
        variables=declared,
        expression=z3.simplify(expression),
        rationale=rationale or "",
    )


def source_assert_to_direct(
    expr: ast.expr,
    *,
    rationale: str = "",
) -> ValidatedDirectFormula:
    """Convert a Python assert expression to one deterministic SMT-LIB2 term.

    Names are fresh unconstrained integer symbols.  In particular, assignments
    in the source are not replayed or substituted into this formula.
    """
    names = sorted(
        {
            node.id
            for node in ast.walk(expr)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in _BUILTIN_NAMES
        }
    )
    env = {name: z3.Int(name) for name in names}
    try:
        value = _python_expr_to_z3(expr, env)
        formula = _as_bool(value).sexpr()
    except DirectFormulaError:
        raise
    except Exception as exc:
        raise DirectTranslationError(
            f"source assertion could not be translated: {exc}"
        ) from exc
    return validate_direct_formula(formula, names, rationale=rationale)


def _normalize_variables(variables: Sequence[str] | None) -> tuple[str, ...]:
    if variables is None:
        variables = []
    if isinstance(variables, (str, bytes)):
        raise DirectTranslationError("variables must be a list of integer symbols")
    try:
        variable_iter = iter(variables)
    except TypeError as exc:
        raise DirectTranslationError(
            "variables must be a list of integer symbols"
        ) from exc

    out: list[str] = []
    seen: set[str] = set()
    for variable in variable_iter:
        if not isinstance(variable, str) or not _SYMBOL_RE.fullmatch(variable):
            raise DirectTranslationError(
                f"invalid integer symbol {variable!r} in variables"
            )
        if variable in _RESERVED_SYMBOLS:
            raise DirectTranslationError(f"reserved SMT-LIB2 symbol {variable!r}")
        if variable in seen:
            raise DirectTranslationError(f"duplicate integer symbol {variable!r}")
        seen.add(variable)
        out.append(variable)
    return tuple(out)


def _sort_is_int(value: z3.ExprRef) -> bool:
    return value.sort().kind() == z3.Z3_INT_SORT


def _sort_is_bool(value: z3.ExprRef) -> bool:
    return value.sort().kind() == z3.Z3_BOOL_SORT


def _validate_term(
    value: z3.ExprRef,
    declared: set[str],
    *,
    expected: str,
) -> None:
    """Validate a Z3 term recursively against the direct QF-LIA whitelist."""
    if z3.is_quantifier(value):
        raise DirectUnsupportedError("quantifiers are not allowed in QF-LIA direct formulas")

    if expected == "Bool" and not _sort_is_bool(value):
        raise DirectTranslationError("formula is not Boolean")
    if expected == "Int" and not _sort_is_int(value):
        raise DirectUnsupportedError("only integer arithmetic is allowed")

    if z3.is_true(value) or z3.is_false(value):
        if expected != "Bool":
            raise DirectUnsupportedError("Boolean literal in integer arithmetic")
        return
    if z3.is_int_value(value):
        if expected != "Int":
            raise DirectUnsupportedError("integer literal where Boolean term was expected")
        return

    decl = value.decl()
    kind = decl.kind()
    args = list(value.children())

    if kind == z3.Z3_OP_UNINTERPRETED:
        if args or decl.name() not in declared:
            raise DirectUnsupportedError(
                f"undeclared or unsupported symbol {decl.name()!s}"
            )
        if not _sort_is_int(value):
            raise DirectUnsupportedError("direct variables must have Int sort")
        if expected != "Int":
            raise DirectUnsupportedError("integer variable where Boolean term was expected")
        return

    bool_connectives = {
        z3.Z3_OP_AND,
        z3.Z3_OP_OR,
        z3.Z3_OP_NOT,
        z3.Z3_OP_IMPLIES,
        z3.Z3_OP_XOR,
    }
    if kind in bool_connectives:
        if expected != "Bool":
            raise DirectUnsupportedError("Boolean connective in integer arithmetic")
        for arg in args:
            _validate_term(arg, declared, expected="Bool")
        return

    if kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT}:
        if expected != "Bool" or len(args) < 2:
            raise DirectUnsupportedError("equality requires Boolean result and two terms")
        first_sort = args[0].sort().kind()
        if first_sort not in {z3.Z3_INT_SORT, z3.Z3_BOOL_SORT}:
            raise DirectUnsupportedError("equality is limited to Int and Bool terms")
        for arg in args:
            _validate_term(
                arg,
                declared,
                expected="Int" if first_sort == z3.Z3_INT_SORT else "Bool",
            )
        return

    comparisons = {
        z3.Z3_OP_LT,
        z3.Z3_OP_LE,
        z3.Z3_OP_GT,
        z3.Z3_OP_GE,
    }
    if kind in comparisons:
        if expected != "Bool" or len(args) != 2:
            raise DirectUnsupportedError("integer comparison requires two operands")
        for arg in args:
            _validate_term(arg, declared, expected="Int")
        return

    if kind in {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS}:
        if expected != "Int":
            raise DirectUnsupportedError("integer arithmetic where Boolean term was expected")
        for arg in args:
            _validate_term(arg, declared, expected="Int")
        return

    if kind == z3.Z3_OP_MUL:
        if expected != "Int" or len(args) != 2:
            raise DirectUnsupportedError("multiplication must be binary integer arithmetic")
        if not (_is_integer_constant(args[0]) or _is_integer_constant(args[1])):
            raise DirectUnsupportedError(
                "nonlinear multiplication is not part of QF-LIA"
            )
        for arg in args:
            _validate_term(arg, declared, expected="Int")
        return

    if kind in {z3.Z3_OP_IDIV, z3.Z3_OP_MOD}:
        if expected != "Int" or len(args) != 2:
            raise DirectUnsupportedError("div/mod must be binary integer arithmetic")
        if not _is_integer_constant(args[1]) or z3.simplify(args[1]).as_long() == 0:
            raise DirectUnsupportedError(
                "division and modulo require a nonzero integer constant divisor"
            )
        for arg in args:
            _validate_term(arg, declared, expected="Int")
        return

    # Real division and any other arithmetic/operator declaration are outside
    # the integer-only direct contract.
    if kind == z3.Z3_OP_DIV:
        raise DirectUnsupportedError("real division is not part of QF-LIA")

    if kind == z3.Z3_OP_ITE:
        if len(args) != 3:
            raise DirectUnsupportedError("ite requires condition, then, and else terms")
        _validate_term(args[0], declared, expected="Bool")
        branch_expected = "Int" if _sort_is_int(args[1]) else "Bool" if _sort_is_bool(args[1]) else ""
        if not branch_expected or args[1].sort().kind() != args[2].sort().kind():
            raise DirectUnsupportedError("ite branches must have the same Int or Bool sort")
        _validate_term(args[1], declared, expected=branch_expected)
        _validate_term(args[2], declared, expected=branch_expected)
        return

    raise DirectUnsupportedError(
        f"unsupported SMT-LIB2 operator {decl.name()}"
    )


def _is_integer_constant(value: z3.ExprRef) -> bool:
    try:
        simplified = z3.simplify(value)
    except Exception:
        return False
    return z3.is_int_value(simplified)


def _as_bool(value: z3.ExprRef) -> z3.BoolRef:
    if z3.is_bool(value):
        return value
    if _sort_is_int(value):
        return value != 0
    raise DirectTranslationError("Python assert expression is not integer/Boolean")


def _as_int(value: z3.ExprRef) -> z3.ArithRef:
    if _sort_is_int(value):
        return value
    if z3.is_bool(value):
        return z3.If(value, z3.IntVal(1), z3.IntVal(0))
    raise DirectTranslationError("Python expression is not integer/Boolean")


def _comparison_operands(left: z3.ExprRef, right: z3.ExprRef) -> tuple[z3.ExprRef, z3.ExprRef]:
    if _sort_is_int(left) and _sort_is_bool(right):
        return left, _as_int(right)
    if _sort_is_bool(left) and _sort_is_int(right):
        return _as_int(left), right
    return left, right


def _python_expr_to_z3(expr: ast.AST, env: dict[str, z3.IntNumRef | z3.ArithRef]):
    if isinstance(expr, ast.Name):
        try:
            return env[expr.id]
        except KeyError as exc:
            raise DirectTranslationError(f"unknown source symbol {expr.id!r}") from exc

    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, bool):
            return z3.BoolVal(expr.value)
        if isinstance(expr.value, int):
            return z3.IntVal(expr.value)
        if expr.value is None:
            return z3.BoolVal(False)
        raise DirectTranslationError(f"unsupported source constant {expr.value!r}")

    if isinstance(expr, ast.BinOp):
        left = _as_int(_python_expr_to_z3(expr.left, env))
        right = _as_int(_python_expr_to_z3(expr.right, env))
        if isinstance(expr.op, ast.Add):
            return left + right
        if isinstance(expr.op, ast.Sub):
            return left - right
        if isinstance(expr.op, ast.Mult):
            return left * right
        if isinstance(expr.op, ast.FloorDiv):
            return left / right
        if isinstance(expr.op, ast.Mod):
            return left % right
        raise DirectTranslationError(f"unsupported source arithmetic {type(expr.op).__name__}")

    if isinstance(expr, ast.UnaryOp):
        value = _python_expr_to_z3(expr.operand, env)
        if isinstance(expr.op, ast.USub):
            return -_as_int(value)
        if isinstance(expr.op, ast.Not):
            return z3.Not(_as_bool(value))
        raise DirectTranslationError(f"unsupported source unary operator {type(expr.op).__name__}")

    if isinstance(expr, ast.BoolOp):
        values = [_as_bool(_python_expr_to_z3(value, env)) for value in expr.values]
        if isinstance(expr.op, ast.And):
            return z3.And(*values)
        if isinstance(expr.op, ast.Or):
            return z3.Or(*values)
        raise DirectTranslationError(f"unsupported source Boolean operator {type(expr.op).__name__}")

    if isinstance(expr, ast.Compare):
        comparisons = []
        left = _python_expr_to_z3(expr.left, env)
        for op, right_node in zip(expr.ops, expr.comparators):
            right = _python_expr_to_z3(right_node, env)
            left_cmp, right_cmp = _comparison_operands(left, right)
            if isinstance(op, ast.Eq):
                comparisons.append(left_cmp == right_cmp)
            elif isinstance(op, ast.NotEq):
                comparisons.append(left_cmp != right_cmp)
            elif isinstance(op, ast.Lt):
                comparisons.append(_as_int(left_cmp) < _as_int(right_cmp))
            elif isinstance(op, ast.LtE):
                comparisons.append(_as_int(left_cmp) <= _as_int(right_cmp))
            elif isinstance(op, ast.Gt):
                comparisons.append(_as_int(left_cmp) > _as_int(right_cmp))
            elif isinstance(op, ast.GtE):
                comparisons.append(_as_int(left_cmp) >= _as_int(right_cmp))
            else:
                raise DirectTranslationError(
                    f"unsupported source comparator {type(op).__name__}"
                )
            left = right
        return z3.And(*comparisons) if len(comparisons) > 1 else comparisons[0]

    if isinstance(expr, ast.IfExp):
        condition = _as_bool(_python_expr_to_z3(expr.test, env))
        then_value = _python_expr_to_z3(expr.body, env)
        else_value = _python_expr_to_z3(expr.orelse, env)
        if z3.is_bool(then_value) and z3.is_bool(else_value):
            return z3.If(condition, then_value, else_value)
        return z3.If(condition, _as_int(then_value), _as_int(else_value))

    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        name = expr.func.id
        values = [_as_int(_python_expr_to_z3(arg, env)) for arg in expr.args]
        if name == "abs" and len(values) == 1:
            return z3.If(values[0] >= 0, values[0], -values[0])
        if name in {"min", "max"} and values:
            result = values[-1]
            for value in reversed(values[:-1]):
                if name == "min":
                    result = z3.If(value <= result, value, result)
                else:
                    result = z3.If(value >= result, value, result)
            return result
        raise DirectTranslationError(
            f"source builtin {name!r} is not an arithmetic expression"
        )

    raise DirectTranslationError(
        f"unsupported source expression {type(expr).__name__}"
    )


__all__ = [
    "DirectFormulaError",
    "DirectTranslationError",
    "DirectUnsupportedError",
    "ValidatedDirectFormula",
    "validate_direct_formula",
    "source_assert_to_direct",
]
