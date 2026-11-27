# Báo cáo tinh chỉnh trọng số (2026-11-27 17:07:52)

- Nhãn dùng được: **2298** cặp đã duyệt, theo sách: Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳 1; KimVânKiềuTânTruyện_金雲翹新傳 14; Trinh Thư Truyên, Nôm_Y 607; truyen vua Le Thai To digcoll_10996_Y 612; Truyên Vua Quang Trung, Nôm_Y 477; Tư Linh Truyên, Nôm_Y 587
- Objective dò: `nll`, 1500 lần thử mỗi lần dò, seed 0, mất 30.5s
- **Bộ trọng số được chọn: ĐÃ DÒ** (chọn tự động: bộ đã dò chỉ được dùng khi thắng mặc định trên dữ liệu held-out)

## 1. Độ chính xác (held-out, leave-one-book-out)

Mỗi sách được giữ lại làm tập kiểm tra; trọng số dò trên các sách còn lại. Đây là ước lượng công bằng cho sách mới.

|  | n | top-1 | top-3 | top-5 | MRR | NLL | ECE | Đúng loại | Macro-F1 loại | Phát hiện cần sửa P / R | top-1 khi thật là KEEP / NOM / QN | top-1 trong loại NOM / QN |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Mặc định | 2283 | 53.0% | 71.0% | 78.3% | 0.648 | 1.911 | 0.050 | 72.3% | 0.589 | 87.6% / 89.3% | 19.2% / 37.1% / 70.3% | 44.6% / 81.7% |
| Đã dò | 2283 | 59.0% | 77.9% | 84.5% | 0.706 | 1.498 | 0.028 | 68.1% | 0.567 | 91.9% / 73.9% | 58.1% / 27.3% / 77.3% | 43.8% / 82.2% |

Khoảng tin cậy 95% (bootstrap) của top-1: mặc định 50.9% – 55.0%; đã dò 57.0% – 61.0%.

### Từng sách giữ lại

|  | n | top-1 | top-3 | top-5 | MRR | NLL | ECE | Đúng loại | Macro-F1 loại | Phát hiện cần sửa P / R | top-1 khi thật là KEEP / NOM / QN | top-1 trong loại NOM / QN |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Trinh Thư Truyên, Nôm_Y (mặc định) | 607 | 52.7% | 74.3% | 81.7% | 0.657 | 1.824 | 0.063 | 74.3% | 0.634 | 89.1% / 91.8% | 28.0% / 43.2% / 64.7% | 51.3% / 79.1% |
| Trinh Thư Truyên, Nôm_Y (đã dò) | 607 | 60.5% | 81.7% | 88.6% | 0.727 | 1.402 | 0.073 | 71.0% | 0.616 | 94.7% / 74.7% | 73.2% / 32.7% / 74.2% | 51.3% / 79.1% |
| truyen vua Le Thai To digcoll_10996_Y (mặc định) | 612 | 52.9% | 69.4% | 78.1% | 0.645 | 1.867 | 0.063 | 70.8% | 0.581 | 85.3% / 89.7% | 15.8% / 39.2% / 75.3% | 48.2% / 83.7% |
| truyen vua Le Thai To digcoll_10996_Y (đã dò) | 612 | 57.4% | 77.3% | 84.0% | 0.694 | 1.503 | 0.063 | 65.4% | 0.570 | 89.8% / 75.0% | 53.7% / 28.4% / 80.3% | 45.9% / 84.1% |
| Truyên Vua Quang Trung, Nôm_Y (mặc định) | 477 | 42.3% | 58.9% | 66.9% | 0.551 | 2.414 | 0.133 | 64.6% | 0.550 | 80.4% / 82.7% | 13.3% / 24.3% / 70.9% | 26.5% / 84.5% |
| Truyên Vua Quang Trung, Nôm_Y (đã dò) | 477 | 48.2% | 63.5% | 72.3% | 0.599 | 2.026 | 0.122 | 54.1% | 0.469 | 80.8% / 60.7% | 37.8% / 16.6% / 80.6% | 26.5% / 85.0% |
| Tư Linh Truyên, Nôm_Y (mặc định) | 587 | 61.8% | 78.9% | 84.3% | 0.721 | 1.638 | 0.095 | 78.2% | 0.578 | 94.0% / 91.0% | 22.0% / 42.6% / 70.8% | 54.8% / 81.0% |
| Tư Linh Truyên, Nôm_Y (đã dò) | 587 | 68.1% | 86.2% | 90.5% | 0.782 | 1.163 | 0.105 | 79.4% | 0.614 | 98.4% / 81.3% | 82.9% / 33.0% / 76.1% | 53.9% / 81.9% |

## 2. Loại sửa (bộ đã chọn, held-out)

Hàng = loại người duyệt chọn, cột = loại top-1 dự đoán.

| thật \ đoán | KEEP | NOM | QN | recall |
|---|---|---|---|---|
| KEEP | 179 | 37 | 92 | 58.1% |
| NOM | 421 | 219 | 77 | 30.5% |
| QN | 95 | 6 | 1157 | 92.0% |
| precision | 25.8% | 83.6% | 87.3% | |

## 3. Tự động chọn theo ngưỡng confidence (held-out)

Nếu pipeline tự áp dụng ứng viên top-1 khi confidence ≥ τ, người duyệt chỉ cần xem phần còn lại.

| τ | cặp tự chọn | coverage | precision |
|---|---|---|---|
| 0.5 | 1273 | 55.8% | 77.8% |
| 0.6 | 977 | 42.8% | 85.0% |
| 0.7 | 754 | 33.0% | 89.7% |
| 0.8 | 589 | 25.8% | 92.9% |
| 0.9 | 370 | 16.2% | 95.7% |
| 0.95 | 203 | 8.9% | 98.0% |
| 0.98 | 53 | 2.3% | 100.0% |
| 0.99 | 21 | 0.9% | 100.0% |

**Ngưỡng gợi ý** cho precision ≥ 95.0%: τ = **0.8761**, tự chọn 18.8% số cặp (429) với precision 95.1%.

## 4. Trọng số

| tham số | mặc định | đã dò | đang dùng |
|---|---|---|---|
| w_vis | 1.5 | 0.4145 | 0.4145 |
| w_nlm | 0.5 | 0.1257 | 0.1257 |
| novis_rank | 20 | 29 | 29 |
| w_ortho | 3.0 | 2.3643 | 2.3643 |
| w_qlm | 0.3 | 0.1836 | 0.1836 |
| nom_prior | 0.0 | 0.0 | 0.0 |
| qn_prior | 0.0 | 1.8013 | 1.8013 |
| keep_score | -4.0 | -3.2623 | -3.2623 |
| max_ortho | 1.2 | 1.2 | 1.2 |
| lm_topk | 20 | 20 | 20 |
| bias_variant | 0.0 | 0.3125 | 0.3125 |
| bias_regional | 0.0 | -2.3937 | -2.3937 |
| bias_relaxed | 0.0 | -0.9523 | -0.9523 |
| bias_reading | 0.0 | -1.229 | -1.229 |
| bias_lm | 0.0 | -3.9688 | -3.9688 |
| cap_only | True | False | False |

`lm_topk`, `max_ortho`, `nom_prior` không được dò (xem tuning.py). Bốn tham số `bias_*` và `cap_only` cho phép ứng viên dị thể / dạng vùng / nới lỏng / âm còn lại / do LM vượt ứng viên sẵn có nếu nhãn cho thấy nên như vậy; mặc định 0 và `cap_only=True` giữ hành vi cũ.

## 5. Lưu ý

- Người duyệt nhìn danh sách đã xếp theo trọng số mặc định nên nhãn thiên về ứng viên đứng đầu (presentation bias): top-1 của mặc định có thể cao hơn thực tế và lợi ích của trọng số mới có thể bị đánh giá thấp.
- Chỉ cặp đã duyệt có nhãn; nhãn ngoài danh sách ứng viên và cặp không còn trong cache bị bỏ.
- `confidence` chỉ đáng tin sau khi dò theo `nll` (ECE thấp); dò theo `mrr`/`top1` chỉ tối ưu thứ hạng.
