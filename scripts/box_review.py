"""Kiểm tra từng box của một ảnh trong PPOCRLabel (nút "Kiểm tra box", external/PPOCRLabel/libs/gk_review.py).

Với ảnh trong `ch/`, mỗi box được đối chiếu bằng scripts/box_check.py (lệch số chữ API/projection, chưa có chữ). Box dính cờ phải được
NGƯỜI bấm "đã check" thì mới coi là xong; box không dính cờ coi như đã check. Khi MỌI box của ảnh đã check thì fileState của ảnh là 1, ngược lại 0.

Tính lại fileState của cả thư mục từ Label.txt + dấu check:  python scripts/box_review.py "<tên sách>" [--dry-run]

Dấu "đã check" của người lưu ở data/<Tên sách>/cache/box_review.json:  {"<số trang>": ["x1,y1,x2,y2,x3,y3,x4,y4", ...]}, khoá theo 4 điểm của box
(cùng khoá với cache chữ trong box), nên box bị dời hoặc vẽ lại thì mất dấu và phải check lại. Số chữ projection lấy từ cache
cache/ocr/row_text/<NNN>.json (box_check.measure_crop); box chưa có trong cache thì đo trực tiếp từ ảnh. Thư mục khác ch không bị kiểm tra.
"""
from __future__ import annotations

import json

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):   # chạy trực tiếp: python scripts/box_review.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "scripts"

from . import box_check, engine_bridge, label_io, library, ocr_cache, ocr_engines as eng, ocr_jobs

REVIEW_FILE = "box_review.json"
CHECKED_FOLDER = "ch"   # chỉ thư mục này có cờ cảnh báo (chữ do Kim Hán Nôm đọc, đối chiếu được với ảnh)


def review_file(book: library.Book):
    return book.cache_dir / REVIEW_FILE


def box_key(points) -> str:
    """Khoá của một box: 4 điểm làm tròn, 'x1,y1,x2,y2,x3,y3,x4,y4'."""
    return ",".join(f"{x},{y}" for x, y in ocr_jobs._pkey(points))


def _read(book: library.Book) -> dict:
    try:
        data = json.loads(review_file(book).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_reviewed(book: library.Book, number: int) -> set[str]:
    """Khoá các box của trang `number` mà người đã bấm 'đã check'."""
    return set(_read(book).get(str(number)) or [])


def save_reviewed(book: library.Book, number: int, keys) -> None:
    """Ghi lại tập box đã check của trang (thay hẳn tập cũ); tập rỗng thì xoá mục của trang."""
    data = _read(book)
    if keys:
        data[str(number)] = sorted(keys)
    else:
        data.pop(str(number), None)
    if data:
        library.write_json_atomic(review_file(book), data)
    elif review_file(book).is_file():
        review_file(book).unlink()


def _cached_measures(book: library.Book, number: int, image) -> dict:
    """{khoá 4 điểm: số đo (proj, check_v)} của các box ch đã đo trong cache chữ; rỗng nếu cache không còn khớp ảnh."""
    try:
        records = ocr_cache.load(book, eng.ROW_TEXT, number, ocr_cache.image_info(image), ocr_jobs._row_text_params()) or []
    except OSError:
        return {}
    out = {}
    for r in records:
        if r.get("folder") == CHECKED_FOLDER and r.get("check_v") == box_check.VERSION and "proj" in r:
            out[box_key(r["points"])] = {k: r[k] for k in ("proj", "check_v")}
    return out


def analyze(image_path, boxes: list[dict]) -> dict:
    """Phân tích các box (đang hiện trên ảnh, kể cả chữ mới sửa) của ảnh `image_path`.

    `boxes`: [{"transcription", "points"}] theo thứ tự trong Label.txt. Trả về
        {"book", "number", "folder", "path", "checked_folder": bool,
         "boxes": [{"index": 0-based, "key", "points", "text", "measure", "reasons", "reviewed", "needs_review", "crop"}]}
    `reasons` rỗng nghĩa là box sạch; `needs_review` = có cờ và người chưa check. `crop`: ảnh BGR của box (chừa vài điểm ảnh) để hiển thị."""
    book, number, folder, path = engine_bridge.locate(image_path)
    checked_folder = folder == CHECKED_FOLDER
    reviewed = load_reviewed(book, number)
    page = eng.load_page_image(path)
    cached = _cached_measures(book, number, path) if checked_folder else {}
    out = []
    for index, b in enumerate(boxes):
        points, text = b["points"], str(b.get("transcription", ""))
        key = box_key(points)
        measure, reasons = None, []
        if checked_folder:
            measure = cached.get(key) or box_check.measure_crop(eng.crop_box(page, points, pad=0))
            reasons = box_check.judge(text, measure)["reasons"]
        out.append({"index": index, "key": key, "points": points, "text": text, "measure": measure, "reasons": reasons,
                    "reviewed": key in reviewed, "needs_review": bool(reasons) and key not in reviewed,
                    "crop": eng.crop_box(page, points)})
    return {"book": book, "number": number, "folder": folder, "path": path, "checked_folder": checked_folder, "boxes": out}


def page_checked(analysis: dict) -> bool:
    """Ảnh đã check khi mọi box đều đã check (box sạch hoặc được người bấm check)."""
    return not any(b["needs_review"] for b in analysis["boxes"])


def warnings(box: dict, text: str) -> list[str]:
    """Các dòng cảnh báo hiển thị cho người: ['Số chữ API 8 ≠ số chữ đếm bằng projection 7'] (rỗng nếu không có)."""
    entry = box_check.judge(text, box["measure"]) if box["measure"] is not None else None
    if entry is None:
        return []
    out = []
    for reason in entry["reasons"]:
        if reason == box_check.COUNT_MISMATCH:
            out.append(f"Số chữ API {entry['api']} ≠ số chữ đếm bằng projection {entry['proj']}")
        else:
            out.append(box_check.REASON_LABELS.get(reason, reason).capitalize())
    return out


def mark_page(analysis: dict, checked_keys) -> set[str]:
    """Lưu dấu 'đã check' của người cho trang: `checked_keys` là khoá các box người đã tick. Chỉ giữ box có cờ (box sạch không cần dấu).
    Trả về tập khoá đã lưu."""
    flagged = {b["key"] for b in analysis["boxes"] if b["reasons"] or b["reviewed"]}
    keys = {k for k in checked_keys if k in flagged}
    save_reviewed(analysis["book"], analysis["number"], keys)
    return keys


def refresh_states(book: library.Book, folder: str = CHECKED_FOLDER, dry_run: bool = False) -> dict:
    """Tính lại fileState của mọi ảnh có dòng trong <sách>/<folder>/Label.txt: 1 nếu mọi box đã check (sạch hoặc người đã bấm check), ngược lại 0.

    Label.txt không đổi; fileState.txt ghi lại (ảnh không có trong Label.txt giữ nguyên). Trả về {khoá ảnh: (trạng thái, số box chưa check)}."""
    folder_dir = book.dir / folder
    labels, states = label_io.read_labels(folder_dir / label_io.LABEL_NAME), label_io.read_states(folder_dir / label_io.STATE_NAME)
    result = {}
    for key, payload in labels.items():
        image = folder_dir / key.split("/", 1)[-1]
        try:
            analysis = analyze(image, json.loads(payload))
        except (OSError, ValueError, eng.EngineError):
            continue   # ảnh mất hoặc dòng hỏng: không đoán
        pending = sum(b["needs_review"] for b in analysis["boxes"])
        result[key] = (0 if pending else 1, pending)
    if result and not dry_run:
        label_io.write_states(folder_dir / label_io.STATE_NAME, {**states, **{k: v[0] for k, v in result.items()}})
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tính lại fileState.txt của một sách từ Label.txt và dấu 'đã check' (cache/box_review.json).")
    p.add_argument("book", help="tên (hoặc phần đầu tên) sách trong data/")
    p.add_argument("--folder", default=CHECKED_FOLDER)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from .import_kimhannom_csv import find_book
    book = find_book(args.book, library.DEFAULT_DATA_DIR)
    result = refresh_states(book, args.folder, args.dry_run)
    checked = sum(1 for s, _ in result.values() if s)
    print(f"{book.dir.name}/{args.folder}: {len(result)} ảnh, {checked} ảnh đã check, {len(result) - checked} ảnh chưa check "
          f"({sum(n for _, n in result.values())} box chưa check)" + (" (chạy thử, chưa ghi)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
