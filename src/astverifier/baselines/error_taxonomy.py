"""
error_taxonomy.py - Categorize claim-level outcomes for the 3 baselines.

Goal (IMPROVEMENT 1.4): replace the "UNKNOWN / NO_CLAIMS / etc." black box
with a richer taxonomy that tells us *why* a claim is wrong / skipped.

For each ``ClaimResult`` we produce one of:

    MISSING_CLAIM             no assert and no synthesized claim
    TIMEOUT                   execution / LLM call exceeded budget
    LLM_HALLUCINATED_VERIFIED LLM said VERIFIED but ground truth has CE
    LLM_HALLUCINATED_CE       LLM said COUNTEREXAMPLE but CPython replay
                              says claim still holds
    UNREACHABLE_PATH          claim line not on a reachable path
    WRONG_LOOP_INVARIANT      LLM k-induction step was wrong
    OK_VERIFIED               ground truth matches VERIFIED
    OK_COUNTEREXAMPLE         ground truth matches COUNTEREXAMPLE
    NO_GROUND_TRUTH           no ground truth available for this program
    OK_UNKNOWN                ground truth is UNKNOWN and we said UNKNOWN
    UNKNOWN_NO_GT             ground truth missing and we said UNKNOWN

Ground truth comparison is *optional*: when not provided we degrade
gracefully to the "OK_*_NO_GT" / "NO_GROUND_TRUTH" labels.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from ..classify import ClaimResult, ProgramResult, StatusEnum


# Public category strings (used as ClaimResult.claim_id suffix / metric buckets)
MISSING_CLAIM = "MISSING_CLAIM"
TIMEOUT = "TIMEOUT"
LLM_HALLUCINATED_VERIFIED = "LLM_HALLUCINATED_VERIFIED"
LLM_HALLUCINATED_CE = "LLM_HALLUCINATED_CE"
UNREACHABLE_PATH = "UNREACHABLE_PATH"
WRONG_LOOP_INVARIANT = "WRONG_LOOP_INVARIANT"
OK_VERIFIED = "OK_VERIFIED"
OK_COUNTEREXAMPLE = "OK_COUNTEREXAMPLE"
NO_GROUND_TRUTH = "NO_GROUND_TRUTH"
OK_UNKNOWN = "OK_UNKNOWN"
UNKNOWN_NO_GT = "UNKNOWN_NO_GT"


def classify_one(
    claim: ClaimResult,
    ground_truth: Optional[str],
) -> str:
    """Return the error category for a single ClaimResult.

    ``ground_truth`` is one of {"VERIFIED", "COUNTEREXAMPLE", "UNKNOWN", None}.
    """
    if ground_truth is None:
        if claim.status == StatusEnum.UNKNOWN_TIMEOUT:
            return UNKNOWN_NO_GT
        if claim.status == StatusEnum.VERIFIED:
            return OK_VERIFIED + "_NO_GT"
        if claim.status == StatusEnum.COUNTEREXAMPLE:
            return OK_COUNTEREXAMPLE + "_NO_GT"
        if claim.status == StatusEnum.UNREACHABLE:
            return UNREACHABLE_PATH
        if claim.status == StatusEnum.TRANSLATION_ERROR:
            return TIMEOUT  # broad bucket
        return NO_GROUND_TRUTH

    # Ground truth available
    if claim.status == StatusEnum.UNKNOWN_TIMEOUT or claim.status == StatusEnum.TRANSLATION_ERROR:
        # Check reason text for "timeout"
        if claim.reason and "timeout" in claim.reason.lower():
            return TIMEOUT
        if claim.reason and "LLM error" in claim.reason:
            return TIMEOUT
        return TIMEOUT

    if claim.status == StatusEnum.VERIFIED:
        if ground_truth == "VERIFIED":
            return OK_VERIFIED
        if ground_truth == "COUNTEREXAMPLE":
            return LLM_HALLUCINATED_VERIFIED
        return OK_VERIFIED  # ground truth UNKNOWN, treat as no complaint

    if claim.status == StatusEnum.COUNTEREXAMPLE:
        if ground_truth == "COUNTEREXAMPLE":
            # Replay verdict: True means CPython reproduced the violation
            # (i.e. claim_violated True on target line).  If replay_verdict
            # is False, the LLM's CE didn't actually violate the claim.
            if (
                claim.counterexample is not None
                and claim.counterexample.replay_verdict is False
            ):
                return LLM_HALLUCINATED_CE
            return OK_COUNTEREXAMPLE
        if ground_truth == "VERIFIED":
            return LLM_HALLUCINATED_CE
        return OK_COUNTEREXAMPLE  # ground truth UNKNOWN

    if claim.status == StatusEnum.UNREACHABLE:
        return UNREACHABLE_PATH

    return NO_GROUND_TRUTH


def categorize_program(
    result: ProgramResult,
    ground_truth: Optional[str],
) -> List[str]:
    """Return one category per claim in ``result``."""
    return [classify_one(c, ground_truth) for c in result.claim_results]


def summarize(
    results: Iterable[ProgramResult],
    ground_truths: dict,
) -> dict:
    """Aggregate category counts across many ProgramResults.

    Returns a dict ``{category: count}`` plus the total claim count.
    """
    counts: dict = {}
    total = 0
    for r in results:
        gt = ground_truths.get(r.program_id)
        for cat in categorize_program(r, gt):
            counts[cat] = counts.get(cat, 0) + 1
            total += 1
    counts["__total__"] = total
    return counts


__all__ = [
    "MISSING_CLAIM", "TIMEOUT", "LLM_HALLUCINATED_VERIFIED",
    "LLM_HALLUCINATED_CE", "UNREACHABLE_PATH", "WRONG_LOOP_INVARIANT",
    "OK_VERIFIED", "OK_COUNTEREXAMPLE", "NO_GROUND_TRUTH",
    "OK_UNKNOWN", "UNKNOWN_NO_GT",
    "classify_one", "categorize_program", "summarize",
]
