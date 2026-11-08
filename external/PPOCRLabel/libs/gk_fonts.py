"""[GK] Nạp font Hán Nôm của dự án (thư mục font/ ở gốc dự án) vào Qt để PPOCRLabel hiển thị đúng chữ Hán Nôm.

Font nạp bằng QFontDatabase.addApplicationFont nên không cần cài vào Windows. Qt dùng chúng để thay thế khi font giao diện
thiếu glyph (danh sách nhãn, ô sửa text), còn nhãn vẽ trên ảnh dùng thẳng họ font đầu tiên (xem PPOCRLabel.MainWindow).
"""
from pathlib import Path

from PyQt5.QtGui import QFont, QFontDatabase

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
    """Cho font giao diện mặc định mượn các font Hán Nôm khi thiếu glyph."""
    families = load_project_fonts()
    if families:
        QFont.insertSubstitutions(default_family, families)
