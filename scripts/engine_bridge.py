"""Cầu nối giữa PPOCRLabel (external/PPOCRLabel) và 3 engine OCR của dự án: Kim Hán Nôm, PaddleOCR-VL, row_projection.

PPOCRLabel gốc nạp 11 mô hình PP-OCR của riêng nó khi khởi động. Bản trong dự án bỏ hết, và mọi chức năng nhận dạng của PPOCRLabel
(Auto Recognition, Re-recognition, Cell Re-Recognition) gọi các hàm dưới đây, tức là dùng đúng các engine của bước OCR trong app:

  recognize_page(ảnh)      nhận dạng cả trang, giống hệt bước OCR của app (cùng cache cache/ocr/, cùng lọc/sắp xếp/làm sạch)
  recognize_crop(mảng ảnh) nhận dạng một vùng đã cắt (một box) và trả về chuỗi chữ

Engine mặc định theo thư mục đang mở (giống đề xuất của app): ch -> Kim Hán Nôm; vi, intro, ref, toc -> PaddleOCR-VL;
trang ch_vi theo nhãn trong cache/phan_loai.json -> row_projection. Người dùng đổi engine trong PPOCRLabel (Edit/Auto -> chọn mô hình).
row_projection chỉ tách hàng, không nhận dạng chữ, nên khi nhận dạng một vùng cắt nó dùng engine chữ mặc định của thư mục.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from statistics import median

from . import library, ocr_engines as eng, ocr_jobs

AUTO = "auto"
ENGINE_CHOICES = [
    (AUTO, "Tự động theo thư mục (ch: Kim Hán Nôm · vi/intro/ref/toc: PaddleOCR-VL · ch_vi: row_projection)"),
    (eng.KIMHANNOM, "Kim Hán Nôm (API, cần TOKEN trong .env)"),
    (eng.PADDLEOCR, "PaddleOCR-VL (chạy trên máy, nạp lần đầu mất một lúc)"),
    (eng.ROW_PROJECTION, "row_projection (chỉ tách hàng, không nhận dạng chữ)"),
]
# Engine nhận dạng chữ cho một vùng cắt khi người dùng chọn tự động hoặc row_projection (không nhận dạng được chữ)
TEXT_ENGINE_BY_FOLDER = {"ch": eng.KIMHANNOM}
DEFAULT_TEXT_ENGINE = eng.PADDLEOCR


def locate(image_path) -> tuple[library.Book, int, str, Path]:
    """Ảnh `<…>/<Tên sách>/<thư mục>/<Tên sách>.<NNN>.jpg` -> (sách, số trang, tên thư mục, đường dẫn tuyệt đối)."""
    path = Path(image_path).resolve()
    book_dir = path.parent.parent
    tail = path.stem.rsplit(".", 1)[-1]
    number = int(tail) if tail.isdigit() else 0
    return library.Book(book_dir, book_dir / f"{book_dir.name}.pdf"), number, path.parent.name, path


def page_tag(book: library.Book, number: int, folder: str) -> str:
    """Nhãn của trang theo cache/phan_loai.json; không có thì suy từ tên thư mục đang mở (khác ch/vi/intro/ref/toc -> vi)."""
    tag = ocr_jobs.page_tags(book).get(number)
    if tag in eng.OCR_TAGS:
        return tag
    return folder if folder in eng.OCR_TAGS else "vi"


def ensure_ready(engine: str) -> None:
    """Chuẩn bị engine. PaddleOCR-VL nạp NGAY lúc cần (người dùng vừa bấm nhận dạng); các lần sau dùng lại mô hình đã nạp."""
    if engine == eng.PADDLEOCR:
        reason = eng.PADDLE.unavailable_reason()
        if reason:
            raise eng.EngineError(reason)
        eng.PADDLE.preload()          # recognize() sẽ chờ nạp xong
    elif engine == eng.KIMHANNOM:
        reason = eng.kimhannom_available()
        if reason:
            raise eng.EngineError(reason)


def recognize_page(image_path, engine: str | None = None, use_cache: bool = True) -> tuple[list[dict], str, bool]:
    """Nhận dạng cả trang. Trả về (các box {"transcription", "points"} của thư mục đang mở, engine đã dùng, lấy từ cache?).

    Đi qua ocr_jobs.ocr_page nên giống hệt bước OCR của app: ảnh đã OCR bằng engine đó thì lấy từ cache, không gọi lại API/mô hình."""
    book, number, folder, path = locate(image_path)
    tag = page_tag(book, number, folder)
    engine = eng.RECOMMENDED[tag] if engine in (None, AUTO) else engine
    ensure_ready(engine)
    result, cached = ocr_jobs.ocr_page(book, number, tag, engine, path, use_cache)
    boxes = result.get(folder)
    if boxes is None:                      # thư mục mở không trùng nhãn nào: lấy phần đầu tiên
        boxes = next(iter(result.values()), [])
    return boxes, engine, cached


def text_engine(engine: str | None, folder: str) -> str:
    if engine in (None, AUTO, eng.ROW_PROJECTION):
        return TEXT_ENGINE_BY_FOLDER.get(folder, DEFAULT_TEXT_ENGINE)
    return engine


def _temp_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "gk_ppocrlabel"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recognize_crop(crop, engine: str | None = None, folder: str = "vi") -> str:
    """Nhận dạng một vùng đã cắt (mảng ảnh BGR của một box) và trả về chuỗi chữ ('' nếu không nhận ra)."""
    import cv2

    engine = text_engine(engine, folder)
    ensure_ready(engine)
    if engine == eng.KIMHANNOM:
        tmp = _temp_dir() / f"crop_{uuid.uuid4().hex[:10]}.jpg"
        ok, encoded = cv2.imencode(".jpg", crop)
        if not ok:
            return ""
        encoded.tofile(str(tmp))
        try:
            # lịch sử upload của các ảnh cắt tạm ghi vào file riêng, không làm đầy data/cache/uploads.json
            boxes = eng.run_kimhannom(tmp, uploads_file=_temp_dir() / "crop_uploads.json", **eng.KIMHANNOM_PARAMS)
        finally:
            tmp.unlink(missing_ok=True)
        boxes = [b for b in boxes if b["text"].strip()]
        if not boxes:
            return ""
        tall = median(max(p[1] for p in b["points"]) - min(p[1] for p in b["points"]) for b in boxes) >= \
            median(max(p[0] for p in b["points"]) - min(p[0] for p in b["points"]) for b in boxes)
        return "".join(b["text"].strip() for b in eng.order_boxes(boxes, vertical_rtl=tall))   # chữ Hán Nôm viết liền
    blocks = [b for b in eng.PADDLE.recognize(crop) if b.get("label") not in eng.PADDLE_SKIP_LABELS and b["text"].strip()]
    return " ".join(" ".join(b["text"].split()) for b in blocks)
