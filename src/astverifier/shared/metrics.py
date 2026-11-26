"""
metrics.py - Aggregate metrics over a run (Phase 4).

Reads ground-truth JSON for FDR; sums Token / HR / CRVR / Latency.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schemas import ProgramResult, StatusEnum

# Default token prices (USD per 1k tokens) — defaults to gemini-3.5-flash-lite
# ($0.30 / 1M input, $2.50 / 1M output). Overridable via aggregate(..., price_*).
PRICE_IN_PER_1K = 0.0003
PRICE_OUT_PER_1K = 0.0025

# Known model prices in USD per 1M tokens: (input_per_1m, output_per_1m).
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "offline": (0.0, 0.0),
    "ollama": (0.0, 0.0),
    "stub": (0.0, 0.0),
}


def resolve_pricing(
    model: str | None = None,
    *,
    provider: str | None = None,
) -> tuple[float, float]:
    """Return ``(price_in_per_1m, price_out_per_1m)`` in USD for a model/provider."""
    if provider and provider.lower() in ("ollama", "offline", "stub"):
        return (0.0, 0.0)
    if model:
        key = model.lower().removeprefix("models/")
        if key in MODEL_PRICING_PER_1M:
            return MODEL_PRICING_PER_1M[key]
        for known, price in MODEL_PRICING_PER_1M.items():
            if key.startswith(known):
                return price
    return (PRICE_IN_PER_1K * 1000.0, PRICE_OUT_PER_1K * 1000.0)


@dataclass
class Metrics:
    fdr: float | None = None           # False Discovery Rate
    accuracy: float | None = None      # (TP + TN) / all labelled claims
    precision: float | None = None     # TP / (TP + FP)
    recall: float | None = None        # TP / (TP + FN)
    f1: float | None = None            # VERIFIED-positive F1 score
    token_avg_in: float = 0.0
    token_avg_out: float = 0.0
    cost_avg_usd: float = 0.0
    hr: float | None = None            # Hallucination Rate (None if no grounding)
    crvr: float | None = None          # Counterexample Replay Validity Rate
    n_programs: int = 0
    n_claims: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    latency_avg_s: float = 0.0
    total_elapsed_s: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    n_verified: int = 0
    n_counterexample: int = 0
    n_unreachable: int = 0
    n_unknown_timeout: int = 0
    n_unsupported: int = 0
    n_translation_error: int = 0
    crvr_confirmed: int = 0
    crvr_total: int = 0
    n_rejected: int = 0


def aggregate(
    results: list[ProgramResult],
    ground_truth: dict[str, str] | None = None,
    *,
    price_in_per_1m: float | None = None,
    price_out_per_1m: float | None = None,
) -> Metrics:
    """Aggregate metrics across many ProgramResult objects.

    ground_truth[program_id] is the expected verdict ("VERIFIED"|"COUNTEREXAMPLE" etc.).
    VERIFIED is treated as the positive class; every other verdict (including
    UNKNOWN_TIMEOUT) is negative.
    """
    if not results:
        return Metrics()

    n_total = len(results)
    n_claims_total = sum(r.n_claims for r in results)
    sum_in = sum(r.total_tokens_in for r in results)
    sum_out = sum(r.total_tokens_out for r in results)
    total_elapsed = sum(r.elapsed_seconds for r in results)
    token_avg_in = sum_in / n_total
    token_avg_out = sum_out / n_total
    latency_avg_s = total_elapsed / n_total

    p_in = (PRICE_IN_PER_1K * 1000.0) if price_in_per_1m is None else price_in_per_1m
    p_out = (PRICE_OUT_PER_1K * 1000.0) if price_out_per_1m is None else price_out_per_1m
    total_cost = (sum_in * p_in + sum_out * p_out) / 1_000_000.0
    cost_avg = total_cost / n_total

    n_verified = sum(r.n_verified for r in results)
    n_counterexample = sum(r.n_counterexample for r in results)
    n_unreachable = sum(r.n_unreachable for r in results)
    n_unknown_timeout = sum(r.n_unknown_timeout for r in results)
    n_unsupported = sum(r.n_unsupported for r in results)
    n_translation_error = sum(r.n_translation_error for r in results)
    n_rejected = sum(1 for r in results if r.rejected_by_subset)

    # Hallucination Rate: (UNSUPPORTED + TRANSLATION_ERROR) / total claims
    # Grounding is only supported when an anchor gate is present.
    # When no claims have grounding (e.g. unanchored baseline D), HR = None (N/A != 0).
    has_any_grounding = any(
        cr.anchor.has_grounding
        for r in results
        for cr in r.claim_results
    )
    if not has_any_grounding or not n_claims_total:
        hr = None if not has_any_grounding else 0.0
    else:
        hr = (n_unsupported + n_translation_error) / n_claims_total

    # CRVR is defined only for counterexamples with an actual replay verdict.
    # Direct Formalization deliberately stores ``replay_verdict = None`` for
    # every model, so it must report n/a rather than treating non-replay as a
    # failed replay.
    replay_verdicts: list[bool] = []
    for r in results:
        for cr in r.claim_results:
            if cr.status == StatusEnum.COUNTEREXAMPLE and cr.counterexample:
                verdict = cr.counterexample.replay_verdict
                if verdict is not None:
                    replay_verdicts.append(verdict)
    crvr_confirmed = sum(verdict is True for verdict in replay_verdicts)
    crvr_total = len(replay_verdicts)
    crvr = (crvr_confirmed / crvr_total) if crvr_total else None

    # Classification metrics: VERIFIED is the positive class. Every other
    # verdict is negative for this binary summary, including UNKNOWN_TIMEOUT.
    fdr = accuracy = precision = recall = f1 = None
    tp = fp = fn = tn = 0
    if ground_truth:
        for r in results:
            gt_status = ground_truth.get(_normalize_program_id(r.program_id))
            if gt_status is None:
                continue
            for cr in r.claim_results:
                if cr.status == StatusEnum.VERIFIED:
                    if gt_status.upper() == "VERIFIED":
                        tp += 1
                    else:
                        fp += 1
                elif gt_status.upper() == "VERIFIED":
                    fn += 1
                else:
                    tn += 1
        denom = tp + fp
        fdr = (fp / denom) if denom else None
        total = tp + fp + fn + tn
        accuracy = ((tp + tn) / total) if total else None
        precision = (tp / (tp + fp)) if (tp + fp) else None
        recall = (tp / (tp + fn)) if (tp + fn) else None
        f1_denom = 2 * tp + fp + fn
        f1 = (2 * tp / f1_denom) if f1_denom else None

    return Metrics(
        fdr=fdr,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        token_avg_in=token_avg_in,
        token_avg_out=token_avg_out,
        cost_avg_usd=cost_avg,
        hr=hr,
        crvr=crvr,
        n_programs=n_total,
        n_claims=n_claims_total,
        total_tokens_in=sum_in,
        total_tokens_out=sum_out,
        total_cost_usd=total_cost,
        latency_avg_s=latency_avg_s,
        total_elapsed_s=total_elapsed,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        n_verified=n_verified,
        n_counterexample=n_counterexample,
        n_unreachable=n_unreachable,
        n_unknown_timeout=n_unknown_timeout,
        n_unsupported=n_unsupported,
        n_translation_error=n_translation_error,
        crvr_confirmed=crvr_confirmed,
        crvr_total=crvr_total,
        n_rejected=n_rejected,
    )


def _normalize_program_id(program_id: str) -> str:
    """Normalize persisted dataset IDs before ground-truth lookup."""
    norm = program_id.replace("__", "/")
    prefix = "sv-benchmarks-loops-py/"
    if norm.startswith(prefix):
        norm = norm[len(prefix):]
    return norm


def load_ground_truth(json_path: str | Path) -> dict[str, str]:
    p = Path(json_path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


__all__ = [
    "MODEL_PRICING_PER_1M",
    "PRICE_IN_PER_1K",
    "PRICE_OUT_PER_1K",
    "Metrics",
    "aggregate",
    "load_ground_truth",
    "resolve_pricing",
]
