"""Standalone test runner for Phase 1/2/3 extensions (no pytest required)."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Import the test module
import importlib.util
spec = importlib.util.spec_from_file_location(
    "test_extensions", ROOT / "tests" / "test_extensions.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


TESTS = [
    ("replay_captures_failing_state_when_capture_true", mod.test_replay_captures_failing_state_when_capture_true),
    ("replay_no_state_when_capture_false", mod.test_replay_no_state_when_capture_false),
    ("counterexample_info_accepts_legacy_fields", mod.test_counterexample_info_accepts_legacy_fields),
    ("counterexample_info_with_feedback_fields", mod.test_counterexample_info_with_feedback_fields),
    ("smt2_parse_simple_inequality", mod.test_smt2_parse_simple_inequality),
    ("smt2_parse_arithmetic", mod.test_smt2_parse_arithmetic),
    ("smt2_parse_nested_arithmetic", mod.test_smt2_parse_nested_arithmetic),
    ("smt2_parse_returns_none_for_garbage", mod.test_smt2_parse_returns_none_for_garbage),
    ("fallback_generates_relational_candidates", mod.test_fallback_generates_relational_candidates),
    ("generator_returns_fallback_candidates_without_llm", mod.test_generator_returns_fallback_candidates_without_llm),
    ("parse_feedback_json_clean", mod.test_parse_feedback_json_clean),
    ("parse_feedback_json_strips_fences", mod.test_parse_feedback_json_strips_fences),
    ("parse_feedback_json_invalid_returns_none", mod.test_parse_feedback_json_invalid_returns_none),
    ("feedback_loop_returns_not_run_when_no_client", mod.test_feedback_loop_returns_not_run_when_no_client),
    ("pipeline_returns_counterexample_with_feedback_fields", mod.test_pipeline_returns_counterexample_with_feedback_fields),
    ("pipeline_verifies_trivial_true_claim", mod.test_pipeline_verifies_trivial_true_claim),
    ("pipeline_preserves_phase0_behavior_on_loop_timeout", mod.test_pipeline_preserves_phase0_behavior_on_loop_timeout),
]


def main() -> int:
    passed = 0
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            traceback.print_exc()
            failed += 1
    print()
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
