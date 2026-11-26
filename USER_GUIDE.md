# Hướng dẫn sử dụng dự án (USER_GUIDE)

Tài liệu hướng dẫn chi tiết cách cài đặt và chạy dự án từ đầu đến cuối trên máy tính mới với bộ dữ liệu nhóm đã nộp.

## 1. Môi trường thực thi

Dự án được kiểm thử và vận hành trên cấu hình:

- Hệ điều hành: Ubuntu 22.04 LTS (ưu tiên) hoặc Windows 10/11 64-bit.
- Python: `Python 3.11.0` (tương thích `3.11.x` theo ràng buộc của `transformers==5.22.1`).
- RAM: tối thiểu 8 GB, khuyến nghị 16 GB.
- Đồ hoạ: CPU hoặc GPU NVIDIA (CUDA 11.8 / 12.1).
- Toàn bộ phiên bản thư viện phụ thuộc đã được cố định (`==`) trong `requirements.txt`, `requirements-ocr.txt` và `requirements-vintern.txt`.

## 2. Cài đặt môi trường

Mở terminal tại thư mục gốc của dự án `nlp-hannom-literature` và tạo môi trường ảo:

Trên Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

Trên Linux (Ubuntu):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Cài đặt thư viện nền tảng (chạy Web UI `app.py`, tách trang PDF, nhận diện trang trắng, gọi API Kim Hán Nôm, tách hàng `row_projection`, dóng hàng và xuất `.csv`/`.xlsx`):

```bash
pip install -r requirements.txt
```

Nếu cần chạy OCR cục bộ (`PaddleOCR-VL`) và mở công cụ duyệt nhãn `PPOCRLabel`:

```bash
pip install torch==2.8.0 torchvision==0.23.0
pip install -r requirements-ocr.txt
```

Nếu máy có GPU NVIDIA, cài cặp `torch==2.8.0` và `torchvision==0.23.0` bản CUDA tương ứng theo hướng dẫn của PyTorch trước khi chạy `pip install -r requirements-ocr.txt`. Mã nguồn `PPOCRLabel` đã được tích hợp trực tiếp trong thư mục `external/PPOCRLabel/`, không cần cài đặt qua `pip`.

Nếu cần chạy thêm mô hình `Vintern-1B-v3.5` (chỉ dùng cho tuỳ chọn đọc từng box chữ Quốc ngữ trong `row_projection`), tạo một môi trường ảo tách biệt dùng `transformers==4.46.3`:

```bash
python -m venv C:\vintern
C:\vintern\Scripts\activate
pip install torch==2.8.0 torchvision==0.23.0
pip install -r requirements-vintern.txt
```

Trên Ubuntu có thể tạo môi trường này tại `~/.venv-vintern` bằng lệnh tương ứng, sau đó khai báo đường dẫn trình thông dịch trong file `.env` ở biến `VINTERN_PYTHON`.

Dựng lại từ điển Hán-Nôm mở rộng (`nom_na_viet.json`) từ các nguồn mở (`hanviet.csv`, Nôm Na Việt, Giúp đọc Nôm và Hán Việt, Tự Điển Chữ Nôm Dẫn Giải) nếu thư mục `data/cache/dict/` chưa có sẵn:

```bash
python scripts/nom_dict.py build
python scripts/nom_dict.py build-full
```

## 3. Cấu hình Token và các mô hình OCR sử dụng

### 3.1. API Kim Hán Nôm (trả phí / yêu cầu tài khoản)

Hệ thống sử dụng API của `https://kimhannom.fit.hcmus.edu.vn/` để OCR chữ Hán Nôm (`ch/`) và tra phiên âm hỗ trợ hậu xử lý:

1. Đăng nhập vào hệ thống `https://kimhannom.fit.hcmus.edu.vn/` trên trình duyệt.
2. Nhấn F12 mở công cụ nhà phát triển, vào mục Application (hoặc Storage), chọn Cookies của tên miền trên và sao chép giá trị của trường `token`.
3. Tạo file `.env` tại thư mục gốc dự án (tham khảo `.env.example`) và điền token:

```env
TOKEN=eyJhbGciOi...chuỗi_token...
```

Không đưa file `.env` lên git hay chia sẻ công khai.

Các tham số gọi API sử dụng trong pipeline (`scripts/ocr_engines.py`, `scripts/api/sinonom_service.py`):

- Upload ảnh (`image-upload`): khử trùng lặp theo SHA-256 thông qua `data/cache/uploads.json`.
- OCR trang Hán Nôm (`image-ocr`): `ocr_id=2` (hành chính), `lang_type=2` (chữ Nôm), `epitaph=0` (văn bản thường).
- Đọc lại từng box chữ Nôm: `ocr_id=2`, `reading_direction` chọn theo tỷ lệ khung cắt (dọc nếu cao hơn rộng, ngang nếu rộng hơn cao).
- Tra phiên âm chữ/câu (`sinonom-transliteration`): `font_type=1`, `lang_type=1`, lưu cache vào `data/<Tên sách>/cache/translit.json`.

### 3.2. Mô hình chạy cục bộ trên máy

- **PaddleOCR-VL**: cài qua gói `paddleocr[doc-parser]==3.7.0`, chạy bằng backend `engine="transformers"` trên PyTorch `2.8.0` (`pipeline_version="v1"`). Dùng để phát hiện bounding box và đọc chữ cho các thư mục chữ Quốc ngữ (`vi/`, `intro/`, `ref/`, `toc/`).
- **Vintern-1B-v3.5** (`5CD-AI/Vintern-1B-v3_5`, `transformers==4.46.3`): dùng cho tuỳ chọn đọc chữ Quốc ngữ trong từng box đã cắt của `row_projection` hoặc thao tác nhận dạng lại box. Prompt nguyên văn truyền vào mô hình gồm tiền tố `<image>` và câu lệnh:

```text
<image>
Đây là một dòng chữ viết tay tiếng Việt. Hãy chép lại chính xác từng chữ, giữ nguyên dấu thanh và dấu mũ, không thêm, không bớt, không giải thích. Chỉ trả về đúng dòng chữ đó.
```

Tham số sinh mặc định của `Vintern-1B-v3.5`: `max_new_tokens=128`, `do_sample=False`, `num_beams=3`, `repetition_penalty=1.1` (tự động thử lại với `repetition_penalty=2.5` nếu phát hiện lặp từ vô hạn), `max_num=12`.

## 4. Khởi chạy Web UI

Chạy ứng dụng chính từ thư mục gốc dự án:

```bash
python app.py
```

Trình duyệt tự động mở tại `http://localhost:8766`. Để tắt ứng dụng, nhấn Ctrl+C trong cửa sổ terminal. Nếu muốn trỏ sang một thư mục dữ liệu khác ngoài `data/`, dùng tuỳ chọn `--data <đường dẫn>`.

## 5. Quy trình xử lý sách

### 5.1. Nhận diện sách và phân loại

Các file PDF gốc được đặt trong `data/` hoặc `data/<Tên sách>/<Tên sách>.pdf` sẽ tự động hiển thị trên giao diện chính:

1. Dùng ô tìm kiếm hoặc bộ lọc để tìm cuốn sách cần xử lý.
2. Bấm **Chọn loại** và gán mã phân loại theo quy chuẩn:

| Loại | Ý nghĩa |
|---|---|
| T1 / T1w | Quốc ngữ ở phần đầu, Nôm ở phần cuối (w: viết tay) |
| T2 / T2w | Xen kẽ, ví dụ phải Nôm, trái Quốc ngữ (w: viết tay) |
| T2b | Quốc ngữ và Nôm chung trang |
| Tnom / Tnomw | Chỉ có Nôm (w: viết tay) |
| Tqn | Chỉ có Quốc ngữ |
| Tref | Tham khảo |
| Tx | Loại khác |
| chưa phân loại | Mặc định |

3. Bấm **Save** để lưu cấu hình vào `data/cache/type.json`.
4. Bấm **Trang** ở hàng cuốn sách tương ứng để chuyển sang màn hình tách và gán nhãn trang.

### 5.2. Tách trang và gán nhãn trang

1. Tại bước **1 Phân loại trang** (thanh các bước ở đầu màn hình sách), chọn độ phân giải DPI (mặc định 300) rồi bấm **Tách trang**. Ảnh trang lưu dưới dạng `<Tên sách>.<NNN>.jpg` trong `data/<Tên sách>/cache/pages/`.
2. Hệ thống tự động phát hiện trang trắng để gán `blank` và gợi ý nhãn ban đầu cho các trang `unknown` theo loại sách đã lưu.
3. Kiểm tra lại toàn bộ lưới ảnh thu nhỏ. Để sửa nhãn các trang bìa, tựa, mục lục hay trang gán sai: chọn các trang cần sửa, bấm **Gán nhãn ▾** và chọn đúng nhãn (`ch`, `vi`, `ch_vi`, `intro`, `toc`, `ref`, `blank`, `misc`). Với các ảnh chụp hai trang Hán Nôm, có thể đánh dấu thuộc tính **Bản chụp 2 trang** để pipeline sắp xếp thứ tự box đúng quy định (trang trái trước, trang phải sau; cột từ phải sang trái).
4. Toàn bộ nhãn trang được lưu tức thời vào `data/<Tên sách>/cache/phan_loai.json`.

## 6. OCR, hiệu chỉnh bằng PPOCRLabel và Web đối chiếu

### 6.1. Chạy OCR và mở PPOCRLabel từ giao diện web

1. Bấm **2 OCR** trên thanh các bước ở đầu màn hình sách để sang bước OCR. Hệ thống tự đồng bộ ảnh từ `cache/pages/` sang các thư mục `ch/`, `vi/`, `intro/`, `ref/`, `toc/`, `blank/`, `misc/`.
2. Tại bảng **OCR riêng từng thư mục và kiểm tra bằng PPOCRLabel**, bấm **OCR thư mục này** để chọn engine phù hợp (`Kim Hán Nôm` cho `ch/`; `PaddleOCR-VL` hoặc `row_projection + Vintern-1B` cho `vi/`, `intro/`, `ref/`, `toc/`). Kết quả thô được lưu cache trong `data/<Tên sách>/cache/ocr/` để tránh gọi lại khi không thay đổi ảnh.
3. Bấm **Mở PPOCRLabel** ở dòng thư mục cần soát (`ch/` hoặc `vi/`). Cửa sổ PPOCRLabel sẽ nạp trực tiếp thư mục ảnh cùng hai file `Label.txt` và `fileState.txt` (cũng có thể mở thủ công bằng lệnh `python scripts/ppocrlabel_boot.py --image_dir "data\<Tên sách>\ch"`).

### 6.2. Phím tắt và thao tác trong PPOCRLabel

Cửa sổ PPOCRLabel gồm danh sách ảnh bên trái, khung hiển thị bounding box ở giữa và danh sách câu/tọa độ bên phải:

- `W`: bật chế độ vẽ khung mới (RectBox).
- `Ctrl+R`: nhận dạng lại chữ cho box đang chọn.
- `Ctrl+Shift+R`: nhận dạng lại toàn bộ box trên trang.
- `Ctrl+B`: tự động sắp xếp thứ tự box theo quy chuẩn đọc (Nôm dọc từ phải sang trái, trên xuống dưới; Quốc ngữ từ trên xuống dưới, trái sang phải).
- `Alt+Up` / `Alt+Down`: dịch chuyển thứ tự box đang chọn lên trên hoặc xuống dưới.
- `Ctrl+Shift+A`: chọn toàn bộ box hiện có trên trang.
- `Ctrl+T`: chuẩn hoá box tứ giác thành hình chữ nhật song song trục toạ độ.
- `Delete`: xoá box đang chọn.
- Rê chuột vào giữa cạnh của box rồi kéo: điều chỉnh song song một cạnh mà không làm méo các góc khác.
- `Space` hoặc nút **Check**: đánh dấu ảnh hiện tại hoàn thành (`1` trong `fileState.txt`) và chuyển sang ảnh kế tiếp.
- `D` / `A`: chuyển sang ảnh tiếp theo / ảnh trước đó.
- `Ctrl+S`: lưu `Label.txt` và `fileState.txt`.
- `Ctrl+M`: mở hộp thoại chuyển đổi engine OCR.

### 6.3. Soát chữ song ngữ trên Web đối chiếu

Dự án tích hợp thêm giao diện **Web đối chiếu CH và VI** giúp đối chiếu từng dòng Hán Nôm với bản Quốc ngữ cùng hình ảnh gốc:

- Khi đang chạy `app.py`, bấm nút **Đối chiếu CH–VI** hoặc truy cập `http://localhost:8766/review/`.
- Hoặc mở độc lập không cần khởi chạy toàn bộ `app.py` bằng lệnh:

```bash
python review_local.py
```

Trên Windows có thể bấm đúp `review-web/Mo-web-doi-chieu.bat`. Web đối chiếu mở tại `http://127.0.0.1:8768/`, hỗ trợ sửa trực tiếp văn bản và lưu vào `Label.txt` mà giữ nguyên toạ độ box và `fileState.txt`. Phím tắt hỗ trợ gồm `←`/`→` (đổi trang), `↑`/`↓` (chọn dòng), `Enter` (phóng to ảnh trang), `R` (tải lại từ đĩa), `Ctrl+S` (lưu chữ) và `?` (xem bảng phím tắt).

## 7. Dóng hàng song ngữ và xuất kết quả

Trên web, bấm **3 Dóng hàng** ở thanh các bước ở đầu màn hình sách. Mỗi box có id `<tên văn bản>.<trang>.<dòng>`; id Nôm và id Việt được sắp rồi ghép lần lượt. Hai ô **Nguồn Nôm** và **Nguồn Việt** cho lấy mỗi vế từ thư mục khác hoặc từ sách khác. Ô lệch thì bấm **Miss vi** (ô Nôm không có Việt) hoặc **Miss nôm** (ô Việt không có Nôm) để các hàng sau ghép lại đúng; mỗi box Nôm có ô dịch âm lấy từ API Kim Hán Nôm; nút ↑ ↓ trên mỗi box đổi chỗ box với box kề trước hoặc kề sau trong thứ tự ghép. Bấm **Xuất file** ở đầu màn hình (xuất toàn bộ các trang; muốn ghi thứ tự đã dời vào `Label.txt` thì bấm **Lưu vào Label.txt**): web chạy đúng lệnh bên dưới, theo cách ghép và dấu miss đã đặt, chỉ xuất file `.csv` (kèm báo cáo), không xuất `.xlsx`; muốn có `.xlsx` thì chạy lệnh dòng lệnh bên dưới. Web hiện báo cáo và link tải `.csv`. File luôn lưu trong thư mục của sách đang mở; nếu một trong hai nguồn lấy từ sách khác thì web cảnh báo và hỏi lại trước khi xuất. Cần nguồn Nôm và nguồn Việt đều đã có `Label.txt`.

Với sách có cả `ch/Label.txt` và `vi/Label.txt` (như cuốn `KimVânKiềuTânTruyện_金雲翹新傳`), chạy lệnh sau để dóng hàng 1-1 và xuất báo cáo:

```bash
python scripts/align_books.py --book "KimVânKiềuTânTruyện_金雲翹新傳"
```

Kết quả được tạo tại `data/<Tên sách>/`:

- `<Tên sách>.csv`: file CSV 4 cột (`img_id`, `sentence_id`, `SinoNom_Char`, `ChuQN_txt`) mã hoá UTF-8 có BOM.
- `<Tên sách>.xlsx`: file Excel cùng 4 cột, cột `SinoNom_Char` dùng font `HAN NOM A`, cột `ChuQN_txt` dùng `Times New Roman`, tự động tô màu đen/đỏ theo từng chữ dựa trên từ điển (`unihan_vi.json`, `nom_na_viet.json`, `nom_bo_sung.json`) và `cache/translit.json`:
  - Màu đen: chữ Nôm và tiếng Quốc ngữ khớp nhau về âm/dị thể trong từ điển.
  - Màu đỏ: chữ Nôm và tiếng Quốc ngữ khác nhau hoặc chữ Nôm chưa có âm khớp trong từ điển.
- `align_report.txt`: thống kê số hàng, kiểm tra lệch số chữ từng cặp, danh sách cặp màu đỏ xuất hiện nhiều và **độ tương đồng (%)** tính theo công thức của quy định đồ án.

## 8. Kiểm tra dữ liệu và đóng gói nộp bài

Chạy script kiểm tra tổng thể để xác nhận toàn bộ file nhãn, ảnh, file dóng hàng và file cố định thư viện đều hợp lệ:

```bash
python scripts/check_submission.py --strict-env
```

Khi đóng gói file nộp bài dạng `<MSSV1_MSSV2_MSSV3_MSSV4>.zip` theo quy định đồ án:

- Mỗi thư mục sách giữ lại: `<Tên sách>.pdf`, các thư mục `ch/`, `vi/`, `intro/`, `ref/`, `toc/`, `blank/`, `misc/`, cùng các file dóng hàng `<Tên sách>.csv`, `<Tên sách>.xlsx` và `align_report.txt` (với sách có cặp song ngữ).
- Loại bỏ toàn bộ thư mục/file tạm cục bộ sinh ra trong quá trình làm việc gồm: `data/<Tên sách>/cache/`, `Cache.cach`, `Label.txt.bak`, `fileState.txt.bak`, `.venv/` và file bí mật `.env`.
- Giữ đầy đủ mã nguồn (`app.py`, `review_local.py`, `scripts/`, `web/`, `review-web/`, `external/`), các file cấu hình phụ thuộc đã cố định (`requirements*.txt`, `.env.example`) và tài liệu (`README.md`, `USER_GUIDE.md`, `scripts/legacy/README.md`).
