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
| `libs/gk_fonts.py`, `PPOCRLabel.py` (`get_main_app`, `MainWindow.__init__`) | Nạp mọi font trong `font/` (gốc dự án) bằng `QFontDatabase.addApplicationFont`, không cần cài vào Windows. `get_main_app` gọi `install_fallbacks` để font giao diện mượn các font này khi thiếu glyph (danh sách nhãn, ô sửa text); nhãn vẽ trên ảnh dùng họ font đầu tiên (`HAN NOM A`) nếu không có `--label_font_path`. Thiếu thư mục `font/` thì bỏ qua, PPOCRLabel chạy như cũ. |
| `PPOCRLabel.py` (`saveLabels`, `gkDetectOnly`) | Bản gốc bỏ mọi box có chữ rỗng khi lưu kết quả Auto, nên "chỉ phát hiện box" (chữ rỗng có chủ ý, engine `…+none`) báo đủ vị trí nhưng không hiện box nào. Giữ box rỗng khi việc đang chọn là `detect` hoặc engine đọc chữ là `none`; các trường hợp khác giữ như cũ. |
| `PPOCRLabel.py` (`autolcm`, `EngineState.alternate`), `libs/gk_ocr.py` | Ô **"Lọc xen kẽ hàng ch/vi"** trong hộp thoại OCR (hiện khi thư mục là ch/vi, việc có phát hiện box, box = row_projection). Trang ch_vi tách hàng: tích = ch nhận hàng lẻ, vi nhận hàng chẵn; bỏ tích = thư mục đang mở nhận mọi hàng. Lựa chọn lưu trong cài đặt (`gk_alternate`, mặc định không lọc) và truyền cho `engine_bridge.recognize_page(alternate=...)`; web có ô tương ứng (`/api/ocr/folder`, trường `alternate`, mặc định lọc). |
| `PPOCRLabel.py` (`reverseBoxOrder`, `moveBoxUp`, `moveBoxDown`, `gkReorderShapes`) | Ba nút/menu Edit đổi thứ tự box (thứ tự danh sách = thứ tự dòng trong Label.txt): **Đảo thứ tự box** (Ctrl+Shift+B), **Đưa box lên** (Alt+Up), **Đưa box xuống** (Alt+Down). Bổ sung cho kéo-thả và Resort position có sẵn. |
| `tablepyxl/tablepyxl.py` | Thay `paddle.utils.try_import` bằng `importlib` (không phụ thuộc paddlepaddle). |

Cách kéo cạnh hoạt động: chỉ số `hVertex` 0..3 là đỉnh như bản gốc, 4..7 là cạnh. Kéo cạnh dịch hai đỉnh của cạnh cùng một đoạn
theo pháp tuyến, nên hình chữ nhật vẫn là hình chữ nhật, box hơi nghiêng giữ nguyên độ nghiêng; không ra ngoài ảnh và không áp sát
cạnh đối diện hơn `MIN_SIDE` (2 pixel ảnh).

## Nhận dạng đi qua 3 engine của dự án

| Chức năng PPOCRLabel | Engine |
|---|---|
| **Auto Recognition** (cả trang) | Mặc định theo thư mục đang mở: `ch` → Kim Hán Nôm; `vi`, `intro`, `ref`, `toc` → PaddleOCR-VL; trang `ch_vi` (theo `cache/phan_loai.json`) → row_projection. Đi qua `ocr_jobs.ocr_page` nên **dùng chung cache** `cache/ocr/` với bước OCR của app: trang đã OCR thì trả ngay, không gọi lại API/mô hình. |
| **Re-recognition** (`Ctrl+R`, `Ctrl+Shift+R`) một/nhiều box | Engine chữ của chế độ đang chọn; mặc định theo thư mục (`ch` → Kim Hán Nôm, còn lại → Vintern-1B). Kim Hán Nôm nhận ảnh cắt qua API (lịch sử upload ghi vào file tạm riêng, không vào `data/cache/uploads.json`). |
| **Choose OCR model** (`Ctrl+M`) | Hộp thoại theo **thư mục đang mở** (`autolcm()`, dữ liệu từ `engine_bridge.engine_options`), giống popup "OCR thư mục này" của app web, nhãn tiếng Việt, không còn lựa chọn "tự động": **Việc cần làm** (both | detect | read = tất cả box | selected = các box đã chọn, mỗi việc có mô tả), **Phát hiện box bằng** (`ch`: Kim Hán Nôm, row_projection; `vi`/`intro`/`ref`/`toc`: PaddleOCR-VL, row_projection) và **Đọc chữ bằng** (`ch`: Kim Hán Nôm; còn lại PaddleOCR-VL, Vintern-1B; bị khoá theo box khi box là Kim Hán Nôm/PaddleOCR-VL vì chúng đọc luôn chữ). Báo mô hình chưa dùng được (`engine_bridge.unavailable_reasons`) và khoá nút khi định chạy ngay. `modelChoose()` lưu khoá "box+chữ" (`row_projection+vintern`, `paddleocr+none`, `existing+paddleocr`, ...) và việc đã chọn (`engine_state.task`) rồi, nếu tích **Chạy ngay trên ảnh đang mở**, gọi `recognizeCurrentImage()` (both, detect: thay box), `reRecognition()` (read: giữ box, tất cả box) hoặc `singleRerecognition()` (selected: chỉ các box đang chọn). Vintern chạy ở tiến trình Python riêng (VINTERN_PYTHON). |
| **OCR log** (View -> OCR log, `Ctrl+Alt+L`) | Dock dưới cùng `OcrLogDock` (`libs/gk_ocr_log.py`, tạo trong `MainWindow.__init__`): việc đang chạy + thời gian, thanh tiến độ, trạng thái Kim Hán Nôm / PaddleOCR-VL / Vintern-1B kèm nút **Nạp**, log có màu theo mức, nút **Dừng** / **Xoá log** / **Sao chép log**. Nhận sự kiện từ `scripts/ocr_events.py` qua tín hiệu Qt (an toàn với luồng nền của `AutoDialog`). `ocrRun()` bọc `autoRecognition`, `recognizeCurrentImage`, `reRecognition`, `singleRerecognition`, `cellreRecognition` (thân hàm cũ đổi tên `_autoRecognition`, ...): mở khung, khoá phần còn lại của giao diện khi chạy trong luồng giao diện (`set_ui_busy`), gom lỗi engine thành MỘT hộp thoại khi xong (`EngineState.quiet`), bắt `Cancelled` khi bấm Dừng. `gkStep()` đầu mỗi box báo tiến độ và kiểm tra Dừng. |
| Nhận dạng bảng | Tắt (quy định đồ án bỏ qua bảng, tranh). |

PaddleOCR-VL được nạp **lúc bạn bấm nhận dạng lần đầu** (không nạp lúc khởi động); lỗi engine (thiếu `TOKEN`, chưa cài PaddleOCR...) hiện hộp thoại, không làm sập PPOCRLabel.

Kiểm tra: `.venv\Scripts\python.exe tests/test_ppocrlabel_boot.py`.

## Nâng cấp PPOCRLabel gốc

Muốn lấy bản mới của PPOCRLabel: chép đè thư mục từ `site-packages` mới rồi áp lại các chỗ `[GK]` ở bảng trên
(so sánh bằng `git diff`), chạy lại test.
