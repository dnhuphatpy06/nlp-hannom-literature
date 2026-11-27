#!/usr/bin/env python3
"""Export a row-per-candidate numeric reranker dataset from the labeled workbook."""
from __future__ import annotations

import csv
import math
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "ranker_candidates_labels_glyph_similarity.xlsx"
OUTPUT = ROOT / "data" / "ranker_training_dataset.csv"


def number(value, default=0.0):
    return default if value is None else float(value)


def main() -> None:
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    sheet = workbook["Candidates"]
    headers = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
    idx = {name: headers.index(name) for name in headers}
    columns = [
        "id", "site_id", "label", "baseline_rank",
        "is_nom", "is_viet", "is_keep",
        "has_nom_visual_rank", "nom_visual_neglog_rank",
        "has_nom_glyph_rank", "nom_glyph_neglog_rank",
        "viet_distance", "nom_new_semantic_delta_logp",
        "viet_new_semantic_delta_logp",
    ]

    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        row_count = positive_count = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            kind = row[idx["candidate_type"]]
            visual_rank_feature = row[idx["nom_visual_neglog_rank"]]
            glyph_rank_mean = row[idx["glyph_rank_mean"]]
            has_visual_rank = kind == "NOM" and visual_rank_feature is not None
            has_glyph_rank = kind == "NOM" and glyph_rank_mean is not None
            out = {
                "id": row[idx["id"]],
                "site_id": row[idx["site_id"]],
                "label": int(row[idx["label"]]),
                "baseline_rank": int(row[idx["candidate_rank"]]),
                "is_nom": int(kind == "NOM"),
                "is_viet": int(kind == "VIET"),
                "is_keep": int(kind == "KEEP"),
                "has_nom_visual_rank": int(has_visual_rank),
                # Missing values are zero-imputed and identified by the flag.
                "nom_visual_neglog_rank": number(visual_rank_feature),
                "has_nom_glyph_rank": int(has_glyph_rank),
                "nom_glyph_neglog_rank": (-math.log(float(glyph_rank_mean) + 1)
                                           if has_glyph_rank else 0.0),
                "viet_distance": number(row[idx["viet_distance"]]),
                "nom_new_semantic_delta_logp": number(
                    row[idx["nom_new_semantic_delta_logp"]]),
                "viet_new_semantic_delta_logp": number(
                    row[idx["viet_new_semantic_delta_logp"]]),
            }
            writer.writerow(out)
            row_count += 1
            positive_count += out["label"]

    print(f"Wrote {OUTPUT}")
    print(f"Rows: {row_count}; positive labels: {positive_count}; features: {len(columns) - 4}")


if __name__ == "__main__":
    main()
