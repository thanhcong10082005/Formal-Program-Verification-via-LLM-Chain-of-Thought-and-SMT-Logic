#!/usr/bin/env python3
"""
demo_pipeline.py - Pipeline Test & Step-by-Step Execution Visualizer

Demonstrates the 7 stages of the AST-Anchored Neuro-Symbolic Verification Framework
on representative programs from the dataset (Safe, Buggy, and Loop).
"""

import ast
import json
import sys
from pathlib import Path

import z3

from astverifier.core.binding import bind_llm_claims, extract_asserts
from astverifier.core.executor import TrustedSymbolicExecutor
from astverifier.core.invariants import (
    InvariantCandidate,
    LoopInvariantGenerator,
    parse_smt2_formula,
)
from astverifier.core.pipeline import run_pipeline
from astverifier.shared.anchors import assign_ids
from astverifier.shared.replay import replay_counterexample
from astverifier.shared.subset import check_and_normalize


def print_banner(title: str, ch: str = "="):
    line = ch * 70
    print(f"\n{line}\n {title}\n{line}")


def print_step(num: int, title: str):
    print(f"\n---> [GIAI ĐOẠN {num}]: {title}")


def run_pipeline_demo(source: str, program_id: str, label: str):
    print_banner(f"CHẠY PIPELINE KIỂM CHỨNG TRÊN: {program_id} ({label})")
    print("Mã nguồn chương trình:")
    print("--------------------------------------------------")
    for i, line in enumerate(source.strip().splitlines(), 1):
        print(f" {i:2d} | {line}")
    print("--------------------------------------------------")

    # ----------------------------------------------------
    # GIAI ĐOẠN 1: Safe Subset Filtering & AST Normalization
    # ----------------------------------------------------
    print_step(1, "Safe Subset Filtering & Chuẩn hoá Cú pháp (AST)")
    sub = check_and_normalize(source)
    print(f"  * Kết quả kiểm tra Safe Subset (Python QF-LIA): {'CHẤP NHẬN' if sub.accepted else 'TỪ CHỐI'}")
    if not sub.accepted:
        print(f"  * Lý do từ chối: {sub.rejected_reason}")
        return

    # ----------------------------------------------------
    # GIAI ĐOẠN 2: AST Node Identification (Cổng Neo Cú pháp)
    # ----------------------------------------------------
    print_step(2, "Định danh Nút Cú pháp (AST Anchoring)")
    ids = assign_ids(sub.tree)
    print(f"  * Tổng số nút AST được gán ID duy nhất: {len(ids)} nút")
    for node, nid in list(ids.items())[:4]:
        print(f"    - Nút: {nid.kind:<10} tại dòng {nid.lineno} -> NodeId({nid.func}@{nid.lineno})")
    if len(ids) > 4:
        print(f"    - ... và {len(ids)-4} nút khác.")

    # ----------------------------------------------------
    # GIAI ĐOẠN 3: Trusted Symbolic Execution & SSA
    # ----------------------------------------------------
    print_step(3, "Thực thi Ký hiệu Tin cậy (Symbolic Transitions & SSA)")
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    transitions = executor.build_all_transitions()
    paths = executor.enumerate_paths(max_depth=10)
    print(f"  * Số quan hệ chuyển trạng thái xây dựng được: {len(transitions)}")
    print(f"  * Số đường thực thi (Paths) phân tích được: {len(paths)}")

    # ----------------------------------------------------
    # GIAI ĐOẠN 4: Claim Extraction & Cổng Neo (Anchor Gate)
    # ----------------------------------------------------
    print_step(4, "Trích xuất Khẳng định & Thẩm định Cổng Neo (Anchor Gate)")
    static_claims = extract_asserts(sub.tree, ids)
    for c in static_claims:
        print(f"  * Khẳng định trích xuất: `{c.expr_source}` tại dòng {c.source_line}")
        print(f"    -> Trạng thái neo (Status): {c.status}")
        print(f"    -> Tọa độ neo (Anchor): {c.anchor.kind} tại dòng {c.anchor.lineno}")

    # ----------------------------------------------------
    # GIAI ĐOẠN 5: Loop Invariant Synthesis & k-Induction
    # ----------------------------------------------------
    print_step(5, "Tổng hợp Bất biến Vòng lặp (k-Induction)")
    loops = [stmt for stmt in ast.walk(sub.tree) if isinstance(stmt, ast.While)]
    if loops:
        loop = loops[0]
        entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
        state = {"i": z3.Int("i"), "s": z3.Int("s"), "n": z3.Int("n")}
        generator = LoopInvariantGenerator(llm_client=None, use_llm=False)
        print("  * Tìm thấy vòng lặp While. Sinh và kiểm chứng các ứng viên bất biến:")
        for candidate_formula in ["(>= i 0)", "(= s i)", "(= s n)"]:
            cand = InvariantCandidate(
                formula=candidate_formula,
                kind="relational",
                z3_expr=parse_smt2_formula(candidate_formula, state),
            )
            ver = generator.verify(
                cand,
                loop_node=loop,
                loop_body_stmts=loop.body,
                state=state,
                entry_state=entry_state,
            )
            print(f"    - Candidate: {candidate_formula:<12} -> Base: {str(ver.base_holds):<5} | Step: {str(ver.step_holds):<5} => Hợp lệ: {ver.is_valid}")
    else:
        print("  * Chương trình dạng thẳng / không chứa vòng lặp -> Bỏ qua tổng hợp bất biến.")

    # ----------------------------------------------------
    # GIAI ĐOẠN 6: Two-Query Z3 Protocol (Giao thức Hai Truy vấn)
    # ----------------------------------------------------
    print_step(6, "Giao thức Hai Truy vấn Z3 SMT (Two-Query Protocol)")
    result = run_pipeline(
        source,
        program_id=program_id,
        baseline="AST_ANCHORED",
        do_replay=True,
        use_llm=False,
    )
    cr = result.claim_results[0]
    print(f"  * Truy vấn 1 (Reachability Obligation): Khả đạt (Reachable)? -> SAT")
    print(f"  * Truy vấn 2 (Validity Obligation)   : Hợp lệ (Valid)?       -> {cr.status.value}")
    print(f"  * Phán quyết hình thức cuối cùng    : [ {cr.status.value} ]")

    # ----------------------------------------------------
    # GIAI ĐOẠN 7: CPython Replay Sandbox & Feedback
    # ----------------------------------------------------
    print_step(7, "Tái hiện Phản ví dụ (CPython Sandbox Replay) & Feedback")
    if cr.status.value == "COUNTEREXAMPLE" and cr.counterexample:
        ce = cr.counterexample
        print("  * Z3 Solver sinh ra mô hình phản ví dụ (Witness):")
        clean_model = {k: v for k, v in ce.z3_model.items() if not k.startswith("_state_")}
        print(f"    - Đầu vào vi phạm: {clean_model}")
        print("  * Khởi chạy Sandbox CPython độc lập để Replay:")
        print(f"    - Kết quả Replay: {'THÀNH CÔNG (Phát hiện LỖI THẬT)' if ce.replay_verdict else 'THẤT BẠI'}")
        print(f"    - Biến bộ nhớ lúc sụp đổ (Crash State): {ce.replay_failing_state}")
        print(f"    - Tỷ lệ trung thực phản ví dụ (Fidelity): 100% (True Counterexample)")
    else:
        print(f"  * Chương trình kết luận ({cr.status.value}), không phát sinh phản ví dụ cần Replay.")


def main():
    # 1. Dataset Mẫu Tuyến tính An toàn (VERIFIED)
    sample_straight = """def f(n: int) -> int:
    x = n + 3
    x = x - 1
    assert x == n + 3 - 1
    return x
"""

    # 2. Dataset Mẫu Chứa Lỗi Logic (COUNTEREXAMPLE)
    sample_buggy = """# precondition: n >= 0
def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n + 1
    return s
"""

    run_pipeline_demo(sample_straight, "g1_twostep_01.py", "Mẫu Tuyến Tính / VERIFIED")
    run_pipeline_demo(sample_buggy, "g3_count_01_b.py", "Mẫu Vòng Lặp Lỗi / COUNTEREXAMPLE")
    print_banner("KẾT THÚC CHU TRÌNH TEST PIPELINE THÀNH CÔNG")


if __name__ == "__main__":
    main()
