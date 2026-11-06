"""Tách hàng chữ NGANG bằng xử lý ảnh cổ điển (chiếu ngang / projection profile), không dùng model, không dùng màu mực.

Dành cho trang có các hàng chữ thẳng (như vở tập chép Hán Nôm: hàng Hán xen hàng quốc ngữ). Kết quả là
bounding box [x1, y1, x2, y2] của từng hàng, theo thứ tự từ trên xuống.

    python scripts/row_projection_viz.py img/test_ocr_2.png
    python scripts/row_projection_viz.py anh.png --thr-ratio 0.015 --merge-gap 3 --min-height 8 --save-crops
    python scripts/row_projection_viz.py "data/<sách>/cache/pages"        # chạy theo lô cả thư mục ảnh

Các bước (mọi ngưỡng tính bằng pixel của ảnh gốc, trừ khi ghi khác):
    1. Thang xám -> tự cắt vùng giấy (bỏ viền trắng, tiêu đề ngoài giấy) nếu không có --no-auto-crop.
    2. Chuẩn hóa nền: nền = closing thang xám bằng kernel --bg-kernel; mực = nền - ảnh.
    3. Nhị phân hóa mực bằng Otsu (hoặc --ink-thresh); loại đường kẻ ô: nét ngang dài hơn --line-len
       (mặc định 1/10 chiều rộng giấy) và nét dọc dài hơn 1/10 chiều cao giấy bị trừ khỏi mặt nạ.
    4. Chiếu ngang: với mỗi hàng pixel đếm số pixel mực, làm mượt Gauss (--smooth), hàng pixel "có chữ" khi
       đếm >= --thr-ratio x chiều rộng giấy.
    5. Các đoạn liên tiếp có chữ là một hàng; khe hở ngắn hơn --merge-gap thì nối lại; đoạn thấp hơn
       --min-height bị bỏ.
    6. Cắt mỗi đoạn tại thung lũng của profile (--valley-ratio, --peak-prom): giữa hai đỉnh liền kề, nếu đáy
       < valley-ratio x đỉnh thấp hơn thì cắt. Đây là bước tách hàng Hán khỏi hàng quốc ngữ dính nhau.
       Tắt bằng --valley-ratio 0.
    7. Chiều ngang mỗi hàng (siết cạnh trái/phải): gom mực thành khối chữ, bỏ ghi chú lề ở xa (như "1a", "4-")
       nhưng giữ dấu câu nhỏ (. ,) ngay sau chữ cuối hàng; xem clean_x và nhóm tham số --blob-*, --near-gap,
       --punct-*. Tắt bằng --no-clean-x (lúc đó dùng mọi pixel mực, cắt --x-trim % ngoài cùng mỗi phía).
       Box được nới --pad px mỗi phía.

Đầu ra trong <out-dir> (mặc định output/row_projection/<tên ảnh>/):
    <tên>_boxes.png    ảnh vẽ box từng hàng + số thứ tự
    <tên>_ink.png      mặt nạ mực sau khi loại đường kẻ (kiểm tra bước 2-3)
    <tên>_profile.png  ảnh gốc kèm biểu đồ profile ngang, vạch ngưỡng và các đoạn tìm được (chỉnh ngưỡng bằng cái này)
    <tên>_res.json     tham số + danh sách hàng
    <tên>.txt          các hàng theo thứ tự: số thứ tự, bbox, chiều cao
    <tên>_Label.txt    một dòng theo định dạng PPOCRLabel (transcription để trống)
    crops/<tên>_NNN.png (chỉ khi --save-crops) ảnh cắt từng hàng

Chạy theo lô (tham số là THƯ MỤC): mỗi ảnh ra một thư mục con <out-dir>/<số trang>/, kèm summary.csv,
summary.json và overview_NN.png (ảnh xem nhanh). Script KHÔNG phân biệt trang bìa/trắng: mọi ảnh đều được
tách hàng như nhau.

Giới hạn: giả định hàng thẳng và nằm ngang; trang nghiêng nên căn thẳng trước.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from paddleocr_vl_viz import draw_boxes, load_font  # noqa: E402

IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def find_paper(gray: np.ndarray, white: int = 245) -> tuple[int, int, int, int]:
    """Bbox vùng giấy: pixel không trắng tinh, lấy thành phần liên thông lớn nhất sau khi đóng lỗ."""
    mask = (gray < white).astype(np.uint8) * 255
    k = max(3, min(gray.shape) // 60)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return 0, 0, gray.shape[1], gray.shape[0]
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[i, :4]
    return int(x), int(y), int(x + w), int(y + h)


def ink_mask(gray: np.ndarray, bg_kernel: int, ink_thresh: float | None, line_len: int):
    """Mặt nạ mực (255 = mực) đã chuẩn hóa nền và loại đường kẻ ô."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bg_kernel, bg_kernel))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)  # closing xoá nét tối mảnh -> còn lại nền giấy
    diff = cv2.subtract(bg, gray)                      # mực dương, nền ~ 0
    diff = cv2.GaussianBlur(diff, (3, 3), 0)
    if ink_thresh is None:
        t, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        t, mask = cv2.threshold(diff, ink_thresh, 255, cv2.THRESH_BINARY)

    # loại đường kẻ: nét ngang rất dài và nét dọc rất dài (lề đỏ)
    h_len, v_len = max(15, line_len), max(15, line_len * gray.shape[0] // max(1, gray.shape[1]))
    hl = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1)))
    vl = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len)))
    lines = cv2.dilate(cv2.bitwise_or(hl, vl), cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    return cv2.bitwise_and(mask, cv2.bitwise_not(lines)), float(t)


def runs(on: np.ndarray) -> list[list[int]]:
    """Các đoạn liên tiếp True: [[start, end_exclusive], ...]"""
    d = np.diff(np.concatenate([[0], on.astype(np.int8), [0]]))
    return [[int(s), int(e)] for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0])]


def merge_close(segs: list[list[int]], gap: int) -> list[list[int]]:
    out: list[list[int]] = []
    for s, e in segs:
        if out and s - out[-1][1] <= gap:
            out[-1][1] = e
        else:
            out.append([s, e])
    return out


def split_valleys(segs: list[list[int]], prof: np.ndarray, valley_ratio: float, peak_prom: float,
                  min_h: int) -> list[list[int]]:
    """Cắt mỗi đoạn tại các thung lũng của profile.

    Tìm các đỉnh có độ nổi >= peak_prom (px mực). Giữa hai đỉnh liền kề, nếu điểm thấp nhất của thung lũng
    < valley_ratio x đỉnh thấp hơn trong hai đỉnh thì cắt tại đó. Mảnh thấp hơn min_h được gộp lại vào mảnh kề.
    """
    from scipy.signal import find_peaks

    out: list[list[int]] = []
    for s, e in segs:
        p = prof[s:e]
        peaks, _ = find_peaks(np.pad(p, 1), prominence=peak_prom)
        peaks = peaks - 1  # bù phần pad để đỉnh sát mép cũng được tính
        cuts = []
        for a, b in zip(peaks, peaks[1:]):
            v = a + int(np.argmin(p[a:b + 1]))
            if p[v] < valley_ratio * min(p[a], p[b]):
                cuts.append(int(s + v))
        bounds = [s] + cuts + [e]
        pieces: list[list[int]] = []
        for lo, hi in zip(bounds, bounds[1:]):
            if pieces and (hi - lo < min_h or pieces[-1][1] - pieces[-1][0] < min_h):
                pieces[-1][1] = hi
            else:
                pieces.append([lo, hi])
        out.extend(pieces)
    return out


def extend_segments(segs: list[list[int]], prof: np.ndarray, low: float) -> list[list[int]]:
    """Kéo dài mỗi hàng ra theo profile để gồm cả đầu chữ (ascender) và đuôi chữ (descender).

    Đoạn tìm được bằng ngưỡng cao chỉ ôm lõi dày nhất của hàng; với mực nhạt, nét mảnh ở đầu/đuôi chữ bị cắt.
    Từ mỗi mép đi ra ngoài chừng nào profile còn >= low, nhưng không vượt quá THUNG LŨNG (điểm thấp nhất của
    profile) giữa hàng này và hàng kề, nên hai hàng không bao giờ chồng nhau. Chỉ dùng profile độ sáng thang xám.
    """
    n = len(prof)
    out = []
    for i, (s, e) in enumerate(segs):
        if i > 0:
            gap = prof[segs[i - 1][1]:s]
            lim_up = segs[i - 1][1] + int(np.mean(np.flatnonzero(gap == gap.min()))) if len(gap) else s
        else:
            lim_up = 0
        if i + 1 < len(segs):
            gap = prof[e:segs[i + 1][0]]
            lim_dn = e + int(np.mean(np.flatnonzero(gap == gap.min()))) if len(gap) else e
        else:
            lim_dn = n
        while s > lim_up and prof[s - 1] >= low:
            s -= 1
        while e < lim_dn and prof[e] >= low:
            e += 1
        out.append([s, e])
    return out


def clean_x(band: np.ndarray, row_h: int, args) -> np.ndarray:
    """Giữ lại mực thuộc THÂN hàng, bỏ ghi chú lề/vết bẩn nhưng giữ dấu câu cuối hàng. Trả về mặt nạ bool đã lọc.

    1. Nở mực theo chiều ngang (--blob-dilate x chiều cao hàng) để gom nét thành khối chữ (chữ Hán, từ quốc ngữ).
    2. Khối "mạnh" = khối lượng mực >= --blob-mass x khối lượng trung vị của hàng. Thân hàng = từ khối mạnh đầu
       tiên đến khối mạnh cuối cùng.
    3. Mở rộng thân hàng sang các khối yếu nằm sát nó, từng khối một từ trong ra ngoài, dừng ở khối đầu tiên bị loại:
         - phía TRÁI: chỉ nhận khối cách thân <= --near-gap x chiều cao hàng (ghi chú lề như "1a", "4-" ở xa hơn nên bị bỏ);
         - phía PHẢI: nhận thêm DẤU CÂU = khối nhỏ (rộng và cao <= --punct-size x chiều cao hàng) cách thân
           <= --punct-gap x chiều cao hàng. Dấu chấm/phẩy cuối câu vì vậy được giữ, còn số trang như "- 115"
           (cao hơn và xa hơn) thì bị bỏ.
    """
    ink = band > 0
    if not ink.any():
        return ink
    dil_w = max(3, int(round(args.blob_dilate * row_h)))
    dil = cv2.dilate(band, cv2.getStructuringElement(cv2.MORPH_RECT, (dil_w, 1)))
    n, labels = cv2.connectedComponents(dil, connectivity=8)
    ys, xs = np.nonzero(ink)
    lab = labels[ys, xs]
    ids = np.unique(lab)
    box = {i: (xs[lab == i].min(), xs[lab == i].max(), ys[lab == i].min(), ys[lab == i].max()) for i in ids}
    mass = {i: int((lab == i).sum()) for i in ids}
    med = float(np.median(list(mass.values())))
    strong = [i for i in ids if mass[i] >= args.blob_mass * med]
    if not strong:
        return ink
    keep = set(strong)
    lo, hi = min(box[i][0] for i in strong), max(box[i][1] for i in strong)
    near = args.near_gap * row_h
    punct_gap = args.punct_gap * row_h
    punct_sz = args.punct_size * row_h

    for i in sorted((i for i in ids if i not in keep and box[i][1] < lo), key=lambda i: -box[i][1]):  # sang trái
        if lo - box[i][1] <= near:
            keep.add(i)
            lo = min(lo, box[i][0])
        else:
            break
    for i in sorted((i for i in ids if i not in keep and box[i][0] > hi), key=lambda i: box[i][0]):    # sang phải
        x1, x2, y1, y2 = box[i]
        is_punct = (x2 - x1 + 1) <= punct_sz and (y2 - y1 + 1) <= punct_sz
        if x1 - hi <= near or (is_punct and x1 - hi <= punct_gap):
            keep.add(i)
            hi = max(hi, x2)
        else:
            break
    return ink & np.isin(labels, list(keep))


def draw_profile(image: Image.Image, prof: np.ndarray, thr: float, segs: list[list[int]],
                 crop: tuple[int, int, int, int]) -> Image.Image:
    """Ảnh gốc ghép biểu đồ profile (thanh ngang) bên phải; vạch đỏ = ngưỡng, nền vàng = đoạn được chọn."""
    x0, y0, x1, y1 = crop
    chart_w = 220
    out = Image.new("RGB", (image.width + chart_w, image.height), (255, 255, 255))
    out.paste(image, (0, 0))
    d = ImageDraw.Draw(out)
    pmax = max(float(prof.max()), thr * 1.5, 1.0)
    for s, e in segs:
        d.rectangle([image.width, y0 + s, image.width + chart_w, y0 + e - 1], fill=(255, 245, 190))
    for i, v in enumerate(prof):
        d.line([(image.width, y0 + i), (image.width + v / pmax * (chart_w - 4), y0 + i)], fill=(40, 90, 200))
    tx = image.width + thr / pmax * (chart_w - 4)
    d.line([(tx, y0), (tx, y1)], fill=(220, 30, 30), width=2)
    d.text((image.width + 4, 2), f"thr={thr:.1f}px  max={prof.max():.0f}", fill=(220, 30, 30), font=load_font(12))
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="đường dẫn ảnh, hoặc THƯ MỤC ảnh để chạy theo lô")
    ap.add_argument("--out-dir", help="thư mục ghi kết quả (mặc định output/row_projection/<tên ảnh hoặc sách>)")
    ap.add_argument("--no-auto-crop", action="store_true", help="không tự cắt vùng giấy (dùng cả ảnh)")
    ap.add_argument("--roi", help="vùng xử lý x1,y1,x2,y2 (ghi đè --auto-crop)")
    ap.add_argument("--margin", type=int, default=6, help="thụt vào trong mép giấy bấy nhiêu px (mặc định 6)")
    ap.add_argument("--bg-kernel", type=int, default=25, help="kernel closing ước lượng nền (mặc định 25)")
    ap.add_argument("--ink-thresh", type=float, help="ngưỡng mực cố định 0-255 (mặc định: Otsu)")
    ap.add_argument("--line-len", type=int, help="độ dài tối thiểu của nét bị coi là đường kẻ (mặc định 1/10 chiều rộng giấy)")
    ap.add_argument("--smooth", type=float, default=2.5, help="sigma làm mượt profile (mặc định 2.5)")
    ap.add_argument("--thr-ratio", type=float, default=0.012,
                    help="hàng pixel có chữ khi số pixel mực >= tỉ lệ này x chiều rộng giấy (mặc định 0.012)")
    ap.add_argument("--valley-ratio", type=float, default=0.6,
                    help="cắt tại thung lũng khi đáy < tỉ lệ này x đỉnh thấp hơn trong hai đỉnh kề; 0 = tắt (mặc định 0.6)")
    ap.add_argument("--peak-prom", type=float, default=0.2,
                    help="độ nổi tối thiểu của đỉnh, tỉ lệ so với profile lớn nhất (mặc định 0.2)")
    ap.add_argument("--edge-ratio", type=float, default=0.05,
                    help="kéo dài mỗi hàng ra trên/dưới tới khi profile < tỉ lệ này x ngưỡng hàng, tối đa tới thung lũng "
                         "với hàng kề: lấy trọn đầu/đuôi chữ khi mực nhạt; 0 = tắt, giữ đoạn ngưỡng cao (mặc định 0.05)")
    ap.add_argument("--merge-gap", type=int, default=2, help="nối hai đoạn cách nhau <= bấy nhiêu px (mặc định 2)")
    ap.add_argument("--min-height", type=int, default=8, help="bỏ hàng thấp hơn bấy nhiêu px (mặc định 8)")
    ap.add_argument("--x-trim", type=float, default=0.5,
                    help="cắt bỏ %% pixel mực ngoài cùng mỗi phía khi tính chiều ngang (mặc định 0.5)")
    ap.add_argument("--pad", type=int, default=2, help="nới box ra bấy nhiêu px mỗi phía (mặc định 2)")
    g = ap.add_argument_group("siết cạnh trái/phải: bỏ chú thích, giữ dấu câu cuối hàng")
    g.add_argument("--no-clean-x", action="store_true",
                   help="tắt: dùng mọi pixel mực, cắt --x-trim %% ngoài cùng (hành vi cũ)")
    g.add_argument("--blob-dilate", type=float, default=0.3,
                   help="nở ngang mực = tỉ lệ này x chiều cao hàng để gom nét thành khối chữ (mặc định 0.3)")
    g.add_argument("--blob-mass", type=float, default=0.35,
                   help="khối có khối lượng mực < tỉ lệ này x trung vị của hàng là khối yếu (mặc định 0.35)")
    g.add_argument("--near-gap", type=float, default=0.35,
                   help="khối yếu được giữ nếu cách thân hàng <= tỉ lệ này x chiều cao hàng (mặc định 0.35)")
    g.add_argument("--punct-gap", type=float, default=2.0,
                   help="PHÍA PHẢI: dấu câu được giữ nếu cách thân hàng <= tỉ lệ này x chiều cao hàng (mặc định 2.0)")
    g.add_argument("--punct-size", type=float, default=0.45,
                   help="khối là dấu câu khi cả rộng lẫn cao <= tỉ lệ này x chiều cao hàng (mặc định 0.45)")
    ap.add_argument("--save-crops", action="store_true", help="cắt từng hàng ra <out-dir>/crops/")
    return ap


def process(args, image_path: Path, out_dir: Path, stem: str | None = None, verbose: bool = True) -> dict:
    """Tách hàng cho MỘT ảnh, ghi kết quả vào out_dir, trả về bản tóm tắt."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or image_path.stem
    log = print if verbose else (lambda *a, **k: None)

    pil = Image.open(image_path).convert("RGB")
    gray_full = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2GRAY)
    H, W = gray_full.shape

    if args.roi:
        x0, y0, x1, y1 = (int(v) for v in args.roi.split(","))
    elif args.no_auto_crop:
        x0, y0, x1, y1 = 0, 0, W, H
    else:
        x0, y0, x1, y1 = find_paper(gray_full)
    m = args.margin
    x0, y0, x1, y1 = x0 + m, y0 + m, x1 - m, y1 - m
    gray = gray_full[y0:y1, x0:x1]
    pw = gray.shape[1]
    log(f"Vùng xử lý: x={x0}..{x1} y={y0}..{y1} ({pw}x{gray.shape[0]}px)")

    line_len = args.line_len or max(20, pw // 10)
    mask, otsu = ink_mask(gray, args.bg_kernel, args.ink_thresh, line_len)
    log(f"Ngưỡng mực: {otsu:.0f} ({'cố định' if args.ink_thresh is not None else 'Otsu'}), "
        f"mực chiếm {100 * (mask > 0).mean():.1f}% vùng")

    prof = (mask > 0).sum(axis=1).astype(np.float32)
    if args.smooth > 0:
        prof = cv2.GaussianBlur(prof.reshape(-1, 1), (1, 0), args.smooth).ravel()
    thr = args.thr_ratio * pw

    segs = merge_close(runs(prof >= thr), args.merge_gap)
    segs = [s for s in segs if s[1] - s[0] >= args.min_height]
    if args.valley_ratio > 0:
        segs = split_valleys(segs, prof, args.valley_ratio, args.peak_prom * float(prof.max()), args.min_height)

    if args.edge_ratio > 0:
        segs = extend_segments(segs, prof, args.edge_ratio * thr)

    rows = []
    for s, e in segs:
        if args.no_clean_x:
            ys, xs = np.nonzero(mask[s:e] > 0)
            if len(xs) == 0:
                continue
            lo, hi = np.percentile(xs, [args.x_trim, 100 - args.x_trim])
        else:
            ys, xs = np.nonzero(clean_x(mask[s:e], e - s, args))
            if len(xs) == 0:
                continue
            lo, hi = xs.min(), xs.max() + 1  # đúng biên khối giữ lại, không cắt phân vị (tránh xén dấu chấm)
        bx1, bx2 = x0 + int(lo) - args.pad, x0 + int(hi) + args.pad
        by1, by2 = y0 + s - args.pad, y0 + e + args.pad
        rows.append([max(0, bx1), max(0, by1), min(W, bx2), min(H, by2)])
    n = len(rows)

    mask_full = np.zeros((H, W), np.uint8)
    mask_full[y0:y1, x0:x1] = mask
    Image.fromarray(255 - mask_full).save(out_dir / f"{stem}_ink.png")
    draw_profile(pil, prof, thr, segs, (x0, y0, x1, y1)).save(out_dir / f"{stem}_profile.png")

    items = [{"label": "row", "bbox": b, "polygon": None, "tag": str(i), "content": "",
              "color": (210, 40, 40) if i % 2 else (30, 110, 200)} for i, b in enumerate(rows, 1)]
    draw_boxes(pil, items, 0).save(out_dir / f"{stem}_boxes.png")

    (out_dir / f"{stem}_res.json").write_text(json.dumps({
        "image": image_path.name, "image_size": [W, H], "roi": [x0, y0, x1, y1],
        "params": {k: v for k, v in vars(args).items() if k not in ("image", "out_dir")},
        "ink_threshold": otsu, "profile_threshold": thr,
        "rows": [{"order": i, "bbox": b, "height": b[3] - b[1]} for i, b in enumerate(rows, 1)],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.txt").write_text(
        "\n".join(f"[{i}] bbox={b} cao={b[3] - b[1]}" for i, b in enumerate(rows, 1)) + "\n", encoding="utf-8")
    label = [{"transcription": "", "points": [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]} for b in rows]
    (out_dir / f"{stem}_Label.txt").write_text(f"{image_path.name}\t{json.dumps(label, ensure_ascii=False)}\n",
                                               encoding="utf-8")
    if args.save_crops:
        crop_dir = out_dir / "crops"
        crop_dir.mkdir(exist_ok=True)
        for i, b in enumerate(rows, 1):
            pil.crop(tuple(b)).save(crop_dir / f"{stem}_{i:03d}.png")

    log(f"\n{n} hàng (ngưỡng profile {thr:.1f}px, max {prof.max():.0f}px)")
    log(f"{'#':>3}  {'bbox':<26} {'cao':>4} {'rộng':>5}")
    for i, b in enumerate(rows, 1):
        log(f"{i:>3}  {str(b):<26} {b[3] - b[1]:>4} {b[2] - b[0]:>5}")
    log(f"\nĐã ghi kết quả vào: {out_dir.resolve()}")
    heights = [b[3] - b[1] for b in rows]
    return {"image": image_path.name, "rows": n, "median_row_height": float(np.median(heights)) if heights else 0,
            "ink_threshold": round(otsu, 1)}


def page_key(path: Path) -> str:
    """'<tên sách>.001.jpg' -> '001'; ảnh không theo mẫu này thì giữ nguyên tên không đuôi."""
    tail = path.stem.rsplit(".", 1)[-1]
    return tail if tail.isdigit() else path.stem


def contact_sheets(root: Path, keys: list[str], per_sheet: int = 12, cols: int = 4, th: int = 520) -> list[Path]:
    """Ghép ảnh _boxes.png của các trang thành vài tờ xem nhanh (cols cột)."""
    sheets = []
    for s in range(0, len(keys), per_sheet):
        chunk = keys[s:s + per_sheet]
        thumbs = []
        for k in chunk:
            im = Image.open(root / k / f"{k}_boxes.png").convert("RGB")
            im = im.resize((max(1, int(im.width * th / im.height)), th))
            ImageDraw.Draw(im).text((6, 4), k, fill=(0, 0, 0), font=load_font(22))
            thumbs.append(im)
        cw = max(t.width for t in thumbs)
        rows_n = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cw, rows_n * th), (255, 255, 255))
        for i, t in enumerate(thumbs):
            sheet.paste(t, ((i % cols) * cw, (i // cols) * th))
        p = root / f"overview_{s // per_sheet + 1:02d}.png"
        sheet.save(p)
        sheets.append(p)
    return sheets


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()

    src = Path(args.image)
    if src.is_file():
        out_dir = Path(args.out_dir) if args.out_dir else Path("output") / "row_projection" / src.stem
        process(args, src, out_dir)
        return
    if not src.is_dir():
        sys.exit(f"Không thấy ảnh/thư mục: {src}")

    files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXT)
    if not files:
        sys.exit(f"Thư mục không có ảnh: {src}")
    if args.out_dir:
        root = Path(args.out_dir)
    else:
        book = src.parent.parent if (src.name == "pages" and src.parent.name == "cache") else src
        root = Path("output") / "row_projection" / unicodedata.normalize("NFC", book.name)
    root.mkdir(parents=True, exist_ok=True)
    print(f"Chạy theo lô {len(files)} ảnh từ {src}\n -> {root.resolve()}")

    summary, ok_keys = [], []
    for i, f in enumerate(files, 1):
        key = page_key(f)
        try:
            s = process(args, f, root / key, stem=key, verbose=False)
            ok_keys.append(key)
            print(f"[{i:>3}/{len(files)}] {key}: {s['rows']:>3} hàng, cao TB {s['median_row_height']:.0f}px")
        except Exception as e:  # một ảnh lỗi không làm hỏng cả lô
            s = {"image": f.name, "error": f"{type(e).__name__}: {e}"}
            print(f"[{i:>3}/{len(files)}] {key}: LỖI {s['error']}")
        summary.append({"page": key, **s})

    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["page", "image", "rows", "median_row_height", "ink_threshold", "error"]
    with (root / "summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summary)
    sheets = contact_sheets(root, ok_keys)
    counts = [s["rows"] for s in summary if "rows" in s]
    print(f"\nXong {len(ok_keys)}/{len(files)} ảnh. Số hàng/trang: min {min(counts, default=0)}, "
          f"trung vị {int(np.median(counts)) if counts else 0}, max {max(counts, default=0)}")
    print(f"Tổng hợp: {root / 'summary.csv'}\nẢnh xem nhanh: {', '.join(p.name for p in sheets)}")


if __name__ == "__main__":
    main()
