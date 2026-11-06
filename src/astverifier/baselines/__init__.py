"""The 14 baselines.

Every baseline is a complete, independent method. It receives the shared
claims and the program's source, and returns a
:class:`~astverifier.shared.schemas.ProgramResult` in the same schema the
Core Proposed Method uses.

The package guarantees three invariants:

1. **No baseline imports :mod:`astverifier.core`.** The Core Proposed
   Method's machinery must not leak into a comparison, so the baselines
   re-implement whatever they need on top of
   :mod:`astverifier.shared`. The invariant is enforced by
   ``tests/test_baselines.py::test_no_baseline_imports_core``.
2. **Every baseline is registered.** ``registry.all_baselines()`` lists all
   14, and the ids match ``registry.EXPECTED_IDS``.
3. **A baseline never crashes.** Every failure is reported inside the
   returned result as ``UNKNOWN_TIMEOUT`` or ``TRANSLATION_ERROR``.
"""

# Importing the modules is what performs the registration.
from . import (
    a_direct_smt,  # noqa: F401
    b_execution,  # noqa: F401
    c_structural,  # noqa: F401
    d_judge,  # noqa: F401
    e_neuro_symbolic,  # noqa: F401
    f_agentic,  # noqa: F401
    g_self_consistency,  # noqa: F401
    h_self_refine,  # noqa: F401
    i_property_based,  # noqa: F401
    j_fuzzing,  # noqa: F401
    k_mutation,  # noqa: F401
    l_symbolic,  # noqa: F401
    m_hoare_nl,  # noqa: F401
    n_invariant_smt,  # noqa: F401
)
from .base import BaseBaseline, ProgramSpec
from .llm import GeminiClient, LLMClient, LLMReply, StubClient, TokenBudget
from .registry import (
    EXPECTED_IDS,
    all_baselines,
    get_baseline,
    register,
    registered_ids,
)

__all__ = [
    "EXPECTED_IDS",
    "BaseBaseline",
    "GeminiClient",
    "LLMClient",
    "LLMReply",
    "ProgramSpec",
    "StubClient",
    "TokenBudget",
    "all_baselines",
    "get_baseline",
    "register",
    "registered_ids",
]
