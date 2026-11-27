# Ranker – kết quả đánh giá (leave-one-book-out)

Dữ liệu: 47228 dòng ứng viên, 2283 vị trí (query), 4 sách. Mỗi query có đúng 1 ứng viên đúng.

## Kết quả gộp 4 fold

| Mô hình | Top-1 | Top-3 | Top-5 | MRR | NDCG@5 | Mean rank |
|---|---|---|---|---|---|---|
| random | 0.057 | 0.171 | 0.276 | 0.191 | 0.165 | 11.06 |
| baseline_rank (old scorer) | 0.530 | 0.710 | 0.783 | 0.648 | 0.665 | 3.88 |
| logreg [raw] | 0.559 | 0.841 | 0.915 | 0.707 | 0.752 | 2.50 |
| lgbm-lambdarank [raw] | 0.614 | 0.849 | 0.917 | 0.743 | 0.780 | 2.34 |
| logreg [raw+rel] | 0.567 | 0.827 | 0.910 | 0.709 | 0.752 | 2.53 |
| lgbm-lambdarank [raw+rel] | 0.631 | 0.862 | 0.921 | 0.756 | 0.791 | 2.24 |
| logreg [raw+rel+baseline_rank] | 0.569 | 0.826 | 0.909 | 0.710 | 0.752 | 2.53 |
| lgbm-lambdarank [raw+rel+baseline_rank] | 0.662 | 0.863 | 0.923 | 0.774 | 0.805 | 2.19 |

## Top-1 theo loại đáp án đúng (gộp)

| Mô hình | gold=NOM | gold=VIET | gold=KEEP |
|---|---|---|---|
| random | 0.049 | 0.058 | 0.071 |
| baseline_rank (old scorer) | 0.372 | 0.703 | 0.191 |
| logreg [raw] | 0.327 | 0.705 | 0.505 |
| lgbm-lambdarank [raw] | 0.454 | 0.738 | 0.479 |
| logreg [raw+rel] | 0.360 | 0.707 | 0.476 |
| lgbm-lambdarank [raw+rel] | 0.538 | 0.727 | 0.460 |
| logreg [raw+rel+baseline_rank] | 0.332 | 0.725 | 0.482 |
| lgbm-lambdarank [raw+rel+baseline_rank] | 0.577 | 0.765 | 0.443 |

(n gold: NOM=716, VIET=1258, KEEP=309)

## Từng sách – lgbm-lambdarank [raw+rel]

| Sách | n | Top-1 | Top-3 | MRR | baseline Top-1 |
|---|---|---|---|---|---|
| Trinh Thư Truyên, Nôm | 607 | 0.595 | 0.868 | 0.737 | 0.527 |
| Truyên Vua Quang Trung, Nôm | 477 | 0.623 | 0.816 | 0.735 | 0.423 |
| Tư Linh Truyên, Nôm | 587 | 0.651 | 0.867 | 0.771 | 0.618 |
| truyen vua Le Thai To digcoll_10996 | 612 | 0.655 | 0.889 | 0.777 | 0.529 |

## Feature importance (gain, mô hình cuối)

- nom_glyph_neglog_rank: 0.320
- viet_distance: 0.192
- nom_glyph_neglog_rank__rank: 0.078
- has_nom_visual_rank: 0.054
- is_viet: 0.051
- viet_distance__gap: 0.044
- score_semantic__rank: 0.037
- viet_new_semantic_delta_logp__rank: 0.030
- viet_distance__rank: 0.028
- score_semantic__gap: 0.028
- viet_new_semantic_delta_logp__gap: 0.026
- score_semantic: 0.017