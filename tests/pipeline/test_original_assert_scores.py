"""Regression test for excluding generated claims that duplicate a source assert."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from score_original_asserts import score  # noqa: E402


def test_score_matches_source_assert_in_proposed_logs(tmp_path):
    program_id = "sample.py"
    source = "assert False\n"
    label = "COUNTEREXAMPLE"
    (tmp_path / program_id).write_text(source)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "programs": [{"path": program_id, "expected_verdict": label}],
    }))
    anchor = "module@Assert:L1:C0#0"
    (tmp_path / "claim_pool.json").write_text(json.dumps({
        "claims": [{
            "program_id": program_id, "claim_id": "bench_assert", "text": "False",
            "source_line": 1, "ast_anchor": anchor, "provenance": "assert@L1",
        }],
    }))
    ids_path = tmp_path / "program_ids.json"
    ids_path.write_text(json.dumps([program_id]))
    shared = {"claim_text": "False", "anchor": {"node_ids": [anchor]}, "reason": ""}
    (tmp_path / "PROPOSED_LLM.jsonl").write_text(json.dumps({
        "program_id": program_id, "source_text": source, "baseline": "PROPOSED_LLM",
        "total_tokens_in": 0, "total_tokens_out": 0, "elapsed_seconds": 0,
        "claim_results": [
            dict(shared, claim_id="llm_claim_1_L1", status="COUNTEREXAMPLE"),
            dict(shared, claim_id="assert_L1_#0", status="UNKNOWN_TIMEOUT"),
        ],
    }) + "\n")

    proposed = score(tmp_path, ids_path, tmp_path)["PROPOSED_LLM"]
    assert proposed["statuses"] == {"UNKNOWN_TIMEOUT": 1}
    assert proposed["correct"] == 0
    assert proposed["coverage_pct"] == 0
