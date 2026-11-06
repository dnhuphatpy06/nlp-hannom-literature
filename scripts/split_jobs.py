"""Tác vụ chạy trên PDF cho Web UI: tách trang (nền), nhận diện blank và gán ch/vi (tự động, nền), ảnh thu nhỏ."""
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import auto_tag, book_types, page_cache
from .library import UNKNOWN, Book, find_book, find_page_image, list_books, norm

try:  # Thiếu PyMuPDF thì vẫn dùng được phần gán loại sách
    from . import page_splitter
    UNAVAILABLE: str | None = None
except ImportError as _e:
    page_splitter = None
    UNAVAILABLE = f"Thiếu thư viện để tách trang ({_e}). Chạy: pip install -r requirements.txt"

try:  # Thiếu numpy/OpenCV thì các chức năng khác vẫn dùng được
    from . import blank_detector
    BLANK_UNAVAILABLE: str | None = None
except ImportError as _e:
    blank_detector = None
    BLANK_UNAVAILABLE = f"Thiếu thư viện để nhận diện trang trắng ({_e}). Chạy: pip install -r requirements.txt"

DEFAULT_DPI = 300
THUMB_SIZE = (360, 520)

# Trạng thái tác vụ tách trang đang chạy / vừa lỗi (trong bộ nhớ)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# PyMuPDF không an toàn đa luồng: tách trang và nhận diện trang trắng không được chạy cùng lúc
pdf_lock = threading.Lock()


class SplitBusy(Exception):
    pass


def split_status(book: Book) -> dict:
    job = _jobs.get(norm(book.name))
    if job:
        return dict(job)
    cache = page_cache.read_cache(book)
    if cache is None:
        return {"status": "none"}
    # Cache do nhận diện trang trắng tạo ra (chưa tách) không được tính là "tách dở"
    if not (cache.get("split_started") or cache.get("split_complete")):
        return {"status": "none"}
    return {
        "status": "done" if cache.get("split_complete") else "partial",
        "total_pages": cache.get("total_pages", 0),
        "dpi": cache.get("dpi"),
    }


def start_split(data_dir: Path, type_file: Path, name: str, dpi: int, force: bool) -> None:
    if page_splitter is None:
        raise ValueError(UNAVAILABLE)
    book = find_book(data_dir, name)
    if book is None:
        raise ValueError(f"Không tìm thấy PDF: {name}")
    if not 72 <= dpi <= 600:
        raise ValueError("DPI phải nằm trong khoảng 72–600.")
    key = norm(book.name)
    with _jobs_lock:
        # PyMuPDF không an toàn đa luồng nên chỉ tách một sách tại một thời điểm
        if any(j["status"] == "running" for j in _jobs.values()):
            raise SplitBusy("Đang có một sách khác được tách trang, hãy đợi xong rồi thử lại.")
        if not pdf_lock.acquire(blocking=False):
            raise SplitBusy("Đang nhận diện trang trắng, hãy đợi xong rồi thử lại.")
        _jobs[key] = {"status": "running", "done": 0, "total": 0}
    try:
        threading.Thread(target=_run_split, args=(book, key, type_file, dpi, force), daemon=True).start()
    except Exception:
        pdf_lock.release()
        raise


def _run_split(book: Book, key: str, type_file: Path, dpi: int, force: bool) -> None:
    job = _jobs[key]
    try:
        with page_splitter.pymupdf.open(book.pdf) as doc:
            if doc.needs_pass:
                raise ValueError("PDF có mật khẩu bảo vệ.")
            total_pages = len(doc)
        page_cache.ensure_cache(book, total_pages, dpi, book_types.layout_code_for(type_file, book.name))

        def on_progress(done: int, total: int) -> None:
            job["done"], job["total"] = done, total

        total, rendered, skipped = page_splitter.render_pdf(
            book.pdf, book.pages_dir, dpi=dpi, force=force, on_progress=on_progress)
        if total == 0:
            raise ValueError("Không đọc được file PDF.")
        missing = total - rendered - skipped
        if missing:
            raise ValueError(f"Còn {missing} trang tách lỗi, hãy bấm tách lại.")

        page_cache.mark_split_complete(book)
        with _jobs_lock:
            _jobs.pop(key, None)
    except Exception as e:  # lỗi được báo lên UI thay vì làm chết luồng im lặng
        with _jobs_lock:
            _jobs[key] = {"status": "error", "error": str(e)}
    finally:
        pdf_lock.release()


def detect_blank(book: Book, type_file: Path) -> dict:
    """Nhận diện trang blank từ PDF, lưu điểm vào cache và gán blank cho các trang unknown bị nhận diện là blank.

    Trang đã có nhãn khác unknown không bị đổi. Chạy đồng bộ, vài giây/sách.
    """
    if blank_detector is None:
        raise ValueError(BLANK_UNAVAILABLE)
    if not pdf_lock.acquire(blocking=False):
        raise SplitBusy("Đang tách trang hoặc nhận diện một sách khác, hãy đợi xong rồi thử lại.")
    started = time.time()
    try:
        results = blank_detector.detect_pdf(book.pdf)
    finally:
        pdf_lock.release()

    scored = [r for r in results if "error" not in r]
    updates = {r["page"]: {"blank": {k: r[k] for k in ("components", "active", "ink")}} for r in scored}
    page_cache.update_pages(
        book, updates, len(results), book_types.layout_code_for(type_file, book.name),
        top_level={"blank_scan": {"at": datetime.now().isoformat(), "side": blank_detector.RENDER_SIDE}})
    verdicts = [blank_detector.classify(r["components"]) for r in scored]
    # Trang blank hoặc "không chắc" mà đang là unknown thì gán luôn blank; trang đã có nhãn thì giữ nguyên.
    suspects = [r["page"] for r, v in zip(scored, verdicts) if v != "content"]
    tagged = page_cache.tag_unknown(book, suspects, "blank")
    return {
        "total": len(results),
        "failed": len(results) - len(scored),
        "blank": verdicts.count("blank"),
        "uncertain": verdicts.count("uncertain"),
        "tagged": tagged,
        "seconds": round(time.time() - started, 1),
    }


def autotag_book(book: Book, type_file: Path) -> dict:
    """Gán blank rồi ch/vi/ch_vi cho các trang unknown theo loại sách (quy tắc ở scripts/auto_tag.py).

    Nhận diện blank chỉ chạy nếu sách chưa được quét. Trang đã có nhãn khác unknown không bị đổi;
    các loại còn lại (intro, toc, ref, misc) và việc sửa sai làm tay.
    """
    if (page_cache.read_cache(book) or {}).get("blank_scan") is None:
        detect_blank(book, type_file)
    stored, _ = book_types.read_type_file(type_file)
    book_type = book_types.type_of(stored, book.name)

    # Loại sách đổi so với lần tự gán trước: bỏ các nhãn ch/vi do máy gán theo loại cũ (nhãn gán tay giữ nguyên)
    previous = page_cache.autotag_type(book)
    if previous is not None and previous != book_type:
        page_cache.clear_auto_script_tags(book)

    planned = auto_tag.plan_tags(book_type, page_cache.unknown_pages(book))
    page_cache.tag_unknown_map(book, planned)
    page_cache.set_autotag_type(book, book_type)

    pages = (page_cache.read_cache(book) or {}).get("pages", {})
    tags = [p.get("tag") for p in pages.values() if isinstance(p, dict)]
    counts = {t: tags.count(t) for t in ("ch", "vi", "ch_vi") if tags.count(t)}
    reason = None
    if book_type not in auto_tag.AUTO_TYPES:
        reason = ("sách chưa có loại, hãy gán loại sách rồi chạy lại" if book_type == UNKNOWN
                  else f"loại {book_type} không tự gán ch/vi, hãy gán tay")
    return {"type": book_type, "total": len(tags), "blank_tagged": tags.count("blank"),
            "counts": counts, "assigned": len(planned), "skipped_reason": reason}


# ---------- tự động chạy nền ----------
# Mỗi sách tự được nhận diện blank và gán ch/vi khi: chưa quét blank, hoặc loại sách khác lần tự gán trước.
_auto: dict[str, dict] = {}   # tên sách (NFC) -> {"status": queued|running|error, ...}; sách xong thì bị xóa khỏi đây
_auto_lock = threading.Lock()
_auto_running = False
_auto_again = False           # có yêu cầu mới khi luồng đang chạy: quét lại danh sách sau khi xong
BUSY_RETRY_SECONDS = 2
BUSY_RETRIES = 150            # chờ tối đa ~5 phút nếu đang tách trang


def needs_autotag(book: Book, book_type: str) -> bool:
    cache = page_cache.read_cache(book)
    if cache is None or cache.get("blank_scan") is None:
        return True
    return page_cache.autotag_type(book) != book_type


def autotag_status() -> dict:
    """Trạng thái tự động gán của các sách đang chờ, đang chạy hoặc lỗi."""
    with _auto_lock:
        return {name: dict(state) for name, state in _auto.items()}


def kick_autotag(data_dir: Path, type_file: Path) -> None:
    """Khởi động luồng nền tự nhận diện blank và gán ch/vi. Gọi nhiều lần cũng an toàn, không chặn."""
    global _auto_running, _auto_again
    if blank_detector is None:
        return
    with _auto_lock:
        if _auto_running:
            _auto_again = True
            return
        _auto_running = True
        _auto_again = False
    threading.Thread(target=_auto_worker, args=(data_dir, type_file), daemon=True).start()


def _auto_worker(data_dir: Path, type_file: Path) -> None:
    global _auto_running, _auto_again
    try:
        while True:
            stored, _ = book_types.read_type_file(type_file)
            todo = []
            with _auto_lock:
                for book in list_books(data_dir):
                    code = book_types.type_of(stored, book.name)
                    failed = _auto.get(norm(book.name))
                    if failed and failed.get("status") == "error" and failed.get("type") == code:
                        continue  # đã lỗi với đúng loại này, không thử lại mãi
                    if needs_autotag(book, code):
                        todo.append(book)
                        _auto[norm(book.name)] = {"status": "queued"}
            for book in todo:
                _auto_one(book, type_file)
            with _auto_lock:
                if not _auto_again:
                    _auto_running = False
                    return
                _auto_again = False
    finally:
        with _auto_lock:
            _auto_running = False


def _auto_one(book: Book, type_file: Path) -> None:
    key = norm(book.name)
    with _auto_lock:
        _auto[key] = {"status": "running"}
    try:
        for _ in range(BUSY_RETRIES):
            try:
                autotag_book(book, type_file)
                break
            except SplitBusy:
                time.sleep(BUSY_RETRY_SECONDS)
        else:  # vẫn bận (đang tách trang): bỏ qua, lần kích hoạt sau sẽ thử lại
            with _auto_lock:
                _auto.pop(key, None)
            return
        with _auto_lock:
            _auto.pop(key, None)
    except Exception as e:  # một sách lỗi (vd. PDF có mật khẩu) không làm dừng các sách khác
        stored, _ = book_types.read_type_file(type_file)
        with _auto_lock:
            _auto[key] = {"status": "error", "error": str(e), "type": book_types.type_of(stored, book.name)}


def thumbnail_path(book: Book, page: int) -> Path | None:
    """Tạo (và cache) ảnh thu nhỏ của một trang để lưới trang tải nhanh."""
    from PIL import Image

    src = find_page_image(book.pages_dir, book.stem, page)
    if src is None:
        return None
    thumb = book.thumbs_dir / f"{page:03d}.jpg"
    if thumb.is_file() and thumb.stat().st_mtime >= src.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f".tmp_{uuid.uuid4().hex[:8]}.jpg")
    with Image.open(src) as im:
        im.draft("RGB", (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))  # giải mã JPEG nhanh hơn
        im = im.convert("RGB")
        im.thumbnail(THUMB_SIZE)
        im.save(tmp, "JPEG", quality=80)
    os.replace(tmp, thumb)
    return thumb
