"""Tách PDF bằng thư viện pdfslice_py (công cụ cũ, giữ lại để tham khảo).

Lưu ý: pdfslice_py tự tạo thư mục <Tên sách>/ cạnh PDF và đặt ảnh thẳng vào đó, không theo cấu trúc
cache/pages/ của dự án. Để tách trang đúng cấu trúc data/, hãy dùng `python app.py` hoặc
`python scripts/page_splitter.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # chạy trực tiếp được như script
from pdfslice_py import split_all  # noqa: E402
from scripts import library  # noqa: E402

if __name__ == "__main__":   # bắt buộc, vì worker dùng "spawn"
    results = split_all(library.DEFAULT_DATA_DIR, level=2)
