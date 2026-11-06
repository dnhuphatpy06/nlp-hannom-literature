"""Tự gán nhãn ch / vi / ch_vi cho các trang còn unknown dựa vào loại sách (data/cache/type.json).

Mỗi sách: nhận diện trang blank trước (scripts/blank_detector.py), rồi chia các trang unknown còn lại theo loại sách:

    Tnom, Tnomw : tất cả là ch
    Tqn         : tất cả là vi
    T2b         : tất cả là ch_vi (Nôm và Quốc ngữ chung trang, chép vào cả ch/ và vi/)
    T1, T1w     : nửa đầu là vi, nửa sau là ch
    T2, T2w     : xen kẽ ch, vi, ch, vi, ... (Nôm trước)
    loại khác   : không gán, để gán tay

Chỉ là giả định theo bố cục: sách T2 thực tế có thể xen kẽ theo khối, ranh giới T1 hiếm khi đúng giữa.
Trang đã có nhãn (kể cả nhãn gán tay như misc, intro) không bị đổi và không tính vào chuỗi.

Dòng lệnh (mọi sách trong data/, hoặc một sách):
    python -m scripts.auto_tag
    python -m scripts.auto_tag --book "Tên sách"
"""
import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy trực tiếp: python scripts/auto_tag.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "scripts"

ALL_CH = {"Tnom", "Tnomw"}
ALL_VI = {"Tqn"}
ALL_BOTH = {"T2b"}
FIRST_VI_THEN_CH = {"T1", "T1w"}
ALTERNATING = {"T2", "T2w"}
AUTO_TYPES = ALL_CH | ALL_VI | ALL_BOTH | FIRST_VI_THEN_CH | ALTERNATING


def plan_tags(book_type: str, unknown_pages: list[int]) -> dict[int, str]:
    """Nhãn dự kiến cho từng trang unknown (theo thứ tự trang). Loại sách không tự gán được thì trả về {}."""
    pages = sorted(unknown_pages)
    if book_type in ALL_CH:
        return {p: "ch" for p in pages}
    if book_type in ALL_VI:
        return {p: "vi" for p in pages}
    if book_type in ALL_BOTH:
        return {p: "ch_vi" for p in pages}
    if book_type in FIRST_VI_THEN_CH:
        half = len(pages) // 2
        return {p: "vi" if i < half else "ch" for i, p in enumerate(pages)}
    if book_type in ALTERNATING:
        return {p: "ch" if i % 2 == 0 else "vi" for i, p in enumerate(pages)}
    return {}


def main() -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    from . import book_types, library, split_jobs

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/ (mặc định: data/)")
    parser.add_argument("--type-file", type=Path, help="File loại sách (mặc định: <data>/cache/type.json)")
    parser.add_argument("--book", help="Chỉ xử lý sách có tên (hoặc một phần tên) này")
    args = parser.parse_args()

    data_dir = args.data.resolve()
    type_file = (args.type_file or book_types.default_type_file(data_dir)).resolve()
    books = library.list_books(data_dir)
    if args.book:
        wanted = library.norm(args.book).casefold()
        books = [b for b in books if wanted in library.norm(b.name).casefold()]
    if not books:
        print("Không có sách nào để xử lý.")
        return 1

    failed = 0
    for book in books:
        try:
            r = split_jobs.autotag_book(book, type_file)
        except Exception as e:  # một sách lỗi không làm dừng cả lô
            failed += 1
            print(f"[LỖI] {book.name}: {e}")
            continue
        counts = ", ".join(f"{k} {v}" for k, v in r["counts"].items()) or "không gán ch/vi"
        note = f"  ({r['skipped_reason']})" if r.get("skipped_reason") else ""
        print(f"{book.name} [{r['type']}]: {r['total']} trang | blank {r['blank_tagged']} | {counts}{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
