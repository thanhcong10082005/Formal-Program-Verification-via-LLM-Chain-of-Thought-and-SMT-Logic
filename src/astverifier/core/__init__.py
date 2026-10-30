"""Core Proposed Method.

This package implements the AST-Anchored neuro-symbolic verification
pipeline proposed in the project proposal:

1. **Grounding** -- :mod:`astverifier.core.binding` binds every claim to an
   exact AST node identifier.
2. **Semantics** -- :mod:`astverifier.core.executor` builds a trusted
   symbolic state and per-path reachability relations straight from the AST.
   No LLM output ever reaches the solver.
3. **Reasoning** -- :mod:`astverifier.core.obligations` issues the two-query
   Z3 protocol (reachability, then validity) for each anchored claim.
4. **Invariant** -- :mod:`astverifier.core.invariants` synthesises and
   k-induction-verifies loop invariants for claims below a loop.
5. **Feedback** -- :mod:`astverifier.core.feedback` runs the retrospective
   self-correction loop over confirmed counterexamples.
6. **Orchestration** -- :mod:`astverifier.core.pipeline` wires the steps
   together into :func:`run_pipeline`.

The modules here deliberately depend only on :mod:`astverifier.shared` and on
:mod:`astverifier.llm`; they never import a Baseline implementation.
"""

from .binding import Claim, bind_llm_claims, extract_asserts
from .executor import Reachability, SymState, TrustedSymbolicExecutor
from .feedback import RetrospectiveFeedbackLoop
from .invariants import LoopInvariantGenerator
from .obligations import build_obligations, run_reachability, run_validity
from .pipeline import run_pipeline

__all__ = [
    "Claim",
    "LoopInvariantGenerator",
    "Reachability",
    "RetrospectiveFeedbackLoop",
    "SymState",
    "TrustedSymbolicExecutor",
    "bind_llm_claims",
    "build_obligations",
    "extract_asserts",
    "run_pipeline",
    "run_reachability",
    "run_validity",
]
