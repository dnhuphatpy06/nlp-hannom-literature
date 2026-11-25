from pathlib import Path

# Kiểm tra phân bố trang Nôm và Quốc ngữ
# Trang Nôm có trong ch/Label.txt
lbl = Path("data/KimVânKiềuTânTruyện_金雲翹新傳/ch/Label.txt")
nom_pages = sorted([int(l.partition("\t")[0].rsplit(".", 2)[1]) for l in lbl.read_text(encoding="utf-8").splitlines()])
print(f"Tổng số trang Nôm hiện có: {len(nom_pages)}")

# Xem các bước nhảy
for i in range(len(nom_pages) - 1):
    diff = nom_pages[i+1] - nom_pages[i]
    if diff > 1:
        print(f"Giữa trang {nom_pages[i]} và {nom_pages[i+1]}: khoảng trống {diff-1} trang ({list(range(nom_pages[i]+1, nom_pages[i+1]))})")
