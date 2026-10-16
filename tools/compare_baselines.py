"""
compare_baselines.py - Aggregate results from 5 baselines and emit a Markdown summary.

Baselines:
  AST_ANCHORED  : Proposed Method (CoT + Z3 + Anchor binding)
  UNANCHORED    : Baseline A / SatLM-style Direct Formalization
  BASELINE_B    : Baseline B / Execution Grounding (Thakur et al. 2026)
  BASELINE_C    : Baseline C / Structural Reasoning (HoarePrompt, ICSE 2026)
  BASELINE_D    : Baseline D / LLM-as-Judge (Zero-shot CoT, Wei et al. 2022)

Usage:
  python tools/compare_baselines.py [--subset 12] [--out summary.md]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.classify import ProgramResult, StatusEnum
from astverifier.metrics import aggregate


RESULT_ROOT = Path(__file__).resolve().parent.parent / "results"
TOOLS_ROOT = Path(__file__).resolve().parent

BASELINES = ["AST_ANCHORED", "UNANCHORED", "BASELINE_B", "BASELINE_C", "BASELINE_D"]
BASELINE_LABELS: Dict[str, str] = {
    "AST_ANCHORED": "**Proposed Method** (CoT + Z3)",
    "UNANCHORED": "Baseline A (SatLM-style)",
    "BASELINE_B": "Baseline B (Execution Grounding)",
    "BASELINE_C": "Baseline C (Structural Reasoning)",
    "BASELINE_D": "Baseline D (LLM-as-Judge)",
}


# -------------------------------------------------------------------
# Ground truth (SV-COMP loops + CRUXEval)
# -------------------------------------------------------------------

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
    # loops-crafted-1: a few are COUNTEREXAMPLE
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

# 12-program QF-LIA subset used for fair 5-way comparison
SUBSET_12: set[str] = {
    "cruxeval/sample_51",
    "loop-invariants/eq2",
    "loops-crafted-1/Mono3_1",
    "loops-crafted-1/Mono4_1",
    "loops-crafted-1/loopv1",
    "loops-crafted-1/mono-crafted_12",
    "loops-crafted-1/mono-crafted_9",
    "loops-crafted-1/nested3-1",
    "loops-crafted-1/nested3-1_abstracted",
    "loops-crafted-1/nested3-2",
    "loops-crafted-1/net_reset",
    "loops-crafted-1/sumt2",
}


# -------------------------------------------------------------------
# Load results
# -------------------------------------------------------------------

def _load_results(baseline_dir: Path) -> List[ProgramResult]:
    out: List[ProgramResult] = []
    for f in sorted(baseline_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(ProgramResult(**data))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
    return out


def _normalize_program_id(program_id: str) -> str:
    """Map persisted benchmark IDs to the keys used by ground truth."""
    norm = program_id.replace("__", "/")
    prefix = "sv-benchmarks-loops-py/"
    if norm.startswith(prefix):
        norm = norm[len(prefix):]
    # cruxeval files: cruxeval__sample_51 -> cruxeval/sample_51
    crux_prefix = "cruxeval__"
    if norm.startswith(crux_prefix):
        norm = "cruxeval/" + norm[len(crux_prefix):]
    return norm


# -------------------------------------------------------------------
# Metrics computation
# -------------------------------------------------------------------

def _verdict_matrix(results: List[ProgramResult], gt: Dict[str, str]) -> Tuple[int, int, int, int, int]:
    """Return claim-level (TP, FP, FN, TN, total) for known programs."""
    tp = fp = fn = tn = 0
    total = 0
    for r in results:
        norm = _normalize_program_id(r.program_id)
        if norm not in gt:
            continue
        expected = gt[norm]
        for claim in r.claim_results:
            total += 1
            verdict = claim.status.value
            if verdict == "VERIFIED" and expected == "VERIFIED":
                tp += 1
            elif verdict == "VERIFIED" and expected != "VERIFIED":
                fp += 1
            elif verdict != "VERIFIED" and expected == "VERIFIED":
                fn += 1
            else:
                tn += 1
    return tp, fp, fn, tn, total


def _status_table(results: List[ProgramResult]) -> str:
    by_status = Counter()
    for r in results:
        if r.rejected_by_subset:
            by_status["REJECTED"] += 1
        elif r.claim_results:
            by_status.update(c.status for c in r.claim_results)
        else:
            by_status["NO_CLAIMS"] += 1

    total = sum(by_status.values()) or 1
    rows = ["| Status | Count | % |", "|--------|------:|--:|"]
    for status, n in sorted(by_status.items(), key=lambda x: -x[1]):
        name = getattr(status, "value", str(status))
        rows.append(f"| {name} | {n} | {n/total*100:.1f}% |")
    return "\n".join(rows)


def _format_crvr(crvr: Optional[float]) -> str:
    return f"{crvr:.3f}" if crvr is not None else "n/a"


def _fdr(tp: int, fp: int) -> Optional[float]:
    if tp + fp == 0:
        return None
    return fp / (tp + fp)


# -------------------------------------------------------------------
# Markdown generation
# -------------------------------------------------------------------

def _header(title: str, level: int = 1) -> str:
    return ("#" * level) + " " + title


def main(argv: List[str]) -> int:
    subset_only = "--subset" in argv
    out_file = RESULT_ROOT / "summary.md"
    if "--out" in argv:
        idx = argv.index("--out")
        if idx + 1 < len(argv):
            out_file = Path(argv[idx + 1])

    # Load all baselines
    all_results: Dict[str, List[ProgramResult]] = {}
    for bl in BASELINES:
        results = _load_results(RESULT_ROOT / bl)
        if subset_only:
            results = [
                r for r in results
                if _normalize_program_id(r.program_id) in SUBSET_12
            ]
        all_results[bl] = results
        print(f"Loaded {len(results)} results for {bl}")

    gt = GROUND_TRUTH

    # Compute metrics per baseline
    metrics_map: Dict[str, object] = {}
    tp_fp_fn_tn: Dict[str, Tuple[int, int, int, int, int]] = {}
    fdr_map: Dict[str, Optional[float]] = {}

    for bl in BASELINES:
        results = all_results[bl]
        m = aggregate(results, gt)
        metrics_map[bl] = m
        tp, fp, fn, tn, total = _verdict_matrix(results, gt)
        tp_fp_fn_tn[bl] = (tp, fp, fn, tn, total)
        fdr_map[bl] = _fdr(tp, fp)

    md: List[str] = []

    # Title
    md.append("# So sánh 5 Baseline kiểm chứng chương trình\n")
    md.append("| Generated: " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M") + " |")
    md.append("")
    md.append("## Tổng quan\n")
    md.append("| Baseline | Mô tả | Reference |")
    md.append("|---------|-------|----------|")
    md.append("| Proposed Method | CoT + Z3 + Anchor binding | DACN 2026 (thesis) |")
    md.append("| Baseline A | SatLM-style Direct Formalization | pipeline.py (existing) |")
    md.append("| Baseline B | Execution Grounding | Thakur et al. 2026, CRUXEval, Self-Debug |")
    md.append("| Baseline C | Structural Reasoning (CFG only, no Z3) | HoarePrompt, ICSE 2026 |")
    md.append("| Baseline D | LLM-as-Judge (Zero-shot CoT) | Wei et al. NeurIPS 2022, VeryTrace |")
    md.append("")

    # Main comparison table
    md.append("## Bảng so sánh chính\n")
    if subset_only:
        md.append(f"*Chỉ {len(SUBSET_12)} bài toán QF-LIA subset.*\n")
    cols = ["Metric"] + BASELINES
    md.append("| " + " | ".join(cols) + " |")
    md.append("| " + " | ".join("---" for _ in cols) + " |")

    def _metric_row(name: str, formula: str, getter) -> None:
        row = [f"{name} ({formula})"]
        for bl in BASELINES:
            val = getter(metrics_map[bl], tp_fp_fn_tn[bl], fdr_map[bl])
            row.append(val)
        md.append("| " + " | ".join(row) + " |")

    def _fdr_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        if fdr_val is None:
            return "n/a"
        return f"**{fdr_val:.3f}**"

    def _acc_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        acc = getattr(m, "accuracy", None)
        if acc is None:
            return "n/a"
        return f"**{acc:.3f}**"

    def _prec_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        p = getattr(m, "precision", None)
        return f"{p:.3f}" if p is not None else "n/a"

    def _recall_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        r = getattr(m, "recall", None)
        return f"{r:.3f}" if r is not None else "n/a"

    def _f1_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        f1 = getattr(m, "f1", None)
        return f"{f1:.3f}" if f1 is not None else "n/a"

    def _tin_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return f"{getattr(m,'token_avg_in',0):.1f}"

    def _tout_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return f"{getattr(m,'token_avg_out',0):.1f}"

    def _cost_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return f"{getattr(m,'cost_avg_usd',0):.6f}"

    def _hr_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return f"**{getattr(m,'hr',0):.3f}**"

    def _crvr_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        crvr = getattr(m, "crvr", None)
        return f"**{_format_crvr(crvr)}**"

    def _n_prog_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return str(getattr(m, "n_programs", 0))

    def _n_claims_getter(m, tp_fp_fn_tn_tuple, fdr_val):
        return str(getattr(m, "n_claims", 0))

    for name, formula, getter in [
        ("False Discovery Rate (FDR)", r"$FP/(TP+FP)$", _fdr_getter),
        ("Accuracy", r"$(TP+TN)/N$", _acc_getter),
        ("Precision", r"$TP/(TP+FP)$", _prec_getter),
        ("Recall", r"$TP/(TP+FN)$", _recall_getter),
        ("F1 Score", r"$2TP/(2TP+FP+FN)$", _f1_getter),
        ("Avg Tokens (in)", r"$T_{in}$", _tin_getter),
        ("Avg Tokens (out)", r"$T_{out}$", _tout_getter),
        ("Avg Cost (USD)", r"$C_{usd}$", _cost_getter),
        ("Hallucination Rate (HR)", r"$H = (N_{UNSUP}+N_{TE})/N_{claims}$", _hr_getter),
        ("Counterexample Replay Validity (CRVR)", r"$card(REPRODUCED)/card(CE_{Z3})$", _crvr_getter),
        ("Programs processed", r"$N_{prog}$", _n_prog_getter),
        ("Claims processed", r"$N_{claims}$", _n_claims_getter),
    ]:
        _metric_row(name, formula, getter)

    md.append("")

    # Verdict matrix
    md.append("## Verdict Matrix (so với ground truth SV-COMP)\n")
    md.append("| Baseline | TP | FP | FN | TN | FDR | Claims matched |")
    md.append("|----------|---:|---:|---:|---:|----:|---:|")
    for bl in BASELINES:
        tp, fp, fn, tn, total = tp_fp_fn_tn[bl]
        fdr = fdr_map[bl]
        fdr_str = f"{fdr:.3f}" if fdr is not None else "n/a"
        label = BASELINE_LABELS.get(bl, bl)
        md.append(f"| {label} | {tp} | {fp} | {fn} | {tn} | **{fdr_str}** | {total} |")
    md.append("")

    # Status breakdown per baseline
    md.append("## Phân bố trạng thái (claim-level)\n")
    for bl in BASELINES:
        md.append(f"### {BASELINE_LABELS.get(bl, bl)}\n")
        md.append(_status_table(all_results[bl]))
        md.append("")
    md.append("")

    # Per-program detail (all 5 baselines)
    md.append("## Chi tiết per-program (5 baseline)\n")
    if subset_only:
        program_ids = SUBSET_12
    else:
        # Collect all program IDs
        program_ids: set = set()
        for results in all_results.values():
            for r in results:
                norm = _normalize_program_id(r.program_id)
                program_ids.add(norm)
        program_ids = sorted(program_ids)

    md.append("| Program | " + " | ".join(bl.split("_")[0] + " V/CE" for bl in BASELINES) + " |")
    md.append("|--------|" + "|".join("---:" for _ in BASELINES) + "|")
    for pid in program_ids:
        short = pid.replace("loop-simple/", "").replace("loops-crafted-1/", "").replace("loop-invariants/", "").replace("cruxeval/", "crx:")
        row = [f"`{short}`"]
        for bl in BASELINES:
            found = None
            for r in all_results[bl]:
                if _normalize_program_id(r.program_id) == pid:
                    found = r
                    break
            if found:
                v = found.n_verified
                ce = found.n_counterexample
                un = found.n_unknown_timeout
                row.append(f"{v}V/{ce}CE/{un}?")
            else:
                row.append("-")
        md.append("| " + " | ".join(row) + " |")
    md.append("")

    # Method descriptions
    md.append("## Mô tả phương pháp\n")
    md.append("### Proposed Method (AST_ANCHORED)\n")
    md.append("- **CoT + Z3 + Anchor binding**: Gemini sinh Chain-of-Thought reasoning + Python assertions, sau đó Z3 kiểm chứng mỗi claim qua reachability query và target-state validity.")
    md.append("- **Key advantage**: Anchor binding đảm bảo claim chỉ được kiểm tra trên path thực sự chứa assertion. Z3 cung cấp completeness trên QF-LIA.")
    md.append("")
    md.append("### Baseline A (UNANCHORED / SatLM-style)\n")
    md.append("- **Direct SMT-LIB2**: Gemini sinh trực tiếp SMT-LIB2 constraints và specifications (không có NodeId, program state, executor, reachability).")
    md.append("- **Key difference**: Không có trace reasoning, không có anchor binding. LLM đóng vai trò symbolic reasoner thuần túy.")
    md.append("")
    md.append("### Baseline B (Execution Grounding)\n")
    md.append("- **References**: Thakur et al. 2026, CRUXEval (Gu et al., ICLR 2024), Self-Debug (Chen et al., ICLR 2024).")
    md.append("- **Mechanism**: Sinh N bộ input biên (boundary values + random), chạy CPython subprocess với `sys.settrace`, LLM đối chiếu trace với claim. Nếu >=1 input vi phạm -> COUNTEREXAMPLE. Replay bằng CPython.")
    md.append("- **Key characteristic**: Dynamic verification — chỉ đúng trên tập mẫu, không guarantees toàn diện.")
    md.append("")
    md.append("### Baseline C (Structural Reasoning / HoarePrompt)\n")
    md.append("- **Reference**: HoarePrompt (Bouras et al., ICSE 2026).")
    md.append("- **Mechanism**: Trích xuất CFG (Control Flow Graph) từ AST, LLM suy luận qua CFG bằng Natural State Propagation (NSP) và k-induction cho loops. KHÔNG dùng Z3.")
    md.append("- **Key question**: Liệu cấu trúc hoá CoT theo Hoare-style mà không có SMT solver có đủ để kiểm chứng chính xác?")
    md.append("")
    md.append("### Baseline D (LLM-as-Judge / Zero-shot CoT)\n")
    md.append("- **References**: Wei et al. NeurIPS 2022 (Chain-of-Thought Prompting), VeryTrace (Zhong et al., 2026).")
    md.append("- **Mechanism**: Prompt CoT đơn giản (few-shot, step-by-step), LLM tự đánh giá claim. KHÔNG có execution, KHÔNG có CFG, KHÔNG có Z3.")
    md.append("- **Key question**: Lower bound của LLM verification — baseline thô (mộc) nhất, không có bất kỳ hỗ trợ nào.")
    md.append("")

    # File structure
    md.append("## Cấu trúc sản phẩm\n")
    md.append("```")
    md.append("prototype/")
    md.append("├── src/astverifier/")
    md.append("│   ├── baselines/           # MỚI: package baselines B, C, D")
    md.append("│   │   ├── __init__.py")
    md.append("│   │   ├── inputs.py        # Boundary-value input generation (B)")
    md.append("│   │   ├── tracing.py       # Subprocess execution trace (B)")
    md.append("│   │   ├── execution_grounding.py  # Baseline B engine")
    md.append("│   │   ├── structural_reasoning.py # Baseline C engine")
    md.append("│   │   └── llm_judge.py            # Baseline D engine")
    md.append("│   ├── pipeline.py          # Mở rộng dispatch cho 5 baseline")
    md.append("│   ├── classify.py          # 6-status schemas (unchanged)")
    md.append("│   ├── metrics.py           # aggregate() (unchanged)")
    md.append("│   └── ... (các module khác)")
    md.append("├── tools/")
    md.append("│   ├── run_benchmark.py     # Mở rộng: --only, --subset, 5 baselines")
    md.append("│   └── compare_baselines.py  # Mở rộng: 5-way comparison")
    md.append("└── results/")
    md.append("    ├── AST_ANCHORED/*.json")
    md.append("    ├── UNANCHORED/*.json")
    md.append("    ├── BASELINE_B/*.json    # MỚI")
    md.append("    ├── BASELINE_C/*.json    # MỚI")
    md.append("    ├── BASELINE_D/*.json    # MỚI")
    md.append("    └── summary.md")
    md.append("```")
    md.append("")

    out_text = "\n".join(md)
    out_file.write_text(out_text, encoding="utf-8")
    print(f"\nWrote {out_file} ({len(out_text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
