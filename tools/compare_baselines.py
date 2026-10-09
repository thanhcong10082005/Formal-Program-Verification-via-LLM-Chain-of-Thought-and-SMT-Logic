"""
compare_baselines.py - Aggregate the per-program JSON outputs from both
baselines, compute the 4 metrics, and emit a Markdown summary to
results/summary.md.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.classify import ProgramResult, StatusEnum
from astverifier.metrics import aggregate, load_ground_truth


RESULT_ROOT = (Path(__file__).resolve().parent.parent / "results")
GT_FILE = Path(__file__).resolve().parent / "ground_truth.json"


# Hand-curated ground truth for SV-COMP loops (loaded from run_benchmark.py too).
GROUND_TRUTH: dict[str, str] = {
    # loop-simple
    "loop-simple/nested_1": "VERIFIED", "loop-simple/nested_1b": "VERIFIED",
    "loop-simple/nested_2": "VERIFIED", "loop-simple/nested_3": "VERIFIED",
    "loop-simple/nested_4": "VERIFIED", "loop-simple/nested_5": "VERIFIED",
    "loop-simple/nested_6": "VERIFIED", "loop-simple/deep-nested": "VERIFIED",
    # loop-invariants
    "loop-invariants/const": "VERIFIED", "loop-invariants/eq1": "VERIFIED",
    "loop-invariants/eq2": "VERIFIED", "loop-invariants/even": "VERIFIED",
    "loop-invariants/odd": "VERIFIED", "loop-invariants/mod4": "VERIFIED",
    "loop-invariants/bin-suffix-5": "VERIFIED",
    "loop-invariants/linear-inequality-inv-a": "VERIFIED",
    "loop-invariants/linear-inequality-inv-b": "VERIFIED",
    "loop-invariants/linear-inequality-inv-c": "VERIFIED",
    # loops-crafted-1: many are COUNTEREXAMPLE
    "loops-crafted-1/iftelse": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de20": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de31": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de32": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de41": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de42": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de51": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de52": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de61": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de62": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv1": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv2": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv3": "COUNTEREXAMPLE",
    "loops-crafted-1/mono-crafted_1": "VERIFIED",
    "loops-crafted-1/mono-crafted_3": "VERIFIED",
    "loops-crafted-1/mono-crafted_6": "VERIFIED",
    "loops-crafted-1/mono-crafted_7": "VERIFIED",
    "loops-crafted-1/mono-crafted_8": "VERIFIED",
    "loops-crafted-1/mono-crafted_9": "VERIFIED",
    "loops-crafted-1/mono-crafted_10": "VERIFIED",
    "loops-crafted-1/mono-crafted_11": "VERIFIED",
    "loops-crafted-1/mono-crafted_12": "VERIFIED",
    "loops-crafted-1/mono-crafted_13": "VERIFIED",
    "loops-crafted-1/mono-crafted_14": "VERIFIED",
    "loops-crafted-1/Mono1_1-1": "VERIFIED",
    "loops-crafted-1/Mono1_1-2": "VERIFIED",
    "loops-crafted-1/Mono3_1": "VERIFIED",
    "loops-crafted-1/Mono4_1": "VERIFIED",
    "loops-crafted-1/Mono5_1": "VERIFIED",
    "loops-crafted-1/Mono6_1": "VERIFIED",
    "loops-crafted-1/nested3-1": "VERIFIED",
    "loops-crafted-1/nested3-1_abstracted": "VERIFIED",
    "loops-crafted-1/nested3-2": "VERIFIED",
    "loops-crafted-1/nested3-2_abstracted": "VERIFIED",
    "loops-crafted-1/nested5-1": "VERIFIED",
    "loops-crafted-1/nested5-2": "VERIFIED",
    "loops-crafted-1/nested_delay_nd": "VERIFIED",
    "loops-crafted-1/nested_delay_notd2": "VERIFIED",
    "loops-crafted-1/net_reset": "VERIFIED",
    "loops-crafted-1/sumt2": "VERIFIED",
    "loops-crafted-1/sumt3": "VERIFIED",
    "loops-crafted-1/sumt4": "VERIFIED",
    "loops-crafted-1/sumt5": "VERIFIED",
    "loops-crafted-1/sumt6": "VERIFIED",
    "loops-crafted-1/sumt7": "VERIFIED",
    "loops-crafted-1/sumt8": "VERIFIED",
    "loops-crafted-1/sumt9": "VERIFIED",
    "loops-crafted-1/sum_by_3": "VERIFIED",
    "loops-crafted-1/sum_by_3_abstracted": "VERIFIED",
    "loops-crafted-1/sum_natnum": "VERIFIED",
    "loops-crafted-1/theatreSquare": "VERIFIED",
    "loops-crafted-1/vnew1": "VERIFIED",
    "loops-crafted-1/vnew2": "VERIFIED",
    "loops-crafted-1/watermelon": "VERIFIED",
}


def _load_results(baseline_dir: Path) -> List[ProgramResult]:
    out: List[ProgramResult] = []
    for f in sorted(baseline_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(ProgramResult(**data))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
    return out


def _status_table(results: List[ProgramResult]) -> str:
    by_status = Counter()
    by_status_accepted = Counter()  # claim-level counts among non-rejected
    for r in results:
        if r.rejected_by_subset:
            by_status["REJECTED"] += 1
        elif r.claim_results:
            by_status_accepted.update(c.status for c in r.claim_results)
        else:
            by_status["NO_CLAIMS"] += 1
    by_status_combined = Counter(by_status_accepted)
    for k, v in by_status.items():
        by_status_combined[k] += v
    rows = [
        "| Status | Count | % |",
        "|--------|------:|--:|",
    ]
    total = sum(by_status_combined.values()) or 1
    for status, n in sorted(by_status_combined.items(), key=lambda x: -x[1]):
        if status is StatusEnum:
            continue
        if hasattr(status, "value"):
            name = status.value
        else:
            name = str(status)
        rows.append(f"| {name} | {n} | {n/total*100:.1f}% |")
    return "\n".join(rows)


def _verdict_matrix(results: List[ProgramResult], gt: Dict[str, str]) -> Tuple[int, int, int, int, int]:
    """Return claim-level (TP, FP, FN, TN, total) for known programs.

    Normalize program_id: `sv-benchmarks-loops-py__loop-simple__nested_1`
    becomes `loop-simple/nested_1`.
    """
    tp = fp = fn = tn = 0
    matched = 0
    for r in results:
        norm = _normalize_program_id(r.program_id)
        if norm in gt:
            expected = gt[norm]
            for claim in r.claim_results:
                matched += 1
                verdict = claim.status.value
                if verdict == "VERIFIED" and expected == "VERIFIED":
                    tp += 1
                elif verdict == "VERIFIED" and expected != "VERIFIED":
                    fp += 1
                elif verdict != "VERIFIED" and expected == "VERIFIED":
                    fn += 1
                else:
                    tn += 1
    return tp, fp, fn, tn, matched


def _normalize_program_id(program_id: str) -> str:
    """Map persisted benchmark IDs to the keys used by ground truth."""
    norm = program_id.replace("__", "/")
    prefix = "sv-benchmarks-loops-py/"
    if norm.startswith(prefix):
        norm = norm[len(prefix):]
    return norm


def _fdr(tp: int, fp: int) -> float | None:
    if tp + fp == 0:
        return None
    return fp / (tp + fp)


def main() -> int:
    gt = GROUND_TRUTH
    GT_FILE.write_text(json.dumps(gt, indent=2), encoding="utf-8")

    anchored = _load_results(RESULT_ROOT / "AST_ANCHORED")
    unanchored = _load_results(RESULT_ROOT / "UNANCHORED")
    print(f"Loaded {len(anchored)} anchored, {len(unanchored)} unanchored results")

    # --- Per-baseline status counts ----------------------------------------
    anchored_table = _status_table(anchored)
    unanchored_table = _status_table(unanchored)

    # --- FDR via ground truth ---------------------------------------------
    a_tp, a_fp, a_fn, a_tn, a_total = _verdict_matrix(anchored, gt)
    u_tp, u_fp, u_fn, u_tn, u_total = _verdict_matrix(unanchored, gt)

    a_fdr = _fdr(a_tp, a_fp)
    u_fdr = _fdr(u_tp, u_fp)

    # --- Aggregate metrics -------------------------------------------------
    a_metrics = aggregate(anchored, gt)
    u_metrics = aggregate(unanchored, gt)

    # --- Build Markdown ----------------------------------------------------
    md: List[str] = []
    md.append("# Báo cáo thực nghiệm: AST-Anchored CoT Verification\n")
    md.append("Framework kiểm tra các chương trình đã qua subset gate với **hai chế độ baseline**.")
    md.append("Các bảng trạng thái và FDR bên dưới là **claim-level**; dữ liệu persisted chỉ phản ánh lần chạy đã được lưu.\n")
    md.append("- `AST_ANCHORED`: exact anchor binding, target-node reachability và target-state validity.")
    md.append("- `UNANCHORED`: free-predicate validity; không dùng NodeId, program state, executor hoặc reachability.\n")

    md.append("## 1. So sánh đối chứng — Bảng chính\n")
    md.append("| Metric | Công thức | AST_ANCHORED | UNANCHORED | Chênh lệch |")
    md.append("|--------|-----------|--------------|------------|-----------|")
    # FDR
    if a_fdr is not None and u_fdr is not None:
        md.append(f"| False Discovery Rate (FDR) | $FP/(TP+FP)$ | **{a_fdr:.3f}** | {u_fdr:.3f} | {(a_fdr or 0)-(u_fdr or 0):+.3f} |")
    elif a_fdr is not None:
        md.append(f"| False Discovery Rate (FDR) | $FP/(TP+FP)$ | **{a_fdr:.3f}** | n/a | - |")
    elif u_fdr is not None:
        md.append(f"| False Discovery Rate (FDR) | $FP/(TP+FP)$ | **n/a** | {u_fdr:.3f} | - |")
    else:
        md.append(f"| False Discovery Rate (FDR) | $FP/(TP+FP)$ | n/a | n/a | - |")
    # Token consumption
    a_in = a_metrics.token_avg_in
    u_in = u_metrics.token_avg_in
    a_out = a_metrics.token_avg_out
    u_out = u_metrics.token_avg_out
    md.append(f"| Avg Tokens (in) | $T_{{in}}$ | {a_in:.1f} | {u_in:.1f} | {a_in - u_in:+.1f} |")
    md.append(f"| Avg Tokens (out) | $T_{{out}}$ | {a_out:.1f} | {u_out:.1f} | {a_out - u_out:+.1f} |")
    # Cost
    a_cost = a_metrics.cost_avg_usd
    u_cost = u_metrics.cost_avg_usd
    md.append(f"| Avg Cost (USD) | $C_{{usd}}$ | {a_cost:.6f} | {u_cost:.6f} | {a_cost - u_cost:+.6f} |")
    # Hallucination
    md.append(f"| Hallucination Rate (HR) | $H = \\frac{{N_{{UNSUP}} + N_{{TE}}}}{{N_{{claims}}}}$ | **{a_metrics.hr:.3f}** | {u_metrics.hr:.3f} | {a_metrics.hr - u_metrics.hr:+.3f} |")
    # CRVR
    if a_metrics.crvr is not None and u_metrics.crvr is not None:
        md.append(f"| Counterexample Replay Validity (CRVR) | $1 - \\frac{{\\text{{card}}(S.V.)}}{{\\text{{card}}(CE_{{Z3}})}}$ | **{a_metrics.crvr:.3f}** | {u_metrics.crvr:.3f} | {a_metrics.crvr - u_metrics.crvr:+.3f} |")
    else:
        md.append(f"| Counterexample Replay Validity (CRVR) | $1 - \\frac{{\\text{{card}}(S.V.)}}{{\\text{{card}}(CE_{{Z3}})}}$ | n/a (no counterexample) | n/a | - |")

    md.append("")
    md.append("Trong đó:")
    md.append("- $T_{in}, T_{out}$: tokens in/out from the recorded run; deterministic runs report zero.")
    md.append("- $H$: claim bị reject vì UNSUPPORTED + TRANSLATION_ERROR.")
    md.append("- $S.V.$: Soundness Violation — counterexample từ Z3 không reproduce trên CPython.")
    md.append("")

    # --- Verdict matrix ---------------------------------------------------
    md.append("## 2. Verdict Matrix (against SV-COMP ground truth)\n")
    md.append("Chỉ các claims thuộc programs trong ground-truth được tính. Tổng cộng:")
    md.append(f"`{a_total}` claims.\n")
    md.append("| Baseline | TP | FP | FN | TN | FDR |")
    md.append("|----------|---:|---:|---:|---:|----:|")
    a_fdr_str = f"{a_fdr:.3f}" if a_fdr is not None else "n/a"
    u_fdr_str = f"{u_fdr:.3f}" if u_fdr is not None else "n/a"
    md.append(f"| AST_ANCHORED | {a_tp} | {a_fp} | {a_fn} | {a_tn} | **{a_fdr_str}** |")
    md.append(f"| UNANCHORED   | {u_tp} | {u_fp} | {u_fn} | {u_tn} | {u_fdr_str} |")
    md.append("")
    md.append("**Diễn giải:**")
    md.append(f"- AST_ANCHORED: {a_tp + a_fp} VERIFIED verdicts ({a_tp} TP, {a_fp} FP) → FDR = {a_fdr_str}.")
    md.append(f"- UNANCHORED: {u_tp + u_fp} VERIFIED verdicts ({u_tp} TP, {u_fp} FP) → FDR = {u_fdr_str}.")
    md.append("")

    # --- Status breakdown -------------------------------------------------
    md.append("## 3. Phân bố trạng thái (claim-level)\n")
    md.append("### AST_ANCHORED\n")
    md.append(anchored_table)
    md.append("\n### UNANCHORED\n")
    md.append(unanchored_table)
    md.append("")

    # --- Program-level summary -------------------------------------------
    md.append("## 4. Program-level Coverage\n")
    a_passed = sum(1 for r in anchored if not r.rejected_by_subset)
    u_passed = sum(1 for r in unanchored if not r.rejected_by_subset)
    md.append(f"- Tổng programs: {len(anchored)} (mỗi baseline)")
    md.append(f"- Subset pass rate (AST_ANCHORED): {a_passed}/{len(anchored)} ({a_passed/len(anchored)*100:.1f}%)")
    md.append(f"- Subset pass rate (UNANCHORED): {u_passed}/{len(unanchored)} ({u_passed/len(unanchored)*100:.1f}%)")
    md.append(f"- Claims (anchored): {a_metrics.n_claims}")
    md.append(f"- Claims (unanchored): {u_metrics.n_claims}")
    md.append("")

    # --- Per-program details (AST_ANCHORED) ------------------------------
    md.append("## 5. Per-program verdicts (AST_ANCHORED, accepted subset)\n")
    md.append("| Program | Claims | V | CE | UNREACH | UNSUP | TE |")
    md.append("|---------|-------:|--:|---:|--------:|------:|---:|")
    for r in sorted(anchored, key=lambda r: r.program_id):
        if r.rejected_by_subset:
            continue
        short = r.program_id.replace("sv-benchmarks-loops-py__", "").replace("__", "/")
        md.append(
            f"| `{short}` | {r.n_claims} | {r.n_verified} | {r.n_counterexample} | {r.n_unreachable} | {r.n_unsupported} | {r.n_translation_error} |"
        )
    md.append("")

    # --- Counterexample details ------------------------------------------
    md.append("## 6. Counterexample Replay (AST_ANCHORED)\n")
    md.append("| Claim | Model bindings | Replay verdict | Replay output |")
    md.append("|-------|----------------|----------------|---------------|")
    for r in sorted(anchored, key=lambda r: r.program_id):
        for cr in r.claim_results:
            if cr.status.value != "COUNTEREXAMPLE" or not cr.counterexample:
                continue
            ce = cr.counterexample
            bindings = ", ".join(f"{k}={v}" for k, v in list(ce.z3_model.items())[:5])
            verdict = ce.replay_verdict
            if verdict is True:
                vstr = "REPRODUCED"
            elif verdict is False:
                vstr = "NOT_REPRODUCED"
            else:
                vstr = "N/A"
            out = (ce.replay_output or "")[:60]
            md.append(f"| `{cr.claim_id}` | {bindings} | {vstr} | {out} |")
    md.append("")

    # --- Notes -----------------------------------------------------------
    md.append("## 7. Ghi chú thực nghiệm\n")
    md.append("1. **LLM usage is explicit**: `run_benchmark.py` is deterministic by default; `--use-llm` is required for Gemini claim generation. Token totals are copied to both baseline records for the same LLM call.")
    md.append("2. **Claim-level accounting**: multi-claim programs contribute every `claim_result` to status counts and FDR; rejected programs are reported separately.")
    md.append("3. **Soundness boundary**: bounded loop/control-flow modeling is reported as `UNKNOWN_TIMEOUT` when path coverage is incomplete; `VERIFIED` is reserved for complete bounded paths.")
    md.append("4. **Replay boundary**: a replay is reproduced only when CPython reaches the target line and evaluates the target claim as false. A normal return alone is not confirmation.")
    md.append("5. **Subset boundary**: nonlinear multiplication and variable-divisor floor division/modulo are rejected; constant-coefficient arithmetic remains eligible.")
    md.append("6. **Ground truth labels**: based on the curated SV-COMP labels in `ground_truth.json`; they are not a proof of the symbolic approximation.")
    md.append("")

    # --- File map -------------------------------------------------------
    md.append("## 8. Cấu trúc sản phẩm\n")
    md.append("```")
    md.append("prototype/")
    md.append("├── src/astverifier/    # Mã nguồn framework")
    md.append("│   ├── subset.py       # QF-LIA whitelist gate")
    md.append("│   ├── nodes.py        # NodeId registry")
    md.append("│   ├── classify.py     # 6-status Pydantic schemas")
    md.append("│   ├── executor.py     # Trusted symbolic executor")
    md.append("│   ├── binding.py      # Anchor binding gate α(c)")
    md.append("│   ├── obligations.py  # Two-query Z3 protocol")
    md.append("│   ├── replay.py       # CPython subprocess counterexample replay")
    md.append("│   ├── pipeline.py     # End-to-end orchestrator")
    md.append("│   └── metrics.py      # FDR / Tokens / HR / CRVR aggregation")
    md.append("├── tools/")
    md.append("│   ├── translate_svcomp.py   # C → Python translator")
    md.append("│   ├── run_svcomp_translate.py")
    md.append("│   ├── run_benchmark.py      # Phase-4 runner")
    md.append("│   └── compare_baselines.py  # This report")
    md.append("└── results/")
    md.append("    ├── AST_ANCHORED/*.json  # per-program verdicts")
    md.append("    ├── UNANCHORED/*.json")
    md.append("    └── summary.md           # this file")
    md.append("```")
    md.append("")

    out = "\n".join(md)
    (RESULT_ROOT / "summary.md").write_text(out, encoding="utf-8")
    print(f"Wrote {RESULT_ROOT / 'summary.md'} ({len(out)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
