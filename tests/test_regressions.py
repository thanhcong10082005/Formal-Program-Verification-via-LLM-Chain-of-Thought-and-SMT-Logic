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


def _var(name):
    return {"kind": "var", "name": name}


def _int(value):
    return {"kind": "int", "value": value}


def _bool(value):
    return {"kind": "bool", "value": value}


def _op(name, *args):
    return {"kind": "op", "op": name, "args": list(args)}


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


def test_unanchored_is_simple_validity_without_program_state():
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"

    anchored = run_pipeline(source, baseline="AST_ANCHORED", do_replay=False)
    unanchored = run_pipeline(source, baseline="UNANCHORED", do_replay=False)

    assert anchored.claim_results[0].status.value == "VERIFIED"
    assert unanchored.claim_results[0].status.value == "COUNTEREXAMPLE"
    assert unanchored.claim_results[0].step == 1
    assert unanchored.claim_results[0].anchor.node_ids == []
    assert unanchored.claim_results[0].anchor.source_lines == []
    assert unanchored.direct_constraints == []
    assert unanchored.claim_results[0].counterexample.replay_verdict is None


def test_simple_qf_lia_claims_and_tautology():
    result = run_pipeline(
        "def f(n):\n    pass\n",
        baseline="UNANCHORED",
        simple_claims=[
            {
                "expression": _op(
                    "eq",
                    _op("add", _var("n"), _int(1)),
                    _op("add", _var("n"), _int(1)),
                )
            },
            {
                "expression": _op(
                    "or",
                    _op("ge", _var("n"), _int(0)),
                    _op("not", _op("ge", _var("n"), _int(0))),
                )
            },
            {
                "expression": _op(
                    "and",
                    _op(
                        "eq",
                        _op("div", _op("mul", _int(2), _var("n")), _int(2)),
                        _var("n"),
                    ),
                    _op("eq", _op("mod", _var("n"), _int(1)), _int(0)),
                )
            },
        ],
        do_replay=True,
    )

    assert [claim.step for claim in result.claim_results] == [1, 2, 3]
    assert [claim.status.value for claim in result.claim_results] == [
        "VERIFIED",
        "VERIFIED",
        "VERIFIED",
    ]
    assert result.claim_results[0].claim_text == "(= (+ n 1) (+ n 1))"
    assert "(div (* 2 n) 2)" in result.claim_results[2].claim_text
    assert "(mod n 1)" in result.claim_results[2].claim_text
    assert all(claim.anchor.node_ids == [] for claim in result.claim_results)
    assert all(claim.anchor.source_lines == [] for claim in result.claim_results)


def test_simple_source_assertions_are_not_duplicated_by_matching_claims():
    result = run_pipeline(
        "def f(n):\n    assert n >= 0\n",
        baseline="UNANCHORED",
        simple_claims=[
            {
                "expression": _op("ge", _var("n"), _int(0)),
                "rationale": "The independently generated form repeats the source assertion.",
            }
        ],
        do_replay=False,
    )

    assert result.n_claims == 1
    assert result.claim_results[0].claim_id == "source_assert_1"
    assert result.claim_results[0].explanation.startswith("The independently generated")
    assert result.claim_results[0].step == 1


def test_simple_counterexample_is_not_replayed():
    result = run_pipeline(
        "def f(n):\n    pass\n",
        baseline="UNANCHORED",
        simple_claims=[
            {"expression": _op("ge", _var("x"), _int(0))}
        ],
        do_replay=True,
    )

    claim = result.claim_results[0]
    assert claim.status.value == "COUNTEREXAMPLE"
    assert claim.counterexample.z3_model["x"] < 0
    assert claim.counterexample.replay_verdict is None


def test_simple_formula_validation_rejects_nonlinear_and_malformed_claims():
    source = "def f(n):\n    pass\n"
    nonlinear = run_pipeline(
        source,
        baseline="UNANCHORED",
        simple_claims=[
            {"expression": _op("eq", _op("mul", _var("x"), _var("x")), _int(0))}
        ],
        do_replay=False,
    )
    malformed = run_pipeline(
        source,
        baseline="UNANCHORED",
        simple_claims=[{"expression": {"kind": "op", "op": "ge", "args": "bad"}}],
        do_replay=False,
    )

    assert nonlinear.claim_results[0].status.value == "UNSUPPORTED"
    assert malformed.claim_results[0].status.value == "TRANSLATION_ERROR"


def test_simple_formula_validation_rejects_quantifiers_and_variable_divisors():
    source = "def f(n):\n    pass\n"
    quantifier = run_pipeline(
        source,
        baseline="UNANCHORED",
        simple_claims=[{"expression": {"kind": "op", "op": "forall", "args": []}}],
        do_replay=False,
    )
    variable_divisor = run_pipeline(
        source,
        baseline="UNANCHORED",
        simple_claims=[
            {"expression": _op("ge", _op("div", _var("x"), _var("d")), _int(0))}
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


def test_unanchored_rejects_legacy_direct_inputs():
    with pytest.raises(ValueError, match="legacy direct-formalization"):
        run_pipeline(
            "def f(n):\n    pass\n",
            baseline="UNANCHORED",
            direct_specifications=[{"formula": "(>= n 0)"}],
            do_replay=False,
        )


def test_simple_llm_failure_is_translation_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("simulated simple call failure")

    monkeypatch.setattr("astverifier.pipeline.generate_simple_claims", fail)
    result = run_pipeline(
        "def f(n):\n    pass\n",
        baseline="UNANCHORED",
        use_llm=True,
        gemini_api_key="test-key",
        do_replay=False,
    )

    assert result.n_translation_error == 1
    assert result.claim_results[0].status.value == "TRANSLATION_ERROR"
    assert "simulated simple call failure" in result.claim_results[0].claim_text


def test_ast_satlm_targets_are_anchored_and_use_smt_lib_text():
    source = "def f(n):\n    x = n + 1\n    assert x >= n\n"
    result = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        anchored_constraints=[
            {"formula": "(= x (+ n 1))", "variables": ["x", "n"]}
        ],
        anchored_specifications=[
            {
                "line": 3,
                "formula": "(>= x n)",
                "variables": ["x", "n"],
                "rationale": "The assertion follows from the assignment relation.",
            }
        ],
        do_replay=True,
    )

    # The generated target repeats the trusted source assertion and is kept
    # exactly once, but its public text is now SMT-LIB2 rather than Python.
    assert result.n_claims == 1
    claim = result.claim_results[0]
    assert claim.claim_text == "(>= x n)"
    assert claim.status.value == "VERIFIED"
    assert claim.anchor.has_grounding is True
    assert claim.anchor.source_lines == [3]
    assert result.direct_constraints == ["(= x (+ n 1))"]


def test_ast_generated_smt_counterexample_is_anchored_without_replay():
    result = run_pipeline(
        "def f(n):\n    x = n + 1\n    return x\n",
        baseline="AST_ANCHORED",
        anchored_specifications=[
            {
                "line": 2,
                "formula": "(>= n 0)",
                "variables": ["n"],
                "rationale": "A deliberately false inferred property.",
            }
        ],
        do_replay=True,
    )

    claim = result.claim_results[0]
    assert claim.status.value == "COUNTEREXAMPLE"
    assert claim.anchor.has_grounding is True
    assert claim.anchor.source_lines == [2]
    assert claim.counterexample is not None
    assert claim.counterexample.replay_verdict is None


def test_ast_satlm_target_rejects_invalid_line_and_formula():
    result = run_pipeline(
        "def f(n):\n    x = n + 1\n    return x\n",
        baseline="AST_ANCHORED",
        anchored_specifications=[
            {"line": 42, "formula": "(>= n 0)", "variables": ["n"]},
            {"line": 2, "formula": "(= (* n n) 0)", "variables": ["n"]},
        ],
        do_replay=False,
    )

    assert result.n_unsupported == 2
    assert all(not claim.anchor.has_grounding for claim in result.claim_results)


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


def test_unanchored_subset_rejections_are_structured_results():
    unsupported = run_pipeline(
        "def f(x):\n    return [x]\n",
        baseline="UNANCHORED",
        do_replay=False,
    )
    syntax_error = run_pipeline(
        "def f(x):\n    if (x > 0 {\n        return x\n",
        baseline="UNANCHORED",
        do_replay=False,
    )
    anchored = run_pipeline(
        "def f(x):\n    return [x]\n",
        baseline="AST_ANCHORED",
        do_replay=False,
    )

    assert unsupported.rejected_by_subset is True
    assert unsupported.n_claims == 1
    assert unsupported.n_unsupported == 1
    assert unsupported.claim_results[0].step == 1
    assert unsupported.claim_results[0].anchor.node_ids == []
    assert unsupported.claim_results[0].anchor.source_lines == []
    assert syntax_error.n_translation_error == 1
    assert syntax_error.claim_results[0].status.value == "TRANSLATION_ERROR"
    # AST_ANCHORED retains its previous empty-result subset rejection behavior.
    assert anchored.rejected_by_subset is True
    assert anchored.n_claims == 0


def test_llm_claims_are_checked_against_the_arithmetic_subset():
    source = "def f(n):\n    x = n + 1\n    return x\n"
    anchored = run_pipeline(
        source,
        baseline="AST_ANCHORED",
        suggested_claims=[{"line": 2, "expression": "n * n >= 0"}],
        do_replay=False,
    )
    simple = run_pipeline(
        source,
        baseline="UNANCHORED",
        simple_claims=[
            {"expression": _op("ge", _op("mul", _var("n"), _var("n")), _int(0))}
        ],
        do_replay=False,
    )

    assert anchored.claim_results[0].status.value == "UNSUPPORTED"
    assert simple.claim_results[0].status.value == "UNSUPPORTED"


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
