# Thư mục lưu trữ script đơn lẻ (`scripts/legacy/`)

Các script dưới đây từng được dùng cho một số tác vụ xử lý dữ liệu một lần, kiểm tra nhanh hoặc thử nghiệm theo từng đầu sách cụ thể trong giai đoạn gắn nhãn và hiệu chỉnh. Toàn bộ được giữ nguyên tại đây để phục vụ tra cứu lịch sử kỹ thuật; không có script nào trong thư mục này được import bởi ứng dụng chính (`app.py`, `review_local.py`) hay pipeline chuẩn (`scripts/*.py`).

## Phân nhóm chức năng

- **Tự động gán bounding box & tách dòng thử nghiệm**:
  - `auto_label_hannom_book.py`, `auto_label_hannom_book3.py`, `auto_label_hannom_spread.py`
  - `refine_all_vi_boxes.py`, `refine_page_285.py`, `apply_page_285_fix.py`
  - `test_detect_vi_lines.py`, `test_perfect_boxes.py`, `test_vi_mapping.py`
- **Kiểm tra & khảo sát dữ liệu cục bộ theo từng cuốn**:
  - `check_kieu_mismatch.py`, `check_nom_vi_mapping.py`, `check_other_pages.py`, `check_user_work.py`
  - `deep_check_space.py`, `find_lines_285.py`, `inspect_b2_missing.py`, `inspect_page_285.py`
  - `inspect_space_book.py`, `inspect_vi_label.py`, `make_grid_285.py`, `scan_all_anomalies.py`
- **Đồng bộ & xử lý dữ liệu đầu vào một lần**:
  - `fill_missing_book1.py`, `populate_vi_and_align.py`, `resort_labels.py`, `setup_vi_and_align.py`
  - `sync_b1_images.py`, `test_clean_kieu.py`, `update_b1_tags.py`
