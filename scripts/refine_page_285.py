import cv2, numpy as np, json, os

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
dirs = [d for d in os.listdir(base_dir) if "Kim" in d and "Truy" in d and "2" not in d and "_" in d]
target_dir = os.path.join(base_dir, dirs[0], "vi")
img_path = os.path.join(target_dir, "KimVânKiềuTânTruyện_金雲翹新傳.285.jpg")
img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Approximate ranges for each of the 15 lines:
# (y1, y2, x_hint_min, x_hint_max)
line_hints = [
    # 0: Ngẫm hay muôn sự tại trời
    (560, 770, 900, 3100),
    # 1: Trời kia đã bắt làm người có thân
    (760, 980, 650, 3450),
    # 2: Bắt phong trần phải phong trần
    (980, 1180, 900, 3450),
    # 3: Cho thanh cao mới được phần thanh cao
    (1180, 1400, 650, 3700),
    # 4: Có đâu thiên vị người nào
    (1390, 1600, 900, 2800),
    # 5: Chữ tài chữ mệnh dồi dào cả hai
    (1590, 1800, 650, 3400),
    # 6: Có tài mà cậy chi tài
    (1790, 2000, 900, 2750),
    # 7: Chữ tài liền với chữ tai một vần
    (1990, 2210, 650, 3550),
    # 8: Đã mang lấy nghiệp vào thân
    (2200, 2420, 900, 3250),
    # 9: Cũng đừng trách lẫn trời gần trời xa
    (2410, 2630, 650, 3700),
    # 10: Thiện căn ở tại lòng ta
    (2620, 2840, 900, 2850),
    # 11: Chữ tâm kia mới bằng ba chữ tài
    (2830, 3060, 650, 3750),
    # 12: Lời quê nhặt dông dài
    (3050, 3260, 900, 3250),
    # 13: Mua vui cũng được một vài trống canh
    (3250, 3520, 650, 3850),
    # 14: Kim Vân Kiều truyện quyển chung
    (4620, 4950, 1250, 3700),
]

refined_boxes = []
preview = img.copy()

for i, (y1, y2, x_min_h, x_max_h) in enumerate(line_hints):
    # Crop this line region
    crop = gray[y1:y2, x_min_h:x_max_h]
    # Threshold
    th = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 14)
    
    # Horizontal projection within crop to find exact x1, x2
    proj_x = np.sum(th, axis=0) / 255
    # Ignore spurious edge noise
    active_x = np.where(proj_x > 8)[0]
    if len(active_x) > 0:
        actual_x1 = x_min_h + max(0, active_x[0] - 20)
        actual_x2 = x_min_h + min(crop.shape[1], active_x[-1] + 20)
    else:
        actual_x1, actual_x2 = x_min_h, x_max_h

    # Vertical projection within the actual_x range to refine y1, y2
    line_crop_th = th[:, max(0, actual_x1 - x_min_h):min(th.shape[1], actual_x2 - x_min_h)]
    proj_y = np.sum(line_crop_th, axis=1) / 255
    active_y = np.where(proj_y > 8)[0]
    if len(active_y) > 0:
        actual_y1 = y1 + max(0, active_y[0] - 15)
        actual_y2 = y1 + min(crop.shape[0], active_y[-1] + 15)
    else:
        actual_y1, actual_y2 = y1, y2
        
    pts = [[int(actual_x1), int(actual_y1)], [int(actual_x2), int(actual_y1)],
           [int(actual_x2), int(actual_y2)], [int(actual_x1), int(actual_y2)]]
    refined_boxes.append(pts)
    
    cv2.polylines(preview, [np.array(pts, dtype=np.int32)], True, (0, 255, 0), 4)
    cv2.putText(preview, str(i), (actual_x1, actual_y1 + 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)

out_preview = r"C:\Users\ADMIN\.gemini\antigravity\brain\f5f7c49f-c29c-47ff-a16e-0bf3af9783e9\page_285_refined.jpg"
small = cv2.resize(preview, (w // 4, h // 4))
cv2.imwrite(out_preview, small)
print("Saved page_285_refined.jpg")
for i, b in enumerate(refined_boxes):
    print(f"Line {i:2d}: x=[{b[0][0]}, {b[1][0]}], y=[{b[0][1]}, {b[2][1]}], w={b[1][0]-b[0][0]}, h={b[2][1]-b[0][1]}")
