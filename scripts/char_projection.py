"""Đếm số chữ (bounding box từng chữ) trong ẢNH MỘT HÀNG chữ ngang bằng xử lý ảnh cổ điển, cùng nguyên lý với row_projection_viz
(chiếu mực lên một trục, cắt tại khe trống), nhưng chiếu theo chiều DỌC: mỗi cột pixel đếm số pixel mực, các đoạn cột liên tiếp có mực là một chữ.

    python scripts/char_projection.py img/tran_vong_tuong_si_van/ch/005_02.png
    python scripts/char_projection.py img/tran_vong_tuong_si_van/ch            # cả thư mục, in số chữ từng ảnh
    python scripts/char_projection.py img/.../ch --save-vis out_dir            # lưu ảnh vẽ box để kiểm tra

Các bước (ngưỡng tính theo chiều cao ảnh h, không phụ thuộc kích thước tuyệt đối):
    1. Thang xám = kênh sáng nhất của BGR: mực đen vẫn tối, còn chú thích mực ĐỎ (phiên âm bên lề chữ Hán) thành sáng; pixel đỏ còn sót
       (R vượt G và B hơn RED_DIFF) bị loại khỏi mặt nạ mực, trừ dòng mà mực đỏ chiếm hơn nửa (dòng viết chính bằng mực đỏ).
    2. ink_mask của row_projection_viz: chuẩn hoá nền, Otsu, trừ đường kẻ ô.
    3. Chiếu dọc: cột có >= PROFILE_RATIO x h pixel mực là cột có chữ; các đoạn liên tiếp là ứng viên chữ.
    4. Nối đoạn cách nhau <= MERGE_GAP x h (nét rời trong một chữ).
    5. Gộp nửa chữ: chữ có hai phần rời (北, 汝, 𠓨...) vẫn ra hai đoạn hẹp liền nhau; gộp hai đoạn kề khi có đoạn hẹp
       (< NARROW x độ rộng chữ trung vị), hai đoạn cách nhau <= SPLIT_GAP x h và đoạn gộp không rộng quá WIDE x h.
    6. Bỏ dấu câu / vết bẩn: đoạn vừa hẹp (< SMALL x h) vừa thấp (mực cao < SMALL x h) không phải chữ.
Kết quả là số đoạn còn lại. Giả định hàng thẳng, nằm ngang; chữ dọc (một cột) không dùng được.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from row_projection_viz import ink_mask, merge_close, runs  # noqa: E402

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PROFILE_RATIO = 0.05   # cột có chữ khi mực >= tỉ lệ này x chiều cao ảnh
MERGE_GAP = 0.18       # nối hai đoạn cách nhau <= tỉ lệ này x chiều cao
MIN_WIDTH = 0.15       # đoạn rộng < tỉ lệ này x chiều cao bị bỏ (nhiễu)
NARROW = 0.55          # đoạn "hẹp" = rộng < tỉ lệ này x độ rộng chữ trung vị
SPLIT_GAP = 0.30       # chỉ gộp nửa chữ nếu hai đoạn cách nhau <= tỉ lệ này x chiều cao
WIDE = 1.05            # đoạn gộp không được rộng hơn tỉ lệ này x chiều cao ảnh (chữ Hán gần vuông)
SMALL = 0.40           # đoạn nhỏ hơn tỉ lệ này x chiều cao cả hai chiều là dấu câu
RED_DIFF = 35          # pixel là mực đỏ khi R lớn hơn cả G và B ít nhất ngần này (0-255)
RED_MAIN = 0.5         # bỏ mực đỏ chỉ khi phần còn lại >= tỉ lệ này của tổng mực
BG_KERNEL = 25         # như row_projection_viz


def read_image(path) -> np.ndarray:
    data = np.fromfile(str(path), np.uint8)   # đọc qua numpy: đường dẫn có dấu tiếng Việt vẫn mở được trên Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Không đọc được ảnh {path}")
    return img


def char_segments(image: np.ndarray) -> list[list[int]]:
    """[[x1, x2_exclusive], ...] của từng chữ theo thứ tự trái sang phải. `image`: mảng BGR (hoặc thang xám)."""
    gray = image.max(axis=2) if image.ndim == 3 else image
    h, w = gray.shape
    mask, _ = ink_mask(gray, BG_KERNEL, None, max(15, w // 10))
    ink = mask > 0
    if image.ndim == 3:   # loại hẳn pixel đỏ (chú thích bên lề), nở nhẹ để bỏ cả viền mờ quanh nét
        b, g, r = (image[:, :, i].astype(np.int16) for i in range(3))
        red = ((r - g > RED_DIFF) & (r - b > RED_DIFF)).astype(np.uint8)
        red = cv2.dilate(red, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
        dark = ink & ~red
        if dark.sum() >= RED_MAIN * ink.sum():   # mực đen là chính thì bỏ chú thích đỏ; dòng viết CHÍNH bằng mực đỏ thì giữ nguyên
            ink = dark
    prof = ink.sum(axis=0)
    segs = merge_close(runs(prof >= max(2, PROFILE_RATIO * h)), int(MERGE_GAP * h))
    segs = [s for s in segs if s[1] - s[0] >= MIN_WIDTH * h]
    if not segs:
        return []

    # gộp nửa chữ rời nhau
    widths = [e - s for s, e in segs]
    ref = float(np.median(widths))
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i in range(len(segs) - 1):
            (s1, e1), (s2, e2) = segs[i], segs[i + 1]
            narrow = min(e1 - s1, e2 - s2) < NARROW * ref
            if narrow and s2 - e1 <= SPLIT_GAP * h and e2 - s1 <= WIDE * h:
                segs[i:i + 2] = [[s1, e2]]
                changed = True
                break

    # bỏ dấu câu, vết bẩn: vừa hẹp vừa thấp
    kept = []
    for s, e in segs:
        rows = np.flatnonzero(ink[:, s:e].any(axis=1))
        height = int(rows[-1] - rows[0] + 1) if len(rows) else 0
        if (e - s) < SMALL * h and height < SMALL * h:
            continue
        kept.append([s, e])
    return kept


def count_chars(path) -> int:
    return len(char_segments(read_image(path)))


def draw(image: np.ndarray, segs: list[list[int]]) -> np.ndarray:
    vis = image.copy()
    for s, e in segs:
        cv2.rectangle(vis, (s, 0), (e - 1, vis.shape[0] - 1), (0, 0, 255), 2)
    cv2.putText(vis, str(len(segs)), (5, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
    return vis


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Đếm số chữ trong ảnh một hàng chữ ngang bằng chiếu dọc.")
    p.add_argument("path", type=Path, help="ảnh, hoặc thư mục ảnh")
    p.add_argument("--save-vis", type=Path, help="thư mục lưu ảnh vẽ box từng chữ")
    args = p.parse_args(argv)
    files = [args.path] if args.path.is_file() else sorted(f for f in args.path.iterdir() if f.suffix.lower() in IMG_EXT)
    if args.save_vis:
        args.save_vis.mkdir(parents=True, exist_ok=True)
    for f in files:
        img = read_image(f)
        segs = char_segments(img)
        print(f"{f.name}\t{len(segs)}")
        if args.save_vis:
            ok, enc = cv2.imencode(".png", draw(img, segs))
            if ok:
                enc.tofile(str(args.save_vis / f"{f.stem}.png"))


if __name__ == "__main__":
    main()
