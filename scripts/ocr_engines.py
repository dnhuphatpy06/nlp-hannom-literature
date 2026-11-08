"""Ba nguồn OCR cho bước OCR của Web UI. Mỗi engine nhận một ảnh trang và trả về danh sách box
{"text": ..., "points": [[x,y] x4]} (chưa phân về ch/ vi/ và chưa sắp thứ tự, xem order_boxes).

  kimhannom       API Kim Hán Nôm (cần TOKEN trong .env). Tốt cho chữ Hán Nôm, theo cột dọc.
  paddleocr       PaddleOCR-VL chạy trên máy (cần requirements-ocr.txt). Nạp một lần, dùng lại cho mọi ảnh.
  row_projection  Tách hàng chữ ngang bằng xử lý ảnh (scripts/row_projection_viz.py), tự nó KHÔNG nhận dạng chữ. Chữ trong từng box
                  được đọc riêng (read_row_text): Kim Hán Nôm cho box của ch, PaddleOCR-VL cho box của vi, chỉ lấy chữ, không dùng
                  box của hai engine đó. Tắt đọc chữ thì transcription để trống để điền hoặc nhận dạng lại trong PPOCRLabel.

RECOMMENDED: engine đề xuất theo nhãn trang. Dùng cho giá trị mặc định trên giao diện, người dùng đổi được.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from statistics import median

from . import ocr_events as events

HERE = Path(__file__).resolve().parent

KIMHANNOM, PADDLEOCR, ROW_PROJECTION, VINTERN = "kimhannom", "paddleocr", "row_projection", "vintern"
ROW_PADDLEOCR = "row_paddleocr"   # tách hàng bằng row_projection, chữ Quốc ngữ đọc bằng PaddleOCR-VL
ROW_TEXT = "row_text"   # tên thư mục cache chữ đã đọc trong các box của row_projection (không phải engine để chọn)
SKIP = "skip"

ENGINES = {
    KIMHANNOM: {"label": "Kim Hán Nôm (API): box + chữ",
                "desc": "Gọi API kimhannom.fit.hcmus.edu.vn nhận cả box lẫn chữ, cần TOKEN trong .env. Phù hợp chữ Hán Nôm."},
    PADDLEOCR: {"label": "PaddleOCR-VL: box + chữ",
                "desc": "Mô hình chạy trên máy, nạp một lần khi vào bước OCR; tìm khối chữ và đọc chữ. Phù hợp chữ Quốc ngữ."},
    ROW_PROJECTION: {"label": "Tách hàng (row_projection) + Kim Hán Nôm",
                     "desc": "Tách từng hàng chữ ngang bằng xử lý ảnh, rồi đọc chữ trong từng box (hàng Nôm: Kim Hán Nôm; hàng Quốc ngữ: Vintern-1B; "
                             "tắt được). Dành cho bản chụp đẹp, hàng thẳng."},
    ROW_PADDLEOCR: {"label": "Tách hàng (row_projection) + PaddleOCR-VL",
                    "desc": "Tách từng hàng chữ ngang bằng xử lý ảnh, rồi đọc chữ Quốc ngữ trong từng box bằng PaddleOCR-VL (hàng Nôm vẫn bằng "
                            "Kim Hán Nôm). Dành cho bản chụp đẹp, hàng thẳng."},
    VINTERN: {"label": "Tách hàng (row_projection) + Vintern-1B",
              "desc": "Tách từng hàng chữ ngang bằng xử lý ảnh (như row_projection) rồi đọc chữ từng hàng bằng Vintern-1B-v3.5, chạy trong "
                      "môi trường Python riêng (VINTERN_PYTHON). Dành cho trang Quốc ngữ, hàng thẳng; không tìm khối/cột nên không hợp trang nhiều cột."},
}
# Nguồn OCR chọn được theo nhãn trang (thứ tự là thứ tự hiện trên giao diện). ch: Kim Hán Nôm nhận box + chữ, hoặc tách hàng rồi Kim Hán Nôm đọc chữ;
# vi/intro/ref/toc: PaddleOCR-VL nhận box + chữ, hoặc tách hàng rồi PaddleOCR-VL / Vintern đọc chữ; ch_vi: đủ các nguồn chia được ch và vi.
VINTERN_TAGS = ("vi", "intro", "ref", "toc")
ENGINES_BY_TAG = {"ch": [KIMHANNOM, ROW_PROJECTION],
                  **{t: [PADDLEOCR, ROW_PADDLEOCR, VINTERN, ROW_PROJECTION] for t in VINTERN_TAGS},   # ROW_PROJECTION: chỉ tách hàng (không đọc chữ)
                  "ch_vi": [ROW_PROJECTION, ROW_PADDLEOCR, VINTERN, KIMHANNOM, PADDLEOCR]}
ROW_ENGINES = (ROW_PROJECTION, ROW_PADDLEOCR, VINTERN)   # nguồn tách hàng bằng row_projection rồi đọc chữ từng box


def engines_for_tag(tag: str) -> list[str]:
    """Các nguồn OCR được chọn cho nhãn trang này."""
    return list(ENGINES_BY_TAG.get(tag, ENGINES))


def engine_info(engine: str, tag: str) -> dict:
    """{label, desc} của nguồn OCR khi dùng cho nhãn `tag` (cùng một nguồn đọc chữ khác nhau tuỳ nhãn: ch chỉ Kim Hán Nôm, ch_vi cả hai)."""
    info = dict(ENGINES[engine])
    if engine == ROW_PROJECTION and tag == "ch_vi":
        info["label"] = "Tách hàng (row_projection): ch → Kim Hán Nôm, vi → Vintern-1B"
    elif engine == ROW_PADDLEOCR and tag == "ch_vi":
        info["label"] = "Tách hàng (row_projection): ch → Kim Hán Nôm, vi → PaddleOCR-VL"
    elif engine == ROW_PROJECTION and tag == "ch":
        info["desc"] = "Tách từng hàng chữ ngang bằng xử lý ảnh, rồi đọc chữ trong từng box bằng Kim Hán Nôm (cần TOKEN; tắt được). Dành cho bản chụp đẹp, hàng thẳng."
    return info


# ---------------------------------------------------------------- "phát hiện box" + "nhận dạng chữ" theo thư mục
# Một lần OCR một thư mục gồm hai việc độc lập, chọn riêng:
#   box   ai tìm box: KIMHANNOM (API, tự đọc luôn chữ) | PADDLEOCR (tự đọc luôn chữ) | ROW_PROJECTION (tách hàng, không đọc chữ) | BOX_EXISTING (dùng box đã có)
#   text  ai đọc chữ: KIMHANNOM | PADDLEOCR | VINTERN | TEXT_NONE (chỉ lấy box, chữ để trống)
# ch: Kim Hán Nôm + Kim Hán Nôm | tách hàng + Kim Hán Nôm.  vi/intro/ref/toc: PaddleOCR + PaddleOCR | tách hàng + PaddleOCR | tách hàng + Vintern.
# Vintern chỉ đọc chữ (không tìm box) nên chỉ đứng ở vế "chữ"; Kim Hán Nôm và PaddleOCR đã đọc chữ cùng lúc với box nên vế chữ của chúng đi theo box.
BOX_EXISTING, TEXT_NONE = "existing", "none"
PIPELINE_BOXES = {"ch": [KIMHANNOM, ROW_PROJECTION], "vi": [PADDLEOCR, ROW_PROJECTION]}
PIPELINE_TEXTS = {"ch": [KIMHANNOM], "vi": [PADDLEOCR, VINTERN]}
BOX_READS_TEXT = {KIMHANNOM: KIMHANNOM, PADDLEOCR: PADDLEOCR}   # engine tìm box và đọc chữ cùng lúc: chữ do chính nó
PIPELINE_NAMES = {KIMHANNOM: "Kim Hán Nôm (API)", PADDLEOCR: "PaddleOCR-VL", ROW_PROJECTION: "row_projection (tách hàng)",
                  VINTERN: "Vintern-1B", BOX_EXISTING: "box đã có", TEXT_NONE: "không đọc chữ"}


def folder_kind(folder: str) -> str:
    """'ch' cho thư mục Hán Nôm, 'vi' cho thư mục chữ Quốc ngữ (vi, intro, ref, toc)."""
    return "ch" if folder == "ch" else "vi"


def pipeline_problem(folder: str, box: str, text: str) -> str | None:
    """Lý do tổ hợp (box, text) không dùng được cho thư mục, hoặc None nếu hợp lệ."""
    kind = folder_kind(folder)
    texts = PIPELINE_TEXTS[kind]
    if box == BOX_EXISTING:
        return None if text in texts else f"Thư mục {folder}/ chỉ đọc chữ trên box đã có bằng: {', '.join(PIPELINE_NAMES[t] for t in texts)}."
    if box not in PIPELINE_BOXES[kind]:
        return f"Thư mục {folder}/ không phát hiện box bằng {PIPELINE_NAMES.get(box, box)}."
    if box in BOX_READS_TEXT:
        if text not in (BOX_READS_TEXT[box], TEXT_NONE):
            return f"{PIPELINE_NAMES[box]} đọc chữ cùng lúc với box nên chữ do chính nó đọc, không thể dùng {PIPELINE_NAMES.get(text, text)}."
        return None
    return None if text in texts + [TEXT_NONE] else f"Thư mục {folder}/ không đọc chữ bằng {PIPELINE_NAMES.get(text, text)}."


def pipeline_engine(box: str, text: str) -> str:
    """Nguồn OCR (khoá trong ENGINES) thực hiện tổ hợp phát hiện box + nhận dạng chữ (không gồm box đã có)."""
    if box == ROW_PROJECTION:
        return {PADDLEOCR: ROW_PADDLEOCR, VINTERN: VINTERN}.get(text, ROW_PROJECTION)   # tách hàng + Kim Hán Nôm, hoặc chỉ tách hàng
    return box   # KIMHANNOM / PADDLEOCR: tự tìm box và đọc chữ


def pipeline_key(box: str, text: str) -> str:
    return f"{box}+{text}"


def parse_pipeline(key: str) -> tuple[str, str] | None:
    """'row_projection+vintern' -> ('row_projection', 'vintern'); không phải khoá dạng box+chữ thì None."""
    box, sep, text = (key or "").partition("+")
    return (box, text) if sep and box and text else None


def pipeline_options(folder: str) -> dict:
    """Lựa chọn cho thư mục (giao diện app và PPOCRLabel dùng chung): các engine tìm box, engine đọc chữ, và engine đọc chữ cố định theo box."""
    kind = folder_kind(folder)
    return {"folder": folder,
            "boxes": [{"key": b, "label": PIPELINE_NAMES[b], "text": BOX_READS_TEXT.get(b)} for b in PIPELINE_BOXES[kind]],
            "texts": [{"key": t, "label": PIPELINE_NAMES[t]} for t in PIPELINE_TEXTS[kind]]}


def read_box_text(page, folder: str, points, reader: str) -> str:
    """Chữ trong MỘT box đã có (ảnh trang dạng mảng BGR, 4 điểm của box) bằng `reader` (KIMHANNOM, PADDLEOCR hoặc VINTERN).

    Dùng cho chế độ "chỉ đọc chữ trên box đã có". Khác read_row_text (box là hàng ngang của row_projection): box Hán Nôm thường là cột dọc,
    nên với Kim Hán Nôm box cao hơn rộng đọc theo hướng dọc (phải sang trái), box ngang đọc theo hàng ngang (trái sang phải). '' nếu không đọc ra chữ."""
    crop = crop_box(page, points)
    if crop is None:
        return ""
    if reader == KIMHANNOM:
        height, width = crop.shape[:2]
        # Cột dọc: đọc từ phải sang trái. Hàng ngang: trái sang phải, cùng tham số mặc định (ocr_id 2, trả từng chữ có toạ độ). Không dùng
        # ocr_id 4 cho hàng ngang chép tay: nó gộp nhiều chữ vào một box và thêm chữ không có (đã thử trên trang 40 "Nhât Tỉnh Ngâm").
        return read_crop_kimhannom(crop, None, vertical=height >= 1.2 * width)
    if reader == PADDLEOCR:
        return PADDLE.recognize_line(crop)
    return VINTERN_WORKER.recognize_line(crop)


def row_reader(engine: str) -> str:
    """Engine đọc chữ Quốc ngữ trong box của nguồn tách hàng này: PaddleOCR-VL cho row_paddleocr, còn lại Vintern-1B."""
    return PADDLEOCR if engine == ROW_PADDLEOCR else VINTERN


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
# Đọc chữ trong MỘT box đã có (ảnh cắt), bỏ bước tìm box của engine, chỉ lấy chữ:
#   Kim Hán Nôm: API chỉ có image-ocr (tự tìm box bên trong); gọi trên ảnh cắt rồi bỏ result_bbox. Hàng ngang: ocr_id 4, reading_direction 2.
#   PaddleOCR-VL: use_layout_detection=False nên cả ảnh cắt là một khối, prompt "ocr"; tắt xoay/nắn ảnh vì ảnh cắt chỉ là một dòng.
KIMHANNOM_CROP_PARAMS = {"ocr_id": 4, "lang_type": 2, "epitaph": 0, "reading_direction": 2}
PADDLE_LINE_PARAMS = {"pipeline_version": "v1", "engine": "transformers", "use_layout_detection": False, "prompt_label": "ocr",
                      "use_doc_orientation_classify": False, "use_doc_unwarping": False}
CROP_PAD = 4   # điểm ảnh chừa thêm quanh box khi cắt, để không cụt nét chữ ở mép

# Vintern-1B-v3.5 đọc chữ Quốc ngữ trong box đã có (thay PaddleOCR-VL ở bước đọc chữ). Nó KHÔNG tìm box: khi chọn làm nguồn OCR (VINTERN trong
# ENGINES) box vẫn do row_projection tách. Chạy ở môi trường Python riêng, xem VinternHolder và scripts/vintern_worker.py.
VINTERN_MODEL_ID = "5CD-AI/Vintern-1B-v3_5"
# Phần của khoá cache chữ đã đọc: đổi mô hình/prompt/tham số sinh chữ thì chữ cũ (kể cả của PaddleOCR-VL) không còn khớp và được đọc lại.
VINTERN_PARAMS = {"model": VINTERN_MODEL_ID, "prompt": "chep-lai-dong-chu-viet-tay-v1", "beams": 3, "repetition_penalty": 1.1,
                  "retry_repetition_penalty": 2.5, "max_num": 12, "max_new_tokens": 128}
ENGINE_LABELS = {k: v["label"] for k, v in ENGINES.items()}


class EngineError(Exception):
    """Lỗi của một engine (thiếu thư viện, thiếu token, ảnh hỏng, ...), có thông báo cho người dùng.

    `code`: mã lỗi của API nếu có (vd. "000403" của Kim Hán Nôm)."""

    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message)
        self.code = code


class EngineConfigError(EngineError):
    """Lỗi cấu hình/môi trường (thiếu TOKEN, thiếu thư viện, chưa nạp PaddleOCR-VL): thử lại không giúp được gì,
    nên job OCR dừng ngay thay vì thử lại từng trang."""


AUTH_ERROR_CODES = {"000401", "000403"}   # Kim Hán Nôm: chưa đăng nhập / access_denied (thường do TOKEN sai hay hết hạn)


def is_auth_error(error: BaseException) -> bool:
    return isinstance(error, EngineError) and error.code in AUTH_ERROR_CODES


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


# ---------------------------------------------------------------- bản chụp 2 trang (trang ch)
# Quy định: "Bản chụp 2 trang (trang trái trước, phải sau)": đánh số hết trang trái rồi mới sang trang phải.
SPREAD_ASPECT = 1.15            # ảnh rộng/cao >= ngưỡng này (và chưa gán tay) thì tự coi là bản chụp 2 trang
SPREAD_BAND = 0.12              # vùng giữa: tâm box cách đường giữa ảnh <= bấy nhiêu x chiều rộng ảnh thì mới xét là nhiễu
SPREAD_WEIGHTS = (0.6, 0.4)     # trọng số của (độ lệch kích thước, độ gần vị trí giữa) trong score nhiễu
SPREAD_NOISE_THRESHOLD = 0.6    # score > ngưỡng thì xoá box (box xa vùng giữa tối đa 0.6 nên không bao giờ bị xoá)
SPREAD_MIN_BOXES = 4            # ít box hơn thì không đủ để lấy kích thước trung vị, không lọc


def guess_spread(width: int, height: int) -> int:
    """Đoán số trang trong một ảnh theo tỉ lệ khung hình: 2 nếu ảnh nằm ngang rõ rệt, ngược lại 1."""
    return 2 if height > 0 and width / height >= SPREAD_ASPECT else 1


def spread_noise_score(box: dict, median_w: float, median_h: float, mid_x: float, band: float) -> float:
    """Score nhiễu của một box trong bản chụp 2 trang, trong khoảng 0..1 (càng cao càng giống nhiễu).

    score = w_size x lệch_kích_thước + w_mid x gần_giữa, với
      lệch_kích_thước = max(|rộng - rộng_trung_vị| / rộng_trung_vị, |cao - cao_trung_vị| / cao_trung_vị), cắt ở 1
      gần_giữa        = 1 - |tâm_x - giữa| / band, cắt ở 0 (đúng đường giữa = 1, từ mép vùng giữa trở ra = 0)
    Cột chữ thật có kích thước gần trung vị nên chỉ được tối đa w_mid; mảnh nằm sát gáy sách (tiêu đề giữa, số trang, vết gáy)
    thường lệch kích thước nên bị đẩy lên trên ngưỡng."""
    w, h = _size(box)
    size_dev = min(1.0, max(abs(w - median_w) / median_w if median_w else 0.0, abs(h - median_h) / median_h if median_h else 0.0))
    near_mid = max(0.0, 1.0 - abs(_center(box)[0] - mid_x) / band) if band > 0 else 0.0
    return SPREAD_WEIGHTS[0] * size_dev + SPREAD_WEIGHTS[1] * near_mid


def filter_spread_noise(boxes: list[dict], width: int, threshold: float = SPREAD_NOISE_THRESHOLD) -> tuple[list[dict], list[dict]]:
    """Bỏ box nhiễu ở vị trí giữa của bản chụp 2 trang. Trả về (box giữ lại, box đã xoá); thứ tự box giữ lại không đổi."""
    if len(boxes) < SPREAD_MIN_BOXES:
        return list(boxes), []
    median_w = median(_size(b)[0] for b in boxes)
    median_h = median(_size(b)[1] for b in boxes)
    mid_x, band = width / 2, width * SPREAD_BAND
    kept, removed = [], []
    for b in boxes:
        (removed if spread_noise_score(b, median_w, median_h, mid_x, band) > threshold else kept).append(b)
    return kept, removed


def order_boxes_spread(boxes: list[dict], vertical_rtl: bool, width: int) -> list[dict]:
    """Thứ tự đọc của bản chụp 2 trang: toàn bộ trang trái (đánh số như một trang), rồi toàn bộ trang phải.

    Mỗi trang xếp theo order_boxes: Nôm dọc thì cột đầu tiên bên phải của trang trái là 1, đi sang trái, rồi tới cột đầu tiên bên phải
    của trang phải; chữ ngang thì hàng đầu tiên của trang trái là 1, đi xuống. Box thuộc trang nào theo tâm box so với giữa ảnh."""
    mid_x = width / 2
    left = [b for b in boxes if _center(b)[0] < mid_x]
    right = [b for b in boxes if _center(b)[0] >= mid_x]
    return order_boxes(left, vertical_rtl) + order_boxes(right, vertical_rtl)


def clamp_points(points, width: int, height: int) -> list[list[int]]:
    return [[min(max(int(round(x)), 0), width), min(max(int(round(y)), 0), height)] for x, y in points]


# ---------------------------------------------------------------- Kim Hán Nôm
def run_kimhannom(image: Path, uploads_file=None, **params) -> list[dict]:
    """Gọi API Kim Hán Nôm cho một ảnh. `uploads_file`: file ghi lịch sử upload (mặc định data/cache/uploads.json);
    ảnh cắt tạm (PPOCRLabel) dùng file riêng để không làm đầy file lịch sử chung."""
    api_dir = str(HERE / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    try:
        import sinonom_service
    except ImportError as e:
        raise EngineConfigError(f"Thiếu thư viện để gọi API Kim Hán Nôm ({e}). Chạy: pip install -r requirements.txt") from e
    if not os.getenv("TOKEN") and not _env_has_token():
        raise EngineConfigError("Chưa có TOKEN trong file .env (xem README, mục Lấy token).")
    started = time.perf_counter()
    events.log(f"Gọi API Kim Hán Nôm cho {image.name}…")
    try:
        data = sinonom_service.ocr(image, uploads_file=uploads_file, **params)
    except sinonom_service.ApiError as e:
        events.log(f"API Kim Hán Nôm lỗi: {e}", "error")
        raise EngineError(f"API Kim Hán Nôm lỗi: {e}", code=e.code) from e
    events.log(f"Kim Hán Nôm trả {len((data or {}).get('result_bbox') or [])} box ({time.perf_counter() - started:.1f}s)")
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


def _crop_temp_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "gk_ppocrlabel"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_crop_kimhannom(crop, params: dict | None = None, vertical: bool | None = None) -> str:
    """Chữ Hán Nôm trong một ảnh đã cắt (mảng BGR của một box) qua API Kim Hán Nôm; '' nếu không nhận ra.

    Chỉ lấy chữ: toạ độ box API trả về bị bỏ (chỉ dùng để xếp thứ tự đọc). `vertical`: True = cột dọc (phải sang trái), False = hàng ngang
    (trái sang phải), None = theo hình dạng ẢNH CẮT (cao hơn rộng là cột dọc, ngược lại là hàng ngang). Không đoán từ hình dạng các box
    chữ API trả về: API trả từng chữ gần vuông, nên cách đó coi hàng ngang là "dọc" và xếp phải sang trái, chữ ra ngược thứ tự (lỗi đã gặp ở
    trang 40 của "Nhât Tỉnh Ngâm"). API trả chữ theo thứ tự của nó (với ocr_id 2 là phải sang trái) nhưng thứ tự đọc ở đây luôn do toạ độ
    quyết định. Lịch sử upload ghi vào file riêng để không làm đầy data/cache/uploads.json."""
    import cv2

    tmp = _crop_temp_dir() / f"crop_{uuid.uuid4().hex[:10]}.jpg"
    ok, encoded = cv2.imencode(".jpg", crop)
    if not ok:
        return ""
    encoded.tofile(str(tmp))
    try:
        boxes = run_kimhannom(tmp, uploads_file=_crop_temp_dir() / "crop_uploads.json", **(params or KIMHANNOM_PARAMS))
    finally:
        tmp.unlink(missing_ok=True)
    boxes = [b for b in boxes if b["text"].strip()]
    if not boxes:
        return ""
    if vertical is None:
        vertical = crop.shape[0] > crop.shape[1]
    return "".join(b["text"].strip() for b in order_boxes(boxes, vertical_rtl=vertical))   # chữ Hán Nôm viết liền


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
        events.model("paddleocr", "loading", "Nạp PaddleOCR-VL…")
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
            events.model("paddleocr", "ready", f"PaddleOCR-VL sẵn sàng ({device}, {self._seconds}s)", device=device, seconds=self._seconds)
        except Exception as e:  # nạp lỗi (thiếu thư viện, hết bộ nhớ, tải model lỗi, ...) báo lên UI
            with self._lock:
                self._state, self._error = "error", f"{type(e).__name__}: {e}"
            events.model("paddleocr", "error", f"Nạp PaddleOCR-VL lỗi: {self._error}")

    def _wait_ready(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise EngineConfigError(reason)
        while True:  # đang nạp thì chờ nạp xong; KHÔNG tự nạp: việc nạp do người dùng bấm nút "Nạp mô hình"
            with self._lock:
                state, error = self._state, self._error
            if state == "ready":
                return
            if state == "idle":
                raise EngineConfigError('Chưa nạp mô hình PaddleOCR-VL. Bấm "Nạp mô hình" ở bước OCR rồi chạy lại.')
            if state == "error":
                raise EngineConfigError(f"Không nạp được PaddleOCR-VL: {error}")
            time.sleep(0.5)

    def recognize_line(self, image) -> str:
        """Chữ trong MỘT ảnh đã cắt (một box), không tìm box: bỏ layout detection, cả ảnh là một khối đọc bằng prompt "ocr".
        Trả về chuỗi (các dòng nối bằng khoảng trắng, '' nếu không đọc được), không có toạ độ.
        `image`: đường dẫn hoặc mảng ảnh numpy (BGR)."""
        self._wait_ready()
        with self._infer_lock:
            source = str(image) if isinstance(image, (str, os.PathLike)) else image
            results = list(self._pipeline.predict(
                source, **{k: v for k, v in PADDLE_LINE_PARAMS.items() if k not in ("pipeline_version", "engine")}))
        texts = []
        for result in results:
            data = result.json
            data = data.get("res", data)
            for block in sorted(data.get("parsing_res_list", []),
                                key=lambda b: (b.get("block_order") is None, b.get("block_order") or 0)):
                text = " ".join((block.get("block_content") or "").split())
                if text:
                    texts.append(text)
        return " ".join(texts)

    def recognize(self, image) -> list[dict]:
        """Mọi khối của một ảnh theo thứ tự đọc của mô hình: {"text", "points", "label"} (chưa lọc theo nhãn khối).
        `image`: đường dẫn (Path/str) hoặc mảng ảnh numpy (BGR)."""
        self._wait_ready()
        with self._infer_lock:
            source = str(image) if isinstance(image, (str, os.PathLike)) else image
            results = list(self._pipeline.predict(source))
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


# ---------------------------------------------------------------- Vintern (đọc chữ Quốc ngữ trong box đã có)
def _env_value(name: str) -> str | None:
    """Giá trị của biến môi trường, không có thì đọc dòng `NAME=...` trong .env của dự án."""
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        for line in (HERE.parent / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == name and val.strip():
                return val.strip().strip('"\'')
    except OSError:
        pass
    return None


class VinternHolder:
    """Chạy scripts/vintern_worker.py trong môi trường Python riêng (Vintern cần transformers 4.x, app dùng 5.x) và đọc chữ
    từng ảnh cắt qua stdin/stdout. Worker khởi động lúc dùng lần đầu, nạp mô hình một lần rồi dùng lại cho mọi box; chết thì tự dựng lại.

    Môi trường lấy từ VINTERN_PYTHON (biến môi trường hoặc .env), không có thì thử .venv-vintern của dự án rồi C:\\vintern.
    `command`: ghi đè lệnh khởi chạy (kiểm thử)."""

    LOAD_TIMEOUT = 900       # giây chờ nạp mô hình lần đầu (có thể phải tải ~2 GB)
    REQUEST_TIMEOUT = 180    # giây chờ một box

    def __init__(self, command: list[str] | None = None):
        self._command = command
        self._lock = threading.Lock()
        self._proc = None
        self._lines = None
        self._next_id = 0
        self.state, self.device, self.error, self.seconds = "idle", "", "", None

    # ---- cấu hình
    @staticmethod
    def python() -> str | None:
        candidates = []
        configured = _env_value("VINTERN_PYTHON")
        if configured:
            path = Path(configured)
            candidates.append(path if path.is_absolute() else HERE.parent / path)
        candidates += [HERE.parent / ".venv-vintern" / "Scripts" / "python.exe", Path("C:/vintern/Scripts/python.exe"),
                       HERE.parent / ".venv-vintern" / "bin" / "python"]
        return next((str(p) for p in candidates if p.is_file()), None)

    def unavailable_reason(self) -> str | None:
        if self._command is not None:
            return None
        if self.python() is None:
            return ("Chưa có môi trường Python cho Vintern. Tạo môi trường riêng theo hướng dẫn đầu scripts/vintern_viz.py "
                    "(transformers>=4.44,<5, xem requirements-vintern.txt) rồi đặt VINTERN_PYTHON=<đường dẫn python.exe> trong .env.")
        return None

    def status(self) -> dict:
        """Cùng dạng với PaddleHolder.status để giao diện dùng chung: state idle | loading | ready | error | unavailable."""
        reason = self.unavailable_reason()
        if reason and self.state != "ready":
            return {"state": "unavailable", "error": reason, "model": VINTERN_MODEL_ID}
        return {"state": self.state, "error": self.error or None, "seconds": self.seconds, "device": self.device or None,
                "model": VINTERN_MODEL_ID}

    def preload(self) -> dict:
        """Khởi động worker và nạp mô hình ở luồng nền nếu chưa nạp (nút "Nạp mô hình"). Gọi nhiều lần cũng an toàn; đang nạp mà có yêu cầu
        đọc chữ thì yêu cầu đó chờ nạp xong. Không bấm nút thì worker tự nạp ở lần đọc chữ đầu tiên."""
        if self.unavailable_reason():
            return self.status()
        if self.state in ("loading", "ready"):
            return {"state": self.state}
        self.state, self.error = "loading", ""
        threading.Thread(target=self._preload, daemon=True).start()
        return {"state": "loading"}

    def _preload(self) -> None:
        try:
            with self._lock:
                if self._proc is None or self._proc.poll() is not None:
                    self._spawn()
        except EngineError as e:   # _spawn đã đặt state=error; giữ lý do cho giao diện
            self.state, self.error = "error", str(e)

    # ---- tiến trình
    def _spawn(self) -> None:
        import json
        import queue
        import subprocess

        reason = self.unavailable_reason()
        if reason:
            raise EngineConfigError(reason)
        command = self._command or [self.python(), "-X", "utf8", str(HERE / "vintern_worker.py")]
        log_path = Path(tempfile.gettempdir()) / "gk_vintern.log"
        self.state, self.error = "loading", ""
        started = time.perf_counter()
        events.model("vintern", "loading", "Nạp Vintern-1B (lần đầu có thể mất vài chục giây)…")
        with open(log_path, "ab") as log:
            self._proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, close_fds=True,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines: queue.Queue = queue.Queue()
        self._lines = lines

        def pump(stream=self._proc.stdout) -> None:
            for raw in iter(stream.readline, b""):
                lines.put(raw.decode("utf-8", errors="replace"))
            lines.put(None)   # hết luồng: tiến trình đã thoát
        threading.Thread(target=pump, daemon=True).start()
        try:
            first = self._read(self.LOAD_TIMEOUT, "Đang nạp Vintern-1B…")
        except EngineError as e:
            self._kill()
            self.state = "error"
            events.model("vintern", "error", str(e))
            raise
        message = json.loads(first) if first else {}
        if not message.get("ready"):
            detail = message.get("fatal") or f"tiến trình thoát (log: {log_path})"
            self._kill()
            self.state, self.error = "error", str(detail)
            events.model("vintern", "error", f"Không nạp được Vintern: {detail}")
            raise EngineConfigError(f"Không nạp được Vintern: {detail}")
        self.state, self.device, self.seconds = "ready", str(message.get("device", "")), round(time.perf_counter() - started, 1)
        events.model("vintern", "ready", f"Vintern-1B sẵn sàng ({self.device}, {self.seconds}s)", device=self.device, seconds=self.seconds)

    def _read(self, timeout: float, waiting: str = "Đang chờ Vintern-1B đọc…") -> str | None:
        """Chờ một dòng từ worker. Có người theo dõi (ocr_events) thì chờ từng nhịp ngắn và phát `tick` để giao diện còn vẽ lại, không bị treo."""
        import queue
        started = time.monotonic()
        deadline = started + timeout
        while True:
            remaining = deadline - time.monotonic()
            step = min(0.4, max(remaining, 0.01)) if events.has_listeners() else max(remaining, 0.01)
            try:
                return self._lines.get(timeout=step)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise EngineError(f"Vintern không phản hồi sau {int(timeout)} giây") from None
                events.tick(waiting, time.monotonic() - started)

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()

    def close(self) -> None:
        with self._lock:
            self._kill()
            self.state = "idle"

    # ---- đọc chữ
    def recognize_line(self, image) -> str:
        """Chữ trong MỘT ảnh cắt (mảng BGR của một box); '' nếu không đọc ra. Lỗi cấu hình/nạp mô hình ném EngineConfigError."""
        import json

        import cv2

        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise EngineError("Không mã hoá được ảnh cắt để đọc chữ")
        path = _vintern_temp_dir() / f"line_{uuid.uuid4().hex[:12]}.png"
        path.write_bytes(encoded.tobytes())
        try:
            with self._lock:
                if self._proc is None or self._proc.poll() is not None:
                    self._spawn()
                self._next_id += 1
                request_id = self._next_id
                try:
                    self._proc.stdin.write((json.dumps({"id": request_id, "image": str(path)}) + "\n").encode("utf-8"))
                    self._proc.stdin.flush()
                    while True:   # bỏ qua trả lời muộn của yêu cầu cũ đã quá hạn
                        raw = self._read(self.REQUEST_TIMEOUT)
                        if raw is None:
                            raise EngineError("Tiến trình Vintern đã thoát giữa chừng")
                        reply = json.loads(raw)
                        if reply.get("id") == request_id:
                            break
                except (EngineError, OSError):
                    self._kill()           # lần sau dựng lại worker mới
                    self.state = "idle"
                    raise
            if "error" in reply:
                raise EngineError(f"Vintern lỗi: {reply['error']}")
            return str(reply.get("text", "")).strip()
        finally:
            path.unlink(missing_ok=True)


def _vintern_temp_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "gk_vintern"
    d.mkdir(parents=True, exist_ok=True)
    return d


VINTERN_WORKER = VinternHolder()


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


def rows_to_scripts(boxes: list[dict], first: str = "ch", alternate: bool = True) -> dict[str, list[dict]]:
    """Chia các hàng của trang chép tay (hàng Hán xen hàng Quốc ngữ) về ch và vi: hàng lẻ là `first`, hàng chẵn là phần còn lại.
    `alternate` False: không lọc xen kẽ, mỗi thư mục nhận MỌI hàng (người dùng tự xoá hàng thừa)."""
    if not alternate:
        return {"ch": list(boxes), "vi": list(boxes)}
    other = "vi" if first == "ch" else "ch"
    return {first: boxes[0::2], other: boxes[1::2]}


def crop_box(page, points, pad: int = CROP_PAD):
    """Vùng ảnh của một box (hình chữ nhật bao 4 điểm, chừa `pad` điểm ảnh, kẹp trong ảnh); None nếu rỗng."""
    height, width = page.shape[:2]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x1, x2 = max(int(min(xs)) - pad, 0), min(int(max(xs)) + pad, width)
    y1, y2 = max(int(min(ys)) - pad, 0), min(int(max(ys)) + pad, height)
    return page[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None


def load_page_image(image: Path):
    """Ảnh trang dạng mảng BGR để cắt từng box (fromfile: đường dẫn có dấu tiếng Việt)."""
    import cv2
    import numpy as np

    page = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if page is None:
        raise EngineError(f"Không đọc được ảnh {image.name}")
    return page


def read_row_text(page, folder: str, points, reader: str = VINTERN) -> str:
    """Chữ trong MỘT box của row_projection: box của `ch` đọc bằng Kim Hán Nôm, box của thư mục khác (Quốc ngữ) bằng `reader`
    (VINTERN: Vintern-1B, mặc định; PADDLEOCR: PaddleOCR-VL).

    Chỉ đọc chữ trong box đã có: không engine nào tìm box mới. '' nếu box rỗng/không đọc ra chữ. Lỗi của engine ném EngineError
    (EngineConfigError nếu thiếu TOKEN/chưa nạp mô hình) để nơi gọi quyết định thử lại, bỏ qua hay dừng."""
    crop = crop_box(page, points)
    if crop is None:
        return ""
    if folder == "ch":
        return read_crop_kimhannom(crop, KIMHANNOM_CROP_PARAMS, vertical=False)
    if reader == PADDLEOCR:
        return PADDLE.recognize_line(crop)
    return VINTERN_WORKER.recognize_line(crop)
