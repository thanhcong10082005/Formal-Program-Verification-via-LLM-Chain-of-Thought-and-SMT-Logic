# AST-Anchored Neuro-Symbolic Verification Framework

> Đồ án chuyên ngành — *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

---

## 1. Framework này làm gì?

Framework thực hiện **kiểm chứng chương trình Python theo phong cách neuro-symbolic**:
LLM đóng vai trò sinh *reasoning trace* và *claim* bằng ngôn ngữ tự nhiên, còn
AST + Z3 đóng vai trò **trusted backbone** — không quyết định nào đến từ LLM.

**Input:** một hàm Python thuộc tập con QF-LIA, kèm các câu lệnh `assert`.

**Output:** với mỗi claim, một trong sáu verdict:


| Verdict             | Ý nghĩa                                                     |
| ------------------- | ----------------------------------------------------------- |
| `VERIFIED`          | Claim đúng trên **mọi** đường đi tới nút AST đích           |
| `COUNTEREXAMPLE`    | Z3 tìm được mô hình vi phạm tại đúng nút đích               |
| `UNREACHABLE`       | Không đường đi nào tới được nút đích                        |
| `UNSUPPORTED`       | Cổng neo AST **từ chối** claim (không neo được vào nút nào) |
| `TRANSLATION_ERROR` | Không dịch được claim thành biểu thức SMT                   |
| `UNKNOWN_TIMEOUT`   | Coverage không đầy đủ → **không dám kết luận**              |


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
   │  tự phê bình, tự sửa     │  ───────▶ │  Z3: reachability +    │
   │  sinh invariant          │            │       validity         │
   └──────────────────────────┘            │  CPython: replay CE    │
                                           └────────────────────────┘
```

- **Claim phải neo được vào một nút AST cụ thể.** Không neo được → `UNSUPPORTED`
→ đây chính là nguồn của **Hallucination Rate** (`HR`).
- **Hai truy vấn Z3 cho mỗi claim**: (1) *reachability* — có đường đi nào tới
nút đích không; (2) *validity* — claim có đúng trên mọi đường đi đó không.
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
│   ├── core/                        # CORE PROPOSED METHOD
│   │   ├── pipeline.py              # Orchestrator: subset → … → roll-up
│   │   ├── executor.py              # Trusted symbolic executor + CFG
│   │   ├── binding.py               # Anchor gate: neo claim vào nút AST
│   │   ├── obligations.py           # Hai truy vấn Z3: reachability + validity
│   │   ├── invariants.py            # Sinh & kiểm chứng loop invariant
│   │   └── feedback.py              # Vòng retrospective feedback
│   ├── shared/                      #  SCHEMAS & UTILITIES dùng chung
│   │   ├── schemas.py               # StatusEnum, ClaimResult, ProgramResult
│   │   ├── subset.py                # Cổng QF-LIA + trích claim từ assert
│   │   ├── anchors.py               # NodeId ổn định cho nút AST
│   │   ├── replay.py                # Replay counterexample trên CPython thật
│   │   ├── metrics.py               # FDR / F1 / CRVR / HR / token / cost
│   │   ├── claims.py                # Kiểu Claim + loader cho Shared Claim Pool
│   │   ├── preconditions.py         # Trích path-precondition từ nhánh if
│   │   ├── structure.py             # CFG skeleton + loop summary
│   │   ├── symexec.py               # Symbolic explorer (Z3)
│   │   ├── prover.py                # Prover chính xác (Z3/Spacer)
│   │   ├── oracle.py                # Oracle CPython (BVA + seed)
│   │   ├── oracle_runner.py         # Batch runner + checkpoint
│   │   └── hashing.py               # Hash AST / normalise
│   ├── baselines/                   # 14 BASELINE A–N (độc lập với core/)
│   │   ├── base.py                  # BaseBaseline (ABC) + ProgramSpec
│   │   ├── registry.py              # @register + get_baseline
│   │   ├── llm.py                   # LLMClient + StubClient + GeminiClient
│   │   ├── common.py                # Prompting / parsing dùng chung
│   │   └── a_direct_smt.py … n_invariant_smt.py 
│   └── llm/gemini.py                # Adapter Gemini (chỉ gọi, không quyết định)
│
├── data/bench_v1/                   # ★ BỘ 1.000 MẪU CHUẨN + Shared Claim Pool
│   ├── manifest.json                # Schema thống nhất + GT + precondition
│   ├── manifest_full.json           # Manifest trước khi gắn precondition
│   ├── claim_pool.json              # 1.000 claim dùng chung cho MỌI phương pháp
│   ├── sample_1000.txt              # Danh sách 1.000 path
│   └── sampling_report.json         # Phân bố claim × verdict + lịch sử lọc
│
├── dataset_temp_200/                # Bộ phát triển harness 200 mẫu (đã backup)
│
├── tools/                           # Script dòng lệnh
│   ├── build_ground_truth.py        # Gán nhãn GT: CPython oracle + Z3 (2 tầng)
│   ├── extract_preconditions.py     # Trích precondition từ nhánh if
│   ├── select_stratified_sample.py  # Lọc trùng + lấy 1.000 mẫu phân tầng
│   └── build_claim_pool.py          # Trích claim từ assert (0 token)
│
├── tests/                           # 109 test (pytest)
│   ├── test_baselines.py            # Contract test cho 14 baseline
│   ├── test_dataset_pipeline.py     # Tính đúng của pipeline dataset
│   ├── test_extensions.py
│   └── test_regressions.py
│
├── pyproject.toml                   # Metadata + cấu hình ruff/pytest
└── README.md                        # File này
```

> **Vì sao tách** `core/` **và** `shared/`**?**
> `core/` = **Proposed Method** — logic riêng, không baseline nào được chạm tới.
> `shared/` = **hợp đồng dữ liệu & tiện ích** — cả Proposed lẫn 14 Baseline
> đều dùng chung. Đây là ranh giới đảm bảo baseline là **phương pháp độc lập**,
> không mượn máy móc của Proposed Method.

### 3.1. Quy ước import

```python
from astverifier.core.pipeline import run_pipeline, BASELINE_AST_ANCHORED
from astverifier.shared.schemas import ProgramResult, ClaimResult, StatusEnum
from astverifier.shared.subset import check_and_normalize
from astverifier.shared.metrics import aggregate, load_ground_truth

from astverifier.baselines import get_baseline, all_baselines, StubClient
from astverifier.shared.claims import Claim, load_claim_pool
```

Cài package ở chế độ editable (`pip install -e .`) hoặc đặt `PYTHONPATH=src`.

---

## 4. 14 Baseline A–N — kết quả hiện thực

Mỗi baseline là một **phương pháp hoàn chỉnh và độc lập**: nhận cùng một
Shared Claim Pool, trả về cùng một schema `ProgramResult` mà Proposed Method dùng.


| Kết quả                            | Giá trị                                                                |
| ---------------------------------- | ---------------------------------------------------------------------- |
| Baseline đã hiện thực              | **14 / 14** (A–N) — 19 file, 2.956 dòng trong `astverifier/baselines/` |
| Contract test                      | **50 / 50 pass** (`tests/test_baselines.py`)                           |
| Toàn bộ test suite                 | **109 pass** · `ruff` **0 lỗi**                                        |
| Baseline chạy offline (0 token)    | **3** — J, K, L · còn 11 baseline cần LLM                              |
| Token Gemini đã tiêu khi hiện thực | **0** (contract test dùng `StubClient`)                                |
| Thay đổi vào `core/`               | **0** — Proposed Method không bị đụng tới                              |


### 4.1. Danh mục


| ID    | Tên                       | Cơ chế                                                                                       | Công cụ kiểm chứng | LLM |
| ----- | ------------------------- | -------------------------------------------------------------------------------------------- | ------------------ | --- |
| **A** | SatLM Direct SMT-LIB2     | Dịch chương trình + claim thành SMT-LIB2; solver kiểm `Constraints ∧ ¬Spec`                  | Z3                 | ✓   |
| **B** | Execution Grounding       | Chạy chương trình trên lưới input tất định dưới trace; mô hình chỉ nhìn *những gì đã xảy ra* | CPython            | ✓   |
| **C** | Structural Reasoning      | Skeleton điều khiển (CFG) + k-induction ở mức Python — **không** gọi Z3                      | CFG                | ✓   |
| **D** | LLM-as-a-Judge            | CoT zero-shot + sanity check biên (−1, 0, 1, MAX, MIN)                                       | LLM-only           | ✓   |
| **E** | Neuro-Symbolic (AutoSpec) | Mô hình đề xuất spec (pre/post/inv); checker kiểm; lỗi → refine, tối đa k=3 vòng             | checker            | ✓   |
| **F** | Agentic Multi-Step        | PLAN → ANALYZE → TEST (thực thi thật) → VERIFY → REFINE                                      | CPython            | ✓   |
| **G** | Self-Consistency          | Lấy mẫu n=5 ở temperature 0.7, majority vote; hòa → `UNKNOWN_TIMEOUT`                        | LLM-only           | ✓   |
| **H** | Self-Refine               | verdict → tự chê → sửa, 3 vòng                                                               | LLM-only           | ✓   |
| **I** | LLM-Guided PBT            | Mô hình đề xuất invariant → thành strategy của PBT; input tìm được phải replay               | CPython            | ✓   |
| **J** | Deterministic Fuzzing     | 256 input tất định (BVA + seed 42), chạy CPython, so với claim                               | CPython            | ✗   |
| **K** | Mutation Testing          | Tiêm mutant (constant shift / comparison swap / binop swap) → hỏi claim còn giữ không        | CPython            | ✗   |
| **L** | Symbolic Execution        | Thực thi ký hiệu thuần trên Z3/Spacer; liệt kê đường đi tới điểm assert                      | Z3                 | ✗   |
| **M** | Hoare Triple (NL)         | Mô hình viết `{P} c {Q}` bằng ngôn ngữ tự nhiên rồi tự đánh giá                              | LLM-only           | ✓   |
| **N** | Invariant Synthesis       | Mô hình đề xuất 1–3 invariant; Z3 kiểm base case + inductive step                            | Z3                 | ✓   |


**3 baseline offline** (J, K, L) chạy **0 token**; **11 baseline** cần LLM.

### 4.2. Bất biến "không lệ thuộp `core/`"

Baseline **không bao giờ** import `astverifier.core`. Đây là điều kiện tiên quyết để
phép so sánh có ý nghĩa — nếu baseline mượn máy móc của Proposed Method thì nó
không còn là phương pháp độc lập nữa.

Bất biến này được **cài đặt thành test tự động**, không phải bằng quy ước:

```python
# tests/test_baselines.py::test_no_baseline_imports_core
# Quét AST mọi file trong baselines/: mọi ast.Import / ast.ImportFrom
# chạm "core" đều bị flag. Import relative bị bỏ qua (vì relative
# trong baselines/ không thể với tới core/).
```

### 4.3. Nguyên tắc "N/A ≠ 0"

Mỗi baseline khai báo tường minh 4 cờ khả năng. Metric không đo được → `None`
→ in `N/A`, **không bao giờ** in `0` thay cho `N/A`.


| ID    | Hallucination Rate | CRVR    | UNREACHABLE detected | Precondition |
| ----- | ------------------ | ------- | -------------------- | ------------ |
| A     | **N/A**            | **N/A** | **N/A**              | ✓            |
| B     | **N/A**            | ✓       | **N/A**              | ✓            |
| C     | **N/A**            | **N/A** | **N/A**              | ✓            |
| D     | **N/A**            | ✓       | **N/A**              | ✓            |
| E     | **N/A**            | **N/A** | **N/A**              | ✓            |
| F     | **N/A**            | ✓       | **N/A**              | ✓            |
| G     | **N/A**            | **N/A** | **N/A**              | ✓            |
| H     | **N/A**            | **N/A** | **N/A**              | ✓            |
| I     | **N/A**            | ✓       | **N/A**              | ✓            |
| J     | **N/A**            | ✓       | **N/A**              | ✓            |
| K     | **N/A**            | ✓       | **N/A**              | ✗            |
| **L** | **N/A**            | ✓       | **✓**                | ✓            |
| M     | **N/A**            | **N/A** | **N/A**              | ✓            |
| N     | **N/A**            | **N/A** | **N/A**              | ✓            |


- **HR = N/A cho cả 14.** Không baseline nào có *cổng neo AST* — kể cả L, dù L
dùng Z3. Không có cổng neo thì **không có khái niệm "bịa"** ⇒ HR không đo được,
không phải bằng 0. Đây là điểm so sánh then chốt với Proposed Method (có cổng neo).
- **CRVR đo được cho 7 baseline** (B, D, F, I, J, K, L) — tức là baseline **thực sự
chạy** chương trình và tạo ra input cụ thể, rồi **replay trên CPython thật**.
7 baseline còn lại chỉ *nói* là có counterexample nhưng không có input cụ thể.
- **Chỉ L trả lời được reachability** — nên chỉ L hỗ trợ `supports_reachability`.

### 4.4. Giao diện chung — `ProgramSpec` / `ProgramResult`

```python
@dataclass
class ProgramSpec:
    program_id: str
    source: str
    source_path: str = ""
    claims: list[Claim] = field(default_factory=list)
    precondition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
@register
class SomeBaseline(BaseBaseline):
    id = "X"
    name = "…"
    supports_grounding = False      # → HR = N/A
    supports_replay = False          # → CRVR = N/A
    supports_reachability = False    # → UNREACHABLE = N/A
    supports_precondition = True
    requires_llm = True

    def run(self, spec: ProgramSpec) -> ProgramResult:
        ...
```

`registry.EXPECTED_IDS` là **nguồn sự thật duy nhất** cho "baseline nào tồn tại";
`@register` ném `ValueError` nếu ID trùng hoặc rỗng.

### 4.5. Cổng LLM — chạy offline được

```python
class LLMClient(Protocol):
    def complete(self, prompt: str, *, system: str = "",
                 temperature: float = 0.0) -> LLMReply: ...
```


| Cài đặt        | Dùng khi nào                              | Token |
| -------------- | ----------------------------------------- | ----- |
| `StubClient`   | Contract test — trả reply JSON cố định    | **0** |
| `GeminiClient` | Chạy thật, đếm token qua `usage_metadata` | Thật  |


Baseline **không** import `astverifier.llm.gemini` trực tiếp — chúng nhận client
qua constructor. Đó là lý do contract test chạy offline hoàn toàn.

### 4.6. Không baseline nào bao giờ crash

Cả 14 baseline bọc logic trong `try/except Exception` và báo lỗi **bên trong**
`ClaimResult` với status `UNKNOWN_TIMEOUT` (kèm `reason` mô tả lỗi) thay vì ném
exception ra ngoài.

## 5. Kiến trúc 7 bước của Proposed Method

```
   1. SAFE SUBSET        subset.py       lọc tập con QF-LIA
   2. AST ANCHOR        anchors.py      gán NodeId ổn định cho nút
      + binding.py      binding.py      Anchor Gate: claim → nút AST
   3. TRUSTED SYMBOLIC  executor.py     dựng CFG, liệt kê đường đi có trạng thái
      EXECUTION
   4. LLM CoT           llm/gemini.py   sinh reasoning trace + claim
   5. INVARIANT         invariants.py   sinh & kiểm chứng loop invariant
   6. TWO-QUERY Z3      obligations.py  reachability + validity
   7. RETROSPECTIVE     feedback.py     vòng tự sửa sau khi có CE
      FEEDBACK
           shared/replay.py — CPython tái hiện counterexample
           shared/metrics.py — roll-up FDR / F1 / CRVR / HR
```


| Bước | Hàm                                                       | Input        | Output                     | Nếu thất bại                                         |
| ---- | --------------------------------------------------------- | ------------ | -------------------------- | ---------------------------------------------------- |
| 1    | `check_and_normalize`                                     | source       | `SubsetResult`             | `rejected_by_subset=True`, **không** vào solver      |
| 2    | `assign_ids` + `extract_asserts` + `bind_llm_claims`      | tree         | `list[Claim]`              | Claim không neo → `UNSUPPORTED` (đếm vào HR)         |
| 3    | `TrustedSymbolicExecutor.build_all_transitions`           | tree + ids   | `dict[NodeId, Transition]` | Trả `TRANSLATION_ERROR` cho toàn chương trình        |
| 4    | `generate_cot_and_claims`                                 | source       | `LLMResult`                | Lỗi → claim `TRANSLATION_ERROR` (không làm hỏng run) |
| 5    | `LoopInvariantGenerator`                                  | loop state   | `InvariantVerification`    | Không có → bỏ qua, không chặn pipeline               |
| 6    | `build_obligations` + `run_reachability` + `run_validity` | claim + path | `TwoQueryObligations`      | Z3 `unknown` → `UNKNOWN_TIMEOUT`                     |
| 7    | `RetrospectiveFeedbackLoop.run_loop`                      | CE + replay  | claim đã sửa               | Lỗi → giữ nguyên CE (fail-safe)                      |


> **Nguyên tắc "fail-safe":** bước 5 và bước 7 là **tùy chọn**. Thiếu API key
> hoặc LLM lỗi thì pipeline vẫn chạy và vẫn trả kết quả hợp lệ — chỉ mất
> đi lớp cải thiện, không mất đi kết quả chính.

### 5.1. Ba cơ chế cốt lõi

**Hai truy vấn Z3 cho mỗi claim** — vì sao 2 thay vì 1:


| Truy vấn         | Hỏi gì                                  | Kết quả                                                        |
| ---------------- | --------------------------------------- | -------------------------------------------------------------- |
| **Reachability** | `Path ∧ Claim` có thỏa mãn được không?  | `unsat` → đường đi này không tới được nút đích                 |
| **Validity**     | `Path ∧ ¬Claim` có thỏa mãn được không? | `sat` → tồn tại input vi phạm → **COUNTEREXAMPLE** kèm mô hình |


> Chỉ kiểm tra *validity* sẽ báo `COUNTEREXAMPLE` cho một claim nằm trong nhánh
> không bao giờ chạy tới — tức là báo động giả. Truy vấn *reachability* loại bỏ
> đúng trường hợp đó, và khi **mọi** đường đi đều `unsat`, claim mới thực sự
> là `UNREACHABLE`.

**Anchor Gate** — cổng chặn ảo giác:


| Tình trạng                                      | Status              | Đếm vào HR |
| ----------------------------------------------- | ------------------- | ---------- |
| Biểu thức hợp lệ + biến trong scope + dòng khớp | `ANCHORED`          | không      |
| Không tìm được dòng phù hợp / biến ngoài scope  | `UNSUPPORTED`       | **có**     |
| Không parse được thành AST                      | `TRANSLATION_ERROR` | **có**     |


**Vòng retrospective** — chạy sau khi đã có counterexample replay thành công:

```
CE (Z3) → replay (CPython) → LLM phê bình → claim sửa → kiểm lại
         ↓ nếu claim sửa được verify → VERIFIED
         ↓ nếu vẫn sai → giữ COUNTEREXAMPLE (fail-safe)
```

### 5.2. Roll-up metric

```python
metrics = aggregate(results, ground_truth)
```


| Metric                  | Công thức                                      | Ghi chú                                               |
| ----------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| FDR                     | `FP / (TP + FP)`                               | `None` nếu `TP+FP == 0`                               |
| Accuracy                | `(TP + TN) / total`                            | Lớp dương = `VERIFIED`                                |
| Precision / Recall / F1 | chuẩn                                          | Lớp dương = `VERIFIED`                                |
| CRVR                    | `# replay=True / # replay được báo`            | `None` nếu không có replay nào                        |
| HR                      | `(UNSUPPORTED + TRANSLATION_ERROR) / n_claims` | `None` nếu không có cổng neo                          |
| Token / Cost            | trung bình mỗi chương trình                    | `PRICE_IN_PER_1K = 0.005`, `PRICE_OUT_PER_1K = 0.015` |


**Replay trên CPython thật** — `replay_verdict = True` (CE tái hiện được → claim
thực sự sai) · `False` (CE không tái hiện → CE của Z3 không đáng tin) ·
`None` (không replay → CRVR = N/A).

---

## 6. Bộ dữ liệu chuẩn `data/bench_v1/`

1.000 chương trình được chọn có hệ thống từ 14.785 chương trình QF-LIA, có ground
truth kiểm chứng bằng **hai tầng độc lập** (CPython execution oracle + Z3/Spacer).


| Thuộc tính        | Giá trị                                                |
| ----------------- | ------------------------------------------------------ |
| Số mẫu            | **1.000** (seed 42)                                    |
| Phân bố verdict   | **500 VERIFIED / 500 COUNTEREXAMPLE** (cân bằng 50/50) |
| Độ phủ dạng claim | **34/34** dạng đại diện được (trong tổng 36 dạng)      |
| Precondition      | Trích từ mọi nhánh `if` chứa assert                    |
| Shared Claim Pool | 1.000 claim — trích từ `assert` sẵn, **0 token**       |


**Tại sao cần Shared Claim Pool:** mọi phương pháp — Proposed lẫn 14 baseline —
phải dùng **cùng một bộ claim** để không ai được lợi vì trích hay diễn đạt claim
tốt hơn người khác.

**Hạn chế đã biết:** bộ dữ liệu không có `nested_loops`, `for`, `break`/`continue`,
`try/except` hay hàm đệ quy. Kết quả đo trên `bench_v1` **không** suy rộng được
sang các cấu trúc đó.

---

## 7. Cài đặt

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

## 8. Chạy thử nhanh

### 8.1. Chạy Proposed Method trên một đoạn code

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

### 8.2. Chạy một baseline (offline, 0 token)

```powershell
$env:PYTHONPATH = "src"
python -c "
from pathlib import Path
from astverifier.baselines import get_baseline, ProgramSpec
from astverifier.shared.claims import load_claim_pool

pool = load_claim_pool('data/bench_v1/claim_pool.json')
program_id, claims = next(iter(pool.items()))
source = (Path('../Dataset/filtered') / program_id).read_text(encoding='utf-8')

cls = get_baseline('L')          # Symbolic Execution — không cần LLM
result = cls().run(ProgramSpec(program_id=program_id, source=source, claims=claims))
print(result.baseline, result.n_claims, result.claim_results[0].status.value)
"
```

### 8.3. Chạy baseline cần LLM

```powershell
$env:PYTHONPATH = "src"
$env:GEMINI_API_KEY = (Get-Content -Raw geminiapi.txt).Trim()
python -c "
from astverifier.baselines import get_baseline, GeminiClient, ProgramSpec

spec = ProgramSpec(program_id='demo', source=open('demo.py', encoding='utf-8').read())
result = get_baseline('D')(llm=GeminiClient()).run(spec)   # LLM-as-a-Judge
print(result.total_tokens_in, result.total_tokens_out)
"
```

### 8.4. Roll-up metric trên nhiều chương trình

```python
import json
from pathlib import Path
from astverifier.core.pipeline import run_pipeline
from astverifier.shared.metrics import aggregate

manifest = json.loads(Path("data/bench_v1/manifest.json").read_text(encoding="utf-8"))
# Chỉ entry có claim mới mang expected_verdict.
labelled = [e for e in manifest["programs"] if e.get("expected_verdict")]

results = [
    run_pipeline((Path("../Dataset/filtered") / e["path"]).read_text(encoding="utf-8"),
                 program_id=e["path"])
    for e in labelled
]

ground_truth = {e["path"]: e["expected_verdict"] for e in labelled}
m = aggregate(results, ground_truth)
print(f"F1={m.f1:.3f} FDR={m.fdr:.3f} HR={m.hr} CRVR={m.crvr}")
```

---

## 9. Kiểm thử

```powershell
$env:PYTHONPATH = "src"

python -m pytest tests -q          # 109 test
python -m ruff check src tools tests
python -m compileall -q src tools tests
```


| Tệp test                   | Phủ                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_baselines.py`        | Registry đủ 14 ID · quét AST chặn import `core/` · contract: mỗi baseline chạy 1 chương trình mẫu → `ProgramResult` đúng schema · chương trình hỏng & claim list rỗng không crash · token khai báo đúng |
| `test_dataset_pipeline.py` | Tính đúng của pipeline dataset: idempotency, replay counterexample, reachability                                                                                                                        |
| `test_extensions.py`       | Subset gate, anchor binding, Z3 obligations, replay, metric                                                                                                                                             |
| `test_regressions.py`      | Các lỗi đã từng xảy ra (cache, undefined name, zip strictness…)                                                                                                                                         |


---

## 10. Quy ước phát triển


| Mục           | Quy ước                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------- |
| **Style**     | PEP 8, `ruff` với `E/W/F/I/N/UP/B/C4/SIM`, line length 88                                |
| **Typing**    | `from __future__ import annotations`                                                     |
| **Enum**      | `StrEnum` (Python 3.11+) để serialize JSON trực tiếp                                     |
| **Docstring** | Google-style, mô tả **tại sao** chứ không chỉ **cái gì**                                 |
| **Test**      | pytest, `pythonpath = ["src"]` cấu hình sẵn trong `pyproject.toml`                       |
| **Bất biến**  | Baseline **không** được import từ `core/`; `core/` **không** được import từ `baselines/` |
| **Fail-safe** | Lỗi ở bước tùy chọn (feedback, invariants) **không** được làm hỏng run chính             |
| **Metric**    | Không đo được → `None` → in `N/A`. **Không bao giờ** in `0` thay cho `N/A`               |


---

## License

Đồ án chuyên ngành — chỉ sử dụng cho mục đích học thuật.