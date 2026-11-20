import json, os, re

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
lbl_path = os.path.join(target_dir, "Label.txt")

# Load refined boxes for page 285
from refine_page_285 import refined_boxes

# Verse texts for page 285
verses_285 = [
    "Ngẫm hay muôn sự tại trời",
    "Trời kia đã bắt làm người có thân",
    "Bắt phong trần phải phong trần",
    "Cho thanh cao mới được phần thanh cao",
    "Có đâu thiên vị người nào",
    "Chữ tài chữ mệnh dồi dào cả hai",
    "Có tài mà cậy chi tài",
    "Chữ tài liền với chữ tai một vần",
    "Đã mang lấy nghiệp vào thân",
    "Cũng đừng trách lẫn trời gần trời xa",
    "Thiện căn ở tại lòng ta",
    "Chữ tâm kia mới bằng ba chữ tài",
    "Lời quê nhặt dông dài",
    "Mua vui cũng được một vài trống canh",
    "Kim Vân Kiều truyện quyển chung"
]

page_285_data = []
for b, text in zip(refined_boxes, verses_285):
    page_285_data.append({
        "transcription": text,
        "points": b,
        "difficult": False
    })

# Read existing Label.txt
lines = open(lbl_path, "r", encoding="utf-8").readlines()
new_lines = []
updated = False

for l in lines:
    l_strip = l.strip()
    if not l_strip:
        continue
    parts = l_strip.split("\t")
    fname = parts[0]
    if "285" in fname:
        new_lines.append(f"{fname}\t{json.dumps(page_285_data, ensure_ascii=False)}")
        updated = True
        print("Updated page 285 in Label.txt!")
    else:
        new_lines.append(l_strip)

with open(lbl_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_lines) + "\n")

print(f"Done! Updated: {updated}. Total pages: {len(new_lines)}")
