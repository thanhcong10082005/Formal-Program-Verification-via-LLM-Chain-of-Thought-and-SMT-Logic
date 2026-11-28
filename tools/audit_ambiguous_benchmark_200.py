"""Check the ambiguous VERIFIED labels using cvc5 induction and CPython reachability."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"
COHORT = ROOT / "results" / "checkpoints" / "results_minicpm_original_asserts_200" / "program_ids.json"
OUTPUT = ROOT / "results" / "benchmark_200_label_audit"

INVARIANTS = {
    "x != 0": "x - y == i - j",
    "y < 1000": "x >= 1 and y >= 0 and x >= y",
    "y < 100000": "x >= 1 and y >= 0 and x >= y",
    "x < 500": "x == 0 or y <= z",
    "x > 0": "x >= 0",
    "i <= n": "i >= 0 and j >= i - 1 and n >= 0 and k >= 0",
}
OPERATORS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
    ast.Eq: "=", ast.NotEq: "distinct",
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.And: "and", ast.Or: "or",
}


def term(node: ast.expr, env: dict[str, str]) -> str:
    match node:
        case ast.Name(id=name):
            return env[name]
        case ast.Constant(value=value) if type(value) is int:
            return str(value) if value >= 0 else f"(- {-value})"
        case ast.BinOp(left=left, op=op, right=right) | ast.Compare(
            left=left, ops=[op], comparators=[right]
        ):
            return f"({OPERATORS[type(op)]} {term(left, env)} {term(right, env)})"
        case ast.BoolOp(op=op, values=values):
            return f"({OPERATORS[type(op)]} {' '.join(term(item, env) for item in values)})"
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            return negate(term(operand, env))
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return f"(- {term(operand, env)})"
        case _:
            raise ValueError(f"Unsupported expression: {ast.unparse(node)}")


def negate(value: str) -> str:
    return f"(not {value})"


def walk(
    statements: list[ast.stmt],
    env: dict[str, str],
    guards: tuple[str, ...],
    *,
    target: ast.Assert | None = None,
):
    """Enumerate straight-line paths, dropping returned paths."""
    if not statements:
        if target is None:
            yield env, guards
        return
    statement, *rest = statements
    match statement:
        case ast.Assign(targets=[ast.Name(id=name)], value=value):
            yield from walk(rest, {**env, name: term(value, env)}, guards, target=target)
        case ast.If(test=test, body=body, orelse=otherwise):
            condition = term(test, env)
            yield from walk(body + rest, env, (*guards, condition), target=target)
            yield from walk(otherwise + rest, env, (*guards, negate(condition)), target=target)
        case ast.Return(value=None) if target is None:
            return
        case ast.Pass():
            yield from walk(rest, env, guards, target=target)
        case ast.Assert() if statement is target:
            yield env, guards
        case _:
            raise ValueError(f"Unsupported statement: {ast.unparse(statement)}")


def query(name: str, assumptions: tuple[str, ...], conclusion: str) -> list[str]:
    return [
        f"; {name}", "(push 1)",
        *(f"(assert {value})" for value in assumptions),
        f"(assert {negate(conclusion)})", "(check-sat)", "(pop 1)",
    ]


def obligations(function: ast.FunctionDef, assertion: ast.Assert) -> tuple[str, list[str]]:
    (loop,) = [node for node in function.body if isinstance(node, ast.While)]
    if loop.orelse:
        raise ValueError("Loop else clauses are unsupported")
    inputs = {arg.arg: f"input_{arg.arg}" for arg in function.args.args}
    idx = function.body.index(loop)
    initial = list(walk(function.body[:idx], inputs, ()))
    if not initial or not all(env.keys() == initial[0][0].keys() for env, _ in initial):
        raise ValueError("Expected common initialized variables at the loop")
    state = {name: f"state_{name}" for name in initial[0][0]}
    invariant = INVARIANTS[ast.unparse(loop.test)]
    formula = ast.parse(invariant, mode="eval").body

    commands = ["(set-logic QF_LIA)", "(set-option :incremental true)"]
    commands.extend(f"(declare-const {symbol} Int)" for symbol in [*inputs.values(), *state.values()])
    for env, guards in initial:
        commands.extend(query("initialization", guards, term(formula, env)))
    assume = (term(formula, state), term(loop.test, state))
    for env, guards in walk(loop.body, state, assume):
        commands.extend(query("loop preservation", guards, term(formula, env)))
    exit_paths = list(walk(function.body[idx + 1:], state, (
        term(formula, state), negate(term(loop.test, state)),
    ), target=assertion))
    if not exit_paths:
        raise ValueError("No analyzed path reaches the target assertion")
    for env, guards in exit_paths:
        commands.extend(query("assertion safety at exit", guards, term(assertion.test, env)))
    return invariant, commands


def reach_witness(source: str, path: Path, function: ast.FunctionDef, assertion: ast.Assert) -> dict:
    values = {"_initial_x": 1, "_initial_y": 2, "_initial_n": 2}
    if ast.unparse(assertion.test) == "y == 0":
        values["_initial_y"] = 1
    inputs = {arg.arg: values.get(arg.arg, 0) for arg in function.args.args}
    scope = {}
    exec(compile(source, str(path), "exec", optimize=0), scope)
    hit = False
    steps = 0

    def trace(frame, event, arg):
        nonlocal hit, steps
        if event == "line":
            steps += 1
            if steps > 2_000_000:
                raise RuntimeError("Reachability witness exceeded its line budget")
            if frame.f_code.co_filename == str(path) and frame.f_lineno == assertion.lineno:
                hit = True
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        scope[function.name](**inputs)
    finally:
        sys.settrace(previous)
    if not hit:
        raise ValueError(f"Witness did not reach the assertion: {path}")
    return inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cvc5", required=True, type=Path)
    args = parser.parse_args()
    solver = str(args.cvc5.resolve())
    version = subprocess.check_output([solver, "--version"], text=True).splitlines()[0]
    manifest = json.loads((DATASET / "manifest.json").read_text())
    cohort = set(json.loads(COHORT.read_text()))
    rows = [row for row in manifest["programs"] if row["path"] in cohort]
    targets = [row for row in rows if row["expected_verdict"] == "VERIFIED" and row["ambiguous"]]
    if len(cohort) != 200 or len(rows) != 200 or len(targets) != 25:
        raise ValueError("Expected exactly 25 ambiguous VERIFIED labels in the 200-sample cohort")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    for row in targets:
        path = DATASET / row["path"]
        source = path.read_text()
        function, = ast.parse(source).body
        if (
            not isinstance(function, ast.FunctionDef) or function.decorator_list
            or function.returns or getattr(function, "type_params", ())
            or function.args.posonlyargs or function.args.kwonlyargs
            or function.args.vararg or function.args.kwarg or function.args.defaults
            or any(arg.annotation for arg in function.args.args)
        ):
            raise ValueError(f"Unsupported function signature: {path}")
        (assertion,) = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
        if row["claims"] != [ast.unparse(assertion.test)]:
            raise ValueError(f"Manifest assertion differs from the source: {path}")
        invariant, commands = obligations(function, assertion)
        proof_file = OUTPUT / f"{path.stem}.smt2"
        proof_file.write_text("\n".join(commands) + "\n")
        proc = subprocess.run(
            [solver, "--produce-proofs", "--check-proofs", str(proof_file)],
            check=True, text=True, capture_output=True, timeout=30,
        )
        checks = proc.stdout.splitlines()
        names = [line[2:] for line in commands if line.startswith("; ")]
        if checks != ["unsat"] * len(names):
            raise ValueError(f"Unproven obligations for {path}: {checks}")
        witness = reach_witness(source, path, function, assertion)
        results.append({
            "program_id": row["path"], "assertion": row["claims"][0], "line": assertion.lineno,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "loop_invariant": invariant, "obligations": list(zip(names, checks, strict=True)),
            "smt2": proof_file.name, "reachability_input": witness,
            "reachability": "CPython reached the original assert and it passed",
            "verified_verdict": "VERIFIED",
        })
    audit = {"solver": version, "arithmetic": "unbounded integers", "labels": results}
    (OUTPUT / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    for row in targets:
        row.update(gt_confidence="high", ambiguous=False, gt_source="independent_cvc5+cpython")
    (OUTPUT / "manifest.json").write_text(json.dumps({"programs": rows}, indent=2) + "\n")
    print(f"Independently verified {len(results)} labels with {version}")


if __name__ == "__main__":
    main()
