"""Tạo tập gold_dataset: lấy mẫu trang ảnh (cache/pages) theo loại sách trong data/cache/type.json.

- T2w: 1 trang 'vi' + 1 trang 'ch' (trang giữa sách theo tag trong phan_loai.json)
- Trần vong tướng sĩ văn (digcoll_13711_Y): trang 011 và 014
- T2b còn lại: mỗi sách 1 trang ch_vi (trang giữa sách)
"""
import csv, json, shutil, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "gold_dataset" / "raw"
N = lambda s: unicodedata.normalize("NFC", s)
FIXED = {N("Tran vong tuong si van digcoll_13711_Y"): [11, 14]}

types = {N(k)[:-4]: v for k, v in json.loads((DATA / "cache/type.json").read_text("utf8")).items()}
books = {N(p.name): p for p in DATA.iterdir() if p.is_dir() and p.name != "cache"}

def middle(pages, tag):
    ps = sorted(int(k) for k, v in pages.items() if v["tag"] == tag)
    return ps[len(ps) // 2] if ps else None

rows = []
def take(book, typ, page, tag, dst):
    pd = books[book] / "cache/pages"
    src = next(pd.glob(f"*.{page:03d}.jpg"), None)
    if src is None:
        print("THIẾU", book, page); return
    d = OUT; d.mkdir(parents=True, exist_ok=True)
    out = d / src.name
    shutil.copy2(src, out)
    rows.append(dict(type=typ, book=book, page=page, tag=tag, file=str(out.relative_to(ROOT)).replace("\\", "/")))

for book, typ in sorted(types.items()):
    if typ not in ("T2w", "T2b") or book not in books:
        continue
    pl = json.loads((books[book] / "cache/phan_loai.json").read_text("utf8"))["pages"]
    if typ == "T2w":
        for tag in ("vi", "ch"):
            p = middle(pl, tag)
            if p: take(book, typ, p, tag, "")
    elif book in FIXED:
        for p in FIXED[book]:
            take(book, typ, p, pl[str(p)]["tag"], "")
    else:
        p = middle(pl, "ch_vi")
        if p: take(book, typ, p, "ch_vi", "")

with open(OUT.parent / "manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["type", "book", "page", "tag", "file"]); w.writeheader(); w.writerows(rows)
print(len(rows), "ảnh ->", OUT)
