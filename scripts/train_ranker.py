#!/usr/bin/env python3
"""Train and evaluate a learning-to-rank model on data/ranker_training_dataset.csv.

Each query = one `site_id` (one OCR character position) with a candidate list
(NOM / VIET / KEEP) and exactly one gold candidate (label=1).

Evaluation is leave-one-book-out (LOBO): train on 3 books, test on the 4th,
so no page/line of the test book is ever seen in training.

Usage:  python -m scripts.train_ranker
Output: resources/ranker/{ranker_lgbm.txt, ranker_logreg.joblib, metrics.json,
        report.md, predictions_lobo.csv}
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

from scripts.sinonom_review.ranker import REL_COLS, add_group_features  # noqa: F401

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "ranker_training_dataset.csv"
OUT = ROOT / "resources" / "ranker"
SEED = 42

BASE_FEATURES = [
    "is_nom", "is_viet", "is_keep",
    "has_nom_visual_rank", "nom_visual_neglog_rank",
    "has_nom_glyph_rank", "nom_glyph_neglog_rank",
    "viet_distance", "nom_new_semantic_delta_logp", "viet_new_semantic_delta_logp",
]
# add_group_features / REL_COLS live in scripts.sinonom_review.ranker so that training and the
# web app compute the within-site features with exactly the same code.


# ----------------------------------------------------------------- data
def load() -> pd.DataFrame:
    d = pd.read_csv(DATA, encoding="utf-8-sig")
    d["book"] = d["id"].str.split("|").str[0]
    d = d.sort_values(["site_id", "baseline_rank"], kind="stable").reset_index(drop=True)
    return add_group_features(d)


def feature_sets(d: pd.DataFrame) -> dict[str, list[str]]:
    rel = [c for c in d.columns if c.endswith("__rank") or c.endswith("__gap")]
    return {
        "raw": BASE_FEATURES,
        "raw+rel": BASE_FEATURES + ["n_cand", "score_semantic"] + rel,
        # baseline_rank is the output of the previous hand-tuned scorer; labels were
        # collected while looking at that order, so we report both with and without it.
        "raw+rel+baseline_rank": BASE_FEATURES + ["n_cand", "score_semantic", "baseline_rank"] + rel,
    }


# -------------------------------------------------------------- metrics
def group_metrics(d: pd.DataFrame, score: np.ndarray) -> dict:
    """d must carry site_id,label,(is_nom/is_viet/is_keep); score: higher = better."""
    t = d[["site_id", "label", "is_nom", "is_viet", "is_keep"]].copy()
    t["s"] = score
    # Ties broken pessimistically (gold placed after tied non-gold) -> honest numbers.
    t["tie_bad"] = -t["label"]
    t = t.sort_values(["site_id", "s", "tie_bad"], ascending=[True, False, True], kind="stable")
    t["rk"] = t.groupby("site_id").cumcount() + 1
    gold = t[t.label == 1]
    r = gold["rk"].to_numpy()
    ndcg5 = np.where(r <= 5, 1.0 / np.log2(r + 1), 0.0)
    out = {
        "n_sites": int(len(gold)),
        "top1": float((r == 1).mean()), "top3": float((r <= 3).mean()),
        "top5": float((r <= 5).mean()), "mrr": float((1.0 / r).mean()),
        "ndcg@5": float(ndcg5.mean()), "mean_rank": float(r.mean()),
    }
    for name in ("nom", "viet", "keep"):
        m = gold[f"is_{name}"] == 1
        out[f"top1_gold_{name}"] = float((r[m.to_numpy()] == 1).mean()) if m.any() else None
        out[f"n_gold_{name}"] = int(m.sum())
    return out


def baseline_scores(d: pd.DataFrame, kind: str, rng: np.random.Generator) -> np.ndarray:
    if kind == "baseline_rank":          # previous hand-tuned order
        return -d["baseline_rank"].to_numpy(float)
    if kind == "random":
        return rng.random(len(d))
    raise ValueError(kind)


# --------------------------------------------------------------- models
LGB_GRID = [
    dict(num_leaves=7, min_data_in_leaf=20, learning_rate=0.05, n_estimators=200),
    dict(num_leaves=15, min_data_in_leaf=30, learning_rate=0.05, n_estimators=300),
    dict(num_leaves=31, min_data_in_leaf=40, learning_rate=0.03, n_estimators=400),
]


def fit_lgb(tr: pd.DataFrame, feats: list[str], p: dict, objective: str = "lambdarank"):
    group = tr.groupby("site_id", sort=False).size().to_numpy()
    m = lgb.LGBMRanker(
        objective=objective, metric="ndcg", eval_at=[1, 3],
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_lambda=1.0, random_state=SEED, verbose=-1, n_jobs=4, **p)
    m.fit(tr[feats], tr["label"], group=group)
    return m


def fit_logreg(tr: pd.DataFrame, feats: list[str], C: float = 1.0):
    m = make_pipeline(StandardScaler(),
                      LogisticRegression(C=C, max_iter=2000, class_weight="balanced"))
    m.fit(tr[feats], tr["label"])
    return m


def predict(model, d: pd.DataFrame, feats: list[str]) -> np.ndarray:
    if isinstance(model, lgb.LGBMRanker):
        return model.predict(d[feats])
    return model.decision_function(d[feats])


def select_lgb_params(tr: pd.DataFrame, feats: list[str]) -> dict:
    """Pick LightGBM params by inner leave-one-book-out on the *training* books only."""
    best, best_mrr = LGB_GRID[0], -1.0
    books = tr["book"].unique()
    for p in LGB_GRID:
        mrrs = []
        for b in books:
            itr, iva = tr[tr.book != b], tr[tr.book == b]
            m = fit_lgb(itr, feats, p)
            mrrs.append(group_metrics(iva, predict(m, iva, feats))["mrr"])
        if np.mean(mrrs) > best_mrr:
            best, best_mrr = p, float(np.mean(mrrs))
    return best


# ----------------------------------------------------------------- main
def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    d = load()
    fsets = feature_sets(d)
    books = sorted(d["book"].unique())
    print(f"{len(d)} rows, {d.site_id.nunique()} sites, books={books}")

    results: dict[str, dict] = {}   # name -> {"per_book": {...}, "pooled": {...}}
    preds = pd.DataFrame({"site_id": d["site_id"], "book": d["book"], "label": d["label"]})

    def record(name: str, score_by_book: dict[str, np.ndarray]):
        per_book = {}
        pooled = np.zeros(len(d))
        for b, s in score_by_book.items():
            m = (d["book"] == b).to_numpy()
            per_book[b] = group_metrics(d[m], s)
            pooled[m] = s
        results[name] = {"per_book": per_book, "pooled": group_metrics(d, pooled)}
        preds[name] = pooled
        p = results[name]["pooled"]
        print(f"  {name:38s} top1={p['top1']:.3f} top3={p['top3']:.3f} mrr={p['mrr']:.3f}")

    print("LOBO evaluation")
    record("random", {b: baseline_scores(d[d.book == b], "random", rng) for b in books})
    record("baseline_rank (old scorer)",
           {b: baseline_scores(d[d.book == b], "baseline_rank", rng) for b in books})

    chosen_params: dict[str, dict] = {}
    for fname, feats in fsets.items():
        record(f"logreg [{fname}]",
               {b: predict(m := fit_logreg(d[d.book != b], feats), d[d.book == b], feats)
                for b in books})
        sc, pr = {}, {}
        for b in books:
            tr, te = d[d.book != b], d[d.book == b]
            p = select_lgb_params(tr, feats)
            pr[b] = p
            sc[b] = predict(fit_lgb(tr, feats, p), te, feats)
        chosen_params[fname] = pr
        record(f"lgbm-lambdarank [{fname}]", sc)

    # ---- final models on ALL data (use the best honest model that does NOT need baseline_rank)
    candidates = {k: v for k, v in results.items()
                  if k.startswith("lgbm") and "baseline_rank" not in k}
    best_name = max(candidates, key=lambda k: results[k]["pooled"]["mrr"])
    best_fset = best_name.split("[")[1].rstrip("]")
    feats = fsets[best_fset]
    # most frequently selected params across folds
    pcount: dict[str, int] = {}
    for p in chosen_params[best_fset].values():
        pcount[json.dumps(p, sort_keys=True)] = pcount.get(json.dumps(p, sort_keys=True), 0) + 1
    final_p = json.loads(max(pcount, key=pcount.get))
    final = fit_lgb(d, feats, final_p)
    final.booster_.save_model(str(OUT / "ranker_lgbm.txt"))
    joblib.dump(fit_logreg(d, fsets["raw"]), OUT / "ranker_logreg_raw.joblib")

    imp = pd.Series(final.booster_.feature_importance("gain"), index=feats)
    imp = (imp / imp.sum()).sort_values(ascending=False)

    meta = {
        "best_model": best_name, "feature_set": best_fset, "features": feats,
        "params": final_p, "lobo_params": chosen_params,
        "results": results,
        "feature_importance_gain": imp.round(4).to_dict(),
    }
    (OUT / "metrics.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    preds.to_csv(OUT / "predictions_lobo.csv", index=False, encoding="utf-8")
    write_report(meta, results, books, imp, d)
    print(f"\nBest honest model: {best_name}\nSaved to {OUT}")


def write_report(meta, results, books, imp, d):
    def row(name, m):
        return (f"| {name} | {m['top1']:.3f} | {m['top3']:.3f} | {m['top5']:.3f} | "
                f"{m['mrr']:.3f} | {m['ndcg@5']:.3f} | {m['mean_rank']:.2f} |")
    L = ["# Ranker – kết quả đánh giá (leave-one-book-out)\n",
         f"Dữ liệu: {len(d)} dòng ứng viên, {d.site_id.nunique()} vị trí (query), "
         f"{len(books)} sách. Mỗi query có đúng 1 ứng viên đúng.\n",
         "## Kết quả gộp 4 fold\n",
         "| Mô hình | Top-1 | Top-3 | Top-5 | MRR | NDCG@5 | Mean rank |",
         "|---|---|---|---|---|---|---|"]
    for n, r in results.items():
        L.append(row(n, r["pooled"]))
    L += ["", "## Top-1 theo loại đáp án đúng (gộp)\n",
          "| Mô hình | gold=NOM | gold=VIET | gold=KEEP |", "|---|---|---|---|"]
    for n, r in results.items():
        p = r["pooled"]
        f = lambda k: "-" if p[k] is None else f"{p[k]:.3f}"
        L.append(f"| {n} | {f('top1_gold_nom')} | {f('top1_gold_viet')} | {f('top1_gold_keep')} |")
    p = next(iter(results.values()))["pooled"]
    L.append(f"\n(n gold: NOM={p['n_gold_nom']}, VIET={p['n_gold_viet']}, KEEP={p['n_gold_keep']})\n")
    L += [f"## Từng sách – {meta['best_model']}\n",
          "| Sách | n | Top-1 | Top-3 | MRR | baseline Top-1 |", "|---|---|---|---|---|---|"]
    base = results["baseline_rank (old scorer)"]["per_book"]
    for b in books:
        m = results[meta["best_model"]]["per_book"][b]
        L.append(f"| {b} | {m['n_sites']} | {m['top1']:.3f} | {m['top3']:.3f} | "
                 f"{m['mrr']:.3f} | {base[b]['top1']:.3f} |")
    L += ["", "## Feature importance (gain, mô hình cuối)\n"]
    L += [f"- {k}: {v:.3f}" for k, v in imp.head(12).items()]
    (OUT / "report.md").write_text("\n".join(L), "utf-8")


if __name__ == "__main__":
    main()
