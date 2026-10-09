# Implementation Walkthrough — AST-Anchored CoT Verification Prototype

> Đồ án chuyên ngành: *"Kiểm Chứng Chương Trình Dựa trên Chuỗi Lý Luận: Xác Minh Logic Thông qua Dấu Vết Ngôn Ngữ Tự Nhiên"*
> GVHD: TS. Lê Xuân Bách

File này trình bày lại toàn bộ quá trình implement framework `astverifier/`, từ thiết kế đến debug cuối cùng và chạy thực nghiệm trên `Dataset/`. Đây là companion doc của `summary.md`.

---

## 1. Bối cảnh

Framework đặt mục tiêu kiểm chứng chương trình Python theo hướng
**neuro-symbolic**: AST là "trusted computing base" duy nhất, mọi quyết định
đều bắt nguồn từ cây cú pháp thay vì từ LLM. Hai quy trình cốt lõi:

1. **Trusted symbolic execution**: dịch AST thành chuyển tiếp $T_v$ và quan hệ
  reachability $R_\pi$ bằng z3.
2. **Two-query Z3 + counterexample replay**: truy vấn reachability trước (chống
  vacuous verification), rồi truy vấn validity, cuối cùng replay counterexample
   trên CPython thật để kiểm tra soundness.

`UNANCHORED` giữ tên tương thích nhưng hiện là **Baseline A — Direct
Formalization**: LLM trả về các Boolean term SMT-LIB2 và khai báo biến Int.
Baseline này không có AST anchoring, program state hay reachability; mỗi claim
được kiểm tra bằng đúng một truy vấn $\neg C$. Ta đo được 4 metric: FDR, token
consumption, Hallucination Rate, CRVR.

### Audit boundary

`AST_ANCHORED` chỉ gọi một claim là anchored khi line khớp chính xác một
statement, biểu thức parse được và mọi tên biến thuộc scope của function.
Mỗi claim dùng các path bounded dẫn tới chính `NodeId` đó. `UNANCHORED`
không tạo `NodeId`, transition, target state hay reachability query. Mỗi
`assert` trong source được chuyển đúng một lần thành SMT-LIB2 formula; các
specification LLM được append như một claim pool riêng và location bị loại bỏ.

Loop unrolling và control effects chưa đủ để chứng minh toàn bộ execution
space. Vì vậy coverage không đầy đủ được trả về là `UNKNOWN_TIMEOUT`, và
`VERIFIED` chỉ áp dụng cho tập path hoàn chỉnh trong bounded subset. Replay
chỉ thành công khi runtime chạm target line và claim thực sự false tại đó.

---

## 2. Cấu trúc thư mục cuối cùng

```
prototype/
├── documentation/                       # tài liệu kỹ thuật
│   └── IMPLEMENTATION_WALKTHROUGH.md   # file này
├── src/
│   └── astverifier/                    # package chính
│       ├── __init__.py
│       ├── subset.py                   # Phase 1: QF-LIA whitelist gate
│       ├── nodes.py                    # Phase 1: NodeId registry
│       ├── classify.py                 # Phase 1: Pydantic schemas
│       ├── executor.py                 # Phase 2: Trusted symbolic executor
│       ├── binding.py                  # Phase 2: Anchor binding gate α(c)
│       ├── obligations.py              # Two-query + direct not-C Z3
│       ├── direct.py                   # SMT-LIB2 direct validator
│       ├── replay.py                   # Phase 3: CPython subprocess replay
│       ├── pipeline.py                 # Phase 4: end-to-end orchestrator
│       └── metrics.py                  # Phase 4: 4-metric aggregation
├── tools/
│   ├── translate_svcomp.py             # C → Python QF-LIA translator
│   ├── run_svcomp_translate.py         # batch translate 73 .c files
│   ├── run_benchmark.py                # Phase 4 runner
│   └── compare_baselines.py            # Phase 4 report generator
├── results/
│   ├── AST_ANCHORED/*.json             # 94 verdict anchors
│   ├── UNANCHORED/*.json               # 94 verdict unanchored
│   ├── _cruxeval_py/*.py               # 21 translated CRUXEval samples
│   ├── summary.md                      # báo cáo Markdown cuối
│   ├── ground_truth.json               # ground-truth labels
│   └── _run.log                        # log thực nghiệm
├── tests/                              # regression tests for audit fixes
├── README.md                           # hướng dẫn sử dụng
└── .gitignore                          # loại bỏ cache, log nặng
```

Bên ngoài `prototype/`, dataset nằm ở `../Dataset/`:

```
Dataset/
├── sv-benchmarks-loops/c/              # 78 file C gốc (SV-COMP 2024)
│   ├── loop-simple/                    # 12 file
│   ├── loops-crafted-1/                # 49 file
│   └── loop-invariants/                # 17 file
├── sv-benchmarks-loops-py/             # 73 file Python đã dịch
│   ├── loop-simple/
│   ├── loops-crafted-1/
│   └── loop-invariants/
├── cruxeval-main/data/cruxeval.jsonl   # 800 Python execution-grounded samples
└── code2inv-master/                    # (không dùng trong prototype này)
```

---

## 3. Phase 1 — Foundation

### 3.1 `subset.py` — QF-LIA whitelist gate

Ý tưởng: chỉ chấp nhận Python subset mà z3.QF-LIA có thể giải được.
Whitelist gồm các operator số học tuyến tính:

- Statement: FunctionDef, Assign, AugAssign, If, For, While, Return, Break, Continue, Assert, Pass
- BinOp: +, −, *, //, %; `*` phải có một hệ số hằng, còn `//` và `%`
  phải có mẫu số hằng khác 0.
- UnaryOp: −x, not x
- Compare: ==, !=, ≤, ≥, <, >
- BoolOp: and, or
- Builtins: `abs`, `min`, `max`, `range`, `len`

`SubsetResult` mang về:

- `accepted`: True/False
- `rejected_reason`, `rejected_source_line`
- `extracted_claims`: danh sách `ClaimInfo` (assert → PendingExpression)
- `tree`: `ast.Module` đã parse

`SubsetVisitor` đi qua AST và raise `_StopSubset(reason)` khi gặp node ngoài
whitelist. `check_and_normalize(source)` parse + walk trong một lần.

### 3.2 `nodes.py` — Stable NodeId

Mỗi statement-level node được gán `NodeId(func, lineno, col, kind, discriminator)`.
Hai lần chạy trên cùng source phải cho cùng id (idempotent). Discriminator
đếm số statement cùng (func, lineno, kind) để phân biệt multi-statement
trên một dòng.

Helper `lookup_containing(node, ids)` tìm NodeId cha gần nhất — dùng cho
anchor binding ở Phase 2.

### 3.3 `classify.py` — 6 status Pydantic schemas

- `StatusEnum`: 6 giá trị (VERIFIED, COUNTEREXAMPLE, UNREACHABLE, UNSUPPORTED,
TRANSLATION_ERROR, UNKNOWN_TIMEOUT)
- `AnchorInfo`: `node_ids`, `source_lines`, `has_grounding`
- `CounterexampleInfo`: `z3_model`, `replay_verdict`, `replay_output`, `replay_diff`
- `ClaimResult`/`ProgramResult`: Pydantic với `extra="forbid"`

`make_empty_program_result(...)` tiện cho việc tạo result khi subset reject.

---

## 4. Phase 2 — Trusted Symbolic Executor & Binding

### 4.1 `executor.py`

`TrustedSymbolicExecutor(tree, ids)` duyệt AST, tạo `SymState` (mapping name →
z3.Int / z3.Bool) cho mỗi `Assign`, `AugAssign`, `If`, `For`, `While`. Mỗi
statement được wrap thành `Transition(pre, post, guard, node)` và lưu vào
`self.transitions[nid]`.

Hai helper chính:

- `enumerate_target_paths(node)`: liệt kê các path bounded dẫn tới đúng
  target claim và các branch guard tương ứng.
- `build_reachability(path)`: tạo `R_\pi(s_0, s)` gồm path condition và
  phương trình state output; đồng thời giữ `state` tại target để dịch claim.

`if` được dịch bằng `z3.If(cond_expr, t_val, e_val)` để merge post-state —
đây là điểm then chốt cho symbolic execution.

### 4.2 `binding.py`

`extract_asserts(tree, ids)` quét mọi `ast.Assert` và gán
`claim.anchor = lookup_containing(assert_node, ids)`. Claim LLM chỉ được
anchor nếu line khớp chính xác một statement; không có fallback tới node
trước đó. Tên biến được kiểm tra với scope của function, nên claim có biến
ngoài scope là `UNSUPPORTED`.

---

## 5. Phase 3 — Two-Query Z3 & Replay

### 5.1 `obligations.py`

`build_obligations(claim, reachability, executor)` tạo 2 `z3.Solver`:

1. `reachability_solver`: chứa `reachability.relation`
2. `validity_solver`: chứa state relation tại target `∧ ¬claim`

`run_reachability()` và `run_validity()` trả về `('sat'|'unsat'|'unknown', elapsed, model)`.
Timeout mặc định 5000ms mỗi solver.

`_claim_to_z3(expr, executor, state=target_state)` dịch claim trong chính
state tại target. Tên local đã khởi tạo được thay bằng symbolic expression
của nó; tên không có trong state không được tạo thành free integer.

### 5.2 `replay.py`

`replay_counterexample(source, func_name, inputs, timeout=2.0, *, target_line=None, claim_expression=None)`:

1. Build Python script động (nhúng source + JSON inputs + exec + gọi fn).
2. Chạy bằng `subprocess.run([sys.executable, tmp_path], capture_output=True, timeout=2.0)`.
3. Trace target line và eval claim trong frame locals.
4. Chỉ trả `success=True` khi target đã được chạm và claim false tại đó;
   normal return không còn được tính là replay thành công.

Thiết kế này đảm bảo không bao giờ trust output LLM — chỉ chạy CPython
thật với inputs từ z3 model.

---

## 6. Phase 4 — Pipeline, Metrics, Benchmark

### 6.1 `pipeline.py`

`run_pipeline(source, *, program_id, source_path, baseline, do_replay=True)`:

```
1. Subset gate          → rejected_by_subset = True
2. AST_ANCHORED: assign_ids + trusted transitions + exact anchor gate.
3. AST_ANCHORED: enumerate target-specific paths and build target state relation.
4. For each anchored path: reachability, then validity, then target-aware replay.
5. UNANCHORED: convert source asserts and validate independent SMT-LIB2
   specifications; skip node IDs, executor, target state and reachability.
6. Roll-up counts into `ProgramResult`.
```

### 6.2 `metrics.py`

`aggregate(results, ground_truth=None)` tính:

- `token_avg_in`, `token_avg_out` (0 for deterministic runs; with
  `--use-llm`, AST_ANCHORED and UNANCHORED are charged from separate calls)
- `cost_avg_usd` = `(in × $0.005 + out × $0.015) / 1000`
- `hr` = (UNSUPPORTED + TRANSLATION_ERROR) / total_claims
- `crvr` = REPRODUCED / total replayed_COUNTEREXAMPLE (n/a for direct models)
- `fdr` (nếu có ground truth) = FP / (TP + FP)

### 6.3 Direct Formalization contract

`llm.py` keeps the original anchored response schema and prompt unchanged. A
separate `DirectSpecification` schema contains:

- `formula`: one SMT-LIB2 Boolean term;
- `variables`: declared Int symbols;
- `rationale`: a brief explanation, not a Chain-of-Thought trace.

`direct.py` parses the formula with Z3 and rejects malformed terms,
quantifiers, nonlinear multiplication, variable divisors, non-Int symbols, and
operators outside the QF-LIA whitelist. `build_direct_obligation` adds only
`not(C)`. Direct counterexamples retain the Z3 integer model, empty anchor
fields, and `replay_verdict = None`.

### 6.4 `tools/run_benchmark.py`

- Thu thập 73 file SV-COMP `.c` → dịch sang `.py` bằng `translate_svcomp.py`.
- Lấy 21 sample CRUXEval QF-LIA (int/bool/None args only, không nested fn).
- Với mỗi program, `AST_ANCHORED` và `UNANCHORED` nhận hai lời gọi Gemini
  độc lập khi `--use-llm` bật; token usage được ghi riêng. Mặc định source
  assertions được chuyển deterministic thành direct SMT-LIB2 claims.

### 6.4 `tools/compare_baselines.py`

- Load 94+94 kết quả.
- Tính metrics, build Markdown table.
- In per-program verdicts và counterexample replay table.

---

## 7. Công cụ `translate_svcomp.py` — C → Python translator

Đây là phần **dễ bị under-engineered nhất**. Translator dùng một mini state
machine để dịch C → Python QF-LIA, hỗ trợ:

- `if (...) { ... } [else { ... }]` (kể cả `if (!cond) return 0;` 1 dòng)
- `while (...) { ... }`, `for (init; cond; step) { ... }`
- Khai báo đơn và đa biến (`int x;`, `unsigned int n,i,j,l=0;`)
- `__VERIFIER_assert(cond)`, `reach_error();abort();` (sửa thành `assert`)
- `assume_abort_if_not(cond)`, `++x`, `x++`, `return 0;`
- Hex literal `0x...` → `int(..., 16)`
- C suffix `U`, `L`, `UL` → strip

**73/73 file SV-COMP được dịch thành công** (5 file `.c` còn lại là helper
Makefile chứ không phải benchmark).

Tuy nhiên: phần lớn file dịch ra vẫn có syntax lỗi (`unmatched ')'`,
`unindent does not match`, `invalid syntax`) — những file này bị subset reject
với lý do `TRANSLATION_ERROR`. Đây là **giới hạn đã được ghi nhận** trong
`summary.md`.

---

## 8. Quá trình debug — từng sự cố

### 8.1 Python 3.14 — `ast.arg.name` không tồn tại

Python 3.14 đổi `ast.arg.name` → `ast.arg.arg`. Lỗi đầu tiên:

```
AttributeError: 'arg' object has no attribute 'name'
```

→ Sửa `subset.py` và `executor.py` dùng `getattr(arg, "arg", None) or getattr(arg, "name", None)`.

### 8.2 `z3.is_bool_value` không tồn tại

Trong z3 5.1.0, `is_bool_value` đã bị bỏ. Lỗi thứ hai:

```
AttributeError: module 'z3' has no attribute 'is_bool_value'
```

→ Sửa `executor._rename()` wrap trong `try/except AttributeError`.

### 8.3 Translator `_split_top_level` nuốt statement

`while (l < n) { if(!(l%2)) i = i+1; else j = j+1; ... }` —
`_split_top_level` không bắt được vì nó tách theo `;` ở top-level, nhưng
`if(!(l%2)) i = i + 1` nằm trong `{ ... }` nên `;` vẫn ở top-level. Vấn đề
nằm ở pattern matching phức tạp.

→ Viết lại `_compile_block` với `_find_if_no_brace` (tách `if <cond> <single-stmt> [else <single-stmt>]` không có `{`). Cũng đổi thứ tự match:
**k declarations trước, while/for/if sau** (vì regex `if\s*\(` ăn mất `int x`).

### 8.4 Hex literal `0x0fffffff`

`while x < 0x0fffffff` — Python không tự convert hex literal. Z3 nhận `Int`
sort nhưng `<` so sánh với `IntVal(0xfffffff)` = không hợp lệ trong Python
lexpr:

```
SyntaxError: invalid syntax / z3 Z3Exception
```

→ Thêm `re.sub(r"0[xX]([0-9A-Fa-f]+)", lambda m: str(int(m.group(0), 16)), expr)` vào translator.

### 8.5 Hex C suffix `u`, `l`, `ul`

`20000001` thường đi kèm suffix `int SIZE = 20000001;` không có suffix,
nhưng `0xffffU` hay `10L` có. → Strip suffix trong translator.

### 8.6 `__VERIFIER_nondet_int()` xuất hiện trong loop body

Translator đã dịch `n = __VERIFIER_nondet_uint()` thành `n = __sym('uint')`,
nhưng subset reject vì `__sym` không nằm trong builtins whitelist và `'uint'`
là string (không phải QF-LIA value). Cuối cùng giải quyết bằng:

- Bỏ hoàn toàn `__sym` trong Python output (vì params đã symbolic).
- Translator drop `__VERIFIER_nondet_*()` thành `0` (literal int).
- Function signature `def f(a, b, c):` — params là symbolic từ Z3.

### 8.7 Assert wrapper cho Int

`__VERIFIER_assert(z % 4)` trong C là hợp lệ (truthy check) nhưng trong
Python `assert <int>` cần Bool → Z3 throws `Value cannot be converted into a Z3 Boolean value`.

→ Translator wrap thành `assert (z % 4) != 0` nếu expr không chứa comparison.

### 8.8 `z3.Not(z % 2)` — Not cần Bool operand

`assert not (z % 2)` trong Python: trong Z3 `Not` chỉ nhận Bool. Cùng lỗi
8.7 nhưng khác fix: `_eval_expr` của executor sửa `Not`:

```python
if z3.is_bool(inner):
    return z3.Not(inner)
return inner == 0     # Python: not <int> == (int == 0)
```

### 8.9 Sort mismatch trong reachability và path_condition

`build_reachability` ghép `z3.And(path_cond, t.guard)` — nhưng `t.guard`
cho `while l < 0:` là `IntVal(0)` (không phải Bool). Lỗi:

```
Sort mismatch at argument #1 for function (declare-fun and (Bool Bool) Bool)
supplied sort is Int
```

→ Coerce `guard` thành Bool trước khi lưu (`guard != 0` nếu không phải Bool),
cũng coerce trong `build_reachability`.

### 8.10 `ast.If` merge state với guard Int

Khi `if (cond):` không phải Bool thì `z3.If(cond, t_val, e_val)` cũng fail.
→ Wrap trong `try/except z3.Z3Exception` và fallback về `t_val` (cho then
branch) hoặc `e_val` (cho else branch).

### 8.11 CRUXEval sample_51 — nested function definition

Sample `sample_51` có `s = '<' * 10\ndef f(num):\n    if num % 2 == 0:\n return s\n    else:\n        return num - 1`. Translator CRUXEval của tôi
ban đầu drop dòng đầu (`s = '<' * 10`) rồi re-emit `def f(num):` → trùng
`def f`, gây nested FunctionDef khiến executor crash.

→ Sửa `_cruxeval_python_subset`: tìm dòng `def f(...)` đầu tiên, drop mọi
thứ trước, dedent body.

### 8.12 CRUXEval filter cho nested fn

Sau fix 8.11 vẫn còn một số sample có nested function (vd `def g(): def inner(): ...`). → Thêm `has_nested` check trong `_cruxeval_python_subset`.

### 8.13 Path normalization cho ground truth

`program_id` lưu dạng `sv-benchmarks-loops-py__loops-crafted-1__sumt2`
nhưng ground truth key là `loops-crafted-1/sumt2`. Hàm `_verdict_matrix`
phải normalize trước khi so sánh.

### 8.14 Counterexample `z3_model` rỗng

`_counterexample_from_model` ban đầu gọi `model.eval(d, model_completion)`
với `d` là `FuncDeclRef` (không phải `ExprRef`) → raise "ast is not an
expression".

→ Đổi sang tạo `z3.Int(d.name())` rồi mới `model.eval(...)`, kiểm
`z3.is_int_value(v)` trước khi `as_long()`.

Sau fix này, 14 counterexample có model bindings (`{l: 1, n: 1, i: 0, j: 0}`
cho sumt2, etc.), 2 trong số đó replay reproduce thành công.

### 8.15 FDR của lần chạy lịch sử

Đây là output của commit cũ, khi `UNANCHORED` vẫn dùng path và executor
chung. Không dùng các con số này để mô tả baseline hiện tại; `compare_baselines`
hiện tính claim-level matrix từ mọi `claim_result` và sinh diễn giải động.

---

## 9. Kết quả persisted của lần refresh deterministic

```
188 runs completed (0 errors)
```

Toàn bộ 188 chương trình (94 mỗi baseline) chạy không lỗi:

- 73 SV-COMP loop benchmarks (đã dịch từ `.c` → `.py`)
- 21 CRUXEval samples (int/bool/None args only)

**Subset pass rate của persisted run**: 12/94 = 12.8% (cả hai baseline).
Phần lớn SV-COMP file bị subset reject do translator xuất ra syntax lỗi.
Đây là kết quả deterministic hiện tại; chạy lại `run_benchmark.py` sẽ tạo
output mới với thời gian Z3 khác nhau.

**Verdict matrix của persisted run** (claim-level trên 17 claims có
ground-truth):


| Baseline     | TP  | FP  | FN  | TN  | FDR   |
| ------------ | --- | --- | --- | --- | ----- |
| AST_ANCHORED | 0   | 0   | 16  | 1   | n/a   |
| UNANCHORED   | 0   | 0   | 16  | 1   | n/a   |


**Counterexample Replay (CRVR) của persisted run**: AST_ANCHORED có 8
counterexamples, 2 reproduce thành công trên CPython subprocess → CRVR =
2/8 = 0.250. Direct Formalization không replay counterexamples nên CRVR là
n/a.

**Hallucination Rate của persisted run**: 0.0. Đây không phải bằng chứng rằng
mọi claim historical đều grounded.

---

## 10. Hạn chế đã biết & Hướng phát triển

1. **Translator coverage thấp** (~12%): nhiều cấu trúc C ngoài whitelist.
  Có thể cải thiện bằng libCST hoặc pycparser thay vì regex.
2. **Symbolic execution không có loop invariant**: z3 tìm được
  counterexample với input âm (vd `n = -1`), nên phần lớn programs cho
   COUNTEREXAMPLE thay vì VERIFIED. Cần thêm invariant generation
   (vd predicate abstraction, ICE).
3. **LLM là opt-in**: benchmark mặc định deterministic từ source assertions;
  dùng `--use-llm` để gọi Gemini độc lập cho hai baseline và ghi token totals
  riêng.
4. **Test coverage**: `tests/test_regressions.py` bao phủ các lỗi audit chính;
  vẫn cần mở rộng nếu executor hỗ trợ thêm control-flow.

---

## 11. Tài liệu liên quan

- `prototype/results/summary.md` — báo cáo Markdown đối chứng
- `prototype/results/_run.log` — log thực nghiệm
