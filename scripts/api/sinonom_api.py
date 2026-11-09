"""Các hàm gọi API Kim Hán Nôm (CLC Sinonom). Chỉ gọi API và trả về nguyên JSON server trả về.

Cấu hình đọc từ file .env ở thư mục gốc dự án: TOKEN (bắt buộc), BASE_URL, PLATFORM, USER_AGENT, MIN_REQUEST_GAP (giây giữa 2 request, mặc định 1).
"""
import mimetypes
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

BASE_URL = (os.getenv("BASE_URL") or "https://kimhannom.fit.hcmus.edu.vn").rstrip("/")
PLATFORM = os.getenv("PLATFORM") or "web"  # web | mobile
# User-Agent KHÔNG được bắt đầu bằng "python-requests" (xem tài liệu).
USER_AGENT = os.getenv("USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
TIMEOUT = 120


def _read_gap() -> float:
    try:
        return max(0.0, float(os.getenv("MIN_REQUEST_GAP") or 1.0))
    except ValueError:
        return 1.0


# Giới hạn tốc độ: hai request liên tiếp (upload, OCR, dịch âm, từ mọi luồng) cách nhau ít nhất ngần này giây.
# Mặc định 1 s; đổi bằng MIN_REQUEST_GAP trong .env (0 = không giới hạn).
MIN_REQUEST_GAP = _read_gap()
_throttle_lock = threading.Lock()
_next_slot = 0.0   # thời điểm sớm nhất (time.monotonic) được gửi request kế tiếp


def _throttle() -> None:
    """Chờ đến lượt gửi: mỗi luồng giữ một khe riêng nên nhiều luồng gọi cùng lúc vẫn cách nhau đủ MIN_REQUEST_GAP."""
    global _next_slot
    if MIN_REQUEST_GAP <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        start = max(now, _next_slot)
        _next_slot = start + MIN_REQUEST_GAP
    if start > now:
        time.sleep(start - now)


def _get_token() -> str:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=True)
    return os.getenv("TOKEN", "")


def _post(endpoint: str, **kwargs) -> dict:
    _throttle()
    resp = requests.post(
        f"{BASE_URL}/api/{PLATFORM}/clc-sinonom/{endpoint}",
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {_get_token()}"},
        timeout=TIMEOUT,
        **kwargs,
    )
    return resp.json()


def upload_image(image_path) -> dict:
    """POST image-upload. Gửi file ảnh (field `image_file`)."""
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as f:
        return _post("image-upload", files={"image_file": (path.name, f, mime)})


def ocr(file_name, ocr_id=2, lang_type=2, epitaph=0, tag=None, reading_direction=None) -> dict:
    """POST image-ocr.

    ocr_id: 1 thường dọc, 2 hành chính, 3 ngoại cảnh, 4 thường ngang
    lang_type: 0 chưa biết, 1 Hán, 2 Nôm
    epitaph: 0 văn bản thường, 1 văn bia
    tag: None = mô hình mặc định, hoặc model_id được cấp riêng
    reading_direction: 1 dọc (mặc định), 2 ngang (không bắt buộc)
    """
    payload = {
        "ocr_id": ocr_id,
        "lang_type": lang_type,
        "epitaph": epitaph,
        "tag": tag,
        "file_name": file_name,
    }
    if reading_direction is not None:
        payload["reading_direction"] = reading_direction
    return _post("image-ocr", json=payload)


def transliterate(text, font_type=1, lang_type=1) -> dict:
    """POST sinonom-transliteration (dịch âm Hán-Nôm)."""
    return _post("sinonom-transliteration",
                 json={"text": text, "font_type": font_type, "lang_type": lang_type})
