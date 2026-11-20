import sys
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.auto_label_hannom_book import process_hannom_image

book_dir = PROJECT_ROOT / "data" / "KimVânKiềuTânTruyện_金雲翹新傳"
ch_dir = book_dir / "ch"
label_file = ch_dir / "Label.txt"

labels_dict = {}
if label_file.exists():
    for line in label_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            k, _, v = line.partition("\t")
            labels_dict[k] = json.loads(v)

missing_pages = [115, 116, 119, 120, 123, 124, 127, 128, 131, 132, 135, 136, 139, 140, 143, 144, 147, 148, 151, 152, 155, 156, 159, 160, 163, 164, 167, 168, 171]
print(f"Bắt đầu bổ sung {len(missing_pages)} trang Nôm còn thiếu...")

for idx, p in enumerate(missing_pages, 1):
    fname = f"KimVânKiềuTânTruyện_金雲翹新傳.{p:03d}.jpg"
    img_path = ch_dir / fname
    if not img_path.exists():
        print(f"[{idx}/{len(missing_pages)}] Không tìm thấy ảnh: {fname}")
        continue
    
    rel_key = f"ch/{fname}"
    if rel_key in labels_dict and len(labels_dict[rel_key]) >= 20:
        print(f"[{idx}/{len(missing_pages)}] Trang {p} đã có nhãn, bỏ qua.")
        continue
    
    print(f"[{idx}/{len(missing_pages)}] Đang xử lý trang {p} ({fname})...", flush=True)
    start_t = time.perf_counter()
    boxes = None
    for attempt in range(1, 4):
        try:
            boxes = process_hannom_image(img_path)
            if boxes:
                break
        except Exception as e:
            print(f"  [WARN] Thử lại lần {attempt}/3 do lỗi: {e}", flush=True)
            time.sleep(2)
            
    if not boxes:
        print(f"  [ERROR] Thất bại trang {p}", flush=True)
        continue
        
    labels_dict[rel_key] = boxes
    elapsed = time.perf_counter() - start_t
    print(f"  -> Xong {len(boxes)} boxes ({elapsed:.1f}s)", flush=True)
    
    # Ghi tạm vào Label.txt
    with open(label_file, "w", encoding="utf-8", newline="\n") as f:
        for k in sorted(labels_dict.keys()):
            f.write(f"{k}\t{json.dumps(labels_dict[k], ensure_ascii=False)}\n")

# Cập nhật fileState.txt
all_imgs = sorted([p.name for p in ch_dir.glob("*.jpg")])
filestate_file = ch_dir / "fileState.txt"
with open(filestate_file, "w", encoding="utf-8", newline="\n") as f:
    for img_name in all_imgs:
        rel = f"ch/{img_name}"
        status = "1" if rel in labels_dict and len(labels_dict[rel]) > 0 else "0"
        f.write(f"{rel}\t{status}\n")

print(f"\n===== HOÀN TẤT BỔ SUNG: Tổng {len(labels_dict)} trang Nôm đã có nhãn! =====")
