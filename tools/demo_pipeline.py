#!/usr/bin/env python3
"""
demo_pipeline.py - Pipeline Test & Step-by-Step Execution Visualizer

Demonstrates the 7 stages of the AST-Anchored Neuro-Symbolic Verification Framework
on representative programs from the dataset (Safe, Loop Safe, and Loop Buggy),
printing full raw LLM JSON outputs and full pipeline outputs at each stage.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import z3

from astverifier.core.binding import bind_llm_claims, extract_asserts
from astverifier.core.executor import TrustedSymbolicExecutor
from astverifier.core.invariants import (
    LoopInvariantGenerator,
    _as_z3_bool,
    _eval_ast_expr,
    _loop_written_vars,
)
from astverifier.core.pipeline import run_pipeline
from astverifier.llm.gemini import (
    generate_cot_and_claims,
    make_client,
    resolve_api_key,
)
from astverifier.shared.anchors import assign_ids
from astverifier.shared.subset import check_and_normalize


def print_banner(title: str, ch: str = "="):
    line = ch * 78
    print(f"\n{line}\n {title}\n{line}")


def print_step(num: int, title: str):
    print(f"\n---> [GIAI ĐOẠN {num}]: {title}")


def run_pipeline_demo(
    source: str,
    program_id: str,
    label: str,
    *,
    use_llm: bool = False,
    model: str = "gemini-3.5-flash-lite",
    api_key: str | None = None,
):
    print_banner(f"CHẠY PIPELINE KIỂM CHỨNG TRÊN: {program_id} ({label})")
    if use_llm:
        print(f"Chế độ: LIVE LLM API (model={model})")
    else:
        print("Chế độ: OFFLINE (không gọi LLM API)")
    print("Mã nguồn chương trình:")
    print("------------------------------------------------------------------------------")
    for i, line in enumerate(source.strip().splitlines(), 1):
        print(f" {i:2d} | {line}")
    print("------------------------------------------------------------------------------")

    # ----------------------------------------------------
    # GIAI ĐOẠN 1: Safe Subset Filtering & AST Normalization
    # ----------------------------------------------------
    print_step(1, "Safe Subset Filtering & Chuẩn hoá Cú pháp (AST)")
    sub = check_and_normalize(source)
    print(
        f"  * Kết quả kiểm tra Safe Subset (Python QF-LIA): "
        f"{'CHẤP NHẬN (accepted=True)' if sub.accepted else 'TỪ CHỐI (accepted=False)'}"
    )
    if not sub.accepted:
        print(f"  * Lý do từ chối: {sub.rejected_reason}")
        return

    # ----------------------------------------------------
    # GIAI ĐOẠN 2: AST Node Identification (Cổng Neo Cú pháp)
    # ----------------------------------------------------
    print_step(2, "Định danh Nút Cú pháp (AST Anchoring)")
    ids = assign_ids(sub.tree)
    print(f"  * Tổng số nút AST được gán ID duy nhất: {len(ids)} nút")
    for _node, nid in ids.items():
        print(
            f"    - Dòng {nid.lineno:2d}, cột {nid.col_offset:2d}: "
            f"{nid.kind:<12} -> {nid.as_string()}"
        )

    # ----------------------------------------------------
    # GIAI ĐOẠN 3: Trusted Symbolic Execution & SSA
    # ----------------------------------------------------
    print_step(3, "Thực thi Ký hiệu Tin cậy (Symbolic Transitions & SSA)")
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    transitions = executor.build_all_transitions()
    paths = executor.enumerate_paths(max_depth=10)
    print(f"  * Số quan hệ chuyển trạng thái xây dựng được: {len(transitions)}")
    for nid, tr in transitions.items():
        print(
            f"    - {nid.as_string():<24} | Guard: {tr.guard} | Post-bindings: {tr.post.bindings}"
        )
    print(f"  * Số đường thực thi (Paths) phân tích được: {len(paths)}")

    # ----------------------------------------------------
    # GIAI ĐOẠN 4: Claim Extraction, CoT & Cổng Neo (Anchor Gate)
    # ----------------------------------------------------
    print_step(4, "Trích xuất Khẳng định, CoT & Thẩm định Cổng Neo (Anchor Gate)")
    static_claims = extract_asserts(sub.tree, ids)
    print(f"  * [Pipeline] Số assert tĩnh trích xuất từ mã nguồn: {len(static_claims)}")
    for c in static_claims:
        print(
            f"    - claim_id={c.claim_id} | dòng {c.source_line}: `{c.expr_source}` "
            f"-> status={c.status}, anchor={c.anchor.as_string() if c.anchor else None}"
        )

    llm_suggestions = []
    cot_trace = ""
    tokens_in = 0
    tokens_out = 0
    if use_llm:
        key = resolve_api_key(api_key)
        llm_res = generate_cot_and_claims(source, api_key=key, model=model)
        llm_suggestions = list(llm_res.claims)
        cot_trace = llm_res.cot_trace
        tokens_in = llm_res.tokens_in
        tokens_out = llm_res.tokens_out
        print(
            f"\n  * [LLM Output - Giai đoạn 4] Raw JSON từ Gemini ({tokens_in} input tokens / {tokens_out} output tokens):"
        )
        try:
            pretty_json = json.dumps(json.loads(llm_res.raw_text), indent=2, ensure_ascii=False)
            for ln in pretty_json.splitlines():
                print(f"      {ln}")
        except Exception:
            print(f"      {llm_res.raw_text}")

        bound_llm = bind_llm_claims(sub.tree, ids, llm_suggestions)
        print(f"\n  * [Pipeline Output - Giai đoạn 4] Kết quả lọc qua Cổng Neo AST ({len(bound_llm)} claims):")
        for c in bound_llm:
            anchor_txt = c.anchor.as_string() if c.anchor else "UNGROUNDED"
            print(
                f"    - claim_id={c.claim_id} | dòng {c.source_line}: `{c.expr_source}` "
                f"-> status={c.status} | anchor={anchor_txt}"
            )

    # ----------------------------------------------------
    # GIAI ĐOẠN 5: Loop Invariant Synthesis & k-Induction
    # ----------------------------------------------------
    print_step(5, "Tổng hợp Bất biến Vòng lặp (k-Induction & Hoare Exit)")
    loops = [stmt for stmt in ast.walk(sub.tree) if isinstance(stmt, ast.While)]
    if loops:
        loop = loops[0]
        entry_state = dict(executor.transitions[ids[loop]].pre.bindings)
        names = sorted(set(entry_state.keys()) | _loop_written_vars(loop))
        state = {k: z3.Int(k) for k in names}
        llm_client = make_client(resolve_api_key(api_key)) if use_llm else None
        generator = LoopInvariantGenerator(
            llm_client=llm_client, use_llm=use_llm, model=model
        )
        post_expr = static_claims[0].expr_node if static_claims else None
        candidates = generator.generate(source, loop, post_expr, state)
        llm_cands = [c for c in candidates if c.source == "llm"]
        if use_llm:
            raw_inv_json = llm_cands[0].raw_attempt if llm_cands else "(không có phản hồi)"
            print("  * [LLM Output - Giai đoạn 5] Raw JSON ứng viên bất biến từ Gemini:")
            for ln in raw_inv_json.strip().splitlines():
                print(f"      {ln}")
            print("\n  * [Pipeline Output - Giai đoạn 5] Kiểm chứng k-Induction từng ứng viên LLM:")
            for cand in llm_cands:
                ver = generator.verify(
                    cand,
                    loop_node=loop,
                    loop_body_stmts=loop.body,
                    state=state,
                    entry_state=entry_state,
                )
                print(
                    f"    - Candidate: {cand.formula:<18} (kind={cand.kind:<10}) | "
                    f"Z3 Expr: {cand.z3_expr} -> Base={ver.base_holds}, Step={ver.step_holds}, "
                    f"Valid={ver.is_valid} ({ver.reason})"
                )
        pre_cond = None
        m_pre = re.search(r"#\s*precondition:\s*([^\n|]+)", source)
        if m_pre and m_pre.group(1).strip().lower() != "none":
            pre_ast = ast.parse(m_pre.group(1).strip(), mode="eval").body
            pre_ev = _eval_ast_expr(pre_ast, dict(state))
            if pre_ev is not None:
                pre_cond = _as_z3_bool(pre_ev)
        ver_hoare = generator.generate_and_verify(
            source,
            loop,
            post_expr,
            state,
            entry_state=entry_state,
            pre_condition=pre_cond,
        )
        if ver_hoare is not None:
            print(
                f"  * [Pipeline Output - Hoare Post-condition Check]: THÀNH CÔNG\n"
                f"    - Công thức đại diện: {ver_hoare.candidate.formula}\n"
                f"    - Hợp tuyển bất biến Z3 (I): {ver_hoare.z3_expr}\n"
                f"    - Lý do: {ver_hoare.reason}"
            )
        else:
            print(
                "  * [Pipeline Output - Hoare Post-condition Check]: KHÔNG CHỨNG MINH ĐƯỢC HẬU ĐIỀU KIỆN\n"
                "    -> Giữ nguyên các đường đi cho Z3 & CPython Replay tìm phản ví dụ."
            )
    else:
        print("  * Chương trình dạng thẳng / không chứa vòng lặp -> Bỏ qua tổng hợp bất biến.")

    # ----------------------------------------------------
    # GIAI ĐOẠN 6: Two-Query Z3 Protocol (Giao thức Hai Truy vấn)
    # ----------------------------------------------------
    print_step(6, "Giao thức Hai Truy vấn Z3 SMT (Two-Query Protocol)")
    invariant_generator = (
        LoopInvariantGenerator(
            llm_client=make_client(resolve_api_key(api_key)),
            use_llm=True,
            model=model,
        )
        if use_llm
        else None
    )
    result = run_pipeline(
        source,
        program_id=program_id,
        baseline="AST_ANCHORED",
        do_replay=True,
        use_llm=False,
        suggested_claims=llm_suggestions if use_llm else None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cot_trace=cot_trace,
        gemini_api_key=api_key,
        gemini_model=model,
        invariant_generator=invariant_generator,
        enable_invariants=True,
        enable_feedback=True,
    )
    print(
        f"  * [Pipeline Summary]: n_claims={result.n_claims}, n_verified={result.n_verified}, "
        f"n_counterexample={result.n_counterexample}, n_unreachable={result.n_unreachable}, "
        f"n_unknown_timeout={result.n_unknown_timeout}, elapsed={result.elapsed_seconds:.3f}s"
    )
    for idx, cr in enumerate(result.claim_results, 1):
        print(
            f"  * Claim #{idx} ({cr.claim_id}): `{cr.claim_text}` @ dòng {cr.anchor.source_lines}\n"
            f"    - Anchor NodeID : {cr.anchor.node_ids} (has_grounding={cr.anchor.has_grounding})\n"
            f"    - Z3 Stats      : {cr.z3_stats}\n"
            f"    - Phán quyết    : [ {cr.status.value} ]\n"
            f"    - Lý do         : {cr.reason}"
        )

    # ----------------------------------------------------
    # GIAI ĐOẠN 7: CPython Replay Sandbox & Feedback
    # ----------------------------------------------------
    print_step(7, "Tái hiện Phản ví dụ (CPython Sandbox Replay) & Vòng Phản hồi Hồi cứu (Feedback Loop)")
    ce_claims = [
        cr
        for cr in result.claim_results
        if cr.status.value == "COUNTEREXAMPLE" and cr.counterexample
    ]
    if ce_claims:
        for cr in ce_claims:
            ce = cr.counterexample
            clean_model = {
                k: v for k, v in ce.z3_model.items() if not k.startswith("_state_")
            }
            print(f"  * Claim bị vi phạm: `{cr.claim_text}` ({cr.claim_id})")
            print(f"    - [Z3 Model đầy đủ]         : {ce.z3_model}")
            print(f"    - [Vector đầu vào trích xuất]: {clean_model}")
            print(
                f"    - [CPython Sandbox Replay]   : replay_verdict={ce.replay_verdict} "
                f"({'XÁC NHẬN LỖI THẬT' if ce.replay_verdict else 'PHẢN VÍ DỤ GIẢ'})"
            )
            print(f"    - [Trạng thái biến lúc lỗi]  : {ce.replay_failing_state}")
            print(
                f"    - [LLM & Pipeline Feedback]  : resolution={ce.feedback_resolution}, "
                f"iterations={ce.feedback_iterations}, "
                f"refined_claim=`{ce.feedback_refined_claim_text}` @ dòng {ce.feedback_refined_claim_line}"
            )
    else:
        print("  * Không có phản ví dụ COUNTEREXAMPLE nào cần Replay.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Gọi Gemini API thật (cần GEMINI_API_KEY trong env hoặc file .env)",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.5-flash-lite",
        help="Tên model Gemini sử dụng khi bật --use-llm (mặc định: gemini-3.5-flash-lite)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key Gemini (tùy chọn, mặc định đọc từ GEMINI_API_KEY hoặc .env)",
    )
    parser.add_argument(
        "--program",
        type=Path,
        default=None,
        help="Đường dẫn tới 1 file Python cụ thể để chạy demo",
    )
    args = parser.parse_args()

    if args.use_llm and not resolve_api_key(args.api_key):
        print(
            "LỖI: Chưa tìm thấy GEMINI_API_KEY.\n"
            "Hãy thiết lập biến môi trường `export GEMINI_API_KEY=...` "
            "hoặc tạo file `.env` ở thư mục gốc repo với nội dung:\n"
            "GEMINI_API_KEY=your_api_key_here",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if args.program is not None:
        source = args.program.read_text(encoding="utf-8")
        run_pipeline_demo(
            source,
            args.program.name,
            f"Custom File ({args.program})",
            use_llm=args.use_llm,
            model=args.model,
            api_key=args.api_key,
        )
        return

    sample_straight = """def f(n: int) -> int:
    x = n + 3
    x = x - 1
    assert x == n + 3 - 1
    return x
"""

    sample_loop_safe = """# precondition: n >= 0
def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n
    return s
"""

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

    run_pipeline_demo(
        sample_straight,
        "g1_twostep_01.py",
        "Mẫu Tuyến Tính / VERIFIED",
        use_llm=args.use_llm,
        model=args.model,
        api_key=args.api_key,
    )
    run_pipeline_demo(
        sample_loop_safe,
        "g3_count_01_safe.py",
        "Mẫu Vòng Lặp An Toàn / VERIFIED (Hoare + Invariant)",
        use_llm=args.use_llm,
        model=args.model,
        api_key=args.api_key,
    )
    run_pipeline_demo(
        sample_buggy,
        "g3_count_01_b.py",
        "Mẫu Vòng Lặp Lỗi / COUNTEREXAMPLE",
        use_llm=args.use_llm,
        model=args.model,
        api_key=args.api_key,
    )
    print_banner("KẾT THÚC CHU TRÌNH TEST PIPELINE THÀNH CÔNG")


if __name__ == "__main__":
    main()
