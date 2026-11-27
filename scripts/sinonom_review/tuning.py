"""Tinh chỉnh trọng số chấm điểm ứng viên từ các nhãn người duyệt đã chọn, và đo độ chính xác của pipeline.

Dữ liệu: mỗi thư mục sách có review_state.json (lựa chọn của người duyệt: KEEP / NOM+chữ mới / QN+âm mới) và candidates_cache.json
(mọi ứng viên của từng cặp lệch kèm bằng chứng thô: hạng hình, Δ log P của LM Nôm và Việt, khoảng cách chính tả, cờ dị thể / dạng vùng / ...).
Từ bằng chứng thô, scoring.rank_hypotheses chấm lại một cặp trong vài chục micro-giây mà không cần nạp mô hình, nên dò được hàng nghìn bộ
trọng số trong vài chục giây.

Nhãn chuẩn của một cặp lệch là giả thuyết (side, nom, qn) mà người duyệt đã chọn. Một cặp đúng khi giả thuyết xếp hạng 1 trùng nhãn chuẩn.

Thước đo (compute_metrics):
  top1 / top3 / top5   tỉ lệ cặp mà nhãn chuẩn nằm trong k ứng viên đầu (KEEP tính là một ứng viên)
  mrr                  trung bình 1 / hạng của nhãn chuẩn
  nll                  trung bình −log confidence của nhãn chuẩn (thấp hơn là tốt hơn; đo cả độ hiệu chỉnh của confidence)
  ece                  sai số hiệu chỉnh kỳ vọng: confidence của top-1 lệch bao nhiêu so với tỉ lệ đúng thực tế (10 khoảng)
  side_accuracy        đoán đúng loại sửa KEEP / NOM / QN, kèm ma trận nhầm lẫn và precision / recall / F1 từng loại
  fix_*                gộp NOM + QN thành "cần sửa", đối lại KEEP: precision / recall của việc phát hiện chỗ cần sửa
  auto                 với ngưỡng confidence τ: tỉ lệ cặp tự chọn được (coverage) và độ chính xác của riêng chúng (precision)
Số liệu "held-out" là kết quả leave-one-book-out: tinh chỉnh trên các sách còn lại, đo trên sách bị giữ lại, nên không bị lạc quan do khớp quá.

Hạn chế cần nhớ khi đọc kết quả:
  * Người duyệt nhìn danh sách đã xếp theo trọng số mặc định nên nhãn thiên về ứng viên đứng đầu (presentation bias); số top-1 của trọng số mặc định
    vì vậy có thể cao hơn thực tế, còn lợi ích của trọng số mới có thể bị đánh giá thấp.
  * Chỉ cặp đã duyệt có nhãn; cặp chưa duyệt không được tính.
"""
from __future__ import annotations

import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .scoring import DEFAULT_WEIGHTS, RANK_DEFAULTS, VARIANT_MARGIN, rank_hypotheses, features_from_hyps

SIDES = ("KEEP", "NOM", "QN")
BASE_WEIGHTS = {**DEFAULT_WEIGHTS, **RANK_DEFAULTS}

# Không gian tìm kiếm. Ba tham số không dò: nom_prior (cố định 0, vì softmax không đổi khi cộng cùng một hằng vào mọi điểm nên chỉ cần
# neo một tham số), max_ortho và lm_topk (đổi tập ứng viên, nằm trong candidates_cache.json nên không chấm lại được nếu không chạy lại LM).
SPACE: dict[str, tuple] = {
    "w_vis": ("log", 0.1, 8.0),
    "w_nlm": ("lin", 0.0, 3.0),
    "novis_rank": ("logint", 3, 200),
    "w_ortho": ("log", 0.2, 10.0),
    "w_qlm": ("lin", 0.0, 3.0),
    "qn_prior": ("lin", -8.0, 8.0),
    "keep_score": ("lin", -16.0, 4.0),
    "bias_variant": ("lin", -5.0, 3.0),
    "bias_regional": ("lin", -5.0, 3.0),
    "bias_relaxed": ("lin", -5.0, 3.0),
    "bias_reading": ("lin", -5.0, 3.0),
    "bias_lm": ("lin", -5.0, 3.0),
    "cap_only": ("bool",),
}
OBJECTIVES = ("nll", "mrr", "top1")


# ====================================================================== nạp nhãn
@dataclass
class Example:
    book: str
    key: str
    feats: tuple            # (keep_nom, keep_qn, nom, qn) từ scoring.features_from_hyps
    gold: tuple             # (side, nom, qn) người duyệt đã chọn


def load_examples(data_dir: str | Path, books: list[str] | None = None, verbose: bool = True) -> list[Example]:
    """Mọi cặp đã duyệt có trong data_dir/<sách>/{review_state.json, candidates_cache.json}. Bỏ cặp không còn trong cache (dữ liệu đã đổi sau
    khi duyệt) và cặp mà lựa chọn không nằm trong danh sách ứng viên."""
    out: list[Example] = []
    for d in sorted(Path(data_dir).iterdir()):
        sp, cp = d / "review_state.json", d / "candidates_cache.json"
        if not (d.is_dir() and sp.exists() and cp.exists()) or (books and d.name not in books):
            continue
        try:
            decisions = json.loads(sp.read_text(encoding="utf-8")).get("decisions", {})
            cands = json.loads(cp.read_text(encoding="utf-8")).get("candidates", {})
        except (OSError, ValueError):
            continue
        n0, stale, off = len(out), 0, 0
        for key, dec in decisions.items():
            hyps = cands.get(key)
            if not hyps:
                stale += 1
                continue
            gold = (dec["side"], dec["nom"], dec["qn"])
            if not any((h["side"], h["nom"], h["qn"]) == gold for h in hyps):
                off += 1
                continue
            out.append(Example(d.name, key, features_from_hyps(hyps), gold))
        if verbose and decisions:
            print(f"  {d.name}: {len(out) - n0}/{len(decisions)} nhãn dùng được"
                  + (f" (bỏ {stale} cặp không còn trong cache, {off} nhãn ngoài danh sách ứng viên)" if stale or off else ""))
    return out


# ====================================================================== chấm và đo
def evaluate(examples: list[Example], weights: dict) -> list[tuple]:
    """Với mỗi ví dụ: (hạng nhãn chuẩn, confidence nhãn chuẩn, loại nhãn chuẩn, loại top-1, confidence top-1, top-1 đúng?,
    hạng nhãn chuẩn trong riêng các ứng viên cùng loại)."""
    W = {**BASE_WEIGHTS, **weights}
    res = []
    for ex in examples:
        hyps = rank_hypotheses(*ex.feats, W)
        for i, h in enumerate(hyps):
            if (h["side"], h["nom"], h["qn"]) == ex.gold:
                break
        top = hyps[0]
        within = 1 + sum(1 for x in hyps[:i] if x["side"] == h["side"])
        res.append((i + 1, h["confidence"], ex.gold[0], top["side"], top["confidence"], i == 0, within))
    return res


def _ece(conf: list[float], ok: list[bool], bins: int = 10) -> float:
    n, tot = len(conf), 0.0
    for b in range(bins):
        idx = [i for i, c in enumerate(conf) if (c >= b / bins) and (c < (b + 1) / bins or b == bins - 1)]
        if idx:
            tot += len(idx) / n * abs(sum(ok[i] for i in idx) / len(idx) - sum(conf[i] for i in idx) / len(idx))
    return tot


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "support": tp + fn}


def compute_metrics(res: list[tuple], taus=(0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99)) -> dict:
    n = len(res)
    if not n:
        return {"n": 0}
    ranks = [r[0] for r in res]
    top_conf, top_ok = [r[4] for r in res], [r[5] for r in res]
    m = {"n": n,
         "top1": sum(r == 1 for r in ranks) / n, "top3": sum(r <= 3 for r in ranks) / n, "top5": sum(r <= 5 for r in ranks) / n,
         "mrr": sum(1 / r for r in ranks) / n,
         "nll": sum(-math.log(max(r[1], 1e-12)) for r in res) / n,
         "ece": _ece(top_conf, top_ok)}
    conf = {t: Counter() for t in SIDES}                     # conf[thật][đoán]
    for r in res:
        conf[r[2]][r[3]] += 1
    m["side_confusion"] = {t: {p: conf[t][p] for p in SIDES} for t in SIDES}
    m["side_accuracy"] = sum(conf[t][t] for t in SIDES) / n
    m["side"] = {t: _prf(conf[t][t], sum(conf[o][t] for o in SIDES if o != t), sum(conf[t][o] for o in SIDES if o != t)) for t in SIDES}
    m["side_macro_f1"] = sum(v["f1"] for v in m["side"].values()) / 3
    fix_tp = sum(conf[t][p] for t in ("NOM", "QN") for p in ("NOM", "QN"))
    fix_fp = sum(conf["KEEP"][p] for p in ("NOM", "QN"))
    fix_fn = sum(conf[t]["KEEP"] for t in ("NOM", "QN"))
    m["fix"] = _prf(fix_tp, fix_fp, fix_fn)
    m["top1_by_side"] = {t: {"n": sum(r[2] == t for r in res),
                             "top1": (sum(r[5] for r in res if r[2] == t) / max(1, sum(r[2] == t for r in res)))} for t in SIDES}
    # chất lượng xếp hạng trong từng loại, tách khỏi việc đoán loại: khi người duyệt chọn sửa NOM (hoặc QN), ứng viên NOM (QN) đứng đầu có đúng không
    m["within_side"] = {}
    for t in ("NOM", "QN"):
        w = [r[6] for r in res if r[2] == t]
        m["within_side"][t] = {"n": len(w), "top1": sum(x == 1 for x in w) / len(w) if w else None,
                               "top3": sum(x <= 3 for x in w) / len(w) if w else None}
    m["auto"] = []
    for t in taus:
        sel = [r for r in res if r[4] >= t]
        m["auto"].append({"tau": t, "coverage": len(sel) / n, "selected": len(sel),
                          "precision": sum(r[5] for r in sel) / len(sel) if sel else None})
    return m


def bootstrap_ci(ok: list[bool], reps: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Khoảng tin cậy bootstrap của một tỉ lệ (ví dụ top-1)."""
    if not ok:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(ok)
    vals = sorted(sum(ok[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return vals[int(alpha / 2 * reps)], vals[int((1 - alpha / 2) * reps) - 1]


def choose_threshold(res: list[tuple], target: float = 0.95, min_support: int = 30) -> dict | None:
    """Ngưỡng confidence τ nhỏ nhất (coverage lớn nhất) mà các cặp có confidence top-1 ≥ τ đạt precision ≥ target với ít nhất min_support cặp.
    Quét theo từng giá trị confidence quan sát được; precision tính trên toàn bộ cặp ≥ τ."""
    pts = sorted(((r[4], r[5]) for r in res), reverse=True)
    best, hits = None, 0
    for i, (c, ok) in enumerate(pts, 1):
        hits += ok
        if i >= min_support and hits / i >= target:
            best = {"tau": round(c, 4), "coverage": i / len(pts), "selected": i, "precision": hits / i}
    return best


# ====================================================================== bản vector hoá (chỉ để dò; báo cáo luôn dùng evaluate)
class Packed:
    """Mọi giả thuyết của mọi ví dụ xếp thành mảng numpy để chấm một bộ trọng số trong vài mili-giây (nhanh ~25 lần evaluate).
    Phải cho cùng kết quả với scoring.rank_hypotheses (kiểm bằng test: so top-1 / hạng trên bộ trọng số ngẫu nhiên). Chỉ khác ở chỗ hoà điểm
    được xử lý bằng thứ tự (side, nom, qn) đã tính sẵn, giống sort của rank_hypotheses."""

    def __init__(self, examples: list[Example]):
        cols = {k: [] for k in ("side", "vr", "nlm", "qlm", "ortho", "variant", "vonly", "lm", "reg", "rel", "read", "tb")}
        offs, gold_idx = [], []
        for ex in examples:
            keep_nom, keep_qn, nom, qn = ex.feats
            rows = [("KEEP", keep_nom, keep_qn, {})]
            rows += [("NOM", c, keep_qn, e) for c, e in nom.items()]
            from .text import match_case
            rows += [("QN", keep_nom, match_case(s2, keep_qn), e) for s2, e in qn.items()]
            order = {k: i for i, k in enumerate(sorted((r[0], r[1], r[2]) for r in rows))}
            offs.append(len(cols["side"]))
            for side, n, q, e in rows:
                if (side, n, q) == ex.gold:
                    gold_idx.append(len(cols["side"]))
                cols["side"].append(SIDES.index(side))
                vr = e.get("visual_rank")
                cols["vr"].append(np.nan if vr is None else vr)
                cols["nlm"].append(e.get("nlm_delta", 0.0))
                cols["qlm"].append(e.get("qlm_delta", 0.0))
                cols["ortho"].append(e.get("ortho", 0.0))
                cols["variant"].append(bool(e.get("variant")))
                cols["vonly"].append(bool(e.get("variant_only")))
                cols["lm"].append(bool(e.get("lm_only")))
                cols["reg"].append(bool(e.get("regional_only")))
                cols["rel"].append(bool(e.get("relaxed_only")))
                cols["read"].append(bool(e.get("reading_only")))
                cols["tb"].append(order[(side, n, q)])
        self.c = {k: np.asarray(v) for k, v in cols.items()}
        self.offs = np.asarray(offs)
        self.gold = np.asarray(gold_idx)
        self.seg = np.repeat(np.arange(len(offs)), np.diff(np.append(offs, len(cols["side"]))))
        c = self.c
        self.is_keep, self.is_nom, self.is_qn = c["side"] == 0, c["side"] == 1, c["side"] == 2
        self.only = c["vonly"] | c["lm"] | c["reg"] | c["rel"] | c["read"]
        self.other = ~self.is_keep & ~self.only
        self.tb_gold = c["tb"][self.gold]

    def result(self, weights: dict) -> dict:
        """Mảng theo từng ví dụ: rank (hạng nhãn chuẩn), logp (log confidence của nhãn chuẩn)."""
        W, c = {**BASE_WEIGHTS, **weights}, self.c
        rank = np.where(np.isnan(c["vr"]), np.where(c["vonly"], 1.0, float(W["novis_rank"])), c["vr"])
        s_nom = W["nom_prior"] - W["w_vis"] * np.log(rank + 1) + W["w_nlm"] * c["nlm"] + W["bias_variant"] * c["variant"]             + W["bias_lm"] * c["lm"] + W["bias_regional"] * c["reg"]
        s_qn = W["qn_prior"] - W["w_ortho"] * c["ortho"] + W["w_qlm"] * c["qlm"] + W["bias_regional"] * c["reg"]             + W["bias_relaxed"] * c["rel"] + W["bias_reading"] * c["read"]
        s = np.where(self.is_keep, W["keep_score"], np.where(self.is_nom, s_nom, s_qn))
        if W["cap_only"]:
            mx = np.maximum.reduceat(np.where(self.only, s, -np.inf), self.offs)
            mn = np.minimum.reduceat(np.where(self.other, s, np.inf), self.offs)
            over = np.where(np.isfinite(mx) & np.isfinite(mn), np.maximum(mx - (mn - VARIANT_MARGIN), 0.0), 0.0)
            s = s - over[self.seg] * self.only
        m = np.maximum.reduceat(s, self.offs)
        e = np.exp(s - m[self.seg])
        z = np.add.reduceat(e, self.offs)
        logp = (s[self.gold] - m) - np.log(z)

        sr = np.round(s, 4)
        gs = sr[self.gold][self.seg]
        ahead = (sr > gs) | ((sr == gs) & (c["tb"] < self.tb_gold[self.seg]))
        return {"rank": 1 + np.add.reduceat(ahead.astype(np.int64), self.offs), "logp": logp}

    def objective(self, weights: dict, objective: str) -> float:
        r = self.result(weights)
        floor = math.log(1e-12)
        nll = float(-np.mean(np.maximum(r["logp"], floor)))
        if objective == "nll":
            return -nll
        main = float(np.mean(1.0 / r["rank"])) if objective == "mrr" else float(np.mean(r["rank"] == 1))
        return main - 1e-3 * nll


# ====================================================================== dò trọng số
def _sample(spec, rng):
    kind = spec[0]
    if kind == "lin":
        return rng.uniform(spec[1], spec[2])
    if kind == "log":
        return math.exp(rng.uniform(math.log(spec[1]), math.log(spec[2])))
    if kind == "logint":
        return int(round(math.exp(rng.uniform(math.log(spec[1]), math.log(spec[2])))))
    return rng.random() < 0.5


def _neighbors(spec, v, step):
    kind = spec[0]
    if kind == "bool":
        return [not v]
    if kind == "lin":
        d = step * (spec[2] - spec[1]) / 8
        return [min(spec[2], v + d), max(spec[1], v - d)]
    f = math.exp(step * 0.5)
    out = [min(spec[2], v * f), max(spec[1], v / f)]
    return [int(round(x)) for x in out] if kind == "logint" else out


def tune(examples: list[Example], objective: str = "nll", trials: int = 400, seed: int = 0, space: dict | None = None,
         start: dict | None = None, log=None) -> tuple[dict, float]:
    """Dò trọng số tối đa hoá objective trên examples: random search toàn không gian + vài điểm xung quanh điểm tốt nhất, rồi dò cục bộ
    (pattern search) từ ba điểm đầu. Trả về (trọng số đầy đủ, giá trị objective). Bộ trọng số mặc định luôn là một ứng viên nên kết quả
    không bao giờ kém mặc định trên chính tập dữ liệu này (nhưng có thể khớp quá: dùng cross_validate để đo)."""
    space = space or SPACE
    rng = random.Random(seed)
    base = {**BASE_WEIGHTS, **(start or {})}
    cache: dict[tuple, float] = {}
    packed = Packed(examples)

    def score(w):
        k = tuple(sorted((n, round(w[n], 6) if isinstance(w[n], float) else w[n]) for n in space))
        if k not in cache:
            cache[k] = packed.objective(w, objective)
        return cache[k]

    pool = [(score(base), base)]
    for _ in range(trials):
        w = {**base, **{n: _sample(s, rng) for n, s in space.items()}}
        pool.append((score(w), w))
    pool.sort(key=lambda x: -x[0])
    for _ in range(max(1, trials // 4)):                    # xung quanh điểm tốt: nhiễu nhỏ trên vài tham số
        _, b = pool[rng.randrange(min(3, len(pool)))]
        w = dict(b)
        for n in rng.sample(list(space), k=min(3, len(space))):
            w[n] = _neighbors(space[n], w[n], rng.uniform(0.2, 1.5))[rng.randrange(len(_neighbors(space[n], w[n], 1)))]
        pool.append((score(w), w))
    pool.sort(key=lambda x: -x[0])

    best_s, best_w = pool[0]
    for s0, w in pool[:3]:
        cur_s, cur = s0, dict(w)
        step = 1.0
        while step >= 0.03:
            improved = False
            for n, spec in space.items():
                for v in _neighbors(spec, cur[n], step):
                    cand = {**cur, n: v}
                    s = score(cand)
                    if s > cur_s + 1e-9:
                        cur_s, cur, improved = s, cand, True
            if not improved:
                step /= 2
        if cur_s > best_s:
            best_s, best_w = cur_s, cur
    if log:
        log(f"    {len(cache)} bộ trọng số đã thử, objective {objective}: mặc định {score(base):+.4f} -> {best_s:+.4f}")
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in best_w.items()}, best_s


def cross_validate(examples: list[Example], objective: str = "nll", trials: int = 400, seed: int = 0, min_fold: int = 50,
                   log=print) -> dict:
    """Leave-one-book-out: với mỗi sách có ≥ min_fold nhãn, dò trọng số trên các sách còn lại rồi đo trên sách đó (cả mặc định và đã dò)."""
    by_book: dict[str, list[Example]] = {}
    for ex in examples:
        by_book.setdefault(ex.book, []).append(ex)
    folds = [b for b, v in by_book.items() if len(v) >= min_fold]
    out = {"folds": {}, "oof_default": [], "oof_tuned": []}
    for b in folds:
        train = [e for e in examples if e.book != b]
        log(f"  fold giữ lại «{b}» ({len(by_book[b])} nhãn), dò trên {len(train)} nhãn của các sách còn lại")
        w, _ = tune(train, objective, trials, seed, log=log)
        test = by_book[b]
        rd, rt = evaluate(test, BASE_WEIGHTS), evaluate(test, w)
        out["oof_default"] += rd
        out["oof_tuned"] += rt
        out["folds"][b] = {"n": len(test), "weights": {k: w[k] for k in SPACE}, "default": compute_metrics(rd), "tuned": compute_metrics(rt)}
    return out


# ====================================================================== chạy trọn quy trình
def run(data_dir: str | Path, out_dir: str | Path, objective: str = "nll", trials: int = 400, seed: int = 0, target_precision: float = 0.95,
        min_fold: int = 50, books: list[str] | None = None, log=print) -> dict:
    """Nạp nhãn -> đo mặc định -> kiểm chứng chéo theo sách -> dò trên toàn bộ nhãn -> tự chọn giữa mặc định và đã dò theo kết quả held-out ->
    chọn ngưỡng tự động -> ghi tuned_weights.json và tuning_report.md/json vào out_dir."""
    t0 = time.time()
    log("Đọc nhãn đã duyệt ...")
    examples = load_examples(data_dir, books)
    if len(examples) < 2 * min_fold:
        raise ValueError(f"Chỉ có {len(examples)} nhãn dùng được, quá ít để dò trọng số (cần ít nhất {2 * min_fold}).")
    log(f"Tổng {len(examples)} nhãn. Kiểm chứng chéo theo sách (objective={objective}, {trials} lần thử mỗi fold) ...")
    cv = cross_validate(examples, objective, trials, seed, min_fold, log)
    if not cv["folds"]:
        raise ValueError(f"Không sách nào có ≥ {min_fold} nhãn để kiểm chứng chéo.")
    oof_d, oof_t = compute_metrics(cv["oof_default"]), compute_metrics(cv["oof_tuned"])
    log("Dò trên toàn bộ nhãn ...")
    w_all, _ = tune(examples, objective, trials, seed, log=log)

    # tự chọn: dùng trọng số đã dò chỉ khi nó thắng mặc định trên dữ liệu held-out theo chính objective (phá hoà bằng top-1)
    key = {"nll": lambda m: -m["nll"], "mrr": lambda m: m["mrr"], "top1": lambda m: m["top1"]}[objective]
    use_tuned = (key(oof_t), oof_t["top1"]) > (key(oof_d), oof_d["top1"])
    selected, chosen_oof = ("tuned", cv["oof_tuned"]) if use_tuned else ("default", cv["oof_default"])
    weights = w_all if use_tuned else dict(BASE_WEIGHTS)
    thr = choose_threshold(chosen_oof, target_precision)
    final_metrics = compute_metrics(evaluate(examples, weights))
    ci = {"default": bootstrap_ci([r[5] for r in cv["oof_default"]]), "tuned": bootstrap_ci([r[5] for r in cv["oof_tuned"]])}

    result = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "objective": objective, "trials": trials, "seed": seed,
              "n_labels": len(examples), "books": dict(Counter(e.book for e in examples)), "selected": selected,
              "weights": {k: weights[k] for k in BASE_WEIGHTS}, "weights_tuned": {k: w_all[k] for k in BASE_WEIGHTS},
              "weights_default": dict(BASE_WEIGHTS), "auto_accept": thr, "target_precision": target_precision,
              "held_out": {"default": oof_d, "tuned": oof_t, "top1_ci95": ci, "folds": cv["folds"]},
              "in_sample": final_metrics, "seconds": round(time.time() - t0, 1)}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tuned_weights.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "tuning_report.md").write_text(render_report(result), encoding="utf-8")
    log(f"Đã chọn: {selected}. Ghi {out / 'tuned_weights.json'} và {out / 'tuning_report.md'} ({result['seconds']}s)")
    return result


# ====================================================================== báo cáo
def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def _metrics_table(rows: dict[str, dict]) -> list[str]:
    head = ["", "n", "top-1", "top-3", "top-5", "MRR", "NLL", "ECE", "Đúng loại", "Macro-F1 loại", "Phát hiện cần sửa P / R",
            "top-1 khi thật là KEEP / NOM / QN", "top-1 trong loại NOM / QN"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, m in rows.items():
        out.append(f"| {name} | {m['n']} | {_pct(m['top1'])} | {_pct(m['top3'])} | {_pct(m['top5'])} | {m['mrr']:.3f} | {m['nll']:.3f} | {m['ece']:.3f} | "
                   f"{_pct(m['side_accuracy'])} | {m['side_macro_f1']:.3f} | {_pct(m['fix']['precision'])} / {_pct(m['fix']['recall'])} | "
                   f"{' / '.join(_pct(m['top1_by_side'][t]['top1']) for t in SIDES)} | "
                   f"{_pct(m['within_side']['NOM']['top1'])} / {_pct(m['within_side']['QN']['top1'])} |")
    return out


def render_report(r: dict) -> str:
    h = r["held_out"]
    L = [f"# Báo cáo tinh chỉnh trọng số ({r['created']})", "",
         f"- Nhãn dùng được: **{r['n_labels']}** cặp đã duyệt, theo sách: " + "; ".join(f"{b} {n}" for b, n in r["books"].items()),
         f"- Objective dò: `{r['objective']}`, {r['trials']} lần thử mỗi lần dò, seed {r['seed']}, mất {r['seconds']}s",
         f"- **Bộ trọng số được chọn: {'ĐÃ DÒ' if r['selected'] == 'tuned' else 'MẶC ĐỊNH'}** "
         "(chọn tự động: bộ đã dò chỉ được dùng khi thắng mặc định trên dữ liệu held-out)", "",
         "## 1. Độ chính xác (held-out, leave-one-book-out)", "",
         "Mỗi sách được giữ lại làm tập kiểm tra; trọng số dò trên các sách còn lại. Đây là ước lượng công bằng cho sách mới.", ""]
    L += _metrics_table({"Mặc định": h["default"], "Đã dò": h["tuned"]})
    ci = h["top1_ci95"]
    L += ["", f"Khoảng tin cậy 95% (bootstrap) của top-1: mặc định {_pct(ci['default'][0])} – {_pct(ci['default'][1])}; "
              f"đã dò {_pct(ci['tuned'][0])} – {_pct(ci['tuned'][1])}.", "",
          "### Từng sách giữ lại", ""]
    rows = {}
    for b, f in h["folds"].items():
        rows[f"{b} (mặc định)"], rows[f"{b} (đã dò)"] = f["default"], f["tuned"]
    L += _metrics_table(rows)
    chosen = h["tuned" if r["selected"] == "tuned" else "default"]
    L += ["", f"## 2. Loại sửa (bộ đã chọn, held-out)", "", "Hàng = loại người duyệt chọn, cột = loại top-1 dự đoán.", "",
          "| thật \\ đoán | KEEP | NOM | QN | recall |", "|---|---|---|---|---|"]
    for t in SIDES:
        row = chosen["side_confusion"][t]
        L.append(f"| {t} | {row['KEEP']} | {row['NOM']} | {row['QN']} | {_pct(chosen['side'][t]['recall'])} |")
    L.append("| precision | " + " | ".join(_pct(chosen["side"][t]["precision"]) for t in SIDES) + " | |")
    L += ["", "## 3. Tự động chọn theo ngưỡng confidence (held-out)", "",
          "Nếu pipeline tự áp dụng ứng viên top-1 khi confidence ≥ τ, người duyệt chỉ cần xem phần còn lại.", "",
          "| τ | cặp tự chọn | coverage | precision |", "|---|---|---|---|"]
    for a in chosen["auto"]:
        L.append(f"| {a['tau']} | {a['selected']} | {_pct(a['coverage'])} | {_pct(a['precision'])} |")
    t = r["auto_accept"]
    L += ["", (f"**Ngưỡng gợi ý** cho precision ≥ {_pct(r['target_precision'])}: τ = **{t['tau']}**, tự chọn {_pct(t['coverage'])} số cặp "
               f"({t['selected']}) với precision {_pct(t['precision'])}." if t else
               f"Không có ngưỡng nào đạt precision ≥ {_pct(r['target_precision'])} với đủ số cặp: chưa nên tự áp dụng, chỉ dùng để xếp thứ tự duyệt."), ""]
    L += ["## 4. Trọng số", "", "| tham số | mặc định | đã dò | đang dùng |", "|---|---|---|---|"]
    for k in BASE_WEIGHTS:
        L.append(f"| {k} | {r['weights_default'][k]} | {r['weights_tuned'][k]} | {r['weights'][k]} |")
    L += ["", "`lm_topk`, `max_ortho`, `nom_prior` không được dò (xem tuning.py). Bốn tham số `bias_*` và `cap_only` cho phép ứng viên dị thể / dạng vùng / "
          "nới lỏng / âm còn lại / do LM vượt ứng viên sẵn có nếu nhãn cho thấy nên như vậy; mặc định 0 và `cap_only=True` giữ hành vi cũ.", "",
          "## 5. Lưu ý", "",
          "- Người duyệt nhìn danh sách đã xếp theo trọng số mặc định nên nhãn thiên về ứng viên đứng đầu (presentation bias): top-1 của mặc định có thể cao hơn "
          "thực tế và lợi ích của trọng số mới có thể bị đánh giá thấp.",
          "- Chỉ cặp đã duyệt có nhãn; nhãn ngoài danh sách ứng viên và cặp không còn trong cache bị bỏ.",
          "- `confidence` chỉ đáng tin sau khi dò theo `nll` (ECE thấp); dò theo `mrr`/`top1` chỉ tối ưu thứ hạng.", ""]
    return "\n".join(L)


# ====================================================================== dùng kết quả
def load_tuned(path: str | Path) -> dict | None:
    """Đọc tuned_weights.json; trả về bản ghi nếu có và đã chọn bộ đã dò, ngược lại None (dùng trọng số mặc định)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("selected") == "tuned" and isinstance(data.get("weights"), dict) else None
