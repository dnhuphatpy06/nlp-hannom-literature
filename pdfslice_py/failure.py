"""Error detail and progress tracking, so a failure says where it happened."""

from __future__ import annotations

import errno
import traceback
from typing import Any


def describe_error(err: BaseException, depth: int = 0) -> dict[str, Any]:
    """Flatten an exception into a JSON-safe dict, including `__cause__` chains.

    Same shape as the `pdfslice` CLI's: name, message, stack, optional code
    (errno name such as ENOENT) and cause.
    """
    detail: dict[str, Any] = {
        "name": type(err).__name__,
        "message": str(err),
        "stack": "".join(traceback.format_exception(type(err), err, err.__traceback__)),
    }
    code = getattr(err, "errno", None)
    if isinstance(code, int):
        detail["code"] = errno.errorcode.get(code, code)
    cause = err.__cause__
    if cause is not None:
        detail["cause"] = describe_error(cause, depth + 1) if depth < 3 else str(cause)
    return detail


_current: dict[str, Any] = {}


def set_progress(**progress: Any) -> None:
    """Remember what the process is doing right now (pdf_path, folder, stage, page).

    Read back when an error is logged, so the error names the stage and page.
    """
    global _current
    _current = {k: v for k, v in progress.items() if v is not None}


def get_progress() -> dict[str, Any]:
    return dict(_current)
