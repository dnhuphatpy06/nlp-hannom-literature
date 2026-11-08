"""Bước OCR của Web UI: chép ảnh theo nhãn vào ch/ vi/ ..., chạy OCR nền, ghi Label.txt + fileState.txt, mở PPOCRLabel.

Luồng: nhãn trang (cache/phan_loai.json) -> sync_folders chép ảnh vào <sách>/<nhãn>/ -> mỗi ảnh chạy engine đã chọn
cho nhãn đó (scripts/ocr_engines.py) -> <sách>/<thư mục>/Label.txt + fileState.txt (state 0 = chờ kiểm tra bằng PPOCRLabel).

  - blank, misc, unknown: chỉ chép ảnh (blank, misc) hoặc bỏ qua (unknown), KHÔNG OCR.
  - ch_vi (T2b): ảnh chép vào cả ch/ và vi/; mỗi thư mục chỉ giữ box đúng loại chữ của nó.
  - Mặc định không ghi đè ảnh đã có dòng trong Label.txt (để giữ phần đã sửa tay); ảnh có state 1 chỉ bị ghi đè khi overwrite.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import re
from datetime import datetime
from pathlib import Path

from . import label_io, ocr_cache, ocr_engines as eng, ocr_events as events, page_cache
from .library import SUBMISSION_FOLDERS, UNKNOWN, Book, find_page_image, norm, write_json_atomic

# nhãn trang -> các thư mục nhận ảnh (ch_vi vào cả hai)
FOLDER_TAGS = {"ch": ["ch"], "vi": ["vi"], "ch_vi": ["ch", "vi"], "intro": ["intro"], "ref": ["ref"],
               "toc": ["toc"], "blank": ["blank"], "misc": ["misc"]}
LABEL_FOLDERS = ("ch", "vi", "intro", "ref", "toc")   # thư mục có Label.txt / fileState.txt
FLUSH_EVERY = 5            # ghi Label.txt sau mỗi bấy nhiêu ảnh, dừng giữa chừng không mất hết
DEFAULT_UI_LANG = "en"      # PPOCRLabel chỉ có giao diện tiếng Trung (ch) và tiếng Anh (en)
EARLY_EXIT_SECONDS = 6      # PPOCRLabel thoát trong vòng ngần này giây sau khi mở thì coi là khởi động lỗi
MAX_PAGE_ATTEMPTS = 3      # số lượt thử mỗi trang; hết lượt vẫn lỗi thì bỏ qua trang đó, ghi log và chạy trang khác
RETRY_BACKOFF_SECONDS = 2  # nghỉ giữa các lượt thử: 2 s sau lượt 1, 4 s sau lượt 2
MAX_AUTH_FAIL_STREAK = 5   # ngần này trang liên tiếp còn lỗi xác thực (TOKEN sai/hết hạn) thì dừng job, không gọi tiếp hàng trăm lần
JOB_LOG_LINES = 200        # số dòng log chạy giữ lại để hiện trên giao diện
_EMPTY_BOX_RE = re.compile(r'"transcription"\s*:\s*""')


class OcrBusy(Exception):
    pass


def folders_for(tag: str) -> list[str]:
    return FOLDER_TAGS.get(tag, [])


def page_tags(book: Book) -> dict[int, str]:
    cache = page_cache.read_cache(book) or {}
    return {int(n): (p.get("tag") or UNKNOWN) for n, p in cache.get("pages", {}).items() if isinstance(p, dict)}


def _image_name(book: Book, number: int) -> str:
    return f"{book.stem}.{number:03d}.jpg"


def _folder_images(book: Book, folder: str) -> dict[int, Path]:
    """{số trang: file} của các ảnh `<Tên sách>.<NNN>.jpg` đang nằm trong thư mục."""
    out: dict[int, Path] = {}
    directory = book.dir / folder
    if not directory.is_dir():
        return out
    for f in directory.glob("*.jpg"):
        stem, _, num = f.stem.rpartition(".")
        if num.isdigit() and norm(stem) == norm(book.stem):
            out[int(num)] = f
    return out


# ---------------------------------------------------------------- chép ảnh vào thư mục theo nhãn
def sync_folders(book: Book) -> dict:
    """Chép ảnh trang vào thư mục theo nhãn; xóa ảnh (và dòng nhãn) của trang đã đổi sang nhãn khác.

    Chỉ đụng tới file `<Tên sách>.<NNN>.jpg`. Ảnh đã có trong thư mục đích thì giữ nguyên (không ghi đè, phòng khi
    ảnh đã xoay/chỉnh tay).
    """
    wanted: dict[str, set[int]] = {f: set() for f in SUBMISSION_FOLDERS}
    tags = page_tags(book)
    for number, tag in tags.items():
        for folder in folders_for(tag):
            wanted[folder].add(number)

    copied = removed = missing = 0
    for folder, numbers in wanted.items():
        (book.dir / folder).mkdir(exist_ok=True)
        existing = _folder_images(book, folder)
        for number in sorted(numbers):
            if number in existing:
                continue
            src = find_page_image(book.pages_dir, book.stem, number)
            if src is None:
                missing += 1
                continue
            shutil.copy2(src, book.dir / folder / _image_name(book, number))
            copied += 1
        # Không có nhãn nào (cache hỏng/mất) thì không xóa gì
        stale = [n for n in existing if n not in numbers] if tags else []
        for number in stale:
            existing[number].unlink(missing_ok=True)
            removed += 1
        if stale and folder in LABEL_FOLDERS:
            label_io.update_folder(book.dir / folder, {}, drop_keys=[label_io.image_key(folder, existing[n].name) for n in stale])
    return {"copied": copied, "removed": removed, "missing_images": missing}


# ---------------------------------------------------------------- trạng thái cho giao diện
def folder_status(book: Book) -> list[dict]:
    out = []
    for folder in LABEL_FOLDERS:
        directory = book.dir / folder
        images = _folder_images(book, folder)
        labels = label_io.read_labels(directory / label_io.LABEL_NAME)
        states = label_io.read_states(directory / label_io.STATE_NAME)
        keys = {label_io.image_key(folder, f.name) for f in images.values()}
        empty = sum(1 for k in keys if k in labels and _EMPTY_BOX_RE.search(labels[k]))   # ảnh còn ít nhất một box chữ rỗng
        out.append({"folder": folder, "path": str(directory), "images": len(images), "empty": empty,
                    "labeled": len(keys & set(labels)), "done": sum(1 for k in keys if states.get(k) == 1)})
    return out


def read_log(book: Book) -> dict:
    try:
        data = json.loads((book.cache_dir / "ocr_log.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def plan(book: Book) -> dict:
    """Dữ liệu cho màn hình OCR: số trang theo nhãn, engine đề xuất, trạng thái thư mục.

    Vào màn hình này thì chép ảnh trang vào ch/ vi/ ... theo nhãn (sync_folders) nếu chưa chép: nếu không, sách mới có thư mục
    rỗng và hai nút "OCR thư mục này" / "Mở PPOCRLabel" bị khoá (chúng cần ảnh trong thư mục), mà trước đây ảnh chỉ được chép
    khi một job OCR bắt đầu. Bỏ qua khi đang có job OCR hoặc PPOCRLabel đang mở (sync có thể xoá ảnh của trang đã đổi nhãn)."""
    if job_status(book)["status"] != "running" and not ppocrlabel_running()["running"]:
        try:
            sync_folders(book)
        except OSError:
            pass   # không chép được thì vẫn trả dữ liệu hiện có; nút sẽ báo "chưa có ảnh"
    counts: dict[str, int] = {}
    for tag in page_tags(book).values():
        counts[tag] = counts.get(tag, 0) + 1
    tags = [{"tag": t, "pages": counts.get(t, 0), "recommended": eng.RECOMMENDED[t], "folders": folders_for(t),
             "engines": eng.engines_for_tag(t),
             "options": [{"key": k, **eng.engine_info(k, t), "available": _engine_unavailable(k)} for k in eng.engines_for_tag(t)]}
            for t in eng.OCR_TAGS]
    return {
        "tags": tags,
        "no_ocr": {t: counts.get(t, 0) for t in ("blank", "misc", UNKNOWN)},
        "engines": {k: {**v, "available": _engine_unavailable(k)} for k, v in eng.ENGINES.items()},
        "folders": [{**f, "options": eng.pipeline_options(f["folder"]), "tags": folder_tags(f["folder"])} for f in folder_status(book)],
        "availability": {k: _engine_unavailable(k) for k in (eng.KIMHANNOM, eng.PADDLEOCR, eng.ROW_PROJECTION, eng.VINTERN)},
        "paddle": eng.PADDLE.status(),
        "vintern": eng.VINTERN_WORKER.status(),
        "ppocrlabel": ppocrlabel_status(),
        "job": job_status(book),
        "failed": {k: _failed_entry(v) for k, v in (read_log(book).get("failed") or {}).items()},
        "cache": ocr_cache.counts(book),
    }


def _engine_unavailable(engine: str) -> str | None:
    """Lý do engine chưa dùng được, hoặc None."""
    if engine == eng.PADDLEOCR:
        return eng.PADDLE.unavailable_reason()
    if engine == eng.KIMHANNOM:
        return eng.kimhannom_available()
    if engine == eng.VINTERN:
        return eng.VINTERN_WORKER.unavailable_reason()
    import importlib.util
    missing = [m for m in ("cv2", "numpy", "scipy") if importlib.util.find_spec(m) is None]
    return f"Thiếu thư viện: {', '.join(missing)}. Chạy: pip install -r requirements.txt" if missing else None


# ---------------------------------------------------------------- làm sạch văn bản
_FOOTNOTE_RE = re.compile(r"\s*(\[\d+\]|\(\d+\))")


def clean_text(text: str, han: bool) -> str:
    """Làm sạch tối thiểu theo "Quy định về dữ liệu văn bản": bỏ chỉ mục chú thích [1], (1), gom khoảng trắng.

    Chữ Quốc ngữ được chuẩn hóa NFC. Phần còn lại (số trang, tiêu đề, chính tả) cần soát tay trong PPOCRLabel.
    """
    text = " ".join(text.split())
    if not han:
        text = unicodedata.normalize("NFC", _FOOTNOTE_RE.sub("", text)).strip()
    return text


# ---------------------------------------------------------------- OCR một trang
def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def raw_result(book: Book, number: int, engine: str, image: Path, use_cache: bool = True) -> tuple[list[dict], bool]:
    """Kết quả thô của engine cho một ảnh: lấy từ cache nếu ảnh này đã chạy với engine này (và ảnh, tham số không đổi),
    ngược lại gọi engine rồi ghi cache. Trả về (các box thô, True nếu lấy từ cache). Xem scripts/ocr_cache.py."""
    if engine == eng.KIMHANNOM:
        params = eng.KIMHANNOM_PARAMS
    elif engine == eng.PADDLEOCR:
        params = eng.PADDLE_PARAMS
    elif engine == eng.ROW_PROJECTION:
        params = eng.row_projection_params()
    else:
        raise ValueError(f"Engine không hợp lệ: {engine}")

    info = ocr_cache.image_info(image)
    if use_cache:
        hit = ocr_cache.load(book, engine, number, info, params)
        if hit is not None:
            events.log(f"{eng.PIPELINE_NAMES.get(engine, engine)}: {len(hit)} box (lấy từ cache)")
            return hit, True

    if engine == eng.KIMHANNOM:
        raw = eng.run_kimhannom(image, **params)
    elif engine == eng.PADDLEOCR:
        raw = eng.PADDLE.recognize(image)
    else:
        raw = eng.run_row_projection(image, book.cache_dir / "ocr" / "row_projection")
    events.log(f"{eng.PIPELINE_NAMES.get(engine, engine)}: {len(raw)} box")
    try:
        ocr_cache.save(book, engine, number, info, params, raw)
    except OSError:
        pass   # không ghi được cache (đĩa đầy, ...) không làm hỏng kết quả OCR
    return raw, False


def reads_row_text(engine: str, read_text: bool) -> bool:
    """Engine này tách hàng rồi đọc chữ từng box (row_projection khi bật đọc chữ; Vintern luôn đọc chữ)."""
    return engine in (eng.VINTERN, eng.ROW_PADDLEOCR) or (engine == eng.ROW_PROJECTION and read_text)


def row_text_engines(tag: str, engine: str = eng.ROW_PROJECTION, only: set[str] | None = None) -> list[str]:
    """Engine đọc chữ trong box của nguồn tách hàng `engine` cho nhãn này: Kim Hán Nôm cho box vào ch/, còn lại Vintern-1B
    (PaddleOCR-VL nếu nguồn là row_paddleocr). `only`: chỉ tính các thư mục này (chạy riêng một thư mục thì không cần engine của thư mục kia)."""
    folders = folders_for(tag)[:2] if tag == "ch_vi" else folders_for(tag)[:1]
    return [eng.KIMHANNOM if f == "ch" else eng.row_reader(engine) for f in folders if only is None or f in only]


def _row_split(raw: list[dict], tag: str, alternate: bool = True) -> dict[str, list[dict]]:
    """Box của row_projection chia về các thư mục: ch_vi lẻ/chẵn -> ch/vi (`alternate` False: mỗi thư mục nhận mọi hàng),
    nhãn khác tất cả vào thư mục của nhãn."""
    return eng.rows_to_scripts(raw, alternate=alternate) if tag == "ch_vi" else {folders_for(tag)[0]: raw}


def _pkey(points) -> tuple:
    """Khoá so khớp một box theo 4 điểm (làm tròn), dùng để ghép box trong Label.txt với box vừa tách/đọc."""
    return tuple((int(round(x)), int(round(y))) for x, y in points)


def _text_status(text: str) -> str:
    return "ok" if text.strip() else "empty"


def _row_text_params(tag: str) -> dict:
    """Khoá cache chữ trong box. KHÔNG gồm engine đọc chữ Quốc ngữ: bản ghi của box ngoài ch có trường `reader` riêng (reader_key), để
    đổi giữa PaddleOCR-VL và Vintern chỉ đọc lại phần Quốc ngữ, không gọi lại API Kim Hán Nôm cho hàng Nôm."""
    return {"tag": tag, "row_projection": eng.row_projection_params(), "kimhannom": eng.KIMHANNOM_CROP_PARAMS}


def reader_key(reader: str) -> str:
    """Định danh engine đọc chữ Quốc ngữ + tham số của nó (đổi mô hình/prompt/tham số thì chữ cũ không còn khớp)."""
    import hashlib
    params = eng.PADDLE_LINE_PARAMS if reader == eng.PADDLEOCR else eng.VINTERN_PARAMS
    return f"{reader}:{hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]}"


def _load_row_records(book: Book, number: int, info: dict, params: dict, size: tuple[int, int],
                      reader: str = eng.VINTERN) -> dict[tuple, dict]:
    """Cache chữ trong box của một trang: {(thư mục, khoá điểm): bản ghi {folder, text, points, status, ...}}.

    Bản ghi cũ chưa có `status` (cache trước khi có trạng thái từng box): có chữ là ok, không chữ là empty. Bản ghi của box ngoài ch chỉ
    được dùng khi do đúng engine đọc chữ hiện tại đọc (`reader` khớp reader_key(reader)); bản của engine khác bị bỏ qua và đọc lại."""
    out: dict[tuple, dict] = {}
    wanted_reader = reader_key(reader)
    for b in ocr_cache.load(book, eng.ROW_TEXT, number, info, params) or []:
        try:
            key = (b["folder"], _pkey(eng.clamp_points(b["points"], *size)))
        except (KeyError, TypeError, ValueError):
            continue
        if b["folder"] != "ch" and b.get("reader") != wanted_reader:
            continue
        out[key] = {**b, "status": b.get("status") or _text_status(str(b.get("text", "")))}
    return out


def row_text_pass(book: Book, number: int, tag: str, image: Path, use_cache: bool = True, first: bool = True,
                  wanted: dict | None = None, attempt: int = 1, reader: str = eng.VINTERN,
                  alternate: bool = True) -> tuple[dict[str, list[dict]], dict]:
    """MỘT lượt đọc chữ trong các box của row_projection (Kim Hán Nôm cho ch, Vintern-1B cho box còn lại). Chỉ đọc chữ,
    toạ độ box là của row_projection.

    Mỗi box được xử lý độc lập và có bản ghi riêng trong cache/ocr/row_text/<NNN>.json với `status`:
      ok     đã có chữ (lần sau dùng lại, không gọi API/mô hình)
      empty  engine không trả ra chữ (lượt đầu của lần chạy sau đọc lại; các lượt thử lại trong cùng một trang thì bỏ qua)
      error  engine báo lỗi (kèm `error`, `code`, `attempts`); lượt thử lại đọc lại đúng các box này
    Cache được ghi sau từng box nên dừng giữa chừng không mất phần đã đọc, và lỗi của một box không chặn các box còn lại.
    `first` False (lượt thử lại) luôn dùng cache. `wanted`: {thư mục: tập khoá điểm cần đọc | None = tất cả}; thư mục hoặc box
    không nằm trong đó không bị đọc (đã có chữ). EngineConfigError (thiếu TOKEN, chưa nạp mô hình) được ném lên ngay.
    Trả về ({thư mục: [{text, points, status, error?, code?}]}, {reads, raw_cached, errors})."""
    from PIL import Image

    info = ocr_cache.image_info(image)
    params = _row_text_params(tag)
    with Image.open(image) as im:
        size = im.size
    stored = _load_row_records(book, number, info, params, size, reader)
    raw, raw_cached = raw_result(book, number, eng.ROW_PROJECTION, image, use_cache or not first)

    def save() -> None:
        try:
            ocr_cache.save(book, eng.ROW_TEXT, number, info, params, list(stored.values()))
        except OSError:
            pass   # không ghi được cache (đĩa đầy, ...) không làm hỏng kết quả OCR

    page = None
    reads, errors = 0, []
    out: dict[str, list[dict]] = {}
    split = _row_split(raw, tag, alternate)
    total_boxes = sum(len(v) for f, v in split.items() if wanted is None or wanted.get(f, "skip") != "skip")
    done_boxes = 0
    for folder, boxes in split.items():
        want = None if wanted is None else wanted.get(folder, "skip")
        rows = []
        for index, b in enumerate(boxes, 1):
            if want != "skip":
                events.check_cancel()
                events.progress(done_boxes, total_boxes, f"{folder}/ box {index}/{len(boxes)}")
                done_boxes += 1
            points = eng.clamp_points(b["points"], *size)
            pk = _pkey(points)
            rec = stored.get((folder, pk))
            if want == "skip" or (want is not None and pk not in want):
                rows.append({"text": "", "points": points, "status": "skipped"})
                continue
            if rec is not None and not (first and not use_cache) and (
                    rec["status"] == "ok" or (not first and rec["status"] == "empty")):
                rows.append({"text": str(rec.get("text", "")), "points": points, "status": rec["status"]})
                continue
            if page is None:
                page = eng.load_page_image(image)
            reads += 1
            try:
                started = time.perf_counter()
                text = eng.read_row_text(page, folder, points, reader)
                row = {"text": text, "points": points, "status": _text_status(text)}
                events.log(f"{folder}/ box {index}/{len(boxes)} ({eng.PIPELINE_NAMES[eng.KIMHANNOM if folder == 'ch' else reader]}, "
                           f"{time.perf_counter() - started:.1f}s): {text[:60] or '(không đọc ra chữ)'}", "info" if text else "warn")
            except eng.EngineConfigError:
                save()
                raise
            except Exception as e:   # engine lỗi/ảnh cắt hỏng: ghi lại, các box khác vẫn đọc tiếp
                code = getattr(e, "code", None)
                message = str(e) if isinstance(e, eng.EngineError) else f"{type(e).__name__}: {e}"
                events.log(f"{folder}/ box {index}/{len(boxes)}: lỗi: {message}", "error")
                row = {"text": "", "points": points, "status": "error", "error": message, "code": code, "attempts": attempt}
                errors.append({"folder": folder, "box": index, "message": message, "code": code})
            stored[(folder, pk)] = {"folder": folder, **row, **({} if folder == "ch" else {"reader": reader_key(reader)})}
            save()
            rows.append(row)
        out[folder] = rows
    return out, {"reads": reads, "raw_cached": raw_cached, "errors": errors}


def effective_spread(book: Book, number: int, tag: str, width: int, height: int, spread: int | None = None) -> int:
    """Số trang sách trong ảnh (1 hoặc 2) dùng khi lọc nhiễu và đánh số box của trang ch.

    Ưu tiên `spread` truyền vào, rồi nhãn gán tay trong phan_loai.json ("spread"); chưa gán thì với nhãn ch tự đoán theo tỉ lệ ảnh
    (ảnh nằm ngang là 2 trang), còn nhãn khác (ch_vi là bản chụp 1 trang theo quy định) luôn là 1."""
    if spread in page_cache.SPREADS:
        return spread
    stored = page_cache.page_spreads(book).get(number)
    if stored:
        return stored
    return eng.guess_spread(width, height) if tag == "ch" else 1


def _ocr_page_once(book: Book, number: int, tag: str, engine: str, image: Path, use_cache: bool, read_text: bool,
                   wanted: dict | None, first: bool = True, attempt: int = 1,
                   spread: int | None = None, alternate: bool = True) -> tuple[dict[str, list[dict]], bool, dict]:
    """Một lượt OCR một ảnh: ({thư mục: [box PPOCRLabel]}, lấy_từ_cache, báo cáo). Lỗi của cả trang (engine cả trang, ảnh hỏng) được ném lên;
    lỗi từng box của row_projection nằm trong báo cáo `errors` (box lỗi để chữ rỗng). Báo cáo còn có `spread` (số trang trong ảnh) và
    `noise_removed` (số box nhiễu ở giữa đã xoá khỏi bản chụp 2 trang). `spread`: ghi đè nhãn trong cache (chủ yếu để kiểm thử)."""
    from PIL import Image

    report = {"status": "ok", "errors": [], "empty_boxes": 0, "ok_boxes": 0}
    reader = eng.row_reader(engine)
    if engine in (eng.VINTERN, eng.ROW_PADDLEOCR):   # tách hàng bằng row_projection rồi đọc chữ từng hàng: đúng đường row_projection có đọc chữ
        engine, read_text = eng.ROW_PROJECTION, True
    if engine == eng.ROW_PROJECTION and read_text:
        split, stats = row_text_pass(book, number, tag, image, use_cache, first, wanted, attempt, reader, alternate)
        cached = stats["reads"] == 0 and stats["raw_cached"]
        report["errors"] = stats["errors"]
        rows = [r for boxes in split.values() for r in boxes]
        report["ok_boxes"] = sum(r["status"] == "ok" for r in rows)
        report["empty_boxes"] = sum(r["status"] == "empty" for r in rows)
        if stats["errors"]:
            report["status"] = "partial" if report["ok_boxes"] or report["empty_boxes"] else "failed"
    else:
        raw, cached = raw_result(book, number, engine, image, use_cache)
        if engine == eng.PADDLEOCR:   # bỏ số trang, đầu/chân trang, chú thích, bảng, tranh (lọc sau cache nên đổi bộ lọc không phải chạy lại)
            raw = [b for b in raw if b.get("label") not in eng.PADDLE_SKIP_LABELS]

        if engine == eng.ROW_PROJECTION:
            split = _row_split(raw, tag, alternate)
        elif tag == "ch_vi":
            split = {"ch": [b for b in raw if eng.is_han_text(b["text"])],
                     "vi": [b for b in raw if not eng.is_han_text(b["text"])]}
            if engine == eng.KIMHANNOM:   # API chuyên cho chữ Nôm: phần không phải chữ Hán là nhiễu, không đưa vào vi/
                split["vi"] = []
        else:
            split = {folders_for(tag)[0]: raw}
        report["ok_boxes"] = sum(len(boxes) for boxes in split.values())

    with Image.open(image) as im:
        width, height = im.size
    pages_in_image = effective_spread(book, number, tag, width, height, spread)
    report["spread"], report["noise_removed"] = pages_in_image, 0
    result: dict[str, list[dict]] = {}
    for folder, boxes in split.items():
        if engine != eng.ROW_PROJECTION:
            boxes = [b for b in boxes if b["text"].strip()]
        if folder == "ch" and engine != eng.ROW_PROJECTION:
            if pages_in_image == 2:   # bản chụp 2 trang: bỏ box nhiễu ở giữa (gáy sách, tiêu đề, số trang) rồi mới đánh số
                boxes, removed = eng.filter_spread_noise(boxes, width)
                report["noise_removed"] += len(removed)
            vertical = _median([_size(b)[1] for b in boxes]) >= _median([_size(b)[0] for b in boxes])
            if pages_in_image == 2:   # trang trái đánh hết rồi mới tới trang phải
                boxes = eng.order_boxes_spread(boxes, vertical_rtl=vertical, width=width)
            else:
                boxes = eng.order_boxes(boxes, vertical_rtl=vertical)   # Nôm: phải sang trái, trên xuống
        elif engine == eng.KIMHANNOM:
            boxes = eng.order_boxes(boxes, vertical_rtl=False)           # còn lại: trái sang phải, trên xuống
        # paddleocr đã trả theo thứ tự đọc của mô hình; row_projection theo thứ tự từ trên xuống
        out = []
        for b in boxes:
            pts = eng.clamp_points(b["points"], width, height)
            text = clean_text(b["text"], han=(folder == "ch"))
            if engine != eng.ROW_PROJECTION and not text:
                continue
            out.append(label_io.make_box(pts, text))
        result[folder] = out
    return result, cached, report


def ocr_page(book: Book, number: int, tag: str, engine: str, image: Path,
             use_cache: bool = True, read_text: bool = True, wanted: dict | None = None,
             alternate: bool = True) -> tuple[dict[str, list[dict]], bool]:
    """Chạy (hoặc lấy từ cache) engine trên một ảnh, MỘT lượt, không thử lại. Trả về ({thư mục: [box PPOCRLabel]}, lấy_từ_cache).

    `read_text` chỉ có tác dụng với row_projection: đọc chữ trong từng box đã tách (xem row_text_pass); tắt thì box có chữ rỗng.
    Với row_projection mà mọi box đã đọc đều lỗi thì ném EngineError (đọc được một phần thì trả phần đó); dùng cho PPOCRLabel,
    còn bước OCR của app dùng ocr_page_report (thử lại, báo cáo từng box)."""
    result, cached, report = _ocr_page_once(book, number, tag, engine, image, use_cache, read_text, wanted,
                                                  alternate=alternate)
    if report["status"] == "failed":
        first_error = report["errors"][0]
        raise eng.EngineError(first_error["message"], code=first_error.get("code"))
    return result, cached


def ocr_page_report(book: Book, number: int, tag: str, engine: str, image: Path, use_cache: bool = True, read_text: bool = True,
                    wanted: dict | None = None, attempts: int = MAX_PAGE_ATTEMPTS, on_event=None,
                    alternate: bool = True) -> tuple[dict, bool, dict]:
    """OCR một ảnh, tối đa `attempts` lượt: lỗi (cả trang hoặc từng box) thì nghỉ rồi thử lại; lượt sau chỉ đọc lại phần còn lỗi.

    Hết lượt vẫn lỗi thì KHÔNG ném lỗi: trả phần đọc được kèm báo cáo để nơi gọi bỏ qua trang này và chạy tiếp.
    Báo cáo: {status: ok|partial|failed, attempts, errors: [{folder, box, message, code}], empty_boxes, ok_boxes, auth}.
    `auth`: mọi lỗi còn lại đều là lỗi xác thực (TOKEN sai/hết hạn). EngineConfigError (thiếu TOKEN, chưa nạp mô hình)
    được ném lên: thử lại không giúp gì."""
    result, cached, report, errors = {}, False, None, []
    for attempt in range(1, attempts + 1):
        try:
            result, cached, report = _ocr_page_once(book, number, tag, engine, image, use_cache if attempt == 1 else True,
                                                    read_text, wanted, first=(attempt == 1), attempt=attempt,
                                                    alternate=alternate)
            errors = report["errors"]
        except eng.EngineConfigError:
            raise
        except Exception as e:   # lỗi cả trang: engine cả trang lỗi, ảnh hỏng, không tách được hàng, ...
            result, cached, report = {}, False, None
            message = str(e) if isinstance(e, eng.EngineError) else f"{type(e).__name__}: {e}"
            errors = [{"folder": None, "box": None, "message": message, "code": getattr(e, "code", None)}]
        if not errors:
            break
        if attempt < attempts:
            if on_event:
                on_event(f"Trang {number:03d}: lần {attempt}/{attempts} lỗi ({_summarize_errors(errors)}), thử lại")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    if report is None:
        report = {"status": "failed", "errors": errors, "empty_boxes": 0, "ok_boxes": 0}
    report = {**report, "attempts": attempt, "auth": bool(errors) and all(e.get("code") in eng.AUTH_ERROR_CODES for e in errors)}
    return result, cached and attempt == 1, report


def _summarize_errors(errors: list[dict]) -> str:
    """Một dòng gọn cho log: lỗi đầu tiên và số lỗi còn lại."""
    first = errors[0]
    where = f"{first['folder']}/box {first['box']}: " if first.get("folder") else ""
    return f"{where}{first['message']}" + (f" (+{len(errors) - 1} lỗi khác)" if len(errors) > 1 else "")


def merge_filled(existing_payload: str, new_boxes: list[dict]) -> tuple[list[dict], int]:
    """Điền chữ mới vào các box ĐANG RỖNG của một dòng Label.txt, ghép theo 4 điểm trùng đúng.

    Box đã có chữ (kể cả đã sửa tay), box người dùng đã dời/thêm/xoá, và thứ tự box giữ nguyên. Trả về (các box, số box đã điền)."""
    boxes = json.loads(existing_payload)
    fresh = {_pkey(b["points"]): b["transcription"] for b in new_boxes if b["transcription"].strip()}
    filled = 0
    for b in boxes:
        if not str(b.get("transcription", "")).strip():
            text = fresh.get(_pkey(b["points"]))
            if text:
                b["transcription"] = text
                filled += 1
    return boxes, filled


def _size(box: dict) -> tuple[float, float]:
    return eng._size(box)


# ---------------------------------------------------------------- tác vụ nền
_job_lock = threading.Lock()
_job: dict | None = None   # tác vụ OCR hiện tại hoặc vừa xong (một tác vụ toàn cục: PaddleOCR và uploads.json không an toàn đa luồng)


def job_status(book: Book | None = None) -> dict:
    with _job_lock:
        if _job is None or (book is not None and _job["book"] != norm(book.name)):
            return {"status": "none"}
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _job.items()}


def validate_choices(engines: dict) -> dict[str, str]:
    """Chuẩn hóa lựa chọn engine theo nhãn; nhãn thiếu lấy engine đề xuất. Giá trị skip nghĩa là không OCR nhãn đó."""
    chosen: dict[str, str] = {}
    for tag in eng.OCR_TAGS:
        value = engines.get(tag, eng.RECOMMENDED[tag])
        if value != eng.SKIP and value not in eng.engines_for_tag(tag):
            raise ValueError(f"Engine không hợp lệ cho nhãn {tag}: {value!r}")
        chosen[tag] = value
    return chosen


def validate_folders(folders) -> set[str] | None:
    """Tập thư mục cần OCR; None/rỗng = tất cả. Báo lỗi nếu có tên không phải thư mục có Label.txt."""
    if not folders:
        return None
    bad = [f for f in folders if f not in LABEL_FOLDERS]
    if bad:
        raise ValueError(f"Thư mục không hợp lệ: {', '.join(map(repr, bad))}. Hợp lệ: {', '.join(LABEL_FOLDERS)}")
    return set(folders)


def start_ocr(book: Book, engines: dict, overwrite: bool, use_cache: bool = True, read_text: bool = True,
              folders=None, boxes_only: bool = False, alternate: bool = True) -> None:
    """Chạy OCR nền. `folders`: chỉ OCR các thư mục này (vd. ["ch"]), không nhất thiết chạy hết; None = tất cả thư mục có trang.
    `boxes_only`: chỉ lấy box, mọi chữ để trống (kể cả khi engine đọc luôn chữ như Kim Hán Nôm, PaddleOCR-VL).
    `alternate`: trang ch_vi tách hàng bằng row_projection có lọc xen kẽ lẻ/chẵn về ch/vi (mặc định); False thì ch/ và vi/ đều nhận mọi hàng."""
    global _job
    chosen = validate_choices(engines)
    only = validate_folders(folders)
    tags_present = set(page_tags(book).values())
    for tag, engine in chosen.items():
        if only is not None and not (set(folders_for(tag)) & only):
            continue   # nhãn này không nuôi thư mục nào được chọn: không cần engine của nó
        if engine != eng.SKIP and tag in tags_present:
            used = [engine] + ([eng.ROW_PROJECTION] if engine in (eng.VINTERN, eng.ROW_PADDLEOCR) else []) + (
                row_text_engines(tag, engine, only) if reads_row_text(engine, read_text) else [])
            for name in used:
                reason = _engine_unavailable(name)
                if reason:
                    extra = (" (đọc chữ trong box; bỏ tích \"Đọc chữ trong box\" để chỉ tách hàng)"
                             if name != engine and engine == eng.ROW_PROJECTION else " (đọc chữ trong box)" if name != engine else "")
                    raise ValueError(f"Engine {eng.ENGINE_LABELS[name]} (nhãn {tag}) chưa dùng được{extra}: {reason}")
    _begin_job(book)
    threading.Thread(target=_run, args=(book, chosen, overwrite, use_cache, read_text, only, boxes_only, alternate), daemon=True).start()


def _begin_job(book: Book) -> None:
    """Tạo tác vụ nền mới (mỗi lúc chỉ một); OcrBusy nếu đang có tác vụ chạy."""
    global _job
    with _job_lock:
        if _job is not None and _job["status"] == "running":
            raise OcrBusy("Đang chạy OCR một sách, hãy đợi xong rồi thử lại.")
        _job = {"book": norm(book.name), "status": "running", "done": 0, "total": 0, "skipped": 0, "cached": 0,
                "current": "Đang chuẩn bị…", "failed": [], "ok": 0, "log": [], "error": None, "started": time.time()}


def folder_tags(folder: str) -> list[str]:
    """Các nhãn trang có ảnh được chép vào thư mục này (ch ← ch, ch_vi; vi ← vi, ch_vi; intro/ref/toc ← nhãn cùng tên)."""
    return [t for t in eng.OCR_TAGS if folder in folders_for(t)]


def start_pipeline(book: Book, folder: str, box: str, text: str, overwrite: bool = False, use_cache: bool = True,
                   alternate: bool = True) -> None:
    """OCR RIÊNG một thư mục với tổ hợp chọn riêng "ai tìm box" và "ai đọc chữ" (xem ocr_engines.pipeline_problem):

      box = kimhannom | paddleocr | row_projection   tìm box (kimhannom, paddleocr đọc luôn chữ; row_projection chỉ tách hàng)
      box = existing                                 KHÔNG tìm box: giữ box đang có trong Label.txt, chỉ đọc chữ (text là engine đọc)
      text = kimhannom | paddleocr | vintern | none  engine đọc chữ; none là chỉ lấy box, chữ để trống

    `overwrite`: với box mới, ghi đè các ảnh đã có dòng trong Label.txt; với box đã có, đọc lại cả box đã có chữ (mặc định chỉ box còn trống)."""
    if folder not in LABEL_FOLDERS:
        raise ValueError(f"Thư mục không hợp lệ: {folder!r}. Hợp lệ: {', '.join(LABEL_FOLDERS)}")
    problem = eng.pipeline_problem(folder, box, text)
    if problem:
        raise ValueError(problem)
    if box == eng.BOX_EXISTING:
        reason = _engine_unavailable(text)
        if reason:
            raise ValueError(f"Engine {eng.PIPELINE_NAMES[text]} chưa dùng được: {reason}")
        _begin_job(book)
        threading.Thread(target=_run_read_boxes, args=(book, folder, text, overwrite), daemon=True).start()
        return
    engine = eng.pipeline_engine(box, text)
    chosen = {t: engine for t in folder_tags(folder)}
    for tag in chosen:
        if engine not in eng.engines_for_tag(tag):
            raise ValueError(f"Nguồn {eng.ENGINE_LABELS[engine]} không dùng được cho trang nhãn {tag}.")
    start_ocr(book, chosen, overwrite, use_cache, read_text=text != eng.TEXT_NONE, folders=[folder], boxes_only=text == eng.TEXT_NONE,
              alternate=alternate)


def _failed_entry(value) -> dict:
    """Mục `failed` của ocr_log.json: {status, attempts, message}. Log cũ chỉ có chuỗi lỗi."""
    if isinstance(value, dict):
        return {"status": value.get("status") or "failed", "attempts": value.get("attempts"),
                "message": str(value.get("message") or "")}
    return {"status": "failed", "attempts": None, "message": str(value)}


def _job_log(line: str) -> None:
    """Thêm một dòng vào log chạy của job (hiện trên giao diện) và cập nhật dòng trạng thái hiện tại."""
    with _job_lock:
        if _job is not None:
            _job["log"].append(f"{datetime.now():%H:%M:%S} {line}")
            del _job["log"][:-JOB_LOG_LINES]
            _job["current"] = line


def _empty_keys(payload: str) -> set | None:
    """Khoá điểm của các box có chữ rỗng trong một dòng Label.txt; None nếu dòng không đọc được."""
    try:
        boxes = json.loads(payload)
        return {_pkey(b["points"]) for b in boxes if not str(b.get("transcription", "")).strip()}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _build_tasks(book: Book, chosen: dict[str, str], overwrite: bool,
                 read_text: bool = True, folders: set[str] | None = None) -> tuple[list[tuple], int]:
    """[(số trang, nhãn, engine, ảnh, [thư mục cần ghi], wanted)], số trang bỏ qua vì đã đủ.

    Một thư mục cần làm khi chưa có dòng trong Label.txt hoặc đang ghi đè. Riêng row_projection có đọc chữ (không ghi đè): dòng đã
    có nhưng còn box chữ rỗng cũng cần làm, và chỉ các box rỗng đó được đọc (wanted[thư mục] = tập khoá điểm; None = tất cả box);
    box đã có chữ không bị đụng tới, kể cả khi đã sửa tay. Trang đủ chữ thì bỏ qua. `folders`: chỉ làm các thư mục này (None = tất cả);
    thư mục ngoài tập đó không bị đụng tới, kể cả khi trang ch_vi có cả hai."""
    existing = {f: label_io.read_labels(book.dir / f / label_io.LABEL_NAME) for f in LABEL_FOLDERS}
    tasks, skipped = [], 0
    for number, tag, engine in ((n, t, chosen.get(t)) for n, t in sorted(page_tags(book).items())):
        if engine is None or engine == eng.SKIP:
            continue
        fill = reads_row_text(engine, read_text) and not overwrite
        need, wanted = [], {}
        for folder in folders_for(tag):
            if folders is not None and folder not in folders:
                continue
            key = label_io.image_key(folder, _image_name(book, number))
            if overwrite or key not in existing[folder]:
                need.append(folder)
                wanted[folder] = None
            elif fill:
                empties = _empty_keys(existing[folder][key])
                if empties:
                    need.append(folder)
                    wanted[folder] = empties
        image = book.dir / folders_for(tag)[0] / _image_name(book, number)
        if not need:
            skipped += 1
        elif image.is_file():
            tasks.append((number, tag, engine, image, need, wanted))
    return tasks, skipped


def _run(book: Book, chosen: dict[str, str], overwrite: bool, use_cache: bool = True, read_text: bool = True,
         folders: set[str] | None = None, boxes_only: bool = False, alternate: bool = True) -> None:
    pending: dict[str, dict[str, list[dict]]] = {}
    log = read_log(book)
    log_pages = log.setdefault("pages", {})
    failed = {k: _failed_entry(v) for k, v in (log.get("failed") or {}).items()}   # giữ trang lỗi từ các lần chạy trước tới khi OCR được
    auth_streak = 0
    try:
        sync_folders(book)
        existing = {f: label_io.read_labels(book.dir / f / label_io.LABEL_NAME) for f in LABEL_FOLDERS}
        tasks, skipped = _build_tasks(book, chosen, overwrite, read_text, folders)
        with _job_lock:
            _job.update(total=len(tasks), skipped=skipped)

        def flush():
            for folder, labels in pending.items():
                if labels:
                    label_io.update_folder(book.dir / folder, labels, state=0)
            pending.clear()
            log.update(updated=datetime.now().isoformat(), failed=failed)
            write_json_atomic(book.cache_dir / "ocr_log.json", log)

        for i, (number, tag, engine, image, need, wanted) in enumerate(tasks, 1):
            with _job_lock:
                _job.update(done=i - 1, current=f"Trang {number:03d} ({tag}, {eng.ENGINES[engine]['label']})")
            # lỗi cấu hình (thiếu TOKEN, chưa nạp mô hình) được ném lên và dừng job; lỗi khác nằm trong báo cáo
            result, from_cache, report = ocr_page_report(book, number, tag, engine, image, use_cache, read_text, wanted,
                                                         on_event=_job_log, alternate=alternate)
            status, errors = report["status"], report["errors"]
            if report.get("spread") == 2 and report.get("noise_removed"):
                _job_log(f"Trang {number:03d}: bản chụp 2 trang, đã xoá {report['noise_removed']} box nhiễu ở giữa")
            message = _summarize_errors(errors) if errors else ""
            if errors:
                failed[str(number)] = {"status": status, "attempts": report["attempts"], "message": message}
                with _job_lock:
                    _job["failed"].append({"page": number, "status": status, "attempts": report["attempts"], "error": message})
                _job_log(f"Trang {number:03d}: " + ("bỏ qua" if status == "failed" else "chỉ đọc được một phần") +
                         f" sau {report['attempts']} lần thử: {message}")
            else:
                failed.pop(str(number), None)
                with _job_lock:
                    _job["ok"] += 1
            auth_streak = auth_streak + 1 if errors and report["auth"] else 0

            empty_boxes = 0
            if status != "failed":   # trang lỗi hoàn toàn: không ghi gì để lần sau làm lại
                for folder in need:
                    key = label_io.image_key(folder, _image_name(book, number))
                    boxes = result.get(folder, [])
                    if boxes_only:   # chỉ lấy box: bỏ chữ engine đã đọc, để trống cho bước đọc chữ sau
                        boxes = [label_io.make_box(b["points"], "") for b in boxes]
                    payload = existing[folder].get(key)
                    if wanted.get(folder) is not None and payload is not None:   # điền vào box trống của dòng đã có
                        try:
                            merged, filled = merge_filled(payload, boxes)
                        except (ValueError, KeyError, TypeError):
                            merged, filled = [], 0
                        empty_boxes += sum(1 for b in merged if not str(b.get("transcription", "")).strip())
                        if filled:
                            pending.setdefault(folder, {})[key] = merged
                    else:
                        empty_boxes += sum(1 for b in boxes if not b["transcription"].strip())
                        pending.setdefault(folder, {})[key] = boxes
                if empty_boxes and not errors:
                    _job_log(f"Trang {number:03d}: còn {empty_boxes} box chưa có chữ (engine không đọc ra chữ)")
            if from_cache:
                with _job_lock:
                    _job["cached"] += 1
            log_pages[str(number)] = {"tag": tag, "engine": engine, "at": datetime.now().isoformat(), "cached": from_cache,
                                      "status": status, "attempts": report["attempts"], "empty_boxes": empty_boxes,
                                      "errors": errors, "boxes": {f: len(result.get(f, [])) for f in need}}
            if auth_streak >= MAX_AUTH_FAIL_STREAK:
                raise RuntimeError(f"Dừng vì {auth_streak} trang liên tiếp lỗi xác thực Kim Hán Nôm ({message}). Kiểm tra TOKEN trong "
                                   "file .env (còn hạn không, đã dán đúng chưa), rồi chạy lại: phần đã đọc được đã được lưu.")
            if i % FLUSH_EVERY == 0:
                flush()
        flush()
        with _job_lock:
            _job.update(status="done", done=len(tasks), current="")
    except Exception as e:
        try:
            for folder, labels in pending.items():
                if labels:
                    label_io.update_folder(book.dir / folder, labels, state=0)
            log.update(updated=datetime.now().isoformat(), failed=failed)
            write_json_atomic(book.cache_dir / "ocr_log.json", log)
        except Exception:
            pass
        with _job_lock:
            _job.update(status="error", error=str(e), current="")


def _run_read_boxes(book: Book, folder: str, reader: str, overwrite: bool) -> None:
    """Tác vụ nền "chỉ đọc chữ trên box đã có": mỗi ảnh có dòng trong <thư mục>/Label.txt được đọc lại chữ ở các box cần đọc, toạ độ box giữ
    nguyên. Mặc định chỉ box đang trống (chữ đã có, kể cả đã sửa tay, không bị đụng tới); `overwrite` đọc lại mọi box. Lưu sau từng
    FLUSH_EVERY ảnh nên dừng giữa chừng không mất phần đã đọc. Box lỗi để nguyên chữ cũ và ghi lại trong danh sách lỗi."""
    pending: dict[str, list[dict]] = {}
    folder_dir = book.dir / folder
    auth_streak = 0

    def flush():
        if pending:
            label_io.update_folder(folder_dir, dict(pending), state=0)
            pending.clear()
    try:
        labels = label_io.read_labels(folder_dir / label_io.LABEL_NAME)
        tasks = []
        for key in sorted(labels, key=label_io.page_number):
            try:
                boxes = json.loads(labels[key])
            except ValueError:
                continue
            need = [i for i, b in enumerate(boxes) if overwrite or not str(b.get("transcription", "")).strip()]
            image = folder_dir / key.split("/", 1)[-1]
            if need and image.is_file():
                tasks.append((key, boxes, need, image))
        with _job_lock:
            _job.update(total=len(tasks), skipped=len(labels) - len(tasks))
        if not labels:
            _job_log(f"{folder}/ chưa có ảnh nào có box trong Label.txt: hãy phát hiện box trước.")
        for i, (key, boxes, need, image) in enumerate(tasks, 1):
            number = label_io.page_number(key)
            with _job_lock:
                _job.update(done=i - 1, current=f"{folder}/ trang {number:03d}: đọc chữ trong {len(need)} box ({eng.PIPELINE_NAMES[reader]})")
            page = eng.load_page_image(image)
            errors, filled = [], 0
            for index in need:
                for attempt in (1, 2):
                    try:
                        text = clean_text(eng.read_box_text(page, folder, boxes[index]["points"], reader), han=(folder == "ch"))
                        if text:
                            boxes[index]["transcription"] = text
                            filled += 1
                        break
                    except eng.EngineConfigError:
                        flush()
                        raise
                    except Exception as e:   # box lỗi: giữ chữ cũ, các box khác vẫn đọc tiếp
                        message = str(e) if isinstance(e, eng.EngineError) else f"{type(e).__name__}: {e}"
                        code = getattr(e, "code", None)
                        if attempt == 2 or code in eng.AUTH_ERROR_CODES:
                            errors.append({"box": index + 1, "message": message, "code": code})
                            break
                        time.sleep(RETRY_BACKOFF_SECONDS)
            pending[key] = boxes
            if errors:
                message = f"box {errors[0]['box']}: {errors[0]['message']}" + (f" (+{len(errors) - 1} lỗi khác)" if len(errors) > 1 else "")
                with _job_lock:
                    _job["failed"].append({"page": number, "status": "partial", "attempts": 2, "error": message})
                _job_log(f"Trang {number:03d}: {len(errors)}/{len(need)} box lỗi: {message}")
            else:
                with _job_lock:
                    _job["ok"] += 1
            if errors and all(e["code"] in eng.AUTH_ERROR_CODES for e in errors):
                auth_streak += 1
            else:
                auth_streak = 0
            if auth_streak >= MAX_AUTH_FAIL_STREAK:
                flush()
                raise RuntimeError(f"Dừng vì {auth_streak} ảnh liên tiếp lỗi xác thực Kim Hán Nôm ({message}). Phần đã đọc được đã được lưu. "
                                   "Kiểm tra TOKEN trong .env hoặc đợi một lúc rồi chạy lại (server có thể đang giới hạn tốc độ).")
            if i % FLUSH_EVERY == 0:
                flush()
        flush()
        with _job_lock:
            _job.update(status="done", done=len(tasks), current="")
    except Exception as e:
        try:
            flush()
        except Exception:
            pass
        with _job_lock:
            _job.update(status="error", error=str(e), current="")


# ---------------------------------------------------------------- PPOCRLabel
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# PPOCRLabel chạy từ mã đã chép vào dự án (external/PPOCRLabel), không cần `pip install PPOCRLabel`.
# Chỉ cần PyQt5 (giao diện) cùng numpy/cv2; nhận dạng chạy qua 3 engine của dự án (scripts/engine_bridge.py), nên PPOCRLabel
# không còn cần paddle/paddleocr (PaddleOCR-VL chỉ cần khi bạn chọn engine đó).
VENDORED = PROJECT_ROOT / "external" / "PPOCRLabel" / "PPOCRLabel.py"
REQUIRED_MODULES = ("PyQt5", "cv2", "numpy")
INSTALL_HINT = "Chạy: pip install -r requirements-ocr.txt (trong môi trường chạy app)."
_ppocr_lock = threading.Lock()
_ppocr: dict | None = None   # {"proc": Popen, "folder": str, "book": str, "path": str, "started": float}


def _dotenv_value(name: str) -> str | None:
    """Giá trị `name` từ biến môi trường, hoặc từ file .env của dự án (không cần khởi động lại shell)."""
    if os.getenv(name):
        return os.getenv(name)
    try:
        for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _project_path(value: str) -> str:
    """Đường dẫn tương đối tính từ thư mục gốc dự án (không phụ thuộc thư mục đang chạy app); tuyệt đối thì giữ nguyên."""
    path = Path(value)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


# PPOCRLabel được mở qua scripts/ppocrlabel_boot.py (nạp torch trước để tránh WinError 1114, rồi chạy bản trong external/).
BOOT_FILE = Path(__file__).resolve().parent / "ppocrlabel_boot.py"


def _candidate_pythons() -> list[str]:
    """Các Python có thể chạy PPOCRLabel, theo thứ tự ưu tiên: PPOCRLABEL_PYTHON (nếu đặt), Python đang chạy app, .venv của dự án."""
    out: list[str] = []
    custom = _dotenv_value("PPOCRLABEL_PYTHON")
    if custom:
        out.append(_project_path(custom))
    out.append(sys.executable)
    for name in (("Scripts", "python.exe"), ("bin", "python")):
        venv_python = PROJECT_ROOT / ".venv" / name[0] / name[1]
        if venv_python.is_file():
            out.append(str(venv_python))
    unique: list[str] = []
    for p in out:
        if p and p not in unique:
            unique.append(p)
    return unique


def _missing_modules(python: str) -> list[str]:
    """Các thư viện PPOCRLabel cần mà Python này chưa có."""
    if os.path.normcase(python) == os.path.normcase(sys.executable):   # Python đang chạy app: kiểm tra ngay, không cần tiến trình con
        import importlib.util
        return [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    code = ("import importlib.util as u;"
            f"print(','.join(m for m in {REQUIRED_MODULES!r} if u.find_spec(m) is None))")
    try:
        out = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return list(REQUIRED_MODULES)
    return [m for m in out.split(",") if m]


def _ppocrlabel_lang() -> str:
    """Giá trị --lang của PPOCRLabel: PPOCRLABEL_LANG, hoặc (tương thích cấu hình cũ) `--lang X` trong PPOCRLABEL_CMD, mặc định en.

    ch = giao diện tiếng Trung; en và mọi mã khác (vd. vi) = giao diện tiếng Anh. Chỉ ảnh hưởng giao diện: PPOCRLabel không còn mô hình
    nhận dạng riêng, nhận dạng đi qua 3 engine của dự án (scripts/engine_bridge.py)."""
    lang = _dotenv_value("PPOCRLABEL_LANG")
    if not lang:
        match = re.search(r"--lang[ =]+(\S+)", _dotenv_value("PPOCRLABEL_CMD") or "")
        lang = match.group(1).strip("\"'") if match else None
    return lang if lang and re.fullmatch(r"[A-Za-z_]{2,20}", lang) else DEFAULT_UI_LANG


def _ppocrlabel_command() -> list[str] | None:
    """Lệnh mở PPOCRLabel: [python, ppocrlabel_boot.py, --lang <ngôn ngữ giao diện>]. None nếu thiếu mã trong external/.

    Python: ứng viên đầu tiên có đủ thư viện; không có ứng viên nào đủ thì dùng ứng viên đầu để báo thiếu gì.
    Giao diện: PPOCRLabel chỉ có tiếng Trung (ch, mặc định của chính nó) và tiếng Anh (en); mặc định en, xem _ppocrlabel_lang().
    """
    if not VENDORED.is_file():
        return None
    candidates = _candidate_pythons()
    python = next((p for p in candidates if not _missing_modules(p)), candidates[0])
    return [python, str(BOOT_FILE), "--lang", _ppocrlabel_lang()]


def ppocrlabel_running() -> dict:
    """Nhẹ (không chạy tiến trình con), dùng cho vòng hỏi trạng thái mỗi giây."""
    with _ppocr_lock:
        if _ppocr is not None and _ppocr["proc"].poll() is None:
            return {"running": True, "folder": _ppocr["folder"], "book": _ppocr["book"]}
    return {"running": False}


def ppocrlabel_status() -> dict:
    cmd = _ppocrlabel_command()
    with _ppocr_lock:
        running = _ppocr is not None and _ppocr["proc"].poll() is None
        current = {"folder": _ppocr["folder"], "book": _ppocr["book"]} if running else {}
    if not cmd:
        return {"available": False, "running": False,
                "hint": f"Thiếu mã PPOCRLabel trong dự án ({VENDORED.relative_to(PROJECT_ROOT)})."}
    missing = _missing_modules(cmd[0])
    if missing:
        return {"available": True, "ready": False, "running": running, **current, "command": cmd[0],
                "hint": f"Môi trường Python ({cmd[0]}) thiếu thư viện cho PPOCRLabel: {', '.join(missing)}. {INSTALL_HINT}"}
    return {"available": True, "ready": True, "running": running, **current, "command": cmd[0]}


def _log_tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-lines:])
    except OSError:
        return ""


def open_ppocrlabel(book: Book, folder: str) -> dict:
    """Mở PPOCRLabel. Chỉ mở một cửa sổ tại một thời điểm (hai cửa sổ cùng sửa Label.txt sẽ ghi đè nhau).

    PPOCRLabel được mở sẵn đúng thư mục ảnh qua tham số --image_dir (như đã bấm File -> Open Dir); hàm trả về đường dẫn thư mục đó. Nếu tiến trình thoát ngay (thiếu thư viện, ...) thì báo lỗi kèm đoạn cuối của log.
    """
    global _ppocr
    if folder not in LABEL_FOLDERS:
        raise ValueError(f"Thư mục không hợp lệ: {folder}")
    directory = book.dir / folder
    if not _folder_images(book, folder):
        raise ValueError(f"Thư mục {folder}/ chưa có ảnh. Hãy chạy OCR (hoặc gán nhãn trang) trước.")
    if job_status(book)["status"] == "running":
        raise OcrBusy("Đang chạy OCR, hãy đợi xong rồi mở PPOCRLabel (tránh ghi đè Label.txt đang được tạo).")
    cmd = _ppocrlabel_command()
    if not cmd:
        raise FileNotFoundError(ppocrlabel_status()["hint"])
    if _missing_modules(cmd[0]):
        raise RuntimeError(ppocrlabel_status()["hint"])

    with _ppocr_lock:
        if _ppocr is not None and _ppocr["proc"].poll() is None:
            raise OcrBusy(f"PPOCRLabel đang mở (thư mục {_ppocr['folder']}/). Đóng cửa sổ đó trước khi mở thư mục khác.")
        log_path = book.cache_dir / "ppocrlabel.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "wb") as log:
            proc = subprocess.Popen([*cmd, "--image_dir", str(directory)], cwd=str(directory), stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, close_fds=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # không bật cửa sổ console đen
        try:
            proc.wait(timeout=EARLY_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass   # còn chạy sau vài giây: coi như đã mở được
        if proc.poll() is not None:
            tail = _log_tail(log_path)
            raise RuntimeError(f"PPOCRLabel thoát ngay (mã {proc.returncode})." + (f"\n{tail}" if tail else ""))
        _ppocr = {"proc": proc, "folder": folder, "book": norm(book.name), "path": str(directory), "started": time.time()}
    return {"folder": folder, "path": str(directory)}
