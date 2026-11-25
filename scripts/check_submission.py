"""Kiểm tra tính hợp lệ của dữ liệu và gói nộp bài theo Quy_định_đồ_án.pdf.

Chạy:
    python scripts/check_submission.py
    python scripts/check_submission.py --strict-env
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import label_io, library  # noqa: E402

IMG_RE_TEMPLATE = r"^{stem}\.\d{{3}}\.jpg$"
REQUIRED_SUBMISSION_FOLDERS = ("ch", "vi", "intro", "ref", "toc", "blank", "misc")


def check_book(book: library.Book) -> dict:
    """Kiểm tra cấu trúc thư mục, định danh ảnh, Label.txt, fileState.txt và file dóng hàng của một cuốn sách."""
    nf_stem = unicodedata.normalize("NFC", book.stem)
    errors: list[str] = []
    warnings: list[str] = []

    missing_dirs = [f for f in REQUIRED_SUBMISSION_FOLDERS if not (book.dir / f).is_dir()]
    if missing_dirs:
        warnings.append(f"Thiếu thư mục chuẩn: {', '.join(missing_dirs)}")

    img_re = re.compile(IMG_RE_TEMPLATE.format(stem=re.escape(nf_stem)))
    folder_stats: dict[str, dict] = {}
    for folder in REQUIRED_SUBMISSION_FOLDERS:
        d = book.dir / folder
        if not d.is_dir():
            continue
        imgs = sorted(d.glob("*.jpg"))
        bad_names = [p.name for p in imgs if not img_re.match(unicodedata.normalize("NFC", p.name))]
        if bad_names:
            errors.append(f"{folder}/ có {len(bad_names)} ảnh sai quy cách tên (<Tên sách>.<NNN>.jpg): {bad_names[:3]}")
        for tmp_name in ("Label.txt.bak", "fileState.txt.bak", "Cache.cach"):
            if (d / tmp_name).exists():
                warnings.append(f"{folder}/{tmp_name} là file tạm local (cần loại khi đóng gói ZIP nộp bài)")

        if folder in ("blank", "misc"):
            folder_stats[folder] = {"images": len(imgs)}
            continue

        lbl_path = d / label_io.LABEL_NAME
        fs_path = d / label_io.STATE_NAME
        if not imgs and not lbl_path.exists() and not fs_path.exists():
            folder_stats[folder] = {"images": 0, "labels": 0, "states": 0}
            continue
        if folder in ("intro", "ref", "toc") and not lbl_path.exists() and not fs_path.exists():
            warnings.append(f"{folder}/ (không bắt buộc theo quy định) có {len(imgs)} ảnh nhưng chưa chạy OCR")
            folder_stats[folder] = {"images": len(imgs), "labels": 0, "states": 0}
            continue

        labels = label_io.read_labels(lbl_path)
        states = label_io.read_states(fs_path)
        img_keys = {label_io.image_key(folder, p.name) for p in imgs}

        lbl_missing_img = sorted(set(labels) - img_keys)
        img_missing_lbl = sorted(img_keys - set(labels))
        state_diff = sorted(img_keys ^ set(states))
        if lbl_missing_img:
            errors.append(f"{folder}/Label.txt có {len(lbl_missing_img)} dòng không có ảnh tương ứng: {lbl_missing_img[:3]}")
        if img_missing_lbl:
            errors.append(f"{folder}/ có {len(img_missing_lbl)} ảnh chưa có trong Label.txt: {img_missing_lbl[:3]}")
        if state_diff:
            errors.append(f"{folder}/fileState.txt lệch {len(state_diff)} ảnh so với thư mục ảnh: {state_diff[:3]}")

        non_4_pts = 0
        empty_boxes = 0
        nom_spaces = 0
        for key, payload in labels.items():
            try:
                boxes = json.loads(payload)
            except ValueError:
                errors.append(f"{folder}/Label.txt JSON lỗi tại {key}")
                continue
            for b in boxes:
                pts = b.get("points") or []
                if len(pts) != 4:
                    non_4_pts += 1
                txt = str(b.get("transcription", "")).strip()
                if not txt:
                    empty_boxes += 1
                elif folder == "ch" and any(ch.isspace() for ch in txt):
                    nom_spaces += 1

        if non_4_pts:
            errors.append(f"{folder}/Label.txt có {non_4_pts} box không đủ 4 điểm")
        if empty_boxes:
            warnings.append(f"{folder}/Label.txt có {empty_boxes} box rỗng transcription")
        if nom_spaces:
            errors.append(f"{folder}/Label.txt có {nom_spaces} box Nôm chứa khoảng trắng")

        folder_stats[folder] = {
            "images": len(imgs),
            "labels": len(labels),
            "states": len(states),
            "done": sum(1 for v in states.values() if v == 1),
        }

    # Kiểm tra file alignment (.csv / .xlsx) nếu có đủ ch và vi
    csv_path = book.dir / f"{book.stem}.csv"
    xlsx_path = book.dir / f"{book.stem}.xlsx"
    align_info: dict | None = None
    has_ch = (book.dir / "ch" / label_io.LABEL_NAME).exists()
    has_vi = (book.dir / "vi" / label_io.LABEL_NAME).exists()
    if has_ch and has_vi:
        if not csv_path.exists() or not xlsx_path.exists():
            errors.append("Có đủ ch/Label.txt và vi/Label.txt nhưng thiếu file .csv hoặc .xlsx")
        else:
            with csv_path.open(encoding="utf-8-sig", newline="") as f:
                csv_rows = list(csv.reader(f))
            if not csv_rows or csv_rows[0] != ["img_id", "sentence_id", "SinoNom_Char", "ChuQN_txt"]:
                errors.append(f"{csv_path.name} sai header 4 cột chuẩn")
            from openpyxl import load_workbook
            wb = load_workbook(xlsx_path, read_only=True)
            ws = wb.active
            xlsx_rows = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
            wb.close()
            if len(csv_rows) != len(xlsx_rows):
                errors.append(f"Số dòng {csv_path.name} ({len(csv_rows)}) != {xlsx_path.name} ({len(xlsx_rows)})")
            elif csv_rows[1:] != xlsx_rows[1:]:
                diff = sum(1 for a, b in zip(csv_rows[1:], xlsx_rows[1:]) if a != b)
                errors.append(f"{csv_path.name} lệch {diff} hàng so với {xlsx_path.name}")
            # Kiểm tra khớp từng câu với ch/Label.txt và vi/Label.txt
            from scripts import align_books
            expected_pairs, _ = align_books.align(book, 0)
            expected_rows = [[p.img_id, p.sentence_id, p.nom, p.qn] for p in expected_pairs]
            if csv_rows[1:] != expected_rows:
                errors.append(f"{csv_path.name} đã cũ so với ch/Label.txt & vi/Label.txt hiện tại (cần chạy lại align_books.py)")
            align_info = {"csv_rows": len(csv_rows) - 1, "xlsx_rows": len(xlsx_rows) - 1}

    return {"book": nf_stem, "errors": errors, "warnings": warnings, "folders": folder_stats, "align": align_info}


def check_requirements_pinned() -> list[str]:
    """Kiểm tra mọi dependency trong requirements*.txt đều được cố định (pinned ==)."""
    issues: list[str] = []
    for req_file in sorted(ROOT.glob("requirements*.txt")):
        for lineno, raw in enumerate(req_file.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-r "):
                continue
            if "==" not in line:
                issues.append(f"{req_file.name}:{lineno}: chưa cố định phiên bản (==): {line}")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/")
    ap.add_argument("--strict-env", action="store_true", help="Kiểm tra luôn pinned requirements*.txt")
    args = ap.parse_args()

    data_dir = args.data.resolve()
    total_errors = 0
    for book in library.list_books(data_dir):
        res = check_book(book)
        if not res["folders"]:
            continue
        status = "OK" if not res["errors"] else f"LỖI ({len(res['errors'])})"
        print(f"=== {res['book']} [{status}] ===")
        for folder, st in res["folders"].items():
            if st["images"] or st.get("labels"):
                print(f"  {folder}/: {st}")
        if res["align"]:
            print(f"  align: {res['align']}")
        for e in res["errors"]:
            total_errors += 1
            print(f"  [ERR] {e}")
        for w in res["warnings"]:
            print(f"  [WARN] {w}")

    if args.strict_env:
        pin_issues = check_requirements_pinned()
        print(f"=== requirements*.txt pinning [{len(pin_issues)} vấn đề] ===")
        for p in pin_issues:
            total_errors += 1
            print(f"  [ERR] {p}")

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
