"""The common interface every Baseline implements.

A baseline is a *complete, independent* method: it receives the program's
source and the shared claims, and returns a :class:`ProgramResult` in the
same schema the Core Proposed Method uses. It must not import from
:mod:`astverifier.core` -- the invariant is enforced by
``tests/test_baselines.py::test_no_baseline_imports_core``.

The four ``supports_*`` flags are the mechanism behind the "N/A != 0"
principle: a baseline that has no AST-anchor gate reports
``supports_grounding = False``, so the shared metric layer prints ``N/A``
for the Hallucination Rate instead of a misleading ``0``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..shared.claims import Claim
from ..shared.schemas import (
    AnchorInfo,
    ClaimResult,
    ProgramResult,
    StatusEnum,
)


@dataclass
class ProgramSpec:
    """Everything a baseline is given for one program.

    Attributes:
        program_id: The program's identifier, e.g. ``loops/p00019.py``.
        source: The program source.
        source_path: Where the source was read from.
        claims: The shared claims for this program.
        precondition: The path condition of the claim, when it has one.
        metadata: Free-form extras (for example the ground-truth verdict).
    """

    program_id: str
    source: str
    source_path: str = ""
    claims: list[Claim] = field(default_factory=list)
    precondition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def function_name(self) -> str:
        """The name of the function under verification.

        Falls back to ``"main"`` when the source cannot be parsed.
        """
        import ast

        try:
            tree = ast.parse(self.source)
        except SyntaxError:
            return "main"
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                return node.name
        return "main"


class BaseBaseline(ABC):
    """Abstract base class for the 14 baselines.

    Subclasses must set :attr:`id` and implement :meth:`run`.
    """

    #: The single-letter identifier of the baseline, ``"A"`` .. ``"N"``.
    id: str = ""

    #: A human-readable name, used in the results table.
    name: str = ""

    #: True when the baseline has an AST-anchor gate -> Hallucination Rate
    #: is measurable. ``False`` means the metric is ``N/A``, not ``0``.
    supports_grounding: bool = False

    #: True when the baseline can replay a counterexample on real CPython ->
    #: CRVR is measurable.
    supports_replay: bool = False

    #: True when the baseline can decide reachability -> "UNREACHABLE
    #: detected" is measurable.
    supports_reachability: bool = False

    #: True when the baseline honours the claim's path precondition.
    supports_precondition: bool = False

    #: True when the baseline needs an LLM to produce a verdict.
    requires_llm: bool = False

    @abstractmethod
    def run(self, spec: ProgramSpec) -> ProgramResult:
        """Verify one program and return its result.

        Implementations must never raise for an ordinary program: a failure
        inside a baseline has to be reported as ``UNKNOWN_TIMEOUT`` or
        ``TRANSLATION_ERROR`` in the returned result, not as an exception.
        """

    # -- helpers for subclasses ------------------------------------------------

    @staticmethod
    def anchor_info(claim: Claim, *, has_grounding: bool = False) -> AnchorInfo:
        """Build the anchor information for a claim.

        Args:
            claim: The claim.
            has_grounding: Whether this baseline actually grounded the claim
                in the AST. When ``False`` the metric layer reports ``N/A``.
        """
        return AnchorInfo(
            node_ids=[claim.ast_anchor] if claim.ast_anchor else [],
            source_lines=[claim.source_line] if claim.source_line else [],
            has_grounding=has_grounding,
        )

    @staticmethod
    def roll_up(
        spec: ProgramSpec,
        baseline_id: str,
        claim_results: list[ClaimResult],
        *,
        started: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cot_trace: str = "",
        extra: dict[str, Any] | None = None,
    ) -> ProgramResult:
        """Aggregate per-claim results into a :class:`ProgramResult`."""
        counts: dict[StatusEnum, int] = dict.fromkeys(StatusEnum, 0)
        for result in claim_results:
            counts[result.status] += 1
        return ProgramResult(
            program_id=spec.program_id,
            source_path=spec.source_path,
            source_text=spec.source,
            n_claims=len(claim_results),
            n_verified=counts[StatusEnum.VERIFIED],
            n_counterexample=counts[StatusEnum.COUNTEREXAMPLE],
            n_unreachable=counts[StatusEnum.UNREACHABLE],
            n_unsupported=counts[StatusEnum.UNSUPPORTED],
            n_translation_error=counts[StatusEnum.TRANSLATION_ERROR],
            n_unknown_timeout=counts[StatusEnum.UNKNOWN_TIMEOUT],
            claim_results=claim_results,
            elapsed_seconds=time.perf_counter() - started,
            baseline=baseline_id,
            total_tokens_in=tokens_in,
            total_tokens_out=tokens_out,
            cot_trace=cot_trace,
            precondition=spec.precondition,
            **(extra or {}),
        )


__all__ = ["BaseBaseline", "ProgramSpec"]
