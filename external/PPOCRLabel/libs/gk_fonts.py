"""[GK] Nạp font Hán Nôm của dự án (thư mục font/ ở gốc dự án) vào Qt để PPOCRLabel hiển thị đúng chữ Hán Nôm.

Font nạp bằng QFontDatabase.addApplicationFont nên không cần cài vào Windows. Qt dùng chúng để thay thế khi font giao diện
thiếu glyph (danh sách nhãn, ô sửa text), còn nhãn vẽ trên ảnh dùng thẳng họ font đầu tiên (xem PPOCRLabel.MainWindow).
"""
from pathlib import Path

from PyQt5.QtCore import QSize
from PyQt5.QtGui import QFont, QFontDatabase, QFontMetrics
from PyQt5.QtWidgets import QStyledItemDelegate, QWidget

FONT_DIR = Path(__file__).resolve().parents[3] / "font"  # external/PPOCRLabel/libs -> gốc dự án
EXTENSIONS = {".ttf", ".otf", ".ttc"}
# Thứ tự ưu tiên: bộ font chuẩn của đồ án trước, phần còn lại theo tên file.
PREFERRED = ["HAN NOM A", "HAN NOM B", "Han-Nom Minh", "Han-Nom Gothic", "Han-Nom Khai", "Han-Nom Kai", "Nom Na Tong",
             "HanaMinA", "HanaMinB"]

_families: list[str] | None = None


def load_project_fonts() -> list[str]:
    """Nạp mọi font trong font/ (cần QApplication đã tạo). Trả về danh sách họ font đã nạp, đã sắp theo ưu tiên; gọi lại trả kết quả cũ."""
    global _families
    if _families is not None:
        return _families
    found: list[str] = []
    if FONT_DIR.is_dir():
        for path in sorted(FONT_DIR.iterdir()):
            if path.suffix.lower() not in EXTENSIONS:
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id < 0:
                print(f"[GK] Không nạp được font {path.name}")
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                if family not in found:
                    found.append(family)
    found.sort(key=lambda f: PREFERRED.index(f) if f in PREFERRED else len(PREFERRED))
    _families = found
    return found


def install_fallbacks(default_family: str) -> None:
    """Cho font giao diện mượn các font Hán Nôm khi thiếu glyph.

    Bảng thay thế của Qt khớp theo TÊN họ font. app.font().family() trên Windows thường là "MS Shell Dlg 2" (bí danh), còn widget thực tế
    vẽ bằng "Segoe UI" (stylesheet/theme phân giải bí danh đó), nên đăng ký riêng cho bí danh thì không bao giờ được dùng: Windows tự chọn font
    dự phòng cho từng chữ và một dòng Hán Nôm bị lẫn nhiều font (nét đậm/nhạt khác nhau, viền màu ClearType). Vì vậy đăng ký cho mọi tên có thể gặp."""
    families = load_project_fonts()
    if not families:
        return
    names = {default_family, QFont().family(), QWidget().font().family(), "Segoe UI", "MS Shell Dlg 2", "MS Shell Dlg", "Tahoma"}
    for name in names:
        if name:
            QFont.insertSubstitutions(name, families)


class _FixedRowDelegate(QStyledItemDelegate):
    """Dòng cao đúng `row_height` pixel, bất kể font dự phòng của chữ trong dòng."""

    row_height = 26

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), self.row_height)


def sync_row_height(list_widgets, widget_font: QFont) -> int:
    """Cho các QListWidget đặt cạnh nhau (danh sách số thứ tự và danh sách nhãn) cùng một chiều cao dòng để dòng i của hai bên luôn thẳng hàng.

    Mỗi QListWidget tự tính chiều cao dòng từ font của chữ trong nó: số thứ tự (Segoe UI, cao 1.33 em) thấp hơn chữ Hán Nôm (font dự phòng có thể
    cao tới 1.44 em), nên sau vài chục dòng hai cột lệch nhau cả dòng. Ở đây lấy chiều cao lớn nhất của font giao diện và font Hán Nôm đầu tiên."""
    height = QFontMetrics(widget_font).height()
    families = load_project_fonts()
    if families:
        han = QFont(widget_font)
        han.setFamily(families[0])
        height = max(height, QFontMetrics(han).height())
    height += 4
    for widget in list_widgets:
        delegate = widget.itemDelegate()
        if not isinstance(delegate, _FixedRowDelegate):
            delegate = _FixedRowDelegate(widget)
            widget.setItemDelegate(delegate)
        delegate.row_height = height
        widget.scheduleDelayedItemsLayout()
    return height
