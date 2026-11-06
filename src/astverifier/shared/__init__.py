"""Shared schemas and utilities.

The modules in this package are used by **both** the Core Proposed Method
(:mod:`astverifier.core`) and by every Baseline (:mod:`astverifier.baselines`).

* :mod:`astverifier.shared.schemas` -- the status/result data model.
* :mod:`astverifier.shared.claims` -- the shared claim model and pool loader.
* :mod:`astverifier.shared.anchors` -- stable AST node identifiers.
* :mod:`astverifier.shared.subset` -- the QF-LIA subset gate.
* :mod:`astverifier.shared.structure` -- structural analysis of a program's shape.
* :mod:`astverifier.shared.symexec` -- symbolic execution over the QF-LIA subset.
* :mod:`astverifier.shared.prover` -- the exact symbolic prover (Z3/Spacer).
* :mod:`astverifier.shared.oracle` -- the CPython execution oracle.
* :mod:`astverifier.shared.preconditions` -- path-precondition extraction.
* :mod:`astverifier.shared.hashing` -- AST-normalized structural hashing.
* :mod:`astverifier.shared.replay` -- counterexample replay on real CPython.
* :mod:`astverifier.shared.metrics` -- aggregate metrics (FDR, CRVR, HR).

Nothing in the *labelling* half of this package calls an LLM: the labels it
produces are the instrument, so the instrument must not be graded by itself.
"""

from .anchors import NodeId, assign_ids, lookup_containing
from .claims import Claim, load_claim_pool
from .claims import load_ground_truth as load_claim_ground_truth
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
    "Claim",
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
    "load_claim_ground_truth",
    "load_claim_pool",
    "load_ground_truth",
    "lookup_containing",
    "make_empty_program_result",
    "replay_counterexample",
    "validate_expression",
]
