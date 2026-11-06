"""Ba nguồn OCR cho bước OCR của Web UI. Mỗi engine nhận một ảnh trang và trả về danh sách box
{"text": ..., "points": [[x,y] x4]} (chưa phân về ch/ vi/ và chưa sắp thứ tự, xem order_boxes).

  kimhannom       API Kim Hán Nôm (cần TOKEN trong .env). Tốt cho chữ Hán Nôm, theo cột dọc.
  paddleocr       PaddleOCR-VL chạy trên máy (cần requirements-ocr.txt). Nạp một lần, dùng lại cho mọi ảnh.
  row_projection  Tách hàng chữ ngang bằng xử lý ảnh (scripts/row_projection_viz.py), KHÔNG nhận dạng chữ:
                  transcription để trống để điền hoặc nhận dạng lại trong PPOCRLabel.

RECOMMENDED: engine đề xuất theo nhãn trang. Dùng cho giá trị mặc định trên giao diện, người dùng đổi được.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent

KIMHANNOM, PADDLEOCR, ROW_PROJECTION = "kimhannom", "paddleocr", "row_projection"
SKIP = "skip"

ENGINES = {
    KIMHANNOM: {"label": "Kim Hán Nôm (API)",
                "desc": "Gọi API kimhannom.fit.hcmus.edu.vn, cần TOKEN trong .env. Phù hợp chữ Hán Nôm."},
    PADDLEOCR: {"label": "PaddleOCR-VL",
                "desc": "Mô hình chạy trên máy, nạp một lần khi vào bước OCR. Phù hợp chữ Quốc ngữ."},
    ROW_PROJECTION: {"label": "Tách hàng (row_projection)",
                     "desc": "Tách từng hàng chữ ngang bằng xử lý ảnh, không nhận dạng chữ. Dành cho bản chụp đẹp, hàng thẳng."},
}

# Nhãn nào được OCR, và engine đề xuất. blank, misc, unknown không OCR.
RECOMMENDED = {
    "ch": KIMHANNOM,
    "vi": PADDLEOCR,
    "ch_vi": ROW_PROJECTION,   # bản chụp đẹp (T2b): hàng Hán xen hàng Quốc ngữ
    "intro": PADDLEOCR,
    "ref": PADDLEOCR,
    "toc": PADDLEOCR,
}
OCR_TAGS = tuple(RECOMMENDED)

# Nhãn khối của PaddleOCR-VL không phải nội dung chính (quy định: bỏ số trang, đầu/chân trang, chú thích, bảng, tranh)
PADDLE_SKIP_LABELS = {"header", "header_image", "footer", "footer_image", "number", "footnote", "vision_footnote",
                      "image", "chart", "table", "seal", "formula", "formula_number", "aside_text", "figure_title"}


# Tham số đưa vào engine; cũng là một phần khóa của cache (đổi tham số thì kết quả cũ không còn khớp)
KIMHANNOM_PARAMS = {"ocr_id": 2, "lang_type": 2, "epitaph": 0}   # 2 = hành chính, 2 = Nôm, 0 = văn bản thường
PADDLE_PARAMS = {"pipeline_version": "v1", "engine": "transformers"}


class EngineError(Exception):
    """Lỗi của một engine (thiếu thư viện, thiếu token, ảnh hỏng, ...), có thông báo cho người dùng."""


# ---------------------------------------------------------------- phân loại chữ Hán/Nôm và Quốc ngữ
def is_han_char(ch: str) -> bool:
    c = ord(ch)
    return (0x2E80 <= c <= 0x2FDF or 0x3400 <= c <= 0x4DBF or 0x4E00 <= c <= 0x9FFF or 0xF900 <= c <= 0xFAFF
            or 0x20000 <= c <= 0x323AF
            or 0xE000 <= c <= 0xF8FF or 0xF0000 <= c <= 0x10FFFF)   # vùng private use: chữ Nôm chưa có mã Unicode


def is_han_text(text: str) -> bool:
    """Chuỗi là chữ Hán Nôm khi chữ Hán chiếm đa số trong các ký tự chữ/số (bỏ khoảng trắng, dấu câu)."""
    chars = [c for c in text if not c.isspace() and (c.isalnum() or is_han_char(c))]
    return bool(chars) and sum(is_han_char(c) for c in chars) * 2 > len(chars)


# ---------------------------------------------------------------- sắp xếp theo quy định bounding box
def _center(box: dict) -> tuple[float, float]:
    xs, ys = [p[0] for p in box["points"]], [p[1] for p in box["points"]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _size(box: dict) -> tuple[float, float]:
    xs, ys = [p[0] for p in box["points"]], [p[1] for p in box["points"]]
    return max(xs) - min(xs), max(ys) - min(ys)


def order_boxes(boxes: list[dict], vertical_rtl: bool) -> list[dict]:
    """Sắp thứ tự đọc theo "Quy định về việc đánh bounding box".

    vertical_rtl=True (Nôm viết dọc): cột từ phải sang trái, trong mỗi cột từ trên xuống.
    vertical_rtl=False (Quốc ngữ, Nôm ngang): hàng từ trên xuống, trong mỗi hàng từ trái sang phải.
    Các box được gom vào cùng cột/hàng khi tâm cách nhau không quá 0.6 lần độ rộng/cao trung vị.
    """
    if len(boxes) < 2:
        return list(boxes)
    if vertical_rtl:
        tol = 0.6 * median(max(1, _size(b)[0]) for b in boxes)
        major, minor, sign = (lambda b: _center(b)[0]), (lambda b: _center(b)[1]), -1
    else:
        tol = 0.6 * median(max(1, _size(b)[1]) for b in boxes)
        major, minor, sign = (lambda b: _center(b)[1]), (lambda b: _center(b)[0]), 1
    groups: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: sign * major(b)):
        if groups and abs(major(b) - median(major(g) for g in groups[-1])) <= tol:
            groups[-1].append(b)
        else:
            groups.append([b])
    return [b for g in groups for b in sorted(g, key=minor)]


def clamp_points(points, width: int, height: int) -> list[list[int]]:
    return [[min(max(int(round(x)), 0), width), min(max(int(round(y)), 0), height)] for x, y in points]


# ---------------------------------------------------------------- Kim Hán Nôm
def run_kimhannom(image: Path, **params) -> list[dict]:
    api_dir = str(HERE / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    try:
        import sinonom_service
    except ImportError as e:
        raise EngineError(f"Thiếu thư viện để gọi API Kim Hán Nôm ({e}). Chạy: pip install -r requirements.txt") from e
    if not os.getenv("TOKEN") and not _env_has_token():
        raise EngineError("Chưa có TOKEN trong file .env (xem README, mục Lấy token).")
    try:
        data = sinonom_service.ocr(image, **params)
    except sinonom_service.ApiError as e:
        raise EngineError(f"API Kim Hán Nôm lỗi: {e}") from e
    boxes = []
    for item in (data or {}).get("result_bbox") or []:
        points, (text, _score) = item[0], item[1]
        boxes.append({"text": str(text), "points": [[int(round(x)), int(round(y))] for x, y in points]})
    return boxes


def _env_has_token() -> bool:
    env = HERE.parent / ".env"
    try:
        return any(line.strip().startswith("TOKEN=") and line.split("=", 1)[1].strip() for line in
                   env.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def kimhannom_available() -> str | None:
    """None nếu dùng được, ngược lại là lý do."""
    if importlib.util.find_spec("requests") is None or importlib.util.find_spec("dotenv") is None:
        return "Thiếu thư viện requests/python-dotenv. Chạy: pip install -r requirements.txt"
    if not os.getenv("TOKEN") and not _env_has_token():
        return "Chưa có TOKEN trong file .env"
    return None


# ---------------------------------------------------------------- PaddleOCR-VL (nạp một lần)
class PaddleHolder:
    """Giữ một pipeline PaddleOCR-VL dùng chung. Nạp ở luồng nền, suy luận tuần tự (pipeline không an toàn đa luồng)."""

    def __init__(self):
        self._lock = threading.Lock()          # bảo vệ trạng thái
        self._infer_lock = threading.Lock()    # một lần suy luận tại một thời điểm
        self._pipeline = None
        self._state = "idle"                   # idle | loading | ready | error
        self._error: str | None = None
        self._seconds: float | None = None
        self._device: str | None = None

    @staticmethod
    def unavailable_reason() -> str | None:
        if importlib.util.find_spec("paddleocr") is None:
            return 'Chưa cài PaddleOCR. Chạy: pip install -r requirements-ocr.txt (hoặc pip install "paddleocr[doc-parser]")'
        return None

    def status(self) -> dict:
        reason = self.unavailable_reason()
        with self._lock:
            if reason and self._state != "ready":
                return {"state": "unavailable", "error": reason}
            return {"state": self._state, "error": self._error, "seconds": self._seconds, "device": self._device}

    def preload(self) -> dict:
        """Bắt đầu nạp mô hình ở luồng nền nếu chưa nạp. Gọi nhiều lần cũng an toàn."""
        if self.unavailable_reason():
            return self.status()
        with self._lock:
            if self._state in ("loading", "ready"):
                return {"state": self._state}
            self._state, self._error = "loading", None
        threading.Thread(target=self._load, daemon=True).start()
        return {"state": "loading"}

    def _load(self) -> None:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        started = time.perf_counter()
        try:
            device = "cpu"
            try:
                import torch
                device = "gpu:0" if torch.cuda.is_available() else "cpu"
            except ImportError:
                pass
            from paddleocr import PaddleOCRVL
            # Giống scripts/paddleocr_vl_viz.py: chạy bằng PyTorch (engine=transformers), không cần paddlepaddle
            pipeline = PaddleOCRVL(pipeline_version="v1", device=device, engine="transformers")
            with self._lock:
                self._pipeline, self._state = pipeline, "ready"
                self._seconds, self._device = round(time.perf_counter() - started, 1), device
        except Exception as e:  # nạp lỗi (thiếu thư viện, hết bộ nhớ, tải model lỗi, ...) báo lên UI
            with self._lock:
                self._state, self._error = "error", f"{type(e).__name__}: {e}"

    def recognize(self, image: Path) -> list[dict]:
        """Mọi khối của một ảnh theo thứ tự đọc của mô hình: {"text", "points", "label"} (chưa lọc theo nhãn khối)."""
        reason = self.unavailable_reason()
        if reason:
            raise EngineError(reason)
        self.preload()
        while True:  # chờ nạp xong (nếu job bắt đầu ngay khi đang nạp)
            with self._lock:
                state, error = self._state, self._error
            if state == "ready":
                break
            if state == "error":
                raise EngineError(f"Không nạp được PaddleOCR-VL: {error}")
            time.sleep(0.5)
        with self._infer_lock:
            results = list(self._pipeline.predict(str(image)))
        if not results:
            return []
        data = results[0].json
        data = data.get("res", data)
        blocks = sorted(data.get("parsing_res_list", []),
                        key=lambda b: (b.get("block_order") is None, b.get("block_order") or 0))
        out = []
        for b in blocks:
            label = str(b.get("block_label", ""))
            text = (b.get("block_content") or "").strip()
            poly = b.get("block_polygon_points")
            if poly and len(poly) == 4:
                points = [[int(round(x)), int(round(y))] for x, y in poly]
            else:
                x1, y1, x2, y2 = (int(round(float(v))) for v in b["block_bbox"])
                points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            out.append({"text": text, "points": points, "label": label})
        return out


PADDLE = PaddleHolder()


# ---------------------------------------------------------------- row_projection
def row_projection_params() -> dict:
    """Tham số mặc định của scripts/row_projection_viz.py (khóa cache của row_projection)."""
    from . import row_projection_viz as rp
    return {k: v for k, v in vars(rp.build_parser().parse_args(["x"])).items() if k not in ("image", "out_dir")}


def run_row_projection(image: Path, debug_dir: Path) -> list[dict]:
    try:
        from . import row_projection_viz as rp
    except ImportError as e:
        raise EngineError(f"Thiếu thư viện cho row_projection ({e}). Chạy: pip install -r requirements.txt") from e
    import json

    args = rp.build_parser().parse_args([str(image)])
    stem = image.stem.rsplit(".", 1)[-1] if image.stem.rsplit(".", 1)[-1].isdigit() else image.stem
    out_dir = debug_dir / stem   # ảnh kiểm tra (box, mặt nạ mực, profile) để chỉnh ngưỡng khi cần
    rp.process(args, image, out_dir, stem=stem, verbose=False)
    res = json.loads((out_dir / f"{stem}_res.json").read_text(encoding="utf-8"))
    boxes = []
    for row in res["rows"]:
        x1, y1, x2, y2 = row["bbox"]
        boxes.append({"text": "", "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]})
    return boxes


def rows_to_scripts(boxes: list[dict], first: str = "ch") -> dict[str, list[dict]]:
    """Chia các hàng của trang chép tay (hàng Hán xen hàng Quốc ngữ) về ch và vi: hàng lẻ là `first`, hàng chẵn là phần còn lại."""
    other = "vi" if first == "ch" else "ch"
    return {first: boxes[0::2], other: boxes[1::2]}
