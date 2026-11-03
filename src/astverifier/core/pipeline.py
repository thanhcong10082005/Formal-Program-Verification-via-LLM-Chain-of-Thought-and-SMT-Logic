"""End-to-end orchestrator for the Core Proposed Method.

The pipeline connects the trusted side of the framework:

    subset -> anchors -> executor -> binding -> obligations -> replay

into a single call :func:`run_pipeline`.

Only the AST-anchored protocol lives here. Baselines (including the former
``UNANCHORED`` SatLM-style direct formalization) are installed separately
during the Baseline phase and must not be reachable from the Core Proposed
Method.
"""

from __future__ import annotations

import ast
import logging
import time
from collections.abc import Iterable, Sequence

import z3

from ..llm.gemini import generate_cot_and_claims, make_client, resolve_api_key
from ..shared.anchors import assign_ids
from ..shared.replay import replay_counterexample
from ..shared.schemas import (
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
    make_empty_program_result,
)
from ..shared.subset import check_and_normalize
from .binding import Claim, bind_llm_claims, extract_asserts
from .executor import Reachability, TrustedSymbolicExecutor
from .obligations import build_obligations, run_reachability, run_validity

log = logging.getLogger(__name__)

BASELINE_AST_ANCHORED = "AST_ANCHORED"


def _select_reachability(
    executor: TrustedSymbolicExecutor, claims: list[Claim]
) -> dict[str, list[Reachability]]:
    """Select the target-specific paths that reach each anchored claim."""
    selected: dict[str, list[Reachability]] = {}
    for claim in claims:
        if claim.anchor is None:
            selected[claim.claim_id] = []
            continue
        selected[claim.claim_id] = [
            executor.build_reachability(path, guards)
            for path, guards in executor.enumerate_target_paths(claim.anchor)
        ]
    return selected


def run_pipeline(
    source: str,
    *,
    program_id: str = "<inline>",
    source_path: str = "<inline>",
    baseline: str = BASELINE_AST_ANCHORED,
    do_replay: bool = True,
    use_llm: bool = False,
    gemini_api_key: str | None = None,
    gemini_model: str = "gemini-3.5-flash-lite",
    suggested_claims: Sequence | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cot_trace: str = "",
    llm_error_message: str | None = None,
    enable_feedback: bool = True,
    enable_invariants: bool = True,
    feedback_loop=None,
    invariant_generator=None,
) -> ProgramResult:
    """Run the Core Proposed Method on a single source.

    Args:
        source: The Python source code to verify.
        program_id: Identifier used in the returned result.
        source_path: Path the source was read from.
        baseline: Must be ``"AST_ANCHORED"``. Other baselines are installed
            separately during the Baseline phase.
        do_replay: Replay a Z3 counterexample on real CPython.
        use_llm: Generate claims with the LLM instead of using only the
            source ``assert`` statements.
        gemini_api_key: Gemini API key; falls back to the environment.
        gemini_model: Gemini model identifier.
        suggested_claims: Pre-computed claims (dicts or
            :class:`~astverifier.llm.gemini.SuggestedClaim`). Mutually
            exclusive with ``use_llm``.
        tokens_in: Tokens billed for the claim-generation call.
        tokens_out: Tokens billed for the claim-generation call.
        cot_trace: Chain-of-Thought trace from the claim-generation call.
        llm_error_message: Simulated LLM failure, surfaced as a translation
            error instead of aborting the run.
        enable_feedback: Allow the retrospective feedback loop to run.
        enable_invariants: Allow the loop-invariant generator to run.
        feedback_loop: An explicit feedback loop instance.
        invariant_generator: An explicit invariant generator instance.

    Returns:
        A :class:`~astverifier.shared.schemas.ProgramResult` holding one
        :class:`~astverifier.shared.schemas.ClaimResult` per claim.

    Raises:
        ValueError: Conflicting arguments, or an unknown baseline name.
    """
    start = time.perf_counter()

    if baseline != BASELINE_AST_ANCHORED:
        raise ValueError(
            f"unknown baseline: {baseline!r}. The Core Proposed Method only "
            f"implements {BASELINE_AST_ANCHORED!r}; other baselines are "
            f"installed separately during the Baseline phase."
        )

    if use_llm and (suggested_claims is not None or llm_error_message is not None):
        raise ValueError(
            "pass either use_llm=True or injected LLM data, not both"
        )

    # --- 1. Subset gate ----------------------------------------------------
    sub = check_and_normalize(source)
    if not sub.accepted:
        return _rejected_result(
            program_id=program_id,
            source_path=source_path,
            source=source,
            baseline=baseline,
            reason=sub.rejected_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cot_trace=cot_trace,
            elapsed=time.perf_counter() - start,
        )

    # --- 2. Claim extraction & Chain-of-Thought ----------------------------
    llm_suggestions: list = []
    llm_error: Claim | None = None

    if use_llm:
        try:
            llm_result = generate_cot_and_claims(
                source,
                api_key=gemini_api_key,
                model=gemini_model,
            )
            tokens_in = llm_result.tokens_in
            tokens_out = llm_result.tokens_out
            cot_trace = llm_result.cot_trace
            llm_suggestions = list(llm_result.claims)
        except Exception as exc:  # noqa: BLE001 - reported as a claim error
            llm_error = _llm_error_claim(exc)
    elif suggested_claims is not None:
        llm_suggestions = _coerce_claim_inputs(suggested_claims)

    if llm_error_message is not None:
        llm_error = _llm_error_claim(llm_error_message)

    # --- 3. Node identifiers and the trusted symbolic executor -------------
    ids = assign_ids(sub.tree)
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    try:
        transitions = executor.build_all_transitions()
    except Exception as exc:
        return _rejected_result(
            program_id=program_id,
            source_path=source_path,
            source=source,
            baseline=baseline,
            reason=f"symbolic execution setup failed: {exc}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cot_trace=cot_trace,
            elapsed=time.perf_counter() - start,
        )

    # --- 4. Anchor binding -------------------------------------------------
    static_claims = extract_asserts(sub.tree, ids)
    llm_claims = bind_llm_claims(sub.tree, ids, llm_suggestions)
    if llm_error is not None:
        llm_claims.append(llm_error)

    raw_claims = static_claims + llm_claims
    claims = [claim for claim in raw_claims if claim.status == "ANCHORED"]
    rejected_claims = [
        claim
        for claim in raw_claims
        if claim.status in ("UNSUPPORTED", "TRANSLATION_ERROR")
    ]

    # --- 5. Optional invariant generator and feedback loop -----------------
    feedback_loop = _resolve_feedback_loop(
        feedback_loop, enable_feedback, gemini_api_key
    )
    invariant_generator = _resolve_invariant_generator(
        invariant_generator, enable_invariants, gemini_api_key
    )

    reachability = _select_reachability(executor, claims)
    claim_results: list[ClaimResult] = []
    for claim in claims:
        claim_results.append(
            _process_claim(
                claim=claim,
                executor=executor,
                reachabilities=reachability.get(claim.claim_id, []),
                source=source,
                do_replay=do_replay,
                feedback_loop=feedback_loop,
                invariant_generator=invariant_generator,
                cot_trace=cot_trace,
            )
        )

    # Record rejected claims so the Hallucination Rate reflects them.
    for claim in rejected_claims:
        status = (
            StatusEnum.UNSUPPORTED
            if claim.status == "UNSUPPORTED"
            else StatusEnum.TRANSLATION_ERROR
        )
        claim_results.append(
            ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=status,
                anchor=AnchorInfo(
                    node_ids=[],
                    source_lines=[claim.source_line],
                    has_grounding=False,
                ),
                reason=f"Claim rejected by Anchor Gate ({claim.status})",
            )
        )

    # --- 6. Roll up to a ProgramResult ------------------------------------
    return _roll_up(
        claims=claim_results,
        program_id=program_id,
        source_path=source_path,
        source=source,
        baseline=baseline,
        elapsed=time.perf_counter() - start,
        transitions=transitions,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cot_trace=cot_trace,
    )


def _resolve_feedback_loop(feedback_loop, enable_feedback: bool, api_key):
    """Return a feedback loop instance, or ``None`` when it cannot run."""
    if feedback_loop is not None or not enable_feedback:
        return feedback_loop
    try:
        from .feedback import RetrospectiveFeedbackLoop

        key = resolve_api_key(api_key)
        if key:
            return RetrospectiveFeedbackLoop(llm_client=make_client(key))
    except Exception as exc:  # noqa: BLE001 - feedback is strictly optional
        log.warning("Failed to init feedback loop: %s", exc)
    return None


def _resolve_invariant_generator(
    invariant_generator, enable_invariants: bool, api_key
):
    """Return an invariant generator instance, or ``None``."""
    if invariant_generator is not None or not enable_invariants:
        return invariant_generator
    try:
        from .invariants import LoopInvariantGenerator

        key = resolve_api_key(api_key)
        if key:
            return LoopInvariantGenerator(
                llm_client=make_client(key),
                use_llm=True,
            )
    except Exception as exc:  # noqa: BLE001 - invariants are strictly optional
        log.warning("Failed to init invariant generator: %s", exc)
    return None


def _coerce_claim_inputs(raw_claims: Iterable) -> list:
    """Normalize injected dicts to the same shape as LLM suggestions."""
    from ..llm.gemini import SuggestedClaim

    out = []
    for claim in raw_claims:
        if isinstance(claim, SuggestedClaim):
            out.append(claim)
        elif isinstance(claim, dict):
            out.append(
                SuggestedClaim(
                    line=claim.get("line", 0),
                    expression=claim.get("expression", ""),
                )
            )
        else:
            out.append(claim)
    return out


def _llm_error_claim(error: object) -> Claim:
    """Wrap an LLM failure as a claim so the run still produces a result."""
    return Claim(
        claim_id="llm_error",
        source_line=1,
        expr_source=f"LLM_ERROR: {error}",
        expr_node=None,
        anchor=None,
        status="TRANSLATION_ERROR",
    )


def _process_claim(
    claim: Claim,
    executor: TrustedSymbolicExecutor,
    reachabilities: list[Reachability] | None = None,
    *,
    source: str,
    do_replay: bool,
    feedback_loop=None,
    invariant_generator=None,
    cot_trace: str = "",
) -> ClaimResult:
    """Run the target-specific reachability and validity queries for a claim."""
    reachability_list = list(reachabilities or [])

    anchor_info = AnchorInfo(
        node_ids=[claim.anchor.as_string()] if claim.anchor else [],
        source_lines=[claim.source_line],
        has_grounding=claim.anchor is not None,
    )

    if claim.expr_node is None:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            reason="Expression could not be parsed to AST",
        )

    if not reachability_list:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            reason="no bounded path to the exact claim node was enumerated",
        )

    reach_ms = 0.0
    valid_ms = 0.0
    reachable_paths = 0

    for reachability in reachability_list:
        try:
            obligation = build_obligations(claim, reachability, executor)
        except Exception as exc:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=anchor_info,
                reason=f"claim could not be translated in target state: {exc}",
            )

        r_kind, r_elapsed, _ = run_reachability(obligation)
        if r_elapsed is not None:
            reach_ms += r_elapsed * 1000
        if r_kind == "unsat":
            continue
        if r_kind == "unknown":
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                z3_stats={"reach_ms": reach_ms},
                reason="Z3 unknown on target-node reachability",
            )

        reachable_paths += 1
        v_kind, v_elapsed, v_model = run_validity(obligation)
        if v_elapsed is not None:
            valid_ms += v_elapsed * 1000
        if v_kind == "unknown":
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
                reason="Z3 unknown on validity for a reachable target path",
            )
        if v_kind == "sat":
            assert v_model is not None
            return _counterexample_result(
                claim=claim,
                model=v_model,
                anchor_info=anchor_info,
                source=source,
                do_replay=do_replay,
                feedback_loop=feedback_loop,
                reachabilities=reachability_list,
                executor=executor,
                cot_trace=cot_trace,
                reach_ms=reach_ms,
                valid_ms=valid_ms,
                failing_reachability=reachability,
            )

    if reachable_paths == 0:
        if any(not item.complete for item in reachability_list):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                z3_stats={"reach_ms": reach_ms},
                reason=(
                    "modeled target paths are unreachable, but control-flow "
                    "coverage is incomplete"
                ),
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNREACHABLE,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms},
            reason="all bounded paths to the exact claim node are unreachable",
        )

    # A bounded loop approximation is not a proof over every concrete
    # execution. Surface that limitation instead of claiming VERIFIED.
    if not all(item.complete for item in reachability_list):
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
            reason=(
                "all modeled paths satisfy the claim, but loop/control-flow "
                "coverage is incomplete"
            ),
        )

    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.VERIFIED,
        anchor=anchor_info,
        z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
        reason="claim holds on every complete path to its exact target node",
    )


def _counterexample_result(
    *,
    claim: Claim,
    model,
    anchor_info: AnchorInfo,
    source: str,
    do_replay: bool,
    feedback_loop,
    reachabilities: list[Reachability],
    executor: TrustedSymbolicExecutor,
    cot_trace: str,
    reach_ms: float,
    valid_ms: float,
    failing_reachability: Reachability | None = None,
) -> ClaimResult:
    """Build the COUNTEREXAMPLE result, optionally running the feedback loop."""
    counterexample = _counterexample_from_model(model, claim)
    replay_verdict: bool | None = None

    if do_replay and counterexample.z3_model:
        replay = _replay_with_model(source, counterexample.z3_model, claim)
        replay_verdict = replay.success
        counterexample.replay_verdict = replay_verdict
        counterexample.replay_output = replay.output_repr
        counterexample.replay_diff = replay.error
        counterexample.replay_iteration_count = replay.failing_iteration_count
        counterexample.replay_failing_state = replay.failing_variable_state

        if replay_verdict and feedback_loop is not None and claim.anchor is not None:
            refined = _run_feedback_loop(
                feedback_loop=feedback_loop,
                source=source,
                cot_trace=cot_trace,
                claim=claim,
                counterexample=counterexample,
                replay=replay,
                executor=executor,
                reachabilities=reachabilities,
                failing_reachability=failing_reachability,
            )
            if refined is not None:
                return refined

    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.COUNTEREXAMPLE,
        anchor=anchor_info,
        counterexample=counterexample,
        z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
        reason=(
            "Z3 found a violating model at the claim's target path"
            + (
                "; replay confirmed the target violation"
                if replay_verdict is True
                else ""
            )
            + (
                "; replay did not confirm the target violation"
                if replay_verdict is False
                else ""
            )
        ),
    )


def _run_feedback_loop(
    *,
    feedback_loop,
    source: str,
    cot_trace: str,
    claim: Claim,
    counterexample: CounterexampleInfo,
    replay,
    executor: TrustedSymbolicExecutor,
    reachabilities: list[Reachability],
    failing_reachability: Reachability | None = None,
) -> ClaimResult | None:
    """Run the retrospective feedback loop; return a VERIFIED result if any.

    The loop is fail-safe: any error keeps the original COUNTEREXAMPLE, so
    this helper returns ``None`` on failure.
    """
    try:
        func_node = executor._enclosing_function_node(claim.anchor)
        func_name = func_node.name if func_node is not None else "f"
        target_reach = (
            failing_reachability
            if failing_reachability is not None
            else (reachabilities[0] if reachabilities else None)
        )
        if target_reach is None:
            return None
        refined_claim, rounds, resolution = feedback_loop.run_loop(
            source=source,
            previous_cot=cot_trace or "",
            claim=claim,
            z3_counterexample=counterexample.z3_model,
            replay=replay,
            executor=executor,
            reachability=target_reach,
            func_name=func_name,
        )
        counterexample.feedback_iterations = len(rounds)
        if refined_claim is not None and refined_claim.claim_id != claim.claim_id:
            counterexample.feedback_refined_claim_text = refined_claim.expr_source
            counterexample.feedback_refined_claim_line = refined_claim.source_line
        counterexample.feedback_resolution = resolution
        if resolution == "verified" and refined_claim is not None:
            # Soundness gate: the refined claim must hold on every reachable target path
            all_valid = True
            for r in reachabilities:
                ob = build_obligations(refined_claim, r, executor)
                rk, _, _ = run_reachability(ob)
                if rk == "unsat":
                    continue
                if rk == "unknown":
                    all_valid = False
                    break
                vk, _, _ = run_validity(ob)
                if vk != "unsat":
                    all_valid = False
                    break
            if all_valid:
                return ClaimResult(
                    claim_id=f"{claim.claim_id}_verified_by_feedback",
                    claim_text=refined_claim.expr_source,
                    status=StatusEnum.VERIFIED,
                    anchor=AnchorInfo(
                        node_ids=[claim.anchor.as_string()] if claim.anchor else [],
                        source_lines=[claim.source_line],
                        has_grounding=claim.anchor is not None,
                    ),
                    reason=(
                        f"Original claim refined after {len(rounds)} feedback rounds: "
                        f"now verified."
                    ),
                )
    except Exception as exc:  # noqa: BLE001 - fail-safe by design
        counterexample.feedback_resolution = f"feedback_error: {exc}"
    return None


def _counterexample_from_model(model, claim: Claim) -> CounterexampleInfo:
    """Extract the integer bindings of a Z3 model."""
    bindings: dict[str, int] = {}
    for decl in model.decls():
        name = decl.name()
        try:
            if str(decl.range()) != "Int":
                continue
            value = model.eval(z3.Int(name), model_completion=True)
            if z3.is_int_value(value):
                bindings[name] = value.as_long()
        except Exception:
            continue
    return CounterexampleInfo(z3_model=bindings, replay_verdict=None)


def _replay_with_model(source: str, model_bindings: dict[str, int], claim: Claim):
    """Replay a Z3 counterexample on the real Python interpreter."""
    from ..shared.replay import ReplayResult

    try:
        tree = ast.parse(source)
    except Exception:
        return ReplayResult(False, "", -1, "source unparseable", 0.0)

    functions = [stmt for stmt in tree.body if isinstance(stmt, ast.FunctionDef)]
    if not functions:
        return ReplayResult(False, "", -1, "source has no function definition", 0.0)

    function = next(
        (
            stmt
            for stmt in functions
            if claim.anchor and stmt.name == claim.anchor.func
        ),
        functions[0],
    )
    parameters = {
        arg.arg
        for arg in list(function.args.posonlyargs)
        + list(function.args.args)
        + list(function.args.kwonlyargs)
    }
    # Only function inputs are executable call arguments; locals must not be
    # passed as keyword arguments.
    inputs = {
        name: int(value)
        for name, value in model_bindings.items()
        if name in parameters and isinstance(value, int)
    }
    return replay_counterexample(
        source,
        function.name,
        inputs,
        target_line=claim.source_line,
        claim_expression=claim.expr_source,
        capture_state=True,
        timeout=2.0,
    )


def _rejected_result(
    *,
    program_id: str,
    source_path: str,
    source: str,
    baseline: str,
    reason: str,
    tokens_in: int,
    tokens_out: int,
    cot_trace: str,
    elapsed: float,
) -> ProgramResult:
    """Build the result returned when the source never reaches the solver."""
    result = make_empty_program_result(
        program_id=program_id,
        source_path=source_path,
        baseline=baseline,
        rejected=True,
        reason=reason,
        source_text=source,
    )
    result.total_tokens_in = tokens_in
    result.total_tokens_out = tokens_out
    result.cot_trace = cot_trace
    result.elapsed_seconds = elapsed
    return result


def _roll_up(
    *,
    claims: list[ClaimResult],
    program_id: str,
    source_path: str,
    source: str,
    baseline: str,
    elapsed: float,
    transitions,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cot_trace: str = "",
) -> ProgramResult:
    """Aggregate the per-claim results into a single program result."""
    by_status: dict[StatusEnum, int] = dict.fromkeys(StatusEnum, 0)
    for claim in claims:
        by_status[claim.status] += 1
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source,
        n_claims=len(claims),
        n_verified=by_status[StatusEnum.VERIFIED],
        n_counterexample=by_status[StatusEnum.COUNTEREXAMPLE],
        n_unreachable=by_status[StatusEnum.UNREACHABLE],
        n_unsupported=by_status[StatusEnum.UNSUPPORTED],
        n_translation_error=by_status[StatusEnum.TRANSLATION_ERROR],
        n_unknown_timeout=by_status[StatusEnum.UNKNOWN_TIMEOUT],
        claim_results=claims,
        elapsed_seconds=elapsed,
        baseline=baseline,
        total_tokens_in=tokens_in,
        total_tokens_out=tokens_out,
        cot_trace=cot_trace,
    )


__all__ = ["BASELINE_AST_ANCHORED", "run_pipeline"]
