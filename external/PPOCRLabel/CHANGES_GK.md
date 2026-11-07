# PPOCRLabel trong dự án này

Thư mục này là bản chép của **PPOCRLabel 3.1.6** (https://github.com/PFCCLab/PPOCRLabel, giấy phép **Apache-2.0**, xem
`LICENSE`; phần bản quyền ở đầu từng file được giữ nguyên) lấy từ `site-packages` để sửa trực tiếp cho nhu cầu của dự án.
Dự án chạy bản này (qua `scripts/ppocrlabel_boot.py`), **không cần `pip install PPOCRLabel`**: chỉ cần các thư viện của nó,
đã có trong `requirements-ocr.txt` (chủ yếu `PyQt5`; PaddleOCR-VL chỉ cần nếu bạn chọn engine đó). **Không cần `paddlepaddle`, `paddleocr`** cho chính PPOCRLabel.

Mọi chỗ đã sửa so với bản gốc được đánh dấu `[GK]` trong mã: `grep -rn "\[GK\]" external/PPOCRLabel`.

| File | Thay đổi |
|---|---|
| `libs/canvas.py` | `epsilon` (vùng bắt đỉnh/cạnh) tính theo **pixel màn hình** (`GRAB_PX = 8`) thay vì cố định 5 pixel ảnh; nếu không thì ảnh ~4000 px thu nhỏ vừa cửa sổ chỉ còn ~1 pixel màn hình và không kéo được góc box. `boundedMoveEdge`: kéo **cạnh**. Con trỏ hai chiều khi rê vào cạnh. |
| `libs/shape.py` | `nearestVertex` trả về `4 + i` khi con trỏ gần cạnh i (chỉ box 4 điểm, và không bắt cạnh khi đang giữ Ctrl). Thêm `distance_to_segment`, `is_horizontal_edge`. |
| `PPOCRLabel.py` | Thêm hành động **Select all boxes** (`Ctrl+Shift+A`, menu Edit) và `selectAllBoxes()`. **Bỏ nạp 11 mô hình PP-OCR** (PaddleOCR, TextRecognition, TextDetection, PPStructureV3) lúc khởi động, bỏ `import paddle`/`paddleocr`: `self.ocr`, `self.text_recognizer`, `self.text_detector` là các lớp bọc engine của dự án; hộp thoại **Choose OCR model** (`Ctrl+M`) chọn 1 trong 3 engine; nhận dạng bảng bị tắt. |
| `PPOCRLabel.py` (khởi động) | Thêm tham số **`--image_dir`** và `openDirAtStart()`: mở sẵn thư mục ảnh khi khởi động (app web truyền thư mục của dòng vừa bấm). Gọi thẳng `importDirImages`, vì `openDirDialog(silent=True)` của bản gốc bỏ qua đường dẫn truyền vào khi chưa có `lastOpenDir`. |
| `libs/gk_ocr.py` (mới) | `EngineOCR`, `EngineTextRecognition`, `EngineTextDetection`, `EngineState`: giữ đúng giao diện `predict(...)` PPOCRLabel gọi, chuyển việc cho `scripts/engine_bridge.py` của dự án. |
| `libs/autoDialog.py` | Truyền `image_path` cho `self.ocr.predict` (để engine dùng cache và nhãn trang). |
| `tablepyxl/tablepyxl.py` | Thay `paddle.utils.try_import` bằng `importlib` (không phụ thuộc paddlepaddle). |

Cách kéo cạnh hoạt động: chỉ số `hVertex` 0..3 là đỉnh như bản gốc, 4..7 là cạnh. Kéo cạnh dịch hai đỉnh của cạnh cùng một đoạn
theo pháp tuyến, nên hình chữ nhật vẫn là hình chữ nhật, box hơi nghiêng giữ nguyên độ nghiêng; không ra ngoài ảnh và không áp sát
cạnh đối diện hơn `MIN_SIDE` (2 pixel ảnh).

## Nhận dạng đi qua 3 engine của dự án

| Chức năng PPOCRLabel | Engine |
|---|---|
| **Auto Recognition** (cả trang) | Mặc định theo thư mục đang mở: `ch` → Kim Hán Nôm; `vi`, `intro`, `ref`, `toc` → PaddleOCR-VL; trang `ch_vi` (theo `cache/phan_loai.json`) → row_projection. Đi qua `ocr_jobs.ocr_page` nên **dùng chung cache** `cache/ocr/` với bước OCR của app: trang đã OCR thì trả ngay, không gọi lại API/mô hình. |
| **Re-recognition** (`Ctrl+R`, `Ctrl+Shift+R`) một/nhiều box | Engine chữ của thư mục (`ch` → Kim Hán Nôm, còn lại → PaddleOCR-VL). Kim Hán Nôm nhận ảnh cắt qua API (lịch sử upload ghi vào file tạm riêng, không vào `data/cache/uploads.json`). |
| **Choose OCR model** (`Ctrl+M`) | Tự động / Kim Hán Nôm / PaddleOCR-VL / row_projection. Có ô tích **Recognize the current image now** (`recognizeCurrentImage()`): nhận dạng ngay ảnh đang mở và thay box hiện có, không bỏ qua ảnh đã nằm trong fileState.txt. row_projection chỉ tách hàng nên khi nhận dạng một box nó dùng engine chữ mặc định của thư mục. |
| Nhận dạng bảng | Tắt (quy định đồ án bỏ qua bảng, tranh). |

PaddleOCR-VL được nạp **lúc bạn bấm nhận dạng lần đầu** (không nạp lúc khởi động); lỗi engine (thiếu `TOKEN`, chưa cài PaddleOCR...) hiện hộp thoại, không làm sập PPOCRLabel.

Kiểm tra: `.venv\Scripts\python.exe tests/test_ppocrlabel_boot.py`.

## Nâng cấp PPOCRLabel gốc

Muốn lấy bản mới của PPOCRLabel: chép đè thư mục từ `site-packages` mới rồi áp lại các chỗ `[GK]` ở bảng trên
(so sánh bằng `git diff`), chạy lại test.
