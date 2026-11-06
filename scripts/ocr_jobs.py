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
import shlex
import shutil
import subprocess
import threading
import time
import unicodedata
import re
from datetime import datetime
from pathlib import Path

from . import label_io, ocr_cache, ocr_engines as eng, page_cache
from .library import SUBMISSION_FOLDERS, UNKNOWN, Book, find_page_image, norm, write_json_atomic

# nhãn trang -> các thư mục nhận ảnh (ch_vi vào cả hai)
FOLDER_TAGS = {"ch": ["ch"], "vi": ["vi"], "ch_vi": ["ch", "vi"], "intro": ["intro"], "ref": ["ref"],
               "toc": ["toc"], "blank": ["blank"], "misc": ["misc"]}
LABEL_FOLDERS = ("ch", "vi", "intro", "ref", "toc")   # thư mục có Label.txt / fileState.txt
FLUSH_EVERY = 5            # ghi Label.txt sau mỗi bấy nhiêu ảnh, dừng giữa chừng không mất hết
MAX_CONSECUTIVE_FAILS = 3  # lỗi liên tiếp (vd. sai TOKEN) thì dừng, không gọi tiếp hàng trăm lần


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
        out.append({"folder": folder, "path": str(directory), "images": len(images),
                    "labeled": len(keys & set(labels)), "done": sum(1 for k in keys if states.get(k) == 1)})
    return out


def read_log(book: Book) -> dict:
    try:
        data = json.loads((book.cache_dir / "ocr_log.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def plan(book: Book) -> dict:
    """Dữ liệu cho màn hình OCR: số trang theo nhãn, engine đề xuất, trạng thái thư mục."""
    counts: dict[str, int] = {}
    for tag in page_tags(book).values():
        counts[tag] = counts.get(tag, 0) + 1
    tags = [{"tag": t, "pages": counts.get(t, 0), "recommended": eng.RECOMMENDED[t]} for t in eng.OCR_TAGS]
    return {
        "tags": tags,
        "no_ocr": {t: counts.get(t, 0) for t in ("blank", "misc", UNKNOWN)},
        "engines": {k: {**v, "available": _engine_unavailable(k)} for k, v in eng.ENGINES.items()},
        "folders": folder_status(book),
        "paddle": eng.PADDLE.status(),
        "ppocrlabel": ppocrlabel_status(),
        "job": job_status(book),
        "failed": read_log(book).get("failed", {}),
        "cache": ocr_cache.counts(book),
    }


def _engine_unavailable(engine: str) -> str | None:
    """Lý do engine chưa dùng được, hoặc None."""
    if engine == eng.PADDLEOCR:
        return eng.PADDLE.unavailable_reason()
    if engine == eng.KIMHANNOM:
        return eng.kimhannom_available()
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
            return hit, True

    if engine == eng.KIMHANNOM:
        raw = eng.run_kimhannom(image, **params)
    elif engine == eng.PADDLEOCR:
        raw = eng.PADDLE.recognize(image)
    else:
        raw = eng.run_row_projection(image, book.cache_dir / "ocr" / "row_projection")
    try:
        ocr_cache.save(book, engine, number, info, params, raw)
    except OSError:
        pass   # không ghi được cache (đĩa đầy, ...) không làm hỏng kết quả OCR
    return raw, False


def ocr_page(book: Book, number: int, tag: str, engine: str, image: Path,
             use_cache: bool = True) -> tuple[dict[str, list[dict]], bool]:
    """Chạy (hoặc lấy từ cache) engine trên một ảnh. Trả về ({thư mục: [box PPOCRLabel]}, lấy_từ_cache)."""
    from PIL import Image

    raw, cached = raw_result(book, number, engine, image, use_cache)
    if engine == eng.PADDLEOCR:   # bỏ số trang, đầu/chân trang, chú thích, bảng, tranh (lọc sau cache nên đổi bộ lọc không phải chạy lại)
        raw = [b for b in raw if b.get("label") not in eng.PADDLE_SKIP_LABELS]

    if tag == "ch_vi":
        if engine == eng.ROW_PROJECTION:
            split = eng.rows_to_scripts(raw)
        else:
            split = {"ch": [b for b in raw if eng.is_han_text(b["text"])],
                     "vi": [b for b in raw if not eng.is_han_text(b["text"])]}
            if engine == eng.KIMHANNOM:   # API chuyên cho chữ Nôm: phần không phải chữ Hán là nhiễu, không đưa vào vi/
                split["vi"] = []
    else:
        split = {folders_for(tag)[0]: raw}

    with Image.open(image) as im:
        width, height = im.size
    result: dict[str, list[dict]] = {}
    for folder, boxes in split.items():
        if engine != eng.ROW_PROJECTION:
            boxes = [b for b in boxes if b["text"].strip()]
        if folder == "ch" and engine != eng.ROW_PROJECTION:
            vertical = _median([_size(b)[1] for b in boxes]) >= _median([_size(b)[0] for b in boxes])
            boxes = eng.order_boxes(boxes, vertical_rtl=vertical)       # Nôm: phải sang trái, trên xuống
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
    return result, cached


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
        if value != eng.SKIP and value not in eng.ENGINES:
            raise ValueError(f"Engine không hợp lệ cho nhãn {tag}: {value!r}")
        chosen[tag] = value
    return chosen


def start_ocr(book: Book, engines: dict, overwrite: bool, use_cache: bool = True) -> None:
    global _job
    chosen = validate_choices(engines)
    tags_present = set(page_tags(book).values())
    for tag, engine in chosen.items():
        if engine != eng.SKIP and tag in tags_present:
            reason = _engine_unavailable(engine)
            if reason:
                raise ValueError(f"Engine {eng.ENGINES[engine]['label']} (nhãn {tag}) chưa dùng được: {reason}")
    with _job_lock:
        if _job is not None and _job["status"] == "running":
            raise OcrBusy("Đang chạy OCR một sách, hãy đợi xong rồi thử lại.")
        _job = {"book": norm(book.name), "status": "running", "done": 0, "total": 0, "skipped": 0, "cached": 0,
                "current": "Đang chuẩn bị…", "failed": [], "error": None, "started": time.time()}
    threading.Thread(target=_run, args=(book, chosen, overwrite, use_cache), daemon=True).start()


def _build_tasks(book: Book, chosen: dict[str, str], overwrite: bool) -> tuple[list[tuple], int]:
    """[(số trang, nhãn, engine, ảnh, [thư mục cần ghi])], số trang bỏ qua vì đã có nhãn."""
    existing = {f: (label_io.read_labels(book.dir / f / label_io.LABEL_NAME),
                    label_io.read_states(book.dir / f / label_io.STATE_NAME)) for f in LABEL_FOLDERS}
    tasks, skipped = [], 0
    for number, tag in sorted(page_tags(book).items()):
        engine = chosen.get(tag)
        if engine is None or engine == eng.SKIP:
            continue
        need = []
        for folder in folders_for(tag):
            key = label_io.image_key(folder, _image_name(book, number))
            labels, states = existing[folder]
            if overwrite or key not in labels:
                need.append(folder)
        image = book.dir / folders_for(tag)[0] / _image_name(book, number)
        if not need:
            skipped += 1
        elif image.is_file():
            tasks.append((number, tag, engine, image, need))
    return tasks, skipped


def _run(book: Book, chosen: dict[str, str], overwrite: bool, use_cache: bool = True) -> None:
    pending: dict[str, dict[str, list[dict]]] = {}
    log = read_log(book)
    log_pages = log.setdefault("pages", {})
    failed: dict[str, str] = {}
    consecutive = 0
    try:
        sync_folders(book)
        tasks, skipped = _build_tasks(book, chosen, overwrite)
        with _job_lock:
            _job.update(total=len(tasks), skipped=skipped)

        def flush():
            for folder, labels in pending.items():
                if labels:
                    label_io.update_folder(book.dir / folder, labels, state=0)
            pending.clear()
            log.update(updated=datetime.now().isoformat(), failed=failed)
            write_json_atomic(book.cache_dir / "ocr_log.json", log)

        for i, (number, tag, engine, image, need) in enumerate(tasks, 1):
            with _job_lock:
                _job.update(done=i - 1, current=f"Trang {number:03d} ({tag}, {eng.ENGINES[engine]['label']})")
            try:
                per_folder, from_cache = ocr_page(book, number, tag, engine, image, use_cache)
                consecutive = 0
                failed.pop(str(number), None)
            except Exception as e:
                consecutive += 1
                message = f"{type(e).__name__}: {e}" if not isinstance(e, eng.EngineError) else str(e)
                failed[str(number)] = message
                with _job_lock:
                    _job["failed"].append({"page": number, "error": message})
                if consecutive >= MAX_CONSECUTIVE_FAILS:
                    raise RuntimeError(f"Dừng vì {consecutive} trang liên tiếp lỗi. Lỗi gần nhất: {message}") from e
                continue
            for folder in need:
                key = label_io.image_key(folder, _image_name(book, number))
                pending.setdefault(folder, {})[key] = per_folder.get(folder, [])
            if from_cache:
                with _job_lock:
                    _job["cached"] += 1
            log_pages[str(number)] = {"tag": tag, "engine": engine, "at": datetime.now().isoformat(), "cached": from_cache,
                                      "boxes": {f: len(per_folder.get(f, [])) for f in need}}
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


# ---------------------------------------------------------------- PPOCRLabel
def _ppocrlabel_command() -> list[str] | None:
    custom = os.getenv("PPOCRLABEL_CMD")
    if custom:
        return shlex.split(custom, posix=(os.name != "nt"))
    exe = shutil.which("PPOCRLabel")
    return [exe] if exe else None


def ppocrlabel_status() -> dict:
    cmd = _ppocrlabel_command()
    if cmd:
        return {"available": True}
    return {"available": False,
            "hint": "Chưa cài PPOCRLabel (sẽ cài sau). Khi có: pip install PPOCRLabel, hoặc đặt biến môi trường "
                    "PPOCRLABEL_CMD là lệnh để mở nó."}


def open_ppocrlabel(book: Book, folder: str) -> dict:
    """Mở PPOCRLabel. Phần mềm không có tham số thư mục ảnh trong dòng lệnh, nên người dùng chọn thư mục trong ứng dụng
    (Menu File -> Open Dir); trả về đường dẫn thư mục cần mở."""
    if folder not in LABEL_FOLDERS:
        raise ValueError(f"Thư mục không hợp lệ: {folder}")
    directory = book.dir / folder
    if not _folder_images(book, folder):
        raise ValueError(f"Thư mục {folder}/ chưa có ảnh. Hãy chạy OCR (hoặc gán nhãn trang) trước.")
    cmd = _ppocrlabel_command()
    if not cmd:
        raise FileNotFoundError(ppocrlabel_status()["hint"])
    subprocess.Popen(cmd, cwd=str(directory), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)
    return {"folder": folder, "path": str(directory)}
