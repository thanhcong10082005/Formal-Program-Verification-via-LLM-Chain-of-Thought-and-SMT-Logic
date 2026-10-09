"""
pipeline.py - End-to-end pipeline orchestrator (Phase 4).

Connects subset -> nodes -> executor -> binding -> obligations -> replay
into a single function `run_pipeline(source, baseline)`.

Baseline == "AST_ANCHORED"  : exact anchor binding + target reachability/state.
Baseline == "UNANCHORED"    : free-predicate validity with no program state.
"""
from __future__ import annotations

import ast
import time
from typing import Dict, Iterable, List, Optional, Sequence

import z3

from .binding import (
    Claim,
    bind_llm_claims,
    extract_asserts,
    parse_unanchored_asserts,
    parse_unanchored_claims,
)
from .llm import generate_cot_and_claims
from .classify import (
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
    make_empty_program_result,
)
from .executor import Reachability, TrustedSymbolicExecutor
from .nodes import assign_ids
from .obligations import (
    build_obligations,
    build_unanchored_obligations,
    run_reachability,
    run_validity,
)
from .replay import replay_counterexample
from .subset import check_and_normalize


# --- Path selection ----------------------------------------------------------


def _select_reachability(
    executor: TrustedSymbolicExecutor, claims: List[Claim]
) -> Dict[str, List[Reachability]]:
    """Select target-specific paths for each anchored claim."""
    selected: Dict[str, List[Reachability]] = {}
    for claim in claims:
        if claim.anchor is None:
            selected[claim.claim_id] = []
            continue
        selected[claim.claim_id] = [
            executor.build_reachability(path, guards)
            for path, guards in executor.enumerate_target_paths(claim.anchor)
        ]
    return selected


# --- Main pipeline ------------------------------------------------------------


def run_pipeline(
    source: str,
    *,
    program_id: str = "<inline>",
    source_path: str = "<inline>",
    baseline: str = "AST_ANCHORED",
    do_replay: bool = True,
    use_llm: bool = False,
    gemini_api_key: Optional[str] = None,
    gemini_model: str = "gemini-flash-latest",
    suggested_claims: Optional[Sequence] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cot_trace: str = "",
    llm_error_message: Optional[str] = None,
) -> ProgramResult:
    """Execute one baseline on a single source.

    ``UNANCHORED`` intentionally does not construct program node IDs or a
    symbolic executor. Its predicates are checked as free Z3 expressions.
    The subset parser still validates the source and supplies source asserts
    as claim text; it does not provide execution context to that baseline.
    """
    start = time.perf_counter()

    if baseline not in {"AST_ANCHORED", "UNANCHORED"}:
        raise ValueError(f"unknown baseline: {baseline}")

    # --- 1. Subset gate ---
    sub = check_and_normalize(source)
    if not sub.accepted:
        elapsed = time.perf_counter() - start
        result = make_empty_program_result(
            program_id=program_id,
            source_path=source_path,
            baseline=baseline,
            rejected=True,
            reason=sub.rejected_reason,
            source_text=source,
        )
        result.elapsed_seconds = elapsed
        return result

    # --- 2. Claim extraction & LLM Chain-of-Thought -------------------------
    llm_suggestions: list = []
    llm_error: Optional[Claim] = None
    if use_llm and (suggested_claims is not None or llm_error_message is not None):
        raise ValueError(
            "pass either use_llm=True or injected LLM data, not both"
        )

    if use_llm:
        try:
            llm_res = generate_cot_and_claims(source, api_key=gemini_api_key, model=gemini_model)
            tokens_in = llm_res.tokens_in
            tokens_out = llm_res.tokens_out
            cot_trace = llm_res.cot_trace
            llm_suggestions = list(llm_res.claims)
        except Exception as e:
            llm_error = Claim(
                claim_id="llm_error",
                source_line=1,
                expr_source=f"LLM_ERROR: {e}",
                expr_node=None,
                anchor=None,
                status="TRANSLATION_ERROR",
            )
    elif suggested_claims is not None:
        llm_suggestions = _coerce_claim_inputs(suggested_claims)

    if llm_error_message is not None:
        llm_error = Claim(
            claim_id="llm_error",
            source_line=1,
            expr_source=f"LLM_ERROR: {llm_error_message}",
            expr_node=None,
            anchor=None,
            status="TRANSLATION_ERROR",
        )

    if baseline == "UNANCHORED":
        # This branch deliberately does not assign NodeIds or build an
        # executor. Static asserts are claim inputs only; their source
        # location is discarded before the free-predicate query.
        static_claims = parse_unanchored_asserts(sub.extracted_claims)
        claims = static_claims + parse_unanchored_claims(llm_suggestions)
        if llm_error is not None:
            claims.append(llm_error)
        claim_results = [_process_unanchored_claim(c) for c in claims]
        return _roll_up(
            claims=claim_results,
            program_id=program_id,
            source_path=source_path,
            source=source,
            baseline=baseline,
            elapsed=time.perf_counter() - start,
            transitions={},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cot_trace=cot_trace,
        )

    # --- 3. Node IDs and trusted symbolic executor -------------------------
    ids = assign_ids(sub.tree)
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    try:
        transitions = executor.build_all_transitions()
    except Exception as exc:
        result = make_empty_program_result(
            program_id=program_id,
            source_path=source_path,
            baseline=baseline,
            rejected=True,
            reason=f"symbolic execution setup failed: {exc}",
            source_text=source,
        )
        result.elapsed_seconds = time.perf_counter() - start
        return result

    # --- 4. Anchor binding ---------------------------------------------------
    static_claims = extract_asserts(sub.tree, ids)
    llm_claims = bind_llm_claims(sub.tree, ids, llm_suggestions)
    if llm_error is not None:
        llm_claims.append(llm_error)

    raw_claims = static_claims + llm_claims
    claims = [c for c in raw_claims if c.status == "ANCHORED"]
    unsupported_claims = [
        c for c in raw_claims if c.status in ("UNSUPPORTED", "TRANSLATION_ERROR")
    ]

    # --- 5. Process each claim ---
    reachability = _select_reachability(executor, claims)

    claim_results: List[ClaimResult] = []

    # Process claims that passed anchor gate
    for claim in claims:
        cr = _process_claim(
            claim=claim,
            executor=executor,
            reachabilities=reachability.get(claim.claim_id, []),
            baseline=baseline,
            source=source,
            do_replay=do_replay,
        )
        claim_results.append(cr)

    # For AST_ANCHORED, record rejected claims so Hallucination Rate reflects them
    for c in unsupported_claims:
        st = StatusEnum.UNSUPPORTED if c.status == "UNSUPPORTED" else StatusEnum.TRANSLATION_ERROR
        claim_results.append(
            ClaimResult(
                claim_id=c.claim_id,
                claim_text=c.expr_source,
                status=st,
                anchor=AnchorInfo(node_ids=[], source_lines=[c.source_line], has_grounding=False),
                reason=f"Claim rejected by Anchor Gate ({c.status})",
            )
        )

    # --- 6. Roll up to ProgramResult ---
    result = _roll_up(
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
    return result


def _coerce_claim_inputs(raw_claims: Iterable) -> list:
    """Normalize injected dicts to the same shape as LLM suggestions."""
    from .llm import SuggestedClaim

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


def _process_claim(
    claim: Claim,
    executor: TrustedSymbolicExecutor,
    reachabilities: Optional[List[Reachability] | Reachability] = None,
    *,
    baseline: str,
    source: str,
    do_replay: bool,
    reachability: Optional[Reachability] = None,
) -> ClaimResult:
    """Run target-specific reachability and validity queries for a claim."""
    if reachability is not None:
        if reachabilities is not None:
            raise ValueError("pass either reachabilities or reachability")
        reachabilities = [reachability]
    elif isinstance(reachabilities, Reachability):
        reachabilities = [reachabilities]
    else:
        reachabilities = list(reachabilities or [])

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

    if not reachabilities:
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
    for reachability in reachabilities:
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

        r_kind, r_t, _ = run_reachability(obligation)
        if r_t is not None:
            reach_ms += r_t * 1000
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
        v_kind, v_t, v_model = run_validity(obligation)
        if v_t is not None:
            valid_ms += v_t * 1000
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
            ce_info = _counterexample_from_model(v_model, claim)
            replay_v: Optional[bool] = None
            replay_out: Optional[str] = None
            if do_replay and ce_info.z3_model:
                replay = _replay_with_model(source, ce_info.z3_model, claim)
                replay_v = replay.success
                replay_out = replay.output_repr
                ce_info.replay_verdict = replay_v
                ce_info.replay_output = replay_out
                ce_info.replay_diff = replay.error
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=anchor_info,
                counterexample=ce_info,
                z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
                reason=(
                    "Z3 found a violating model at the claim's target path"
                    + ("; replay confirmed the target violation" if replay_v is True else "")
                    + ("; replay did not confirm the target violation" if replay_v is False else "")
                ),
            )

    if reachable_paths == 0:
        if any(not reachability.complete for reachability in reachabilities):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                z3_stats={"reach_ms": reach_ms},
                reason="modeled target paths are unreachable, but control-flow coverage is incomplete",
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNREACHABLE,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms},
            reason="all bounded paths to the exact claim node are unreachable",
        )

    # A bounded loop/unstructured-control approximation is not a proof over
    # every concrete execution. Surface that limitation instead of calling it
    # VERIFIED when the path set is known to be incomplete.
    if not all(reachability.complete for reachability in reachabilities):
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
            reason="all modeled paths satisfy the claim, but loop/control-flow coverage is incomplete",
        )

    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.VERIFIED,
        anchor=anchor_info,
        z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
        reason="claim holds on every complete path to its exact target node",
    )


def _process_unanchored_claim(claim: Claim) -> ClaimResult:
    """Check a claim as a free predicate, with no program location."""
    anchor = AnchorInfo(node_ids=[], source_lines=[], has_grounding=False)
    if claim.expr_node is None:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason="free predicate could not be parsed",
        )
    if claim.status == "UNSUPPORTED":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNSUPPORTED,
            anchor=anchor,
            reason="free predicate is outside the supported arithmetic subset",
        )
    try:
        obligation = build_unanchored_obligations(claim)
    except Exception as exc:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason=f"free predicate could not be translated: {exc}",
        )
    kind, elapsed, model = run_validity(obligation)
    stats = {"valid_ms": (elapsed or 0) * 1000}
    if kind == "unsat":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.VERIFIED,
            anchor=anchor,
            z3_stats=stats,
            reason="free predicate is valid for every integer assignment",
        )
    if kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor,
            z3_stats=stats,
            reason="Z3 unknown on free-predicate validity",
        )
    assert model is not None
    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.COUNTEREXAMPLE,
        anchor=anchor,
        counterexample=_counterexample_from_model(model, claim),
        z3_stats=stats,
        reason="free predicate has an integer counterexample; no program replay is applicable",
    )


def _counterexample_from_model(model, claim: Claim) -> CounterexampleInfo:
    bindings: Dict[str, int] = {}
    for d in model.decls():
        name = d.name()
        try:
            rng = d.range()
            if str(rng) == "Int":
                const = z3.Int(name)
            else:
                continue
            v = model.eval(const, model_completion=True)
            if z3.is_int_value(v):
                bindings[name] = v.as_long()
            else:
                try:
                    bindings[name] = int(v.as_long())
                except Exception:
                    pass
        except Exception:
            continue
    return CounterexampleInfo(
        z3_model=bindings,
        replay_verdict=None,
    )


def _replay_with_model(source: str, model_bindings: Dict[str, int], claim: Claim):
    """Replay a Z3 counterexample on the real Python interpreter."""
    try:
        tree = ast.parse(source)
    except Exception:
        from .replay import ReplayResult as RR

        return RR(False, "", -1, "source unparseable", 0.0)
    functions = [stmt for stmt in tree.body if isinstance(stmt, ast.FunctionDef)]
    if not functions:
        from .replay import ReplayResult as RR

        return RR(False, "", -1, "source has no function definition", 0.0)
    fn = next(
        (stmt for stmt in functions if claim.anchor and stmt.name == claim.anchor.func),
        functions[0],
    )
    params = {
        arg.arg
        for arg in list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs)
    }
    # A model may also contain target-state symbols. Only function inputs are
    # executable call arguments; locals must not be passed as **kwargs.
    inputs: Dict[str, int] = {
        name: int(value)
        for name, value in model_bindings.items()
        if name in params and isinstance(value, int)
    }
    return replay_counterexample(
        source,
        fn.name,
        inputs,
        target_line=claim.source_line,
        claim_expression=claim.expr_source,
        timeout=2.0,
    )


def _roll_up(
    *,
    claims: List[ClaimResult],
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
    by_status: Dict[StatusEnum, int] = {s: 0 for s in StatusEnum}
    for c in claims:
        by_status[c.status] += 1
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


__all__ = ["run_pipeline"]
