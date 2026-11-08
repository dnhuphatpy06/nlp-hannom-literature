"""Cache nhãn từng trang: data/<Tên sách>/cache/phan_loai.json.

Mỗi trang: {"tag": "unknown", "rotate": 0, "notes": "", "blank": {...}}.
  - Mọi trang mặc định có tag "unknown"; nhãn đã sửa được giữ nguyên khi tách trang lại.
  - "blank" là điểm của scripts/blank_detector.py; sau khi nhận diện, trang unknown bị coi là blank được gán tag "blank"
    (xem tag_unknown), trang đã có nhãn khác thì giữ nguyên.
  - "spread": 1 | 2 (không bắt buộc) là bản chụp 1 trang hay 2 trang của trang ch; không có thì OCR tự đoán theo tỉ lệ ảnh.
  - "auto": true đánh dấu nhãn do máy tự gán (blank, ch/vi theo loại sách); gán tay (set_tags) xóa dấu này.
    Mục "autotag" ở cấp sách ghi loại sách đã dùng cho lần tự gán gần nhất.
"""
import json
import os
import threading
from datetime import datetime

from .library import UNKNOWN, Book, norm, write_json_atomic

DEFAULT_PAGE = {"tag": UNKNOWN, "rotate": 0, "notes": ""}
# Nhãn trang hợp lệ: các thư mục trong "Quy định về dữ liệu nộp" (ch_vi = chép vào cả ch/ và vi/)
PAGE_TAGS = {"ch", "vi", "ch_vi", "intro", "ref", "toc", "blank", "misc", UNKNOWN}
SPREADS = (1, 2)   # "spread": số trang sách trong một ảnh (bản chụp 1 trang hay 2 trang), chỉ dùng cho trang ch

_cache_lock = threading.Lock()  # đọc-sửa-ghi phan_loai.json phải tuần tự


def read_cache(book: Book) -> dict | None:
    cache_file = book.page_cache_file
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("pages"), dict):
            return data
    except (OSError, ValueError):
        pass
    # Cache hỏng: để lại bản .bak thay vì xóa mất
    try:
        os.replace(cache_file, cache_file.with_name(cache_file.name + ".bak"))
    except OSError:
        pass
    return None


def _full_pages(old_pages: dict, total_pages: int) -> dict:
    """Đủ trang 1..total_pages; trang đã có giữ nguyên, trang mới là unknown."""
    pages = {}
    for n in range(1, total_pages + 1):
        old = old_pages.get(str(n))
        pages[str(n)] = old if isinstance(old, dict) else dict(DEFAULT_PAGE)
    return pages


def ensure_cache(book: Book, total_pages: int, dpi: int, layout_code: str) -> dict:
    """Tạo/cập nhật phan_loai.json khi bắt đầu tách trang: giữ nhãn và điểm đã có."""
    with _cache_lock:
        cache = read_cache(book) or {}
        cache.update(
            book_name=book.stem, pdf=book.name, layout_code=layout_code, total_pages=total_pages,
            dpi=dpi, split_started=True, split_complete=False, last_updated=datetime.now().isoformat(),
            pages=_full_pages(cache.get("pages", {}), total_pages),
        )
        cache.setdefault("notes", "")
        write_json_atomic(book.page_cache_file, cache)
        return cache


def mark_split_complete(book: Book) -> None:
    with _cache_lock:
        cache = read_cache(book)
        if cache is not None:
            cache["split_complete"] = True
            cache["last_updated"] = datetime.now().isoformat()
            write_json_atomic(book.page_cache_file, cache)


def update_pages(book: Book, updates: dict[int, dict], total_pages: int, layout_code: str,
                 top_level: dict | None = None) -> dict:
    """Gộp thêm trường vào từng trang (vd. điểm blank).

    Tạo cache nếu chưa có, để chạy được trước khi tách trang; không đụng tới split_complete.
    """
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            cache = {"book_name": book.stem, "pdf": book.name, "layout_code": layout_code,
                     "dpi": None, "split_complete": False, "notes": ""}
        cache["total_pages"] = total_pages
        cache["pages"] = _full_pages(cache.get("pages", {}), total_pages)
        for number, fields in updates.items():
            cache["pages"][str(number)].update(fields)
        cache.update(top_level or {})
        cache["last_updated"] = datetime.now().isoformat()
        write_json_atomic(book.page_cache_file, cache)
        return cache


def set_tags(book: Book, pages: list[int], tag: str) -> int:
    """Gán nhãn cho nhiều trang. Trả về số trang đã đổi."""
    if tag not in PAGE_TAGS:
        raise ValueError(f"Nhãn không hợp lệ: {tag!r}. Hợp lệ: {', '.join(sorted(PAGE_TAGS))}")
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            raise ValueError("Sách chưa có cache trang. Hãy nhận diện hoặc tách trang trước.")
        total = int(cache.get("total_pages", 0))
        changed = 0
        for number in {int(p) for p in pages}:
            if not 1 <= number <= total:
                raise ValueError(f"Trang {number} nằm ngoài khoảng 1–{total}.")
            page = cache["pages"].setdefault(str(number), dict(DEFAULT_PAGE))
            page.pop("auto", None)  # gán tay: không còn là nhãn tự động, tự gán lại sẽ không đụng tới
            if page.get("tag") != tag:
                page["tag"] = tag
                changed += 1
        cache["last_updated"] = datetime.now().isoformat()
        write_json_atomic(book.page_cache_file, cache)
        return changed


def set_spread(book: Book, pages: list[int], spread: int | None) -> int:
    """Gán số trang trong một ảnh (1 hoặc 2) cho các trang; None xoá nhãn gán tay để OCR tự đoán theo tỉ lệ ảnh.

    Dùng cho trang ch: bản chụp 2 trang được lọc nhiễu ở giữa và đánh số trang trái trước, trang phải sau
    (ocr_engines.filter_spread_noise, order_boxes_spread). Trả về số trang đã đổi."""
    if spread is not None and spread not in SPREADS:
        raise ValueError(f"Số trang trong ảnh không hợp lệ: {spread!r}. Hợp lệ: 1, 2, hoặc bỏ trống để tự đoán.")
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            raise ValueError("Sách chưa có cache trang. Hãy nhận diện hoặc tách trang trước.")
        total = int(cache.get("total_pages", 0))
        changed = 0
        for number in {int(p) for p in pages}:
            if not 1 <= number <= total:
                raise ValueError(f"Trang {number} nằm ngoài khoảng 1–{total}.")
            page = cache["pages"].setdefault(str(number), dict(DEFAULT_PAGE))
            if page.get("spread") != spread:
                changed += 1
            if spread is None:
                page.pop("spread", None)
            else:
                page["spread"] = spread
        cache["last_updated"] = datetime.now().isoformat()
        write_json_atomic(book.page_cache_file, cache)
        return changed


def page_spreads(book: Book) -> dict[int, int]:
    """{số trang: 1 | 2} của các trang đã được gán tay; trang chưa gán không có mặt (OCR sẽ tự đoán)."""
    cache = read_cache(book) or {}
    return {int(n): p["spread"] for n, p in cache.get("pages", {}).items()
            if isinstance(p, dict) and p.get("spread") in SPREADS}


def tag_unknown(book: Book, pages: list[int], tag: str) -> int:
    """Gán nhãn chỉ cho những trang đang là unknown, giữ nguyên trang đã có nhãn. Trả về số trang đã gán."""
    if tag not in PAGE_TAGS:
        raise ValueError(f"Nhãn không hợp lệ: {tag!r}.")
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            return 0
        changed = 0
        for number in {int(p) for p in pages}:
            page = cache["pages"].get(str(number))
            if isinstance(page, dict) and page.get("tag", UNKNOWN) == UNKNOWN:
                page["tag"] = tag
                page["auto"] = True
                changed += 1
        if changed:
            cache["last_updated"] = datetime.now().isoformat()
            write_json_atomic(book.page_cache_file, cache)
        return changed


def tag_unknown_map(book: Book, assignments: dict[int, str]) -> int:
    """Gán nhãn riêng cho từng trang, chỉ với trang đang unknown (trang đã có nhãn giữ nguyên). Trả về số trang đã gán."""
    bad = {tag for tag in assignments.values() if tag not in PAGE_TAGS}
    if bad:
        raise ValueError(f"Nhãn không hợp lệ: {', '.join(sorted(map(repr, bad)))}.")
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            return 0
        changed = 0
        for number, tag in assignments.items():
            page = cache["pages"].get(str(number))
            if isinstance(page, dict) and page.get("tag", UNKNOWN) == UNKNOWN:
                page["tag"] = tag
                page["auto"] = True
                changed += 1
        if changed:
            cache["last_updated"] = datetime.now().isoformat()
            write_json_atomic(book.page_cache_file, cache)
        return changed


def clear_auto_script_tags(book: Book) -> int:
    """Trả về unknown các trang ch/vi/ch_vi do tự động gán (khi loại sách đổi). Nhãn gán tay giữ nguyên."""
    with _cache_lock:
        cache = read_cache(book)
        if cache is None:
            return 0
        cleared = 0
        for page in cache["pages"].values():
            if isinstance(page, dict) and page.get("auto") and page.get("tag") in ("ch", "vi", "ch_vi"):
                page["tag"] = UNKNOWN
                page.pop("auto", None)
                cleared += 1
        if cleared:
            cache["last_updated"] = datetime.now().isoformat()
            write_json_atomic(book.page_cache_file, cache)
        return cleared


def autotag_type(book: Book) -> str | None:
    """Loại sách đã dùng cho lần tự gán gần nhất, None nếu chưa tự gán lần nào."""
    record = (read_cache(book) or {}).get("autotag")
    return record.get("type") if isinstance(record, dict) else None


def set_autotag_type(book: Book, book_type: str) -> None:
    with _cache_lock:
        cache = read_cache(book)
        if cache is not None:
            cache["autotag"] = {"type": book_type, "at": datetime.now().isoformat()}
            write_json_atomic(book.page_cache_file, cache)


def unknown_pages(book: Book) -> list[int]:
    """Số thứ tự các trang đang unknown, theo thứ tự trang."""
    cache = read_cache(book)
    if cache is None:
        return []
    return sorted(int(n) for n, p in cache["pages"].items()
                  if isinstance(p, dict) and p.get("tag", UNKNOWN) == UNKNOWN)


def rendered_pages(book: Book) -> list[int]:
    """Số thứ tự các trang đã có ảnh trong cache/pages/."""
    if not book.pages_dir.is_dir():
        return []
    found = []
    for f in book.pages_dir.glob("*.jpg"):
        stem, _, num = f.stem.rpartition(".")
        if num.isdigit() and norm(stem) == norm(book.stem):
            found.append(int(num))
    return sorted(found)
