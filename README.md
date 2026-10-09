# AST-Anchored CoT Verification Prototype

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

Framework thực hiện kiểm chứng neuro-symbolic cho chương trình Python: **AST
là trusted backbone của chế độ `AST_ANCHORED`**, không quyết định nào đến từ
LLM. Mọi claim được kiểm tra với `NodeId` trong chế độ này, và hai-truy vấn
Z3 (reachability + validity) được dùng cho từng claim target.

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
pip install z3-solver pydantic google-genai
```

## Chạy kiểm chứng với Gemini LLM

```powershell
# Thiết lập API Key (hoặc nhập trực tiếp khi script hỏi)
$env:GEMINI_API_KEY = "AIzaSy..."

# Chạy kiểm chứng code Python thông qua Chain-of-Thought của Gemini + Z3 SMT
python tools/demo_gemini_verify.py

# Hoặc truyền file bất kỳ
python tools/demo_gemini_verify.py --file path/to/code.py
```

## Chạy nhanh Benchmark có sẵn

```powershell
# 1. (Tuỳ chọn) dịch SV-COMP .c sang Python QF-LIA nếu chưa có
python tools/run_svcomp_translate.py

# 2. Chạy benchmark end-to-end (mặc định deterministic, không gọi LLM)
python tools/run_benchmark.py

# 3. Generate báo cáo Markdown
python tools/compare_baselines.py
# → results/summary.md

# Muốn benchmark sinh claim bằng Gemini thì bật rõ ràng:
python tools/run_benchmark.py --use-llm
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

## Diễn giải kết quả

- `AST_ANCHORED` yêu cầu dòng chính xác, scope biến hợp lệ và kiểm tra các
  path bounded dẫn tới đúng `NodeId`.
- `UNANCHORED` không xây dựng `NodeId`, symbolic executor, state relation hay
  reachability query; claim được kiểm tra như predicate tự do trên các biến
  số nguyên. Anchor/location fields của kết quả luôn rỗng.
- Loop unrolling và `break`/`continue` chưa tạo thành chứng minh toàn bộ
  execution space. Khi coverage không đầy đủ, kết quả là `UNKNOWN_TIMEOUT`
  thay vì `VERIFIED`.
- Replay chỉ được tính là thành công khi CPython thực sự chạm target line và
  đánh giá claim là false tại dòng đó.

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
