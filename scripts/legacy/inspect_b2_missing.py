import json
from pathlib import Path

p = Path('data/KimVânKiềuTânTruyện_金雲翹新傳_2/ch/Label.txt')
lines = p.read_text(encoding='utf-8').splitlines()

pages = {}
for line in lines:
    k, v = line.split('\t', 1)
    pages[k] = json.loads(v)

# Check page 028, 038, 042
for pnum_str in ['028', '038', '042', '058']:
    matching = [k for k in pages if f'.{pnum_str}.jpg' in k]
    if not matching:
        continue
    k = matching[0]
    boxes = pages[k]
    print(f"\n=== {k}: {len(boxes)} boxes ===")
    # Sort boxes by cx descending (or as they are)
    # Check why it's missing 1 or 2 boxes
    for idx, b in enumerate(boxes):
        pts = b['points']
        cx = sum(pt[0] for pt in pts)/4
        cy = sum(pt[1] for pt in pts)/4
        txt = b.get('transcription', '')
        print(f"  [{idx+1}] cx={cx:.0f}, cy={cy:.0f}: {txt}")
