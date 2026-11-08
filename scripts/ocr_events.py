"""Sự kiện log và tiến độ của các engine OCR, để giao diện (khung "OCR log" trong PPOCRLabel) hiện được việc đang làm.

Các engine, bước đọc chữ và cầu nối (engine_bridge) gọi emit()/log()/progress()/model()/tick(); ai muốn theo dõi thì subscribe() một hàm nhận
sự kiện. Không ai subscribe thì mọi lệnh gọi gần như không tốn gì (app web không dùng, nó có log riêng ở ocr_jobs). Hàm nhận được gọi NGAY trong luồng
phát sự kiện (có thể là luồng nền), nên nơi nhận cần tự chuyển sang luồng giao diện nếu cần (PPOCRLabel dùng tín hiệu Qt).

Một sự kiện là dict {"time", "kind", "message", ...}:
  log       message, level (info | warn | error | ok)           một dòng log
  progress  done, total, message                                tiến độ: đã xong `done` trong `total` (total 0 = chưa biết)
  model     name (kimhannom | paddleocr | vintern), state (loading | ready | error), message, device?, seconds?
  tick      message, seconds                                    nhịp khi đang chờ một việc dài (nạp mô hình, một lần đọc) để giao diện còn vẽ lại
  begin/end title, total / status (done | cancelled | error), message   mở đầu / kết thúc một lượt chạy

Huỷ: giao diện gọi request_cancel(); các vòng lặp theo từng box gọi check_cancel() giữa các box và nó ném Cancelled (BaseException, để các
`except Exception` chung của engine không nuốt mất). Lượt chạy mới gọi clear_cancel().
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_listeners: list = []
_cancel = threading.Event()


class Cancelled(BaseException):
    """Người dùng bấm Dừng; ném giữa các box (BaseException để không bị `except Exception` chung của engine nuốt)."""


def subscribe(listener):
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)
    return listener


def unsubscribe(listener) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def has_listeners() -> bool:
    return bool(_listeners)


def emit(kind: str, message: str = "", **data) -> None:
    if not _listeners:
        return
    event = {"time": time.time(), "kind": kind, "message": message, **data}
    for listener in list(_listeners):
        try:
            listener(event)
        except Exception:   # người nghe lỗi không được làm hỏng việc OCR
            pass


def log(message: str, level: str = "info", **data) -> None:
    emit("log", message, level=level, **data)


def progress(done: int, total: int, message: str = "", **data) -> None:
    emit("progress", message, done=done, total=total, **data)


def model(name: str, state: str, message: str = "", **data) -> None:
    emit("model", message, name=name, state=state, **data)


def tick(message: str, seconds: float) -> None:
    emit("tick", message, seconds=round(seconds, 1))


def begin(title: str, total: int = 0) -> None:
    emit("begin", title, total=total)


def end(status: str = "done", message: str = "") -> None:
    emit("end", message, status=status)


def request_cancel() -> None:
    _cancel.set()


def clear_cancel() -> None:
    _cancel.clear()


def is_cancelled() -> bool:
    return _cancel.is_set()


def check_cancel() -> None:
    """Gọi giữa các box/ảnh: ném Cancelled nếu người dùng đã bấm Dừng."""
    if _cancel.is_set():
        raise Cancelled()
