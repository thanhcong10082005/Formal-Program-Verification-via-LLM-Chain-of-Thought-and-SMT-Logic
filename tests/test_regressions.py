"""Regression tests for the Core Proposed Method.

These tests pin down the behaviour that the Baseline phase relies on:
the anchor gate, the two-query Z3 protocol, the subset gate, the replay
mechanism and the metric definitions.
"""

from __future__ import annotations

import pytest

from astverifier.core.binding import extract_asserts
from astverifier.core.executor import TrustedSymbolicExecutor
from astverifier.core.pipeline import run_pipeline
from astverifier.shared.anchors import assign_ids
from astverifier.shared.metrics import aggregate
from astverifier.shared.replay import replay_counterexample
from astverifier.shared.schemas import (
    AnchorInfo,
    ClaimResult,
    ProgramResult,
    StatusEnum,
)
from astverifier.shared.subset import check_and_normalize


def test_anchor_requires_exact_line_and_scope():
    """A claim is only anchored when it names exactly one real statement."""
    source = "def f(n):\n    x = n + 1\n    return x\n"

    missing_line = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        suggested_claims=[{"line": 42, "expression": "n >= 0"}],
        do_replay=False,
    )
    out_of_scope = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        suggested_claims=[{"line": 2, "expression": "missing >= 0"}],
        do_replay=False,
    )

    assert missing_line.claim_results[0].status.value == "UNSUPPORTED"
    assert out_of_scope.claim_results[0].status.value == "UNSUPPORTED"


def test_claim_after_dead_loop_uses_skip_path():
    """A loop with a false guard is skipped, so the claim is reachable."""
    source = (
        "def f(y, z):\n"
        "    while 0:\n"
        "        y = y + 1\n"
        "    assert y == z\n"
    )

    result = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)

    assert result.claim_results[0].status.value == "COUNTEREXAMPLE"


def test_incomplete_break_path_is_not_reported_as_unreachable():
    """Incomplete control-flow coverage yields UNKNOWN_TIMEOUT, not UNREACHABLE."""
    source = (
        "def f(n):\n"
        "    while n > 0:\n"
        "        break\n"
        "    assert n <= 0\n"
    )

    result = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)

    assert result.claim_results[0].status.value == "UNKNOWN_TIMEOUT"


def test_reachability_relation_contains_target_state_equations():
    """The reachability relation carries the state, not just the path condition."""
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"
    subset = check_and_normalize(source)
    ids = assign_ids(subset.tree)
    executor = TrustedSymbolicExecutor(subset.tree, ids)
    executor.build_all_transitions()
    claim = extract_asserts(subset.tree, ids)[0]
    path, guards = executor.enumerate_target_paths(claim.anchor)[0]
    reachability = executor.build_reachability(path, guards)

    assert str(reachability.relation) != str(reachability.path_condition)
    assert str(reachability.state.bindings["x"]) == "n + 1"


def test_subset_rejects_nonlinear_and_variable_division():
    """The QF-LIA gate rejects nonlinear terms and variable divisors."""
    assert not check_and_normalize("def f(n):\n    x = n * n\n").accepted
    assert not check_and_normalize("def f(n, d):\n    x = n // d\n").accepted
    assert check_and_normalize("def f(n):\n    x = n // 2\n").accepted


def test_subset_rejects_strings_lists_and_imports():
    """Strings, lists and imports are outside the supported subset."""
    assert not check_and_normalize("def f(n):\n    x = 'a'\n").accepted
    assert not check_and_normalize("def f(n):\n    x = [1, 2]\n").accepted
    assert not check_and_normalize("import os\n").accepted


def test_llm_claims_are_checked_against_the_arithmetic_subset():
    """An LLM claim outside the subset is rejected, not silently verified."""
    source = "def f(n):\n    x = n + 1\n    return x\n"
    result = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        suggested_claims=[{"line": 2, "expression": "n * n >= 0"}],
        do_replay=False,
    )

    assert result.claim_results[0].status.value == "UNSUPPORTED"


def test_replay_requires_target_line_and_false_claim():
    """Replay only succeeds when the target line is reached and the claim is false."""
    source = "def f(n):\n    assert n >= 0\n    return n\n"
    reproduced = replay_counterexample(
        source,
        "f",
        {"n": -1},
        target_line=2,
        claim_expression="n >= 0",
    )
    not_reproduced = replay_counterexample(
        source,
        "f",
        {"n": 1},
        target_line=2,
        claim_expression="n >= 0",
    )

    assert reproduced.success is True
    assert not_reproduced.success is False
    assert "claim" in (not_reproduced.error or "")


def test_verdict_metrics_use_verified_as_the_positive_class():
    """VERIFIED is the positive class; every other verdict is a negative."""
    anchor = AnchorInfo(node_ids=[], source_lines=[], has_grounding=False)
    result = ProgramResult(
        program_id="p",
        source_path="<test>",
        baseline="AST_ANCHORED",
        n_claims=3,
        claim_results=[
            ClaimResult(
                claim_id="v",
                claim_text="x >= 0",
                status=StatusEnum.VERIFIED,
                anchor=anchor,
            ),
            ClaimResult(
                claim_id="f",
                claim_text="x >= 0",
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=anchor,
            ),
            ClaimResult(
                claim_id="u",
                claim_text="x >= 0",
                status=StatusEnum.UNKNOWN_TIMEOUT,
                anchor=anchor,
            ),
        ],
    )
    metrics = aggregate([result], {"p": "VERIFIED"})

    assert metrics.accuracy == 1 / 3
    assert metrics.precision == 1.0
    assert metrics.recall == 1 / 3
    assert metrics.f1 == 0.5
    assert metrics.crvr is None


def test_unknown_baseline_is_rejected():
    """The Core Proposed Method only implements the anchored protocol."""
    with pytest.raises(ValueError, match="unknown baseline"):
        run_pipeline(
            "def f(n):\n    assert n >= 0\n",
            baseline="UNANCHORED",
            do_replay=False,
        )


def test_use_llm_and_injected_claims_are_mutually_exclusive():
    """Passing both claim sources is a usage error."""
    with pytest.raises(ValueError, match="not both"):
        run_pipeline(
            "def f(n):\n    assert n >= 0\n",
            baseline="AST_ANCHORED",
            use_llm=True,
            suggested_claims=[{"line": 2, "expression": "n >= 0"}],
            do_replay=False,
        )
