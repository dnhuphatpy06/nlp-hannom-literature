# [GK] Khung "OCR log" của PPOCRLabel: hiện việc đang làm, tiến độ, log từng bước và trạng thái các mô hình nhận dạng.
#
# Các engine (scripts/ocr_engines.py), bước đọc chữ (scripts/ocr_jobs.py) và cầu nối (scripts/engine_bridge.py) phát sự kiện qua scripts/ocr_events.py;
# khung này đăng ký nhận và vẽ lại. Sự kiện có thể đến từ luồng nền (AutoDialog chạy nhận dạng ở QThread) nên đi qua tín hiệu Qt. Với việc chạy
# NGAY TRONG luồng giao diện (Re-recognition, đọc lại chữ các box đã chọn) khung gọi processEvents() sau mỗi sự kiện để vẽ lại; lúc đó
# set_ui_busy() khoá mọi thứ khác (menu, canvas, các dock) để người dùng không bấm lung tung giữa chừng, chỉ còn nút Dừng của khung này bấm được.
import html
import logging
import time

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("PPOCRLabel")

LEVEL_COLORS = {"info": None, "ok": "#067647", "warn": "#b26a00", "error": "#b42318"}
STATE_STYLE = {"ready": ("●", "#067647"), "loading": ("◐", "#b26a00"), "idle": ("○", "#6a7282"),
               "error": ("●", "#b42318"), "unavailable": ("○", "#b42318")}
STATE_TEXT = {"ready": "sẵn sàng", "loading": "đang nạp…", "idle": "chưa nạp", "error": "lỗi", "unavailable": "chưa dùng được"}


def _bridge():
    from libs.gk_ocr import bridge
    return bridge()


def _events():
    from libs.gk_ocr import events
    return events()


class OcrLogDock(QDockWidget):
    """Dock dưới cùng: trạng thái + tiến độ + trạng thái mô hình + log. begin()/end() do cửa sổ chính gọi quanh mỗi lượt chạy."""

    eventReceived = pyqtSignal(object)
    MODELS = (("kimhannom", "Kim Hán Nôm (API)", False), ("paddleocr", "PaddleOCR-VL", True), ("vintern", "Vintern-1B", True))

    def __init__(self, parent=None):
        super().__init__("OCR log", parent)
        self.setObjectName("ocrLogDock")
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.running = False
        self.pump_ui = False        # đang chạy trong luồng giao diện: vẽ lại sau mỗi sự kiện
        self._pumping = False
        self._title = ""
        self._started = 0.0
        self._total = 0

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 4, 6, 6)
        self.status = QLabel("Chưa chạy")
        font = self.status.font()
        font.setBold(True)
        self.status.setFont(font)
        self.detail = QLabel("")
        self.detail.setStyleSheet("color: gray;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setFormat("%v/%m")
        layout.addWidget(self.status)
        layout.addWidget(self.detail)
        layout.addWidget(self.bar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setContentsMargins(0, 2, 0, 2)
        self.modelLabels, self.loadButtons = {}, {}
        for row, (key, name, loadable) in enumerate(self.MODELS):
            grid.addWidget(QLabel(name), row, 0)
            label = QLabel("")
            self.modelLabels[key] = label
            grid.addWidget(label, row, 1)
            if loadable:
                button = QPushButton("Nạp")
                button.setToolTip("Nạp mô hình ở nền (không cần chờ khi chạy nhận dạng)")
                button.clicked.connect(lambda _=False, k=key: self.load_model(k))
                self.loadButtons[key] = button
                grid.addWidget(button, row, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.stopBtn = QPushButton("Dừng")
        self.stopBtn.setToolTip("Dừng sau box hiện tại")
        self.stopBtn.setEnabled(False)
        self.stopBtn.clicked.connect(self.request_stop)
        self.clearBtn = QPushButton("Xoá log")
        self.clearBtn.clicked.connect(lambda: self.view.clear())
        self.copyBtn = QPushButton("Sao chép log")
        self.copyBtn.clicked.connect(lambda: QApplication.clipboard().setText(self.view.toPlainText()))
        for button in (self.stopBtn, self.clearBtn, self.copyBtn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(3000)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.view.setFont(mono)
        layout.addWidget(self.view, 1)
        self.setWidget(body)

        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._on_clock)
        self.clock.start()
        self.eventReceived.connect(self._on_event)
        self._listener = lambda event: self.eventReceived.emit(event)   # có thể được gọi từ luồng nền
        _events().subscribe(self._listener)
        self.destroyed.connect(lambda *_: self._unsubscribe())

    def _unsubscribe(self):
        try:
            _events().unsubscribe(self._listener)
        except Exception:
            pass

    # ------------------------------------------------------------ vẽ lại trong lúc luồng giao diện bị việc nặng chiếm
    def _pump(self):
        app = QApplication.instance()
        if self.pump_ui and not self._pumping and app is not None and QThread.currentThread() == app.thread():
            self._pumping = True
            try:
                app.processEvents()
            finally:
                self._pumping = False

    # ------------------------------------------------------------ một lượt chạy
    def begin(self, title, total=0, pump_ui=False):
        _events().clear_cancel()
        self.running, self.pump_ui, self._title, self._started, self._total = True, pump_ui, title, time.time(), total
        self.stopBtn.setEnabled(True)
        self.detail.setText("")
        self.set_progress(0, total, "")
        self.log("▶ " + title)
        self.show()
        self.refresh_models()
        self._on_clock()
        self._pump()

    def end(self, status="done"):
        seconds = time.time() - self._started
        self.running, self.pump_ui = False, False
        self.stopBtn.setEnabled(False)
        self.detail.setText("")
        if status == "done":
            if self._total:
                self.bar.setRange(0, self._total)
                self.bar.setValue(self._total)
            self.status.setText("Xong trong %.1f s" % seconds)
            self.log("✔ Xong trong %.1f s" % seconds, "ok")
        elif status == "cancelled":
            self.status.setText("Đã dừng sau %.1f s" % seconds)
            self.log("■ Đã dừng theo yêu cầu sau %.1f s" % seconds, "warn")
        else:
            self.status.setText("Lỗi sau %.1f s" % seconds)
            self.log("✖ Dừng vì lỗi sau %.1f s" % seconds, "error")
        self.refresh_models()

    def request_stop(self):
        _events().request_cancel()
        self.stopBtn.setEnabled(False)
        self.detail.setText("Đang dừng… (xong box hiện tại)")
        self.log("Đã yêu cầu dừng: sẽ dừng sau box đang đọc.", "warn")

    # ------------------------------------------------------------ hiển thị
    def set_progress(self, done, total, text):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
            self.bar.setFormat("%v/%m")
            if text:                 # bước hiện tại ("box 4/7") nằm ở dòng chi tiết dưới trạng thái, thanh chỉ hiện số xong/tổng
                self.detail.setText(("Đang đọc " + text) if text.startswith("box") or text.startswith("Box") else text)
        else:                        # chưa biết tổng: thanh chạy qua lại
            self.bar.setRange(0, 0)

    def log(self, message, level="info"):
        text = html.escape(str(message))
        stamp = time.strftime("%H:%M:%S")
        color = LEVEL_COLORS.get(level)
        line = f"<span style='color:gray'>{stamp}</span> " + (f"<span style='color:{color}'>{text}</span>" if color else text)
        self.view.appendHtml(line)

    def refresh_models(self):
        try:
            status = _bridge().model_status()
        except Exception as e:   # không lấy được trạng thái không được làm hỏng giao diện
            logger.debug("model_status: %s", e)
            return
        for key, _name, _loadable in self.MODELS:
            self._show_model(key, status.get(key, {}))

    def _show_model(self, key, info):
        state = info.get("state", "idle")
        dot, color = STATE_STYLE.get(state, STATE_STYLE["idle"])
        detail = str(info.get("detail") or "")
        text = STATE_TEXT.get(state, state) + (f" ({detail})" if detail and state in ("ready", "error", "unavailable") else "")
        self.modelLabels[key].setText(f"<span style='color:{color}'>{dot}</span> {html.escape(text)}")
        self.modelLabels[key].setToolTip(detail)
        button = self.loadButtons.get(key)
        if button is not None:
            button.setVisible(state in ("idle", "error"))

    def load_model(self, key):
        try:
            self.log(f"Nạp {dict((k, n) for k, n, _ in self.MODELS)[key]} ở nền…")
            _bridge().preload_model(key)
        except Exception as e:
            self.log(f"Không nạp được: {e}", "error")
        self.refresh_models()

    # ------------------------------------------------------------ sự kiện từ engine
    def _on_event(self, event):
        kind = event.get("kind")
        if kind == "log":
            self.log(event.get("message", ""), event.get("level", "info"))
        elif kind == "progress":
            self.set_progress(event.get("done", 0), event.get("total", 0), event.get("message", ""))
        elif kind == "model":
            self.log(event.get("message", ""), {"ready": "ok", "error": "error"}.get(event.get("state"), "info"))
            self._show_model(event.get("name"), {"state": event.get("state"), "detail": _detail(event)})
        elif kind == "tick":
            self.detail.setText("%s %.0f s" % (event.get("message", ""), event.get("seconds", 0)))
        self._pump()

    def _on_clock(self):
        if self.running:
            self.status.setText("%s  ·  %.0f s" % (self._title, time.time() - self._started))
        elif any(self.loadButtons.get(k) is not None and self.modelLabels[k].text().find("đang nạp") >= 0 for k in self.modelLabels):
            self.refresh_models()   # mô hình đang nạp ở nền: cập nhật trạng thái tới khi xong


def _detail(event):
    parts = [str(event.get("device") or ""), ("%ss" % event["seconds"]) if event.get("seconds") else ""]
    return " ".join(p for p in parts if p) or ("" if event.get("state") != "error" else str(event.get("message") or ""))


def set_ui_busy(window, keep, busy):
    """Khoá/mở menu, thanh công cụ, canvas và các dock khác của cửa sổ chính trong lúc chạy ở luồng giao diện, trừ dock `keep` (khung log, còn nút Dừng)."""
    if busy:
        if getattr(window, "_gk_busy_saved", None) is not None:
            return
        targets = [window.menuBar(), window.centralWidget(), *window.findChildren(QToolBar), *window.findChildren(QDockWidget)]
        window._gk_busy_saved = [(w, w.isEnabled()) for w in targets if w is not None and w is not keep]
        for widget, _was in window._gk_busy_saved:
            widget.setEnabled(False)
    else:
        for widget, was in getattr(window, "_gk_busy_saved", None) or []:
            widget.setEnabled(was)
        window._gk_busy_saved = None
