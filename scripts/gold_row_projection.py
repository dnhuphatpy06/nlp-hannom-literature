"""Chạy row_projection (chỉ lấy box, chữ để trống) cho mọi ảnh trong gold_dataset/raw/ rồi ghi Label.txt + fileState.txt ở đó.

    .venv\Scripts\python.exe scripts/gold_row_projection.py [--overwrite] [--no-cache]

Cache dùng chung với sách gốc (engine_bridge.locate tra ảnh về data/<Tên sách>/cache/ocr/row_projection/<NNN>.json), nên ảnh đã chạy
trên web thì lấy từ cache, ảnh mới chạy xong cũng có cache cho web. Mở bằng open_ppocrlabel.bat --image_dir gold_dataset\raw.
Ảnh đã có dòng trong Label.txt được giữ nguyên (đã có thể sửa tay) trừ khi --overwrite.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from scripts import engine_bridge, label_io  # noqa: E402

GOLD = ROOT / "gold_dataset" / "raw"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--overwrite", action="store_true", help="ghi đè ảnh đã có dòng trong Label.txt")
    ap.add_argument("--no-cache", action="store_true", help="bỏ qua cache, chạy lại row_projection")
    args = ap.parse_args()
    existing = label_io.read_labels(GOLD / label_io.LABEL_NAME)
    images = sorted(GOLD.glob("*.jpg"))
    new, failed = {}, []
    for i, image in enumerate(images, 1):
        key = label_io.image_key(GOLD.name, image.name)
        if key in existing and not args.overwrite:
            print(f"[{i}/{len(images)}] giữ nguyên {image.name}")
            continue
        try:
            boxes, _, cached = engine_bridge.recognize_page(image, "row_projection+none", use_cache=not args.no_cache)
        except Exception as e:
            failed.append(image.name)
            print(f"[{i}/{len(images)}] LỖI {image.name}: {e}")
            continue
        new[key] = [label_io.make_box(b["points"], "") for b in boxes]
        print(f"[{i}/{len(images)}] {image.name}: {len(boxes)} box{' (cache)' if cached else ''}")
    if new:
        label_io.update_folder(GOLD, new, state=0)
    print(f"Xong: {len(new)} ảnh ghi vào {GOLD / label_io.LABEL_NAME}" + (f", lỗi {len(failed)}: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
