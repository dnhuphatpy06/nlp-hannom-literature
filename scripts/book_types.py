"""Loại sách (T1, T2, Tnom, ...) và file data/cache/type.json.

type.json có dạng {"<Tên sách>.pdf": "<loại>"}:
  - mọi sách đều có mặt, sách chưa phân loại được ghi là "unknown";
  - mục không còn sách tương ứng vẫn được giữ nguyên, không báo lỗi.
"""
import json
import threading
from pathlib import Path

from .library import UNKNOWN, norm, shared_cache_dir, write_json_atomic

LAYOUT_UNKNOWN = "Chưa phân loại"  # giá trị layout_code của phan_loai.json khi sách chưa có loại

# Theo mục "Chú thích về phân loại sách" trong docs/Quy_dinh_do_an.md
BOOK_TYPES = [
    {"code": "T1", "desc": "Quốc ngữ nằm ở phần đầu, Nôm ở phần cuối"},
    {"code": "T1w", "desc": "T1 nhưng viết tay Nôm (w là writing)"},
    {"code": "T2", "desc": "Xen kẽ (Ví dụ: Phải: Nôm, Trái: Quốc ngữ)"},
    {"code": "T2w", "desc": "T2 nhưng viết tay Nôm (w là writing)"},
    {"code": "T2b", "desc": "Quốc ngữ và Nôm chung trang"},
    {"code": "Tref", "desc": "Tham khảo"},
    {"code": "Tx", "desc": "Loại khác"},
    {"code": "Tqn", "desc": "Chỉ có Quốc ngữ"},
    {"code": "Tnom", "desc": "Chỉ có Nôm"},
    {"code": "Tnomw", "desc": "Chỉ có viết tay Nôm (w là writing)"},
    {"code": UNKNOWN, "desc": "Chưa phân loại"},
]
VALID_CODES = {t["code"] for t in BOOK_TYPES}

_save_lock = threading.Lock()


def default_type_file(data_dir: Path) -> Path:
    return shared_cache_dir(data_dir) / "type.json"


def read_type_file(type_file: Path) -> tuple[dict[str, str], str | None]:
    """Trả về (mapping tên file -> loại, thông báo lỗi nếu file hỏng)."""
    if not type_file.is_file():
        return {}, None
    try:
        data = json.loads(type_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"Không đọc được {type_file.name}: {e}"
    if not isinstance(data, dict):
        return {}, f"{type_file.name} phải là một object JSON dạng {{\"tên file.pdf\": \"loại\"}}."
    return {str(k): v for k, v in data.items()}, None


def raw_type_of(stored: dict[str, str], book_name: str):
    """Giá trị đang lưu cho sách (khớp NFC/NFD), None nếu chưa có."""
    return next((v for k, v in stored.items() if norm(k) == norm(book_name)), None)


def type_of(stored: dict[str, str], book_name: str) -> str:
    value = raw_type_of(stored, book_name)
    return value if isinstance(value, str) and value in VALID_CODES else UNKNOWN


def layout_code_for(type_file: Path, book_name: str) -> str:
    """Giá trị layout_code ghi vào phan_loai.json."""
    stored, _ = read_type_file(type_file)
    code = type_of(stored, book_name)
    return LAYOUT_UNKNOWN if code == UNKNOWN else code


def save_types(book_names: list[str], type_file: Path, assignments: dict) -> dict:
    """Ghi type.json: mọi sách hiện có + giữ nguyên các mục không còn sách."""
    with _save_lock:
        stored, error = read_type_file(type_file)
        if error:
            raise ValueError(error + " Hãy sửa hoặc xóa file rồi thử lại để tránh mất dữ liệu.")

        present = {norm(n): n for n in book_names}
        assigned = {norm(k): v for k, v in assignments.items()}

        # Giữ nguyên các mục không có sách (không báo lỗi)
        result: dict[str, str] = {k: v for k, v in stored.items() if norm(k) not in present}

        for key, real_name in present.items():
            code = assigned.get(key)
            if code is None:
                code = type_of(stored, real_name)
            if code not in VALID_CODES:
                raise ValueError(f"Loại không hợp lệ cho '{real_name}': {code!r}")
            result[real_name] = code

        ordered = dict(sorted(result.items(), key=lambda kv: norm(kv[0]).casefold()))
        write_json_atomic(type_file, ordered)
        return {"saved": len(ordered), "pdf_count": len(present)}
