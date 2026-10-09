"""
classify.py - The 6 status outputs as Pydantic schemas (Phase 1, Foundation).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class StatusEnum(str, Enum):
    VERIFIED = "VERIFIED"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    UNREACHABLE = "UNREACHABLE"
    UNSUPPORTED = "UNSUPPORTED"
    TRANSLATION_ERROR = "TRANSLATION_ERROR"
    UNKNOWN_TIMEOUT = "UNKNOWN_TIMEOUT"


class AnchorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: List[str]
    source_lines: List[int]
    has_grounding: bool


class CounterexampleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    z3_model: Dict[str, int]
    replay_verdict: Optional[bool] = None  # True=reproduced, False=violation, None=not replayed
    replay_output: Optional[str] = None
    replay_diff: Optional[str] = None


class ClaimResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_text: str
    status: StatusEnum
    anchor: AnchorInfo
    counterexample: Optional[CounterexampleInfo] = None
    z3_stats: Dict[str, float] = {}
    reason: str = ""
    explanation: str = ""


class ProgramResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str
    source_path: str
    source_text: str = ""
    n_claims: int = 0
    n_verified: int = 0
    n_counterexample: int = 0
    n_unreachable: int = 0
    n_unsupported: int = 0
    n_translation_error: int = 0
    n_unknown_timeout: int = 0
    claim_results: List[ClaimResult] = []
    elapsed_seconds: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    cot_trace: str = ""
    baseline: str = ""
    rejected_by_subset: bool = False
    subset_rejection_reason: Optional[str] = None


def make_empty_program_result(
    program_id: str,
    source_path: str,
    baseline: str,
    *,
    rejected: bool = False,
    reason: Optional[str] = None,
    source_text: str = "",
) -> ProgramResult:
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source_text,
        baseline=baseline,
        rejected_by_subset=rejected,
        subset_rejection_reason=reason,
    )


__all__ = [
    "StatusEnum",
    "AnchorInfo",
    "CounterexampleInfo",
    "ClaimResult",
    "ProgramResult",
    "make_empty_program_result",
]
