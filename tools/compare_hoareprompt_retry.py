"""Select failed samples and score a separately saved retry pass."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_benchmark import HOAREPROMPT_OFFICIAL, ROOT, SELF_REFINE_OFFICIAL
from score_original_asserts import score


def read_rows(checkpoint: Path) -> dict:
    rows = {}
    for line in checkpoint.read_text().splitlines():
        row = json.loads(line)
        pid = row["program_id"]
        if pid in rows:
            raise ValueError(f"Duplicate program ID {pid} in {checkpoint}")
        rows[pid] = row
    return rows


def merge_retries(original: dict, retried: dict) -> dict:
    """Replace only missing verdicts, retaining the cost of both attempts."""
    merged = original.copy()
    for pid, row in retried.items():
        prior = original[pid]
        if prior["n_verified"] + prior["n_counterexample"]:
            raise ValueError(f"Cannot replace a decisive verdict for {pid}")
        merged[pid] = dict(
            row,
            total_tokens_in=prior["total_tokens_in"] + row["total_tokens_in"],
            total_tokens_out=prior["total_tokens_out"] + row["total_tokens_out"],
            elapsed_seconds=prior["elapsed_seconds"] + row["elapsed_seconds"],
        )
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "compare"))
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--retry", required=True, type=Path)
    parser.add_argument(
        "--method", choices=(HOAREPROMPT_OFFICIAL, SELF_REFINE_OFFICIAL), default=HOAREPROMPT_OFFICIAL,
    )
    args = parser.parse_args()
    original_ids = args.primary / "program_ids.json"
    program_ids = json.loads(original_ids.read_text())
    checkpoint = f"{args.method}.jsonl"
    original = read_rows(args.primary / checkpoint)
    if len(set(program_ids)) != len(program_ids) or set(original) != set(program_ids):
        raise ValueError("Primary run must have a row for every cohort sample")
    if args.method == HOAREPROMPT_OFFICIAL:
        failed_ids = [pid for pid in program_ids if original[pid]["n_translation_error"] == 1]
        prefix = "HOAREPROMPT"
    else:
        failed_ids = [
            pid for pid in program_ids
            if original[pid]["n_verified"] + original[pid]["n_counterexample"] == 0
        ]
        prefix = args.method
    retry_ids = args.retry / "program_ids.json"
    if args.action == "prepare":
        args.retry.mkdir(parents=True, exist_ok=True)
        if retry_ids.exists() and json.loads(retry_ids.read_text()) != failed_ids:
            raise ValueError("Existing retry cohort differs from failed primary samples")
        retry_ids.write_text(json.dumps(failed_ids, indent=2) + "\n")
        print(f"Selected {len(failed_ids)} failed samples for retry")
        return

    retried = read_rows(args.retry / checkpoint) if failed_ids else {}
    if set(retried) != set(failed_ids):
        raise ValueError("Retry must have a row for every failed primary sample")
    merged = merge_retries(original, retried)
    comparison = args.retry / "comparison"
    comparison.mkdir(exist_ok=True)
    for name, rows in (("FIRST_PASS", original), ("WITH_RETRY", merged)):
        (comparison / f"{prefix}_{name}.jsonl").write_text(
            "".join(json.dumps(rows[pid]) + "\n" for pid in program_ids)
        )
    summary = score(comparison, original_ids, ROOT / "dataset")
    report = {
        "retried_samples": len(retried),
        "recovered_verdicts": sum(
            row["n_verified"] + row["n_counterexample"] for row in retried.values()
        ),
        "variants": summary,
    }
    for row in summary.values():
        decided = sum(row["statuses"].get(status, 0) for status in ("VERIFIED", "COUNTEREXAMPLE"))
        row["accuracy_among_verdicts_pct"] = round(100 * row["correct"] / decided, 2) if decided else None
    (args.retry / "retry_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
