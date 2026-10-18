# AST-Anchored SatLM Verification Prototype

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

Framework thực hiện kiểm chứng neuro-symbolic cho chương trình Python:
`AST_ANCHORED` dùng prompt SatLM-style để sinh joint SMT-LIB2 constraints và
target formulas có line anchor. **AST là trusted backbone**, không quyết định
nào đến từ LLM: từng target phải bind vào đúng `NodeId`, rồi qua hai truy vấn
Z3 (reachability + validity).

`UNANCHORED`: Baseline A, simple claim-list không có AST anchoring,
program-state hay reachability. Gemini trả về một JSON claim-list có biểu thức
QF-LIA có cấu trúc; pipeline dịch từng claim sang SMT-LIB2 và Z3 kiểm tra chỉ
với `NOT(claim)`. Tên baseline và thư mục kết quả được giữ lại để tương thích.

## Cấu trúc

```
prototype/
├── src/astverifier/    # 9 module framework (subset, executor, obligations, replay, ...)
├── tools/              # C-to-Python translator + benchmark runner
├── documentation/      # Walkthrough chi tiết về quá trình triển khai
└── results/            # JSON per-program verdicts + summary.md
```

Xem `documentation/IMPLEMENTATION_WALKTHROUGH.md` để hiểu chi tiết từng
phase và quá trình debug. Metrics là artifact tùy chọn do benchmark tools sinh;
working tree hiện không chứa persisted report.

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

`--use-llm` tạo hai claim pool độc lập cho mỗi source: một lời gọi SatLM-style
cho `AST_ANCHORED` (SMT-LIB2 constraints và target có line anchor), và một lời
gọi structured claim-list độc lập cho `UNANCHORED` (Baseline A, không
anchor/state).
Với corpus mặc định 94 source, đây là khoảng 188 request Gemini; cần model và
quota hoạt động.

Với `gemini-3.5-flash-lite`, adapter tự động chèn khoảng 4.2 giây giữa các
lần thử request (xấp xỉ 14 request/phút), để nằm dưới giới hạn free-tier quan
sát được là 15 request/phút. Có thể đặt rõ hoặc thay đổi khoảng chờ bằng
`GEMINI_REQUEST_INTERVAL_SECONDS`; đây là throttling cục bộ, không làm tăng
quota hoặc sửa lỗi project bị Google từ chối.

Không commit file key. Có thể nạp key cục bộ từ `geminiapi.txt` như sau:

```powershell
$env:GEMINI_API_KEY = (Get-Content -Raw geminiapi.txt).Trim()
$env:PYTHONPATH = "src"
$env:GEMINI_REQUEST_INTERVAL_SECONDS = "4.2"
python tools/run_benchmark.py --use-llm --model gemini-3.5-flash-lite
python tools/compare_baselines.py
Remove-Item Env:GEMINI_API_KEY
Remove-Item Env:GEMINI_REQUEST_INTERVAL_SECONDS
```

Trên macOS/Linux:

```bash
export GEMINI_API_KEY="$(tr -d '\r\n' < geminiapi.txt)"
export GEMINI_REQUEST_INTERVAL_SECONDS=4.2
PYTHONPATH=src python tools/run_benchmark.py --use-llm --model gemini-3.5-flash-lite
PYTHONPATH=src python tools/compare_baselines.py
unset GEMINI_API_KEY
unset GEMINI_REQUEST_INTERVAL_SECONDS
```

Mỗi lần chạy benchmark sẽ ghi các JSON trong
`results/AST_ANCHORED/` và `results/UNANCHORED/`. Lệnh
`compare_baselines.py` có thể tạo `results/summary.md`; các artifact này không
được commit trong working tree hiện tại.
Token input/output được lưu riêng cho hai baseline. Nếu Gemini trả về lỗi
quota hoặc permission, không dùng run đó làm metrics mới; sửa quyền/quota rồi
chạy lại toàn bộ benchmark.

### 3. Kiểm tra kết quả

```powershell
PYTHONPATH=src pytest -q tests
python -m compileall -q src tools tests
```

Nếu đã chạy benchmark, xem `results/summary.md` để kiểm tra FDR, accuracy,
token consumption, hallucination rate và CRVR. Các JSON per-program chứa
trạng thái chi tiết và `total_tokens_in`/`total_tokens_out`.

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
- `direct_constraints`: joint SMT-LIB2 context của formalization
  `AST_ANCHORED`; context được kiểm tra cùng target state. Baseline
  `UNANCHORED` luôn để trường này rỗng.
- `claim_results`: list `ClaimResult` với target SMT-LIB2, anchor NodeId, Z3
  model và replay verdict khi target là source assertion (AST_ANCHORED), hoặc
  structured claim/rationale không anchor và không replay (UNANCHORED).
  - `rejected_by_subset`: True nếu source không phải QF-LIA subset.

## Diễn giải kết quả

- `AST_ANCHORED` dùng SMT-LIB2 formalization với line anchor, scope biến,
  trusted program state và kiểm tra các bounded
  path dẫn tới đúng `NodeId`. Target do LLM suy ra có anchor nhưng không có
  Python expression để replay; source `assert` vẫn replay được.
- `UNANCHORED` là Baseline A simple claim-list: không xây dựng `NodeId`,
  symbolic executor, state relation hay reachability query. LLM sinh một JSON
  claim-list có biểu thức QF-LIA; source `assert` được dịch deterministic và
  thêm đúng một lần. Mỗi claim được kiểm tra bằng `NOT(claim)`. Anchor/location
  fields luôn rỗng, và counterexample không có replay verdict.
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
