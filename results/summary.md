# So sánh 5 Baseline kiểm chứng chương trình (2026-10-15)

> **Subset đánh giá:** 13 chương trình SV-COMP có `assert` (assert-bearing subset) cho B, C, D.  
> Proposed Method và Baseline A vẫn dùng 94 programs (full set).  
> **LLM:** Gemini 3.5 Flash Lite (free tier, 15 RPM)

---

## Tổng quan


| Baseline        | Mô tả                            | Reference                                |     |
| --------------- | -------------------------------- | ---------------------------------------- | --- |
| Proposed Method | CoT + Z3 + Anchor binding        | DACN 2026 (thesis)                       |     |
| Baseline A      | SatLM-style Direct Formalization | pipeline.py (existing)                   |     |
| **Baseline B**  | **Execution Grounding**          | Thakur et al. 2026, CRUXEval, Self-Debug |     |
| **Baseline C**  | **Structural Reasoning**         | HoarePrompt, ICSE 2026                   |     |
| **Baseline D**  | **LLM-as-Judge**                 | Wei et al. NeurIPS 2022, VeryTrace       |     |


---



## Bảng so sánh chính


| Metric                                | AST_ANCHORED | UNANCHORED | **BASELINE_B (P1)**   | **BASELINE_C (P1)** | **BASELINE_D (P1)** |
| ------------------------------------- | ------------ | ---------- | --------------------- | ------------------- | ------------------- |
| False Discovery Rate (FDR)            | **0.062**    | **0.000**  | **0.333**             | **0.273**           | **0.357**           |
| Accuracy                              | **0.298**    | **0.053**  | **0.400**             | **0.400**           | **0.450**           |
| Precision                             | 0.938        | 1.000      | **0.667**             | **0.727**           | **0.643**           |
| Recall                                | 0.278        | 0.027      | **0.500**             | **0.471**           | **0.600**           |
| **F1 Score**                          | 0.429        | 0.053      | **0.571**             | **0.571**           | **0.621**           |
| Hallucination Rate (HR)               | 0.017        | 0.000      | 0.000                 | 0.000               | 0.000               |
| Counterexample Replay Validity (CRVR) | 0.176        | n/a        | n/a*                  | n/a*                | **1.000**           |
| Programs processed                    | 94           | 94         | **13**                | **13**              | **13**              |
| Claims processed                      | 58           | 40         | 20                    | 20                  | 20                  |
| Avg LLM calls per claim               | 1            | 1          | 2 (Stage 1 + Stage 2) | 1                   | 1                   |


*Baseline B & C: CRVR = n/a trong Phase 1 vì LLM không report counterexample nào đúng. Baseline D có CRVR = 1.000 (1 CE ở `loopv1` được CPython xác nhận).

---

---



## Verdict Matrix (so với ground truth SV-COMP, claim-level)


| Baseline                                 | TP  | FP  | FN  | TN  | FDR       | Claims matched |
| ---------------------------------------- | --- | --- | --- | --- | --------- | -------------- |
| Proposed Method (CoT + Z3) — P0, 94 prog | 15  | 1   | 39  | 2   | **0.062** | 57             |
| Baseline A (SatLM-style) — P0, 94 prog   | 1   | 0   | 36  | 1   | **0.000** | 38             |
| **Baseline B (P1, 13 assert-bearing)**   | 8   | 4   | 8   | 0   | **0.333** | 20             |
| **Baseline C (P1, 13 assert-bearing)**   | 8   | 3   | 9   | 0   | **0.273** | 20             |
| **Baseline D (P1, 13 assert-bearing)**   | 9   | 5   | 6   | 0   | **0.357** | 20             |


---



## Error Taxonomy


| Category                    | B      | C      | D      |
| --------------------------- | ------ | ------ | ------ |
| `OK_VERIFIED`               | 8      | 8      | 8      |
| `OK_COUNTEREXAMPLE`         | 0      | 0      | **1**  |
| `TIMEOUT`                   | 8      | 9      | 6      |
| `LLM_HALLUCINATED_CE`       | 3      | 2      | 5      |
| `LLM_HALLUCINATED_VERIFIED` | 1      | 1      | 0      |
| **Tổng claims**             | **20** | **20** | **20** |


**Nhận xét:**

- Baseline D là **DUY NHẤT** tìm được counterexample đúng (1 ở `loopv1`).
- Baseline C có nhiều TIMEOUT nhất (9/20) — LLM bỏ cuộc với loops phức tạp.
- Baseline D có nhiều hallucinated CE nhất (5/20) — FDR cao nhưng cũng recall cao nhất.



### CLI

- Thêm `--assert-subset` flag để chỉ chạy 13 programs có `assert`.
- Warning khi chạy B/C/D mà quên `--use-llm`.

---

---



## Cách reproduce

```powershell
# Phase 1
cd e:\HK261\DACN\prototype
$env:PYTHONPATH = "src"

# Chạy từng baseline
python tools/run_benchmark.py --only BASELINE_B --assert-subset --use-llm --model gemini-3.5-flash-lite
python tools/run_benchmark.py --only BASELINE_C --assert-subset --use-llm --model gemini-3.5-flash-lite
python tools/run_benchmark.py --only BASELINE_D --assert-subset --use-llm --model gemini-3.5-flash-lite

# Tính metrics
python tools/compute_bcd_metrics.py
```

---

---

*Summary regenerated: 2026-10-15* 