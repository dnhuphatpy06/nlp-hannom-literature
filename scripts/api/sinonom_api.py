"""Các hàm gọi API Kim Hán Nôm (CLC Sinonom). Chỉ gọi API và trả về nguyên JSON server trả về.

Cấu hình đọc từ file .env ở thư mục gốc dự án: TOKEN (bắt buộc), BASE_URL, PLATFORM, USER_AGENT.
"""
import mimetypes
import os
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


def _post(endpoint: str, **kwargs) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api/{PLATFORM}/clc-sinonom/{endpoint}",
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {os.getenv('TOKEN', '')}"},
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
