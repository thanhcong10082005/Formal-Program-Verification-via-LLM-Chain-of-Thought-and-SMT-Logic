# `prototype/` — AST-Anchored Neuro-Symbolic Verification Framework

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách
>
> **Trạng thái:** Core Proposed Method đã hoàn thiện, sẵn sàng cho giai đoạn
> cài đặt **Baseline**. Bộ dữ liệu tạm `dataset_temp_200/` đã sẵn sàng.

---

## Mục lục

1. [Framework này làm gì](#1-framework-này-làm-gì)
2. [Triết lý thiết kế: AST là trusted backbone](#2-triết-lý-thiết-kế-ast-là-trusted-backbone)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Kiến trúc 7 bước của Proposed Method](#4-kiến-trúc-7-bước-của-proposed-method)
5. [Mô tả chi tiết từng module](#5-mô-tả-chi-tiết-từng-module)
6. [Cài đặt](#6-cài-đặt)
7. [Chạy thử nhanh](#7-chạy-thử-nhanh)
8. [Bộ dữ liệu `dataset_temp_200/`](#8-bộ-dữ-liệu-dataset_temp_200)
9. [Công cụ trong `tools/`](#9-công-cụ-trong-tools)
10. [Kết quả & cách diễn giải](#10-kết-quả--cách-diễn-giải)
11. [Kiểm thử](#11-kiểm-thử)
12. [Giai đoạn tiếp theo: cài đặt Baseline](#12-giai-đoạn-tiếp-theo-cài-đặt-baseline)
13. [Quy ước phát triển](#13-quy-ước-phát-triển)

---

## 1. Framework này làm gì?

Framework thực hiện **kiểm chứng chương trình Python theo phong cách neuro-symbolic**:
LLM đóng vai trò sinh *reasoning trace* và *claim* bằng ngôn ngữ tự nhiên, còn
AST + Z3 đóng vai trò **trusted backbone** — không quyết định nào đến từ LLM.

**Input:** một hàm Python thuộc tập con QF-LIA, kèm các câu lệnh `assert`.

**Output:** với mỗi claim, một trong sáu verdict:

| Verdict | Ý nghĩa |
|---------|---------|
| `VERIFIED` | Claim đúng trên **mọi** đường đi tới nút AST đích |
| `COUNTEREXAMPLE` | Z3 tìm được mô hình vi phạm tại đúng nút đích |
| `UNREACHABLE` | Không đường đi nào tới được nút đích |
| `UNSUPPORTED` | Cổng neo AST **từ chối** claim (không neo được vào nút nào) |
| `TRANSLATION_ERROR` | Không dịch được claim thành biểu thức SMT |
| `UNKNOWN_TIMEOUT` | Coverage không đầy đủ → **không dám kết luận** |

> `UNKNOWN_TIMEOUT` là một quyết định thiết kế quan trọng: khi vòng lặp được
> xấp xỉ hữu hạn, framework **không** tuyên bố `VERIFIED` — nó báo rằng bằng
> chứng chưa đủ. Đây là ranh giới giữa "chứng minh" và "đoán".

---

## 2. Triết lý thiết kế: AST là trusted backbone

Điểm khác biệt cốt lõi so với các phương pháp LLM-as-a-Judge thuần:

```
        LLM (không tin được)                    Trusted backbone
   ┌──────────────────────────┐            ┌────────────────────────┐
   │  sinh CoT + claim (NL)   │            │  AST: NodeId, scope    │
   │  tự phê bình, tự sửa    │  ─────────▶ │  Z3: reachability +    │
   │  sinh invariant          │            │       validity          │
   └──────────────────────────┘            │  CPython: replay CE    │
                                           └────────────────────────┘
```

- **Claim phải neo được vào một nút AST cụ thể.** Không neo được → `UNSUPPORTED`
  → đây chính là nguồn của **Hallucination Rate** (`HR = 1.81%`).
- **Hai truy vấn Z3 cho mỗi claim**: (1) *reachability* — có đường đi nào tới
  nút đích không; (2) *validity* — claim có đúng trên mọi đường đi đó không.
  → Đây là trục **REACHABILITY** mà không baseline nào khác trả lời được (RQ3).
- **Counterexample phải được CPython tái hiện** mới tính là đáng tin
  → **CRVR = 88.0%** (so với 40.0% của LLM-as-a-Judge).

> **Lưu ý về mô hình tin cậy:** LLM chỉ được phép *đề xuất*; **quyết định**
> luôn do executor ký hiệu đáng tin cùng Z3 đưa ra. Đây là lý do framework
> có thể báo `COUNTEREXAMPLE` với bằng chứng cụ thể (mô hình Z3 + dòng lỗi
> replay), thay vì chỉ một nhãn.

---

## 3. Cấu trúc thư mục

```
prototype/
├── src/astverifier/                 # Package Python chuẩn (PEP 8, src-layout)
│   ├── __init__.py                  # Re-export API cấp cao
│   ├── core/                        # ★ CORE PROPOSED METHOD
│   │   ├── __init__.py
│   │   ├── pipeline.py      (714 d.) # Orchestrator: subset → … → roll-up
│   │   ├── executor.py      (753 d.) # Trusted symbolic executor + CFG
│   │   ├── binding.py       (270 d.) # Anchor gate: neo claim vào nút AST
│   │   ├── obligations.py   (129 d.) # Hai truy vấn Z3: reachability + validity
│   │   ├── invariants.py    (561 d.) # Sinh & kiểm chứng loop invariant
│   │   └── feedback.py      (456 d.) # Vòng retrospective feedback
│   ├── shared/                      # ★ SCHEMAS & UTILITIES dùng chung
│   │   ├── __init__.py
│   │   ├── schemas.py       (130 d.) # StatusEnum, ClaimResult, ProgramResult
│   │   ├── subset.py        (450 d.) # Cổng QF-LIA + trích claim từ assert
│   │   ├── anchors.py       (118 d.) # NodeId ổn định cho nút AST
│   │   ├── replay.py        (271 d.) # Replay counterexample trên CPython thật
│   │   └── metrics.py       (143 d.) # FDR / F1 / CRVR / HR / token / cost
│   └── llm/                         # Adapter Gemini (chỉ gọi, không quyết định)
│       ├── __init__.py
│       └── gemini.py        (215 d.) # Sinh CoT + claim, đếm token
│
├── dataset_temp_200/                # ★ Bộ benchmark tạm (200 chương trình)
│   ├── README.md                    # Quy tắc sinh, chính sách nhãn, hạn chế
│   ├── manifest.json                # Ground truth index (200 entries)
│   └── programs/{g1..g5}/           # 200 file .py, mỗi file 1 assert
│
├── tools/                           # Script dòng lệnh
│   ├── build_temp200.py             # Sinh + kiểm chứng bộ 200 mẫu
│   ├── smoke_temp200.py             # Smoke test pipeline trên bộ 200 mẫu
│   └── replay_curated.py            # Replay bộ curated cũ
│
├── tests/                           # 27 test (pytest)
│   ├── test_extensions.py   (306 d.)
│   └── test_regressions.py  (198 d.)
│
├── pyproject.toml                   # Metadata + cấu hình ruff/pytest
└── README.md                        # File này
```

> **Vì sao tách `core/` và `shared/`?**
> `core/` = **Proposed Method** — logic riêng, không baseline nào được chạm tới.
> `shared/` = **hợp đồng dữ liệu & tiện ích** — cả Proposed lẫn 14 Baseline
> đều dùng chung. Đây là ranh giới mà `Information/BASELINE_DISCOVERY.md`
> đề xuất để cài đặt Baseline ở giai đoạn sau mà không sửa lại code lõi.

### 3.1. Quy ước import

```python
from astverifier.core.pipeline import run_pipeline, BASELINE_AST_ANCHORED
from astverifier.shared.schemas import ProgramResult, ClaimResult, StatusEnum
from astverifier.shared.subset import check_and_normalize
from astverifier.shared.metrics import aggregate, load_ground_truth
```

Cài package ở chế độ editable (`pip install -e .`) hoặc đặt `PYTHONPATH=src`.

---

## 4. Kiến trúc 7 bước của Proposed Method

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │  1. SAFE SUBSET        subset.py       lọc tập con QF-LIA            │
   │  2. AST ANCHOR        anchors.py      gán NodeId ổn định cho nút    │
   │     + binding.py      binding.py      Anchor Gate: claim → nút AST  │
   │  3. TRUSTED SYMBOLIC  executor.py     dựng CFG, liệt kê đường đi     │
   │     EXECUTION                                        có trạng thái  │
   │  4. LLM CoT           llm/gemini.py   sinh reasoning trace + claim  │
   │  5. INVARIANT         invariants.py   sinh & kiểm chứng invariant   │
   │  6. TWO-QUERY Z3      obligations.py  reachability + validity      │
   │  7. RETROSPECTIVE     feedback.py     vòng tự sửa sau khi có CE    │
   │     FEEDBACK                                                        │
   │                                                                     │
   │              shared/replay.py — CPython tái hiện counterexample      │
   │              shared/metrics.py — roll-up FDR / F1 / CRVR / HR        │
   └──────────────────────────────────────────────────────────────────────┘
```

**Bảng đường đi dữ liệu trong `pipeline.py`:**

| Bước | Hàm | Input | Output | Nếu thất bại |
|------|-----|-------|--------|--------------|
| 1 | `check_and_normalize` | source | `SubsetResult` | `rejected_by_subset=True`, **không** vào solver |
| 2 | `assign_ids` + `extract_asserts` + `bind_llm_claims` | tree | `list[Claim]` | Claim không neo → `UNSUPPORTED` (đếm vào HR) |
| 3 | `TrustedSymbolicExecutor.build_all_transitions` | tree + ids | `dict[NodeId, Transition]` | Trả `TRANSLATION_ERROR` cho toàn chương trình |
| 4 | `generate_cot_and_claims` | source | `LLMResult` | Lỗi → claim `TRANSLATION_ERROR` (không làm hỏng run) |
| 5 | `LoopInvariantGenerator` | loop state | `InvariantVerification` | Không có → bỏ qua, không chặn pipeline |
| 6 | `build_obligations` + `run_reachability` + `run_validity` | claim + path | `TwoQueryObligations` | Z3 `unknown` → `UNKNOWN_TIMEOUT` |
| 7 | `RetrospectiveFeedbackLoop.run_loop` | CE + replay | claim đã sửa | Lỗi → giữ nguyên CE (fail-safe) |

> **Nguyên tắc "fail-safe":** bước 5 và bước 7 là **tùy chọn**. Thiếu API key
> hoặc LLM lỗi thì pipeline vẫn chạy và vẫn trả kết quả hợp lệ — chỉ mất
> đi lớp cải thiện, không mất đi kết quả chính.

---

## 5. Mô tả chi tiết từng module

### 5.1. `core/pipeline.py` — Orchestrator

Điểm vào duy nhất của toàn framework:

```python
result = run_pipeline(source, program_id="demo", source_path="demo.py")
```

**Tham số quan trọng:**

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `baseline` | `"AST_ANCHORED"` | **Chỉ** chấp nhận giá trị này — baseline khác phải cài ở giai đoạn sau |
| `use_llm` | `False` | `True` → gọi Gemini sinh claim; `False` → dùng `assert` trong source |
| `suggested_claims` | `None` | Inject claim đã tính sẵn (dùng cho Shared Claim Pool) |
| `do_replay` | `True` | Tái hiện counterexample trên CPython thật |
| `enable_feedback` | `True` | Bật vòng retrospective (cần API key) |
| `enable_invariants` | `True` | Bật bộ sinh invariant (cần API key) |

> **`baseline` chỉ nhận `"AST_ANCHORED"`** là một chủ ý thiết kế: đảm bảo
> Proposed Method không vô tình dùng đường tắt của baseline nào, và đảm bảo
> logic của baseline sẽ được cài **bên ngoài** `core/`, không trộn vào.

### 5.2. `core/executor.py` — Trusted symbolic executor

753 dòng, phần lớn nhất của framework. Trách nhiệm:

- **Dựng CFG** từ AST, theo dõi biến nào chắc chắn đã được gán (`definite
  assignment`) để phát hiện claim dùng biến chưa khởi tạo.
- **Liệt kê đường đi** (`enumerate_paths`) với trạng thái ký hiệu (Z3 expr).
- **`enumerate_target_paths(anchor)`** — chỉ giữ những đường đi thực sự tới
  *đúng nút AST* mà claim neo vào. Đây là điểm khác biệt so với baseline
  chỉ kiểm tra "công thức có nhất quán không".
- **`build_reachability`** — sinh điều kiện Z3 cho một đường đi cụ thể.
- **Bounded loop approximation** — vòng lặp được xấp xỉ bằng số vòng hữu hạn;
  khi coverage không đầy đủ, framework trả `UNKNOWN_TIMEOUT` thay vì `VERIFIED`.

### 5.3. `core/binding.py` — Anchor Gate

Nơi quyết định claim có **được neo** hay không:

| Tình trạng | Status | Đếm vào HR |
|------------|--------|-----------|
| Claim có biểu thức hợp lệ + biến trong scope + dòng khớp | `ANCHORED` | không |
| Không tìm được dòng phù hợp | `UNSUPPORTED` | **có** |
| Biến nằm ngoài scope tại điểm neo | `UNSUPPORTED` | **có** |
| Không parse được thành AST | `TRANSLATION_ERROR` | **có** |

> Đây chính là **cổng chặn ảo giác**: một LLM có thể "viết" một claim nghe
> hợp lý nhưng không gắn được vào bất kỳ nút nào của chương trình — và nó bị
> chặn, thay vì được chấp nhận rồi báo sai.

### 5.4. `core/obligations.py` — Hai truy vấn Z3

```python
def build_obligations(claim, reachability, executor) -> TwoQueryObligations
def run_reachability(obligations) -> (kind, elapsed, model)
def run_validity(obligations)    -> (kind, elapsed, model)
```

| Truy vấn | Hỏi gì | Kết quả |
|----------|---------|---------|
| **Reachability** | `Path ∧ Claim` có thỏa mãn được không? | `unsat` → đường đi này không tới được nút đích |
| **Validity** | `Path ∧ ¬Claim` có thỏa mãn được không? | `sat` → tồn tại input vi phạm → **COUNTEREXAMPLE** kèm mô hình |

> **Vì sao 2 truy vấn thay vì 1?** Chỉ kiểm tra *validity* sẽ báo
> `COUNTEREXAMPLE` cho một claim nằm trong nhánh không bao giờ chạy tới — tức
> là báo động giả. Truy vấn *reachability* loại bỏ đúng trường hợp đó, và khi
> **mọi** đường đi đều `unsat`, claim mới thực sự là `UNREACHABLE`.
> Đây là cơ sở kỹ thuật của **RQ3**.

### 5.5. `core/invariants.py` — Bộ sinh loop invariant

Sinh candidate invariant (dạng constant / linear / quadratic / relational),
rồi **kiểm chứng bằng Z3**: base case + inductive step + termination.
Có parser SMT-LIB2 nhúng để đọc candidate từ LLM.

### 5.6. `core/feedback.py` — Vòng retrospective

Chạy **sau khi** có counterexample đã replay thành công:

```
CE (Z3) → replay (CPython) → LLM phê bình → claim sửa → kiểm lại
         ↓ nếu claim sửa được verify → VERIFIED
         ↓ nếu vẫn sai → giữ COUNTEREXAMPLE (fail-safe)
```

Theo dõi `feedback_iterations`, `feedback_resolution` trong `CounterexampleInfo`.

### 5.7. `shared/schemas.py` — Hợp đồng dữ liệu

**Đây là hợp đồng mọi Baseline phải tuân theủ.**

```python
class StatusEnum(StrEnum):
    VERIFIED | COUNTEREXAMPLE | UNREACHABLE
    UNSUPPORTED | TRANSLATION_ERROR | UNKNOWN_TIMEOUT

class ClaimResult(BaseModel):
    claim_id: str
    claim_text: str
    status: StatusEnum
    anchor: AnchorInfo            # node_ids, source_lines, has_grounding
    counterexample: CounterexampleInfo | None = None
    z3_stats: dict[str, float] = {}
    reason: str = ""
    explanation: str = ""

class ProgramResult(BaseModel):
    program_id: str
    source_path: str
    n_claims / n_verified / n_counterexample / n_unreachable
    n_unsupported / n_translation_error / n_unknown_timeout
    claim_results: list[ClaimResult]
    elapsed_seconds: float
    total_tokens_in / total_tokens_out: int
    baseline: str
```

> **`has_grounding` và `replay_verdict` là `Optional`.** Đây là cách hiện
> thực hoá nguyên tắc **"N/A ≠ 0"**: baseline không có cổng neo sẽ để
> `has_grounding=False` → `Hallucination Rate = N/A` (**không phải 0**).
> Baseline không replay sẽ để `replay_verdict=None` → `CRVR = N/A`.

### 5.8. `shared/subset.py` — Cổng QF-LIA

Whitelist chi tiết ở `dataset_temp_200/README.md` §2. Tóm tắt:

- **Chấp nhận:** `int`/`bool`, `+ - * // %` (với hằng số), `if/while/for`,
  `break/continue/return/assert`, so sánh, phép logic, `abs/min/max/range`.
- **Từ chối:** `float`/`str`/`list`/`dict`, `class`, `import`, `print/input`,
  nhân phi tuyến (`n * (n-1)`), attribute access, subscript, lambda, f-string.

Đồng thời **trích claim** từ mọi `assert` trong source → `ClaimInfo`
(claim_id, source_line, expr_source, expr_node).

### 5.9. `shared/replay.py` — Replay trên CPython thật

Chạy chương trình gốc trong subprocess với `sys.settrace`, theo dõi biến tại
`target_line`, đánh giá lại claim tại đó. Kết quả:

| `replay_verdict` | Ý nghĩa |
|------------------|---------|
| `True` | CE tái hiện được → claim thực sự sai (đáng tin) |
| `False` | CE không tái hiện → CE của Z3 không đáng tin |
| `None` | Không replay (baseline không hỗ trợ) → CRVR = N/A |

### 5.10. `shared/metrics.py` — Roll-up metric

```python
metrics = aggregate(results, ground_truth)
```

| Metric | Công thức | Ghi chú |
|--------|-----------|---------|
| FDR | `FP / (TP + FP)` | `None` nếu `TP+FP == 0` |
| Accuracy | `(TP + TN) / total` | Lớp dương = `VERIFIED` |
| Precision / Recall / F1 | chuẩn | Lớp dương = `VERIFIED` |
| CRVR | `# replay=True / # replay được báo` | `None` nếu không có replay nào |
| HR | `(UNSUPPORTED + TRANSLATION_ERROR) / n_claims` | `None` nếu không có cổng neo |
| Token / Cost | trung bình mỗi chương trình | `PRICE_IN_PER_1K = 0.005`, `PRICE_OUT_PER_1K = 0.015` |

> **Lưu ý về `HR`:** hàm `aggregate` hiện trả `hr = 0.0` khi không có claim.
> Khi cài Baseline, **phải** sửa thành `None` cho baseline không có cổng neo
> theo nguyên tắc "N/A ≠ 0" — xem `Information/BASELINE_DISCOVERY.md` §5.3.

---

## 6. Cài đặt

**Yêu cầu:** Python 3.11+ (khuyến nghị 3.13/3.14), `z3-solver`, `pydantic` v2.

```powershell
# Cài trong virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[llm,dev]"

# Hoặc cài tối thiểu
pip install z3-solver pydantic pytest ruff
pip install google-genai        # chỉ cần khi chạy LLM
```

**Cấu hình API key** (chỉ cần cho LLM):

```powershell
$env:GEMINI_API_KEY = "AIzaSy..."
# hoặc nạp từ file cục bộ
$env:GEMINI_API_KEY = (Get-Content -Raw geminiapi.txt).Trim()
```

> **Không commit file key.** `.env` và `geminiapi.txt` đã nằm trong `.gitignore`.

---

## 7. Chạy thử nhanh

### 7.1. Smoke test tối thiểu (không cần API key)

```powershell
cd prototype
$env:PYTHONPATH = "src"
python tools/smoke_temp200.py --size 20 --no-feedback
```

Kết quả mẫu (20 chương trình ngẫu nhiên, seed=7):

```
g1_accum_01_b      gt=COUNTEREXAMPLE  got=COUNTEREXAMPLE  Z3 found a violating model...
g1_accum_02        gt=VERIFIED        got=VERIFIED        claim holds on every complete path...
g1_negate_02_b     gt=COUNTEREXAMPLE  got=COUNTEREXAMPLE  Z3 found a violating model...
...
--- status counts ---
  COUNTEREXAMPLE       13
  VERIFIED             7
```

> `--no-feedback` tắt vòng retrospective (gọi LLM) — pipeline chạy offline,
> mỗi chương trình ~1 giây.

### 7.2. Chạy trực tiếp trên một đoạn code

```powershell
$env:PYTHONPATH = "src"
python -c "
from astverifier.core.pipeline import run_pipeline
src = '''
def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n
    return s
'''
r = run_pipeline(src, program_id='demo')
print(r.claim_results[0].status.value, '|', r.claim_results[0].reason)
"
```

### 7.3. Với LLM sinh claim

```powershell
$env:PYTHONPATH = "src"
$env:GEMINI_API_KEY = (Get-Content -Raw geminiapi.txt).Trim()
python -c "
from astverifier.core.pipeline import run_pipeline
src = open('dataset_temp_200/programs/g3_loops/g3_count_01.py', encoding='utf-8').read()
r = run_pipeline(src, program_id='g3_count_01', use_llm=True,
                 gemini_model='gemini-3.5-flash')
print(r.n_claims, r.claim_results[0].status.value)
"
```

### 7.4. Roll-up metric trên nhiều chương trình

```python
import json
from pathlib import Path
from astverifier.core.pipeline import run_pipeline
from astverifier.shared.metrics import aggregate, load_ground_truth

DATASET = Path("dataset_temp_200")
manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))

results = []
for entry in manifest["programs"]:
    source = (DATASET / entry["path"]).read_text(encoding="utf-8")
    results.append(run_pipeline(source, program_id=entry["program_id"]))

ground_truth = {e["program_id"]: e["expected_verdict"] for e in manifest["programs"]}
m = aggregate(results, ground_truth)
print(f"F1={m.f1:.3f} FDR={m.fdr:.3f} HR={m.hr:.4f} CRVR={m.crvr}")
```

---

## 8. Bộ dữ liệu `dataset_temp_200/`

200 chương trình QF-LIA, ground truth bằng **execution oracle** (không LLM).

| Nhóm | Số mẫu | Nội dung |
|------|---------|----------|
| `g1_arithmetic/` | 40 | Biểu thức số học & tuyến tính đơn giản |
| `g2_conditionals/` | 40 | Rẽ nhánh & điều kiện đường đi |
| `g3_loops/` | 50 | Vòng lặp đơn & loop invariants |
| `g4_nested/` | 40 | Vòng lặp lồng nhau & cận sâu |
| `g5_bugs/` | 30 | Bug thật / Unreachable code |

| Nhãn | Số mẫu |
|------|--------|
| `VERIFIED` | 85 |
| `COUNTEREXAMPLE` | 100 |
| `UNREACHABLE` | 15 |

**Điểm khác biệt so với `dataset_curated` (68 mẫu):**

| Tiêu chí | `dataset_curated` | `dataset_temp_200` |
|----------|-------------------|-------------------|
| Nhãn ground truth | không nhất quán (38/30 vs 41/27 vs 45/32) | **execution oracle**, 100% khớp hành vi quan sát được |
| Chương trình có `assert` | 19.8% | **100%** (đúng 1 assert) |
| Cỡ mẫu | 68 | 200 |
| Phân loại lỗi | không có | 15 loại `bug_type` |
| Precondition | không ghi | ghi trong manifest |

> Chi tiết đầy đủ (quy tắc sinh, chính sách nhãn, miền oracle, hạn chế):
> **`dataset_temp_200/README.md`**

---

## 9. Công cụ trong `tools/`

| Script | Mục đích | Lệnh |
|--------|----------|------|
| `build_temp200.py` | Sinh & kiểm chứng bộ 200 mẫu (subset gate + execution oracle) | `python tools/build_temp200.py` |
| `smoke_temp200.py` | Smoke test pipeline trên ngẫu nhiên N mẫu | `python tools/smoke_temp200.py --size 20 --no-feedback` |
| `replay_curated.py` | Replay bộ `dataset_curated` cũ | `python tools/replay_curated.py` |

**`build_temp200.py` là idempotent**: chạy lại sẽ ghi đè `programs/` và
`manifest.json`; nếu có bất kỳ chương trình nào lệch với oracle thì **build
fail** và không ghi manifest.

---

## 10. Kết quả & cách diễn giải

### 10.1. Số liệu hiện tại (tập `dataset_curated`, 68 mẫu)

| Phương pháp | F1 | Accuracy | Precision | Recall | FDR | CRVR | HR | Thời gian TB |
|-------------|-----|----------|-----------|--------|-----|------|----|-------------|
| A — SatLM Direct | 0.635 | 54.4% | 57.4% | 71.1% | 42.6% | N/A | 5.56% | 1.41 s |
| D — LLM-as-a-Judge | 0.696 | 58.8% | 59.3% | 84.2% | 40.7% | 40.0% | N/A | 1.92 s |
| **Proposed** | 0.617 | 54.4% | 58.1% | 65.8% | 41.9% | **88.0%** | **1.81%** | 7.71 s |

### 10.2. Đọc đúng các con số này

**Điều 1 — Proposed thua D về F1, nhưng thắng về độ tin cậy.**

| Metric | D | Proposed | Ý nghĩa |
|--------|---|----------|---------|
| CRVR | 40.0% | **88.0%** | Counterexample của Proposed tái hiện được gần như luôn; của D chỉ 2/5 |
| HR | N/A | **1.81%** | Proposed có cổng neo → đo được; D không có → **N/A, không phải 0** |

> **Đây chính là điểm cốt lõi của đồ án.** Baseline D có Recall cao (84.2%)
> nhưng 60% counterexample của nó **không tái hiện được** — tức là nó báo
> động giả. Proposed có Recall thấp hơn nhưng khi báo `COUNTEREXAMPLE` thì
> đúng 88%. Trong kiểm chứng chương trình, **tín hiệu âm sai còn tệ hơn tín
> hiệu dương thiếu**.

**Điều 2 — Không được đọc `HR = N/A` của D là "D không ảo giác".**

D không có cổng neo AST → **không có cơ chế đo** → `N/A`. In `0` sẽ khiến
reviewer hiểu nhầm là "D không ảo giác", tức là D tốt hơn Proposed về mặt
này. Đây là lý do nguyên tắc **N/A ≠ 0** là bất di bất dịch.

**Điều 3 — F1 ở hai mức khác nhau.**

Proposed sinh **6.5 claim/chương trình**, D sinh **1.2**. F1 ở mức claim-level
và program-level cho ra số khác nhau. Báo cáo cuối **phải trình bày cả hai**;
nếu không, con số nào được chọn cũng sẽ bị chất vấn.

### 10.3. Rủi ro đã biết (nêu thẳng trong báo cáo)

| Rủi ro | Mức | Xử lý trong phase Baseline |
|--------|-----|----------------------------|
| **Baseline N (Invariant + SMT, không neo AST) có thể ≈ Proposed** | 🔴 | Nếu xảy ra → framing đóng góp phải đổi từ "AST anchor là chìa khóa" sang "grounding + reachability" |
| **Baseline D đang bị đánh giá thấp** (tắt self-consistency vì hết quota) | 🔴 | Baseline G (n=5, majority vote) phải chạy trước; nếu G > D đáng kể thì kết luận "Proposed thua D" là do lỗi cấu hình |
| **Chưa có baseline không dùng LLM** | 🟡 | Baseline J (fuzzing) & L (symbolic thuần) — "sanity floor", chạy offline 0 token |
| **Ground truth của bộ curated không nhất quán** | 🟡 | Dùng `dataset_temp_200` có oracle; đồng bộ với bộ chính của Tâm |
| **14 baseline × 200 mẫu vượt quota Gemini** | 🟡 | 6 baseline cốt lõi chạy đầy đủ, 8 cái chạy tập con 50 mẫu |

---

## 11. Kiểm thử

```powershell
$env:PYTHONPATH = "src"

python -m pytest tests -q          # 27 test
python -m ruff check src tools tests
python -m compileall -q src tools tests
```

| Tệp test | Phủ |
|----------|-----|
| `test_extensions.py` | Subset gate, anchor binding, Z3 obligations, replay, metric |
| `test_regressions.py` | Các lỗi đã từng xảy ra (cache, undefined name, zip strictness…) |

---

## 12. Giai đoạn tiếp theo: cài đặt Baseline

Bối cảnh đầy đủ: `Information/BASELINE_DISCOVERY.md` (14 baseline, 8 trục cơ chế,
ánh xạ 4 RQ, 7 phép thử ablation) và `Information/BASELINE_DISCOVERY_PLAN.md`.

### 12.1. Nguyên tắc bắt buộc

| # | Nguyên tắc | Cơ chế hiện thực hoá |
|---|------------|------------------------|
| 1 | **Baseline không sửa code trong `core/`** | Cài ở `astverifier/baselines/`; dùng `shared/` |
| 2 | **Cùng một giao diện** | Mỗi baseline trả `ProgramResult` của `shared/schemas.py` |
| 3 | **Metric không đo được → `None`** | `has_grounding=False` → HR = N/A; `replay_verdict=None` → CRVR = N/A |
| 4 | **Cùng một bộ claim** | `Shared Claim Pool` trích 1 lần, mọi baseline dùng chung |
| 5 | **Báo cáo 2 mức** | claim-level **và** program-level |

### 12.2. Thứ tự thực hiện đề xuất

| Bước | Việc | Ghi chú |
|------|------|---------|
| 1 | **N, J, L** — baseline offline, 0 token | Chạy được ngay, không chờ quota; N là phép thử quyết định |
| 2 | **G, E** — cần LLM nhưng rẻ | G quyết định tính công bằng của baseline D |
| 3 | **A, B, C, D** — tái tạo số liệu hiện có | Trên `dataset_temp_200` để so sánh trực tiếp |
| 4 | **H, F, M, K** — cơ chế tự sửa | Phục vụ ablation AB-3, AB-4 |
| 5 | **B+, C+** — biến thể nâng cấp | Chỉ khi cần thêm điểm so sánh |

### 12.3. Giao diện tối thiểu cho một baseline

```python
# astverifier/baselines/n_invariant_smt.py
class BaselineN(BaseBaseline):
    id = "N"
    supports_grounding = False      # → HR = N/A
    supports_replay = True          # → CRVR đo được
    supports_reachability = False   # → UNREACHABLE = N/A

    def run(self, program: ProgramSpec, claims: list[Claim]) -> ProgramResult:
        ...
```

> **`supports_*` là cơ chế hiện thực hoá nguyên tắc "N/A ≠ 0" ở tầng
> registry** — thay vì mỗi baseline tự nhớ phải để `None`.

---

## 13. Quy ước phát triển

| Mục | Quy ước |
|-----|---------|
| **Style** | PEP 8, `ruff` với `E/W/F/I/N/UP/B/C4/SIM`, line length 88 |
| **Typing** | `from __future__ import annotations`, `X | None` (không `Optional[X]`) |
| **Enum** | `StrEnum` (Python 3.11+) để serialize JSON trực tiếp |
| **Docstring** | Google-style, mô tả **tại sao** chứ không chỉ **cái gì** |
| **Test** | pytest, `pythonpath = ["src"]` cấu hình sẵn trong `pyproject.toml` |
| **Bất biến** | Baseline **không** được import từ `core/`; `core/` **không** được import từ `baselines/` |
| **Fail-safe** | Lỗi ở bước tùy chọn (feedback, invariants) **không** được làm hỏng run chính |
| **Metric** | Không đo được → `None` → in `N/A`. **Không bao giờ** in `0` thay cho `N/A` |

---

## Tài liệu liên quan

| Tài liệu | Nội dung |
|----------|----------|
| `Information/BASELINE_DISCOVERY.md` | 14 baseline, ma trận 8 trục, ánh xạ RQ, quy tắc N/A |
| `Information/BASELINE_DISCOVERY_PLAN.md` | Kế hoạch chi tiết giai đoạn Baseline (temp200, harness) |
| `Information/BASELINE_DISCOVERY_REPORT.md` | Báo cáo tiến độ & định hướng implement |
| `Information/BASELINE_DISCOVERY_PRESENTATION.md` | Slide thuyết trình cho team |
| `Information/DACN-Proposal.md` | Đề cương nghiên cứu (RQ, §11 metrics) |
| `dataset_temp_200/README.md` | Quy tắc sinh dataset, chính sách nhãn, hạn chế |

---

## License

Đồ án chuyên ngành — chỉ sử dụng cho mục đích học thuật.
