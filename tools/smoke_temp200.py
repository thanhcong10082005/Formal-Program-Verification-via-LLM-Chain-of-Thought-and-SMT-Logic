"""Smoke-check the temporary dataset against the Core Proposed Method.

Runs the AST-anchored pipeline on a deterministic sample of the dataset and
prints the agreement between the pipeline verdict and the execution-oracle
ground truth. This is a sanity check, not a benchmark: the pipeline is not
expected to solve every program, but it must not crash and it must agree on
the easy shapes.

Run with::

    python tools/smoke_temp200.py [--size N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.core.pipeline import run_pipeline  # noqa: E402

DATASET_DIR = ROOT / "dataset_temp_200"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=20,
                        help="number of programs to sample")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-feedback", action="store_true",
                        help="disable the retrospective feedback loop "
                             "(it calls the LLM and dominates the runtime)")
    args = parser.parse_args()

    manifest = json.loads(
        (DATASET_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    entries = manifest["programs"]
    random.seed(args.seed)
    sample = random.sample(entries, min(args.size, len(entries)))

    counts: dict[str, int] = {}
    for entry in sorted(sample, key=lambda item: item["program_id"]):
        path = DATASET_DIR / entry["path"]
        source = path.read_text(encoding="utf-8")
        result = run_pipeline(
            source,
            program_id=entry["program_id"],
            source_path=str(path),
            do_replay=True,
            enable_feedback=not args.no_feedback,
        )
        if result.claim_results:
            status = result.claim_results[0].status.value
            reason = result.claim_results[0].reason[:70]
        else:
            status = "NO_CLAIM"
            reason = result.subset_rejection_reason or ""
        counts[status] = counts.get(status, 0) + 1
        print(
            f"{entry['program_id']:38s} gt={entry['expected_verdict']:16s} "
            f"got={status:16s} {reason}"
        )

    print("\n--- status counts ---")
    for status, count in sorted(counts.items()):
        print(f"  {status:20s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
