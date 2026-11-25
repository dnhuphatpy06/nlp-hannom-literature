"""Tự động phát hiện và căn chỉnh bounding box cho các trang bản chụp đôi (spread)
của bộ Kim Vân Kiều Tân Truyện 2 (KimVânKiềuTânTruyện_金雲翹新傳_2).

Quy định đồ án (trang 7):
- Bản chụp 2 trang: trang trái trước, trang phải sau.
- Trong mỗi trang: cột từ phải sang trái (RTL), trong mỗi cột từ trên xuống dưới (câu lục 6 chữ trên, câu bát 8 chữ dưới).
- Mỗi trang đôi chuẩn gồm 48 boxes:
  + Box 01 - 24: Trang trái (12 cột x 2 tầng)
  + Box 25 - 48: Trang phải (12 cột x 2 tầng)
- Loại bỏ hoàn toàn chú thích chân trang, số trang, chữ lề ngoài khung.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import ocr_engines as eng


def process_half_columns(top_cols: list[dict], bot_cols: list[dict]) -> list[dict]:
    """Căn chỉnh 12 cột (top câu lục, bot câu bát) của một nửa trang."""
    top_12 = top_cols[:12]
    bot_12 = bot_cols[:12]

    top_y1_med = int(np.median([b["y1"] for b in top_12])) if top_12 else 800
    top_y2_med = int(np.median([b["y2"] for b in top_12])) if top_12 else 1950
    bot_y1_med = int(np.median([b["y1"] for b in bot_12])) if bot_12 else 2150
    bot_y2_med = int(np.median([b["y2"] for b in bot_12])) if bot_12 else 3750

    for b in top_12:
        clamped = False
        if b["y2"] > top_y2_med + 50:
            b["y2"] = top_y2_med
            clamped = True
        if b["y1"] < top_y1_med - 50:
            b["y1"] = top_y1_med
        if clamped and len(b["transcription"]) > 6:
            b["transcription"] = b["transcription"][:6]

    for b in bot_12:
        clamped = False
        if b["y2"] > bot_y2_med + 50:
            b["y2"] = bot_y2_med
            clamped = True
        if b["y1"] < bot_y1_med - 50:
            b["y1"] = bot_y1_med
        if clamped and len(b["transcription"]) > 8:
            b["transcription"] = b["transcription"][:8]

    res = []
    n_cols = max(len(top_12), len(bot_12))
    for i in range(n_cols):
        if i < len(top_12):
            t = top_12[i]
            res.append({
                "transcription": t["transcription"],
                "points": [[t["x1"], t["y1"]], [t["x2"], t["y1"]], [t["x2"], t["y2"]], [t["x1"], t["y2"]]],
                "difficult": False,
            })
        if i < len(bot_12):
            b = bot_12[i]
            res.append({
                "transcription": b["transcription"],
                "points": [[b["x1"], b["y1"]], [b["x2"], b["y1"]], [b["x2"], b["y2"]], [b["x1"], b["y2"]]],
                "difficult": False,
            })
    return res


def process_spread_image(img_path: Path, page_num: int) -> list[dict] | None:
    img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print(f"  [ERROR] Không đọc được ảnh: {img_path.name}")
        return None

    h, w = img.shape[:2]
    scale = 1600.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))

    tmp = eng._crop_temp_dir() / f"auto_b2_{img_path.stem}.jpg"
    cv2.imencode(".jpg", small)[1].tofile(str(tmp))
    try:
        raw_boxes = eng.run_kimhannom(
            tmp,
            uploads_file=eng._crop_temp_dir() / "crop_uploads.json",
            ocr_id=1,
            lang_type=2,
            reading_direction=1,
        )
    finally:
        tmp.unlink(missing_ok=True)

    # Trang 1: Trang bìa / tựa sách -> giữ các box nhận diện được sắp xếp RTL
    if page_num == 1:
        res = []
        for b in sorted(raw_boxes, key=lambda bx: -bx["points"][0][0]):
            pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            res.append({
                "transcription": b.get("text", "").strip(),
                "points": [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]],
                "difficult": False,
            })
        return res

    # Phân loại box thô
    full_col_candidates = []
    top_candidates = []
    bot_candidates = []

    for b in raw_boxes:
        pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        box_h = y2 - y1
        xmid = (x1 + x2) / 2
        ymid = (y1 + y2) / 2
        text = b.get("text", "").strip()

        # Bỏ chữ lề quá ngắn
        if box_h < 600:
            continue
        # Bỏ mép ngoài cùng
        if xmid > w - 80 or xmid < 80:
            continue

        item = {
            "transcription": text,
            "x1": x1, "x2": x2, "y1": y1, "y2": y2,
            "xmid": xmid, "ymid": ymid, "h": box_h,
        }

        # Box nối liền cả 2 tầng
        if box_h > 2600 and y1 < 1500 and y2 > 3000:
            full_col_candidates.append(item)
        elif ymid < 2050:
            top_candidates.append(item)
        else:
            bot_candidates.append(item)

    # Ước lượng vị trí phân tầng từ các box hiện có
    top_y2_est = int(np.median([b["y2"] for b in top_candidates])) if top_candidates else 1950
    bot_y1_est = int(np.median([b["y1"] for b in bot_candidates])) if bot_candidates else 2150
    bot_y2_est = int(np.median([b["y2"] for b in bot_candidates])) if bot_candidates else 3750

    # Tách box dính 2 tầng
    for item in full_col_candidates:
        full_text = item["transcription"]
        top_candidates.append({
            "transcription": full_text[:6],
            "x1": item["x1"], "x2": item["x2"],
            "y1": item["y1"], "y2": top_y2_est,
            "xmid": item["xmid"], "ymid": (item["y1"] + top_y2_est) / 2,
            "h": top_y2_est - item["y1"],
        })
        bot_candidates.append({
            "transcription": full_text[6:],
            "x1": item["x1"], "x2": item["x2"],
            "y1": bot_y1_est, "y2": min(item["y2"], bot_y2_est),
            "xmid": item["xmid"], "ymid": (bot_y1_est + bot_y2_est) / 2,
            "h": bot_y2_est - bot_y1_est,
        })

    # Sắp xếp RTL
    top_candidates.sort(key=lambda b: -b["xmid"])
    bot_candidates.sort(key=lambda b: -b["xmid"])

    # Trang 2: Nửa trái là 12 cột lục bát đầu tiên của Kiều, nửa phải là bài thơ chữ Hán
    if page_num == 2:
        x_split = w / 2
        # Trang trái (x < x_split)
        left_top = [b for b in top_candidates if b["xmid"] < x_split]
        left_bot = [b for b in bot_candidates if b["xmid"] < x_split]
        left_boxes = process_half_columns(left_top, left_bot)

        # Trang phải (x >= x_split): các dòng thơ chữ Hán
        right_raw = [b for b in raw_boxes if (b["points"][0][0] / scale) >= x_split]
        right_boxes = []
        for b in sorted(right_raw, key=lambda bx: -bx["points"][0][0]):
            pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(ys) - min(ys) < 400:
                continue
            right_boxes.append({
                "transcription": b.get("text", "").strip(),
                "points": [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]],
                "difficult": False,
            })
        # Trang trái trước, trang phải sau
        return left_boxes + right_boxes

    # Trang 70: Trang kết thúc truyện Kiều
    if page_num == 70:
        x_split = w / 2
        left_top = [b for b in top_candidates if b["xmid"] < x_split]
        left_bot = [b for b in bot_candidates if b["xmid"] < x_split]
        right_top = [b for b in top_candidates if b["xmid"] >= x_split]
        right_bot = [b for b in bot_candidates if b["xmid"] >= x_split]
        left_boxes = process_half_columns(left_top, left_bot)
        right_boxes = process_half_columns(right_top, right_bot)
        return left_boxes + right_boxes

    # Các trang 3 đến 69: Trang đôi lục bát chuẩn (24 box phải + 24 box trái = 48 boxes)
    # 12 cột đầu là trang phải, 12 cột sau là trang trái
    right_top = top_candidates[:12]
    left_top = top_candidates[12:24]
    right_bot = bot_candidates[:12]
    left_bot = bot_candidates[12:24]

    right_boxes = process_half_columns(right_top, right_bot)
    left_boxes = process_half_columns(left_top, left_bot)

    # Theo Quy định đồ án (trang 7): Trang trái trước, trang phải sau
    return left_boxes + right_boxes


def run_batch(book_name: str = "KimVânKiềuTânTruyện_金雲翹新傳_2", start_page: int = 1, end_page: int = 70, test_only: bool = False):
    book_dir = PROJECT_ROOT / "data" / book_name
    ch_dir = book_dir / "ch"
    if not ch_dir.is_dir():
        print(f"Không tìm thấy thư mục: {ch_dir}")
        return

    label_file = ch_dir / "Label.txt"
    labels_dict = {}
    if label_file.exists():
        for line in label_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                k, _, v = line.partition("\t")
                labels_dict[k] = json.loads(v)

    pages = list(range(start_page, end_page + 1))
    print(f"Tổng số trang cần xử lý từ trang {start_page} đến {end_page}: {len(pages)} trang")
    if test_only:
        pages = [2, 3, 4]
        print(f"--> [Chế độ thử nghiệm] Chỉ chạy thử 3 trang: {pages}")

    if label_file.exists():
        bak_file = ch_dir / "Label.txt.bak"
        shutil.copyfile(label_file, bak_file)
        print(f"Đã sao lưu {label_file.name} -> {bak_file.name}")

    count_success = 0
    for idx, p in enumerate(pages, 1):
        fname = f"{book_name}.{p:03d}.jpg"
        img_path = ch_dir / fname
        if not img_path.exists():
            print(f"[{idx}/{len(pages)}] Bỏ qua (không có file): {fname}")
            continue

        print(f"[{idx}/{len(pages)}] Đang xử lý trang {p} ({fname})...")
        start_t = time.perf_counter()
        boxes = None
        for attempt in range(1, 4):
            try:
                boxes = process_spread_image(img_path, page_num=p)
                if boxes is not None:
                    break
            except Exception as e:
                print(f"  [WARN] Lỗi xử lý trang {p} (lần {attempt}/3): {e}")
                time.sleep(3)

        if boxes is None:
            print(f"  [ERROR] Không thể xử lý trang {p} sau 3 lần thử. Bỏ qua.")
            continue

        rel_key = f"ch/{fname}"
        labels_dict[rel_key] = boxes
        elapsed = time.perf_counter() - start_t
        print(f"  -> Xong {len(boxes)} boxes ({elapsed:.1f}s)")
        count_success += 1

        with open(label_file, "w", encoding="utf-8") as f:
            for k in sorted(labels_dict.keys()):
                f.write(f"{k}\t{json.dumps(labels_dict[k], ensure_ascii=False)}\n")

        filestate_file = ch_dir / "fileState.txt"
        fs_text = filestate_file.read_text(encoding="utf-8") if filestate_file.exists() else ""
        if rel_key not in fs_text:
            with open(filestate_file, "a", encoding="utf-8") as fs:
                fs.write(f"{rel_key}\t0\n")

    print(f"\n Hoàn tất! Đã xử lý thành công {count_success} trang và cập nhật vào Label.txt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", default="KimVânKiềuTânTruyện_金雲翹新傳_2")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=70)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    run_batch(args.book, args.start, args.end, test_only=args.test)
