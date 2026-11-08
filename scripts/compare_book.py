"""Hậu xử lý OCR N trang Nôm đầu của một sách và xuất file so sánh <Tên>_SO_SANH.xlsx (giống BichCauKyNgo1_SO_SANH.xlsx).

  Bản cũ  = kết quả OCR (ch/Label.txt, vi/Label.txt) ghép 1-1 theo dòng, chỉ chuẩn hoá định dạng (chữ thường, bỏ dấu câu,
            bỏ số câu ở lề / ký tự lạ).
  Bản mới = sau khi sửa: Tầng 1 sửa Quốc ngữ theo âm chữ Nôm, Tầng 2 đổi chữ Nôm giản thể -> phồn thể (scripts/postocr.py).

Sheet: 0. Tổng quan | 1. Bản cũ | 2. Bản mới | 3. Thay đổi Quốc ngữ | 4. Thay đổi SinoNom OCR.
Màu chữ Nôm: đen = khớp âm, xanh = khớp qua dị thể hoặc chỉ khác dấu thanh/dấu mũ (âm Nôm biến từ âm Hán-Việt),
đỏ = không khớp / không có trong từ điển.

Dùng:
    python scripts/nom_dict.py build                     # một lần: dựng từ điển Unihan
    python scripts/compare_book.py --book "Nhat Tinh Ngam" --pages 5 [--no-api]
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import library, postocr  # noqa: E402
from scripts.align_books import find_book, load_pages  # noqa: E402
from scripts.nom_dict import NomDict, TranslitCache  # noqa: E402
from scripts.postocr import BLACK, BLUE, RED, STAR, Edit, Line  # noqa: E402

NOM_FONT = "Nom Na Tong"
LATIN_FONT = "Calibri"
HEADER_FILL = "EEECE7"
COLOR_RGB = {BLACK: "FF000000", BLUE: "FF0000FF", RED: "FFFF0000"}
EDIT_RGB = "FFFF0000"
STAGES = ["Trước khi sửa", "+ Tầng 1: sửa Quốc ngữ", "+ Tầng 2: chữ Nôm theo dị thể"]


# ---------------------------------------------------------------- đọc dữ liệu
def load_lines(book: library.Book, n_pages: int) -> list[Line]:
    ch, vi = load_pages(book, "ch"), load_pages(book, "vi")
    shared = [n for n in sorted(ch) if n in vi][:n_pages]
    lines: list[Line] = []
    for n in shared:
        img_id = ch[n][0]
        nom_boxes, qn_boxes = ch[n][1], vi[n][1]
        for i in range(max(len(nom_boxes), len(qn_boxes))):
            nb = nom_boxes[i] if i < len(nom_boxes) else None
            qb = qn_boxes[i] if i < len(qn_boxes) else None
            qn, noise = postocr.clean_qn(qb["text"]) if qb else ([], [])
            pts = (nb or qb)["points"]
            lines.append(Line(
                id=f"image{n}_{i + 1}",
                sentence_id=f"{img_id[:-4]}.{i + 1}",
                img_id=img_id,
                box=[[float(x), float(y)] for x, y in pts],
                nom=postocr.clean_nom(nb["text"]) if nb else [],
                qn=qn,
                noise=noise,
            ))
    return lines


def enrich(nomdict: NomDict, cache: TranslitCache, lines: list[Line]) -> list[Line]:
    """Thêm âm của API vào từ điển: âm theo ngữ cảnh của cả dòng và âm của từng chữ."""
    for ln in lines:
        if not ln.nom:
            continue
        ctx = cache.line(ln.nom_text)
        if ctx and len(ctx) == len(ln.nom):
            ln.context = ctx
            for ch, r in zip(ln.nom, ctx):
                nomdict.add(ch, r)
    for ch in sorted({c for ln in lines for c in ln.nom}):
        nomdict.add(ch, cache.char(ch))
    return lines


# ---------------------------------------------------------------- xuất xlsx
def _rich(parts: list[tuple[str, str | None, bool]], font: str):
    """parts: [(text, màu ARGB | None, đậm)] -> CellRichText."""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont

    blocks = []
    for text, color, bold in parts:
        kw = {"rFont": font, "sz": 11, "b": bold}
        if color:
            kw["color"] = color
        blocks.append(TextBlock(InlineFont(**kw), text))
    return CellRichText(blocks)


def _nom_cell(nomdict, ln: Line, bold_pos: set[int] = frozenset(), red_pos: set[int] | None = None):
    if not ln.nom:
        return ""
    if red_pos is not None:   # sheet 4: chữ đổi tô đỏ đậm, còn lại mặc định
        return _rich([(c, EDIT_RGB if i in red_pos else None, i in red_pos) for i, c in enumerate(ln.nom)], NOM_FONT)
    colors, _ = postocr.color_line(nomdict, ln.nom, ln.qn)
    return _rich([(c, COLOR_RGB[colors[i]], i in bold_pos) for i, c in enumerate(ln.nom)], NOM_FONT)


def _qn_cell(words: list[str], marked: set[int]):
    if not marked:
        return " ".join(words)
    parts = []
    for j, w in enumerate(words):
        sep = "" if j == len(words) - 1 else " "
        parts.append((w + sep, EDIT_RGB if j in marked else None, j in marked))
    return _rich(parts, LATIN_FONT)


def _header(ws, row: int, values: list[str]) -> None:
    from openpyxl.styles import Font, PatternFill

    for col, v in enumerate(values, 1):
        c = ws.cell(row, col, v)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor=HEADER_FILL)


def _changed_qn(edits: list[Edit], ln: Line) -> set[int]:
    t1 = [e for e in edits if e.layer == "T1" and e.line_id == ln.id]
    if any(e.pos == -1 for e in t1):
        return set(range(len(ln.qn)))
    return {e.pos for e in t1}


def _sheet_lines(wb, title: str, nomdict, lines: list[Line], edits: list[Edit] | None) -> None:
    ws = wb.create_sheet(title)
    _header(ws, 1, ["ID", "Image Box", "SinoNom OCR", "Chữ Quốc ngữ"])
    for r, ln in enumerate(lines, 2):
        bold = {e.pos for e in edits if e.layer == "T2" and e.line_id == ln.id} if edits else set()
        ws.cell(r, 1, ln.id)
        ws.cell(r, 2, str(ln.box))
        ws.cell(r, 3, _nom_cell(nomdict, ln, bold))
        ws.cell(r, 4, _qn_cell(ln.qn, _changed_qn(edits, ln) if edits else set()))
    for col, w in zip("ABCD", (16.7109375, 46.7109375, 30.7109375, 46.7109375)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "B2"


def _sheet_qn_changes(wb, old: dict[str, Line], new: list[Line], edits: list[Edit]) -> None:
    ws = wb.create_sheet("3. Thay đổi Quốc ngữ")
    _header(ws, 1, ["ID", "Sửa", "Câu CŨ", "Câu MỚI"])
    r = 2
    for ln in new:
        t1 = [e for e in edits if e.layer == "T1" and e.line_id == ln.id]
        if not t1:
            continue
        before = old[ln.id]
        if any(e.pos == -1 for e in t1):
            fix = "cả dòng → máy phiên"
            marked_old, marked_new = set(range(len(before.qn))), set(range(len(ln.qn)))
        else:
            fix = "; ".join(f"{e.old} → {e.new}" for e in t1)
            marked_old = marked_new = {e.pos for e in t1}
        ws.cell(r, 1, ln.id)
        ws.cell(r, 2, fix)
        ws.cell(r, 3, _qn_cell(before.qn, marked_old))
        ws.cell(r, 4, _qn_cell(ln.qn, marked_new))
        r += 1
    for col, w in zip("ABCD", (16.7109375, 20.7109375, 78.7109375, 78.7109375)):
        ws.column_dimensions[col].width = w


def _sheet_nom_changes(wb, nomdict, old: dict[str, Line], new: list[Line], edits: list[Edit]) -> None:
    ws = wb.create_sheet("4. Thay đổi SinoNom OCR")
    _header(ws, 1, ["ID", "Nguồn", "Sửa", "Chữ Nôm CŨ", "Chữ Nôm MỚI", "Câu Quốc ngữ"])
    r = 2
    for ln in new:
        t2 = [e for e in edits if e.layer == "T2" and e.line_id == ln.id]
        if not t2:
            continue
        pos = {e.pos for e in t2}
        qn_pos = {j for i, j in postocr.align_tokens(nomdict, ln.nom, ln.qn) if i in pos and j is not None}
        ws.cell(r, 1, ln.id)
        ws.cell(r, 2, "; ".join(sorted({e.source for e in t2})))
        ws.cell(r, 3, "; ".join(f"{e.old}→{e.new}" for e in t2))
        ws.cell(r, 4, _nom_cell(nomdict, old[ln.id], red_pos=pos))
        ws.cell(r, 5, _nom_cell(nomdict, ln, red_pos=pos))
        ws.cell(r, 6, _qn_cell(ln.qn, qn_pos))
        r += 1
    for col, w in zip("ABCDEF", (16.7109375, 28.7109375, 18.7109375, 46.7109375, 46.7109375, 70.7109375)):
        ws.column_dimensions[col].width = w


def _sheet_overview(wb, book_name: str, old: list[Line], new: list[Line], counts: list[dict], edits: list[Edit]) -> None:
    from openpyxl.styles import Font

    ws = wb.active
    ws.title = "0. Tổng quan"
    rows: list[tuple] = []

    def add(*values, bold=False, header=False, size=None):
        rows.append((values, bold, header, size))

    add(f"{book_name} — tổng quan sửa lỗi", bold=True, size=14)
    add()
    add("Sheet trong file này:", bold=True)
    add("  • 1. Bản cũ — chỉ OCR + ghép, chưa sửa gì")
    add("  • 2. Bản mới — đã sửa, chữ Nôm đổi in đậm, tiếng Quốc ngữ đổi tô đỏ đậm")
    add("  • 3. Thay đổi Quốc ngữ — nguyên câu cũ/mới, từ đổi tô đỏ đậm")
    add("  • 4. Thay đổi SinoNom OCR — nguyên dòng chữ Nôm cũ/mới, có cột Nguồn")
    add()
    add("Các bước hậu xử lý (chỉ dùng một kết quả OCR):", bold=True)
    add("  • Bước 0 — làm sạch: chữ thường, bỏ dấu câu, bỏ số câu ở lề và ký tự lạ (áp dụng cho cả Bản cũ)")
    add("  • Tầng 1 — sửa Quốc ngữ: dóng từng chữ Nôm với từng tiếng, đổi tiếng đỏ sang âm đọc gần nhất của chữ Nôm "
        f"(khác ≤ 1 chữ cái); tiếng chỉ khác dấu thanh/dấu mũ được giữ nguyên vì thường là âm Nôm; "
        "dòng có quá nửa tiếng sai cấu trúc âm tiết thì thay bằng bản máy phiên")
    add("  • Tầng 2 — sửa chữ Nôm theo dị thể: chữ giản thể chỉ khớp âm qua dạng phồn thể thì đổi sang phồn thể")
    add("  • Từ điển: Unihan kVietnamese + dị thể Unihan; âm theo ngữ cảnh và âm từng chữ của API Kim Hán Nôm")
    add()
    add("Kết quả theo màu (Đen = khớp âm, Xanh = khớp qua dị thể / chỉ khác dấu, Đỏ = không khớp, "
        "* = tiếng không có chữ Nôm)", bold=True)
    add("Giai đoạn", "Đen", "Xanh", "Đỏ", "*", "Đen+Xanh", header=True)
    for name, c in zip(STAGES, counts):
        add(name, c[BLACK], c[BLUE], c[RED], c[STAR], f"{postocr.similarity(c):.2%}")
    add()
    r0, r1 = counts[0][RED], counts[-1][RED]
    add(f"Ô đỏ giảm {r0} → {r1}  (−{(r0 - r1) / r0:.0%})" if r0 else "Không có ô đỏ")
    add()
    noise = Counter(n.kind for ln in old for n in ln.noise)
    add("Tổng thể (các trang đã xử lý)", bold=True)
    add("Số trang", len({ln.img_id for ln in old}))
    add("Số dòng (dòng chữ Nôm)", len(old))
    add("Số chữ Nôm", sum(len(ln.nom) for ln in new))
    add("Dòng có Quốc ngữ từ OCR", sum(ln.qn_source == "ocr" for ln in new))
    add("Dòng có Quốc ngữ do máy phiên", sum(ln.qn_source == "may_phien" for ln in new))
    add("Đoạn nhiễu đã loại", sum(noise.values()))
    add("   • số thứ tự câu ở lề lẫn vào dòng Quốc ngữ", noise.get("so_cau", 0))
    add("   • ký tự lạ (không phải chữ Quốc ngữ)", noise.get("ky_tu_la", 0))
    add()
    t1 = [e for e in edits if e.layer == "T1"]
    t2 = [e for e in edits if e.layer == "T2"]
    add("Số chỗ đã sửa theo tầng", bold=True)
    add("Tầng", "Số chỗ", header=True)
    add("Tầng 1 — sửa Quốc ngữ (từng tiếng)", sum(e.pos != -1 for e in t1))
    add("Tầng 1 — thay cả dòng bằng máy phiên", sum(e.pos == -1 for e in t1))
    add("Tầng 2 — thay chữ Nôm (dị thể)", len(t2))
    add("Tổng", len(edits))

    for r, (values, bold, header, size) in enumerate(rows, 1):
        if header:
            _header(ws, r, list(values))
            continue
        for col, v in enumerate(values, 1):
            c = ws.cell(r, col, v)
            if bold or size:
                c.font = Font(bold=bold, size=size or 11)
    ws.column_dimensions["A"].width = 34.7109375
    ws.column_dimensions["B"].width = 13.7109375


def write_xlsx(path: Path, book_name: str, nomdict, old: list[Line], new: list[Line],
               counts: list[dict], edits: list[Edit]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    old_by_id = {ln.id: ln for ln in old}
    _sheet_overview(wb, book_name, old, new, counts, edits)
    _sheet_lines(wb, "1. Bản cũ", nomdict, old, None)
    _sheet_lines(wb, "2. Bản mới", nomdict, new, edits)
    _sheet_qn_changes(wb, old_by_id, new, edits)
    _sheet_nom_changes(wb, nomdict, old_by_id, new, edits)
    wb.save(path)


# ---------------------------------------------------------------- chạy
def run(lines: list[Line], nomdict) -> tuple[list[Line], list[dict], list[Edit]]:
    """Trả (Bản mới, số đếm màu sau từng mốc, các chỗ sửa)."""
    counts = [postocr.count_colors(nomdict, lines)]
    edits: list[Edit] = []
    cur = lines
    for step in (postocr.correct_qn, postocr.correct_nom):
        nxt = []
        for ln in cur:
            fixed, e = step(nomdict, ln)
            nxt.append(fixed)
            edits.extend(e)
        cur = nxt
        counts.append(postocr.count_colors(nomdict, cur))
    return cur, counts, edits


def short_name(stem: str) -> str:
    """'Nhât Tỉnh Ngâm, Nôm_Y' -> 'NhatTinhNgam'."""
    head = stem.split(",")[0].replace("đ", "d").replace("Đ", "D")
    head = "".join(c for c in unicodedata.normalize("NFKD", head) if not unicodedata.combining(c))
    return re.sub(r"[^0-9A-Za-z]+", "", head.title())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", required=True, help="Tên sách (thư mục trong data/), gõ không dấu cũng được")
    ap.add_argument("--pages", type=int, default=5, help="Số trang Nôm đầu tiên (mặc định 5)")
    ap.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/")
    ap.add_argument("--out", type=Path, help="File xlsx ra (mặc định data/<sách>/<Tên>_SO_SANH.xlsx)")
    ap.add_argument("--no-api", action="store_true", help="Không gọi API Kim Hán Nôm, chỉ dùng từ điển + cache")
    args = ap.parse_args()

    data_dir = args.data.resolve()
    book = find_book(data_dir, args.book)
    lines = load_lines(book, args.pages)
    if not lines:
        print(f"[LỖI] {book.stem}: chưa có Label.txt ở cả ch/ và vi/.")
        return 1

    nomdict = NomDict.load(data_dir)
    cache = TranslitCache(book.dir / "cache" / "translit.json", use_api=not args.no_api)
    enrich(nomdict, cache, lines)
    new, counts, edits = run(lines, nomdict)

    out = (args.out or book.dir / f"{short_name(book.stem)}_SO_SANH.xlsx").resolve()
    write_xlsx(out, book.stem, nomdict, lines, new, counts, edits)

    print(f"{library.norm(book.stem)}: {len(lines)} dòng, {len({ln.img_id for ln in lines})} trang"
          f" (API gọi mới {cache.calls} lần) -> {out}")
    for name, c in zip(STAGES, counts):
        print(f"  {name:32s} đen={c[BLACK]:4d} xanh={c[BLUE]:3d} đỏ={c[RED]:4d} *={c[STAR]:3d}"
              f"  Đen+Xanh={postocr.similarity(c):.2%}")
    print(f"  Sửa: tầng 1 = {sum(e.layer == 'T1' for e in edits)}, tầng 2 = {sum(e.layer == 'T2' for e in edits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
