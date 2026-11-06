"""Cấu trúc thư mục data/ và các đường dẫn dùng chung.

Mỗi sách là một thư mục có cấu trúc giống thư mục nộp bài (docs/Quy_dinh_do_an.md),
cộng thêm một thư mục `cache/` chứa mọi thứ phát sinh trong quá trình xử lý:

    data/
    ├── cache/                       <- cache dùng chung: type.json, uploads.json
    └── <Tên sách>/
        ├── <Tên sách>.pdf           <- file gốc
        ├── ch/ vi/ intro/ ref/ toc/ blank/ misc/
        └── cache/                   <- không nộp
            ├── pages/               <- ảnh vừa tách, chưa phân loại
            ├── thumbs/              <- ảnh thu nhỏ cho Web UI
            └── phan_loai.json       <- nhãn từng trang
"""
import json
import os
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO / "data"

CACHE_NAME = "cache"
UNKNOWN = "unknown"
# Các thư mục ảnh theo "Quy định về dữ liệu nộp"
SUBMISSION_FOLDERS = ("ch", "vi", "intro", "ref", "toc", "blank", "misc")


def norm(name: str) -> str:
    """Chuẩn hóa NFC để khớp tên file tiếng Việt dù được lưu theo NFC hay NFD."""
    return unicodedata.normalize("NFC", name)


def write_json_atomic(path: Path, data) -> None:
    """Ghi JSON qua file tạm rồi đổi tên, để lỡ dừng giữa chừng không hỏng file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp_{path.name}_{os.getpid()}_{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    """Ghi file văn bản UTF-8, xuống dòng LF, qua file tạm rồi đổi tên."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp_{path.name}_{os.getpid()}_{uuid.uuid4().hex[:6]}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def shared_cache_dir(data_dir: Path) -> Path:
    """data/cache/: nơi đặt type.json và uploads.json."""
    return data_dir / CACHE_NAME


def find_page_image(pages_dir: Path, stem: str, number: int) -> Path | None:
    """Tìm ảnh `<stem>.<STT>.jpg`, chấp nhận cả hai dạng Unicode NFC và NFD của tên."""
    name = f"{stem}.{number:03d}.jpg"
    for candidate in (name, norm(name), unicodedata.normalize("NFD", name)):
        path = pages_dir / candidate
        if path.exists():
            return path
    return None


@dataclass(frozen=True)
class Book:
    dir: Path   # data/<Tên sách>/
    pdf: Path   # data/<Tên sách>/<Tên sách>.pdf

    @property
    def stem(self) -> str:
        return self.pdf.stem

    @property
    def name(self) -> str:
        """Tên file PDF, dùng làm khóa trong type.json."""
        return self.pdf.name

    @property
    def cache_dir(self) -> Path:
        return self.dir / CACHE_NAME

    @property
    def pages_dir(self) -> Path:
        return self.cache_dir / "pages"

    @property
    def thumbs_dir(self) -> Path:
        return self.cache_dir / "thumbs"

    @property
    def page_cache_file(self) -> Path:
        return self.cache_dir / "phan_loai.json"


def _book_in(folder: Path) -> Book | None:
    """Thư mục là một sách nếu bên trong có `<tên thư mục>.pdf`."""
    exact = folder / f"{folder.name}.pdf"
    if exact.is_file():
        return Book(folder, exact)
    wanted = norm(f"{folder.name}.pdf").casefold()
    try:
        for f in folder.iterdir():
            if f.is_file() and norm(f.name).casefold() == wanted:
                return Book(folder, f)
    except OSError:
        pass
    return None


def list_books(data_dir: Path) -> list[Book]:
    if not data_dir.is_dir():
        return []
    books = []
    for folder in data_dir.iterdir():
        if folder.is_dir() and folder.name != CACHE_NAME and not folder.name.startswith("."):
            book = _book_in(folder)
            if book:
                books.append(book)
    return sorted(books, key=lambda b: norm(b.name).casefold())


def find_book(data_dir: Path, name: str) -> Book | None:
    """Chỉ chấp nhận tên có trong danh sách sách của data/ (chống path traversal)."""
    return next((b for b in list_books(data_dir) if norm(b.name) == norm(name)), None)


def organize(data_dir: Path) -> list[str]:
    """Đưa cấu trúc data/ về dạng thư mục nộp bài.

    - PDF nằm lẻ trong data/ được chuyển vào data/<Tên sách>/<Tên sách>.pdf.
    - Mọi sách có đủ các thư mục ch, vi, intro, ref, toc, blank, misc và cache.
    Không bao giờ ghi đè: nếu đã có PDF cùng tên trong thư mục sách thì giữ nguyên cả hai.
    Trả về danh sách thông báo để in ra.
    """
    notes: list[str] = []
    if not data_dir.is_dir():
        return notes

    for pdf in sorted(data_dir.iterdir()):
        if not (pdf.is_file() and pdf.suffix.lower() == ".pdf"):
            continue
        if pdf.stem == CACHE_NAME or pdf.stem.startswith("."):
            notes.append(f"Bỏ qua '{pdf.name}': tên sách không hợp lệ.")
            continue
        book_dir = data_dir / pdf.stem
        target = book_dir / pdf.name
        if target.exists() or (book_dir.is_dir() and _book_in(book_dir)):
            notes.append(f"Bỏ qua '{pdf.name}': {book_dir.name}/ đã có PDF cùng tên, hãy kiểm tra thủ công.")
            continue
        book_dir.mkdir(parents=True, exist_ok=True)
        os.replace(pdf, target)
        notes.append(f"Đã chuyển '{pdf.name}' vào {book_dir.name}/")

    shared_cache_dir(data_dir).mkdir(exist_ok=True)
    for book in list_books(data_dir):
        for folder in (*SUBMISSION_FOLDERS, CACHE_NAME):
            (book.dir / folder).mkdir(exist_ok=True)
    return notes
