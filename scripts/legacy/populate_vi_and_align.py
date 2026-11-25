import json
import sys
import time
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
ch_dir = book_dir / "ch"
vi_dir = book_dir / "vi"

ch_label_file = ch_dir / "Label.txt"
vi_label_file = vi_dir / "Label.txt"

# Đọc ch/Label.txt
ch_labels = {}
for line in ch_label_file.read_text(encoding="utf-8").splitlines():
    if line.strip():
        k, _, v = line.partition("\t")
        ch_labels[k] = json.loads(v)

# Đọc vi/Label.txt
vi_labels = {}
if vi_label_file.exists():
    for line in vi_label_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            k, _, v = line.partition("\t")
            vi_labels[k] = json.loads(v)

sorted_keys = sorted(ch_labels.keys())
print(f"Bắt đầu dịch âm Quốc ngữ cho {len(sorted_keys)} trang...")

for idx, ch_key in enumerate(sorted_keys, 1):
    fname = ch_key.replace("ch/", "")
    p_nom = int(fname.rsplit(".", 2)[1])
    p_vi = p_nom + 2
    vi_fname = f"{book_name}.{p_vi:03d}.jpg"
    vi_key = f"vi/{vi_fname}"
    
    # Kiểm tra xem trang vi này đã có text chưa
    if vi_key in vi_labels and len(vi_labels[vi_key]) > 0:
        if all(b.get("transcription") for b in vi_labels[vi_key]):
            # Đã có text đầy đủ
            continue
            
    nom_boxes = ch_labels[ch_key]
    nom_texts = [b["transcription"].strip() for b in nom_boxes if b.get("transcription", "").strip()]
    if not nom_texts:
        continue
        
    print(f"[{idx}/{len(sorted_keys)}] Dịch âm trang Nôm {p_nom} -> Quốc ngữ {p_vi} ({len(nom_texts)} câu)...", flush=True)
    
    qn_lines = []
    for attempt in range(1, 4):
        try:
            batch_text = "\n".join(nom_texts)
            res = sinonom_service.transliterate(batch_text)
            qn_lines = res.get("result_text_transcription", [])
            if len(qn_lines) == len(nom_texts):
                break
        except Exception as e:
            print(f"  [WARN] Thử lại lần {attempt}/3: {e}", flush=True)
            time.sleep(2)
            
    if not qn_lines:
        print(f"  [ERROR] Không dịch được trang {p_nom}", flush=True)
        continue
        
    # Gán vào vi_labels
    if vi_key in vi_labels and len(vi_labels[vi_key]) == len(qn_lines):
        for i, qn in enumerate(qn_lines):
            vi_labels[vi_key][i]["transcription"] = qn
    else:
        # Tự sinh box nếu chưa có
        h, w = 6446, 4092
        y_top = 620
        y_bot = 5350
        step = (y_bot - y_top) / max(1, len(qn_lines))
        boxes = []
        for i, qn in enumerate(qn_lines):
            y1 = int(round(y_top + i * step))
            y2 = int(round(y1 + min(step * 0.75, 150)))
            x1 = int(round(w * 0.15))
            x2 = int(round(w * 0.85))
            boxes.append({
                "transcription": qn,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "difficult": False,
            })
        vi_labels[vi_key] = boxes
        
    # Ghi lại vi/Label.txt mỗi 5 trang
    if idx % 5 == 0 or idx == len(sorted_keys):
        with open(vi_label_file, "w", encoding="utf-8", newline="\n") as f:
            for k in sorted(vi_labels.keys()):
                f.write(f"{k}\t{json.dumps(vi_labels[k], ensure_ascii=False)}\n")

# Ghi lần cuối
with open(vi_label_file, "w", encoding="utf-8", newline="\n") as f:
    for k in sorted(vi_labels.keys()):
        f.write(f"{k}\t{json.dumps(vi_labels[k], ensure_ascii=False)}\n")

# Cập nhật fileState.txt
all_vi_imgs = sorted([p.name for p in vi_dir.glob("*.jpg")])
filestate_file = vi_dir / "fileState.txt"
with open(filestate_file, "w", encoding="utf-8", newline="\n") as f:
    for img_name in all_vi_imgs:
        rel = f"vi/{img_name}"
        status = "1" if rel in vi_labels and len(vi_labels[rel]) > 0 and vi_labels[rel][0]["transcription"] else "0"
        f.write(f"{rel}\t{status}\n")

print(f"\n===== HOÀN TẤT DỊCH ÂM & GÁN NHÃN QUỐC NGỮ: {len(vi_labels)} trang =====")
