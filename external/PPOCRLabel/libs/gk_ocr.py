# [GK] Thay mô hình PP-OCR của PPOCRLabel bằng các chế độ OCR của dự án (Kim Hán Nôm, PaddleOCR-VL, tách hàng row_projection + Kim Hán Nôm/PaddleOCR-VL/Vintern).
#
# PPOCRLabel gốc nạp 11 mô hình (PaddleOCR, TextRecognition, TextDetection, PPStructureV3) ngay khi khởi động, mất 1-2 phút. Bản này không nạp
# gì cả: các lớp dưới đây giữ đúng giao diện `predict(...)` mà PPOCRLabel gọi, nhưng chuyển việc cho scripts/engine_bridge.py của dự án.
# Mô hình/API chỉ được gọi khi bạn bấm nhận dạng, PaddleOCR-VL nạp lúc đó nếu cần.
#
#   self.ocr               EngineOCR                 Auto Recognition: predict(ảnh BGR, image_path=...) -> rec_polys, rec_texts, rec_scores
#   self.text_recognizer   EngineTextRecognition     Re-recognition (một box): predict(vùng cắt) -> rec_text, rec_score
#   self.text_detector     EngineTextDetection       Cell re-recognition: coi cả vùng cắt là một dòng chữ
#   (nhận dạng bảng không còn: quy định đồ án bỏ qua bảng, tranh)
import logging
import sys
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication, QMessageBox

logger = logging.getLogger("PPOCRLabel")
PROJECT_ROOT = Path(__file__).resolve().parents[3]   # external/PPOCRLabel/libs/gk_ocr.py -> thư mục gốc dự án


def bridge():
    """scripts/engine_bridge.py của dự án (nạp lười: chỉ khi cần nhận dạng)."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts import engine_bridge

    return engine_bridge


def events():
    """scripts/ocr_events.py: log, tiến độ và lệnh Dừng của các engine (lấy trực tiếp, không qua cầu nối)."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts import ocr_events

    return ocr_events


class EngineState:
    """Engine đang chọn và thư mục ảnh đang mở, dùng chung cho các lớp bên dưới."""

    def __init__(self, folder_getter):
        self.engine = "auto"            # auto | khoá "box+chữ" (vd. row_projection+vintern, kimhannom+none, existing+paddleocr), xem engine_bridge
        self.folder_getter = folder_getter
        self.quiet = False              # đang chạy hàng loạt (khung OCR log): lỗi chỉ ghi vào log, báo một lần khi xong thay vì hộp thoại từng box
        self.alternate = False          # trang ch_vi tách hàng bằng row_projection: True = lọc xen kẽ lẻ/chẵn về ch/vi, False = thư mục đang mở nhận mọi hàng (lưu trong cài đặt PPOCRLabel)
        self.task = None                # both | detect | read | selected (nhớ để mở lại hộp thoại đúng lựa chọn); None = chưa chọn
        self.last_error = None

    def folder(self):
        try:
            return self.folder_getter() or "vi"
        except Exception:
            return "vi"

    def fail(self, error):
        """Báo lỗi engine: hiện hộp thoại nếu đang ở luồng giao diện; luồng nền thì để flush_error() báo sau."""
        message = str(error) or type(error).__name__
        logger.error("OCR engine: %s", message)
        self.last_error = message
        events().log(message, "error")
        if self.quiet:
            return
        app = QApplication.instance()
        if app is not None and QThread.currentThread() == app.thread():
            QMessageBox.warning(None, "OCR", message)
            self.last_error = None

    def flush_error(self, parent=None):
        if self.last_error:
            QMessageBox.warning(parent, "OCR", self.last_error)
            self.last_error = None


class EngineOCR:
    """Thay PaddleOCR(...) cho self.ocr: nhận dạng cả trang bằng engine của dự án (có dùng cache cache/ocr/)."""

    def __init__(self, state):
        self.state = state

    def predict(self, img, image_path=None):
        boxes = []
        try:
            if not image_path:
                raise RuntimeError("Thiếu đường dẫn ảnh để nhận dạng cả trang.")
            boxes, engine, cached = bridge().recognize_page(image_path, self.state.engine, alternate=self.state.alternate)
            logger.info("OCR trang %s bằng %s%s: %d box", Path(image_path).name, engine, " (cache)" if cached else "", len(boxes))
        except events().Cancelled:   # người dùng bấm Dừng ở khung OCR log
            events().log("Đã dừng.", "warn")
        except Exception as e:  # một ảnh lỗi không được làm sập vòng nhận dạng hàng loạt
            self.state.fail(e)
        return [{
            "rec_polys": [np.array(b["points"], dtype=np.float32) for b in boxes],
            "rec_texts": [b["transcription"] for b in boxes],
            "rec_scores": [1.0 for _ in boxes],
        }]


class EngineTextRecognition:
    """Thay TextRecognition(...) cho self.text_recognizer: nhận dạng một vùng đã cắt."""

    def __init__(self, state):
        self.state = state

    def predict(self, crop):
        text = ""
        try:
            text = bridge().recognize_crop(crop, self.state.engine, self.state.folder())
        except Exception as e:
            self.state.fail(e)
        return [{"rec_text": text, "rec_score": 1.0 if text else 0.0}]


class EngineTextDetection:
    """Thay TextDetection(...) cho self.text_detector: coi cả vùng cắt (một ô/một box) là một dòng chữ."""

    def predict(self, crop):
        height, width = crop.shape[:2]
        return [{"dt_polys": np.array([[[0, 0], [width, 0], [width, height], [0, height]]], dtype=np.float32)}]
