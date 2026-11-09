# Pipeline xử lý sách Hán Nôm

Gồm hai phần:

- **Web UI `app.py`**: gán loại sách, tách PDF thành ảnh từng trang, gán nhãn trang (`ch`, `vi`, `blank`, ...) để chuẩn bị dữ liệu nộp bài. Xem [mục Web UI](#web-ui-apppy). **Không cần token.**
- **API Kim Hán Nôm** (`scripts/api/`): các hàm Python gọi API upload ảnh, OCR và dịch âm Hán-Nôm của `https://kimhannom.fit.hcmus.edu.vn/`, dùng trong pipeline xử lý. Xem [mục 2 trở đi](#2-lấy-token).

## Cấu trúc thư mục

```
GK/
├── .env                  <- điền TOKEN vào đây
├── .env.example          <- file mẫu
├── requirements.txt      <- thư viện cơ bản
├── requirements-ocr.txt  <- thêm cho OCR cục bộ (PaddleOCR-VL) và PPOCRLabel
├── open_ppocrlabel.bat   <- bấm đúp để mở PPOCRLabel không qua app (xem mục "Mở PPOCRLabel để kiểm tra")
├── app.py                <- file chính: Web UI (gán loại sách, tách trang), chạy `python app.py`
├── web/index.html        <- giao diện của app.py
├── data/                 <- dữ liệu, cấu trúc giống thư mục nộp bài (git chỉ theo dõi các file cache .json, xem bên dưới)
│   ├── cache/            <- cache dùng chung: type.json (loại sách), uploads.json (lịch sử upload ảnh)
│   └── <Tên sách>/       <- <Tên sách>.pdf, ch/ vi/ intro/ ref/ toc/ blank/ misc/ và cache/ (không nộp)
├── external/PPOCRLabel/ <- bản PPOCRLabel chép vào dự án để sửa trực tiếp (xem CHANGES_GK.md)
├── img/                  <- ảnh dùng để test
├── scripts/
│   ├── library.py            <- cấu trúc thư mục data/, đường dẫn dùng chung
│   ├── book_types.py         <- loại sách (T1, T2, ...) và type.json
│   ├── page_splitter.py      <- tách PDF thành ảnh JPEG (thư viện + dòng lệnh)
│   ├── page_cache.py         <- cache nhãn từng trang (cache/phan_loai.json)
│   ├── blank_detector.py     <- nhận diện nhanh trang blank từ PDF (numpy + OpenCV), có dòng lệnh
│   ├── auto_tag.py           <- tự gán ch/vi/ch_vi theo loại sách (sau blank), có dòng lệnh
│   ├── split_jobs.py         <- tách trang chạy nền, nhận diện trang blank, tự gán nhãn, ảnh thu nhỏ cho Web UI
│   ├── ocr_engines.py        <- 3 nguồn OCR: Kim Hán Nôm, PaddleOCR-VL, row_projection; thứ tự box
│   ├── engine_bridge.py      <- nối PPOCRLabel với 3 engine OCR (nhận dạng cả trang/một box, dùng chung cache)
│   ├── ocr_jobs.py           <- bước OCR: chép ảnh theo nhãn, chạy nền, ghi Label.txt, mở PPOCRLabel
│   ├── box_check.py          <- đối chiếu chữ Nôm API với số chữ (projection) của box, gắn cờ "chưa check"
│   ├── box_review.py         <- kiểm tra từng box trong PPOCRLabel: phân tích cờ, dấu "đã check", tính lại fileState
│   ├── char_projection.py    <- đếm chữ trong ảnh một hàng bằng chiếu dọc
│   ├── import_kimhannom_csv.py <- nhập CSV Kim Hán Nôm đã đọc sẵn vào cache chữ trong box
│   ├── label_io.py           <- đọc/ghi/kiểm tra Label.txt và fileState.txt (chuẩn PPOCRLabel)
│   ├── page_classifier.py    <- phân loại từng trang vào ch/ vi/ ... (Web UI cũ + dòng lệnh)
│   ├── pdfslice_split.py     <- tách PDF bằng pdfslice_py (công cụ cũ)
│   └── api/
│       ├── sinonom_api.py       <- 3 hàm gọi API, trả nguyên JSON server trả về
│       ├── sinonom_service.py   <- hàm dùng cho pipeline: kiểm tra lỗi, trả `data`, lưu lịch sử upload
│       └── sinonom_demo.py      <- file chạy thử các hàm service
└── docs/images/          <- ảnh tài liệu API
```

## Web UI (`app.py`)

### Chạy

```bash
pip install -r requirements.txt     # chỉ cần thư viện cơ bản, không cần requirements-ocr.txt
python app.py
```

App mở trình duyệt tại `http://localhost:8766` (nếu cổng bận thì thử cổng kế tiếp, tối đa 20 cổng; địa chỉ thật được in ra console). Nhấn **Ctrl+C** ở console để dừng.

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--data DIR` | Thư mục dữ liệu (mặc định `data/`) |
| `--type-file FILE` | File JSON lưu loại sách (mặc định `<data>/cache/type.json`) |
| `--port N` | Cổng bắt đầu (mặc định `8766`) |
| `--no-browser` | Không tự mở trình duyệt |

App chỉ lắng nghe ở `127.0.0.1` (chỉ máy bạn truy cập được).

### Chuẩn bị dữ liệu: bỏ file PDF vào `data/`

Git **chỉ theo dõi các file cache `.json`** trong `data/`, nên sau khi clone repo bạn tự chép PDF vào. Cụ thể (xem `.gitignore`):

| Được đưa lên git | Không đưa lên git |
|---|---|
| `data/cache/.gitkeep`, `type.json`, `uploads.json` | PDF, ảnh trong `ch/ vi/ intro/ ref/ toc/ blank/ misc/`, `Label.txt`, `fileState.txt` |
| `data/<Tên sách>/cache/*.json` (`phan_loai.json`, `ocr_log.json`) | `data/<Tên sách>/cache/pages/` và `thumbs/` (ảnh) |
| `data/<Tên sách>/cache/ocr/<engine>/<NNN>.json` (kết quả OCR thô) | ảnh và file debug của row_projection (`cache/ocr/row_projection/<NNN>/`), file `.bak` |

Nhờ vậy cả nhóm dùng chung nhãn trang và kết quả OCR đã chạy mà không phải gọi lại API Kim Hán Nôm hay chạy lại mô hình. Cache gắn với ảnh theo sha256, nên máy khác tách trang ra ảnh khác byte thì cache không khớp và tự tính lại. `.gitkeep` chỉ cần ở `data/cache/` (thư mục của sách sinh ra khi có PDF hoặc khi pull các file cache).

**Cách 1 (đơn giản nhất): chép thẳng file PDF vào `data/`**

```
data/
├── Kim Vân Kiều tân truyện.pdf
└── Tống Trân tân truyện.pdf
```

Chạy `python app.py` (hoặc tải lại trang web nếu app đang chạy), mỗi PDF tự được chuyển vào thư mục riêng cùng tên và app tạo sẵn các thư mục con:

```
data/
├── cache/
├── Kim Vân Kiều tân truyện/
│   ├── Kim Vân Kiều tân truyện.pdf
│   ├── ch/ vi/ intro/ ref/ toc/ blank/ misc/ cache/
└── Tống Trân tân truyện/
    └── ...
```

Console in dòng `Đã chuyển '<tên>.pdf' vào <tên>/` cho mỗi file được chuyển.

**Cách 2: tự tạo thư mục sách**

Tạo `data/<Tên sách>/` rồi bỏ file đúng tên `<Tên sách>.pdf` vào trong. Tên thư mục và tên file PDF phải trùng nhau (không phân biệt hoa thường), nếu không app không nhận ra đó là một sách.

**Lưu ý**

- Tên file PDF cũng là tên sách trong app và là khoá trong `type.json`, nên đặt tên rõ ràng và **đừng đổi tên sau khi đã gán loại** (đổi tên thì loại cũ thành mục "trong type.json không có PDF tương ứng" ở cuối danh sách).
- Tiếng Việt có dấu, dấu cách và chữ Hán trong tên đều dùng được.
- Không bao giờ ghi đè: nếu `data/<Tên sách>/` đã có PDF cùng tên thì file lẻ bị bỏ qua và app in `Bỏ qua '<tên>.pdf': ... đã có PDF cùng tên`. Hãy đổi tên hoặc xoá bản cũ rồi tải lại trang.
- Tên `cache` và tên bắt đầu bằng dấu `.` không dùng được làm tên sách.
- Chỉ nhận file `.pdf` nằm trực tiếp trong `data/` hoặc trong đúng một thư mục sách; PDF lồng sâu hơn không được nhận.
- Muốn dùng thư mục khác: `python app.py --data <đường dẫn>`.
- Sau khi chép thêm PDF khi app đang chạy, **tải lại trang (F5)** là thấy sách mới trong danh sách và app tự bắt đầu nhận diện trang trắng, gán nhãn.

### Quy trình dùng

1. **Màn hình danh sách**: mỗi dòng là một sách. Dùng ô tìm kiếm và bộ lọc loại để lọc. Bấm vào nhãn loại hoặc nút **Chọn loại** để chọn loại sách (T1, T2, Tnom, ...; xem bảng bên dưới), nút **Xem PDF** mở file gốc ở tab mới. Chọn xong bấm **Save** để lưu vào `data/cache/type.json` (chưa lưu thì có chữ "Chưa lưu" và trình duyệt cảnh báo khi đóng tab).
2. **Tách trang**: bấm **Trang** ở dòng của sách để vào màn hình trang, chọn DPI (150 / 200 / 300 / 400, mặc định 300), bấm **Tách trang**. Ảnh JPEG lưu ở `data/<Tên sách>/cache/pages/` (`<Tên sách>.001.jpg`, ...), thanh tiến trình hiện trong lúc chạy. Mặc định bỏ qua trang đã có ảnh hợp lệ; tích **Ghi đè** để tách lại tất cả. Mỗi lúc chỉ tách một sách, bấm thêm sẽ báo bận.
3. **Xem và gán nhãn trang**: lưới ảnh thu nhỏ của mọi trang, mỗi trang có nhãn hiện tại. Lọc theo nhãn bằng ô **Lọc**. Chọn trang bằng ô tích ở góc ảnh, hoặc gõ khoảng vào ô **Trang** rồi bấm **Chọn** (`1-5, 8, 10-12`; `40-` là từ trang 40 đến hết; `a:b:c` có bước nhảy, ví dụ `2::2` là mọi trang chẵn), hoặc **Chọn tất cả** (theo bộ lọc đang hiển thị) / **Bỏ chọn**. Sau đó bấm **Gán nhãn ▾** và chọn nhãn cho các trang đã chọn. Nút **ⓘ** hiện mô tả từng nhãn. Bấm vào ảnh một trang để xem ảnh đầy đủ (phím **←** **→** chuyển trang đang hiển thị, **Esc** hoặc bấm ra ngoài để đóng).
4. Nhãn được lưu ngay vào `data/<Tên sách>/cache/phan_loai.json`, không cần bấm Save.
5. Gán nhãn xong thì sang bước OCR (bấm **2. OCR →**), xem mục [Bước 2: OCR](#bước-2-ocr).

Các nhãn trang: `ch` (Hán Nôm), `vi` (Quốc ngữ), `ch_vi` (cả hai, bố cục T2b), `intro`, `toc`, `ref`, `blank`, `misc`, `unknown` (chưa gán, mặc định).

### Loại sách

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

### Thư mục sinh ra

```
data/
├── cache/type.json                 <- loại từng sách
└── <Tên sách>/
    ├── <Tên sách>.pdf
    ├── ch/ vi/ intro/ ref/ toc/ blank/ misc/   <- cấu trúc nộp bài
    └── cache/                      <- không nộp
        ├── pages/                  <- ảnh JPEG đã tách
        ├── thumbs/                 <- ảnh thu nhỏ cho Web UI
        └── phan_loai.json          <- nhãn từng trang
```

### Tự gán nhãn trang

Không cần bấm nút nào. Khi chạy `python app.py`, khi có PDF mới trong `data/` hoặc khi lưu loại sách mới, một luồng nền tự gán `blank` cho trang trắng rồi chia các trang `unknown` còn lại theo loại sách (tiến trình hiện ở dòng thống kê của sách). Sách đã xử lý và không đổi loại thì không chạy lại. Khi loại sách đổi, các nhãn `ch`/`vi`/`ch_vi` do máy gán được gán lại theo loại mới, nhãn gán tay giữ nguyên. Cũng chạy được bằng dòng lệnh: `python -m scripts.auto_tag [--book TÊN]`.

| Loại sách | Nhãn |
|---|---|
| Tnom, Tnomw | tất cả `ch` |
| Tqn | tất cả `vi` |
| T2b | tất cả `ch_vi` |
| T1, T1w | nửa đầu `vi`, nửa sau `ch` |
| T2, T2w | xen kẽ `ch`, `vi`, `ch`, `vi`, ... (Nôm trước, bỏ qua blank) |
| Tref, Tx, chưa phân loại | không gán, gán tay |

Đây là giả định theo bố cục nên có thể sai (T2 thực tế có thể xen kẽ theo khối, ranh giới T1 hiếm khi đúng giữa). Chỉ trang `unknown` bị đổi và trang đã có nhãn không tính vào chuỗi xen kẽ/chia đôi. Vì máy tự gán ngay khi mở app, hãy duyệt lưới trang và sửa tay `intro`, `toc`, `ref`, `misc` cùng các trang gán sai; nhãn sửa tay không bị ghi đè. Kiểm tra quy tắc bằng `python tests/test_auto_tag.py`.


### Bước 2: OCR

Sau khi gán nhãn trang xong, bấm **2. OCR →** ở thanh đầu của màn hình sách. Màn hình có mục trạng thái hai mô hình chạy trên máy, **PaddleOCR-VL** và **Vintern-1B**, mỗi mục có nút **Nạp mô hình** (nạp ở nền, dùng chung cho mọi ảnh và mọi sách cho tới khi tắt app). App **không tự nạp** khi vào bước này; khi bạn chạy OCR cần PaddleOCR-VL mà chưa nạp thì app tự nạp rồi chạy (các ảnh đợi nạp xong), còn Vintern tự nạp ở lần đọc chữ đầu. Việc OCR làm **theo từng thư mục** bằng nút **OCR thư mục này** ở bảng cuối màn hình, xem [OCR từng thư mục](#ocr-từng-thư-mục).

Cách làm của từng nguồn theo nhãn trang (chọn trong popup của nút **OCR thư mục này**, xem bên dưới):

| Nhãn | Nguồn đề xuất | Cách làm |
|---|---|---|
| `ch` | Kim Hán Nôm (API) | Cần `TOKEN` trong `.env`. Box sắp từ phải sang trái, trên xuống dưới |
| `vi` | PaddleOCR-VL | Chạy trên máy, cần `paddleocr[doc-parser]` (xem `requirements-ocr.txt`). Box theo khối văn bản, đúng thứ tự đọc của mô hình |
| `ch_vi` (bản chụp đẹp) | row_projection | Tách hàng chữ ngang bằng xử lý ảnh. Hàng lẻ ghi vào `ch/`, hàng chẵn vào `vi/` (hàng Hán xen hàng Quốc ngữ). Trước khi chia, **hậu xử lý ô nhỏ** gộp mảnh mỏng (phần chữ cao/đuôi chữ bị cắt rời) vào hàng kề và bỏ ô quá hẹp (nét nguệch ngoạc, số kho lưu, vết bẩn), để số hàng không bị thừa và thứ tự lẻ/chẵn không bị lệch (trên vở "Nhật Tỉnh Ngâm": số hàng bị chia sai ngôn ngữ giảm từ 10,5% xuống ~0%); chỉnh bằng `--sliver-ratio`, `--min-width-ratio`... hoặc tắt bằng `--no-post` ở `scripts/row_projection_viz.py`. **Đọc chữ trong từng box** (ô tích "Đọc chữ trong box", mặc định bật): hàng `ch` đọc bằng Kim Hán Nôm (cần `TOKEN`; ảnh cắt từng box, hàng ngang `ocr_id=4`, `reading_direction=2`), hàng `vi` đọc bằng **Vintern-1B-v3.5** (`5CD-AI/Vintern-1B-v3_5`, mỗi box là một lần hỏi "chép lại chữ trong ảnh"; xem mục [Vintern đọc chữ Quốc ngữ](#vintern-đọc-chữ-quốc-ngữ)). Hai engine này **chỉ trả chữ, không tìm box mới**: toạ độ box luôn là của row_projection. Box không đọc được để `transcription` rỗng; bỏ tích thì mọi box rỗng. Soát lại trong PPOCRLabel (chữ viết tay dễ sai) |
| `intro`, `ref`, `toc` | PaddleOCR-VL | Như `vi` |
| `blank`, `misc` | không OCR | Chỉ chép ảnh vào thư mục |
| `unknown` | bỏ qua | Gán nhãn ở bước 1 trước |

Với `ch_vi` mà chọn Kim Hán Nôm hoặc PaddleOCR-VL: box có chữ Hán/Nôm chiếm đa số thì vào `ch/`, còn lại vào `vi/` (Kim Hán Nôm chỉ ghi phần `ch/`).

#### OCR từng thư mục

Không cần chạy hết các thư mục. Bảng "OCR riêng từng thư mục và kiểm tra bằng PPOCRLabel" ở cuối màn hình OCR có nút **OCR thư mục này** trên từng dòng; bấm thì hiện popup để chọn:

1. **Việc cần làm**
   - **Phát hiện box và đọc chữ**: tìm box mới rồi đọc chữ trong từng box.
   - **Chỉ phát hiện box**: chỉ tìm box, chữ để trống (đọc chữ sau).
   - **Chỉ đọc chữ trên box đã có**: không tìm box mới, giữ nguyên các box đang có trong `Label.txt` và chỉ điền chữ.
2. **Phát hiện box bằng** (không hiện khi chỉ đọc chữ) và **Đọc chữ bằng** (không hiện khi chỉ phát hiện box):

| Thư mục | Phát hiện box bằng | Đọc chữ bằng |
|---|---|---|
| `ch` | Kim Hán Nôm (API) | Kim Hán Nôm (chữ do chính API trả cùng box) |
| `ch` | row_projection (tách hàng) | Kim Hán Nôm (đọc từng box) |
| `vi`, `intro`, `ref`, `toc` | PaddleOCR-VL | PaddleOCR-VL (chữ do chính nó trả cùng box) |
| `vi`, `intro`, `ref`, `toc` | row_projection (tách hàng) | PaddleOCR-VL **hoặc** Vintern-1B (đọc từng box) |
| `ch`, `vi`, ... (chỉ đọc chữ trên box đã có) | không phát hiện | `ch`: Kim Hán Nôm; `vi`, `intro`, `ref`, `toc`: PaddleOCR-VL **hoặc** Vintern-1B |

Kim Hán Nôm và PaddleOCR-VL đọc chữ cùng lúc với box nên khi chọn chúng để phát hiện box thì ô "Đọc chữ bằng" bị khoá theo. **Vintern-1B chỉ đọc chữ, không tìm được box**, nên chỉ đứng ở vế đọc chữ (với `row_projection` hoặc trên box đã có). Tách hàng chỉ hợp trang có hàng chữ ngang thẳng. Engine chưa dùng được (thiếu `TOKEN`, chưa cài PaddleOCR, chưa có môi trường Vintern) được báo ngay trong popup và nút Chạy bị khoá.

**Hướng đọc của Kim Hán Nôm trên ảnh cắt.** Khi chỉ đọc chữ trong một box (PPOCRLabel `Ctrl+R`, "Chỉ đọc chữ trên box đã có"), hướng đọc lấy từ **hình dạng ảnh cắt**: cao hơn rộng là cột dọc (phải sang trái), ngược lại là hàng ngang (trái sang phải). API trả từng chữ theo thứ tự riêng của nó (với `ocr_id=2` là phải sang trái) nên thứ tự đọc luôn do toạ độ các chữ quyết định. (Trước đây hướng được đoán từ hình dạng các chữ API trả về, chữ gần vuông bị coi là "dọc" nên hàng ngang bị lưu ngược thứ tự; lỗi này đã sửa. Trang chữ Hán đã đọc lại bằng `Ctrl+R` trước đó có thể còn bị ngược và cần đọc lại.) Với box hàng ngang có sẵn, `read_box_text` dùng `ocr_id=2`; `ocr_id=4` gộp nhiều chữ vào một box và thêm chữ không có trên các hàng chép tay đã thử.

3. **Tuỳ chọn**: với box mới, "Ghi đè ảnh đã có box" (mặc định bỏ qua ảnh đã có dòng trong `Label.txt`, giữ phần đã sửa tay); với "chỉ đọc chữ", "Đọc lại cả box đã có chữ" (mặc định chỉ đọc các box còn trống, chữ đã có kể cả đã sửa tay không bị đụng tới); "Bỏ qua cache".

Chỉ thư mục đã chọn bị đụng tới. Thư mục được nuôi bởi các nhãn: `ch/` ← `ch`, `ch_vi`; `vi/` ← `vi`, `ch_vi`; `intro/`, `ref/`, `toc/` ← nhãn cùng tên. Với trang `ch_vi` (hàng Hán xen hàng Quốc ngữ), chạy `ch/` rồi `vi/` (hoặc ngược lại) đều được: mỗi lần chỉ ghi thư mục đã chọn, và hàng Hán vẫn lấy từ cache khi chạy tiếp `vi/`, không tốn thêm lượt API. Chỉ cần engine của thư mục đang chạy (chạy `vi/` thì không cần `TOKEN`). Đổi giữa PaddleOCR-VL và Vintern chỉ đọc lại phần Quốc ngữ. API: `POST /api/ocr/folder` với `{name, folder, box, text, overwrite, use_cache}`; `box` là `kimhannom | paddleocr | row_projection | existing`, `text` là `kimhannom | paddleocr | vintern | none` (`POST /api/ocr/run` kiểu cũ theo nhãn vẫn dùng được).

Khi bấm **Chạy OCR** trong popup, app làm lần lượt:

1. Chép ảnh từ `cache/pages/` vào thư mục theo nhãn (`ch/`, `vi/`, `intro/`, ..., `blank/`, `misc/`; `ch_vi` vào cả `ch/` và `vi/`). Ảnh của trang đã đổi nhãn bị xoá khỏi thư mục cũ cùng dòng nhãn tương ứng.
2. OCR từng ảnh và ghi vào `<thư mục>/Label.txt` và `<thư mục>/fileState.txt` theo đúng chuẩn PPOCRLabel (mỗi ảnh một dòng `ch/Tên sách.012.jpg` TAB mảng `{"transcription","points"}`, mỗi box đúng 4 điểm; `fileState.txt` ghi `0`: chưa hoàn thành, chờ duyệt). Ghi từng đợt 5 ảnh nên dừng giữa chừng không mất kết quả.
3. Ghi thông số (nguồn dùng, số box, lấy từ cache hay không, ảnh lỗi) vào `cache/ocr_log.json`, không đưa vào `Label.txt`.

Lưu ý:

- Mặc định **không ghi đè** ảnh đã có dòng trong `Label.txt`, để giữ phần đã sửa tay. Tích **Ghi đè nhãn đã có** để OCR lại.
- **Điền box trống** (row_projection có đọc chữ, không ghi đè): ảnh đã có dòng nhãn nhưng còn box `transcription` rỗng (ví dụ nhãn tạo trước khi có đọc chữ, hoặc lần trước lỗi) vẫn được chạy, và **chỉ các box rỗng được đọc**. Box đã có chữ (kể cả đã sửa tay), box bạn đã dời/thêm/xoá và thứ tự box giữ nguyên; chữ chỉ điền vào box có 4 điểm trùng đúng box do row_projection tách. Ảnh được điền chữ quay về trạng thái `0` (cần duyệt lại), ảnh đủ chữ bị bỏ qua. Cột **Còn box trống** ở bảng thư mục cho biết còn bao nhiêu ảnh cần điền.
- **Ghi đè** thay toàn bộ box và chữ bằng kết quả máy, nhưng box đã đọc nằm trong cache theo từng box nên không đọc lại, chỉ phần chưa đọc hoặc lỗi mới tốn thời gian.
- **Cache kết quả thô**: xem mục dưới.
- **Thử lại và bỏ qua trang lỗi**: mỗi ảnh được thử tối đa **3 lần** (nghỉ 2 s, 4 s giữa các lần; lần sau chỉ đọc lại box còn lỗi). Vẫn lỗi thì **bỏ qua ảnh đó, ghi log và chạy ảnh khác** (phần đọc được vẫn được lưu; một engine lỗi không chặn engine kia: Kim Hán Nôm lỗi thì hàng Quốc ngữ vẫn được đọc). Job chỉ dừng khi lỗi cấu hình (thiếu `TOKEN`, chưa Nạp mô hình PaddleOCR-VL, chưa có môi trường Vintern hoặc worker Vintern không nạp được mô hình) hoặc **5 ảnh liên tiếp còn lỗi xác thực** (`000401`/`000403`, thường do `TOKEN` sai hay hết hạn).
- **Ảnh nào lỗi**: trường `status` của từng ảnh nằm trong `cache/ocr_log.json` (`ok` | `partial` một phần box lỗi | `failed` không đọc được gì), kèm `attempts`, `empty_boxes` và `errors` (thư mục, số thứ tự box, thông báo). Mục `failed` của file này liệt kê các ảnh còn lỗi, giữ tới khi ảnh đó OCR được; giao diện hiện danh sách đó (badge `lỗi` / `một phần`, số lần thử, thông báo) cùng log chạy. `Label.txt` vẫn chỉ có hai trường `transcription` và `points`. Chạy lại (không ghi đè) sẽ thử lại đúng các ảnh/box còn lỗi hoặc còn trống.
- Làm sạch văn bản ở đây chỉ gồm gom khoảng trắng, chuẩn hoá NFC và bỏ chỉ mục chú thích `[1]`, `(1)` với Quốc ngữ. Số trang, tiêu đề, chân trang, chính tả vẫn cần soát tay.
- Kim Hán Nôm ghi thêm vào `data/cache/uploads.json` mỗi lần upload ảnh mới (file này đang được theo dõi bởi git).
- Mỗi lúc chỉ chạy OCR một sách.

#### Vintern đọc chữ Quốc ngữ

Ở bước đọc chữ trong box của `row_projection`, hàng Quốc ngữ (`vi/`) đọc bằng **Vintern-1B-v3.5** thay cho PaddleOCR-VL (cũng là engine chữ mặc định khi nhận dạng một box trong PPOCRLabel cho thư mục không phải `ch`). Vintern chỉ **đọc chữ trong box đã có**, không tự tìm box, nên các nhãn `vi`/`intro`/`ref`/`toc` chọn nguồn **PaddleOCR-VL** (cả trang) vẫn dùng PaddleOCR-VL cho cả box lẫn chữ.

- **Chọn làm engine đọc chữ**: trong popup **OCR thư mục này** của `vi/`, `intro/`, `ref/`, `toc/`, ô "Đọc chữ bằng" có **Vintern-1B** (với phát hiện box bằng row_projection, hoặc "Chỉ đọc chữ trên box đã có"). Hợp trang Quốc ngữ có hàng thẳng; trang nhiều cột/đoạn thì dùng PaddleOCR-VL (box + chữ). Mặc định khi nhận dạng một box trong PPOCRLabel cho thư mục không phải `ch` là Vintern-1B.
- **Nạp mô hình trên giao diện**: mục "Mô hình Vintern-1B" ở bước OCR có nút **Nạp mô hình** như PaddleOCR-VL, kèm trạng thái chưa nạp / đang nạp / sẵn sàng (thiết bị, thời gian nạp) / lỗi. Có thể chạy OCR ngay khi đang nạp (các hàng đợi nạp xong); không bấm nút thì worker tự nạp ở lần đọc đầu. API: `POST /api/ocr/preload` với `{"model": "vintern"}`, trạng thái ở trường `vintern` của `GET /api/ocr/status`.

- **Môi trường riêng**: mã của Vintern chỉ chạy với `transformers` 4.x còn app dùng `transformers>=5.10` cho PaddleOCR-VL, nên Vintern chạy ở một tiến trình Python khác (`scripts/vintern_worker.py`, nói chuyện với app qua stdin/stdout). Cài môi trường theo `requirements-vintern.txt` rồi đặt `VINTERN_PYTHON=<đường dẫn python.exe>` trong `.env`. Không đặt thì app thử `.venv-vintern` của dự án rồi `C:\vintern\Scripts\python.exe`; không thấy thì bước OCR báo lỗi cấu hình rõ ràng ngay từ đầu.
- **Nạp một lần**: worker khởi động ở lần đọc chữ Quốc ngữ đầu tiên (lần đầu mất khoảng nửa phút để nạp mô hình lên GPU, chưa kể tải ~2 GB nếu chưa có), rồi dùng lại cho mọi box; chết giữa chừng thì tự dựng lại. Log của worker ở `%TEMP%\gk_vintern.log`.
- **Chống kết quả hỏng**: mô hình hỏi đáp đôi khi lặp vô hạn một từ/số (`ấm ấm ấm …`, `1. 2. 3. …`) hoặc chép lại nguyên câu hỏi. Kết quả như vậy bị phát hiện, thử lại một lần với phạt lặp mạnh hơn (2.5), vẫn hỏng thì box để trống (thành `empty`, lần chạy sau đọc lại) thay vì ghi chữ rác.
- **Cache**: tham số Vintern nằm trong khoá cache chữ đã đọc (`cache/ocr/row_text/`), nên chữ do PaddleOCR-VL đọc trước đây **không còn được dùng lại**; chạy lại để đọc bằng Vintern (cache tách hàng `row_projection` vẫn giữ). Muốn đọc lại các ảnh đã có dòng trong `Label.txt`, tích **Ghi đè nhãn đã có**, hoặc xoá box rỗng để chỉ điền phần trống.
- Thử riêng trên một ảnh: `C:\vintern\Scripts\python.exe scripts/vintern_viz.py <ảnh> --label <Label.txt>`.

#### Bản chụp 1 trang hay 2 trang (trang `ch`)

Mỗi trang `ch` có thêm thuộc tính `spread` (1 hoặc 2 trang sách trong một ảnh), lưu trong `cache/phan_loai.json`. Đặt ở bước 1: chọn trang, bấm **Gán nhãn ▾**, rồi chọn **Bản chụp 1 trang**, **Bản chụp 2 trang** hoặc **Bản chụp: tự đoán** (thẻ trang hiện badge `2 trang` khi đã gán tay). Chưa gán thì OCR tự đoán theo tỉ lệ ảnh: rộng/cao ≥ 1.15 là 2 trang (nhãn `ch_vi` luôn là bản chụp 1 trang theo quy định). Nhãn này chỉ ảnh hưởng bước xử lý sau khi engine tìm box, nên **không làm mất cache OCR** (cache vẫn là kết quả thô):

- **Lọc box nhiễu ở giữa** (gáy sách, tiêu đề, số trang). Mỗi box có score `0.6 × độ lệch kích thước + 0.4 × độ gần đường giữa`, trong đó độ lệch kích thước là chênh lệch so với rộng/cao trung vị của các box trong ảnh (cắt ở 1) và độ gần đường giữa giảm tuyến tính về 0 khi tâm box cách giữa ảnh `12%` chiều rộng. Score **> 0.6** thì xoá luôn. Một yếu tố đơn lẻ không đủ để bị xoá: cột chữ thật nằm sát gáy (kích thước chuẩn) hay mảnh lạ nằm xa giữa đều được giữ. Ngưỡng và trọng số chỉnh ở các hằng số `SPREAD_*` trong `scripts/ocr_engines.py`.
- **Đánh số theo quy định bản chụp 2 trang**: đánh hết trang trái rồi mới sang trang phải. Nôm dọc: cột đầu tiên bên phải của trang trái là 1, đi sang trái, rồi tới cột đầu tiên bên phải của trang phải. Chữ ngang: hàng đầu tiên của trang trái là 1, đi xuống, rồi tới trang phải.
- Áp dụng cho engine tìm box cả trang (Kim Hán Nôm, PaddleOCR-VL) ở thư mục `ch/`; `row_projection` chỉ tách hàng ngang nên không dùng.
- Log chạy ghi `Trang NNN: bản chụp 2 trang, đã xoá N box nhiễu ở giữa`.

#### Cache kết quả OCR

Mỗi lần OCR một ảnh, app lưu kết quả thô của engine vào `data/<Tên sách>/cache/ocr/<engine>/<NNN>.json` (`kimhannom`, `paddleocr`, `row_projection`; chữ đọc trong box của row_projection cache riêng **theo từng box** ở `cache/ocr/row_text/`, mỗi box có `status`: `ok` có chữ (dùng lại), `empty` engine không ra chữ (lần chạy sau đọc lại), `error` lỗi (thử lại); ghi sau từng box nên dừng giữa chừng không mất phần đã đọc). Lần sau, ảnh nào đã chạy với **đúng engine đó** thì lấy kết quả từ file này, không gọi lại API Kim Hán Nôm hay chạy lại mô hình. Màn hình OCR hiện số ảnh đã cache và số ảnh lấy từ cache trong lần chạy.

- Cache khớp khi **sha256 của ảnh** và **tham số engine** đều không đổi. Ảnh bị xoay/chỉnh sáng hoặc tham số đổi thì tự tính lại.
- Cache lưu kết quả **chưa lọc, chưa sắp xếp, chưa làm sạch**, nên đổi cách lọc khối (số trang, đầu/chân trang), sắp thứ tự box hay làm sạch văn bản thì chỉ cần chạy lại (tích **Ghi đè nhãn đã có**), không tốn lượt API hay thời gian chạy mô hình.
- Muốn gọi lại API hoặc mô hình: tích **Bỏ qua cache**. Muốn xoá hẳn: xoá thư mục `cache/ocr/<engine>/` (ảnh debug của row_projection nằm trong thư mục con của nó).
- Kết quả rỗng và ảnh lỗi của engine cả trang **không** được cache, nên lần sau sẽ thử lại. File cache hỏng được coi như chưa có.
- Nguồn nào cũng có cache riêng: đổi nguồn OCR của một nhãn thì chạy engine mới, engine cũ vẫn còn cache.
- Chỉ bước upload của Kim Hán Nôm có thêm cache riêng trong `data/cache/uploads.json`.
- **Chữ trong box theo 4 toạ độ** (`cache/ocr/row_text/`): bản ghi của một box có khoá là (thư mục, 4 điểm của box) trên một ảnh trang nhất định (sha256). Box nào ra **đúng 4 toạ độ cũ** thì dùng lại chữ đã đọc, kể cả khi `row_projection` đã chạy lại với tham số khác hay đổi nhãn trang; chỉ box đổi toạ độ mới gọi lại API.

#### Kiểm tra chữ Nôm: đếm chữ bằng projection, cờ "chưa check"

Khi OCR trang có hàng Nôm bằng **row_projection + Kim Hán Nôm** (nhãn `ch_vi`, hoặc chọn `row_projection` + `kimhannom` cho thư mục `ch`), mỗi box trong `ch/` được đối chiếu với chính ảnh của nó (`scripts/box_check.py`, `scripts/char_projection.py`):

| Cờ | Điều kiện |
|---|---|
| `count_mismatch` | số chữ API trả về khác số chữ **đếm bằng chiếu dọc** (mỗi cột pixel đếm mực, đoạn liên tiếp là một chữ) |
| `no_text` | API không đọc ra chữ hoặc lỗi |

- Trang mà **mọi** box Nôm đều sạch thì ghi `fileState.txt` = **1** (đã check). Chỉ cần một box dính cờ (hoặc trang chỉ đọc được một phần) thì giữ **0** (chưa check) để người xem lại bằng PPOCRLabel. Trang không đi qua bước này (engine cả trang, thư mục `vi/`...) vẫn là 0. Đặt `AUTO_CHECK = False` trong `scripts/ocr_jobs.py` để luôn ghi 0 như trước.
- Cờ không đổi nội dung `Label.txt`. Box nào bị cờ, vì sao: dòng log của lượt chạy (`Trang 005: 3/10 box ch/ cần kiểm tra: box 2: số chữ API 8 ≠ projection 7; ...`) và `cache/ocr_log.json` (`pages[<trang>].review`, `state`). Màn hình OCR báo số ảnh tự đánh dấu đã check và số ảnh chưa check.
- Số chữ đếm bằng projection được **cache cùng bản ghi chữ của box** (`proj`, `check_v` trong `cache/ocr/row_text/<NNN>.json`, khoá 4 toạ độ như chữ): box giữ nguyên toạ độ không bị đo lại. Đổi thuật toán đo thì tăng `VERSION` trong `scripts/box_check.py` để đo lại (không gọi lại API). Ngưỡng đếm chữ ở đầu `scripts/char_projection.py`. Xem thử: `python scripts/char_projection.py <thư mục ảnh hàng chữ>` (in số chữ từng ảnh).
- **Kiểm tra trong PPOCRLabel**: bấm nút **Kiểm tra box** (hoặc `Ctrl+Shift+K`) khi mở ảnh trong `ch/`. Hộp thoại chỉ hiện **ảnh cắt từng box, chữ (sửa được) và cảnh báo**; tick **Đã check** cho từng box có cảnh báo rồi **Lưu**. Khi **mọi box đã check** thì ảnh được đặt là đã check (`fileState` = 1, biểu tượng ✔ trong danh sách ảnh); còn box chưa check thì ảnh giữ 0. **Lưu và sang ảnh chưa check kế tiếp** nhảy sang ảnh có fileState 0 tiếp theo. Dấu check của người lưu ở `cache/box_review.json` theo 4 toạ độ box (box bị dời thì phải check lại). PPOCRLabel giờ giữ nguyên 0/1 trong `fileState.txt` (bản gốc ép thành 1 mỗi lần mở).
- Tính lại `fileState.txt` cả sách từ `Label.txt` và dấu check (vd. khi file bị ghi đè bởi bản PPOCRLabel cũ): `python scripts/box_review.py "<tên sách>"` (thêm `--dry-run` để chỉ báo cáo).
- Cờ chỉ lọc ra những box đáng ngờ, không đảm bảo box còn lại đúng: trang tự đánh dấu đã check vẫn có thể sai chữ.

#### Nhập kết quả Kim Hán Nôm đã đọc sẵn vào cache

`scripts/ocr_folder_kimhannom.py` đọc cả thư mục ảnh hàng chữ (đã cắt từ trang, kèm `manifest.csv` ghi toạ độ trong ảnh trang) và ghi `ocr_kimhannom.csv`. Để bước OCR dùng lại kết quả đó thay vì gọi API:

```bash
python scripts/import_kimhannom_csv.py img/tran_vong_tuong_si_van/ch            # thêm --dry-run để chỉ báo cáo, --book "<tên sách>" nếu không tự nhận ra
```

Mỗi dòng thành một bản ghi `ch` (`status: ok`, kèm `proj`) trong `data/<Tên sách>/cache/ocr/row_text/<NNN>.json`, khoá theo 4 toạ độ box trong `manifest.csv`. Bản ghi `ok` đã có được giữ (thêm `--force` để ghi đè).

Đồng thời script **điền chữ vào `ch/Label.txt`**: box có 4 điểm trùng toạ độ trong manifest và đang rỗng được điền chữ; box đã có chữ (kể cả sửa tay) và các trường khác như `difficult` giữ nguyên. `fileState` của ảnh là 1 nếu mọi box đều có chữ và không box nào dính cờ, ngược lại 0. `Label.txt` và `fileState.txt` được sao lưu sang `*.bak` (nếu chưa có) trước khi ghi. `--no-label` chỉ nhập cache, `--dry-run` chỉ báo cáo.

#### Mở PPOCRLabel để kiểm tra

Mỗi thư mục `ch/`, `vi/`, `intro/`, `ref/`, `toc/` có nút **Mở PPOCRLabel**, kèm số ảnh, số ảnh đã có nhãn và đã duyệt. Bấm nút thì PPOCRLabel mở lên **với đúng thư mục của dòng đó đã được mở sẵn** (app truyền `--image_dir`, tương đương File → Open Dir): ảnh, `Label.txt` và `fileState.txt` được nạp, ảnh đầu tiên hiện luôn trên màn hình. Mở bằng `open_ppocrlabel.bat` thì thêm `--image_dir "data\Tên sách\ch"`, không thêm thì chọn File → Open Dir.

**Không cần `pip install PPOCRLabel`.** Mã PPOCRLabel đã chép sẵn trong dự án ([external/PPOCRLabel/](external/PPOCRLabel/), kèm `LICENSE` Apache-2.0; các chỗ sửa riêng đánh dấu `[GK]` và liệt kê ở [CHANGES_GK.md](external/PPOCRLabel/CHANGES_GK.md)). Chỉ cần thư viện giao diện `PyQt5` (có trong `requirements-ocr.txt`): `pip install -r requirements-ocr.txt`. PPOCRLabel **không còn cần `paddlepaddle`/`paddleocr`** (xem mục nối 3 engine bên dưới). Bấm **Mở PPOCRLabel** là app chạy `scripts/ppocrlabel_boot.py` bằng Python đang chạy app (nếu thiếu thư viện thì thử `.venv` của dự án); thiếu thư viện thì màn hình OCR hiện cảnh báo kèm lệnh cài. Chỉ mở được một cửa sổ PPOCRLabel tại một thời điểm; trong lúc nó mở, các số liệu bên dưới tự cập nhật sau khi bạn lưu. Cấu hình tùy chọn trong `.env` (xem `.env.example`): `PPOCRLABEL_LANG` (`en` mặc định = giao diện tiếng Anh, `ch` = tiếng Trung, hoặc mã khác như `vi`) và `PPOCRLABEL_PYTHON` (Python khác có cài `requirements-ocr.txt`, đường dẫn tương đối tính từ thư mục gốc dự án). PPOCRLabel chỉ có giao diện tiếng Trung và tiếng Anh, không có tiếng Việt.

**Nhận dạng trong PPOCRLabel đi qua các engine của dự án** (thay cho 11 mô hình PP-OCR của nó): **Auto Recognition** (cả trang) dùng engine mặc định theo thư mục, `ch` → Kim Hán Nôm, `vi`/`intro`/`ref`/`toc` → PaddleOCR-VL (box + chữ), trang `ch_vi` → row_projection, và **dùng chung cache `cache/ocr/` với bước OCR của app** (trang đã OCR thì trả ngay, không gọi lại API/mô hình); **Re-recognition** (`Ctrl+R` một box, `Ctrl+Shift+R` cả ảnh) dùng engine chữ của chế độ đang chọn; mặc định theo thư mục (`ch` → Kim Hán Nôm, còn lại → Vintern-1B; chế độ tách hàng + PaddleOCR-VL → PaddleOCR-VL); **Choose OCR model** (`Ctrl+M`, menu PaddleOCR) mở hộp thoại **giống popup "OCR thư mục này" của app web**, theo **thư mục đang mở**: ô **Việc cần làm** (*Phát hiện box và đọc chữ (cả trang)* | *Chỉ phát hiện box (cả trang, chữ để trống)* | *Đọc lại chữ của tất cả box trong trang (giữ nguyên box)* | *Đọc lại chữ của các box đã chọn (giữ nguyên box)*, mỗi việc có mô tả bên dưới), ô **Phát hiện box bằng** và ô **Đọc chữ bằng** với cùng các mô hình như bảng [OCR từng thư mục](#ocr-từng-thư-mục) (`ch`: Kim Hán Nôm | row_projection, chữ Kim Hán Nôm; `vi`, `intro`, `ref`, `toc`: PaddleOCR-VL | row_projection, chữ PaddleOCR-VL hoặc Vintern-1B). Hộp thoại chỉ hiện ô liên quan (chỉ phát hiện box thì không hỏi mô hình chữ; chỉ đọc chữ thì không hỏi mô hình phát hiện box; Kim Hán Nôm / PaddleOCR-VL tìm box thì đọc luôn chữ nên ô chữ bị khoá theo), báo ngay mô hình chưa dùng được (thiếu `TOKEN`, chưa cài PaddleOCR, chưa có môi trường Vintern), và lần mở sau chọn sẵn theo lựa chọn đã lưu. Nút **Chạy** (khi tích *Chạy ngay trên ảnh đang mở*, mặc định bật khi đang mở ảnh) chạy luôn: *phát hiện box và đọc chữ* và *chỉ phát hiện box* nhận dạng lại cả ảnh và **thay các box hiện có**; *đọc lại chữ của tất cả box* chạy Re-recognition (`Ctrl+Shift+R`, **giữ box**, kể cả box vừa sửa chưa lưu); *đọc lại chữ của các box đã chọn* chạy Re-recognition RectBox (`Ctrl+R`) chỉ cho các box đang chọn (chưa chọn box nào thì nút bị khoá kèm lý do; `Ctrl+Shift+A` chọn tất cả). Chữ cũ của các box được đọc lại bị thay, kể cả chữ đã sửa tay. Bỏ tích thì nút đổi thành **Lưu lựa chọn**: chỉ lưu cho Auto Recognition/Re-recognition lần sau (Auto Recognition **bỏ qua ảnh đã có trong `fileState.txt`**, tức mọi ảnh app đã OCR; Re-recognition chỉ nhận dạng lại các box đang có, ảnh chưa có box nào thì không làm gì). PaddleOCR-VL chỉ nạp lúc bạn bấm nhận dạng lần đầu; Kim Hán Nôm cần `TOKEN` trong `.env`; lỗi engine hiện hộp thoại thay vì làm sập PPOCRLabel. Nhận dạng bảng đã tắt (quy định đồ án bỏ qua bảng, tranh).

**Khung OCR log trong PPOCRLabel** (dock dưới cùng; tự hiện khi bắt đầu nhận dạng, bật/tắt bằng **View → OCR log**, `Ctrl+Alt+L`) cho biết mô hình đang làm gì:

- **Việc đang chạy** kèm thời gian đã chạy, **thanh tiến độ** `đã xong/tổng` theo box và dòng chi tiết bước hiện tại ("Đang đọc box 4/7", "Đang nạp Vintern-1B… 12 s").
- **Trạng thái ba mô hình**: Kim Hán Nôm (có/thiếu `TOKEN`), PaddleOCR-VL và Vintern-1B (chưa nạp / đang nạp / sẵn sàng kèm thiết bị và thời gian nạp / lỗi), và nút **Nạp** để nạp trước mô hình ở nền.
- **Log theo thời gian**: mở đầu/kết thúc lượt chạy, mỗi lần gọi API hoặc nạp mô hình, mỗi box đọc được gì và mất bao lâu, box không đọc ra chữ (cam), lỗi (đỏ), thành công (xanh). Có nút **Xoá log** và **Sao chép log**.
- **Nút Dừng**: dừng sau box đang đọc (chữ của các box đã đọc xong được giữ).
- Áp dụng cho Re-recognition (`Ctrl+Shift+R`), đọc lại các box đã chọn (`Ctrl+R`), Cell Re-Recognition, nhận dạng ảnh đang mở và Auto Recognition. Với các việc chạy ngay trong luồng giao diện (đọc lại box), phần còn lại của PPOCRLabel (menu, canvas, các dock) tạm khoá cho tới khi xong để không bấm lung tung giữa chừng, chỉ khung log (nút Dừng) còn bấm được. Lỗi engine trong lúc chạy hàng loạt chỉ ghi vào log và báo **một lần** khi xong thay vì hộp thoại cho từng box.
- Nguồn dữ liệu: các engine, bước đọc chữ và cầu nối phát sự kiện qua `scripts/ocr_events.py` (log, tiến độ, trạng thái nạp mô hình, nhịp chờ, huỷ); app web không dùng nó (có log riêng ở bước OCR).


**Mở PPOCRLabel không qua app:** bấm đúp [open_ppocrlabel.bat](open_ppocrlabel.bat) (hoặc chạy trong cmd, thêm tham số nếu cần, ví dụ `open_ppocrlabel.bat --lang ch`; mặc định `--lang en`). Lỗi (nếu có) hiện ngay trong cửa sổ cmd. Cửa sổ hiện sau vài giây (PPOCRLabel gốc nạp 11 mô hình PP-OCR mất ~70 giây; bản này không nạp mô hình nào lúc khởi động). Nếu gọi thẳng `PPOCRLabel.exe` của gói pip (không cần nữa) thì PyQt5 nạp trước `torch` làm `torch` lỗi `WinError 1114`; `ppocrlabel_boot.py` nạp `torch` trước. Đã sửa riêng trong mã: **vùng bắt đỉnh box** tính theo pixel màn hình (bản gốc chỉ 5 pixel *của ảnh*, ảnh 300 DPI thu nhỏ chỉ còn ~1 pixel màn hình nên gần như không kéo được góc); **kéo cạnh** (rê vào giữa một cạnh rồi kéo, chỉ cạnh đó dịch song song nên box luôn là hình chữ nhật; giữ `Ctrl` khi bấm thì không bắt cạnh); **`Ctrl+Shift+A` chọn tất cả box** đang hiện (rồi `Ctrl+T` đổi hết thành hình chữ nhật trục, `Delete` xóa, kéo để dịch cả nhóm). Lưu ý: khoảng 78% box Kim Hán Nôm là tứ giác hơi nghiêng chứ không phải hình chữ nhật trục; với các box đó kéo *đỉnh* sẽ làm méo hình, nên dùng kéo cạnh hoặc `Ctrl+T`. Kiểm tra các chỉnh sửa bằng `.venv\Scripts\python.exe tests/test_ppocrlabel_boot.py`.

Lưu ý: PPOCRLabel coi **mọi ảnh có trong `fileState.txt` là đã hoàn thành** (ghi lại thành `1` khi lưu), kể cả dòng app ghi `0`; chỉ lưu sau khi đã kiểm tra hết ảnh. Kiểm tra định dạng `Label.txt` bằng `python tests/test_ocr_jobs.py`.


## 1. Cài đặt

Python 3.12. Nên dùng môi trường ảo ngay trong dự án (`.venv/` đã nằm trong `.gitignore`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt        # cơ bản: app, tách trang, nhận diện trang trắng, gọi API, row_projection
```

Muốn OCR chạy trên máy (PaddleOCR-VL) và mở PPOCRLabel để kiểm tra nhãn:

```bash
pip install torch torchvision          # chọn bản CPU/CUDA đúng máy: https://pytorch.org/get-started/locally/
pip install -r requirements-ocr.txt    # PaddleOCR-VL (paddleocr, transformers), PyQt5 (đã gồm requirements.txt)
```

- Chỉ **một** gói OpenCV (`opencv-contrib-python`, đúng bản paddlex ghim). Đừng cài thêm `opencv-python` hay bản headless: chúng ghi đè lẫn nhau lên thư mục `cv2/` và gỡ một gói sẽ làm hỏng `cv2`.
- Không cần `pip install PPOCRLabel` (mã đã có trong `external/`).
- `kraken`, `shapely` chỉ dành cho `scripts/kraken_viz.py` (thử nghiệm), app không dùng nên đã bỏ khỏi requirements.
- Kiểm tra môi trường khớp requirements: `pip install --dry-run -r requirements-ocr.txt` (không có dòng "Would install" nghĩa là khớp) và `pip check`.

## 2. Lấy token

Token là chuỗi xác thực tài khoản của bạn. **Không chia sẻ token cho người khác.**

### Cách A: lấy từ Cookies

1. Đăng nhập vào website (link "này" trong tài liệu API) và giữ nguyên trang sau khi đăng nhập.
2. Nhấn **F12** (hoặc Ctrl + Shift + I) để mở Inspect.
3. Chọn tab **Application** (Chrome/Edge/Cốc Cốc) hoặc **Storage** (Firefox).

   > Đây chỉ là trang tổng quan. **Không bấm "Clear selected"** vì sẽ xóa cookie và đăng xuất bạn.

4. Ở cột trái, mở **Cookies**, bấm vào tên miền `https://kimhannom.fit.hcmus.edu.vn`.
5. Tìm dòng có **Name = `token`**, bấm vào dòng đó rồi copy toàn bộ giá trị ở cột **Value**.

   Nếu chỉ thấy các cookie như hình dưới (`_ga...`, `_clck`, `mf_user`) thì **chưa có token**: đây toàn là cookie theo dõi. Hãy đăng nhập lại, nhấn F5 rồi xem lại, hoặc dùng Cách B.

### Cách B: lấy từ tab Network

1. Đăng nhập vào website, mở Inspect, chọn tab **Network**.
2. Nhấn **F5** để tải lại trang (hoặc thực hiện một thao tác như OCR một ảnh để trang gọi API).
3. Gõ `api` vào ô Filter, bấm vào một request bất kỳ.
4. Chọn tab **Headers**, kéo xuống **Request Headers**, tìm dòng `Authorization: Bearer ...`.
5. Copy phần chuỗi **sau chữ `Bearer `**. Đó chính là token.

## 3. Điền token vào `.env`

Mở file `.env` và dán token vào sau dấu `=`:

```
TOKEN=eyJhbGciOi...đoạn-token-của-bạn...
```

- Không cần ngoặc kép.
- Không gõ chữ `Bearer` (code tự thêm vào header). Chỉ dán phần token.
- Token hết hạn thì đăng nhập lại, lấy token mới và thay vào đây.
- File `.env` đã được thêm vào `.gitignore`, đừng đưa nó lên git hay gửi cho người khác.

## 4. Sử dụng

Có hai lớp, cả hai nằm trong `scripts/api/`:

- **`sinonom_api.py`**: mỗi hàm gọi đúng một endpoint và trả về nguyên JSON server trả về (`is_success`, `code`, `message`, `data`), không kiểm tra hay xử lý gì.
- **`sinonom_service.py`**: lớp dùng cho pipeline. Kiểm tra `is_success` / `code`, lỗi thì ném `ApiError`, thành công thì trả về phần `data`. Ghi lịch sử upload vào `data/cache/uploads.json`.

### Hàm service

```python
import sys; sys.path.insert(0, "scripts/api")
from sinonom_service import upload, ocr, ocr_file, transliterate, load_uploads, ApiError

file_name = upload("img/test_api/test_1.png")       # trả về file_name trên server
data = ocr("img/test_api/test_1.png", ocr_id=2, lang_type=2)
data["result_ocr_text"]                              # list: mỗi phần tử là một dòng chữ
ocr_file(file_name, ocr_id=1)                        # OCR file đã có sẵn trên server

tr = transliterate("皇帝制曰朕惟人臣事上百勞靡此于壯懷")
tr["result_hannom_text"]                             # list văn bản Hán-Nôm theo dòng
tr["result_text_transcription"]                      # list phiên âm Quốc ngữ tương ứng

load_uploads()                                       # các lần upload đã ghi
```

| Hàm | Endpoint | Việc làm |
|---|---|---|
| `upload(image_path, force=False)` | `image-upload` | Upload ảnh, trả về `file_name`. Ảnh cùng nội dung đã upload (theo sha256) thì dùng lại `file_name` cũ, `force=True` để upload lại. |
| `ocr_file(file_name, **params)` | `image-ocr` | OCR file đã có trên server. Trả về `data`: `result_ocr_text`, `result_bbox`, `result_file_name`. |
| `ocr(image_path, **params)` | `image-upload` + `image-ocr` | Upload (nếu cần) rồi OCR. Nếu server báo `000404` (không còn file) thì tự upload lại một lần. |
| `transliterate(text, font_type=1, lang_type=1)` | `sinonom-transliteration` | Dịch âm Hán-Nôm. Trả về `data`: `result_hannom_text`, `result_text_transcription`. |
| `load_uploads()` | không gọi API | Đọc lịch sử upload từ `data/cache/uploads.json`. |

`result_ocr_text` là list: mỗi phần tử là một dòng chữ server nhận ra trên ảnh (với chữ viết dọc thì là một cột). Một số ký tự Nôm chưa có mã Unicode chính thức được server trả về bằng mã trong vùng private use, nên có thể hiện thành ô trống tuỳ font.

### Lịch sử upload (`data/cache/uploads.json`)

Mỗi lần upload thật được ghi thêm một bản ghi, `image_path` tính tương đối từ gốc dự án (ảnh nằm ngoài dự án thì lưu đường dẫn tuyệt đối):

```json
[
  {
    "image_path": "img/test_api/test_1.png",
    "sha256": "67062d62...",
    "file_name": "upload_72243aa75b744d9595cae62f57694767.png",
    "uploaded_at": "2026-11-03T03:55:12+00:00"
  }
]
```

Đổi vị trí file bằng biến môi trường `UPLOADS_FILE`. File này chỉ thêm bản ghi, không xoá, và không có cơ chế khoá nên không nên cho nhiều tiến trình cùng ghi.

### Gọi API thô

```python
import sinonom_api

up = sinonom_api.upload_image("image.png")           # {"is_success": ..., "code": ..., "data": {"file_name": ...}}
res = sinonom_api.ocr(up["data"]["file_name"], ocr_id=2, lang_type=2)
res = sinonom_api.transliterate("皇帝")
```

### Chạy thử

`scripts/api/sinonom_demo.py` là nơi thử nhanh các hàm service. Sửa `IMAGE` và `TEXT` ở đầu file, bỏ comment dòng muốn thử rồi chạy:

```bash
python scripts/api/sinonom_demo.py
```

## 5. Tham số

| Tham số | Giá trị |
|---|---|
| `ocr_id` | 1: thông thường dọc, 2: hành chính, 3: ngoại cảnh, 4: thông thường ngang |
| `lang_type` | 0: chưa biết, 1: Hán, 2: Nôm |
| `epitaph` | 0: văn bản thường, 1: văn bia |
| `reading_direction` | 1: dọc (mặc định), 2: ngang (không bắt buộc) |
| `tag` | mặc định `null`; gán `model_id` được cấp riêng nếu muốn dùng mô hình chuyên biệt |
| `font_type` (dịch âm) | tài liệu không giải thích, ví dụ dùng `1` |

Mặc định `ocr()` / `ocr_file()` dùng `ocr_id=2`, `lang_type=2`, `epitaph=0` (ví dụ trong tài liệu dùng `epitaph=1`). Đổi bằng cách truyền tham số cùng tên, ví dụ `ocr(path, ocr_id=1, lang_type=1)`.

## 6. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Server trả `code` khác `000000` (service ném `ApiError`) | Xem `message.title` / `message.text` / `debug_msg` trong thông báo lỗi. |
| Lỗi xác thực (token sai hoặc hết hạn) | Điền `TOKEN` trong `.env`; hết hạn thì đăng nhập lại, lấy token mới. Code không còn kiểm tra token riêng, nên lỗi do server trả về. |
| Bị chặn dù token đúng | `User-Agent` không được bắt đầu bằng `python-requests`. Mặc định đã là User-Agent trình duyệt; chỉ cần kiểm tra nếu bạn đổi `USER_AGENT` trong `.env`. |
| `[000404] t_file_not_found` | `file_name` không có trên server. Hay gặp khi đưa đường dẫn ảnh trên máy vào `ocr_file()`: hàm này cần `file_name` do `upload()` trả về. `ocr()` tự upload lại khi gặp lỗi này. |
| Chữ Hán hiện thành `?` trên console Windows | Đặt `sys.stdout.reconfigure(encoding="utf-8")` trước khi in, hoặc ghi kết quả ra file UTF-8. |

## 7. Tài liệu API gốc (ảnh)

### Cấu trúc chung: domain, header, cách lấy token
![Tổng quan](docs/images/01-tong-quan-domain-header-token.png)

### Cấu trúc JSON response chuẩn
![Cấu trúc response](docs/images/02-cau-truc-response-json.png)

### Lưu ý với thư viện `requests` của Python
![User-Agent](docs/images/03-luu-y-python-requests-user-agent.png)

### API upload ảnh: `/api/web/clc-sinonom/image-upload`
![Upload ảnh](docs/images/04-api-image-upload.png)

### API OCR: `/api/web/clc-sinonom/image-ocr`
![OCR ảnh](docs/images/05-api-image-ocr.png)

### API dịch âm: `/api/web/clc-sinonom/sinonom-transliteration`
![Dịch âm](docs/images/06-api-sinonom-transliteration.png)

Với mobile, thay `/api/web/` bằng `/api/mobile/` (đặt `PLATFORM=mobile` trong `.env`).
