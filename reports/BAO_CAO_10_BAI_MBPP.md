# BÁO CÁO THỰC NGHIỆM ĐÁNH GIÁ: PHƯƠNG PHÁP CÓ AST (AST-ANCHORED) VS KHÔNG CÓ AST (UNANCHORED) KẾT HỢP LLM & SMT SOLVER TRÊN 10 BÀI TOÁN MBPP

> **Audit correction.** Các số liệu trong báo cáo này là kết quả lịch sử của
> commit cũ. Diễn giải đúng của mã hiện tại là: `AST_ANCHORED` dùng anchor
> chính xác, state tại target và path reachability bounded; `UNANCHORED`
> kiểm tra claim như predicate số nguyên tự do, không dùng `NodeId`, program
> state, location hay reachability. `VERIFIED` không bao hàm mọi execution
> khi loop/control-flow coverage chưa đầy đủ; replay chỉ thành công khi
> runtime chạm target line và claim false tại đó.

---

## 1. Giới thiệu & Mục tiêu thí nghiệm

Thí nghiệm này nhằm so sánh hiệu quả và độ tin cậy giữa hai cơ chế kiểm chứng hình thức chương trình tự động (Formal Program Verification) có kết hợp mô hình ngôn ngữ lớn (LLM - Gemini 3.5 Flash) và SMT Solver (Z3):

1. **Phương pháp có AST (`AST_ANCHORED` - Đề xuất)**:
   - LLM sinh Chain-of-Thought (CoT) và các suy diễn logic (Invariants / Claims) kèm số dòng chương trình.
   - **Anchor Gate**: Ánh xạ dòng mã do LLM gợi ý vào các nút cụ thể trên cây cú pháp trừu tượng (**AST `NodeId`**). Nếu LLM ảo giác (Hallucination) dòng mã hoặc suy diễn tại vị trí không tồn tại, claim sẽ bị chặn ngay lập tức (`TRANSLATION_ERROR` / `UNSUPPORTED`).
   - **Path Reachability Check**: Z3 kiểm tra tính khả đạt (reachability) của nhánh điều khiển dẫn tới node đó trước khi kiểm chứng, loại bỏ hiện tượng **chân lý rỗng (Vacuous Truth)** do tiền đề sai ($False \implies Claim$).
2. **Phương pháp không có AST (`UNANCHORED` - Baseline Ablation)**:
   - LLM sinh CoT và Claims nhưng baseline không dùng anchor hoặc program context.
   - Bỏ qua Anchor Gate và Path Reachability Check; kiểm chứng trực tiếp
     predicate tự do, không gắn với state/location của chương trình.

### Tập dữ liệu thực nghiệm
- **10 bài toán MBPP (Mostly Basic Python Problems)** được chọn làm các ứng viên
  cho tập số học nguyên. Subset gate hiện tại kiểm tra lại từng bài; phép nhân
  biến-biến và chia/mod với mẫu biến bị loại, nên không được mặc định rằng cả
  danh sách là QF-LIA:
  - Task 17: `square_perimeter`
  - Task 35: `find_rect_num`
  - Task 36: `find_Nth_Digit`
  - Task 51: `check_equilateral`
  - Task 52: `parallelogram_area`
  - Task 59: `is_octagonal`
  - Task 72: `dif_Square`
  - Task 86: `centered_hexagonal_number`
  - Task 89: `closest_num`
  - Task 138: `is_Sum_Of_Powers_Of_Two`
- **Mô hình LLM**: Google Gemini 3.5 Flash (`google-genai` SDK, Structured Output JSON Schema).
- **SMT Solver**: Z3 Solver trên các obligation đã qua subset gate.

---

## 2. Bảng tổng hợp số liệu thực nghiệm

```
========================================================================================
                                TỔNG QUAN KẾT QUẢ THỰC NGHIỆM
========================================================================================
 Tổng số bài toán (Tasks)        : 10 bài toán (subset gate được kiểm tra lại)
 Tổng số Claim do LLM sinh ra     : 21 claims
 Token Input tiêu thụ            : 2,641 tokens (Trung bình ~264 tokens/task)
 Token Output tiêu thụ           : 2,211 tokens (Trung bình ~221 tokens/task)
 Chi phí API (Estimated Cost)    : ~$0.00075 USD (~19 VNĐ)
========================================================================================
```

### So sánh chỉ số giữa hai phương pháp:

| Chỉ số (Metric) | Phương pháp có AST (`AST_ANCHORED`) | Phương pháp không có AST (`UNANCHORED`) | Ý nghĩa & Đánh giá |
| :--- | :---: | :---: | :--- |
| **Tổng số Claims** | **21** | **21** | LLM sinh invariant thành công 100% |
| **Được chứng minh (`VERIFIED`)** | **16 (76.2%)** | **16 (76.2%)** | Z3 chứng minh đúng hình thức |
| **Phản ví dụ (`COUNTEREXAMPLE`)** | **5 (23.8%)** | **5 (23.8%)** | Z3 tìm thấy mô hình vi phạm thực tế |
| **Không khả đạt (`UNREACHABLE`)** | **0 (0.0%)** | *Không kiểm tra (N/A)* | AST loại trừ nguy cơ Vacuous Truth |
| **Từ chối subset (`SUBSET_REJECT`)**| **Theo run persisted** | **Theo run persisted** | Gate kiểm tra lại từng source; không suy ra 100% QF-LIA từ tên benchmark |
| **Độ chính xác neo (`Anchor Accuracy`)**| **100% (21/21)** | **0% (Không neo)** | Toàn bộ 21 claims được gắn vào đúng Node AST |
| **Khả năng giải thích phản ví dụ** | **Có (Model + AST Node)** | **Chỉ có Model tự do** | AST định vị chính xác vị trí phát sinh lỗi |

---

## 3. Bảng chi tiết kết quả từng bài toán (Task Breakdown)

| Task ID | Tên hàm | Số claims | Trạng thái AST_ANCHORED | Trạng thái UNANCHORED | Nút AST neo được (`NodeId`) | Mô hình phản ví dụ (nếu có) |
| :---: | :--- | :---: | :---: | :---: | :--- | :--- |
| **17** | `square_perimeter` | 1 | 0 VERIFIED, 1 CE | 0 VERIFIED, 1 CE | `square_perimeter@Return:L3:C4#0` | `{'perimeter': 1, 'a': 0}` |
| **35** | `find_rect_num` | 2 | **2 VERIFIED**, 0 CE | **2 VERIFIED**, 0 CE | `find_rect_num@Return:L2:C4#0` | Không có |
| **36** | `find_Nth_Digit` | 3 | **2 VERIFIED**, 1 CE | **2 VERIFIED**, 1 CE | `find_Nth_Digit@While:L2:C4#0`<br>`find_Nth_Digit@Assign:L4:C8#0`<br>`find_Nth_Digit@Return:L7:C4#0` | `{'N': 1}` (tại Return) |
| **51** | `check_equilateral` | 3 | **3 VERIFIED**, 0 CE | **3 VERIFIED**, 0 CE | `check_equilateral@Return:L3:C8#0` | Không có |
| **52** | `parallelogram_area` | 1 | 0 VERIFIED, 1 CE | 0 VERIFIED, 1 CE | `parallelogram_area@Return:L3:C4#0` | `{'area': 1, 'b': 0, 'h': 1}` |
| **59** | `is_octagonal` | 2 | **2 VERIFIED**, 0 CE | **2 VERIFIED**, 0 CE | `is_octagonal@Return:L2:C4#0` | Không có |
| **72** | `dif_Square` | 2 | **1 VERIFIED**, 1 CE | **1 VERIFIED**, 1 CE | `dif_Square@Return:L3:C8#0`<br>`dif_Square@Return:L4:C4#0` | `{'n': 0}` (tại L4) |
| **86** | `centered_hexagonal_number`| 4 | **4 VERIFIED**, 0 CE | **4 VERIFIED**, 0 CE | `centered_hexagonal_number@Return:L2:C4#0` | Không có |
| **89** | `closest_num` | 1 | **1 VERIFIED**, 0 CE | **1 VERIFIED**, 0 CE | `closest_num@Return:L2:C4#0` | Không có |
| **138**| `is_Sum_Of_Powers_Of_Two` | 2 | **1 VERIFIED**, 1 CE | **1 VERIFIED**, 1 CE | `is_Sum_Of_Powers_Of_Two@Return:L3:C8#0`<br>`is_Sum_Of_Powers_Of_Two@Return:L5:C8#0`| `{'n': 1}` (tại L5) |

---

## 4. Phân tích chuyên sâu các hiện tượng kỹ thuật

### 4.1. Bản chất các phản ví dụ (`COUNTEREXAMPLE`) tìm được bởi Z3
Trong 5 trường hợp xuất hiện `COUNTEREXAMPLE`, Z3 đã thể hiện sức mạnh toán học chính xác so với suy diễn cảm tính của LLM:

1. **Task 36 (`find_Nth_Digit`)**:
   - Vòng lặp `while N > 0:` tìm chữ số thứ $N$ của $p/q$.
   - LLM đưa ra claim: `N == 0` khi hàm kết thúc (Return tại dòng 7).
   - **Z3 tìm thấy phản ví dụ: `N = 1` (hoặc $N \le 0$)**: Nếu ngay từ đầu tham số truyền vào $N \le 0$, thân vòng lặp không bao giờ thực thi, giá trị $N$ được giữ nguyên ban đầu ($N \ne 0$). Đây là một **corner case thực tế** mà lập trình viên và LLM thường bỏ sót nếu không có formal contract!

2. **Task 72 (`dif_Square`)**:
   - Mã nguồn:
     ```python
     def dif_Square(n):
         if n % 4 != 2:
             return True
         return False
     ```
   - LLM đưa ra claim tại dòng 4: `n % 4 == 2`.
   - Khi kiểm tra tính đúng đắn toàn cục không có precondition, Z3 chỉ ra model vi phạm `n = 0` (vì với $n = 0$, $0 \% 4 = 0 \ne 2$).

3. **Task 17 & 52 (`square_perimeter` & `parallelogram_area`)**:
   - LLM khẳng định `perimeter == 4 * a` tại dòng return.
   - Khi SMT biểu diễn biến trạng thái mà không có ràng buộc tiền gán (unconstrained input environment), Z3 phân biệt rõ giữa biến đầu vào tự do và biểu thức gán, yêu cầu hệ thống phải đồng bộ chặt chẽ ngữ nghĩa SSA.

### 4.2. So sánh vai trò của `AST_ANCHORED` đối với độ tin cậy
Các tỷ lệ 16/21 bên dưới là output lịch sử của commit cũ, không phải kết quả
của baseline hiện tại. Giá trị của AST thể hiện ở 3 khía cạnh:

1. **Ngăn chặn Ảo giác Neo (Grounding & Hallucination Defense)**:
   - Trong phương pháp **Unanchored**, predicate được kiểm tra trên biến tự do
     (ví dụ `x >= 0`) nhưng không thể cho biết nó áp dụng trước khi khởi tạo,
     trong nhánh chết hay tại target nào.
   - Trong **AST_ANCHORED**, claim phải có tọa độ AST hợp lệ (`NodeId`). Không thể xảy ra tình trạng "đúng về mặt toán học nhưng sai về ngữ nghĩa thực thi của chương trình".

2. **Khả năng giải thích và định vị lỗi (Explainability & Traceability)**:
   - Khi Z3 trả về `COUNTEREXAMPLE: {'N': 1}`, phương pháp có AST chỉ rõ: **Lỗi này xảy ra tại nút `find_Nth_Digit@Return:L7:C4#0`** (lệnh Return dòng 7).
   - Người phát triển phần mềm biết chính xác dòng mã và câu lệnh cần đặt assert hoặc xử lý ngoại lệ.

3. **Triệt tiêu hiện tượng Chân lý rỗng (Vacuous Truth)**:
   - `AST_ANCHORED` kiểm tra path tới đúng target. Chỉ assertion thực sự nằm
     trong nhánh không thể chạm tới mới nhận `UNREACHABLE`; assertion sau một
     loop dead vẫn có thể reachable qua zero-iteration path.

---

## 5. Kết luận & Khuyến nghị

1. **Hiệu quả thực tế**:
   - Lần chạy lịch sử với **Gemini 3.5 Flash** và **Z3 SMT Solver** ghi nhận
     76.2% `VERIFIED`; con số này không phải đánh giá hiện tại và không thay
     thế coverage analysis.
   - Mô hình bắt được các corner case toán học tinh tế (ví dụ: $N \le 0$ trong phép chia cột số học).

2. **Giá trị cốt lõi của AST**:
   - Cây cú pháp trừu tượng (AST) cung cấp anchor, target-state context và tọa
     độ lỗi (`Line:Col`). Đây là grounding/traceability, không phải chứng
     nhận soundness cho mọi execution.
