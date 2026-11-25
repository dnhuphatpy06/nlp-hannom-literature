import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

api_dir = PROJECT_ROOT / "scripts" / "api"
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

import sinonom_service
import cv2
import numpy as np

book_name = "KimVânKiềuTânTruyện_金雲翹新傳"
book_dir = PROJECT_ROOT / "data" / book_name
pages_dir = book_dir / "cache" / "pages"
ch_dir = book_dir / "ch"
vi_dir = book_dir / "vi"
vi_dir.mkdir(parents=True, exist_ok=True)

ch_label_file = ch_dir / "Label.txt"
vi_label_file = vi_dir / "Label.txt"
vi_filestate_file = vi_dir / "fileState.txt"

# 1. Đọc ch/Label.txt
ch_labels = {}
for line in ch_label_file.read_text(encoding="utf-8").splitlines():
    if line.strip():
        k, _, v = line.partition("\t")
        ch_labels[k] = json.loads(v)

print(f"Tổng số trang Nôm trong ch/Label.txt: {len(ch_labels)}")

# 2. Xây dựng mapping: trang Nôm p -> trang Quốc ngữ p + 2
vi_labels = {}
copied_count = 0

for ch_key in sorted(ch_labels.keys()):
    fname = ch_key.replace("ch/", "")
    p_nom = int(fname.rsplit(".", 2)[1])
    p_vi = p_nom + 2
    
    vi_fname = f"{book_name}.{p_vi:03d}.jpg"
    src_page = pages_dir / vi_fname
    dst_page = vi_dir / vi_fname
    
    if src_page.exists():
        if not dst_page.exists() or dst_page.stat().st_size != src_page.stat().st_size:
            shutil.copyfile(src_page, dst_page)
            copied_count += 1
            
    # Lấy kích thước ảnh Quốc ngữ
    if dst_page.exists():
        im = cv2.imdecode(np.fromfile(str(dst_page), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        h, w = im.shape if im is not None else (6446, 4092)
    else:
        h, w = 6446, 4092
        
    nom_boxes = ch_labels[ch_key]
    n_boxes = len(nom_boxes)
    
    # Tính tọa độ 24 dòng cho trang Quốc ngữ
    # Dòng bắt đầu khoảng y=620, kết thúc khoảng y=5350
    # Chiều cao box khoảng 140px
    y_top = 620
    y_bot = 5350
    step = (y_bot - y_top) / max(1, n_boxes)
    
    vi_boxes_for_page = []
    vi_key = f"vi/{vi_fname}"
    
    for i, nb in enumerate(nom_boxes):
        y1 = int(round(y_top + i * step))
        y2 = int(round(y1 + min(step * 0.75, 150)))
        x1 = int(round(w * 0.15))
        x2 = int(round(w * 0.85))
        
        # Dùng câu Hán Nôm dịch âm hoặc giữ nhãn tương ứng
        vi_boxes_for_page.append({
            "transcription": "", # Ban đầu để chuỗi hoặc text
            "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "difficult": False,
        })
        
    vi_labels[vi_key] = vi_boxes_for_page

print(f"Đã đồng bộ {copied_count} file ảnh vào vi/")
print(f"Tổng số trang Quốc ngữ chuẩn bị gán nhãn: {len(vi_labels)}")

# 3. Ghi vi/Label.txt
with open(vi_label_file, "w", encoding="utf-8", newline="\n") as f:
    for k in sorted(vi_labels.keys()):
        f.write(f"{k}\t{json.dumps(vi_labels[k], ensure_ascii=False)}\n")

# 4. Ghi vi/fileState.txt
all_vi_imgs = sorted([p.name for p in vi_dir.glob("*.jpg")])
with open(vi_filestate_file, "w", encoding="utf-8", newline="\n") as f:
    for img_name in all_vi_imgs:
        rel = f"vi/{img_name}"
        status = "1" if rel in vi_labels and len(vi_labels[rel]) > 0 else "0"
        f.write(f"{rel}\t{status}\n")

print(f"Ghi thành công {vi_label_file.name} và {vi_filestate_file.name} ({len(all_vi_imgs)} file)")
