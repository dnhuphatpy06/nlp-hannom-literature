#!/usr/bin/env python3
"""Chuẩn bị mô hình ranker để web dùng (chạy sau scripts/train_ranker.py, không huấn luyện lại).

1. Nhiệt độ softmax T: chọn T để confidence = softmax(điểm / T) có NLL nhỏ nhất trên điểm out-of-fold (predictions_lobo.csv),
   nên confidence phản ánh đúng mức tin cậy tương đối của mô hình trên sách chưa thấy. Ghi resources/ranker/ranker_config.json.
2. Nạp sẵn cache độ giống nét chữ (resources/ranker/glyph_pairs.pkl) từ các cặp đã tính trong
   data/ranker_candidates_labels_glyph_similarity.xlsx, để 4 sách đã gán nhãn không phải vẽ lại chữ.

Chạy: python -m scripts.finalize_ranker
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from scripts.sinonom_review.ranker import RANKER_DIR, GlyphSimilarity

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "data" / "ranker_candidates_labels_glyph_similarity.xlsx"


def fit_temperature(model_col: str) -> dict:
    p = pd.read_csv(RANKER_DIR / "predictions_lobo.csv", usecols=["site_id", "label", model_col])
    s = p[model_col].to_numpy()
    y = p["label"].to_numpy()
    sites = p["site_id"].to_numpy()
    _, inv = np.unique(sites, return_inverse=True)

    def nll(logT: float) -> float:
        z = s / np.exp(logT)
        m = np.full(inv.max() + 1, -np.inf)
        np.maximum.at(m, inv, z)
        e = np.exp(z - m[inv])
        tot = np.bincount(inv, weights=e)
        gold = np.bincount(inv, weights=e * y) / tot
        return float(-np.log(np.maximum(gold, 1e-12)).mean())

    res = minimize_scalar(nll, bounds=(-3, 3), method="bounded")
    return {"temperature": float(np.exp(res.x)), "nll_at_T": float(res.fun), "nll_at_T1": nll(0.0)}


def prewarm_glyph_cache() -> int:
    from scripts import add_nom_glyph_similarity as g
    df = pd.read_excel(WORKBOOK, sheet_name="Candidates")
    df = df[(df.candidate_type == "NOM") & df.glyph_dice.notna()]
    cache = GlyphSimilarity(cache_path=RANKER_DIR / "glyph_pairs.pkl")
    n = 0
    for o, t, *vals in zip(df["text_nôm_origin"].fillna(""), df["text_nôm"].fillna(""), df.glyph_dice, df.glyph_cosblur,
                           df.glyph_ssim, df.glyph_chamfer):
        pair = g.changed_pair(o, t)
        if pair and pair not in cache.pairs:
            cache.pairs[pair] = tuple(float(v) for v in vals)
            n += 1
    cache.save()
    return n


def main() -> None:
    meta = json.loads((RANKER_DIR / "metrics.json").read_text(encoding="utf-8"))
    t = fit_temperature(meta["best_model"])
    cfg = {"temperature": round(t["temperature"], 4), "model": meta["best_model"],
           "nll_oof": round(t["nll_at_T"], 4), "nll_oof_T1": round(t["nll_at_T1"], 4)}
    (RANKER_DIR / "ranker_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ranker_config.json:", cfg)
    print("cặp chữ nạp sẵn vào glyph_pairs.pkl:", prewarm_glyph_cache())


if __name__ == "__main__":
    main()
