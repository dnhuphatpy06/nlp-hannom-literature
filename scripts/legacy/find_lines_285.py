import cv2
import numpy as np
import os

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
img_path = os.path.join(target_dir, "KimVânKiềuTânTruyện_金雲翹新傳.285.jpg")

img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Let's crop the text region and check line positions
# Let's see where the lines actually are!
# On standard pages, line spacing is around 205 px!
# Let's find lines 0 to 13 and line 14
# Line 0: "Ngẫm hay muôn sự tại trời"
# Line 13: "Mua vui cũng được một vài trống canh"
# Line 14: "Kim Vân Kiều truyện quyển chung"

# Let's test binarization and horizontal projection
crop = gray[400:4500, 600:3600]
th = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)

# Horizontal projection across width to find Y of lines
proj_y = np.sum(th, axis=1) / 255
print("proj_y shape:", proj_y.shape)

# Let's find peaks in proj_y
from scipy.signal import find_peaks
peaks, props = find_peaks(proj_y, distance=140, prominence=50)
print(f"Found {len(peaks)} peaks in y:")
for p in peaks:
    print(f"  y = {400 + p}, peak_val = {proj_y[p]}")
