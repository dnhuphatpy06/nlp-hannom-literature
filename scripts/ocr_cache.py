"""Cache kết quả thô của từng engine OCR: data/<Tên sách>/cache/ocr/<engine>/<NNN>.json.

Mỗi file ứng với MỘT ảnh và MỘT engine, ghi những gì engine trả về (chưa lọc, chưa sắp thứ tự, chưa làm sạch):

    {"engine": "kimhannom", "page": 12, "saved": "2026-11-07T...",
     "image": {"name": "Tên.012.jpg", "sha256": "...", "size": 123456},
     "params": {"ocr_id": 2, "lang_type": 2, "epitaph": 0},
     "boxes": [{"text": "...", "points": [[x,y] x4], ...}, ...]}

Trước khi gọi engine, ocr_jobs hỏi cache: có file cho (engine, trang) mà sha256 của ảnh và `params` khớp thì dùng lại,
không gọi API hay chạy mô hình. Ảnh bị sửa (xoay, chỉnh sáng) hoặc tham số đổi thì tự coi là chưa có và tính lại.
Vì lưu kết quả thô nên đổi cách lọc/sắp xếp/làm sạch văn bản cũng không cần gọi lại engine.

Không cache kết quả rỗng (có thể là lỗi tạm thời của server), và file cache hỏng được coi như chưa có.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .library import Book, write_json_atomic


def cache_root(book: Book) -> Path:
    return book.cache_dir / "ocr"


def cache_file(book: Book, engine: str, number: int) -> Path:
    return cache_root(book) / engine / f"{number:03d}.json"


def image_info(image: Path) -> dict:
    h = hashlib.sha256()
    with image.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"name": image.name, "sha256": h.hexdigest(), "size": image.stat().st_size}


def _canonical(params: dict) -> dict:
    """Đưa params về dạng JSON để so sánh (tuple -> list, ...)."""
    return json.loads(json.dumps(params, sort_keys=True, default=str))


def load(book: Book, engine: str, number: int, info: dict, params: dict) -> list[dict] | None:
    """Kết quả thô đã cache của ảnh này với engine này; None nếu chưa có hoặc không còn khớp."""
    path = cache_file(book, engine, number)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("boxes"), list):
        return None
    if (data.get("engine") != engine or (data.get("image") or {}).get("sha256") != info["sha256"]
            or _canonical(data.get("params") or {}) != _canonical(params)):
        return None
    return data["boxes"]


def save(book: Book, engine: str, number: int, info: dict, params: dict, boxes: list[dict]) -> bool:
    """Ghi cache. Kết quả rỗng không được ghi. Trả về True nếu đã ghi."""
    if not boxes:
        return False
    write_json_atomic(cache_file(book, engine, number), {
        "engine": engine, "page": number, "saved": datetime.now().isoformat(),
        "image": info, "params": _canonical(params), "boxes": boxes,
    })
    return True


def counts(book: Book) -> dict[str, int]:
    """Số ảnh đã cache theo engine."""
    root = cache_root(book)
    if not root.is_dir():
        return {}
    return {d.name: sum(1 for _ in d.glob("*.json")) for d in sorted(root.iterdir()) if d.is_dir()}


def clear(book: Book, engine: str | None = None) -> int:
    """Xóa cache của một engine (hoặc tất cả). Trả về số file JSON đã xóa. Ảnh debug của row_projection được giữ nguyên."""
    removed = 0
    for name in ([engine] if engine else list(counts(book))):
        directory = cache_root(book) / name
        for f in directory.glob("*.json"):
            f.unlink(missing_ok=True)
            removed += 1
        if directory.is_dir() and not any(directory.iterdir()):
            shutil.rmtree(directory, ignore_errors=True)
    return removed
