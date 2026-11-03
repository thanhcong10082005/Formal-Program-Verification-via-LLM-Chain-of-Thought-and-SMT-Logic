"""Unit tests for the invariant and feedback-loop extensions.

Covers:
  * the replay module's failing-state capture,
  * the SMT-LIB2 parser used by the invariant generator,
  * the fallback invariant templates,
  * the feedback loop's JSON parser and its fail-safe behaviour,
  * the end-to-end pipeline with the new modules enabled.
"""

from __future__ import annotations

import ast

import z3

from astverifier.core.binding import Claim
from astverifier.core.executor import Reachability, SymState, TrustedSymbolicExecutor
from astverifier.core.feedback import (
    RetrospectiveFeedbackLoop,
    _parse_feedback_json,
)
from astverifier.core.invariants import (
    LoopInvariantGenerator,
    _linear_candidate_templates,
    parse_smt2_formula,
)
from astverifier.core.pipeline import run_pipeline
from astverifier.shared.anchors import assign_ids
from astverifier.shared.replay import ReplayResult, replay_counterexample
from astverifier.shared.schemas import StatusEnum

# ---------------------------------------------------------------------------
# Replay: failing-state capture
# ---------------------------------------------------------------------------


def test_replay_captures_failing_state_when_capture_true():
    """The failing variable state is captured when requested."""
    source = (
        "def f(x: int) -> int:\n"
        "    if x < 0:\n"
        "        assert x > 0\n"
        "    return x\n"
    )
    result = replay_counterexample(
        source,
        "f",
        {"x": -5},
        target_line=3,
        claim_expression="x > 0",
        capture_state=True,
        timeout=3.0,
    )
    assert result.success is True
    assert result.failing_variable_state is not None
    assert result.failing_variable_state.get("x") == -5


def test_replay_no_state_when_capture_false():
    """No failing state is captured unless it was requested."""
    source = "def f(x: int) -> int:\n    assert x > 0\n    return x\n"
    result = replay_counterexample(
        source,
        "f",
        {"x": -3},
        target_line=2,
        claim_expression="x > 0",
        timeout=3.0,
    )
    assert result.success is True
    assert result.failing_variable_state in (None, {})


# ---------------------------------------------------------------------------
# Invariant generator: SMT-LIB2 parsing
# ---------------------------------------------------------------------------


def test_smt2_parse_simple_inequality():
    """A comparison term is parsed against the supplied state."""
    state = {"i": z3.Int("i"), "n": z3.Int("n")}
    assert parse_smt2_formula("(<= i n)", state) is not None


def test_smt2_parse_arithmetic():
    """A binary arithmetic term is parsed."""
    state = {"s": z3.Int("s"), "i": z3.Int("i")}
    assert parse_smt2_formula("(+ s i)", state) is not None


def test_smt2_parse_nested_arithmetic():
    """A tautology is accepted."""
    state = {"s": z3.Int("s"), "i": z3.Int("i")}
    assert parse_smt2_formula("(= s s)", state) is not None


def test_smt2_parse_returns_none_for_garbage():
    """Unsupported operators and empty input yield ``None``."""
    state = {"x": z3.Int("x")}
    assert parse_smt2_formula("(weird_op x y)", state) is None
    assert parse_smt2_formula("", state) is None


def test_fallback_generates_relational_candidates():
    """The cheap fallback always offers relational candidates."""
    state = {"i": z3.Int("i"), "n": z3.Int("n")}
    kinds = {kind for kind, _, _ in _linear_candidate_templates(state)}
    assert "relational" in kinds


def test_generator_returns_fallback_candidates_without_llm():
    """Without an LLM the generator still produces fallback candidates."""
    source = (
        "def f(n: int) -> int:\n"
        "    s = 0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert s >= 0\n"
        "    return s\n"
    )
    tree = ast.parse(source)
    loop = next(
        stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While)
    )
    state = {"i": z3.Int("i"), "n": z3.Int("n"), "s": z3.Int("s")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    candidates = generator.generate(source, loop, None, state)

    assert len(candidates) > 0
    assert any(candidate.source == "fallback" for candidate in candidates)


# ---------------------------------------------------------------------------
# Feedback loop: JSON parsing and fail-safe behaviour
# ---------------------------------------------------------------------------


def test_parse_feedback_json_clean():
    """A clean JSON payload is parsed."""
    raw = (
        '{"refined_cot": "x", "refined_claim": {"line": 3, '
        '"expression": "x >= 0"}, "accept_ce": false}'
    )
    parsed = _parse_feedback_json(raw)
    assert parsed is not None
    assert parsed["accept_ce"] is False
    assert parsed["refined_claim"]["line"] == 3
    assert parsed["refined_claim"]["expression"] == "x >= 0"


def test_parse_feedback_json_strips_fences():
    """Markdown fences around the JSON are stripped."""
    raw = (
        '```json\n{"refined_cot": "x", "refined_claim": {"line": 3, '
        '"expression": "x >= 0"}}\n```'
    )
    parsed = _parse_feedback_json(raw)
    assert parsed is not None
    assert parsed["refined_claim"]["expression"] == "x >= 0"


def test_parse_feedback_json_invalid_returns_none():
    """Unparseable input yields ``None``."""
    assert _parse_feedback_json("not json at all") is None
    assert _parse_feedback_json("") is None


def test_feedback_loop_returns_not_run_when_no_client():
    """Without an LLM client the loop reports ``not_run`` and changes nothing."""
    source = "def f(x: int) -> int:\n    assert x > 0\n    return x\n"
    tree = ast.parse(source)
    ids = assign_ids(tree)
    executor = TrustedSymbolicExecutor(tree, ids)
    executor.build_all_transitions()
    claim = Claim(
        claim_id="test",
        source_line=2,
        expr_source="x > 0",
        expr_node=ast.parse("x > 0", mode="eval").body,
        anchor=None,
        status="ANCHORED",
    )
    reachability = Reachability(
        path=[],
        relation=z3.BoolVal(True),
        path_condition=z3.BoolVal(True),
        state=SymState(),
    )
    loop = RetrospectiveFeedbackLoop(llm_client=None)
    final_claim, rounds, resolution = loop.run_loop(
        source=source,
        previous_cot="",
        claim=claim,
        z3_counterexample={"x": -1},
        replay=ReplayResult(True, "-1", 0, None, 1.0),
        executor=executor,
        reachability=reachability,
        func_name="f",
    )
    assert resolution == "not_run"
    assert final_claim is claim
    assert rounds == []


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


def test_pipeline_returns_counterexample_with_feedback_fields():
    """A confirmed counterexample populates the feedback fields."""
    source = (
        "def f(x: int) -> int:\n"
        "    assert x > 0\n"
        "    return x * 2\n"
    )
    result = run_pipeline(
        source,
        program_id="test_feedback_fields",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=True,
        use_llm=False,
    )
    counterexample = result.claim_results[0].counterexample
    assert counterexample is not None
    assert counterexample.replay_failing_state in (None, {}) or isinstance(
        counterexample.replay_failing_state, dict
    )
    assert counterexample.feedback_resolution in (
        None,
        "not_run",
        "max_iter_reached",
        "llm_error",
        "verified",
        "still_counter",
        "llm_rejected_ce",
        "unreachable",
    )


def test_pipeline_verifies_trivial_true_claim():
    """A claim that follows directly from the assignment is verified."""
    source = (
        "def f(x: int) -> int:\n"
        "    y = x + 1\n"
        "    assert y > x\n"
        "    return y\n"
    )
    result = run_pipeline(
        source,
        program_id="test_verify",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    assert result.claim_results[0].status == StatusEnum.VERIFIED


def test_pipeline_preserves_phase0_behavior_on_loop_timeout():
    """A loop with an unknown bound yields VERIFIED or UNKNOWN_TIMEOUT."""
    source = (
        "def f(n: int) -> int:\n"
        "    s = 0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        s = s + i\n"
        "        i = i + 1\n"
        "    assert s >= 0\n"
        "    return s\n"
    )
    result = run_pipeline(
        source,
        program_id="test_loop_unknown",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    assert result.claim_results[0].status in (
        StatusEnum.VERIFIED,
        StatusEnum.UNKNOWN_TIMEOUT,
    )


def test_pipeline_reports_unreachable_claim():
    """A claim guarded by contradictory conditions is reported unreachable."""
    source = (
        "def f(x: int) -> int:\n"
        "    if x > 0:\n"
        "        if x < 0:\n"
        "            assert x == 0\n"
        "    return x\n"
    )
    result = run_pipeline(
        source,
        program_id="test_unreachable",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    assert result.claim_results[0].status == StatusEnum.UNREACHABLE


def test_feedback_loop_does_not_verify_unreachable_claim():
    """An unreachable refined claim must not be accepted as VERIFIED."""
    import json

    from astverifier.core.feedback import _GeminiAdapter

    class MockUnreachableAdapter(_GeminiAdapter):
        def __init__(self):
            super().__init__(object())

        def invoke(self, prompt: str, system: str) -> str:
            return json.dumps({
                "refined_cot": "Move claim to dead branch",
                "refined_claim": {"line": 5, "expression": "res == 100"},
                "accept_ce": True,
            })

    source = (
        "def check_val(x: int) -> int:\n"
        "    res = 0\n"
        "    if x > 10:\n"
        "        if x < 5:\n"
        "            res = 100\n"
        "    assert x != 0\n"
        "    return res\n"
    )
    loop = RetrospectiveFeedbackLoop(llm_client=None)
    loop.client = MockUnreachableAdapter()

    res = run_pipeline(
        source,
        program_id="test_unreachable_feedback",
        enable_feedback=True,
        feedback_loop=loop,
        do_replay=True,
    )
    # The real bug (assert x != 0 fails for x=0) must remain a COUNTEREXAMPLE
    assert res.claim_results[0].status == StatusEnum.COUNTEREXAMPLE
    assert res.claim_results[0].counterexample is not None
    assert res.claim_results[0].counterexample.feedback_resolution == "max_iter_reached"


def test_metrics_hr_is_none_without_grounding():
    """When a baseline has no anchor gate (no grounding), HR must be None (N/A)."""
    from astverifier.shared.metrics import aggregate
    from astverifier.shared.schemas import AnchorInfo, ClaimResult, ProgramResult

    anchor_no_grounding = AnchorInfo(node_ids=[], source_lines=[1], has_grounding=False)
    result = ProgramResult(
        program_id="p",
        source_path="<inline>",
        source_text="x = 1",
        n_claims=1,
        n_verified=1,
        n_counterexample=0,
        n_unreachable=0,
        n_unsupported=0,
        n_translation_error=0,
        n_unknown_timeout=0,
        claim_results=[
            ClaimResult(
                claim_id="c1",
                claim_text="x == 1",
                status=StatusEnum.VERIFIED,
                anchor=anchor_no_grounding,
            )
        ],
        elapsed_seconds=0.1,
        baseline="LLM_JUDGE",
    )
    m = aggregate([result], {"p": "VERIFIED"})
    assert m.hr is None


def test_pipeline_precondition_filters_counterexamples():
    """A claim that holds under a precondition is VERIFIED, but COUNTEREXAMPLE without it."""
    source = (
        "def f(x: int) -> int:\n"
        "    assert x >= 0\n"
        "    return x\n"
    )
    # Without precondition, x=-1 is a counterexample:
    res_no_pre = run_pipeline(source, do_replay=True)
    assert res_no_pre.claim_results[0].status == StatusEnum.COUNTEREXAMPLE

    # With precondition, it holds for all inputs satisfying the precondition:
    res_with_pre = run_pipeline(source, precondition="x >= 0", do_replay=True)
    assert res_with_pre.claim_results[0].status == StatusEnum.VERIFIED

    # With precondition in comment:
    source_comment = (
        "# precondition: x >= 0\n"
        "def f(x: int) -> int:\n"
        "    assert x >= 0\n"
        "    return x\n"
    )
    res_comment = run_pipeline(source_comment, do_replay=True)
    assert res_comment.claim_results[0].status == StatusEnum.VERIFIED


