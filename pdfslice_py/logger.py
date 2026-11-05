"""Logging, built on Python's stdlib logging.

Call sites use `logger.info("message", key=value, ...)` — extra keyword
arguments are the "meta" object, printed as JSON after the message and, if
`log_file` is set, also written as a JSON line per record.

Output is line-for-line identical to the `pdfslice` CLI's: `<level>: <message>
<compact JSON>`, meta keys camelCase (`output_folder=` is printed as
`outputFolder`), errors on stderr.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_COLOR_BY_LEVEL = {
    "debug": "\033[34m",  # blue
    "info": "\033[32m",  # green
    "warn": "\033[33m",  # yellow
    "error": "\033[31m",  # red
}
# Same reset code winston's colorize() emits, so lines match byte for byte.
_RESET = "\033[39m"


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _dumps(value: Any) -> str:
    # Compact separators and raw UTF-8 match JS JSON.stringify.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def utc_now_iso() -> str:
    """UTC timestamp like JS `new Date().toISOString()` (millis, trailing Z)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Keys the record itself owns. winston lets them win over same-named meta, so do the same.
_RESERVED = {"level", "message", "timestamp"}


def _clean_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    # None is dropped like JS `undefined` (JSON.stringify skips it).
    return {
        _camel(k): v
        for k, v in (meta or {}).items()
        if v is not None and _camel(k) not in _RESERVED
    }


def format_line(level: str, message: str, meta: dict[str, Any] | None = None) -> str:
    """One console line: `<level>: <message> <compact JSON meta>`."""
    camel_meta = _clean_meta(meta)
    extra = f" {_dumps(camel_meta)}" if camel_meta else ""
    return f"{_COLOR_BY_LEVEL.get(level, '')}{level}{_RESET}: {message}{extra}"


class Logger:
    def __init__(self, level: str, log_file: str | Path | None = None) -> None:
        self._level = level
        self._log_file = Path(log_file) if log_file is not None else None
        self._order = {"debug": 0, "info": 1, "warn": 2, "error": 3}

    def _enabled(self, level: str) -> bool:
        return self._order[level] >= self._order[self._level]

    def _emit(self, level: str, message: str, meta: dict[str, Any]) -> None:
        if not self._enabled(level):
            return

        stream = sys.stderr if level == "error" else sys.stdout
        print(format_line(level, message, meta), file=stream)

        if self._log_file is not None:
            record = {
                **_clean_meta(meta),
                "level": level,
                "message": message,
                "timestamp": utc_now_iso(),
            }
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(_dumps(record) + "\n")

    def debug(self, message: str, **meta: Any) -> None:
        self._emit("debug", message, meta)

    def info(self, message: str, **meta: Any) -> None:
        self._emit("info", message, meta)

    def warn(self, message: str, **meta: Any) -> None:
        self._emit("warn", message, meta)

    def error(self, message: str, **meta: Any) -> None:
        self._emit("error", message, meta)


def create_logger(
    verbose: bool = False, quiet: bool = False, log_file: str | Path | None = None
) -> Logger:
    level = "error" if quiet else "debug" if verbose else "info"
    return Logger(level=level, log_file=log_file)


# Keep the stdlib logging module importable/usable by consumers who'd rather
# wire pdfslice_py into their own logging config instead of using Logger above.
_stdlib_logger = logging.getLogger("pdfslice_py")
