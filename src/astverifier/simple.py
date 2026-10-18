"""Structured QF-LIA expressions for the simple unanchored baseline.

The simple baseline accepts a small JSON expression language rather than
SMT-LIB text.  Expressions are translated by constructing Z3 terms directly;
there is no parser fallback that could admit declarations, additional sorts,
or arbitrary SMT-LIB commands.

The canonical JSON spelling is::

    {"kind": "var", "name": "x"}
    {"kind": "int", "value": 2}
    {"kind": "bool", "value": true}
    {"kind": "op", "op": "ge", "args": [left, right]}

For callers that inject test data, a few unambiguous aliases (``type``,
``var``, ``int``, ``bool``, and symbolic comparison/operator names) are also
accepted.  They are normalized to the same strict internal grammar.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Mapping

import z3


class SimpleExpressionError(ValueError):
    """Base class for invalid structured simple expressions."""


class SimpleTranslationError(SimpleExpressionError):
    """The JSON shape or operand sorts are malformed."""


class SimpleUnsupportedError(SimpleExpressionError):
    """The expression is well-shaped but outside the supported QF-LIA set."""


@dataclass(frozen=True)
class ValidatedSimpleExpression:
    """A validated structured expression and its deterministic SMT term."""

    formula: str
    expression: z3.ExprRef
    variables: tuple[str, ...]
    rationale: str = ""


_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_NAMES = {
    "true",
    "false",
    "let",
    "forall",
    "exists",
    "Int",
    "Real",
    "Bool",
    "Array",
    "BitVec",
}


_OP_ALIASES = {
    "+": "add",
    "add": "add",
    "plus": "add",
    "-": "sub",
    "sub": "sub",
    "minus": "sub",
    "neg": "neg",
    "negate": "neg",
    "uminus": "neg",
    "*": "mul",
    "mul": "mul",
    "multiply": "mul",
    "/": "div",
    "div": "div",
    "floordiv": "div",
    "//": "div",
    "%": "mod",
    "mod": "mod",
    "modulo": "mod",
    "and": "and",
    "&&": "and",
    "or": "or",
    "||": "or",
    "not": "not",
    "!": "not",
    "implies": "implies",
    "=>": "implies",
    "xor": "xor",
    "eq": "eq",
    "=": "eq",
    "==": "eq",
    "neq": "neq",
    "ne": "neq",
    "distinct": "neq",
    "!=": "neq",
    "lt": "lt",
    "<": "lt",
    "le": "le",
    "lte": "le",
    "<=": "le",
    "gt": "gt",
    ">": "gt",
    "ge": "ge",
    "gte": "ge",
    ">=": "ge",
}

_BOOLEAN_OPS = {"and", "or", "not", "implies", "xor"}
_COMPARISON_OPS = {"eq", "neq", "lt", "le", "gt", "ge"}
_ARITHMETIC_OPS = {"add", "sub", "neg", "mul", "div", "mod"}
_KNOWN_UNSUPPORTED_OPS = {
    "forall",
    "exists",
    "quantifier",
    "ite",
    "if",
    "select",
    "store",
    "array",
    "real",
    "to_real",
    "to_int",
    "bitvec",
    "bvadd",
    "bvsub",
    "bvmul",
    "concat",
    "extract",
    "lambda",
    "let",
    "abs",
    "min",
    "max",
}
_OPERATOR_FIELDS = {
    "kind",
    "type",
    "node_type",
    "op",
    "operator",
    "args",
    "operands",
    "left",
    "right",
    "operand",
    "arg",
}


def translate_simple_expression(
    expression: Mapping[str, Any], *, rationale: str = ""
) -> ValidatedSimpleExpression:
    """Validate and translate one structured JSON expression.

    The result is a Boolean or integer Z3 term.  A simple claim must use the
    Boolean result; source ``assert`` conversion uses the integer result only
    briefly before applying Python's ``value != 0`` assertion semantics.
    """
    value, variables = _translate_node(expression, path="$", seen=set())
    return ValidatedSimpleExpression(
        formula=_render_smt(expression, path="$", seen=set()),
        expression=value,
        variables=tuple(sorted(variables)),
        rationale=rationale or "",
    )


def validate_simple_claim(
    expression: Mapping[str, Any], *, rationale: str = ""
) -> ValidatedSimpleExpression:
    """Validate one expression and require its result to be Boolean."""
    validated = translate_simple_expression(expression, rationale=rationale)
    if not z3.is_bool(validated.expression):
        raise SimpleTranslationError("simple claim expression must be Boolean")
    return validated


def source_assert_to_simple(
    expr: ast.expr, *, rationale: str = ""
) -> ValidatedSimpleExpression:
    """Convert a Python ``assert`` expression to the structured QF-LIA term.

    This conversion is deterministic and deliberately shares the simple
    operator whitelist.  It does not use source locations or program state.
    """
    structured = _python_to_structured(expr)
    translated = translate_simple_expression(structured, rationale=rationale)
    if z3.is_bool(translated.expression):
        return translated
    # Python permits ``assert integer_expression``; its truth value is
    # equivalent to nonzero for the integer-only subset.
    coerced = {
        "kind": "op",
        "op": "neq",
        "args": [structured, {"kind": "int", "value": 0}],
    }
    return validate_simple_claim(coerced, rationale=rationale)


def expression_to_smtlib(expression: Mapping[str, Any]) -> str:
    """Return the canonical SMT-LIB2 Boolean text for a simple claim."""
    return validate_simple_claim(expression).formula


# Friendly aliases for callers that describe the operation as JSON-to-SMT.
json_to_smt = expression_to_smtlib
translate_json_expression = translate_simple_expression


def _translate_node(
    node: Any, *, path: str, seen: set[int]
) -> tuple[z3.ExprRef, set[str]]:
    if not isinstance(node, Mapping):
        raise SimpleTranslationError(f"{path} must be a JSON object expression")

    # A recursive object graph is not JSON, but checking this explicitly keeps
    # injected Python objects from causing surprising recursion failures.
    marker = id(node)
    if marker in seen:
        raise SimpleTranslationError(f"{path} contains a cyclic expression")
    seen.add(marker)
    try:
        return _translate_mapping(node, path=path, seen=seen)
    finally:
        seen.remove(marker)


def _translate_mapping(
    node: Mapping[str, Any], *, path: str, seen: set[int]
) -> tuple[z3.ExprRef, set[str]]:
    if "var" in node or "variable" in node:
        if len(node) != 1 or ("var" in node and "variable" in node):
            raise SimpleTranslationError(f"{path} has ambiguous variable fields")
        name = node.get("var", node.get("variable"))
        return _variable(name, path)

    if "int" in node or "int_const" in node or "integer" in node or "const" in node:
        if len(node) != 1:
            raise SimpleTranslationError(f"{path} has extra fields on an integer constant")
        value = node.get(
            "int",
            node.get("int_const", node.get("integer", node.get("const"))),
        )
        return _integer_constant(value, path)

    if "bool" in node or "bool_const" in node or "boolean" in node:
        if len(node) != 1:
            raise SimpleTranslationError(f"{path} has extra fields on a Boolean constant")
        value = node.get("bool", node.get("bool_const", node.get("boolean")))
        if type(value) is not bool:
            raise SimpleTranslationError(f"{path} Boolean constant must contain true or false")
        return z3.BoolVal(value), set()

    kind = node.get("kind", node.get("type", node.get("node_type")))
    if isinstance(kind, str):
        kind_lower = kind.lower()
        if kind_lower in {"var", "variable", "int_var", "integer_var", "integer_variable"}:
            name = node.get("name", node.get("value"))
            if set(node) - {"kind", "type", "node_type", "name", "sort"}:
                raise SimpleTranslationError(f"{path} has extra variable fields")
            if "sort" in node and str(node["sort"]).lower() not in {"int", "integer"}:
                raise SimpleUnsupportedError(f"{path} uses a non-Int variable sort")
            return _variable(name, path)
        if kind_lower in {"int", "integer", "int_const", "integer_const", "integer_constant"}:
            value = node.get("value")
            if set(node) - {"kind", "type", "node_type", "value"}:
                raise SimpleTranslationError(f"{path} has extra integer-constant fields")
            return _integer_constant(value, path)
        if kind_lower in {"bool", "boolean", "bool_const", "boolean_const", "boolean_constant"}:
            value = node.get("value")
            if set(node) - {"kind", "type", "node_type", "value"}:
                raise SimpleTranslationError(f"{path} has extra Boolean-constant fields")
            if type(value) is not bool:
                raise SimpleTranslationError(f"{path} Boolean constant must contain true or false")
            return z3.BoolVal(value), set()
        if kind_lower in {"const", "constant", "literal"}:
            if set(node) - {"kind", "type", "node_type", "value"}:
                raise SimpleTranslationError(f"{path} has extra constant fields")
            value = node.get("value")
            if type(value) is bool:
                return z3.BoolVal(value), set()
            return _integer_constant(value, path)
        if kind_lower in {
            "bool_var",
            "boolean_var",
            "real",
            "real_var",
            "real_const",
            "array",
            "array_var",
            "array_expr",
            "bitvec",
            "bitvec_var",
            "bitvector",
        }:
            raise SimpleUnsupportedError(f"{path} uses an unsupported non-QF-LIA sort")

    raw_op = node.get("op", node.get("operator"))
    if raw_op is None and isinstance(kind, str):
        # The compact spelling {"kind": "add", "args": [...]} is useful
        # for hand-written fixtures while the canonical spelling uses kind=op.
        raw_op = kind
    if raw_op is None:
        for unsupported_key in ("forall", "exists", "quantifier", "array", "bitvec", "real"):
            if unsupported_key in node:
                raise SimpleUnsupportedError(f"{path} uses unsupported feature {unsupported_key!r}")
        raise SimpleTranslationError(f"{path} must contain a variable, constant, or operator")
    if not isinstance(raw_op, str):
        raise SimpleTranslationError(f"{path}.op must be a string")
    op = _canonical_operator(raw_op, path)
    unexpected = set(node) - _OPERATOR_FIELDS
    if unexpected:
        raise SimpleTranslationError(
            f"{path} has unsupported operator fields: {', '.join(sorted(unexpected))}"
        )

    args = _operator_args(node, path)
    values: list[z3.ExprRef] = []
    variables: set[str] = set()
    for index, child in enumerate(args):
        value, child_variables = _translate_node(
            child, path=f"{path}.args[{index}]", seen=seen
        )
        values.append(value)
        variables.update(child_variables)

    if op in _BOOLEAN_OPS:
        _require_bool_args(values, op, path)
        if op == "and":
            _require_arity(values, minimum=2, path=path, op=op)
            return z3.And(*values), variables
        if op == "or":
            _require_arity(values, minimum=2, path=path, op=op)
            return z3.Or(*values), variables
        if op == "not":
            _require_arity(values, exact=1, path=path, op=op)
            return z3.Not(values[0]), variables
        _require_arity(values, exact=2, path=path, op=op)
        if op == "implies":
            return z3.Implies(values[0], values[1]), variables
        return z3.Xor(values[0], values[1]), variables

    if op in _COMPARISON_OPS:
        _require_arity(values, exact=2, path=path, op=op)
        left, right = values
        if op in {"eq", "neq"}:
            if not (z3.is_int(left) and z3.is_int(right)) and not (
                z3.is_bool(left) and z3.is_bool(right)
            ):
                raise SimpleUnsupportedError(
                    f"{path} equality requires two Int terms or two Bool terms"
                )
            result = left == right
            return (z3.Not(result) if op == "neq" else result), variables
        if not (z3.is_int(left) and z3.is_int(right)):
            raise SimpleUnsupportedError(f"{path} ordered comparisons require Int terms")
        if op == "lt":
            return left < right, variables
        if op == "le":
            return left <= right, variables
        if op == "gt":
            return left > right, variables
        return left >= right, variables

    # Arithmetic operators.
    _require_int_args(values, op, path)
    if op == "add":
        _require_arity(values, minimum=2, path=path, op=op)
        return z3.Sum(values), variables
    if op == "sub":
        _require_arity(values, minimum=2, path=path, op=op)
        result = values[0]
        for value in values[1:]:
            result = result - value
        return result, variables
    if op == "neg":
        _require_arity(values, exact=1, path=path, op=op)
        return -values[0], variables
    if op == "mul":
        _require_arity(values, exact=2, path=path, op=op)
        if not (_is_integer_constant(values[0]) or _is_integer_constant(values[1])):
            raise SimpleUnsupportedError(
                f"{path} has nonlinear multiplication; one operand must be an integer constant"
            )
        return values[0] * values[1], variables
    if op in {"div", "mod"}:
        _require_arity(values, exact=2, path=path, op=op)
        if not _is_integer_constant(values[1]):
            raise SimpleUnsupportedError(
                f"{path} requires a nonzero integer constant divisor"
            )
        divisor = _integer_value(values[1])
        if divisor == 0:
            raise SimpleUnsupportedError(f"{path} divisor must be nonzero")
        return (
            (values[0] / values[1]) if op == "div" else (values[0] % values[1]),
            variables,
        )

    # The aliases above make this unreachable, but keeping the guard makes the
    # whitelist explicit if an operator is added later.
    raise SimpleUnsupportedError(f"{path} uses unsupported operator {op!r}")


def _canonical_operator(raw_op: str, path: str) -> str:
    """Normalize an operator name after checking the explicit whitelist."""
    op = _OP_ALIASES.get(raw_op.lower())
    if op is None:
        # Keep all unknown operators in the unsupported bucket.  In
        # particular, this prevents a future alias from silently admitting a
        # quantifier, a different sort, or an arbitrary SMT-LIB declaration.
        raise SimpleUnsupportedError(f"{path} uses unsupported operator {raw_op!r}")
    return op


def _render_smt(node: Any, *, path: str, seen: set[int]) -> str:
    """Render a validated JSON tree without semantic simplification.

    Z3's pretty-printer may reverse comparisons with an explicit numeral and
    ``simplify`` may erase the original operator tree.  Rendering the already
    validated JSON directly keeps the public SMT-LIB2 text deterministic and
    auditable while the Z3 term remains the source of solver semantics.
    """
    if not isinstance(node, Mapping):
        raise SimpleTranslationError(f"{path} must be a JSON object expression")
    marker = id(node)
    if marker in seen:
        raise SimpleTranslationError(f"{path} contains a cyclic expression")
    seen.add(marker)
    try:
        if "var" in node or "variable" in node:
            name = node.get("var", node.get("variable"))
            if len(node) != 1 or ("var" in node and "variable" in node):
                raise SimpleTranslationError(f"{path} has ambiguous variable fields")
            _variable(name, path)
            return str(name)

        if "int" in node or "int_const" in node or "integer" in node or "const" in node:
            if len(node) != 1:
                raise SimpleTranslationError(f"{path} has extra fields on an integer constant")
            value = node.get(
                "int",
                node.get("int_const", node.get("integer", node.get("const"))),
            )
            _integer_constant(value, path)
            return str(value)

        if "bool" in node or "bool_const" in node or "boolean" in node:
            if len(node) != 1:
                raise SimpleTranslationError(f"{path} has extra fields on a Boolean constant")
            value = node.get("bool", node.get("bool_const", node.get("boolean")))
            if type(value) is not bool:
                raise SimpleTranslationError(f"{path} Boolean constant must contain true or false")
            return "true" if value else "false"

        kind = node.get("kind", node.get("type", node.get("node_type")))
        if isinstance(kind, str):
            kind_lower = kind.lower()
            if kind_lower in {"var", "variable", "int_var", "integer_var", "integer_variable"}:
                name = node.get("name", node.get("value"))
                _variable(name, path)
                return str(name)
            if kind_lower in {"int", "integer", "int_const", "integer_const", "integer_constant"}:
                value = node.get("value")
                _integer_constant(value, path)
                return str(value)
            if kind_lower in {"bool", "boolean", "bool_const", "boolean_const", "boolean_constant"}:
                value = node.get("value")
                if type(value) is not bool:
                    raise SimpleTranslationError(f"{path} Boolean constant must contain true or false")
                return "true" if value else "false"
            if kind_lower in {"const", "constant", "literal"}:
                value = node.get("value")
                if type(value) is bool:
                    return "true" if value else "false"
                _integer_constant(value, path)
                return str(value)

        raw_op = node.get("op", node.get("operator"))
        if raw_op is None and isinstance(kind, str):
            raw_op = kind
        if not isinstance(raw_op, str):
            raise SimpleTranslationError(f"{path} must contain a supported operator")
        op = _canonical_operator(raw_op, path)
        unexpected = set(node) - _OPERATOR_FIELDS
        if unexpected:
            raise SimpleTranslationError(
                f"{path} has unsupported operator fields: {', '.join(sorted(unexpected))}"
            )
        args = _operator_args(node, path)
        rendered = [_render_smt(child, path=f"{path}.args[{i}]", seen=seen)
                    for i, child in enumerate(args)]
        if op == "neg":
            return f"(- {rendered[0]})"
        if op == "implies":
            return f"(=> {' '.join(rendered)})"
        if op == "neq":
            return f"(not (= {' '.join(rendered)}))"
        symbol = {
            "add": "+",
            "sub": "-",
            "mul": "*",
            "div": "div",
            "mod": "mod",
            "and": "and",
            "or": "or",
            "not": "not",
            "xor": "xor",
            "eq": "=",
            "lt": "<",
            "le": "<=",
            "gt": ">",
            "ge": ">=",
        }[op]
        return f"({symbol} {' '.join(rendered)})"
    finally:
        seen.remove(marker)


def _operator_args(node: Mapping[str, Any], path: str) -> list[Any]:
    if "args" in node and "operands" in node:
        raise SimpleTranslationError(f"{path} cannot contain both args and operands")
    if "args" in node:
        args = node["args"]
    elif "operands" in node:
        args = node["operands"]
    elif "left" in node or "right" in node:
        if "left" not in node or "right" not in node:
            raise SimpleTranslationError(f"{path} binary operator needs left and right")
        args = [node["left"], node["right"]]
    elif "operand" in node:
        args = [node["operand"]]
    elif "arg" in node:
        args = [node["arg"]]
    else:
        raise SimpleTranslationError(f"{path} operator has no operands")
    if not isinstance(args, (list, tuple)):
        raise SimpleTranslationError(f"{path} operator operands must be a JSON array")
    return list(args)


def _require_arity(
    values: list[z3.ExprRef], *, path: str, op: str, exact: int | None = None, minimum: int | None = None
) -> None:
    if exact is not None and len(values) != exact:
        raise SimpleTranslationError(f"{path} operator {op!r} expects {exact} operands")
    if minimum is not None and len(values) < minimum:
        raise SimpleTranslationError(f"{path} operator {op!r} expects at least {minimum} operands")


def _require_bool_args(values: list[z3.ExprRef], op: str, path: str) -> None:
    if not all(z3.is_bool(value) for value in values):
        raise SimpleUnsupportedError(f"{path} Boolean operator {op!r} needs Boolean operands")


def _require_int_args(values: list[z3.ExprRef], op: str, path: str) -> None:
    if not all(z3.is_int(value) for value in values):
        raise SimpleUnsupportedError(f"{path} arithmetic operator {op!r} needs Int operands")


def _variable(name: Any, path: str) -> tuple[z3.IntNumRef | z3.ArithRef, set[str]]:
    if not isinstance(name, str) or not _SYMBOL_RE.fullmatch(name):
        raise SimpleTranslationError(f"{path} variable name must be a valid identifier")
    if name in _RESERVED_NAMES:
        raise SimpleTranslationError(f"{path} variable name {name!r} is reserved")
    return z3.Int(name), {name}


def _integer_constant(value: Any, path: str) -> tuple[z3.IntNumRef, set[str]]:
    if type(value) is not int:
        raise SimpleTranslationError(f"{path} integer constant must be an integer")
    return z3.IntVal(value), set()


def _is_integer_constant(value: z3.ExprRef) -> bool:
    return z3.is_int_value(z3.simplify(value))


def _integer_value(value: z3.ExprRef) -> int:
    return z3.simplify(value).as_long()


def _python_to_structured(expr: ast.AST) -> dict[str, Any]:
    if isinstance(expr, ast.Name):
        return {"kind": "var", "name": expr.id}
    if isinstance(expr, ast.Constant):
        if type(expr.value) is bool:
            return {"kind": "bool", "value": expr.value}
        if type(expr.value) is int:
            return {"kind": "int", "value": expr.value}
        raise SimpleUnsupportedError(
            f"source assertion constant {expr.value!r} is outside QF-LIA"
        )
    if isinstance(expr, ast.UnaryOp):
        if isinstance(expr.op, ast.USub):
            return {"kind": "op", "op": "neg", "args": [_python_to_structured(expr.operand)]}
        if isinstance(expr.op, ast.Not):
            return {
                "kind": "op",
                "op": "not",
                "args": [_python_boolean_operand(expr.operand)],
            }
        raise SimpleUnsupportedError(
            f"source assertion unary operator {type(expr.op).__name__} is outside QF-LIA"
        )
    if isinstance(expr, ast.BoolOp):
        op = "and" if isinstance(expr.op, ast.And) else "or" if isinstance(expr.op, ast.Or) else None
        if op is None:
            raise SimpleUnsupportedError("source Boolean operator is outside QF-LIA")
        return {
            "kind": "op",
            "op": op,
            "args": [_python_boolean_operand(value) for value in expr.values],
        }
    if isinstance(expr, ast.BinOp):
        if isinstance(expr.op, ast.Add):
            op = "add"
        elif isinstance(expr.op, ast.Sub):
            op = "sub"
        elif isinstance(expr.op, ast.Mult):
            left_constant = _python_integer_constant(expr.left)
            right_constant = _python_integer_constant(expr.right)
            if left_constant is None and right_constant is None:
                raise SimpleUnsupportedError("source nonlinear multiplication is outside QF-LIA")
            if left_constant is not None:
                return {
                    "kind": "op",
                    "op": "mul",
                    "args": [{"kind": "int", "value": left_constant}, _python_to_structured(expr.right)],
                }
            return {
                "kind": "op",
                "op": "mul",
                "args": [_python_to_structured(expr.left), {"kind": "int", "value": right_constant}],
            }
        elif isinstance(expr.op, ast.FloorDiv):
            divisor = _python_integer_constant(expr.right)
            if divisor is None or divisor == 0:
                raise SimpleUnsupportedError("source division requires a nonzero integer constant divisor")
            return {
                "kind": "op",
                "op": "div",
                "args": [_python_to_structured(expr.left), {"kind": "int", "value": divisor}],
            }
        elif isinstance(expr.op, ast.Mod):
            divisor = _python_integer_constant(expr.right)
            if divisor is None or divisor == 0:
                raise SimpleUnsupportedError("source modulo requires a nonzero integer constant divisor")
            return {
                "kind": "op",
                "op": "mod",
                "args": [_python_to_structured(expr.left), {"kind": "int", "value": divisor}],
            }
        else:
            raise SimpleUnsupportedError("source arithmetic operator is outside QF-LIA")
        return {
            "kind": "op",
            "op": op,
            "args": [_python_to_structured(expr.left), _python_to_structured(expr.right)],
        }
    if isinstance(expr, ast.Compare):
        if not expr.ops:
            raise SimpleTranslationError("source comparison has no operator")
        comparisons = []
        left = expr.left
        for op_node, right in zip(expr.ops, expr.comparators):
            operator = {
                ast.Eq: "eq",
                ast.NotEq: "neq",
                ast.Lt: "lt",
                ast.LtE: "le",
                ast.Gt: "gt",
                ast.GtE: "ge",
            }.get(type(op_node))
            if operator is None:
                raise SimpleUnsupportedError("source comparison operator is outside QF-LIA")
            comparisons.append(
                {
                    "kind": "op",
                    "op": operator,
                    "args": [_python_to_structured(left), _python_to_structured(right)],
                }
            )
            left = right
        if len(comparisons) == 1:
            return comparisons[0]
        return {"kind": "op", "op": "and", "args": comparisons}
    raise SimpleUnsupportedError(
        f"source assertion expression {type(expr).__name__} is outside QF-LIA"
    )


def _python_integer_constant(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _python_integer_constant(node.operand)
        return -value if value is not None else None
    return None


def _python_boolean_operand(node: ast.AST) -> dict[str, Any]:
    """Translate a Python condition, coercing Int truthiness to ``!= 0``."""
    structured = _python_to_structured(node)
    value, _ = _translate_node(structured, path="$source", seen=set())
    if z3.is_bool(value):
        return structured
    if z3.is_int(value):
        return {
            "kind": "op",
            "op": "neq",
            "args": [structured, {"kind": "int", "value": 0}],
        }
    raise SimpleUnsupportedError("source condition is outside the Int/Bool QF-LIA subset")


__all__ = [
    "SimpleExpressionError",
    "SimpleTranslationError",
    "SimpleUnsupportedError",
    "ValidatedSimpleExpression",
    "translate_simple_expression",
    "validate_simple_claim",
    "source_assert_to_simple",
    "expression_to_smtlib",
    "json_to_smt",
    "translate_json_expression",
]
