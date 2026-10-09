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

    # CRVR: counterexamples where replay succeeded / total counterexamples
    ce_total = sum(r.n_counterexample for r in results)
    ce_replayed = 0
    for r in results:
        for cr in r.claim_results:
            if cr.status == StatusEnum.COUNTEREXAMPLE and cr.counterexample:
                if cr.counterexample.replay_verdict is True:
                    ce_replayed += 1
    crvr = (ce_replayed / ce_total) if ce_total else None

    # FDR: requires ground truth labels
    fdr = None
    if ground_truth:
        tp, fp = 0, 0
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
                # FDR is defined over positive VERIFIED reports.  A
                # COUNTEREXAMPLE on a verified program is a false negative,
                # not a false discovery, so it is excluded from this ratio.
        denom = tp + fp
        fdr = (fp / denom) if denom else None

    return Metrics(
        fdr=fdr,
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
