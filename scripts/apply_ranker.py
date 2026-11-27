#!/usr/bin/env python3
"""Re-rank candidates with the trained ranker; originals are left untouched.

Reads  data/ranker_candidates_labels_glyph_similarity.xlsx (sheet Candidates)
       resources/ranker/predictions_lobo.csv  (out-of-fold scores, column
       'lgbm-lambdarank [raw+rel]': each book scored by a model that never saw it)
Writes data/ranker_candidates_reranked.xlsx / .csv with extra columns:
       ranker_score, ranker_rank (1 = best). `candidate_rank` (old order) is kept.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "ranker_candidates_labels_glyph_similarity.xlsx"
PRED = ROOT / "resources" / "ranker" / "predictions_lobo.csv"
MODEL_COL = "lgbm-lambdarank [raw+rel]"

x = pd.read_excel(SRC, sheet_name="Candidates")
p = pd.read_csv(PRED, usecols=["site_id", MODEL_COL])
# train_ranker sorted rows by (site_id, baseline_rank); baseline_rank == candidate_rank
p["candidate_rank"] = pd.read_csv(ROOT / "data" / "ranker_training_dataset.csv",
                                  encoding="utf-8-sig")\
    .sort_values(["site_id", "baseline_rank"], kind="stable")["baseline_rank"].to_numpy()
p = p.rename(columns={MODEL_COL: "ranker_score"})
assert not p.duplicated(["site_id", "candidate_rank"]).any()

m = x.merge(p, on=["site_id", "candidate_rank"], how="left", validate="1:1")
assert m["ranker_score"].notna().all() and len(m) == len(x)
m["_tb"] = -m["label"]  # ties: gold placed last (pessimistic)
m = m.sort_values(["site_id", "ranker_score", "_tb"], ascending=[True, False, True], kind="stable")
m["ranker_rank"] = m.groupby("site_id").cumcount() + 1
m = m.drop(columns="_tb")
# keep original block order of the workbook, ranked within each site
order = pd.Series(range(len(x)), index=x.set_index(["site_id", "candidate_rank"]).index)
m["_o"] = order.reindex(m.set_index(["site_id", "candidate_rank"]).index).to_numpy()
m = m.sort_values(["_o"]).drop(columns="_o")
m.to_csv(ROOT / "data" / "ranker_candidates_reranked.csv", index=False, encoding="utf-8-sig")
with pd.ExcelWriter(ROOT / "data" / "ranker_candidates_reranked.xlsx") as w:
    m.to_excel(w, sheet_name="Candidates", index=False)

g = m[m.label == 1]
print("rows", len(m), "sites", m.site_id.nunique())
print("top1 old", (g.candidate_rank == 1).mean().round(4), "new", (g.ranker_rank == 1).mean().round(4))
print("top5 old", (g.candidate_rank <= 5).mean().round(4), "new", (g.ranker_rank <= 5).mean().round(4))
print("MRR new", (1 / g.ranker_rank).mean().round(4))
