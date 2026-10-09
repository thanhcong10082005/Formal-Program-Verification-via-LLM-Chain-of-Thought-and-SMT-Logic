# Báo cáo thực nghiệm: AST-Anchored CoT Verification

Framework kiểm tra các chương trình đã qua subset gate với **hai chế độ baseline**.
Các bảng trạng thái và FDR bên dưới là **claim-level**; dữ liệu persisted chỉ phản ánh lần chạy đã được lưu.

- `AST_ANCHORED`: exact anchor binding, target-node reachability và target-state validity.
- `UNANCHORED`: free-predicate validity; không dùng NodeId, program state, executor hoặc reachability.

## 1. So sánh đối chứng — Bảng chính

| Metric | Công thức | AST_ANCHORED | UNANCHORED | Chênh lệch |
|--------|-----------|--------------|------------|-----------|
| False Discovery Rate (FDR) | $FP/(TP+FP)$ | **n/a** | 0.333 | - |
| Avg Tokens (in) | $T_{in}$ | 0.0 | 0.0 | +0.0 |
| Avg Tokens (out) | $T_{out}$ | 0.0 | 0.0 | +0.0 |
| Avg Cost (USD) | $C_{usd}$ | 0.000000 | 0.000000 | +0.000000 |
| Hallucination Rate (HR) | $H = \frac{N_{UNSUP} + N_{TE}}{N_{claims}}$ | **0.000** | 0.000 | +0.000 |
| Counterexample Replay Validity (CRVR) | $1 - \frac{\text{card}(S.V.)}{\text{card}(CE_{Z3})}$ | **0.143** | 0.143 | +0.000 |

Trong đó:
- $T_{in}, T_{out}$: tokens in/out from the recorded run; deterministic runs report zero.
- $H$: claim bị reject vì UNSUPPORTED + TRANSLATION_ERROR.
- $S.V.$: Soundness Violation — counterexample từ Z3 không reproduce trên CPython.

## 2. Verdict Matrix (against SV-COMP ground truth)

Chỉ các claims thuộc programs trong ground-truth được tính. Tổng cộng:
`17` claims.

| Baseline | TP | FP | FN | TN | FDR |
|----------|---:|---:|---:|---:|----:|
| AST_ANCHORED | 0 | 0 | 16 | 1 | **n/a** |
| UNANCHORED   | 2 | 1 | 14 | 0 | 0.333 |

**Diễn giải:**
- AST_ANCHORED: 0 VERIFIED verdicts (0 TP, 0 FP) → FDR = n/a.
- UNANCHORED: 3 VERIFIED verdicts (2 TP, 1 FP) → FDR = 0.333.

## 3. Phân bố trạng thái (claim-level)

### AST_ANCHORED

| Status | Count | % |
|--------|------:|--:|
| REJECTED | 82 | 81.2% |
| COUNTEREXAMPLE | 14 | 13.9% |
| UNREACHABLE | 3 | 3.0% |
| NO_CLAIMS | 2 | 2.0% |

### UNANCHORED

| Status | Count | % |
|--------|------:|--:|
| REJECTED | 82 | 81.2% |
| COUNTEREXAMPLE | 14 | 13.9% |
| VERIFIED | 3 | 3.0% |
| NO_CLAIMS | 2 | 2.0% |

## 4. Program-level Coverage

- Tổng programs: 94 (mỗi baseline)
- Subset pass rate (AST_ANCHORED): 12/94 (12.8%)
- Subset pass rate (UNANCHORED): 12/94 (12.8%)
- Claims (anchored): 17
- Claims (unanchored): 17

## 5. Per-program verdicts (AST_ANCHORED, accepted subset)

| Program | Claims | V | CE | UNREACH | UNSUP | TE |
|---------|-------:|--:|---:|--------:|------:|---:|
| `cruxeval/sample_51` | 0 | 0 | 0 | 0 | 0 | 0 |
| `loop-invariants/eq2` | 1 | 0 | 0 | 1 | 0 | 0 |
| `loops-crafted-1/Mono3_1` | 1 | 0 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/Mono4_1` | 1 | 0 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/loopv1` | 1 | 0 | 0 | 1 | 0 | 0 |
| `loops-crafted-1/mono-crafted_12` | 1 | 0 | 0 | 1 | 0 | 0 |
| `loops-crafted-1/mono-crafted_9` | 1 | 0 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/nested3-1` | 3 | 0 | 3 | 0 | 0 | 0 |
| `loops-crafted-1/nested3-1_abstracted` | 4 | 0 | 4 | 0 | 0 | 0 |
| `loops-crafted-1/nested3-2` | 3 | 0 | 3 | 0 | 0 | 0 |
| `loops-crafted-1/net_reset` | 0 | 0 | 0 | 0 | 0 | 0 |
| `loops-crafted-1/sumt2` | 1 | 0 | 1 | 0 | 0 | 0 |

## 6. Counterexample Replay (AST_ANCHORED)

| Claim | Model bindings | Replay verdict | Replay output |
|-------|----------------|----------------|---------------|
| `assert_L10` | y=0 | NOT_REPRODUCED |  |
| `assert_L10` | x=0, y=0 | NOT_REPRODUCED |  |
| `assert_L10` | y=1, x=0 | REPRODUCED | None |
| `assert_L14` | x=0 | NOT_REPRODUCED |  |
| `assert_L12` | y=0 | NOT_REPRODUCED |  |
| `assert_L10` | z=0 | NOT_REPRODUCED |  |
| `assert_L13` | x=0 | NOT_REPRODUCED |  |
| `assert_L11` | y=0 | NOT_REPRODUCED |  |
| `assert_L8` | z=268435455 | NOT_REPRODUCED |  |
| `assert_L9` | z=0 | NOT_REPRODUCED |  |
| `assert_L14` | x=1 | NOT_REPRODUCED |  |
| `assert_L12` | y=1 | NOT_REPRODUCED |  |
| `assert_L10` | z=1 | NOT_REPRODUCED |  |
| `assert_L12` | l=1, n=1, i=0, j=0 | REPRODUCED | None |

## 7. Ghi chú thực nghiệm

1. **LLM usage is explicit**: `run_benchmark.py` is deterministic by default; `--use-llm` is required for Gemini claim generation. Token totals are copied to both baseline records for the same LLM call.
2. **Claim-level accounting**: multi-claim programs contribute every `claim_result` to status counts and FDR; rejected programs are reported separately.
3. **Soundness boundary**: bounded loop/control-flow modeling is reported as `UNKNOWN_TIMEOUT` when path coverage is incomplete; `VERIFIED` is reserved for complete bounded paths.
4. **Replay boundary**: a replay is reproduced only when CPython reaches the target line and evaluates the target claim as false. A normal return alone is not confirmation.
5. **Subset boundary**: nonlinear multiplication and variable-divisor floor division/modulo are rejected; constant-coefficient arithmetic remains eligible.
6. **Ground truth labels**: based on the curated SV-COMP labels in `ground_truth.json`; they are not a proof of the symbolic approximation.

## 8. Cấu trúc sản phẩm

```
prototype/
├── src/astverifier/    # Mã nguồn framework
│   ├── subset.py       # QF-LIA whitelist gate
│   ├── nodes.py        # NodeId registry
│   ├── classify.py     # 6-status Pydantic schemas
│   ├── executor.py     # Trusted symbolic executor
│   ├── binding.py      # Anchor binding gate α(c)
│   ├── obligations.py  # Two-query Z3 protocol
│   ├── replay.py       # CPython subprocess counterexample replay
│   ├── pipeline.py     # End-to-end orchestrator
│   └── metrics.py      # FDR / Tokens / HR / CRVR aggregation
├── tools/
│   ├── translate_svcomp.py   # C → Python translator
│   ├── run_svcomp_translate.py
│   ├── run_benchmark.py      # Phase-4 runner
│   └── compare_baselines.py  # This report
└── results/
    ├── AST_ANCHORED/*.json  # per-program verdicts
    ├── UNANCHORED/*.json
    └── summary.md           # this file
```
