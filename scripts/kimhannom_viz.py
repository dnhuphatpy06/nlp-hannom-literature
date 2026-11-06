"""OCR một ảnh bằng API Kim Hán Nôm, ghi kết quả ra file và vẽ bounding box để kiểm tra chất lượng.

Dùng chung cấu hình .env (TOKEN, BASE_URL) và bộ gọi API của dự án (scripts/api/sinonom_service.py).
Không cần GPU, không cần tải model; ảnh được upload lên server Kim Hán Nôm (ảnh trùng nội dung đã
upload trước đó thì dùng lại, xem data/cache/uploads.json).

    python scripts/kimhannom_viz.py anh.png
    python scripts/kimhannom_viz.py anh.png --ocr-id 1 --lang-type 1 --show-text 8

Đầu ra trong <out-dir> (mặc định output/kimhannom/<tên ảnh>/):
    <tên>_boxes.png    ảnh vẽ box từng dòng/cột chữ; màu theo độ tin cậy (xanh >= 0.9, vàng 0.7-0.9, đỏ < 0.7)
    <tên>_res.json     toàn bộ `data` server trả về
    <tên>.txt          các dòng chữ theo thứ tự server trả về: số thứ tự, score, bbox, nội dung
    <tên>_Label.txt    một dòng theo định dạng PPOCRLabel (đường dẫn ảnh TAB mảng transcription/points),
                       dùng được làm nhãn khởi đầu rồi sửa tay

Lưu ý: một số ký tự Nôm chưa có mã Unicode chính thức được server trả về bằng mã vùng private use, và
một số chữ hiếm nằm ngoài BMP; font trên ảnh có thể hiện ô vuông, nhưng file .txt / .json vẫn giữ đúng mã.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # paddleocr_vl_viz (dùng lại hàm vẽ)
sys.path.insert(0, str(HERE / "api"))    # sinonom_service, sinonom_api

from PIL import Image  # noqa: E402

from paddleocr_vl_viz import draw_boxes  # noqa: E402

HIGH, MID = 0.9, 0.7
BUCKET_COLORS = {"high": (30, 150, 60), "mid": (235, 140, 0), "low": (210, 30, 30), "no_score": (90, 90, 200)}


def bucket(score: float | None) -> str:
    if score is None:
        return "no_score"
    return "high" if score >= HIGH else "mid" if score >= MID else "low"


def bbox_of(points) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]


def parse_lines(data: dict) -> list[dict]:
    """Gộp result_bbox ([[4 điểm], [chữ, score]]) với details.details (PPOCRLabel) nếu có."""
    lines = []
    for item in data.get("result_bbox") or []:
        points, (text, score) = item[0], item[1]
        lines.append({"points": [[int(round(x)), int(round(y))] for x, y in points],
                      "text": text, "score": float(score)})
    if not lines:  # phòng khi server chỉ trả details
        for d in (data.get("details") or {}).get("details", []):
            lines.append({"points": d["points"], "text": d["transcription"], "score": None})
    return lines


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="đường dẫn ảnh cần OCR")
    ap.add_argument("--out-dir", help="thư mục ghi kết quả (mặc định output/kimhannom/<tên ảnh>)")
    ap.add_argument("--ocr-id", type=int, default=2, choices=[1, 2, 3, 4],
                    help="1 thông thường dọc, 2 hành chính (mặc định), 3 ngoại cảnh, 4 thông thường ngang")
    ap.add_argument("--lang-type", type=int, default=2, choices=[0, 1, 2], help="0 chưa biết, 1 Hán, 2 Nôm (mặc định)")
    ap.add_argument("--epitaph", type=int, default=0, choices=[0, 1], help="0 văn bản thường (mặc định), 1 văn bia")
    ap.add_argument("--reading-direction", type=int, choices=[1, 2], help="1 dọc, 2 ngang (mặc định để server tự chọn)")
    ap.add_argument("--show-text", type=int, default=0, help="số ký tự nội dung in cạnh nhãn trên ảnh (0 = chỉ số thứ tự + score)")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        sys.exit(f"Không thấy ảnh: {image_path}")
    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / "kimhannom" / image_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    import sinonom_service  # sau khi đã thêm scripts/api vào sys.path

    params = {"ocr_id": args.ocr_id, "lang_type": args.lang_type, "epitaph": args.epitaph}
    if args.reading_direction:
        params["reading_direction"] = args.reading_direction
    print(f"Gọi API Kim Hán Nôm: {params}")
    try:
        data = sinonom_service.ocr(image_path, **params)
    except sinonom_service.ApiError as e:
        sys.exit(f"Lỗi API: {e}")

    lines = parse_lines(data)
    stem = image_path.stem
    (out_dir / f"{stem}_res.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    items, txt = [], []
    for i, ln in enumerate(lines, 1):
        score = ln["score"]
        tag = f"{i}" + (f" {score:.2f}" if score is not None else "")
        items.append({"label": bucket(score), "bbox": bbox_of(ln["points"]), "polygon": ln["points"],
                      "tag": tag, "content": ln["text"], "color": BUCKET_COLORS[bucket(score)]})
        txt.append(f"[{tag}] bbox={bbox_of(ln['points'])}\n{ln['text']}\n")
    (out_dir / f"{stem}.txt").write_text("\n".join(txt), encoding="utf-8")

    label = json.dumps([{"transcription": ln["text"], "points": ln["points"]} for ln in lines], ensure_ascii=False)
    (out_dir / f"{stem}_Label.txt").write_text(f"{image_path.name}\t{label}\n", encoding="utf-8")

    image = Image.open(image_path).convert("RGB")
    draw_boxes(image, items, args.show_text).save(out_dir / f"{stem}_boxes.png")

    scored = [ln["score"] for ln in lines if ln["score"] is not None]
    print(f"\n{len(lines)} dòng/cột chữ"
          + (f", score TB {sum(scored) / len(scored):.3f}, thấp nhất {min(scored):.3f}" if scored else ""))
    if scored:
        counts = {b: sum(1 for s in scored if bucket(s) == b) for b in ("high", "mid", "low")}
        print(f"  xanh (>= {HIGH}): {counts['high']} | vàng ({MID}-{HIGH}): {counts['mid']} | đỏ (< {MID}): {counts['low']}")
    print(f"{'#':>3}  {'score':>5}  {'bbox':<26} nội dung")
    for it, ln in zip(items, lines):
        sc = "-" if ln["score"] is None else f"{ln['score']:.2f}"
        print(f"{it['tag'].split()[0]:>3}  {sc:>5}  {str(it['bbox']):<26} {ln['text'][:30]}")
    print(f"\nĐã ghi kết quả vào: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
