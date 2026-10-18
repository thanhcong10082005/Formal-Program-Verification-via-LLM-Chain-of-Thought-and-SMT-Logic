# Dataset CURATED cho AST Verifier Pipeline

> Tổng hợp từ 4 nguồn: `sv-benchmarks-loops-py`, `cruxeval-main`, `code2inv-master`, `mbpp`
> Ngày build: 2026-10-18
> Tác giả: Công (phụ trách Proposed Method + dataset curation)

## Cấu trúc thư mục

```
prototype/dataset_curated/
├── README.md          # File này
├── manifest.json      # Metadata đầy đủ (JSON)
├── manifest.csv       # Metadata dạng bảng (Excel-friendly)
├── programs/          # Source code các programs
│   ├── sv-comp/       # 73 programs từ SV-COMP (Python subset)
│   └── cruxeval/      # 21 programs từ CRUXEval
```

## Thống kê

- **Tổng programs:** 39 (đã deduplicate + filter)
- **By source:** {'sv-comp': 29, 'synthesized': 10}
- **By category:** {'loop-invariants': 7, 'loop-simple': 8, 'loops-crafted-1': 14, 'other': 10}
- **By parseability:** {'c_style': 14, 'pure_python': 25}
- **By expected_verdict:** {'true': 39}
- **Excluded:** {'broken_heavy_c': 6, 'broken': 36, 'unbalanced_braces': 2}

## Tiêu chí filter

1. **Brace balance**: bỏ file có `{` ≠ `}` hoặc `(` ≠ `)` (Dũng report 2 file)
2. **Parseability**: chỉ giữ file parse được bằng `ast.parse()` HOẶC là C-style (có `&&`, `||`, `c++`, `i++`)
3. **Has assertion**: file SV-COMP phải có `assert` (để có target verify)
4. **Size**: 5-200 dòng (quá ngắn: trivial; quá dài: phức tạp)
5. **Deduplicate**: hash nội dung để tránh trùng

## Cách dùng

```python
import json
manifest = json.load(open("manifest.json"))
for p in manifest["programs"]:
    print(p["program_id"], p["category"], p["loc"], p["difficulty"])
```

## Đánh giá chất lượng

Dataset cũ (94 programs từ 2 folder) có vấn đề:
- 2 file lỗi cấu trúc (`nested_delay_nd.py`, `nested_delay_notd2.py`) — đã loại
- 58 file dùng cú pháp C-style (`&&`, `c++`, `# skip`) — giữ nhưng đánh dấu `c_style`
- 15 file Python thuần — đánh dấu `pure_python`
- 21 file CRUXEval — functional, không có assertion

**Tổng cộng curated: 39 programs** (so với 94 ban đầu)

## Khuyến nghị

- **Cho pipeline E2E:** Dùng `25` programs pure_python để có kết quả sạch nhất
- **Cho benchmark realistic:** Dùng tất cả 39 programs (gồm cả c_style)
- **Cho paper:** Báo cáo metric riêng cho từng tier
