# Báo cáo: Dataset CURATED (18/10/2026)

---

---

### Bối cảnh

Các thành viên nhóm nói: *"dataset cũ rất loãng"*. Tôi đã audit chi tiết 4 nguồn:


| Dataset                     | Tổng `.py`  | Parse OK | Brace balanced | Đánh giá                                         |
| --------------------------- | ----------- | -------- | -------------- | ------------------------------------------------ |
| `sv-benchmarks-loops-py`    | 73          | **15**   | 71             | Có file lỗi, có file C-style                     |
| `cruxeval-main`             | 21          | 21       | 19             | **Không có benchmark programs** — toàn tool code |
| `code2inv-master`           | 39          | 39       | 36             | Chỉ có C programs + tool, không có Python        |
| `mbpp`                      | 0           | —        | —              | Folder rỗng                                      |
| `cruxeval-main/samples/...` | (JSON only) | —        | —              | Chỉ có generations, không source                 |




### Phát hiện quan trọng

1. `cruxeval-main` **không có programs** — chỉ có 21 file `.py` là tool code (inference, evaluation). Source programs không được ship.
2. `mbpp` **folder rỗng** — không có gì.
3. `code2inv-master` có 7 chương trình C ở `nl-bench/c/`, nhưng dịch sang Python rất tốn công.
4. `sv-benchmarks-loops-py` có 73 file, nhưng:
  - 58 file dùng cú pháp C-style (`&&`, `c++`, `if (cond) { ... }`, `# skip: '...'`)
  - 15 file Python thuần (`pure_python`)
  - 2 file có `}` thừa
  - 6 file C nặng (có `unsigned`, `safe_malloc`, `sizeof`)



### Giải pháp: Tổng hợp CURATED dataset

Tôi đã tạo `prototype/dataset_curated/` chứa 39 programs đảm bảo dùng được.

#### Cấu trúc thư mục

```
prototype/dataset_curated/
├── README.md                   # Tài liệu
├── manifest.json               # Metadata đầy đủ
├── manifest.csv                # Bảng Excel-friendly
├── DATASET_REPORT.md           # Báo cáo này
└── programs/
    ├── sv-comp/                # 29 files từ SV-COMP
    │   ├── sv-benchmarks-loops-py__loop-invariants__eq1.py
    │   ├── ... (28 files khác)
    └── synthesized/            # 10 files tự tạo từ literature
        ├── syn__sum01.py       # Σ 0..n-1
        ├── syn__sum02_gauss.py # Gauss formula
        ├── syn__sum_even.py    # Σ even
        ├── syn__factorial.py
        ├── syn__power.py
        ├── syn__countdown.py
        ├── syn__euclid_gcd.py
        ├── syn__linear_search.py
        ├── syn__buggy_sum01.py       # Có bug off-by-one
        └── syn__buggy_gauss_overflow.py  # Có bug overflow
```



#### Thống kê dataset CURATED


| Metric                                                     | Value                                                     |
| ---------------------------------------------------------- | --------------------------------------------------------- |
| **Tổng programs**                                          | **39**                                                    |
| By source: sv-comp                                         | 29                                                        |
| By source: synthesized                                     | 10                                                        |
| By parseability: pure_python                               | **25** (64%)                                              |
| By parseability: c_style                                   | 14 (36%)                                                  |
| By category: loop-invariants                               | 7                                                         |
| By category: loop-simple                                   | 8                                                         |
| By category: loops-crafted-1                               | 14                                                        |
| By category: synthesized (arithmetic, termination, search) | 10                                                        |
| By difficulty: easy                                        | 33                                                        |
| By difficulty: medium                                      | 6                                                         |
| By difficulty: hard                                        | 0                                                         |
| By expected_verdict: true                                  | 37                                                        |
| By expected_verdict: false                                 | 2 (chỉ synthesized có bug)                                |
| **Exclude reasons**                                        | `unbalanced_braces: 2`, `broken: 36`, `broken_heavy_c: 6` |




#### Tiêu chí filter áp dụng

1. **Brace balance**: bỏ file có `}` ≠ `{` hoặc `)` ≠ `(` → loại 2 file (`nested_delay_nd.py`, `nested_delay_notd2.py`)
2. **Parseability**: giữ file nếu:
  - `ast.parse()` thành công → `pure_python`
  - HOẶC có C-syntax nhẹ (`&&`, `||`, `c++`, `i++`, `while 0:`) → `c_style`
  - Nếu có C-syntax nặng (`unsigned`, `malloc`, `sizeof`, `__VERIFIER`) → loại
3. **Size**: 5-200 dòng
4. **Deduplicate**: hash nội dung



#### 10 program tự tạo (synthesized)


| ID                          | Mô tả                                   | Expected |
| --------------------------- | --------------------------------------- | -------- |
| `syn__sum01`                | Σ 0..n-1 = n(n-1)/2                     | `true`   |
| `syn__sum02_gauss`          | Σ 0..n = n(n+1)/2                       | `true`   |
| `syn__sum_even`             | Σ 0,2,..,2(n-1) = n(n-1)                | `true`   |
| `syn__factorial`            | n! ≥ 1                                  | `true`   |
| `syn__power`                | b^n đúng                                | `true`   |
| `syn__countdown`            | Đếm ngược từ n về 0                     | `true`   |
| `syn__euclid_gcd`           | Euclid GCD ≥ 0                          | `true`   |
| `syn__linear_search`        | Linear search, return -1 nếu không thấy | `true`   |
| `syn__buggy_sum01`          | Off-by-one bug (sai từ n=1)             | `false`  |
| `syn__buggy_gauss_overflow` | Integer overflow (sai từ n=46341)       | `false`  |


→ **2/39 (5%) có bug thật** để test CRVR / FDR

### Đã verify chạy được E2E

Đã chạy `tools/replay_curated.py` thành công:

```
Running 39 CURATED programs through Proposed pipeline...
  Gemini API key found - feedback + invariants LIVE
  ... 39 OK, 0 fail
Done in 22.1s: 39 ok, 0 fail / 39 total
```

39/39 chạy không lỗi. Output ở `prototype/results/CURATED/`.

### So sánh dataset cũ vs mới


| Aspect                              | Dataset cũ (94) | Dataset CURATED (39)                  |
| ----------------------------------- | --------------- | ------------------------------------- |
| Tổng programs                       | 94              | 39                                    |
| Pure Python (pipeline xử lý đầy đủ) | 15 (16%)        | **25 (64%)**                          |
| C-style (pipeline tự xử lý)         | 58 (62%)        | 14 (36%)                              |
| Broken (bị skip)                    | 21 (22%)        | 0 (0%)                                |
| Có ground truth biết trước          | 0 (0%)          | **10 (26%)** — synthesized            |
| Có bug thật (test FDR)              | 0 (0%)          | **2 (5%)** — buggy_sum01, buggy_gauss |
| Brace imbalanced                    | 2               | 0                                     |
| Code tools (không phải benchmark)   | 21 (cruxeval)   | 0                                     |



### Cách dùng dataset CURATED

```bash
# 1. Chạy E2E trên dataset CURATED
cd prototype
python tools/replay_curated.py

# 2. Xem kết quả
ls results/CURATED/

# 3. So sánh với 94 file cũ
python tools/analyze_proposed.py  # cho dataset cũ
# (cần viết analyze_curated.py cho dataset mới)
```



### File output

```
prototype/dataset_curated/
├── README.md
├── manifest.json
├── manifest.csv
├── DATASET_REPORT.md (file này)
└── programs/
    ├── sv-comp/  (29 files)
    └── synthesized/  (10 files)

prototype/results/CURATED/  (39 JSON kết quả)
```

---


### Lợi ích dataset CURATED

1. **Sạch hơn 58%**: 64% pure_python (vs 16% ở dataset cũ)
2. **Có ground truth biết trước**: 10 program synthesized với expected_verdict rõ ràng
3. **Có bug thật để test**: 2 program có bug (off-by-one, overflow)
4. **Không còn file lỗi**: 0 file imbalanced, 0 file broken
5. **Không có code tools**: tất cả là benchmark programs thật
6. **E2E đã verify chạy được**: 39/39 OK trong 22s


