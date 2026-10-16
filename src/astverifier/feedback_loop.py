"""
feedback_loop.py - Closed-Loop Retrospective Self-Correction (Phase 3 of
IMPLEMENTATION_PLAN_PROPOSED.md).

When Z3 finds a counterexample and CPython replay confirms the bug, the
framework's *trusted* side already has everything it needs: the exact
concrete input, the failing variable state, and the line of the failing
assertion.  The only thing missing in Phase 0 was a way to feed that
information back to the LLM and ask it to refine its own reasoning.

This module wires that loop.  Each round:

  1. Package a ``FeedbackContext`` (CE + failing trace + previous CoT).
  2. Ask Gemini to re-analyze with this evidence and propose a refined
     claim, or justify that the original claim was correct.
  3. Re-run the Z3 two-query + replay on the refined claim.
  4. Stop when the refined claim is verified, the LLM rejects the CE
     (justification logged), the LLM errors out, or K iterations have
     been spent (K=3 by default).

The loop is fail-safe: any error keeps the original COUNTEREXAMPLE
intact, so it can never make a Phase 0 result worse.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import z3

from .binding import Claim
from .executor import Reachability, TrustedSymbolicExecutor
from .obligations import build_obligations, run_reachability, run_validity
from .replay import ReplayResult, replay_counterexample

log = logging.getLogger(__name__)


MAX_FEEDBACK_ITERATIONS = 3
FEEDBACK_TIMEOUT_S = 2.0


# --- Context + result types -------------------------------------------------


@dataclass
class FeedbackContext:
    """The information we hand back to the LLM for self-correction."""

    counterexample_input: Dict[str, int]
    counterexample_output: str
    claim_text: str
    claim_line: int
    failing_assertion_line: int
    failing_variable_state: Dict[str, int]
    failing_iteration_count: int
    previous_cot: str
    previous_anchor_line: int
    function_signature: str
    source_snippet: str


@dataclass
class FeedbackRound:
    """One iteration of the feedback loop, recorded for the report."""

    iteration: int
    llm_response_excerpt: str
    refined_claim_text: Optional[str]
    refined_claim_line: Optional[int]
    z3_kind: str
    replay_verdict: Optional[bool]
    resolution: str  # see FeedbackLoop._resolve


# --- Gemini client adapter --------------------------------------------------


def _make_feedback_client(llm_client) -> "_FeedbackLLMClient":
    """Wrap the existing genai client in a tiny adapter.

    The pipeline's ``llm.py`` uses ``google.genai.Client``.  We replicate
    just enough of its interface here so the feedback loop does not need
    to import the full Gemini module.
    """
    if llm_client is None:
        return _NullClient()
    return _GeminiAdapter(llm_client)


class _NullClient:
    def invoke(self, prompt: str, system: str) -> Optional[str]:
        return None


class _GeminiAdapter:
    def __init__(self, raw_client) -> None:
        self.raw = raw_client

    def invoke(self, prompt: str, system: str) -> Optional[str]:
        try:
            from google.genai import types
        except Exception:
            return None
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = self.raw.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                return getattr(response, "text", None) or None
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                # Retry on 503/UNAVAILABLE/RESOURCE_EXHAUSTED
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                # Other errors: fail immediately
                log.warning("Feedback LLM call failed: %s", exc)
                return None
        log.warning("Feedback LLM call failed after 3 attempts: %s", last_err)
        return None


# --- Prompt -----------------------------------------------------------------


FEEDBACK_SYSTEM_PROMPT = """You are a Chain-of-Thought refinement assistant for
formal program verification.

The user will give you:
  * a Python function with a source line numbered reference,
  * a previous CoT reasoning trace and the claim it produced,
  * a counterexample that was found by the formal verifier (Z3) and
    confirmed by replaying the function on the real CPython interpreter.

Your task: re-analyze the program, identify which step of the previous
reasoning was wrong, and either (a) propose a *refined* claim that better
matches the program's actual behavior, or (b) confirm the original
counterexample is correct, in which case you may keep the original
claim.

Output strict JSON with these fields:
{
  "refined_cot":   "step-by-step re-analysis (string)",
  "refined_claim": {"line": <int>, "expression": "<Python bool expression>"},
  "explanation":   "short reason the previous CoT was wrong",
  "accept_ce":     <bool>   // true if you confirm the counterexample
}
"""


def _build_feedback_prompt(
    source: str, ctx: FeedbackContext, previous_cot: str
) -> str:
    numbered = "\n".join(
        f"{i + 1:3d} | {ln}" for i, ln in enumerate(source.splitlines())
    )
    return (
        f"Python program:\n```python\n{numbered}\n```\n\n"
        f"Previous Chain-of-Thought (the LLM's own reasoning):\n"
        f"```\n{previous_cot or '(empty)'}\n```\n\n"
        f"Previous claim anchored at line {ctx.claim_line}: "
        f"`{ctx.claim_text}`\n\n"
        f"Counterexample discovered by Z3 and confirmed by CPython replay:\n"
        f"  - input:       {ctx.counterexample_input}\n"
        f"  - output:      {ctx.counterexample_output}\n"
        f"  - failing at line: {ctx.failing_assertion_line}\n"
        f"  - failing variable state: {ctx.failing_variable_state}\n"
        f"  - iteration count: {ctx.failing_iteration_count}\n\n"
        f"Refine the claim.  If the counterexample is invalid, set "
        f"`accept_ce` to false and explain why in `explanation`."
    )


# --- Feedback loop ----------------------------------------------------------


class RetrospectiveFeedbackLoop:
    """Drive K=3 rounds of LLM self-correction on top of the trusted path."""

    def __init__(
        self,
        llm_client=None,
        *,
        max_iterations: int = MAX_FEEDBACK_ITERATIONS,
        timeout: float = FEEDBACK_TIMEOUT_S,
    ) -> None:
        self.client = _make_feedback_client(llm_client)
        self.max_iter = max_iterations
        self.timeout = timeout

    def run_loop(
        self,
        *,
        source: str,
        previous_cot: str,
        claim: Claim,
        z3_counterexample: Dict[str, int],
        replay: ReplayResult,
        executor: TrustedSymbolicExecutor,
        reachability: Reachability,
        func_name: str,
    ) -> Tuple[Optional[Claim], List[FeedbackRound], str]:
        """Run the self-correction loop. Returns (final_claim, rounds, resolution)."""
        rounds: List[FeedbackRound] = []
        if self.client is None or not isinstance(self.client, _GeminiAdapter):
            return claim, rounds, "not_run"
        ctx = self._build_context(source, claim, z3_counterexample, replay, func_name)
        current_claim = claim
        current_cot = previous_cot
        for it in range(1, self.max_iter + 1):
            prompt = _build_feedback_prompt(source, ctx, current_cot)
            raw = self.client.invoke(prompt, FEEDBACK_SYSTEM_PROMPT)
            if not raw:
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt="",
                    refined_claim_text=None, refined_claim_line=None,
                    z3_kind="error", replay_verdict=None,
                    resolution="llm_error",
                ))
                return current_claim, rounds, "llm_error"
            parsed = _parse_feedback_json(raw)
            if parsed is None:
                log.warning("Feedback JSON parse failed. raw=%s", raw[:300])
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=None, refined_claim_line=None,
                    z3_kind="error", replay_verdict=None,
                    resolution="llm_error",
                ))
                return current_claim, rounds, "llm_error"
            if parsed.get("accept_ce") is False:
                # LLM explicitly rejected the counterexample
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=None, refined_claim_line=None,
                    z3_kind="unsat", replay_verdict=False,
                    resolution="llm_rejected_ce",
                ))
                return current_claim, rounds, "llm_rejected_ce"
            new_line = int(parsed.get("refined_claim", {}).get("line", 0) or 0)
            new_expr = str(parsed.get("refined_claim", {}).get("expression", "") or "")
            current_cot = parsed.get("refined_cot", current_cot) or current_cot
            if not new_line or not new_expr:
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind="error", replay_verdict=None,
                    resolution="llm_error",
                ))
                return current_claim, rounds, "llm_error"
            # Re-bind and re-run Z3 with the refined claim
            refined_claim = _make_refined_claim(current_claim, new_line, new_expr, executor)
            if refined_claim is None:
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind="error", replay_verdict=None,
                    resolution="llm_error",
                ))
                return current_claim, rounds, "llm_error"
            try:
                obligation = build_obligations(refined_claim, reachability, executor)
            except Exception as exc:
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind="error", replay_verdict=None,
                    resolution=f"z3_setup_error: {exc}",
                ))
                return current_claim, rounds, "llm_error"
            r_kind, _, _ = run_reachability(obligation)
            if r_kind != "sat":
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind=r_kind, replay_verdict=None,
                    resolution="verified" if r_kind == "unsat" else "unknown_timeout",
                ))
                if r_kind == "unsat":
                    return refined_claim, rounds, "verified"
                return current_claim, rounds, "unknown_timeout"
            v_kind, _, v_model = run_validity(obligation)
            new_ce = _model_to_dict(v_model) if v_kind == "sat" else {}
            new_replay = ReplayResult(False, "", -1, "not_run", 0.0)
            if v_kind == "sat" and new_ce:
                new_replay = replay_counterexample(
                    source, func_name, new_ce,
                    target_line=new_line,
                    claim_expression=new_expr,
                    capture_state=True,
                    timeout=self.timeout,
                )
            if v_kind == "unsat":
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind="unsat", replay_verdict=None,
                    resolution="verified",
                ))
                return refined_claim, rounds, "verified"
            if v_kind == "unknown" or (v_kind == "sat" and not new_replay.success):
                # Refined claim is still a counterexample OR z3 gave up
                rounds.append(FeedbackRound(
                    iteration=it, llm_response_excerpt=raw[:200],
                    refined_claim_text=new_expr, refined_claim_line=new_line,
                    z3_kind=v_kind, replay_verdict=new_replay.success,
                    resolution="still_counter" if v_kind == "sat" else "unknown_timeout",
                ))
                current_claim = refined_claim
                continue
            # new CE confirmed by replay → still a counterexample; continue
            rounds.append(FeedbackRound(
                iteration=it, llm_response_excerpt=raw[:200],
                refined_claim_text=new_expr, refined_claim_line=new_line,
                z3_kind="sat", replay_verdict=True,
                resolution="still_counter",
            ))
            current_claim = refined_claim
        return current_claim, rounds, "max_iter_reached"

    def _build_context(
        self,
        source: str,
        claim: Claim,
        z3_model: Dict[str, int],
        replay: ReplayResult,
        func_name: str,
    ) -> FeedbackContext:
        sig = ""
        try:
            tree = ast_module().parse(source)
            for stmt in tree.body:
                if isinstance(stmt, ast_module().FunctionDef) and stmt.name == func_name:
                    sig = ast_module().unparse(stmt.args)
                    break
        except Exception:
            pass
        snippet_lines = source.splitlines()[:40]
        return FeedbackContext(
            counterexample_input=dict(z3_model),
            counterexample_output=replay.output_repr or "",
            claim_text=claim.expr_source,
            claim_line=claim.source_line,
            failing_assertion_line=claim.source_line,
            failing_variable_state=replay.failing_variable_state or {},
            failing_iteration_count=replay.failing_iteration_count or 0,
            previous_cot="",
            previous_anchor_line=claim.source_line,
            function_signature=sig,
            source_snippet="\n".join(snippet_lines),
        )


# --- Helpers ---------------------------------------------------------------


def ast_module():
    import ast
    return ast


def _parse_feedback_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _make_refined_claim(
    previous_claim: Claim, line: int, expression: str, executor: TrustedSymbolicExecutor
) -> Optional[Claim]:
    """Build a new Claim that re-uses the previous claim's anchor context."""
    try:
        import ast as _ast
        expr_node = _ast.parse(expression, mode="eval").body
    except Exception:
        return None
    if not _ast.dump(expr_node).startswith(_ast.dump(_ast.parse("0", mode="eval").body)[:10]):
        pass
    new_claim = Claim(
        claim_id=f"feedback_refined_{previous_claim.claim_id}_L{line}",
        source_line=line,
        expr_source=expression,
        expr_node=expr_node,
        anchor=previous_claim.anchor,
        status="ANCHORED",
        anchor_node=previous_claim.anchor_node,
    )
    return new_claim


def _model_to_dict(model) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if model is None:
        return out
    for d in model.decls():
        name = str(d.name())
        try:
            v = model.eval(model.ctx.mk_const(d.name(), model.ctx.int_sort()),
                           model_completion=True)
        except Exception:
            try:
                v = model[d.name()]
            except Exception:
                continue
        try:
            out[name] = int(v.as_long())
        except Exception:
            try:
                out[name] = int(v)
            except Exception:
                continue
    return out


__all__ = [
    "FeedbackContext",
    "FeedbackRound",
    "RetrospectiveFeedbackLoop",
    "MAX_FEEDBACK_ITERATIONS",
]
