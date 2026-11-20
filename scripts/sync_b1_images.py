import sys
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import library, ocr_jobs, label_io

data_dir = Path('data')
books = library.list_books(data_dir)
book = [b for b in books if 'KimVânKiềuTânTruyện_金雲翹新傳' == b.stem][0]

tags = ocr_jobs.page_tags(book)
print(f"Total tags: {len(tags)}")
tag_counts = {}
for t in tags.values():
    tag_counts[t] = tag_counts.get(t, 0) + 1
print(f"Tag distribution: {tag_counts}")

# Target folders: ch, vi, intro, misc
pages_dir = book.pages_dir
ch_dir = book.dir / 'ch'
vi_dir = book.dir / 'vi'
intro_dir = book.dir / 'intro'
misc_dir = book.dir / 'misc'

for d in [ch_dir, vi_dir, intro_dir, misc_dir]:
    d.mkdir(parents=True, exist_ok=True)

# 1. Populate images into correct folders from pages_dir (or ch_dir)
for pnum, tag in tags.items():
    img_name = f"{book.stem}.{pnum:03d}.jpg"
    target_dir = book.dir / tag
    target_img = target_dir / img_name
    
    # Source image
    src_img = pages_dir / img_name
    if not src_img.exists():
        src_img = ch_dir / img_name
        
    if src_img.exists() and not target_img.exists():
        shutil.copy2(src_img, target_img)

# 2. Clean up ch/: only keep pages with tag == 'ch'
ch_files = list(ch_dir.glob("*.jpg"))
for f in ch_files:
    stem, _, num = f.stem.rpartition('.')
    if num.isdigit():
        pnum = int(num)
        if tags.get(pnum) != 'ch':
            # Remove non-ch images from ch
            f.unlink()

# 3. Update fileState.txt for ch and vi
for folder, expected_tag in [('ch', 'ch'), ('vi', 'vi')]:
    fdir = book.dir / folder
    lbl_file = fdir / 'Label.txt'
    fst_file = fdir / 'fileState.txt'
    
    labels = label_io.read_labels(lbl_file)
    folder_imgs = sorted([f.name for f in fdir.glob("*.jpg")])
    
    # Write fileState.txt matching folder images
    states = {}
    for img_name in folder_imgs:
        key = f"{folder}/{img_name}"
        states[key] = 1 if key in labels else 0
        
    label_io.write_states(fst_file, states)
    print(f"Updated {folder}: {len(folder_imgs)} images, {len(labels)} labeled in Label.txt, {len(states)} in fileState.txt")

# Check intro and misc
print(f"intro/ has {len(list(intro_dir.glob('*.jpg')))} images")
print(f"misc/ has {len(list(misc_dir.glob('*.jpg')))} images")
