"""Serve the CH/VI review page and save edits to local Label.txt files."""

import argparse
import hashlib
import json
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.parse import parse_qs, urlsplit


WEB_DIR = Path(__file__).resolve().parent / "review-web"
REVIEW_PATH = "/review"
LABEL_NAME = "Label.txt"
STATE_NAME = "fileState.txt"
SIDES = ("ch", "vi")
IMAGES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".bmp": "image/bmp",
}
PDF_LOCK = Lock()
ASSETS = {
    "/": ("review.html", "text/html; charset=utf-8"),
    "/review-data.js": ("review-data.js", "text/javascript; charset=utf-8"),
    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
}


def file_type(name):
    if name.lower() in {LABEL_NAME.lower(), STATE_NAME.lower()}:
        return "text/plain; charset=utf-8"
    return IMAGES.get(Path(name).suffix.lower())


def data_file(root, relative):
    parts = relative.split("/")
    side_file = len(parts) in (2, 3) and parts[-2] in SIDES and file_type(parts[-1])
    cache_image = (len(parts) in (3, 4) and parts[-3:-1] == ["cache", "pages"]
                   and Path(parts[-1]).suffix.lower() in IMAGES)
    if not (side_file or cache_image):
        return None
    if any(not part or part.startswith(".") or any(ch in part for ch in "\\:\0") for part in parts):
        return None
    path = root.joinpath(*parts)
    return path if path.is_file() and path.resolve() == path else None


def book_pdf_settings(book):
    if book.resolve() != book:
        return None
    pdf = book / (book.name + ".pdf")
    metadata = book / "cache" / "phan_loai.json"
    if not pdf.is_file() or pdf.resolve() != pdf or metadata.resolve() != metadata:
        return None
    try:
        settings = json.loads(metadata.read_text(encoding="utf-8-sig"))
        dpi, total = settings["dpi"], settings["total_pages"]
        if type(dpi) is int and 72 <= dpi <= 600 and type(total) is int and total > 0:
            return pdf, dpi, total
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def labeled_pdf_page(settings, name):
    if settings:
        pdf, dpi, total = settings
        match = re.fullmatch(re.escape(pdf.stem) + r"\.(\d{3,8})\.jpg", name)
        if match and 1 <= (page := int(match[1])) <= total:
            return pdf, page, dpi
    return None


def pdf_page_source(root, relative):
    parts = relative.split("/")
    if len(parts) not in (3, 4) or parts[-3:-1] != ["cache", "pages"]:
        return None
    if any(not part or part.startswith(".") or any(ch in part for ch in "\\:\0") for part in parts):
        return None
    return labeled_pdf_page(book_pdf_settings(root.joinpath(*parts[:-3])), parts[-1])


def render_pdf_page(source, pdf_lock):
    import pymupdf

    pdf, page, dpi = source
    with pdf_lock, pymupdf.open(pdf) as document:
        if page > len(document):
            return None
        pixmap = document[page - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        return pixmap.tobytes("jpeg", jpg_quality=95)


def library_files(root):
    if not root.is_dir():
        return []
    books = [root]
    books.extend(path for path in root.iterdir()
                 if path.is_dir() and path.name not in {*SIDES, "cache"} and not path.name.startswith("."))
    paths = {}
    for book in books:
        pdf_settings = book_pdf_settings(book)
        for side in sorted(SIDES):
            folder = book / side
            label = folder / LABEL_NAME
            if not data_file(root, label.relative_to(root).as_posix()):
                continue
            for file in folder.iterdir():
                relative = file.relative_to(root).as_posix()
                if data_file(root, relative):
                    paths[relative] = None
            for line in label.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                key, sep, _ = line.partition("\t")
                if not sep:
                    continue
                name = key.replace("\\", "/").rsplit("/", 1)[-1]
                cache_path = (book / "cache" / "pages" / name).relative_to(root).as_posix()
                if cache_path not in paths and (data_file(root, cache_path) or labeled_pdf_page(pdf_settings, name)):
                    paths[cache_path] = None
    return list(paths)


def revision(content):
    return '"' + hashlib.sha256(content).hexdigest() + '"'


def replace_label(path, body):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def review_handler(root, *, prefix="", pdf_lock=PDF_LOCK):
    token = secrets.token_urlsafe(32)
    save_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_body(self, body, content_type, status=200, etag=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, body, status=200):
            self.send_body(json.dumps(body, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", status)

        def check_host(self):
            port = self.server.server_port
            host = self.headers.get("Host")
            if host == f"127.0.0.1:{port}" or (prefix and host == f"localhost:{port}"):
                return True
            self.send_json({"error": "Địa chỉ web không hợp lệ."}, 403)
            return False

        def resolve_file(self, url):
            relative = parse_qs(url.query).get("path", [""])[0]
            return data_file(root, relative)

        def do_GET(self):
            if not self.check_host():
                return
            url = urlsplit(self.path)
            route = url.path[len(prefix):]
            try:
                if prefix and not route:
                    self.send_response(308)
                    self.send_header("Location", prefix + "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if route in ASSETS:
                    name, content_type = ASSETS[route]
                    body = (WEB_DIR / name).read_bytes()
                    if name == "review.html":
                        body = body.replace(b"<head>", b'<head>\n<meta name="review-local" content="true">', 1)
                    self.send_body(body, content_type)
                    return
                if route == "/api/library":
                    self.send_json({"name": root.name, "paths": library_files(root), "token": token})
                    return
                if route == "/api/file":
                    path = self.resolve_file(url)
                    if path:
                        body = path.read_bytes()
                        etag = revision(body) if path.name.lower() == LABEL_NAME.lower() else None
                        self.send_body(body, file_type(path.name), etag=etag)
                        return
                    relative = parse_qs(url.query).get("path", [""])[0]
                    source = pdf_page_source(root, relative)
                    if source:
                        try:
                            body = render_pdf_page(source, pdf_lock)
                        except ImportError:
                            self.send_json({"error": "Cần PyMuPDF để mở ảnh trang từ PDF."}, 503)
                            return
                        except (RuntimeError, ValueError):
                            self.send_json({"error": "Không dựng được ảnh trang từ PDF."}, 422)
                            return
                        if body:
                            self.send_body(body, "image/jpeg")
                            return
                self.send_json({"error": "Không tìm thấy tệp."}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as error:
                self.send_json({"error": str(error)}, 500)

        def do_PUT(self):
            url = urlsplit(self.path)
            if prefix and url.path != prefix and not url.path.startswith(prefix + "/"):
                self.send_error(501, f"Unsupported method ({self.command!r})")
                return
            if not self.check_host():
                return
            supplied_token = self.headers.get("X-Review-Token", "").encode("latin-1")
            if not secrets.compare_digest(supplied_token, token.encode("ascii")):
                self.send_json({"error": "Phiên lưu đã hết. Hãy tải lại trang web."}, 403)
                return
            try:
                path = self.resolve_file(url) if url.path == prefix + "/api/file" else None
                if path is None or path.name.lower() != LABEL_NAME.lower():
                    self.send_json({"error": "Không tìm thấy Label.txt."}, 404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Thiếu nội dung Label.txt.")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Nội dung Label.txt chưa được gửi đầy đủ.")
                body.decode("utf-8")
                with save_lock:
                    current = path.read_bytes()
                    if self.headers.get("If-Match") != revision(current):
                        self.send_json({"error": "Label.txt vừa thay đổi trên đĩa. Hãy kiểm tra lại trước khi lưu."}, 409)
                        return
                    replace_label(path, body)
                self.send_json({"saved": True})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
            except OSError as error:
                self.send_json({"error": str(error)}, 500)

    return Handler


def main():
    data_dir = WEB_DIR / "data"
    if not data_dir.is_dir():
        data_dir = WEB_DIR.parent / "data"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=data_dir, help="Folder containing the book data")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    root = args.data.resolve()
    if not root.is_dir():
        parser.error(f'Data folder not found: {root}. Pass --data "path to the data folder".')
    with ThreadingHTTPServer(("127.0.0.1", args.port), review_handler(root)) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Web doi chieu: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
