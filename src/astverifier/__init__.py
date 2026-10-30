"""AST-Anchored Neuro-Symbolic Verification Framework.

Layout
------
``astverifier.core``
    The Core Proposed Method (see :mod:`astverifier.core`).
``astverifier.shared``
    Schemas and utilities shared by the Proposed Method and by every
    Baseline (see :mod:`astverifier.shared`).
``astverifier.llm``
    Gemini adapters. The LLM proposes; it never decides.

Typical use::

    from astverifier import run_pipeline

    result = run_pipeline(source, baseline="AST_ANCHORED")
    print(result.n_verified, result.n_counterexample)
"""

__version__ = "0.2.0"

from .core.pipeline import run_pipeline
from .llm.gemini import generate_cot_and_claims
from .shared.metrics import Metrics, aggregate
from .shared.schemas import ProgramResult, StatusEnum

__all__ = [
    "Metrics",
    "ProgramResult",
    "StatusEnum",
    "aggregate",
    "generate_cot_and_claims",
    "run_pipeline",
]
