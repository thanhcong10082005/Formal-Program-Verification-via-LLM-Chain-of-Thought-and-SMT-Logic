"""
pipeline.py - End-to-end pipeline orchestrator (Phase 4).

Connects subset -> nodes -> executor -> binding -> obligations -> replay
into a single function `run_pipeline(source, baseline)`.

Baseline == "AST_ANCHORED"  : full pipeline with anchor binding + reachability.
Baseline == "UNANCHORED"    : binding/anchor-gating turned off, no reachability.
"""
from __future__ import annotations

import ast
import time
from typing import Dict, List, Optional, Tuple

import z3

from .binding import Claim, extract_asserts, bind_llm_claims
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
    TwoQueryObligations,
    build_obligations,
    run_reachability,
    run_validity,
)
from .replay import replay_counterexample
from .subset import check_and_normalize


# --- Path selection ----------------------------------------------------------


def _select_reachability(
    executor: TrustedSymbolicExecutor, claims: List[Claim]
) -> List[Reachability]:
    """Pick a single canonical path for the whole program (root path)."""
    paths = executor.enumerate_paths(max_paths=4, max_depth=4)
    if not paths:
        return []
    canonical = paths[0]
    return [executor.build_reachability(canonical)]


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
) -> ProgramResult:
    """Execute the full pipeline on a single source."""
    start = time.perf_counter()

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

    # --- 2. Node IDs ---
    ids = assign_ids(sub.tree)

    # --- 3. Trusted symbolic executor ---
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    transitions = executor.build_all_transitions()

    # --- 4. Claim extraction & LLM Chain-of-Thought ---
    static_claims = extract_asserts(sub.tree, ids)
    llm_claims: List[Claim] = []
    tokens_in = 0
    tokens_out = 0
    cot_trace = ""

    if use_llm:
        try:
            llm_res = generate_cot_and_claims(source, api_key=gemini_api_key, model=gemini_model)
            tokens_in = llm_res.tokens_in
            tokens_out = llm_res.tokens_out
            cot_trace = llm_res.cot_trace
            llm_claims = bind_llm_claims(sub.tree, ids, llm_res.claims)
        except Exception as e:
            llm_claims = [
                Claim(
                    claim_id="llm_error",
                    source_line=1,
                    expr_source=f"LLM_ERROR: {e}",
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                )
            ]

    raw_claims = static_claims + llm_claims
    unsupported_claims: List[Claim] = []

    if baseline == "AST_ANCHORED":
        claims = [c for c in raw_claims if c.status == "ANCHORED"]
        unsupported_claims = [c for c in raw_claims if c.status in ("UNSUPPORTED", "TRANSLATION_ERROR")]
    else:
        # Unanchored: pretend every claim is grounded by stubbing its anchor.
        claims = raw_claims

    # --- 5. Process each claim ---
    reach_list = _select_reachability(executor, claims)
    if not reach_list:
        reachability = Reachability(path=[], relation=z3.BoolVal(True), path_condition=z3.BoolVal(True))
    else:
        reachability = reach_list[0]

    claim_results: List[ClaimResult] = []

    # Process claims that passed anchor gate
    for claim in claims:
        cr = _process_claim(
            claim=claim,
            executor=executor,
            reachability=reachability,
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


def _process_claim(
    claim: Claim,
    executor: TrustedSymbolicExecutor,
    reachability: Reachability,
    *,
    baseline: str,
    source: str,
    do_replay: bool,
) -> ClaimResult:
    """Run Two-Query + replay on a single claim."""
    anchor_info = AnchorInfo(
        node_ids=[claim.anchor.as_string()] if claim.anchor else [],
        source_lines=[claim.source_line],
        has_grounding=claim.anchor is not None,
    )

    # UNANCHORED baselines also reject when the claim has no anchor at all
    if baseline == "UNANCHORED":
        anchor_info.has_grounding = True  # pretend it has grounding

    if claim.expr_node is None:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            reason="Expression could not be parsed to AST",
        )

    # Translate the expression
    obligation = build_obligations(claim, reachability, executor)

    if baseline == "AST_ANCHORED":
        # --- Reachability ---
        r_kind, r_t, _ = run_reachability(obligation)
        if r_kind == "unsat":
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNREACHABLE,
                anchor=anchor_info,
                z3_stats={"reach_ms": (r_t or 0) * 1000},
                reason="path is unreachable (reachability UNSAT)",
            )
        if r_kind == "unknown":
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                reason="Z3 unknown on reachability (timeout or incomplete)",
            )

    # --- Validity ---
    v_kind, v_t, v_model = run_validity(obligation)
    z3_stats = {"valid_ms": (v_t or 0) * 1000}
    if baseline == "AST_ANCHORED" and "reach_ms" not in z3_stats:
        # not present; OK
        pass

    if v_kind == "unsat":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.VERIFIED,
            anchor=anchor_info,
            z3_stats=z3_stats,
            reason="claim holds on every reachable execution",
        )
    if v_kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            z3_stats=z3_stats,
            reason="Z3 unknown on validity (timeout or incomplete)",
        )

    # SAT -> COUNTEREXAMPLE
    assert v_kind == "sat" and v_model is not None
    ce_info = _counterexample_from_model(v_model, claim)
    replay_v: Optional[bool] = None
    replay_out: Optional[str] = None
    if do_replay and ce_info.z3_model:
        replay = _replay_with_model(source, ce_info.z3_model)
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
        z3_stats=z3_stats,
        reason=("Z3 found a violating model"
                + ("" if replay_v is None else
                   f"; replay_verdict={replay_v}"))
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


def _replay_with_model(source: str, model_bindings: Dict[str, int]):
    """Replay a Z3 counterexample on the real Python interpreter."""
    # Find the function name (assume first FunctionDef in source)
    try:
        tree = ast.parse(source)
    except Exception:
        from .replay import ReplayResult as RR

        return RR(False, "", -1, "source unparseable", 0.0)
    fn_name = "<missing>"
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            fn_name = stmt.name
            break
    # Filter bindings to ints only; pass as **kwargs
    inputs: Dict[str, int] = {k: int(v) for k, v in model_bindings.items() if isinstance(v, int)}
    return replay_counterexample(source, fn_name, inputs, timeout=2.0)


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