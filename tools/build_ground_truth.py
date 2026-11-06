"""Phase 0.5.1 -- Execution Oracle & Z3 Ground Truth Labeling.

Turns the raw ``Dataset/filtered`` manifest into a labeled manifest by
assigning every claim-carrying program an ``expected_verdict``.

Two independent layers produce the label:

1. **CPython execution oracle (tier 1).** The program is executed in a
   sandbox over a deterministic input grid. A failed assert is a concrete
   counterexample -> ``COUNTEREXAMPLE``. A program whose assert never runs
   -> ``UNREACHABLE``. Otherwise the claim held on every input tried.

2. **Z3 validation (tier 2).** A finite oracle can only ever *falsify* a
   claim; it can never prove one. Every program that survived tier 1 is
   therefore handed to the exact symbolic prover, which encodes the loop as
   a Datalog relation and asks Spacer the exact reachability question
   (reachability + violation). A proof yields ``gt_confidence = high``; an
   inconclusive proof leaves it at ``medium`` and marks the sample
   ``ambiguous``.

The labelling loop contains no LLM: no method is ever graded by its own
instrument.

The script is idempotent. Programs that already carry a label in the output
manifest are skipped, so an interrupted run can simply be restarted.

Run with::

    python tools/build_ground_truth.py \
        --dataset ..\\Dataset\\filtered \
        --out data/bench_v1/manifest.json
"""

from __future__ import annotations

import argparse
import ast
import json
import multiprocessing as mp
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.oracle import (  # noqa: E402
    OBS_COUNTEREXAMPLE,
    OBS_ERROR,
    OBS_NO_VIOLATION,
    OBS_TIMEOUT,
    OBS_UNREACHABLE,
    observe,
    replay_witness,
)
from astverifier.shared.prover import (  # noqa: E402
    VERDICT_COUNTEREXAMPLE as PROVER_COUNTEREXAMPLE,
)
from astverifier.shared.prover import (  # noqa: E402
    VERDICT_REACHABLE as PROVER_REACHABLE,
)
from astverifier.shared.prover import (  # noqa: E402
    VERDICT_UNREACHABLE as PROVER_UNREACHABLE,
)
from astverifier.shared.prover import (  # noqa: E402
    VERDICT_VERIFIED as PROVER_VERIFIED,
)
from astverifier.shared.prover import prove, prove_reachability  # noqa: E402

# --- Label vocabulary --------------------------------------------------------

VERDICT_VERIFIED = "VERIFIED"
VERDICT_COUNTEREXAMPLE = "COUNTEREXAMPLE"
VERDICT_UNREACHABLE = "UNREACHABLE"
VERDICT_UNKNOWN = "UNKNOWN"

# How many programs one worker reports after.
PROGRESS_EVERY = 250


# --- Worker ------------------------------------------------------------------


def _signature_params(source: str, func_name: str) -> list[str]:
    """Return the parameter names of ``func_name`` (or the first function)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
            node.name == func_name or func_name == "main"
        ):
            return [
                a.arg
                for a in list(node.args.posonlyargs) + list(node.args.args)
            ]
    return []


def _resolve_function(source: str, entry: dict) -> tuple[str, list[str]]:
    """Return the function under test and its parameter names.

    The manifest records the function name for the programs that were
    extracted from a top-level function, but leaves it ``null`` for the
    programs that were extracted as a whole record. In those cases the
    function is the single top-level definition of the file.
    """
    func_name = entry.get("function_name")
    params = list(entry.get("benchmark_nondeterministic_inputs") or [])

    if not func_name:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return "main", params
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                func_name = node.name
                break
        else:
            func_name = "main"

    if not params:
        params = _signature_params(source, func_name)
    return func_name, params


def _process_entry(args: tuple) -> dict:
    """Label one program. Runs in a worker process."""
    dataset_dir, entry = args
    root = Path(dataset_dir)
    path = root / entry["path"]

    result = {
        "path": entry["path"],
        "expected_verdict": None,
        "gt_source": None,
        "gt_confidence": None,
        "counterexample_input": None,
        "oracle_inputs_tried": None,
        "oracle_error": None,
        "ambiguous": False,
    }

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.update(
            expected_verdict=VERDICT_UNKNOWN,
            gt_source="execution",
            gt_confidence="low",
            oracle_error=f"read: {exc}",
        )
        return result

    claims = entry.get("claims") or []
    if not claims:
        result.update(
            expected_verdict=VERDICT_UNKNOWN,
            gt_source="execution",
            gt_confidence="low",
            oracle_error="no claim",
        )
        return result

    func_name, params = _resolve_function(source, entry)

    # --- Tier 1: the execution oracle --------------------------------------
    outcome = observe(source, func_name, params)
    result["oracle_inputs_tried"] = outcome.inputs_tried

    if outcome.observation == OBS_COUNTEREXAMPLE:
        result.update(
            expected_verdict=VERDICT_COUNTEREXAMPLE,
            gt_source="execution",
            gt_confidence="high",
            counterexample_input=outcome.witness,
        )
        return result

    if outcome.observation == OBS_UNREACHABLE:
        # The assert never executed on the grid. A finite grid cannot
        # *prove* unreachability -- it can only fail to find an input --
        # so the exact reachability query decides the verdict.
        reach = prove_reachability(
            source,
            claims[0],
            tag=f"g{abs(hash(entry['path'])) % 1_000_000}",
        )
        if reach.verdict == PROVER_UNREACHABLE:
            result.update(
                expected_verdict=VERDICT_UNREACHABLE,
                gt_source="execution+z3",
                gt_confidence="high",
            )
        elif reach.verdict == PROVER_REACHABLE:
            # The site really is reachable, so the claim is not vacuous.
            # Tier 2 now decides whether it holds there.
            proof = prove(
                source,
                claims[0],
                precondition=entry.get("precondition"),
                tag=f"g{abs(hash(entry['path'])) % 1_000_000}",
            )
            if proof.verdict == PROVER_VERIFIED:
                result.update(
                    expected_verdict=VERDICT_VERIFIED,
                    gt_source="execution+z3",
                    gt_confidence="high",
                )
            elif proof.verdict == PROVER_COUNTEREXAMPLE:
                witness = proof.witness
                confirmed = bool(witness) and replay_witness(
                    source, func_name, witness
                )
                if confirmed:
                    result.update(
                        expected_verdict=VERDICT_COUNTEREXAMPLE,
                        gt_source="execution+z3",
                        gt_confidence="high",
                        counterexample_input=witness,
                    )
                else:
                    result.update(
                        expected_verdict=VERDICT_VERIFIED,
                        gt_source="execution+z3",
                        gt_confidence="medium",
                        ambiguous=True,
                        oracle_error=(
                            "prover candidate counterexample did not replay: "
                            f"{proof.reason}"
                        ),
                    )
            else:
                result.update(
                    expected_verdict=VERDICT_VERIFIED,
                    gt_source="execution",
                    gt_confidence="medium",
                    ambiguous=True,
                    oracle_error=proof.reason,
                )
        else:
            # The exact query could not decide. The grid's observation
            # stands, but it is a *guess*, so it is downgraded and marked
            # ambiguous rather than asserted as fact.
            result.update(
                expected_verdict=VERDICT_UNREACHABLE,
                gt_source="execution",
                gt_confidence="medium",
                ambiguous=True,
                oracle_error=reach.reason,
            )
        return result

    if outcome.observation in (OBS_TIMEOUT, OBS_ERROR):
        # The oracle could not decide. Tier 2 may still decide.
        result["oracle_error"] = outcome.error
        oracle_observation = outcome.observation
    else:
        oracle_observation = OBS_NO_VIOLATION

    # --- Tier 2: the exact symbolic prover ---------------------------------
    proof = prove(
        source,
        claims[0],
        precondition=entry.get("precondition"),
        tag=f"g{abs(hash(entry['path'])) % 1_000_000}",
    )

    if proof.verdict == PROVER_VERIFIED:
        result.update(
            expected_verdict=VERDICT_VERIFIED,
            gt_source="execution+z3",
            gt_confidence="high",
        )
    elif proof.verdict == PROVER_UNREACHABLE:
        result.update(
            expected_verdict=VERDICT_UNREACHABLE,
            gt_source="execution+z3",
            gt_confidence="high",
        )
    elif proof.verdict == PROVER_COUNTEREXAMPLE:
        # The prover found a candidate violation. It only counts as a
        # counterexample once CPython confirms it, so the witness is
        # replayed here.
        witness = proof.witness
        confirmed = False
        if witness:
            confirmed = replay_witness(source, func_name, witness)
        if confirmed:
            result.update(
                expected_verdict=VERDICT_COUNTEREXAMPLE,
                gt_source="execution+z3",
                gt_confidence="high",
                counterexample_input=witness,
            )
        else:
            # The model was not reproducible. The claim is neither proven
            # nor proven false, so the label stays at the tier-1 level and
            # is marked ambiguous.
            result.update(
                expected_verdict=VERDICT_VERIFIED,
                gt_source="execution+z3"
                if oracle_observation == OBS_NO_VIOLATION
                else "execution",
                gt_confidence="medium",
                ambiguous=True,
                oracle_error=(
                    f"prover candidate counterexample did not replay: {proof.reason}"
                ),
            )
    else:
        # Inconclusive. The label falls back to the tier-1 observation,
        # downgraded.
        if oracle_observation == OBS_NO_VIOLATION:
            result.update(
                expected_verdict=VERDICT_VERIFIED,
                gt_source="execution",
                gt_confidence="medium",
                ambiguous=True,
                oracle_error=proof.reason,
            )
        else:
            result.update(
                expected_verdict=VERDICT_UNKNOWN,
                gt_source="execution",
                gt_confidence="low",
                ambiguous=True,
                oracle_error=result["oracle_error"] or proof.reason,
            )
    return result


# --- Driver ------------------------------------------------------------------


def load_manifest(dataset_dir: Path) -> dict:
    return json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT.parent / "Dataset" / "filtered"))
    parser.add_argument("--out", default=str(ROOT / "data" / "bench_v1" / "manifest.json"))
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    parser.add_argument("--label", default="", help="only label this claim text")
    parser.add_argument(
        "--checkpoint",
        default="",
        help="a JSONL file that receives every label as it is produced, so "
        "an interrupted run can be resumed without redoing the work",
    )
    parser.add_argument(
        "--shard",
        default="",
        help="label only the programs whose index modulo --shards equals "
        "--shard; used to split the dataset across several processes",
    )
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument(
        "--relabel-unreachable",
        action="store_true",
        help="re-process only the programs already labelled UNREACHABLE by "
        "the execution oracle, so that tier 2 confirms each of them; the "
        "labels it produces replace the old ones",
    )
    parser.add_argument(
        "--relabel-z3",
        action="store_true",
        help="re-process every program whose label was decided by tier 2, "
        "so that a corrected prover is applied to the whole dataset",
    )
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(dataset_dir)
    programs = manifest["programs"]

    # Resume support: keep labels that were already produced.
    existing: dict[str, dict] = {}
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
            for entry in previous.get("programs", []):
                if entry.get("expected_verdict"):
                    existing[entry["path"]] = entry
        except (json.JSONDecodeError, OSError):
            existing = {}

    # The checkpoint is a JSONL file: one label per line. It is the
    # primary resume source, because it survives a run that is killed
    # before the final manifest can be written.
    checkpoint_path = Path(args.checkpoint).resolve() if args.checkpoint else None
    if checkpoint_path and checkpoint_path.exists():
        with checkpoint_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("expected_verdict"):
                    existing[record["path"]] = record

    todo = [p for p in programs if p.get("claims") and p["path"] not in existing]
    if args.relabel_unreachable:
        # Re-process exactly the programs the execution oracle labelled
        # UNREACHABLE, so that tier 2 confirms (or corrects) each of them.
        # The checkpoint's own labels are dropped for these paths, because
        # the new run has to redo them.
        targets = {
            path
            for path, label in existing.items()
            if label.get("expected_verdict") == VERDICT_UNREACHABLE
        }
        for path in targets:
            existing.pop(path, None)
        todo = [p for p in programs if p["path"] in targets]
    if args.relabel_z3:
        # Re-process every program whose label was decided by tier 2.
        # This is needed after the prover's reachability query was made
        # exact: Spacer's characterization over-approximates the reachable
        # states, so a label that was decided with it can be wrong.
        targets = {
            path
            for path, label in existing.items()
            if label.get("gt_source") == "execution+z3"
        }
        for path in targets:
            existing.pop(path, None)
        todo = [p for p in programs if p["path"] in targets]
    if args.label:
        todo = [p for p in todo if args.label in (p.get("claims") or [])]
    if args.shards > 1:
        shard = int(args.shard or 0)
        todo = [p for i, p in enumerate(todo) if i % args.shards == shard]
    if args.limit:
        todo = todo[: args.limit]

    print(f"dataset        : {dataset_dir}")
    print(f"programs       : {len(programs)}")
    print(f"with claims    : {sum(1 for p in programs if p.get('claims'))}")
    print(f"already labeled: {len(existing)}")
    print(f"to label       : {len(todo)}")
    print(f"workers        : {args.workers}")
    if checkpoint_path:
        print(f"checkpoint     : {checkpoint_path}")
    print("-" * 60, flush=True)

    started = time.perf_counter()
    labels: dict[str, dict] = {}
    if todo:
        checkpoint_handle = (
            checkpoint_path.open("a", encoding="utf-8") if checkpoint_path else None
        )
        try:
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=args.workers, maxtasksperchild=200) as pool:
                for done, result in enumerate(
                    pool.imap_unordered(
                        _process_entry,
                        [(str(dataset_dir), p) for p in todo],
                        chunksize=4,
                    ),
                    start=1,
                ):
                    labels[result["path"]] = result
                    if checkpoint_handle is not None:
                        checkpoint_handle.write(
                            json.dumps(result, ensure_ascii=False) + "\n"
                        )
                        checkpoint_handle.flush()
                    if done % PROGRESS_EVERY == 0:
                        rate = done / max(1e-9, time.perf_counter() - started)
                        print(
                            f"  {done}/{len(todo)}  ({rate:.1f}/s)  "
                            f"eta={((len(todo) - done) / max(rate, 1e-9)) / 60:.1f} min",
                            flush=True,
                        )
        finally:
            if checkpoint_handle is not None:
                checkpoint_handle.close()

    elapsed = time.perf_counter() - started

    # Merge the labels back into the original manifest, preserving order.
    #
    # The fields that earlier phases wrote into the same manifest are kept
    # as well: re-running the labelling must not wipe the preconditions
    # that Phase 0.5.2 extracted.
    preserved = (
        "precondition",
        "precondition_line",
        "precondition_guarded",
        "precondition_qf_lia",
        "precondition_error",
    )
    merged = []
    for entry in programs:
        out = dict(entry)
        previous = existing.get(entry["path"])
        if previous:
            for field in preserved:
                if field in previous:
                    out[field] = previous[field]
        label = previous or labels.get(entry["path"])
        if label:
            out.update(
                expected_verdict=label.get("expected_verdict"),
                gt_source=label.get("gt_source"),
                gt_confidence=label.get("gt_confidence"),
                counterexample_input=label.get("counterexample_input"),
                oracle_inputs_tried=label.get("oracle_inputs_tried"),
                oracle_error=label.get("oracle_error"),
                ambiguous=bool(label.get("ambiguous")),
            )
        merged.append(out)

    counts = Counter(
        e.get("expected_verdict") for e in merged if e.get("expected_verdict")
    )
    confidence = Counter(
        e.get("gt_confidence") for e in merged if e.get("gt_confidence")
    )
    source_counts = Counter(e.get("gt_source") for e in merged if e.get("gt_source"))

    result_manifest = {
        "version": "2.0",
        "generated_by": "tools/build_ground_truth.py",
        "source_manifest": str(dataset_dir / "manifest.json"),
        "labeling": (
            "tier 1 = CPython execution oracle over a deterministic input grid; "
            "tier 2 = exact Z3/Spacer proof of the survivors. No LLM in the "
            "labeling loop."
        ),
        "n_programs": len(merged),
        "counts_by_verdict": dict(sorted(counts.items())),
        "counts_by_gt_confidence": dict(sorted(confidence.items())),
        "counts_by_gt_source": dict(sorted(source_counts.items())),
        "n_ambiguous": sum(1 for e in merged if e.get("ambiguous")),
        "elapsed_seconds": round(elapsed, 2),
        "programs": merged,
    }

    # Carry over the summary blocks that the other phases added, so that
    # re-running the labelling does not drop them.
    if out_path.exists():
        try:
            previous_manifest = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_manifest = {}
        for key in ("preconditions", "sampling"):
            if key in previous_manifest:
                result_manifest[key] = previous_manifest[key]
    out_path.write_text(
        json.dumps(result_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("-" * 60)
    print(f"wrote {out_path}")
    print(f"verdict    : {dict(sorted(counts.items()))}")
    print(f"confidence : {dict(sorted(confidence.items()))}")
    print(f"source     : {dict(sorted(source_counts.items()))}")
    print(f"ambiguous  : {result_manifest['n_ambiguous']}")
    print(f"elapsed    : {elapsed / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
