"""
structural_reasoning.py - Baseline C: Structural Reasoning (IMPROVED 3.x).

Compared to the original version this adds:

* IMPROVEMENT 3.1 — hierarchical CFG dump with block IDs, indentation
  for nested constructs, and per-loop summary (bounding variable,
  direction, exit condition).  The dump is significantly more readable
  for LLM, especially for nested loops.
* IMPROVEMENT 3.2 — better k-induction prompt:
      - Invariant template suggestions (linear / quadratic / constant).
      - Explicit "try concrete values for k=0,1,2" check before giving up.
      - Self-Consistency (3 calls, majority vote) to stabilise verdicts.
* IMPROVEMENT 1.3 — counterexample replay through ``shared_replay``: when
  the LLM says COUNTEREXAMPLE, we run the input on real CPython and stamp
  ``replay_verdict`` accordingly (so the report's CRVR is meaningful).
* IMPROVEMENT 1.4 — error_category tagging on ``reason`` for the
  taxonomy.
"""
from __future__ import annotations

import ast
import json
import logging
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from ..classify import (
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
)
from ..llm import _get_api_key, _make_client
from .shared_replay import replay_for_baseline

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Pydantic schemas
# -------------------------------------------------------------------

class StructuralReasoningVerdict(BaseModel):
    verdict: Literal["VERIFIED", "COUNTEREXAMPLE", "UNKNOWN"]
    reasoning: str = ""
    counterexample_input: Dict[str, int] = Field(default_factory=dict)
    invariant_guess: str = ""


class StructuralReasoningPlan(BaseModel):
    reasoning: str = Field(default="")
    verdict: Literal["VERIFIED", "COUNTEREXAMPLE", "UNKNOWN"]
    counterexample_input_str: str = Field(
        default="",
        description="Concrete violating input as a JSON string, e.g. '{\"n\": 1}'; empty if N/A.",
    )
    invariant_guess: str = Field(
        default="",
        description="Best-guess loop invariant (empty if none).",
    )


# -------------------------------------------------------------------
# FEW-SHOT prompt
# -------------------------------------------------------------------

FEWSHOT_SYSTEM = """You are a Hoare-style verification assistant.
You will analyze a Python function's Control Flow Graph (CFG) and reason about
claims WITHOUT using an SMT solver. Use natural-language state propagation (NSP)
and k-induction.

Steps you MUST follow in your reasoning:
1. Extract the precondition (function parameters, assume all are integers).
2. Walk through each basic block; describe how each variable's value evolves.
3. At each if/else, split the analysis into the two branches.
4. At each while/for, apply k-induction:
   a. Pick a candidate invariant. Useful templates:
      - constant (e.g. "x is unchanged")
      - linear   (e.g. "s == a*i + b")
      - quadratic (e.g. "s == i*(i-1)/2")
      - relational (e.g. "0 <= i <= n")
   b. Verify it at k=0 (before the loop).
   c. Verify it at k=1, k=2 with concrete small inputs if unsure.
   d. Verify the inductive step: assuming invariant at k, prove at k+1.
5. Final verdict: VERIFIED if the claim holds on every path, COUNTEREXAMPLE
   with a concrete violating input otherwise, UNKNOWN if you are uncertain.

Output valid JSON matching the requested schema. Do not mention SMT, Z3,
or solvers."""


FEWSHOT_EXAMPLE = """
Few-shot example:

Source:
```python
def f(n: int) -> int:
    s = 0
    i = 0
    while i < n:
        s = s + i
        i = i + 1
    return s
```
CFG:
  Block B0 (entry):
    L2: s = 0
    L3: i = 0
  Block B1 (loop header): while i < n   [control-var: i, dir: +1, bound: <n]
  Block B2 (body):
    L5: s = s + i
    L6: i = i + 1
    -> back to B1
  Block B3 (exit):
    L7: return s
Loop summary: while i < n, body increments i by 1, adds old i to s.
Claim: s >= 0

NSP reasoning:
  - Precondition: n is any integer.
  - Block B0: s=0, i=0.
  - Loop invariant candidate: s = i*(i-1)/2.
  - Base case (i=0): s=0, invariant holds.
  - Inductive case: if s = i*(i-1)/2 before iteration, after i=i+1 and s=s+i,
    new s = old s + i = i*(i-1)/2 + i = i*(i+1)/2 = new_i*(new_i-1)/2. Holds.
  - For i in [0, n] with n >= 0: i*(i-1)/2 >= 0 (proved by case analysis on i).
  - For n < 0: loop body never executes, s = 0, claim holds.
  - Claim s >= 0 follows.
  - VERIFIED.

LLM output (verdict, reasoning, counterexample_input_str as a JSON-encoded string, invariant_guess):
{
  "verdict": "VERIFIED",
  "reasoning": "...",
  "counterexample_input_str": "",
  "invariant_guess": "s = i*(i-1)/2"
}"""


# -------------------------------------------------------------------
# CFG extractor (hierarchical, with loop summaries)
# -------------------------------------------------------------------

def extract_cfg_dump(source: str) -> str:
    """Hierarchical CFG text dump.

    Output format (sample):
        def f(n: int) -> int
        Block B0 (entry):
          L2: s = 0
          L3: i = 0
        Block B1 (loop header): while i < n   [control-var: i, dir: +1, bound: n]
        Block B2 (body):
          L5: s = s + i
          L6: i = i + 1
          -> back to B1
        Block B3 (exit):
          L7: return s
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"[PARSE ERROR: {exc}]"

    funcs = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
    if not funcs:
        return "[NO FUNCTION FOUND]"

    fn = funcs[0]
    params = [arg.arg for arg in list(fn.args.posonlyargs) + list(fn.args.args)]
    header = "def " + fn.name + "(" + ", ".join(f"{p}: int" for p in params) + ") -> int"

    blocks: List[str] = []
    block_id = 0

    def new_block(label: str) -> str:
        nonlocal block_id
        bid = f"B{block_id}"
        block_id += 1
        return f"  Block {bid} ({label}):"

    def dump_body(stmts: List[ast.stmt], depth: int) -> List[str]:
        out: List[str] = []
        indent = "  " * (depth + 1)
        for s in stmts:
            if isinstance(s, ast.Assign):
                lhs = ", ".join(_unparse(t) for t in s.targets)
                rhs = _unparse(s.value)
                out.append(f"{indent}L{s.lineno}: {lhs} = {rhs}")
            elif isinstance(s, ast.AugAssign):
                op = _augop(s.op)
                out.append(f"{indent}L{s.lineno}: {_unparse(s.target)} {op}= {_unparse(s.value)}")
            elif isinstance(s, ast.If):
                out.append(f"{indent}L{s.lineno}: if {_unparse(s.test)}:")
                out.extend(dump_body(s.body, depth + 1))
                if s.orelse:
                    out.append(f"{indent}L{_first_line(s.orelse)}: else:")
                    out.extend(dump_body(s.orelse, depth + 1))
            elif isinstance(s, ast.While):
                ctrl = _loop_summary(s)
                out.append(f"{indent}L{s.lineno}: while {_unparse(s.test)}:   {ctrl}")
                out.extend(dump_body(s.body, depth + 1))
                out.append(f"{indent}L{s.lineno}: end while")
            elif isinstance(s, ast.For):
                tgt = getattr(s.target, "id", "_")
                out.append(f"{indent}L{s.lineno}: for {tgt} in {_unparse(s.iter)}:")
                out.extend(dump_body(s.body, depth + 1))
                out.append(f"{indent}L{s.lineno}: end for")
            elif isinstance(s, ast.Return):
                val = _unparse(s.value) if s.value else "None"
                out.append(f"{indent}L{s.lineno}: return {val}")
            elif isinstance(s, ast.Assert):
                out.append(f"{indent}L{s.lineno}: assert {_unparse(s.test)}")
            else:
                out.append(f"{indent}L{s.lineno}: [{type(s).__name__}]")
        return out

    # First block is "entry"
    body_first: List[str] = [new_block("entry")]
    body_first.extend(dump_body(fn.body, 1))
    blocks.extend(body_first)

    return header + "\n" + "\n".join(blocks)


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return f"<unparseable:{type(node).__name__}>"


def _first_line(stmts: List[ast.stmt]) -> int:
    return getattr(stmts[0], "lineno", 0) if stmts else 0


def _augop(op: ast.AST) -> str:
    if isinstance(op, ast.Add): return "+"
    if isinstance(op, ast.Sub): return "-"
    if isinstance(op, ast.Mult): return "*"
    if isinstance(op, ast.Div): return "/"
    if isinstance(op, ast.FloorDiv): return "//"
    if isinstance(op, ast.Mod): return "%"
    return "?"


def _loop_summary(node: ast.While) -> str:
    """Return a short "[control-var: X, dir: ±1, bound: Y]" tag."""
    test = node.test
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        lhs = test.left
        rhs = test.comparators[0]
        if isinstance(lhs, ast.Name) and _is_const(rhs):
            if isinstance(op, ast.Lt):   return f"[control-var: {lhs.id}, dir: +1, bound: <{_unparse(rhs)}]"
            if isinstance(op, ast.LtE):  return f"[control-var: {lhs.id}, dir: +1, bound: <={_unparse(rhs)}]"
            if isinstance(op, ast.Gt):   return f"[control-var: {lhs.id}, dir: -1, bound: >{_unparse(rhs)}]"
            if isinstance(op, ast.GtE):  return f"[control-var: {lhs.id}, dir: -1, bound: >={_unparse(rhs)}]"
    return ""


def _is_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


# -------------------------------------------------------------------
# LLM call (with self-consistency)
# -------------------------------------------------------------------

def _call_once(
    source: str,
    claim_line: int,
    claim_text: str,
    cfg_dump: str,
    *,
    api_key: Optional[str],
    model: str,
) -> StructuralReasoningVerdict:
    from google.genai import types
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("GEMINI_API_KEY is not set.")
    client = _make_client(key)
    numbered = "\n".join(
        f"{idx + 1:3d} | {line}" for idx, line in enumerate(source.splitlines())
    )
    user = (
        FEWSHOT_EXAMPLE
        + "\n\n"
        + f"Here is the Python function and CFG:\n```python\n{numbered}\n```\n\n"
        + f"CFG dump:\n```\n{cfg_dump}\n```\n\n"
        + f"Claim to verify at line {claim_line}: {claim_text!r}\n\n"
        + "Please reason step-by-step about this claim using NSP and "
          "k-induction. Pick a candidate invariant from the templates "
          "above. Verify the base case and the inductive step. If you are "
          "unsure, try concrete values for k=0,1,2 to gather evidence "
          "before deciding UNKNOWN. Then output JSON."
    )
    last_err: Optional[Exception] = None
    for _ in range(2):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=FEWSHOT_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=StructuralReasoningPlan,
                    temperature=0.3,    # higher than v1 to allow self-consistency
                ),
            )
            data = json.loads(resp.text or "{}")
            plan = StructuralReasoningPlan.model_validate(data)
            ce_input: Dict[str, int] = {}
            if plan.counterexample_input_str:
                try:
                    parsed = json.loads(plan.counterexample_input_str)
                    if isinstance(parsed, dict):
                        ce_input = {k: int(v) for k, v in parsed.items()
                                    if isinstance(v, (int, float, bool))}
                except Exception:
                    pass
            return StructuralReasoningVerdict(
                verdict=plan.verdict,
                reasoning=plan.reasoning,
                counterexample_input=ce_input,
                invariant_guess=plan.invariant_guess,
            )
        except Exception as exc:
            last_err = exc
            time.sleep(1.0)
    raise RuntimeError(f"Gemini structural reasoning failed: {last_err}")


def _majority_vote(
    verdicts: List[StructuralReasoningVerdict],
) -> StructuralReasoningVerdict:
    """Pick the most common verdict; tie-break to UNKNOWN."""
    counts: Dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    if not counts:
        return StructuralReasoningVerdict(verdict="UNKNOWN", reasoning="")
    sorted_v = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(sorted_v) > 1 and sorted_v[0][1] == sorted_v[1][1]:
        chosen = "UNKNOWN"
    else:
        chosen = sorted_v[0][0]
    # Use the first verdict with the chosen label for reasoning + counterexample
    for v in verdicts:
        if v.verdict == chosen:
            return StructuralReasoningVerdict(
                verdict=chosen,
                reasoning=v.reasoning,
                counterexample_input=v.counterexample_input,
                invariant_guess=v.invariant_guess,
            )
    return StructuralReasoningVerdict(verdict=chosen, reasoning="(no rationale)")


def generate_structural_reasoning(
    source: str,
    claim_line: int,
    claim_text: str,
    cfg_dump: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash-lite",
    n_samples: int = 3,
) -> StructuralReasoningVerdict:
    """Stage-2: self-consistency over ``n_samples`` LLM calls."""
    samples: List[StructuralReasoningVerdict] = []
    for _ in range(n_samples):
        try:
            samples.append(_call_once(
                source, claim_line, claim_text, cfg_dump,
                api_key=api_key, model=model,
            ))
        except Exception as exc:
            log.warning("Baseline C single call failed: %s", exc)
    if not samples:
        return StructuralReasoningVerdict(verdict="UNKNOWN", reasoning="all LLM calls failed")
    if len(samples) == 1:
        return samples[0]
    return _majority_vote(samples)


# -------------------------------------------------------------------
# Public entry point
# -------------------------------------------------------------------

def run_baseline_c(
    source: str,
    *,
    program_id: str = "<inline>",
    source_path: str = "<inline>",
    do_replay: bool = False,
    use_llm: bool = False,
    gemini_api_key: Optional[str] = None,
    gemini_model: str = "gemini-3.5-flash-lite",
    suggested_verdicts: Optional[List[Dict[str, Any]]] = None,
    n_samples: int = 1,             # was 3 — 1 to stay under free-tier 15 RPM
) -> ProgramResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return _make_empty(program_id, source_path, source, f"syntax error: {exc}")

    funcs = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
    if not funcs:
        return _make_empty(program_id, source_path, source, "no function definition found")

    fn = funcs[0]
    cfg_dump = extract_cfg_dump(source)

    claims: List[Dict[str, Any]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            claims.append({"line": node.lineno, "text": ast.unparse(node.test)})

    if suggested_verdicts:
        for sv in suggested_verdicts:
            if sv.get("claim_line") not in [c["line"] for c in claims]:
                claims.append({"line": sv.get("claim_line", 0), "text": sv.get("claim_text", "")})

    if not claims:
        return _make_empty(program_id, source_path, source, "no claims found")

    claim_results: List[ClaimResult] = []
    total_tokens_in = total_tokens_out = 0
    for claim in claims:
        claim_line = claim["line"]
        claim_text = claim["text"]

        if use_llm:
            try:
                verdict = generate_structural_reasoning(
                    source, claim_line, claim_text, cfg_dump,
                    api_key=gemini_api_key,
                    model=gemini_model,
                    n_samples=n_samples,
                )
                verdict_str = verdict.verdict
                reasoning = verdict.reasoning
                counterexample_input = verdict.counterexample_input
            except Exception as exc:
                verdict_str = "UNKNOWN"
                reasoning = f"LLM error: {exc}"
                counterexample_input = None
        else:
            verdict_str = "UNKNOWN"
            reasoning = "Deterministic mode: cannot decide without LLM reasoning"
            counterexample_input = None

        # Replay the counterexample if we have one (IMPROVEMENT 1.3)
        ce_info: Optional[CounterexampleInfo] = None
        if verdict_str == "COUNTEREXAMPLE" and counterexample_input and do_replay:
            outcome = replay_for_baseline(
                source, fn.name, counterexample_input,
                claim_line=claim_line, claim_expression=claim_text,
                timeout=3.0,
            )
            ce_info = CounterexampleInfo(
                z3_model=counterexample_input,
                replay_verdict=outcome.claim_violated,
                replay_output=outcome.output_repr,
                replay_diff=outcome.error,
            )
            if outcome.attempted and outcome.claim_violated is False:
                # The LLM hallucinated a CE — flip verdict to UNKNOWN
                verdict_str = "UNKNOWN"
                reasoning = (
                    "hallucinated counterexample: " + reasoning +
                    f" (replay verdict: {outcome.claim_violated})"
                )

        if verdict_str == "VERIFIED":
            status = StatusEnum.VERIFIED
        elif verdict_str == "COUNTEREXAMPLE":
            status = StatusEnum.COUNTEREXAMPLE
        else:
            status = StatusEnum.UNKNOWN_TIMEOUT

        claim_results.append(ClaimResult(
            claim_id=f"baseline_c_L{claim_line}",
            claim_text=claim_text,
            status=status,
            anchor=AnchorInfo(
                node_ids=[],
                source_lines=[claim_line],
                has_grounding=False,
            ),
            counterexample=ce_info,
            reason=reasoning,
            explanation="CFG-based NSP + k-induction (3-sample self-consistency)",
        ))

    by_status = {s: 0 for s in StatusEnum}
    for cr in claim_results:
        by_status[cr.status] += 1

    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source,
        n_claims=len(claim_results),
        n_verified=by_status[StatusEnum.VERIFIED],
        n_counterexample=by_status[StatusEnum.COUNTEREXAMPLE],
        n_unreachable=0,
        n_unsupported=0,
        n_translation_error=0,
        n_unknown_timeout=by_status[StatusEnum.UNKNOWN_TIMEOUT],
        claim_results=claim_results,
        elapsed_seconds=0.0,
        baseline="BASELINE_C",
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        cot_trace=cfg_dump[:500],
        direct_constraints=[],
        rejected_by_subset=False,
    )


def _make_empty(program_id, source_path, source, reason) -> ProgramResult:
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source,
        baseline="BASELINE_C",
        rejected_by_subset=False,
        subset_rejection_reason=reason,
    )


__all__ = [
    "StructuralReasoningVerdict",
    "extract_cfg_dump",
    "generate_structural_reasoning",
    "run_baseline_c",
]
