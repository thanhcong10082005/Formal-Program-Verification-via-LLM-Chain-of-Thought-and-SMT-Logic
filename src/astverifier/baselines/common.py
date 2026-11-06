"""Shared helpers for the LLM-driven baselines.

The nine LLM baselines share three things:

* a way to render the program for the model,
* a way to turn the model's free-form answer into a verdict, and
* a way to ask for a JSON object and parse it.

They differ in *what* they ask and *what they do with the answer* -- that
difference is the whole point of the comparison -- but the plumbing lives
here so that each baseline file stays readable.
"""

from __future__ import annotations

import ast
from typing import Any

from ..shared.claims import Claim
from ..shared.schemas import StatusEnum
from .llm import LLMClient, LLMReply, TokenBudget

#: The verdict vocabulary a baseline may report.
VERDICTS = ("VERIFIED", "COUNTEREXAMPLE", "UNREACHABLE", "UNKNOWN_TIMEOUT")

#: Map a free-form verdict word onto the shared status enum.
_VERDICT_ALIASES = {
    "verified": StatusEnum.VERIFIED,
    "true": StatusEnum.VERIFIED,
    "valid": StatusEnum.VERIFIED,
    "holds": StatusEnum.VERIFIED,
    "counterexample": StatusEnum.COUNTEREXAMPLE,
    "counter-example": StatusEnum.COUNTEREXAMPLE,
    "false": StatusEnum.COUNTEREXAMPLE,
    "violated": StatusEnum.COUNTEREXAMPLE,
    "violation": StatusEnum.COUNTEREXAMPLE,
    "unreachable": StatusEnum.UNREACHABLE,
    "not reachable": StatusEnum.UNREACHABLE,
    "unknown": StatusEnum.UNKNOWN_TIMEOUT,
    "unknown_timeout": StatusEnum.UNKNOWN_TIMEOUT,
    "timeout": StatusEnum.UNKNOWN_TIMEOUT,
    "unsupported": StatusEnum.UNSUPPORTED,
    "translation_error": StatusEnum.TRANSLATION_ERROR,
}


def numbered_source(source: str) -> str:
    """Render the source with 1-based line numbers."""
    return "\n".join(
        f"{index + 1:3d} | {line}" for index, line in enumerate(source.splitlines())
    )


def program_prompt(spec, *, task: str, extra: str = "") -> str:
    """Render the standard program prompt for an LLM baseline.

    Args:
        spec: The program under verification.
        task: What the baseline asks the model to do.
        extra: Any extra instruction appended to the prompt.
    """
    parts = [
        task.strip(),
        "",
        "```python",
        numbered_source(spec.source),
        "```",
    ]
    if spec.claims:
        parts += ["", "Claims to verify:"]
        for claim in spec.claims:
            line = f"- (line {claim.source_line}) {claim.text}"
            if claim.precondition:
                line += f"   [only required when: {claim.precondition}]"
            parts.append(line)
    if spec.precondition:
        parts += ["", f"Path precondition of the program: {spec.precondition}"]
    if extra:
        parts += ["", extra.strip()]
    parts += [
        "",
        "Answer with a JSON object and nothing else.",
    ]
    return "\n".join(parts)


def normalize_verdict(value: Any) -> StatusEnum:
    """Map a model's verdict word onto a :class:`StatusEnum` member.

    An unrecognised verdict becomes ``UNKNOWN_TIMEOUT`` -- never a guess.
    """
    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "_")
        if key in _VERDICT_ALIASES:
            return _VERDICT_ALIASES[key]
        if key.replace("_", "-") in _VERDICT_ALIASES:
            return _VERDICT_ALIASES[key.replace("_", "-")]
    return StatusEnum.UNKNOWN_TIMEOUT


def ask_json(
    client: LLMClient,
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.0,
    budget: TokenBudget | None = None,
) -> Any:
    """Ask the model for a JSON object and parse it.

    Returns ``None`` when the model replied with something that is not JSON;
    the caller then reports ``UNKNOWN_TIMEOUT``.
    """
    reply = client.complete(prompt, system=system, temperature=temperature)
    if budget is not None:
        budget.add(reply)
    try:
        return reply.json()
    except ValueError:
        return None


def ask_text(
    client: LLMClient,
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.0,
    budget: TokenBudget | None = None,
) -> LLMReply:
    """Ask the model for free-form text."""
    reply = client.complete(prompt, system=system, temperature=temperature)
    if budget is not None:
        budget.add(reply)
    return reply


def extract_claim_text(payload: Any, fallback: Claim) -> str:
    """Read a claim expression out of a model's JSON answer."""
    if isinstance(payload, dict):
        for key in ("claim", "claim_text", "expression", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback.text


def parse_witness(payload: Any) -> dict[str, int]:
    """Read a concrete input out of a model's JSON answer.

    The model may answer with a dict of integers, or with a list of
    ``{"name": ..., "value": ...}`` objects. Anything else yields ``{}``.
    """
    if not isinstance(payload, dict):
        return {}
    for key in ("witness", "counterexample_input", "input", "inputs"):
        value = payload.get(key)
        if isinstance(value, dict):
            return {
                str(k): int(v)
                for k, v in value.items()
                if isinstance(v, int) and not isinstance(v, bool)
            }
        if isinstance(value, list):
            out: dict[str, int] = {}
            for item in value:
                if (
                    isinstance(item, dict)
                    and "name" in item
                    and isinstance(item.get("value"), int)
                    and not isinstance(item.get("value"), bool)
                ):
                    out[str(item["name"])] = int(item["value"])
            return out
    return {}


def find_function(source: str) -> ast.FunctionDef | None:
    """Return the first top-level function of ``source``, if any."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node
    return None


def program_shape_summary(source: str) -> str:
    """Return a compact structural summary of the program.

    The summary is what the structural-reasoning baselines show the model in
    place of the raw source: the control-flow skeleton plus the loop.
    """
    func = find_function(source)
    if func is None:
        return "(the program could not be parsed)"

    lines: list[str] = [f"function {func.name}({', '.join(_params(func))}):"]
    for stmt in func.body:
        lines.append("  " + _describe(stmt, indent=1))
    return "\n".join(lines)


def _params(func: ast.FunctionDef) -> list[str]:
    return [arg.arg for arg in func.args.args]


def _describe(stmt: ast.stmt, indent: int) -> str:
    """Describe one statement structurally."""
    if isinstance(stmt, ast.While):
        return (
            f"while {ast.unparse(stmt.test)}:\n"
            + "\n".join(_describe(inner, indent + 1) for inner in stmt.body)
        )
    if isinstance(stmt, ast.If):
        head = f"if {ast.unparse(stmt.test)}:"
        body = "\n".join(_describe(inner, indent + 1) for inner in stmt.body)
        if stmt.orelse:
            body += "\n" + "\n".join(
                _describe(inner, indent + 1) for inner in stmt.orelse
            )
        return head + "\n" + body
    if isinstance(stmt, ast.Assert):
        return f"assert {ast.unparse(stmt.test)}"
    if isinstance(stmt, ast.Assign):
        return f"{ast.unparse(stmt)}"
    if isinstance(stmt, ast.AugAssign):
        return f"{ast.unparse(stmt)}"
    if isinstance(stmt, ast.Return):
        return f"return {ast.unparse(stmt.value) if stmt.value else ''}".rstrip()
    if isinstance(stmt, ast.Expr):
        return f"{ast.unparse(stmt.value)}"
    return type(stmt).__name__


__all__ = [
    "VERDICTS",
    "ask_json",
    "ask_text",
    "extract_claim_text",
    "find_function",
    "normalize_verdict",
    "numbered_source",
    "parse_witness",
    "program_prompt",
    "program_shape_summary",
]
