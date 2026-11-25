import json
from pathlib import Path

p = Path('data/Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳/ch/Label.txt')
lines = p.read_text(encoding='utf-8').splitlines()

print(f"Total lines in space book: {len(lines)}")
for line in lines[:4]:
    k, v = line.split('\t', 1)
    boxes = json.loads(v)
    print(f"\n=== {k}: {len(boxes)} boxes ===")
    for idx in range(min(8, len(boxes))):
        b = boxes[idx]
        pts = b['points']
        cx = sum(pt[0] for pt in pts)/4
        cy = sum(pt[1] for pt in pts)/4
        txt = b.get('transcription', '')
        print(f"  box {idx+1}: cx={cx:.0f}, cy={cy:.0f}, text='{txt}'")
    if len(boxes) >= 28:
        print("  ...")
        for idx in range(24, 28):
            b = boxes[idx]
            pts = b['points']
            cx = sum(pt[0] for pt in pts)/4
            cy = sum(pt[1] for pt in pts)/4
            txt = b.get('transcription', '')
            print(f"  box {idx+1}: cx={cx:.0f}, cy={cy:.0f}, text='{txt}'")
