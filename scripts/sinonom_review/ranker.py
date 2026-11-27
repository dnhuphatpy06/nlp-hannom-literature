"""Xếp hạng ứng viên bằng mô hình ranker học máy (LightGBM LambdaRank) huấn luyện ở scripts/train_ranker.py.

Mô hình thay cho công thức cộng điểm tay của scoring.rank_hypotheses: nó đọc lại bằng chứng thô đã lưu trong candidates_cache.json
(visual_rank, nlm_delta, ortho, qlm_delta ...) cộng thêm độ giống nét chữ NOM (vẽ chữ bằng font Hán Nôm, đúng như
scripts/add_nom_glyph_similarity.py), nên giống `rerank` ở chỗ: không cần nạp LM, không ghi lại candidates_cache.json.

Đặc trưng phải trùng từng chữ số với bộ dữ liệu huấn luyện (data/ranker_training_dataset.csv, do scripts/export_ranker_dataset.py xuất);
`add_group_features` là hàm duy nhất dựng đặc trưng tương đối trong một vị trí, dùng chung cho huấn luyện và web.

Mô hình lấy từ resources/ranker/: ranker_lgbm.txt (mô hình), metrics.json (danh sách đặc trưng), ranker_config.json (nhiệt độ softmax để
confidence có nghĩa tương đối). Thiếu file hoặc thiếu thư viện (lightgbm, fontTools, Pillow, scipy) thì `Ranker.load` ném RankerUnavailable
và web quay về xếp hạng theo trọng số.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RANKER_DIR = ROOT / "resources" / "ranker"
FONT_DIR = ROOT / "font"

# Cột tín hiệu chính được viết lại theo thứ hạng / khoảng cách so với các ứng viên khác của cùng vị trí.
REL_COLS = ["nom_glyph_neglog_rank", "viet_distance",
            "nom_new_semantic_delta_logp", "viet_new_semantic_delta_logp"]


class RankerUnavailable(RuntimeError):
    """Không dùng được mô hình ranker (thiếu file mô hình, thư viện hoặc font)."""


def add_group_features(d: pd.DataFrame) -> pd.DataFrame:
    """Thêm đặc trưng theo nhóm (một nhóm = một vị trí site_id). Cần cột site_id, is_nom và các cột REL_COLS."""
    d["score_semantic"] = d["nom_new_semantic_delta_logp"] + d["viet_new_semantic_delta_logp"]
    g = d.groupby("site_id", sort=False)
    d["n_cand"] = g["is_nom"].transform("size")
    for c in REL_COLS + ["score_semantic"]:
        d[f"{c}__rank"] = g[c].rank(ascending=False, method="average") / d["n_cand"]
        d[f"{c}__gap"] = d[c] - g[c].transform("max")
    return d


# ------------------------------------------------------------------ cache kết quả xếp hạng (ranker_cache.json trong thư mục sách)
CACHE_FILE = "ranker_cache.json"
_EVIDENCE_KEYS = ("visual_rank", "nlm_delta", "ortho", "qlm_delta")     # đúng những bằng chứng mô hình đọc


def model_stamp(directory: Path | str = RANKER_DIR) -> str | None:
    """Dấu vân tay của mô hình (nội dung ranker_lgbm.txt, ranker_config.json, metrics.json). None nếu máy này không có file mô hình."""
    directory = Path(directory)
    files = [directory / n for n in ("ranker_lgbm.txt", "ranker_config.json", "metrics.json")]
    if not files[0].is_file():
        return None
    h = hashlib.sha1()
    for f in files:
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()


def candidates_digest(candidates: dict[str, list[dict]]) -> str:
    """Dấu vân tay của bằng chứng đầu vào (không phụ thuộc thứ tự, điểm hay thứ hạng hiện có) nên cache dùng được sau khi xếp lại theo trọng số."""
    h = hashlib.sha1()
    for site in sorted(candidates):
        h.update(site.encode())
        for x in sorted((x["side"], x["nom"], x["qn"], *(repr((x.get("evidence") or {}).get(k)) for k in _EVIDENCE_KEYS)) for x in candidates[site]):
            h.update("".join(map(str, x)).encode())
    return h.hexdigest()


def _softmax_conf(scores: list[float], temperature: float) -> list[float]:
    m = max(scores)
    e = [math.exp((v - m) / temperature) for v in scores]
    z = sum(e)
    return [v / z for v in e]


def save_ranked(path: Path, ranked: dict[str, list[dict]], digest: str, stamp: str | None, info: dict) -> None:
    """Ghi thứ tự và điểm của từng ứng viên. Chỉ lưu (loại, chữ Nôm, âm, điểm); bằng chứng nằm ở candidates_cache.json, confidence tính lại từ điểm."""
    data = {"version": 1, "digest": digest, "model_stamp": stamp, "info": info,
            "ranked": {site: [[h["side"], h["nom"], h["qn"], h["score"]] for h in hyps] for site, hyps in ranked.items()}}
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_ranked(path: Path, candidates: dict[str, list[dict]], digest: str, stamp: str | None):
    """Dựng lại kết quả xếp hạng từ cache nếu còn khớp bằng chứng đầu vào (và mô hình, khi máy này có mô hình). Trả về (ranked, info) hoặc None.
    Không cần lightgbm, font hay vẽ chữ."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != 1 or data.get("digest") != digest:
        return None
    if stamp is not None and data.get("model_stamp") != stamp:
        return None
    info = data.get("info") or {}
    temp = float(info.get("temperature", 1.0)) or 1.0
    ranked = {}
    for site, rows in data["ranked"].items():
        by = {(h["side"], h["nom"], h["qn"]): h for h in candidates.get(site, [])}
        if len(rows) != len(by):
            return None
        hyps = []
        for (side, nom, qn, score), conf in zip(rows, _softmax_conf([r[3] for r in rows], temp)):
            h = dict(by[(side, nom, qn)])
            h["score"], h["confidence"] = score, conf
            hyps.append(h)
        for r, h in enumerate(hyps, 1):
            h["rank"] = r
        ranked[site] = hyps
    return (ranked, info) if set(ranked) == set(candidates) else None


# ------------------------------------------------------------------ độ giống nét chữ
class GlyphSimilarity:
    """Dice, cosine làm mờ, SSIM, Chamfer giữa chữ gốc và chữ ứng viên khi vẽ ra ảnh 256px (cùng định nghĩa với add_nom_glyph_similarity).

    Kết quả của mỗi cặp (chữ gốc, chữ ứng viên) được lưu ra file để lần sau không phải tính lại; cặp có chữ không font nào vẽ được lưu là None.
    Phần dùng riêng cho từng chữ (làm mờ, bản đồ khoảng cách, cửa sổ SSIM) chỉ tính một lần cho mỗi chữ gốc."""

    WIN = 7

    def __init__(self, font_dir: Path = FONT_DIR, cache_path: Path | None = None, workers: int = 4):
        self.font_dir = Path(font_dir)
        self.cache_path = Path(cache_path) if cache_path else None
        self.workers = workers
        self._fonts = None
        self._lock = threading.Lock()
        self.pairs: dict[tuple[str, str], tuple | None] = {}
        self._load()

    # ---- file cache
    def _load(self):
        if self.cache_path and self.cache_path.exists():
            try:
                self.pairs = pickle.loads(self.cache_path.read_bytes())
            except Exception:
                self.pairs = {}

    def save(self):
        if not self.cache_path:
            return
        tmp = self.cache_path.with_suffix(".tmp")
        with self._lock:
            tmp.write_bytes(pickle.dumps(self.pairs, protocol=4))
        tmp.replace(self.cache_path)

    # ---- vẽ chữ
    def _font_list(self):
        if self._fonts is None:
            try:
                from .. import add_nom_glyph_similarity as g
            except ImportError as e:      # thiếu fontTools / Pillow / scipy / openpyxl
                raise RankerUnavailable(f"thiếu thư viện để vẽ chữ: {e}") from e
            self._g = g
            fonts = g.load_fonts(self.font_dir)
            if not fonts:
                raise RankerUnavailable(f"không có font Hán Nôm trong {self.font_dir}")
            self._fonts = fonts
        return self._fonts

    def _prep(self, ch: str) -> dict | None:
        from scipy.ndimage import binary_erosion, distance_transform_edt, gaussian_filter, uniform_filter
        g = self._g
        try:
            a = g.render(ch, self._fonts)
        except ValueError:
            return None
        A = a > .5
        e = A ^ binary_erosion(A)
        ga = gaussian_filter(a, g.BLUR)
        ux = uniform_filter(a, self.WIN)
        cov = self.WIN ** 2 / (self.WIN ** 2 - 1)
        return {"a": a, "A": A, "ga": ga, "gn": float(np.linalg.norm(ga)), "ux": ux,
                "vx": (uniform_filter(a * a, self.WIN) - ux * ux) * cov,
                "e": e, "dt": distance_transform_edt(~e) if e.any() else None}

    def _pair(self, p: dict, q: dict) -> tuple:
        """Giống add_nom_glyph_similarity.metrics(p.a, q.a), nhưng dùng lại phần đã tính theo từng chữ."""
        from scipy.ndimage import uniform_filter
        size = self._g.SIZE
        A, B = p["A"], q["A"]
        s = A.sum() + B.sum()
        dice = 2 * (A & B).sum() / s if s else 0.0
        denom = p["gn"] * q["gn"]
        cos = float((p["ga"] * q["ga"]).sum() / denom) if denom else 0.0
        w, cov = self.WIN, self.WIN ** 2 / (self.WIN ** 2 - 1)
        c1, c2 = 0.01 ** 2, 0.03 ** 2
        uxy = (uniform_filter(p["a"] * q["a"], w) - p["ux"] * q["ux"]) * cov
        ssim = ((2 * p["ux"] * q["ux"] + c1) * (2 * uxy + c2)
                / ((p["ux"] ** 2 + q["ux"] ** 2 + c1) * (p["vx"] + q["vx"] + c2)))
        ssim = float(ssim[3:-3, 3:-3].mean())
        if p["dt"] is None or q["dt"] is None:
            chamfer = math.hypot(size, size)
        else:
            chamfer = (q["dt"][p["e"]].mean() + p["dt"][q["e"]].mean()) / 2
        return tuple(round(float(v), 6) for v in (dice, cos, ssim, chamfer))

    def _compute_group(self, query: str, cands: list[str]) -> dict:
        p = self._prep(query)
        out = {}
        for c in cands:
            q = self._prep(c) if p is not None else None
            out[(query, c)] = None if p is None or q is None else self._pair(p, q)
        return out

    def compute(self, pairs, progress=None) -> dict:
        """pairs: các (chữ gốc, chữ ứng viên). Trả về {cặp: (dice, cosblur, ssim, chamfer) | None}; tính cặp chưa có và lưu cache."""
        pairs = set(pairs)
        todo = [p for p in pairs if p not in self.pairs]
        if todo:
            self._font_list()
            groups: dict[str, list[str]] = {}
            for qch, c in todo:
                groups.setdefault(qch, []).append(c)
            done = 0
            with ThreadPoolExecutor(self.workers) as pool:
                for res in pool.map(lambda kv: self._compute_group(*kv), list(groups.items())):
                    with self._lock:
                        self.pairs.update(res)
                    done += len(res)
                    if progress:
                        progress(done, len(todo))
            self.save()
        return {p: self.pairs[p] for p in pairs}


# ------------------------------------------------------------------ mô hình
class Ranker:
    def __init__(self, booster, features: list[str], temperature: float, glyph: GlyphSimilarity, info: dict):
        self.booster, self.features, self.temperature, self.glyph, self.info = booster, features, temperature, glyph, info

    @classmethod
    def load(cls, directory: Path | str = RANKER_DIR, font_dir: Path | str = FONT_DIR) -> "Ranker":
        directory = Path(directory)
        model_file, meta_file = directory / "ranker_lgbm.txt", directory / "metrics.json"
        if not model_file.is_file() or not meta_file.is_file():
            raise RankerUnavailable(f"chưa có mô hình ranker trong {directory} (chạy python -m scripts.train_ranker)")
        try:
            import lightgbm as lgb
        except ImportError as e:
            raise RankerUnavailable("thiếu thư viện lightgbm (pip install lightgbm)") from e
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        cfg_file = directory / "ranker_config.json"
        cfg = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.is_file() else {}
        booster = lgb.Booster(model_file=str(model_file))
        glyph = GlyphSimilarity(Path(font_dir), directory / "glyph_pairs.pkl")
        info = {"model": meta.get("best_model"), "features": len(meta["features"]),
                "temperature": cfg.get("temperature", 1.0), "file": model_file.name}
        return cls(booster, list(meta["features"]), float(cfg.get("temperature", 1.0)), glyph, info)

    # ---- đặc trưng
    def frame(self, candidates: dict[str, list[dict]], progress=None) -> pd.DataFrame:
        """Mỗi giả thuyết của mọi vị trí là một dòng, các cột như data/ranker_training_dataset.csv, cộng cột phụ _site/_i để ghép lại."""
        rows = []
        for key, hyps in candidates.items():
            keep = next((h for h in hyps if h["side"] == "KEEP"), None)
            keep_nom = keep["nom"] if keep else ""
            for i, h in enumerate(hyps):
                e = h.get("evidence") or {}
                side = h["side"]
                rank = e.get("visual_rank")
                rows.append({
                    "site_id": key, "_i": i, "_query": keep_nom, "_cand": h["nom"],
                    "is_nom": int(side == "NOM"), "is_viet": int(side == "QN"), "is_keep": int(side == "KEEP"),
                    "has_nom_visual_rank": int(side == "NOM" and rank is not None),
                    "nom_visual_neglog_rank": -math.log(rank + 1) if side == "NOM" and rank is not None else 0.0,
                    "viet_distance": float(e.get("ortho") or 0.0) if side == "QN" else 0.0,
                    "nom_new_semantic_delta_logp": float(e.get("nlm_delta") or 0.0) if side == "NOM" else 0.0,
                    "viet_new_semantic_delta_logp": float(e.get("qlm_delta") or 0.0) if side == "QN" else 0.0})
        d = pd.DataFrame(rows)
        if d.empty:
            return d
        # độ giống nét chữ: thứ hạng trung bình của 4 thước đo trong số ứng viên NOM của cùng vị trí (như add_nom_glyph_similarity)
        from scipy.stats import rankdata
        nom = d[d.is_nom == 1]
        m = self.glyph.compute(set(zip(nom["_query"], nom["_cand"])), progress)
        d["has_nom_glyph_rank"], d["nom_glyph_neglog_rank"] = 0, 0.0
        for site, grp in nom.groupby("site_id", sort=False):
            idx, vals = [], []
            for ix, qch, c in zip(grp.index, grp["_query"], grp["_cand"]):
                v = m.get((qch, c))
                if v is not None:
                    idx.append(ix)
                    vals.append(v)
            if not idx:
                continue
            v = np.asarray(vals)
            ranks = np.vstack([rankdata(-v[:, 0], method="average"), rankdata(-v[:, 1], method="average"),
                               rankdata(-v[:, 2], method="average"), rankdata(v[:, 3], method="average")]).mean(axis=0)
            ranks = np.round(ranks, 4)
            d.loc[idx, "has_nom_glyph_rank"] = 1
            d.loc[idx, "nom_glyph_neglog_rank"] = -np.log(ranks + 1)
        return add_group_features(d)

    # ---- xếp hạng
    def rank_all(self, candidates: dict[str, list[dict]], progress=None) -> dict[str, list[dict]]:
        """Trả về bản sao của candidates với score = điểm mô hình, confidence = softmax(điểm / T) trong từng vị trí, rank theo thứ tự mới."""
        if not candidates:
            return {}
        d = self.frame(candidates, progress)
        d["_score"] = self.booster.predict(d[self.features])
        out: dict[str, list[dict]] = {}
        for site, grp in d.groupby("site_id", sort=False):
            hyps = candidates[site]
            sc = grp["_score"].to_numpy() / self.temperature
            e = np.exp(sc - sc.max())
            conf = e / e.sum()
            new = []
            for i, s, c in zip(grp["_i"], grp["_score"], conf):
                h = dict(hyps[i])
                h["score"], h["confidence"] = round(float(s), 4), float(c)
                new.append(h)
            new.sort(key=lambda h: (-h["score"], h["side"], h["nom"], h["qn"]))
            for r, h in enumerate(new, 1):
                h["rank"] = r
            out[site] = new
        return out
