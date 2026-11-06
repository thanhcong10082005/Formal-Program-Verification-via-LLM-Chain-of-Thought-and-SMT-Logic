"""Phase 0.5.2 -- Precondition Extraction.

About 45.9% of the dataset's programs hold their ``assert`` inside an ``if``
branch, so the claim only holds *on that path*. A verifier that ignores the
path condition reports a false alarm on every such program.

This tool writes a ``precondition`` field into the manifest for every
program, so that every baseline receives exactly the same information.

The tool is idempotent: an existing ``precondition`` is never overwritten
unless ``--force`` is given.

Run with::

    python tools/extract_preconditions.py \
        --dataset ..\\Dataset\\filtered \
        --manifest data/bench_v1/manifest_full.json \
        --out data/bench_v1/manifest.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.preconditions import extract  # noqa: E402
from astverifier.shared.structure import (  # noqa: E402
    analyze,
    site_precondition,
)

# How many programs one worker reports after.
PROGRESS_EVERY = 500


def _process_entry(args: tuple) -> dict:
    """Extract the precondition of one program. Runs in a worker process."""
    dataset_dir, entry = args
    root = Path(dataset_dir)
    path = root / entry["path"]

    result = {
        "path": entry["path"],
        "precondition": None,
        "precondition_line": None,
        "precondition_guarded": False,
        "precondition_qf_lia": True,
        "precondition_error": None,
    }

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["precondition_error"] = f"read: {exc}"
        result["precondition_qf_lia"] = False
        return result

    outcome = extract(source)
    result.update(
        precondition=outcome.precondition,
        precondition_line=outcome.line,
        precondition_guarded=outcome.guarded,
        precondition_qf_lia=outcome.in_qf_lia,
        precondition_error=outcome.reason,
    )

    # Cross-check: the extracted precondition must agree with the shared
    # structural analysis, or one of the two is wrong.
    try:
        shape = analyze(source)
        if shape.sites:
            expected = site_precondition(shape.sites[0])
            if expected != outcome.precondition:
                result["precondition_error"] = (
                    "structure and precondition extraction disagree"
                )
    except (SyntaxError, ValueError):
        pass

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT.parent / "Dataset" / "filtered"))
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "bench_v1" / "manifest_full.json"),
        help="the manifest to read the programs from",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "bench_v1" / "manifest.json"),
        help="the manifest to write the preconditions into",
    )
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    parser.add_argument("--force", action="store_true", help="overwrite existing values")
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset).resolve()
    manifest_path = Path(args.manifest).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    programs = manifest["programs"]

    existing: dict[str, dict] = {}
    if out_path.exists() and not args.force:
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
            for entry in previous.get("programs", []):
                if "precondition" in entry:
                    existing[entry["path"]] = entry
        except (json.JSONDecodeError, OSError):
            existing = {}

    todo = [p for p in programs if p["path"] not in existing]
    print(f"manifest       : {manifest_path}")
    print(f"programs       : {len(programs)}")
    print(f"already done   : {len(existing)}")
    print(f"to process     : {len(todo)}")
    print("-" * 60, flush=True)

    started = time.perf_counter()
    results: dict[str, dict] = {}
    if todo:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.workers, maxtasksperchild=500) as pool:
            for done, result in enumerate(
                pool.imap_unordered(
                    _process_entry, [(str(dataset_dir), p) for p in todo], chunksize=16
                ),
                start=1,
            ):
                results[result["path"]] = result
                if done % PROGRESS_EVERY == 0:
                    rate = done / max(1e-9, time.perf_counter() - started)
                    print(
                        f"  {done}/{len(todo)}  ({rate:.1f}/s)  "
                        f"eta={((len(todo) - done) / max(rate, 1e-9)) / 60:.1f} min",
                        flush=True,
                    )

    elapsed = time.perf_counter() - started

    merged = []
    for entry in programs:
        out = dict(entry)
        found = existing.get(entry["path"]) or results.get(entry["path"])
        if found:
            out.update(
                precondition=found.get("precondition"),
                precondition_line=found.get("precondition_line"),
                precondition_guarded=bool(found.get("precondition_guarded")),
                precondition_qf_lia=bool(found.get("precondition_qf_lia", True)),
                precondition_error=found.get("precondition_error"),
            )
        merged.append(out)

    guarded = sum(1 for e in merged if e.get("precondition_guarded"))
    with_pre = sum(1 for e in merged if e.get("precondition"))
    qf_lia = sum(1 for e in merged if e.get("precondition_qf_lia"))
    errors = sum(1 for e in merged if e.get("precondition_error"))

    result_manifest = dict(manifest)
    result_manifest["programs"] = merged
    result_manifest["version"] = "2.1"
    result_manifest["preconditions"] = {
        "generated_by": "tools/extract_preconditions.py",
        "n_with_precondition": with_pre,
        "n_guarded": guarded,
        "n_qf_lia": qf_lia,
        "n_errors": errors,
        "elapsed_seconds": round(elapsed, 2),
    }
    out_path.write_text(
        json.dumps(result_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("-" * 60)
    print(f"wrote {out_path}")
    print(f"with precondition : {with_pre}")
    print(f"guarded           : {guarded}")
    print(f"qf-lia            : {qf_lia}")
    print(f"errors            : {errors}")
    print(f"elapsed           : {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
