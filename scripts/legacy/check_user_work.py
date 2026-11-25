import json
import re
from pathlib import Path

def check_book_1():
    b1_lbl = Path('data/KimVânKiềuTânTruyện_金雲翹新傳/ch/Label.txt')
    lines = b1_lbl.read_text(encoding='utf-8').splitlines()

    user_pages = []
    for line in lines:
        if '\t' in line:
            k, v = line.split('\t', 1)
            pnum = int(k.split('.')[-2])
            if pnum <= 107:
                user_pages.append((k, pnum, json.loads(v)))

    print(f'\n=== CHECKING BOOK 1: KimVânKiềuTânTruyện_金雲翹新傳 (USER PAGES <= 107: {len(user_pages)} pages) ===')
    issues = []
    for k, pnum, boxes in user_pages:
        if len(boxes) != 24:
            issues.append(f'{k}: {len(boxes)} boxes (expected 24)')
        
        for idx, b in enumerate(boxes):
            txt = b.get('transcription', '')
            if ' ' in txt:
                issues.append(f'{k} box {idx+1}: contains spaces in Nom text "{txt}"')
            if not txt.strip():
                issues.append(f'{k} box {idx+1}: EMPTY text')
                
            pts = b.get('points', [])
            if not pts:
                issues.append(f'{k} box {idx+1}: NO points')
                continue
            min_y = min(p[1] for p in pts)
            max_y = max(p[1] for p in pts)
            if min_y < 800:
                issues.append(f'{k} box {idx+1}: min_y={min_y:.0f} < 800 (possible upper commentary!) text="{b.get("transcription")}"')
            if max_y > 5300:
                issues.append(f'{k} box {idx+1}: max_y={max_y:.0f} > 5300 (possible lower margin note) text="{b.get("transcription")}"')
                
        # Check sorting order: RTL column by column, luc then bat
        for i in range(0, len(boxes) - 1, 2):
            b_top = boxes[i]
            b_bot = boxes[i+1]
            cy_top = sum(p[1] for p in b_top['points']) / 4
            cy_bot = sum(p[1] for p in b_bot['points']) / 4
            if cy_top > cy_bot:
                issues.append(f'{k} pair {i//2 + 1}: Luc (top) cy={cy_top:.0f} > Bat (bot) cy={cy_bot:.0f}! Wrong vertical order!')
            
            if i + 2 < len(boxes):
                b_next_top = boxes[i+2]
                cx_cur = sum(p[0] for p in b_top['points']) / 4
                cx_next = sum(p[0] for p in b_next_top['points']) / 4
                if cx_cur < cx_next - 50:
                    issues.append(f'{k} column order inverted: col {i//2 + 1} cx={cx_cur:.0f} < col {i//2 + 2} cx={cx_next:.0f}! Not RTL!')

    print(f'Total issues found in Book 1 user pages: {len(issues)}')
    for iss in issues[:20]:
        print('  *', iss)


def check_book_3():
    p = Path('data/KimVânKiềuTânTập_金雲翹新集/ch/Label.txt')
    lines = p.read_text(encoding='utf-8').splitlines()

    user_pages = []
    for line in lines:
        if '\t' in line:
            k, v = line.split('\t', 1)
            pnum = int(k.split('.')[-2])
            if pnum <= 40:
                user_pages.append((k, pnum, json.loads(v)))

    print(f'\n=== CHECKING BOOK 3: KimVânKiềuTânTập_金雲翹新集 (USER PAGES <= 40: {len(user_pages)} pages) ===')
    issues = []
    for k, pnum, boxes in user_pages:
        expected_cnt = 16 if pnum == 2 else 32
        if len(boxes) != expected_cnt:
            issues.append(f'{k}: {len(boxes)} boxes (expected {expected_cnt})')
            
        for idx, b in enumerate(boxes):
            txt = b.get('transcription', '')
            if ' ' in txt:
                issues.append(f'{k} box {idx+1}: space in Nom text "{txt}"')
            if not txt.strip():
                issues.append(f'{k} box {idx+1}: EMPTY text')
            
            pts = b.get('points', [])
            min_y = min(pt[1] for pt in pts) if pts else 0
            max_y = max(pt[1] for pt in pts) if pts else 0
            if min_y < 1400:
                issues.append(f'{k} box {idx+1}: min_y={min_y:.0f} < 1400 (DÍNH BÌNH CHÚ HÁN TẦNG TRÊN!) text="{txt}"')
            if max_y > 4200:
                issues.append(f'{k} box {idx+1}: max_y={max_y:.0f} > 4200 (DÍNH CHÚ THÍCH NGOÀI LỀ DƯỚI!) text="{txt}"')

        # Check spread sorting: Left page first (smaller X), Right page second (larger X)
        if len(boxes) == 32:
            left_half = boxes[:16]
            right_half = boxes[16:]
            left_xs = [sum(pt[0] for pt in b['points'])/4 for b in left_half]
            right_xs = [sum(pt[0] for pt in b['points'])/4 for b in right_half]
            avg_left_x = sum(left_xs) / len(left_xs)
            avg_right_x = sum(right_xs) / len(right_xs)
            if avg_left_x > avg_right_x:
                issues.append(f'{k}: Left half boxes have avg_x={avg_left_x:.0f} > Right half avg_x={avg_right_x:.0f}! (Trang phải xếp trước trang trái)')

    print(f'Total issues found in Book 3 user pages: {len(issues)}')
    for iss in issues[:20]:
        print('  *', iss)


def check_book_2():
    p = Path('data/KimVânKiềuTânTruyện_金雲翹新傳_2/ch/Label.txt')
    lines = p.read_text(encoding='utf-8').splitlines()

    pages = []
    for line in lines:
        if '\t' in line:
            k, v = line.split('\t', 1)
            pnum = int(k.split('.')[-2])
            pages.append((k, pnum, json.loads(v)))

    print(f'\n=== CHECKING BOOK 2: KimVânKiềuTânTruyện_金雲翹新傳_2 (TOTAL {len(pages)} pages) ===')
    issues = []
    for k, pnum, boxes in pages:
        expected_cnt = 24 if pnum == 2 else 48
        if len(boxes) != expected_cnt:
            issues.append(f'{k}: {len(boxes)} boxes (expected {expected_cnt})')
            
        for idx, b in enumerate(boxes):
            txt = b.get('transcription', '')
            if ' ' in txt:
                issues.append(f'{k} box {idx+1}: space in Nom text "{txt}"')
            if not txt.strip():
                issues.append(f'{k} box {idx+1}: EMPTY text')
            pts = b.get('points', [])
            min_y = min(pt[1] for pt in pts) if pts else 0
            if min_y < 600:
                issues.append(f'{k} box {idx+1}: min_y={min_y:.0f} < 600 (DÍNH BÌNH CHÚ HÁN!) text="{txt}"')

    print(f'Total issues found in Book 2: {len(issues)}')
    for iss in issues[:20]:
        print('  *', iss)


def check_book_space():
    p = Path('data/Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳/ch/Label.txt')
    if not p.exists():
        return
    lines = p.read_text(encoding='utf-8').splitlines()

    pages = []
    for line in lines:
        if '\t' in line:
            k, v = line.split('\t', 1)
            pnum = int(k.split('.')[-2])
            pages.append((k, pnum, json.loads(v)))

    print(f'\n=== CHECKING BOOK SPACES: Kim Vân Kiều tân truyện _ 金 雲 翹 新 傳 (TOTAL {len(pages)} pages) ===')
    issues = []
    for k, pnum, boxes in pages:
        for idx, b in enumerate(boxes):
            txt = b.get('transcription', '')
            if ' ' in txt:
                issues.append(f'{k} box {idx+1}: space in Nom text "{txt}"')
            if not txt.strip():
                issues.append(f'{k} box {idx+1}: EMPTY text')
            pts = b.get('points', [])
            min_y = min(pt[1] for pt in pts) if pts else 0
            if min_y < 300:
                issues.append(f'{k} box {idx+1}: min_y={min_y:.0f} < 300 (Dính chữ trên lề) text="{txt}"')

    print(f'Total issues found in Book Spaces: {len(issues)}')
    for iss in issues[:20]:
        print('  *', iss)

if __name__ == '__main__':
    check_book_1()
    check_book_3()
    check_book_2()
    check_book_space()
