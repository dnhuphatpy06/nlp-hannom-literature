"""Khởi động PPOCRLabel bản đã chép vào dự án (external/PPOCRLabel). Dùng chung cho app.py và open_ppocrlabel.bat.
Chạy bằng Python của môi trường có cài requirements-ocr.txt (không cần `pip install PPOCRLabel`, mã đã ở trong dự án):

    .venv\\Scripts\\python.exe scripts\\ppocrlabel_boot.py [--lang en]

Mã nguồn PPOCRLabel nằm ngay trong dự án để sửa trực tiếp (xem external/PPOCRLabel/CHANGES_GK.md: các chỗ đã sửa được
đánh dấu "[GK]" trong mã). File này chỉ làm hai việc:

1. Đặt external/ lên đầu sys.path để `PPOCRLabel` được nạp từ bản trong dự án, không phải bản trong site-packages.
2. Nạp torch TRƯỚC: nếu PyQt5 được nạp trước torch thì torch/lib/c10.dll lỗi "WinError 1114" trên Windows (đã thử riêng: chỉ cần
   PyQt5 rồi torch là đủ để lỗi, không liên quan paddle). PPOCRLabel nạp PyQt5 ngay từ đầu, và PaddleOCR-VL (qua engine_bridge)
   cần torch khi bạn chọn nó, nên torch phải có mặt trước.

PPOCRLabel không còn nạp mô hình nào lúc khởi động và không cần paddle/paddleocr: nhận dạng đi qua 3 engine của dự án
(scripts/engine_bridge.py, nối trong external/PPOCRLabel/libs/gk_ocr.py).
"""
import sys
from pathlib import Path

EXTERNAL = Path(__file__).resolve().parent.parent / "external"


def main() -> int:
    try:
        import torch  # noqa: F401  (phải nạp trước paddle/PyQt5, xem ghi chú đầu file)
    except Exception:
        pass
    sys.path.insert(0, str(EXTERNAL))
    preload_vintern = "--preload-vintern" in sys.argv   # chỉ open_ppocrlabel.bat truyền cờ này
    if preload_vintern:
        sys.argv.remove("--preload-vintern")
    if not any(a == "--lang" or a.startswith("--lang=") for a in sys.argv[1:]):
        # Không truyền --lang: dùng đúng giá trị mà web truyền khi mở PPOCRLabel (PPOCRLABEL_LANG / PPOCRLABEL_CMD trong .env)
        try:
            sys.path.insert(0, str(EXTERNAL.parent))
            from scripts import ocr_jobs
            sys.argv[1:1] = ["--lang", ocr_jobs._ppocrlabel_lang()]
        except Exception:
            pass
    if preload_vintern:
        # Giống bấm "Nạp" Vintern-1B trong khung OCR log: nạp ở nền (worker riêng, VINTERN_PYTHON), cửa sổ hiện ngay, không chờ.
        try:
            from scripts import engine_bridge   # cùng module (cùng VINTERN_WORKER) mà PPOCRLabel dùng qua libs/gk_ocr.bridge()
            engine_bridge.preload_model("vintern")
        except Exception as e:
            print(f"[canh bao] Khong nap truoc duoc Vintern: {e}", file=sys.stderr)
    from PPOCRLabel.PPOCRLabel import main as ppocrlabel_main

    return ppocrlabel_main()


if __name__ == "__main__":
    sys.exit(main())
