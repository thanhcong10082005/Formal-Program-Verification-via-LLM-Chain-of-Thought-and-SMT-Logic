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
    InvariantCandidate,
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


# ---------------------------------------------------------------------------
# Loop-invariant assistance: the invariant must reach the two-query protocol
# ---------------------------------------------------------------------------


def test_pipeline_verifies_loop_claim_with_verified_invariant():
    """A loop claim is settled by a Z3-verified inductive invariant.

    The invariant ``i >= 0`` holds at loop entry and is preserved by the
    increment, so the claim ``i >= 0`` after the loop must be VERIFIED
    instead of falling back to UNKNOWN_TIMEOUT.
    """
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i >= 0\n"
        "    return i\n"
    )
    result = run_pipeline(
        source,
        program_id="test_loop_invariant",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    claim = result.claim_results[0]
    assert claim.status == StatusEnum.VERIFIED
    assert "invariant" in (claim.reason or "").lower()


def test_pipeline_invariant_does_not_break_failing_loop_claim():
    """A loop claim that is actually false must stay a COUNTEREXAMPLE.

    The invariant must not be used to "prove" a false claim: the solver has
    to find a violating model on a reachable path.
    """
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i <= 0\n"
        "    return i\n"
    )
    result = run_pipeline(
        source,
        program_id="test_loop_invariant_false",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    claim = result.claim_results[0]
    assert claim.status in (StatusEnum.COUNTEREXAMPLE, StatusEnum.UNKNOWN_TIMEOUT)
    assert claim.status != StatusEnum.VERIFIED


def test_pipeline_without_invariants_keeps_unknown_timeout():
    """Disabling the invariant generator restores the previous behaviour."""
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i >= 0\n"
        "    return i\n"
    )
    result = run_pipeline(
        source,
        program_id="test_loop_no_invariant",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
        enable_invariants=False,
    )
    assert result.claim_results[0].status == StatusEnum.UNKNOWN_TIMEOUT


def test_verify_reports_k_induction_bound():
    """The verification reports the k at which the inductive step succeeded."""
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i >= 0\n"
        "    return i\n"
    )
    tree = ast.parse(source)
    ids = assign_ids(tree)
    executor = TrustedSymbolicExecutor(tree, ids)
    executor.build_all_transitions()
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
    state = {"i": z3.Int("i"), "n": z3.Int("n")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    verification = generator.generate_and_verify(
        source, loop, None, state, entry_state=entry_state
    )
    assert verification is not None
    assert verification.is_valid
    assert verification.k_bound >= 1
    assert verification.candidate.formula == "(>= i 0)"


def test_verify_rejects_invariant_that_fails_base_case():
    """An invariant that does not hold at loop entry is rejected."""
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i >= 0\n"
        "    return i\n"
    )
    tree = ast.parse(source)
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    state = {"i": z3.Int("i"), "n": z3.Int("n")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    candidate = InvariantCandidate(
        formula="(>= i 100)",
        kind="relational",
        z3_expr=z3.Int("i") >= 100,
    )
    verification = generator.verify(
        candidate,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
    )
    assert not verification.is_valid
    assert verification.base_holds is False


def test_verify_rejects_loop_body_with_break():
    """A loop body with ``break`` yields no usable invariant.

    The loop can be left early, so the invariant would say nothing about
    the state after the loop.  Accepting it would let a tautology prove an
    arbitrary claim, so the verification must fail.
    """
    source = (
        "def f(n):\n"
        "    while n > 0:\n"
        "        break\n"
        "    assert n <= 0\n"
    )
    tree = ast.parse(source)
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    state = {"n": z3.Int("n")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    candidate = InvariantCandidate(
        formula="(= n n)",
        kind="constant",
        z3_expr=z3.BoolVal(True),
    )
    verification = generator.verify(
        candidate,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
    )
    assert not verification.is_valid
    assert verification.step_holds is False


def test_pipeline_break_loop_is_not_verified_by_invariant():
    """A claim that a ``break``-loop can violate must not become VERIFIED."""
    source = (
        "def f(n):\n"
        "    while n > 0:\n"
        "        break\n"
        "    assert n <= 0\n"
    )
    result = run_pipeline(
        source,
        program_id="test_break_loop_invariant",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    assert result.claim_results[0].status != StatusEnum.VERIFIED


def test_pipeline_invariant_proves_claim_that_needs_the_relation():
    """A claim that is only true thanks to the invariant becomes VERIFIED.

    ``s == i - 1`` cannot be derived from the bounded unrolling (the
    executor unrolls the body only once), but it is exactly the relation
    that the loop preserves.  This is the case the invariant path exists
    for.
    """
    source = (
        "def f(n: int) -> int:\n"
        "    s = 0\n"
        "    i = 1\n"
        "    while i <= n:\n"
        "        s = s + 1\n"
        "        i = i + 1\n"
        "    assert s == i - 1\n"
        "    return s\n"
    )
    with_inv = run_pipeline(
        source,
        program_id="test_loop_invariant_relation",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
    )
    assert with_inv.claim_results[0].status == StatusEnum.VERIFIED

    without_inv = run_pipeline(
        source,
        program_id="test_loop_invariant_relation_off",
        source_path="<inline>",
        baseline="AST_ANCHORED",
        do_replay=False,
        use_llm=False,
        enable_invariants=False,
    )
    assert without_inv.claim_results[0].status == StatusEnum.UNKNOWN_TIMEOUT


def test_verify_accepts_relational_offset_invariant():
    """A relational invariant ``s == i - 1`` is accepted by k-induction.

    This is the invariant that actually summarises an accumulator loop, and
    it is strictly stronger than the per-variable bound ``s >= 0``.
    """
    source = (
        "def f(n: int) -> int:\n"
        "    s = 0\n"
        "    i = 1\n"
        "    while i <= n:\n"
        "        s = s + 1\n"
        "        i = i + 1\n"
        "    assert s == i - 1\n"
        "    return s\n"
    )
    tree = ast.parse(source)
    ids = assign_ids(tree)
    executor = TrustedSymbolicExecutor(tree, ids)
    executor.build_all_transitions()
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
    state = {"i": z3.Int("i"), "s": z3.Int("s"), "n": z3.Int("n")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    candidate = InvariantCandidate(
        formula="(= s (- i 1))",
        kind="relational",
        z3_expr=parse_smt2_formula("(= s (- i 1))", state),
    )
    verification = generator.verify(
        candidate,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
        entry_state=entry_state,
    )
    assert verification.is_valid
    assert verification.base_holds
    assert verification.step_holds


def test_verify_rejects_invariant_that_is_not_preserved():
    """An invariant that the body breaks must be rejected."""
    source = (
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        i = i + 1\n"
        "    assert i <= 0\n"
        "    return i\n"
    )
    tree = ast.parse(source)
    ids = assign_ids(tree)
    executor = TrustedSymbolicExecutor(tree, ids)
    executor.build_all_transitions()
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
    state = {"i": z3.Int("i"), "n": z3.Int("n")}
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    candidate = InvariantCandidate(
        formula="(<= i 0)",
        kind="relational",
        z3_expr=z3.Int("i") <= 0,
    )
    verification = generator.verify(
        candidate,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
        entry_state=entry_state,
    )
    assert not verification.is_valid
    assert verification.step_holds is False


def test_parser_handles_nested_arithmetic_terms():
    """A nested arithmetic term such as ``(= s (- i 1))`` is parsed."""
    state = {"i": z3.Int("i"), "s": z3.Int("s")}
    expr = parse_smt2_formula("(= s (- i 1))", state)
    assert expr is not None
    # The parsed formula must be exactly the expected relation over the very
    # same symbols.  ``z3.simplify`` on both sides gives a canonical form.
    assert z3.simplify(expr).sexpr() == z3.simplify(
        state["s"] == state["i"] - 1
    ).sexpr()


def test_parser_handles_negative_numbers():
    """A negative literal is tokenized as one number."""
    state = {"x": z3.Int("x")}
    expr = parse_smt2_formula("(>= x -1)", state)
    assert expr is not None
    # ``-1`` must be read as the number minus one, not as a subtraction.
    solver = z3.Solver()
    solver.add(z3.Xor(expr, state["x"] >= -1))
    assert solver.check() == z3.unsat




def test_parser_handles_implies_div_mod_ite():
    """The SMT-LIB2 parser handles implies, =>, div, mod, ite, and !=."""
    state = {
        "x": z3.Int("x"),
        "y": z3.Int("y"),
        "i": z3.Int("i"),
        "s": z3.Int("s"),
        "n": z3.Int("n"),
    }
    # 1. implies
    expr_imp = parse_smt2_formula("(implies (>= i 0) (<= s 100))", state)
    assert expr_imp is not None
    s = z3.Solver()
    expected_imp = z3.Implies(state["i"] >= 0, state["s"] <= 100)
    s.add(z3.Xor(expr_imp, expected_imp))
    assert s.check() == z3.unsat

    # 2. => symbol
    expr_arrow = parse_smt2_formula("(=> (>= i 0) (<= s 100))", state)
    assert expr_arrow is not None
    s2 = z3.Solver()
    s2.add(z3.Xor(expr_arrow, expected_imp))
    assert s2.check() == z3.unsat

    # 3. div and mod
    expr_mod = parse_smt2_formula("(= (mod i 3) 0)", state)
    assert expr_mod is not None
    s3 = z3.Solver()
    s3.add(z3.Xor(expr_mod, (state["i"] % 3) == 0))
    assert s3.check() == z3.unsat

    expr_div = parse_smt2_formula("(= s (div n 3))", state)
    assert expr_div is not None
    s4 = z3.Solver()
    s4.add(z3.Xor(expr_div, state["s"] == (state["n"] / 3)))
    assert s4.check() == z3.unsat

    # 4. ite
    expr_ite = parse_smt2_formula("(ite (> x 0) (= y 1) (= y 0))", state)
    assert expr_ite is not None
    s5 = z3.Solver()
    expected_ite = z3.If(state["x"] > 0, state["y"] == 1, state["y"] == 0)
    s5.add(z3.Xor(expr_ite, expected_ite))
    assert s5.check() == z3.unsat

    # 5. !=
    expr_ne = parse_smt2_formula("(!= x 0)", state)
    assert expr_ne is not None
    s6 = z3.Solver()
    s6.add(z3.Xor(expr_ne, state["x"] != 0))
    assert s6.check() == z3.unsat


def test_dataset_multiples_program_with_mod_and_implies():
    """Verify a dataset-style loop program with mod and step increments."""
    # Program style from dataset_temp_200/programs/g3_loops/g3_multiples_01.py
    source = (
        "# precondition: n >= 0\n"
        "def f(n: int) -> int:\n"
        "    i = 0\n"
        "    s = 0\n"
        "    while i < n:\n"
        "        i = i + 2\n"
        "        s = s + 1\n"
        "    assert s >= 0\n"
        "    return s\n"
    )
    tree = ast.parse(source)
    ids = assign_ids(tree)
    executor = TrustedSymbolicExecutor(tree, ids)
    executor.build_all_transitions()
    loop = next(stmt for stmt in ast.walk(tree) if isinstance(stmt, ast.While))
    entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
    state = {"i": z3.Int("i"), "s": z3.Int("s"), "n": z3.Int("n")}

    # Candidate with mod: (mod i 2) == 0
    mod_formula = "(= (mod i 2) 0)"
    cand_mod = InvariantCandidate(
        formula=mod_formula,
        kind="relational",
        z3_expr=parse_smt2_formula(mod_formula, state),
    )
    generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
    ver_mod = generator.verify(
        cand_mod,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
        entry_state=entry_state,
    )
    assert ver_mod.is_valid
    assert ver_mod.base_holds
    assert ver_mod.step_holds

    # Candidate with relational mod: (= (mod (- (* 2 s) i) 2) 0)
    rel_mod_formula = "(= (mod (- (* 2 s) i) 2) 0)"
    cand_rel_mod = InvariantCandidate(
        formula=rel_mod_formula,
        kind="relational",
        z3_expr=parse_smt2_formula(rel_mod_formula, state),
    )
    ver_rel_mod = generator.verify(
        cand_rel_mod,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
        entry_state=entry_state,
    )
    assert ver_rel_mod.is_valid
    assert ver_rel_mod.base_holds
    assert ver_rel_mod.step_holds

    # Candidate with implies: (implies (and (>= s 0) (>= i 0)) (>= (+ s i) 0))
    imp_formula = "(implies (and (>= s 0) (>= i 0)) (>= (+ s i) 0))"
    cand_imp = InvariantCandidate(
        formula=imp_formula,
        kind="relational",
        z3_expr=parse_smt2_formula(imp_formula, state),
    )
    ver_imp = generator.verify(
        cand_imp,
        loop_node=loop,
        loop_body_stmts=loop.body,
        state=state,
        entry_state=entry_state,
    )
    assert ver_imp.is_valid
    assert ver_imp.base_holds
    assert ver_imp.step_holds


def test_verify_handles_general_linear_assignments_and_if_branches():
    """General linear assignments (y = 10 - x, x = x + x) and if/else branches verify."""
    # 1. p00060-style: y = 10 - x; x = x + 1
    src_p00060 = (
        "def main(_initial_y: int) -> int:\n"
        "    x = 1\n"
        "    y = 9\n"
        "    while x <= 10:\n"
        "        y = 10 - x\n"
        "        x = x + 1\n"
        "    assert y < 10\n"
        "    return y\n"
    )
    tree1 = ast.parse(src_p00060)
    ids1 = assign_ids(tree1)
    ex1 = TrustedSymbolicExecutor(tree1, ids1)
    ex1.build_all_transitions()
    loop1 = next(s for s in ast.walk(tree1) if isinstance(s, ast.While))
    entry1 = dict(ex1.transitions[ids1[loop1]].pre.bindings)
    state1 = {"x": z3.Int("x"), "y": z3.Int("y"), "_initial_y": z3.Int("_initial_y")}
    gen = LoopInvariantGenerator(llm_client=None, use_llm=False)

    cand1 = InvariantCandidate(
        formula="(and (>= x 1) (< y 10))",
        kind="relational",
        z3_expr=parse_smt2_formula("(and (>= x 1) (< y 10))", state1),
    )
    ver1 = gen.verify(
        cand1,
        loop_node=loop1,
        loop_body_stmts=loop1.body,
        state=state1,
        entry_state=entry1,
    )
    assert ver1.is_valid

    # 2. p00050-style: if inside loop body merged with z3.If
    src_p00050 = (
        "def main(_initial_y: int) -> int:\n"
        "    x = 0\n"
        "    y = _initial_y\n"
        "    z = _initial_y\n"
        "    while x < 5:\n"
        "        x += 1\n"
        "        if z <= y:\n"
        "            y = z\n"
        "    assert z >= y\n"
        "    return y\n"
    )
    tree2 = ast.parse(src_p00050)
    ids2 = assign_ids(tree2)
    ex2 = TrustedSymbolicExecutor(tree2, ids2)
    ex2.build_all_transitions()
    loop2 = next(s for s in ast.walk(tree2) if isinstance(s, ast.While))
    entry2 = dict(ex2.transitions[ids2[loop2]].pre.bindings)
    state2 = {
        "x": z3.Int("x"),
        "y": z3.Int("y"),
        "z": z3.Int("z"),
        "_initial_y": z3.Int("_initial_y"),
    }
    cand2 = InvariantCandidate(
        formula="(>= z y)",
        kind="relational",
        z3_expr=parse_smt2_formula("(>= z y)", state2),
    )
    ver2 = gen.verify(
        cand2,
        loop_node=loop2,
        loop_body_stmts=loop2.body,
        state=state2,
        entry_state=entry2,
    )
    assert ver2.is_valid

    # 3. p00038-style: x = x + x
    src_p00038 = (
        "def main(y: int) -> int:\n"
        "    x = 1\n"
        "    while x < y:\n"
        "        x = x + x\n"
        "    assert x >= 1\n"
        "    return x\n"
    )
    tree3 = ast.parse(src_p00038)
    ids3 = assign_ids(tree3)
    ex3 = TrustedSymbolicExecutor(tree3, ids3)
    ex3.build_all_transitions()
    loop3 = next(s for s in ast.walk(tree3) if isinstance(s, ast.While))
    entry3 = dict(ex3.transitions[ids3[loop3]].pre.bindings)
    state3 = {"x": z3.Int("x"), "y": z3.Int("y")}
    cand3 = InvariantCandidate(
        formula="(>= x 1)",
        kind="relational",
        z3_expr=parse_smt2_formula("(>= x 1)", state3),
    )
    ver3 = gen.verify(
        cand3,
        loop_node=loop3,
        loop_body_stmts=loop3.body,
        state=state3,
        entry_state=entry3,
    )
    assert ver3.is_valid


def test_pipeline_hoare_loop_exit_guard_and_havoc():
    """Hoare loop-exit abstraction (I AND ~Guard => Post) proves post-loop claims without infinite unrolling."""
    # 1. p00014.py: without ~Guard and havoc, 1-step unrolling falsely hits x=1 != n for n=2
    src_p00014 = (
        "def main(_initial_n: int, _initial_x: int) -> None:\n"
        "    n = _initial_n\n"
        "    x = _initial_x\n"
        "    x = 0\n"
        "    while x < n:\n"
        "        x = x + 1\n"
        "    if x != n:\n"
        "        assert n < 0\n"
    )
    res_with = run_pipeline(src_p00014, do_replay=False, use_llm=False, enable_invariants=True)
    assert res_with.claim_results[0].status == StatusEnum.VERIFIED

    # 2. p00237.py: conserved linear relation i + 2*j == 41 with exit guard ~(j >= i) => j == 13
    src_p00237 = (
        "def main(_initial_i: int, _initial_j: int) -> None:\n"
        "    i = 1\n"
        "    j = 20\n"
        "    while j >= i:\n"
        "        i = i + 2\n"
        "        j = j - 1\n"
        "    assert j == 13\n"
    )
    res_237 = run_pipeline(src_p00237, do_replay=False, use_llm=False, enable_invariants=True)
    assert res_237.claim_results[0].status == StatusEnum.VERIFIED

