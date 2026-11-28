"""Publish one result per baseline for the fixed MiniCPM benchmark cohort."""
from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from compare_hoareprompt_retry import merge_retries, read_rows
from score_original_asserts import DECISIVE, ROOT, score

PRIMARY = ROOT / "results" / "checkpoints" / "results_minicpm_original_asserts_200"
V3 = ROOT / "results" / "checkpoints" / "results_llm_tokenlab_minicpm_reliability_v3_stratified_200"
HOARE_RETRY = ROOT / "results" / "checkpoints" / "results_minicpm_original_asserts_200_retry60"
H_OFFICIAL = ROOT / "results" / "checkpoints" / "results_self_refine_official_minicpm_stratified_200"
H_RETRY = H_OFFICIAL.with_name(H_OFFICIAL.name + "_retry")
H_REPAIR = H_OFFICIAL.with_name(H_OFFICIAL.name + "_repair14")
H_FINAL = H_OFFICIAL.with_name(H_OFFICIAL.name + "_repair4")
OUTPUT = ROOT / "results" / "benchmark_200.csv"
AUDITED_MANIFEST = OUTPUT.parent / "benchmark_200_label_audit" / "manifest.json"


@dataclass(frozen=True)
class Candidate:
    key: str
    version: str
    sources: tuple[Path, ...]
    retry_attempts: int = 0
    recovered_verdicts: int = 0


def decisive_count(rows: Mapping) -> int:
    return sum(row["n_verified"] + row["n_counterexample"] > 0 for row in rows.values())


def merge_stages(first: Path, stages: tuple[Path, ...], cohort: set[str]) -> tuple[dict, int, int]:
    rows = read_rows(first)
    if rows.keys() != cohort:
        raise ValueError(f"Checkpoint does not match the common cohort: {first}")
    prior_decisive = decisive_count(rows)
    attempts = 0
    for checkpoint in stages:
        if not checkpoint.exists():
            continue
        retry = read_rows(checkpoint)
        rows = merge_retries(rows, retry)
        attempts += len(retry)
    return rows, attempts, decisive_count(rows) - prior_decisive


def copy_candidate(candidate: Candidate, directory: Path, cohort: set[str]) -> None:
    if read_rows(candidate.sources[0]).keys() != cohort:
        raise ValueError(f"Checkpoint does not match the common cohort: {candidate.sources[0]}")
    (directory / f"{candidate.key}.jsonl").symlink_to(candidate.sources[0])


def main() -> None:
    ids_path = PRIMARY / "program_ids.json"
    ids = json.loads(ids_path.read_text())
    cohort = set(ids)
    if len(ids) != len(cohort) or len(cohort) != 200:
        raise ValueError("Expected the fixed cohort of 200 unique samples")

    families = {name: [Candidate(name, "Lượt chính", (PRIMARY / f"{name}.jsonl",))]
                for name in "ABCDEFGHIJKLMN"}
    for name in "ACDEHN":
        families[name].append(Candidate(f"{name}_V3", "Reliability v3", (V3 / f"{name}.jsonl",)))
    families["A"].append(Candidate("A_SSA_V2", "SSA v2", (PRIMARY / "A_SSA_V2.jsonl",)))
    references = {
        "PROPOSED_LLM": "MiniCPM",
        "PROPOSED": "Offline",
    }
    for name, version in references.items():
        families[name] = [Candidate(name, version, (PRIMARY / f"{name}.jsonl",))]

    hoare_sources = (PRIMARY / "HOAREPROMPT_OFFICIAL.jsonl", HOARE_RETRY / "HOAREPROMPT_OFFICIAL.jsonl")
    hoare_rows, hoare_attempts, hoare_recovered = merge_stages(
        hoare_sources[0], hoare_sources[1:], cohort,
    )
    families["C"].append(Candidate(
        "HOAREPROMPT_OFFICIAL", "HoarePrompt gốc + retry", hoare_sources,
        hoare_attempts, hoare_recovered,
    ))

    h_first = H_OFFICIAL / "SELF_REFINE_OFFICIAL.jsonl"
    h_retry = H_RETRY / h_first.name
    h_rows, h_attempts, h_recovered = merge_stages(h_first, (h_retry,), cohort)
    h_sources = [h_first, h_retry]
    h_state = "Hoàn tất"
    h_version = "Self-Refine gốc + retry + sửa parser"
    for folder in (H_REPAIR, H_FINAL):
        ids_file = folder / "program_ids.json"
        if not ids_file.exists():
            continue
        target = json.loads(ids_file.read_text())
        missing = {pid for pid, row in h_rows.items() if not row["n_verified"] + row["n_counterexample"]}
        if len(target) != len(missing) or set(target) != missing:
            raise ValueError(f"{folder}: retry list does not match the preceding missing verdicts")
        checkpoint = folder / h_first.name
        repaired = read_rows(checkpoint) if checkpoint.exists() else {}
        if not repaired.keys() <= missing:
            raise ValueError(f"{folder}: retry contains samples outside its retry list")
        decisive_before = decisive_count(h_rows)
        h_rows = merge_retries(h_rows, repaired)
        h_attempts += len(repaired)
        h_recovered += decisive_count(h_rows) - decisive_before
        if checkpoint.exists():
            h_sources.append(checkpoint)
        if folder == H_FINAL:
            h_version += " + retry cuối"
        if len(repaired) < len(target):
            h_state = f"Tạm tính: đã chạy {len(repaired)}/{len(target)} mẫu của lượt sửa H"
            break
    families["H"].append(Candidate(
        "SELF_REFINE_OFFICIAL", h_version, tuple(h_sources), h_attempts, h_recovered,
    ))

    with TemporaryDirectory() as temporary_path:
        directory = Path(temporary_path)
        for family in families.values():
            for candidate in family:
                rows = {"HOAREPROMPT_OFFICIAL": hoare_rows, "SELF_REFINE_OFFICIAL": h_rows}.get(candidate.key)
                if rows is None:
                    copy_candidate(candidate, directory, cohort)
                else:
                    (directory / f"{candidate.key}.jsonl").write_text(
                        "".join(json.dumps(rows[pid]) + "\n" for pid in ids)
                    )
        ground_truth = AUDITED_MANIFEST if AUDITED_MANIFEST.exists() else ROOT / "dataset" / "manifest.json"
        scores = score(directory, ids_path, ROOT / "dataset", ground_truth_path=ground_truth)

    def verdicts(candidate: Candidate) -> int:
        return sum(scores[candidate.key]["statuses"].get(status, 0) for status in DECISIVE)

    table = []
    for name, family in families.items():
        selected = max(family, key=lambda candidate: (scores[candidate.key]["correct"], verdicts(candidate)))
        result = scores[selected.key]
        correct = result["correct"]
        covered = verdicts(selected)
        state = h_state if name == "H" else "Hoàn tất"
        table.append({
            "baseline": name,
            "version": selected.version,
            "correct": correct,
            "total": len(cohort),
            "accuracy_pct": result["accuracy_pct"],
            "verdicts": covered,
            "coverage_pct": result["coverage_pct"],
            "wrong_verdicts": covered - correct,
            "no_verdict": len(cohort) - covered,
            "retry_attempts": selected.retry_attempts,
            "recovered_verdicts": selected.recovered_verdicts,
            "status": state,
            "ground_truth": str(ground_truth.relative_to(ROOT)),
            "sources": " | ".join(str(path.relative_to(ROOT)) for path in selected.sources),
        })

    with TemporaryDirectory(dir=OUTPUT.parent) as temporary_path:
        temporary = Path(temporary_path) / OUTPUT.name
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
        temporary.replace(OUTPUT)
    print(f"Wrote {len(table)} rows to {OUTPUT}; H: {h_state}")


if __name__ == "__main__":
    main()
