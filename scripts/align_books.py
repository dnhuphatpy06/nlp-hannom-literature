"""Dóng hàng Nôm - Quốc ngữ theo heuristic 1-1 (không so khớp nghĩa) và xuất <Tên sách>.csv, <Tên sách>.xlsx.

Cách dóng (xem docs/Quy_dinh_do_an.md, "Quy định về việc dóng hàng"):

  - Lấy N trang đầu có chữ Nôm (ch/Label.txt) mà vi/Label.txt cũng có.
  - Mỗi trang: sắp các box theo thứ tự đọc (từ trên xuống, cùng hàng thì từ trái sang), bỏ box rỗng, rồi ghép box thứ i của Nôm
    với box thứ i của Quốc ngữ (một dòng thơ/một dòng văn tương ứng một dòng). Không dùng từ điển, không so nghĩa.
  - Trang có số dòng Nôm khác số dòng Quốc ngữ: vẫn ghép theo thứ tự, dòng thừa thành hàng chỉ có một vế và được ghi vào align_report.txt.
  - Mọi chữ tô màu đen. Tô đỏ cần so nghĩa bằng từ điển nên chưa làm ở bước này (xem "Hạn chế" trong align_report.txt).

Đầu ra (trong data/<Tên sách>/):
  <Tên sách>.csv    4 cột img_id, sentence_id, SinoNom_Char, ChuQN_txt (UTF-8 có BOM để Excel mở không lỗi chữ)
  <Tên sách>.xlsx   cùng cột; cột Nôm dùng font HAN NOM A
  align_report.txt  các cặp không chắc chắn: lệch số dòng, hàng một vế, số chữ Nôm khác số chữ Quốc ngữ

Dùng:
    python scripts/align_books.py --book "Nhât Tỉnh Ngâm, Nôm_Y" --pages 5
(--book chấp nhận gõ không dấu, vd "Nhat Tinh Ngam Nom_Y"; thiếu --book thì liệt kê sách có đủ ch/vi.)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import label_io, library  # noqa: E402
from scripts.library import norm  # noqa: E402

COLUMNS = ["img_id", "sentence_id", "SinoNom_Char", "ChuQN_txt"]
NOM_FONT = "HAN NOM A"      # font Hán Nôm trong thư mục font/
LATIN_FONT = "Times New Roman"
# Dấu câu bỏ đi khi đếm chữ (Hán Nôm lẫn Latin)
PUNCT_RE = re.compile(r"[\s\.,;:!?\"'()\[\]{}<>«»“”‘’…\-–—、。，！？；：「」『』《》〈〉·]+")
ROW_TOLERANCE = 0.5   # hai box cùng hàng nếu tâm dọc lệch chưa tới nửa chiều cao box thấp hơn


@dataclass
class Pair:
    img_id: str
    index: int            # số thứ tự câu trong trang, từ 1
    nom: str
    qn: str

    @property
    def sentence_id(self) -> str:
        return f"{self.img_id[:-4]}.{self.index}" if self.img_id.lower().endswith(".jpg") else f"{self.img_id}.{self.index}"


# ---------------------------------------------------------------- đọc dữ liệu
def fold(text: str) -> str:
    """Bỏ dấu, hạ chữ thường, chỉ giữ chữ-số: để --book gõ không dấu vẫn khớp tên sách."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^0-9a-z]+", "", "".join(c for c in text if not unicodedata.combining(c)).casefold())


def find_book(data_dir: Path, query: str) -> library.Book:
    books = library.list_books(data_dir)
    key = fold(query.removesuffix(".pdf"))
    hits = [b for b in books if fold(b.stem) == key] or [b for b in books if key and key in fold(b.stem)]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(b.stem for b in (hits or books))
    raise SystemExit(f"[LỖI] {'Có nhiều sách khớp' if hits else 'Không thấy sách'} '{query}'. Chọn một trong: {names}")


def page_boxes(payload: str) -> list[dict]:
    """Các box của một trang theo thứ tự đọc, bỏ box không có chữ."""
    boxes = []
    for b in json.loads(payload):
        text = norm(str(b.get("transcription", ""))).strip()
        pts = b.get("points") or []
        if text and pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            boxes.append({"text": text, "x": min(xs), "top": min(ys), "cy": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys),
                          "points": pts})
    boxes.sort(key=lambda b: b["cy"])
    rows: list[list[dict]] = []   # gom các box cùng hàng rồi đọc từ trái sang
    for b in boxes:
        if rows and abs(b["cy"] - rows[-1][-1]["cy"]) < ROW_TOLERANCE * min(b["h"], rows[-1][-1]["h"]):
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b["x"])]


def load_pages(book: library.Book, folder: str) -> dict[int, tuple[str, list[dict]]]:
    """{số trang: (tên ảnh, box đã sắp)} của ch/ hoặc vi/."""
    out = {}
    for key, payload in label_io.read_labels(book.dir / folder / label_io.LABEL_NAME).items():
        number = label_io.page_number(key)
        if number:
            out[number] = (key.rsplit("/", 1)[-1], page_boxes(payload))
    return out


# ---------------------------------------------------------------- dóng hàng
def align(book: library.Book, n_pages: int) -> tuple[list[Pair], list[str]]:
    """Dóng 1-1 N trang đầu. Trả về (các cặp, ghi chú cho align_report)."""
    ch, vi = load_pages(book, "ch"), load_pages(book, "vi")
    shared = [n for n in sorted(ch) if n in vi][:n_pages]
    notes: list[str] = []
    if len(shared) < n_pages:
        notes.append(f"Chỉ có {len(shared)}/{n_pages} trang có cả Nôm lẫn Quốc ngữ.")
    pairs: list[Pair] = []
    for n in shared:
        img_id = ch[n][0]
        nom_lines = [b["text"].replace(" ", "") for b in ch[n][1]]
        qn_lines = [" ".join(b["text"].split()) for b in vi[n][1]]
        if len(nom_lines) != len(qn_lines):
            notes.append(f"Trang {n:03d} ({img_id}): {len(nom_lines)} dòng Nôm nhưng {len(qn_lines)} dòng Quốc ngữ; "
                         f"ghép theo thứ tự, {abs(len(nom_lines) - len(qn_lines))} dòng cuối chỉ có một vế.")
        for i in range(max(len(nom_lines), len(qn_lines))):
            pairs.append(Pair(img_id, i + 1, nom_lines[i] if i < len(nom_lines) else "", qn_lines[i] if i < len(qn_lines) else ""))
    return pairs, notes


def nom_len(text: str) -> int:
    return len(PUNCT_RE.sub("", text))


def qn_len(text: str) -> int:
    return len([w for w in PUNCT_RE.split(text) if w])


def build_report(book: library.Book, pairs: list[Pair], notes: list[str]) -> str:
    lines = [f"BÁO CÁO DÓNG HÀNG: {book.stem}", "",
             f"Kiểu dóng: 1-1 theo thứ tự dòng, không so nghĩa. {len(pairs)} hàng, "
             f"{len({p.img_id for p in pairs})} trang.", ""]
    lines.append("1. Trang lệch số dòng / thiếu trang")
    lines += [f"   - {n}" for n in notes] or ["   (không có)"]
    one_side = [p for p in pairs if not p.nom or not p.qn]
    lines += ["", "2. Hàng chỉ có một vế (cần soát tay)"]
    lines += [f"   - {p.sentence_id}: Nôm={p.nom or '(trống)'} | Quốc ngữ={p.qn or '(trống)'}" for p in one_side] or ["   (không có)"]
    # Quy định: nếu là dịch âm thì số chữ Quốc ngữ phải bằng số chữ Nôm. Thơ lục bát: lệch thường do OCR sót/thừa chữ.
    mismatch = [p for p in pairs if p.nom and p.qn and nom_len(p.nom) != qn_len(p.qn)]
    lines += ["", f"3. Số chữ Nôm khác số tiếng Quốc ngữ ({len(mismatch)}/{len(pairs)} hàng; nghi lỗi OCR hoặc ghép nhầm dòng)"]
    lines += [f"   - {p.sentence_id}: {nom_len(p.nom)} chữ Nôm / {qn_len(p.qn)} tiếng | {p.nom} | {p.qn}" for p in mismatch] or ["   (không có)"]
    lines += ["", "Hạn chế",
              "   - Chưa so nghĩa: mọi chữ trong xlsx tô đen. Quy định yêu cầu đỏ cho cặp khác nghĩa hoặc chữ Nôm không có trong từ điển,",
              "     việc này cần tra từ điển (Unihan, Nôm Na Việt, ...) nên chưa làm; vì vậy chưa tính được điểm tương đồng.",
              "   - Chưa ghép câu bị ngắt giữa hai trang và chưa tách theo dấu câu: mỗi dòng ảnh là một câu."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- xuất file
def write_csv(path: Path, pairs: list[Pair]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM: Excel nhận UTF-8, không lỗi font/lệch cột
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(COLUMNS)
        w.writerows([p.img_id, p.sentence_id, p.nom, p.qn] for p in pairs)


def write_xlsx(path: Path, pairs: list[Pair]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "align"
    ws.append(COLUMNS)
    for c in ws[1]:
        c.font = Font(name=LATIN_FONT, bold=True)
    black = "FF000000"   # đen = cùng nghĩa (chưa kiểm tra nghĩa, xem docstring)
    for p in pairs:
        ws.append([p.img_id, p.sentence_id, p.nom, p.qn])
        row = ws.max_row
        ws.cell(row, 1).font = ws.cell(row, 2).font = Font(name=LATIN_FONT, color=black)
        ws.cell(row, 3).font = Font(name=NOM_FONT, color=black, size=14)
        ws.cell(row, 4).font = Font(name=LATIN_FONT, color=black, size=12)
        for col in (1, 2, 3, 4):
            ws.cell(row, col).alignment = Alignment(vertical="center", wrap_text=col == 4)
    for col, width in zip("ABCD", (34, 34, 38, 52)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", help="Tên sách (thư mục trong data/), gõ không dấu cũng được")
    ap.add_argument("--pages", type=int, default=5, help="Số trang Nôm đầu tiên cần dóng (mặc định 5)")
    ap.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/")
    ap.add_argument("--out-dir", type=Path, help="Thư mục ghi kết quả (mặc định: thư mục của sách)")
    args = ap.parse_args()

    data_dir = args.data.resolve()
    if not args.book:
        ap.error("thiếu --book. Sách hiện có: " + "; ".join(b.stem for b in library.list_books(data_dir)))
    book = find_book(data_dir, args.book)
    out_dir = (args.out_dir or book.dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs, notes = align(book, args.pages)
    if not pairs:
        print(f"[LỖI] {book.stem}: chưa có Label.txt ở cả ch/ và vi/ (chạy bước OCR trước).")
        return 1
    stem = norm(book.stem)
    write_csv(out_dir / f"{book.stem}.csv", pairs)
    write_xlsx(out_dir / f"{book.stem}.xlsx", pairs)
    (out_dir / "align_report.txt").write_text(build_report(book, pairs, notes), encoding="utf-8")
    print(f"{stem}: {len(pairs)} hàng từ {len({p.img_id for p in pairs})} trang -> {out_dir}")
    for note in notes:
        print("  !", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
