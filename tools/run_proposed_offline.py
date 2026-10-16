"""
run_proposed_offline.py - E2E benchmark cho Proposed Method ở chế độ
DETERMINISTIC (không gọi LLM). Mục đích:
  1. Đo lường hiệu quả khi các module mới chạy ở fallback mode.
  2. Tính các metric Proposed (FSR/HRR = 0 vì không có LLM refinement).
  3. Làm control group cho E2E với LLM thật.

Chạy lại toàn bộ pipeline trên dataset, lưu JSON vào
results/PROPOSED/<program_id>.json.
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
from astverifier.pipeline import run_pipeline

DATASET_ROOT = PROJECT_ROOT.parent / "Dataset"
PY_LOOPS_ROOT = DATASET_ROOT / "sv-benchmarks-loops-py"
CRUXEVAL_JSONL = DATASET_ROOT / "cruxeval-main" / "data" / "cruxeval.jsonl"
RESULT_ROOT = PROJECT_ROOT / "results" / "PROPOSED_OFFLINE"


def collect_source_files() -> List[Path]:
    files: List[Path] = []
    for p in sorted(PY_LOOPS_ROOT.rglob("*.py")):
        if "/." in str(p) or p.name.startswith("."):
            continue
        files.append(p)
    if CRUXEVAL_JSONL.exists():
        import tempfile
        with open(CRUXEVAL_JSONL, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()][:21]
        tmp_dir = Path(tempfile.gettempdir()) / "cruxeval_proposed_offline"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = rec.get("code") or rec.get("python") or ""
            if not src:
                continue
            out_path = tmp_dir / f"cruxeval_sample_{i}.py"
            out_path.write_text(src, encoding="utf-8")
            files.append(out_path)
    return files


def main() -> int:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    files = collect_source_files()
    print(f"Running Proposed Method (offline/deterministic) on {len(files)} files...")
    n_ok = 0
    n_fail = 0
    t0 = time.perf_counter()
    for src_path in files:
        source = src_path.read_text(encoding="utf-8")
        try:
            relative = str(src_path.relative_to(DATASET_ROOT)).replace("\\", "/")
            program_id = relative.replace("/", "__").replace(".py", "")
        except ValueError:
            program_id = f"cruxeval__{src_path.stem}"
        try:
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=str(src_path),
                baseline="AST_ANCHORED",
                do_replay=True,
                use_llm=False,
                enable_feedback=False,  # no LLM available
                enable_invariants=True,
            )
        except Exception as exc:
            n_fail += 1
            print(f"  FAIL  {program_id}: {exc}")
            continue
        out_path = RESULT_ROOT / f"{program_id}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(res.model_dump(), fh, indent=2, ensure_ascii=False)
        n_ok += 1
    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.1f}s: {n_ok} ok, {n_fail} fail / {len(files)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
