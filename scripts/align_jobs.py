"""Bước 3 của web: dóng hàng Nôm - Quốc ngữ cho một sách bằng scripts/align_books.py, chạy nền và báo trạng thái cho giao diện.

align_books.py được chạy như tiến trình riêng (cùng lệnh như dòng lệnh trong USER_GUIDE.md), nên web và dòng lệnh luôn ra cùng một kết quả:
  data/<Tên sách>/<Tên sách>.csv, <Tên sách>.xlsx và align_report.txt.
Mỗi sách tối đa một lượt chạy; trạng thái lượt gần nhất giữ trong bộ nhớ tới khi tắt app.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import align_rows, library

SCRIPT = Path(__file__).resolve().parent / "align_books.py"
REPORT_NAME = "align_report.txt"
REPORT_PREVIEW_CHARS = 8000
LOG_TAIL_CHARS = 4000

_lock = threading.Lock()
_jobs: dict[str, dict] = {}   # tên sách (NFC) -> {"status": running|done|error, "started", "finished", "pages", "output", "error"}


class AlignBusy(Exception):
    """Sách này đang được dóng hàng."""


def _key(book: library.Book) -> str:
    return library.norm(book.stem)


def output_files(book: library.Book) -> dict[str, Path]:
    """Các file kết quả của align_books.py theo loại tải về: csv | xlsx | report."""
    return {"csv": book.dir / f"{book.stem}.csv", "xlsx": book.dir / f"{book.stem}.xlsx", "report": book.dir / REPORT_NAME}


def _file_info(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime}


def status(book: library.Book) -> dict:
    """Trạng thái bước dóng hàng của sách: nhãn ch/vi đã có chưa, file kết quả, báo cáo và lượt chạy gần nhất."""
    labels = align_rows.source_counts(book)   # số ảnh có nhãn của nguồn Nôm (ch) và nguồn Việt (vi) đã chọn
    files = {kind: _file_info(path) for kind, path in output_files(book).items()}
    report = ""
    if files["report"]:
        try:
            report = output_files(book)["report"].read_text(encoding="utf-8")[:REPORT_PREVIEW_CHARS]
        except OSError:
            pass
    with _lock:
        job = dict(_jobs.get(_key(book)) or {"status": "idle"})
    return {"labels": labels, "ready": all(labels.values()), "files": files, "report": report, "job": job}


def _run(book: library.Book, pages: int) -> None:
    key = _key(book)
    cmd = [sys.executable, str(SCRIPT), "--book", book.stem, "--data", str(book.dir.parent), "--no-xlsx"]   # web chỉ xuất .csv
    if pages > 0:
        cmd += ["--pages", str(pages)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = {}
    try:
        proc = subprocess.run(cmd, capture_output=True, env=env, cwd=str(SCRIPT.parent.parent))
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
        result = {"status": "done" if proc.returncode == 0 else "error", "output": output[-LOG_TAIL_CHARS:]}
        if proc.returncode != 0:
            result["error"] = (output.splitlines() or ["align_books.py báo lỗi."])[-1]
    except Exception as e:   # không chạy được tiến trình
        result = {"status": "error", "error": f"{type(e).__name__}: {e}", "output": ""}
    with _lock:
        _jobs[key].update(result, finished=time.time())


def start(book: library.Book, pages: int = 0) -> None:
    """Chạy nền align_books.py cho `book` (pages: số trang Nôm đầu cần dóng, 0 = toàn bộ). AlignBusy nếu đang chạy."""
    if pages < 0:
        raise ValueError("Số trang không được âm.")
    with _lock:
        if (_jobs.get(_key(book)) or {}).get("status") == "running":
            raise AlignBusy("Sách này đang được dóng hàng.")
        _jobs[_key(book)] = {"status": "running", "started": time.time(), "pages": pages, "output": ""}
    threading.Thread(target=_run, args=(book, pages), daemon=True, name="align").start()
