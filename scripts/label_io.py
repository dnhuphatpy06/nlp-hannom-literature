"""Đọc/ghi Label.txt và fileState.txt theo định dạng PPOCRLabel (docs/Quy_dinh_do_an.md, "Quy định về file OCR").

Label.txt      mỗi ảnh một dòng:  <thư mục>/<tên ảnh> TAB [{"transcription": "...", "points": [[x,y] x4]}, ...]
fileState.txt  mỗi ảnh một dòng:  <thư mục>/<tên ảnh> TAB 1 (đã hoàn thành) | 0 (chưa hoàn thành)

Mỗi box có đúng 4 điểm. Chỉ ghi hai trường transcription và points (quy định cho phép thêm trường khác nhưng
phải chú thích; điểm tin cậy được lưu ở cache/ocr_log.json thay vì Label.txt).
Tên file cố định: LABEL_NAME, STATE_NAME. Mọi ghi đều qua file tạm rồi đổi tên, nên dừng giữa chừng không hỏng file.
"""
from __future__ import annotations

import json
from pathlib import Path

from .library import write_text_atomic

LABEL_NAME = "Label.txt"
STATE_NAME = "fileState.txt"


def image_key(folder: str, filename: str) -> str:
    """Khóa ảnh trong Label.txt / fileState.txt: <thư mục>/<tên ảnh>."""
    return f"{folder}/{filename}"


def page_number(key: str) -> int:
    """'ch/Tên sách.012.jpg' -> 12 (để sắp xếp theo số trang); không tách được thì 0."""
    tail = key.rsplit("/", 1)[-1].rsplit(".", 2)
    return int(tail[-2]) if len(tail) == 3 and tail[-2].isdigit() else 0


def _sorted_keys(keys) -> list[str]:
    return sorted(keys, key=lambda k: (page_number(k), k))


def make_box(points, text: str) -> dict:
    """Box hợp lệ: đúng 4 điểm nguyên. Nhiều hơn/ít hơn 4 điểm thì lấy hình chữ nhật bao ngoài."""
    pts = [[int(round(float(x))), int(round(float(y)))] for x, y in points]
    if len(pts) != 4:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return {"transcription": text, "points": pts}


def rect_box(x1, y1, x2, y2, text: str) -> dict:
    return make_box([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], text)


def dumps_boxes(boxes: list[dict]) -> str:
    """JSON trên một dòng, gọn như ví dụ trong quy định."""
    return json.dumps([{"transcription": b["transcription"], "points": b["points"]} for b in boxes],
                      ensure_ascii=False, separators=(",", ":"))


def read_labels(path: Path) -> dict[str, str]:
    """{khóa ảnh: chuỗi JSON thô}. Giữ nguyên chuỗi thô để không làm đổi phần đã sửa tay trong PPOCRLabel."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, payload = line.partition("\t")
        if sep and key:
            out[key] = payload
    return out


def read_states(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("\t")
        if sep and key:
            out[key] = 1 if value.strip() == "1" else 0
    return out


def write_labels(path: Path, labels: dict[str, str]) -> None:
    text = "".join(f"{k}\t{labels[k]}\n" for k in _sorted_keys(labels))
    write_text_atomic(path, text)


def write_states(path: Path, states: dict[str, int]) -> None:
    text = "".join(f"{k}\t{1 if states[k] else 0}\n" for k in _sorted_keys(states))
    write_text_atomic(path, text)


def update_folder(folder_dir: Path, new_labels: dict[str, list[dict]], state: int = 0,
                  drop_keys=(), state_by_key: dict[str, int] | None = None) -> None:
    """Gộp nhãn mới vào <folder>/Label.txt và <folder>/fileState.txt.

    new_labels: {khóa ảnh: [box, ...]}; ảnh đã có dòng thì bị thay, ảnh khác giữ nguyên.
    state: trạng thái ghi cho các ảnh mới (0 = chưa hoàn thành, chờ kiểm tra bằng PPOCRLabel).
    state_by_key: {khóa ảnh: trạng thái} ghi đè `state` cho từng ảnh (ảnh không có trong đó dùng `state`).
    drop_keys: các khóa cần xóa khỏi cả hai file (ảnh đã bị chuyển khỏi thư mục này).
    """
    label_file, state_file = folder_dir / LABEL_NAME, folder_dir / STATE_NAME
    labels, states = read_labels(label_file), read_states(state_file)
    for key in drop_keys:
        labels.pop(key, None)
        states.pop(key, None)
    for key, boxes in new_labels.items():
        labels[key] = dumps_boxes(boxes)
        states[key] = state
    for key, value in (state_by_key or {}).items():
        if key in new_labels:
            states[key] = 1 if value else 0
    if labels or label_file.exists():
        write_labels(label_file, labels)
    if states or state_file.exists():
        write_states(state_file, states)


def validate_boxes(payload: str) -> str | None:
    """Trả về mô tả lỗi nếu chuỗi JSON của một dòng Label.txt sai quy định, ngược lại None."""
    try:
        boxes = json.loads(payload)
    except ValueError as e:
        return f"JSON sai: {e}"
    if not isinstance(boxes, list):
        return "không phải mảng"
    for i, b in enumerate(boxes, 1):
        if not isinstance(b, dict) or "transcription" not in b or "points" not in b:
            return f"box {i} thiếu transcription/points"
        pts = b["points"]
        if not (isinstance(pts, list) and len(pts) == 4 and all(isinstance(p, list) and len(p) == 2 for p in pts)):
            return f"box {i} phải có đúng 4 điểm"
    return None


def validate_folder(folder_dir: Path) -> list[str]:
    """Kiểm tra Label.txt / fileState.txt của một thư mục: khóa khớp nhau, ảnh tồn tại, mỗi box đúng 4 điểm."""
    problems: list[str] = []
    labels = read_labels(folder_dir / LABEL_NAME)
    states = read_states(folder_dir / STATE_NAME)
    for key, payload in labels.items():
        if key.split("/", 1)[0] != folder_dir.name:
            problems.append(f"{key}: tiền tố thư mục không phải '{folder_dir.name}/'")
        if not (folder_dir / key.split("/", 1)[-1]).is_file():
            problems.append(f"{key}: không thấy ảnh trong thư mục")
        err = validate_boxes(payload)
        if err:
            problems.append(f"{key}: {err}")
        if key not in states:
            problems.append(f"{key}: thiếu trong {STATE_NAME}")
    problems += [f"{key}: có trong {STATE_NAME} nhưng không có trong {LABEL_NAME}" for key in states if key not in labels]
    return problems
