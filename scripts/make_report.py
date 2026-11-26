"""Báo cáo so sánh trước/sau sửa lỗi, mỗi sách một file <Tên sách>_SO_SANH.xlsx, theo mẫu BichCauKyNgo1_SO_SANH.xlsx.

Chỉ đọc các file mà quy trình gán nhãn đã xuất trong outputs/ (không tải mô hình, không chạy lại quy trình):
  statistics.json         số liệu theo từng sách
  candidates_review.csv   ứng viên được chọn: hạng, confidence
  mismatch_before.xlsx    toàn bộ câu trước khi sửa (rich-text: đỏ = lệch, xám = thừa)
  mismatch_after.xlsx     toàn bộ câu sau khi sửa (xanh = đã sửa, cam = KEEP, đỏ = còn lệch) + câu trước khi sửa

Mỗi file có 5 sheet:
  0. Tổng quan | 1. Bản cũ | 2. Bản mới | 3. Thay đổi Quốc ngữ | 4. Thay đổi SinoNom OCR

Ví dụ (chạy ở thư mục dự án Alignment/):
  python scripts/make_report.py                                  # mọi sách trong outputs/ -> outputs/<Tên sách>_SO_SANH.xlsx
  python scripts/make_report.py --report-dir reports             # ghi vào thư mục khác
  python scripts/make_report.py --book "Tư Linh Truyên, Nôm"     # chỉ một sách
  python scripts/make_report.py --output-dir outputs1            # đọc thư mục kết quả khác
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # để import được package sinonom_review nằm cạnh file này

import pandas as pd
from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from xlsxwriter import Workbook

from sinonom_review.text import tokenize_nom, tokenize_qn

NOM_FONT = "Nom Na Tong"          # font của mẫu cho cột chữ Nôm; máy không có thì Excel tự thay font khác
HEADER_BG = "#EEECE7"             # màu nền tiêu đề bảng của mẫu
RED, ORANGE, GRAY = "#FF0000", "#C55A11", "#8C8C8C"

# màu run trong mismatch_*.xlsx (do export.py ghi) -> loại token
RUN_KIND = {"C00000": "red", "C55A11": "keep", "00873E": "fixed", "8C8C8C": "gray"}

SHEETS = ["0. Tổng quan", "1. Bản cũ", "2. Bản mới", "3. Thay đổi Quốc ngữ", "4. Thay đổi SinoNom OCR"]


# ---------------------------------------------------------------------------------------------- đọc outputs
@dataclass
class Sentence:
    book: str
    sid: str
    patch_id: str
    nom_before: list[str]
    nom_after: list[str]
    qn_before: list[str]
    qn_after: list[str]
    nom_kind: list = field(default_factory=list)       # theo token Nôm sau: None | red | keep | fixed | gray
    qn_kind: list = field(default_factory=list)
    nom_kind_before: list = field(default_factory=list)
    qn_kind_before: list = field(default_factory=list)

    def nom_changes(self):
        return [(i, a, b) for i, (a, b) in enumerate(zip(self.nom_before, self.nom_after)) if a != b]

    def qn_changes(self):
        return [(i, a, b) for i, (a, b) in enumerate(zip(self.qn_before, self.qn_after)) if a != b]


def _kind(font):
    rgb = getattr(getattr(font, "color", None), "rgb", None)
    return RUN_KIND.get(rgb[-6:].upper()) if isinstance(rgb, str) else None


def cell_tokens(cell, tokenizer):
    """Tách một ô (rich-text hoặc chuỗi thường) thành (danh sách token, danh sách loại màu từng token)."""
    v = cell.value
    if v is None:
        parts = []
    elif isinstance(v, CellRichText):
        parts = [(x.text, _kind(x.font)) if isinstance(x, TextBlock) else (str(x), None) for x in v]
    else:                                   # chuỗi đơn: màu nằm ở định dạng của cả ô (xlsxwriter write_string)
        parts = [(str(v), _kind(cell.font))]
    toks, kinds = [], []
    for text, k in parts:
        t = tokenizer(text)
        toks += t
        kinds += [k] * len(t)
    return toks, kinds


def _rows(path: Path):
    wb = load_workbook(path, rich_text=True)
    ws = wb.worksheets[0]
    return list(ws.iter_rows(min_row=2))


def read_sentences(out_dir: Path) -> list[Sentence]:
    before, after = _rows(out_dir / "mismatch_before.xlsx"), _rows(out_dir / "mismatch_after.xlsx")
    if len(before) != len(after):
        sys.exit(f"LỖI: mismatch_before.xlsx có {len(before)} câu, mismatch_after.xlsx có {len(after)} câu; "
                 "hai file phải do cùng một lần xuất.")
    sents = []
    for rb, ra in zip(before, after):
        # before: Sách, ID, Patch ID, Ảnh, Dòng, Nôm, Quốc ngữ, ...
        # after : Sách, ID, Patch ID, Nôm (sau), Quốc ngữ (sau), Lệch, Đã sửa, KEEP, Chưa duyệt, Nôm (trước), Quốc ngữ (trước)
        if (rb[0].value, rb[1].value) != (ra[0].value, ra[1].value):
            sys.exit(f"LỖI: hai file không cùng thứ tự câu tại {rb[0].value} / ID {rb[1].value}.")
        nom_b, kb = cell_tokens(rb[5], tokenize_nom)
        nom_a, ka = cell_tokens(ra[3], tokenize_nom)
        qn_b, kqb = cell_tokens(ra[10], tokenize_qn)
        qn_a, kq = cell_tokens(ra[4], tokenize_qn)
        if len(nom_b) != len(nom_a) or len(qn_b) != len(qn_a):
            print(f"  ! bỏ qua so sánh câu {rb[0].value} ID {rb[1].value}: số chữ/âm trước và sau khác nhau",
                  file=sys.stderr)
            nom_a, ka, qn_a, kq = nom_b, kb, qn_b, kqb
        sents.append(Sentence(book=str(ra[0].value), sid=str(ra[1].value), patch_id=str(ra[2].value or ""),
                              nom_before=nom_b, nom_after=nom_a, qn_before=qn_b, qn_after=qn_a,
                              nom_kind=ka, qn_kind=kq, nom_kind_before=kb, qn_kind_before=kqb))
    return sents


def read_selected(out_dir: Path) -> dict:
    """{(sách, ID, vị trí Nôm 1-based, side): dòng ứng viên được chọn}: để lấy hạng và confidence."""
    df = pd.read_csv(out_dir / "candidates_review.csv", encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df = df[df["selected"] == "x"]
    return {(r.book, str(r.ID), int(r.nom_pos), r.side): r for r in df.itertuples()}


# ---------------------------------------------------------------------------------------------- ghi xlsx
class Fmt:
    def __init__(self, wb: Workbook):
        self.wb = wb
        self._c: dict = {}
        self.head = wb.add_format({"bold": True, "bg_color": HEADER_BG})
        self.head_wrap = wb.add_format({"bold": True, "bg_color": HEADER_BG, "text_wrap": True, "valign": "top"})
        self.title = wb.add_format({"bold": True, "font_size": 14})
        self.bold = wb.add_format({"bold": True})
        self.red_bold = wb.add_format({"bold": True, "font_color": RED})
        self.pct = wb.add_format({})
        self.wrap = wb.add_format({"text_wrap": True, "valign": "top"})

    def get(self, *, nom=False, color=None, bold=False, italic=False):
        key = (nom, color, bold, italic)
        if key not in self._c:
            p = {}
            if nom:
                p["font_name"] = NOM_FONT
            if color:
                p["font_color"] = color
            if bold:
                p["bold"] = True
            if italic:
                p["italic"] = True
            self._c[key] = self.wb.add_format(p)
        return self._c[key]


def write_rich(ws, r, c, tokens, styles, sep, fmt: Fmt, nom: bool):
    """Ghi một ô rich-text. `styles[i]` là dict định dạng của token i; gộp các token liền kề cùng định dạng."""
    frags = []
    for i, (t, st) in enumerate(zip(tokens, styles)):
        txt = t + (sep if i < len(tokens) - 1 else "")
        f = fmt.get(nom=nom, **st)
        if frags and frags[-2] is f:
            frags[-1] += txt
        else:
            frags += [f, txt]
    if not frags:
        ws.write_string(r, c, "")
    elif len(frags) == 2:
        ws.write_string(r, c, frags[1], frags[0])
    else:
        ws.write_rich_string(r, c, *frags)


def _header(ws, fmt: Fmt, cols, widths, wrap=False):
    for j, (name, w) in enumerate(zip(cols, widths)):
        ws.write(0, j, name, fmt.head_wrap if wrap else fmt.head)
        ws.set_column(j, j, w)


def pair_style(kind):
    """Cặp không khớp từ điển tô đỏ (kể cả cặp KEEP, vì vẫn không có trong từ điển); chữ/âm thừa xám nghiêng."""
    if kind in ("red", "keep"):
        return {"color": RED}
    if kind == "gray":
        return {"color": GRAY, "italic": True}
    return {}


def _sheet_full(wb, fmt, sents, name, nom, nom_kind, qn, qn_kind):
    """Cả hai cột (chữ Nôm và Quốc ngữ) tô đỏ tại các cặp không khớp."""
    ws = wb.add_worksheet(name)
    _header(ws, fmt, ["ID", "Patch ID", "SinoNom OCR", "Chữ Quốc ngữ"], [8, 34, 34, 46])
    ws.freeze_panes(1, 1)
    for r, s in enumerate(sents, start=1):
        ws.write(r, 0, int(s.sid) if s.sid.isdigit() else s.sid)
        ws.write(r, 1, s.patch_id)
        write_rich(ws, r, 2, getattr(s, nom), [pair_style(k) for k in getattr(s, nom_kind)], "", fmt, True)
        write_rich(ws, r, 3, getattr(s, qn), [pair_style(k) for k in getattr(s, qn_kind)], " ", fmt, False)


def sheet_old(wb, fmt, sents):
    _sheet_full(wb, fmt, sents, SHEETS[1], "nom_before", "nom_kind_before", "qn_before", "qn_kind_before")


def sheet_new(wb, fmt, sents):
    _sheet_full(wb, fmt, sents, SHEETS[2], "nom_after", "nom_kind", "qn_after", "qn_kind")


def sheet_qn(wb, fmt, sents):
    ws = wb.add_worksheet(SHEETS[3])
    _header(ws, fmt, ["ID", "Sửa", "Câu CŨ", "Câu MỚI"], [8, 30, 70, 70], wrap=True)
    ws.freeze_panes(1, 0)
    n_rows = n_changes = 0
    for s in sents:
        ch = s.qn_changes()
        if not ch:
            continue
        n_rows += 1
        n_changes += len(ch)
        r = n_rows
        changed = {i for i, _, _ in ch}
        ws.write(r, 0, int(s.sid) if s.sid.isdigit() else s.sid, fmt.wrap)
        ws.write(r, 1, "; ".join(f"{a} → {b}" for _, a, b in ch), fmt.wrap)
        for c, toks in ((2, s.qn_before), (3, s.qn_after)):
            write_rich(ws, r, c, toks, [{"color": RED, "bold": True} if i in changed else {} for i in range(len(toks))],
                       " ", fmt, False)
    return n_rows, n_changes


def sheet_nom(wb, fmt, sents, selected):
    ws = wb.add_worksheet(SHEETS[4])
    _header(ws, fmt, ["ID", "Nguồn", "Sửa", "Chữ Nôm CŨ", "Chữ Nôm MỚI", "Câu Quốc ngữ"], [8, 36, 20, 36, 36, 60])
    ws.freeze_panes(1, 0)
    n_rows = n_changes = 0
    for s in sents:
        ch = s.nom_changes()
        if not ch:
            continue
        n_rows += 1
        n_changes += len(ch)
        r = n_rows
        changed = {i for i, _, _ in ch}
        src = []
        for i, _, _ in ch:
            row = selected.get((s.book, s.sid, i + 1, "NOM"))
            src.append(f"Gán nhãn · hạng {row.rank} · conf {float(row.confidence):.2f}" if row else "Gán nhãn")
        ws.write(r, 0, int(s.sid) if s.sid.isdigit() else s.sid)
        ws.write(r, 1, "; ".join(src))
        ws.write(r, 2, "; ".join(f"{a}→{b}" for _, a, b in ch), fmt.get(nom=True))
        for c, toks in ((3, s.nom_before), (4, s.nom_after)):
            write_rich(ws, r, c, toks, [{"color": RED, "bold": True} if i in changed else {} for i in range(len(toks))],
                       "", fmt, True)
        ws.write(r, 5, " ".join(s.qn_after))
    return n_rows, n_changes


# ---------------------------------------------------------------------------------------------- tổng quan
def _pct(a, b):
    return f"{100 * a / b:.2f}%" if b else "0.00%"


def stage_rows(b: dict):
    """Ba giai đoạn: (nhãn, đen, đỏ, *, đen %). Đỏ giảm dần khi áp các sửa QN rồi NOM; KEEP vẫn đỏ (không có trong từ điển)."""
    pos = b["alignment_positions"]
    star = b["extra_nom"] + b["extra_qn"]
    red0 = b["mismatch_before"]
    red1 = red0 - b["decision_QN"]
    red2 = red1 - b["decision_NOM"]
    rows = []
    for label, red in (("Trước khi sửa", red0), ("+ Sửa Quốc ngữ (QN)", red1), ("+ Sửa chữ Nôm (NOM)", red2)):
        black = pos - red - star
        rows.append((label, black, red, star, _pct(black, pos)))
    return rows, red2


def sheet_overview(wb, fmt, stats, book, qn_sheet, nom_sheet):
    """Trang tổng quan của một sách (số liệu của sách đó trong statistics.json)."""
    ws = wb.add_worksheet(SHEETS[0])
    b = stats["books"][book]
    st = stats.get("settings", {})
    w = st.get("weights", {})
    ws.set_column(0, 0, 52)
    ws.set_column(1, 4, 14)
    r = 0

    def line(text="", f=None):
        nonlocal r
        ws.write(r, 0, text, f)
        r += 1

    line(f"{book} — tổng quan sửa lỗi", fmt.title)
    r += 1
    line("Sheet trong file này:", fmt.bold)
    line(f"  • {SHEETS[1]} — dữ liệu trước khi sửa; cặp chữ Nôm – Quốc ngữ không khớp từ điển tô đỏ")
    line(f"  • {SHEETS[2]} — dữ liệu sau khi áp dụng lựa chọn của người duyệt; cặp còn không khớp tô đỏ")
    line(f"  • {SHEETS[3]} — nguyên câu cũ/mới, âm đổi tô đỏ đậm ({qn_sheet[0]} câu, {qn_sheet[1]} chỗ)")
    line(f"  • {SHEETS[4]} — nguyên cột chữ Nôm cũ/mới, có cột Nguồn ({nom_sheet[0]} câu, {nom_sheet[1]} chỗ)")
    line("  Mọi thay đổi xem ở hai sheet 3 và 4; hai sheet 1 và 2 chỉ dùng để so màu đỏ trước/sau.")
    r += 1

    line("Phương pháp", fmt.bold)
    line("  1. Dóng hàng: mỗi câu chữ Nôm (OCR) được dóng với câu Quốc ngữ bằng Levenshtein; hai chữ khớp khi từ điển có "
         "cặp (âm, chữ) đó (QuocNgu_SinoNom_TongHop3). Biến thể chính tả cổ (vị trí dấu hoà/hòa, gi/d, i/y) coi là cùng âm.")
    line("  2. Cặp lệch: chữ Nôm và âm Quốc ngữ được dóng với nhau nhưng từ điển không có cặp đó. "
         "Chữ Nôm thừa và âm thừa (*) không tính là cặp lệch.")
    line("  3. Sinh ứng viên cho mỗi cặp lệch, mọi ứng viên đều nằm trong từ điển:")
    line("       – NOM: thay chữ Nôm bằng chữ đọc được đúng âm Quốc ngữ và nằm trong 20 chữ giống hình nhất "
         "(SinoNom_Similar_Dic_v2).")
    line("       – QN: thay âm Quốc ngữ bằng một âm đọc của chữ Nôm đó, cách âm hiện tại không quá "
         f"{w.get('max_ortho', 1.2)} về khoảng cách chính tả.")
    line("       – KEEP: giữ nguyên.")
    line(f"  4. Chấm điểm bằng mô hình ngôn ngữ: {st.get('nom_model', 'SinoNomBERT')} chấm phía chữ Nôm (che chữ cần xét, "
         f"dùng {st.get('nom_context_lines', 1)} dòng trước và sau làm ngữ cảnh, softmax trên các chữ ứng viên); "
         f"{st.get('qn_model', 'Qwen2.5-0.5B')} chấm phía Quốc ngữ (log P của cả dòng, so dòng gốc với dòng đã thay một âm).")
    line(f"       Điểm NOM = {w.get('w_vis', 1.5)}·(−log(hạng hình + 1)) + {w.get('w_nlm', 0.5)}·ΔlogP(NOM); "
         f"điểm QN = −{w.get('w_ortho', 3.0)}·khoảng cách chính tả + {w.get('w_qlm', 0.3)}·ΔlogP(QN); "
         f"điểm KEEP = {w.get('keep_score', -4.0)}. Ứng viên được xếp hạng top-k theo điểm.")
    line("  5. Người duyệt: xem từng câu trên giao diện web cạnh ảnh trang gốc, chọn một ứng viên hoặc KEEP cho từng cặp lệch.")
    line("  6. Kết quả: áp dụng lựa chọn rồi so sánh trước/sau. Cặp KEEP không đổi nên vẫn không khớp từ điển và vẫn tô đỏ.")
    r += 1

    line("Kết quả theo màu (cặp chữ Nôm – âm Quốc ngữ được dóng hàng)", fmt.bold)
    line("Đen = khớp từ điển · Đỏ = không khớp · * = chữ Nôm thừa + âm Quốc ngữ thừa · Đen % = Đen / mọi vị trí dóng hàng")
    for j, h in enumerate(["Giai đoạn", "Đen", "Đỏ", "*", "Đen %"]):
        ws.write(r, j, h, fmt.head)
    r += 1
    rows, red_final = stage_rows(b)
    if red_final != b["mismatch_after"]:
        print(f"  ! {book}: đỏ tính lại {red_final} khác mismatch_after trong statistics.json {b['mismatch_after']}",
              file=sys.stderr)
    for lab, black, red, star, pct in rows:
        ws.write(r, 0, lab)
        ws.write(r, 1, black)
        ws.write(r, 2, red, fmt.red_bold)
        ws.write(r, 3, star)
        ws.write(r, 4, pct)
        r += 1
    red0, red2 = rows[0][2], rows[-1][2]
    drop = round(100 * (red0 - red2) / red0) if red0 else 0
    r += 1
    line(f"Ô đỏ giảm {red0} → {red2}  (−{drop}%)", fmt.red_bold)
    r += 1

    line("Tổng thể của sách", fmt.bold)
    for label, val in [
        ("Số câu", b["sentences"]),
        ("Số câu có cặp lệch", b["sentences_with_mismatch"]),
        ("Số cặp chữ–âm được dóng", b["aligned_pairs"]),
        ("Chữ Nôm thừa", b["extra_nom"]),
        ("Âm Quốc ngữ thừa", b["extra_qn"]),
        ("Cặp lệch có ứng viên (hiện ra để duyệt)", b["pairs_to_review"]),
        ("Cặp lệch không có ứng viên (không hiện, vẫn đỏ)", b["mismatch_no_candidate"]),
        ("Cặp đã duyệt", b["reviewed_pairs"]),
        ("Cặp chưa duyệt", max(0, b["decision_UNREVIEWED"])),
    ]:
        ws.write(r, 0, label)
        ws.write(r, 1, val)
        r += 1
    r += 1

    line("Số chỗ đã sửa theo loại", fmt.bold)
    ws.write(r, 0, "Loại", fmt.head)
    ws.write(r, 1, "Số chỗ", fmt.head)
    r += 1
    for label, val, f in [("QN — sửa Quốc ngữ", b["decision_QN"], None),
                          ("NOM — sửa chữ Nôm", b["decision_NOM"], None),
                          ("KEEP — giữ nguyên (không đổi)", b["decision_KEEP"], None),
                          ("Tổng số chỗ đã sửa (QN + NOM)", b["decision_QN"] + b["decision_NOM"], fmt.red_bold)]:
        ws.write(r, 0, label, f)
        ws.write(r, 1, val, f)
        r += 1


# ---------------------------------------------------------------------------------------------- main
def safe_filename(name: str) -> str:
    """Tên sách -> tên file hợp lệ trên Windows (giữ dấu tiếng Việt, dấu phẩy, dấu cách)."""
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return out or "sach"


def write_book_report(out_path: Path, book: str, sents: list, stats: dict, selected: dict):
    """Ghi một file báo cáo 5 sheet cho một sách. Trả về (số câu/chỗ sửa Quốc ngữ, số câu/chỗ sửa Nôm)."""
    with Workbook(str(out_path), {"strings_to_formulas": False, "strings_to_urls": False}) as wb:
        fmt = Fmt(wb)
        # Tổng quan là sheet đầu nhưng cần số câu/chỗ đổi của hai sheet cuối, nên đếm trước
        qn_cnt = (sum(1 for s in sents if s.qn_changes()), sum(len(s.qn_changes()) for s in sents))
        nom_cnt = (sum(1 for s in sents if s.nom_changes()), sum(len(s.nom_changes()) for s in sents))
        sheet_overview(wb, fmt, stats, book, qn_cnt, nom_cnt)
        sheet_old(wb, fmt, sents)
        sheet_new(wb, fmt, sents)
        qn_n = sheet_qn(wb, fmt, sents)
        nom_n = sheet_nom(wb, fmt, sents, selected)
    return qn_n, nom_n


def build_reports(out_dir: Path, report_dir: Path, only: list[str] | None = None):
    for name in ("statistics.json", "candidates_review.csv", "mismatch_before.xlsx", "mismatch_after.xlsx"):
        if not (out_dir / name).exists():
            sys.exit(f"LỖI: thiếu {out_dir / name}. Chạy quy trình gán nhãn (và bấm 💾 Lưu kết quả) trước.")
    stats = json.loads((out_dir / "statistics.json").read_text(encoding="utf-8"))
    books = [k for k in stats["books"] if k != "TOTAL"]
    if only:
        missing = [b for b in only if b not in books]
        if missing:
            sys.exit(f"LỖI: không có sách {missing} trong {out_dir}. Các sách có: {books}")
        books = only
    print(f"Đọc {out_dir} ...")
    sents = read_sentences(out_dir)
    selected = read_selected(out_dir)
    print(f"  {len(sents)} câu, {len(selected)} quyết định, {len(books)} sách")

    report_dir.mkdir(parents=True, exist_ok=True)
    for book in books:
        bs = [s for s in sents if s.book == book]
        out_path = report_dir / f"{safe_filename(book)}_SO_SANH.xlsx"
        qn_n, nom_n = write_book_report(out_path, book, bs, {**stats, "books": {book: stats["books"][book]}}, selected)
        bk = stats["books"][book]
        ok = qn_n[1] == bk["decision_QN"] and nom_n[1] == bk["decision_NOM"] and len(bs) == bk["sentences"]
        print(f"- {book}: {len(bs)} câu; sửa Quốc ngữ {qn_n[1]} chỗ ({qn_n[0]} câu), sửa chữ Nôm {nom_n[1]} chỗ "
              f"({nom_n[0]} câu) {'✔' if ok else '! KHÁC statistics.json (QN %d, NOM %d, câu %d)' % (bk['decision_QN'], bk['decision_NOM'], bk['sentences'])}")
        print(f"    → {out_path}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", default="outputs", help="thư mục kết quả của quy trình gán nhãn (mặc định: outputs)")
    ap.add_argument("--report-dir", default=None,
                    help="thư mục ghi các file <Tên sách>_SO_SANH.xlsx (mặc định: chính --output-dir)")
    ap.add_argument("--book", action="append", default=None, metavar="TÊN_SÁCH",
                    help="chỉ làm sách này (tên đúng như cột 'Sách' / statistics.json); lặp lại được")
    args = ap.parse_args()
    out_dir = Path(args.output_dir)
    build_reports(out_dir, Path(args.report_dir) if args.report_dir else out_dir, args.book)


if __name__ == "__main__":
    main()
