"""The shared result schemas.

Every method -- the Core Proposed Method and every Baseline -- reports its
results with these types, so a shared claim pool and a shared metric
implementation can be reused across all of them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StatusEnum(StrEnum):
    """The verdict assigned to a single claim."""

    VERIFIED = "VERIFIED"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    UNREACHABLE = "UNREACHABLE"
    UNSUPPORTED = "UNSUPPORTED"
    TRANSLATION_ERROR = "TRANSLATION_ERROR"
    UNKNOWN_TIMEOUT = "UNKNOWN_TIMEOUT"


class AnchorInfo(BaseModel):
    """Where a claim is anchored in the program under verification."""

    model_config = ConfigDict(extra="forbid")

    node_ids: list[str]
    source_lines: list[int]
    has_grounding: bool


class CounterexampleInfo(BaseModel):
    """A concrete counterexample produced by the solver."""

    model_config = ConfigDict(extra="forbid")

    z3_model: dict[str, int]
    replay_verdict: bool | None = None
    """``True`` = reproduced, ``False`` = violation, ``None`` = not replayed."""

    replay_output: str | None = None
    replay_diff: str | None = None
    replay_iteration_count: int | None = None
    replay_failing_state: dict[str, int] | None = None
    feedback_iterations: int | None = None
    feedback_refined_claim_text: str | None = None
    feedback_refined_claim_line: int | None = None
    feedback_resolution: str | None = None
    """One of ``verified``, ``still_counter``, ``llm_rejected_ce``,
    ``max_iter_reached``, ``llm_error``, ``not_run``."""


class ClaimResult(BaseModel):
    """The verdict for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_text: str
    status: StatusEnum
    anchor: AnchorInfo
    counterexample: CounterexampleInfo | None = None
    z3_stats: dict[str, float] = {}
    reason: str = ""
    explanation: str = ""


class ProgramResult(BaseModel):
    """The verdict for one program, aggregated over its claims."""

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
    claim_results: list[ClaimResult] = []
    elapsed_seconds: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    cot_trace: str = ""
    baseline: str = ""
    direct_constraints: list[str] = []
    """SMT-LIB2 context of the SatLM-style direct baseline.

    Empty for the AST-anchored protocol.
    """

    rejected_by_subset: bool = False
    subset_rejection_reason: str | None = None


def make_empty_program_result(
    program_id: str,
    source_path: str,
    baseline: str,
    *,
    rejected: bool = False,
    reason: str | None = None,
    source_text: str = "",
) -> ProgramResult:
    """Build the result returned when a program never reaches the solver."""
    return ProgramResult(
        program_id=program_id,
        source_path=source_path,
        source_text=source_text,
        baseline=baseline,
        rejected_by_subset=rejected,
        subset_rejection_reason=reason,
    )


__all__ = [
    "AnchorInfo",
    "ClaimResult",
    "CounterexampleInfo",
    "ProgramResult",
    "StatusEnum",
    "make_empty_program_result",
]
