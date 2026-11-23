"""Measure the effect of the loop-invariant path on a real dataset.

The tool runs the pipeline twice over the same samples: once with the
invariant generator enabled and once with it disabled, then reports the
verdict distribution and every claim whose verdict changed.  It is the
quickest way to see whether the invariant path actually pays off on a
given corpus.

Usage::

    python tools/measure_invariant_effect.py [--limit N] [--dataset DIR]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from astverifier.core.pipeline import run_pipeline
from astverifier.shared.schemas import StatusEnum

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = ROOT / "data" / "bench_v1" / "sample_1000.txt"
DEFAULT_DATASET = ROOT.parent / "Dataset" / "filtered"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=40, help="how many samples to process"
    )
    parser.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE,
        help="file listing the sample paths, one per line",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="root directory the sample paths are relative to",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.sample.exists():
        raise SystemExit(f"sample list not found: {args.sample}")

    paths = [line.strip() for line in args.sample.read_text().splitlines() if line.strip()]
    stats = {
        "with_inv": {status.value: 0 for status in StatusEnum},
        "without_inv": {status.value: 0 for status in StatusEnum},
    }
    changed: list[tuple[str, str, str]] = []
    processed = 0
    started = time.perf_counter()

    for relative in paths[: args.limit]:
        source_path = args.dataset / relative
        if not source_path.exists():
            continue
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        processed += 1

        with_inv = run_pipeline(source, do_replay=False, use_llm=False)
        without_inv = run_pipeline(
            source, do_replay=False, use_llm=False, enable_invariants=False
        )
        for result, key in ((with_inv, "with_inv"), (without_inv, "without_inv")):
            for claim_result in result.claim_results:
                stats[key][claim_result.status.value] += 1
        if with_inv.claim_results and without_inv.claim_results:
            before = without_inv.claim_results[0].status.value
            after = with_inv.claim_results[0].status.value
            if before != after:
                changed.append((relative, before, after))

    elapsed = time.perf_counter() - started
    print(f"processed {processed} samples in {elapsed:.1f}s\n")
    for key in ("without_inv", "with_inv"):
        total = sum(stats[key].values()) or 1
        print(f"--- {key} ---")
        for status, count in stats[key].items():
            if count:
                print(f"  {status:18s} {count:5d}  ({100 * count / total:5.1f}%)")
        print()

    print(f"changed verdicts: {len(changed)}")
    for relative, before, after in changed[:20]:
        print(f"  {relative}\n     {before} -> {after}")


if __name__ == "__main__":
    main()
