# Báo cáo thực nghiệm: AST-Anchored vs Direct Formalization

Framework kiểm tra các chương trình đã qua subset gate với **hai baseline được sinh độc lập**.
Các bảng trạng thái và FDR bên dưới là **claim-level**; dữ liệu persisted chỉ phản ánh lần chạy đã được lưu.

- `AST_ANCHORED`: exact anchor binding, target-node reachability và target-state validity.
- `UNANCHORED`: Baseline A, Direct Formalization bằng SMT-LIB2; không dùng NodeId, program state, executor hoặc reachability.

## 1. So sánh đối chứng — Bảng chính

| Metric | Công thức | AST_ANCHORED | UNANCHORED | Chênh lệch |
|--------|-----------|--------------|------------|-----------|
| False Discovery Rate (FDR) | $FP/(TP+FP)$ | **0.062** | 0.000 | +0.062 |
| Accuracy | $(TP+TN)/N$ | **0.298** | 0.053 | +0.246 |
| Precision (VERIFIED) | $TP/(TP+FP)$ | **0.938** | 1.000 | - |
| Recall (VERIFIED) | $TP/(TP+FN)$ | **0.278** | 0.027 | - |
| F1 (VERIFIED) | $2TP/(2TP+FP+FN)$ | **0.429** | 0.053 | - |
| Avg Tokens (in) | $T_{in}$ | 452.4 | 470.2 | -17.8 |
| Avg Tokens (out) | $T_{out}$ | 214.2 | 121.2 | +93.0 |
| Avg Cost (USD) | $C_{usd}$ | 0.005475 | 0.004170 | +0.001306 |
| Hallucination Rate (HR) | $H = \frac{N_{UNSUP} + N_{TE}}{N_{claims}}$ | **0.017** | 0.000 | +0.017 |
| Counterexample Replay Validity (CRVR) | $\frac{\text{card}(REPRODUCED)}{\text{card}(CE_{Z3})}$ | **0.176** | n/a | - |

Trong đó:
- $T_{in}, T_{out}$: tokens in/out from the recorded run; deterministic runs report zero.
- $H$: claim bị reject vì UNSUPPORTED + TRANSLATION_ERROR.
- $S.V.$: Soundness Violation — counterexample từ Z3 không reproduce trên CPython.
- Accuracy / Precision / Recall / F1 treat `VERIFIED` as positive; all other statuses are negative.

## 2. Verdict Matrix (against SV-COMP ground truth)

Chỉ các claims thuộc programs trong ground-truth được tính. Tổng cộng:
`57` claims.

| Baseline | TP | FP | FN | TN | FDR |
|----------|---:|---:|---:|---:|----:|
| AST_ANCHORED | 15 | 1 | 39 | 2 | **0.062** |
| UNANCHORED   | 1 | 0 | 36 | 1 | 0.000 |

**Diễn giải:**
- AST_ANCHORED: 16 VERIFIED verdicts (15 TP, 1 FP) → FDR = 0.062.
- UNANCHORED: 1 VERIFIED verdicts (1 TP, 0 FP) → FDR = 0.000.

## 3. Phân bố trạng thái (claim-level)

### AST_ANCHORED

| Status | Count | % |
|--------|------:|--:|
| REJECTED | 82 | 58.2% |
| UNKNOWN_TIMEOUT | 24 | 17.0% |
| COUNTEREXAMPLE | 17 | 12.1% |
| VERIFIED | 16 | 11.3% |
| UNSUPPORTED | 1 | 0.7% |
| NO_CLAIMS | 1 | 0.7% |

### UNANCHORED

| Status | Count | % |
|--------|------:|--:|
| REJECTED | 82 | 67.2% |
| COUNTEREXAMPLE | 39 | 32.0% |
| VERIFIED | 1 | 0.8% |

## 4. Program-level Coverage

- Tổng programs: 94 (mỗi baseline)
- Subset pass rate (AST_ANCHORED): 12/94 (12.8%)
- Subset pass rate (UNANCHORED): 12/94 (12.8%)
- Claims (anchored): 58
- Claims (unanchored): 40

## 5. Per-program verdicts (AST_ANCHORED, accepted subset)

| Program | Claims | V | CE | UNREACH | UNSUP | TE |
|---------|-------:|--:|---:|--------:|------:|---:|
| `cruxeval/sample_51` | 1 | 0 | 1 | 0 | 0 | 0 |
| `loop-invariants/eq2` | 2 | 1 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/Mono3_1` | 5 | 2 | 0 | 0 | 0 | 0 |
| `loops-crafted-1/Mono4_1` | 6 | 2 | 2 | 0 | 0 | 0 |
| `loops-crafted-1/loopv1` | 3 | 1 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/mono-crafted_12` | 5 | 3 | 0 | 0 | 0 | 0 |
| `loops-crafted-1/mono-crafted_9` | 6 | 2 | 3 | 0 | 1 | 0 |
| `loops-crafted-1/nested3-1` | 9 | 1 | 0 | 0 | 0 | 0 |
| `loops-crafted-1/nested3-1_abstracted` | 8 | 2 | 1 | 0 | 0 | 0 |
| `loops-crafted-1/nested3-2` | 9 | 1 | 6 | 0 | 0 | 0 |
| `loops-crafted-1/net_reset` | 0 | 0 | 0 | 0 | 0 | 0 |
| `loops-crafted-1/sumt2` | 4 | 1 | 2 | 0 | 0 | 0 |

## 6. Counterexample Replay (AST_ANCHORED)

| Claim | Model bindings | Replay verdict | Replay output |
|-------|----------------|----------------|---------------|
| `llm_claim_1_L2` | num=-2147483649, _state_5377847993748766589_num=-2147483649 | REPRODUCED | -2147483650 |
| `assert_L5_#5` | y=1, z=0, _state_42965111498772409_z=0, _state_42965111498772409_y=1, x=0 | REPRODUCED | None |
| `llm_claim_3_L10` | _state_6911736308847080190_y=500000, _state_6911736308847080190_x=1 | NOT_REPRODUCED | None |
| `llm_claim_4_L10` | _state_6911736308847080190_y=500000, _state_6911736308847080190_x=1 | NOT_REPRODUCED | None |
| `assert_L10_#5` | n=1, i=1, _state_6006930900743687437_n=1, _state_6006930900743687437_i=1, j=0 | REPRODUCED | None |
| `assert_L10_#7` | _state_6911736308847080190_y=500000, _state_6911736308847080190_x=1 | NOT_REPRODUCED | None |
| `llm_claim_4_L10` | _state_6911736308847080190_y=500000, _state_6911736308847080190_x=1 | NOT_REPRODUCED | None |
| `llm_claim_5_L10` | _state_6911736308847080190_y=500000, _state_6911736308847080190_x=1 | NOT_REPRODUCED | None |
| `assert_L9_#37` | _state_4069729154077372838_z=0, _state_4069729154077372838_w=0, _state_4069729154077372838_x=0, _state_4069729154077372838_y=0 | NOT_REPRODUCED | None |
| `assert_L14_#9` | _state_6647481356346844098_y=1, _state_6647481356346844098_x=1, _state_6647481356346844098_w=0, _state_6647481356346844098_z=1 | NOT_REPRODUCED | None |
| `assert_L12_#25` | _state_5919542300938332834_z=1, _state_5919542300938332834_w=0, _state_5919542300938332834_y=1, _state_5919542300938332834_x=0 | NOT_REPRODUCED | None |
| `assert_L10_#37` | _state_2323538479351049394_w=0, _state_2323538479351049394_x=0, _state_2323538479351049394_y=0, _state_2323538479351049394_z=1 | NOT_REPRODUCED | None |
| `llm_claim_4_L10` | _state_2323538479351049394_w=0, _state_2323538479351049394_x=0, _state_2323538479351049394_y=0, _state_2323538479351049394_z=1 | NOT_REPRODUCED | None |
| `llm_claim_5_L12` | _state_5919542300938332834_z=1, _state_5919542300938332834_w=0, _state_5919542300938332834_y=1, _state_5919542300938332834_x=0 | NOT_REPRODUCED | None |
| `llm_claim_6_L14` | _state_6647481356346844098_y=1, _state_6647481356346844098_x=1, _state_6647481356346844098_w=0, _state_6647481356346844098_z=1 | NOT_REPRODUCED | None |
| `assert_L12_#6` | i=1, n=0, j=0, _state_6493095030292635476_n=0, _state_6493095030292635476_j=0 | NOT_REPRODUCED | None |
| `llm_claim_2_L6` | n=-1, j=0, _state_4134104508008881893_j=0, _state_4134104508008881893_n=-1, _state_4134104508008881893_l=0 | NOT_REPRODUCED | None |

## 7. Ghi chú thực nghiệm

1. **Independent generation**: `run_benchmark.py` is deterministic by default; `--use-llm` makes one anchored Python-claim call and one direct SMT-LIB2 call per source. Token totals are recorded separately and are not copied between baselines.
2. **Claim-level accounting**: multi-claim programs contribute every `claim_result` to status counts and FDR; rejected programs are reported separately.
3. **Soundness boundary**: bounded loop/control-flow modeling is reported as `UNKNOWN_TIMEOUT` when path coverage is incomplete; `VERIFIED` is reserved for complete bounded paths.
4. **Replay boundary**: only AST_ANCHORED counterexamples are replayed; reproduction requires CPython to reach the target line and evaluate the target claim as false. Direct counterexamples have no replay verdict.
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
│   ├── direct.py       # Direct SMT-LIB2 validator
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
