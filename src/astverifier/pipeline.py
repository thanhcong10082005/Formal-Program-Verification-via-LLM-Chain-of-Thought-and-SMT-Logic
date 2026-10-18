"""
pipeline.py - End-to-end pipeline orchestrator (Phase 4).

Connects subset -> nodes -> executor -> binding -> obligations -> replay
into a single function `run_pipeline(source, baseline)`.

Baseline == "AST_ANCHORED"  : exact anchor binding + target reachability/state.
Baseline == "UNANCHORED"    : simple structured claim-list validity checks
                               (no AST anchoring, state, context, or replay).
"""
from __future__ import annotations

import ast
import time
from typing import Dict, Iterable, List, Optional, Sequence

import z3

from .binding import (
    Claim,
    bind_anchored_direct_claims,
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
    generate_anchored_formalization,
    generate_simple_claims,
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
    build_anchored_direct_obligations,
    build_direct_context,
    build_direct_obligation,
    build_simple_obligation,
    run_reachability,
    run_context,
    run_simple_validity,
    run_validity,
)
from .replay import replay_counterexample
from .simple import (
    SimpleExpressionError,
    SimpleTranslationError,
    SimpleUnsupportedError,
    source_assert_to_simple,
    validate_simple_claim,
)
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
    simple_claims: Optional[Sequence] = None,
    anchored_constraints: Optional[Sequence] = None,
    anchored_specifications: Optional[Sequence] = None,
    direct_constraints: Optional[Sequence] = None,
    direct_specifications: Optional[Sequence] = None,
    direct_specs: Optional[Sequence] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cot_trace: str = "",
    llm_error_message: Optional[str] = None,
) -> ProgramResult:
    """Execute one baseline on a single source.

    ``AST_ANCHORED`` uses the improved SatLM-style contract when
    ``anchored_constraints``/``anchored_specifications`` are supplied (or
    when ``use_llm=True``): Gemini returns joint SMT-LIB2 constraints and
    line-anchored target specifications, then the trusted AST executor checks
    reachability and validity at the anchored target state. The legacy
    ``suggested_claims`` path remains available for compatibility.

    ``UNANCHORED`` is the simple claim-list baseline.  Gemini (or
    ``simple_claims`` injected by a caller) supplies structured JSON Boolean
    expressions.  Source assertions are converted deterministically, each
    claim is translated to QF-LIA SMT-LIB2, and each is checked using only
    ``not(claim)``.  The result is an ordered, 1-based list with empty
    anchors and no replay.
    """
    start = time.perf_counter()

    if baseline not in {"AST_ANCHORED", "UNANCHORED"}:
        raise ValueError(f"unknown baseline: {baseline}")

    if direct_specs is not None:
        if direct_specifications is not None:
            raise ValueError("pass only one of direct_specifications or direct_specs")
        direct_specifications = direct_specs

    if baseline == "AST_ANCHORED" and (
        direct_constraints is not None or direct_specifications is not None
    ):
        raise ValueError(
            "direct_constraints/direct_specifications are legacy simple-baseline inputs and are not valid for AST_ANCHORED"
        )
    if baseline == "UNANCHORED" and (
        anchored_constraints is not None or anchored_specifications is not None
    ):
        raise ValueError(
            "anchored constraints/specifications are only valid for AST_ANCHORED"
        )
    if baseline == "AST_ANCHORED" and simple_claims is not None:
        raise ValueError("simple_claims are only valid for the UNANCHORED baseline")
    if baseline == "AST_ANCHORED" and suggested_claims is not None and (
        anchored_constraints is not None or anchored_specifications is not None
    ):
        raise ValueError(
            "pass either legacy suggested_claims or anchored constraints/specifications"
        )
    if baseline == "UNANCHORED" and suggested_claims is not None:
        raise ValueError(
            "suggested_claims are legacy Python claims for the anchored baseline; pass simple_claims for UNANCHORED"
        )
    if baseline == "UNANCHORED" and (
        direct_constraints is not None or direct_specifications is not None
    ):
        raise ValueError(
            "direct_constraints/direct_specifications are legacy direct-formalization inputs; pass simple_claims for UNANCHORED"
        )

    # --- 1. Subset gate ---
    sub = check_and_normalize(source)
    if not sub.accepted:
        elapsed = time.perf_counter() - start
        if baseline == "UNANCHORED":
            # Keep the simple baseline's result contract total over dataset
            # inputs.  A source outside the strict QF-LIA Python subset is
            # still rejected, but it is represented as one ordered result so
            # benchmark aggregation can distinguish unsupported syntax from
            # an empty/missing run.  No Gemini request is made for rejected
            # source, and no non-QF-LIA feature is admitted here.
            rejection_status = (
                StatusEnum.TRANSLATION_ERROR
                if (sub.rejected_reason or "").startswith("SyntaxError:")
                else StatusEnum.UNSUPPORTED
            )
            rejection_claim = ClaimResult(
                claim_id="subset_rejection",
                claim_text=f"SOURCE_REJECTED: {sub.rejected_reason or 'outside QF-LIA subset'}",
                status=rejection_status,
                anchor=AnchorInfo(
                    node_ids=[],
                    source_lines=[],
                    has_grounding=False,
                ),
                reason=(
                    "source could not enter the simple claim-list baseline "
                    "because it is outside the strict QF-LIA Python subset"
                ),
                explanation=sub.rejected_reason or "source rejected by subset gate",
                step=1,
            )
            result = _roll_up(
                claims=[rejection_claim],
                program_id=program_id,
                source_path=source_path,
                source=source,
                baseline=baseline,
                elapsed=elapsed,
                transitions={},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cot_trace=cot_trace,
            )
            result.rejected_by_subset = True
            result.subset_rejection_reason = sub.rejected_reason
            return result
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
    anchored_constraint_suggestions: list = []
    anchored_specification_suggestions: list = []
    simple_claim_suggestions: list = []
    llm_error: Optional[Claim] = None
    simple_llm_error: Optional[Claim] = None
    if use_llm and baseline == "AST_ANCHORED" and (
        suggested_claims is not None
        or anchored_constraints is not None
        or anchored_specifications is not None
        or llm_error_message is not None
    ):
        raise ValueError(
            "pass either use_llm=True or injected LLM data, not both"
        )
    if use_llm and baseline == "UNANCHORED" and (
        simple_claims is not None or llm_error_message is not None
    ):
        raise ValueError(
            "pass either use_llm=True or injected simple_claims, not both"
        )

    if use_llm:
        if baseline == "AST_ANCHORED":
            try:
                llm_res = generate_anchored_formalization(
                    source,
                    api_key=gemini_api_key,
                    model=gemini_model,
                )
                tokens_in = llm_res.tokens_in
                tokens_out = llm_res.tokens_out
                anchored_constraint_suggestions = list(llm_res.constraints)
                anchored_specification_suggestions = list(llm_res.specifications)
            except Exception as e:
                llm_error = _llm_error_claim(e)
        else:
            try:
                simple_res = generate_simple_claims(
                    source,
                    api_key=gemini_api_key,
                    model=gemini_model,
                )
                tokens_in = getattr(simple_res, "tokens_in", 0)
                tokens_out = getattr(simple_res, "tokens_out", 0)
                if isinstance(simple_res, dict):
                    raw_simple_claims = simple_res.get(
                        "claims", simple_res.get("simple_claims", [])
                    )
                elif isinstance(simple_res, (list, tuple)):
                    raw_simple_claims = simple_res
                else:
                    raw_simple_claims = getattr(
                        simple_res,
                        "claims",
                        getattr(simple_res, "simple_claims", []),
                    )
                simple_claim_suggestions = list(raw_simple_claims or [])
            except Exception as e:
                simple_llm_error = _llm_error_claim(e)
    elif baseline == "AST_ANCHORED" and suggested_claims is not None:
        llm_suggestions = _coerce_claim_inputs(suggested_claims)
    elif baseline == "AST_ANCHORED":
        if anchored_constraints is not None:
            anchored_constraint_suggestions = _coerce_direct_inputs(anchored_constraints)
        if anchored_specifications is not None:
            anchored_specification_suggestions = list(anchored_specifications)
    elif baseline == "UNANCHORED" and simple_claims is not None:
        if isinstance(simple_claims, dict):
            if "claims" in simple_claims or "simple_claims" in simple_claims:
                simple_claim_suggestions = list(
                    simple_claims.get(
                        "claims", simple_claims.get("simple_claims", [])
                    )
                )
            else:
                simple_claim_suggestions = [simple_claims]
        elif hasattr(simple_claims, "claims"):
            simple_claim_suggestions = list(simple_claims.claims)
        else:
            simple_claim_suggestions = list(simple_claims)

    if llm_error_message is not None:
        if baseline == "AST_ANCHORED":
            llm_error = _llm_error_claim(llm_error_message)
        else:
            simple_llm_error = _llm_error_claim(llm_error_message)

    anchored_formalization_mode = baseline == "AST_ANCHORED" and (
        use_llm
        or anchored_constraints is not None
        or anchored_specifications is not None
    )

    if baseline == "UNANCHORED":
        # No NodeIds, executor, target state, context formula, reachability,
        # or replay is constructed for the simple baseline.  Every structured
        # claim receives one independent ``Not(claim)`` query.
        source_claims = _source_simple_claims(sub.extracted_claims)
        generated_claims = _parse_simple_claims(simple_claim_suggestions)
        claims = _merge_simple_claims(source_claims, generated_claims)

        if simple_llm_error is not None:
            claims.append(simple_llm_error)
        elif use_llm and not simple_claim_suggestions:
            claims.append(_llm_error_claim("LLM returned no simple claims"))

        for step, claim in enumerate(claims, start=1):
            claim.step = step
        claim_results = [_process_simple_claim(c) for c in claims]
        result = _roll_up(
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
        return result

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
    if anchored_formalization_mode:
        static_claims = extract_asserts(sub.tree, ids)
        anchored_static_claims = _source_anchored_direct_claims(static_claims)
        generated_claims = bind_anchored_direct_claims(
            sub.tree,
            ids,
            anchored_specification_suggestions,
        )
        claims = _merge_anchored_direct_claims(
            anchored_static_claims,
            generated_claims,
        )
        validated_constraints, constraint_errors = _parse_direct_constraints(
            anchored_constraint_suggestions
        )
        anchored_context = _combine_direct_constraints(validated_constraints)
        context_kind = "error" if constraint_errors else "sat"
        context_elapsed: Optional[float] = None
        context_error = ""
        if constraint_errors:
            context_error = "; ".join(
                f"{claim.claim_id}: {claim.explanation or claim.expr_source}"
                for claim in constraint_errors
            )
        else:
            try:
                context_obligation = build_direct_context(anchored_context)
                context_kind, context_elapsed, _ = run_context(context_obligation)
            except Exception as exc:
                context_kind = "error"
                context_error = f"anchored context could not be built: {exc}"

        if llm_error is not None:
            claims.append(llm_error)
        elif use_llm and not anchored_specification_suggestions:
            claims.append(
                _llm_error_claim("LLM returned no anchored specifications")
            )
        claim_results: List[ClaimResult] = [
            _process_anchored_direct_claim(
                c,
                executor=executor,
                reachabilities=[
                    executor.build_reachability(path, guards)
                    for path, guards in executor.enumerate_target_paths(c.anchor)
                ] if c.anchor is not None else [],
                constraints=anchored_context,
                context_kind=context_kind,
                context_elapsed=context_elapsed,
                context_error=context_error,
                baseline=baseline,
                source=source,
                do_replay=do_replay,
            )
            for c in (constraint_errors + claims)
        ]
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
            direct_constraints=[
                validated.formula for validated in validated_constraints
            ],
        )
        return result

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


def _simple_value(item, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _simple_expression_from_item(item):
    """Extract an expression object without accepting direct SMT text.

    The public schema uses ``expression``.  Accepting ``claim`` and a bare
    expression object keeps injected fixtures ergonomic while still requiring
    the actual expression to be structured JSON.
    """
    if isinstance(item, dict):
        if "expression" in item:
            return item["expression"]
        if "claim" in item:
            return item["claim"]
        if "expr" in item:
            return item["expr"]
        if any(key in item for key in ("kind", "type", "node_type", "op", "operator", "var", "variable", "int", "bool")):
            return item
        return None
    return getattr(item, "expression", None)


def _simple_item_text(expression) -> str:
    try:
        import json

        return json.dumps(expression, sort_keys=True, separators=(",", ":"))
    except Exception:
        return repr(expression)


def _simple_claim_from_error(
    *, claim_id: str, step: int, expression, status: str, explanation: str
) -> Claim:
    return Claim(
        claim_id=claim_id,
        source_line=0,
        expr_source=_simple_item_text(expression),
        expr_node=None,
        anchor=None,
        status=status,
        explanation=explanation,
        step=step,
    )


def _source_simple_claims(claim_infos: Iterable) -> List[Claim]:
    """Translate every source assertion exactly once, in source order."""
    out: List[Claim] = []
    for index, info in enumerate(claim_infos, start=1):
        try:
            validated = source_assert_to_simple(
                info.expr_node,
                rationale="Deterministic source assertion.",
            )
        except SimpleUnsupportedError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=f"source_assert_{index}",
                    step=0,
                    expression=info.expr_source,
                    status="UNSUPPORTED",
                    explanation=str(exc),
                )
            )
        except SimpleTranslationError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=f"source_assert_{index}",
                    step=0,
                    expression=info.expr_source,
                    status="TRANSLATION_ERROR",
                    explanation=str(exc),
                )
            )
        except SimpleExpressionError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=f"source_assert_{index}",
                    step=0,
                    expression=info.expr_source,
                    status="TRANSLATION_ERROR",
                    explanation=str(exc),
                )
            )
        except Exception as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=f"source_assert_{index}",
                    step=0,
                    expression=info.expr_source,
                    status="TRANSLATION_ERROR",
                    explanation=f"source assertion translation failed: {exc}",
                )
            )
        else:
            out.append(
                Claim(
                    claim_id=f"source_assert_{index}",
                    source_line=0,
                    expr_source=validated.formula,
                    expr_node=None,
                    anchor=None,
                    status="SIMPLE",
                    explanation=validated.rationale,
                    simple_expression=validated.expression,
                    simple_variables=validated.variables,
                )
            )
    return out


def _parse_simple_claims(simple_claims: Iterable) -> List[Claim]:
    """Validate generated structured claims while preserving their order."""
    out: List[Claim] = []
    for index, item in enumerate(simple_claims, start=1):
        expression = _simple_expression_from_item(item)
        rationale = _simple_value(item, "rationale", "")
        rationale = rationale if isinstance(rationale, str) else str(rationale)
        claim_id = f"simple_claim_{index}"
        if isinstance(item, dict) and set(item).intersection(
            {"line", "source_line", "target_line", "anchor", "node_id", "node_ids", "constraints", "reachability"}
        ):
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=item,
                    status="TRANSLATION_ERROR",
                    explanation="simple claims cannot contain source anchors, lines, constraints, or reachability data",
                )
            )
            continue
        if expression is None:
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=item,
                    status="TRANSLATION_ERROR",
                    explanation="simple claim must contain one structured expression object",
                )
            )
            continue
        try:
            validated = validate_simple_claim(expression, rationale=rationale)
        except SimpleUnsupportedError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=expression,
                    status="UNSUPPORTED",
                    explanation=rationale or str(exc),
                )
            )
        except SimpleTranslationError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=expression,
                    status="TRANSLATION_ERROR",
                    explanation=rationale or str(exc),
                )
            )
        except SimpleExpressionError as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=expression,
                    status="TRANSLATION_ERROR",
                    explanation=rationale or str(exc),
                )
            )
        except Exception as exc:
            out.append(
                _simple_claim_from_error(
                    claim_id=claim_id,
                    step=0,
                    expression=expression,
                    status="TRANSLATION_ERROR",
                    explanation=rationale or f"simple claim translation failed: {exc}",
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
                    status="SIMPLE",
                    explanation=validated.rationale,
                    simple_expression=validated.expression,
                    simple_variables=validated.variables,
                )
            )
    return out


def _simple_claim_key(claim: Claim) -> Optional[str]:
    if claim.simple_expression is None or not z3.is_bool(claim.simple_expression):
        return None
    try:
        return z3.simplify(claim.simple_expression).sexpr()
    except Exception:
        return None


def _merge_simple_claims(
    source_claims: Iterable[Claim], generated_claims: Iterable[Claim]
) -> List[Claim]:
    """Keep source assertions once and append new generated claims in order."""
    merged = list(source_claims)
    source_seen: dict[str, Claim] = {}
    for claim in merged:
        key = _simple_claim_key(claim)
        if key is not None:
            source_seen.setdefault(key, claim)

    for claim in generated_claims:
        key = _simple_claim_key(claim)
        existing = source_seen.get(key) if key is not None else None
        if existing is not None:
            if claim.explanation:
                existing.explanation = claim.explanation
            continue
        merged.append(claim)
    return merged


def _source_direct_claims(claim_infos: Iterable) -> List[Claim]:
    """Convert each source assert to exactly one deterministic direct claim."""
    out: List[Claim] = []
    for index, info in enumerate(claim_infos, start=1):
        # The direct baseline intentionally does not expose source locations as
        # claim anchors.  Keep the source line only in this private conversion
        # object for compatibility with the shared Claim dataclass.
        claim_id = f"source_assert_{index}"
        try:
            validated = source_assert_to_direct(
                info.expr_node,
                rationale="Deterministic source assertion.",
            )
            out.append(
                Claim(
                    claim_id=claim_id,
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
                    claim_id=claim_id,
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
                    claim_id=claim_id,
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
                    claim_id=claim_id,
                    source_line=info.source_line,
                    expr_source=info.expr_source,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=f"Deterministic source assertion: {exc}",
                )
            )
    return out


def _source_anchored_direct_claims(claims: Iterable[Claim]) -> List[Claim]:
    """Convert trusted source asserts into anchored SMT target claims."""
    out: List[Claim] = []
    for claim in claims:
        try:
            validated = source_assert_to_direct(
                claim.expr_node,
                rationale="Deterministic source assertion anchored to the AST.",
            )
        except DirectUnsupportedError:
            out.append(
                Claim(
                    claim_id=claim.claim_id,
                    source_line=claim.source_line,
                    expr_source=claim.expr_source,
                    expr_node=claim.expr_node,
                    anchor=claim.anchor,
                    status="UNSUPPORTED",
                    anchor_node=claim.anchor_node,
                    explanation="Deterministic source assertion.",
                    replay_expr_source=claim.expr_source,
                )
            )
        except DirectFormulaError:
            out.append(
                Claim(
                    claim_id=claim.claim_id,
                    source_line=claim.source_line,
                    expr_source=claim.expr_source,
                    expr_node=claim.expr_node,
                    anchor=claim.anchor,
                    status="TRANSLATION_ERROR",
                    anchor_node=claim.anchor_node,
                    explanation="Deterministic source assertion.",
                    replay_expr_source=claim.expr_source,
                )
            )
        except Exception as exc:
            out.append(
                Claim(
                    claim_id=claim.claim_id,
                    source_line=claim.source_line,
                    expr_source=claim.expr_source,
                    expr_node=claim.expr_node,
                    anchor=claim.anchor,
                    status="TRANSLATION_ERROR",
                    anchor_node=claim.anchor_node,
                    explanation=f"Deterministic source assertion: {exc}",
                    replay_expr_source=claim.expr_source,
                )
            )
        else:
            out.append(
                Claim(
                    claim_id=claim.claim_id,
                    source_line=claim.source_line,
                    expr_source=validated.formula,
                    expr_node=claim.expr_node,
                    anchor=claim.anchor,
                    status="DIRECT",
                    anchor_node=claim.anchor_node,
                    explanation=validated.rationale,
                    direct_expression=validated.expression,
                    direct_variables=validated.variables,
                    replay_expr_source=claim.expr_source,
                )
            )
    return out


def _direct_claim_key(claim: Claim) -> tuple[str, str] | None:
    if claim.direct_expression is None or claim.anchor is None:
        return None
    return (
        claim.anchor.as_string(),
        z3.simplify(claim.direct_expression).sexpr(),
    )


def _merge_anchored_direct_claims(
    source_claims: Iterable[Claim],
    generated_claims: Iterable[Claim],
) -> List[Claim]:
    """Keep each source assert once while retaining new anchored targets."""
    out: List[Claim] = []
    seen: set[tuple[str, str]] = set()
    for claim in list(source_claims) + list(generated_claims):
        key = _direct_claim_key(claim)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(claim)
    return out


def _parse_direct_constraints(
    specifications: Iterable,
) -> tuple[List, List[Claim]]:
    """Validate jointly assumed direct constraints.

    Invalid context formulas are returned as translation/unsupported claims so
    the result records the failure instead of silently dropping the context.
    """
    validated = []
    errors: List[Claim] = []
    for index, spec in enumerate(specifications, start=1):
        formula = _spec_value(spec, "formula", "")
        variables = _spec_value(spec, "variables", [])
        rationale = _spec_value(spec, "rationale", "")
        claim_id = f"direct_constraint_{index}"
        claim_text = formula if isinstance(formula, str) else repr(formula)
        rationale_text = rationale if isinstance(rationale, str) else str(rationale)
        try:
            item = validate_direct_formula(
                formula,
                variables,
                rationale=rationale_text,
            )
        except DirectUnsupportedError:
            errors.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="UNSUPPORTED",
                    explanation=rationale_text,
                )
            )
        except DirectFormulaError:
            errors.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=rationale_text,
                )
            )
        except Exception as exc:
            errors.append(
                Claim(
                    claim_id=claim_id,
                    source_line=0,
                    expr_source=claim_text,
                    expr_node=None,
                    anchor=None,
                    status="TRANSLATION_ERROR",
                    explanation=f"direct constraint validation failed: {exc}",
                )
            )
        else:
            validated.append(item)
    return validated, errors


def _parse_direct_claims(specifications: Iterable) -> List[Claim]:
    """Validate generated direct target specifications one-by-one."""
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


def _combine_direct_constraints(validated_constraints: Iterable) -> z3.BoolRef:
    """Build the one joint SatLM context from validated constraint formulas."""
    expressions = [item.expression for item in validated_constraints]
    if not expressions:
        return z3.BoolVal(True)
    return z3.simplify(z3.And(*expressions))


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


def _process_simple_claim(claim: Claim) -> ClaimResult:
    """Check one simple claim using only the negated translated formula."""
    anchor = AnchorInfo(node_ids=[], source_lines=[], has_grounding=False)
    base = {
        "claim_id": claim.claim_id,
        "claim_text": claim.expr_source,
        "anchor": anchor,
        "step": claim.step,
        "explanation": claim.explanation,
    }

    if claim.status == "UNSUPPORTED":
        return ClaimResult(
            **base,
            status=StatusEnum.UNSUPPORTED,
            reason="structured claim uses a feature outside the supported QF-LIA subset",
        )
    if claim.status == "TRANSLATION_ERROR":
        return ClaimResult(
            **base,
            status=StatusEnum.TRANSLATION_ERROR,
            reason="structured claim could not be translated to QF-LIA SMT-LIB2",
        )
    if claim.simple_expression is None or not z3.is_bool(claim.simple_expression):
        return ClaimResult(
            **base,
            status=StatusEnum.TRANSLATION_ERROR,
            reason="simple claim has no validated Boolean expression",
        )

    try:
        obligation = build_simple_obligation(claim.simple_expression)
    except Exception as exc:
        return ClaimResult(
            **base,
            status=StatusEnum.TRANSLATION_ERROR,
            reason=f"simple validity obligation could not be built: {exc}",
        )

    kind, elapsed, model = run_simple_validity(obligation)
    stats = {"valid_ms": (elapsed or 0) * 1000}
    if kind == "unsat":
        return ClaimResult(
            **base,
            status=StatusEnum.VERIFIED,
            z3_stats=stats,
            reason="the negated simple claim is unsatisfiable",
        )
    if kind == "unknown":
        return ClaimResult(
            **base,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            z3_stats=stats,
            reason="Z3 returned unknown for the negated simple claim",
        )

    assert model is not None
    # Deliberately do not call replay_counterexample here.  The simple
    # baseline has no source target location or trusted program state.
    return ClaimResult(
        **base,
        status=StatusEnum.COUNTEREXAMPLE,
        counterexample=_counterexample_from_model(model, claim),
        z3_stats=stats,
        reason="Z3 found an integer model satisfying the negated simple claim",
    )


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


def _process_anchored_direct_claim(
    claim: Claim,
    *,
    executor: TrustedSymbolicExecutor,
    reachabilities: List[Reachability],
    constraints: z3.BoolRef,
    context_kind: str,
    context_elapsed: Optional[float],
    context_error: str,
    baseline: str,
    source: str,
    do_replay: bool,
) -> ClaimResult:
    """Check an SMT target after binding it to an AST-derived target state."""
    anchor_info = AnchorInfo(
        node_ids=[claim.anchor.as_string()] if claim.anchor else [],
        source_lines=[claim.source_line] if claim.source_line > 0 else [],
        has_grounding=claim.anchor is not None,
    )

    if claim.status == "UNSUPPORTED":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNSUPPORTED,
            anchor=anchor_info,
            reason="anchored SMT-LIB2 formula is outside the supported QF-LIA or AST subset",
            explanation=claim.explanation,
        )
    if claim.status == "TRANSLATION_ERROR":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            reason="anchored SMT-LIB2 formula could not be parsed or validated",
            explanation=claim.explanation,
        )
    if claim.direct_expression is None or claim.anchor is None:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            reason="anchored target has no validated formula or exact AST anchor",
            explanation=claim.explanation,
        )

    if context_kind == "error":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            z3_stats={"context_ms": (context_elapsed or 0) * 1000},
            reason=(
                "joint anchored constraints could not be translated or validated"
                + (f": {context_error}" if context_error else "")
            ),
            explanation=claim.explanation,
        )
    if context_kind == "unsat":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor_info,
            z3_stats={"context_ms": (context_elapsed or 0) * 1000},
            reason="joint anchored constraints are inconsistent; target entailment would be vacuous",
            explanation=claim.explanation,
        )
    if context_kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            z3_stats={"context_ms": (context_elapsed or 0) * 1000},
            reason="Z3 returned unknown while checking anchored constraint consistency",
            explanation=claim.explanation,
        )

    if not reachabilities:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            reason="no bounded path to the exact anchored target was enumerated",
            explanation=claim.explanation,
        )

    reach_ms = 0.0
    valid_ms = 0.0
    reachable_paths = 0
    for reachability in reachabilities:
        try:
            obligation = build_anchored_direct_obligations(
                claim,
                reachability,
                constraints,
            )
        except Exception as exc:
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.TRANSLATION_ERROR,
                anchor=anchor_info,
                reason=f"anchored target could not be bound to target state: {exc}",
                explanation=claim.explanation,
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
                reason="Z3 returned unknown on anchored target reachability",
                explanation=claim.explanation,
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
                reason="Z3 returned unknown on anchored target validity",
                explanation=claim.explanation,
            )
        if v_kind == "sat":
            assert v_model is not None
            ce_info = _counterexample_from_model(v_model, claim)
            replay_v: Optional[bool] = None
            replay_out: Optional[str] = None
            if do_replay and claim.replay_expr_source and ce_info.z3_model:
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
                    "Z3 found a violating model at the exact AST target"
                    + ("; replay confirmed the source assertion violation" if replay_v is True else "")
                    + ("; replay did not confirm the source assertion violation" if replay_v is False else "")
                ),
                explanation=claim.explanation,
            )

    if reachable_paths == 0:
        if any(not reachability.complete for reachability in reachabilities):
            return ClaimResult(
                claim_id=claim.claim_id,
                claim_text=claim.expr_source,
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor_info,
                z3_stats={"reach_ms": reach_ms},
                reason="anchored target paths are unreachable, but control-flow coverage is incomplete",
                explanation=claim.explanation,
            )
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNREACHABLE,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms},
            reason="all bounded paths to the exact AST target are unreachable",
            explanation=claim.explanation,
        )

    if not all(reachability.complete for reachability in reachabilities):
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor_info,
            z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
            reason="all modeled anchored paths satisfy the target, but loop/control-flow coverage is incomplete",
            explanation=claim.explanation,
        )

    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.VERIFIED,
        anchor=anchor_info,
        z3_stats={"reach_ms": reach_ms, "valid_ms": valid_ms},
        reason="target holds on every complete AST-derived path to its exact anchor",
        explanation=claim.explanation,
    )


def _process_direct_claim(
    claim: Claim,
    *,
    constraints: z3.BoolRef,
    context_kind: str,
    context_elapsed: Optional[float],
    context_error: str = "",
) -> ClaimResult:
    """Check one direct target with the joint SatLM-style context.

    The direct result deliberately has no AST anchor, source target location,
    symbolic-executor state, reachability query, or replay verdict.
    """
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

    if context_kind == "error":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            z3_stats={
                "context_ms": (context_elapsed or 0) * 1000,
            },
            reason=(
                "joint direct constraints could not be translated or validated"
                + (f": {context_error}" if context_error else "")
            ),
            explanation=claim.explanation,
        )
    if context_kind == "unsat":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            z3_stats={"context_ms": (context_elapsed or 0) * 1000},
            reason=(
                "joint direct constraints are inconsistent; target entailment "
                "would otherwise be vacuous"
            ),
            explanation=claim.explanation,
        )
    if context_kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor,
            z3_stats={"context_ms": (context_elapsed or 0) * 1000},
            reason="Z3 returned unknown while checking direct constraint consistency",
            explanation=claim.explanation,
        )

    try:
        obligation = build_direct_obligation(constraints, claim.direct_expression)
    except Exception as exc:
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.TRANSLATION_ERROR,
            anchor=anchor,
            reason=f"direct entailment obligation could not be built: {exc}",
            explanation=claim.explanation,
        )

    kind, elapsed, model = run_validity(obligation)
    stats = {
        "context_ms": (context_elapsed or 0) * 1000,
        "valid_ms": (elapsed or 0) * 1000,
    }
    if kind == "unsat":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.VERIFIED,
            anchor=anchor,
            z3_stats=stats,
            reason=(
                "the target follows from the joint direct constraints; "
                "no AST anchor or replay is applicable"
            ),
            explanation=claim.explanation,
        )
    if kind == "unknown":
        return ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.expr_source,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=anchor,
            z3_stats=stats,
            reason="Z3 returned unknown for the direct constraints AND not-target query",
            explanation=claim.explanation,
        )

    assert model is not None
    # Direct counterexamples are deliberately not replayed: there is no target
    # location or program-state binding in this baseline.
    return ClaimResult(
        claim_id=claim.claim_id,
        claim_text=claim.expr_source,
        status=StatusEnum.COUNTEREXAMPLE,
        anchor=anchor,
        counterexample=_counterexample_from_model(model, claim),
        z3_stats=stats,
        reason=(
            "Z3 found an integer model satisfying the joint direct constraints "
            "and not the target; no program replay is applicable"
        ),
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
        claim_expression=claim.replay_expr_source or claim.expr_source,
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
    direct_constraints: Optional[Sequence[str]] = None,
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
        direct_constraints=list(direct_constraints or []),
    )


__all__ = ["run_pipeline"]
