# `dataset_temp_200/` — Temporary QF-LIA Benchmark (200 programs)

> Bộ dữ liệu tạm thời phục vụ giai đoạn **Baseline**, được sinh bằng
> **execution oracle** (không dùng LLM để gán nhãn).
>
> Sinh lại bằng: `python tools/build_temp200.py`

---

## 1. Vì sao cần bộ này?

Bộ `dataset_curated` hiện tại (68 mẫu) có **ba vấn đề** khiến số liệu
benchmark không đáng tin:

| Vấn đề | Chi tiết |
|---------|----------|
| **Ground truth không nhất quán** | `manifest.json` ghi 38/30, đề cương §11.1 ghi 45/32, `benchmark.tex` ghi 41/27 → mọi metric đang đo trên một nhãn không xác định |
| **Cỡ mẫu quá nhỏ** | 68 mẫu → sai số ±6% quanh F1, không phân biệt được các baseline khác nhau 2–3 điểm |
| **80.2% chương trình không có `assert`** | Baseline B/C/D bị trả `NO_CLAIMS` hàng loạt → số mẫu so sánh bị sụt, kết luận không đáng tin |

`dataset_temp_200` giải quyết cả ba:

1. **Nhãn bằng execution oracle** — tác vụ kiểm chứng được, không LLM can thiệp.
2. **200 mẫu** đủ để phân biệt baseline.
3. **100% chương trình có đúng 1 `assert`** → không còn `NO_CLAIMS`.

> **Lưu ý:** Đây là bộ **tạm thời**. Bộ chính của Tâm sẽ thay thế sau khi
> thống nhất schema `manifest.json` (xem §7).

---

## 2. Ràng buộc phạm vi (Strict QF-LIA Scope)

Mọi chương trình đều vượt qua cổng subset
(`astverifier.shared.subset.check_and_normalize`) — đây là **điều kiện tiên quyết**
khi build, không phải hậu kiểm.

**Được phép:**

- Kiểu dữ liệu: `int`, `bool`
- Phép toán: `+`, `-`, `*` (với hằng số), `//`, `%` (với hằng số khác 0)
- Cấu trúc điều khiển: `if/elif/else`, `while`, `for ... in range(...)`,
  `break`, `continue`, `return`, `assert`
- Biểu thức: phép so sánh, phép logic, biểu thức điều kiện, `abs/min/max/range`
- Kiểu chú thích: `int`, `bool`, `None`

**Cấm (bị từ chối):**

- `float`, `str`, `list`, `dict`, `set`, `tuple`
- `class` / OOP / decorator / nested function
- `import` bên ngoài
- I/O: `print`, `input`
- Phép nhân phi tuyến (`n * (n-1)`), `**`, `/` (float)
- Attribute access, subscript, lambda, f-string

> **Lưu ý về phép nhân phi tuyến:** cổng subset từ chối `n * (n-1)` vì
> đó không thuộc QF-LIA. Bộ dataset này vì vậy dùng `n * 3` (nhân hằng số)
> thay vì `n * n`. Điều này giữ bộ dữ liệu **tương thích với solver SMT**,
> nên baseline nào dùng Z3 cũng chạy được trên cùng bộ — cần thiết cho
> tính công bằng so sánh.

---

## 3. Phân bổ 200 mẫu

### 3.1. Theo nhóm cấu trúc

| Nhóm | Thư mục | Số mẫu | Nội dung |
|------|---------|---------|----------|
| **G1** | `programs/g1_arithmetic/` | 40 | Biểu thức số học & tuyến tính đơn giản |
| **G2** | `programs/g2_conditionals/` | 40 | Rẽ nhánh & điều kiện đường đi |
| **G3** | `programs/g3_loops/` | 50 | Vòng lặp đơn & loop invariants |
| **G4** | `programs/g4_nested/` | 40 | Vòng lặp lồng nhau & cận sâu |
| **G5** | `programs/g5_bugs/` | 30 | Bug thật / Unreachable code |

### 3.2. Theo nhãn ground truth

| Nhãn | Số mẫu | Ý nghĩa |
|------|---------|---------|
| `VERIFIED` | 85 | Claim đúng trên mọi miền input đã thử |
| `COUNTEREXAMPLE` | 100 | Claim sai — có input làm claim fail |
| `UNREACHABLE` | 15 | Claim không bao giờ được thực thi |

> Tỷ lệ **100 COUNTEREXAMPLE / 85 VERIFIED** được cân bằng có chủ đích:
> phần lớn baseline hiện tại có **Recall cao, Precision thấp** (A: P=57.4%
> / R=71.1%). Bộ cân bằng này đủ số COUNTEREXAMPLE để đo **FDR** — metric
> phân biệt chính — mà không làm lệch sang một đầu.

### 3.3. Theo loại lỗi

`manifest.json` trường `bug_type` phân loại nguyên nhân khiến claim sai.
Có **15 loại lỗi**; các chương trình `VERIFIED` mang giá trị `none`:

| `bug_type` | Số lẫn | Mô tả |
|------------|---------|-------|
| `claim_off_by_one` | 29 | Claim lệch 1 đơn vị so với giá trị thật |
| `claim_ignores_effect` | 14 | Claim bỏ qua tác dụng của vòng lặp/nhánh lọc |
| `claim_inverted` | 10 | Đảo ngược so sánh (`>=` ↔ `<=`) |
| `claim_missing_branch` | 10 | Claim bỏ qua một nhánh của điều kiện |
| `claim_strict_bound` | 6 | Dùng `>` thay vì `>=` (loại trừ biên 0) |
| `claim_missing_constant` | 4 | Bỏ sót hằng số trong biểu thức |
| `claim_sign_error` | 4 | Sai dấu |
| `claim_wrong_value` | 4 | Ghim giá trị cụ thể sai |
| `claim_wrong_bound` | 2 | Dùng sai cận |
| `claim_wrong_coefficient` | 2 | Sai hệ số nhân |
| `impl_off_by_one` | 5 | **Bug thật trong code**: `<=` thay vì `<` |
| `impl_wrong_update` | 5 | **Bug thật trong code**: biến đếm nhảy 2 đơn vị |
| `impl_bad_init` | 5 | **Bug thật trong code**: khởi tạo sai (`s = 1`) |
| `unreachable_after_return` | 8 | Claim nằm **sau** `return` |
| `unreachable_branch` | 7 | Claim nằm trong **nhánh mâu thuẫn** |
| `none` | 85 | Chương trình `VERIFIED` — không có lỗi |

> **Phân biệt quan trọng:** `claim_*` = **claim sai, code đúng**.
> `impl_*` = **code có bug thật, claim đúng**.
> `unreachable_*` = **claim không bao giờ chạy tới**.
>
> Ba nhóm này đo ba thứ khác nhau:
> - `claim_*` đo **sensitivity của verifier** (baseline có phát hiện được claim sai không)
> - `impl_*` đo **khả năng tìm bug thật** (giá trị thực tiễn của đồ án)
> - `unreachable_*` đo **trục REACHABILITY** — RQ3 của đề tài

---

## 4. Quy trình sinh & kiểm chứng

```
tools/build_temp200.py
        │
        ├─ 1. Sinh 200 source từ 50 "family" mẫu
        │      mỗi family sinh 1 bản VERIFIED + 1 bản COUNTEREXAMPLE
        │
        ├─ 2. Cổng subset (bắt buộc)
        │      check_and_normalize(source).accepted == True
        │      → nếu fail: build dừng, in danh sách lỗi
        │
        ├─ 3. Execution oracle
        │      assert → _chk(expr)          (giữ nguyên số dòng)
        │      chạy hàm trên miền input hữu hạn
        │      kết quả:
        │        claim fail ở input nào  → COUNTEREXAMPLE (+ witness)
        │        claim không chạy tới    → UNREACHABLE
        │        claim đúng mọi input   → VERIFIED
        │
        └─ 4. So khớp nhãn kỳ vọng
               observed == expected ?  → ghi manifest
                                      → lệch: build FAIL
```

**Điểm mấu chốt:** bước 4 là cổng chặn cuối. Nếu oracle quan sát được hành vi
khác với nhãn kỳ vọng, **build thất bại** — không ghi được manifest. Nhờ vậy
nhãn trong `manifest.json` **luôn khớp với hành vi quan sát được**, không phải
với ý định của người viết.

### 4.1. Miền input của oracle

| Hằng | Giá trị | Dùng cho |
|-------|----------|----------|
| `DOMAIN_ALL` | `-3 … 32` | G1, G2, G5 (nhận cả số âm) |
| `DOMAIN_NON_NEG` | `0 … 32` | G3, G4 (vòng lặp cần `n >= 0`) |
| `DOMAIN_POSITIVE` | `1 … 32` | (dành cho claim cần `n > 0`) |

Vài family cần giá trị cụ thể (ví dụ `strip_factor` cần một bội số của `c`),
nên miền được mở rộng thêm bằng `_domain_with(...)`.

### 4.2. Precondition

Manifest lưu `precondition` (ví dụ `"n >= 0"`) cho các chương trình vòng lặp.
Đây là **tiền đề của claim**, được hiểu là giả định khi kiểm chứng — không
phải một `assert` thứ hai trong file. Baseline nào không xử lý được tiền đề
phải báo `N/A` cho những chương trình này, **không được coi là 0**.

---

## 5. Định dạng `manifest.json`

```jsonc
{
  "version": "1.0",
  "generated_by": "tools/build_temp200.py",
  "subset": "QF-LIA (int/bool, linear arithmetic, if/while/for over range)",
  "labeling": "execution oracle (no LLM in the labeling loop)",
  "n_programs": 200,
  "counts_by_verdict": { "VERIFIED": 85, "COUNTEREXAMPLE": 100, "UNREACHABLE": 15 },
  "counts_by_group":   { "g1_arithmetic": 40, ... },
  "counts_by_bug_type":{ "claim_off_by_one": 29, ... },
  "programs": [
    {
      "program_id": "g5_bug_offbyone_01",
      "path": "programs/g5_bugs/g5_bug_offbyone_01.py",
      "group": "g5_bugs",
      "family": "bug_off_by_one",
      "expected_verdict": "COUNTEREXAMPLE",
      "bug_type": "impl_off_by_one",
      "precondition": "n >= 0",
      "params": ["n"],
      "assert_line": 13,
      "claim_expression": "s == n",
      "note": "the loop condition runs one iteration too many",
      "gt_source": "execution_oracle",
      "gt_confidence": "high"
    }
  ]
}
```

**Ý nghĩa các trường quan trọng:**

| Trường | Ý nghĩa |
|--------|---------|
| `expected_verdict` | Nhãn ground truth — **nguồn sự thật duy nhất** cho mọi metric |
| `bug_type` | Phân loại nguyên nhân — dùng để phân tích theo lỗi, không chỉ theo nhãn |
| `precondition` | Tiền đề của claim — baseline không xử lý được thì báo `N/A` |
| `assert_line` | Dòng chứa `assert` — pipeline dùng để neo claim vào nút AST |
| `claim_expression` | Biểu thức claim dạng text — tiện cho Shared Claim Pool |
| `gt_source` | `execution_oracle` — **luôn luôn** là giá trị này |
| `gt_confidence` | `high` — nhãn chắc chắn vì đã kiểm chứng bằng thực thi |

---

## 6. Ví dụ chương trình

### 6.1. `VERIFIED` — G1

```python
# g1_addconst_01
# group: g1_arithmetic | family: add_const
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of x = n + 3

def f(n: int) -> int:
    x = n + 3
    assert x == n + 3
    return x
```

### 6.2. `COUNTEREXAMPLE` — claim sai (G3)

```python
# g3_count_01_b
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n + 1     # <- claim sai, code đúng
    return s
```

### 6.3. `COUNTEREXAMPLE` — bug thật trong code (G5)

```python
# g5_bug_offbyone_01
# expected_verdict: COUNTEREXAMPLE | bug_type: impl_off_by_one
# note: the loop condition runs one iteration too many

def f(n: int) -> int:
    i = 0
    s = 0
    while i <= n:          # <- bug: <= thay vì <
        s = s + 1
        i = i + 1
    assert s == n          # <- claim đúng, code sai
    return s
```

### 6.4. `UNREACHABLE` — claim sau `return` (G5)

```python
# g5_unreach_afterreturn_01
# expected_verdict: UNREACHABLE | bug_type: unreachable_after_return

def f(n: int) -> int:
    x = n + 3
    return x
    assert x == n + 3     # <- không bao giờ chạy tới
```

### 6.5. `UNREACHABLE` — nhánh mâu thuẫn (G5)

```python
# g5_unreach_branch_01
# expected_verdict: UNREACHABLE | bug_type: unreachable_branch

def f(n: int) -> int:
    x = n + 4
    if n > 100 and n < 0:  # <- mâu thuẫn, không nhánh nào vào được
        assert x == n + 4
    return x
```

---

## 7. Hạn chế đã biết & việc cần làm tiếp

| # | Vấn đề | Hướng xử lý |
|---|--------|-------------|
| 1 | **Chưa có `gt_confidence` đa cấp** | Hiện mọi mẫu đều `high`. Khi tích hợp bộ chính của Tâm, cần tách mẫu `medium` (ground truth suy ra từ tài liệu, chưa chạy thử) |
| 2 | **Miền oracle hữu hạn** | Oracle kiểm tra trên tối đa 13 giá trị/param. Một claim sai chỉ trên input ngoài miền sẽ bị gán nhầm `VERIFIED`. Cần bổ sung kiểm tra bằng **Z3** cho các mẫu `VERIFIED` trong phase Baseline |
| 3 | **Ground truth = verdict của 1 claim** | Mỗi chương trình có đúng 1 `assert`. Bộ chính có thể có nhiều claim/chương trình → metric phải báo **cả claim-level và program-level** |
| 4 | **Chưa đồng bộ với bộ của Tâm** | Cần `tools/migrate_manifest.py` để chuyển bộ chính sang cùng schema `manifest.json` |
| 5 | **Chỉ 1 ngôn ngữ, 1 subset** | Toàn bộ kết quả áp dụng cho Python QF-LIA. Không suy rộng ra ngoài phạm vi này khi viết báo cáo |

---

## 8. Cách dùng trong code

```python
import json
from pathlib import Path

DATASET = Path("dataset_temp_200")
manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))

for entry in manifest["programs"]:
    source = (DATASET / entry["path"]).read_text(encoding="utf-8")
    ground_truth = entry["expected_verdict"]
    # → đưa `source` và `ground_truth` vào runner của baseline
```

Ground truth cho `astverifier.shared.metrics.load_ground_truth` cần dạng
`{program_id: "VERIFIED"|"COUNTEREXAMPLE"|"UNREACHABLE"}` — trích ra như sau:

```python
ground_truth = {
    entry["program_id"]: entry["expected_verdict"]
    for entry in manifest["programs"]
}
```
