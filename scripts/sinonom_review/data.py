"""Đọc dữ liệu: chỉ dùng các file xlsx (câu Nôm, câu Quốc ngữ, Patch ID) và các file ảnh trang.

Cấu trúc thư mục dữ liệu data/ (chỉ phần được dùng):

data/
├── Alignment/                     # các file xlsx (mỗi file = một sách)
│   └── <tên sách>.xlsx            # cột: ID, Patch ID, Vietnamese, Label
└── Yale/
    └── <tên sách>/images/
        └── <tên sách>.<trang>.jpg # ảnh trang

Patch ID = "<tên sách>.<trang>.<dòng>" -> ảnh "<tên sách>.<trang>.jpg" (bỏ phần cuối là số dòng).
Các file .txt (Label.txt, char_det.txt...) KHÔNG được đọc: chương trình không vẽ khung dòng trên ảnh.
Ảnh được tra thẳng theo đường dẫn trên, không quét đệ quy.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .text import nfc, tokenize_nom, tokenize_qn

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


@dataclass
class Sentence:
    idx: int                 # thứ tự toàn cục (0-based)
    book: str                # tên sách (tên file xlsx, bỏ đuôi)
    file: str                # tên file xlsx
    row: int                 # chỉ số dòng trong file xlsx (0-based, không tính tiêu đề)
    sid: str                 # giá trị cột ID
    image_id: str            # giá trị cột Patch ID
    nom: str
    qn: str
    chars: list[str] = field(default_factory=list)
    syls: list[str] = field(default_factory=list)
    book_pos: int = 0        # thứ tự trong sách (để lấy ngữ cảnh dòng trước/sau)


@dataclass
class ImageRef:
    path: Path | None        # đường dẫn ảnh trang, None nếu không tìm thấy
    line_index: int | None   # dòng thứ mấy trên trang (số cuối của Patch ID, tính từ 0)


def find_tables(data_path: str | Path) -> list[Path]:
    """Các bảng dữ liệu: mọi file .xlsx/.csv trong <data_path>/Alignment (data_path là thư mục data/), hoặc đúng
    file được chỉ định. Không tự dò thư mục khác."""
    data_path = Path(data_path)
    if data_path.is_file():
        return [data_path]
    base = data_path / "Alignment"
    if not base.is_dir():
        raise FileNotFoundError(f"Không có thư mục Alignment/ trong {data_path}: hãy trỏ paths.data tới thư mục data/.")
    return sorted(p for p in [*base.glob("*.xlsx"), *base.glob("*.csv")] if not p.name.startswith("~$"))


def load_sentences(data_path: str | Path, nom_col: str = "Label", qn_col: str = "Vietnamese",
                   id_col: str = "ID", image_col: str = "Patch ID", files: list[str] | None = None,
                   verbose: bool = True) -> list[Sentence]:
    """Đọc mọi bảng dữ liệu. files: chỉ giữ các file có tên (không đuôi hoặc có đuôi) trong danh sách."""
    out: list[Sentence] = []
    wanted = {nfc(Path(f).stem) for f in files} if files else None
    for path in find_tables(data_path):
        book = nfc(path.stem)
        if wanted is not None and book not in wanted:
            continue
        df = pd.read_excel(path, dtype=str) if path.suffix.lower() == ".xlsx" else \
            pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        missing = [c for c in (nom_col, qn_col) if c not in df.columns]
        if missing:
            warnings.warn(f"Bỏ qua {path.name}: thiếu cột {missing}")
            continue
        n0 = len(out)
        for row, r in enumerate(df.itertuples(index=False)):
            rec = dict(zip(df.columns, r))
            nom, qn = rec.get(nom_col), rec.get(qn_col)
            if not isinstance(nom, str) or not isinstance(qn, str) or not nom.strip() or not qn.strip():
                continue
            sid = rec.get(id_col) if id_col in df.columns else None
            image_id = rec.get(image_col) if image_col in df.columns else None
            out.append(Sentence(
                idx=len(out), book=book, file=path.name, row=row,
                sid="" if sid is None or (isinstance(sid, float) and pd.isna(sid)) else str(sid),
                image_id=nfc(str(image_id)) if isinstance(image_id, str) else "",
                nom=nom.strip(), qn=nfc(qn.strip()), chars=tokenize_nom(nom), syls=tokenize_qn(qn),
                book_pos=len(out) - n0))
        if verbose:
            print(f"  {path.name}: {len(out) - n0} câu")
    return out


class ImageIndex:
    """Ảnh trang theo cấu trúc cố định của data/: <data>/Yale/<tên sách>/images/<tên sách>.<trang>.jpg.

    Không quét đệ quy: chỉ liệt kê các thư mục `images/` nằm ngay dưới từng thư mục sách trong Yale/."""

    def __init__(self, data_path: str | Path | None):
        self.images: dict[str, Path] = {}          # "<tên sách>.<trang>" (NFC) -> file ảnh
        yale = Path(data_path) / "Yale" if data_path else None
        if yale is None or not yale.is_dir():
            return
        for book_dir in sorted(p for p in yale.iterdir() if p.is_dir()):
            img_dir = book_dir / "images"
            if not img_dir.is_dir():
                continue
            for f in sorted(img_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                    self.images.setdefault(nfc(f.stem), f)

    def lookup(self, image_id: str) -> ImageRef:
        """Patch ID "<tên sách>.<trang>.<dòng>" -> ảnh "<tên sách>.<trang>.jpg" (bỏ số dòng ở cuối)."""
        page, _, line = nfc(image_id or "").rpartition(".")
        if page and line.isdigit():
            return ImageRef(self.images.get(page), int(line))
        return ImageRef(None, None)

    def summary(self) -> dict:
        return {"images": len(self.images)}


def load_page_image(ref: ImageRef, max_side: int = 1800):
    """Mở ảnh trang (PIL.Image, thu nhỏ nếu quá lớn); None nếu không có ảnh."""
    if ref.path is None:
        return None
    from PIL import Image

    page = Image.open(ref.path).convert("RGB")
    scale = max_side / max(page.size)
    if scale < 1:
        page = page.resize((int(page.width * scale), int(page.height * scale)))
    return page
