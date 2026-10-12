# AST-Anchored CoT Verification Prototype

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

Framework thực hiện kiểm chứng neuro-symbolic cho chương trình Python: **AST
là trusted backbone của chế độ `AST_ANCHORED`**, không quyết định nào đến từ
LLM. Mọi claim được kiểm tra với `NodeId` trong chế độ này, và hai-truy vấn
Z3 (reachability + validity) được dùng cho từng claim target.

`UNANCHORED`: Baseline A, SatLM-style direct formalization without AST
anchoring or reachability. Gemini returns a joint declarative context of
SMT-LIB2 `constraints` and target `specifications`; Z3 checks each target with
`constraints AND NOT(target)`. Tên baseline và thư mục kết quả được giữ lại
để tương thích.

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

# Chạy kiểm chứng code Python thông qua Gemini + Z3 SMT
python tools/demo_gemini_verify.py

# Hoặc truyền file bất kỳ
python tools/demo_gemini_verify.py --file path/to/code.py
```

## E2E flow: chạy benchmark và lấy metrics

Chạy các lệnh dưới đây từ thư mục gốc của repository. `run_benchmark.py` tìm
dataset ở `../Dataset` trước, sau đó mới thử `./Dataset`. Cần có
`sv-benchmarks-loops-py/` và `cruxeval-main/data/cruxeval.jsonl`. Nếu mới chỉ
có các file SV-COMP `.c`, dịch chúng sang Python QF-LIA trước:

```powershell
python tools/run_svcomp_translate.py
```

### 1. Refresh deterministic (không dùng API)

Chế độ mặc định dùng các `assert` trong source làm claim, nên phù hợp để kiểm
tra pipeline và tạo một mốc lặp lại được. Token và cost được ghi là zero.

```powershell
python tools/run_benchmark.py
python tools/compare_baselines.py
```

### 2. Refresh bằng Gemini (LLM E2E)

`--use-llm` tạo hai claim pool độc lập cho mỗi source: một lời gọi cho
`AST_ANCHORED` (Python claims có line anchor) và một lời gọi cho
`UNANCHORED` (Baseline A, SatLM-style SMT-LIB2 constraints plus target
specifications). Với corpus mặc định 94 source, đây là khoảng 188 request
Gemini; cần model và quota hoạt động.

Không commit file key. Có thể nạp key cục bộ từ `geminiapi.txt` như sau:

```powershell
$env:GEMINI_API_KEY = (Get-Content -Raw geminiapi.txt).Trim()
$env:PYTHONPATH = "src"
python tools/run_benchmark.py --use-llm --model gemini-3.5-flash
python tools/compare_baselines.py
Remove-Item Env:GEMINI_API_KEY
```

Trên macOS/Linux:

```bash
export GEMINI_API_KEY="$(tr -d '\r\n' < geminiapi.txt)"
PYTHONPATH=src python tools/run_benchmark.py --use-llm --model gemini-3.5-flash
PYTHONPATH=src python tools/compare_baselines.py
unset GEMINI_API_KEY
```

Mỗi lần chạy benchmark sẽ ghi đè các JSON trong
`results/AST_ANCHORED/` và `results/UNANCHORED/`. Lệnh
`compare_baselines.py` tạo lại `results/summary.md`; đây là file metrics chính.
Token input/output được lưu riêng cho hai baseline. Nếu Gemini trả về lỗi
quota hoặc permission, không dùng run đó làm metrics mới; sửa quyền/quota rồi
chạy lại toàn bộ benchmark.

### 3. Kiểm tra kết quả

```powershell
PYTHONPATH=src pytest -q tests
python -m compileall -q src tools tests
```

Xem `results/summary.md` để kiểm tra FDR, accuracy, token consumption,
hallucination rate và CRVR. Các JSON per-program chứa trạng thái chi tiết và
`total_tokens_in`/`total_tokens_out` của lần chạy tương ứng.

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
- `direct_constraints`: context SMT-LIB2 chung của `UNANCHORED`; rỗng với
  `AST_ANCHORED`.
- `claim_results`: list `ClaimResult` với anchor NodeId, Z3 model, replay verdict
  (AST_ANCHORED), hoặc SMT-LIB2 target/rationale và không replay
  (UNANCHORED).
  - `rejected_by_subset`: True nếu source không phải QF-LIA subset.

## Diễn giải kết quả

- `AST_ANCHORED` yêu cầu dòng chính xác, scope biến hợp lệ và kiểm tra các
  path bounded dẫn tới đúng `NodeId`.
- `UNANCHORED` là Baseline A SatLM-style Direct Formalization: không xây dựng
  `NodeId`, symbolic executor, state relation hay reachability query. LLM sinh
  một context gồm các constraint QF-LIA và các target SMT-LIB2; source `assert`
  được thêm đúng một lần như target deterministic. Mỗi target được kiểm tra
  bằng `constraints AND NOT(target)`. Anchor/location fields luôn rỗng, và
  counterexample không có replay verdict.
- Loop unrolling và `break`/`continue` chưa tạo thành chứng minh toàn bộ
  execution space. Khi coverage không đầy đủ, kết quả là `UNKNOWN_TIMEOUT`
  thay vì `VERIFIED`.
- Replay chỉ được tính là thành công khi CPython thực sự chạm target line và
  đánh giá claim là false tại dòng đó.

## 4 metrics


| Metric                 | Công thức                                      | Ý nghĩa                                    |
| ---------------------- | ---------------------------------------------- | ------------------------------------------ |
| **FDR**                | $FP/(TP+FP)$                                   | False Discovery Rate trên ground-truth     |
| **Token Consumption**  | $\bar T_{in}, \bar T_{out}$                    | Token LLM trung bình riêng cho từng lời gọi baseline (0 nếu không gọi LLM) |
| **Hallucination Rate** | $(N_{UNSUP} + N_{TE}) / N_{claims}$            | Claim bị reject vì không grounded          |
| **CRVR**               | $\text{card}(\text{REPRODUCED}) / \text{card}(CE_{Z3})$ | Counterexample replay hợp lệ trên CPython  |


Xem `documentation/IMPLEMENTATION_WALKTHROUGH.md` § 6.2 để biết chi tiết
cách tính.

## Tài liệu

- `documentation/IMPLEMENTATION_WALKTHROUGH.md` — chi tiết từng phase và quá trình debug.

## License

Đồ án chuyên ngành — chỉ sử dụng cho mục đích học thuật.
