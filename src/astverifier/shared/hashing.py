"""AST-normalized structural hashing.

The raw dataset carries a ``duplicates`` field in its manifest, but that field
points at *upstream archive members* -- it does not mean two files in the
working tree are identical. Verified facts about ``Dataset/filtered``:

* raw byte hash: 14,785 / 14,785 unique
* ``normalized_sha256`` from the manifest: 14,785 / 14,785 unique
* AST-normalized hash (this module): 14,781 distinct groups -- 3 groups of
  true semantic duplicates covering 7 files

So the only dedup that actually removes anything is the third one. This
module implements it.

The normalization is deliberately conservative: it renames *local* variables
to positional names and drops statements that only write to ``junk_*``
scratch variables. It never renames function parameters, because a parameter
is part of the program signature and two programs with different signatures
are not interchangeable.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

# Statements whose only effect is to write a scratch variable are dropped.
_JUNK_PREFIX = "junk_"


@dataclass(frozen=True)
class StructuralFingerprint:
    """The structural identity of one program."""

    ast_hash: str
    """Hash of the normalized AST -- the semantic identity."""

    signature_hash: str
    """Hash of the function signature -- two programs with different
    signatures are not interchangeable even if their bodies coincide."""

    n_params: int
    n_loops: int
    n_branches: int


class _Normalizer(ast.NodeTransformer):
    """Rename locals positionally and delete ``junk_*`` scratch statements."""

    def __init__(self, protected: set[str]) -> None:
        self.protected = protected
        self.counter = 0
        self._scope: list[dict[str, str]] = [{}]

    # -- scope handling ----------------------------------------------------
    def _push(self) -> None:
        self._scope.append(dict(self._scope[-1]))

    def _pop(self) -> None:
        self._scope.pop()

    def _rename(self, name: str) -> str:
        if name in self.protected:
            return name
        for scope in reversed(self._scope):
            if name in scope:
                return scope[name]
        new_name = f"v{self.counter}"
        self.counter += 1
        self._scope[-1][name] = new_name
        return new_name

    # -- visitors ----------------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self._push()
        node.args = self.visit(node.args)
        # The function's own name stays: it identifies the program.
        # `body` is a list of statements; every other field is either a
        # single node or a list of nodes, so both are visited element-wise.
        for field_name in ("body", "decorator_list", "returns", "type_params"):
            if not hasattr(node, field_name):
                continue
            value = getattr(node, field_name)
            if value is None:
                continue
            if isinstance(value, list):
                setattr(node, field_name, [self.visit(item) for item in value])
            else:
                setattr(node, field_name, self.visit(value))
        self._pop()
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        # Parameters are part of the signature -- never renamed.
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, (ast.Store, ast.Load)):
            node.id = self._rename(node.id)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        self.generic_visit(node)
        if _only_writes_junk(node):
            return ast.Pass()
        return node

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        self.generic_visit(node)
        if _only_writes_junk(node):
            return ast.Pass()
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        # An `if` whose branches only contain junk writes is transparent.
        self.generic_visit(node)
        if _branch_is_junk_only(node.body) and _branch_is_junk_only(node.orelse):
            return ast.Pass()
        return node


def _only_writes_junk(node: ast.Assign | ast.AugAssign) -> bool:
    """True iff the statement only writes to ``junk_*`` scratch variables."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if not isinstance(target, ast.Name):
            return False
        if not target.id.startswith(_JUNK_PREFIX):
            return False
    return True


def _branch_is_junk_only(body: list[ast.stmt]) -> bool:
    """True iff every statement in ``body`` only writes to ``junk_*``."""
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, (ast.Assign, ast.AugAssign)) and _only_writes_junk(stmt):
            continue
        return False
    return True


def _protected_names(tree: ast.Module) -> set[str]:
    """Return the names that must keep their identity.

    Parameters and function names are part of the program's interface, so two
    programs with different signatures are never collapsed.
    """
    protected: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            protected.add(node.name)
            for arg in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            ):
                protected.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            for arg in (
                list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            ):
                protected.add(arg.arg)
    return protected


def signature_of(tree: ast.Module) -> tuple[str, ...]:
    """Return the parameter names of the first top-level function."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return tuple(
                arg.arg
                for arg in list(node.args.posonlyargs) + list(node.args.args)
            )
    return ()


def fingerprint(source: str) -> StructuralFingerprint:
    """Compute the structural fingerprint of one program.

    Raises:
        SyntaxError: The source does not parse.
    """
    tree = ast.parse(source)
    protected = _protected_names(tree)

    normalizer = _Normalizer(protected)
    normalized = normalizer.visit(ast.parse(source))
    ast.fix_missing_locations(normalized)

    ast_hash = hashlib.sha256(
        ast.dump(normalized, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()

    params = signature_of(tree)
    signature_hash = hashlib.sha256(
        ("|".join(params)).encode("utf-8")
    ).hexdigest()

    n_loops = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.While, ast.For)))
    n_branches = sum(1 for n in ast.walk(tree) if isinstance(n, ast.If))

    return StructuralFingerprint(
        ast_hash=ast_hash,
        signature_hash=signature_hash,
        n_params=len(params),
        n_loops=n_loops,
        n_branches=n_branches,
    )


def semantic_key(source: str) -> tuple[str, str]:
    """Return the ``(ast_hash, signature_hash)`` pair used for dedup.

    Two programs share a semantic key iff their normalized bodies coincide
    *and* they expose the same signature.
    """
    fp = fingerprint(source)
    return (fp.ast_hash, fp.signature_hash)


__all__ = [
    "StructuralFingerprint",
    "fingerprint",
    "semantic_key",
    "signature_of",
]
