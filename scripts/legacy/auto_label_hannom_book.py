"""Tự động phát hiện và căn chỉnh bounding box 2 tầng (câu lục trên, câu bát dưới)
cho các trang chữ Hán Nôm của bộ Kim Vân Kiều Tân Truyện.

Mỗi trang Hán Nôm chuẩn gồm 12 cột câu thơ lục bát, chia thành 2 tầng:
- Tầng trên (câu lục 6 chữ): Y khoảng [1000, 2800]
- Tầng dưới (câu bát 8 chữ): Y khoảng [2850, 5150]
Tổng cộng đúng 24 bounding box được xếp theo thứ tự:
Cột 1 Trên -> Cột 1 Dưới -> Cột 2 Trên -> Cột 2 Dưới -> ... -> Cột 12 Trên -> Cột 12 Dưới.
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


def is_hannom_page(page_num: int) -> bool:
    """Quy luật xen kẽ của sách:
    15, 16: Hán Nôm
    17, 18: Quốc ngữ
    19, 20: Hán Nôm ...
    => (page - 15) % 4 in (0, 1) là trang Hán Nôm.
    """
    return (page_num - 15) % 4 in (0, 1)


def process_hannom_image(img_path: Path) -> list[dict] | None:
    """Chạy OCR Kim Hán Nôm và căn chỉnh thành đúng 24 box (12 cột x 2 tầng) chuẩn xác
    theo khung thơ gốc (6 chữ câu lục trên, 8 chữ câu bát dưới), loại bỏ chữ lề/chú thích ngoài khung.
    """
    img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print(f"  [ERROR] Không đọc được ảnh: {img_path.name}")
        return None

    h, w = img.shape[:2]
    scale = 1600.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))

    tmp = eng._crop_temp_dir() / f"auto_{img_path.stem}.jpg"
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

    # 1. Phân loại các box thô và lọc nhiễu lề ngoài
    full_col_candidates = []  # Box kéo dài nối liền cả 2 tầng (h > 3000)
    top_candidates = []
    bot_candidates = []

    for b in raw_boxes:
        pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        xmid = (x1 + x2) / 2
        ymid = (y1 + y2) / 2
        box_h = y2 - y1
        text = b.get("text", "").strip()

        # Bỏ lề bên ngoài (dấu mộc, khung rìa sách ngoài cùng)
        if xmid > 3900 or xmid < 120:
            continue

        item = {
            "transcription": text,
            "x1": x1,
            "x2": x2,
            "y1": y1,
            "y2": y2,
            "xmid": xmid,
            "ymid": ymid,
            "h": box_h,
        }

        # Nếu box nối liền cả 2 tầng (như trên trang 115)
        if box_h > 3000 and y1 < 1800 and y2 > 4200:
            full_col_candidates.append(item)
        # Tầng trên: câu lục (y1 ~ 1000-1400, h >= 1000 và <= 2200)
        elif y1 < 1600 and y2 > 2200 and 1000 <= box_h <= 2200:
            top_candidates.append(item)
        # Tầng dưới: câu bát (y1 ~ 2600-3300, h >= 1400)
        elif 2600 <= y1 <= 3300 and box_h >= 1400:
            bot_candidates.append(item)

    # 2. Tính median chuẩn của khung thơ (tầng trên và tầng dưới) từ các cột đơn lẻ
    top_y1_med = int(np.median([b["y1"] for b in top_candidates])) if top_candidates else 1150
    top_y2_med = int(np.median([b["y2"] for b in top_candidates])) if top_candidates else 2650
    bot_y1_med = int(np.median([b["y1"] for b in bot_candidates])) if bot_candidates else 2950
    bot_y2_med = int(np.median([b["y2"] for b in bot_candidates])) if bot_candidates else 4980

    # 3. Tách các box kéo dài nối liền cả 2 tầng
    for item in full_col_candidates:
        full_text = item["transcription"]
        top_txt = full_text[:6]
        bot_txt = full_text[6:]

        top_candidates.append({
            "transcription": top_txt,
            "x1": item["x1"],
            "x2": item["x2"],
            "y1": item["y1"],
            "y2": top_y2_med,
            "xmid": item["xmid"],
            "ymid": (item["y1"] + top_y2_med) / 2,
            "h": top_y2_med - item["y1"],
        })
        bot_candidates.append({
            "transcription": bot_txt,
            "x1": item["x1"],
            "x2": item["x2"],
            "y1": bot_y1_med,
            "y2": min(item["y2"], bot_y2_med),
            "xmid": item["xmid"],
            "ymid": (bot_y1_med + bot_y2_med) / 2,
            "h": bot_y2_med - bot_y1_med,
        })

    # Cập nhật lại median sau khi đã gom đủ các cột
    if top_candidates:
        top_y1_med = int(np.median([b["y1"] for b in top_candidates]))
        top_y2_med = int(np.median([b["y2"] for b in top_candidates]))
    if bot_candidates:
        bot_y1_med = int(np.median([b["y1"] for b in bot_candidates]))
        bot_y2_med = int(np.median([b["y2"] for b in bot_candidates]))

    # 4. Sắp xếp RTL (từ phải sang trái) và lấy tối đa 12 cột chính
    top_candidates.sort(key=lambda b: -b["xmid"])
    bot_candidates.sort(key=lambda b: -b["xmid"])

    top_12 = top_candidates[:12]
    bot_12 = bot_candidates[:12]

    # 5. Căn chỉnh khung (clamping) và cắt bỏ chữ thừa ngoài khung thơ
    for b in top_12:
        clamped = False
        if b["y2"] > top_y2_med + 50:
            b["y2"] = top_y2_med
            clamped = True
        if b["y1"] < top_y1_med - 50:
            b["y1"] = top_y1_med
        # Nếu bị tràn khung và có nhiều hơn 6 chữ, cắt về đúng 6 chữ câu lục
        if clamped and len(b["transcription"]) > 6:
            b["transcription"] = b["transcription"][:6]

    for b in bot_12:
        clamped = False
        if b["y2"] > bot_y2_med + 50:
            b["y2"] = bot_y2_med
            clamped = True
        if b["y1"] < bot_y1_med - 50:
            b["y1"] = bot_y1_med
        # Nếu bị tràn khung và có nhiều hơn 8 chữ, cắt về đúng 8 chữ câu bát
        if clamped and len(b["transcription"]) > 8:
            b["transcription"] = b["transcription"][:8]

    # 6. Ghép xen kẽ theo thứ tự chuẩn: Cột 1 Trên, Cột 1 Dưới, Cột 2 Trên, Cột 2 Dưới...
    final_boxes = []
    n_cols = max(len(top_12), len(bot_12))
    for i in range(n_cols):
        if i < len(top_12):
            t = top_12[i]
            final_boxes.append({
                "transcription": t["transcription"],
                "points": [[t["x1"], t["y1"]], [t["x2"], t["y1"]], [t["x2"], t["y2"]], [t["x1"], t["y2"]]],
                "difficult": False,
            })
        if i < len(bot_12):
            b = bot_12[i]
            final_boxes.append({
                "transcription": b["transcription"],
                "points": [[b["x1"], b["y1"]], [b["x2"], b["y1"]], [b["x2"], b["y2"]], [b["x1"], b["y2"]]],
                "difficult": False,
            })

    return final_boxes


def run_batch(book_name: str, start_page: int = 108, end_page: int = 302, test_only: bool = False):
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

    # Tìm các trang Hán Nôm trong dải trang yêu cầu
    hannom_pages = [p for p in range(start_page, end_page + 1) if is_hannom_page(p)]
    print(f"Tổng số trang Hán Nôm cần xử lý từ trang {start_page} đến {end_page}: {len(hannom_pages)} trang")
    if test_only:
        hannom_pages = hannom_pages[:3]
        print(f"--> [Chế độ thử nghiệm] Chỉ chạy thử 3 trang đầu: {hannom_pages}")

    # Backup file Label.txt trước khi ghi
    if label_file.exists():
        bak_file = ch_dir / "Label.txt.bak"
        shutil.copyfile(label_file, bak_file)
        print(f"Đã sao lưu {label_file.name} -> {bak_file.name}")

    count_success = 0
    for idx, p in enumerate(hannom_pages, 1):
        fname = f"{book_name}.{p:03d}.jpg"
        img_path = ch_dir / fname
        if not img_path.exists():
            print(f"[{idx}/{len(hannom_pages)}] Bỏ qua (không có file): {fname}")
            continue

        print(f"[{idx}/{len(hannom_pages)}] Đang xử lý trang {p} ({fname})...")
        start_t = time.perf_counter()
        boxes = None
        for attempt in range(1, 4):
            try:
                boxes = process_hannom_image(img_path)
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

        # Ghi tạm vào Label.txt sau mỗi trang để tránh mất tiến độ nếu gián đoạn
        with open(label_file, "w", encoding="utf-8") as f:
            for k in sorted(labels_dict.keys()):
                f.write(f"{k}\t{json.dumps(labels_dict[k], ensure_ascii=False)}\n")

        # Cập nhật fileState.txt cho PPOCRLabel
        filestate_file = ch_dir / "fileState.txt"
        if filestate_file.exists():
            fs_text = filestate_file.read_text(encoding="utf-8")
            if rel_key not in fs_text:
                with open(filestate_file, "a", encoding="utf-8") as fs:
                    fs.write(f"{rel_key}\t0\n")

    print(f"\n Hoàn tất! Đã xử lý thành công {count_success} trang và cập nhật vào Label.txt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", default="KimVânKiềuTânTruyện_金雲翹新傳")
    parser.add_argument("--start", type=int, default=108)
    parser.add_argument("--end", type=int, default=302)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    run_batch(args.book, args.start, args.end, test_only=args.test)
