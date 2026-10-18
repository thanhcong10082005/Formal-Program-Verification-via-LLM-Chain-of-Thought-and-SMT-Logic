"""
replay_curated.py - Replay curated dataset through Proposed pipeline.

Different from replay_proposed.py:
- Reads from prototype/dataset_curated/programs/ (not results/AST_ANCHORED/)
- Runs full pipeline (no pre-extracted claims)
- Outputs to results/CURATED/
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from astverifier.feedback_loop import RetrospectiveFeedbackLoop
from astverifier.invariant import LoopInvariantGenerator
from astverifier.llm import _get_api_key, _make_client
from astverifier.pipeline import run_pipeline

CURATED_DIR = PROJECT_ROOT / "dataset_curated" / "programs"
OUT_DIR = PROJECT_ROOT / "results" / "CURATED"

def collect_programs() -> list[Path]:
    files = []
    for sub in CURATED_DIR.rglob("*.py"):
        files.append(sub)
    return sorted(files)

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = collect_programs()
    print(f"Running {len(files)} CURATED programs through Proposed pipeline...")

    # Try to enable LLM
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

    n_ok = n_fail = 0
    t0 = time.perf_counter()
    for p in files:
        program_id = p.stem
        try:
            source = p.read_text(encoding="utf-8")
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  read {p.name}: {exc}")
            continue
        try:
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=str(p),
                baseline="AST_ANCHORED",
                do_replay=True,
                use_llm=False,
                enable_feedback=use_llm,
                enable_invariants=use_llm,
            )
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  pipeline {program_id}: {exc}")
            continue
        out_path = OUT_DIR / f"{program_id}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(res.model_dump(), fh, indent=2, ensure_ascii=False)
        n_ok += 1
        marker = ""
        for cr in res.claim_results:
            ce = cr.counterexample
            if ce and ce.feedback_resolution and ce.feedback_resolution != "not_run":
                marker += f" FB:{cr.claim_id[:18]}={ce.feedback_resolution}"
        print(f"  OK  {program_id[:50]:<50} V={res.n_verified:>2} CE={res.n_counterexample:>2} UT={res.n_unknown_timeout:>2}{marker}")

    elapsed = time.perf_counter() - t0
    print(f"\nDone in {elapsed:.1f}s: {n_ok} ok, {n_fail} fail / {len(files)} total")
    return 0

if __name__ == "__main__":
    sys.exit(main())
