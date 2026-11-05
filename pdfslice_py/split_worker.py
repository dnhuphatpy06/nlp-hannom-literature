"""Worker-process entry point, mirroring pdfslice's split-worker.ts.

One process per PDF, so a crash or memory blowup on one book only kills that
process, never the parent orchestrating the whole batch.

Protocol (over a multiprocessing Pipe, dicts only): any number of
{"progress": {...}} messages, then either {"ok": True, "result": {...}} or
{"ok": False, "error": {...}, "stage": ..., "page": ...}, and exit.
"""

from __future__ import annotations

import faulthandler
import os
from dataclasses import asdict
from multiprocessing.connection import Connection

from .failure import describe_error
from .split_one_file import SplitOneOptions, SplitProgress, split_one_file


def rss_mb() -> int | None:
    """Current resident memory in MB (Linux); None where unavailable."""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            resident_pages = int(f.read().split()[1])
        return round(resident_pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024)
    except (OSError, ValueError, IndexError):
        return None


def run_worker(opts: SplitOneOptions, conn: Connection) -> None:
    # A native crash inside PDFium prints a Python traceback to the inherited stderr.
    faulthandler.enable()
    last: SplitProgress = {"stage": "start"}

    def on_progress(progress: SplitProgress) -> None:
        nonlocal last
        last = progress
        conn.send({"progress": {**progress, "rss_mb": rss_mb()}})

    try:
        try:
            result = split_one_file(opts, on_progress)
            conn.send({"ok": True, "result": asdict(result)})
        except Exception as err:
            conn.send(
                {
                    "ok": False,
                    "error": describe_error(err),
                    "stage": last.get("stage"),
                    "page": last.get("page"),
                }
            )
    finally:
        # Connection.send is synchronous, so nothing is left unflushed here.
        conn.close()
