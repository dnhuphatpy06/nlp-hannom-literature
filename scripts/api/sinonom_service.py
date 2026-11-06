"""Service gọi từ pipeline cho 3 endpoint Kim Hán Nôm.

Khác với sinonom_api.py (trả nguyên JSON), ở đây:
  - kiểm tra `is_success` / `code`, lỗi thì ném ApiError;
  - trả về phần `data` của response;
  - mỗi lần upload được ghi vào file JSON (mặc định <gốc dự án>/data/cache/uploads.json) và dùng lại
    nếu ảnh có cùng nội dung đã được upload, khỏi upload lại.

    from sinonom_service import upload, ocr, transliterate
    data = ocr("anh.png")                 # upload (nếu cần) rồi OCR
    print(data["result_ocr_text"])
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

import sinonom_api

SUCCESS_CODE = "000000"
FILE_NOT_FOUND_CODE = "000404"  # server không còn / không có file_name này

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOADS_FILE = Path(os.getenv("UPLOADS_FILE") or PROJECT_ROOT / "data" / "cache" / "uploads.json")


class ApiError(Exception):
    def __init__(self, message, code=None, debug_msg=None):
        super().__init__(message)
        self.code = code
        self.debug_msg = debug_msg


def _unwrap(call, *args, **kwargs):
    """Gọi một hàm của sinonom_api, kiểm tra kết quả và trả về `data`."""
    try:
        body = call(*args, **kwargs)
    except (requests.RequestException, ValueError) as e:
        raise ApiError(f"Không gọi được API: {e}") from e
    if not isinstance(body, dict) or not body.get("is_success") or body.get("code") != SUCCESS_CODE:
        body = body if isinstance(body, dict) else {}
        msg = body.get("message")
        if isinstance(msg, dict):
            title, text, debug = msg.get("title"), msg.get("text"), msg.get("debug_msg")
        else:
            title, text, debug = None, msg, None
        detail = " - ".join(str(x) for x in (title, text) if x) or "Lỗi không rõ"
        raise ApiError(f"[{body.get('code')}] {detail}", code=body.get("code"), debug_msg=debug)
    return body.get("data")


# --------------------------------------------------------------- lịch sử upload
def load_uploads(uploads_file=None) -> list[dict]:
    """Toàn bộ các lần upload đã ghi: [{image_path, sha256, file_name, uploaded_at}, ...]."""
    path = Path(uploads_file or UPLOADS_FILE)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_upload(record: dict, uploads_file=None) -> None:
    path = Path(uploads_file or UPLOADS_FILE)
    records = load_uploads(path)
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)  # data/cache/ có thể chưa tồn tại
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # ghi nguyên tử, lỡ dừng giữa chừng không hỏng file


def _relative_to_project(path: Path) -> str:
    """Đường dẫn tương đối từ gốc dự án (dấu /); ngoài dự án thì giữ đường dẫn tuyệt đối."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------- endpoints
def upload(image_path, force=False, uploads_file=None) -> str:
    """Upload ảnh, trả về `file_name` trên server.

    Nếu ảnh cùng nội dung đã upload trước đó (có trong file lịch sử) thì trả lại file_name cũ,
    trừ khi force=True. Mỗi lần upload thật đều được ghi thêm vào file lịch sử.
    """
    path = Path(image_path)
    if not path.is_file():
        raise ApiError(f"Không tìm thấy file ảnh: {path}")
    digest = _sha256(path)

    if not force:
        for rec in reversed(load_uploads(uploads_file)):
            if rec["sha256"] == digest:
                return rec["file_name"]

    file_name = _unwrap(sinonom_api.upload_image, path)["file_name"]
    _save_upload({
        "image_path": _relative_to_project(path),
        "sha256": digest,
        "file_name": file_name,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, uploads_file)
    return file_name


def ocr_file(file_name, **params) -> dict:
    """OCR một file đã có trên server. `params`: ocr_id, lang_type, epitaph, tag, reading_direction.

    Trả về `data`: result_ocr_text, result_bbox, result_file_name.
    """
    return _unwrap(sinonom_api.ocr, file_name, **params)


def ocr(image_path, uploads_file=None, **params) -> dict:
    """OCR một ảnh trên máy: upload (nếu chưa có) rồi OCR. `params` như ocr_file().

    Nếu file_name lấy từ lịch sử không còn trên server thì tự upload lại một lần.
    """
    file_name = upload(image_path, uploads_file=uploads_file)
    try:
        return ocr_file(file_name, **params)
    except ApiError as e:
        if e.code != FILE_NOT_FOUND_CODE:
            raise
    file_name = upload(image_path, force=True, uploads_file=uploads_file)
    return ocr_file(file_name, **params)


def transliterate(text, font_type=1, lang_type=1) -> dict:
    """Dịch âm Hán-Nôm. Trả về `data`: result_hannom_text, result_text_transcription (đều là list)."""
    return _unwrap(sinonom_api.transliterate, text, font_type=font_type, lang_type=lang_type)
