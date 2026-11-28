"""Compare benchmark checkpoints on a fixed cohort using only original assertions."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from run_benchmark import PROPOSED_METHODS, ROOT, load_claim_pool, load_ground_truth

DECISIVE = {"VERIFIED", "COUNTEREXAMPLE"}


def score(
    results_dir: Path, ids_path: Path, dataset: Path, *, ground_truth_path: Path | None = None,
) -> dict:
    program_ids = json.loads(ids_path.read_text())
    if not program_ids or len(set(program_ids)) != len(program_ids):
        raise ValueError("Expected a nonempty cohort of unique program IDs")
    labels = load_ground_truth(ground_truth_path or dataset / "manifest.json")
    pool = load_claim_pool(dataset / "claim_pool.json")
    original = {}
    for pid in program_ids:
        (claim,) = pool[pid]
        if labels[pid] not in DECISIVE or not claim.provenance.startswith("assert@"):
            raise ValueError(f"Expected an original assertion with a binary label: {pid}")
        original[pid] = claim
    sources = {pid: (dataset / pid).read_text() for pid in program_ids}
    predictions = {}
    summary = {}
    for path in sorted(results_dir.glob("*.jsonl")):
        method = path.stem
        by_id = {}
        for line in path.read_text().splitlines():
            run = json.loads(line)
            pid = run["program_id"]
            if pid not in original:
                continue
            if pid in by_id or run["source_text"] != sources[pid]:
                raise ValueError(f"{path}: duplicate ID or different source for {pid}")
            assertion = original[pid]
            candidates = [c for c in run["claim_results"] if c["claim_id"] == assertion.claim_id]
            if not candidates and run["baseline"] in PROPOSED_METHODS:
                candidates = [
                    c for c in run["claim_results"]
                    if c["claim_id"].startswith(f"assert_L{assertion.source_line}_#")
                    and c["claim_text"] == assertion.text
                    and assertion.ast_anchor in c["anchor"]["node_ids"]
                ]
            (claim,) = candidates or (None,)
            status = claim["status"] if claim else "NO_ASSERT_RESULT"
            reason = claim["reason"] if claim else run.get("subset_rejection_reason")
            if reason and reason.startswith("benchmark runner caught"):
                status = "RUNNER_ERROR"
            replay = (claim.get("counterexample") or {}).get("replay_verdict") if claim else None
            by_id[pid] = (status, replay is True, reason, run)
        stats = Counter(by_id[pid][0] if pid in by_id else "MISSING" for pid in program_ids)
        correct = sum(status == labels[pid] for pid, (status, _, _, _) in by_id.items())
        covered = sum(stats[s] for s in DECISIVE)
        summary[method] = {
            "expected": len(program_ids), "logged": len(by_id), "correct": correct,
            "accuracy_pct": round(100 * correct / len(program_ids), 2),
            "coverage_pct": round(100 * covered / len(program_ids), 2),
            "statuses": dict(sorted(stats.items())),
            "false_verified": sum(s == "VERIFIED" and labels[p] == "COUNTEREXAMPLE"
                                  for p, (s, _, _, _) in by_id.items()),
            "false_counterexample": sum(s == "COUNTEREXAMPLE" and labels[p] == "VERIFIED"
                                        for p, (s, _, _, _) in by_id.items()),
            "replayed_counterexamples": sum(s == "COUNTEREXAMPLE" and ok
                                            for s, ok, _, _ in by_id.values()),
            "tokens_in": sum(row["total_tokens_in"] for *_, row in by_id.values()),
            "tokens_out": sum(row["total_tokens_out"] for *_, row in by_id.values()),
            "sum_program_seconds": round(sum(row["elapsed_seconds"] for *_, row in by_id.values()), 2),
        }
        predictions[method] = by_id

    with (results_dir / "original_assert_verdicts.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["method", "program_id", "claim_id", "expected", "verdict", "replayed", "reason"])
        for method, by_id in predictions.items():
            for pid in program_ids:
                status, replay, reason, _ = by_id.get(pid, ("MISSING", False, "", None))
                writer.writerow([method, pid, original[pid].claim_id, labels[pid], status, replay, reason])
    proposed = predictions.get("PROPOSED_LLM", {})
    for method, by_id in predictions.items():
        if method != "PROPOSED_LLM" and proposed:
            both = proposed.keys() & by_id.keys()
            summary[method]["paired_with_proposed"] = dict(
                compared=len(both),
                proposed_only_correct=sum(proposed[p][0] == labels[p] != by_id[p][0] for p in both),
                baseline_only_correct=sum(by_id[p][0] == labels[p] != proposed[p][0] for p in both),
            )
    (results_dir / "original_assert_scores.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--program-ids", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset")
    parser.add_argument("--ground-truth", type=Path, help="Optional manifest with independently audited labels.")
    args = parser.parse_args()
    scores = score(args.results_dir, args.program_ids, args.dataset, ground_truth_path=args.ground_truth)
    for method, row in scores.items():
        print(f"{method:22} logged={row['logged']:3} correct={row['correct']:3} coverage={row['coverage_pct']:6.2f}%")


if __name__ == "__main__":
    main()
