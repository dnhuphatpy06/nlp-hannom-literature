# [GK] Hộp thoại "Kiểm tra box" của PPOCRLabel: với ảnh đang mở, chỉ hiện ẢNH CẮT của từng box, chữ (sửa được) và cảnh báo (lệch số chữ API/projection,
# chưa có chữ), cùng ô "Đã check" cho từng box. Khi MỌI box đã check thì ảnh được đặt là đã check (fileState = 1).
#
# Phần xử lý nằm ở scripts/box_review.py của dự án (không phụ thuộc Qt); file này chỉ là giao diện. Nút gọi: PPOCRLabel.gkReview().
import cv2
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

IMAGE_MAX_WIDTH = 900    # ảnh cắt của box được co về tối đa ngần này pixel ngang
IMAGE_MAX_HEIGHT = 140
WARN_STYLE = "color:#b42318;font-weight:bold;"
OK_STYLE = "color:#667085;"
FLAGGED_STYLE = "QFrame#row{background:#fff4e5;border:1px solid #f0b46c;border-radius:6px;}"
CLEAN_STYLE = "QFrame#row{background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;}"


def review_module():
    """scripts/box_review.py của dự án (logic không phụ thuộc Qt)."""
    from libs.gk_ocr import bridge
    from scripts import box_review   # bridge() đã thêm thư mục gốc dự án vào sys.path
    bridge()
    return box_review


def _pixmap(crop):
    """Mảng BGR -> QPixmap, co về vừa khung hiển thị."""
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    pix = QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())
    return pix.scaled(IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class ReviewRow(QFrame):
    """Một box: ảnh cắt, ô chữ, cảnh báo, ô 'Đã check'."""

    def __init__(self, box, total, font_family, on_change):
        super().__init__()
        self.setObjectName("row")
        self.box = box
        self.on_change = on_change
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.title = QLabel("Box %d / %d" % (box["index"] + 1, total))
        self.title.setStyleSheet("font-weight:bold;")
        layout.addWidget(self.title)
        image = QLabel()
        if box["crop"] is not None:
            image.setPixmap(_pixmap(box["crop"]))
        else:
            image.setText("(không cắt được ảnh box)")
        layout.addWidget(image)
        line = QHBoxLayout()
        self.edit = QLineEdit(box["text"])
        font = QFont(font_family) if font_family else QFont()
        font.setPointSize(18)
        self.edit.setFont(font)
        self.edit.textChanged.connect(self._text_changed)
        line.addWidget(self.edit, 1)
        self.check = QCheckBox("Đã check")
        self.check.setChecked(not box["needs_review"])
        self.check.toggled.connect(lambda _=None: on_change())
        line.addWidget(self.check)
        layout.addLayout(line)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        layout.addWidget(self.warn)
        self._refresh_warning()

    def text(self):
        return self.edit.text()

    def flagged(self):
        return bool(self.reasons)

    def _refresh_warning(self):
        lines = review_module().warnings(self.box, self.text())
        self.reasons = lines
        if lines:
            self.warn.setText("⚠ " + " · ".join(lines))
            self.warn.setStyleSheet(WARN_STYLE)
            self.setStyleSheet(FLAGGED_STYLE)
        else:
            self.warn.setText("Không có cảnh báo")
            self.warn.setStyleSheet(OK_STYLE)
            self.setStyleSheet(CLEAN_STYLE)

    def _text_changed(self, _text):
        """Sửa chữ thì cảnh báo tính lại ngay (sửa cho khớp số chữ thì mất cảnh báo); người vẫn tự quyết định tick 'Đã check'."""
        self._refresh_warning()
        self.on_change()


class ReviewDialog(QDialog):
    """Hộp thoại kiểm tra các box của ảnh đang mở. `win`: cửa sổ chính PPOCRLabel (cung cấp gkReviewSave, gkReviewNext)."""

    def __init__(self, win, analysis, font_family=None):
        super().__init__(win)
        self.win, self.analysis = win, analysis
        self.setWindowTitle("Kiểm tra box")
        self.resize(1000, 760)
        root = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setStyleSheet("font-size:14px;font-weight:bold;")
        root.addWidget(self.summary)
        self.onlyPending = QCheckBox("Chỉ hiện box chưa check")
        self.onlyPending.setChecked(True)
        self.onlyPending.toggled.connect(self._apply_filter)
        root.addWidget(self.onlyPending)

        total = len(analysis["boxes"])
        self.rows = [ReviewRow(b, total, font_family, self._update_summary) for b in analysis["boxes"]]
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for row in self.rows:
            body_layout.addWidget(row)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.empty = QLabel("Không còn box nào cần check trong ảnh này.")
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty)

        buttons = QHBoxLayout()
        self.checkAll = QPushButton("Tick tất cả")
        self.checkAll.clicked.connect(self._check_all)
        self.saveBtn = QPushButton("Lưu")
        self.saveBtn.clicked.connect(lambda: self._save(False))
        self.nextBtn = QPushButton("Lưu và sang ảnh chưa check kế tiếp")
        self.nextBtn.clicked.connect(lambda: self._save(True))
        self.nextBtn.setDefault(True)
        close = QPushButton("Đóng")
        close.clicked.connect(self.reject)
        for w in (self.checkAll, None, self.saveBtn, self.nextBtn, close):
            if w is None:
                buttons.addStretch(1)
            else:
                buttons.addWidget(w)
        root.addLayout(buttons)
        self._apply_filter()
        self._update_summary()

    # ------------------------------------------------------------------ trạng thái
    def pending(self):
        """Box còn cảnh báo mà chưa tick 'Đã check'."""
        return [r for r in self.rows if r.flagged() and not r.check.isChecked()]

    def _update_summary(self):
        pending = len(self.pending())
        flagged = sum(r.flagged() for r in self.rows)
        name = self.analysis["path"].name
        state = "✔ mọi box đã check: ảnh sẽ được đặt là ĐÃ CHECK khi lưu" if not pending else "còn %d box chưa check: ảnh vẫn là CHƯA CHECK" % pending
        self.summary.setText("%s: %d box, %d có cảnh báo, %s" % (name, len(self.rows), flagged, state))

    def _apply_filter(self):
        only = self.onlyPending.isChecked()
        shown = 0
        for r in self.rows:
            visible = (not only) or (r.flagged() and not r.check.isChecked())
            r.setVisible(bool(visible))
            shown += bool(visible)
        self.empty.setVisible(shown == 0)

    def _check_all(self):
        for r in self.rows:
            r.check.setChecked(True)

    # ------------------------------------------------------------------ lưu
    def _save(self, go_next):
        checked = {r.box["key"] for r in self.rows if r.check.isChecked()}
        edits = {r.box["index"]: r.text() for r in self.rows if r.text() != r.box["text"]}
        done = self.win.gkReviewSave(self.analysis, edits, checked)
        for r in self.rows:
            r.box["text"] = r.text()   # chữ đã áp lên ảnh: lần Lưu sau không coi là sửa nữa
        if go_next:
            self.accept()
            self.win.gkReviewNext()
        elif done:
            self.accept()
        else:
            self._after_partial_save()

    def _after_partial_save(self):
        """Lưu xong nhưng còn box chưa check: giữ hộp thoại mở, ẩn các box đã xong theo bộ lọc."""
        self._apply_filter()
        self._update_summary()
