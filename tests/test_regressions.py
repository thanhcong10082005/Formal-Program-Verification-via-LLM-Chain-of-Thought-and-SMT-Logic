import sys

import pytest

sys.path.insert(0, "src")

from astverifier.executor import TrustedSymbolicExecutor
from astverifier.binding import extract_asserts
from astverifier.classify import AnchorInfo, ClaimResult, ProgramResult, StatusEnum
from astverifier.metrics import aggregate
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


def test_unanchored_is_direct_formalization_without_program_state():
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"

    anchored = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)
    unanchored = run_pipeline(source, baseline="UNANCHORED", do_replay=False)

    assert anchored.claim_results[0].status.value == "VERIFIED"
    assert unanchored.claim_results[0].status.value == "COUNTEREXAMPLE"
    assert unanchored.claim_results[0].anchor.node_ids == []
    assert unanchored.claim_results[0].anchor.source_lines == []


def test_direct_formalization_uses_smtlib_and_only_not_c():
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"
    result = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {
                "formula": "(or (>= x 0) (< x 0))",
                "variables": ["x"],
                "rationale": "Every integer is nonnegative or negative.",
            }
        ],
        do_replay=True,
    )

    assert result.n_claims == 2
    assert result.claim_results[0].claim_id == "assert_L3_#1"
    assert result.claim_results[0].claim_text == "(>= x n)"
    assert result.claim_results[0].status.value == "COUNTEREXAMPLE"
    assert result.claim_results[1].status.value == "VERIFIED"
    assert result.claim_results[1].explanation.startswith("Every integer")
    for claim in result.claim_results:
        assert claim.anchor.node_ids == []
        assert claim.anchor.source_lines == []
        if claim.counterexample:
            assert claim.counterexample.replay_verdict is None


def test_source_assertions_are_not_duplicated_by_matching_direct_specs():
    result = run_pipeline(
        "def f(n):\n    assert n >= 0\n",
        baseline="UNANCHORED",
        direct_specifications=[
            {
                "formula": "(>= n 0)",
                "variables": ["n"],
                "rationale": "The independently generated form repeats the source assertion.",
            }
        ],
        do_replay=False,
    )

    assert result.n_claims == 1
    assert result.claim_results[0].claim_id == "assert_L2_#1"
    assert result.claim_results[0].explanation.startswith("The independently generated")


def test_direct_counterexample_has_unconstrained_integer_model():
    result = run_pipeline(
        "def f(n):\n    pass\n",
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(>= x 0)", "variables": ["x"]}
        ],
        do_replay=True,
    )

    claim = result.claim_results[0]
    assert claim.status.value == "COUNTEREXAMPLE"
    assert isinstance(claim.counterexample.z3_model["x"], int)
    assert claim.counterexample.replay_verdict is None


def test_direct_formula_validation_rejects_nonlinear_and_malformed_specs():
    source = "def f(n):\n    pass\n"
    nonlinear = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(= (* x x) 0)", "variables": ["x"]}
        ],
        do_replay=False,
    )
    malformed = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(>= x 0", "variables": ["x"]}
        ],
        do_replay=False,
    )

    assert nonlinear.claim_results[0].status.value == "UNSUPPORTED"
    assert malformed.claim_results[0].status.value == "TRANSLATION_ERROR"


def test_direct_formula_validation_rejects_quantifiers_and_variable_divisors():
    source = "def f(n):\n    pass\n"
    quantifier = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(forall ((x Int)) (>= x 0))", "variables": []}
        ],
        do_replay=False,
    )
    variable_divisor = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(>= (div x d) 0)", "variables": ["x", "d"]}
        ],
        do_replay=False,
    )

    assert quantifier.claim_results[0].status.value == "UNSUPPORTED"
    assert variable_divisor.claim_results[0].status.value == "UNSUPPORTED"


def test_unanchored_rejects_legacy_python_claim_inputs():
    with pytest.raises(ValueError, match="legacy Python claims"):
        run_pipeline(
            "def f(n):\n    pass\n",
            baseline="UNANCHORED",
            suggested_claims=[{"line": 2, "expression": "n >= 0"}],
            do_replay=False,
        )


def test_direct_llm_failure_is_translation_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("simulated direct call failure")

    monkeypatch.setattr("astverifier.pipeline.generate_direct_formalization", fail)
    result = run_pipeline(
        "def f(n):\n    pass\n",
        baseline="UNANCHORED",
        use_llm=True,
        gemini_api_key="test-key",
        do_replay=False,
    )

    assert result.n_translation_error == 1
    assert result.claim_results[0].status.value == "TRANSLATION_ERROR"
    assert "simulated direct call failure" in result.claim_results[0].claim_text


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
    anchored = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        suggested_claims=[{"line": 2, "expression": "n * n >= 0"}],
        do_replay=False,
    )
    direct = run_pipeline(
        source,
        baseline="UNANCHORED",
        direct_specifications=[
            {"formula": "(>= (* n n) 0)", "variables": ["n"]}
        ],
        do_replay=False,
    )

    assert anchored.claim_results[0].status.value == "UNSUPPORTED"
    assert direct.claim_results[0].status.value == "UNSUPPORTED"


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


def test_verdict_metrics_use_verified_as_the_positive_class():
    anchor = AnchorInfo(node_ids=[], source_lines=[], has_grounding=False)
    result = ProgramResult(
        program_id="p",
        source_path="<test>",
        baseline="AST_ANCHORED",
        n_claims=3,
        claim_results=[
            ClaimResult(claim_id="v", claim_text="x >= 0", status=StatusEnum.VERIFIED, anchor=anchor),
            ClaimResult(claim_id="f", claim_text="x >= 0", status=StatusEnum.COUNTEREXAMPLE, anchor=anchor),
            ClaimResult(claim_id="u", claim_text="x >= 0", status=StatusEnum.UNKNOWN_TIMEOUT, anchor=anchor),
        ],
    )
    metrics = aggregate([result], {"p": "VERIFIED"})

    assert metrics.accuracy == 1 / 3
    assert metrics.precision == 1.0
    assert metrics.recall == 1 / 3
    assert metrics.f1 == 0.5
    assert metrics.crvr is None
