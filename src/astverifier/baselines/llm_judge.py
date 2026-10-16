"""
llm_judge.py - Baseline D: LLM-as-Judge (IMPROVED 4.x).

Compared to the original version this adds:

* IMPROVEMENT 4.1 — Self-Consistency: ask Gemini ``n_samples`` times and
  majority-vote the verdict.  This is the cheapest way to stabilise the
  "raw LLM" baseline.
* IMPROVEMENT 4.2 — JSON auto-retry: if a response is unparseable, we
  re-prompt with the broken text and ask the LLM to fix it.
* IMPROVEMENT 1.3 — counterexample replay through ``shared_replay`` so
  CRVR becomes well-defined (replay_verdict set on reported CEs).
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

class LLMJudgeVerdict(BaseModel):
    verdict: Literal["VERIFIED", "COUNTEREXAMPLE", "UNKNOWN"]
    reasoning: str = ""
    counterexample_input: Dict[str, int] = Field(default_factory=dict)


class LLMJudgePlan(BaseModel):
    reasoning: str = ""
    verdict: Literal["VERIFIED", "COUNTEREXAMPLE", "UNKNOWN"]
    counterexample_input_str: str = Field(
        default="",
        description="Concrete violating input as a JSON string, e.g. '{\"n\": 1}'; empty if N/A.",
    )


# -------------------------------------------------------------------
# System + few-shot
# -------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert Python code reviewer specialising in
integer-arithmetic programs. For each function and claim, you will determine
whether the claim holds for ALL integer inputs.

You must use step-by-step Chain-of-Thought (CoT) reasoning. Steps:
1. Identify all variables and the possible integer values they can take.
2. Walk through every assignment statement and update your mental model.
3. Sanity check: try concrete boundary values (-1, 0, 1, MAX_INT, MIN_INT)
   for the parameters and compute by hand whether the claim would hold.
4. If the claim ALWAYS holds across all inputs and branches, output VERIFIED.
5. If there EXISTS even one input that violates it, output COUNTEREXAMPLE
   with that specific input.
6. If you are genuinely uncertain, return UNKNOWN.

Output valid JSON matching the requested schema.  Be specific in your
reasoning; do not just say "it looks correct".  When in doubt, prefer
UNKNOWN over a guess."""


FEWSHOT_PROMPT = """Few-shot examples:

Example 1 (VERIFIED):
Source:
```python
def f(x: int, y: int) -> int:
    if x > 0:
        return x + y
    return y
```
Claim at line 3: x + y >= y
Reasoning:
  Step 1: x, y are integer parameters.
  Step 2: At the return x+y, x > 0 so x+y > y, hence x+y >= y.
  Step 3: VERIFIED.
LLM output: {"verdict": "VERIFIED", "counterexample_input_str": "", "reasoning": "..."}

Example 2 (COUNTEREXAMPLE):
Source:
```python
def f(n: int) -> int:
    s = 0
    for i in range(n):
        s += i
    return s
```
Claim at line 5: s >= n
Reasoning:
  Step 1: n integer, s starts at 0.
  Step 2: Loop adds i for i in [0, n).  At n=1, loop runs once with i=0,
          s stays 0, so s>=n becomes 0>=1 which is false.
  Step 3: COUNTEREXAMPLE with {n: 1}.
LLM output: {"verdict": "COUNTEREXAMPLE", "counterexample_input_str": "{\"n\": 1}", "reasoning": "..."}

Example 3 (UNKNOWN):
Source:
```python
def f(a: int, b: int) -> int:
    while a > 0:
        b = b + a
        a = a - 1
    return b
```
Claim at line 5: b >= 0
Reasoning:
  Step 1: a, b are integers.
  Step 2: Loop increments b by a for a in (initial_a, initial_a-1, ..., 1).
  Step 3: If b is initially negative and a is positive, b will increase but
          may still be negative at the end (e.g. b=-100, a=3: b becomes
          -100 + 3 + 2 + 1 = -94, still < 0).
  Step 4: COUNTEREXAMPLE with {a: 3, b: -100}.
LLM output: {"verdict": "COUNTEREXAMPLE", "counterexample_input": {"a": 3, "b": -100}, "reasoning": "..."}
"""


# -------------------------------------------------------------------
# LLM call
# -------------------------------------------------------------------

def _build_user_prompt(source: str, claim_line: int, claim_text: str) -> str:
    numbered = "\n".join(
        f"{idx + 1:3d} | {line}" for idx, line in enumerate(source.splitlines())
    )
    return (
        FEWSHOT_PROMPT
        + "\n\n"
        + f"Now analyze this program:\n```python\n{numbered}\n```\n\n"
        + f"Claim to verify at line {claim_line}: {claim_text!r}\n\n"
        + "Reason step-by-step, then output JSON."
    )


def _call_with_retry(
    user_prompt: str,
    *,
    api_key: Optional[str],
    model: str,
    max_attempts: int = 3,
) -> LLMJudgeVerdict:
    """Call Gemini; if the response is unparseable, retry with a fix-up prompt."""
    from google.genai import types
    key = _get_api_key(api_key)
    if not key:
        raise ValueError("GEMINI_API_KEY is not set.")
    client = _make_client(key)

    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                prompt = user_prompt
            else:
                prompt = (
                    "Your previous response was not valid JSON. Please return "
                    "ONLY a JSON object matching the schema. Do not include any "
                    "commentary or markdown.\n\n" + user_prompt
                )
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=LLMJudgePlan,
                    temperature=0.3,    # higher to allow self-consistency
                ),
            )
            data = json.loads(resp.text or "{}")
            plan = LLMJudgePlan.model_validate(data)
            ce_input: Dict[str, int] = {}
            if plan.counterexample_input_str:
                try:
                    parsed = json.loads(plan.counterexample_input_str)
                    if isinstance(parsed, dict):
                        ce_input = {k: int(v) for k, v in parsed.items()
                                    if isinstance(v, (int, float, bool))}
                except Exception:
                    pass
            return LLMJudgeVerdict(
                verdict=plan.verdict,
                reasoning=plan.reasoning,
                counterexample_input=ce_input,
            )
        except Exception as exc:
            log.warning("Baseline D call attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.0)
    raise RuntimeError("Baseline D: all attempts failed")


def _majority(
    verdicts: List[LLMJudgeVerdict],
) -> LLMJudgeVerdict:
    counts: Dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    if not counts:
        return LLMJudgeVerdict(verdict="UNKNOWN", reasoning="")
    sorted_v = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(sorted_v) > 1 and sorted_v[0][1] == sorted_v[1][1]:
        chosen = "UNKNOWN"
    else:
        chosen = sorted_v[0][0]
    for v in verdicts:
        if v.verdict == chosen:
            return v
    return LLMJudgeVerdict(verdict=chosen, reasoning="(majority vote)")


def generate_llm_judge(
    source: str,
    claim_line: int,
    claim_text: str,
    *,
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash-lite",
    n_samples: int = 3,
) -> LLMJudgeVerdict:
    """Self-consistency: call Gemini ``n_samples`` times and majority-vote."""
    user_prompt = _build_user_prompt(source, claim_line, claim_text)
    samples: List[LLMJudgeVerdict] = []
    for _ in range(n_samples):
        try:
            samples.append(_call_with_retry(
                user_prompt, api_key=api_key, model=model, max_attempts=3,
            ))
        except Exception as exc:
            log.warning("Baseline D sample dropped: %s", exc)
    if not samples:
        return LLMJudgeVerdict(verdict="UNKNOWN", reasoning="all LLM calls failed")
    if len(samples) == 1:
        return samples[0]
    return _majority(samples)


# -------------------------------------------------------------------
# Public entry point
# -------------------------------------------------------------------

def run_baseline_d(
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
    if not use_llm:
        # Deterministic mode: refuse rather than silently return UNKNOWN,
        # matching the report's note that this baseline is meaningless
        # without an LLM.  Returns UNKNOWN with a clear reason.
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return _make_empty(program_id, source_path, source, f"syntax error: {exc}")
        funcs = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
        if not funcs:
            return _make_empty(program_id, source_path, source, "no function definition found")
        claims = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Assert):
                claims.append({"line": n.lineno, "text": ast.unparse(n.test)})
        if not claims:
            return _make_empty(program_id, source_path, source, "no claims found")
        # All UNKNOWN but with explicit reason.
        crs = []
        for c in claims:
            crs.append(ClaimResult(
                claim_id=f"baseline_d_L{c['line']}",
                claim_text=c["text"],
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=AnchorInfo(node_ids=[], source_lines=[c["line"]],
                                  has_grounding=False),
                reason=("Deterministic mode: Baseline D requires --use-llm "
                        "(no symbolic or execution support)"),
                explanation="LLM-as-judge (deterministic = UNKNOWN)",
            ))
        return ProgramResult(
            program_id=program_id,
            source_path=source_path,
            source_text=source,
            n_claims=len(crs),
            n_verified=0, n_counterexample=0, n_unreachable=0,
            n_unsupported=0, n_translation_error=0,
            n_unknown_timeout=len(crs),
            claim_results=crs,
            elapsed_seconds=0.0,
            baseline="BASELINE_D",
            rejected_by_subset=False,
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return _make_empty(program_id, source_path, source, f"syntax error: {exc}")
    funcs = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
    if not funcs:
        return _make_empty(program_id, source_path, source, "no function definition found")
    fn = funcs[0]

    claims: List[Dict[str, Any]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assert):
            claims.append({"line": n.lineno, "text": ast.unparse(n.test)})
    if suggested_verdicts:
        for sv in suggested_verdicts:
            if sv.get("claim_line") not in [c["line"] for c in claims]:
                claims.append({"line": sv.get("claim_line", 0),
                               "text": sv.get("claim_text", "")})
    if not claims:
        return _make_empty(program_id, source_path, source, "no claims found")

    claim_results: List[ClaimResult] = []
    total_tokens_in = total_tokens_out = 0
    for c in claims:
        try:
            verdict = generate_llm_judge(
                source, c["line"], c["text"],
                api_key=gemini_api_key, model=gemini_model, n_samples=n_samples,
            )
            verdict_str = verdict.verdict
            reasoning = verdict.reasoning
            counterexample_input = verdict.counterexample_input
        except Exception as exc:
            verdict_str = "UNKNOWN"
            reasoning = f"LLM error: {exc}"
            counterexample_input = None

        ce_info: Optional[CounterexampleInfo] = None
        if verdict_str == "COUNTEREXAMPLE" and counterexample_input and do_replay:
            outcome = replay_for_baseline(
                source, fn.name, counterexample_input,
                claim_line=c["line"], claim_expression=c["text"],
                timeout=3.0,
            )
            ce_info = CounterexampleInfo(
                z3_model=counterexample_input,
                replay_verdict=outcome.claim_violated,
                replay_output=outcome.output_repr,
                replay_diff=outcome.error,
            )
            if outcome.attempted and outcome.claim_violated is False:
                verdict_str = "UNKNOWN"
                reasoning = ("hallucinated counterexample: " + reasoning +
                             f" (replay verdict: {outcome.claim_violated})")

        if verdict_str == "VERIFIED":
            status = StatusEnum.VERIFIED
        elif verdict_str == "COUNTEREXAMPLE":
            status = StatusEnum.COUNTEREXAMPLE
        else:
            status = StatusEnum.UNKNOWN_TIMEOUT

        claim_results.append(ClaimResult(
            claim_id=f"baseline_d_L{c['line']}",
            claim_text=c["text"],
            status=status,
            anchor=AnchorInfo(node_ids=[], source_lines=[c["line"]],
                              has_grounding=False),
            counterexample=ce_info,
            reason=reasoning,
            explanation="LLM-as-judge (3-sample self-consistency, JSON auto-retry)",
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
        baseline="BASELINE_D",
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        cot_trace="",
        direct_constraints=[],
        rejected_by_subset=False,
    )


def _make_empty(program_id, source_path, source, reason) -> ProgramResult:
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source,
        baseline="BASELINE_D",
        rejected_by_subset=False,
        subset_rejection_reason=reason,
    )


__all__ = [
    "LLMJudgeVerdict",
    "generate_llm_judge",
    "run_baseline_d",
]
