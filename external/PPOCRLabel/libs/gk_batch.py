# [GK] Hộp thoại "Chạy hàng loạt" của PPOCRLabel: chọn NHIỀU ảnh của thư mục đang mở (tick từng ảnh, tick cả khoảng, tick theo trạng thái check) rồi chạy
# MỘT việc do người dùng chọn cho tất cả ảnh đã tick:
#
#   both     phát hiện box và đọc chữ          (OCR cả trang, thay các box hiện có)
#   detect   chỉ phát hiện box                  (chữ để trống)
#   read     đọc lại chữ của mọi box            (giữ nguyên box, chỉ thay chữ)
#   check    đặt ảnh là ĐÃ CHECK (fileState 1)
#   uncheck  đặt ảnh là CHƯA CHECK (fileState 0)
#
# Hộp thoại chỉ thu lựa chọn; việc chạy do PPOCRLabel.gkRunBatch làm (cùng khung OCR log, nút Dừng, lưu sau từng ảnh).
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QPushButton,
    QVBoxLayout,
)

OCR_TASKS = ("both", "detect", "read")
STATE_TASKS = ("check", "uncheck")
# Nhãn ngắn kiểu tiêu đề; phần giải thích nằm ở dòng trợ giúp dưới ô chọn (TASK_HELP), không để trong ngoặc ở nhãn.
TASK_LABELS = {
    "both": "Phát hiện box và đọc chữ",
    "detect": "Chỉ phát hiện box",
    "read": "Đọc lại chữ",
    "check": "Đặt đã check",
    "uncheck": "Đặt chưa check",
}
# Tên engine trong ô chọn: ngắn, không ghi chú trong ngoặc; ghi chú nằm ở tooltip của từng dòng.
ENGINE_LABELS = {
    "kimhannom": ("Kim Hán Nôm", "Gọi API Kim Hán Nôm, cần TOKEN trong file .env"),
    "paddleocr": ("PaddleOCR-VL", "Mô hình chạy trên máy"),
    "row_projection": ("Tách hàng", "Tách hàng chữ ngang bằng xử lý ảnh (row_projection)"),
    "vintern": ("Vintern-1B", "Mô hình chạy trên máy, đọc chữ Quốc ngữ"),
}
TASK_HELP = {
    "both": "Nhận dạng cả trang: tìm box mới rồi đọc chữ trong từng box. Thay các box hiện có của ảnh.",
    "detect": "Nhận dạng cả trang nhưng chỉ tìm box, chữ để trống. Thay các box hiện có của ảnh.",
    "read": "Giữ nguyên box, chỉ đọc lại chữ của mọi box trong ảnh. Chữ cũ bị thay.",
    "check": "Đặt fileState là 1. Chỉ đổi trạng thái, không đụng tới box và chữ.",
    "uncheck": "Đặt fileState là 0. Chỉ đổi trạng thái, không đụng tới box và chữ.",
}


def parse_ranges(text, count):
    """'1-5, 8, 10-' -> {0, 1, 2, 3, 4, 7, 9, ...}: chỉ số 0-based của các dòng (số thứ tự 1-based trong danh sách, dải 'a-' là từ a tới hết,
    '-b' là từ đầu tới b). Số ngoài danh sách bị bỏ qua; ValueError nếu một phần không hiểu được."""
    out = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            try:
                start = int(left) if left.strip() else 1
                end = int(right) if right.strip() else count
            except ValueError:
                raise ValueError("Không hiểu khoảng %r" % part)
            if start > end:
                start, end = end, start
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise ValueError("Không hiểu số %r" % part)
        out.update(i - 1 for i in range(max(start, 1), min(end, count) + 1))
    return out


class BatchDialog(QDialog):
    """`items`: [{"path", "name", "state": 1|0|None, "boxes": số box}] theo thứ tự danh sách ảnh; `options`: engine_bridge.engine_options(thư mục);
    `win`: cửa sổ chính (lấy lựa chọn engine đã lưu để chọn sẵn, `engine_state.alternate`)."""

    def __init__(self, win, items, options, folder, bridge):
        super().__init__(win)
        self.items, self.options, self.folder, self.bridge = items, options, folder, bridge
        self.engine_state = win.engine_state
        self.setWindowTitle("Chạy hàng loạt")
        self.resize(900, 620)
        root = QHBoxLayout(self)

        # ---------------------------------------------------------------- trái: chọn ảnh
        left = QVBoxLayout()
        left.addWidget(QLabel("Chọn ảnh trong thư mục %s" % folder))
        self.fileList = QListWidget()
        self.fileList.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for item in items:
            self.fileList.addItem(self._make_item(item))
        self.fileList.itemChanged.connect(self._refresh)
        left.addWidget(self.fileList, 1)
        row = QHBoxLayout()
        for text, slot in (("Tất cả", lambda: self._set_all(True)), ("Bỏ hết", lambda: self._set_all(False)), ("Đảo chọn", self._invert),
                           ("Chưa check", lambda: self._tick_where(lambda it: it["state"] != 1)),
                           ("Đã check", lambda: self._tick_where(lambda it: it["state"] == 1))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        left.addLayout(row)
        row = QHBoxLayout()
        self.rangeEdit = QLineEdit()
        self.rangeEdit.setPlaceholderText("Khoảng ảnh, ví dụ 1-5, 8, 10-")
        self.rangeEdit.setToolTip("Số thứ tự trong danh sách. a- là từ a tới hết, -b là từ đầu tới b")
        self.rangeEdit.returnPressed.connect(self._tick_range)
        row.addWidget(self.rangeEdit, 1)
        button = QPushButton("Tick khoảng")
        button.clicked.connect(self._tick_range)
        row.addWidget(button)
        button = QPushButton("Tick dòng tô sáng")
        button.setToolTip("Chọn nhiều dòng bằng Shift/Ctrl + bấm (tô sáng) rồi bấm nút này để tick cùng lúc")
        button.clicked.connect(self._tick_highlighted)
        row.addWidget(button)
        left.addLayout(row)
        self.rangeNote = QLabel()
        self.rangeNote.setStyleSheet("color:#b42318;")
        left.addWidget(self.rangeNote)
        root.addLayout(left, 3)

        # ---------------------------------------------------------------- phải: việc cần làm
        right = QVBoxLayout()
        right.addWidget(QLabel("Việc cần làm"))
        self.taskBox = QComboBox()
        self.taskBox.setObjectName("batchTaskBox")
        for key in OCR_TASKS + STATE_TASKS:
            self.taskBox.addItem(TASK_LABELS[key], key)
        right.addWidget(self.taskBox)
        self.taskHelp = QLabel()
        self.taskHelp.setWordWrap(True)
        self.taskHelp.setStyleSheet("color:gray;")
        right.addWidget(self.taskHelp)
        self.boxLabel = QLabel("Phát hiện box")
        self.boxBox = QComboBox()
        self.boxBox.setObjectName("batchBoxBox")
        for option in options["boxes"]:
            self.boxBox.addItem(ENGINE_LABELS.get(option["key"], (option["label"], ""))[0], option["key"])
            self.boxBox.setItemData(self.boxBox.count() - 1, ENGINE_LABELS.get(option["key"], ("", ""))[1], Qt.ToolTipRole)
            self.boxBox.setItemData(self.boxBox.count() - 1, option["text"], Qt.UserRole + 1)
        self.textLabel = QLabel("Đọc chữ")
        self.textBox = QComboBox()
        self.textBox.setObjectName("batchTextBox")
        for option in options["texts"]:
            self.textBox.addItem(ENGINE_LABELS.get(option["key"], (option["label"], ""))[0], option["key"])
            self.textBox.setItemData(self.textBox.count() - 1, ENGINE_LABELS.get(option["key"], ("", ""))[1], Qt.ToolTipRole)
        parsed = bridge.eng.parse_pipeline(self.engine_state.engine)   # chọn sẵn theo lựa chọn đã lưu trong "Choose OCR model"
        if parsed:
            box, text = parsed
            self.boxBox.setCurrentIndex(max(0, self.boxBox.findData(box)))
            self.textBox.setCurrentIndex(max(0, self.textBox.findData(text)))
            task = self.engine_state.task
            if task in OCR_TASKS:
                self.taskBox.setCurrentIndex(max(0, self.taskBox.findData(task)))
        for widget in (self.boxLabel, self.boxBox, self.textLabel, self.textBox):
            right.addWidget(widget)
        self.alternateCheck = QCheckBox("Lọc xen kẽ hàng ch/vi")
        self.alternateCheck.setToolTip("Trang có cả chữ Hán và Quốc ngữ: hàng lẻ vào ch, hàng chẵn vào vi")
        self.alternateCheck.setChecked(self.engine_state.alternate)
        right.addWidget(self.alternateCheck)
        self.skipCacheCheck = QCheckBox("Bỏ qua cache")
        self.skipCacheCheck.setObjectName("batchSkipCache")
        self.skipCacheCheck.setToolTip("Gọi lại API hoặc mô hình dù ảnh đã có kết quả lưu. Đọc lại chữ không dùng cache nên không bị ảnh hưởng")
        right.addWidget(self.skipCacheCheck)
        self.skipCheck = QCheckBox("Bỏ qua ảnh đã check")
        self.skipCheck.setChecked(True)
        self.skipCheck.setToolTip("Ảnh đã check thường đã được soát tay: không nên bị nhận dạng đè lên")
        right.addWidget(self.skipCheck)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#b42318;")
        right.addWidget(self.note)
        right.addStretch(1)
        self.count = QLabel()
        self.count.setStyleSheet("font-weight:bold;")
        right.addWidget(self.count)
        buttons = QHBoxLayout()
        self.okBtn = QPushButton("Chạy")
        self.okBtn.setDefault(True)
        self.okBtn.clicked.connect(self.accept)
        cancel = QPushButton("Đóng")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.okBtn)
        buttons.addWidget(cancel)
        right.addLayout(buttons)
        root.addLayout(right, 2)

        self.taskBox.currentIndexChanged.connect(self._refresh)
        self.boxBox.currentIndexChanged.connect(self._refresh)
        self.textBox.currentIndexChanged.connect(self._refresh)
        self.skipCheck.toggled.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------ danh sách ảnh
    @staticmethod
    def _label(item, index):
        state = {1: "đã check", 0: "chưa check"}.get(item["state"], "chưa có dòng nhãn")
        return "%d. %s  ·  %s  ·  %d box" % (index + 1, item["name"], state, item["boxes"])

    def _make_item(self, item):
        widget = QListWidgetItem(self._label(item, self.fileList.count()))
        widget.setFlags(widget.flags() | Qt.ItemIsUserCheckable)
        widget.setCheckState(Qt.Unchecked)
        return widget

    def _rows(self):
        return [self.fileList.item(i) for i in range(self.fileList.count())]

    def _bulk(self, fn):
        self.fileList.blockSignals(True)
        try:
            fn()
        finally:
            self.fileList.blockSignals(False)
        self._refresh()

    def _set_all(self, on):
        self._bulk(lambda: [r.setCheckState(Qt.Checked if on else Qt.Unchecked) for r in self._rows()])

    def _invert(self):
        self._bulk(lambda: [r.setCheckState(Qt.Unchecked if r.checkState() == Qt.Checked else Qt.Checked) for r in self._rows()])

    def _tick_where(self, predicate):
        """Chọn CHỈ các ảnh thoả `predicate` (bỏ tick mọi ảnh khác)."""
        self._bulk(lambda: [r.setCheckState(Qt.Checked if predicate(self.items[i]) else Qt.Unchecked) for i, r in enumerate(self._rows())])

    def _tick_range(self):
        try:
            indexes = parse_ranges(self.rangeEdit.text(), len(self.items))
        except ValueError as e:
            self.rangeNote.setText(str(e))
            return
        self.rangeNote.setText("")
        rows = self._rows()
        self._bulk(lambda: [rows[i].setCheckState(Qt.Checked) for i in indexes])

    def _tick_highlighted(self):
        selected = self.fileList.selectedItems()
        self._bulk(lambda: [r.setCheckState(Qt.Checked) for r in selected])

    # ------------------------------------------------------------------ kết quả
    def indexes(self):
        return [i for i, r in enumerate(self._rows()) if r.checkState() == Qt.Checked]

    def paths(self):
        return [self.items[i]["path"] for i in self.indexes()]

    @property
    def task(self):
        return self.taskBox.currentData()

    @property
    def box(self):
        return self.boxBox.currentData()

    @property
    def text(self):
        return self.textBox.currentData()

    @property
    def alternate(self):
        return self.alternateCheck.isChecked() and not self.alternateCheck.isHidden()

    @property
    def use_cache(self):
        return not (self.skipCacheCheck.isChecked() and not self.skipCacheCheck.isHidden())

    @property
    def skip_checked(self):
        return self.skipCheck.isChecked() and self.task in OCR_TASKS

    # ------------------------------------------------------------------ đồng bộ ô chọn
    def _refresh(self, *_):
        task = self.taskBox.currentData()
        ocr = task in OCR_TASKS
        detects = task in ("both", "detect")
        reads = task in ("both", "read")
        self.boxLabel.setVisible(detects)
        self.boxBox.setVisible(detects)
        fixed = self.boxBox.currentData(Qt.UserRole + 1) if task == "both" else None
        if fixed:   # Kim Hán Nôm / PaddleOCR-VL đọc chữ cùng lúc với box: chữ do chính nó
            self.textBox.setCurrentIndex(max(0, self.textBox.findData(fixed)))
        self.textLabel.setVisible(reads)
        self.textBox.setVisible(reads)
        self.textBox.setEnabled(not fixed)
        alt = detects and self.folder in ("ch", "vi") and self.boxBox.currentData() == "row_projection"
        self.alternateCheck.setVisible(alt)
        self.skipCheck.setVisible(ocr)
        self.skipCacheCheck.setVisible(detects)
        self.taskHelp.setText(TASK_HELP.get(task, ""))
        problems = list(self.bridge.unavailable_reasons(task, self.boxBox.currentData(), self.textBox.currentData())) if ocr else []
        n = len(self.indexes())
        skipped = sum(1 for i in self.indexes() if self.items[i]["state"] == 1) if self.skip_checked else 0
        if ocr and task == "read":
            skipped += sum(1 for i in self.indexes() if self.items[i]["boxes"] == 0 and not (self.skip_checked and self.items[i]["state"] == 1))
        if not n:
            problems.append("Chưa tick ảnh nào.")
        self.note.setText("\n".join(problems))
        self.count.setText("Đã tick %d / %d ảnh" % (n, len(self.items))
                           + ("\nBỏ qua %d ảnh %s" % (skipped, "đã check hoặc chưa có box" if task == "read" else "đã check") if skipped else ""))
        self.okBtn.setEnabled(not problems)
        self.okBtn.setText("Chạy %d ảnh" % n if n else "Chạy")
