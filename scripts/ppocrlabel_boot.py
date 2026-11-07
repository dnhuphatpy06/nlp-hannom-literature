"""Khởi động PPOCRLabel bản đã chép vào dự án (third_party/PPOCRLabel). Dùng chung cho app.py và open_ppocrlabel.bat.
Chạy bằng Python của môi trường có cài requirements-ocr.txt (không cần `pip install PPOCRLabel`, mã đã ở trong dự án):

    .venv\\Scripts\\python.exe scripts\\ppocrlabel_boot.py [--lang en]

Mã nguồn PPOCRLabel nằm ngay trong dự án để sửa trực tiếp (xem third_party/PPOCRLabel/CHANGES_GK.md: các chỗ đã sửa được
đánh dấu "[GK]" trong mã). File này chỉ làm hai việc:

1. Đặt third_party/ lên đầu sys.path để `PPOCRLabel` được nạp từ bản trong dự án, không phải bản trong site-packages.
2. Nạp torch TRƯỚC: nếu PyQt5 được nạp trước torch thì torch/lib/c10.dll lỗi "WinError 1114" trên Windows (đã thử riêng: chỉ cần
   PyQt5 rồi torch là đủ để lỗi, không liên quan paddle). PPOCRLabel nạp PyQt5 ngay từ đầu, và PaddleOCR-VL (qua engine_bridge)
   cần torch khi bạn chọn nó, nên torch phải có mặt trước.

PPOCRLabel không còn nạp mô hình nào lúc khởi động và không cần paddle/paddleocr: nhận dạng đi qua 3 engine của dự án
(scripts/engine_bridge.py, nối trong third_party/PPOCRLabel/libs/gk_ocr.py).
"""
import sys
from pathlib import Path

THIRD_PARTY = Path(__file__).resolve().parent.parent / "third_party"


def main() -> int:
    try:
        import torch  # noqa: F401  (phải nạp trước paddle/PyQt5, xem ghi chú đầu file)
    except Exception:
        pass
    sys.path.insert(0, str(THIRD_PARTY))
    from PPOCRLabel.PPOCRLabel import main as ppocrlabel_main

    return ppocrlabel_main()


if __name__ == "__main__":
    sys.exit(main())
