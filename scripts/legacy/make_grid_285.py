import cv2, numpy as np, os

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
img_path = os.path.join(target_dir, "KimVânKiềuTânTruyện_金雲翹新傳.285.jpg")
img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)

# Let's save a reduced version of page 285 with grid lines every 500 px so we can see exact pixel locations
h, w = img.shape[:2]
disp = img.copy()
for y in range(0, h, 200):
    cv2.line(disp, (0, y), (w, y), (0, 255, 0), 2)
    cv2.putText(disp, str(y), (50, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)

for x in range(0, w, 200):
    cv2.line(disp, (x, 0), (x, h), (255, 0, 0), 2)
    cv2.putText(disp, str(x), (x + 10, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)

out_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\f5f7c49f-c29c-47ff-a16e-0bf3af9783e9\page_285_grid.jpg"
small = cv2.resize(disp, (w // 4, h // 4))
cv2.imwrite(out_path, small)
print("Saved grid preview to:", out_path)
