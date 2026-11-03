"""Khảo sát và phân loại trang PDF thành các thư mục ch, vi, intro, ref, toc, blank, misc.

Hỗ trợ giao diện Web tương tác trực quan (mở bằng trình duyệt qua Python built-in server)
và chế độ dòng lệnh (CLI) để gán nhãn theo khoảng trang, xoay ảnh và xuất PDF cập nhật.
"""
import argparse
import http.server
import json
import os
import shutil
import socket
import sys
import unicodedata
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import pymupdf

# Đảm bảo hiển thị đúng tiếng Việt / Unicode trên console Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

# Các thư mục / nhãn hợp lệ theo Quy_định_đồ_án.pdf
VALID_TAGS = {
    "ch": "Hán Nôm (ch/)",
    "vi": "Quốc ngữ (vi/)",
    "ch_vi": "Cả hai (Lưu vào cả ch/ và vi/ - chuẩn T2b)",
    "intro": "Lời nói đầu / Giới thiệu (intro/)",
    "toc": "Mục lục (toc/)",
    "ref": "Chú thích / Tham khảo (ref/)",
    "blank": "Trang trắng (blank/)",
    "misc": "Trang bìa / Khác (misc/)",
    "unclassified": "Chưa phân loại",
}

LAYOUT_CODES = {
    "Chưa phân loại": "Chưa xác định bố cục",
    "T1": "Quốc ngữ nằm ở phần đầu, Nôm ở phần cuối",
    "T1w": "T1 nhưng viết tay Nôm (w: writing)",
    "T2": "Xen kẽ (Ví dụ: Phải: Nôm, Trái: Quốc Ngữ)",
    "T2w": "T2 nhưng viết tay Nôm (w: writing)",
    "T2b": "Quốc ngữ và Nôm chung trang (Lưu cả ch/ và vi/)",
    "Tref": "Tham khảo",
    "Tx": "Loại khác",
    "Tqn": "Chỉ có Quốc ngữ",
    "Tnom": "Chỉ có Nôm",
    "Tnomw": "Chỉ có viết tay Nôm (w: writing)",
}

MANAGED_FOLDERS = ["ch", "vi", "intro", "ref", "toc", "blank", "misc"]


def parse_page_ranges(range_str: str, max_pages: int) -> list[int]:
    """Phân tích chuỗi khoảng trang như '1-5, 8, 10-15' hoặc 'all' thành danh sách số trang (1-indexed)."""
    range_str = range_str.strip().lower()
    if not range_str:
        return []
    if range_str in ("all", "tat_ca", "*"):
        return list(range(1, max_pages + 1))

    pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            sub = part.split("-", 1)
            start_s = sub[0].strip()
            end_s = sub[1].strip()
            start = 1 if not start_s else int(start_s)
            end = max_pages if (not end_s or end_s in ("end", "max")) else int(end_s)
            if start > end:
                start, end = end, start
            for p in range(max(1, start), min(max_pages, end) + 1):
                pages.add(p)
        else:
            try:
                p = int(part)
                if 1 <= p <= max_pages:
                    pages.add(p)
            except ValueError:
                pass
    return sorted(pages)


def get_available_books(da_tach_dir: Path) -> list[dict]:
    """Quét thư mục da_tach để tìm các sách đã được tách trang."""
    books = []
    if not da_tach_dir.is_dir():
        return books
    for item in sorted(da_tach_dir.iterdir()):
        if item.is_dir():
            pages_dir = item / "pages"
            if pages_dir.is_dir():
                page_files = sorted(pages_dir.glob("*.jpg"))
                if page_files:
                    cfg_file = item / "phan_loai.json"
                    is_classified = cfg_file.exists()
                    books.append({
                        "name": item.name,
                        "path": str(item),
                        "total_pages": len(page_files),
                        "is_classified": is_classified,
                    })
    return books


def load_classification(book_dir: Path, total_pages: int) -> dict:
    """Đọc file phan_loai.json của sách hoặc khởi tạo cấu trúc mặc định nếu chưa có."""
    cfg_file = book_dir / "phan_loai.json"
    if cfg_file.is_file():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Đảm bảo đủ các trang
            pages_data = data.get("pages", {})
            for p in range(1, total_pages + 1):
                sp = str(p)
                if sp not in pages_data:
                    pages_data[sp] = {"tag": "unclassified", "rotate": 0, "notes": ""}
            data["pages"] = pages_data
            data["total_pages"] = total_pages
            return data
        except Exception as e:
            print(f"[CẢNH BÁO] Không thể đọc {cfg_file}: {e}. Đang khởi tạo lại mặc định.")

    pages_data = {}
    for p in range(1, total_pages + 1):
        pages_data[str(p)] = {"tag": "unclassified", "rotate": 0, "notes": ""}

    return {
        "book_name": book_dir.name,
        "layout_code": "Chưa phân loại",
        "notes": "",
        "total_pages": total_pages,
        "last_updated": datetime.now().isoformat(),
        "pages": pages_data,
    }


def save_classification(book_dir: Path, data: dict):
    """Lưu dữ liệu phân loại vào file phan_loai.json an toàn (atomic write)."""
    data["last_updated"] = datetime.now().isoformat()
    cfg_file = book_dir / "phan_loai.json"
    temp_file = book_dir / f".tmp_phan_loai_{os.getpid()}.json"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, cfg_file)


def apply_classification(book_dir: Path, repo_root: Path) -> dict:
    """
    Áp dụng phân loại:
    1. Copy hoặc cập nhật ảnh từ pages/ vào ch/, vi/, intro/, ref/, toc/, blank/, misc/.
    2. Nếu tag là ch_vi (chuẩn T2b): copy vào cả ch/ và vi/.
    3. Tự động xoay ảnh nếu rotate != 0 và xuất <Tên sách>_updated.pdf theo Quy định đồ án.
    4. Dọn dẹp ảnh ở thư mục cũ nếu đã đổi tag, tuyệt đối bảo toàn Label.txt, fileState.txt.
    5. Đảm bảo copy <Tên sách>.pdf gốc vào thư mục nếu chưa có.
    """
    pages_dir = book_dir / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục ảnh gốc: {pages_dir}")

    page_files = sorted(pages_dir.glob("*.jpg"))
    if not page_files:
        raise FileNotFoundError(f"Không có file ảnh nào trong: {pages_dir}")

    total_pages = len(page_files)
    cfg = load_classification(book_dir, total_pages)
    pages_cfg = cfg.get("pages", {})

    # Tạo các thư mục quản lý nếu chưa có
    for folder in MANAGED_FOLDERS:
        (book_dir / folder).mkdir(parents=True, exist_ok=True)

    # Đảm bảo file PDF gốc có trong thư mục sách
    target_orig_pdf = book_dir / f"{book_dir.name}.pdf"
    if not target_orig_pdf.exists():
        for cand in [
            repo_root / "work" / "pdf" / f"{book_dir.name}.pdf",
            repo_root / "pdf" / f"{book_dir.name}.pdf",
        ]:
            if cand.is_file():
                try:
                    shutil.copy2(cand, target_orig_pdf)
                    break
                except OSError as e:
                    print(f"[CẢNH BÁO] Không thể copy PDF gốc: {e}")

    counts = {folder: 0 for folder in MANAGED_FOLDERS}
    has_rotations = False
    rotated_count = 0

    # Lập bản đồ file cho từng trang
    page_to_file = {}
    for pf in page_files:
        # Tách số thứ tự từ tên file: <Tên sách>.<STT 3 số>.jpg
        parts = pf.stem.rsplit(".", 1)
        if len(parts) == 2 and parts[1].isdigit():
            page_num = int(parts[1])
            page_to_file[page_num] = pf

    for page_num in range(1, total_pages + 1):
        sp = str(page_num)
        p_info = pages_cfg.get(sp, {"tag": "unclassified", "rotate": 0})
        tag = p_info.get("tag", "unclassified")
        rot = p_info.get("rotate", 0) % 360
        if rot != 0:
            has_rotations = True
            rotated_count += 1

        src_file = page_to_file.get(page_num)
        if not src_file:
            continue

        target_name = src_file.name

        # Xác định danh sách thư mục cần chứa ảnh này
        target_folders = []
        if tag == "ch_vi":
            target_folders = ["ch", "vi"]
        elif tag in MANAGED_FOLDERS:
            target_folders = [tag]

        # Cập nhật / copy ảnh vào đúng thư mục đích
        for folder in target_folders:
            dest_file = book_dir / folder / target_name
            counts[folder] += 1

            needs_write = True
            if dest_file.exists():
                # Nếu đã tồn tại và không xoay, không cần ghi lại
                if rot == 0 and dest_file.stat().st_size == src_file.stat().st_size:
                    needs_write = False

            if needs_write:
                temp_dest = book_dir / folder / f".tmp_{target_name}_{os.getpid()}.jpg"
                if rot == 0:
                    shutil.copy2(src_file, temp_dest)
                else:
                    doc = pymupdf.open(src_file)
                    page = doc[0]
                    mat = pymupdf.Matrix(rot)
                    pix = page.get_pixmap(matrix=mat)
                    pix.save(str(temp_dest), jpg_quality=95)
                    doc.close()
                os.replace(temp_dest, dest_file)

        # Dọn dẹp ảnh ở các thư mục khác nếu trang này đã đổi tag
        for folder in MANAGED_FOLDERS:
            if folder not in target_folders:
                old_file = book_dir / folder / target_name
                if old_file.exists():
                    try:
                        old_file.unlink()
                    except OSError:
                        pass

    # Xử lý xuất <Tên sách>_updated.pdf nếu có xoay trang (theo Quy định đồ án)
    updated_pdf_created = False
    updated_pdf_path = book_dir / f"{book_dir.name}_updated.pdf"

    if has_rotations:
        print(f"[{book_dir.name}] Phát hiện {rotated_count} trang có điều chỉnh góc xoay. Đang tạo {updated_pdf_path.name}...")
        try:
            if target_orig_pdf.is_file():
                doc = pymupdf.open(target_orig_pdf)
                for page_num in range(1, min(len(doc), total_pages) + 1):
                    sp = str(page_num)
                    rot = pages_cfg.get(sp, {}).get("rotate", 0) % 360
                    if rot != 0:
                        page = doc[page_num - 1]
                        page.set_rotation((page.rotation + rot) % 360)
                temp_pdf = book_dir / f".tmp_{updated_pdf_path.stem}_{os.getpid()}.pdf"
                doc.save(str(temp_pdf))
                doc.close()
                os.replace(temp_pdf, updated_pdf_path)
                updated_pdf_created = True
                print(f"[{book_dir.name}] Đã xuất thành công: {updated_pdf_path}")
            else:
                print(f"[CẢNH BÁO] Không tìm thấy PDF gốc để tạo _updated.pdf")
        except Exception as e:
            print(f"[LỖI] Tạo _updated.pdf thất bại: {e}")

    return {
        "book_name": book_dir.name,
        "counts": counts,
        "has_rotations": has_rotations,
        "rotated_count": rotated_count,
        "updated_pdf_created": updated_pdf_created,
    }


# ==============================================================================
# GIAO DIỆN WEB (HTML / CSS / JS NHÚNG)
# ==============================================================================
HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Khảo sát & Phân loại trang Hán Nôm - Quốc ngữ</title>
<style>
:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-card: #334155;
  --text-primary: #f8fafc;
  --text-muted: #94a3b8;
  --accent: #38bdf8;
  --border: #475569;
  
  --tag-ch: #10b981;
  --tag-vi: #3b82f6;
  --tag-ch-vi: #8b5cf6;
  --tag-intro: #6366f1;
  --tag-toc: #06b6d4;
  --tag-ref: #14b8a6;
  --tag-blank: #6b7280;
  --tag-misc: #f59e0b;
  --tag-unclassified: #475569;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg-primary);
  color: var(--text-primary);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
header {
  background-color: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  padding: 12px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 50;
  box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
}
.header-title { font-size: 1.15rem; font-weight: 700; color: var(--accent); }
.header-controls { display: flex; gap: 12px; align-items: center; }
select, input, button {
  font-family: inherit;
  font-size: 0.9rem;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-primary);
  outline: none;
}
select:focus, input:focus { border-color: var(--accent); }
button {
  cursor: pointer;
  font-weight: 600;
  transition: all 0.15s ease;
}
button:hover { filter: brightness(1.15); transform: translateY(-1px); }
button:active { transform: translateY(0); }
.btn-primary { background: #0284c7; color: white; border: none; }
.btn-success { background: #059669; color: white; border: none; }
.btn-warning { background: #d97706; color: white; border: none; }

.toolbar {
  background: var(--bg-secondary);
  padding: 10px 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 57px;
  z-index: 40;
}
.tool-group { display: flex; align-items: center; gap: 6px; }
.tool-label { font-size: 0.82rem; color: var(--text-muted); font-weight: 600; }

.badge-tag {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  color: white;
}
.bg-ch { background-color: var(--tag-ch); }
.bg-vi { background-color: var(--tag-vi); }
.bg-ch_vi { background-color: var(--tag-ch-vi); }
.bg-intro { background-color: var(--tag-intro); }
.bg-toc { background-color: var(--tag-toc); }
.bg-ref { background-color: var(--tag-ref); }
.bg-blank { background-color: var(--tag-blank); }
.bg-misc { background-color: var(--tag-misc); }
.bg-unclassified { background-color: var(--tag-unclassified); }

.summary-bar {
  padding: 8px 24px;
  background: #111827;
  display: flex;
  justify-content: space-between;
  font-size: 0.85rem;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
}
.stat-pill { margin-right: 12px; }
.stat-val { font-weight: 700; color: var(--text-primary); }

main { flex: 1; padding: 20px 24px; overflow-y: auto; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
}
.card {
  background: var(--bg-secondary);
  border: 2px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.15s ease;
  position: relative;
  display: flex;
  flex-direction: column;
}
.card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
.card.selected { border-color: #38bdf8; box-shadow: 0 0 0 2px #38bdf8; }
.card-header {
  padding: 6px 10px;
  background: rgba(0,0,0,0.2);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.8rem;
  font-weight: 600;
}
.card-img-wrap {
  width: 100%;
  height: 230px;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.card-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.2s ease;
}
.card-footer {
  padding: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 4px;
  background: rgba(0,0,0,0.15);
}
.card-footer select { padding: 3px 6px; font-size: 0.75rem; flex: 1; }

/* Modal phóng to */
.modal {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.85);
  z-index: 100;
  justify-content: center;
  align-items: center;
  flex-direction: column;
  padding: 20px;
}
.modal.open { display: flex; }
.modal-img-wrap { max-width: 90vw; max-height: 80vh; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.modal-img { max-width: 100%; max-height: 80vh; object-fit: contain; transition: transform 0.2s; }
.modal-controls {
  margin-top: 15px;
  display: flex;
  gap: 12px;
  align-items: center;
}
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: #059669;
  color: white;
  padding: 12px 20px;
  border-radius: 8px;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  display: none;
  z-index: 200;
}
</style>
</head>
<body>

<header>
  <div class="header-title">📖 Khảo sát & Phân loại sách Hán Nôm</div>
  <div class="header-controls">
    <label class="tool-label" for="bookSelect">Chọn sách:</label>
    <select id="bookSelect" onchange="loadBookData()"></select>
    <button class="btn-success" onclick="applyClassification()">💾 Áp dụng & Xuất thư mục</button>
  </div>
</header>

<div class="toolbar">
  <div class="tool-group">
    <span class="tool-label">Bố cục:</span>
    <select id="layoutSelect" onchange="updateLayout()">
      <option value="Chưa phân loại">Chưa phân loại</option>
      <option value="T1">T1: Quốc ngữ đầu, Nôm cuối</option>
      <option value="T1w">T1w: T1 (viết tay Nôm)</option>
      <option value="T2">T2: Xen kẽ (Phải Nôm, Trái Quốc ngữ)</option>
      <option value="T2w">T2w: T2 (viết tay Nôm)</option>
      <option value="T2b">T2b: Chung trang (Cả Nôm & Quốc ngữ)</option>
      <option value="Tref">Tref: Tham khảo</option>
      <option value="Tqn">Tqn: Chỉ có Quốc ngữ</option>
      <option value="Tnom">Tnom: Chỉ có Nôm</option>
    </select>
  </div>

  <div class="tool-group">
    <span class="tool-label">Khoảng trang:</span>
    <input type="text" id="rangeInput" placeholder="vd: 1-5, 8, 10-20 hoặc all" style="width: 170px;">
    <button onclick="selectByRange()">Chọn</button>
    <button onclick="selectAll()">Tất cả</button>
    <button onclick="deselectAll()">Bỏ chọn</button>
  </div>

  <div class="tool-group">
    <span class="tool-label">Gán nhanh:</span>
    <button class="badge-tag bg-ch" onclick="setTagSelected('ch')">ch (Nôm)</button>
    <button class="badge-tag bg-vi" onclick="setTagSelected('vi')">vi (Quốc ngữ)</button>
    <button class="badge-tag bg-ch_vi" onclick="setTagSelected('ch_vi')">ch + vi (T2b)</button>
    <button class="badge-tag bg-intro" onclick="setTagSelected('intro')">intro</button>
    <button class="badge-tag bg-toc" onclick="setTagSelected('toc')">toc</button>
    <button class="badge-tag bg-ref" onclick="setTagSelected('ref')">ref</button>
    <button class="badge-tag bg-blank" onclick="setTagSelected('blank')">blank</button>
    <button class="badge-tag bg-misc" onclick="setTagSelected('misc')">misc (bìa)</button>
  </div>

  <div class="tool-group">
    <span class="tool-label">Xoay:</span>
    <button onclick="rotateSelected(90)">↻ 90°</button>
    <button onclick="rotateSelected(180)">180°</button>
    <button onclick="rotateSelected(270)">270°</button>
    <button onclick="rotateSelected(0)">0°</button>
  </div>
</div>

<div class="summary-bar" id="summaryBar">
  <div>
    <span class="stat-pill">Tổng số trang: <span class="stat-val" id="statTotal">0</span></span>
    <span class="stat-pill">Đã gán: <span class="stat-val" id="statClassified">0</span></span>
    <span class="stat-pill">Nôm (ch): <span class="stat-val" id="statCh">0</span></span>
    <span class="stat-pill">Quốc ngữ (vi): <span class="stat-val" id="statVi">0</span></span>
    <span class="stat-pill">Cả hai (ch_vi): <span class="stat-val" id="statChVi">0</span></span>
    <span class="stat-pill">Xoay: <span class="stat-val" id="statRotate">0</span></span>
  </div>
  <div>
    <span id="selectedCountText" style="color: var(--accent); font-weight: 600;">Đang chọn: 0 trang</span>
  </div>
</div>

<main id="mainGrid" class="grid"></main>

<div class="modal" id="imageModal" onclick="closeModal(event)">
  <div class="modal-img-wrap">
    <img id="modalImg" class="modal-img" src="" alt="Xem lớn">
  </div>
  <div class="modal-controls" onclick="event.stopPropagation()">
    <button onclick="prevPage()">⬅ Trang trước</button>
    <span id="modalPageTitle" style="font-weight: 700; color: white;">Trang 001</span>
    <select id="modalTagSelect" onchange="changeModalTag()">
      <option value="ch">ch (Nôm)</option>
      <option value="vi">vi (Quốc ngữ)</option>
      <option value="ch_vi">ch + vi (Cả hai)</option>
      <option value="intro">intro</option>
      <option value="toc">toc</option>
      <option value="ref">ref</option>
      <option value="blank">blank</option>
      <option value="misc">misc</option>
      <option value="unclassified">Chưa phân loại</option>
    </select>
    <button onclick="rotateModal(90)">↻ Xoay 90°</button>
    <button onclick="nextPage()">Trang sau ➡</button>
    <button onclick="closeModal()">Đóng (Esc)</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let currentBook = "";
let bookData = {};
let selectedPages = new Set();
let modalCurrentPage = 1;

async function init() {
  const res = await fetch("/api/books");
  const books = await res.json();
  const select = document.getElementById("bookSelect");
  select.innerHTML = "";
  books.forEach(b => {
    const opt = document.createElement("option");
    opt.value = b.name;
    opt.textContent = `${b.name} (${b.total_pages} trang)${b.is_classified ? " [Đã lưu]" : ""}`;
    select.appendChild(opt);
  });
  if (books.length > 0) {
    currentBook = books[0].name;
    loadBookData();
  }
}

async function loadBookData() {
  currentBook = document.getElementById("bookSelect").value;
  selectedPages.clear();
  updateSelectedCount();
  const res = await fetch("/api/book?name=" + encodeURIComponent(currentBook));
  bookData = await res.json();
  document.getElementById("layoutSelect").value = bookData.layout_code || "Chưa phân loại";
  renderGrid();
  updateStats();
}

function renderGrid() {
  const grid = document.getElementById("mainGrid");
  grid.innerHTML = "";
  const total = bookData.total_pages || 0;
  for (let p = 1; p <= total; p++) {
    const pStr = p.toString();
    const info = bookData.pages[pStr] || { tag: "unclassified", rotate: 0 };
    const card = document.createElement("div");
    card.className = "card" + (selectedPages.has(p) ? " selected" : "");
    card.id = "card-" + p;
    card.onclick = (e) => toggleSelect(p, e);

    const padNum = String(p).padStart(3, "0");
    const tagClass = "bg-" + (info.tag || "unclassified");

    card.innerHTML = `
      <div class="card-header">
        <span>Trang ${padNum}</span>
        <span class="badge-tag ${tagClass}">${info.tag}</span>
      </div>
      <div class="card-img-wrap" ondblclick="openModal(${p})">
        <img class="card-img" loading="lazy" style="transform: rotate(${info.rotate || 0}deg)"
             src="/api/image?book=${encodeURIComponent(currentBook)}&page=${p}" alt="Trang ${p}">
      </div>
      <div class="card-footer" onclick="event.stopPropagation()">
        <select onchange="changePageTag(${p}, this.value)">
          <option value="ch" ${info.tag==='ch'?'selected':''}>ch</option>
          <option value="vi" ${info.tag==='vi'?'selected':''}>vi</option>
          <option value="ch_vi" ${info.tag==='ch_vi'?'selected':''}>ch_vi</option>
          <option value="intro" ${info.tag==='intro'?'selected':''}>intro</option>
          <option value="toc" ${info.tag==='toc'?'selected':''}>toc</option>
          <option value="ref" ${info.tag==='ref'?'selected':''}>ref</option>
          <option value="blank" ${info.tag==='blank'?'selected':''}>blank</option>
          <option value="misc" ${info.tag==='misc'?'selected':''}>misc</option>
          <option value="unclassified" ${info.tag==='unclassified'?'selected':''}>chưa gán</option>
        </select>
        <button style="padding: 2px 6px; font-size: 0.75rem;" onclick="rotatePage(${p})">↻</button>
        <button style="padding: 2px 6px; font-size: 0.75rem;" onclick="openModal(${p})">🔍</button>
      </div>
    `;
    grid.appendChild(card);
  }
}

function updateStats() {
  const total = bookData.total_pages || 0;
  let ch = 0, vi = 0, ch_vi = 0, rotated = 0, classified = 0;
  for (let p = 1; p <= total; p++) {
    const info = bookData.pages[p.toString()] || {};
    if (info.tag && info.tag !== "unclassified") classified++;
    if (info.tag === "ch") ch++;
    if (info.tag === "vi") vi++;
    if (info.tag === "ch_vi") ch_vi++;
    if (info.rotate && info.rotate % 360 !== 0) rotated++;
  }
  document.getElementById("statTotal").textContent = total;
  document.getElementById("statClassified").textContent = `${classified} (${Math.round(classified/total*100 || 0)}%)`;
  document.getElementById("statCh").textContent = ch;
  document.getElementById("statVi").textContent = vi;
  document.getElementById("statChVi").textContent = ch_vi;
  document.getElementById("statRotate").textContent = rotated;
}

let lastClicked = 1;
function toggleSelect(p, event) {
  if (event && event.shiftKey && lastClicked) {
    const start = Math.min(lastClicked, p);
    const end = Math.max(lastClicked, p);
    for (let i = start; i <= end; i++) selectedPages.add(i);
  } else {
    if (selectedPages.has(p)) selectedPages.delete(p);
    else selectedPages.add(p);
    lastClicked = p;
  }
  updateSelectedStyles();
  updateSelectedCount();
}

function updateSelectedStyles() {
  const total = bookData.total_pages || 0;
  for (let p = 1; p <= total; p++) {
    const card = document.getElementById("card-" + p);
    if (card) {
      if (selectedPages.has(p)) card.classList.add("selected");
      else card.classList.remove("selected");
    }
  }
}

function updateSelectedCount() {
  document.getElementById("selectedCountText").textContent = `Đang chọn: ${selectedPages.size} trang`;
}

function selectAll() {
  const total = bookData.total_pages || 0;
  for (let p = 1; p <= total; p++) selectedPages.add(p);
  updateSelectedStyles();
  updateSelectedCount();
}

function deselectAll() {
  selectedPages.clear();
  updateSelectedStyles();
  updateSelectedCount();
}

function selectByRange() {
  const raw = document.getElementById("rangeInput").value.trim();
  const total = bookData.total_pages || 0;
  if (!raw) return;
  if (raw.toLowerCase() === "all") { selectAll(); return; }
  selectedPages.clear();
  const parts = raw.split(",");
  for (const part of parts) {
    if (part.includes("-")) {
      const [s, e] = part.split("-");
      const start = parseInt(s) || 1;
      const end = (e.trim().toLowerCase() === "end") ? total : (parseInt(e) || total);
      for (let i = Math.max(1, start); i <= Math.min(total, end); i++) selectedPages.add(i);
    } else {
      const p = parseInt(part);
      if (p >= 1 && p <= total) selectedPages.add(p);
    }
  }
  updateSelectedStyles();
  updateSelectedCount();
}

function setTagSelected(tag) {
  if (selectedPages.size === 0) {
    alert("Vui lòng chọn ít nhất một trang bằng cách click vào trang hoặc nhập khoảng trang.");
    return;
  }
  selectedPages.forEach(p => {
    const sp = p.toString();
    if (!bookData.pages[sp]) bookData.pages[sp] = { tag: "unclassified", rotate: 0 };
    bookData.pages[sp].tag = tag;
  });
  renderGrid();
  updateStats();
  saveDataAuto();
}

function rotateSelected(deg) {
  if (selectedPages.size === 0) {
    alert("Vui lòng chọn ít nhất một trang để xoay.");
    return;
  }
  selectedPages.forEach(p => {
    const sp = p.toString();
    if (!bookData.pages[sp]) bookData.pages[sp] = { tag: "unclassified", rotate: 0 };
    bookData.pages[sp].rotate = deg;
  });
  renderGrid();
  updateStats();
  saveDataAuto();
}

function changePageTag(p, tag) {
  const sp = p.toString();
  if (!bookData.pages[sp]) bookData.pages[sp] = { tag: "unclassified", rotate: 0 };
  bookData.pages[sp].tag = tag;
  renderGrid();
  updateStats();
  saveDataAuto();
}

function rotatePage(p) {
  const sp = p.toString();
  if (!bookData.pages[sp]) bookData.pages[sp] = { tag: "unclassified", rotate: 0 };
  bookData.pages[sp].rotate = ((bookData.pages[sp].rotate || 0) + 90) % 360;
  renderGrid();
  updateStats();
  saveDataAuto();
}

function updateLayout() {
  bookData.layout_code = document.getElementById("layoutSelect").value;
  saveDataAuto();
}

async function saveDataAuto() {
  await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(bookData)
  });
}

async function applyClassification() {
  showToast("Đang áp dụng phân loại và đồng bộ thư mục...");
  await saveDataAuto();
  const res = await fetch("/api/apply?name=" + encodeURIComponent(currentBook), { method: "POST" });
  const result = await res.json();
  if (result.success) {
    let msg = `Đã áp dụng thành công cho ${currentBook}!\\n`;
    for (const [k, v] of Object.entries(result.counts)) {
      if (v > 0) msg += ` - ${k}: ${v} ảnh\\n`;
    }
    if (result.updated_pdf_created) msg += ` - Đã tạo PDF cập nhật xoay: ${currentBook}_updated.pdf\\n`;
    alert(msg);
    showToast("Áp dụng hoàn tất!");
  } else {
    alert("Lỗi: " + result.error);
  }
}

function openModal(p) {
  modalCurrentPage = p;
  const sp = p.toString();
  const info = bookData.pages[sp] || { tag: "unclassified", rotate: 0 };
  document.getElementById("modalPageTitle").textContent = `Trang ${String(p).padStart(3, "0")} / ${bookData.total_pages}`;
  document.getElementById("modalTagSelect").value = info.tag || "unclassified";
  const img = document.getElementById("modalImg");
  img.style.transform = `rotate(${info.rotate || 0}deg)`;
  img.src = `/api/image?book=${encodeURIComponent(currentBook)}&page=${p}`;
  document.getElementById("imageModal").classList.add("open");
}

function closeModal(e) {
  if (!e || e.target.id === "imageModal") {
    document.getElementById("imageModal").classList.remove("open");
  }
}

function nextPage() {
  if (modalCurrentPage < bookData.total_pages) openModal(modalCurrentPage + 1);
}

function prevPage() {
  if (modalCurrentPage > 1) openModal(modalCurrentPage - 1);
}

function changeModalTag() {
  const tag = document.getElementById("modalTagSelect").value;
  changePageTag(modalCurrentPage, tag);
}

function rotateModal(deg) {
  const sp = modalCurrentPage.toString();
  bookData.pages[sp].rotate = ((bookData.pages[sp].rotate || 0) + deg) % 360;
  document.getElementById("modalImg").style.transform = `rotate(${bookData.pages[sp].rotate}deg)`;
  renderGrid();
  updateStats();
  saveDataAuto();
}

window.addEventListener("keydown", (e) => {
  if (!document.getElementById("imageModal").classList.contains("open")) return;
  if (e.key === "ArrowRight") nextPage();
  else if (e.key === "ArrowLeft") prevPage();
  else if (e.key === "Escape") closeModal();
});

function showToast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

window.onload = init;
</script>
</body>
</html>
"""


class ClassificationHandler(http.server.BaseHTTPRequestHandler):
    repo_root = Path(__file__).resolve().parent.parent
    da_tach_dir = repo_root / "work" / "da_tach"

    def log_message(self, format, *args):
        # Tắt bớt log HTTP để terminal gọn gàng
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/books":
            books = get_available_books(self.da_tach_dir)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(books, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/book":
            book_name = query.get("name", [""])[0]
            book_dir = self.da_tach_dir / book_name
            pages = list((book_dir / "pages").glob("*.jpg"))
            data = load_classification(book_dir, len(pages))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/image":
            book_name = query.get("book", [""])[0]
            page_str = query.get("page", ["1"])[0]
            try:
                page_num = int(page_str)
            except ValueError:
                page_num = 1

            pages_dir = self.da_tach_dir / book_name / "pages"
            # Tìm file ảnh
            target = pages_dir / f"{book_name}.{page_num:03d}.jpg"
            if not target.exists():
                # Hỗ trợ dạng chuẩn hóa Unicode khác
                name_nfc = unicodedata.normalize("NFC", target.name)
                name_nfd = unicodedata.normalize("NFD", target.name)
                for cand in (pages_dir / name_nfc, pages_dir / name_nfd):
                    if cand.exists():
                        target = cand
                        break

            if target.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(target, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self.send_response(404)
                self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        if path == "/api/save":
            try:
                data = json.loads(body)
                book_name = data.get("book_name")
                book_dir = self.da_tach_dir / book_name
                save_classification(book_dir, data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        if path == "/api/apply":
            book_name = query.get("name", [""])[0]
            book_dir = self.da_tach_dir / book_name
            try:
                res = apply_classification(book_dir, self.repo_root)
                res["success"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def start_server(port: int = 8765, open_browser: bool = True):
    """Khởi động Web UI phân loại trang trên cổng khả dụng."""
    server = None
    for p in range(port, port + 20):
        try:
            server = http.server.HTTPServer(("127.0.0.1", p), ClassificationHandler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print("[LỖI] Không thể tìm thấy cổng trống để khởi động Web UI.")
        return 1

    url = f"http://localhost:{port}"
    print("=" * 65)
    print("  ỨNG DỤNG KHẢO SÁT & PHÂN LOẠI TRANG HÁN NÔM - QUỐC NGỮ")
    print("=" * 65)
    print(f"Đang chạy tại: {url}")
    print("Nhấn Ctrl + C để dừng máy chủ.\n")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng máy chủ Web UI.")
    finally:
        server.server_close()
    return 0


# ==============================================================================
# XỬ LÝ CHẾ ĐỘ DÒNG LỆNH (CLI)
# ==============================================================================
def handle_cli(args, repo_root: Path, da_tach_dir: Path) -> int:
    books = get_available_books(da_tach_dir)
    if not books:
        print(f"[CẢNH BÁO] Không tìm thấy sách nào trong {da_tach_dir}. Hãy chạy tach_trang.py trước.")
        return 0

    if args.list:
        print("=== DANH SÁCH SÁCH ĐÃ TÁCH TRANG ===")
        for idx, b in enumerate(books, 1):
            cfg_file = Path(b["path"]) / "phan_loai.json"
            status = "ĐÃ LƯU" if cfg_file.exists() else "CHƯA PHÂN LOẠI"
            print(f"  {idx}. {b['name']} ({b['total_pages']} trang) -> [{status}]")
        return 0

    target_book_name = args.book
    if not target_book_name:
        # Nếu chỉ có 1 sách thì chọn mặc định, nếu nhiều sách yêu cầu chọn --book
        if len(books) == 1:
            target_book_name = books[0]["name"]
        else:
            print("Vui lòng chỉ định tên sách hoặc số thứ tự bằng tham số --book <Tên sách hoặc STT>.")
            print("Chạy 'python .\\scripts\\phan_loai.py --list' để xem danh sách.")
            return 1

    book_dir = None
    # 1. Chọn theo số thứ tự từ danh sách --list (1, 2, 3...)
    if target_book_name.isdigit():
        idx = int(target_book_name) - 1
        if 0 <= idx < len(books):
            book_dir = Path(books[idx]["path"])

    # 2. Khớp trực tiếp tên thư mục
    if not book_dir:
        cand = da_tach_dir / target_book_name
        if cand.is_dir():
            book_dir = cand

    # 3. Khớp theo Unicode NFC / NFD
    if not book_dir:
        norm_target = unicodedata.normalize("NFC", target_book_name)
        for b in books:
            if unicodedata.normalize("NFC", b["name"]) == norm_target:
                book_dir = Path(b["path"])
                break

    # 4. Khớp theo từ khóa tìm kiếm (substring case-insensitive)
    if not book_dir:
        norm_target = unicodedata.normalize("NFC", target_book_name).lower()
        matches = [b for b in books if norm_target in unicodedata.normalize("NFC", b["name"]).lower()]
        if len(matches) == 1:
            book_dir = Path(matches[0]["path"])
        elif len(matches) > 1:
            print(f"[CẢNH BÁO] Có nhiều sách khớp với từ khóa '{target_book_name}': {[b['name'] for b in matches]}")
            print("Vui lòng chỉ định rõ hơn hoặc dùng số thứ tự từ --list.")
            return 1

    if not book_dir:
        print(f"[LỖI] Không tìm thấy sách nào khớp với: '{target_book_name}'")
        print("Chạy 'python .\\scripts\\phan_loai.py --list' để xem danh sách.")
        return 1

    pages_dir = book_dir / "pages"
    total_pages = len(list(pages_dir.glob("*.jpg")))
    cfg = load_classification(book_dir, total_pages)

    if args.layout:
        if args.layout not in LAYOUT_CODES:
            print(f"[LỖI] Mã bố cục '{args.layout}' không hợp lệ. Các mã hợp lệ: {list(LAYOUT_CODES.keys())}")
            return 1
        cfg["layout_code"] = args.layout
        print(f"-> Đã gán bố cục sách: {args.layout} ({LAYOUT_CODES[args.layout]})")

    if args.set:
        # Cú pháp: "1-5:misc, 6-70:ch, 71-72:misc"
        items = [x.strip() for x in args.set.split(",") if x.strip()]
        for item in items:
            if ":" not in item:
                print(f"[LỖI] Cú pháp gán sai: '{item}'. Chuẩn: 'khoảng_trang:tag' (vd: '1-5:misc')")
                return 1
            range_part, tag = item.split(":", 1)
            range_part = range_part.strip()
            tag = tag.strip()
            if tag not in VALID_TAGS:
                print(f"[LỖI] Nhãn '{tag}' không hợp lệ. Các nhãn hợp lệ: {list(VALID_TAGS.keys())}")
                return 1
            pages = parse_page_ranges(range_part, total_pages)
            for p in pages:
                sp = str(p)
                if sp not in cfg["pages"]:
                    cfg["pages"][sp] = {"tag": "unclassified", "rotate": 0}
                cfg["pages"][sp]["tag"] = tag
            print(f"-> Đã gán {len(pages)} trang [{range_part}] thành nhãn: {tag}")

    if args.rotate:
        # Cú pháp: "1-72:90" hoặc "5:180"
        items = [x.strip() for x in args.rotate.split(",") if x.strip()]
        for item in items:
            if ":" not in item:
                print(f"[LỖI] Cú pháp xoay sai: '{item}'. Chuẩn: 'khoảng_trang:góc' (vd: '1-72:90')")
                return 1
            range_part, deg_s = item.split(":", 1)
            try:
                deg = int(deg_s.strip()) % 360
            except ValueError:
                print(f"[LỖI] Góc xoay không hợp lệ: {deg_s}")
                return 1
            pages = parse_page_ranges(range_part.strip(), total_pages)
            for p in pages:
                sp = str(p)
                if sp not in cfg["pages"]:
                    cfg["pages"][sp] = {"tag": "unclassified", "rotate": 0}
                cfg["pages"][sp]["rotate"] = deg
            print(f"-> Đã đặt góc xoay {deg}° cho {len(pages)} trang [{range_part}]")

    save_classification(book_dir, cfg)
    print(f"-> Đã lưu cấu hình phân loại vào: {book_dir / 'phan_loai.json'}")

    if args.status or (not args.apply and not args.set and not args.rotate and not args.layout):
        print("\n" + "=" * 50)
        print(f"THÔNG TIN PHÂN LOẠI: {book_dir.name}")
        print(f"Bố cục: {cfg.get('layout_code')} ({LAYOUT_CODES.get(cfg.get('layout_code'), '')})")
        print(f"Tổng số trang: {total_pages}")
        stats = {}
        rot_count = 0
        for p in range(1, total_pages + 1):
            info = cfg["pages"].get(str(p), {})
            t = info.get("tag", "unclassified")
            stats[t] = stats.get(t, 0) + 1
            if info.get("rotate", 0) % 360 != 0:
                rot_count += 1
        print("Chi tiết nhãn:")
        for t, cnt in sorted(stats.items()):
            print(f"  - {t:<12} ({VALID_TAGS.get(t, t)}): {cnt} trang")
        if rot_count > 0:
            print(f"Số trang được xoay: {rot_count}")
        print("=" * 50)

    if args.apply:
        print("\nĐang áp dụng phân loại và đồng bộ thư mục...")
        res = apply_classification(book_dir, repo_root)
        print("Kết quả phân loại:")
        for k, v in res["counts"].items():
            if v > 0:
                print(f"  -> {k:<8}: {v} trang")
        if res["updated_pdf_created"]:
            print(f"  -> Đã tạo PDF cập nhật: {book_dir.name}_updated.pdf")
        print("Hoàn tất!")

    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Cổng cho Web UI (mặc định: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    parser.add_argument("--cli", action="store_true", help="Chạy chế độ dòng lệnh thay vì Web UI")
    parser.add_argument("--list", action="store_true", help="Liệt kê các sách đã tách trang")
    parser.add_argument("--book", type=str, help="Tên sách cần thao tác qua CLI")
    parser.add_argument("--layout", type=str, help="Mã bố cục (T1, T1w, T2, T2w, T2b, Tqn, Tnom, v.v.)")
    parser.add_argument("--set", type=str, help="Gán khoảng trang: '1-5:misc, 6-70:ch, 71-72:misc'")
    parser.add_argument("--rotate", type=str, help="Xoay khoảng trang: '1-72:90'")
    parser.add_argument("--apply", action="store_true", help="Áp dụng phân loại vào thư mục ch/, vi/, v.v.")
    parser.add_argument("--status", action="store_true", help="Xem trạng thái phân loại của sách")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    da_tach_dir = repo_root / "work" / "da_tach"

    # Nếu có bất kỳ tham số CLI nào được truyền vào thì chạy CLI
    if args.cli or args.list or args.book or args.set or args.rotate or args.layout or args.apply or args.status:
        return handle_cli(args, repo_root, da_tach_dir)

    # Mặc định mở Web UI trực quan
    return start_server(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main() or 0)
