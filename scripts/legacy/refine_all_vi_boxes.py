import sys
import shutil
import cv2
import numpy as np
import json
import re
from pathlib import Path
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import library, label_io

# 1. Load clean 3254 verses
raw_verses = Path('data/truyen_kieu_wikisource.txt').read_text(encoding='utf-8').splitlines()
verses = []
for v in raw_verses:
    v = v.strip()
    if not v:
        continue
    v = re.sub(r'\{\{.*?\}\}\s*', '', v)
    v = re.sub(r'[,;\.\?!]+$', '', v).strip()
    if v:
        verses.append(v)

# 2. Build mapping of 136 vi pages
vi_pages = []
for k in range(68):
    p1 = 4*k + 14
    p2 = 4*k + 17
    v_slice1 = verses[48*k : 48*k + 24]
    v_slice2 = verses[48*k + 24 : 48*k + 48]
    if k == 67: # last page 285
        v_slice2 = verses[48*k + 24 :]
        v_slice2.append("Kim Vân Kiều truyện quyển chung")
    vi_pages.append((p1, v_slice1))
    vi_pages.append((p2, v_slice2))

data_dir = Path('data')
book = [b for b in library.list_books(data_dir) if 'KimVânKiềuTânTruyện_金雲翹新傳' == b.stem][0]
pages_dir = book.pages_dir
vi_dir = book.dir / 'vi'
vi_dir.mkdir(parents=True, exist_ok=True)

# 3. Synchronize image files in vi/
valid_vi_pnums = {p for p, _ in vi_pages}
# Copy all valid vi images from pages_dir
for pnum in sorted(valid_vi_pnums):
    img_name = f"{book.stem}.{pnum:03d}.jpg"
    src = pages_dir / img_name
    dst = vi_dir / img_name
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)

# Remove any non-vi images from vi/
for f in vi_dir.glob("*.jpg"):
    num = f.stem.rpartition('.')[-1]
    if num.isdigit() and int(num) not in valid_vi_pnums:
        f.unlink()

print(f"Total vi images present: {len(list(vi_dir.glob('*.jpg')))}")

# 4. Process each page to detect tight bounding boxes per line
labels_dict = {}

def detect_lines(img_path, num_lines):
    img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Margin numbers at x < 650, punch holes at x > 3350
    # Bottom margin notes at y > 5500
    y_min_bound = int(h * 0.08)
    y_max_bound = int(h * 0.86)
    x_min_bound = int(w * 0.15)
    x_max_bound = int(w * 0.83)
    
    crop = gray[y_min_bound:y_max_bound, x_min_bound:x_max_bound]
    th = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)
    
    proj_y = np.sum(th > 0, axis=1)
    smooth_y = gaussian_filter1d(proj_y.astype(float), sigma=15)
    
    active_indices = np.where(smooth_y > np.max(smooth_y) * 0.08)[0]
    if len(active_indices) > 0:
        top_y = y_min_bound + active_indices[0]
        bot_y = y_min_bound + active_indices[-1]
    else:
        top_y, bot_y = int(h * 0.09), int(h * 0.84)
        
    line_step = (bot_y - top_y) / float(num_lines)
    
    boxes = []
    for i in range(num_lines):
        y_center = top_y + (i + 0.5) * line_step
        y1 = max(0, int(y_center - line_step * 0.54))
        y2 = min(h, int(y_center + line_step * 0.54))
        
        # Analyze x range for this line
        line_crop = th[max(0, y1 - y_min_bound):min(th.shape[0], y2 - y_min_bound), :]
        proj_x = np.sum(line_crop > 0, axis=0)
        smooth_x = gaussian_filter1d(proj_x.astype(float), sigma=10)
        
        active_x = np.where(smooth_x > np.max(smooth_x) * 0.08)[0]
        if len(active_x) > 0:
            x1 = x_min_bound + max(0, active_x[0] - 25)
            x2 = x_min_bound + min(crop.shape[1], active_x[-1] + 25)
        else:
            x1 = int(w * 0.18)
            x2 = int(w * 0.78)
            
        boxes.append([[int(x1), int(y1)], [int(x2), int(y1)], [int(x2), int(y2)], [int(x1), int(y2)]])
    return boxes

print("Detecting tight boxes & assigning exact text for all 136 vi pages...")
for idx, (pnum, verse_list) in enumerate(vi_pages):
    img_name = f"{book.stem}.{pnum:03d}.jpg"
    img_path = vi_dir / img_name
    key = f"vi/{img_name}"
    
    boxes = detect_lines(img_path, len(verse_list))
    if not boxes:
        print(f"Failed to load image for page {pnum}!")
        continue
        
    page_data = []
    for b_pts, text in zip(boxes, verse_list):
        page_data.append({
            "transcription": text,
            "points": b_pts,
            "difficult": False
        })
    labels_dict[key] = json.dumps(page_data, ensure_ascii=False)
    if (idx + 1) % 20 == 0 or idx == len(vi_pages) - 1:
        print(f"  Processed {idx + 1}/136 pages (Page {pnum}: {len(page_data)} boxes)")

# 5. Write vi/Label.txt and vi/fileState.txt
lbl_file = vi_dir / 'Label.txt'
fst_file = vi_dir / 'fileState.txt'

lines_lbl = [f"{k}\t{v}" for k, v in sorted(labels_dict.items())]
lbl_file.write_text('\n'.join(lines_lbl) + '\n', encoding='utf-8')

states = {k: 1 for k in labels_dict}
lines_fst = [f"{k}\t{states[k]}" for k in sorted(states)]
fst_file.write_text('\n'.join(lines_fst) + '\n', encoding='utf-8')

print(f"Successfully wrote {len(labels_dict)} pages to vi/Label.txt and vi/fileState.txt!")

# 6. Update cache/phan_loai.json
b1_cache = book.page_cache_file
if b1_cache.exists():
    pl_data = json.loads(b1_cache.read_text(encoding='utf-8'))
    for pnum_str, pinfo in pl_data['pages'].items():
        pn = int(pnum_str)
        if pn in valid_vi_pnums:
            pinfo['tag'] = 'vi'
        elif pn <= 13:
            pinfo['tag'] = 'intro'
        elif pn > 285:
            pinfo['tag'] = 'misc'
        else:
            pinfo['tag'] = 'ch'
    b1_cache.write_text(json.dumps(pl_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print("Updated phan_loai.json!")
