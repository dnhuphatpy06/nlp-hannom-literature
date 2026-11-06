"""Chạy PaddleOCR-VL trên MỘT ảnh, ghi kết quả ra file và vẽ bounding box để kiểm tra chất lượng.

Cài đặt (xem README đi kèm trong câu trả lời; tóm tắt):
    python -m venv C:\\ovl --system-site-packages       # đường dẫn NGẮN, tránh lỗi đường dẫn dài trên Windows
    C:\\ovl\\Scripts\\activate
    pip install "paddleocr[doc-parser]"                  # đã có torch + transformers>=5.10 trong máy
    python scripts/paddleocr_vl_viz.py anh.png

Lần chạy đầu tự tải model về C:\\Users\\<bạn>\\.paddlex\\official_models (~2GB).

Đầu ra trong <out-dir> (mặc định output/paddleocr_vl/<tên ảnh>/):
    <tên>_blocks.png   ảnh vẽ các block KẾT QUẢ (sau khi gộp): thứ tự đọc, nhãn, nội dung rút gọn
    <tên>_layout.png   ảnh vẽ các box THÔ của bộ phát hiện layout: nhãn + điểm tin cậy (score)
    <tên>_res.json     toàn bộ kết quả gốc của pipeline
    <tên>.txt          các block theo thứ tự đọc: nhãn, bbox, nội dung

Cách đọc chất lượng:
    * _layout.png: layout có bắt đúng vùng chữ không, score bao nhiêu, có box chồng lấn / thiếu không.
    * _blocks.png: các block cuối có bị gộp nhầm không (đổi --merge-mode), thứ tự đọc đúng không.
    * <tên>.txt / ảnh: nội dung nhận dạng có khớp ảnh không.
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")  # bỏ bước kiểm tra kết nối chậm

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/mingliu.ttc", "C:/Windows/Fonts/YuGothR.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()  # không hiển thị được chữ Hán, nhưng nhãn Latin vẫn đọc được


def label_color(label: str) -> tuple[int, int, int]:
    """Màu cố định theo tên nhãn: cùng nhãn luôn cùng màu giữa các lần chạy."""
    h = int(hashlib.md5(label.encode()).hexdigest()[:6], 16) % 360 / 360
    r, g, b = colorsys.hsv_to_rgb(h, 0.85, 0.9)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_boxes(image: Image.Image, items: list[dict], show_content: int) -> Image.Image:
    """items: {label, bbox:[x1,y1,x2,y2], polygon:[[x,y],...]|None, tag:str, content:str}"""
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    width = max(2, base.width // 400)
    font = load_font(max(14, base.width // 60))

    for it in items:
        color = it.get("color") or label_color(it["label"])
        poly = it.get("polygon")
        if poly and len(poly) >= 3:
            pts = [tuple(p) for p in poly]
            od.polygon(pts, fill=color + (45,))
            od.line(pts + [pts[0]], fill=color + (255,), width=width)
        else:
            x1, y1, x2, y2 = it["bbox"]
            od.rectangle([x1, y1, x2, y2], fill=color + (45,), outline=color + (255,), width=width)

    out = Image.alpha_composite(base, overlay)
    d = ImageDraw.Draw(out)
    placed: list[tuple[float, float, float, float]] = []  # nhãn đã đặt, để các nhãn không đè lên nhau
    for it in items:
        color = it.get("color") or label_color(it["label"])
        x1, y1, _, _ = it["bbox"]
        text = it["tag"]
        if show_content and it.get("content"):
            snippet = it["content"].replace("\n", " ")
            text += " | " + snippet[:show_content] + ("…" if len(snippet) > show_content else "")
        left, top, right, bottom = d.textbbox((0, 0), text, font=font)
        tw, th = right - left, bottom - top
        tx = min(max(0, x1), max(0, out.width - tw - 6))
        step = th + 8
        ty = y1 - step
        for _ in range(8):  # nhãn chồng nhãn khác thì đẩy lên cao hơn (cột chữ hẹp, nhãn rộng hơn box)
            if ty >= 0 and not any(tx < r + 1 and tx + tw + 6 > l - 1 and ty < b and ty + th + 6 > t
                                   for l, t, r, b in placed):
                break
            ty -= step
        if ty < 0:
            ty = y1 + 2  # hết chỗ phía trên: đặt nhãn vào trong box
        placed.append((tx, ty, tx + tw + 6, ty + th + 6))
        d.rectangle([tx, ty, tx + tw + 6, ty + th + 6], fill=color + (255,))
        d.text((tx + 3 - left, ty + 3 - top), text, fill=(255, 255, 255, 255), font=font)
    return out.convert("RGB")


def to_int_box(coord) -> list[int]:
    return [int(round(float(v))) for v in coord]


def main():
    for stream in (sys.stdout, sys.stderr):  # console Windows mặc định cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="đường dẫn ảnh cần nhận diện")
    ap.add_argument("--out-dir", help="thư mục ghi kết quả (mặc định output/paddleocr_vl/<tên ảnh>)")
    ap.add_argument("--engine", choices=["transformers", "paddle"], default="transformers",
                    help="transformers: chạy bằng PyTorch, KHÔNG cần paddlepaddle (mặc định). "
                         "paddle: cần cài paddlepaddle riêng")
    ap.add_argument("--device", help="gpu:0 | cpu (mặc định: gpu:0 nếu torch thấy CUDA, ngược lại cpu)")
    ap.add_argument("--dtype", choices=["float32", "bfloat16", "float16"],
                    help="kiểu dữ liệu khi engine=transformers (mặc định để thư viện tự chọn)")
    ap.add_argument("--pipeline-version", default="v1", choices=["v1", "v1.5", "v1.6"],
                    help="v1 = PaddleOCR-VL-0.9B gốc; v1.5 / v1.6 là các bản mới hơn")
    ap.add_argument("--merge-mode", choices=["large", "small", "union"],
                    help="cách xử lý các box layout chồng lấn: large = giữ box lớn, bỏ box nằm trong nó "
                         "(mặc định của thư viện); small = giữ box nhỏ; union = giữ cả hai. "
                         "Trang chữ dọc thường bị 'large' gộp thành 1 block to, thử 'small' / 'union'")
    ap.add_argument("--layout-threshold", type=float, help="ngưỡng score của layout (mặc định của thư viện)")
    ap.add_argument("--max-new-tokens", type=int, help="giới hạn số token sinh ra mỗi block")
    ap.add_argument("--show-content", type=int, default=12,
                    help="số ký tự nội dung in cạnh nhãn trên ảnh (0 = chỉ in nhãn)")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        sys.exit(f"Không thấy ảnh: {image_path}")
    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / "paddleocr_vl" / image_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    device = args.device
    if device is None:
        try:
            import torch
            device = "gpu:0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    try:
        from paddleocr import PaddleOCRVL
    except ImportError:
        sys.exit('Chưa cài paddleocr. Chạy: pip install "paddleocr[doc-parser]"')

    init_kwargs = {"pipeline_version": args.pipeline_version, "device": device}
    if args.engine == "transformers":
        init_kwargs["engine"] = "transformers"
        if args.dtype:
            init_kwargs["engine_config"] = {"dtype": args.dtype}
    if args.layout_threshold is not None:
        init_kwargs["layout_threshold"] = args.layout_threshold
    if args.merge_mode:
        init_kwargs["layout_merge_bboxes_mode"] = args.merge_mode

    print(f"Nạp pipeline: {init_kwargs}")
    t0 = time.perf_counter()
    pipeline = PaddleOCRVL(**init_kwargs)
    print(f"  nạp xong sau {time.perf_counter() - t0:.1f}s")

    predict_kwargs = {}
    if args.max_new_tokens:
        predict_kwargs["max_new_tokens"] = args.max_new_tokens
    t1 = time.perf_counter()
    results = list(pipeline.predict(str(image_path), **predict_kwargs))
    print(f"  nhận diện xong sau {time.perf_counter() - t1:.1f}s ({len(results)} trang kết quả)")
    if not results:
        sys.exit("Pipeline không trả về kết quả.")

    image = Image.open(image_path).convert("RGB")
    for n, result in enumerate(results):
        data = result.json
        data = data.get("res", data)
        suffix = "" if len(results) == 1 else f"_{n + 1}"
        stem = image_path.stem + suffix

        # --- JSON gốc
        (out_dir / f"{stem}_res.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        # --- block kết quả (reading order)
        blocks = sorted(data.get("parsing_res_list", []),
                        key=lambda b: (b.get("block_order") is None, b.get("block_order") or 0))
        block_items, lines = [], []
        for b in blocks:
            order = b.get("block_order")
            label = b.get("block_label", "?")
            bbox = to_int_box(b["block_bbox"])
            content = b.get("block_content") or ""
            tag = f"#{order if order is not None else '-'} {label}"
            block_items.append({"label": label, "bbox": bbox, "tag": tag, "content": content,
                                "polygon": b.get("block_polygon_points")})
            lines.append(f"[{tag}] bbox={bbox} id={b.get('block_id')}\n{content}\n")
        (out_dir / f"{stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        draw_boxes(image, block_items, args.show_content).save(out_dir / f"{stem}_blocks.png")

        # --- box thô của layout
        raw = (data.get("layout_det_res") or {}).get("boxes", [])
        raw_items = [{"label": r["label"], "bbox": to_int_box(r["coordinate"]),
                      "tag": f"#{r.get('order', '-')} {r['label']} {r['score']:.2f}",
                      "polygon": r.get("polygon_points")} for r in raw]
        draw_boxes(image, raw_items, 0).save(out_dir / f"{stem}_layout.png")

        # --- tóm tắt ra console
        print(f"\nTrang {n + 1}: {len(block_items)} block kết quả, {len(raw_items)} box layout thô")
        print(f"{'order':>5}  {'label':<18} {'bbox':<26} {'chars':>5}  nội dung")
        for it in block_items:
            preview = it["content"].replace("\n", " ")[:30]
            print(f"{it['tag'].split()[0][1:]:>5}  {it['label']:<18} {str(it['bbox']):<26} {len(it['content']):>5}  {preview}")

    print(f"\nĐã ghi kết quả vào: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
