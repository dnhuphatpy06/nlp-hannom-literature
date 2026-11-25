import cv2
import numpy as np
import json
import re
from pathlib import Path
from PIL import Image

# 1. Load clean 3254 verses
raw_verses = Path('data/truyen_kieu_wikisource.txt').read_text(encoding='utf-8').splitlines()
verses = []
for v in raw_verses:
    v = v.strip()
    if not v:
        continue
    # remove wikisource tags like {{số|25}}
    v = re.sub(r'\{\{.*?\}\}\s*', '', v)
    # clean punctuation or keep natural
    v = re.sub(r'[,;\.\?!]+$', '', v).strip()
    if v:
        verses.append(v)

print(f"Total clean verses: {len(verses)} (expected 3254)")

# 2. Build mapping of 136 vi pages
# Pairs of pages:
# k=0..67: p1 = 4k + 14, p2 = 4k + 17
vi_pages = []
for k in range(68):
    p1 = 4*k + 14
    p2 = 4*k + 17
    # slice of verses
    v_slice1 = verses[48*k : 48*k + 24]
    v_slice2 = verses[48*k + 24 : 48*k + 48]
    if k == 67: # last page 285 has 14 verses (3241-3254) + conclusion line
        v_slice2 = verses[48*k + 24 :]
        v_slice2.append("Kim Vân Kiều truyện quyển chung")
    vi_pages.append((p1, v_slice1))
    vi_pages.append((p2, v_slice2))

print(f"Total mapped vi pages: {len(vi_pages)}")
p_first, v_first = vi_pages[0]
print(f"Page {p_first}: {len(v_first)} verses. First: '{v_first[0]}', Last: '{v_first[-1]}'")
p_second, v_second = vi_pages[1]
print(f"Page {p_second}: {len(v_second)} verses. First: '{v_second[0]}', Last: '{v_second[-1]}'")
p_last, v_last = vi_pages[-1]
print(f"Page {p_last}: {len(v_last)} verses. First: '{v_last[0]}', Last: '{v_last[-1]}'")
