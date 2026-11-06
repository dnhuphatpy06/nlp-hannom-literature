"""Nhận diện nhanh trang blank (không có chữ cần OCR) trực tiếp từ PDF.

Mỗi trang được render xám ở ~640 px cạnh dài (khoảng 18 ms/trang) rồi:
  1. trừ nền cục bộ để lấy nét mực (ảnh scan thường tối và không đồng đều, không dùng ngưỡng trắng tuyệt đối);
  2. gỡ đường kẻ dài ngang/dọc (khung in, viền scan) để trang chỉ có khung vẫn được coi là blank;
  3. bỏ viền ngoài, đếm số thành phần liên thông đủ lớn (nét chữ). Trang blank có rất ít thành phần.

Kết quả chỉ là gợi ý. Ngưỡng mặc định lấy từ phân bố trên các PDF mẫu, cần hiệu chỉnh khi duyệt thực tế.

Dòng lệnh (in các trang có ít chữ nhất của mỗi sách, dùng để hiệu chỉnh):
    python scripts/blank_detector.py data --top 5
    python scripts/blank_detector.py "data/<sách>/<sách>.pdf"
"""
import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # chạy trực tiếp được như script
from scripts import library  # noqa: E402

RENDER_SIDE = 640      # cạnh dài (px) của ảnh phân tích
GRID = (24, 18)        # lưới ô để tính tỉ lệ vùng có chữ
MARGIN = 0.05          # bỏ viền ngoài (tỉ lệ)
MIN_COMPONENT = 6      # thành phần nhỏ hơn (px) coi là bụi
BLANK_MAX = 15         # số thành phần tối đa để gợi ý "blank"
UNCERTAIN_MAX = 60     # số thành phần tối đa để xếp "không chắc"


def render_gray(page: "pymupdf.Page", side: int = RENDER_SIDE) -> np.ndarray:
    """Render trang thành mảng xám uint8. Scale theo cạnh dài vì kích thước trang PDF rất khác nhau."""
    scale = side / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]


def page_metrics(gray: np.ndarray) -> dict:
    """Đo lượng "chữ" còn lại trên trang sau khi bỏ nền, khung và viền."""
    h, w = gray.shape
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # Nền cục bộ: median trên bản thu nhỏ rồi phóng lại. diff > 0 ở nơi tối hơn nền (nét mực).
    background = cv2.medianBlur(cv2.resize(gray, (w // 4 + 1, h // 4 + 1)), 15)
    diff = cv2.subtract(cv2.resize(background, (w, h)), gray)
    threshold = max(18, int(np.percentile(diff, 50) * 3 + 10))
    ink = (diff > threshold).astype(np.uint8) * 255

    # Gỡ đường kẻ dài (khung in, viền scan): mở hình thái theo ngang và dọc rồi trừ đi.
    horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 8), 1))
    vertical = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 8)))
    lines = cv2.bitwise_or(cv2.morphologyEx(ink, cv2.MORPH_OPEN, horizontal),
                           cv2.morphologyEx(ink, cv2.MORPH_OPEN, vertical))
    text = cv2.bitwise_and(ink, cv2.bitwise_not(cv2.dilate(lines, np.ones((3, 3), np.uint8))))

    mx, my = max(1, int(w * MARGIN)), max(1, int(h * MARGIN))
    text[:my], text[-my:], text[:, :mx], text[:, -mx:] = 0, 0, 0, 0

    count, _, stats, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    keep = stats[1:, cv2.CC_STAT_AREA] >= MIN_COMPONENT
    gx, gy = GRID
    cells = np.zeros((gy, gx), bool)
    for i in np.where(keep)[0] + 1:
        cx = stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] // 2
        cy = stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] // 2
        cells[min(gy - 1, cy * gy // h), min(gx - 1, cx * gx // w)] = True
    return {
        "components": int(keep.sum()),
        "active": round(float(cells.mean()), 4),
        "ink": round(float(text.mean() / 255), 4),
    }


def classify(components: int, blank_max: int = BLANK_MAX, uncertain_max: int = UNCERTAIN_MAX) -> str:
    if components <= blank_max:
        return "blank"
    return "uncertain" if components <= uncertain_max else "content"


def detect_pdf(pdf: Path, progress: Callable[[int, int], None] | None = None) -> list[dict]:
    """Phân tích mọi trang của PDF. Trả về [{page, components, active, ink}] (hoặc {page, error})."""
    results = []
    with pymupdf.open(pdf) as doc:
        if doc.needs_pass:
            raise ValueError("PDF có mật khẩu bảo vệ.")
        total = len(doc)
        for i in range(total):
            try:
                results.append({"page": i + 1, **page_metrics(render_gray(doc[i]))})
            except Exception as e:  # một trang lỗi không làm hỏng cả sách
                results.append({"page": i + 1, "error": str(e)})
            if progress:
                progress(i + 1, total)
    return results


def main() -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", type=Path, default=library.DEFAULT_DATA_DIR,
                        help="File PDF hoặc thư mục data/ (mặc định: data/)")
    parser.add_argument("--top", type=int, default=5, help="Số trang ít chữ nhất in ra cho mỗi sách (mặc định: 5)")
    parser.add_argument("--blank-max", type=int, default=BLANK_MAX, help=f"Ngưỡng blank (mặc định: {BLANK_MAX})")
    parser.add_argument("--uncertain-max", type=int, default=UNCERTAIN_MAX, help=f"Ngưỡng không chắc (mặc định: {UNCERTAIN_MAX})")
    args = parser.parse_args()

    if args.source.is_dir():
        pdfs = [b.pdf for b in library.list_books(args.source)]
        pdfs += sorted(p for p in args.source.glob("*.pdf") if p.is_file())  # PDF còn nằm lẻ
    elif args.source.is_file():
        pdfs = [args.source]
    else:
        print(f"[LỖI] Không tìm thấy: {args.source}")
        return 1
    if not pdfs:
        print(f"Không có PDF nào trong '{args.source}'.")
        return 0

    started = time.time()
    pages_total = 0
    for pdf in pdfs:
        try:
            results = [r for r in detect_pdf(pdf) if "error" not in r]
        except Exception as e:
            print(f"[LỖI] {pdf.name}: {e}")
            continue
        pages_total += len(results)
        verdicts = [classify(r["components"], args.blank_max, args.uncertain_max) for r in results]
        print(f"\n{pdf.name}: {len(results)} trang | blank {verdicts.count('blank')}, "
              f"không chắc {verdicts.count('uncertain')}")
        for r in sorted(results, key=lambda r: r["components"])[:args.top]:
            verdict = classify(r["components"], args.blank_max, args.uncertain_max)
            print(f"   trang {r['page']:>4}: {r['components']:>4} thành phần, "
                  f"{r['active'] * 100:4.0f}% ô có chữ  -> {verdict}")
    elapsed = time.time() - started
    print(f"\n{pages_total} trang trong {elapsed:.1f} s ({elapsed / max(1, pages_total) * 1000:.0f} ms/trang)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
