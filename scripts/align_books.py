"""Dóng hàng Nôm - Quốc ngữ theo heuristic 1-1 và xuất <Tên sách>.csv, <Tên sách>.xlsx.

Cách dóng (xem docs/Quy_dinh_do_an.md, "Quy định về việc dóng hàng"):

  - Mỗi bbox có id <tên văn bản>.<trang>.<dòng> (ví dụ "Nhât Tỉnh Ngâm, Nôm.006.000", xem scripts/align_rows.py). Sắp id Nôm (ch/Label.txt)
    và id Việt (vi/Label.txt) theo (trang, dòng), bỏ box rỗng, rồi ghép box thứ i của Nôm với box thứ i của Quốc ngữ.
  - Dấu miss vi / miss nôm đặt trong bước 3 của web (cache/align_rows.json) cho phép bỏ qua một vế: hàng chỉ có một vế và được ghi vào
    align_report.txt, các box sau vẫn ghép đúng nhau.
  - Tô màu trong .xlsx bằng từ điển Unihan + từ điển Nôm mở rộng + cache translit (nếu có): đen = khớp âm/dị thể, đỏ = khác âm hoặc thiếu trong từ điển.

Đầu ra (trong data/<Tên sách>/):
  <Tên sách>.csv    4 cột img_id, sentence_id, SinoNom_Char, ChuQN_txt (UTF-8 có BOM để Excel mở không lỗi chữ)
  <Tên sách>.xlsx   cùng cột; cột Nôm dùng font HAN NOM A, tô màu đen/đỏ theo quy định
  align_report.txt  báo cáo chất lượng dóng hàng, điểm tương đồng và danh sách cần soát tay

Dùng:
    python scripts/align_books.py --book "KimVânKiềuTânTruyện_金雲翹新傳"
(--book chấp nhận gõ không dấu; thiếu --book thì liệt kê sách có đủ ch/vi.)
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

from scripts import align_rows, label_io, library  # noqa: E402
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
        # số thứ tự câu đánh từ 1 và lưu đủ 3 chữ số như số trang trong id bbox: <tên ảnh bỏ .jpg>.016.024
        stem = self.img_id[:-4] if self.img_id.lower().endswith(".jpg") else self.img_id
        return f"{stem}.{self.index:03d}"


# ---------------------------------------------------------------- đọc dữ liệu
def fold(text: str) -> str:
    """Bỏ dấu, hạ chữ thường, chỉ giữ chữ-số: để --book gõ không dấu vẫn khớp tên sách."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^0-9a-z]+", "", "".join(c for c in text if not unicodedata.combining(c)).casefold())


def find_book(data_dir: Path, query: str) -> library.Book:
    books = library.list_books(data_dir)
    q = norm(query.removesuffix(".pdf"))
    exact = [b for b in books if norm(b.stem) == q or norm(b.stem).casefold() == q.casefold()]
    if len(exact) == 1:
        return exact[0]
    key = fold(q)
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
            boxes.append({"text": text, "points": pts})
    return boxes


def load_pages(book: library.Book, folder: str) -> dict[int, tuple[str, list[dict]]]:
    """{số trang: (tên ảnh, box đã sắp)} của ch/ hoặc vi/."""
    out = {}
    for key, payload in label_io.read_labels(book.dir / folder / label_io.LABEL_NAME).items():
        number = label_io.page_number(key)
        if number:
            out[number] = (key.rsplit("/", 1)[-1], page_boxes(payload))
    return out


# ---------------------------------------------------------------- dóng hàng
def align(book: library.Book, n_pages: int = 0) -> tuple[list[Pair], list[str]]:
    """Dóng hàng theo id: sắp id Nôm và id Việt (trang, dòng) rồi ghép lần lượt, có tính dấu miss vi / miss nôm đặt trong giao diện bước 3
    (scripts/align_rows.py). `n_pages` > 0: chỉ lấy tới trang Nôm thứ n_pages. Trả về (các cặp, ghi chú cho align_report)."""
    rows, notes = align_rows.pairs_for_export(book)
    if n_pages > 0:
        pages = sorted({r["nom"]["page"] for r in rows if r["nom"]})
        if pages:
            last = pages[min(n_pages, len(pages)) - 1]
            keep = max((i for i, r in enumerate(rows) if r["nom"] and r["nom"]["page"] <= last), default=-1) + 1
            rows = rows[:keep]
            notes.append(f"Chỉ lấy {min(n_pages, len(pages))} trang Nôm đầu ({len(rows)} hàng).")
    # Quy định file .csv: img_id là tên ảnh trang Nôm (không kèm thư mục); sentence_id là số thứ tự CÂU NÔM trong trang Nôm, đánh từ 1:
    # <img_id bỏ đuôi .jpg>.<số thứ tự>. Hàng có Nôm lấy sentence_id của chính câu Nôm đó; chỉ hàng có Nôm mới làm tăng số thứ tự.
    # Hàng chỉ có vế Việt (miss nôm) không có câu Nôm riêng nên dùng lại img_id và sentence_id của câu Nôm đứng ngay trước
    # (hàng Việt đứng đầu thì lấy của câu Nôm đầu tiên). Không có hàng Nôm nào thì dùng tên ảnh Việt, số thứ tự đếm theo hàng.
    first_nom = next((r["nom"]["file"] for r in rows if r["nom"]), None)
    pairs: list[Pair] = []
    counters: dict[str, int] = {}
    img_id, index = first_nom, 1
    for row in rows:
        nom, vi = row["nom"], row["vi"]
        if nom:
            img_id = nom["file"]
            counters[img_id] = index = counters.get(img_id, 0) + 1
        elif first_nom is None:
            img_id = vi["file"]
            counters[img_id] = index = counters.get(img_id, 0) + 1
        pairs.append(Pair(img_id, index, nom["text"].replace(" ", "") if nom else "", " ".join(vi["text"].split()) if vi else ""))
    return pairs, notes


def nom_len(text: str) -> int:
    return len(PUNCT_RE.sub("", text))


def qn_len(text: str) -> int:
    return len([w for w in PUNCT_RE.split(text) if w])


BLACK_HEX = "FF000000"
RED_HEX = "FFFF0000"


def _load_nomdict(data_dir: Path, book_cache_dir: Path | None = None):
    """Nạp từ điển Unihan + mở rộng + cache translit (nếu có) để tô màu và tính độ tương đồng."""
    try:
        from scripts import postocr
        from scripts.nom_dict import NomDict, TranslitCache
        nomdict = NomDict.load(data_dir)
        translit_count = 0
        if book_cache_dir:
            tp = book_cache_dir / "translit.json"
            if tp.exists():
                translit_count = nomdict.add_translit(TranslitCache(tp, use_api=False))
        return nomdict, postocr, translit_count
    except Exception as e:
        print(f"  ! Không nạp được từ điển tô màu ({e}), dùng màu đen mặc định.")
        return None, None, 0


def evaluate_pairs(pairs: list[Pair], data_dir: Path = library.DEFAULT_DATA_DIR,
                   book_cache_dir: Path | None = None) -> dict:
    """Chấm màu đen/đỏ từng chữ Nôm và tiếng Quốc ngữ, đồng thời tính độ tương đồng theo quy định."""
    nomdict, postocr, translit_count = _load_nomdict(data_dir, book_cache_dir)
    row_colors: list[tuple[list[str], list[str], dict[int, str], dict[int, str]] | None] = []
    tot_nom = tot_qn = red_nom = red_qn = 0
    red_examples: dict[tuple[str, str], int] = {}

    for p in pairs:
        nom_chars = [c for c in p.nom if not c.isspace()]
        qn_words = p.qn.split()
        tot_nom += len(nom_chars)
        tot_qn += len(qn_words)

        if nomdict and postocr and p.nom and p.qn:
            clean_qn = []
            for w in qn_words:
                cl, _ = postocr.clean_qn(w)
                clean_qn.append(cl[0] if cl else w.lower())

            aligned = postocr.align_tokens(nomdict, nom_chars, clean_qn)
            nom_color: dict[int, str] = {}
            qn_color: dict[int, str] = {}
            for i, j in aligned:
                if i is not None and j is not None:
                    k = postocr.match_kind(nomdict, nom_chars[i], clean_qn[j])
                    c = BLACK_HEX if k in (postocr.BLACK, postocr.BLUE) else RED_HEX
                    nom_color[i] = c
                    qn_color[j] = c
                    if c == RED_HEX:
                        key = (nom_chars[i], clean_qn[j])
                        red_examples[key] = red_examples.get(key, 0) + 1
                elif i is not None:
                    nom_color[i] = RED_HEX
                    key = (nom_chars[i], "(thiếu QN)")
                    red_examples[key] = red_examples.get(key, 0) + 1
                elif j is not None:
                    qn_color[j] = RED_HEX
                    key = ("(thiếu Nôm)", clean_qn[j])
                    red_examples[key] = red_examples.get(key, 0) + 1
            red_nom += sum(1 for i in range(len(nom_chars)) if nom_color.get(i, RED_HEX) == RED_HEX)
            red_qn += sum(1 for j in range(len(qn_words)) if qn_color.get(j, RED_HEX) == RED_HEX)
            row_colors.append((nom_chars, qn_words, nom_color, qn_color))
        else:
            if not p.qn:
                red_nom += len(nom_chars)
            if not p.nom:
                red_qn += len(qn_words)
            row_colors.append(None)

    denom = tot_nom + tot_qn
    similarity = (1.0 - (red_nom + red_qn) / denom) * 100.0 if denom > 0 else 0.0
    top_red = sorted(red_examples.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    return {
        "has_dict": nomdict is not None,
        "translit_count": translit_count,
        "row_colors": row_colors,
        "total_nom": tot_nom,
        "red_nom": red_nom,
        "total_qn": tot_qn,
        "red_qn": red_qn,
        "similarity": similarity,
        "top_red": top_red,
    }


def build_report(book: library.Book, pairs: list[Pair], notes: list[str], stats: dict | None = None) -> str:
    lines = [f"BÁO CÁO DÓNG HÀNG: {book.stem}", "",
             f"Kiểu dóng: 1-1 theo thứ tự dòng. Tổng {len(pairs)} hàng từ {len({p.img_id for p in pairs})} trang.", ""]
    lines.append("1. Trang lệch số dòng / thiếu trang")
    lines += [f"   - {n}" for n in notes] or ["   (không có)"]
    one_side = [p for p in pairs if not p.nom or not p.qn]
    lines += ["", "2. Hàng chỉ có một vế (cần soát tay)"]
    lines += [f"   - {p.sentence_id}: Nôm={p.nom or '(trống)'} | Quốc ngữ={p.qn or '(trống)'}" for p in one_side] or ["   (không có)"]
    # Quy định: nếu là dịch âm thì số chữ Quốc ngữ phải bằng số chữ Nôm. Thơ lục bát: lệch thường do OCR sót/thừa chữ.
    mismatch = [p for p in pairs if p.nom and p.qn and nom_len(p.nom) != qn_len(p.qn)]
    lines += ["", f"3. Số chữ Nôm khác số tiếng Quốc ngữ ({len(mismatch)}/{len(pairs)} hàng; nghi lỗi OCR hoặc ghép nhầm dòng)"]
    lines += [f"   - {p.sentence_id}: {nom_len(p.nom)} chữ Nôm / {qn_len(p.qn)} tiếng | {p.nom} | {p.qn}" for p in mismatch] or ["   (không có)"]
    if stats and stats["has_dict"]:
        lines += [
            "",
            "4. Thống kê tô màu và độ tương đồng (theo quy định đồ án)",
            f"   - Nôm: {stats['total_nom']} chữ (đen: {stats['total_nom'] - stats['red_nom']}, đỏ: {stats['red_nom']})",
            f"   - Quốc ngữ: {stats['total_qn']} tiếng (đen: {stats['total_qn'] - stats['red_qn']}, đỏ: {stats['red_qn']})",
            f"   - Độ tương đồng: {stats['similarity']:.2f}% "
            f"(1 - ({stats['red_nom']} + {stats['red_qn']}) / ({stats['total_nom']} + {stats['total_qn']}))",
            f"   - Cache phiên âm đã nạp: {stats['translit_count']} ký tự từ cache/translit.json (nếu có).",
        ]
        if stats["top_red"]:
            lines.append("   - Một số cặp đỏ xuất hiện nhiều (PUA/khác nghĩa/nghi lỗi OCR):")
            for (ch, qn), cnt in stats["top_red"]:
                lines.append(f"     * {ch} <-> {qn}: {cnt} lần")
    lines += [
        "",
        "Ghi chú",
        "   - Tô màu đen/đỏ dựa trên đối chiếu Unihan + từ điển Hán-Nôm mở rộng (nom_na_viet.json) + nom_bo_sung.json + cache translit.",
        "   - Một số ký tự PUA hoặc cặp nghi sai OCR vẫn được giữ nguyên màu đỏ để phản ánh trung thực kết quả kiểm tra.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- xuất file
def write_csv(path: Path, pairs: list[Pair]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM: Excel nhận UTF-8, không lỗi font/lệch cột
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(COLUMNS)
        w.writerows([p.img_id, p.sentence_id, p.nom, p.qn] for p in pairs)


def write_xlsx(path: Path, pairs: list[Pair], data_dir: Path = library.DEFAULT_DATA_DIR,
               book_cache_dir: Path | None = None, stats: dict | None = None) -> dict:
    from openpyxl import Workbook
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Alignment, Font

    if stats is None:
        stats = evaluate_pairs(pairs, data_dir=data_dir, book_cache_dir=book_cache_dir)

    wb = Workbook()
    ws = wb.active
    ws.title = "align"
    ws.append(COLUMNS)
    for c in ws[1]:
        c.font = Font(name=LATIN_FONT, bold=True)

    for p, rc in zip(pairs, stats["row_colors"]):
        row = ws.max_row + 1
        if rc is not None:
            nom_chars, qn_words, nom_color, qn_color = rc
            nom_blocks = [TextBlock(InlineFont(rFont=NOM_FONT, color=nom_color.get(i, RED_HEX), sz=14), ch)
                          for i, ch in enumerate(nom_chars)]
            qn_blocks = []
            for j, w in enumerate(qn_words):
                if j > 0:
                    qn_blocks.append(TextBlock(InlineFont(rFont=LATIN_FONT, color=BLACK_HEX, sz=12), " "))
                qn_blocks.append(TextBlock(InlineFont(rFont=LATIN_FONT, color=qn_color.get(j, RED_HEX), sz=12), w))

            ws.append([p.img_id, p.sentence_id, CellRichText(nom_blocks), CellRichText(qn_blocks)])
        else:
            ws.append([p.img_id, p.sentence_id, p.nom, p.qn])
            ws.cell(row, 3).font = Font(name=NOM_FONT, color=BLACK_HEX if (p.qn or not stats["has_dict"]) else RED_HEX, size=14)
            ws.cell(row, 4).font = Font(name=LATIN_FONT, color=BLACK_HEX if (p.nom or not stats["has_dict"]) else RED_HEX, size=12)

        ws.cell(row, 1).font = ws.cell(row, 2).font = Font(name=LATIN_FONT, color=BLACK_HEX)
        for col in (1, 2, 3, 4):
            ws.cell(row, col).alignment = Alignment(vertical="center", wrap_text=col == 4)
    for col, width in zip("ABCD", (34, 34, 38, 52)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", help="Tên sách (thư mục trong data/), gõ không dấu cũng được")
    ap.add_argument("--pages", type=int, default=0, help="Số trang Nôm đầu tiên cần dóng (mặc định 0 = toàn bộ sách)")
    ap.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/")
    ap.add_argument("--out-dir", type=Path, help="Thư mục ghi kết quả (mặc định: thư mục của sách)")
    ap.add_argument("--no-xlsx", action="store_true",
                    help="chỉ xuất .csv và align_report.txt, không tô màu và không xuất .xlsx (không cần từ điển Unihan); web dùng cờ này")
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
    stats = None if args.no_xlsx else evaluate_pairs(pairs, data_dir=data_dir, book_cache_dir=book.cache_dir)
    write_csv(out_dir / f"{book.stem}.csv", pairs)
    if stats is not None:
        write_xlsx(out_dir / f"{book.stem}.xlsx", pairs, data_dir=data_dir, book_cache_dir=book.cache_dir, stats=stats)
    (out_dir / "align_report.txt").write_text(build_report(book, pairs, notes, stats=stats), encoding="utf-8")
    sim_info = f", tương đồng={stats['similarity']:.2f}%" if stats and stats["has_dict"] else ""
    print(f"{stem}: {len(pairs)} hàng từ {len({p.img_id for p in pairs})} trang{sim_info} -> {out_dir}")
    for note in notes:
        print("  !", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
