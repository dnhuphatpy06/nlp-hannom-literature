import cv2
import numpy as np
import json
import os
from pathlib import Path

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")

for f in sorted(os.listdir(target_dir)):
    if "285" in f and f.endswith(".jpg"):
        img_path = os.path.join(target_dir, f)
        img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        print("Loaded:", f, "Shape:", img.shape)
