"""Dữ liệu của bước 3 (Dóng hàng) trên web: id của từng bbox, ghép Nôm - Việt theo id, dấu miss và ô dịch âm.

Id của một bbox:   <tên văn bản>.<số trang>.<số dòng>      ví dụ  Nhât Tỉnh Ngâm, Nôm.006.000
  tên văn bản  tên sách bỏ hậu tố "_Y" (nếu có); số trang = số trong tên ảnh (<tên>.006.jpg); số dòng = vị trí box trong dòng nhãn của
  ảnh trong Label.txt, đếm từ 0. Id suy ra từ Label.txt nên không ghi vào file nhãn (PPOCRLabel không bị ảnh hưởng).
  Nôm (ch/) và Việt (vi/) đánh id cùng quy tắc; hai bên phân biệt bằng thư mục, nên khoá lưu là "ch:<id>" hoặc "vi:<id>".

Dóng hàng:  sắp id Nôm và id Việt theo (trang, dòng), rồi ghép lần lượt cái thứ i của Nôm với cái thứ i của Việt.
  Nút lên / xuống trên mỗi box đổi chỗ box đó với box kề trước / kề sau trong thứ tự ghép của vế mình (không sửa Label.txt, id không đổi).
  Box không có chữ bị bỏ (như align_books.py). Dấu miss cho phép lệch nhau:
    miss vi   (đặt trên ô Nôm)  Nôm này không có dòng Việt: hàng chỉ có vế Nôm, dòng Việt kế tiếp ghép với ô Nôm kế tiếp
    miss nôm  (đặt trên ô Việt) Việt này không có dòng Nôm: hàng chỉ có vế Việt, dòng Nôm kế tiếp ghép với ô Việt kế tiếp

Nguồn: mặc định vế Nôm lấy từ ch/ và vế Việt lấy từ vi/ của chính sách đang mở. Có thể đổi mỗi vế sang một thư mục khác của sách này
  hoặc của sách khác (ví dụ sách chỉ có Nôm lấy bản Quốc ngữ từ vi/ của một sách khác); id khi đó mang tên văn bản của sách nguồn.

Lưu ở data/<Tên sách>/cache/align_rows.json:
  {"sources": {"ch": {"book": "<tên sách>", "folder": "ch"}, "vi": {"book": "...", "folder": "vi"}},   (không có = mặc định)
   "marks": {"ch:<id>": "vi", "vi:<id>": "nom"},
   "auto": {"marks": [khoá dấu miss do so khớp tự động tạo], "move_roles": ["ch"], ...tóm tắt lần so khớp},
   "edits": {"ch:<id>": "<chữ Nôm đã sửa>", "vi:<id>": "<chữ Việt đã sửa>"},     (chưa ghi vào Label.txt, nút Lưu mới ghi)
   "moves": {"ch": [["<id a>", "<id b>"], ...], "vi": [...]},    (các lần đổi chỗ hai box kề nhau khi bấm lên / xuống, áp theo thứ tự)
   "dich":  {"<id Nôm>": {"src": "<chữ Nôm lúc dịch>", "text": "<âm Quốc ngữ>", "manual": false}}}
Ô dịch hết hiệu lực khi chữ Nôm của box đổi (src khác chữ hiện tại).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from . import label_io, library
from .library import norm

STATE_NAME = "align_rows.json"
SIDES = ("ch", "vi")   # hai vế: "ch" là vế Nôm, "vi" là vế Việt (tên theo thư mục mặc định của mỗi vế)
MISS_OF_SIDE = {"ch": "vi", "vi": "nom"}   # dấu miss đặt trên ô của thư mục này: thiếu vế "vi" / "nom"
CROP_PAD = 4
CROP_MAX_SIDE = 420

_lock = threading.RLock()


# ---------------------------------------------------------------- id
def book_label(book: library.Book) -> str:
    """Tên văn bản trong id: tên sách bỏ hậu tố "_Y"."""
    stem = norm(book.stem)
    return stem[:-2] if stem.endswith("_Y") else stem


def box_id(book: library.Book, page: int, line: int) -> str:
    return f"{book_label(book)}.{page:03d}.{line:03d}"


def _id_key(box: dict) -> tuple[int, int]:
    return box["page"], box["line"]


# ---------------------------------------------------------------- đọc box
def load_side(book: library.Book, folder: str) -> list[dict]:
    """Mọi box của thư mục `folder` của sách `book` theo thứ tự id (trang, dòng), kể cả box không có chữ (box["text"] rỗng)."""
    out: list[dict] = []
    for key, payload in label_io.read_labels(book.dir / folder / label_io.LABEL_NAME).items():
        page = label_io.page_number(key)
        if not page:
            continue
        try:
            boxes = json.loads(payload)
        except ValueError:
            continue
        for line, b in enumerate(boxes):
            points = b.get("points") or []
            if len(points) < 3:
                continue
            out.append({"id": box_id(book, page, line), "folder": folder, "page": page, "line": line,
                        "text": " ".join(norm(str(b.get("transcription", ""))).split()),
                        "points": points, "file": key.split("/", 1)[-1], "dir": str(book.dir / folder)})
    out.sort(key=_id_key)
    return out


# ---------------------------------------------------------------- trạng thái lưu
def state_file(book: library.Book) -> Path:
    return book.cache_dir / STATE_NAME


def read_state(book: library.Book) -> dict:
    state = {"sources": {}, "marks": {}, "dich": {}, "moves": {}, "edits": {}, "auto": {}}
    try:
        loaded = json.loads(state_file(book).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if isinstance(loaded, dict):
        for k in state:
            if isinstance(loaded.get(k), dict):
                state[k] = loaded[k]
    return state


def _write_state(book: library.Book, state: dict) -> None:
    library.write_json_atomic(state_file(book), state)


def set_mark(book: library.Book, side: str, box: str, miss: bool) -> None:
    if side not in SIDES:
        raise ValueError(f"Thư mục không hợp lệ: {side!r}")
    with _lock:
        state = read_state(book)
        key = f"{side}:{box}"
        if miss:
            state["marks"][key] = MISS_OF_SIDE[side]
        else:
            state["marks"].pop(key, None)
        _write_state(book, state)


def set_dich(book: library.Book, box: str, text: str, src: str, manual: bool) -> None:
    with _lock:
        state = read_state(book)
        text = " ".join(text.split())
        if text:
            state["dich"][box] = {"src": src, "text": text, "manual": manual}
        else:
            state["dich"].pop(box, None)
        _write_state(book, state)


# ---------------------------------------------------------------- nguồn của hai vế
def _book_by_stem(data_dir: Path, stem: str) -> library.Book | None:
    wanted = norm(stem)
    return next((b for b in library.list_books(data_dir) if norm(b.stem) == wanted), None)


def _folder_has_labels(book: library.Book, folder: str) -> bool:
    return bool(folder) and "/" not in folder and "\\" not in folder and not folder.startswith(".") and folder != library.CACHE_NAME \
        and (book.dir / folder / label_io.LABEL_NAME).is_file()


def source_of(book: library.Book, role: str, state: dict | None = None) -> tuple[library.Book, str]:
    """(sách nguồn, thư mục nguồn) của vế `role` ("ch" = Nôm, "vi" = Việt). Không đặt hoặc nguồn đã mất thì dùng thư mục mặc định của chính sách."""
    saved = ((state or read_state(book))["sources"] or {}).get(role)
    if isinstance(saved, dict):
        src = _book_by_stem(book.dir.parent, str(saved.get("book", "")))
        folder = str(saved.get("folder", ""))
        if src is not None and _folder_has_labels(src, folder):
            return src, folder
    return book, role


def set_source(book: library.Book, role: str, src_stem: str, folder: str) -> None:
    if role not in SIDES:
        raise ValueError(f"Vế không hợp lệ: {role!r}")
    src = _book_by_stem(book.dir.parent, src_stem)
    if src is None or not _folder_has_labels(src, folder):
        raise ValueError("Nguồn này không có Label.txt.")
    with _lock:
        state = read_state(book)
        if (norm(src.stem), folder) == (norm(book.stem), role):
            state["sources"].pop(role, None)       # trở về mặc định
        else:
            state["sources"][role] = {"book": src.stem, "folder": folder}
        _write_state(book, state)


def load_role(book: library.Book, role: str, state: dict | None = None) -> list[dict]:
    """Box của vế `role` theo nguồn đã chọn, với chữ đã sửa (chưa lưu vào Label.txt) thay vào: box["edited"] cho biết box nào đã sửa."""
    state = state or read_state(book)
    src, folder = source_of(book, role, state)
    edits = state["edits"] or {}
    boxes = load_side(src, folder)
    for b in boxes:
        key = f"{role}:{b['id']}"
        b["edited"] = key in edits
        if b["edited"]:
            b["text"] = _clean(edits[key])
    return boxes


def source_info(book: library.Book, state: dict | None = None) -> dict:
    """Nguồn hiện tại của hai vế: {"ch": {"book", "folder"}, "vi": {...}}."""
    state = state or read_state(book)
    out = {}
    for role in SIDES:
        src, folder = source_of(book, role, state)
        out[role] = {"book": src.stem, "folder": folder}
    return out


def foreign_sources(book: library.Book) -> list[dict]:
    """Vế nào đang lấy nguồn từ sách khác với sách đang mở: [{"role", "name", "book", "folder"}] (rỗng khi cả hai vế là của sách đang mở)."""
    state = read_state(book)
    out = []
    for role, name in (("ch", "Nôm"), ("vi", "Việt")):
        src, folder = source_of(book, role, state)
        if norm(src.stem) != norm(book.stem):
            out.append({"role": role, "name": name, "book": src.stem, "folder": folder})
    return out


def list_sources(book: library.Book) -> list[dict]:
    """Mọi thư mục có Label.txt của mọi sách trong data/: [{"book", "folder", "images", "current": bool}], sách đang mở đứng đầu."""
    books = library.list_books(book.dir.parent)
    books.sort(key=lambda b: (norm(b.stem) != norm(book.stem), norm(b.stem)))
    out = []
    for b in books:
        try:
            folders = sorted(p.name for p in b.dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in folders:
            if _folder_has_labels(b, folder):
                images = len(label_io.read_labels(b.dir / folder / label_io.LABEL_NAME))
                out.append({"book": b.stem, "folder": folder, "images": images, "current": norm(b.stem) == norm(book.stem)})
    return out


def source_counts(book: library.Book) -> dict[str, int]:
    """Số ảnh có nhãn của nguồn hai vế (cho trạng thái "sẵn sàng" của bước 3)."""
    state = read_state(book)
    counts = {}
    for role in SIDES:
        src, folder = source_of(book, role, state)
        counts[role] = len(label_io.read_labels(src.dir / folder / label_io.LABEL_NAME))
    return counts


def _clean(text) -> str:
    return " ".join(norm(str(text)).split())


def _raw_texts(book: library.Book, role: str, state: dict) -> dict[str, str]:
    """{id: chữ trong Label.txt} của vế `role` (chưa áp chữ đã sửa)."""
    src, folder = source_of(book, role, state)
    return {b["id"]: b["text"] for b in load_side(src, folder)}


def _put_edit(state: dict, role: str, box: str, text: str, raw: str) -> bool:
    """Ghi chữ đã sửa của một box vào state; trùng chữ trong Label.txt thì bỏ lần sửa. Trả về True nếu state đổi."""
    key = f"{role}:{box}"
    text = _clean(text)
    before = state["edits"].get(key)
    if text == raw:
        state["edits"].pop(key, None)
    else:
        state["edits"][key] = text
    return state["edits"].get(key) != before


def set_text(book: library.Book, side: str, box: str, text: str) -> None:
    """Sửa chữ của một box (chưa ghi vào Label.txt, bấm Lưu mới ghi)."""
    if side not in SIDES:
        raise ValueError(f"Vế không hợp lệ: {side!r}")
    with _lock:
        state = read_state(book)
        raw = _raw_texts(book, side, state)
        if box not in raw:
            raise ValueError("Không có box này.")
        _put_edit(state, side, box, text, raw[box])
        _write_state(book, state)


def reset_edits(book: library.Book) -> None:
    """Bỏ mọi chữ đã sửa mà chưa lưu vào Label.txt."""
    with _lock:
        state = read_state(book)
        state["edits"] = {}
        _write_state(book, state)


def _fold(text) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text.casefold() if c.isalnum() and not unicodedata.combining(c))


def import_xlsx(book: library.Book, data: bytes) -> dict:
    """Nhập file .xlsx có cột Patch ID (id bbox Nôm), Label (chữ Nôm) và Vietnamese (chữ Việt).

    Patch ID khớp với id bbox Nôm của vế Nôm: chữ Nôm của box đó đổi thành Label; box Việt đang ghép với box Nôm đó (theo cách dóng hàng hiện
    tại) đổi thành Vietnamese. Ô trống thì bỏ qua. Chỉ ghi vào danh sách chữ đã sửa (chưa vào Label.txt, bấm Lưu mới ghi).
    Trả về {"rows", "matched", "nom_changed", "vi_changed", "no_vi", "unmatched": [tối đa 20 id], "unmatched_count"}."""
    import io

    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Không đọc được file .xlsx: {e}")
    header = None
    for ws in wb.worksheets:
        for r, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True)):
            cols = {_fold(c): i for i, c in enumerate(row) if c is not None}
            if "patchid" in cols and ("label" in cols or "vietnamese" in cols):
                header = (ws, r + 1, cols)
                break
        if header:
            break
    if header is None:
        raise ValueError("Không thấy hàng tiêu đề có cột Patch ID và Label hoặc Vietnamese.")
    ws, header_row, cols = header
    i_id, i_nom, i_vi = cols["patchid"], cols.get("label"), cols.get("vietnamese")

    with _lock:
        state = read_state(book)
        rows, _ = build_rows(book)
        pair_vi = {r["nom"]["id"]: r["vi"] for r in rows if r["nom"]}
        nom_raw, vi_raw = _raw_texts(book, "ch", state), _raw_texts(book, "vi", state)
        nom_ids = {norm(i).strip(): i for i in nom_raw}
        out = {"rows": 0, "matched": 0, "nom_changed": 0, "vi_changed": 0, "no_vi": 0}
        unmatched: list[str] = []
        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            def cell(i):
                return "" if i is None or i >= len(row) or row[i] is None else _clean(row[i])
            pid = cell(i_id)
            if not pid:
                continue
            out["rows"] += 1
            real = nom_ids.get(norm(pid).strip())
            if real is None:
                unmatched.append(pid)
                continue
            out["matched"] += 1
            label, viet = cell(i_nom), cell(i_vi)
            if label and _put_edit(state, "ch", real, label, nom_raw[real]):
                out["nom_changed"] += 1
            if viet:
                partner = pair_vi.get(real)
                if partner is None:
                    out["no_vi"] += 1
                elif _put_edit(state, "vi", partner["id"], viet, vi_raw.get(partner["id"], "")):
                    out["vi_changed"] += 1
        _write_state(book, state)
    out["unmatched"], out["unmatched_count"] = unmatched[:20], len(unmatched)
    return out


def _sim_matrix(a_txt: list[str], b_txt: list[str]):
    """Ma trận độ giống (cosine trên tập chữ của mỗi dòng) giữa các dòng Nôm của hai văn bản: [len(a), len(b)]."""
    import numpy as np

    vocab: dict[str, int] = {}
    for s in a_txt + b_txt:
        for c in s:
            vocab.setdefault(c, len(vocab))

    def mat(txts):
        m = np.zeros((len(txts), len(vocab)), np.float32)
        for i, s in enumerate(txts):
            for c in set(s):
                m[i, vocab[c]] = 1
        return m

    ma, mb = mat(a_txt), mat(b_txt)
    la, lb = ma.sum(1), mb.sum(1)
    sim = (ma @ mb.T) / np.sqrt(np.maximum(la[:, None], 1) * np.maximum(lb[None, :], 1))
    sim[la == 0, :] = 0
    sim[:, lb == 0] = 0
    return sim


def match_lines(a_txt: list[str], b_txt: list[str], tau: float = 0.5, window: int = 60) -> list[tuple[int, int]]:
    """Ghép từng dòng Nôm của văn bản `a` với đúng một dòng Nôm của văn bản mẫu `b` có độ giống >= tau trong khoảng +-window dòng quanh vị trí
    dự đoán (độ lệch cục bộ lấy từ các cặp giống rõ rệt), ưu tiên cặp giống nhất. Cho phép lệch thứ tự (vd. hai nửa của bản chụp 2 trang đảo chỗ)."""
    import numpy as np

    n, m = len(a_txt), len(b_txt)
    if not n or not m:
        return []
    sim = _sim_matrix(a_txt, b_txt)
    best, best_sim = sim.argmax(1), sim.max(1)
    anchors = [(i, int(best[i]) - i) for i in range(n) if best_sim[i] >= 0.6]
    ai = np.array([a for a, _ in anchors])
    ad = np.array([d for _, d in anchors])
    glob = int(np.median(ad)) if len(ad) else 0
    cands = []
    for i in range(n):
        near = ad[np.abs(ai - i) <= 40] if len(ai) else []
        off = int(np.median(near)) if len(near) >= 3 else glob
        lo, hi = max(0, i + off - window), min(m, i + off + window + 1)
        seg = sim[i, lo:hi]
        for k in np.nonzero(seg >= tau)[0]:
            cands.append((float(seg[k]), i, lo + int(k)))
    cands.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_a, used_b, pairs = set(), set(), []
    for _, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j))
    return sorted(pairs)


def clear_auto(book: library.Book) -> None:
    """Bỏ các dấu miss và các lần dời do so khớp tự động tạo ra (dấu miss và lần dời đặt tay giữ nguyên)."""
    with _lock:
        state = read_state(book)
        _clear_auto_state(state)
        _write_state(book, state)


def _clear_auto_state(state: dict) -> None:
    auto = state.get("auto") or {}
    for key in auto.get("marks", []):
        state["marks"].pop(key, None)
    for role in auto.get("move_roles", []):
        state["moves"].pop(role, None)
    state["auto"] = {}


def _apply_pairing(state: dict, own: list[dict], ref: list[dict], pairs: list[tuple[int, int]], extra: dict) -> dict:
    """Áp kết quả ghép dòng Nôm `own` với dòng Việt `ref` (pairs: [(chỉ số Nôm, chỉ số Việt)], mỗi dòng tối đa một lần) vào state: dời các dòng Nôm
    cho đúng thứ tự dòng Việt đã ghép, Miss vi cho dòng Nôm không ghép được, Miss nôm cho dòng Việt không có dòng Nôm nào ghép tới."""
    match_j = dict(pairs)
    matched_ref = set(match_j.values())
    # thứ tự mới của các dòng Nôm: theo thứ tự dòng Việt đã ghép; dòng không ghép được đứng ngay sau dòng đứng trước nó
    keyed, last = [], -1
    for i in range(len(own)):
        if i in match_j:
            last = match_j[i]
            keyed.append(((last, 0, i), i))
        else:
            keyed.append(((last, 1, i), i))
    order = [i for _, i in sorted(keyed)]
    moves = _swaps_to([b["id"] for b in own], [own[i]["id"] for i in order])
    if moves:
        state["moves"]["ch"] = moves
    added = []
    for i, b in enumerate(own):
        key = f"ch:{b['id']}"
        if i not in match_j and key not in state["marks"]:
            state["marks"][key] = "vi"
            added.append(key)
    for j, b in enumerate(ref):
        key = f"vi:{b['id']}"
        if j not in matched_ref and key not in state["marks"]:
            state["marks"][key] = "nom"
            added.append(key)
    summary = {"own": len(own), "ref": len(ref), "matched": len(pairs), "miss_vi": len(own) - len(pairs), "miss_nom": len(ref) - len(pairs),
               "moves": len(moves), **extra}
    state["auto"] = {**summary, "marks": added, "move_roles": ["ch"] if moves else []}
    return summary


def auto_align(book: library.Book, tau: float = 0.4, window: int = 60) -> dict:
    """Dóng câu: ghép từng dòng Nôm của nguồn Nôm với dòng Việt của nguồn Việt đã chọn, bằng từ điển và khoảng cách Levenshtein như dóng chữ.

    Độ giống của một cặp (dòng Nôm, dòng Việt) = số cặp chữ - âm khớp từ điển sau khi dóng Levenshtein từng chữ với từng âm / số chữ hoặc số âm
    nhiều hơn (sinonom_review.alignment.levenshtein_align, dấu câu bỏ khi so khớp). Mỗi dòng Nôm chỉ xét các dòng Việt trong khoảng +-window dòng
    quanh vị trí dự đoán, ghép cặp độ giống >= tau theo thứ tự từ giống nhất, mỗi dòng ghép tối đa một lần. Dòng Nôm không ghép được: Miss vi;
    dòng Việt không có dòng Nôm ghép tới: Miss nôm; các dòng Nôm được dời cho đúng thứ tự dòng Việt. Thay kết quả tự động trước và mọi lần dời của hai vế.
    Trả về {"own", "ref", "matched", "miss_vi", "miss_nom", "moves", "kind", "tau", "window"}."""
    from . import review_pipeline as rp
    from .sinonom_review.alignment import levenshtein_align

    m = rp._imports()
    dic = rp._get_dict(rp.load_config())
    with _lock:
        state = read_state(book)
        _clear_auto_state(state)
        state["moves"].pop("ch", None)
        state["moves"].pop("vi", None)
        own = [b for b in load_role(book, "ch", state) if b["text"]]
        ref = [b for b in load_role(book, "vi", state) if b["text"]]
        if not own or not ref:
            raise ValueError("Chưa có chữ Nôm hoặc chữ Việt để dóng câu: kiểm tra nguồn Nôm và nguồn Việt.")
        chars = [rp.split_nom(b["text"].replace(" ", ""), m["tokenize_nom"])[0] for b in own]
        syls = [rp.split_qn(m["nfc"](b["text"]))[0] for b in ref]
        noms = [set().union(*(dic.viet_to_nom(s) for s in ss)) if ss else set() for ss in syls]     # chữ đọc được là một âm của dòng Việt
        n, k = len(own), len(ref)

        def bag(i: int, j: int) -> float:
            a = chars[i]
            return sum(c in noms[j] for c in a) / max(len(a), len(syls[j]), 1) if a and syls[j] else 0.0

        # độ lệch vị trí cục bộ: lấy mẫu mỗi 15 dòng Nôm, tìm dòng Việt giống nhất trong toàn văn bản
        anchors = []
        for i in range(0, n, 15):
            best = max(range(k), key=lambda j: bag(i, j))
            if bag(i, best) >= 0.6:
                anchors.append((i, best - i))
        ai = [a for a, _ in anchors]
        ad = sorted(d for _, d in anchors)
        glob = ad[len(ad) // 2] if ad else 0

        def offset(i: int) -> int:
            near = sorted(d for a, d in anchors if abs(a - i) <= 100)
            return near[len(near) // 2] if len(near) >= 3 else glob

        cands = []
        for i in range(n):
            if not chars[i]:
                continue
            off = offset(i)
            lo, hi = max(0, i + off - window), min(k, i + off + window + 1)
            scored = sorted(((bag(i, j), j) for j in range(lo, hi)), reverse=True)[:6]       # lọc nhanh bằng từ điển, rồi dóng Levenshtein các ứng viên đầu
            for s, j in scored:
                if s < tau * 0.5 or not syls[j]:
                    break
                aln = levenshtein_align(chars[i], syls[j], dic.compatible)
                sim = sum(1 for p in aln if p.status == m["MATCH"]) / max(len(chars[i]), len(syls[j]))
                if sim >= tau:
                    cands.append((sim, i, j))
        cands.sort(key=lambda t: (-t[0], t[1], t[2]))
        used_a, used_b, pairs = set(), set(), []
        for _, i, j in cands:
            if i in used_a or j in used_b:
                continue
            used_a.add(i)
            used_b.add(j)
            pairs.append((i, j))
        summary = _apply_pairing(state, own, ref, sorted(pairs), {"kind": "align", "tau": tau, "window": window})
        _write_state(book, state)
    return summary


def auto_match(book: library.Book, tau: float = 0.5, window: int = 60) -> dict:
    """So khớp chữ Nôm của sách này với văn bản mẫu (sách đang là nguồn Việt, đã dóng Nôm - Việt) để đánh dấu miss tự động.

    Mỗi dòng Nôm của sách này được ghép với dòng Nôm giống nhất của văn bản mẫu (độ giống >= tau, lệch tối đa `window` dòng). Dòng Nôm không
    ghép được: Miss vi. Dòng Việt của văn bản mẫu không có dòng Nôm nào ghép tới: Miss nôm. Các dòng Nôm được dời (lên / xuống) sao cho cặp
    ghép nằm đúng thứ tự của văn bản mẫu, rồi dóng câu ghép lần lượt như thường. Thay thế kết quả so khớp trước và mọi lần dời của hai vế.
    Trả về {"own", "ref", "matched", "miss_vi", "miss_nom", "moves"}."""
    with _lock:
        state = read_state(book)
        vi_src, vi_folder = source_of(book, "vi", state)
        if norm(vi_src.stem) == norm(book.stem):
            raise ValueError("Nguồn Việt đang là của chính sách này. Chọn nguồn Việt từ văn bản đã dóng Nôm - Việt để làm văn bản mẫu.")
        if vi_folder != "vi":
            raise ValueError("Văn bản mẫu phải được lấy từ thư mục vi/ của sách đã dóng Nôm - Việt.")
        ref_rows, _ = build_rows(vi_src)
        nom_of = {r["vi"]["id"]: (r["nom"]["text"] if r["nom"] else "") for r in ref_rows if r["vi"]}
        _clear_auto_state(state)
        state["moves"].pop("ch", None)
        state["moves"].pop("vi", None)
        own = [b for b in load_role(book, "ch", state) if b["text"]]
        ref = [b for b in load_role(book, "vi", state) if b["text"]]
        if not own or not ref:
            raise ValueError("Chưa có chữ Nôm hoặc chữ Việt để so khớp.")
        if not any(nom_of.get(b["id"]) for b in ref):
            raise ValueError("Văn bản mẫu chưa có hàng nào dóng được Nôm - Việt: dóng câu cho văn bản đó trước.")
        pairs = match_lines([b["text"].replace(" ", "") for b in own], [nom_of.get(b["id"], "").replace(" ", "") for b in ref], tau, window)
        summary = _apply_pairing(state, own, ref, pairs, {"kind": "match", "tau": tau, "window": window, "ref_book": vi_src.stem})
        _write_state(book, state)
    return summary


def _id_sort_key(box: str) -> tuple[int, int]:
    """(trang, dòng) của id <tên>.<trang>.<dòng>, để sắp theo thứ tự id."""
    parts = box.rsplit(".", 2)
    return (int(parts[-2]), int(parts[-1])) if len(parts) == 3 and parts[-2].isdigit() and parts[-1].isdigit() else (0, 0)


def _swaps_to(base_ids: list[str], target_ids: list[str]) -> list[list[str]]:
    """Dãy lần đổi chỗ [id a, id b] (áp theo thứ tự, xem _apply_moves) biến thứ tự `base_ids` thành `target_ids` (cùng một tập id)."""
    cur = list(base_ids)
    pos = {b: i for i, b in enumerate(cur)}
    swaps = []
    for p, want in enumerate(target_ids):
        if cur[p] != want:
            q = pos[want]
            swaps.append([cur[p], want])
            cur[p], cur[q] = cur[q], cur[p]
            pos[cur[p]], pos[cur[q]] = p, q
    return swaps


def _apply_moves(boxes: list[dict], moves: list) -> list[dict]:
    """Áp lần lượt các lần đổi chỗ [id a, id b] lên danh sách box (bỏ qua lần có id không còn trong danh sách)."""
    pos = {b["id"]: i for i, b in enumerate(boxes)}
    for pair in moves:
        if not (isinstance(pair, list) and len(pair) == 2):
            continue
        i, j = pos.get(pair[0]), pos.get(pair[1])
        if i is None or j is None:
            continue
        boxes[i], boxes[j] = boxes[j], boxes[i]
        pos[pair[0]], pos[pair[1]] = j, i
    return boxes


def _sequence(book: library.Book, side: str, state: dict) -> list[dict]:
    """Thứ tự ghép của một vế: box có chữ, sắp theo id rồi áp các lần dời lên / xuống."""
    boxes = [b for b in load_role(book, side, state) if b["text"]]
    return _apply_moves(boxes, (state["moves"] or {}).get(side) or [])


def move_box(book: library.Book, side: str, box: str, direction: str) -> None:
    """Đổi chỗ box `box` của vế `side` với box kề trước ("up") hoặc kề sau ("down") trong thứ tự ghép. "reset" bỏ mọi lần dời (side = ch | vi | all)."""
    with _lock:
        state = read_state(book)
        if direction == "reset":
            for s in (SIDES if side == "all" else (side,)):
                state["moves"].pop(s, None)
            _write_state(book, state)
            return
        if side not in SIDES or direction not in ("up", "down"):
            raise ValueError("Yêu cầu dời không hợp lệ.")
        seq = _sequence(book, side, state)
        idx = next((i for i, b in enumerate(seq) if b["id"] == box), None)
        if idx is None:
            raise ValueError("Không có box này trong thứ tự ghép.")
        target = idx - 1 if direction == "up" else idx + 1
        if not 0 <= target < len(seq):
            raise ValueError("Box đã ở đầu hoặc cuối.")
        pair = [seq[idx]["id"], seq[target]["id"]]
        moves = state["moves"].setdefault(side, [])
        if moves and sorted(moves[-1]) == sorted(pair):
            moves.pop()          # đổi chỗ lại đúng hai box vừa đổi: hoàn tác
        else:
            moves.append(pair)
        if not moves:
            state["moves"].pop(side, None)
        _write_state(book, state)


def _id_page(box: str) -> int:
    """Số trang trong id (<tên>.<trang>.<dòng>)."""
    parts = box.rsplit(".", 2)
    return int(parts[-2]) if len(parts) == 3 and parts[-2].isdigit() else 0


def save_order(book: library.Book) -> dict:
    """Ghi vào Label.txt của hai nguồn: chữ đã sửa và thứ tự đã dời (nút lên / xuống). Trong mỗi ảnh, chữ sửa được thay vào đúng box, rồi các box có
    chữ được xếp lại đúng thứ tự trong dóng hàng (box không có chữ giữ nguyên chỗ). Lần dời đổi chỗ hai box thuộc hai ảnh khác nhau không biểu diễn
    được trong Label.txt nên được giữ lại trong cache/align_rows.json. Id của box đổi theo vị trí mới, nên dấu miss, ô dịch và các lần dời còn lại
    được đổi sang id mới.
    Trả về {"changed": {"ch": số ảnh, "vi": số ảnh}, "files": [đường dẫn Label.txt đã ghi], "kept_moves": số lần dời giữa hai ảnh còn giữ}."""
    with _lock:
        state = read_state(book)
        changed = {"ch": 0, "vi": 0}
        files: list[str] = []
        kept = 0
        for role in SIDES:
            moves = (state["moves"] or {}).get(role) or []
            edits = {k[3:]: v for k, v in state["edits"].items() if k.startswith(f"{role}:")}
            if not moves and not edits:
                continue
            src, folder = source_of(book, role, state)
            label_path = src.dir / folder / label_io.LABEL_NAME
            labels = label_io.read_labels(label_path)
            key_of_page = {}
            for key in labels:
                key_of_page.setdefault(label_io.page_number(key), key)
            edit_by_page: dict[int, dict[int, str]] = {}
            for b in load_side(src, folder):
                if b["id"] in edits:
                    edit_by_page.setdefault(b["page"], {})[b["line"]] = edits[b["id"]]
            seq = _apply_moves([b for b in load_role(book, role, state) if b["text"]], moves)   # chữ đã sửa, thứ tự đã dời
            remap: dict[str, str] = {}
            new_labels = dict(labels)
            for page, key in key_of_page.items():
                try:
                    orig = [dict(b) for b in json.loads(labels[key])]
                except ValueError:
                    continue
                text_changed = False
                for line, text in edit_by_page.get(page, {}).items():
                    if line < len(orig) and orig[line].get("transcription") != text:
                        orig[line]["transcription"] = text
                        text_changed = True
                slots = [i for i, b in enumerate(orig) if _clean(b.get("transcription", "")) and len(b.get("points") or []) >= 3]
                order = [b["line"] for b in seq if b["page"] == page]          # dòng cũ theo thứ tự mới
                reorder = len(order) == len(slots) and order != slots
                if not text_changed and not reorder:
                    continue
                new_list = list(orig)
                if reorder:
                    for slot, old_line in zip(slots, order):
                        new_list[slot] = orig[old_line]
                        remap[box_id(src, page, old_line)] = box_id(src, page, slot)
                new_labels[key] = json.dumps(new_list, ensure_ascii=False)
                changed[role] += 1
            if changed[role]:
                label_io.write_labels(label_path, new_labels)
                files.append(str(label_path))
            for k in [k for k in state["edits"] if k.startswith(f"{role}:")]:
                del state["edits"][k]                 # đã nằm trong Label.txt
            # đổi id trong dấu miss, ô dịch và các lần dời còn lại; lần dời trong cùng một ảnh đã nằm trong Label.txt nên bỏ
            state["marks"] = {(f"{role}:{remap.get(k[3:], k[3:])}" if k.startswith(f"{role}:") else k): v for k, v in state["marks"].items()}
            if role == "ch":
                state["dich"] = {remap.get(k, k): v for k, v in state["dich"].items()}
            # phần thứ tự chưa nằm trong Label.txt (chuyển box giữa hai ảnh): tính lại từ thứ tự id mới tới thứ tự đích
            target = [remap.get(b["id"], b["id"]) for b in seq]
            residual = _swaps_to(sorted(target, key=_id_sort_key), target)
            kept += len(residual)
            if residual:
                state["moves"][role] = residual
            else:
                state["moves"].pop(role, None)
        if files:
            state["auto"] = {}                    # id đã đổi theo vị trí mới: các dấu miss tự động giờ là dấu thường
        _write_state(book, state)
    return {"changed": changed, "files": files, "kept_moves": kept}


# ---------------------------------------------------------------- ghép hàng
def build_rows(book: library.Book) -> tuple[list[dict], dict]:
    """(các hàng, thông tin). Mỗi hàng: {"n", "nom": box|None, "vi": box|None}; box có thêm "miss" khi bị đánh dấu."""
    state = read_state(book)
    marks = state["marks"]
    nom_all, vi_all = load_role(book, "ch", state), load_role(book, "vi", state)
    nom = _apply_moves([b for b in nom_all if b["text"]], (state["moves"] or {}).get("ch") or [])
    vi = _apply_moves([b for b in vi_all if b["text"]], (state["moves"] or {}).get("vi") or [])
    for side, boxes in (("ch", nom), ("vi", vi)):
        for k, b in enumerate(boxes):
            b["miss"] = marks.get(f"{side}:{b['id']}")
            b["first"], b["last"] = k == 0, k == len(boxes) - 1
    rows: list[dict] = []
    i = j = 0
    while i < len(nom) or j < len(vi):
        n = nom[i] if i < len(nom) else None
        v = vi[j] if j < len(vi) else None
        if n and n["miss"] == "vi":              # Nôm không có Việt
            rows.append({"nom": n, "vi": None}); i += 1
        elif v and v["miss"] == "nom":           # Việt không có Nôm
            rows.append({"nom": None, "vi": v}); j += 1
        else:
            rows.append({"nom": n, "vi": v})
            i += 1 if n else 0
            j += 1 if v else 0
    for k, row in enumerate(rows, 1):
        row["n"] = k
        n = row["nom"]
        d = state["dich"].get(n["id"]) if n else None
        row["dich"] = d["text"] if d and d.get("src") == n["text"] else ""
        row["dich_manual"] = bool(d and d.get("manual") and d.get("src") == n["text"])
    info = {"label": book_label(book), "sources": source_info(book, state), "foreign": foreign_sources(book), "nom": len(nom), "vi": len(vi),
            "nom_empty": len(nom_all) - len(nom), "vi_empty": len(vi_all) - len(vi),
            "miss_vi": sum(1 for b in nom if b["miss"]), "miss_nom": sum(1 for b in vi if b["miss"]),
            "moves": {"ch": len((state["moves"] or {}).get("ch") or []), "vi": len((state["moves"] or {}).get("vi") or [])},
            "edits": {"ch": sum(1 for k in state["edits"] if k.startswith("ch:")), "vi": sum(1 for k in state["edits"] if k.startswith("vi:"))},
            "auto": {k: v for k, v in (state.get("auto") or {}).items() if k not in ("marks", "move_roles")}}
    return rows, info


def public_row(row: dict) -> dict:
    """Hàng gửi cho giao diện: bỏ toạ độ (ảnh cắt lấy qua /api/align/crop)."""
    def cell(b):
        return None if b is None else {k: b[k] for k in ("id", "page", "line", "text", "miss", "first", "last", "edited")}
    return {"n": row["n"], "nom": cell(row["nom"]), "vi": cell(row["vi"]), "dich": row["dich"], "dich_manual": row["dich_manual"]}


# ---------------------------------------------------------------- ảnh cắt
def _find_file(folder: Path, name: str) -> Path | None:
    wanted = norm(name)
    try:
        for f in folder.iterdir():
            if f.is_file() and norm(f.name) == wanted:
                return f
    except OSError:
        pass
    return None


def crop_jpeg(book: library.Book, side: str, box: str) -> bytes | None:
    """Ảnh cắt (JPEG) của bbox `box` thuộc thư mục `side`; None nếu không tìm thấy box hoặc ảnh."""
    import io

    from PIL import Image

    if side not in SIDES:
        return None
    target = next((b for b in load_role(book, side) if b["id"] == box), None)
    if target is None:
        return None
    path = _find_file(Path(target["dir"]), target["file"])
    if path is None:
        return None
    xs, ys = [p[0] for p in target["points"]], [p[1] for p in target["points"]]
    with Image.open(path) as im:
        w, h = im.size
        left, top = max(0, int(min(xs)) - CROP_PAD), max(0, int(min(ys)) - CROP_PAD)
        right, bottom = min(w, int(max(xs)) + CROP_PAD), min(h, int(max(ys)) + CROP_PAD)
        if right <= left or bottom <= top:
            return None
        crop = im.convert("RGB").crop((left, top, right, bottom))
    crop.thumbnail((CROP_MAX_SIDE, CROP_MAX_SIDE))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------- dịch âm bằng API Kim Hán Nôm
_jobs: dict[str, dict] = {}


class TranslitBusy(Exception):
    """Sách này đang chạy dịch âm."""


def _api():
    api_dir = str(Path(__file__).resolve().parent / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    import sinonom_service   # nạp trễ: chỉ cần khi gọi API
    return sinonom_service


def translit_status(book: library.Book) -> dict:
    with _lock:
        return dict(_jobs.get(norm(book.stem)) or {"status": "idle"})


def _translit_run(book: library.Book, ids: list[str]) -> None:
    from .nom_dict import TranslitCache

    key = norm(book.stem)
    cache = TranslitCache(book.cache_dir / "translit.json", use_api=False)   # dùng chung cache với tô màu của align_books
    boxes = {b["id"]: b for b in load_role(book, "ch")}
    done = failed = 0
    error = ""
    try:
        service = _api()
        for box_id_ in ids:
            box = boxes.get(box_id_)
            if box is None or not box["text"]:
                continue
            text = box["text"].replace(" ", "")
            try:
                if text in cache.data["lines"]:
                    syllables = cache.data["lines"][text]
                else:
                    data = service.transliterate(text)
                    syllables = " ".join((data or {}).get("result_text_transcription") or []).split()
                    cache.data["lines"][text] = [s.lower() for s in syllables]
                    cache.save()
                set_dich(book, box_id_, " ".join(syllables), box["text"], manual=False)
                done += 1
            except Exception as e:   # lỗi một box (mạng, token, quá hạn): ghi lại, dừng nếu liên tiếp lỗi
                failed += 1
                error = f"{type(e).__name__}: {e}"
                if failed >= 3 and not done:
                    break
            with _lock:
                _jobs[key].update(done=done, failed=failed)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    with _lock:
        _jobs[key].update(status="error" if error and not done else "done", done=done, failed=failed, error=error, finished=time.time())


def start_translit(book: library.Book, ids: list[str] | None = None) -> int:
    """Dịch âm các box Nôm có id trong `ids` (None: mọi box Nôm có chữ mà chưa có dịch hợp lệ) ở nền. Trả về số box sẽ dịch."""
    rows, _ = build_rows(book)
    todo = []
    wanted = set(ids) if ids is not None else None
    for row in rows:
        n = row["nom"]
        if n is None or (wanted is not None and n["id"] not in wanted):
            continue
        if wanted is None and row["dich"]:
            continue
        todo.append(n["id"])
    if not todo:
        return 0
    key = norm(book.stem)
    with _lock:
        if (_jobs.get(key) or {}).get("status") == "running":
            raise TranslitBusy("Sách này đang dịch âm.")
        _jobs[key] = {"status": "running", "total": len(todo), "done": 0, "failed": 0, "started": time.time()}
    threading.Thread(target=_translit_run, args=(book, todo), daemon=True, name="translit").start()
    return len(todo)


# ---------------------------------------------------------------- xuất cho align_books
def pairs_for_export(book: library.Book):
    """(các hàng đã ghép, ghi chú) cho align_books: cùng thứ tự ghép với giao diện."""
    rows, info = build_rows(book)
    notes = [f"Ghép theo id đã sắp: {info['nom']} box Nôm, {info['vi']} box Việt, {len(rows)} hàng "
             f"(miss vi: {info['miss_vi']}, miss nôm: {info['miss_nom']})."]
    for f in info["foreign"]:
        notes.append(f"CẢNH BÁO: nguồn {f['name']} lấy từ sách khác ({f['book']} / {f['folder']}), file xuất vẫn lưu trong thư mục của sách đang mở.")
    if info["nom_empty"] or info["vi_empty"]:
        notes.append(f"Bỏ box không có chữ: {info['nom_empty']} box Nôm, {info['vi_empty']} box Việt.")
    return rows, notes
