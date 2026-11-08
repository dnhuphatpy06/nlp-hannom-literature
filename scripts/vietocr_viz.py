"""Đọc chữ trong các bounding box của MỘT ảnh trong thư mục vi/ bằng VietOCR (bản fine-tune chữ viết tay), rồi vẽ kết quả để
kiểm tra chất lượng. Chỉ nhận dạng chữ trong box đã có: không tìm box mới, toạ độ lấy từ Label.txt (do row_projection ghi).

Script độc lập (chỉ cần Pillow, torch, vietocr; không import code của dự án) để chạy trong môi trường riêng, vì `pip install vietocr`
ghim Pillow 10.2.0, albumentations, imgaug... sẽ xung đột với môi trường chính của dự án. Hướng dẫn cài đặt:

    python -m venv C:\\vocr                                   # đường dẫn NGẮN, tránh lỗi đường dẫn dài trên Windows
    C:\\vocr\\Scripts\\activate
    pip install torch torchvision                             # bản CPU hoặc CUDA đúng máy: https://pytorch.org/get-started/locally/
    pip install vietocr huggingface_hub
    python scripts/vietocr_viz.py "data/<Tên sách>/vi/<Tên sách>.008.jpg"

Trọng số (~150 MB) tải từ Hugging Face (DungHugging/vietocr-handwritten-finetune, file transformerocr.pth) ở lần chạy đầu và
được cache. Muốn tự tải trước rồi chỉ đường dẫn: --weights đường/dẫn/transformerocr.pth. Có thể phải tải thêm file cấu hình nhỏ
(vgg-transformer.yml) của thư viện vietocr ở lần chạy đầu.

Đầu ra trong <out-dir> (mặc định output/vietocr/<tên ảnh>/):
    <tên>_boxes.png   ảnh trang: vẽ từng box (đánh số) kèm chữ VietOCR nhận được ngay trên box
    <tên>_lines.png   từng box cắt ra, bên dưới là chữ VietOCR (và chữ đang có trong Label.txt, và chữ chuẩn nếu có --ref)
    <tên>.json        kết quả từng box (toạ độ, chữ, CER nếu có --ref) và tóm tắt
    <tên>.txt         mỗi dòng một box theo thứ tự trong Label.txt

Đo chất lượng: tạo file chữ chuẩn (UTF-8, mỗi dòng là chữ đúng của một box, theo thứ tự box trong Label.txt) rồi chạy với
--ref chu_chuan.txt: in CER/WER từng box và tổng (có dấu và bỏ dấu), vẽ lên ảnh.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
HF_REPO, HF_FILE = "DungHugging/vietocr-handwritten-finetune", "transformerocr.pth"
CROP_PAD = 4   # điểm ảnh chừa quanh box khi cắt, giống bước OCR của app (ocr_engines.CROP_PAD)
PALETTE = [(220, 38, 38), (37, 99, 235), (22, 163, 74), (217, 119, 6), (147, 51, 234), (8, 145, 178), (219, 39, 119), (101, 163, 13)]
FONT_CANDIDATES = [   # cần font có đủ dấu tiếng Việt
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/times.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


# ---------------------------------------------------------------- đọc Label.txt
def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def resolve_path(path: Path) -> Path:
    """Đường dẫn thật trên đĩa dù bạn gõ dấu tiếng Việt khác dạng chuẩn hoá Unicode với tên file (NFC dựng sẵn so với NFD tổ hợp).

    Tên sách của dự án có thể nằm ở dạng NFD trên đĩa trong khi bàn phím/terminal gõ ra NFC nên `is_file()` báo không thấy. Mỗi thành
    phần của đường dẫn được khớp theo tên đã chuẩn hoá NFC (không phân biệt hoa thường); không khớp được thì trả lại đường dẫn gốc."""
    if path.exists():
        return path
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        candidate = current / part
        if not candidate.exists():
            try:
                match = next((c for c in current.iterdir() if nfc(c.name).casefold() == nfc(part).casefold()), None)
            except OSError:
                match = None
            if match is None:
                return path
            candidate = match
        current = candidate
    return current


def read_boxes(label_file: Path, image: Path) -> list[dict]:
    """Các box [{points, text}] của ảnh trong Label.txt (dòng: <thư mục>/<tên ảnh> TAB mảng JSON)."""
    if not label_file.is_file():
        raise SystemExit(f"Không thấy {label_file}. Dùng --label để chỉ đường dẫn Label.txt.")
    wanted = {nfc(f"{image.parent.name}/{image.name}"), nfc(image.name)}
    keys = []
    for line in label_file.read_text(encoding="utf-8").splitlines():
        key, sep, payload = line.partition("\t")
        if not sep:
            continue
        keys.append(key)
        if nfc(key) in wanted or nfc(key.rsplit("/", 1)[-1]) == nfc(image.name):
            boxes = json.loads(payload)
            return [{"points": b["points"], "text": b.get("transcription", "")} for b in boxes]
    raise SystemExit(f"Ảnh {image.name} chưa có dòng nào trong {label_file}. Có {len(keys)} ảnh, ví dụ: {keys[:3]}")


def crop_box(page: Image.Image, points, pad: int = CROP_PAD) -> Image.Image | None:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x1, x2 = max(int(min(xs)) - pad, 0), min(int(max(xs)) + pad, page.width)
    y1, y2 = max(int(min(ys)) - pad, 0), min(int(max(ys)) + pad, page.height)
    return page.crop((x1, y1, x2, y2)) if x2 > x1 and y2 > y1 else None


# ---------------------------------------------------------------- VietOCR
def load_predictor(weights: str | None, device: str, beam: bool, max_width: int | None):
    """Trả về (hàm predict(PIL.Image) -> str, thiết bị). vietocr nạp ở đây (không ở đầu file) để --help chạy được khi chưa cài."""
    try:
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor
    except ImportError as e:
        raise SystemExit(f"Chưa cài vietocr ({e}). Xem hướng dẫn ở đầu file: pip install vietocr huggingface_hub")
    if device == "auto":
        import torch
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    if weights is None:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            raise SystemExit("Chưa cài huggingface_hub (pip install huggingface_hub), hoặc tải sẵn transformerocr.pth rồi dùng --weights.")
        print(f"Tải trọng số {HF_REPO}/{HF_FILE} (lần đầu, sau đó dùng cache)…")
        weights = hf_hub_download(HF_REPO, HF_FILE)
    elif not Path(weights).is_file():
        raise SystemExit(f"Không thấy file trọng số: {weights}")

    config = Cfg.load_config_from_name("vgg_transformer")
    config["weights"] = str(weights)
    config["cnn"]["pretrained"] = False          # trọng số đầy đủ đã có trong transformerocr.pth, không tải lại backbone VGG
    config["device"] = device
    config["predictor"]["beamsearch"] = beam
    if max_width:                                # VietOCR ép ảnh về cao 32 px, rộng tối đa image_max_width (mặc định 512): ảnh quá dài bị nén ngang
        config["dataset"]["image_max_width"] = max_width
    print(f"VietOCR vgg_transformer · thiết bị: {device} · beam search: {beam} · image_max_width: {config['dataset'].get('image_max_width')}")
    return Predictor(config).predict, device


# ---------------------------------------------------------------- đo lỗi
def _edit_distance(a, b) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def _strip_marks(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return unicodedata.normalize("NFC", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def _norm(s: str) -> str:
    return " ".join(nfc(s).lower().split())


def error_rates(ref: str, hyp: str) -> dict:
    """CER/WER (không phân biệt hoa thường). `*_nodau`: bỏ dấu cả hai vế, để tách lỗi chữ cái khỏi lỗi dấu thanh/dấu mũ."""
    r, h = _norm(ref), _norm(hyp)
    rb, hb = _strip_marks(r), _strip_marks(h)
    return {"cer": _edit_distance(r, h) / max(1, len(r)), "wer": _edit_distance(r.split(), h.split()) / max(1, len(r.split())),
            "cer_nodau": _edit_distance(rb, hb) / max(1, len(rb)), "wer_nodau": _edit_distance(rb.split(), hb.split()) / max(1, len(rb.split())),
            "_ed": _edit_distance(r, h), "_len": len(r), "_edb": _edit_distance(rb, hb), "_lenb": len(rb),
            "_wed": _edit_distance(r.split(), h.split()), "_wlen": len(r.split()),
            "_wedb": _edit_distance(rb.split(), hb.split()), "_wlenb": len(rb.split())}


def summarize(rates: list[dict]) -> dict:
    """Tỉ lệ lỗi gộp theo tổng số ký tự/từ (không phải trung bình các box)."""
    if not rates:
        return {}
    s = lambda k: sum(r[k] for r in rates)  # noqa: E731
    return {"boxes": len(rates), "cer": s("_ed") / max(1, s("_len")), "wer": s("_wed") / max(1, s("_wlen")),
            "cer_nodau": s("_edb") / max(1, s("_lenb")), "wer_nodau": s("_wedb") / max(1, s("_wlenb")),
            "exact": sum(r["_ed"] == 0 for r in rates)}


# ---------------------------------------------------------------- vẽ
def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    print("Cảnh báo: không tìm thấy font có dấu tiếng Việt, chữ trên ảnh có thể lỗi. Sửa FONT_CANDIDATES.", file=sys.stderr)
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    x1, y1, x2, y2 = draw.textbbox((0, 0), text, font=font)
    return x2 - x1, y2 - y1


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and text_size(draw, trial, font)[0] > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur or not lines else lines


def draw_boxes(page: Image.Image, items: list[dict]) -> Image.Image:
    base = page.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    width = max(2, base.width // 500)
    font = load_font(max(16, base.width // 75))
    for it in items:
        color = PALETTE[(it["index"] - 1) % len(PALETTE)]
        pts = [tuple(p) for p in it["points"]]
        od.polygon(pts, fill=color + (35,))
        od.line(pts + [pts[0]], fill=color + (255,), width=width)
    for it in items:   # chữ vẽ sau cùng để không bị tô mờ; nền trắng bán trong suốt cho dễ đọc
        color = PALETTE[(it["index"] - 1) % len(PALETTE)]
        label = f"{it['index']}. {it['hyp']}"
        tw, th = text_size(od, label, font)
        xs, ys = [p[0] for p in it["points"]], [p[1] for p in it["points"]]
        x, y = min(xs), min(ys) - th - 8
        if y < 0:
            y = max(ys) + 2
        od.rectangle([x, y, x + tw + 8, y + th + 8], fill=(255, 255, 255, 215))
        od.text((x + 4, y + 2), label, font=font, fill=color + (255,))
    return Image.alpha_composite(base, overlay).convert("RGB")


def draw_lines(items: list[dict], panel_width: int = 1000, name: str = "VietOCR") -> Image.Image:
    font = load_font(20)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    rows = []
    for it in items:
        crop = it["crop"]
        if crop is None:
            continue
        scale = min(panel_width / crop.width, 2.0)   # thu vừa khung; box nhỏ thì phóng tối đa 2 lần
        shown = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
        color = PALETTE[(it["index"] - 1) % len(PALETTE)]
        texts = [(name, it["hyp"], color)]
        if it.get("label_text"):
            texts.append(("Label.txt", it["label_text"], (110, 110, 110)))
        if it.get("ref") is not None:
            r = it["rates"]
            texts.append(("Chuẩn", it["ref"], (22, 130, 60)))
            texts.append(("Lỗi", f"CER {r['cer']:.0%} · WER {r['wer']:.0%} · bỏ dấu: CER {r['cer_nodau']:.0%} · WER {r['wer_nodau']:.0%}", (150, 40, 40)))
        lines = []
        for name, text, c in texts:
            for part in wrap(probe, f"{name}: {text}", font, panel_width - 12):
                lines.append((part, c))
        line_h = text_size(probe, "Ag", font)[1] + 8
        rows.append((it["index"], shown, lines, line_h, color))
    height = sum(shown.height + len(lines) * lh + 34 for _, shown, lines, lh, _ in rows) + 10
    canvas = Image.new("RGB", (panel_width + 20, max(height, 40)), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    y = 8
    for index, shown, lines, lh, color in rows:
        d.text((10, y), f"#{index}", font=font, fill=color)
        y += 26
        canvas.paste(shown, (10, y))
        d.rectangle([10, y, 10 + shown.width, y + shown.height], outline=color, width=2)
        y += shown.height + 4
        for text, c in lines:
            d.text((12, y), text, font=font, fill=c)
            y += lh
        y += 4
    return canvas


# ---------------------------------------------------------------- chạy
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Đọc chữ trong các bounding box của một ảnh vi/ bằng VietOCR (chữ viết tay), vẽ kết quả.")
    p.add_argument("image", type=Path, help="ảnh trong thư mục vi/, ví dụ data/<Tên sách>/vi/<Tên sách>.008.jpg")
    p.add_argument("--label", type=Path, help="Label.txt chứa box (mặc định: Label.txt cùng thư mục với ảnh)")
    p.add_argument("--weights", help="đường dẫn transformerocr.pth đã tải sẵn (mặc định: tải từ Hugging Face, có cache)")
    p.add_argument("--device", default="auto", help="auto | cpu | cuda:0 (mặc định auto)")
    p.add_argument("--beam", action="store_true", help="bật beam search (chậm hơn, đôi khi chính xác hơn)")
    p.add_argument("--max-width", type=int, help="image_max_width của VietOCR (mặc định của thư viện 512): tăng nếu dòng quá dài bị nén ngang")
    p.add_argument("--pad", type=int, default=CROP_PAD, help=f"điểm ảnh chừa quanh box khi cắt (mặc định {CROP_PAD})")
    p.add_argument("--ref", type=Path, help="file chữ chuẩn (UTF-8), mỗi dòng ứng với một box theo thứ tự trong Label.txt; để tính CER/WER")
    p.add_argument("--out-dir", type=Path, help="thư mục đầu ra (mặc định output/vietocr/<tên ảnh>/)")
    return p


def run(args: argparse.Namespace, predict=None, device: str = "?", name: str = "VietOCR") -> dict:
    """`predict`: hàm PIL.Image -> str; None thì nạp VietOCR (tham số này để test vẽ/đo lỗi mà không cần mô hình, và để các script
    mô hình khác như vintern_viz.py dùng lại toàn bộ phần đọc Label.txt, cắt box, vẽ, đo lỗi). `name`: tên mô hình hiện trên ảnh/JSON."""
    image = resolve_path(args.image)
    if not image.is_file():
        raise SystemExit(f"Không thấy ảnh: {args.image}")
    if image.parent.name != "vi":
        print(f"Cảnh báo: ảnh không nằm trong thư mục vi/ (đang ở {image.parent.name}/); vẫn chạy.", file=sys.stderr)
    boxes = read_boxes(resolve_path(args.label) if args.label else image.parent / "Label.txt", image)
    if not boxes:
        raise SystemExit("Ảnh có dòng trong Label.txt nhưng không có box nào.")
    refs = None
    if args.ref:
        ref_file = resolve_path(args.ref)
        if not ref_file.is_file():
            raise SystemExit(f"Không thấy file --ref: {args.ref}")
        refs = [line.rstrip("\r\n") for line in ref_file.read_text(encoding="utf-8").splitlines()]
        if len(refs) != len(boxes):
            print(f"Cảnh báo: --ref có {len(refs)} dòng nhưng ảnh có {len(boxes)} box; chỉ so các dòng có cả hai.", file=sys.stderr)

    if predict is None:
        predict, device = load_predictor(args.weights, args.device, args.beam, args.max_width)
    page = Image.open(image).convert("RGB")

    items, rates, seconds = [], [], 0.0
    for i, b in enumerate(boxes, 1):
        crop = crop_box(page, b["points"], args.pad)
        hyp, took = "", 0.0
        if crop is not None:
            started = time.perf_counter()
            try:
                hyp = predict(crop).strip()
            except Exception as e:   # một box lỗi không làm hỏng cả trang
                hyp = f"[lỗi: {type(e).__name__}: {e}]"
            took = time.perf_counter() - started
        seconds += took
        item = {"index": i, "points": b["points"], "crop": crop, "hyp": hyp, "label_text": b["text"], "seconds": round(took, 3), "ref": None}
        if refs is not None and i <= len(refs):
            item["ref"], item["rates"] = refs[i - 1], error_rates(refs[i - 1], hyp)
            rates.append(item["rates"])
        items.append(item)
        print(f"{i:>2}. {took:5.2f}s  {hyp}")

    out = args.out_dir or ROOT / "output" / name.lower() / image.stem
    out.mkdir(parents=True, exist_ok=True)
    stem = image.stem
    draw_boxes(page, items).save(out / f"{stem}_boxes.png")
    draw_lines(items, name=name).save(out / f"{stem}_lines.png")
    summary = summarize(rates)
    (out / f"{stem}.txt").write_text("\n".join(it["hyp"] for it in items) + "\n", encoding="utf-8")
    result = {"image": str(image), "device": device, "seconds_total": round(seconds, 2),
              "seconds_per_box": round(seconds / len(items), 3), "summary": summary,
              "boxes": [{"index": it["index"], "points": it["points"], name.lower(): it["hyp"], "label_txt": it["label_text"],
                         "ref": it["ref"], **({"cer": round(it["rates"]["cer"], 4), "wer": round(it["rates"]["wer"], 4),
                                              "cer_nodau": round(it["rates"]["cer_nodau"], 4), "wer_nodau": round(it["rates"]["wer_nodau"], 4)}
                                             if it["ref"] is not None else {}), "seconds": it["seconds"]} for it in items]}
    (out / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(items)} box · {seconds:.1f}s ({seconds / len(items):.2f}s/box) · đã ghi vào {out}")
    if summary:
        print(f"CER {summary['cer']:.1%} · WER {summary['wer']:.1%} · bỏ dấu: CER {summary['cer_nodau']:.1%} · WER {summary['wer_nodau']:.1%} "
              f"· đúng hoàn toàn {summary['exact']}/{summary['boxes']} box")
    return result


def main(argv=None) -> None:
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
