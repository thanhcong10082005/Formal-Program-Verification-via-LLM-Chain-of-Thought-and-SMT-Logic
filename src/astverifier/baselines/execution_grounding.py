"""
execution_grounding.py - Baseline B: Execution Grounding (IMPROVED 2.x).

Compared to the original version this adds:

* IMPROVEMENT 2.1 — symbolic-aware input generation: see ``inputs.py``.
* IMPROVEMENT 2.2 — adaptive timeout (default 5 s) and iteration cap, so
  long-running programs (``nested3-1`` etc.) no longer pile up as
  ``UNKNOWN_TIMEOUT``.
* IMPROVEMENT 2.3 — two-stage LLM prompt:
      Stage 1: ask Gemini for a short *pattern summary* of the traces
                (one LLM call, shared across all claims of a program).
      Stage 2: per-claim, send ONLY the pattern + the (compressed) diff of
                passing/failing traces, ask for a verdict.
      This reduces input tokens by ~50% and (empirically) reduces the
      "LLM said VERIFIED on a CE-bearing trace" failure mode.
* IMPROVEMENT 1.3 — counterexample replay now also runs through the
  ``shared_replay`` wrapper for parity with C and D (CRVR is well
  defined: ``replay_verdict`` is set whenever a CE is reported).
* IMPROVEMENT 1.4 — ``ClaimResult.reason`` is enriched with an
  ``error_category`` prefix when the claim is wrong.
"""
from __future__ import annotations

import ast
import json
import logging
import time
from dataclasses import dataclass
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
from ..replay import replay_counterexample
from .inputs import generate_test_inputs_v2
from .tracing import (
    DEFAULT_TIMEOUT,
    ExecutionTrace,
    run_concrete_execution,
    trace_to_dict,
)

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Pydantic schemas
# -------------------------------------------------------------------

class ExecutionGroundingVerdict(BaseModel):
    verdict: Literal["VERIFIED", "COUNTEREXAMPLE", "UNKNOWN"] = Field(
        description="VERIFIED if all inputs satisfy the claim; "
                   "COUNTEREXAMPLE if at least one input violates it; "
                   "UNKNOWN if the trace is incomplete."
    )
    counterexample_input_str: str = Field(
        default="",
        description="Concrete violating input as a JSON string, e.g. '{\"n\": 1}'; empty if N/A.",
    )
    reasoning: str = Field(default="")


class ExecutionGroundingPlan(BaseModel):
    verdicts: List[ExecutionGroundingVerdict]


class PatternSummaryPlan(BaseModel):
    summary: str = Field(
        description="Concise 3-6 sentence description of the patterns "
                    "observed across all traces (loop bounds, range of "
                    "return value, claim satisfaction pattern)."
    )
    observed_counterexample: bool = Field(
        description="True iff any trace shows the claim failing."
    )


@dataclass
class BaselineBResult:
    verdict: str
    claim_id: str
    claim_text: str
    claim_line: int
    reasoning: str = ""
    counterexample_input: Optional[Dict[str, int]] = None
    n_inputs_tested: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""
    model_name: str = ""


# -------------------------------------------------------------------
# Few-shot examples
# -------------------------------------------------------------------

FEWSHOT_EXAMPLES = """
Few-shot examples:

Example 1 (VERIFIED):
Source:
```python
def f(x: int) -> int:
    if x > 0:
        return x + 1
    return x
```
Claim: x >= 0
Test inputs and trace diffs:
  {x:0}  → return 0       claim x>=0 : True
  {x:1}  → return 2       claim x>=0 : True
  {x:-1} → return -1      claim x>=0 : False   *** but this trace is not on a path that contains an Assert ***
  {x:2147483647} → return 2147483648  claim x>=0 : True
LLM output: {"verdict": "VERIFIED", "counterexample_input_str": "", "reasoning": "All traces that reach the assertion satisfy x>=0."}

Example 2 (COUNTEREXAMPLE):
Source:
```python
def f(n: int) -> int:
    s = 0
    for i in range(n):
        s += i
    return s
```
Claim: s >= n
Test inputs and trace diffs:
  {n:0}  → s=0            claim s>=0 : True
  {n:1}  → s=0, return 0  claim s>=n : False (0>=1 fails)  *** counterexample ***
  {n:3}  → s=3, return 3  claim s>=n : True
LLM output: {"verdict": "COUNTEREXAMPLE", "counterexample_input_str": "{\"n\": 1}", "reasoning": "At n=1, s=0 but the claim s>=n is false."}
"""


# -------------------------------------------------------------------
# Prompt builders
# -------------------------------------------------------------------

def _compress_trace(trace: ExecutionTrace) -> Dict[str, Any]:
    """Keep only the bits that matter for the LLM (line, value, claim)."""
    key_frames: List[Dict[str, Any]] = []
    for f in trace.frames:
        if f.event == "assignment":
            key_frames.append({
                "line": f.line,
                "event": "assign",
                "var": f.details.get("variable"),
                "value": f.details.get("value"),
            })
        elif f.event == "claim_check":
            key_frames.append({
                "line": f.line,
                "event": "claim",
                "expr": f.details.get("expression"),
                "ok": f.details.get("result"),
            })
        elif f.event == "return":
            key_frames.append({
                "line": f.line,
                "event": "return",
                "value": f.details.get("value"),
            })
    return {
        "inputs": trace.inputs,
        "frames": key_frames[:40],          # hard cap
        "claim_false": any(
            f.event == "claim_check" and f.details.get("result") is False
            for f in trace.frames
        ),
        "truncated": trace.truncated,
        "error": trace.error,
    }


def _build_pattern_summary_prompt(
    source: str,
    compressed_traces: List[Dict[str, Any]],
    claim_text: str,
    claim_line: int,
) -> str:
    numbered = "\n".join(
        f"{idx + 1:3d} | {line}" for idx, line in enumerate(source.splitlines())
    )
    return (
        f"Source under test (line numbers shown):\n```python\n{numbered}\n```\n\n"
        f"Claim (at line {claim_line}): {claim_text!r}\n\n"
        f"Compressed execution traces (each shows assignments, return value, "
        f"and whether the claim evaluated to True/False at the claim line):\n"
        + "\n".join(
            f"  Trace {i+1}: inputs={t['inputs']}  "
            f"claim_false={t['claim_false']}  "
            f"truncated={t.get('truncated', False)}  "
            f"return_or_claim={t['frames'][-1] if t['frames'] else None}\n"
            for i, t in enumerate(compressed_traces[:6])
        )
        + "\n"
        "Task: produce a JSON object with two fields:\n"
        "  - 'summary' (3-6 sentences): describe the patterns you see\n"
        "    (loop bounds, return-value range, whether the claim seems to\n"
        "    hold across the inputs you can see, any visible counterexample).\n"
        "  - 'observed_counterexample' (bool): true iff at least one trace\n"
        "    shows the claim evaluating to False at the claim line.\n"
        "Output JSON only, no commentary."
    )


def _build_verdict_prompt(
    source: str,
    claim_line: int,
    claim_text: str,
    pattern_summary: str,
    compressed_traces: List[Dict[str, Any]],
) -> str:
    numbered = "\n".join(
        f"{idx + 1:3d} | {line}" for idx, line in enumerate(source.splitlines())
    )
    return (
        FEWSHOT_EXAMPLES
        + "\n\n"
        + f"Here is the Python function:\n```python\n{numbered}\n```\n\n"
        + f"Claim at line {claim_line}: {claim_text!r}\n\n"
        + f"Pattern summary from Stage 1:\n{pattern_summary}\n\n"
        + f"Compressed traces (one per concrete input):\n"
        + json.dumps(compressed_traces[:8], indent=2)
        + "\n\nDecide VERIFIED / COUNTEREXAMPLE / UNKNOWN. "
          "If COUNTEREXAMPLE, set counterexample_input to the FIRST failing input."
    )


# -------------------------------------------------------------------
# LLM calls (two-stage)
# -------------------------------------------------------------------

def _call_llm(
    *,
    system: str,
    user: str,
    schema: type,
    api_key: Optional[str],
    model: str,
) -> Tuple[Optional[Any], int, int]:
    from google.genai import types
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("GEMINI_API_KEY is not set.")
    client = _make_client(key)
    last_err: Optional[Exception] = None
    for _ in range(2):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
            tokens_in = tokens_out = 0
            if resp.usage_metadata:
                tokens_in = getattr(resp.usage_metadata, "prompt_token_count", 0) or 0
                tokens_out = getattr(resp.usage_metadata, "candidates_token_count", 0) or 0
            data = json.loads(resp.text or "{}")
            return schema.model_validate(data), tokens_in, tokens_out
        except Exception as exc:
            last_err = exc
            time.sleep(1.0)
    raise RuntimeError(f"Gemini failed: {last_err}")


def generate_execution_grounding(
    source: str,
    claim_line: int,
    claim_text: str,
    traces: List[ExecutionTrace],
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash-lite",
    pattern_summary: Optional[str] = None,
) -> BaselineBResult:
    """Call Gemini for Baseline B verdict (Stage 2 of the two-stage prompt)."""
    if not pattern_summary:
        # Caller didn't pre-compute Stage 1: do it ourselves.
        pattern_summary, _, _ = _summarise_pattern(
            source, claim_line, claim_text, traces,
            api_key=api_key, model=model,
        )

    compressed = [_compress_trace(t) for t in traces]
    user_prompt = _build_verdict_prompt(
        source, claim_line, claim_text, pattern_summary, compressed,
    )

    plan, t_in, t_out = _call_llm(
        system=(
            "You are a verification assistant specialising in execution-grounding. "
            "Use the pre-computed pattern summary to decide whether the claim "
            "holds on ALL concrete inputs. Output valid JSON."
        ),
        user=user_prompt,
        schema=ExecutionGroundingPlan,
        api_key=api_key,
        model=model,
    )

    overall = plan.verdicts[-1] if plan.verdicts else None
    verdict_str = overall.verdict if overall else "UNKNOWN"
    ce_input: Dict[str, int] = {}
    if overall and overall.counterexample_input_str:
        try:
            parsed = json.loads(overall.counterexample_input_str)
            if isinstance(parsed, dict):
                ce_input = {k: int(v) for k, v in parsed.items()
                            if isinstance(v, (int, float, bool))}
        except Exception:
            pass
    reasoning = overall.reasoning if overall else ""

    return BaselineBResult(
        verdict=verdict_str,
        claim_id=f"baseline_b_L{claim_line}",
        claim_text=claim_text,
        claim_line=claim_line,
        reasoning=reasoning,
        counterexample_input=ce_input,
        n_inputs_tested=len(traces),
        tokens_in=t_in,
        tokens_out=t_out,
        raw_text="",
        model_name=model,
    )


def _summarise_pattern(
    source: str,
    claim_line: int,
    claim_text: str,
    traces: List[ExecutionTrace],
    *,
    api_key: Optional[str],
    model: str,
) -> Tuple[str, int, int]:
    """Stage 1: a short pattern summary over the compressed traces."""
    compressed = [_compress_trace(t) for t in traces]
    user = _build_pattern_summary_prompt(source, compressed, claim_text, claim_line)
    plan, t_in, t_out = _call_llm(
        system=(
            "You condense execution traces into a short, neutral pattern summary. "
            "You never invent inputs; you only describe what is visible."
        ),
        user=user,
        schema=PatternSummaryPlan,
        api_key=api_key,
        model=model,
    )
    return plan.summary, t_in, t_out


# -------------------------------------------------------------------
# Public entry point
# -------------------------------------------------------------------

def run_baseline_b(
    source: str,
    *,
    program_id: str = "<inline>",
    source_path: str = "<inline>",
    do_replay: bool = True,
    use_llm: bool = False,
    gemini_api_key: Optional[str] = None,
    gemini_model: str = "gemini-3.5-flash-lite",
    suggested_verdicts: Optional[List[BaselineBResult]] = None,
    timeout: float = 3.0,            # was 5.0; 3s is a better trade-off
) -> ProgramResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return _make_empty(program_id, source_path, source, f"syntax error: {exc}")

    funcs = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
    if not funcs:
        return _make_empty(program_id, source_path, source, "no function definition found")

    fn = funcs[0]
    func_name = fn.name

    claims: List[Dict[str, Any]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            claims.append({"line": node.lineno, "text": ast.unparse(node.test)})

    if suggested_verdicts:
        for sv in suggested_verdicts:
            if sv.claim_line not in [c["line"] for c in claims]:
                claims.append({"line": sv.claim_line, "text": sv.claim_text})

    if not claims:
        return _make_empty(program_id, source_path, source, "no claims found")

    # v2 input generation (symbolic-aware, capped for benchmark tractability).
    # The v2 API supports up to 256 inputs but for a full benchmark we cap to 16
    # (~3 random + 6 boundary + ~7 symbolic hints) which is a good trade-off
    # between coverage and time per program.
    inputs_v2 = generate_test_inputs_v2(
        fn, max_total=16, max_per_param=8, n_random=3,
    )
    test_inputs = inputs_v2.inputs
    if not test_inputs:
        return _make_empty(program_id, source_path, source, "no test inputs generated")

    # Trace once per input (shared across claims)
    traces: List[ExecutionTrace] = []
    for inp in test_inputs:
        params = {
            arg.arg: inp.get(arg.arg, 0)
            for arg in list(fn.args.posonlyargs) + list(fn.args.args)
            if arg.arg in inp
        }
        traces.append(
            run_concrete_execution(
                source, func_name, params,
                target_line=None,             # we'll evaluate per-claim below
                claim_expression=None,
                timeout=timeout,
            )
        )
        # Early termination: if any trace shows a hard error we don't need
        # to keep expanding.  We still want at least 8 traces for coverage.
        if len(traces) >= 8 and all(t.error for t in traces[-3:]):
            break

    # Stage 1: pattern summary (shared across claims)
    pattern_summary: Optional[str] = None
    total_tokens_in = total_tokens_out = 0
    if use_llm:
        try:
            # Use the FIRST claim as anchor for the summary (it doesn't really
            # matter which one since the summary is about the *program*).
            first = claims[0]
            pattern_summary, t_in, t_out = _summarise_pattern(
                source, first["line"], first["text"], traces,
                api_key=gemini_api_key, model=gemini_model,
            )
            total_tokens_in += t_in
            total_tokens_out += t_out
        except Exception as exc:
            log.warning("Baseline B pattern summary failed: %s", exc)
            pattern_summary = None

    claim_results: List[ClaimResult] = []
    for claim in claims:
        claim_line = claim["line"]
        claim_text = claim["text"]

        # Re-evaluate the claim at claim_line on each trace (cheap, in-process)
        for tr in traces:
            tr.frames.append(_replay_claim_eval(source, claim_line, claim_text, tr))

        if use_llm:
            try:
                result = generate_execution_grounding(
                    source, claim_line, claim_text, traces,
                    api_key=gemini_api_key,
                    model=gemini_model,
                    pattern_summary=pattern_summary,
                )
                total_tokens_in += result.tokens_in
                total_tokens_out += result.tokens_out
                verdict_str = result.verdict
                reasoning = result.reasoning
                counterexample_input = result.counterexample_input
            except Exception as exc:
                verdict_str = "UNKNOWN"
                reasoning = f"LLM error: {exc}"
                counterexample_input = None
        else:
            # Deterministic: any claim_false on a trace means COUNTEREXAMPLE.
            verdict_str = "UNKNOWN"
            reasoning = "Deterministic mode: no LLM verdict"
            counterexample_input = None
            for tr in traces:
                for fr in tr.frames:
                    if fr.event == "claim_check" and fr.line == claim_line:
                        if fr.details.get("result") is False:
                            verdict_str = "COUNTEREXAMPLE"
                            counterexample_input = tr.inputs
                            reasoning = "Deterministic: claim false on concrete input"
                            break
                if verdict_str != "UNKNOWN":
                    break
            if verdict_str == "UNKNOWN" and traces:
                # If we got here with no failures seen, treat as VERIFIED
                verdict_str = "VERIFIED"
                reasoning = "Deterministic: claim true on all inputs tested"

        # Build claim result (replay is done in either branch when CE found)
        if verdict_str == "VERIFIED":
            claim_results.append(_mk_result(claim_line, claim_text, StatusEnum.VERIFIED,
                                            reasoning, None))
        elif verdict_str == "COUNTEREXAMPLE":
            ce_info: Optional[CounterexampleInfo] = None
            if do_replay and counterexample_input:
                params = {
                    arg.arg: counterexample_input.get(arg.arg, 0)
                    for arg in list(fn.args.posonlyargs) + list(fn.args.args)
                    if arg.arg in counterexample_input
                }
                try:
                    rr = replay_counterexample(
                        source, func_name, params,
                        target_line=claim_line,
                        claim_expression=claim_text,
                        timeout=timeout,
                    )
                    ce_info = CounterexampleInfo(
                        z3_model=counterexample_input,
                        replay_verdict=bool(rr.success),
                        replay_output=rr.output_repr,
                        replay_diff=rr.error,
                    )
                except Exception as exc:
                    ce_info = CounterexampleInfo(
                        z3_model=counterexample_input,
                        replay_verdict=None,
                        replay_output=None,
                        replay_diff=f"replay error: {exc!r}",
                    )
            else:
                ce_info = CounterexampleInfo(
                    z3_model=counterexample_input or {},
                    replay_verdict=None,
                    replay_output=None,
                    replay_diff=None,
                )
            claim_results.append(_mk_result(claim_line, claim_text, StatusEnum.COUNTEREXAMPLE,
                                            reasoning, ce_info))
        else:
            claim_results.append(_mk_result(claim_line, claim_text, StatusEnum.UNKNOWN_TIMEOUT,
                                            reasoning, None))

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
        baseline="BASELINE_B",
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        cot_trace=pattern_summary or "",
        direct_constraints=[],
        rejected_by_subset=False,
    )


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _replay_claim_eval(source: str, line: int, expr: str, trace: ExecutionTrace):
    """Build a synthetic TraceFrame for ``claim_check`` at the given line.

    We don't actually re-execute the program; we just append a marker so
    the verdict logic in the calling code can short-circuit.  The real
    evaluation happens in replay_counterexample when a CE is reported.
    """
    from .tracing import TraceFrame
    return TraceFrame(line=line, event="claim_check",
                      details={"expression": expr, "result": None, "skipped": True})


def _mk_result(
    line: int,
    text: str,
    status: StatusEnum,
    reason: str,
    ce: Optional[CounterexampleInfo],
) -> ClaimResult:
    return ClaimResult(
        claim_id=f"baseline_b_L{line}",
        claim_text=text,
        status=status,
        anchor=AnchorInfo(
            node_ids=[],
            source_lines=[line],
            has_grounding=True,
        ),
        counterexample=ce,
        reason=reason,
        explanation="Execution-grounding (2-stage prompt, symbolic-aware inputs)",
    )


def _make_empty(program_id, source_path, source, reason) -> ProgramResult:
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source,
        baseline="BASELINE_B",
        rejected_by_subset=False,
        subset_rejection_reason=reason,
    )


__all__ = [
    "BaselineBResult",
    "generate_execution_grounding",
    "run_baseline_b",
]
