"""Cắt từng bounding box trong thư mục vi/ của một sách ra thành ảnh riêng (mỗi box một file PNG) để kiểm tra, gán nhãn tay hoặc chạy thử
các mô hình nhận dạng.

    python scripts/crop_vi_boxes.py "data/<Tên sách>" --out img/<thư mục>

Box lấy từ <sách>/vi/Label.txt, ảnh từ <sách>/vi/*.jpg. Mỗi box được cắt giống hệt ảnh đưa vào các mô hình OCR của dự án (hình chữ nhật bao
4 điểm, chừa --pad điểm ảnh, mặc định 4). Tên file: <số trang 3 chữ số>_<số thứ tự box 2 chữ số>.png (theo thứ tự box trong Label.txt).
Ngoài ảnh còn có manifest.csv (UTF-8 BOM, mở được bằng Excel): tên file, trang, box, toạ độ, chữ hiện có trong Label.txt.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vietocr_viz as common  # noqa: E402  (dùng lại resolve_path, crop_box)


def read_all_labels(label_file: Path) -> dict[str, list[dict]]:
    labels = {}
    for line in label_file.read_text(encoding="utf-8").splitlines():
        key, sep, payload = line.partition("\t")
        if sep:
            labels[common.nfc(key.rsplit("/", 1)[-1])] = json.loads(payload)
    return labels


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Cắt từng bounding box trong vi/ của một sách thành ảnh riêng.")
    p.add_argument("book", type=Path, help="thư mục sách, ví dụ \"data/Nhât Tỉnh Ngâm, Nôm_Y\" (đặt trong dấu nháy)")
    p.add_argument("--out", type=Path, required=True, help="thư mục đầu ra, ví dụ img/nhat_tinh_ngam_vi")
    p.add_argument("--folder", default="vi", help="thư mục con chứa ảnh và Label.txt (mặc định vi)")
    p.add_argument("--pad", type=int, default=common.CROP_PAD, help=f"điểm ảnh chừa quanh box (mặc định {common.CROP_PAD}; 0 = đúng khung box)")
    args = p.parse_args(argv)

    folder = common.resolve_path(args.book) / args.folder
    if not folder.is_dir():
        raise SystemExit(f"Không thấy thư mục {folder}")
    label_file = folder / "Label.txt"
    if not label_file.is_file():
        raise SystemExit(f"Không thấy {label_file}")
    labels = read_all_labels(label_file)
    args.out.mkdir(parents=True, exist_ok=True)

    rows, pages = [], 0
    for image_file in sorted(folder.glob("*.jpg")):
        match = re.search(r"\.(\d+)\.jpg$", image_file.name)
        boxes = labels.get(common.nfc(image_file.name))
        if not match or not boxes:
            continue
        page = int(match.group(1))
        pages += 1
        with Image.open(image_file) as im:
            im = im.convert("RGB")
            for index, box in enumerate(boxes, 1):
                crop = common.crop_box(im, box["points"], args.pad)
                if crop is None:
                    continue
                name = f"{page:03d}_{index:02d}.png"
                crop.save(args.out / name)
                xs, ys = [q[0] for q in box["points"]], [q[1] for q in box["points"]]
                rows.append([name, page, index, min(xs), min(ys), max(xs), max(ys), crop.width, crop.height, box.get("transcription", "")])

    with open(args.out / "manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "page", "box", "x1", "y1", "x2", "y2", "width", "height", "label_text"])
        w.writerows(rows)
    print(f"{len(rows)} ảnh từ {pages} trang -> {args.out}")


if __name__ == "__main__":
    main()
