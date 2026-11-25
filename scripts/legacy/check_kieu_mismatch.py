import re

with open('data/truyen_kieu_quoc_ngu.txt', 'r', encoding='utf-8') as f:
    lines = [l.strip() for l in f if l.strip()]

for i, l in enumerate(lines):
    m = re.match(r'^(\d+)', l)
    if m:
        num = int(m.group(1))
        if num != i + 1:
            print(f"Mismatch at index {i}: num in text={num}, expected={i+1}")
            print(f"Prev line: {lines[i-1]}")
            print(f"Current line: {lines[i]}")
            break
