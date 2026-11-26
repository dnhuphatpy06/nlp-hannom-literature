"""Web UI cho pipeline xử lý sách Hán Nôm. Chạy: python app.py

Hiện có các bước:
  1. Gán loại sách (T1, T2, Tnom, ...) cho từng PDF, lưu vào data/cache/type.json.
  2. Tách trang PDF thành ảnh JPEG, lưu vào data/<Tên sách>/cache/pages/ cùng cache nhãn từng trang.
  3. Tự động (nền) nhận diện blank rồi gán ch/vi/ch_vi theo loại sách (scripts/auto_tag.py) cho trang unknown,
     chạy khi mở app, khi có PDF mới và khi đổi loại sách. intro, toc, ref, misc và sửa sai gán tay hàng loạt.
  4. Bước OCR (sau khi gán nhãn trang): chọn nguồn OCR theo nhãn (Kim Hán Nôm, PaddleOCR-VL, row_projection),
     ghi <Tên sách>/<ch|vi|...>/Label.txt + fileState.txt theo chuẩn PPOCRLabel, rồi mở PPOCRLabel để kiểm tra
     (scripts/ocr_jobs.py). Không OCR trang blank, misc.
  5. Bước dóng hàng (scripts/align_rows.py, align_jobs.py): đối chiếu Hán Nôm - Việt theo id bbox, dấu miss vi / miss nôm, ô dịch âm bằng
     API Kim Hán Nôm; xuất .csv/.xlsx bằng scripts/align_books.py.
  6. Các tab Kiểm tra, Đề xuất, Kết quả của bước 3 (scripts/review_pipeline.py, dùng gói scripts/sinonom_review chép từ dự án Alignment):
     tô cặp chữ Nôm - âm Quốc ngữ lệch từ điển (<Tên>.csv), duyệt ứng viên sửa top-k, xuất <Tên>.xlsx và <Tên>_SO_SANH.xlsx vào thư mục sách.
  7. Đối chiếu CH và VI tại /review/ để kiểm tra ảnh và sửa chữ trong Label.txt.

Cấu trúc data/ giống thư mục nộp bài, xem scripts/library.py. Các công cụ hỗ trợ nằm trong scripts/.
"""
import argparse
import base64
import http.server
import json
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from review_local import REVIEW_PATH, review_handler
from scripts import align_jobs, align_rows, book_types, library, ocr_engines, ocr_jobs, page_cache, review_pipeline, split_jobs
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
        "kind": {"ocr": not review_pipeline.is_xlsx_book(book), "xlsx": review_pipeline.is_xlsx_book(book),
                 "has_input": review_pipeline.has_input(book)},
        "pages_dir": str(book.pages_dir),
        "cache_file": str(book.page_cache_file),
    }


# ==============================================================================
# HTTP server
# ==============================================================================
def make_handler(data_dir: Path, type_file: Path):
    class Handler(review_handler(data_dir, prefix=REVIEW_PATH, pdf_lock=split_jobs.pdf_lock)):
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

        def _send_file(self, path: Path, content_type: str, cache_control: str | None = None, download_name: str | None = None):
            try:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(path.stat().st_size))
                if download_name:   # tải về với tên file (UTF-8, tên sách có dấu)
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name))
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
            path = parsed.path
            if path == REVIEW_PATH or path.startswith(REVIEW_PATH + "/"):
                super().do_GET()
                return
            query = urllib.parse.parse_qs(parsed.query)
            name = query.get("name", [""])[0]

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
            elif path == "/api/align":
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                else:
                    self._send_json(align_jobs.status(book))
            elif path == "/api/align/rows":
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                else:
                    rows, info = align_rows.build_rows(book)
                    self._send_json({"info": info, "rows": [align_rows.public_row(r) for r in rows],
                                     "translit": align_rows.translit_status(book)})
            elif path.startswith("/api/review/"):
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                    return
                kind = path[len("/api/review/"):]
                try:
                    page = int(query.get("page", ["0"])[0] or 0)
                    if kind == "summary":
                        self._send_json(review_pipeline.summary(book))
                    elif kind == "check":
                        self._send_json(review_pipeline.check_page(book, page, query.get("only", ["1"])[0] != "0"))
                    elif kind == "page":
                        self._send_json(review_pipeline.review_page(book, page))
                    elif kind == "results":
                        self._send_json(review_pipeline.results_page(book, page))
                    elif kind == "file":
                        target = review_pipeline.out_files(book).get(query.get("kind", [""])[0])
                        if target is None or not target.is_file():
                            self.send_error(404)
                        else:
                            self._send_file(target, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if target.suffix == ".xlsx"
                                            else "text/csv; charset=utf-8" if target.suffix == ".csv" else "application/json", download_name=target.name)
                    else:
                        self.send_error(404)
                except ValueError as e:
                    self._send_json({"error": str(e)}, status=400)
            elif path == "/api/align/sources":
                book = library.find_book(data_dir, name)
                if book is None:
                    self._send_json({"error": f"Không tìm thấy PDF: {name}"}, status=404)
                else:
                    self._send_json({"options": align_rows.list_sources(book), "current": align_rows.source_info(book)})
            elif path == "/api/align/crop":
                book = library.find_book(data_dir, name)
                jpeg = align_rows.crop_jpeg(book, query.get("side", [""])[0], query.get("id", [""])[0]) if book is not None else None
                if jpeg is None:
                    self.send_error(404)
                else:
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(jpeg)))
                        self.send_header("Cache-Control", "private, max-age=600")
                        self.end_headers()
                        self.wfile.write(jpeg)
                    except (ConnectionError, OSError):
                        pass   # trình duyệt đã hủy tải ảnh (chuyển trang, vẽ lại danh sách): không phải lỗi
            elif path == "/api/align/file":
                book = library.find_book(data_dir, name)
                target = align_jobs.output_files(book).get(query.get("kind", [""])[0]) if book is not None else None
                if target is None or not target.is_file():
                    self.send_error(404)
                else:
                    self._send_file(target, "text/plain; charset=utf-8" if target.suffix == ".txt" else
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if target.suffix == ".xlsx"
                                    else "text/csv; charset=utf-8", download_name=target.name)
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
                            "/api/ocr/run", "/api/ocr/folder", "/api/ppocrlabel/open", "/api/align/run",
                            "/api/align/mark", "/api/align/dich", "/api/align/translit", "/api/align/source", "/api/align/move", "/api/align/save",
                            "/api/align/text", "/api/align/import", "/api/review/csv", "/api/review/score", "/api/review/decide",
                            "/api/review/save", "/api/review/export", "/api/align/match", "/api/review/upload", "/api/review/source"):
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
                elif path == "/api/align/match":
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    if payload.get("clear"):
                        align_rows.clear_auto(book)
                        self._send_json({"success": True})
                    elif payload.get("kind") == "align":
                        self._send_json({"success": True, **align_rows.auto_align(book, float(payload.get("tau", 0.4)))})
                    else:
                        self._send_json({"success": True, **align_rows.auto_match(book, float(payload.get("tau", 0.5)))})
                elif path.startswith("/api/review/"):
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    kind = path[len("/api/review/"):]
                    if kind == "csv":
                        self._send_json({"success": True, **review_pipeline.save_csv(book)})
                    elif kind == "score":
                        use_lm = payload.get("use_lm")
                        review_pipeline.start_scoring(book, None if use_lm is None else bool(use_lm))
                        self._send_json({"success": True})
                    elif kind == "upload":
                        try:
                            data = base64.b64decode(str(payload.get("file", "")), validate=True)
                        except ValueError:
                            raise ValueError("Nội dung file không hợp lệ.")
                        self._send_json({"success": True, **review_pipeline.save_input(book, data)})
                    elif kind == "source":
                        self._send_json({"success": True, **review_pipeline.set_source(book, str(payload.get("source", "")))})
                    elif kind == "decide":
                        progress = review_pipeline.decide(book, str(payload.get("key", "")), payload.get("side") or None,
                                                          payload.get("nom"), payload.get("qn"))
                        self._send_json({"success": True, "progress": progress})
                    elif kind == "save":
                        self._send_json({"success": True, **review_pipeline.save_review(book)})
                    else:
                        which = str(payload.get("kind", ""))
                        if which not in ("result", "compare"):
                            raise ValueError("Loại file không hợp lệ.")
                        export = review_pipeline.export_result if which == "result" else review_pipeline.export_compare
                        self._send_json({"success": True, **export(book)})
                elif path in ("/api/align/mark", "/api/align/dich", "/api/align/translit", "/api/align/source", "/api/align/move", "/api/align/save",
                              "/api/align/text", "/api/align/import"):
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    if path == "/api/align/mark":
                        align_rows.set_mark(book, str(payload.get("side", "")), str(payload.get("id", "")), bool(payload.get("miss")))
                        self._send_json({"success": True})
                    elif path == "/api/align/text":
                        if payload.get("reset"):
                            align_rows.reset_edits(book)
                        else:
                            align_rows.set_text(book, str(payload.get("side", "")), str(payload.get("id", "")), str(payload.get("text", "")))
                        self._send_json({"success": True})
                    elif path == "/api/align/import":
                        try:
                            data = base64.b64decode(str(payload.get("file", "")), validate=True)
                        except ValueError:
                            raise ValueError("Nội dung file không hợp lệ.")
                        self._send_json({"success": True, **align_rows.import_xlsx(book, data)})
                    elif path == "/api/align/save":
                        self._send_json({"success": True, **align_rows.save_order(book)})
                    elif path == "/api/align/move":
                        align_rows.move_box(book, str(payload.get("side", "")), str(payload.get("id", "")), str(payload.get("dir", "")))
                        self._send_json({"success": True})
                    elif path == "/api/align/source":
                        align_rows.set_source(book, str(payload.get("role", "")), str(payload.get("book", "")), str(payload.get("folder", "")))
                        self._send_json({"success": True})
                    elif path == "/api/align/dich":
                        box = str(payload.get("id", ""))
                        target = next((b for b in align_rows.load_role(book, "ch") if b["id"] == box), None)
                        if target is None:
                            raise ValueError("Không có box Nôm này.")
                        align_rows.set_dich(book, box, str(payload.get("text", "")), target["text"], manual=True)
                        self._send_json({"success": True})
                    else:
                        ids = payload.get("ids")
                        if ids is not None and not isinstance(ids, list):
                            raise ValueError("Trường 'ids' phải là danh sách id.")
                        self._send_json({"success": True, "count": align_rows.start_translit(book, [str(i) for i in ids] if ids is not None else None)})
                elif path == "/api/align/run":
                    book = library.find_book(data_dir, str(payload.get("name", "")))
                    if book is None:
                        raise ValueError("Không tìm thấy PDF.")
                    if not align_jobs.status(book)["ready"]:
                        raise ValueError("Sách chưa có Label.txt ở cả ch/ và vi/. Chạy bước OCR trước.")
                    align_jobs.start(book, int(payload.get("pages") or 0))
                    self._send_json({"success": True})
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
            except (split_jobs.SplitBusy, ocr_jobs.OcrBusy, align_jobs.AlignBusy, align_rows.TranslitBusy, review_pipeline.ReviewBusy) as e:
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
