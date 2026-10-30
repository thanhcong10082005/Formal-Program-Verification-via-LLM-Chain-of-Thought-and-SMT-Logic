"""Shared schemas and utilities.

The modules in this package are used by **both** the Core Proposed Method
(:mod:`astverifier.core`) and by every Baseline that will be installed
during the Baseline phase.

* :mod:`astverifier.shared.schemas` -- the status/result data model.
* :mod:`astverifier.shared.anchors` -- stable AST node identifiers.
* :mod:`astverifier.shared.subset` -- the QF-LIA subset gate.
* :mod:`astverifier.shared.replay` -- counterexample replay on real CPython.
* :mod:`astverifier.shared.metrics` -- aggregate metrics (FDR, CRVR, HR).
"""

from .anchors import NodeId, assign_ids, lookup_containing
from .metrics import Metrics, aggregate, load_ground_truth
from .replay import ReplayResult, replay_counterexample
from .schemas import (
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
    make_empty_program_result,
)
from .subset import ClaimInfo, SubsetResult, check_and_normalize, validate_expression

__all__ = [
    "AnchorInfo",
    "ClaimInfo",
    "ClaimResult",
    "CounterexampleInfo",
    "Metrics",
    "NodeId",
    "ProgramResult",
    "ReplayResult",
    "StatusEnum",
    "SubsetResult",
    "aggregate",
    "assign_ids",
    "check_and_normalize",
    "load_ground_truth",
    "lookup_containing",
    "make_empty_program_result",
    "replay_counterexample",
    "validate_expression",
]
