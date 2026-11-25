import re
from pathlib import Path

# Clean Truyen Kieu lines
raw_lines = Path('data/truyen_kieu_quoc_ngu.txt').read_text(encoding='utf-8').splitlines()
kieu_verses = []
for l in raw_lines:
    l = l.strip()
    if not l:
        continue
    # Remove leading number prefixes like '1..', '25.. ', '26. '
    cleaned = re.sub(r'^\d+[\.\s]+', '', l).strip()
    # Strip trailing punctuation if needed or keep standard
    if cleaned:
        kieu_verses.append(cleaned)

print(f"Total cleaned verses: {len(kieu_verses)}")
print("Verse 1-4:", kieu_verses[:4])
print("Verse 25-28:", kieu_verses[24:28])
print("Verse -4 to end:", kieu_verses[-4:])
