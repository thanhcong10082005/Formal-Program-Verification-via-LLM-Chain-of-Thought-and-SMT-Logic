import sys

sys.path.insert(0, "src")

from astverifier.executor import TrustedSymbolicExecutor
from astverifier.binding import extract_asserts
from astverifier.nodes import assign_ids
from astverifier.pipeline import run_pipeline
from astverifier.replay import replay_counterexample
from astverifier.subset import check_and_normalize


def test_anchor_requires_exact_line_and_scope():
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


def test_unanchored_is_a_free_predicate_baseline():
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"

    anchored = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)
    unanchored = run_pipeline(source, baseline="UNANCHORED", do_replay=False)

    assert anchored.claim_results[0].status.value == "VERIFIED"
    assert unanchored.claim_results[0].status.value == "COUNTEREXAMPLE"
    assert unanchored.claim_results[0].anchor.node_ids == []
    assert unanchored.claim_results[0].anchor.source_lines == []


def test_claim_after_dead_loop_uses_skip_path():
    source = (
        "def f(y, z):\n"
        "    while 0:\n"
        "        y = y + 1\n"
        "    assert y == z\n"
    )

    result = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)

    assert result.claim_results[0].status.value == "COUNTEREXAMPLE"


def test_incomplete_break_path_is_not_reported_as_unreachable():
    source = (
        "def f(n):\n"
        "    while n > 0:\n"
        "        break\n"
        "    assert n <= 0\n"
    )

    result = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)

    assert result.claim_results[0].status.value == "UNKNOWN_TIMEOUT"


def test_reachability_relation_contains_target_state_equations():
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
    assert not check_and_normalize("def f(n):\n    x = n * n\n").accepted
    assert not check_and_normalize("def f(n, d):\n    x = n // d\n").accepted
    assert check_and_normalize("def f(n):\n    x = n // 2\n").accepted


def test_llm_claims_are_checked_against_the_arithmetic_subset():
    source = "def f(n):\n    x = n + 1\n    return x\n"
    for baseline in ("AST_ANCHORED", "UNANCHORED"):
        result = run_pipeline(
            source,
            baseline=baseline,
            suggested_claims=[{"line": 2, "expression": "n * n >= 0"}],
            do_replay=False,
        )
        assert result.claim_results[0].status.value == "UNSUPPORTED"


def test_replay_requires_target_line_and_false_claim():
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
