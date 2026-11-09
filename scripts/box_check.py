"""Kiểm tra chữ Kim Hán Nôm đọc được trong MỘT box (hàng chữ Nôm) và gắn cờ "cần người kiểm tra lại".

Mỗi box Nôm được đọc bằng API Kim Hán Nôm, rồi đối chiếu với số chữ đếm trên chính ảnh của box (scripts/char_projection.py), không phụ thuộc API:

  count_mismatch  số chữ API trả về khác số chữ đếm bằng chiếu dọc (API đọc thừa hoặc thiếu chữ)
  no_text         API không trả ra chữ nào (hoặc lỗi), chưa có gì để tin

Box không dính cờ nào là "sạch". Trang mà MỌI box Nôm đều sạch thì được ghi fileState = 1 (đã hoàn thành); chỉ cần một box dính cờ thì trang
giữ fileState = 0 (chưa check) để người xem lại bằng PPOCRLabel (xem ocr_jobs._run). Cờ không đổi nội dung Label.txt; chi tiết từng box nằm trong
cache/ocr_log.json (pages[<trang>].review) và dòng log của lượt chạy.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import char_projection  # noqa: E402

VERSION = 3   # tăng khi đổi thuật toán đếm chữ (char_projection): số đo đã cache với phiên bản khác bị đo lại

COUNT_MISMATCH = "count_mismatch"
NO_TEXT = "no_text"

REASON_LABELS = {
    COUNT_MISMATCH: "số chữ API khác số chữ đếm bằng projection",
    NO_TEXT: "chưa có chữ",
}


def api_count(text: str) -> int:
    """Số chữ API trả về (bỏ khoảng trắng; chữ Nôm ngoài BMP vẫn tính một chữ vì đếm theo ký tự Unicode)."""
    return sum(1 for ch in text if not ch.isspace())


def measure_crop(crop) -> dict:
    """Số đo từ ảnh box (mảng BGR của đúng vùng box, không đệm), không phụ thuộc chữ API nên cache cùng bản ghi chữ của box (ocr_jobs.row_text_pass):
    {"proj": số chữ đếm bằng chiếu dọc (None nếu không có ảnh), "check_v": VERSION}."""
    if crop is None or not getattr(crop, "size", 0):
        return {"proj": None, "check_v": VERSION}
    return {"proj": len(char_projection.char_segments(crop)), "check_v": VERSION}


def judge(text: str, measure: dict) -> dict:
    """Đối chiếu chữ `text` với số đo của ảnh (measure_crop).

    Trả về {"proj", "api": số chữ API, "reasons": [mã cờ]}; `reasons` rỗng nghĩa là box sạch."""
    api, proj = api_count(text), measure.get("proj")
    reasons = []
    if not api:
        reasons.append(NO_TEXT)
    elif api != proj:   # proj None (không cắt được ảnh): không chứng minh được số chữ nên cũng gắn cờ
        reasons.append(COUNT_MISMATCH)
    return {"proj": proj, "api": api, "reasons": reasons}


def check_crop(crop, text: str) -> dict:
    """measure_crop + judge: đo ảnh `crop` rồi đối chiếu với chữ `text`."""
    return judge(text, measure_crop(crop))


def describe(entry: dict) -> str:
    """Một dòng ngắn cho log: 'box 3: số chữ API 8 ≠ projection 7'."""
    parts = []
    for reason in entry.get("reasons", []):
        if reason == COUNT_MISMATCH:
            parts.append(f"số chữ API {entry.get('api')} ≠ projection {entry.get('proj')}")
        else:
            parts.append(REASON_LABELS.get(reason, reason))
    return f"box {entry.get('box')}: " + "; ".join(parts)
