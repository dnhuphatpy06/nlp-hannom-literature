"""Phiên gán nhãn: dữ liệu + dóng hàng + ứng viên + lựa chọn của người gán nhãn (tự lưu, chạy lại được)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from . import __version__
from .alignment import MATCH, MISMATCH, AlignedPair, levenshtein_align
from .data import ImageIndex, Sentence, load_sentences
from .dictionary import NomVietDict
from .scoring import DEFAULT_WEIGHTS, CandidateScorer, Site
from .scoring import rerank as rerank_hyps

STATE_FILE = "review_state.json"
CACHE_FILE = "candidates_cache.json"


class ReviewSession:
    def __init__(self, sentences: list[Sentence], dic: NomVietDict, images: ImageIndex, output_dir: str | Path,
                 costs: dict | None = None, only_with_image: bool = False):
        self.sentences = sentences
        self.dic = dic
        self.images = images
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.costs = costs
        self.alignments: list[list[AlignedPair]] = [
            levenshtein_align(s.chars, s.syls, dic.compatible, costs) for s in sentences]
        self.sites: list[Site] = []
        self.sent_sites: dict[int, list[Site]] = {}
        for s, aln in zip(sentences, self.alignments):
            k = 0
            for p in aln:
                if p.status == MISMATCH:
                    k += 1
                    st = Site(f"{s.book}|{s.row}|{p.aln_pos}", s.idx, p.aln_pos, p.nom_pos, p.qn_pos, p.nom, p.qn, k)
                    self.sites.append(st)
                    self.sent_sites.setdefault(s.idx, []).append(st)
        self.site_by_key = {st.key: st for st in self.sites}
        self.only_with_image = only_with_image
        self._mismatch_pages: list[int] = sorted(self.sent_sites)       # các câu có ít nhất một cặp lệch
        if only_with_image:                                       # chỉ duyệt câu có ảnh (ngữ cảnh/thống kê vẫn tính đủ)
            self._mismatch_pages = [i for i in self._mismatch_pages
                                    if images.lookup(sentences[i].image_id).path is not None]
        self.pages: list[int] = list(self._mismatch_pages)       # câu cần duyệt; thu hẹp lại sau khi chấm điểm
        self.candidates: dict[str, list[dict]] = {}
        self.decisions: dict[str, dict] = {}
        self.score_settings: dict = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ dựng phiên
    @classmethod
    def build(cls, data_path, dict_path, output_dir, nom_col="Label", qn_col="Vietnamese", id_col="ID",
              image_col="Patch ID", files=None, extra_dicts=(), use_variants=True, costs=None, verbose=True,
              only_with_image=False):
        if verbose:
            print("Đọc từ điển ...")
        dic = NomVietDict.load(dict_path, extra_csvs=extra_dicts, use_variants=use_variants)
        if verbose:
            print("  ", dic.stats())
            print("Đọc dữ liệu ...")
        sents = load_sentences(data_path, nom_col, qn_col, id_col, image_col, files, verbose)
        if not sents:
            raise FileNotFoundError(f"Không tìm thấy câu nào trong {data_path}")
        images = ImageIndex(data_path)
        sess = cls(sents, dic, images, output_dir, costs, only_with_image)
        if verbose:
            cover: dict[str, list[int]] = {}
            for s in sents:
                c = cover.setdefault(s.book, [0, 0])
                c[1] += 1
                c[0] += images.lookup(s.image_id).path is not None
            print(f"  Ảnh: {images.summary()}")
            for book, (found, total) in cover.items():
                warn = "" if found == total else "   <-- THIẾU ẢNH" if found == 0 else "   <-- thiếu một phần"
                print(f"    {book}: {found}/{total} câu có ảnh{warn}")
            if any(f < t for f, t in cover.values()):
                print("    (câu không có ảnh vẫn dóng hàng và chấm điểm được, chỉ là giao diện không hiện ảnh; "
                      "đặt only_with_image=True / ONLY_WITH_IMAGE=True để chỉ duyệt câu có ảnh)")
            print(f"Dóng hàng xong: {len(sents)} câu, {len(sess.sites)} cặp lệch; "
                  f"{len(sess.pages)} câu có cặp lệch" + (" (chỉ câu có ảnh)" if only_with_image else ""))
        sess.load_state(verbose=verbose)
        return sess

    # ------------------------------------------------------------------ ứng viên
    def _signature(self, scorer: CandidateScorer, nom_model, qn_model) -> str:
        return self._signature_of(scorer.w, scorer.nom_ctx, scorer.qn_ctx,
                                  nom_model if scorer.nom_lm else None, qn_model if scorer.qn_lm else None)

    def _signature_of(self, weights: dict, nom_ctx: int, qn_ctx: int, nom_model, qn_model) -> str:
        """Dấu vân tay của mọi thứ ảnh hưởng tới điểm: trọng số, ngữ cảnh, mô hình (None = tắt), từ điển và danh sách
        cặp lệch (tức là cả dữ liệu). Đổi bất kỳ thứ nào thì phải chấm lại."""
        payload = {"v": __version__, "w": weights, "ctx": [nom_ctx, qn_ctx], "models": [nom_model, qn_model],
                   "dict": self.dic.stats(), "variants": self.dic.use_variants,
                   "sites": [(st.key, st.nom, st.qn) for st in self.sites]}
        return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _read_cache(self, sig: str) -> dict | None:
        cache = self.output_dir / CACHE_FILE
        if not cache.exists():
            return None
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if data.get("signature") == sig else None

    def cache_matches(self, nom_model_name=None, qn_model_name=None, weights=None, nom_context_lines=1,
                      qn_context_lines=0) -> bool:
        """Kết quả chấm điểm đã lưu trong thư mục output có còn khớp không? Không cần nạp mô hình để trả lời.
        nom_model_name / qn_model_name: tên mô hình sẽ dùng, None nếu tắt mô hình đó."""
        sig = self._signature_of({**DEFAULT_WEIGHTS, **(weights or {})}, nom_context_lines, qn_context_lines,
                                 nom_model_name, qn_model_name)
        return self._read_cache(sig) is not None

    def load_cached(self, nom_model_name=None, qn_model_name=None, weights=None, nom_context_lines=1,
                    qn_context_lines=0, verbose=True) -> bool:
        """Dùng lại kết quả chấm điểm đã lưu nếu còn khớp (xem cache_matches). Trả về True nếu đã nạp được."""
        sig = self._signature_of({**DEFAULT_WEIGHTS, **(weights or {})}, nom_context_lines, qn_context_lines,
                                 nom_model_name, qn_model_name)
        data = self._read_cache(sig)
        if data is None:
            return False
        self.candidates = data["candidates"]
        self.score_settings = data.get("settings", {})
        if verbose:
            print(f"Dùng lại ứng viên đã chấm trong {CACHE_FILE} ({len(self.candidates)} cặp), không cần nạp mô hình")
        self.refresh_pages(verbose)
        return True

    def score_candidates(self, nom_lm=None, qn_lm=None, weights=None, nom_context_lines=1, qn_context_lines=0,
                         nom_model_name=None, qn_model_name=None, use_cache=True, verbose=True):
        scorer = CandidateScorer(self.dic, nom_lm, qn_lm, weights, nom_context_lines, qn_context_lines)
        sig = self._signature(scorer, nom_model_name, qn_model_name)
        cache = self.output_dir / CACHE_FILE
        if use_cache and self.load_cached(nom_model_name if nom_lm else None, qn_model_name if qn_lm else None,
                                          weights, nom_context_lines, qn_context_lines, verbose):
            return self.candidates
        t = time.time()
        if verbose:
            print(f"Chấm điểm ứng viên cho {len(self.sites)} cặp lệch "
                  f"(LM Nôm: {'bật' if nom_lm else 'tắt'}, LM Việt: {'bật' if qn_lm else 'tắt'}) ...")
        self.candidates = scorer.score(self.sites, self.sentences, show_progress=verbose)
        self.score_settings = {"weights": scorer.w, "nom_model": nom_model_name if nom_lm else None,
                               "qn_model": qn_model_name if qn_lm else None,
                               "nom_context_lines": nom_context_lines, "qn_context_lines": qn_context_lines}
        cache.write_text(json.dumps({"signature": sig, "settings": self.score_settings,
                                     "candidates": self.candidates}, ensure_ascii=False), encoding="utf-8")
        if verbose:
            print(f"  xong sau {time.time() - t:.1f}s")
        self.refresh_pages(verbose)
        return self.candidates

    def rerank(self, weights: dict | None, verbose=False) -> bool:
        """Xếp hạng lại ứng viên đã chấm theo trọng số khác (ví dụ bộ đã dò bởi tuning.py) từ bằng chứng thô trong self.candidates, không nạp LM
        và không ghi candidates_cache.json (cache vẫn khớp với trọng số mặc định). Gọi lại sau mỗi lần load_cached / score_candidates."""
        if not weights or not self.candidates:
            return False
        self.candidates = {k: rerank_hyps(h, weights) for k, h in self.candidates.items()}
        self.score_settings = {**self.score_settings, "weights": {**DEFAULT_WEIGHTS, **weights}, "reranked": True}
        self.refresh_pages(verbose)
        return True

    def review_sites(self, sent_idx: int) -> list[Site]:
        """Các cặp lệch của câu CÓ ít nhất một ứng viên sửa — chỉ những cặp này được hiển thị để duyệt."""
        return [st for st in self.sent_sites.get(sent_idx, []) if self.top_candidates(st.key)]

    def refresh_pages(self, verbose=False):
        """Chỉ giữ lại các câu có ít nhất một cặp lệch có ứng viên (cặp không có ứng viên không hiển thị)."""
        self.pages = [i for i in self._mismatch_pages if self.review_sites(i)]
        self.review_keys = {st.key for i in self.pages for st in self.review_sites(i)}
        if verbose:
            total = sum(len(self.sent_sites[i]) for i in self._mismatch_pages)
            print(f"  {len(self.review_keys)}/{total} cặp lệch có ứng viên, ở {len(self.pages)} câu cần duyệt; "
                  f"{total - len(self.review_keys)} cặp không có ứng viên nên không hiển thị (vẫn tính là lệch)")

    def top_candidates(self, key: str, k: int | None = None) -> list[dict]:
        """Ứng viên sửa (bỏ KEEP), giảm dần theo điểm, tối đa k."""
        c = [h for h in self.candidates.get(key, []) if h["side"] != "KEEP"]
        return c if k is None else c[:k]

    def keep_hyp(self, key: str) -> dict | None:
        return next((h for h in self.candidates.get(key, []) if h["side"] == "KEEP"), None)

    # ------------------------------------------------------------------ lựa chọn
    def set_decision(self, key: str, side: str | None, nom: str | None = None, qn: str | None = None,
                     autosave: bool = True):
        """side: KEEP | NOM | QN | None (xoá lựa chọn)."""
        st = self.site_by_key[key]
        with self._lock:
            if side is None:
                self.decisions.pop(key, None)
            else:
                if side == "KEEP":
                    hyp = self.keep_hyp(key)
                elif side in ("NOM", "QN"):
                    hyp = next((h for h in self.candidates.get(key, [])
                                if h["side"] == side and h["nom"] == nom and h["qn"] == qn), None)
                else:
                    hyp = None
                self.decisions[key] = {
                    "side": side, "nom": st.nom if side == "KEEP" else (nom or st.nom),
                    "qn": st.qn if side == "KEEP" else (qn or st.qn),
                    "rank": hyp["rank"] if hyp else None,
                    "orig_nom": st.nom, "orig_qn": st.qn,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        if autosave:
            self.save_state()

    def decision(self, key: str) -> dict | None:
        return self.decisions.get(key)

    def save_state(self):
        path = self.output_dir / STATE_FILE
        with self._lock:
            # giữ lựa chọn của các cặp không thuộc phiên này (ví dụ đã duyệt ở nguồn câu khác của cùng sách), kẻo đổi nguồn rồi lưu sẽ làm mất
            try:
                kept = {k: d for k, d in json.loads(path.read_text(encoding="utf-8")).get("decisions", {}).items() if k not in self.site_by_key}
            except (OSError, ValueError, AttributeError):
                kept = {}
            data = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "decisions": {**kept, **self.decisions}}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        return path

    def load_state(self, verbose=True):
        path = self.output_dir / STATE_FILE
        if not path.exists():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for key, d in data.get("decisions", {}).items():
            st = self.site_by_key.get(key)
            if st and st.nom == d.get("orig_nom") and st.qn == d.get("orig_qn"):
                self.decisions[key] = d
                n += 1
        if verbose:
            print(f"Khôi phục {n} lựa chọn đã lưu từ {path}")
        return n

    # ------------------------------------------------------------------ kết quả sau gán nhãn
    def after_alignment(self, sent_idx: int) -> list[dict]:
        """Từng vị trí dóng hàng sau khi áp dụng lựa chọn: nom, qn, status_before, status_after, how."""
        s = self.sentences[sent_idx]
        out = []
        for p in self.alignments[sent_idx]:
            nom, qn, how = p.nom, p.qn, ""
            if p.status == MISMATCH:
                d = self.decisions.get(f"{s.book}|{s.row}|{p.aln_pos}")
                if d:
                    how = d["side"]
                    nom, qn = d["nom"], d["qn"]
            if p.status in (MATCH, MISMATCH):
                after = MATCH if self.dic.compatible(nom, qn) else MISMATCH
            else:
                after = p.status
            out.append({"aln_pos": p.aln_pos, "nom_pos": p.nom_pos, "qn_pos": p.qn_pos, "nom": nom, "qn": qn,
                        "orig_nom": p.nom, "orig_qn": p.qn, "status_before": p.status, "status_after": after,
                        "how": how})
        return out

    def progress(self) -> dict:
        keys = getattr(self, "review_keys", {st.key for st in self.sites})
        n_pages_done = sum(1 for i in self.pages if all(st.key in self.decisions for st in self.review_sites(i)))
        return {"pairs": len(keys), "pairs_done": sum(1 for k in keys if k in self.decisions),
                "pages": len(self.pages), "pages_done": n_pages_done}

    def save_all(self, verbose=True) -> dict:
        from .export import export_all
        self.save_state()
        return export_all(self, verbose=verbose)
