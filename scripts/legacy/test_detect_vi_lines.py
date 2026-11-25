import cv2
import numpy as np
from PIL import Image
from pathlib import Path

# Load page 17
img_path = Path("data/KimVânKiềuTânTruyện_金雲翹新傳/vi/KimVânKiềuTânTruyện_金雲翹新傳.017.jpg")
img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]
print(f"Image size: {w}x{h}")

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# The text region is between y ~ 400 and y ~ 5300
# And x ~ 500 and x ~ 3400 (exclude punch holes on right x > 3400 and margin numbers on left x < 500)
# Let's inspect horizontal projection in text band
text_band = gray[400:5300, 500:3400]
th = cv2.adaptiveThreshold(text_band, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)

# Horizontal projection (sum along width)
proj_y = np.sum(th > 0, axis=1)

# Divide into 24 bands (since we know there are exactly 24 lines of poem!)
band_h = (5300 - 400) / 24
print(f"Approx line height: {band_h:.1f}px")

boxes = []
for i in range(24):
    y_start = int(400 + i * band_h)
    y_end = int(400 + (i + 1) * band_h)
    
    # Sub-image for this line
    line_th = th[int(i * band_h):int((i + 1) * band_h), :]
    
    # Projection along x to find exact start and end of this line!
    proj_x = np.sum(line_th > 0, axis=0)
    
    # Find active pixels (> 5% of peak or threshold)
    active_x = np.where(proj_x > np.max(proj_x) * 0.05)[0]
    if len(active_x) > 0:
        x_min = 500 + max(0, active_x[0] - 30)
        x_max = 500 + min(2900, active_x[-1] + 30)
    else:
        x_min, x_max = 700, 3100
        
    # Find exact top/bottom within the band
    line_proj_y = np.sum(line_th > 0, axis=1)
    active_y = np.where(line_proj_y > np.max(line_proj_y) * 0.05)[0]
    if len(active_y) > 0:
        y_min = y_start + max(0, active_y[0] - 15)
        y_max = y_start + min(int(band_h), active_y[-1] + 15)
    else:
        y_min, y_max = y_start + 10, y_end - 10
        
    box = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
    boxes.append(box)
    print(f"Line {i+1:02d}: x={x_min:4d}..{x_max:4d} (w={x_max-x_min:4d}), y={y_min:4d}..{y_max:4d} (h={y_max-y_min:3d})")

# Draw boxes on thumbnail to verify
thumb = Image.open(img_path).convert('RGB')
from PIL import ImageDraw
draw = ImageDraw.Draw(thumb)
for b in boxes:
    pts = [tuple(p) for p in b]
    pts.append(pts[0])
    draw.line(pts, fill='green', width=10)

art_dir = Path(r'C:\Users\ADMIN\.gemini\antigravity\brain\f5f7c49f-c29c-47ff-a16e-0bf3af9783e9')
thumb.thumbnail((1000, 1000))
thumb.save(art_dir / 'page_017_refined_boxes.jpg')
print("Saved refined boxes visualization to artifact dir!")
