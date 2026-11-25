"""Sắp xếp lại thứ tự box trong file Label.txt theo quy định đồ án:
- Bản chụp 2 trang: trang trái trước, trang phải sau.
- Chữ Nôm (thư mục ch): cột từ phải sang trái, trong mỗi cột từ trên xuống dưới.
- Chữ Quốc ngữ (vi, intro, ref, toc): hàng từ trên xuống dưới, trong hàng từ trái sang phải.
"""

import argparse
import json
import shutil
from pathlib import Path
from statistics import median
from PIL import Image
from scripts import ocr_engines as eng


def resort_label_file(label_path: Path, backup: bool = True) -> tuple[int, int]:
    """Sắp xếp lại một file Label.txt. Trả về (tổng số trang, số trang đã đổi thứ tự)."""
    label_path = Path(label_path).resolve()
    if not label_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file {label_path}")

    folder = label_path.parent.name
    content = label_path.read_text(encoding="utf-8")
    lines = [l for l in content.splitlines() if l.strip()]

    new_lines = []
    reordered_count = 0

    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) != 2:
            new_lines.append(line)
            continue
        img_key, payload = parts
        try:
            boxes = json.loads(payload)
        except Exception:
            new_lines.append(line)
            continue

        if len(boxes) < 2:
            new_lines.append(line)
            continue

        # Tìm ảnh để lấy width, height
        img_path = label_path.parent / Path(img_key).name
        if not img_path.exists():
            img_path = label_path.parent.parent / img_key

        if img_path.exists():
            try:
                with Image.open(img_path) as im:
                    width, height = im.size
            except Exception:
                width = max(max(p[0] for p in b["points"]) for b in boxes) + 1
                height = max(max(p[1] for p in b["points"]) for b in boxes) + 1
        else:
            width = max(max(p[0] for p in b["points"]) for b in boxes) + 1
            height = max(max(p[1] for p in b["points"]) for b in boxes) + 1

        pages_in_image = eng.guess_spread(width, height) if folder == "ch" else 1

        boxes_with_idx = [{"orig_idx": i, "points": b["points"], "raw": b} for i, b in enumerate(boxes)]
        if folder == "ch":
            med_h = median(max(1, eng._size(bx)[1]) for bx in boxes_with_idx)
            med_w = median(max(1, eng._size(bx)[0]) for bx in boxes_with_idx)
            vertical_rtl = med_h >= med_w
        else:
            vertical_rtl = False

        if pages_in_image == 2:
            ordered = eng.order_boxes_spread(boxes_with_idx, vertical_rtl=vertical_rtl, width=width)
        else:
            ordered = eng.order_boxes(boxes_with_idx, vertical_rtl=vertical_rtl)

        new_order = [bx["orig_idx"] for bx in ordered]
        if new_order != list(range(len(boxes))):
            reordered_count += 1
            boxes = [bx["raw"] for bx in ordered]

        new_lines.append(img_key + "\t" + json.dumps(boxes, ensure_ascii=False))

    if backup and reordered_count > 0:
        bak_path = label_path.with_suffix(".txt.bak")
        shutil.copy2(label_path, bak_path)
        print(f"Đã sao lưu file gốc tại: {bak_path}")

    label_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return len(lines), reordered_count


def main():
    parser = argparse.ArgumentParser(description="Tự động sắp xếp lại thứ tự box trong Label.txt theo quy định đồ án.")
    parser.add_argument("path", nargs="?", default="data", help="Đường dẫn file Label.txt hoặc thư mục data/")
    parser.add_argument("--no-backup", action="store_true", help="Không tạo file .bak")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_file() and p.name == "Label.txt":
        files = [p]
    elif p.is_dir():
        files = list(p.rglob("Label.txt"))
    else:
        print(f"Đường dẫn không hợp lệ: {p}")
        return

    if not files:
        print("Không tìm thấy file Label.txt nào.")
        return

    for f in files:
        print(f"\nĐang xử lý: {f}")
        total, changed = resort_label_file(f, backup=not args.no_backup)
        print(f"-> Xong: Tổng cộng {total} trang, {changed} trang đã được sắp xếp lại thứ tự.")


if __name__ == "__main__":
    main()

