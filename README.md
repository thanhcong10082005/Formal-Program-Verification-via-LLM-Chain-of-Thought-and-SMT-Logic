# AST-Anchored CoT Verification Prototype

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

Framework thực hiện kiểm chứng neuro-symbolic cho chương trình Python: **AST
là trusted backbone duy nhất**, không quyết định nào đến từ LLM. Mọi claim
phải được ràng buộc (anchor binding) với `NodeId` của AST, và hai-truy vấn
Z3 (reachability + validity) được dùng để xác minh tính đúng đắn.

## Cấu trúc

```
prototype/
├── src/astverifier/    # 9 module framework (subset, executor, obligations, replay, ...)
├── tools/              # C-to-Python translator + benchmark runner
├── documentation/      # Walkthrough chi tiết về quá trình triển khai
└── results/            # JSON per-program verdicts + summary.md
```

Xem `documentation/IMPLEMENTATION_WALKTHROUGH.md` để hiểu chi tiết từng
phase và quá trình debug.

Xem `results/summary.md` để có báo cáo thực nghiệm cuối (4 metric: FDR,
token consumption, Hallucination Rate, CRVR) trên 188 programs với 2
baseline (`AST_ANCHORED` vs `UNANCHORED`).

## Cài đặt

```powershell
# Yêu cầu Python 3.14+ (do dùng ast.arg.arg)
pip install z3-solver pydantic
```

## Chạy nhanh

```powershell
# 1. (Tuỳ chọn) dịch SV-COMP .c sang Python QF-LIA nếu chưa có
cd prototype
python tools/run_svcomp_translate.py

# 2. Chạy benchmark end-to-end
python tools/run_benchmark.py

# 3. Generate báo cáo Markdown
python tools/compare_baselines.py
# → results/summary.md
```

## Smoke test pipeline

```powershell
cd prototype
python -c "
import sys; sys.path.insert(0, 'src')
from astverifier.pipeline import run_pipeline
src = '''
def f(s: int, n: int):
    x = 0
    i = 0
    while i < n:
        x = x + i
        i = i + 1
    assert x >= 0
'''
r = run_pipeline(src, baseline='AST_ANCHORED')
print(r.n_claims, r.claim_results[0].status.value, r.claim_results[0].reason)
"
```

## Đầu vào / Đầu ra

- **Input**: source Python (đã được kiểm tra thuộc QF-LIA whitelist).
- **Output** (`ProgramResult`):
  - `n_verified`, `n_counterexample`, `n_unreachable`, `n_unsupported`,
  `n_translation_error`, `n_unknown_timeout`
  - `claim_results`: list `ClaimResult` với anchor NodeId, Z3 model, replay verdict.
  - `rejected_by_subset`: True nếu source không phải QF-LIA subset.

## 4 metrics


| Metric                 | Công thức                                      | Ý nghĩa                                    |
| ---------------------- | ---------------------------------------------- | ------------------------------------------ |
| **FDR**                | $FP/(TP+FP)$                                   | False Discovery Rate trên ground-truth     |
| **Token Consumption**  | $\bar T_{in}, \bar T_{out}$                    | Token LLM trung bình (0 nếu không gọi LLM) |
| **Hallucination Rate** | $(N_{UNSUP} + N_{TE}) / N_{claims}$            | Claim bị reject vì không grounded          |
| **CRVR**               | $1 - \text{card}(S.V.) / \text{card}(CE_{Z3})$ | Counterexample replay hợp lệ trên CPython  |


Xem `documentation/IMPLEMENTATION_WALKTHROUGH.md` § 6.2 để biết chi tiết
cách tính.

## Tài liệu

- `documentation/IMPLEMENTATION_WALKTHROUGH.md` — chi tiết từng phase và quá trình debug.

## License

Đồ án chuyên ngành — chỉ sử dụng cho mục đích học thuật.