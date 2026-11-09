"""Web UI cho pipeline xử lý sách Hán Nôm. Chạy: python app.py

Hiện có các bước:
  1. Gán loại sách (T1, T2, Tnom, ...) cho từng PDF, lưu vào data/cache/type.json.
  2. Tách trang PDF thành ảnh JPEG, lưu vào data/<Tên sách>/cache/pages/ cùng cache nhãn từng trang.
  3. Tự động (nền) nhận diện blank rồi gán ch/vi/ch_vi theo loại sách (scripts/auto_tag.py) cho trang unknown,
     chạy khi mở app, khi có PDF mới và khi đổi loại sách. intro, toc, ref, misc và sửa sai gán tay hàng loạt.
  4. Bước OCR (sau khi gán nhãn trang): chọn nguồn OCR theo nhãn (Kim Hán Nôm, PaddleOCR-VL, row_projection),
     ghi <Tên sách>/<ch|vi|...>/Label.txt + fileState.txt theo chuẩn PPOCRLabel, rồi mở PPOCRLabel để kiểm tra
     (scripts/ocr_jobs.py). Không OCR trang blank, misc.

Cấu trúc data/ giống thư mục nộp bài, xem scripts/library.py. Các công cụ hỗ trợ nằm trong scripts/.
"""
import argparse
import http.server
import json
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from scripts import book_types, library, ocr_engines, ocr_jobs, page_cache, split_jobs
from scripts.library import UNKNOWN

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

HTML_FILE = Path(__file__).resolve().parent / "web" / "index.html"
FONT_DIR = Path(__file__).resolve().parent / "font"  # font Hán Nôm, web nạp qua @font-face
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}


# ==============================================================================
# Dữ liệu trả cho giao diện
# ==============================================================================
def build_state(data_dir: Path, type_file: Path) -> dict:
    stored, error = book_types.read_type_file(type_file)

    files = []
    for book in library.list_books(data_dir):
        entry = {"name": book.name, "size": book.pdf.stat().st_size, "type": UNKNOWN,
                 "split": split_jobs.split_status(book)}
        raw = book_types.raw_type_of(stored, book.name)
        if raw is not None:
            if isinstance(raw, str) and raw in book_types.VALID_CODES:
                entry["type"] = raw
            else:
                entry["invalid_value"] = str(raw)
        files.append(entry)

    present = {library.norm(f["name"]) for f in files}
    orphans = [{"name": k, "type": v} for k, v in stored.items() if library.norm(k) not in present]
    return {
        "types": book_types.BOOK_TYPES,
        "files": files,
        "orphans": orphans,
        "type_file": str(type_file),
        "error": error,
        "split_error": split_jobs.UNAVAILABLE,
        "blank_error": split_jobs.BLANK_UNAVAILABLE,
        "default_dpi": split_jobs.DEFAULT_DPI,
    }


def build_book(data_dir: Path, type_file: Path, name: str) -> dict:
    book = library.find_book(data_dir, name)
    if book is None:
        raise ValueError(f"Không tìm thấy PDF: {name}")
    stored, _ = book_types.read_type_file(type_file)
    return {
        "name": book.name,
        "type": book_types.type_of(stored, book.name),
        "split": split_jobs.split_status(book),
        "cache": page_cache.read_cache(book),
        "rendered": page_cache.rendered_pages(book),
        "pages_dir": str(book.pages_dir),
        "cache_file": str(book.page_cache_file),
    }


# ==============================================================================
# HTTP server
# ==============================================================================
def make_handler(data_dir: Path, type_file: Path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str, cache_control: str | None = None):
            try:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(path.stat().st_size))
                if cache_control:
                    self.send_header("Cache-Control", cache_control)
                self.end_headers()
                with open(path, "rb") as f:
                    while chunk := f.read(1 << 20):
                        self.wfile.write(chunk)
            except (ConnectionError, OSError):
                pass  # trình duyệt đóng tab giữa chừng

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            name = query.get("name", [""])[0]
            path = parsed.path

            if path in ("/", "/index.html"):
                if not HTML_FILE.is_file():
                    self.send_error(500, "Thiếu file giao diện web/index.html")
                    return
                self._send_file(HTML_FILE, "text/html; charset=utf-8")
            elif path.startswith("/font/"):
                fname = urllib.parse.unquote(path[len("/font/"):])
                font = FONT_DIR / fname if "/" not in fname and "\\" not in fname else None
                if font is None or font.suffix.lower() not in FONT_TYPES or not font.is_file():
                    self.send_error(404)
                else:
                    self._send_file(font, FONT_TYPES[font.suffix.lower()], "public, max-age=86400")
            elif path == "/api/state":
                for note in library.organize(data_dir):  # nhận PDF mới chép vào data/
                    print(note)
                split_jobs.kick_autotag(data_dir, type_file)  # sách mới chép vào data/ cũng được tự xử lý
                self._send_json(build_state(data_dir, type_file))
            elif path == "/api/autotag":
                self._send_json(split_jobs.autotag_status())
            elif path == "/api/splits":
                self._send_json({b.name: split_jobs.split_status(b) for b in library.list_books(data_dir)})
            elif path == "/api/book":
                try:
                    self._send_json(build_book(data_dir, type_file, name))
                except ValueError as e:
                    self._send_json({"error": str(e)}, status=404)
            elif path == "/pdf":
                book = library.find_book(data_dir, name)
                if book is None:
                    self.send_error(404)
                else:
                    self._send_file(book.pdf, "application/pdf")
            elif path == "/api/ocr":
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                else:
                    self._send_json(ocr_jobs.plan(book))
            elif path == "/api/ocr/status":
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                else:
                    self._send_json({"job": ocr_jobs.job_status(book), "paddle": ocr_engines.PADDLE.status(),
                                     "vintern": ocr_engines.VINTERN_WORKER.status(),
                                     "folders": ocr_jobs.folder_status(book),
                                     "ppocrlabel_running": ocr_jobs.ppocrlabel_running()})
            elif path == "/api/thumb":
                self._send_thumb(name, query.get("page", [""])[0])
            elif path == "/api/page":
                self._send_page(name, query.get("page", [""])[0])
            else:
                self.send_error(404)

        def _send_thumb(self, name: str, page_str: str):
            book = library.find_book(data_dir, name)
            if book is None or not page_str.isdigit():
                self.send_error(404)
                return
            try:
                thumb = split_jobs.thumbnail_path(book, int(page_str))
            except Exception as e:
                self.send_error(500, f"Không tạo được ảnh thu nhỏ: {e}")
                return
            if thumb is None:
                self.send_error(404)
            else:
                self._send_file(thumb, "image/jpeg", "private, max-age=3600")

        def _send_page(self, name: str, page_str: str):
            """Ảnh đầy đủ của một trang (file JPEG trong cache/pages/) để xem phóng to."""
            book = library.find_book(data_dir, name)
            image = library.find_page_image(book.pages_dir, book.stem, int(page_str))                 if book is not None and page_str.isdigit() else None
            if image is None:
                self.send_error(404)
            else:
                self._send_file(image, "image/jpeg", "private, max-age=3600")

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path not in ("/api/save", "/api/split", "/api/pages/tag", "/api/pages/spread", "/api/ocr/preload",
                            "/api/ocr/run", "/api/ocr/folder", "/api/ppocrlabel/open"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/save":
                    assignments = payload.get("types")
                    if not isinstance(assignments, dict):
                        raise ValueError("Thiếu trường 'types'.")
                    names = [b.name for b in library.list_books(data_dir)]
                    result = book_types.save_types(names, type_file, assignments)
                    split_jobs.kick_autotag(data_dir, type_file)  # loại sách mới: gán lại ch/vi tự động
                    self._send_json({"success": True, **result})
                elif path == "/api/ocr/preload":
                    model = str(payload.get("model", "paddle"))   # mô hình cần nạp: paddle (mặc định) hoặc vintern
                    if model not in ("paddle", "vintern"):
                        raise ValueError(f"Mô hình không hợp lệ: {model!r}")
                    holder = ocr_engines.VINTERN_WORKER if model == "vintern" else ocr_engines.PADDLE
                    holder.preload()
                    self._send_json({"success": True, "paddle": ocr_engines.PADDLE.status(),
                                     "vintern": ocr_engines.VINTERN_WORKER.status()})
                elif path == "/api/ocr/folder":
                    # OCR riêng một thư mục: box = kimhannom | paddleocr | row_projection | existing, text = kimhannom | paddleocr | vintern | none
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    ocr_jobs.start_pipeline(book, str(payload.get("folder", "")), str(payload.get("box", "")), str(payload.get("text", "")),
                                            overwrite=bool(payload.get("overwrite", False)), use_cache=bool(payload.get("use_cache", True)),
                                            alternate=bool(payload.get("alternate", True)))
                    self._send_json({"success": True})
                elif path in ("/api/ocr/run", "/api/ppocrlabel/open"):
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    if path == "/api/ocr/run":
                        engines = payload.get("engines") or {}
                        if not isinstance(engines, dict):
                            raise ValueError("Trường 'engines' phải là object {nhãn: engine}.")
                        ocr_jobs.start_ocr(book, engines, bool(payload.get("overwrite", False)),
                                           use_cache=bool(payload.get("use_cache", True)),
                                           read_text=bool(payload.get("read_text", True)),
                                           folders=payload.get("folders"))
                        self._send_json({"success": True})
                    else:
                        self._send_json({"success": True, **ocr_jobs.open_ppocrlabel(book, str(payload.get("folder", "")))})
                elif path == "/api/pages/tag":
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    pages = payload.get("pages")
                    if not isinstance(pages, list):
                        raise ValueError("Thiếu trường 'pages'.")
                    changed = page_cache.set_tags(book, pages, str(payload.get("tag", "")))
                    self._send_json({"success": True, "changed": changed})
                elif path == "/api/pages/spread":
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    pages = payload.get("pages")
                    if not isinstance(pages, list):
                        raise ValueError("Thiếu trường 'pages'.")
                    spread = payload.get("spread")   # 1, 2 hoặc null (tự đoán theo tỉ lệ ảnh)
                    changed = page_cache.set_spread(book, pages, int(spread) if spread is not None else None)
                    self._send_json({"success": True, "changed": changed})
                else:
                    split_jobs.start_split(data_dir, type_file, str(payload.get("name", "")),
                                           int(payload.get("dpi", split_jobs.DEFAULT_DPI)),
                                           bool(payload.get("force", False)))
                    self._send_json({"success": True})
            except (split_jobs.SplitBusy, ocr_jobs.OcrBusy) as e:
                self._send_json({"success": False, "error": str(e)}, status=409)
            except Exception as e:  # trả lỗi về UI thay vì làm sập server
                self._send_json({"success": False, "error": str(e)}, status=400)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=library.DEFAULT_DATA_DIR, help="Thư mục data/ (mặc định: data/)")
    parser.add_argument("--type-file", type=Path, help="File JSON lưu loại sách (mặc định: <data>/cache/type.json)")
    parser.add_argument("--port", type=int, default=8766, help="Cổng Web UI (mặc định: 8766)")
    parser.add_argument("--no-browser", action="store_true", help="Không tự mở trình duyệt")
    args = parser.parse_args()

    data_dir = args.data.resolve()
    type_file = (args.type_file or book_types.default_type_file(data_dir)).resolve()

    for note in library.organize(data_dir):
        print(note)

    split_jobs.kick_autotag(data_dir, type_file)
    handler = make_handler(data_dir, type_file)
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = Server(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    if server is None:
        print("[LỖI] Không tìm được cổng trống.")
        return 1

    url = f"http://localhost:{port}"
    print(f"Web UI: {url}")
    print(f"  Dữ liệu:   {data_dir}")
    print(f"  Loại sách: {type_file}")
    print("Nhấn Ctrl+C để dừng.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
