# Hướng dẫn sử dụng dự án (USER_GUIDE)

Tài liệu hướng dẫn chi tiết cách cài đặt và chạy dự án từ đầu đến cuối trên máy tính mới với dữ liệu đã nộp.

## 1. Môi trường thực thi

Dự án được kiểm thử và vận hành trên cấu hình:

- Hệ điều hành: Ubuntu 22.04 LTS hoặc Windows 10/11 64-bit.
- Python: 3.10 đến 3.12 (khuyến nghị Python 3.11).
- RAM: tối thiểu 8 GB, khuyến nghị 16 GB.
- Đồ hoạ: CPU hoặc GPU NVIDIA (CUDA 11.8 / 12.1).

## 2. Cài đặt môi trường

Mở terminal tại thư mục gốc của dự án và chạy các lệnh sau:

### Tạo môi trường ảo

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

### Cài đặt thư viện

```bash
pip install -r requirements.txt
pip install torch torchvision
pip install -r requirements-ocr.txt
```

Bản PPOCRLabel đã được tích hợp sẵn trong thư mục `external/PPOCRLabel/` và tinh chỉnh cho dự án, không cần chạy `pip install PPOCRLabel`.

## 3. Cấu hình Token API Kim Hán Nôm

Nếu cần gọi API nhận dạng chữ Hán Nôm từ hệ thống `https://kimhannom.fit.hcmus.edu.vn/`:

1. Đăng nhập vào trang web trên trình duyệt.
2. Nhấn F12 mở công cụ nhà phát triển, vào mục Application (hoặc Storage), chọn Cookies của trang web và sao chép giá trị của mục `token`.
3. Tạo file `.env` tại thư mục gốc và dán token:

```env
TOKEN=eyJhbGciOi...chuỗi_token...
```

Không đưa file `.env` lên git hay chia sẻ ra ngoài.

## 4. Khởi chạy Web UI

Chạy lệnh:

```bash
python app.py
```

Trình duyệt tự động mở tại địa chỉ `http://localhost:8766`. Nhấn Ctrl+C tại terminal khi muốn tắt ứng dụng.

## 5. Quy trình xử lý sách

### 5.1. Nhận diện sách và phân loại

File PDF đặt trong thư mục `data/` sẽ tự động được nhận diện:

1. Màn hình chính liệt kê toàn bộ các sách trong thư mục `data/`.
2. Bấm nút **Chọn loại** để gán loại sách theo bảng quy chuẩn:

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

3. Bấm **Save** để lưu cấu hình loại sách vào `data/cache/type.json`.
4. Bấm nút **Trang** ở hàng cuốn sách cần làm để vào giao diện chi tiết.

### 5.2. Tách trang và gán nhãn trang

1. Tại mục **1. Nhãn trang**, chọn DPI (khuyên dùng 300) rồi bấm **Tách trang**. Ảnh trang sau khi tách được lưu tại `data/<Tên sách>/cache/pages/`.
2. Xem lưới ảnh trang để kiểm tra nhãn: hệ thống tự động gán nhãn `blank` cho trang trắng và gợi ý nhãn `ch`, `vi` theo loại sách đã chọn.
3. Nếu cần sửa nhãn: tích chọn các trang trên lưới, bấm **Gán nhãn ▾** rồi chọn nhãn phù hợp:
   - `ch`: trang chữ Hán Nôm.
   - `vi`: trang chữ Quốc ngữ.
   - `intro`: trang bìa, tựa, mục lục đầu sách.
   - `blank`: trang giấy trắng.
4. Nhãn lưu tự động vào `data/<Tên sách>/cache/phan_loai.json`.

## 6. OCR và kiểm tra bằng PPOCRLabel

### 6.1. Mở PPOCRLabel từ giao diện web

1. Bấm nút **2. OCR →** ở thanh điều hướng trên cùng để chuyển sang màn hình OCR.
2. Tại bảng **OCR riêng từng thư mục và kiểm tra bằng PPOCRLabel**, các thư mục thành phần sẽ hiển thị (`ch/`, `vi/`, `intro/`...).
3. Bấm nút **Mở PPOCRLabel** ở hàng thư mục cần kiểm tra:
   - Bấm ở dòng `ch/` để mở toàn bộ ảnh và nhãn chữ Nôm.
   - Bấm ở dòng `vi/` để mở toàn bộ ảnh và nhãn chữ Quốc ngữ.
4. Cửa sổ PPOCRLabel tự động nạp thư mục ảnh cùng file nhãn `Label.txt` và trạng thái `fileState.txt`.

### 6.2. Phím tắt và thao tác trong PPOCRLabel

Cửa sổ PPOCRLabel gồm danh sách file ảnh ở bên trái, ảnh trang sách ở giữa và danh sách box cùng chữ ở bên phải.

Các phím tắt thao tác:

- `W`: bật chế độ vẽ khung chữ nhật (RectBox) mới quanh dòng chữ.
- `Ctrl+R`: nhận dạng lại chữ cho box đang chọn hoặc box vừa vẽ.
- `Ctrl+Shift+R`: nhận dạng lại toàn bộ box trên trang.
- `Ctrl+B`: tự động sắp xếp lại thứ tự box theo quy chuẩn đọc (Nôm dọc từ trên xuống dưới, phải sang trái; Quốc ngữ từ trên xuống dưới, trái sang phải).
- `Alt+Up` / `Alt+Down`: đưa box đang chọn lên trước hoặc xuống sau trong danh sách dòng/câu.
- `Ctrl+Shift+A`: chọn tất cả box đang có trên trang.
- `Ctrl+T`: chuyển box tứ giác thành hình chữ nhật song song các trục toạ độ.
- `Delete`: xoá box đang chọn.
- Rê chuột vào giữa cạnh box rồi kéo: dịch cạnh song song, giúp box ôm khít chữ mà không bị méo góc.
- `Space` hoặc nút **Check**: đánh dấu trang hiện tại đã duyệt xong (ghi `1` vào `fileState.txt`) và tự động chuyển sang trang tiếp theo.
- `D` / `A`: chuyển sang trang tiếp theo / trang trước đó.
- `Ctrl+S`: lưu nhãn vào file `Label.txt`.
- `Ctrl+Shift+K`: mở hộp thoại kiểm tra chi tiết các box bị cảnh báo lệch số chữ.
- `Ctrl+M`: mở hộp thoại chọn mô hình OCR.

Quy trình duyệt: kiểm tra box xanh trên ảnh, sửa chữ ở danh sách bên phải nếu cần, bấm `Ctrl+B` để chuẩn hoá thứ tự câu, sau đó bấm `Space` (hoặc nút Check) để hoàn thành trang và chuyển tiếp. Khi tắt PPOCRLabel, số liệu trang đã duyệt trên giao diện web tự động cập nhật.

## 7. Dóng hàng song ngữ và xuất kết quả

Với sách có cả trang Nôm và Quốc ngữ, chạy lệnh sau để dóng hàng 1-1 từng câu thơ:

```bash
python scripts/align_books.py --book "KimVânKiềuTânTruyện_金雲翹新傳"
```

Kết quả xuất ra tại thư mục `data/<Tên sách>/`:

- `<Tên sách>.xlsx`: file Excel gồm 4 cột (`img_id`, `sentence_id`, `SinoNom_Char`, `ChuQN_txt`), cột Nôm định dạng font HAN NOM A.
- `<Tên sách>.csv`: file CSV mã hoá UTF-8 có BOM, mở trực tiếp bằng Excel không lỗi font.
- `align_report.txt`: báo cáo các trường hợp lệch dòng hoặc hàng một vế.

### Quy định cấu trúc và tô màu file `<Tên sách>.xlsx`:

- Cấu trúc giống như file `<Tên sách>.csv` (gồm 4 cột).
- Tô màu chữ Nôm và chữ Quốc ngữ để thể hiện tình trạng dóng hàng:
  - Màu đen (bắt buộc): Chữ Nôm và chữ Quốc ngữ giống nhau về nghĩa hoặc âm đọc (được kiểm tra qua từ điển Unihan và từ điển Nôm bổ sung).
  - Màu đỏ (bắt buộc): Chữ Nôm và chữ Quốc ngữ khác nhau về nghĩa hoặc âm đọc.
  - Chữ Nôm không tìm thấy trong từ điển cũng được tính là sai: tô màu đỏ cho cả chữ Nôm và chữ Quốc ngữ tương ứng.
- Ví dụ:
  - Chữ 一 và `nhất` giống nghĩa nhau: tô màu đen cho cả hai chữ.
  - Chữ 一 và `nhị` khác nghĩa nhau: tô màu đỏ cho cả hai chữ.
