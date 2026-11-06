"""Phát hiện dòng chữ (baseline segmentation) bằng Kraken blla trên MỘT ảnh, ghi kết quả ra file và vẽ box để kiểm tra.

Kraken chỉ PHÁT HIỆN dòng (baseline + polygon bao dòng + vùng), không nhận dạng chữ và không có score
độ tin cậy; ở đây dùng để so sánh chất lượng cắt dòng với Kim Hán Nôm / PaddleOCR-VL.

Cài đặt (Kraken chỉ hỗ trợ chính thức Linux/macOS, trên Windows vẫn chạy được cho phần segment):
    python -m venv C:\\kr --system-site-packages       # đường dẫn NGẮN; dùng lại torch đã có trong máy
    C:\\kr\\Scripts\\activate
    pip install kraken
    pip install "safetensors>=0.8.0"                    # tránh xung đột với transformers của hệ thống
    python scripts/kraken_viz.py anh.png

Model mặc định là blla.mlmodel đi kèm gói kraken (~5 MB), không cần tải thêm. Muốn dùng model tự huấn
luyện / tải từ kho model của Kraken thì truyền --model <file.mlmodel>.

    python scripts/kraken_viz.py anh.png --text-direction horizontal-lr --device cuda:0
    python scripts/kraken_viz.py anh.png --model my_seg.mlmodel --save-crops

GỘP MẢNH DÒNG NGANG (--merge): model hay cắt một hàng chữ ngang thành nhiều mảnh ngắn. Bật --merge để gộp hai
mảnh thành một dòng khi CẢ BA điều kiện cùng đúng (mọi ngưỡng tính bằng pixel của ảnh gốc):
    1. cả hai đều là dòng ngang: góc baseline <= --merge-max-angle độ (mặc định 20);
    2. lệch dọc nhỏ: |y_giữa_baseline_a - y_giữa_baseline_b| <= --merge-y-tol (mặc định 8);
    3. cách nhau theo chiều ngang < --merge-gap (mặc định 30); hai mảnh chồng nhau theo x thì khoảng cách = 0.
Việc gộp có tính bắc cầu (a gần b, b gần c thì cả a, b, c thành một dòng). Dòng sau khi gộp có baseline nối các
điểm theo x và polygon là hợp của các polygon thành phần, lấp khoảng trống giữa hai mảnh kề nhau.
Dòng không ngang (cột chữ dọc) không bị đụng tới. Console in chiều cao dòng trung vị để chọn ngưỡng:
gợi ý --merge-gap ~ 1-2 lần chiều cao dòng, --merge-y-tol ~ 0.3 lần chiều cao dòng.
    python scripts/kraken_viz.py anh.png --merge --merge-gap 60 --merge-y-tol 12 --min-width 40

Đầu ra trong <out-dir> (mặc định output/kraken/<tên ảnh>/):
    <tên>_boxes.png     ảnh vẽ polygon từng dòng + số thứ tự đọc; đường baseline vẽ đè bằng nét trắng-đen
    <tên>_raw_boxes.png (chỉ khi --merge) ảnh các mảnh TRƯỚC khi gộp, để so sánh
    <tên>_regions.png   ảnh vẽ các vùng (region) Kraken phát hiện, nếu có
    <tên>_res.json      toàn bộ kết quả: dòng (id, baseline, boundary, tags, merged_from), vùng, thứ tự đọc
    <tên>.txt           các dòng theo thứ tự đọc: số thứ tự, bbox, baseline
    <tên>_Label.txt     một dòng theo định dạng PPOCRLabel (đường dẫn ảnh TAB mảng transcription/points),
                        transcription để trống; dùng làm khung nhãn khởi đầu rồi gõ chữ vào
    crops/<tên>_NNN.png (chỉ khi --save-crops) ảnh cắt từng dòng theo bbox, có thể đưa thẳng vào bộ nhận dạng

Cách đọc chất lượng:
    * _boxes.png: số dòng có khớp thực tế không, polygon có bao đúng một dòng hay nuốt cả dòng kế bên,
      thứ tự đọc có đúng không (với trang ngang xen hàng Hán / hàng quốc ngữ, xem hai loại hàng có tách riêng không).
    * Kraken không có score, nên màu chỉ phân biệt theo thứ tự đọc.
    * Khi bật --merge: ngưỡng quá lớn sẽ gộp nhầm hai hàng sát nhau (tăng --merge-y-tol) hoặc gộp hai cột
      của trang hai cột (tăng --merge-gap); quá nhỏ thì dòng vẫn bị cắt vụn.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # paddleocr_vl_viz (dùng lại hàm vẽ)

from PIL import Image, ImageDraw  # noqa: E402

from paddleocr_vl_viz import draw_boxes  # noqa: E402


def bbox_of(points) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]


def int_points(points) -> list[list[int]]:
    return [[int(round(x)), int(round(y))] for x, y in points]


def order_color(i: int) -> tuple[int, int, int]:
    """Màu theo thứ tự đọc: golden-ratio hue nên các dòng liền kề khác màu rõ ràng, dễ thấy polygon lấn dòng."""
    h = (i * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.85, 0.9)
    return int(r * 255), int(g * 255), int(b * 255)


def line_type(line) -> str:
    """Kraken 7 lưu tags['type'] dạng [{'type': 'default'}]; bản cũ lưu thẳng chuỗi."""
    t = (getattr(line, "tags", None) or {}).get("type", "line")
    if isinstance(t, (list, tuple)) and t:
        t = t[0]
    if isinstance(t, dict):
        t = t.get("type", "line")
    return str(t)


def load_model(path: str | None, device: str):
    if not path:
        return None  # blla.segment tự nạp model mặc định
    from kraken.lib import vgsl
    model = vgsl.TorchVGSLModel.load_model(path)
    model.to(device)
    return model


# ----------------------------------------------------------------------------------------------------------
# Dòng dạng dict thuần: {id, type, tags, baseline:[[x,y],..], boundary:[[x,y],..]|None, merged_from:[id,..]}
# ----------------------------------------------------------------------------------------------------------

def to_dicts(lines) -> list[dict]:
    out = []
    for ln in lines:
        base = int_points(ln.baseline)
        out.append({"id": ln.id, "type": line_type(ln), "tags": dict(ln.tags) if ln.tags else None,
                    "baseline": base,
                    "boundary": int_points(ln.boundary) if ln.boundary else None,
                    "merged_from": [ln.id]})
    return out


def poly_of(ln: dict) -> list[list[int]]:
    return ln["boundary"] or ln["baseline"]


def baseline_angle(base) -> float:
    """Góc (độ, 0-90) giữa baseline và phương ngang, tính từ điểm trái nhất tới điểm phải nhất."""
    pts = sorted(base)  # theo x tăng dần nên dx >= 0, dùng |dy| => góc trong [0, 90]
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    return math.degrees(math.atan2(abs(y2 - y1), x2 - x1))


def mean_y(base) -> float:
    return sum(p[1] for p in base) / len(base)


def merge_polygons(polys: list[list[list[int]]]) -> list[list[int]]:
    """Hợp các polygon, lấp khoảng trống giữa hai polygon kề nhau (theo x) bằng bao lồi của cặp đó."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    geoms = [Polygon(p).buffer(0) for p in polys if len(p) >= 3]
    geoms = [g for g in geoms if not g.is_empty]
    if not geoms:
        return []
    geoms.sort(key=lambda g: g.bounds[0])
    parts = list(geoms)
    for a, b in zip(geoms, geoms[1:]):
        parts.append(unary_union([a, b]).convex_hull)  # cầu nối hai mảnh kề nhau
    merged = unary_union(parts)
    if isinstance(merged, MultiPolygon):  # vẫn rời nhau (hiếm): lấy bao lồi của tất cả
        merged = merged.convex_hull
    if merged.geom_type != "Polygon":
        merged = merged.convex_hull
    return [[int(round(x)), int(round(y))] for x, y in list(merged.exterior.coords)[:-1]]


def merge_horizontal(lines: list[dict], gap: float, y_tol: float, max_angle: float) -> list[dict]:
    """Gộp các mảnh dòng ngang cùng một hàng (xem điều kiện ở docstring đầu file)."""
    n = len(lines)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    horizontal = [baseline_angle(ln["baseline"]) <= max_angle for ln in lines]
    boxes = [bbox_of(poly_of(ln)) for ln in lines]
    ys = [mean_y(ln["baseline"]) for ln in lines]

    for i in range(n):
        if not horizontal[i]:
            continue
        for j in range(i + 1, n):
            if not horizontal[j] or abs(ys[i] - ys[j]) > y_tol:
                continue
            h_gap = max(0, max(boxes[i][0], boxes[j][0]) - min(boxes[i][2], boxes[j][2]))
            if h_gap < gap:
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(lines[members[0]])
            continue
        pts = sorted({tuple(p) for i in members for p in lines[i]["baseline"]})
        polys = [poly_of(lines[i]) for i in members]
        boundary = merge_polygons(polys)
        first = lines[members[0]]
        merged.append({"id": first["id"], "type": first["type"], "tags": first["tags"],
                       "baseline": [list(p) for p in pts],
                       "boundary": boundary or None,
                       "merged_from": [lines[i]["id"] for i in members]})
    return merged


def sort_reading_order(lines: list[dict], text_direction: str) -> list[dict]:
    """Sau khi gộp thứ tự Kraken không còn đúng: dòng ngang xếp theo hàng (trên xuống) rồi theo x."""
    if text_direction == "horizontal-rl":
        return sorted(lines, key=lambda ln: (mean_y(ln["baseline"]), -max(p[0] for p in poly_of(ln))))
    if text_direction == "horizontal-lr":
        return sorted(lines, key=lambda ln: (mean_y(ln["baseline"]), min(p[0] for p in poly_of(ln))))
    return lines  # chữ dọc: giữ thứ tự Kraken


# ----------------------------------------------------------------------------------------------------------

def render(image: Image.Image, lines: list[dict], show_order: bool, draw_baseline: bool) -> Image.Image:
    items = [{"label": ln["type"], "bbox": bbox_of(poly_of(ln)), "polygon": poly_of(ln),
              "tag": str(i) if show_order else "", "content": "", "color": order_color(i)}
             for i, ln in enumerate(lines, 1)]
    out = draw_boxes(image, items, 0)
    if draw_baseline:
        d = ImageDraw.Draw(out)
        w = max(2, image.width // 500)
        for ln in lines:
            pts = [tuple(p) for p in ln["baseline"]]
            if len(pts) >= 2:
                d.line(pts, fill=(0, 0, 0), width=w + 2)
                d.line(pts, fill=(255, 255, 255), width=w)
    return out


def main():
    for stream in (sys.stdout, sys.stderr):  # console Windows mặc định cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="đường dẫn ảnh cần phát hiện dòng")
    ap.add_argument("--out-dir", help="thư mục ghi kết quả (mặc định output/kraken/<tên ảnh>)")
    ap.add_argument("--model", help="file segmentation .mlmodel (mặc định: blla.mlmodel đi kèm gói kraken)")
    ap.add_argument("--text-direction", default="horizontal-lr",
                    choices=["horizontal-lr", "horizontal-rl", "vertical-lr", "vertical-rl"],
                    help="hướng chữ chính, dùng cho thứ tự đọc và hướng dòng khi model không chắc "
                         "(mặc định horizontal-lr; chữ Hán dọc đọc từ phải sang trái thử vertical-rl)")
    ap.add_argument("--device", help="cuda:0 | cpu (mặc định: cuda:0 nếu torch thấy CUDA, ngược lại cpu)")
    ap.add_argument("--show-order", type=int, default=1, choices=[0, 1],
                    help="1 = in số thứ tự đọc cạnh mỗi dòng (mặc định), 0 = không in nhãn")
    ap.add_argument("--no-baseline", action="store_true", help="không vẽ đường baseline lên ảnh")
    ap.add_argument("--save-crops", action="store_true", help="cắt từng dòng theo bbox ra <out-dir>/crops/")

    g = ap.add_argument_group("gộp mảnh dòng ngang (hậu xử lý, ngưỡng tính bằng pixel)")
    g.add_argument("--merge", action="store_true", help="bật gộp các mảnh dòng ngang cùng hàng")
    g.add_argument("--merge-gap", type=float, default=30,
                   help="gộp khi khoảng cách ngang giữa hai mảnh < giá trị này (mặc định 30)")
    g.add_argument("--merge-y-tol", type=float, default=8,
                   help="gộp khi lệch dọc giữa hai baseline <= giá trị này (mặc định 8)")
    g.add_argument("--merge-max-angle", type=float, default=20,
                   help="chỉ gộp dòng có góc baseline <= giá trị này (độ, mặc định 20); dòng dọc không gộp")
    g.add_argument("--min-width", type=float, default=0,
                   help="sau khi gộp bỏ dòng có bbox rộng < giá trị này (mặc định 0 = giữ hết); "
                        "hữu ích để loại mảnh nhiễu nhỏ")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        sys.exit(f"Không thấy ảnh: {image_path}")
    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / "kraken" / image_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    device = args.device
    if device is None:
        try:
            import torch
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    try:
        from kraken import blla
    except ImportError:
        sys.exit("Chưa cài kraken. Chạy: pip install kraken  (xem hướng dẫn đầu file)")

    print(f"Nạp model: {args.model or 'blla.mlmodel mặc định'} | device={device}")
    t0 = time.perf_counter()
    model = load_model(args.model, device)
    print(f"  nạp xong sau {time.perf_counter() - t0:.1f}s")

    image = Image.open(image_path).convert("RGB")
    t1 = time.perf_counter()
    seg = blla.segment(image, text_direction=args.text_direction, model=model, device=device)
    print(f"  phát hiện xong sau {time.perf_counter() - t1:.1f}s")

    stem = image_path.stem
    raw = to_dicts(seg.lines)  # đã theo thứ tự đọc do Kraken ước lượng
    lines = raw

    if raw:
        heights = [bbox_of(poly_of(ln))[3] - bbox_of(poly_of(ln))[1] for ln in raw]
        print(f"  chiều cao dòng trung vị: {statistics.median(heights):.0f}px "
              f"(gợi ý: --merge-gap ~ {statistics.median(heights):.0f}-{2 * statistics.median(heights):.0f}, "
              f"--merge-y-tol ~ {0.3 * statistics.median(heights):.0f})")

    if args.merge:
        lines = merge_horizontal(raw, args.merge_gap, args.merge_y_tol, args.merge_max_angle)
        lines = sort_reading_order(lines, args.text_direction)
        print(f"  gộp: {len(raw)} mảnh -> {len(lines)} dòng "
              f"(gap<{args.merge_gap:g}, lệch dọc<={args.merge_y_tol:g}, góc<={args.merge_max_angle:g}°)")
    if args.min_width > 0:
        before = len(lines)
        lines = [ln for ln in lines if (lambda b: b[2] - b[0])(bbox_of(poly_of(ln))) >= args.min_width]
        print(f"  bỏ dòng rộng < {args.min_width:g}px: {before} -> {len(lines)}")
    n = len(lines)

    txt, label_rows = [], []
    for i, ln in enumerate(lines, 1):
        bbox = bbox_of(poly_of(ln))
        merged = f" merged={len(ln['merged_from'])}" if len(ln["merged_from"]) > 1 else ""
        txt.append(f"[{i}] type={ln['type']} bbox={bbox}{merged}\nbaseline={ln['baseline']}\n")
        label_rows.append({"transcription": "", "points": poly_of(ln)})

    data = {
        "image": image_path.name,
        "image_size": list(image.size),
        "text_direction": seg.text_direction,
        "model": args.model or "blla.mlmodel (default)",
        "merge": {"enabled": args.merge, "gap": args.merge_gap, "y_tol": args.merge_y_tol,
                  "max_angle": args.merge_max_angle, "min_width": args.min_width,
                  "raw_line_count": len(raw)} if args.merge or args.min_width > 0 else None,
        "lines": [{"order": i, **ln} for i, ln in enumerate(lines, 1)],
        "regions": {rtype: [{"id": r.id, "boundary": int_points(r.boundary), "tags": dict(r.tags) if r.tags else None}
                            for r in regs]
                    for rtype, regs in (seg.regions or {}).items()},
        "line_orders": [list(o) for o in (seg.line_orders or [])],
    }
    (out_dir / f"{stem}_res.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.txt").write_text("\n".join(txt), encoding="utf-8")
    (out_dir / f"{stem}_Label.txt").write_text(
        f"{image_path.name}\t{json.dumps(label_rows, ensure_ascii=False)}\n", encoding="utf-8")

    render(image, lines, bool(args.show_order), not args.no_baseline).save(out_dir / f"{stem}_boxes.png")
    if args.merge:
        render(image, raw, bool(args.show_order), not args.no_baseline).save(out_dir / f"{stem}_raw_boxes.png")

    region_items = []
    for rtype, regs in (seg.regions or {}).items():
        for j, r in enumerate(regs, 1):
            poly = int_points(r.boundary)
            region_items.append({"label": rtype, "bbox": bbox_of(poly), "polygon": poly, "tag": f"{rtype} {j}"})
    if region_items:
        draw_boxes(image, region_items, 0).save(out_dir / f"{stem}_regions.png")

    if args.save_crops:
        crop_dir = out_dir / "crops"
        crop_dir.mkdir(exist_ok=True)
        for i, ln in enumerate(lines, 1):
            x1, y1, x2, y2 = bbox_of(poly_of(ln))
            image.crop((max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))).save(
                crop_dir / f"{stem}_{i:03d}.png")

    print(f"\n{n} dòng, {sum(len(v) for v in (seg.regions or {}).values())} vùng, text_direction={seg.text_direction}")
    print(f"{'#':>3}  {'type':<10} {'bbox':<26} {'cao':>4}  {'mảnh':>4}")
    for i, ln in enumerate(lines, 1):
        b = bbox_of(poly_of(ln))
        print(f"{i:>3}  {ln['type']:<10} {str(b):<26} {b[3] - b[1]:>4}  {len(ln['merged_from']):>4}")
    print(f"\nĐã ghi kết quả vào: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
