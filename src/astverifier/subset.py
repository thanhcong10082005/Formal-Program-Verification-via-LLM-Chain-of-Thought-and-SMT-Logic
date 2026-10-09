"""
subset.py - The QF-LIA Python Subset Gate (Phase 1, Foundation).

Implement the whitelist defined in IMPLEMENTATION_PLAN.md §1.4.
Reject (UNSUPPORTED) anything that uses string/list/dict, method calls,
complex operators, or anything outside quantifier-free linear integer arithmetic.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional


# --- Whitelist constants -----------------------------------------------------

ALLOWED_STMT_TYPES = (
    ast.FunctionDef,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Return,
    ast.Break,
    ast.Continue,
    ast.Assert,
    ast.Pass,
    ast.Expr,  # only allowed form is Call(assert, ...)
)

ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)
ALLOWED_UNARYOPS = (ast.USub, ast.Not)
ALLOWED_COMPARATORS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
ALLOWED_BOOLOPS = (ast.And, ast.Or)
ALLOWED_EXPR_TYPES = (
    ast.Name,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
)

ALLOWED_AUGOPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.Mod,
)

ALLOWED_BUILTINS = {"abs", "min", "max", "range", "len"}


# --- Allowed Python value types (for input/output literals) ------------------

# Tuple of (type, allow-list-of-Python-types-for-input-and-output-literals).
# We accept int, bool, None only — these are QF-LIA compatible.
ALLOWED_INPUT_VALUE_TYPES = (int, bool, type(None))


# --- SubsetResult ------------------------------------------------------------


@dataclass
class ClaimInfo:
    """A claim extracted from `assert <expr>` in the source."""

    claim_id: str
    source_line: int
    expr_source: str
    expr_node: ast.expr


@dataclass
class SubsetResult:
    accepted: bool
    rejected_reason: Optional[str] = None
    rejected_node_kind: Optional[str] = None
    rejected_source_line: Optional[int] = None
    rejected_source_text: Optional[str] = None
    extracted_claims: list[ClaimInfo] = field(default_factory=list)
    tree: Optional[ast.Module] = None
    used_builtins: list[str] = field(default_factory=list)


# --- The gate -----------------------------------------------------------------


class SubsetVisitor(ast.NodeVisitor):
    """Walk the AST and reject anything outside the QF-LIA subset."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, int, str]] = []
        self.used_builtins: set[str] = set()
        self.claims: list[ClaimInfo] = []
        self._claim_seq = 0

    def _reject(self, reason: str, node: ast.AST) -> None:
        src = ast.unparse(node).splitlines()[0] if hasattr(ast, "unparse") else ""
        self.errors.append((reason, getattr(node, "lineno", -1), src))
        # Stop traversal: raise to exit early
        raise _StopSubset(reason)

    def _ok(self, node: ast.AST) -> None:
        self.generic_visit(node)

    def visit(self, node: ast.AST):
        if isinstance(node, ast.stmt) and not isinstance(node, ALLOWED_STMT_TYPES):
            self._reject(
                f"statement type {type(node).__name__} not in the supported subset",
                node,
            )
        if isinstance(node, ast.expr) and not isinstance(node, ALLOWED_EXPR_TYPES):
            self._reject(
                f"expression type {type(node).__name__} not in the supported subset",
                node,
            )
        return super().visit(node)

    def _arg_name(self, arg) -> str:
        return getattr(arg, "arg", None) or getattr(arg, "name", None)

    # --- Module level ---------------------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Import) or isinstance(stmt, ast.ImportFrom):
                self._reject("imports not allowed", stmt)
            if isinstance(stmt, ast.ClassDef):
                self._reject("class definitions not allowed", stmt)
            self._ok(stmt)

    # --- Function def ---------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Reject nested function definitions
        for child in ast.walk(node):
            if child is not node and isinstance(child, ast.FunctionDef):
                self._reject("nested function definitions not allowed", child)
        # No decorators
        if node.decorator_list:
            self._reject("function decorators not allowed", node.decorator_list[0])
        # No annotations beyond simple int
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            arg_name = self._arg_name(arg)
            if arg.annotation is not None:
                ann_src = ast.unparse(arg.annotation)
                if ann_src not in ("int", "bool", "None"):
                    self._reject(
                        f"argument annotation {ann_src!r} not allowed (only int/bool/None)",
                        arg,
                    )
        if node.returns is not None:
            ret_src = ast.unparse(node.returns)
            if ret_src not in ("int", "bool", "None"):
                self._reject(
                    f"return annotation {ret_src!r} not allowed (only int/bool/None)",
                    node,
                )
        # No *args, **kwargs, default values, type-annotated locals with custom types
        if node.args.vararg is not None or node.args.kwarg is not None:
            self._reject("*args/**kwargs not allowed", node)
        if node.args.kw_defaults or node.args.defaults:
            self._reject("default argument values not allowed", node)
        self._ok(node)

    # --- Statements -----------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) != 1:
            self._reject("only single-target assignments allowed", node)
        self._ok(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if not isinstance(node.op, ALLOWED_AUGOPS):
            self._reject(
                f"augmented operator {type(node.op).__name__} not allowed", node
            )
        if isinstance(node.op, ast.Mult) and _constant_integer(node.value) is None:
            self._reject(
                "nonlinear augmented multiplication is not part of the QF-LIA subset",
                node,
            )
        if isinstance(node.op, (ast.FloorDiv, ast.Mod)):
            divisor = _constant_integer(node.value)
            if divisor is None or divisor == 0:
                self._reject(
                    "augmented floor division and modulo require a nonzero constant divisor",
                    node,
                )
        self._ok(node)

    def visit_If(self, node: ast.If) -> None:
        self._ok(node)

    def visit_For(self, node: ast.For) -> None:
        # Only `for x in range(...)` allowed
        if not isinstance(node.iter, ast.Call):
            self._reject("for-loop only over range(...)", node)
        if not isinstance(node.iter.func, ast.Name):
            self._reject("for-loop only over range(...)", node.iter)
        if node.iter.func.id != "range":
            self._reject("for-loop only over range(...)", node.iter)
        # Range args must be Const or simple Name (no nested calls)
        for arg in node.iter.args:
            if isinstance(arg, ast.Call):
                self._reject("range() argument must be constant/name", arg)
        self.used_builtins.add("range")
        self._ok(node)

    def visit_While(self, node: ast.While) -> None:
        # `while True` allowed as base case for infinite-loop invariant analysis,
        # but for the main pipeline we reject it to keep path enumeration finite.
        # The framework can be configured; we treat `while True` as allowed here
        # because the trusted symbolic executor handles it via invariant guards.
        self._ok(node)

    def visit_Return(self, node: ast.Return) -> None:
        self._ok(node)

    def visit_Break(self, node: ast.Break) -> None:
        pass

    def visit_Continue(self, node: ast.Continue) -> None:
        pass

    def visit_Pass(self, node: ast.Pass) -> None:
        pass

    def visit_Assert(self, node: ast.Assert) -> None:
        self._claim_seq += 1
        claim_id = f"assert_L{node.lineno}_#{self._claim_seq}"
        self.claims.append(
            ClaimInfo(
                claim_id=claim_id,
                source_line=node.lineno,
                expr_source=ast.unparse(node.test),
                expr_node=node.test,
            )
        )
        self._ok(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        # Only allow `assert False` or similar statements; otherwise reject.
        # `assert` itself is parsed as Assert, not Expr.
        self._reject("standalone expressions not allowed", node)

    # --- Expressions ----------------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, (int, bool, type(None))):
            self._reject(
                f"constant of type {type(node.value).__name__} not allowed (strings/lists/dicts violate QF-LIA)",
                node,
            )

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            # Name being stored must look like a simple identifier
            pass

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, ALLOWED_BINOPS):
            self._reject(
                f"binary operator {type(node.op).__name__} not allowed", node
            )
        if isinstance(node.op, ast.Mult):
            if not (_constant_integer(node.left) is not None or _constant_integer(node.right) is not None):
                self._reject(
                    "nonlinear multiplication is not part of the QF-LIA subset",
                    node,
                )
        elif isinstance(node.op, (ast.FloorDiv, ast.Mod)):
            divisor = _constant_integer(node.right)
            if divisor is None or divisor == 0:
                self._reject(
                    "floor division and modulo require a nonzero constant divisor",
                    node,
                )
        self._ok(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, ALLOWED_UNARYOPS):
            self._reject(
                f"unary operator {type(node.op).__name__} not allowed", node
            )
        self._ok(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not isinstance(node.op, ALLOWED_BOOLOPS):
            self._reject(
                f"boolean operator {type(node.op).__name__} not allowed", node
            )
        self._ok(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            if not isinstance(op, ALLOWED_COMPARATORS):
                self._reject(
                    f"comparison operator {type(op).__name__} not allowed", node
                )
        self._ok(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Only allow calls to bare names in ALLOWED_BUILTINS.
        if not isinstance(node.func, ast.Name):
            self._reject("only bare-name function calls allowed (no methods)", node)
        if node.func.id not in ALLOWED_BUILTINS:
            self._reject(
                f"function {node.func.id!r} not in builtin whitelist {sorted(ALLOWED_BUILTINS)}",
                node,
            )
        if node.keywords:
            self._reject("keyword arguments not allowed", node)
        self.used_builtins.add(node.func.id)
        self._ok(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._ok(node)  # ternary expression: a if c else b

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._reject("subscripting (list/dict/str index) not allowed", node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._reject("attribute access (object methods) not allowed", node)

    def visit_List(self, node: ast.List) -> None:
        self._reject("list literals not allowed", node)

    def visit_Tuple(self, node: ast.Tuple) -> None:
        self._reject("tuple literals not allowed", node)

    def visit_Dict(self, node: ast.Dict) -> None:
        self._reject("dict literals not allowed", node)

    def visit_Set(self, node: ast.Set) -> None:
        self._reject("set literals not allowed", node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._reject("lambda expressions not allowed", node)

    def visit_FormattedValue(self, node) -> None:
        self._reject("f-string / formatted value not allowed", node)

    def visit_JoinedStr(self, node) -> None:
        self._reject("f-string / joined string not allowed", node)


class _StopSubset(Exception):
    """Internal sentinel to short-circuit subset traversal."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _constant_integer(node: ast.AST) -> int | None:
    """Evaluate the small constant subset needed for linearity checks."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _constant_integer(node.operand)
        return -value if value is not None else None
    if isinstance(node, ast.BinOp):
        left = _constant_integer(node.left)
        right = _constant_integer(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv) and right != 0:
            return left // right
        if isinstance(node.op, ast.Mod) and right != 0:
            return left % right
    return None


# --- Public API ---------------------------------------------------------------


def check_and_normalize(source: str) -> SubsetResult:
    """Parse source, walk it under whitelist rules, extract claims from `assert`."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return SubsetResult(
            accepted=False,
            rejected_reason=f"SyntaxError: {e.msg}",
            rejected_source_line=e.lineno,
            rejected_source_text=(e.text or "").strip(),
        )
    visitor = SubsetVisitor()
    try:
        visitor.visit(tree)
    except _StopSubset as stop:
        # Surface the first rejection found
        # `errors` is appended _before_ raising, so it has at least one entry
        first = visitor.errors[-1]
        return SubsetResult(
            accepted=False,
            rejected_reason=first[0],
            rejected_node_kind=ast.dump(ast.parse("None", mode="eval").body)  # unused
            if False
            else None,
            rejected_source_line=first[1],
            rejected_source_text=first[2],
            extracted_claims=[],
            tree=tree,
        )
    return SubsetResult(
        accepted=True,
        rejected_reason=None,
        extracted_claims=visitor.claims,
        tree=tree,
        used_builtins=sorted(visitor.used_builtins),
    )


def validate_expression(expr: ast.expr) -> Optional[str]:
    """Validate a standalone claim expression against the same subset gate."""
    visitor = SubsetVisitor()
    try:
        visitor.visit(expr)
    except _StopSubset as stop:
        return stop.reason
    return None


def is_qf_lia_value(value) -> bool:
    """True iff a Python value (input/output literal) is QF-LIA compatible."""
    return isinstance(value, ALLOWED_INPUT_VALUE_TYPES)


__all__ = [
    "ALLOWED_STMT_TYPES",
    "ALLOWED_BINOPS",
    "ALLOWED_UNARYOPS",
    "ALLOWED_COMPARATORS",
    "ALLOWED_BOOLOPS",
    "ALLOWED_EXPR_TYPES",
    "ALLOWED_BUILTINS",
    "ALLOWED_INPUT_VALUE_TYPES",
    "ClaimInfo",
    "SubsetResult",
    "check_and_normalize",
    "validate_expression",
    "is_qf_lia_value",
]
