"""The baseline registry.

Every baseline module registers its class with the :func:`register`
decorator. The registry is the single source of truth for *which* baselines
exist, so ``tests/test_baselines.py`` can assert the full ID list without
hard-coding it twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .base import BaseBaseline

#: The 14 baseline identifiers, in the order of the discovery catalogue.
EXPECTED_IDS: tuple[str, ...] = (
    "A",  # SatLM Direct SMT-LIB2
    "B",  # Execution Grounding
    "C",  # Structural Reasoning
    "D",  # LLM-as-a-Judge
    "E",  # Neuro-Symbolic (AutoSpec)
    "F",  # Agentic Multi-Step
    "G",  # Self-Consistency
    "H",  # Self-Refine
    "I",  # LLM-Guided Property-Based Testing
    "J",  # Deterministic Fuzzing
    "K",  # Mutation Testing
    "L",  # Symbolic Execution
    "M",  # Hoare Triple in Natural Language
    "N",  # Invariant Synthesis
)

_REGISTRY: dict[str, type[BaseBaseline]] = {}


def register(cls: type[BaseBaseline]) -> type[BaseBaseline]:
    """Register a baseline class under its :attr:`~BaseBaseline.id`.

    Raises:
        ValueError: The class has no ``id``, or the ``id`` is already taken.
    """
    baseline_id = getattr(cls, "id", "")
    if not baseline_id:
        raise ValueError(f"{cls.__name__} has no id")
    if baseline_id in _REGISTRY:
        raise ValueError(f"duplicate baseline id {baseline_id!r}")
    _REGISTRY[baseline_id] = cls
    return cls


def get_baseline(baseline_id: str) -> type[BaseBaseline]:
    """Return the baseline class registered under ``baseline_id``.

    Raises:
        KeyError: No baseline is registered under that id.
    """
    try:
        return _REGISTRY[baseline_id]
    except KeyError:
        raise KeyError(
            f"unknown baseline {baseline_id!r}; known: {sorted(_REGISTRY)}"
        ) from None


def all_baselines() -> list[type[BaseBaseline]]:
    """Return every registered baseline class, ordered by id."""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def registered_ids() -> list[str]:
    """Return the sorted ids of every registered baseline."""
    return sorted(_REGISTRY)


__all__ = [
    "EXPECTED_IDS",
    "all_baselines",
    "get_baseline",
    "register",
    "registered_ids",
]
