"""Cầu nối giữa PPOCRLabel (external/PPOCRLabel) và các engine OCR của dự án: Kim Hán Nôm, PaddleOCR-VL, row_projection, Vintern-1B.

PPOCRLabel gốc nạp 11 mô hình PP-OCR của riêng nó khi khởi động. Bản trong dự án bỏ hết, và mọi chức năng nhận dạng của PPOCRLabel
(Auto Recognition, Re-recognition, Cell Re-Recognition) gọi các hàm dưới đây, tức là dùng đúng các engine của bước OCR trong app:

  recognize_page(ảnh)      nhận dạng cả trang, giống hệt bước OCR của app (cùng cache cache/ocr/, cùng lọc/sắp xếp/làm sạch)
  recognize_crop(mảng ảnh) nhận dạng một vùng đã cắt (một box) và trả về chuỗi chữ

Lựa chọn (đặt trong hộp thoại "Choose OCR model" của PPOCRLabel, giống popup "OCR thư mục này" của app) là một khoá "box+chữ":

  box   ai phát hiện box: kimhannom | paddleocr | row_projection | existing (không phát hiện: dùng box đã có trong Label.txt)
  chữ   ai đọc chữ: kimhannom | paddleocr | vintern | none (chỉ lấy box, chữ để trống)

Ví dụ "row_projection+vintern" tách hàng rồi đọc từng hàng bằng Vintern; "kimhannom+none" chỉ lấy box từ API; "existing+paddleocr" giữ box
đang có và đọc lại chữ bằng PaddleOCR-VL. Tổ hợp nào dùng được cho thư mục nào xem ocr_engines.pipeline_problem / pipeline_options.
Khoá "auto" (mặc định) và khoá một engine đơn (kimhannom, paddleocr, row_projection, vintern, ...) vẫn dùng được: auto theo thư mục
(ch -> Kim Hán Nôm; vi, intro, ref, toc -> PaddleOCR-VL; trang ch_vi theo nhãn trong cache/phan_loai.json -> row_projection).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import label_io, library, ocr_engines as eng, ocr_events as events, ocr_jobs

AUTO = "auto"
AUTO_LABEL = "Tự động theo thư mục (ch: Kim Hán Nôm · vi/intro/ref/toc: PaddleOCR-VL · ch_vi: row_projection)"

# Việc cần làm trong hộp thoại PPOCRLabel: (khoá, nhãn). "read" và "selected" cùng khoá engine existing+chữ, khác nhau ở phạm vi khi chạy ngay:
# tất cả box của ảnh (Re-recognition) hay chỉ các box đang chọn (Re-recognition RectBox).
TASKS = [("both", "Phát hiện box và đọc chữ (cả trang)"),
         ("detect", "Chỉ phát hiện box (cả trang, chữ để trống)"),
         ("read", "Đọc lại chữ của tất cả box trong trang (giữ nguyên box)"),
         ("selected", "Đọc lại chữ của các box đã chọn (giữ nguyên box)")]
TASK_HELP = {"both": "Tìm box mới rồi đọc chữ trong từng box. Thay các box hiện có của ảnh.",
             "detect": "Chỉ tìm box, chữ để trống (đọc chữ sau). Thay các box hiện có của ảnh.",
             "read": "Không tìm box mới: giữ nguyên mọi box đang có và đọc lại chữ của tất cả. Chữ cũ bị thay.",
             "selected": "Không tìm box mới: chỉ đọc lại chữ của các box đang chọn trên ảnh (Ctrl+Shift+A chọn tất cả). Box khác giữ nguyên."}


def engine_options(folder: str) -> dict:
    """Lựa chọn cho thư mục đang mở (giống popup của app): {"boxes": [...], "texts": [...], "tasks": [...], "task_help": {...}},
    xem ocr_engines.pipeline_options."""
    return {**eng.pipeline_options(folder), "tasks": TASKS, "task_help": TASK_HELP}


def build_engine(task: str, box: str | None, text: str | None) -> str:
    """Khoá engine của PPOCRLabel từ ba ô chọn: both -> box+chữ, detect -> box+none, read/selected -> existing+chữ."""
    if task == "detect":
        return eng.pipeline_key(box, eng.TEXT_NONE)
    if task in ("read", "selected"):
        return eng.pipeline_key(eng.BOX_EXISTING, text)
    return eng.pipeline_key(box, eng.BOX_READS_TEXT.get(box) or text)


def unavailable_reasons(task: str, box: str | None, text: str | None) -> list[str]:
    """Lý do các engine mà lựa chọn này cần chưa dùng được (thiếu TOKEN, chưa cài PaddleOCR, chưa có môi trường Vintern, ...); rỗng nếu dùng được."""
    used = [text] if task in ("read", "selected") else [box] if task == "detect" else [box, text]
    reasons = []
    for name in dict.fromkeys(k for k in used if k):
        reason = ocr_jobs._engine_unavailable(name)
        if reason:
            reasons.append(reason)
    return reasons


# Engine nhận dạng chữ cho một vùng cắt khi người dùng chọn tự động hoặc row_projection (không nhận dạng được chữ)
TEXT_ENGINE_BY_FOLDER = {"ch": eng.KIMHANNOM}
DEFAULT_TEXT_ENGINE = eng.VINTERN     # đọc chữ Quốc ngữ trong box có sẵn; chọn paddleocr tường minh thì vẫn dùng PaddleOCR-VL


def describe(engine: str | None) -> str:
    """Mô tả ngắn một khoá engine để ghi log: 'tách hàng (row_projection) → Vintern-1B', 'PaddleOCR-VL (box + chữ)', ..."""
    parsed = eng.parse_pipeline(engine) if engine else None
    if parsed:
        box, text = parsed
        names = eng.PIPELINE_NAMES
        if box == eng.BOX_EXISTING:
            return f"giữ box đã có → đọc chữ bằng {names.get(text, text)}"
        if text == eng.TEXT_NONE:
            return f"chỉ phát hiện box bằng {names.get(box, box)}"
        return f"{names.get(box, box)} → {names.get(text, text)}" if box not in eng.BOX_READS_TEXT else f"{names.get(box, box)} (box + chữ)"
    if engine in (None, AUTO):
        return "tự động theo thư mục"
    return eng.ENGINE_LABELS.get(engine, str(engine))


def model_status() -> dict:
    """Trạng thái ba mô hình/dịch vụ cho khung log: {"kimhannom"|"paddleocr"|"vintern": {"state", "detail", ...}}.
    state: ready | idle | loading | error | unavailable; kimhannom chỉ có ready/unavailable (API, cần TOKEN)."""
    kim = eng.kimhannom_available()
    paddle = eng.PADDLE.status()
    vintern = eng.VINTERN_WORKER.status()
    return {"kimhannom": {"state": "unavailable" if kim else "ready", "detail": kim or "có TOKEN"},
            "paddleocr": {**paddle, "detail": paddle.get("error") or (f"{paddle.get('device') or ''} {paddle.get('seconds') or ''}s".strip() if paddle["state"] == "ready" else "")},
            "vintern": {**vintern, "detail": vintern.get("error") or (f"{vintern.get('device') or ''} {vintern.get('seconds') or ''}s".strip() if vintern["state"] == "ready" else "")}}


def preload_model(name: str) -> dict:
    """Nạp trước một mô hình ở nền (nút "Nạp" trong khung log); trả về trạng thái hiện tại của mô hình đó."""
    if name == "vintern":
        eng.VINTERN_WORKER.preload()
        return eng.VINTERN_WORKER.status()
    if name == "paddleocr":
        eng.PADDLE.preload()
        return eng.PADDLE.status()
    raise ValueError(f"Không có mô hình nào tên {name!r}.")


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
    elif engine == eng.VINTERN:
        reason = eng.VINTERN_WORKER.unavailable_reason()   # worker tự nạp mô hình ở lần đọc đầu tiên
        if reason:
            raise eng.EngineError(reason)


def recognize_page(image_path, engine: str | None = None, use_cache: bool = True) -> tuple[list[dict], str, bool]:
    """Nhận dạng cả trang. Trả về (các box {"transcription", "points"} của thư mục đang mở, engine đã dùng, lấy từ cache?).

    `engine`: auto, một engine đơn, hoặc khoá "box+chữ" (xem đầu file). Đi qua ocr_jobs.ocr_page nên giống hệt bước OCR của app: ảnh đã OCR
    bằng engine đó thì lấy từ cache, không gọi lại API/mô hình. "existing+chữ" không phát hiện box: đọc lại chữ trên box đang có trong Label.txt."""
    book, number, folder, path = locate(image_path)
    tag = page_tag(book, number, folder)
    read_text, boxes_only = True, False
    events.log(f"Nhận dạng ảnh {path.name} ({folder}/): {describe(engine)}")
    started = time.perf_counter()
    parsed = eng.parse_pipeline(engine) if engine else None
    if parsed:
        box, text = parsed
        problem = eng.pipeline_problem(folder, box, text)
        if problem:
            raise eng.EngineError(problem)
        if box == eng.BOX_EXISTING:        # giữ box đã có: không tìm box, chỉ đọc lại chữ
            boxes = read_existing_boxes(path, folder, text)
            events.log(f"Xong {path.name}: đọc lại chữ của {len(boxes)} box ({time.perf_counter() - started:.1f}s)", "ok")
            return boxes, engine, False
        read_text, boxes_only = text != eng.TEXT_NONE, text == eng.TEXT_NONE
        used_key, engine = engine, eng.pipeline_engine(box, text)
    else:
        used_key = None
        engine = eng.RECOMMENDED[tag] if engine in (None, AUTO) else engine
    if engine not in eng.engines_for_tag(tag):
        raise eng.EngineError(f"Chế độ \"{eng.ENGINES[engine]['label']}\" không dùng được cho trang nhãn {tag}.")
    ensure_ready(engine)
    wanted = {folder: None} if engine in eng.ROW_ENGINES else None   # trang ch_vi: chỉ đọc chữ của thư mục đang mở
    if engine in eng.ROW_ENGINES and read_text:   # tách hàng rồi đọc chữ trong từng box bằng Kim Hán Nôm (ch) / Vintern-1B hoặc PaddleOCR-VL
        for text_engine_name in ocr_jobs.row_text_engines(tag, engine, {folder}):
            ensure_ready(text_engine_name)
    result, cached = ocr_jobs.ocr_page(book, number, tag, engine, path, use_cache, read_text, wanted)
    boxes = result.get(folder)
    if boxes is None:                      # thư mục mở không trùng nhãn nào: lấy phần đầu tiên
        boxes = next(iter(result.values()), [])
    if boxes_only:                         # chỉ lấy box: bỏ chữ engine đã đọc luôn
        boxes = [label_io.make_box(b["points"], "") for b in boxes]
    events.log(f"Xong {path.name}: {len(boxes)} box ({time.perf_counter() - started:.1f}s{', từ cache' if cached else ''})", "ok")
    return boxes, used_key or engine, cached


def read_existing_boxes(path: Path, folder: str, reader: str) -> list[dict]:
    """Chế độ giữ box: các box đang có của ảnh trong Label.txt của thư mục, toạ độ giữ nguyên, chữ đọc lại bằng `reader`.

    Box mà engine không đọc ra chữ giữ chữ cũ (không xoá). Lỗi engine ném EngineError; ảnh chưa có box thì báo rõ thay vì trả danh sách rỗng."""
    problem = eng.pipeline_problem(folder, eng.BOX_EXISTING, reader)
    if problem:
        raise eng.EngineError(problem)
    payload = label_io.read_labels(path.parent / label_io.LABEL_NAME).get(label_io.image_key(folder, path.name))
    boxes = json.loads(payload) if payload else []
    if not boxes:
        raise eng.EngineError(f"Ảnh {path.name} chưa có box nào trong {folder}/Label.txt để đọc lại chữ. "
                              "Hãy phát hiện box trước (chọn \"Phát hiện box và đọc chữ\" hoặc \"Chỉ phát hiện box\") hoặc vẽ box.")
    ensure_ready(reader)
    page = eng.load_page_image(path)
    out = []
    name = eng.PIPELINE_NAMES.get(reader, reader)
    for i, b in enumerate(boxes, 1):
        events.check_cancel()
        events.progress(i - 1, len(boxes), f"Box {i}/{len(boxes)}")
        started = time.perf_counter()
        text = eng.read_box_text(page, folder, b["points"], reader).strip()
        events.log(f"[{i}/{len(boxes)}] {name} ({time.perf_counter() - started:.1f}s): {text[:60] or '(không đọc ra chữ, giữ chữ cũ)'}", "info" if text else "warn")
        out.append({"transcription": text or str(b.get("transcription", "")), "points": b["points"]})
    events.progress(len(boxes), len(boxes), "Xong")
    return out


def text_engine(engine: str | None, folder: str) -> str:
    """Engine đọc chữ trong một vùng cắt: khoá "box+chữ" -> engine chữ đã chọn; tự động/tách hàng -> engine chữ mặc định của thư mục
    (ch: Kim Hán Nôm, còn lại: Vintern-1B); row_paddleocr -> PaddleOCR-VL cho thư mục Quốc ngữ; chọn tay engine nào thì dùng engine đó."""
    parsed = eng.parse_pipeline(engine) if engine else None
    if parsed:
        box, text = parsed
        chosen = eng.BOX_READS_TEXT.get(box) if box != eng.BOX_EXISTING else None
        chosen = chosen or (text if text != eng.TEXT_NONE else None)
        return chosen or TEXT_ENGINE_BY_FOLDER.get(folder, DEFAULT_TEXT_ENGINE)
    if engine == eng.ROW_PADDLEOCR:
        return TEXT_ENGINE_BY_FOLDER.get(folder, eng.PADDLEOCR)
    if engine in (None, AUTO, eng.ROW_PROJECTION):
        return TEXT_ENGINE_BY_FOLDER.get(folder, DEFAULT_TEXT_ENGINE)
    return engine


def recognize_crop(crop, engine: str | None = None, folder: str = "vi") -> str:
    """Nhận dạng một vùng đã cắt (mảng ảnh BGR của một box) và trả về chuỗi chữ ('' nếu không nhận ra)."""
    engine = text_engine(engine, folder)
    name = eng.PIPELINE_NAMES.get(engine, engine)
    started = time.perf_counter()
    try:
        ensure_ready(engine)
        if engine == eng.KIMHANNOM:
            text = eng.read_crop_kimhannom(crop)   # chỉ lấy chữ; lịch sử upload ghi vào file riêng
        elif engine == eng.VINTERN:
            text = eng.VINTERN_WORKER.recognize_line(crop)   # box đã có sẵn: chỉ đọc chữ
        else:
            text = eng.PADDLE.recognize_line(crop)     # chọn paddleocr tường minh: bỏ layout detection, chỉ đọc chữ
    except Exception as e:
        events.log(f"{name}: lỗi: {e}", "error")
        raise
    seconds = time.perf_counter() - started
    events.log(f"{name} ({seconds:.1f}s): {text[:60]}" if text else f"{name} ({seconds:.1f}s): không đọc ra chữ", "info" if text else "warn")
    return text
