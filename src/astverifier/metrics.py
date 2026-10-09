"""
metrics.py - Aggregate metrics over a run (Phase 4).

Reads ground-truth JSON for FDR; sums Token / HR / CRVR.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from .classify import ProgramResult, StatusEnum


# Default token prices (USD per 1k tokens) - configurable
PRICE_IN_PER_1K = 0.005
PRICE_OUT_PER_1K = 0.015


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
    hr: float = 0.0                    # Hallucination Rate
    crvr: float | None = None          # Counterexample Replay Validity Rate
    n_programs: int = 0
    n_claims: int = 0


def aggregate(
    results: List[ProgramResult],
    ground_truth: Dict[str, str] | None = None,
) -> Metrics:
    """Aggregate metrics across many ProgramResult objects.

    ground_truth[program_id] is the expected verdict ("VERIFIED"|"UNSAT" etc.).
    We consider a ProgramResult correct if its dominant status matches the GT.
    """
    if not results:
        return Metrics()

    n_total = len(results)
    n_claims_total = sum(r.n_claims for r in results)
    sum_in = sum(r.total_tokens_in for r in results)
    sum_out = sum(r.total_tokens_out for r in results)
    token_avg_in = sum_in / n_total
    token_avg_out = sum_out / n_total
    cost_avg = (sum_in * PRICE_IN_PER_1K + sum_out * PRICE_OUT_PER_1K) / n_total / 1000

    # Hallucination Rate: (UNSUPPORTED + TRANSLATION_ERROR) / total claims
    hr_n = sum(r.n_unsupported + r.n_translation_error for r in results)
    hr = hr_n / n_claims_total if n_claims_total else 0.0

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
    crvr = (
        sum(verdict is True for verdict in replay_verdicts) / len(replay_verdicts)
        if replay_verdicts
        else None
    )

    # Classification metrics: VERIFIED is the positive class. Every other
    # verdict is negative for this binary summary, including UNKNOWN_TIMEOUT.
    fdr = accuracy = precision = recall = f1 = None
    if ground_truth:
        tp = fp = fn = tn = 0
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
    )


def _normalize_program_id(program_id: str) -> str:
    """Normalize persisted dataset IDs before ground-truth lookup."""
    norm = program_id.replace("__", "/")
    prefix = "sv-benchmarks-loops-py/"
    if norm.startswith(prefix):
        norm = norm[len(prefix):]
    return norm


def load_ground_truth(json_path: str | Path) -> Dict[str, str]:
    p = Path(json_path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


__all__ = [
    "Metrics",
    "aggregate",
    "load_ground_truth",
    "PRICE_IN_PER_1K",
    "PRICE_OUT_PER_1K",
]
