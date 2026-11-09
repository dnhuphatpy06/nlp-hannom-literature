"""Nhập kết quả đọc Kim Hán Nôm đã có (CSV của scripts/ocr_folder_kimhannom.py) vào cache chữ trong box của sách, để bước OCR không gọi lại API.

    python scripts/import_kimhannom_csv.py img/tran_vong_tuong_si_van/ch
    python scripts/import_kimhannom_csv.py img/tran_vong_tuong_si_van/ch --book "Tran vong" --dry-run

Thư mục ảnh cần có hai file cùng thư mục:
    manifest.csv         file, page, box, x1, y1, x2, y2, ...  (vị trí mỗi ảnh cắt trong ảnh TRANG: toạ độ 4 góc của box)
    ocr_kimhannom.csv    name, source_ocr, text, num_chars     (chữ API đọc được trong ảnh cắt đó)

Mỗi dòng thành một bản ghi `ch` trong data/<Tên sách>/cache/ocr/row_text/<NNN>.json, khoá theo 4 toạ độ box (ocr_jobs.row_text_pass): lần OCR sau,
box nào ra đúng 4 toạ độ này thì lấy chữ từ cache. Bản ghi cũng lưu số chữ đếm bằng projection của box (box_check.measure_crop).
Bản ghi `ok` đã có trong cache được giữ nguyên (trừ khi --force). Cache gắn với ảnh trang bằng sha256 nên dùng ảnh trong data/<Tên sách>/ch/
(đúng ảnh bước OCR đọc); ảnh trang không còn khớp với cache cũ thì file cache của trang đó được viết lại.

Cùng lúc điền chữ vào data/<Tên sách>/<thư mục>/Label.txt: box có 4 điểm trùng toạ độ trong manifest và đang RỖNG được điền chữ (box đã có chữ, kể cả
sửa tay, giữ nguyên; các trường khác như "difficult" giữ nguyên). fileState của ảnh: 1 nếu mọi box đều có chữ và không box nào dính cờ của box_check
(lệch số chữ), ngược lại 0 để người xem lại. Label.txt và fileState.txt được sao lưu sang *.bak (nếu chưa có) trước khi ghi.
--no-label: chỉ nhập cache.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import json
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import box_check, label_io, library, ocr_cache, ocr_engines as eng, ocr_jobs  # noqa: E402


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def slug(name: str) -> str:
    """'Tran vong tuong si van digcoll_13711_Y' -> 'tran_vong_tuong_si_van_digcoll_13711_y' (bỏ dấu, chữ thường, mọi ký tự lạ thành '_')."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def find_book(hint: str, data_dir: Path) -> library.Book:
    """Sách trong data/ mà slug tên bắt đầu bằng (hoặc chứa) slug của `hint`; phải ra đúng một sách."""
    want = slug(hint)
    books = library.list_books(data_dir)
    matches = [b for b in books if slug(b.dir.name).startswith(want)] or [b for b in books if want in slug(b.dir.name)]
    if len(matches) != 1:
        names = ", ".join(b.dir.name for b in (matches or books))
        raise SystemExit(f"Không xác định được sách cho {hint!r} ({'có nhiều sách khớp' if matches else 'không sách nào khớp'}): {names}. Dùng --book.")
    return matches[0]


def box_points(row: dict) -> list[list[int]]:
    x1, y1, x2, y2 = (int(row[k]) for k in ("x1", "y1", "x2", "y2"))
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def fill_labels(book: library.Book, folder: str, page_records: dict, dry_run: bool) -> tuple[int, int, int, int]:
    """Điền chữ vào các box RỖNG của <sách>/<folder>/Label.txt có 4 điểm trùng bản ghi, đặt fileState (xem đầu file).

    Trả về (số box đã điền, số ảnh fileState 1, số ảnh fileState 0, số box dính cờ trong các box vừa điền)."""
    folder_dir = book.dir / folder
    label_file, state_file = folder_dir / label_io.LABEL_NAME, folder_dir / label_io.STATE_NAME
    labels, states = label_io.read_labels(label_file), label_io.read_states(state_file)
    by_name = {library.norm(k): k for k in labels}
    filled = checked = unchecked = flagged = 0
    new_labels, new_states = dict(labels), dict(states)
    for number, (image, wanted) in sorted(page_records.items()):
        key = by_name.get(library.norm(label_io.image_key(folder, image.name)))
        if key is None:
            print(f"Trang {number:03d}: chưa có dòng trong {folder}/Label.txt, bỏ qua (chạy OCR phát hiện box trước)")
            continue
        try:
            boxes = json.loads(labels[key])
        except ValueError:
            print(f"Trang {number:03d}: dòng Label.txt hỏng, bỏ qua")
            continue
        page_filled = page_flagged = 0
        for b in boxes:
            if str(b.get("transcription", "")).strip():
                continue   # đã có chữ (kể cả sửa tay): giữ nguyên
            rec = wanted.get(ocr_jobs._pkey(b["points"]))
            if rec is None:
                continue
            b["transcription"] = ocr_jobs.clean_text(rec["text"], han=True)
            page_filled += 1
            page_flagged += bool(box_check.judge(b["transcription"], rec)["reasons"])
        if not page_filled:
            continue
        filled += page_filled
        flagged += page_flagged
        clean = not page_flagged and all(str(b.get("transcription", "")).strip() for b in boxes)
        new_labels[key] = json.dumps(boxes, ensure_ascii=False)   # định dạng của PPOCRLabel, giữ các trường khác (difficult)
        new_states[key] = 1 if clean and ocr_jobs.AUTO_CHECK else 0
        checked, unchecked = checked + new_states[key], unchecked + (1 - new_states[key])
    if filled and not dry_run:
        for f in (label_file, state_file):
            if f.is_file() and not f.with_name(f.name + ".bak").exists():
                shutil.copy2(f, f.with_name(f.name + ".bak"))
        label_io.write_labels(label_file, new_labels)
        label_io.write_states(state_file, new_states)
    return filled, checked, unchecked, flagged


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("folder", type=Path, help="thư mục ảnh cắt, có manifest.csv và file CSV kết quả OCR")
    p.add_argument("--book", help="tên (hoặc phần đầu tên) sách trong data/; mặc định lấy theo tên thư mục cha của ảnh")
    p.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="thư mục dữ liệu (mặc định data/)")
    p.add_argument("--csv", default="ocr_kimhannom.csv", help="tên file CSV kết quả OCR (mặc định ocr_kimhannom.csv)")
    p.add_argument("--manifest", default="manifest.csv")
    p.add_argument("--target", default="ch", help="thư mục của sách nhận bản ghi (mặc định ch)")
    p.add_argument("--force", action="store_true", help="ghi đè cả bản ghi đã đọc được (status ok) trong cache")
    p.add_argument("--no-label", action="store_true", help="không điền chữ vào Label.txt (mặc định điền các box rỗng khớp toạ độ)")
    p.add_argument("--dry-run", action="store_true", help="chỉ báo cáo, không ghi cache và Label.txt")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    folder = args.folder.resolve()
    manifest, texts = read_csv(folder / args.manifest), {r["name"]: r for r in read_csv(folder / args.csv) if r.get("name")}
    book = find_book(args.book or folder.parent.name, args.data)
    print(f"Sách: {book.dir.name}")

    by_page: dict[int, list[dict]] = defaultdict(list)
    missing = []
    for row in manifest:
        if row["file"] in texts:
            by_page[int(row["page"])].append(row)
        else:
            missing.append(row["file"])

    params = ocr_jobs._row_text_params()
    imported = kept = empty = unusable = pages_done = 0
    page_records: dict[int, tuple[Path, dict]] = {}   # trang -> (ảnh, {khoá 4 điểm: bản ghi}) của các box có chữ trong manifest, để điền Label.txt
    for number, rows in sorted(by_page.items()):
        image = library.find_page_image(book.dir / args.target, book.stem, number)
        if image is None:
            unusable += len(rows)
            print(f"Trang {number:03d}: không thấy ảnh trong {args.target}/ (bỏ qua {len(rows)} box)")
            continue
        info = ocr_cache.image_info(image)
        with Image.open(image) as im:
            size = im.size
        # bản ghi hiện có của đúng ảnh này (cả thư mục vi, để ghi lại đủ); None khi ảnh/tham số không còn khớp
        raw = ocr_cache.load(book, eng.ROW_TEXT, number, info, params) or []
        records = {(b["folder"], ocr_jobs._pkey(eng.clamp_points(b["points"], *size))): b for b in raw if "folder" in b and "points" in b}
        page = eng.load_page_image(image)
        dirty = False
        wanted: dict[tuple, dict] = {}
        for row in sorted(rows, key=lambda r: int(r["box"])):
            text = (texts[row["file"]].get("text") or "").strip()
            points = eng.clamp_points(box_points(row), *size)
            key = (args.target, ocr_jobs._pkey(points))
            if not text:
                empty += 1
                continue
            if key in records and records[key].get("status") == "ok" and not args.force:
                kept += 1
            else:
                records[key] = {"folder": args.target, "text": text, "points": points, "status": "ok", "source": "import"}
                imported += 1
                dirty = True
            rec = records[key]
            if rec.get("check_v") != box_check.VERSION or "proj" not in rec:   # số chữ projection của box, cache cùng bản ghi chữ
                rec.pop("two_inks", None)   # cờ 2 màu mực đã bỏ: dọn trường của cache cũ
                rec.update(box_check.measure_crop(eng.crop_box(page, points, pad=0)))
                dirty = True
            wanted[key[1]] = rec
        page_records[number] = (image, wanted)
        if dirty and not args.dry_run:
            ocr_cache.save(book, eng.ROW_TEXT, number, info, params, list(records.values()))
            pages_done += 1

    filled = checked = unchecked = flagged = 0
    if not args.no_label and page_records:
        filled, checked, unchecked, flagged = fill_labels(book, args.target, page_records, args.dry_run)

    note = " (chạy thử, chưa ghi gì)" if args.dry_run else ""
    print(f"Đã nhập {imported} box vào cache row_text ({pages_done} trang){note}; giữ {kept} bản ghi đã có, bỏ qua {empty} dòng rỗng"
          + (f", {unusable} box không có ảnh trang" if unusable else "") + (f", {len(missing)} ảnh trong manifest không có dòng CSV" if missing else "") + ".")
    if not args.no_label:
        print(f"Label.txt{note}: điền chữ vào {filled} box; {checked} ảnh fileState 1 (mọi box sạch), {unchecked} ảnh fileState 0 "
              f"(cần xem lại; {flagged} box dính cờ lệch số chữ).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
