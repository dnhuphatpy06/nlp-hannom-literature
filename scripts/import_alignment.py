"""Nhập dữ liệu của dự án Alignment cho các văn bản không phải Kim*: file xlsx câu Nôm - Việt, ứng viên đã chấm điểm và lựa chọn đã duyệt.

  - Các file <tên văn bản>.xlsx trong <Alignment>/data*/Alignment/ được chép thành data/<thư mục sách>/input.xlsx
    (thư mục sách tên trùng tên file, có hoặc không có hậu tố _Y).
  - candidates_cache.json và review_state.json trong <Alignment>/outputs*/ (nhiều sách trong một file, khoá "<tên sách>|<dòng xlsx>|<vị trí dóng>")
    được tách theo từng sách và ghi vào data/<thư mục sách>/candidates_cache.json, review_state.json. Chữ ký của cache được tính lại cho phiên của
    riêng sách đó, nên mở tab Đề xuất là dùng ngay, không cần nạp mô hình ngôn ngữ. Ứng viên chỉ được ghi khi đủ cho mọi cặp lệch của sách; lựa chọn
    đã có sẵn trong thư mục sách được giữ nguyên, chỉ thêm các lựa chọn còn thiếu.

    python scripts/import_alignment.py [--alignment-dir ../Alignment] [--data-dir data] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import library, review_pipeline as rp  # noqa: E402

CACHE_NAME = "candidates_cache.json"
STATE_NAME = "review_state.json"


def find_book(books: list[library.Book], label: str) -> library.Book | None:
    wanted = {library.norm(label), library.norm(label) + "_Y"}
    return next((b for b in books if library.norm(b.stem) in wanted), None)


def import_inputs(alignment_dir: Path, books: list[library.Book], dry_run: bool) -> dict[str, library.Book]:
    """Chép xlsx thành input.xlsx. Trả về {tên văn bản: sách} của các văn bản có xlsx."""
    found: dict[str, library.Book] = {}
    for folder in sorted(alignment_dir.glob("data*/Alignment")):
        for xlsx in sorted(folder.glob("*.xlsx")):
            if xlsx.name.startswith("~$"):
                continue
            label = library.norm(xlsx.stem)
            book = find_book(books, label)
            if book is None:
                print(f"  ! {xlsx.name}: không có thư mục sách tương ứng trong data/")
                continue
            target = rp.input_file(book)
            same = target.exists() and target.read_bytes() == xlsx.read_bytes()
            if not same and not dry_run:
                shutil.copy2(xlsx, target)
            print(f"  {'=' if same else '+'} {xlsx.name} -> {book.dir.name}/{rp.INPUT_NAME}" + (" (đã có, giữ nguyên)" if same else ""))
            found[label] = book
    return found


def import_outputs(alignment_dir: Path, found: dict[str, library.Book], dry_run: bool) -> None:
    for out_dir in sorted(alignment_dir.glob("outputs*")):
        cache_file, state_file = out_dir / CACHE_NAME, out_dir / STATE_NAME
        if not cache_file.exists():
            continue
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        decisions = json.loads(state_file.read_text(encoding="utf-8")).get("decisions", {}) if state_file.exists() else {}
        in_cache = {k.split("|", 1)[0] for k in cache["candidates"]}
        print(f"{out_dir.name}: {len(cache['candidates'])} cặp lệch đã chấm của {len(in_cache)} sách, {len(decisions)} lựa chọn")
        for label in sorted(in_cache):
            book = found.get(label)
            if book is None:
                print(f"  ! {label}: chưa có input.xlsx nên bỏ qua")
                continue
            rp._sessions.pop(library.norm(book.stem), None)
            sess = rp.get_session(book)                      # phiên của riêng sách, dựng từ input.xlsx như Alignment
            keys = set(sess.site_by_key)
            mine = {k: v for k, v in cache["candidates"].items() if k in keys}
            coverage = f"{len(mine)}/{len(keys)} cặp lệch"
            if len(mine) != len(keys):
                print(f"  ! {label}: ứng viên chỉ phủ {coverage} (dữ liệu hoặc từ điển đã đổi so với lúc chấm), bỏ qua cache ứng viên")
            else:
                st = cache["settings"]
                sig = sess._signature_of(st.get("weights") or {}, st.get("nom_context_lines", 1), st.get("qn_context_lines", 0),
                                         st.get("nom_model"), st.get("qn_model"))
                if not dry_run:
                    (book.dir / CACHE_NAME).write_text(json.dumps({"signature": sig, "settings": st, "candidates": mine}, ensure_ascii=False),
                                                       encoding="utf-8")
                print(f"  + {label}: ghi {CACHE_NAME} ({coverage})")
            mine_dec = {k: v for k, v in decisions.items() if k in keys}
            if mine_dec:
                path = book.dir / STATE_NAME
                have = json.loads(path.read_text(encoding="utf-8")).get("decisions", {}) if path.exists() else {}
                merged = {**mine_dec, **have}                # lựa chọn đã có trong thư mục sách được giữ nguyên
                if not dry_run:
                    path.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "decisions": merged}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
                print(f"  + {label}: ghi {STATE_NAME} ({len(mine_dec)} lựa chọn nhập, {len(have)} đã có, {len(merged)} sau khi gộp)")
            rp._sessions.pop(library.norm(book.stem), None)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alignment-dir", type=Path, default=ROOT.parent / "Alignment", help="thư mục dự án Alignment (mặc định: ../Alignment)")
    ap.add_argument("--data-dir", type=Path, default=library.DEFAULT_DATA_DIR, help="thư mục data/ của dự án này")
    ap.add_argument("--dry-run", action="store_true", help="chỉ báo cáo, không ghi file")
    args = ap.parse_args()
    if not args.alignment_dir.is_dir():
        ap.error(f"không thấy thư mục Alignment: {args.alignment_dir}")
    books = library.list_books(args.data_dir.resolve())
    print(f"Chép xlsx từ {args.alignment_dir}:")
    found = import_inputs(args.alignment_dir.resolve(), books, args.dry_run)
    if args.dry_run:
        print("(--dry-run) bỏ qua phần nhập kết quả chấm điểm và lựa chọn, vì cần input.xlsx đã chép để dựng phiên.")
        return 0
    print("Nhập kết quả chấm điểm và lựa chọn:")
    import_outputs(args.alignment_dir.resolve(), found, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
