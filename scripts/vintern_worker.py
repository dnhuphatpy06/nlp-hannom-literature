"""Tiến trình nền đọc chữ Quốc ngữ bằng Vintern-1B-v3.5 cho bước OCR của app (ocr_engines.VinternHolder khởi chạy file này).

Vintern cần transformers 4.x còn môi trường chính của app dùng transformers>=5.10 (PaddleOCR-VL), nên Vintern chạy trong môi trường
riêng (xem hướng dẫn ở đầu scripts/vintern_viz.py, biến VINTERN_PYTHON trong .env) và nói chuyện với app qua stdin/stdout.

Giao thức, mỗi dòng một JSON UTF-8:
  worker -> app:  {"ready": true, "device": "cuda"}      sau khi nạp xong mô hình (một lần)
                  {"fatal": "<lý do>"}                    nạp mô hình lỗi, tiến trình thoát
                  {"id": N, "text": "<chữ>"}              kết quả của yêu cầu N ("" nếu không đọc ra)
                  {"id": N, "error": "<lý do>"}           yêu cầu N lỗi, tiến trình vẫn chạy tiếp
  app -> worker:  {"id": N, "image": "<đường dẫn ảnh cắt>"}

Mô hình hỏi đáp đôi khi hỏng trên box khó: lặp vô hạn một từ/số ("ấm ấm ấm …", "1. 2. 3. …"), chép lại nguyên câu hỏi. Kết quả như vậy bị
phát hiện (looks_degenerate), thử lại một lần với phạt lặp mạnh hơn, vẫn hỏng thì trả "" để app coi là box chưa đọc được thay vì ghi chữ rác.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import vintern_viz cùng thư mục

RETRY_REPETITION_PENALTY = 2.5      # model card dùng 2.5; mặc định 1.1 ít làm đổi từ lặp hợp lệ nên chỉ dùng khi lần đầu hỏng
PROMPT_ECHO = ("chép lại chính xác", "không thêm, không bớt", "không giải thích")
_NUMBER = re.compile(r"^\d+[.,)]?$")


def looks_degenerate(text: str) -> bool:
    """Kết quả hỏng của mô hình: chép lại câu hỏi, một ký tự lặp dài, một từ/cụm lặp liên tiếp, hoặc chuỗi toàn số thứ tự."""
    text = text.strip()
    if not text:
        return False
    low = text.lower()
    if any(p in low for p in PROMPT_ECHO) or re.search(r"(\S)\1{9,}", text):
        return True
    words = low.split()
    for n in range(1, 7):   # cụm n từ (1 đến 6) lặp liên tiếp từ 5 lần trở lên
        run = 1
        for i in range(n, len(words) - n + 1, n):
            run = run + 1 if words[i:i + n] == words[i - n:i] else 1
            if run >= 5:
                return True
    return len(words) >= 8 and sum(bool(_NUMBER.match(w)) for w in words) / len(words) > 0.6


def read_line(predict, image) -> str:
    """Chữ trong một ảnh; kết quả hỏng thì thử lại một lần với phạt lặp mạnh, vẫn hỏng thì ''."""
    text = predict(image)
    if not looks_degenerate(text):
        return text
    text = predict(image, repetition_penalty=RETRY_REPETITION_PENALTY)
    return "" if looks_degenerate(text) else text


def main() -> None:
    protocol = sys.stdout
    sys.stdout = sys.stderr   # thư viện in gì cũng đi vào stderr (log), không lẫn vào kênh giao thức

    def send(message: dict) -> None:
        protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        import vintern_viz
        from PIL import Image
        args = vintern_viz.build_parser().parse_args(["-"])    # chỉ lấy tham số mặc định
        predict, device = vintern_viz.load_predictor(args)
    except SystemExit as e:   # load_predictor báo lỗi cài đặt bằng SystemExit
        send({"fatal": str(e)})
        return
    except Exception as e:
        send({"fatal": f"{type(e).__name__}: {e}"})
        return
    send({"ready": True, "device": device})

    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            with Image.open(request["image"]) as im:
                text = read_line(predict, im.convert("RGB"))
            send({"id": request_id, "text": text})
        except Exception as e:   # một ảnh lỗi không làm chết worker
            send({"id": request_id, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
