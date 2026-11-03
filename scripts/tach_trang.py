"""Tách một PDF hoặc cả thư mục thành ảnh JPEG theo số trang gốc."""
import argparse
import os
import sys
import unicodedata
import uuid
from pathlib import Path

import pymupdf

# Đảm bảo hiển thị đúng tiếng Việt / ký tự đặc biệt trên console Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def is_valid_jpeg(path: Path) -> bool:
    """Kiểm tra file JPEG có tồn tại, kích thước hợp lệ và đầy đủ header/trailer JPEG."""
    try:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if size < 100:
            return False
        with open(path, "rb") as f:
            header = f.read(3)
            if header != b"\xff\xd8\xff":
                return False
            f.seek(-4, 2)
            tail = f.read(4)
            return b"\xff\xd9" in tail
    except Exception:
        return False


def find_existing_page_file(destination: Path, stem: str, number: int) -> Path | None:
    """Tìm file ảnh của trang, hỗ trợ cả hai dạng chuẩn hóa Unicode NFC và NFD."""
    name_orig = f"{stem}.{number:03d}.jpg"
    target = destination / name_orig
    if target.exists():
        return target
    name_nfc = unicodedata.normalize("NFC", name_orig)
    name_nfd = unicodedata.normalize("NFD", name_orig)
    for candidate in (destination / name_nfc, destination / name_nfd):
        if candidate.exists():
            return candidate
    return None


def clean_temp_files(destination: Path):
    """Dọn dẹp các file tạm dở dang (.tmp_*) còn sót lại nếu tiến trình trước bị dừng đột ngột."""
    if not destination.is_dir():
        return
    for tmp in destination.glob(".tmp_*"):
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def render_pdf(
    pdf: Path,
    destination: Path,
    dpi: int = 300,
    first: int = 1,
    last: int | None = None,
    force: bool = False,
) -> tuple[int, int, int]:
    """
    Tách các trang PDF thành ảnh JPEG theo chuẩn đồ án (<Tên sách>.<STT 3 số>.jpg).
    Hỗ trợ tách bù trang thiếu, bỏ qua trang đã có và ghi ảnh atomic an toàn khi dừng đột ngột.
    Trả về: (tổng số trang trong khoảng, số trang tách mới, số trang hợp lệ đã có sẵn).
    """
    try:
        doc = pymupdf.open(pdf)
    except Exception as e:
        print(f"[LỖI] Không thể đọc file PDF '{pdf.name}': {e}")
        return 0, 0, 0

    with doc:
        total_doc_pages = len(doc)
        end = total_doc_pages if last is None else last

        if doc.needs_pass:
            print(f"[LỖI] PDF có mật khẩu bảo vệ: '{pdf.name}'")
            return 0, 0, 0

        if not 1 <= first <= end <= total_doc_pages:
            raise ValueError(
                f"Khoảng trang không hợp lệ cho '{pdf.name}': first={first}, last={end} "
                f"(tài liệu có {total_doc_pages} trang)."
            )

        destination.mkdir(parents=True, exist_ok=True)
        clean_temp_files(destination)

        total_range = end - first + 1
        pages_to_render = []

        for number in range(first, end + 1):
            existing_file = find_existing_page_file(destination, pdf.stem, number)
            if force or existing_file is None or not is_valid_jpeg(existing_file):
                pages_to_render.append(number)

        already_done = total_range - len(pages_to_render)

        if not pages_to_render:
            print(f"[{pdf.name}] Đã đầy đủ {already_done}/{total_range} trang hợp lệ (khoảng {first}–{end}). Bỏ qua.")
            return total_range, 0, already_done

        print(
            f"[{pdf.name}] Khoảng {first}–{end} ({total_range} trang): "
            f"đã có {already_done} trang, cần tách {len(pages_to_render)} trang..."
        )

        rendered_count = 0
        for idx, number in enumerate(pages_to_render, 1):
            target = destination / f"{pdf.stem}.{number:03d}.jpg"
            temp_name = f".tmp_{pdf.stem}.{number:03d}_{os.getpid()}_{uuid.uuid4().hex[:6]}.jpg"
            temp_target = destination / temp_name

            try:
                page = doc[number - 1]
                pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
                pix.save(str(temp_target), jpg_quality=95)
                os.replace(temp_target, target)
                rendered_count += 1

                if sys.stdout.isatty():
                    print(
                        f"  -> [{idx}/{len(pages_to_render)}] Đang tách trang {number:03d}...",
                        end="\r",
                        flush=True,
                    )
                elif idx % 20 == 0 or idx == len(pages_to_render):
                    print(f"  -> Đã tách {idx}/{len(pages_to_render)} trang (trang {number:03d})...")
            except Exception as e:
                print(f"\n[LỖI] Thất bại khi tách trang {number} của '{pdf.name}': {e}")
                if temp_target.exists():
                    try:
                        temp_target.unlink()
                    except OSError:
                        pass

        if sys.stdout.isatty():
            print()  # Xuống dòng sau tiến trình \r

        print(
            f"[{pdf.name}] Hoàn tất: {already_done + rendered_count}/{total_range} trang -> {destination}"
        )
        return total_range, rendered_count, already_done


def prepare_workspace(repo: Path) -> Path:
    """Tạo cấu trúc thư mục làm việc và đảm bảo .gitignore có /work/."""
    ignore = repo / ".gitignore"
    if ignore.exists():
        lines = [line.strip() for line in ignore.read_text(encoding="utf-8").splitlines()]
        if "/work/" not in lines and "work/" not in lines and "work" not in lines:
            with ignore.open("a", encoding="utf-8") as stream:
                stream.write("\n/work/\n")
    work = repo / "work"
    for folder in ("pdf", "da_tach"):
        (work / folder).mkdir(parents=True, exist_ok=True)
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="File PDF hoặc thư mục chứa các PDF (mặc định: work/pdf)")
    parser.add_argument("--out", type=Path, help="Thư mục gốc nhận ảnh (mặc định: work/da_tach)")
    parser.add_argument("--dpi", type=int, default=300, help="Độ phân giải DPI (mặc định: 300)")
    parser.add_argument("--first", type=int, default=1, help="Trang đầu (tính từ 1)")
    parser.add_argument("--last", type=int, help="Trang cuối (bao gồm trang này)")
    parser.add_argument("--force", action="store_true", help="Ghi đè/tách lại kể cả khi ảnh hợp lệ đã có sẵn")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    work = prepare_workspace(repo)
    source = args.source or work / "pdf"
    output = args.out or work / "da_tach"

    if source.is_dir():
        if args.first != 1 or args.last is not None:
            parser.error("Hãy chọn một file PDF cụ thể khi chỉ xuất một khoảng trang (--first/--last).")
        files = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
        root = source
    else:
        files = [source] if source.is_file() and source.suffix.lower() == ".pdf" else []
        root = source.parent

    if not files and args.source is None:
        print(f"Không tìm thấy file PDF nào trong '{source}'.")
        print(f"-> Hãy chép các file PDF vào thư mục: {source} rồi chạy lại lệnh này.")
        return 0

    if not files:
        print(f"[LỖI] Không tìm thấy file PDF hợp lệ từ nguồn: {source}")
        return 1

    if args.dpi <= 0:
        parser.error("DPI phải là số nguyên dương (> 0).")

    print("=== TIẾN TRÌNH TÁCH TRANG PDF SANG ẢNH JPEG ===")
    print(f"Nguồn: {source} ({len(files)} file PDF)")
    print(f"Đích:  {output}")
    print(f"DPI:   {args.dpi}")
    print("-" * 50)

    total_books = len(files)
    total_rendered_all = 0
    total_skipped_all = 0
    errors = 0

    for idx, pdf in enumerate(files, 1):
        rel_path = pdf.relative_to(root).with_suffix("")
        destination = output / rel_path / "pages"
        print(f"\n({idx}/{total_books}) Sách: {pdf.name}")
        total, rendered, skipped = render_pdf(
            pdf=pdf,
            destination=destination,
            dpi=args.dpi,
            first=args.first,
            last=args.last,
            force=args.force,
        )
        total_rendered_all += rendered
        total_skipped_all += skipped
        if total == 0:
            errors += 1

    print("\n" + "=" * 50)
    print("TỔNG KẾT:")
    print(f"  - Số file PDF đã duyệt: {total_books}")
    print(f"  - Trang mới được tách:  {total_rendered_all}")
    print(f"  - Trang hợp lệ đã có:   {total_skipped_all}")
    if errors > 0:
        print(f"  - File gặp sự cố/lỗi:   {errors}")
    print("=" * 50)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
