import json
from pathlib import Path

p = Path('data/Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳/ch/Label.txt')
lines = p.read_text(encoding='utf-8').splitlines()

print(f"Total pages: {len(lines)}")
issues = []
for line_idx, line in enumerate(lines):
    if '\t' not in line:
        issues.append(f"Line {line_idx+1}: missing tab separator")
        continue
    k, v = line.split('\t', 1)
    boxes = json.loads(v)
    
    # Check text
    for b_idx, b in enumerate(boxes):
        txt = b.get('transcription', '')
        if not txt.strip():
            issues.append(f"{k} box {b_idx+1}: EMPTY transcription")
        if ' ' in txt:
            issues.append(f"{k} box {b_idx+1}: contains SPACE: '{txt}'")
            
        pts = b.get('points', [])
        if len(pts) != 4:
            issues.append(f"{k} box {b_idx+1}: invalid points count ({len(pts)})")
        
        # Check box area / degenerate
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w < 10 or h < 20:
            issues.append(f"{k} box {b_idx+1}: unusually small box {w}x{h}")

print(f"Detailed check results on Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳:")
print(f"Total issues: {len(issues)}")
for iss in issues[:30]:
    print("  *", iss)
if len(issues) > 30:
    print(f"  ... and {len(issues)-30} more")
