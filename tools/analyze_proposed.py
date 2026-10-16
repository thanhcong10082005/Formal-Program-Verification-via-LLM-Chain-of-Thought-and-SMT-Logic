"""
analyze_proposed.py - Phân tích kết quả Proposed Method.

Input:
  * results/PROPOSED_OFFLINE/*.json  (deterministic run)
  * results/AST_ANCHORED/*.json      (Phase 0 results, để so sánh)

Output:
  * results/PROPOSED_METRICS.json    (raw numbers)
  * In stdout: summary tables
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.classify import ProgramResult, StatusEnum

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS = PROJECT_ROOT / "results"
AST_ANCHORED_DIR = RESULTS / "AST_ANCHORED"
PROPOSED_DIR = RESULTS / "PROPOSED"


# Mirror GROUND_TRUTH from run_benchmark.py
GROUND_TRUTH: dict[str, str] = {
    "loop-simple/nested_1": "VERIFIED", "loop-simple/nested_1b": "VERIFIED",
    "loop-simple/nested_2": "VERIFIED", "loop-simple/nested_3": "VERIFIED",
    "loop-simple/nested_4": "VERIFIED", "loop-simple/nested_5": "VERIFIED",
    "loop-simple/nested_6": "VERIFIED", "loop-simple/deep-nested": "VERIFIED",
    "loop-invariants/const": "VERIFIED", "loop-invariants/eq1": "VERIFIED",
    "loop-invariants/eq2": "VERIFIED", "loop-invariants/even": "VERIFIED",
    "loop-invariants/odd": "VERIFIED", "loop-invariants/mod4": "VERIFIED",
    "loop-invariants/bin-suffix-5": "VERIFIED",
    "loop-invariants/linear-inequality-inv-a": "VERIFIED",
    "loop-invariants/linear-inequality-inv-b": "VERIFIED",
    "loop-invariants/linear-inequality-inv-c": "VERIFIED",
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
}


def _load_results(directory: Path) -> Dict[str, ProgramResult]:
    out: Dict[str, ProgramResult] = {}
    for p in sorted(directory.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            res = ProgramResult.model_validate(data)
            out[res.program_id] = res
        except Exception as exc:
            print(f"  warn: failed to load {p.name}: {exc}", file=sys.stderr)
    return out


def _gt_for(pid: str) -> str:
    """Lookup ground truth for a program_id; cruxeval excluded."""
    if pid.startswith("cruxeval__"):
        return ""
    # pid: sv-benchmarks-loops-py__loops-crafted-1__sumt2
    parts = pid.split("__")
    if len(parts) < 3:
        return ""
    rel = f"{parts[-2]}/{parts[-1]}"
    return GROUND_TRUTH.get(rel, "")


def _aggregate_metrics(results: Dict[str, ProgramResult]) -> dict:
    """Compute claim-level TP/FP/FN/TN + precision/recall/F1/FDR."""
    tp = fp = fn = tn = 0
    status_counter: Counter = Counter()
    n_ce = 0
    n_replay_true = 0
    n_ce_with_feedback = 0
    n_feedback_verified = 0
    n_feedback_iterations_total = 0
    feedback_resolutions: Counter = Counter()
    n_unknown_timeout = 0
    n_unsupported = 0
    n_translation_error = 0
    n_verified_by_feedback = 0
    for res in results.values():
        for cr in res.claim_results:
            status = cr.status.value
            status_counter[status] += 1
            if status == "UNKNOWN_TIMEOUT":
                n_unknown_timeout += 1
            if status == "UNSUPPORTED":
                n_unsupported += 1
            if status == "TRANSLATION_ERROR":
                n_translation_error += 1
            if status == "COUNTEREXAMPLE":
                n_ce += 1
                if cr.counterexample is not None:
                    if cr.counterexample.replay_verdict is True:
                        n_replay_true += 1
                    fb_res = cr.counterexample.feedback_resolution
                    fb_iters = cr.counterexample.feedback_iterations
                    if fb_iters is not None and fb_iters > 0:
                        n_ce_with_feedback += 1
                        n_feedback_iterations_total += fb_iters
                        if fb_res is not None:
                            feedback_resolutions[fb_res] += 1
                        if fb_res == "verified":
                            n_feedback_verified += 1
                            n_verified_by_feedback += 1
            # Ground-truth scoring
            gt = _gt_for(res.program_id)
            if gt == "":
                continue  # skip
            verdict_positive = status == "VERIFIED"
            gt_positive = gt == "VERIFIED"
            if verdict_positive and gt_positive:
                tp += 1
            elif verdict_positive and not gt_positive:
                fp += 1
            elif not verdict_positive and gt_positive:
                fn += 1
            else:
                tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    fdr = fp / (tp + fp) if (tp + fp) else 0.0
    accuracy = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) else 0.0
    # FSR: % of CE refined to VERIFIED
    fsr = n_feedback_verified / n_ce_with_feedback if n_ce_with_feedback else 0.0
    # HRR: hallucination recovery rate (placeholder = fsr when no LLM)
    hrr = fsr
    # CRVR
    crvr = n_replay_true / n_ce if n_ce else 0.0
    # Hallucination Rate
    n_h = n_unsupported + n_translation_error
    n_total = sum(status_counter.values())
    hr = n_h / n_total if n_total else 0.0
    return {
        "n_claims": n_total,
        "n_verified": status_counter.get("VERIFIED", 0),
        "n_counterexample": n_ce,
        "n_unknown_timeout": n_unknown_timeout,
        "n_unsupported": n_unsupported,
        "n_translation_error": n_translation_error,
        "n_unreachable": status_counter.get("UNREACHABLE", 0),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fdr": fdr,
        "accuracy": accuracy,
        "n_ce": n_ce,
        "n_replay_true": n_replay_true,
        "crvr": crvr,
        "n_ce_with_feedback": n_ce_with_feedback,
        "n_feedback_verified": n_feedback_verified,
        "n_feedback_iterations_total": n_feedback_iterations_total,
        "avg_feedback_iterations": (
            n_feedback_iterations_total / n_ce_with_feedback
            if n_ce_with_feedback else 0.0
        ),
        "fsr": fsr,
        "hrr": hrr,
        "hallucination_rate": hr,
        "feedback_resolutions": dict(feedback_resolutions),
        "status_counter": dict(status_counter),
        "n_verified_by_feedback": n_verified_by_feedback,
    }


def main() -> int:
    print("=" * 72)
    print("PROPOSED METHOD - E2E BENCHMARK ANALYSIS")
    print("=" * 72)
    print()
    print(f"Loading AST_ANCHORED  (Phase 0 baseline) from {AST_ANCHORED_DIR}")
    phase0 = _load_results(AST_ANCHORED_DIR)
    print(f"  loaded {len(phase0)} programs")
    print()
    print(f"Loading PROPOSED       (Phase new)         from {PROPOSED_DIR}")
    proposed = _load_results(PROPOSED_DIR)
    print(f"  loaded {len(proposed)} programs")
    print()
    m0 = _aggregate_metrics(phase0)
    m1 = _aggregate_metrics(proposed)
    # Save raw numbers
    out = {"phase0_AST_ANCHORED": m0, "PROPOSED": m1}
    out_path = RESULTS / "PROPOSED_METRICS.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved raw numbers to {out_path}")
    # Print summary
    print()
    print("=" * 72)
    print("BẢNG SO SÁNH CLAIM-LEVEL")
    print("=" * 72)
    rows = [
        ("Total claims",     m0["n_claims"], m1["n_claims"]),
        ("VERIFIED",         m0["n_verified"], m1["n_verified"]),
        ("COUNTEREXAMPLE",   m0["n_counterexample"], m1["n_counterexample"]),
        ("UNKNOWN_TIMEOUT",  m0["n_unknown_timeout"], m1["n_unknown_timeout"]),
        ("UNSUPPORTED",      m0["n_unsupported"], m1["n_unsupported"]),
        ("TRANSLATION_ERROR",m0["n_translation_error"], m1["n_translation_error"]),
    ]
    print(f"{'Metric':<22} {'Phase 0':>10} {'Proposed':>10} {'Delta':>10}")
    for name, v0, v1 in rows:
        delta = v1 - v0
        sign = "+" if delta > 0 else ""
        print(f"{name:<22} {v0:>10} {v1:>10} {sign}{delta:>9}")
    print()
    print("=" * 72)
    print("VERDICT MATRIX (vs SV-COMP ground truth)")
    print("=" * 72)
    print(f"{'':22} {'Phase 0':>10} {'Proposed':>10}")
    for k in ("tp", "fp", "fn", "tn"):
        print(f"{k.upper():<22} {m0[k]:>10} {m1[k]:>10}")
    print()
    print(f"{'Precision':<22} {m0['precision']:>10.3f} {m1['precision']:>10.3f}")
    print(f"{'Recall':<22} {m0['recall']:>10.3f} {m1['recall']:>10.3f}")
    print(f"{'F1':<22} {m0['f1']:>10.3f} {m1['f1']:>10.3f}")
    print(f"{'FDR':<22} {m0['fdr']:>10.3f} {m1['fdr']:>10.3f}")
    print(f"{'Accuracy':<22} {m0['accuracy']:>10.3f} {m1['accuracy']:>10.3f}")
    print()
    print("=" * 72)
    print("METRIC MỚI (Phase mới)")
    print("=" * 72)
    print(f"  CRVR                    = {m1['crvr']:.3f}  ({m1['n_replay_true']}/{m1['n_ce']})")
    print(f"  FSR (Feedback Success)  = {m1['fsr']:.3f}  ({m1['n_feedback_verified']}/{m1['n_ce_with_feedback']})")
    print(f"  HRR (Hallucination Rec) = {m1['hrr']:.3f}")
    print(f"  Hallucination Rate      = {m1['hallucination_rate']:.3f}")
    print(f"  Verified-by-Feedback    = {m1['n_verified_by_feedback']}")
    print()
    print("=" * 72)
    print("FEEDBACK RESOLUTION BREAKDOWN")
    print("=" * 72)
    for k, v in sorted(m1["feedback_resolutions"].items(), key=lambda x: -x[1]):
        print(f"  {k:<24} {v}")
    if not m1["feedback_resolutions"]:
        print("  (no feedback rounds executed — offline/deterministic mode)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
