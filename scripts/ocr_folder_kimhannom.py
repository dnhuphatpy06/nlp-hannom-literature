"""Đọc chữ trong mọi ảnh của một thư mục bằng API Kim Hán Nôm, ghi ra file CSV nằm CÙNG thư mục với ảnh.

    python scripts/ocr_folder_kimhannom.py img/tran_vong_tuong_si_van/ch
    python scripts/ocr_folder_kimhannom.py img/tran_vong_tuong_si_van/ch --out ket_qua.csv --ocr-id 1 --reading-direction 1

CSV (UTF-8 có BOM, mở được bằng Excel) có 4 cột:
    name        tên file ảnh
    source_ocr  nguồn OCR, luôn là "kimhannom"
    text        chuỗi chữ API trả về (các dòng nối liền nhau)
    num_chars   số chữ ĐẾM TỪ ẢNH bằng chiếu dọc (scripts/char_projection.py, cùng nguyên lý với row_projection), không phụ thuộc API;
                dùng để đối chiếu với len(text): lệch nhiều thường là API đọc thừa/thiếu chữ

Cần TOKEN trong .env (xem README). Giữa hai request cách nhau ít nhất MIN_REQUEST_GAP giây (scripts/api/sinonom_api.py).
Chạy lại cùng lệnh thì bỏ qua ảnh đã có trong CSV (dùng --redo để đọc lại tất cả); ảnh lỗi không được ghi nên lần chạy sau sẽ thử lại.
Tham số mặc định giống lúc đọc một box đã cắt trong dự án: hàng ngang (ocr_id 4, reading_direction 2), chữ Nôm (lang_type 2).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "api"))

import char_projection  # noqa: E402
import sinonom_service  # noqa: E402

SOURCE = "kimhannom"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
FIELDS = ["name", "source_ocr", "text", "num_chars"]
AUTH_ERROR_CODES = {"000401", "000403"}   # TOKEN sai / hết hạn
MAX_AUTH_FAIL_STREAK = 3


def natural_key(path: Path):
    """Sắp xếp theo số: 2.png trước 10.png."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def count_chars(image: Path) -> str:
    """Số chữ đếm từ ảnh; '' nếu không đọc được ảnh."""
    try:
        return str(char_projection.count_chars(image))
    except Exception as e:   # ảnh hỏng không làm dừng cả lượt OCR
        print(f"{image.name}: không đếm được số chữ ({e})", file=sys.stderr)
        return ""


def load_rows(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.DictReader(f) if row.get("name")]


def write_rows(csv_path: Path, rows: list[dict]) -> None:
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(csv_path)


def backfill_num_chars(csv_path: Path, folder: Path) -> int:
    """Điền cột num_chars cho các dòng cũ chưa có (file tạo trước khi có cột này), chỉ xử lý ảnh, không gọi API. Trả về số dòng đã điền."""
    rows = load_rows(csv_path)
    filled = 0
    for row in rows:
        if not str(row.get("num_chars") or "").strip() and (folder / row["name"]).is_file():
            row["num_chars"] = count_chars(folder / row["name"])
            filled += bool(row["num_chars"])
    if filled:
        write_rows(csv_path, rows)
    return filled


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="OCR mọi ảnh trong một thư mục bằng API Kim Hán Nôm, ghi CSV cùng thư mục.")
    p.add_argument("folder", type=Path, help="thư mục chứa ảnh")
    p.add_argument("--out", default="ocr_kimhannom.csv", help="tên file CSV, đặt trong thư mục ảnh (mặc định ocr_kimhannom.csv)")
    p.add_argument("--ocr-id", type=int, default=4, choices=[1, 2, 3, 4], help="1 thường dọc, 2 hành chính, 3 ngoại cảnh, 4 thường ngang (mặc định 4)")
    p.add_argument("--lang-type", type=int, default=2, choices=[0, 1, 2], help="0 chưa biết, 1 Hán, 2 Nôm (mặc định 2)")
    p.add_argument("--epitaph", type=int, default=0, choices=[0, 1], help="0 văn bản thường, 1 văn bia (mặc định 0)")
    p.add_argument("--reading-direction", type=int, default=2, choices=[1, 2], help="1 dọc, 2 ngang (mặc định 2)")
    p.add_argument("--tag", default=None, help="model_id được cấp riêng (mặc định: mô hình mặc định)")
    p.add_argument("--sep", default="", help="chuỗi nối các dòng API trả về (mặc định rỗng: nối liền)")
    p.add_argument("--redo", action="store_true", help="đọc lại cả ảnh đã có trong CSV (ghi đè file)")
    p.add_argument("--uploads-file", type=Path, default=Path(tempfile.gettempdir()) / "gk_kimhannom_uploads.json",
                   help="file nhớ ảnh đã upload (ảnh trùng nội dung khỏi upload lại)")
    args = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):   # console Windows mặc định cp1252, không in được tiếng Việt/chữ Hán
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    folder = args.folder
    if not folder.is_dir():
        raise SystemExit(f"Không thấy thư mục {folder}")
    images = sorted((f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES), key=natural_key)
    if not images:
        raise SystemExit(f"Không có ảnh nào trong {folder}")

    csv_path = folder / args.out
    if not args.redo and csv_path.is_file():
        filled = backfill_num_chars(csv_path, folder)
        if filled:
            print(f"Đã điền num_chars cho {filled} dòng cũ trong {csv_path.name}")
    done = set() if args.redo else {row["name"] for row in load_rows(csv_path)}
    todo = [f for f in images if f.name not in done]
    print(f"{len(images)} ảnh, đã có {len(images) - len(todo)}, cần đọc {len(todo)} -> {csv_path}")

    params = {"ocr_id": args.ocr_id, "lang_type": args.lang_type, "epitaph": args.epitaph,
              "tag": args.tag, "reading_direction": args.reading_direction}
    new_file = args.redo or not csv_path.is_file()
    ok, failed, auth_streak = 0, [], 0
    with open(csv_path, "w" if new_file else "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for i, image in enumerate(todo, 1):
            try:
                data = sinonom_service.ocr(image, uploads_file=args.uploads_file, **params) or {}
            except sinonom_service.ApiError as e:
                failed.append(image.name)
                print(f"[{i}/{len(todo)}] {image.name}: LỖI {e}", file=sys.stderr)
                auth_streak = auth_streak + 1 if e.code in AUTH_ERROR_CODES else 0
                if auth_streak >= MAX_AUTH_FAIL_STREAK:
                    print(f"Dừng vì {auth_streak} ảnh liên tiếp lỗi xác thực: kiểm tra TOKEN trong .env hoặc đợi một lúc rồi chạy lại.",
                          file=sys.stderr)
                    break
                continue
            auth_streak = 0
            lines = [str(t).strip() for t in (data.get("result_ocr_text") or []) if str(t).strip()]
            text = args.sep.join(lines)
            num_chars = count_chars(image)
            writer.writerow({"name": image.name, "source_ocr": SOURCE, "text": text, "num_chars": num_chars})
            f.flush()   # dừng giữa chừng vẫn giữ phần đã đọc
            ok += 1
            print(f"[{i}/{len(todo)}] {image.name}: {text}  (api {len(text)} / ảnh {num_chars or '-'})")

    print(f"Xong: {ok} ảnh đọc được, {len(failed)} lỗi" + (f" ({', '.join(failed[:5])}{'…' if len(failed) > 5 else ''})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
