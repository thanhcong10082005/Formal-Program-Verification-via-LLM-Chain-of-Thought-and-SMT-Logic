"""
run_proposed_benchmark.py - E2E benchmark cho Proposed Method.

Chạy pipeline với các flag mới:
  * --enable-feedback    : bật Retrospective Feedback Loop
  * --enable-invariants  : bật Loop Invariant Generation
  * --use-llm            : gọi Gemini (cần API key)

Kết quả: prototype/results/PROPOSED/*.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.classify import ProgramResult
from astverifier.feedback_loop import RetrospectiveFeedbackLoop
from astverifier.invariant import LoopInvariantGenerator
from astverifier.llm import _get_api_key, _make_client
from astverifier.pipeline import run_pipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = PROJECT_ROOT.parent / "Dataset"
PY_LOOPS_ROOT = DATASET_ROOT / "sv-benchmarks-loops-py"
CRUXEVAL_JSONL = DATASET_ROOT / "cruxeval-main" / "data" / "cruxeval.jsonl"
RESULT_ROOT = PROJECT_ROOT / "results" / "PROPOSED"

GROUND_TRUTH: dict[str, str] = {
    # loop-simple
    "loop-simple/nested_1": "VERIFIED", "loop-simple/nested_1b": "VERIFIED",
    "loop-simple/nested_2": "VERIFIED", "loop-simple/nested_3": "VERIFIED",
    "loop-simple/nested_4": "VERIFIED", "loop-simple/nested_5": "VERIFIED",
    "loop-simple/nested_6": "VERIFIED", "loop-simple/deep-nested": "VERIFIED",
    # loop-invariants
    "loop-invariants/const": "VERIFIED", "loop-invariants/eq1": "VERIFIED",
    "loop-invariants/eq2": "VERIFIED", "loop-invariants/even": "VERIFIED",
    "loop-invariants/odd": "VERIFIED", "loop-invariants/mod4": "VERIFIED",
    "loop-invariants/bin-suffix-5": "VERIFIED",
    "loop-invariants/linear-inequality-inv-a": "VERIFIED",
    "loop-invariants/linear-inequality-inv-b": "VERIFIED",
    "loop-invariants/linear-inequality-inv-c": "VERIFIED",
    # loops-crafted-1
    "loops-crafted-1/iftelse": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de20": "COUNTEREXAMPLE", "loops-crafted-1/in-de31": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de32": "COUNTEREXAMPLE", "loops-crafted-1/in-de41": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de42": "COUNTEREXAMPLE", "loops-crafted-1/in-de51": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de52": "COUNTEREXAMPLE", "loops-crafted-1/in-de61": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de62": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv1": "COUNTEREXAMPLE", "loops-crafted-1/loopv2": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv3": "COUNTEREXAMPLE",
    "loops-crafted-1/mono-crafted_1": "VERIFIED", "loops-crafted-1/mono-crafted_3": "VERIFIED",
    "loops-crafted-1/mono-crafted_6": "VERIFIED", "loops-crafted-1/mono-crafted_7": "VERIFIED",
    "loops-crafted-1/mono-crafted_8": "VERIFIED", "loops-crafted-1/mono-crafted_9": "VERIFIED",
    "loops-crafted-1/mono-crafted_10": "VERIFIED", "loops-crafted-1/mono-crafted_11": "VERIFIED",
    "loops-crafted-1/mono-crafted_12": "VERIFIED", "loops-crafted-1/mono-crafted_13": "VERIFIED",
    "loops-crafted-1/mono-crafted_14": "VERIFIED", "loops-crafted-1/Mono1_1-1": "VERIFIED",
    "loops-crafted-1/Mono1_1-2": "VERIFIED", "loops-crafted-1/Mono3_1": "VERIFIED",
    "loops-crafted-1/Mono4_1": "VERIFIED", "loops-crafted-1/Mono5_1": "VERIFIED",
    "loops-crafted-1/Mono6_1": "VERIFIED", "loops-crafted-1/nested3-1": "VERIFIED",
    "loops-crafted-1/nested3-1_abstracted": "VERIFIED", "loops-crafted-1/nested3-2": "VERIFIED",
    "loops-crafted-1/nested3-2_abstracted": "VERIFIED", "loops-crafted-1/nested5-1": "VERIFIED",
    "loops-crafted-1/nested5-2": "VERIFIED", "loops-crafted-1/nested_delay_nd": "VERIFIED",
    "loops-crafted-1/nested_delay_notd2": "VERIFIED", "loops-crafted-1/net_reset": "VERIFIED",
    "loops-crafted-1/sumt2": "VERIFIED", "loops-crafted-1/sumt3": "VERIFIED",
    "loops-crafted-1/sumt4": "VERIFIED", "loops-crafted-1/sumt5": "VERIFIED",
    "loops-crafted-1/sumt6": "VERIFIED", "loops-crafted-1/sumt7": "VERIFIED",
    "loops-crafted-1/sumt8": "VERIFIED", "loops-crafted-1/sumt9": "VERIFIED",
    "loops-crafted-1/sum_by_3": "VERIFIED", "loops-crafted-1/sum_by_3_abstracted": "VERIFIED",
    "loops-crafted-1/sum_natnum": "VERIFIED", "loops-crafted-1/theatreSquare": "VERIFIED",
    "loops-crafted-1/vnew1": "COUNTEREXAMPLE", "loops-crafted-1/vnew2": "VERIFIED",
    "loops-crafted-1/watermelon": "VERIFIED",
    # cruxeval: skip ground-truth for non-benchmark
}


def collect_source_files() -> list[Path]:
    """Return 94 source files (73 sv-benchmarks + 21 cruxeval)."""
    files: list[Path] = []
    # 1. SV-COMP Python loops
    for p in sorted(PY_LOOPS_ROOT.rglob("*.py")):
        if "/." in str(p) or p.name.startswith("."):
            continue
        files.append(p)
    # 2. CRUXEval: build wrappers from jsonl (limit 21 to match Phase 0)
    if CRUXEVAL_JSONL.exists():
        import tempfile
        with open(CRUXEVAL_JSONL, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()][:21]
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = rec.get("code") or rec.get("python") or ""
            if not src:
                continue
            tmp_dir = Path(tempfile.gettempdir()) / "cruxeval_proposed"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_dir / f"cruxeval_sample_{i}.py"
            out_path.write_text(src, encoding="utf-8")
            files.append(out_path)
    return files


def run_proposed(
    source_path: Path,
    *,
    enable_feedback: bool = True,
    enable_invariants: bool = True,
    use_llm: bool = True,
) -> ProgramResult:
    source = source_path.read_text(encoding="utf-8")
    # Build program_id consistent with Phase 0
    try:
        relative = str(source_path.relative_to(DATASET_ROOT)).replace("\\", "/")
        program_id = relative.replace("/", "__").replace(".py", "")
    except ValueError:
        program_id = f"cruxeval__{source_path.stem}"
    # Build optional LLM client for feedback + invariants
    feedback_loop = None
    invariant_gen = None
    if use_llm and (enable_feedback or enable_invariants):
        key = _get_api_key()
        if key:
            client = _make_client(key)
            if enable_feedback:
                feedback_loop = RetrospectiveFeedbackLoop(llm_client=client)
            if enable_invariants:
                invariant_gen = LoopInvariantGenerator(
                    llm_client=client, use_llm=True
                )
    return run_pipeline(
        source,
        program_id=program_id,
        source_path=str(source_path),
        baseline="AST_ANCHORED",
        do_replay=True,
        enable_feedback=enable_feedback,
        enable_invariants=enable_invariants,
        feedback_loop=feedback_loop,
        invariant_generator=invariant_gen,
        use_llm=use_llm,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-feedback", action="store_true")
    parser.add_argument("--enable-invariants", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=RESULT_ROOT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    files = collect_source_files()
    if args.limit:
        files = files[: args.limit]
    print(f"Running Proposed Method on {len(files)} files...")
    n_ok = 0
    n_fail = 0
    t0 = time.perf_counter()
    for src_path in files:
        try:
            res = run_proposed(
                src_path,
                enable_feedback=args.enable_feedback,
                enable_invariants=args.enable_invariants,
                use_llm=args.use_llm,
            )
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  {src_path.name}: {exc}")
            continue
        # Save JSON
        out_path = args.out / f"{res.program_id}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(res.model_dump(), fh, indent=2, ensure_ascii=False)
        n_ok += 1
        flag = []
        if any(getattr(c, "feedback_resolution", None) == "verified" for c in res.claim_results):
            flag.append("FB✓")
        if any(getattr(c, "feedback_iterations", None) for c in res.claim_results):
            flag.append(f"FB")
        print(f"  OK    {res.program_id}: V={res.n_verified} CE={res.n_counterexample} "
              f"UT={res.n_unknown_timeout} {' '.join(flag)}")
    elapsed = time.perf_counter() - t0
    print()
    print(f"Done in {elapsed:.1f}s: {n_ok} ok, {n_fail} fail / {len(files)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
