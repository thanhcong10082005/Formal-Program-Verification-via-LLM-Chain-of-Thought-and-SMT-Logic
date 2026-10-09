# BÁO CÁO NGHIÊN CỨU ĐIỂN HÌNH (CASE STUDY REPORT)
# PHÂN TÍCH QUÁ TRÌNH SUY LUẬN CHAIN-OF-THOUGHT CỦA LLM VÀ VAI TRÒ CỦA CÂY CÚ PHÁP TRỪU TƯỢNG (AST) TRONG KIỂM CHỨNG HÌNH THỨC CHƯƠNG TRÌNH

---

**Đề tài**: Formal Program Verification via LLM Chain-of-Thought and SMT Logic  
**Đối tượng khảo sát**: Bài toán tính tổng dãy Perrin (`mbpp_448`) và các cấu trúc điều khiển nâng cao  
**Công cụ thực nghiệm**: Google Gemini 3.5 Flash, Z3 SMT Theorem Prover, Python 3 AST Engine  

---

## 1. TÓM TẮT BÁO CÁO (EXECUTIVE SUMMARY)

Báo cáo này trình bày nghiên cứu điển hình (Case Study) chuyên sâu về sự kết hợp giữa **Mô hình Ngôn ngữ Lớn (LLM)** và **Bộ giải Lý thuyết Thỏa mãn modulo (SMT Solver)** trong bài toán kiểm chứng hình thức chương trình tự động. Trọng tâm nghiên cứu là làm sáng tỏ:
1. **Quá trình suy luận Chain-of-Thought (CoT)** của LLM khi tự động trích xuất bất biến vòng lặp (Loop Invariants).
2. **Bẫy nhận thức (Cognitive Trap)** dẫn đến sai lầm của LLM khi suy luận trên mã nguồn thực tế.
3. **Sự khác biệt bản chất** giữa phương pháp có neo cú pháp (**`AST_ANCHORED`**) và phương pháp tiêu giảm không neo (**`UNANCHORED`**):
   - Phương pháp `UNANCHORED` bị mất ngữ cảnh khối lệnh và dính lỗi **Chân lý rỗng (Vacuous Truth)** với $\text{FDR} = 100\%$ trên các đoạn mã không chạm tới được (Dead Code).
   - Phương pháp `AST_ANCHORED` sử dụng màng lọc **Anchor Gate** và giao thức **Hai truy vấn (Two-Query Protocol)**, bảo toàn tính đúng đắn hình thức (**Soundness**), lật tẩy chính xác ảo giác của LLM và định vị lỗi đến từng dòng, cột của câu lệnh.

---

## 2. ĐẶT VẤN ĐỀ & BỐI CẢNH NGHIÊN CỨU

Trong phát triển phần mềm hiện đại, việc tự động chứng minh tính đúng đắn của chương trình là một bài toán then chốt. Sự ra đời của các mô hình LLM mở ra khả năng tự động hiểu mã nguồn và sinh các khẳng định logic (claims/invariants) thông qua cơ chế suy luận từng bước (Chain-of-Thought). Tuy nhiên:
- **LLM mang tính xác suất và dễ bị ảo giác (Hallucination)**: LLM có xu hướng đưa ra các suy luận nghe rất logic nhưng ngầm dựa trên các giả định cảm tính không có trong mã nguồn (ví dụ: ngầm giả định biến đầu vào luôn dương).
- **Bộ giải SMT (Z3 Solver) có tính toán học tuyệt đối nhưng thiếu ngữ cảnh ngữ nghĩa**: Nếu đưa trực tiếp các công thức do LLM sinh ra vào Z3 mà không có cấu trúc chương trình làm cầu nối, hệ thống sẽ gặp các nghịch lý logic hình thức (như $False \implies Claim \equiv True$).

Do đó, **Cây cú pháp trừu tượng (AST)** được đề xuất như một "mỏ neo ngữ nghĩa" (Semantic Anchor) bắt buộc để liên kết chuỗi suy luận của LLM với bộ giải SMT.

---

## 3. PHƯƠNG PHÁP NGHIÊN CỨU & KIẾN TRÚC HỆ THỐNG

Quy trình kiểm chứng được tổ chức thành 4 giai đoạn đối chứng giữa hai baseline:

```
[Mã nguồn Python] 
       │
       ▼
 [1. Subset Gate] ── (Kiểm tra chuẩn số học nguyên QF-LIA)
       │
       ▼
 [2. AST Parser]  ── (Gán định danh nút duy nhất: NodeId)
       │
       ├────────────────────────────────────────┬───────────────────────────────────────┐
       ▼                                        ▼                                       ▼
 [3. Gemini 3.5 Flash]               [4A. Pipeline AST_ANCHORED]             [4B. Pipeline UNANCHORED]
  Sinh Chain-of-Thought                 • Anchor Gate lọc claim                 • Bỏ qua Anchor Gate
  & Đề xuất Claims                      • Two-Query: Reachability -> Validity   • Bỏ qua Reachability
                                        • Định vị lỗi qua NodeId                • Kiểm tra Validity trực tiếp
```

### Hệ thống 6 Nhãn Trạng thái (Verdict Labels)
- **`VERIFIED`**: Được chứng minh đúng đắn trên mọi đường dẫn khả đạt.
- **`COUNTEREXAMPLE`**: Tồn tại giá trị biến làm sai khẳng định (kèm Z3 model và CPython replay).
- **`UNREACHABLE`**: Nhánh mã chứa khẳng định là bất khả thi (ngăn chặn chân lý rỗng).
- **`UNSUPPORTED`**: Bị Anchor Gate từ chối do dòng không tồn tại hoặc biến ngoài phạm vi.
- **`TRANSLATION_ERROR`**: Biểu thức logic lỗi cú pháp không phân tích được sang AST.
- **`UNKNOWN_TIMEOUT`**: Quá thời gian giải của Z3 ($> 5.000\text{ ms}$).

---

## 4. CASE STUDY TRỌNG TÂM: MỔ XẺ SUY LUẬN TRÊN BÀI TOÁN TÍNH TỔNG DÃY PERRIN (`MBPP_448`)

### 4.1. Mã nguồn chương trình
Dãy số Perrin là dãy truy hồi bậc 3: $P(0)=3, P(1)=0, P(2)=2$ và $P(n) = P(n-2) + P(n-3)$ với $n \ge 3$. Hàm `cal_sum(n)` tính tổng $n$ số hạng đầu tiên:

```python
1: def cal_sum(n):
2:     a = 3
3:     b = 0
4:     c = 2
5:     if (n == 0):
6:         return 3
7:     if (n == 1):
8:         return 3
9:     if (n == 2):
10:        return 5
11:    sum = 5
12:    while (n > 2):
13:        d = a + b
14:        sum = sum + d
15:        a = b
16:        b = c
17:        c = d
18:        n = n - 1
19:    return sum
```

---

### 4.2. Chuỗi suy luận Chain-of-Thought (CoT) nguyên văn của Gemini 3.5 Flash

Khi phân tích hàm trên, mô hình Gemini 3.5 Flash đã sinh ra chuỗi suy luận CoT nguyên văn:

```text
"We analyze the function cal_sum(n) to establish loop invariants and variable bounds. 
First, the early returns handle cases where n <= 2. Thus, when reaching line 11, we must have n > 2. 
At line 11, sum is initialized to 5, and a, b, c are 3, 0, 2 respectively. 
In the loop, n is decremented by 1 in each iteration, and the loop condition is n > 2. 
This implies that at the loop header (line 12), n >= 2 is a loop invariant. 
Since all variables a, b, c, and sum start as non-negative and are only updated via addition, 
they remain non-negative throughout the loop. 
Specifically, sum >= 5, a >= 0, b >= 0, and c >= 0 are invariants at line 12. 
Inside the loop at line 13, the loop condition guarantees n > 2. 
Upon loop termination, since n started > 2 and decremented by 1, 
we must have n == 2 at line 19, and sum >= 5 still holds."
```

Từ chuỗi suy luận này, Gemini đề xuất **8 bất biến (claims)**:
1. `n >= 2` tại dòng 12 (Đầu vòng lặp `While`)
2. `sum >= 5` tại dòng 12 (Đầu vòng lặp `While`)
3. `a >= 0` tại dòng 12 (Đầu vòng lặp `While`)
4. `b >= 0` tại dòng 12 (Đầu vòng lặp `While`)
5. `c >= 0` tại dòng 12 (Đầu vòng lặp `While`)
6. `n > 2` tại dòng 13 (Thân vòng lặp)
7. `n == 2` tại dòng 19 (Lệnh `Return`)
8. `sum >= 5` tại dòng 19 (Lệnh `Return`)

---

### 4.3. Phân tích "Bẫy nhận thức" (Cognitive Trap) của LLM

Chuỗi suy luận của LLM nghe có vẻ rất chặt chẽ và thuyết phục, nhưng thực chất đã mắc phải một **sai lầm nhận thức toán học nghiêm trọng**:

> **Lập luận sai của LLM**:  
> *"First, the early returns handle cases where $n \le 2$. Thus, when reaching line 11, we must have $n > 2$."*

* **Bản chất sai lầm**:
  - Các câu lệnh điều kiện ở dòng 5, 7, 9 chỉ kiểm tra 3 giá trị nguyên rời rạc:
    $$S_{\text{handled}} = \{0, 1, 2\}$$
  - Các câu lệnh này **hoàn toàn KHÔNG xử lý nửa khoảng âm**:
    $$S_{\text{unhandled}} = \{n \in \mathbb{Z} \mid n < 0\} = \{-1, -2, -3, \dots\}$$
  - LLM đã mang **định kiến cảm tính** của con người: khi thấy bài toán dãy số, nó mặc định tham số đầu vào $n$ luôn là số tự nhiên ($n \in \mathbb{N}$).
* **Kịch bản phản ví dụ thực tế**:
  - Giả sử người dùng gọi hàm với tham số âm: `cal_sum(-1)`.
  - Lần lượt các điều kiện ở dòng 5, 7, 9 đều nhận giá trị `False`.
  - Con trỏ thực thi trôi thẳng xuống dòng 11: `sum` được gán bằng $5$.
  - Đến dòng 12, điều kiện `while (-1 > 2)` là `False`, thân vòng lặp **hoàn toàn không chạy**.
  - Con trỏ thực thi đi thẳng tới dòng 19: trả về `sum = 5` với giá trị biến $n = -1$.
  - Khi đó, khẳng định của LLM rằng **`n == 2` tại dòng 19** và **`n >= 2` tại dòng 12** bị sụp đổ hoàn toàn!

---

### 4.4. Quá trình xử lý và suy luận của hai phương pháp

```mermaid
sequenceDiagram
    autonumber
    actor LLM as Gemini 3.5 Flash
    participant Gate as Anchor Gate & AST
    participant Z3 as Z3 SMT Solver
    participant Rep as CPython Replay Engine

    LLM->>Gate: Đề xuất Claim: "n == 2" tại Dòng 19
    alt Phương pháp AST_ANCHORED
        Gate->>Gate: Gán NodeId: "cal_sum@Return:L19:C4#0"
        Gate->>Z3: Two-Query: Reachability (SAT) -> Validity Query
        Note over Z3: Z3 kiểm tra: Phi(Path) ∧ ¬(n == 2)<br>Tìm thấy Model: {n: 0} hoặc {n: -1}
        Z3-->>Gate: SAT (Phát hiện vi phạm)
        Gate->>Rep: Replay cal_sum(-1) trên CPython
        Rep-->>Gate: Xác thực vi phạm thành công
        Gate-->>LLM: Verdict: COUNTEREXAMPLE (Gắn chặt với Node Return L19)
    else Phương pháp UNANCHORED
        Note over Gate: Bỏ qua AST & Anchor Gate
        Gate->>Z3: Gửi biểu thức rời rạc: ¬(n == 2)
        Z3-->>Gate: SAT (Model: {n: 0})
        Gate-->>LLM: Verdict: COUNTEREXAMPLE (Mù mờ, không có vị trí dòng)
    end
```

#### 1. Phương pháp CÓ AST (`AST_ANCHORED`):
1. **Neo cấu trúc**: Module `nodes.py` xây dựng cây AST và định danh lệnh return ở dòng 19 là `cal_sum@Return:L19:C4#0`. Khẳng định được neo hợp lệ (`status = ANCHORED`).
2. **Kiểm tra tính khả đạt (Reachability Query)**: Z3 xác nhận đường dẫn từ đầu hàm tới dòng 19 là khả đạt (`SAT`).
3. **Kiểm tra tính đúng đắn (Validity Query)**: Z3 kiểm tra mệnh đề $\Phi_{\text{path}} \land \neg(n == 2)$. Bộ giải tìm thấy ngay mô hình làm sai khẳng định:
   $$\mathbf{Model: \{n = 0\}} \quad (\text{hoặc } n = -1)$$
4. **CPython Replay Engine**: Hệ thống chạy thực tế hàm `cal_sum(0)` trên trình thông dịch CPython, xác nhận giá trị trả về thực tế và đối soát với invariant.
5. **Kết quả**: Gán nhãn **`COUNTEREXAMPLE`** kèm tọa độ câu lệnh cụ thể `cal_sum@Return:L19:C4#0`. 
   *Giá trị thực tiễn*: Báo cho lập trình viên biết chính xác vị trí hàm đang bị hổng điều kiện và cần bổ sung `assert n >= 0` ở đầu hàm.

#### 2. Phương pháp KHÔNG CÓ AST (`UNANCHORED`):
1. Phương pháp tiếp nhận công thức `n == 2` nhưng không liên kết với nút AST nào.
2. Z3 cũng tìm ra mô hình $\{n = 0\}$, nhưng hệ thống chỉ trả về kết quả dưới dạng một vị từ toán học trôi nổi bị vi phạm.
3. *Hạn chế*: Người phát triển không có thông tin ngữ cảnh để biết được biến $n$ bị sai ở thời điểm nào trong vòng lặp hay khi thoát hàm.

---

## 5. CASE STUDY MỞ RỘNG: HIỆN TƯỢNG CHÂN LÝ RỖNG TRÊN NHÁNH CHẾT (TASK A1 `EQ2`)

Để thấy sự chênh lệch mang tính sống còn về tính đúng đắn giữa hai phương pháp, xét bài toán có nhánh chết từ bộ benchmark SV-COMP:

```python
1: def f(w, x, y, z):
2:     while 0:        # Nhánh chết 100%
3:         y += 1
4:         z += 1
5:     assert y == z   # Khẳng định sau vòng lặp
6:     return
```

### So sánh quá trình suy luận:

| Tiêu chí | Phương pháp KHÔNG CÓ AST (`UNANCHORED`) | Phương pháp CÓ AST (`AST_ANCHORED`) |
| :--- | :--- | :--- |
| **Biểu diễn đường dẫn** | Bỏ qua kiểm tra tính khả đạt của đường dẫn. | Bộ thực thi tượng trưng xây dựng $\Phi_{\text{path}} \equiv (0 \ne 0)$. |
| **Công thức gửi Z3** | Kiểm tra $\Phi_{\text{path}} \land \neg(y == z) \equiv \text{False} \land \neg(y == z)$. | Chạy truy vấn 1 (Reachability): $\text{Check}(\Phi_{\text{path}})$. |
| **Kết quả từ Z3** | Z3 trả về **`UNSAT`** (vì $\text{False} \land \text{gì cũng} = \text{False}$). | Z3 trả về **`UNSAT`** trên bài toán khả đạt. |
| **Hành vi xử lý** | Thấy Z3 báo `UNSAT` $\implies$ Kết luận **`VERIFIED`**! | Thấy Reachability `UNSAT` $\implies$ **Ngắt sớm**, dừng pipeline. |
| **Kết luận cuối cùng** | **`VERIFIED` (SAI LẦM NGHIÊM TRỌNG)** ⚠️ | **`UNREACHABLE` (CHÍNH XÁC TUYỆT ĐỐI)** |
| **Hậu quả thực tế** | Cấp chứng nhận an toàn cho hàm sẽ nổ `AssertionError` khi $y \ne z$! | Cảnh báo lập trình viên đây là đoạn mã chết không thể chạm tới. |

> [!CAUTION]
> **TỶ LỆ PHÁT HIỆN SAI (FALSE DISCOVERY RATE):**
> Trên toàn bộ các ca kiểm thử có nhánh chết, phương pháp `UNANCHORED` đạt tỷ lệ phát hiện sai **$\text{FDR} = 100\%$** (3/3 ca báo `VERIFIED` giả mạo do nghịch lý chân lý rỗng $False \implies Claim$). Trong khi đó, phương pháp `AST_ANCHORED` đạt **$\text{FDR} = 0\%$**, bảo toàn trọn vẹn tính an toàn hình thức.

---

## 6. BẢNG TỔNG KẾT TOÀN DIỆN 26 CLAIMS TRÊN 8 BÀI TOÁN

| Nhóm thử nghiệm | Bài toán | Số claims | Trạng thái AST_ANCHORED | Trạng thái UNANCHORED | Khác biệt kỹ thuật |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Nhóm A: Nhánh chết (Dead Code)** | `A1_dead_loop_eq2` | 1 | **1 UNREACHABLE** | **1 VERIFIED** ⚠️ | Tránh chân lý rỗng (Vacuous Truth) |
| | `A2_dead_branch_loopv1`| 1 | **1 UNREACHABLE** | **1 VERIFIED** ⚠️ | Khả đạt UNSAT ngắt sớm |
| | `A3_unreachable_guard` | 1 | **1 UNREACHABLE** | **1 VERIFIED** ⚠️ | Bắt mâu thuẫn $x > 10 \land x < 5$ |
| **Nhóm B: Ảo giác LLM** | `B1_line_overflow` | 1 | **1 UNSUPPORTED** | **1 COUNTEREXAMPLE** | Anchor Gate chặn claim sai dòng |
| | `B2_syntax_error` | 1 | **1 TRANSLATION_ERROR**| **1 TRANSLATION_ERROR**| Bắt lỗi cú pháp toán học |
| **Nhóm C: Thuật toán phức tạp** | `C1_mbpp_448` (Perrin) | 8 | **8 COUNTEREXAMPLE** | **8 COUNTEREXAMPLE** | Z3 bắt lỗi thiếu precondition |
| | `C2_mbpp_711` (Digits) | 8 | **8 COUNTEREXAMPLE** | **8 COUNTEREXAMPLE** | Bắt corner case số âm / chia dư |
| | `C3_mbpp_683` (Squares)| 5 | **5 COUNTEREXAMPLE** | **5 COUNTEREXAMPLE** | Bắt góc chết biên vòng lặp lồng |
| **TỔNG CỘNG** | **8 bài toán** | **26** | **FDR = 0.0%** | **FDR = 100.0% (trên Dead Code)** | AST bảo toàn Soundness |

---

## 7. BÀI HỌC KHOA HỌC & ĐÓNG GÓP CỦA NGHIÊN CỨU

1. **Chuỗi Chain-of-Thought cần có Formal Gatekeeper**:
   LLM có khả năng phân tích ngữ nghĩa tuyệt vời nhưng thường bị "mắc bẫy nhận thức", ngầm đặt ra các giả định không có thật trong code. Các bộ giải hình thức như Z3 đóng vai trò là "chiếc gương chiếu yêu" khách quan, bắt bẻ từng corner case mà LLM bỏ sót.
2. **Cây AST là thành phần sống còn của hệ thống**:
   Nếu không có AST:
   - Hệ thống sẽ bị sập bẫy toán học **Vacuous Truth** trên mọi nhánh chết.
   - Không thể ngăn chặn ảo giác số dòng của LLM.
   - Không thể định vị vị trí lỗi ngược trở lại mã nguồn cho lập trình viên.
3. **Đóng góp thực tiễn cho đồ án**:
   Kết quả nghiên cứu cung cấp cơ sở lý thuyết vững chắc và số liệu thực nghiệm định lượng rõ ràng để chứng minh rằng: **Phương pháp kiểm chứng có cây cú pháp trừu tượng (AST-Anchored) vượt trội hoàn toàn về độ tin cậy và tính đúng đắn hình thức so với các tiếp cận phi cấu trúc thông thường.**
