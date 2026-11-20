import json, os

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
lbl_path = os.path.join(target_dir, "Label.txt")

with open(lbl_path, "r", encoding="utf-8") as f:
    lines = [l.strip().split("\t") for l in f if l.strip()]

print(f"Total pages in Label.txt: {len(lines)}")
anomalies = []
for idx, (fname, data_str) in enumerate(lines):
    boxes = json.loads(data_str)
    n = len(boxes)
    if n not in (24, 15):
        anomalies.append((fname, f"unexpected box count {n}"))
    
    for bi, b in enumerate(boxes):
        pts = b["points"]
        w = pts[1][0] - pts[0][0]
        h = pts[2][1] - pts[0][1]
        y1 = pts[0][1]
        y2 = pts[2][1]
        if h > 280:
            anomalies.append((fname, f"box {bi} h={h} > 280"))
        if y2 > 5500:
            anomalies.append((fname, f"box {bi} y2={y2} > 5500"))

print(f"Found {len(anomalies)} anomalies across all 136 pages:")
for a in anomalies[:20]:
    print(" ", a)
