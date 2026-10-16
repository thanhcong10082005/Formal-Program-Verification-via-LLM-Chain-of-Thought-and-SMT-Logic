"""
shared_replay.py - Counterexample replay helper for Baselines B, C, D.

Goal (IMPROVEMENT 1.3):
  - Provide a single function that takes a Python source + (LLM-suggested)
    counterexample input and runs it on CPython in an isolated subprocess.
  - Returns whether the claim actually fails on that input.
  - Allows C and D to upgrade from "no replay" to "CPython-confirmed
    counterexample", giving them a CRVR-like metric.

This is intentionally a thin wrapper around `astverifier.replay.replay_counterexample`
so we keep a single source of truth for the subprocess plumbing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from ..replay import ReplayResult, replay_counterexample

log = logging.getLogger(__name__)


@dataclass
class SharedReplayOutcome:
    """Result of replaying a counterexample through real CPython.

    Fields
    ------
    attempted           : True iff we actually tried to replay.
    claim_violated      : True iff the claim at target_line evaluated False
                          (i.e. the counterexample is genuine).
    output_repr         : repr() of the function's return value.
    error               : any error message from the subprocess, or None.
    elapsed_ms          : wall-clock time for the replay.
    """
    attempted: bool
    claim_violated: Optional[bool]  # None when not attempted or not targeted
    output_repr: Optional[str]
    error: Optional[str]
    elapsed_ms: float


def replay_for_baseline(
    source: str,
    func_name: str,
    counterexample_input: Optional[Dict[str, int]],
    *,
    claim_line: Optional[int] = None,
    claim_expression: Optional[str] = None,
    timeout: float = 3.0,
) -> SharedReplayOutcome:
    """Run a counterexample on CPython and report whether the claim fails.

    Parameters
    ----------
    source : str
        Full Python source of the program.
    func_name : str
        Name of the function to call.
    counterexample_input : Optional[Dict[str, int]]
        Input dict from the LLM.  When None, returns an empty outcome.
    claim_line, claim_expression : optional
        If supplied, the subprocess will evaluate ``claim_expression`` at
        ``claim_line`` to decide success/failure; otherwise we just check
        whether ``fn(**inputs)`` raises.
    timeout : float
        Subprocess timeout in seconds.
    """
    if not counterexample_input:
        return SharedReplayOutcome(
            attempted=False,
            claim_violated=None,
            output_repr=None,
            error="no counterexample input supplied",
            elapsed_ms=0.0,
        )

    # Filter to integer-valued entries (skip None / str that may have slipped in)
    safe_inputs: Dict[str, int] = {}
    for k, v in counterexample_input.items():
        if isinstance(v, bool):
            safe_inputs[k] = int(v)
        elif isinstance(v, int):
            safe_inputs[k] = v
        else:
            log.warning("replay_for_baseline: skipping non-int arg %s=%r", k, v)
    if not safe_inputs:
        return SharedReplayOutcome(
            attempted=False,
            claim_violated=None,
            output_repr=None,
            error="counterexample had no int/bool args",
            elapsed_ms=0.0,
        )

    try:
        result: ReplayResult = replay_counterexample(
            source,
            func_name,
            safe_inputs,
            target_line=claim_line,
            claim_expression=claim_expression,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - host failure
        return SharedReplayOutcome(
            attempted=True,
            claim_violated=None,
            output_repr=None,
            error=f"replay host error: {exc!r}",
            elapsed_ms=0.0,
        )

    return SharedReplayOutcome(
        attempted=True,
        claim_violated=bool(result.success),
        output_repr=result.output_repr,
        error=result.error,
        elapsed_ms=result.elapsed_ms,
    )


__all__ = [
    "SharedReplayOutcome",
    "replay_for_baseline",
]
