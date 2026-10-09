"""
pipeline.py - End-to-end pipeline orchestrator (Phase 4).

Connects subset -> nodes -> executor -> binding -> obligations -> replay
into a single function `run_pipeline(source, baseline)`.

Baseline == "AST_ANCHORED"  : exact anchor binding + target reachability/state.
Baseline == "UNANCHORED"    : Baseline A Direct Formalization (SMT-LIB2
                               validity with no AST anchoring or reachability).
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
)
from .direct import (
    DirectFormulaError,
    DirectUnsupportedError,
    source_assert_to_direct,
    validate_direct_formula,
)
from .llm import (
    generate_cot_and_claims,
    generate_direct_formalization,
)
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
    build_direct_obligation,
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
    gemini_model: str = "gemini-3.5-flash",
    suggested_claims: Optional[Sequence] = None,
    direct_specifications: Optional[Sequence] = None,
    direct_specs: Optional[Sequence] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cot_trace: str = "",
    llm_error_message: Optional[str] = None,
) -> ProgramResult:
    """Execute one baseline on a single source.

    ``UNANCHORED`` is retained as the external compatibility name for
    Baseline A, Direct Formalization. It does not construct program node IDs,
    a symbolic executor, a target state, or reachability queries. Source
    assertions become deterministic SMT-LIB2 terms and independently supplied
    direct specifications are checked with the single query ``not C``.

    ``suggested_claims`` is the legacy Python/line-number input and is valid
    only for ``AST_ANCHORED``. Use ``direct_specifications`` for the direct
    baseline.
    """
    start = time.perf_counter()

    if baseline not in {"AST_ANCHORED", "UNANCHORED"}:
        raise ValueError(f"unknown baseline: {baseline}")

    if direct_specs is not None:
        if direct_specifications is not None:
            raise ValueError("pass only one of direct_specifications or direct_specs")
        direct_specifications = direct_specs

    if baseline == "AST_ANCHORED" and direct_specifications is not None:
        raise ValueError(
            "direct_specifications are only valid for the UNANCHORED direct-formalization baseline"
        )
    if baseline == "UNANCHORED" and suggested_claims is not None:
        raise ValueError(
            "suggested_claims are legacy Python claims; pass direct_specifications for UNANCHORED"
        )

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
        result.total_tokens_in = tokens_in
        result.total_tokens_out = tokens_out
        result.cot_trace = cot_trace
        result.elapsed_seconds = elapsed
        return result

    # --- 2. Claim extraction & LLM Chain-of-Thought -------------------------
    llm_suggestions: list = []
    direct_suggestions: list = []
    llm_error: Optional[Claim] = None
    direct_llm_error: Optional[Claim] = None
    if use_llm and baseline == "AST_ANCHORED" and (
        suggested_claims is not None or llm_error_message is not None
    ):
        raise ValueError(
            "pass either use_llm=True or injected LLM data, not both"
        )
    if use_llm and baseline == "UNANCHORED" and (
        direct_specifications is not None or llm_error_message is not None
    ):
        raise ValueError(
            "pass either use_llm=True or injected direct specifications, not both"
        )

    if use_llm:
        if baseline == "AST_ANCHORED":
            try:
                llm_res = generate_cot_and_claims(
                    source,
                    api_key=gemini_api_key,
                    model=gemini_model,
                )
                tokens_in = llm_res.tokens_in
                tokens_out = llm_res.tokens_out
                cot_trace = llm_res.cot_trace
                llm_suggestions = list(llm_res.claims)
            except Exception as e:
                llm_error = _llm_error_claim(e)
        else:
            try:
                direct_res = generate_direct_formalization(
                    source,
                    api_key=gemini_api_key,
                    model=gemini_model,
                )
                tokens_in = direct_res.tokens_in
                tokens_out = direct_res.tokens_out
                direct_suggestions = list(direct_res.specifications)
            except Exception as e:
                direct_llm_error = _llm_error_claim(e)
    elif baseline == "AST_ANCHORED" and suggested_claims is not None:
        llm_suggestions = _coerce_claim_inputs(suggested_claims)
    elif baseline == "UNANCHORED" and direct_specifications is not None:
        direct_suggestions = _coerce_direct_inputs(direct_specifications)

    if llm_error_message is not None:
        if baseline == "AST_ANCHORED":
            llm_error = _llm_error_claim(llm_error_message)
        else:
            direct_llm_error = _llm_error_claim(llm_error_message)

    if baseline == "UNANCHORED":
        # No NodeIds, executor, state relation, target path, or replay is
        # constructed in this branch.
        static_claims = _source_direct_claims(sub.extracted_claims)
        direct_claims = _parse_direct_claims(direct_suggestions)
        claims = _merge_direct_claims(static_claims, direct_claims)
        if direct_llm_error is not None:
            claims.append(direct_llm_error)
        elif use_llm and not direct_suggestions:
            claims.append(_llm_error_claim("LLM returned no direct specifications"))
        elif direct_specifications is not None and not direct_suggestions:
            claims.append(_llm_error_claim("direct specification list is empty"))
        claim_results = [_process_direct_claim(c) for c in claims]
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
        result.total_tokens_in = tokens_in
        result.total_tokens_out = tokens_out
        result.cot_trace = cot_trace
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


def _coerce_direct_inputs(raw_specs: Iterable) -> list:
    """Normalize injected direct specifications without accepting Python claims."""
    out = []
    for spec in raw_specs:
        if isinstance(spec, str):
            out.append({"formula": spec, "variables": [], "rationale": ""})
        else:
            out.append(spec)
    return out


def _spec_value(spec, name: str, default):
    if isinstance(spec, dict):
        return spec.get(name, default)
    return getattr(spec, name, default)


def _llm_error_claim(error: object) -> Claim:
    return Claim(
        claim_id="llm_error",
        source_line=1,
        expr_source=f"LLM_ERROR: {error}",
        expr_node=None,
        anchor=None,
        status="TRANSLATION_ERROR",
    )


def _source_direct_claims(claim_infos: Iterable) -> List[Claim]:
    """Convert each source assert to exactly one deterministic direct claim."""
    out: List[Claim] = []
    for info in claim_infos:
        try:
            validated = source_assert_to_direct(
                info.expr_node,
                rationale="Deterministic source assertion.",
            )
            out.append(
                Claim(
                    claim_id=info.claim_id,
                    source_line=info.source_line,
                    expr_source=validated.formula,
                    expr_node=None,
                    anchor=None,
                    status="DIRECT",
                    explanation=validated.rationale,
                    direct_expression=validated.expression,
                    direct_variables=validated.variables,
                )
            )
        except DirectUnsupportedError:
            out.append(
                Claim(
                    claim_id=info.claim_id,
                    source_line=info.source_line,
                    expr_source=info.expr_source,
                    expr_node=None,
                    anchor=None,
                    status="UNSUPPORTED",
                    explanation="Deterministic source assertion.",
                )
            )
        except DirectFormulaError:
            out.append(
                Claim(
                    claim_id=info.claim_id,
                    source_line=info.source_line,
                    expr_source=info.expr_source,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation="Deterministic source assertion.",
                )
            )
        except Exception as exc:
            out.append(
                Claim(
                    claim_id=info.claim_id,
                    source_line=info.source_line,
                    expr_source=info.expr_source,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=f"Deterministic source assertion: {exc}",
                )
            )
    return out


def _parse_direct_claims(specifications: Iterable) -> List[Claim]:
    """Validate injected/generated direct specifications one-by-one."""
    out: List[Claim] = []
    for index, spec in enumerate(specifications, start=1):
        formula = _spec_value(spec, "formula", "")
        variables = _spec_value(spec, "variables", [])
        rationale = _spec_value(spec, "rationale", "")
        claim_id = f"direct_spec_{index}"
        claim_text = formula if isinstance(formula, str) else repr(formula)
        try:
            validated = validate_direct_formula(
                formula,
                variables,
                rationale=rationale if isinstance(rationale, str) else str(rationale),
            )
        except DirectUnsupportedError:
            out.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="UNSUPPORTED",
                    explanation=rationale if isinstance(rationale, str) else str(rationale),
                )
            )
        except DirectFormulaError:
            out.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=rationale if isinstance(rationale, str) else str(rationale),
                )
            )
        except Exception as exc:
            out.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=f"direct specification validation failed: {exc}",
                )
            )
        else:
            out.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=validated.formula,
                    expr_node=None,
                    anchor=None,
                    status="DIRECT",
                    explanation=validated.rationale,
                    direct_expression=validated.expression,
                    direct_variables=validated.variables,
                )
            )
    return out


def _merge_direct_claims(
    source_claims: List[Claim], generated_claims: List[Claim]
) -> List[Claim]:
    """Combine deterministic source assertions with generated direct claims.

    Source assertions are the trusted, deterministic part of the direct input
    pool.  A direct LLM response may repeat one of those assertions while also
    proposing new properties.  Keep each canonical Boolean formula once, and
    retain a generated rationale on the source claim when it is available.
    Invalid source claims are not used for de-duplication, so a generated
    formula can still provide a valid replacement property.
    """
    merged = list(source_claims)
    seen: dict[str, Claim] = {}
    for claim in merged:
        key = _direct_claim_key(claim)
        if key is not None:
            seen.setdefault(key, claim)

    for claim in generated_claims:
        key = _direct_claim_key(claim)
        if key is None:
            merged.append(claim)
            continue
        existing = seen.get(key)
        if existing is not None:
            if claim.explanation:
                existing.explanation = claim.explanation
            continue
        seen[key] = claim
        merged.append(claim)
    return merged


def _direct_claim_key(claim: Claim) -> Optional[str]:
    """Return a stable key for a validated direct Boolean term."""
    if claim.direct_expression is None:
        return None
    try:
        return repr(_canonical_direct_term(z3.simplify(claim.direct_expression)))
    except Exception:
        return None


def _canonical_direct_term(value: z3.ExprRef):
    """Canonicalize harmless SMT-LIB2 spelling differences for de-duplication."""
    if z3.is_true(value):
        return ("bool", True)
    if z3.is_false(value):
        return ("bool", False)
    if z3.is_int_value(value):
        return ("int", value.as_long())

    decl = value.decl()
    kind = decl.kind()
    args = [_canonical_direct_term(arg) for arg in value.children()]

    # Normalize reversed comparison spellings, e.g. ``(>= n 0)`` and
    # ``(<= 0 n)``, which represent the same source assertion.
    if kind == z3.Z3_OP_GE:
        return ("le", args[1], args[0])
    if kind == z3.Z3_OP_LE:
        return ("le", args[0], args[1])
    if kind == z3.Z3_OP_GT:
        return ("lt", args[1], args[0])
    if kind == z3.Z3_OP_LT:
        return ("lt", args[0], args[1])
    if kind == z3.Z3_OP_EQ:
        return ("eq", *sorted(args, key=repr))
    if kind == z3.Z3_OP_DISTINCT:
        return ("distinct", *sorted(args, key=repr))
    if kind in {
        z3.Z3_OP_AND,
        z3.Z3_OP_OR,
        z3.Z3_OP_XOR,
        z3.Z3_OP_ADD,
        z3.Z3_OP_MUL,
    }:
        return (decl.name(), *sorted(args, key=repr))
    if kind == z3.Z3_OP_UNINTERPRETED:
        return ("var", str(decl.name()))
    return (decl.name(), *args)


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


def _process_direct_claim(claim: Claim) -> ClaimResult:
    """Check one Direct Formalization claim with only the ``not C`` query."""
    anchor = AnchorInfo(node_ids=[], source_lines=[], has_grounding=False)

    if claim.status == "UNSUPPORTED":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNSUPPORTED,
            anchor=anchor,
            reason="direct SMT-LIB2 formula is outside the supported QF-LIA subset",
            explanation=claim.explanation,
        )
    if claim.status == "TRANSLATION_ERROR":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason="direct SMT-LIB2 formula could not be parsed or validated",
            explanation=claim.explanation,
        )
    if claim.direct_expression is None:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason="direct claim has no validated Boolean formula",
            explanation=claim.explanation,
        )

    try:
        obligation = build_direct_obligation(claim.direct_expression)
    except Exception as exc:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason=f"direct formula could not build the not-C obligation: {exc}",
            explanation=claim.explanation,
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
            reason="the direct SMT-LIB2 formula is valid for every declared integer assignment",
            explanation=claim.explanation,
        )
    if kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor,
            z3_stats=stats,
            reason="Z3 returned unknown for the direct not-C validity query",
            explanation=claim.explanation,
        )

    assert model is not None
    # Direct counterexamples are deliberately not replayed: there is no
    # target location or program-state binding in this baseline.
    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.COUNTEREXAMPLE,
        anchor=anchor,
        counterexample=_counterexample_from_model(model, claim),
        z3_stats=stats,
        reason="Z3 found an integer assignment satisfying not C; no program replay is applicable",
        explanation=claim.explanation,
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
