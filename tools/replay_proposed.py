"""
replay_proposed.py - Replay Phase 0 results qua Proposed Method pipeline.

Cách làm:
  1. Đọc từng file JSON trong results/AST_ANCHORED/
  2. Trích LLM claims (id bắt đầu "llm_claim_") + assert claims
  3. Gọi lại run_pipeline với cùng suggested_claims + enable_feedback=True
  4. Lưu kết quả vào results/PROPOSED/

Lợi ích:
  - Đảm bảo so sánh công bằng (cùng claim input)
  - Không cần gọi Gemini thêm lần
  - Feedback loop vẫn chạy được với client None (offline: resolution="not_run")
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import List

logging.basicConfig(level=logging.WARNING)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from astverifier.classify import ProgramResult
from astverifier.feedback_loop import RetrospectiveFeedbackLoop
from astverifier.invariant import LoopInvariantGenerator
from astverifier.llm import _get_api_key, _make_client
from astverifier.pipeline import run_pipeline

PHASE0_DIR = PROJECT_ROOT / "results" / "AST_ANCHORED"
PROPOSED_DIR = PROJECT_ROOT / "results" / "PROPOSED"


def _extract_suggested_claims(phase0: dict) -> list[dict]:
    """Pull llm_claim_* entries out of a Phase 0 result JSON."""
    out = []
    for cr in phase0.get("claim_results", []):
        cid = cr.get("claim_id", "")
        if cid.startswith("llm_claim_") and cr.get("status") not in (
            "UNSUPPORTED", "TRANSLATION_ERROR"
        ):
            # Reconstruct the (line, expression) pair
            text = cr.get("claim_text", "")
            # parse "L<n>" from id
            try:
                tail = cid.rsplit("_L", 1)[-1]
                line = int(tail)
            except Exception:
                continue
            if text:
                out.append({"line": line, "expression": text})
    return out


def main() -> int:
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    phase0_files = sorted(PHASE0_DIR.glob("*.json"))
    print(f"Replaying {len(phase0_files)} Phase 0 results through Proposed pipeline...")
    # Try to enable LLM-driven feedback + invariants
    key = _get_api_key()
    feedback_loop = None
    invariant_gen = None
    use_llm = False
    if key:
        try:
            client = _make_client(key)
            feedback_loop = RetrospectiveFeedbackLoop(llm_client=client)
            invariant_gen = LoopInvariantGenerator(llm_client=client, use_llm=True)
            use_llm = True
            print("  Gemini API key found - feedback + invariants LIVE")
        except Exception as exc:
            print(f"  Failed to init Gemini: {exc}")
    else:
        print("  No GEMINI_API_KEY - running offline (feedback disabled)")
    n_ok = 0
    n_fail = 0
    t0 = time.perf_counter()
    for p in phase0_files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  load {p.name}: {exc}")
            continue
        program_id = data.get("program_id", p.stem)
        source = data.get("source_text", "")
        if not source:
            n_fail += 1
            print(f"  FAIL  no source for {p.name}")
            continue
        suggested = _extract_suggested_claims(data)
        try:
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=data.get("source_path", "<unknown>"),
                baseline="AST_ANCHORED",
                do_replay=True,
                use_llm=False,  # claims are pre-injected; feedback loop is independent
                suggested_claims=suggested,
                enable_feedback=use_llm,
                enable_invariants=use_llm,
            )
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  pipeline {program_id}: {exc}")
            continue
        out_path = PROPOSED_DIR / f"{program_id}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(res.model_dump(), fh, indent=2, ensure_ascii=False)
        n_ok += 1
        # Annotate with feedback info
        fb_info = []
        for cr in res.claim_results:
            ce = cr.counterexample
            if ce and ce.feedback_resolution and ce.feedback_resolution != "not_run":
                fb_info.append(
                    f"{cr.claim_id[:18]}={ce.feedback_resolution}"
                )
        marker = " FB:" + ",".join(fb_info) if fb_info else ""
        print(f"  OK  {program_id[:50]:<50} V={res.n_verified:>2} CE={res.n_counterexample:>2} UT={res.n_unknown_timeout:>2}{marker}")
    elapsed = time.perf_counter() - t0
    print()
    print(f"Done in {elapsed:.1f}s: {n_ok} ok, {n_fail} fail / {len(phase0_files)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
