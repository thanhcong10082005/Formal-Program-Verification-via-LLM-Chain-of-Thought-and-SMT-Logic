"""
baselines/ - Baseline B, C, D implementations (improved 2026-10-15).

This package contains three independently generated verification baselines that are
compared against the AST-anchored CoT + Z3 approach (Baseline A) and the
Proposed Method.

Baseline B  : Execution Grounding  - Thakur et al. 2026, CRUXEval, Self-Debug
              (IMPROVEMENT 2.x: symbolic-aware inputs, 5s timeout, 2-stage prompt)
Baseline C  : Structural Reasoning - HoarePrompt (Bouras et al., ICSE 2026)
              (IMPROVEMENT 3.x: hierarchical CFG, 3-sample self-consistency)
Baseline D  : LLM-as-Judge        - Wei et al. NeurIPS 2022, VeryTrace
              (IMPROVEMENT 4.x: self-consistency, JSON auto-retry)

Each baseline returns a ProgramResult with ClaimResults compatible with
astverifier.metrics.aggregate().
"""
from __future__ import annotations

from .error_taxonomy import (
    classify_one as classify_claim,
    summarize as summarize_taxonomy,
)
from .inputs import GeneratedInputs, generate_test_inputs, generate_test_inputs_v2
from .shared_replay import SharedReplayOutcome, replay_for_baseline
from .tracing import ExecutionTrace, run_concrete_execution
from .execution_grounding import (
    BaselineBResult,
    generate_execution_grounding,
    run_baseline_b,
)
from .structural_reasoning import (
    StructuralReasoningVerdict,
    extract_cfg_dump,
    generate_structural_reasoning,
    run_baseline_c,
)
from .llm_judge import (
    LLMJudgeVerdict,
    generate_llm_judge,
    run_baseline_d,
)

__all__ = [
    # inputs & tracing
    "generate_test_inputs",
    "generate_test_inputs_v2",
    "GeneratedInputs",
    "ExecutionTrace",
    "run_concrete_execution",
    # Baseline B
    "BaselineBResult",
    "generate_execution_grounding",
    "run_baseline_b",
    # Baseline C
    "StructuralReasoningVerdict",
    "extract_cfg_dump",
    "generate_structural_reasoning",
    "run_baseline_c",
    # Baseline D
    "LLMJudgeVerdict",
    "generate_llm_judge",
    "run_baseline_d",
    # shared utilities (IMPROVEMENT 1.3 / 1.4)
    "replay_for_baseline",
    "SharedReplayOutcome",
    "classify_claim",
    "summarize_taxonomy",
]
