import cv2, numpy as np, json, os

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
lbl_path = os.path.join(target_dir, "Label.txt")

with open(lbl_path, "r", encoding="utf-8") as f:
    lines = [l.strip().split("\t") for l in f if l.strip()]

for check_page in ["014", "017", "018"]:
    for fname, data_str in lines:
        if f".{check_page}.jpg" in fname:
            boxes = json.loads(data_str)
            img_path = os.path.join(target_dir, os.path.basename(fname))
            img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            print(f"Page {check_page}: {len(boxes)} boxes. Image shape: {img.shape}")
            # draw boxes on a thumbnail
            for i, b in enumerate(boxes):
                pts = np.array(b["points"], dtype=np.int32)
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
                cv2.putText(img, str(i), (pts[0][0], pts[0][1] + 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            
            out_p = rf"C:\Users\ADMIN\.gemini\antigravity\brain\f5f7c49f-c29c-47ff-a16e-0bf3af9783e9\check_page_{check_page}.jpg"
            small = cv2.resize(img, (w // 4, h // 4))
            cv2.imwrite(out_p, small)
            print(f"Saved: {out_p}")
