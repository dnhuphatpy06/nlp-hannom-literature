"""Bước 3 của web, phần sau dóng câu: Kiểm tra (tô cặp lệch), Đề xuất (chọn ứng viên) và Kết quả.

Dùng lại mã của dự án Alignment trong scripts/sinonom_review/ (dóng hàng chữ Nôm - âm Quốc ngữ bằng Levenshtein theo từ điển, sinh và chấm điểm
ứng viên KEEP / NOM / QN, xuất xlsx tô màu) và scripts/make_report.py (file <Tên>_SO_SANH.xlsx). Dữ liệu vào là các hàng đã dóng câu ở
scripts/align_rows.py: mỗi hàng có cả chữ Nôm lẫn chữ Việt là một câu; Patch ID của câu là id bbox Nôm.

Mọi file đều ghi vào thư mục của sách (data/<Tên sách>/), như dự án Alignment ghi vào paths.output:
  <Tên>.csv                 các câu Nôm - Việt theo cấu trúc file dóng hàng của quy định đồ án (tab Kiểm tra)
  candidates_cache.json     ứng viên đã chấm điểm (dùng lại khi dữ liệu, từ điển, mô hình không đổi)
  review_state.json         lựa chọn của người duyệt (tự lưu mỗi lần chọn)
  candidates_review.csv/.xlsx, mismatch_pairs_before.csv/.xlsx, mismatch_before.xlsx, mismatch_after.xlsx,
  statistics.json/.csv, confirmed_keep_pairs.csv        do nút Lưu ở tab Đề xuất xuất ra
  <Tên>.xlsx                kết quả sau khi áp dụng lựa chọn, chỉ tô đỏ các cặp còn lệch (tab Kết quả)
  <Tên>_SO_SANH.xlsx        báo cáo so sánh trước/sau 5 sheet như outputs_16_11 của Alignment (tab Kết quả)
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unicodedata
from pathlib import Path

from . import align_rows, library

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config_review.yaml"
DEFAULT_CONFIG = {
    "dictionary": {"dir": "resources/dictionaries", "extra_dicts": [], "nom_variants": True},
    "models": {"use_lm": True, "nom_model": "btqkhai/SinoNomBERT", "quocngu_model": "Qwen/Qwen2.5-0.5B", "device": "auto"},
    "review": {"top_k": 0, "use_tuned_weights": True, "tuned_weights_file": "resources/tuning/tuned_weights.json",
               "use_ranker": True, "ranker_dir": "resources/ranker"},
}

_lock = threading.RLock()
_sessions: dict[str, dict] = {}     # tên sách (NFC) -> {"stamp", "session"}
_dict_cache: dict = {}
_jobs: dict[str, dict] = {}


class ReviewBusy(Exception):
    """Sách này đang chấm điểm đề xuất."""


# ---------------------------------------------------------------- cấu hình
def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        import yaml
        loaded = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, ImportError, ValueError):
        loaded = {}
    for section, values in loaded.items():
        if isinstance(values, dict) and isinstance(cfg.get(section), dict):
            cfg[section].update(values)
    return cfg


def tuned_weights_path(cfg: dict | None = None) -> Path | None:
    r = (cfg or load_config())["review"]
    if not r.get("use_tuned_weights", True) or not r.get("tuned_weights_file"):
        return None
    p = Path(r["tuned_weights_file"])
    return p if p.is_absolute() else ROOT / p


def _apply_tuned(sess) -> bool:
    """Xếp hạng lại ứng viên của phiên theo trọng số đã dò (resources/tuning/tuned_weights.json, do scripts/tune_review.py ghi) nếu file có và đã
    được chọn tự động. Không chạy LM; tắt bằng review.use_tuned_weights: false."""
    path = tuned_weights_path()
    if path is None:
        return False
    from .sinonom_review.tuning import load_tuned
    rec = load_tuned(path)
    return bool(rec) and sess.rerank(rec["weights"])


def ranker_dir(cfg: dict | None = None) -> Path | None:
    r = (cfg or load_config())["review"]
    if not r.get("use_ranker", True):
        return None
    p = Path(r.get("ranker_dir") or "resources/ranker")
    return p if p.is_absolute() else ROOT / p


_ranker_cache: dict = {}


def _get_ranker():
    """Mô hình ranker (LightGBM) dùng chung cho mọi phiên; nạp lại khi file mô hình đổi. None nếu tắt hoặc không dùng được
    (thiếu file / thư viện / font): khi đó web giữ xếp hạng theo trọng số, lý do nằm ở _ranker_cache['error'] và hiện ở tab Đề xuất."""
    d = ranker_dir()
    if d is None:
        _ranker_cache.clear()
        return None
    model = d / "ranker_lgbm.txt"
    try:
        stamp = (model.stat().st_mtime_ns, (d / "ranker_config.json").stat().st_mtime_ns if (d / "ranker_config.json").exists() else 0)
    except OSError:
        stamp = None
    with _lock:
        if _ranker_cache.get("stamp") != stamp or ("ranker" not in _ranker_cache and "error" not in _ranker_cache):
            _ranker_cache.clear()
            _ranker_cache["stamp"] = stamp
            try:
                from .sinonom_review.ranker import Ranker, RankerUnavailable
                _ranker_cache["ranker"] = Ranker.load(d)
            except Exception as e:       # RankerUnavailable hoặc lỗi nạp mô hình: không làm hỏng phiên duyệt
                _ranker_cache["error"] = f"{type(e).__name__}: {e}"
        return _ranker_cache.get("ranker")


def _apply_ranking(sess) -> bool:
    """Thứ hạng ứng viên của phiên: trọng số đã dò (nếu có) rồi mô hình ranker — ranker quyết định thứ tự cuối.
    Kết quả của ranker được lưu ở <thư mục sách>/ranker_cache.json: còn khớp bằng chứng đầu vào và mô hình thì đọc lại từ file (không cần
    lightgbm, font hay tính lại); không thì chạy mô hình rồi ghi cache. Không chạy LM; tắt ranker bằng review.use_ranker: false."""
    _apply_tuned(sess)
    d = ranker_dir()
    if d is None or not sess.candidates:
        return False
    from .sinonom_review import ranker as rk
    path = sess.output_dir / rk.CACHE_FILE
    stamp, digest = rk.model_stamp(d), rk.candidates_digest(sess.candidates)
    hit = rk.load_ranked(path, sess.candidates, digest, stamp)
    if hit:
        return sess.set_ranked(*hit)
    ranker = _get_ranker()
    if ranker is None:
        sess.score_settings = {**sess.score_settings, "ranker_error": _ranker_cache.get("error") or "ranker không dùng được"}
        return False
    try:
        ranked = ranker.rank_all(sess.candidates)
        rk.save_ranked(path, ranked, digest, stamp, ranker.info)
        return sess.set_ranked(ranked, ranker.info)
    except Exception as e:               # lỗi lúc vẽ chữ / dự đoán / ghi cache: giữ nguyên thứ hạng theo trọng số
        sess.score_settings = {**sess.score_settings, "ranker_error": f"{type(e).__name__}: {e}"}
        return False


def _imports():
    """Nạp các mô-đun của gói sinonom_review (nặng: pandas) lúc cần."""
    from .sinonom_review import export, session
    from .sinonom_review.alignment import EXTRA_NOM, EXTRA_QN, MATCH, MISMATCH
    from .sinonom_review.data import ImageRef, Sentence
    from .sinonom_review.dictionary import NomVietDict
    from .sinonom_review.text import nfc, tokenize_nom, tokenize_qn
    return {"export": export, "session": session, "MATCH": MATCH, "MISMATCH": MISMATCH, "EXTRA_NOM": EXTRA_NOM, "EXTRA_QN": EXTRA_QN,
            "ImageRef": ImageRef, "Sentence": Sentence, "NomVietDict": NomVietDict, "nfc": nfc, "tokenize_nom": tokenize_nom,
            "tokenize_qn": tokenize_qn}


def _get_dict(cfg: dict):
    d = cfg["dictionary"]
    extra = tuple(str((ROOT / p) if not Path(p).is_absolute() else p) for p in (d.get("extra_dicts") or []))
    variants = bool(d.get("nom_variants", True))
    key = (str(ROOT / d["dir"]), extra, tuple(Path(p).stat().st_mtime if Path(p).exists() else 0 for p in extra), variants)
    with _lock:
        if _dict_cache.get("key") != key:
            _dict_cache["dic"] = _imports()["NomVietDict"].load(ROOT / d["dir"], extra_csvs=extra, use_variants=True, nom_variants=variants)
            _dict_cache["key"] = key
        return _dict_cache["dic"]


# ---------------------------------------------------------------- dấu câu
def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def split_nom(text: str, tokenize) -> tuple[list[str], dict[int, str]]:
    """Chữ Nôm của câu bỏ dấu câu, kèm {vị trí chữ đứng trước: dấu câu theo sau} (-1: dấu câu đứng đầu câu) để ghép lại khi xuất kết quả."""
    chars: list[str] = []
    after: dict[int, str] = {}
    for tok in tokenize(text):
        if len(tok) == 1 and _is_punct(tok):
            after[len(chars) - 1] = after.get(len(chars) - 1, "") + tok
        else:
            chars.append(tok)
    return chars, after


def split_qn(text: str) -> tuple[list[str], list[str], list[str]]:
    """Âm Quốc ngữ của câu bỏ dấu câu ở hai đầu mỗi âm: (các âm, phần dấu đứng trước, phần dấu đứng sau). Token chỉ toàn dấu câu gộp vào âm trước."""
    syls, pre, suf = [], [], []
    for tok in text.split():
        a, b = 0, len(tok)
        while a < b and _is_punct(tok[a]):
            a += 1
        while b > a and _is_punct(tok[b - 1]):
            b -= 1
        if a == b:                       # chỉ toàn dấu câu
            if syls:
                suf[-1] += tok
            else:
                pre.append(tok)
            continue
        syls.append(tok[a:b])
        pre.append(tok[:a] + (pre.pop() if len(pre) > len(syls) - 1 else ""))
        suf.append(tok[b:])
    return syls, pre[:len(syls)], suf


# ---------------------------------------------------------------- phiên làm việc của một sách
class BookImages:
    """Tra ảnh trang của câu theo Patch ID (id bbox Nôm), thay ImageIndex của Alignment: ảnh nằm trong thư mục nguồn Nôm của sách."""

    def __init__(self, mapping: dict):
        self.map = mapping

    def lookup(self, image_id: str):
        return self.map.get(image_id) or _imports()["ImageRef"](None, None)

    def summary(self) -> dict:
        return {"images": len(self.map)}


INPUT_NAME = "input.xlsx"
SOURCE_FILE = "review_source.json"   # nguồn câu Nôm - Việt người dùng chọn cho sách: "bbox" (mặc định) hoặc "xlsx"


def has_input(book: library.Book) -> bool:
    return input_file(book).is_file()


def is_xlsx_book(book: library.Book) -> bool:
    """Mặc định mọi văn bản đi theo OCR và dóng câu theo bbox. Chỉ khi có input.xlsx và người dùng đã chọn thì câu Nôm - Việt mới đọc từ input.xlsx
    (khi đó văn bản chỉ có phân loại trang và dóng hàng)."""
    if not has_input(book):
        return False
    try:
        return json.loads((book.dir / SOURCE_FILE).read_text(encoding="utf-8")).get("source") == "xlsx"
    except (OSError, ValueError, AttributeError):
        return False


def set_source(book: library.Book, source: str) -> dict:
    """Chọn nguồn câu Nôm - Việt của sách: "bbox" (OCR và dóng câu) hoặc "xlsx" (đọc input.xlsx, cần file này có sẵn)."""
    if source not in ("bbox", "xlsx"):
        raise ValueError("Nguồn không hợp lệ.")
    if source == "xlsx" and not has_input(book):
        raise ValueError("Văn bản này chưa có file input.xlsx.")
    path = book.dir / SOURCE_FILE
    tmp = path.with_name(SOURCE_FILE + ".tmp")
    tmp.write_text(json.dumps({"source": source}), encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _sessions.pop(library.norm(book.stem), None)
    return {"source": source}


def input_file(book: library.Book) -> Path:
    return book.dir / INPUT_NAME


class XlsxImages:
    """Tra ảnh trang của câu theo Patch ID (<tên>.<trang>.<dòng>) cho văn bản đọc từ xlsx: ảnh nằm ở cache/pages của sách."""

    def __init__(self, book: library.Book):
        self.book = book

    def lookup(self, image_id: str):
        page, _, line = image_id.rpartition(".")
        number = page.rpartition(".")[2]
        ref = _imports()["ImageRef"]
        if number.isdigit() and line.isdigit():
            return ref(library.find_page_image(self.book.pages_dir, self.book.stem, int(number)), int(line))
        return ref(None, None)

    def summary(self) -> dict:
        return {}


def page_layout(book: library.Book, nom_page: int) -> dict:
    """Trang cần hiện cho một câu ở văn bản đọc từ xlsx. Trang ch_vi (Nôm và Quốc ngữ xen kẽ trên cùng một trang): chỉ một trang. Trang chỉ có Nôm
    (nhãn ch): thêm trang Quốc ngữ tương ứng, là trang vi thứ k khi trang Nôm là trang ch thứ k. Người dùng đổi trang tay được trên giao diện."""
    try:
        cache = json.loads(book.page_cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    pages = cache.get("pages", {})
    tag = (pages.get(str(nom_page)) or {}).get("tag")
    vi_page = None
    if tag == "ch":
        ch = sorted(int(p) for p, v in pages.items() if v.get("tag") == "ch" and p.isdigit())
        vi = sorted(int(p) for p, v in pages.items() if v.get("tag") == "vi" and p.isdigit())
        if nom_page in ch and ch.index(nom_page) < len(vi):
            vi_page = vi[ch.index(nom_page)]
    return {"kind": "pair" if vi_page else "single", "tag": tag, "nom_page": nom_page, "vi_page": vi_page, "total": cache.get("total_pages") or 0}


def _stamp(book: library.Book) -> tuple:
    """Dấu hiệu dữ liệu đầu vào đổi: nhãn của hai nguồn (hoặc input.xlsx), file align_rows.json và cấu hình."""
    if is_xlsx_book(book):
        files = [input_file(book), CONFIG_FILE]
    else:
        state = align_rows.read_state(book)
        files = [align_rows.state_file(book), CONFIG_FILE]
        for role in align_rows.SIDES:
            src, folder = align_rows.source_of(book, role, state)
            files.append(src.dir / folder / "Label.txt")
    if tuned_weights_path():
        files.append(tuned_weights_path())              # dò lại trọng số thì phiên được dựng lại với thứ hạng mới
    if ranker_dir():
        files += [ranker_dir() / "ranker_lgbm.txt", ranker_dir() / "ranker_config.json"]   # huấn luyện lại ranker thì cũng dựng lại
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(f), 0, 0))
    return tuple(out)


def _bbox_sentences(book: library.Book, m: dict):
    rows, _ = align_rows.build_rows(book)
    sentences, images = [], {}
    label = align_rows.book_label(book)
    for r in rows:
        n, v = r["nom"], r["vi"]
        if not (n and v and n["text"] and v["text"]):
            continue
        chars, nom_punct = split_nom(n["text"].replace(" ", ""), m["tokenize_nom"])
        syls, qn_pre, qn_suf = split_qn(m["nfc"](v["text"]))
        if not chars or not syls:                  # chỉ toàn dấu câu: không có gì để so khớp
            continue
        idx = len(sentences)
        s = m["Sentence"](idx=idx, book=label, file="", row=n["page"] * 1000 + n["line"], sid=str(r["n"]), image_id=n["id"],
                          nom="".join(chars), qn=" ".join(syls), chars=chars, syls=syls, book_pos=idx)
        s.nom_punct, s.qn_pre, s.qn_suf = nom_punct, qn_pre, qn_suf   # để ghép lại dấu câu khi xuất kết quả
        s.vi_id, s.vi_line = v["id"], v["line"]                          # bbox Việt đã ghép với bbox Nôm của câu
        sentences.append(s)
        images[n["id"]] = m["ImageRef"](Path(n["dir"]) / n["file"], n["line"])
    if not sentences:
        raise ValueError("Chưa có hàng nào có cả chữ Nôm lẫn chữ Việt: hãy dóng câu ở tab Dóng câu trước.")
    return sentences, BookImages(images)


def _xlsx_sentences(book: library.Book, m: dict):
    path = input_file(book)
    if not path.is_file():
        raise ValueError("Chưa có file input.xlsx trong thư mục của văn bản này. Tải lên file xlsx có các cột Patch ID, Label (chữ Nôm), Vietnamese.")
    from .sinonom_review.data import load_sentences

    sentences = load_sentences(path, verbose=False)      # đúng cách đọc của Alignment: khoá cặp lệch trùng với kết quả đã chấm của Alignment
    if not sentences:
        raise ValueError("input.xlsx không có hàng nào có cả chữ Nôm lẫn chữ Việt (cần cột Label và Vietnamese).")
    label = align_rows.book_label(book)
    for s in sentences:
        s.book = label
    return sentences, XlsxImages(book)


def _build_session(book: library.Book):
    m = _imports()
    cfg = load_config()
    sentences, images = (_xlsx_sentences if is_xlsx_book(book) else _bbox_sentences)(book, m)
    sess = m["session"].ReviewSession(sentences, _get_dict(cfg), images, book.dir)
    sess.load_state(verbose=False)
    cache = book.dir / m["session"].CACHE_FILE            # dùng lại ứng viên đã chấm nếu còn khớp (cùng dữ liệu, từ điển, mô hình)
    try:
        settings = json.loads(cache.read_text(encoding="utf-8")).get("settings", {})
        sess.load_cached(settings.get("nom_model"), settings.get("qn_model"), settings.get("weights") or None,
                         settings.get("nom_context_lines", 1), settings.get("qn_context_lines", 0), verbose=False)
    except (OSError, ValueError):
        pass
    _apply_ranking(sess)
    return sess


def save_input(book: library.Book, data: bytes) -> dict:
    """Lưu file xlsx tải lên thành input.xlsx của văn bản (cần cột Patch ID, Label, Vietnamese)."""
    import io

    import openpyxl

    try:
        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    except Exception as e:
        raise ValueError(f"Không đọc được file .xlsx: {e}")
    names = {str(c).strip() for c in header if c is not None}
    missing = [c for c in ("Patch ID", "Label", "Vietnamese") if c not in names]
    if missing:
        raise ValueError(f"File thiếu cột {', '.join(missing)} ở hàng tiêu đề.")
    tmp = input_file(book).with_name(INPUT_NAME + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(input_file(book))
    with _lock:
        _sessions.pop(library.norm(book.stem), None)
    return {"file": _file_info(input_file(book))}


def get_session(book: library.Book):
    """Phiên của sách, dựng lại khi dữ liệu đầu vào đổi (lựa chọn đã lưu được khôi phục theo cặp chữ - âm)."""
    key = library.norm(book.stem)
    stamp = _stamp(book)
    with _lock:
        item = _sessions.get(key)
        if item and item["stamp"] == stamp:
            return item["session"]
        sess = _build_session(book)
        _sessions[key] = {"stamp": stamp, "session": sess}
        return sess


# ---------------------------------------------------------------- tên file
def stem(book: library.Book) -> str:
    return library.norm(book.stem)


def out_files(book: library.Book) -> dict[str, Path]:
    return {"csv": book.dir / f"{stem(book)}.csv", "result": book.dir / f"{stem(book)}.xlsx",
            "compare": book.dir / f"{stem(book)}_SO_SANH.xlsx", "candidates": book.dir / "candidates_cache.json",
            "state": book.dir / "review_state.json"}


def _file_info(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"name": path.name, "size": st.st_size, "mtime": st.st_mtime}


# ---------------------------------------------------------------- tổng quan và tab Kiểm tra
def _stats(sess) -> dict:
    m = _imports()
    st = m["export"].statistics(sess)
    return st["books"]["TOTAL"]


def summary(book: library.Book) -> dict:
    """Số liệu tổng quan, tiến độ duyệt và các file đã xuất của sách."""
    try:
        sess = get_session(book)
    except ValueError as e:
        return {"ready": False, "error": str(e), "mode": "xlsx" if is_xlsx_book(book) else "bbox", "has_input": has_input(book),
                "files": {k: _file_info(p) for k, p in out_files(book).items()}, "job": job_status(book)}
    prog = sess.progress() if sess.candidates else {"pairs": 0, "pairs_done": 0, "pages": 0, "pages_done": 0}
    total = _stats(sess)
    has = bool(sess.candidates)
    return {"ready": True, "mode": "xlsx" if is_xlsx_book(book) else "bbox", "has_input": has_input(book),
            "sentences": len(sess.sentences), "sites": len(sess.sites), "stats": total,
            "has_candidates": has, "progress": prog, "all_reviewed": has and prog["pairs_done"] >= prog["pairs"], "pairs_open": prog["pairs"] - prog["pairs_done"],
            "settings": sess.score_settings, "config": load_config(), "files": {k: _file_info(p) for k, p in out_files(book).items()},
            "job": job_status(book)}


def check_page(book: library.Book, page: int, only_mismatch: bool = True, size: int = 25) -> dict:
    """Các câu của tab Kiểm tra: mỗi câu là danh sách vị trí dóng hàng (chữ Nôm, âm Quốc ngữ, trạng thái)."""
    sess = get_session(book)
    ids = [s.idx for s in sess.sentences if not only_mismatch or any(p.status == "mismatch" for p in sess.alignments[s.idx])]
    pages = max(1, -(-len(ids) // size))
    page = max(0, min(page, pages - 1))
    out = []
    for i in ids[page * size:(page + 1) * size]:
        s = sess.sentences[i]
        ref = sess.images.lookup(s.image_id)
        out.append({"idx": i, "sid": s.sid, "id": s.image_id, "line": ref.line_index,
                    "pairs": [{"pos": p.aln_pos + 1, "nom": p.nom, "qn": p.qn, "status": p.status} for p in sess.alignments[i]]})
    return {"page": page, "pages": pages, "total": len(ids), "sentences": out}


def _sentence_ids(book: library.Book, sess) -> list[tuple[str, str]]:
    """(img_id, sentence_id) của từng câu theo quy định file dóng hàng: img_id là tên ảnh trang Nôm (không kèm thư mục), sentence_id là
    <img_id bỏ đuôi .jpg>.<số thứ tự câu Nôm trong trang, từ 1, đủ 3 chữ số>."""
    counters: dict[str, int] = {}
    out = []
    for s in sess.sentences:
        ref = sess.images.lookup(s.image_id)
        number = s.image_id.rpartition(".")[0].rpartition(".")[2]
        img_id = ref.path.name if ref.path else (f"{stem(book)}.{int(number):03d}.jpg" if number.isdigit() else "")
        counters[img_id] = counters.get(img_id, 0) + 1
        out.append((img_id, f"{img_id[:-4] if img_id.lower().endswith('.jpg') else img_id}.{counters[img_id]:03d}"))
    return out


def save_csv(book: library.Book) -> dict:
    """Ghi <Tên>.csv (img_id, sentence_id, SinoNom_Char, ChuQN_txt, UTF-8 có BOM) của tab Kiểm tra.
    Văn bản Kim* (bbox): cùng nội dung với nút Xuất file của tab Dóng câu (scripts/align_books.py). Văn bản đọc từ input.xlsx: các câu trong
    input.xlsx, chưa áp dụng lựa chọn nào."""
    path = out_files(book)["csv"]
    if is_xlsx_book(book):
        import csv

        sess = get_session(book)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            w.writerow(["img_id", "sentence_id", "SinoNom_Char", "ChuQN_txt"])
            for s, (img_id, sentence_id) in zip(sess.sentences, _sentence_ids(book, sess)):
                w.writerow([img_id, sentence_id, s.nom, s.qn])
    else:
        from . import align_books

        pairs, _ = align_books.align(book, 0)
        if not pairs:
            raise ValueError("Chưa có hàng nào để lưu: hãy dóng câu ở tab Dóng câu trước.")
        align_books.write_csv(path, pairs)
    return {"path": str(path), "file": _file_info(path)}


# ---------------------------------------------------------------- tab Đề xuất: chấm điểm
def job_status(book: library.Book) -> dict:
    with _lock:
        return dict(_jobs.get(library.norm(book.stem)) or {"status": "idle"})


def _score_run(book: library.Book, use_lm: bool) -> None:
    key = library.norm(book.stem)
    cfg = load_config()["models"]
    try:
        sess = get_session(book)
        nom_lm = qn_lm = None
        names = (cfg["nom_model"], cfg["quocngu_model"]) if use_lm else (None, None)
        if sess.cache_matches(*names):          # kết quả chấm điểm đã lưu còn khớp (cùng dữ liệu, từ điển, mô hình): không cần nạp mô hình
            sess.load_cached(*names, verbose=False)
            _apply_ranking(sess)
            with _lock:
                _jobs[key].update(status="done", finished=time.time(), stage="Dùng lại kết quả đã chấm, không nạp mô hình", reused=True)
            return
        with _lock:
            _jobs[key]["stage"] = "Nạp mô hình ngôn ngữ" if use_lm else "Chấm điểm bằng từ điển"
        if use_lm:
            from .sinonom_review.lm import load_models
            nom_lm, qn_lm, device = load_models(cfg["nom_model"], cfg["quocngu_model"], cfg.get("device", "auto"))
            with _lock:
                _jobs[key].update(stage=f"Chấm điểm {len(sess.sites)} cặp lệch trên {device}", device=device)
        sess.score_candidates(nom_lm, qn_lm, nom_model_name=cfg["nom_model"], qn_model_name=cfg["quocngu_model"], use_cache=True, verbose=False)
        _apply_ranking(sess)
        with _lock:
            _jobs[key].update(status="done", finished=time.time(), stage="Xong")
    except Exception as e:   # lỗi nạp mô hình, thiếu thư viện, hết bộ nhớ ...
        with _lock:
            _jobs[key].update(status="error", error=f"{type(e).__name__}: {e}", finished=time.time())


def start_scoring(book: library.Book, use_lm: bool | None = None) -> None:
    """Sinh và chấm điểm ứng viên ở nền (dùng lại cache nếu còn khớp). use_lm None: theo config_review.yaml."""
    cfg = load_config()["models"]
    use_lm = cfg.get("use_lm", True) if use_lm is None else use_lm
    key = library.norm(book.stem)
    get_session(book)                      # báo lỗi ngay nếu chưa có câu nào
    with _lock:
        if (_jobs.get(key) or {}).get("status") == "running":
            raise ReviewBusy("Sách này đang chấm điểm đề xuất.")
        _jobs[key] = {"status": "running", "started": time.time(), "use_lm": use_lm, "stage": "Bắt đầu"}
    threading.Thread(target=_score_run, args=(book, use_lm), daemon=True, name="review-score").start()


# ---------------------------------------------------------------- tab Đề xuất: duyệt từng câu
def _cand(h: dict, site) -> dict:
    change = f"{site.nom}→{h['nom']}" if h["side"] == "NOM" else f"{site.qn}→{h['qn']}"
    return {"side": h["side"], "nom": h["nom"], "qn": h["qn"], "score": h["score"], "confidence": round(h["confidence"], 3), "change": change,
            "variant": bool((h.get("evidence") or {}).get("variant"))}


def review_page(book: library.Book, page: int) -> dict:
    """Một câu cần duyệt (câu có ít nhất một cặp lệch có ứng viên): bảng dóng hàng, các thẻ cặp lệch kèm ứng viên top-k và lựa chọn."""
    sess = get_session(book)
    if not sess.candidates:
        raise ValueError("Chưa chấm điểm đề xuất.")
    top_k = int(load_config()["review"].get("top_k", 0)) or None      # 0 hoặc rỗng: hiện tất cả ứng viên
    pages = sess.pages
    if not pages:
        raise ValueError("Không có cặp lệch nào có ứng viên để duyệt.")
    page = max(0, min(page, len(pages) - 1))
    i = pages[page]
    s = sess.sentences[i]
    sites = sess.review_sites(i)
    shown = {st.aln_pos: k for k, st in enumerate(sites, 1)}
    after = sess.after_alignment(i)
    cells = []
    for a in after:
        cells.append({"pos": a["aln_pos"] + 1, "nom": a["nom"], "qn": a["qn"], "orig_nom": a["orig_nom"], "orig_qn": a["orig_qn"],
                      "status": a["status_before"], "how": a["how"], "mark": shown.get(a["aln_pos"])})
    cards = []
    for k, st in enumerate(sites, 1):
        cands = sess.top_candidates(st.key, top_k)
        sides = {h["side"] for h in cands}
        cards.append({"key": st.key, "order": k, "nom": st.nom, "qn": st.qn,
                      "kind": "Nôm" if sides == {"NOM"} else "Quốc ngữ" if sides == {"QN"} else "Nôm / Quốc ngữ",
                      "candidates": [_cand(h, st) for h in cands], "decision": sess.decision(st.key)})
    ref = sess.images.lookup(s.image_id)
    unreviewed = [p for p, j in enumerate(pages) if any(st.key not in sess.decisions for st in sess.review_sites(j))]
    keep_pages = [p for p, j in enumerate(pages) if any((sess.decisions.get(st.key) or {}).get("side") == "KEEP" for st in sess.review_sites(j))]
    layout = None
    if is_xlsx_book(book):
        number = s.image_id.rpartition(".")[0].rpartition(".")[2]
        layout = page_layout(book, int(number)) if number.isdigit() else {"kind": "single", "nom_page": None, "vi_page": None, "total": 0}
    return {"page": page, "pages": len(pages), "idx": i, "sid": s.sid, "id": s.image_id, "line": ref.line_index, "layout": layout,
            "vi_id": getattr(s, "vi_id", None), "vi_line": getattr(s, "vi_line", None), "nom": s.nom, "qn": s.qn,
            "cells": cells, "cards": cards, "unreviewed_pages": unreviewed, "keep_pages": keep_pages, "progress": sess.progress()}


def decide(book: library.Book, key: str, side: str | None, nom: str | None = None, qn: str | None = None) -> dict:
    """Chọn ứng viên (side NOM | QN, kèm chữ/âm), KEEP, hoặc bỏ lựa chọn (side rỗng). Tự lưu vào review_state.json."""
    sess = get_session(book)
    if key not in sess.site_by_key:
        raise ValueError("Không có cặp lệch này.")
    if side not in (None, "", "KEEP", "NOM", "QN"):
        raise ValueError("Lựa chọn không hợp lệ.")
    sess.set_decision(key, side or None, nom, qn)
    return sess.progress()


def save_review(book: library.Book) -> dict:
    """Nút Lưu của tab Đề xuất: lưu lựa chọn và xuất mọi file kết quả của Alignment vào thư mục sách."""
    sess = get_session(book)
    if not sess.candidates:
        raise ValueError("Chưa chấm điểm đề xuất.")
    sess.save_all(verbose=False)
    return {"progress": sess.progress(), "files": {k: _file_info(p) for k, p in out_files(book).items()}}


# ---------------------------------------------------------------- tab Kết quả
def results_page(book: library.Book, page: int, size: int = 25) -> dict:
    """Các câu có chỗ đã sửa (NOM hoặc QN): từng chỗ đổi chữ Nôm / âm Quốc ngữ, kèm trạng thái sau khi sửa."""
    sess = get_session(book)
    changed = []
    for s in sess.sentences:
        items = []
        for a in sess.after_alignment(s.idx):
            if a["how"] in ("NOM", "QN", "KEEP"):
                items.append({"pos": a["aln_pos"] + 1, "how": a["how"], "nom": a["nom"], "qn": a["qn"], "orig_nom": a["orig_nom"],
                              "orig_qn": a["orig_qn"], "still": a["status_after"] == "mismatch"})
        if any(x["how"] in ("NOM", "QN") for x in items):
            changed.append({"sid": s.sid, "id": s.image_id, "changes": items})
    pages = max(1, -(-len(changed) // size))
    page = max(0, min(page, pages - 1))
    return {"page": page, "pages": pages, "total": len(changed), "sentences": changed[page * size:(page + 1) * size], "stats": _stats(sess)}


def _require_scored(sess) -> None:
    """Xuất kết quả không đòi duyệt hết: cặp chưa duyệt và cặp KEEP đều còn lệch từ điển nên được tô đỏ trong file."""
    if not sess.candidates:
        raise ValueError("Chưa chấm điểm đề xuất.")


def export_result(book: library.Book) -> dict:
    """Ghi <Tên>.xlsx: câu sau khi áp dụng lựa chọn, chỉ tô đỏ các cặp còn lệch từ điển (kể cả cặp KEEP). Cột theo cấu trúc file dóng hàng của quy định
    đồ án (giống <Tên>.csv): img_id (tên ảnh trang Nôm, không kèm thư mục), sentence_id (<img_id bỏ đuôi .jpg>.<số thứ tự câu Nôm trong trang, đủ 3 chữ số>),
    SinoNom_Char (văn bản Hán Nôm), ChuQN_txt (văn bản Quốc ngữ)."""
    from xlsxwriter import Workbook

    m = _imports()
    sess = get_session(book)
    _require_scored(sess)
    path = out_files(book)["result"]
    with Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False}) as wb:
        default = wb.add_format({})
        red = wb.add_format({"font_color": m["export"].RED, "bold": True})
        head = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1})
        ws = wb.add_worksheet("Sheet1")
        cols, widths = ["img_id", "sentence_id", "SinoNom_Char", "ChuQN_txt"], [38, 42, 34, 50]
        ids = _sentence_ids(book, sess)
        for j, (c, w) in enumerate(zip(cols, widths)):
            ws.write(0, j, c, head)
            ws.set_column(j, j, w)
        ws.freeze_panes(1, 0)
        for r, s in enumerate(sess.sentences, start=1):
            chars, syls = list(s.chars), list(s.syls)
            nom_f, qn_f = [None] * len(chars), [None] * len(syls)
            for a in sess.after_alignment(s.idx):
                f = red if a["status_after"] == m["MISMATCH"] else None
                if a["nom_pos"] is not None:
                    chars[a["nom_pos"]], nom_f[a["nom_pos"]] = a["nom"], f
                if a["qn_pos"] is not None:
                    syls[a["qn_pos"]], qn_f[a["qn_pos"]] = a["qn"], f
            ws.write(r, 0, ids[r - 1][0])
            ws.write(r, 1, ids[r - 1][1])
            # ghép lại dấu câu đã bỏ khi so khớp: dấu của Quốc ngữ đi cùng âm, dấu của Nôm đứng sau chữ đứng trước nó
            qn_pre, qn_suf, nom_punct = getattr(s, "qn_pre", []), getattr(s, "qn_suf", []), getattr(s, "nom_punct", {})
            syls = [(qn_pre[k] if k < len(qn_pre) else "") + w + (qn_suf[k] if k < len(qn_suf) else "") for k, w in enumerate(syls)]
            chars_p, nom_f_p = ([nom_punct[-1]], [None]) if nom_punct.get(-1) else ([], [])   # dấu câu đứng đầu câu
            for k, c in enumerate(chars):
                chars_p.append(c)
                nom_f_p.append(nom_f[k])
                if nom_punct.get(k):
                    chars_p.append(nom_punct[k])
                    nom_f_p.append(None)
            m["export"]._write_tokens(ws, r, 2, chars_p, nom_f_p, "", default)
            m["export"]._write_tokens(ws, r, 3, syls, qn_f, " ", default)
        ws.autofilter(0, 0, len(sess.sentences), len(cols) - 1)
    return {"path": str(path), "file": _file_info(path)}


def export_compare(book: library.Book) -> dict:
    """Ghi <Tên>_SO_SANH.xlsx (5 sheet: tổng quan, bản cũ, bản mới, thay đổi Quốc ngữ, thay đổi SinoNom OCR) bằng make_report.py của Alignment."""
    sess = get_session(book)
    _require_scored(sess)
    sess.save_all(verbose=False)           # make_report đọc mismatch_before/after.xlsx, candidates_review.csv, statistics.json do save_all vừa ghi
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import make_report

    out_dir = book.dir
    stats = json.loads((out_dir / "statistics.json").read_text(encoding="utf-8"))
    label = align_rows.book_label(book)
    sents = [s for s in make_report.read_sentences(out_dir) if s.book == label]
    selected = make_report.read_selected(out_dir)
    path = out_files(book)["compare"]
    make_report.write_book_report(path, label, sents, {**stats, "books": {label: stats["books"][label]}}, selected)
    return {"path": str(path), "file": _file_info(path)}
