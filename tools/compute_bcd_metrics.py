"""
compute_bcd_metrics.py - Compute metrics for the 3 baselines on the
assertion-bearing subset (13 programs, SV-COMP only).

Reads results/BASELINE_{B,C,D}/*.json, keeps only the recent 13 files
matching the assert-bearing subset, and computes F1/Accuracy/Precision/
Recall/FDR plus the new error taxonomy from IMPROVEMENT 1.4.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

from astverifier.baselines.error_taxonomy import (
    categorize_program,
    summarize,
)
from astverifier.classify import (
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
)

ROOT = Path("results")

# Assertion-bearing subset (13 programs, matches the --assert-subset filter)
ASSERT_SUBSET = {
    f"sv-benchmarks-loops-py__{p}" for p in {
        "loop-invariants__eq2", "loops-crafted-1__Mono3_1",
        "loops-crafted-1__Mono4_1", "loops-crafted-1__loopv1", "loops-crafted-1__loopv3",
        "loops-crafted-1__mono-crafted_12", "loops-crafted-1__mono-crafted_9",
        "loops-crafted-1__nested3-1", "loops-crafted-1__nested3-1_abstracted",
        "loops-crafted-1__nested3-2", "loops-crafted-1__sumt2",
        "loop-simple__nested_1", "loop-simple__nested_1b",
    }
}

# Ground truth (from run_benchmark.py GROUND_TRUTH)
GROUND_TRUTH = {
    "loop-simple/nested_1": "VERIFIED",
    "loop-simple/nested_1b": "VERIFIED",
    "loop-invariants/eq2": "VERIFIED",
    "loops-crafted-1/loopv1": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv3": "COUNTEREXAMPLE",
    "loops-crafted-1/mono-crafted_12": "VERIFIED",
    "loops-crafted-1/mono-crafted_9": "VERIFIED",
    "loops-crafted-1/Mono3_1": "VERIFIED",
    "loops-crafted-1/Mono4_1": "VERIFIED",
    "loops-crafted-1/nested3-1": "VERIFIED",
    "loops-crafted-1/nested3-1_abstracted": "VERIFIED",
    "loops-crafted-1/nested3-2": "VERIFIED",
    "loops-crafted-1/sumt2": "VERIFIED",
}


def _gt_for(pid: str) -> str:
    """Map program_id → ground truth."""
    # Direct match (sv-benchmarks-loops-py__loops-crafted-1__loopv1)
    if pid in GROUND_TRUTH:
        return GROUND_TRUTH[pid]
    # Short match (loops-crafted-1__loopv1)
    short = pid.replace("sv-benchmarks-loops-py__", "").replace("__", "/")
    if short in GROUND_TRUTH:
        return GROUND_TRUTH[short]
    return ""


def _load_recent(baseline: str, max_age_min: int = 60, only_assert_subset: bool = True):
    """Load only recently-written JSON results (within max_age_min minutes).

    When ``only_assert_subset`` is True we keep only files whose stem is in
    :data:`ASSERT_SUBSET` (the 13 programs that actually have an ``assert``
    statement in their source).  This filters out stale files left from
    earlier benchmark runs.
    """
    d = ROOT / baseline
    out = []
    now = datetime.datetime.now()
    for f in sorted(d.glob("*.json")):
        if only_assert_subset and f.stem not in ASSERT_SUBSET:
            continue
        age = (now - datetime.datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 60
        if age > max_age_min:
            continue
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            pass
    out.sort(key=lambda x: x[1].get("program_id", x[0].stem))
    return out


def _claim_results_from_json(crs_json: list) -> list[ClaimResult]:
    """Convert raw JSON claim list → ClaimResult list."""
    out = []
    for cr in crs_json:
        try:
            anchor_j = cr.get("anchor", {})
            ce_j = cr.get("counterexample")
            ce_info = None
            if ce_j:
                ce_info = CounterexampleInfo(
                    z3_model=ce_j.get("z3_model", {}),
                    replay_verdict=ce_j.get("replay_verdict"),
                    replay_output=ce_j.get("replay_output"),
                    replay_diff=ce_j.get("replay_diff"),
                )
            out.append(ClaimResult(
                claim_id=cr.get("claim_id", ""),
                claim_text=cr.get("claim_text", ""),
                status=StatusEnum(cr["status"]),
                anchor=AnchorInfo(
                    node_ids=anchor_j.get("node_ids", []),
                    source_lines=anchor_j.get("source_lines", []),
                    has_grounding=anchor_j.get("has_grounding", False),
                ),
                counterexample=ce_info,
                reason=cr.get("reason", ""),
                explanation=cr.get("explanation", ""),
            ))
        except Exception:
            pass
    return out


def _compute_metrics(results: list, baseline: str) -> dict:
    """
    Per-claim metrics (TP/FP/FN/TN) against SV-COMP ground truth.

    Logic:
      status == VERIFIED  & GT == VERIFIED   → TP
      status == VERIFIED  & GT == CE        → FP  (over-verification)
      status == CE        & GT == VERIFIED   → FP  (hallucinated CE)
      status == CE        & GT == CE        → TP
      status == UNKNOWN   & GT == VERIFIED   → FN  (missed proof)
      status == UNKNOWN   & GT == CE        → FN  (missed counterexample)
    """
    tp = fp = fn_ = tn = 0
    matched_claims = 0
    crvr_ok = crvr_total = 0
    per_prog = []

    for f, data in results:
        pid = data.get("program_id", f.stem)
        gt = _gt_for(pid)
        if not gt:  # skip programs without ground truth
            continue
        crs = _claim_results_from_json(data.get("claim_results", []))
        if not crs:
            continue
        matched_claims += len(crs)
        p_tp = p_fp = p_fn = p_tn = 0
        for cr in crs:
            s = cr.status
            if s == StatusEnum.VERIFIED:
                if gt == "VERIFIED":
                    tp += 1; p_tp += 1
                else:
                    fp += 1; p_fp += 1
            elif s == StatusEnum.COUNTEREXAMPLE:
                if gt == "COUNTEREXAMPLE":
                    tp += 1; p_tp += 1
                    ce = cr.counterexample
                    if ce and ce.replay_verdict is True:
                        crvr_ok += 1
                    if ce:
                        crvr_total += 1
                else:
                    fp += 1; p_fp += 1  # hallucinated CE
            else:  # UNKNOWN_TIMEOUT / UNREACHABLE / etc.
                fn_ += 1; p_fn += 1
        per_prog.append({
            "pid": pid, "gt": gt,
            "tp": p_tp, "fp": p_fp, "fn": p_fn,
            "n": len(crs),
        })

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn_) if (tp + fn_) > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    acc = (tp + tn) / matched_claims if matched_claims > 0 else 0.0
    fdr = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    crvr = crvr_ok / crvr_total if crvr_total > 0 else None

    return dict(
        baseline=baseline, n_programs=len(results), n_claims=matched_claims,
        tp=tp, fp=fp, fn=fn_, tn=tn,
        precision=prec, recall=rec, f1=f1,
        accuracy=acc, fdr=fdr, crvr=crvr,
        per_program=per_prog,
    )


def _compute_taxonomy(results: list, baseline: str) -> dict:
    """Error taxonomy per IMPROVEMENT 1.4."""
    all_counts = {}
    for f, data in results:
        pid = data.get("program_id", f.stem)
        gt = _gt_for(pid)
        crs = _claim_results_from_json(data.get("claim_results", []))
        if not crs:
            continue
        pr = ProgramResult(
            program_id=pid, source_path=data.get("source_path", ""),
            baseline=baseline, claim_results=crs,
        )
        for cat in categorize_program(pr, gt):
            all_counts[cat] = all_counts.get(cat, 0) + 1
    return all_counts


def main():
    print("=" * 80)
    print("Baseline B / C / D — Assertion-bearing subset metrics")
    print("(13 SV-COMP programs, --use-llm, gemini-3.5-flash-lite)")
    print("=" * 80)

    all_metrics = []
    all_taxonomy = {}

    for baseline in ("BASELINE_B", "BASELINE_C", "BASELINE_D"):
        results = _load_recent(baseline, max_age_min=60)
        print(f"\n{baseline}: loaded {len(results)} recent results")
        if not results:
            print("  (no recent results found — run benchmark first)")
            continue
        m = _compute_metrics(results, baseline)
        all_metrics.append(m)
        all_taxonomy[baseline] = _compute_taxonomy(results, baseline)

    # Summary table
    print("\n" + "=" * 80)
    print("METRICS")
    print("=" * 80)
    hdr = (f"{'Baseline':<14} {'Prog':>5} {'Clm':>5} "
           f"{'TP':>4} {'FP':>4} {'FN':>4} {'Prec':>7} "
           f"{'Rec':>7} {'F1':>7} {'FDR':>7} {'Acc':>7} {'CRVR':>7}")
    print(hdr)
    print("-" * len(hdr))
    for m in all_metrics:
        crvr_s = f"{m['crvr']:.3f}" if m['crvr'] is not None else "   n/a"
        print(
            f"{m['baseline']:<14} "
            f"{m['n_programs']:>5} {m['n_claims']:>5} "
            f"{m['tp']:>4} {m['fp']:>4} {m['fn']:>4} "
            f"{m['precision']:>7.3f} {m['recall']:>7.3f} "
            f"{m['f1']:>7.3f} {m['fdr']:>7.3f} {m['accuracy']:>7.3f} "
            f"{crvr_s:>7}"
        )

    # Per-program breakdown
    print("\n" + "=" * 80)
    print("PER-PROGRAM")
    print("=" * 80)
    for m in all_metrics:
        print(f"\n{m['baseline']} (F1={m['f1']:.3f}):")
        for p in sorted(m["per_program"], key=lambda x: x["pid"]):
            print(f"  {p['pid']:<55} GT={p['gt']:14}  "
                  f"TP={p['tp']} FP={p['fp']} FN={p['fn']} (n={p['n']})")

    # Error taxonomy
    print("\n" + "=" * 80)
    print("ERROR TAXONOMY (IMPROVEMENT 1.4)")
    print("=" * 80)
    for baseline, counts in all_taxonomy.items():
        print(f"\n{baseline}:")
        if not counts:
            print("  (no taxonomy data)")
        for cat in sorted(counts.keys()):
            if cat.startswith("__"):
                continue
            print(f"  {cat:<35s}: {counts[cat]:3d}")


if __name__ == "__main__":
    main()
