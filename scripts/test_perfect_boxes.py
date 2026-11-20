import cv2
import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path

img_path = Path("data/KimVânKiềuTânTruyện_金雲翹新傳/vi/KimVânKiềuTânTruyện_金雲翹新傳.017.jpg")
img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# In page 17, margin numbers 25, 30, 35, 40, 45 are at x < 650
# Holes are at x > 3350
# Bottom notes (1) Q: nét... are at y > 5500
# Text body is strictly: x in [650, 3350], y in [600, 5450]
crop = gray[600:5450, 650:3350]
th = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)

# Horizontal projection
proj_y = np.sum(th > 0, axis=1)

# Smooth projection with Gaussian
from scipy.ndimage import gaussian_filter1d
smooth_y = gaussian_filter1d(proj_y.astype(float), sigma=15)

# Find 24 peaks or slice into 24 equal bands from exact top to exact bottom
# Active range of text
active_indices = np.where(smooth_y > np.max(smooth_y) * 0.1)[0]
top_y = 600 + active_indices[0]
bot_y = 600 + active_indices[-1]
print(f"Text body y-range: {top_y} -> {bot_y} (total height: {bot_y - top_y}px)")

line_step = (bot_y - top_y) / 24.0
print(f"Step per line: {line_step:.1f}px")

boxes = []
for i in range(24):
    y_center = top_y + (i + 0.5) * line_step
    y1 = int(y_center - line_step * 0.55)
    y2 = int(y_center + line_step * 0.55)
    
    # Analyze line x range
    line_crop = th[max(0, y1-600):min(th.shape[0], y2-600), :]
    proj_x = np.sum(line_crop > 0, axis=0)
    smooth_x = gaussian_filter1d(proj_x.astype(float), sigma=10)
    
    # Active x
    active_x = np.where(smooth_x > np.max(smooth_x) * 0.08)[0]
    if len(active_x) > 0:
        x1 = 650 + max(0, active_x[0] - 25)
        x2 = 650 + min(crop.shape[1], active_x[-1] + 25)
    else:
        x1, x2 = 800, 3100
        
    boxes.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])

thumb = Image.open(img_path).convert('RGB')
draw = ImageDraw.Draw(thumb)
for i, b in enumerate(boxes):
    pts = [tuple(p) for p in b]
    pts.append(pts[0])
    draw.line(pts, fill='green', width=12)

art_dir = Path(r'C:\Users\ADMIN\.gemini\antigravity\brain\f5f7c49f-c29c-47ff-a16e-0bf3af9783e9')
thumb.thumbnail((1000, 1000))
thumb.save(art_dir / 'page_017_perfect_boxes.jpg')
print("Saved perfect boxes to artifact dir!")
