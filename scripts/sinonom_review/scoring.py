"""Sinh ứng viên và chấm điểm cho từng cặp lệch (substitution) sau dóng hàng.

Với mỗi cặp lệch (chữ c, âm s) có ba loại giả thuyết:
  KEEP : giữ nguyên (c, s)                                        điểm = keep_score
  NOM  : thay chữ c -> c'   (c' đọc được là s, giống hình với c)   điểm = nom_prior + w_vis·(−log(hạng_hình+1)) + w_nlm·ΔlogP_Nôm
  QN   : thay âm  s -> s'   (s' là âm đọc của c, gần s về chính tả) điểm = qn_prior − w_ortho·khoảng_cách + w_qlm·ΔlogP_Việt
confidence = softmax(điểm) trên mọi giả thuyết của cặp đó. Ứng viên được sắp giảm dần theo điểm.

Trọng số mặc định được dò bằng random search trên lỗi giả lập (dự án SinoNom-alignment, experiments/tune_weights.py; không nằm trong thư mục này),
chưa được hiệu chỉnh trên lỗi thật: hãy coi confidence là thứ hạng tương đối, không phải xác suất đúng.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .dictionary import NomVietDict
from .text import match_case

DEFAULT_WEIGHTS = {
    "w_vis": 1.5,        # Nôm: −log(hạng hình + 1)
    "w_nlm": 0.5,        # Nôm: Δ log P của LM chữ Nôm (softmax hạn chế trong tập ứng viên)
    "novis_rank": 20,    # Nôm: hạng hình giả định cho ứng viên chỉ do LM đưa ra
    "w_ortho": 3.0,      # Quốc ngữ: phạt khoảng cách chính tả
    "w_qlm": 0.3,        # Quốc ngữ: Δ log P(dòng) của LM tiếng Việt
    "nom_prior": 0.0,
    "qn_prior": 0.0,
    "keep_score": -4.0,  # điểm của giả thuyết giữ nguyên
    "max_ortho": 1.2,    # khoảng cách chính tả tối đa để một âm được làm ứng viên
    "lm_topk": 0,        # số ứng viên Nôm CHỈ do LM đưa ra (không giống hình) được thêm vào; 0 = tắt
}
LM_CANDIDATES_MAX = 20


@dataclass
class Site:
    key: str           # "<sách>|<dòng trong file>|<vị trí dóng hàng>"
    sent_idx: int
    aln_pos: int
    nom_pos: int
    qn_pos: int
    nom: str
    qn: str
    pair_order: int    # thứ tự cặp lệch trong câu (1-based)


def _softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    z = sum(e)
    return [v / z for v in e]


class CandidateScorer:
    def __init__(self, dic: NomVietDict, nom_lm=None, qn_lm=None, weights: dict | None = None,
                 nom_context_lines: int = 1, qn_context_lines: int = 0):
        self.dic = dic
        self.nom_lm, self.qn_lm = nom_lm, qn_lm
        self.w = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.nom_ctx, self.qn_ctx = nom_context_lines, qn_context_lines

    # ------------------------------------------------------------------ ngữ cảnh
    @staticmethod
    def _book_lines(sentences):
        books: dict[str, list] = {}
        for s in sentences:
            books.setdefault(s.book, []).append(s)
        return books

    def _nom_context(self, books, sent, pos):
        lines = books[sent.book]
        i, k = sent.book_pos, self.nom_ctx
        left = [c for s in lines[max(0, i - k): i] for c in s.chars]
        right = [c for s in lines[i + 1: i + 1 + k] for c in s.chars]
        return left + list(sent.chars) + right, len(left) + pos

    def _qn_text(self, books, sent, pos, new):
        lines = books[sent.book]
        i, k = sent.book_pos, self.qn_ctx
        prev = [x for s in lines[max(0, i - k): i] for x in s.syls]
        cur = list(sent.syls)
        cur[pos] = new
        return " ".join(prev + cur)

    # ------------------------------------------------------------------ chấm điểm
    def score(self, sites: list[Site], sentences, show_progress: bool = True) -> dict[str, list[dict]]:
        """Trả về {site.key: [ứng viên sắp giảm dần theo điểm]} — mỗi ứng viên gồm
        rank, side (KEEP/NOM/QN), nom, qn, score, confidence, evidence."""
        W, dic = self.w, self.dic
        books = self._book_lines(sentences)
        feats = []
        for st in sites:
            sent = sentences[st.sent_idx]
            targets = dic.viet_to_nom(st.qn)
            nom = {c2: {"visual_rank": r} for c2, r in dic.nom_candidates(st.nom, st.qn)}
            qn = {s2: {"ortho": d} for s2, d in dic.viet_candidates(st.nom, st.qn, W["max_ortho"])}
            feats.append({"site": st, "sent": sent, "targets": targets, "nom": nom, "qn": qn})

        # --- LM chữ Nôm: softmax hạn chế trong {chữ đọc được là s} ∪ {chữ hiện tại}
        if self.nom_lm is not None and feats:
            reqs = []
            for f in feats:
                ctx, idx = self._nom_context(books, f["sent"], f["site"].nom_pos)
                reqs.append((ctx, idx, sorted(set(f["targets"]) | {f["site"].nom})))
            for f, lm in zip(feats, self.nom_lm.restricted_scores(reqs, show_progress)):
                vals = [v for v in lm.values() if v is not None]
                lo = min(vals) if vals else 0.0
                med = statistics.median(vals) if vals else 0.0
                old = lm.get(f["site"].nom)
                old = med if old is None else old          # chữ OCR ngoài vocab: trung tính
                if W["lm_topk"] > 0:                         # thêm ứng viên chỉ do LM đưa ra
                    ranked = sorted(((v, c2) for c2, v in lm.items()
                                     if v is not None and c2 in f["targets"] and c2 != f["site"].nom), reverse=True)
                    for _, c2 in ranked[: min(W["lm_topk"], LM_CANDIDATES_MAX)]:
                        f["nom"].setdefault(c2, {"visual_rank": None})
                for c2, ev in f["nom"].items():
                    new = lm.get(c2)
                    ev["lm_oov"] = new is None
                    ev["nlm_delta"] = (lo if new is None else new) - old

        # --- LM Quốc ngữ: Δ log P(dòng) khi thay một âm
        if self.qn_lm is not None:
            texts = set()
            for f in feats:
                if f["qn"]:
                    st = f["site"]
                    texts.add(self._qn_text(books, f["sent"], st.qn_pos, f["sent"].syls[st.qn_pos]))
                    texts.update(self._qn_text(books, f["sent"], st.qn_pos, s2) for s2 in f["qn"])
            lp = self.qn_lm.logprobs(sorted(texts), show_progress) if texts else {}
            for f in feats:
                st = f["site"]
                if f["qn"]:
                    base = lp[self._qn_text(books, f["sent"], st.qn_pos, f["sent"].syls[st.qn_pos])]
                    for s2, ev in f["qn"].items():
                        ev["qlm_delta"] = lp[self._qn_text(books, f["sent"], st.qn_pos, s2)] - base

        out: dict[str, list[dict]] = {}
        for f in feats:
            st = f["site"]
            hyps = [{"side": "KEEP", "nom": st.nom, "qn": st.qn, "score": W["keep_score"], "evidence": {}}]
            for c2, ev in f["nom"].items():
                rank = ev["visual_rank"] if ev["visual_rank"] is not None else W["novis_rank"]
                d = ev.get("nlm_delta", 0.0)
                sc = W["nom_prior"] + W["w_vis"] * (-math.log(rank + 1)) + W["w_nlm"] * d
                hyps.append({"side": "NOM", "nom": c2, "qn": st.qn, "score": sc, "evidence": {
                    "visual_rank": ev["visual_rank"], "nlm_delta": round(d, 3), "lm_oov": ev.get("lm_oov")}})
            for s2, ev in f["qn"].items():
                d = ev.get("qlm_delta", 0.0)
                sc = W["qn_prior"] - W["w_ortho"] * ev["ortho"] + W["w_qlm"] * d
                hyps.append({"side": "QN", "nom": st.nom, "qn": match_case(s2, st.qn), "score": sc, "evidence": {
                    "ortho": round(ev["ortho"], 2), "qlm_delta": round(d, 3)}})
            probs = _softmax([h["score"] for h in hyps])
            for h, p in zip(hyps, probs):
                h["confidence"] = p
                h["score"] = round(h["score"], 4)
            hyps.sort(key=lambda h: (-h["score"], h["side"], h["nom"], h["qn"]))
            for r, h in enumerate(hyps, 1):
                h["rank"] = r
            out[st.key] = hyps
        return out
