"""Tự động phát hiện và căn chỉnh bounding box cho các trang bản chụp đôi (spread)
của bộ Kim Vân Kiều Tân Tập (KimVânKiềuTânTập_金雲翹新集 - 104 trang).

Đặc điểm bản khắc:
- Bản chụp 2 trang (spread): W ~ 4267, H ~ 4509.
- Mỗi nửa trang gồm 8 CỘT (khác với bản Tân Truyện 2 có 12 cột).
- Mỗi cột gồm:
  + Tầng trên cùng (y < 1550): Bình chú chữ Hán ngoài khung (LOẠI BỎ THEO YÊU CẦU USER).
  + Tầng giữa (y in [1550, 2650]): Câu lục 6 chữ Nôm.
  + Tầng dưới (y in [2650, 4050]): Câu bát 8 chữ Nôm.
  + Dưới đáy (y > 4100): Số trang, chú thích chân trang (LOẠI BỎ).
- Quy định đồ án (trang 7):
  + Trang trái trước (Box 01 - 16: 8 cột x 2 tầng)
  + Trang phải sau (Box 17 - 32: 8 cột x 2 tầng)
  + Tổng cộng: đúng 32 boxes cho mỗi trang chuẩn.
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


def process_half_columns_8(top_cols: list[dict], bot_cols: list[dict]) -> list[dict]:
    """Căn chỉnh 8 cột (top câu lục, bot câu bát) của một nửa trang."""
    top_8 = top_cols[:8]
    bot_8 = bot_cols[:8]

    top_y1_med = int(np.median([b["y1"] for b in top_8])) if top_8 else 1550
    top_y2_med = int(np.median([b["y2"] for b in top_8])) if top_8 else 2600
    bot_y1_med = int(np.median([b["y1"] for b in bot_8])) if bot_8 else 2720
    bot_y2_med = int(np.median([b["y2"] for b in bot_8])) if bot_8 else 4020

    for b in top_8:
        clamped = False
        if b["y2"] > top_y2_med + 50:
            b["y2"] = top_y2_med
            clamped = True
        if b["y1"] < top_y1_med - 50:
            b["y1"] = top_y1_med
        if clamped and len(b["transcription"]) > 6:
            b["transcription"] = b["transcription"][:6]

    for b in bot_8:
        clamped = False
        if b["y2"] > bot_y2_med + 50:
            b["y2"] = bot_y2_med
            clamped = True
        if b["y1"] < bot_y1_med - 50:
            b["y1"] = bot_y1_med
        if clamped and len(b["transcription"]) > 8:
            b["transcription"] = b["transcription"][:8]

    res = []
    n_cols = max(len(top_8), len(bot_8))
    for i in range(n_cols):
        if i < len(top_8):
            t = top_8[i]
            res.append({
                "transcription": t["transcription"],
                "points": [[t["x1"], t["y1"]], [t["x2"], t["y1"]], [t["x2"], t["y2"]], [t["x1"], t["y2"]]],
                "difficult": False,
            })
        if i < len(bot_8):
            b = bot_8[i]
            res.append({
                "transcription": b["transcription"],
                "points": [[b["x1"], b["y1"]], [b["x2"], b["y1"]], [b["x2"], b["y2"]], [b["x1"], b["y2"]]],
                "difficult": False,
            })
    return res


def process_spread_image_b3(img_path: Path, page_num: int) -> list[dict] | None:
    img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print(f"  [ERROR] Không đọc được ảnh: {img_path.name}")
        return None

    h, w = img.shape[:2]
    scale = 1600.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))

    tmp = eng._crop_temp_dir() / f"auto_b3_{img_path.stem}.jpg"
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

    # Trang 2: Nửa phải là bài tựa/thơ chữ Hán Phạm Quý Thích, Nửa trái là 8 câu lục bát đầu Truyện Kiều
    if page_num == 2:
        x_split = w / 2
        # Nửa trái: 8 câu lục bát Kiều
        left_raw = [b for b in raw_boxes if (b["points"][0][0] / scale) < x_split]
        top_candidates = []
        bot_candidates = []
        for b in left_raw:
            pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            ymid = (y1 + y2) / 2
            txt = b.get("text", "").strip()
            # Bỏ bình chú tầng trên
            if ymid < 1550:
                continue
            if 1550 <= ymid < 2650:
                top_candidates.append({
                    "x1": x1, "x2": x2, "y1": y1, "y2": y2,
                    "xmid": (x1 + x2) / 2, "ymid": ymid, "box_h": y2 - y1,
                    "transcription": txt[:6],
                })
            elif ymid >= 2650:
                bot_candidates.append({
                    "x1": x1, "x2": x2, "y1": y1, "y2": y2,
                    "xmid": (x1 + x2) / 2, "ymid": ymid, "box_h": y2 - y1,
                    "transcription": txt[:8],
                })
        top_candidates.sort(key=lambda b: -b["xmid"])
        bot_candidates.sort(key=lambda b: -b["xmid"])
        left_boxes = process_half_columns_8(top_candidates, bot_candidates)

        # Nửa phải: Bài tựa thơ Hán
        right_raw = [b for b in raw_boxes if (b["points"][0][0] / scale) >= x_split]
        right_boxes = []
        for b in sorted(right_raw, key=lambda bx: -bx["points"][0][0]):
            pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(ys) - min(ys) < 300:
                continue
            right_boxes.append({
                "transcription": b.get("text", "").strip(),
                "points": [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]],
                "difficult": False,
            })
        return left_boxes + right_boxes

    # Trang 104: Trang kết thúc Truyện Kiều (câu 3254 kết thúc)
    if page_num == 104:
        res = []
        for b in sorted(raw_boxes, key=lambda bx: -bx["points"][0][0]):
            pts = [[int(round(x / scale)), int(round(y / scale))] for x, y in b["points"]]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            y1, y2 = min(ys), max(ys)
            if y1 > 4200:  # Chú thích ngoài lề chân trang (Chiết, Đài, Chí...)
                continue
            res.append({
                "transcription": b.get("text", "").strip(),
                "points": [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]],
                "difficult": False,
            })
        return res

    # Trang 3 đến 103: Trang đôi chuẩn 16 cột (8 cột phải, 8 cột trái)
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
        txt = b.get("text", "").strip()

        # Loại bỏ các box lề ngoài khung:
        # 1. Tầng bình chú chữ Hán ở trên cùng (ymid < 1550)
        if ymid < 1550:
            continue
        # 2. Chú thích chân trang ở đáy (y1 > 4100 hoặc ymid > 4150)
        if y1 > 4100 or ymid > 4150:
            continue
        # 3. Box quá ngắn (chữ lẻ/rác mép)
        if box_h < 350:
            continue

        # Nếu box bao trùm cả cột (h > 2100) -> tách đôi thành câu lục và câu bát
        if box_h > 2100:
            split_y = y1 + int(round(box_h * 0.42))
            top_candidates.append({
                "x1": x1, "x2": x2, "y1": y1, "y2": split_y,
                "xmid": xmid, "ymid": (y1 + split_y) / 2,
                "box_h": split_y - y1,
                "transcription": txt[:6],
            })
            bot_candidates.append({
                "x1": x1, "x2": x2, "y1": split_y + 40, "y2": y2,
                "xmid": xmid, "ymid": (split_y + 40 + y2) / 2,
                "box_h": y2 - split_y - 40,
                "transcription": txt[6:14] if len(txt) > 6 else txt,
            })
            continue

        # Phân loại theo ymid
        if ymid < 2650:
            # Tầng lục (câu 6 chữ)
            top_candidates.append({
                "x1": x1, "x2": x2, "y1": y1, "y2": y2,
                "xmid": xmid, "ymid": ymid,
                "box_h": box_h,
                "transcription": txt[:6],
            })
        else:
            # Tầng bát (câu 8 chữ)
            bot_candidates.append({
                "x1": x1, "x2": x2, "y1": y1, "y2": y2,
                "xmid": xmid, "ymid": ymid,
                "box_h": box_h,
                "transcription": txt[:8],
            })

    # Sắp xếp từ phải sang trái (RTL: xmid giảm dần)
    top_candidates.sort(key=lambda b: -b["xmid"])
    bot_candidates.sort(key=lambda b: -b["xmid"])

    # Mỗi trang đôi gồm 8 cột phải + 8 cột trái = 16 cột
    right_top = top_candidates[:8]
    left_top = top_candidates[8:16]
    right_bot = bot_candidates[:8]
    left_bot = bot_candidates[8:16]

    right_boxes = process_half_columns_8(right_top, right_bot)
    left_boxes = process_half_columns_8(left_top, left_bot)

    # Theo Quy định đồ án (trang 7): Trang trái trước, trang phải sau
    return left_boxes + right_boxes


def run_batch(book_name: str = "KimVânKiềuTânTập_金雲翹新集", start_page: int = 1, end_page: int = 104, test_only: bool = False):
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
        pages = [2, 5, 103]
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

        print(f"[{idx}/{len(pages)}] Đang xử lý trang {p} ({fname})...", flush=True)
        start_t = time.perf_counter()
        boxes = None
        for attempt in range(1, 4):
            try:
                boxes = process_spread_image_b3(img_path, page_num=p)
                if boxes is not None:
                    break
            except Exception as e:
                print(f"  [WARN] Lỗi xử lý trang {p} (lần {attempt}/3): {e}", flush=True)
                time.sleep(3)

        if boxes is None:
            print(f"  [ERROR] Không thể xử lý trang {p} sau 3 lần thử. Bỏ qua.", flush=True)
            continue

        rel_key = f"ch/{fname}"
        labels_dict[rel_key] = boxes
        elapsed = time.perf_counter() - start_t
        print(f"  -> Xong {len(boxes)} boxes ({elapsed:.1f}s)", flush=True)
        count_success += 1

        # Ghi tạm vào Label.txt mỗi 5 trang để an toàn
        if count_success % 5 == 0 or idx == len(pages):
            with open(label_file, "w", encoding="utf-8", newline="\n") as f:
                for k in sorted(labels_dict.keys()):
                    f.write(f"{k}\t{json.dumps(labels_dict[k], ensure_ascii=False)}\n")

    # Cập nhật fileState.txt
    all_imgs = sorted([p.name for p in ch_dir.glob("*.jpg")])
    filestate_file = ch_dir / "fileState.txt"
    with open(filestate_file, "w", encoding="utf-8", newline="\n") as f:
        for img_name in all_imgs:
            rel = f"ch/{img_name}"
            status = "1" if rel in labels_dict and len(labels_dict[rel]) > 0 else "0"
            f.write(f"{rel}\t{status}\n")

    print(f"\n===== HOÀN TẤT: Đã gán nhãn {count_success}/{len(pages)} trang =====")
    print(f"Label.txt: {label_file} (Tổng {len(labels_dict)} trang có nhãn)")
    print(f"fileState.txt: {filestate_file} (Tổng {len(all_imgs)} file)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Chỉ chạy thử 3 trang (2, 5, 103)")
    parser.add_argument("--start", type=int, default=1, help="Trang bắt đầu")
    parser.add_argument("--end", type=int, default=104, help="Trang kết thúc")
    args = parser.parse_args()

    run_batch(start_page=args.start, end_page=args.end, test_only=args.test)
