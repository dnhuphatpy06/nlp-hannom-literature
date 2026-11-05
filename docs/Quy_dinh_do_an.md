# Hướng dẫn thực hiện đồ án xây dựng dữ liệu song song Hán Nôm và Quốc ngữ

## Tổng quan

Tài liệu để phổ biến và hướng dẫn chung các nhóm về quy định thực hiện đồ án xây dựng dữ liệu song song Hán Nôm và Quốc ngữ.

## Quy định chung

- Thời gian nộp dữ liệu dựa theo thời gian đã được các thầy phổ biến.
- **Trao đổi trực tiếp trên group Zalo** nếu có câu hỏi.
- Liên hệ với người giao tài liệu nếu sách có nhiều trang có chất lượng chưa tốt (rách, mờ,…) với số lượng >20% tổng số trang Nôm.
- Luôn làm (dóng hàng) các trang có bản dịch Quốc ngữ, ví dụ các trang bìa Hán Nôm, hoặc mục lục nếu có bản dịch Quốc ngữ thì vẫn làm.
- Luôn làm toàn bộ file sách/tài liệu được giao, ngoại trừ các nhóm được bàn giao sách/tài liệu theo phần hoặc chương.
- Toàn bộ quy trình (pipeline) **khuyến khích làm tự động**, hạn chế làm thủ công, trừ khi các lỗi cần phải sửa tay thủ công.
- Không giới hạn ngôn ngữ lập trình, nhưng ưu tiên Python.
- Không giới hạn mô hình LLM, OCR, công cụ miễn phí hay tính phí,…
- Nếu nhóm đề xuất tự xây dựng mô hình riêng, cần phải cân nhắc thời gian thực hiện để kịp tiến độ.
- **Dữ liệu có thể sẽ được yêu cầu kiểm tra lại nếu chưa đúng yêu cầu hoặc chất lượng chưa tốt**.
- **Yêu cầu độ tương đồng của sách phải đạt tối thiểu là 95% (≥95%)** (Xem [**Đánh giá dữ liệu**](#đánh-giá-dữ-liệu)).
- **Nếu nhóm không thể hoàn thành đúng tiến độ, cần thông báo sớm với các thầy càng sớm càng tốt**.
- Nếu dự án có đính kèm dữ liệu khác có kích thước lớn mà không thể nộp trực tiếp được, có thể lưu trên nền tảng đám mây (**ưu tiên Huggingface**), sau đó liên hệ với các thầy và người bàn giao tài liệu để chia sẻ quyền truy cập vào dữ liệu.

## Dữ liệu thô và công cụ hỗ trợ

- Các nhóm sẽ gửi được gửi dữ liệu thô là file sách PDF, các sách này sẽ có bản Nôm và bản dịch Quốc Ngữ, thường là bản dịch âm 1-1.
- Dữ liệu từ điển để hỗ trợ kiểm tra chính tả, hoặc các nhóm có thể tự xây dựng riêng (local hoặc query trực tiếp). Sẽ được gửi sau.
- File hướng dẫn sử dụng API của trang web [`https://kimhannom.fit.hcmus.edu.vn/`](https://kimhannom.fit.hcmus.edu.vn/). Sẽ được gửi sau.

## Quy trình thực hiện tổng thể

### 1. Nhận đề tài và xác nhận phạm vi

Trước khi làm, nhóm phải xác nhận:

- Mã đề tài và tên tác phẩm.
- Quyển, tập, chương hoặc khoảng trang được giao.
- Nguồn Nôm và nguồn Quốc ngữ.
- Định dạng từng nguồn: ảnh, PDF Scan, PDF Text hoặc Text.

> **Lưu ý**
>
> Không bắt đầu OCR hàng loạt nếu chưa chắc hai bản chứa cùng phạm vi nội dung.

> **Lưu ý**
>
> Luôn làm (dóng hàng) các trang có bản dịch Quốc ngữ, ví dụ các trang bìa Hán Nôm, hoặc mục lục nếu có bản dịch Quốc ngữ thì vẫn làm (Xem [**Quy định chung**](#quy-định-chung)).

### 2. Kiểm tra nguồn dữ liệu

- Kiểm tra xem chất lượng file sách, nếu có rách, mờ hoặc chất lượng không tốt cần liên hệ người giao tài liệu trước khi thực hiện (Xem [**Quy định chung**](#quy-định-chung)).

### 3. Phân loại hình ảnh

- Chia file PDF thành các hình ảnh, đặt tên theo đúng cú pháp (Xem [**Quy định về file hình ảnh**](#quy-định-về-file-hình-ảnh)) (có thể sử dụng [pdfslice-py](https://github.com/obj-see/pdfslice-py)).
- Phân loại hình ảnh vào các thư mục (Xem [**Quy định về dữ liệu nộp**](#quy-định-về-dữ-liệu-nộp)).
- Tiền xử lý:
  - Kiểm tra hướng của hình ảnh, nếu sai thì phải xoay lại hình ảnh (có thể sử dụng [pdfarranger](https://github.com/pdfarranger/pdfarranger)).
  - Tăng chỉnh độ sáng, làm nét hình ảnh,…nếu cần thiết.
- Nếu nhóm có tiền xử lý hình ảnh, theo [**Quy định về file hình ảnh**](#quy-định-về-file-hình-ảnh) thì nhóm phải merge các hình ảnh thành file PDF mới (`<Tên sách>_updated.pdf`) và báo cáo.

### 4. Chạy OCR hình ảnh

- Cài đặt các mô hình để chạy OCR theo từng ngôn ngữ Nôm hoặc Quốc ngữ.
  - Nôm:
    - Ưu tiên sử dụng công cụ [`https://kimhannom.fit.hcmus.edu.vn/`](https://kimhannom.fit.hcmus.edu.vn/) để chạy OCR chữ Nôm, vì đây là công cụ được tinh chỉnh cho dữ liệu Hán Nôm.
    - Có thể dùng các mô hình OCR chữ Hán, hoặc các mô hình OCR đa năng (Xem [**Mô hình OCR**](#1-mô-hình-ocr-chữ-nôm) [**chữ Nôm**](#1-mô-hình-ocr-chữ-nôm)).
    - Hoặc nhóm tự xây dựng mô hình OCR chữ Nôm riêng (cần cân nhắc thời gian thực hiện để kịp tiến độ).
  - Quốc ngữ:
    - Có thể dùng các mô hình OCR chữ Quốc ngữ, hoặc các mô hình OCR đa năng (Xem [**Mô**](#2-mô-hình-ocr-chữ-quốc-ngữ) [**hình OCR chữ Quốc ngữ**](#2-mô-hình-ocr-chữ-quốc-ngữ)).
    - Hoặc nhóm tự xây dựng mô hình OCR chữ Quốc ngữ riêng (cần cân nhắc thời gian thực hiện để kịp tiến độ).
- Nên chạy kiểm thử mô hình với vài hình ảnh mẫu (sample) để kiểm tra kết quả.
- Có thể chạy 2–3 mô hình OCR, sau đó sử dụng các kỹ thuật voting để đạt kết quả tốt hơn.

### 5. Làm sạch dữ liệu

- Map dữ liệu OCR theo định dạng của [PPOCRLabel](https://github.com/PFCCLab/PPOCRLabel) (Xem [**Quy định về file OCR**](#quy-định-về-file-ocr)).
- Kiểm tra bounding box, loại bỏ các bounding box không cần thiết (Xem [**Quy định về việc đánh**](#quy-định-về-việc-đánh-bounding-box) [**bounding box**](#quy-định-về-việc-đánh-bounding-box)).
- Làm sạch dữ liệu văn bản (Xem [**Quy định về dữ liệu văn bản**](#quy-định-về-dữ-liệu-văn-bản)).
- Kiểm tra lỗi chính tả, sửa lỗi nếu cần thiết:
  - Có thể dùng từ điển (Xem [**Nguồn từ điển**](#5-nguồn-từ-điển)).
  - Hoặc các công cụ kiểm tra chính tả (Xem [**Mô hình kiểm tra chính tả (Spellcheck)**](#4-mô-hình-kiểm-tra-chính-tả-spellcheck)).

### 6. Dóng hàng dữ liệu

- Ghép từ/câu bị ngắt do chuyển trang trước khi thực hiện việc tách câu.
- Xây dựng mô hình để dóng hàng dữ liệu (Xem [**Mô hình dóng hàng Nôm – Quốc ngữ**](#3-mô-hình-dóng-hàng-nôm--quốc-ngữ)).
- Dóng hàng dữ liệu theo định dạng file `<Tên sách>.csv` và `<Tên sách>.xlsx` (Xem [**Quy định về**](#quy-định-về-việc-dóng-hàng) [**việc dóng hàng**](#quy-định-về-việc-dóng-hàng)).

### 7. Đánh giá dữ liệu

- Kiểm tra độ tương đồng giữa chữ Nôm và chữ Quốc ngữ (Xem [**Đánh giá dữ liệu**](#đánh-giá-dữ-liệu)).

### 8. Nộp dữ liệu

- Kiểm tra dữ liệu trước khi nộp, đảm bảo các file đúng định dạng, không bị lỗi font, không bị lệch cột, không bị trùng lặp hình ảnh,…
- Nộp dữ liệu theo đúng cấu trúc (Xem [**Quy định về dữ liệu nộp**](#quy-định-về-dữ-liệu-nộp)).

## Quy định về dữ liệu nộp

```text
<MSSV1_MSSV2_MSSV3_MSSV4>.zip/
|-- <Tên sách>/
|   |-- ch/
|   |   |-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |   |-- Label.txt
|   |   `-- fileState.txt
|   |-- vi/
|   |   |-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |   |-- Label.txt
|   |   `-- fileState.txt
|   |-- intro/
|   |   |-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |   |-- Label.txt
|   |   `-- fileState.txt
|   |-- ref/
|   |   |-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |   |-- Label.txt
|   |   `-- fileState.txt
|   |-- toc/
|   |   |-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |   |-- Label.txt
|   |   `-- fileState.txt
|   |-- blank/
|   |   `-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |-- misc/
|   |   `-- <Tên sách>.<Số thứ tự hình ảnh>.jpg
|   |-- <Tên sách>.pdf
|   |-- <Tên sách>_updated.pdf (nếu có)
|   |-- <Tên sách>.xlsx (dữ liệu đã dóng hàng)
|   `-- <Tên sách>.csv (dữ liệu đã dóng hàng)
|-- <MSSV1_MSSV2_MSSV3_MSSV4>_report.pdf (dựa theo tên file zip)
|-- source.zip
`-- extras.zip (dữ liệu thêm,...)
```

**Chú thích**:

- **Tên file zip**: Do các thầy phổ biến. Mặc định: `MSSV1_MSSV2_MSSV3_MSSV4.zip`.
- Thư mục `<Tên sách>`: Thư mục chứa dữ liệu của sách
  - **Tên sách phải giữ theo tên file sách pdf được giao**, trừ khi có sự cập nhật từ phía người bàn giao.
  - Ví dụ trong quá trình xử lý tên sách `Lorem ipsum` được chuyển thành `Lorem%20ipsum`, thì dữ liệu nộp phải chuyển lại `Lorem ipsum` y như tên sách được giao.
  - **Quy định này áp dụng cho toàn bộ dữ liệu nộp**.
- Thư mục `ch` (**bắt buộc có**): Thư mục chứa các trang Hán Nôm
  - Tên hình ảnh `<Tên sách>.<Số thứ tự hình ảnh>.jpg`.
  - File dữ liệu OCR `Label.txt`, theo format của PPOCRLabel (Xem [**Quy định về file OCR**](#quy-định-về-file-ocr)).
  - File tiến trình đánh nhãn `fileState.txt`, theo format của PPOCRLabel (Xem [**Quy định về file**](#quy-định-về-file-ocr) [**OCR**](#quy-định-về-file-ocr)).
- Thư mục `vi` (**bắt buộc có**): Thư mục chứa các trang Quốc Ngữ
  - Cấu trúc giống như thư mục `ch`.
- Thư mục `intro` (không bắt buộc làm): Thư mục chứa các trang giới thiệu, nếu có
  - Cấu trúc giống như thư mục `ch`.
- Thư mục `ref` (không bắt buộc làm): Thư mục chứa các trang chú thích, nếu có
  - Cấu trúc giống như thư mục `ch`.
- Thư mục `toc` (không bắt buộc làm): Thư mục chứa các trang mục lục, nếu có
  - Cấu trúc giống như thư mục `ch`.
- Thư mục `blank`: Thư mục chỉ chứa các trang trắng, các trang không có dữ liệu cần OCR (ví dụ: trang có khung khắc in Nôm nhưng không có chữ nào), nếu có.
- Thư mục `misc`: Thư mục chứa các trang còn lại, thường là trang bìa hoặc không liên quan.
- File `<Tên sách>.pdf` (**bắt buộc có**): **File tài liệu gốc** mà nhóm được bàn giao.
- File `<Tên sách>_updated.pdf`: File tài liệu mà nhóm đã chỉnh sửa (nếu có).
- File `<Tên sách>.xlsx` (**bắt buộc có**): Xem [**Quy định về việc dóng hàng**](#quy-định-về-việc-dóng-hàng).
- File `<Tên sách>.csv` (**bắt buộc có**): Xem [**Quy định về việc dóng hàng**](#quy-định-về-việc-dóng-hàng).
- File `<MSSV1_MSSV2_MSSV3_MSSV4>_report.pdf` (**bắt buộc có**): Xem [**Quy định về file báo cáo**](#quy-định-về-file-báo-cáo).
- File `source.zip` (**bắt buộc có**): Xem [**Quy định về mã nguồn**](#quy-định-về-mã-nguồn).
- File `extras.zip`: Dữ liệu thêm. Không có quy định cụ thể, nhưng cần nêu rõ trong báo cáo.

## Quy định về file hình ảnh

- Cú pháp: `<Tên sách>.<Số thứ tự hình ảnh>.jpg`.
- `<Số thứ tự hình ảnh>`: Gồm ít nhất 3 số, **với số thứ tự đánh từ số 1, tính từ trang đầu tiên của file pdf**. Ví dụ `Lorem ipsum.001.jpg`, `Lorem ipsum.011.jpg`, `Lorem ipsum.111.jpg`. Nếu số trang của sách lớn hơn 999 thì để không thêm chữ số 0 ở đầu nữa, ví dụ `Lorem ipsum.1234.jpg`, `Lorem ipsum.123456.jpg`,…
- Định dạng hình ảnh (extension): `.jpg` (JPEG).
- Một hình ảnh có thể thuộc 1 hoặc nhiều thư mục khác nhau, ví dụ 1 hình ảnh đều thuộc thư mục `ch` hoặc `vi` (trường hợp hình ảnh có Nôm và Quốc ngữ lẫn lộn, xem ở dưới), nhưng khi merge lại file pdf phải **đảm bảo là không có hình ảnh trùng lặp**.
- Nếu nhóm có bất kỳ thay đổi gì về hình ảnh, như chỉnh độ sáng, xoay trang, làm nét ảnh,…**cần phải merge thành file pdf mới**, gửi cùng với file thư mục (như đã quy định ở mục **Cấu trúc file** nộp) và **thông báo với người giao sách là có thay đổi về nội dung file pdf**.

## Quy định về file OCR

- Với thư mục `vi` và `ch`, sẽ bao gồm chủ yếu hai file `Label.txt` và `fileState.txt` theo định dạng của PPOCRLabel:
  - `Label.txt`: File dữ liệu gồm toạ độ bounding box và văn bản đã nhận diện, mỗi hàng (`\n`) là dữ liệu bounding box cho một hình ảnh:
    - Phần đầu là đường dẫn hình ảnh (image path): cú pháp `<folderName>/<imageName>`. Ví dụ với hình ảnh `a.jpg` nằm trong thư mục `foo`, khi dùng công cụ PPOCRLabel để mở thư mục `foo` thì file `Label.txt` sẽ được lưu tại `foo/Label.txt` và đường dẫn hình ảnh trong `Label.txt` sẽ có cú pháp `foo/a.jpg`.
    - Phần sau là dữ liệu bounding box: mảng chứa các đối tượng (array object) với yêu cầu có 2 trường chính là `transcription` (văn bản nhận dạng) và `points` (toạ độ các điểm của bounding box).
    - **Mỗi box chỉ có đúng 4 điểm**, không được ít hơn hoặc nhiều hơn.
    - Hai phần được cách nhau bởi ký tự `\t` (tab).
    - **Nội dung** `transcription` **phải được làm sạch** và tuân thủ theo [**Quy định về dữ liệu văn bản**](#quy-định-về-dữ-liệu-văn-bản).
    - **Lưu ý**: Ngoài hai trường chính là `transcription` và `points`, có thể thêm có trường khác (như confident, score,…) nhưng phải có chú thích rõ.
  - `fileState.txt`: File tiến trình đánh nhãn, mỗi hàng (`\n`) là tình trạng đánh nhãn cho một hình ảnh:
    - Phần đầu là đường dẫn hình ảnh (image path) có cú pháp giống như trong file `Label.txt`
    - Phần sau là tình trạng gán nhãn, `1` là đã hoàn thành, `0` là chưa hoàn thành.
    - Hai phần được cách nhau bởi ký tự `\t` (tab).

`Label.txt` — mỗi ảnh là **một dòng** trong file; `TAB` là ký tự tab (`\t`), `LF` là ký tự xuống dòng (`\n`). Việc ngắt dòng bên dưới chỉ do khổ giấy, không có trong file thật.

```text
vi/Lorem ipsum.024.jpg TAB  [{"transcription":"Lorem
     ipsum","points":[[249,361],[1064,361],[1064,465],[249,465]]},{"transcription":"Lorem
     ipsum","points":[[254,540],[1068,540],[1068,878],[254,878]]}] LF
vi/Lorem ipsum.025.jpg TAB  [{"transcription":"Lorem
     ipsum","points":[[249,361],[1064,361],[1064,465],[249,465]]},{"transcription":"Lorem
     ipsum","points":[[254,540],[1068,540],[1068,878],[254,878]]}] LF
```

Cùng một dòng của ảnh `vi/Lorem ipsum.024.jpg`, trình bày lại cho dễ đọc (*chỉ để minh hoạ*, trong file thật phải nằm trên một dòng):

```text
vi/Lorem ipsum.024.jpg          <- (1) đường dẫn ảnh: <folderName>/<imageName>
TAB                             <- (2) ký tự tab ngăn cách
[                               <- (3) mảng JSON, mỗi phần tử là một bounding box
  { "transcription": "Lorem ipsum",
    "points": [[249,361], [1064,361], [1064,465], [249,465]] },   <- đúng 4 điểm
  { "transcription": "Lorem ipsum",
    "points": [[254,540], [1068,540], [1068,878], [254,878]] }
] LF
```

`fileState.txt` — sau tab là `1` (đã hoàn thành) hoặc `0` (chưa hoàn thành):

```text
vi/Lorem ipsum.024.jpg TAB  1 LF
vi/Lorem ipsum.025.jpg TAB  1 LF
```

## Quy định về việc đánh bounding box

- Các bounding box phải ôm sát, tránh để bị lệch các hàng, chữ.
- Bỏ qua các bảng biểu và tranh/ảnh nếu có.
- Loại bỏ các dữ liệu không cần thiết trong trang như: số trang (cả số trang Nôm và Quốc ngữ), tiêu đề (cả Nôm và Quốc ngữ), chân trang,…
- Trang Nôm (**ưu tiên**):
  - Cấp độ: Đánh **theo từng cột dọc**. Nếu chữ Nôm được viết ngang thì đánh theo hàng.
  - Đối với các hàng chữ Nôm được cách ra (có khoảng trắng giữa các chữ trong cột) thì đánh 1 box riêng, không gộp chung.
  - Đối với các chữ Nôm được ghép từ 2 ký tự hàng ngang, đánh 2 box riêng biệt.
- Trang Quốc ngữ:
  - Cấp độ: Nếu công cụ có hỗ trợ thì đánh box **theo hàng** (cột ngang) hoặc **theo đoạn**, còn không thì đánh 1 box cho **toàn trang**.
- Thứ tự bounding box:
  - Nôm: từ phải sang trái, từ trên xuống dưới. Nếu chữ Nôm được viết ngang thì từ trái sang phải, từ trên xuống dưới
  - Quốc ngữ: từ trái sang phải, từ trên xuống dưới.
- **Yêu cầu phải đánh nhãn cho toàn bộ hình ảnh có trong thư mục, hoặc theo phần/chương được giao**.

> **Lưu ý**
>
> Phải đảm bảo các file đúng định dạng của PPOCRLabel, mở và xem được trên công cụ [PPOCR-](https://github.com/PFCCLab/PPOCRLabel) [Label](https://github.com/PFCCLab/PPOCRLabel).

**Các trường hợp đặc biệt**

*Xem* [***Chú thích về phân loại sách***](#chú-thích-về-phân-loại-sách) *để biết về các phân loại sách.*

- **Bản chụp 1 trang**:
  - Nôm và Quốc ngữ lẫn lộn (thường là phân loại T2b, T2bw):
    - Lưu hình ảnh cho cả hai thư mục `ch` và `vi`. Hình ảnh trong `ch` thì chỉ giữ lại các bounding box Nôm và ngược lại, hình ảnh trong `vi` chỉ giữ lại các bounding box Quốc ngữ.
    - Thứ tự bounding box vẫn theo như trên, bởi vì đã loại bỏ các bounding box không cùng ngôn ngữ.
- **Bản chụp 2 trang** (trang trái trước, phải sau):
  - Nôm – Nôm (thường là phân loại T1, T1w, Tnom, Tnomw):
    - Thứ tự bounding box: cột đầu tiên bên phải của trang trái tính là 1, đánh thứ tự tiếp theo từ phải sang trái như bình thường cho trang trái, sau đó là cột đầu tiên bên phải của trang phải.
    - Nếu Nôm viết ngang thì xử lý như trường hợp Quốc ngữ – Quốc ngữ.
  - Quốc ngữ – Quốc ngữ (thường là T1, T1w, Tqn): hàng đầu tiên của trang trái tính là 1, đánh thứ tự tiếp theo từ trên xuống dưới như bình thường cho trang trái, sau đó là hàng đầu tiên của trang phải.
  - Nôm – Quốc ngữ (thường là phân loại T2, T2w): xử lý như bản chụp 1 trang.

## Quy định về dữ liệu văn bản

- Bỏ đầu trang, chân trang và các trang không thuộc nội dung chính.
- Loại dòng trùng do OCR lặp vùng.
- Loại bỏ chú thích nằm ngoài nội dung chính, bao gồm chú thích ở chân trang, các chỉ mục chú thích chèn trong nội dung chính (như \[1\], (1) v.v..).
- Sửa ký tự rác và khoảng trắng bất thường.
- Chuẩn hóa dấu câu nhưng không thay đổi nghĩa.
- Giữ tên riêng, niên hiệu, con số và đơn vị đo theo nguồn.
- Nôm:
  - Các chữ viết dính liền, không có khoảng trắng. Sử dụng các dấu phù hợp, ví dụ 、(dấu phẩy), 。 (dấu chấm),…
  - Ký tự hợp lệ, có thể xem được bằng các font chữ Hán Nôm được đề cập trong phần thông tin tham khảo.
- Quốc ngữ:
  - Đúng chính tả, các chữ tuân theo quy cách Tiếng Việt thông thường, ví dụ khoảng trắng sau dấu phẩy, dấu chấm,…

## Quy định về việc dóng hàng

### Tách câu

- **Ghép từ/câu bị ngắt do chuyển trang** trước khi thực hiện việc tách câu.
- Nôm:
  - Dùng 。！？； làm gợi ý, không coi là quy tắc tuyệt đối.
  - Văn bản cổ có thể không có dấu câu hoặc dấu câu do người biên tập thêm.
- Quốc ngữ:
  - Dùng `.?!;:` kết hợp cấu trúc ngữ nghĩa.
  - Chú ý chữ viết tắt, số thứ tự và tên riêng có dấu chấm.
  - Bản dịch có thể gộp hoặc diễn giải nhiều câu Nôm.

### Cấu trúc file dóng hàng (align)

- Cấu trúc file `<Tên sách>.csv`:

| Tên cột | Chú thích |
| --- | --- |
| `img_id` | Tên hình ảnh, không chứa tên thư mục |
| `sentence_id` | **Số thứ tự câu Nôm trong các trang Nôm**, với số thứ tự đánh từ 1. **Bắt buộc theo định dạng** `<img_id bỏ đuôi .jpg>.<số thứ tự câu>`, ví dụ `Lorem ipsum.024.1` |
| `SinoNom_Char` | Văn bản Hán Nôm |
| `ChuQN_txt` | Văn bản Quốc ngữ |

- Ví dụ:

| img_id | sentence_id | SinoNom_Char | ChuQN_txt |
| --- | --- | --- | --- |
| Lorem ipsum.024.jpg | Lorem ipsum.024.1 | 経申母福。 | Kinh Thân Mẫu Phúc. |
| Lorem ipsum.024.jpg | Lorem ipsum.024.2 | 経福实糝䋦。 | Kinh Phúc Thật Tám Mối. |
| Lorem ipsum.025.jpg | Lorem ipsum.025.1 | 経告命。 | Kinh Cáo Mình. |

- Cấu trúc file `<Tên sách>.xlsx`:
  - Cấu trúc giống như Cấu trúc file `<Tên sách>.csv`.
  - Tô màu chữ Nôm và chữ Quốc ngữ để thể hiện tình trạng dóng hàng:
    - Màu đen (**bắt buộc**): Chữ Nôm và chữ Quốc ngữ giống nhau về nghĩa (có thể dùng từ điển để kiểm tra).
    - Màu đỏ (**bắt buộc**): Chữ Nôm và chữ Quốc ngữ khác nhau về nghĩa (có thể dùng từ điển để kiểm tra).
    - Chữ Nôm không tìm thấy trong từ điển cũng được tính là sai: tô màu đỏ (cho cả chữ Nôm và chữ Quốc ngữ tương ứng).
    - Ví dụ:
      - Chữ 一 và `nhất` giống nghĩa nhau → tô màu đen cho cả hai chữ.
      - Chữ 一 và `nhị` khác nghĩa nhau → tô màu đỏ cho cả hai chữ.

### Các kiểu dóng hàng được chấp nhận

- `1--1`: một câu/đoạn Nôm tương ứng một câu/đoạn Việt.
- `1--n`: một đơn vị Nôm tương ứng nhiều câu Việt; gộp các câu Việt liên quan vào cùng một ô.
- `n--1`: nhiều câu Nôm tương ứng một câu Việt; gộp các câu Hán liên quan vào cùng một ô.
- `n--m`: chỉ dùng khi bản dịch tái cấu trúc mạnh và không thể tách nhỏ hơn mà vẫn giữ đúng nghĩa.

> **Lưu ý**
>
> Ưu tiên đơn vị nhỏ nhất có ý nghĩa đầy đủ. Không chia cơ học theo dấu câu nếu việc chia làm mất quan hệ dịch.

### Tiêu chí một cặp dóng hàng đúng

- Một cặp được xem là đúng khi:
  1. Hai vế nói về cùng nội dung.
  2. Không bỏ mất ý quan trọng.
  3. Không thêm nội dung thuộc câu/đoạn khác.
  4. Tên riêng, chức danh, địa danh cơ bản tương ứng.
  5. Thời gian, niên hiệu và con số tương ứng.
  6. Ranh giới câu/đoạn hợp lý.
  7. Không phải dòng tiêu đề, số trang hoặc OCR rác ghép nhầm.
- **Nếu là dịch âm thì số chữ Quốc ngữ phải bằng số chữ Hán Nôm**.
- Nếu không phải là dịch âm thì bản dịch không cần sát từng chữ; đánh giá dựa trên quan hệ dịch và phạm vi nội dung.

## Quy định về file báo cáo

- Tên file báo cáo dựa trên tên file zip theo các thầy đã phổ biến.
- Thông tin của từng thành viên: họ tên, mssv, email sinh viên.
- **Cấu trúc báo cáo dựa theo hướng dẫn của các thầy (nếu có)**. Có thể viết bằng Word, Google Docs, Latex,…**miễn là file PDF là được**.
- Mô tả các việc nhóm đã làm, như thiết lập mô hình, cào dữ liệu,…
- Phần trích dẫn (nếu có).

> **Lưu ý**
>
> Phần phụ lục phải đính kèm các hình ảnh như sau:
>
> - Ảnh chụp mẫu một trang Hán Nôm mở được bằng công cụ PPOCRLabel (kiểm tra xem có đúng format, bounding box có đúng vị trí hay không).
> - Ảnh chụp mẫu một trang Quốc ngữ mở được bằng công cụ PPOCRLabel (kiểm tra xem có đúng format, bounding box có đúng vị trí hay không).
> - Ảnh chụp mẫu trang tính (.xlsx) cho thấy file mở được và không bị lỗi font.
> - Ảnh chụp mẫu trang tính (.csv) cho thấy file mở được và không bị lệch cột.

## Quy định về mã nguồn

- Không giới hạn ngôn ngữ lập trình như đã đề cập ở phần **Quy định chung**, nhưng **ưu tiên Python**.
- File **README.md**:
  - Mô tả chi tiết dự án.
  - Chú thích rõ môi trường mà dự án đã chạy, như phiên bản Python, OS (**ưu tiên Ubuntu**), RAM, cấu hình đồ hoạ,…
- Các thư viện cài đặt (dependencies) **PHẢI** được cố định (pinned).
- File **USER_GUIDE.md**:
  - **Mô tả chi tiết cách chạy dự án từ đầu đến cuối** (cách cài thư viện, chạy lệnh,…) như trên một máy tính mới, **với dữ liệu mà nhóm đã nộp**.
  - Đối với các công cụ/mô hình có tính phí cũng cần được chú thích đầy đủ về mô hình, prompt đã sử dụng, cách cài đặt (token,…).

## Đánh giá dữ liệu

- Dữ liệu được đánh giá dựa trên phần trăm độ tương đồng của từ chữ Nôm và chữ Quốc ngữ:

  $$\text{độ tương đồng (\%)} = \left(1 - \frac{\text{tổng số chữ không tương đồng (chữ đỏ)}}{\text{tổng số chữ}}\right) \times 100$$

- **Yêu cầu độ tương đồng của sách phải đạt tối thiểu là 95% (≥95%)**.

## Thông tin tham khảo

> **Lưu ý**
>
> Các thông tin dưới đây **chỉ mang tính chất tham khảo**, không bắt buộc các nhóm phải sử dụng.

### 1. Mô hình OCR chữ Nôm

| Tên mô hình / Công cụ | Liên kết tài nguyên |
| --- | --- |
| **Công cụ chuyển tự và OCR Hán Nôm (Ưu tiên)** | [Website](https://kimhannom.fit.hcmus.edu.vn/) |
| **PP-OCRv5 (Baidu)** | [GitHub](https://github.com/PaddlePaddle/PaddleOCR) \| [ArXiv](https://arxiv.org/abs/2510.04003) \| [Demo](https://huggingface.co/spaces/MinhDS/Fine-tuned-PaddleOCRv5) |
| **HunyuanOCR (Tencent)** | [Huggingface](https://huggingface.co/tencent/HunyuanOCR) |
| **PaddleOCR-VL (Baidu)** | [ArXiv](https://arxiv.org/abs/2510.14528) \| [Weights](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) \| [GitHub](https://github.com/PaddlePaddle/PaddleOCR) |
| **GLM-OCR (Z.ai)** | [Huggingface](https://huggingface.co/zai-org/GLM-OCR) |
| **HanDoc-OrderOCR** | [GitHub](https://github.com/Polo-Marco/HanDoc-OrderOCR) |
| **Qwen2.5-VL-7B-Instruct (Alibaba)** | [Huggingface](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) |
| **Qianfan-OCR (Baidu)** | [Huggingface](https://huggingface.co/baidu/Qianfan-OCR) |
| **Qwen3.5-9B (Alibaba)** | [Huggingface](https://huggingface.co/Qwen/Qwen3.5-9B) |

> **Lưu ý**
>
> Vì sao có thể dùng tạm mô hình OCR chữ Hán cho dữ liệu Hán Nôm:
>
> - Chữ Nôm được người Việt tạo ra dựa trên chữ Hán (mượn nguyên chữ Hán hoặc ghép từ các thành phần chữ Hán), và văn bản Hán Nôm cũng chứa nhiều chữ Hán thuần, nên phần lớn chữ, nét và bộ thủ đã nằm trong tập ký tự mà mô hình OCR chữ Hán học được.
> - Hạn chế: chữ Nôm tự tạo mà mô hình chưa từng gặp dễ bị nhận sai hoặc bỏ sót, và các mô hình này chưa được tinh chỉnh cho bản in hoặc bản viết tay cổ, nên kết quả cần được đối chiếu với từ điển và sửa tay.

### 2. Mô hình OCR chữ Quốc ngữ

| Tên mô hình / Công cụ | Liên kết tài nguyên |
| --- | --- |
| **MonkeyOCRv2** | [GitHub](https://github.com/Yuliang-Liu/MonkeyOCRv2) \| [Paper](https://huggingface.co/papers/2607.11562) |
| **HunyuanOCR (Tencent)** | [Huggingface](https://huggingface.co/tencent/HunyuanOCR) |
| **PaddleOCR-VL (Baidu)** | [ArXiv](https://arxiv.org/abs/2510.14528) \| [Weights](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) \| [GitHub](https://github.com/PaddlePaddle/PaddleOCR) |
| **dots.ocr (rednote-hilab)** | [Huggingface](https://huggingface.co/rednote-hilab/dots.ocr) |
| **Qwen2.5-VL-7B-Instruct (Alibaba)** | [Huggingface](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) |
| **Vintern-1B-v2 (5CD-AI)** | [Huggingface](https://huggingface.co/5CD-AI/Vintern-1B-v2) |
| **Qianfan-OCR (Baidu)** | [Huggingface](https://huggingface.co/baidu/Qianfan-OCR) |
| **Qwen3.5-9B (Alibaba)** | [Huggingface](https://huggingface.co/Qwen/Qwen3.5-9B) |

### 3. Mô hình dóng hàng Nôm – Quốc ngữ

| Công trình / Phương pháp & Tác giả | Liên kết tài nguyên |
| --- | --- |
| **Ancient-Modern Chinese Translation** Liu, Lv, Yang, Qu (ACM TALLIP) | [ArXiv](https://arxiv.org/abs/1808.03738) \| [DOI](https://doi.org/10.1145/3325887) |
| **Transliterating Nôm Scripts into Vietnamese** Dinh, Nguyen, Nguyen (IJACSA, vol. 12, no. 2, 2021) | [Paper PDF](https://thesai.org/Downloads/Volume12No2/Paper_5-Transliterating_Nôm_Scripts_into_Vietnamese.pdf) |
| **SentAlign** Steingrímsson, Loftsson, Way (EMNLP 2023 Demo) | [ACL Anthology](https://aclanthology.org/2023.emnlp-demo.22) \| [GitHub](https://github.com/steinst/SentAlign) |
| **Bertalign** Liu & Zhu (Digital Scholarship in the Humanities, 2022) | [DOI](https://doi.org/10.1093/llc/fqac089) \| [PyPI](https://pypi.org/project/bertalign/) |
| **Vecalign** Thompson & Koehn (EMNLP-IJCNLP 2019) | [ACL Anthology](https://aclanthology.org/D19-1136/) |

### 4. Mô hình kiểm tra chính tả (Spellcheck)

| Phân loại | Tên mô hình / Công cụ | Mô tả chi tiết | Liên kết tài nguyên |
| --- | --- | --- | --- |
| **Hán / Hán Nôm** | **SCOPE** | Kiểm tra chính tả tiếng Trung | [ArXiv](https://arxiv.org/abs/2210.10996) \| [GitHub](https://github.com/jiahaozhenbang/SCOPE) \| [Checkpoint](https://huggingface.co/iioSnail/ChineseBERT-for-csc) |
| **Hán / Hán Nôm** | **ReaLiSe** | Mô hình sửa lỗi chính tả tiếng Trung | [ACL](https://aclanthology.org/2021.findings-acl.64) \| [GitHub](https://github.com/DaDaMrX/ReaLiSe) \| [Checkpoint](https://huggingface.co/iioSnail/ReaLiSe-for-csc) |
| **Quốc ngữ** | **BARTpho** | Mô hình ngôn ngữ tiếng Việt (syllable) | [Huggingface](https://huggingface.co/vinai/bartpho-syllable) |
| **Quốc ngữ** | **ViSoBERT** | Mô hình BERT tiếng Việt | [Huggingface](https://huggingface.co/uitnlp/visobert) |

### 5. Nguồn từ điển

| Tên từ điển | Ngôn ngữ / Phạm vi hỗ trợ | Liên kết tài nguyên |
| --- | --- | --- |
| **Nôm Na Việt** | Chữ Hán Nôm – Quốc ngữ (âm đọc) | [Website](https://nomnaviet.com/en/search) |
| **Unihan (Unicode Consortium)** | Chữ Hán/CJK, âm Hán-Việt, Quan thoại, Quảng Đông, Nhật, Hàn, nghĩa Anh | [Mô tả](https://www.unicode.org/charts/unihan.html) \| [Dữ liệu Unihan.zip](https://www.unicode.org/Public/UNIDATA/Unihan.zip) |
| **Bảng chữ Hán Nôm chuẩn (Hannom-RCV)** | Chữ Hán Nôm – Quốc ngữ (âm đọc) | [Website](https://hannom-rcv.org/) |
| **Bảng Tra Chữ Nôm (Quỹ BT Di sản Nôm)** | Chữ Nôm – Quốc ngữ | [Website](https://nomfoundation.org/nom-tools/BTCN?uiLang=en) |
| **Digitizing Vietnam** | Bộ từ điển Hán Nôm | [Website](https://digitizingvietnam.com/en/tools/han-nom-dictionaries/) |
| **HVDIC** | Hán-Việt và Nôm – tiếng Việt | [Website](https://hvdic.thivien.net/) |
| **Đại Nam Quấc âm tự vị (1895)** | Quốc ngữ – Quốc ngữ, có kèm Hán Nôm (Wikisource) | [Wikisource](https://vi.wikisource.org/w/index.php?search=Đại+Nam+quấc+âm+tự+vị) |
| **Wiktionary tiếng Việt** | Việt – Việt, có bảng Hán-Việt và Nôm | [Website](https://vi.wiktionary.org/) |
| **Wiktionary tiếng Anh (Kaikki.org)** | Đa ngôn ngữ (Trung, Việt, Nhật, Hàn…), giải nghĩa tiếng Anh | [Website](https://kaikki.org/dictionary/) |
| **CC-CEDICT** | Trung – Anh | [Website](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) |
| **CC-Canto** | Âm Quảng Đông (Jyutping) cho CC-CEDICT | [Download](https://cantonese.org/download.html) |
| **Make Me a Hanzi** | Chữ Hán, nghĩa tiếng Anh, cấu tạo, từ nguyên, bộ thủ | [GitHub](https://github.com/skishore/makemeahanzi) |
| **cjkvi-ids & cjkvi-variants** | Chữ Hán/CJK, cấu trúc ký tự và quan hệ dị thể | [cjkvi-ids](https://github.com/cjkvi/cjkvi-ids) \| [cjkvi-variants](https://github.com/cjkvi/cjkvi-variants) |
| **MOE Revised Mandarin Dictionary** | Quan thoại (Đài Loan) – Quan thoại | [GitHub](https://github.com/g0v/moedict-data) |
| **ERDRG (JMdict, KANJIDIC2)** | Nhật – Anh, kanji | [Website](https://www.edrdg.org/) |
| **Kengdic** | Hàn – Anh | [GitHub](https://github.com/garfieldnate/kengdic) |
| **vdict** | Anh – Việt, Việt – Anh, Việt – Việt | [Website](https://vdict.com/) |
| **vtudien** | Anh – Việt, Trung – Việt, Việt – Trung, Việt – Việt | [Website](https://vtudien.com/) |

### 6. Font chữ

| Tên font | Mô tả & Hướng dẫn | Liên kết tài nguyên |
| --- | --- | --- |
| **HAN NOM A, HAN NOM B (VietUnicode)** | Font chữ Hán Nôm tiêu chuẩn | [Mô tả](https://vietunicode.sourceforge.net/fonts/fonts_hannom.html) \| [Tải về](https://sourceforge.net/projects/vietunicode/files/hannom/hannom%20v2005/) |
| **Nom Na Tong Light (Quỹ Bảo tồn Nôm)** | Font chữ Nôm chuyên dụng | [Hướng dẫn](https://hannom-rcv.org/font.html) \| [Nom Foundation](https://nomfoundation.org/) |
| **Han-Nom Gothic, Han-Nom Ming, Han-Nom Kai** | Hannom-RCV | [Website](https://hannom-rcv.org/font.html) |
| **HanaMin A, HanaMin B** | Font chữ CJK/Hán Nôm rộng | [Hướng dẫn](https://hannom-rcv.org/font.html) |
| **MingLiU, MingLiU-ExtB** | Font hệ thống hỗ trợ ExtB | [Website](https://hannom-rcv.org/font.html) |

### 7. Công cụ hỗ trợ

| Tên công cụ | Mô tả chức năng chính | Liên kết tài nguyên |
| --- | --- | --- |
| **PPOCRLabel** | Hỗ trợ đánh dấu bounding box cho OCR | [GitHub](https://github.com/PFCCLab/PPOCRLabel) |
| **pdfarranger** | Hỗ trợ sắp xếp/chỉnh sửa file PDF (Linux) | [GitHub](https://github.com/pdfarranger/pdfarranger) |
| **pdfslice-py** | Hỗ trợ chia trang file PDF | [GitHub](https://github.com/obj-see/pdfslice-py) |

## Chú thích về phân loại sách

| Code (Mã) | Layout & Script Description (Mô tả bố cục & chữ viết) |
| --- | --- |
| T1 | Quốc ngữ nằm ở phần đầu, Nôm ở phần cuối |
| T1w | T1 nhưng viết tay Nôm (w là writing) |
| T2 | Xen kẽ (Ví dụ: Phải: Nôm, Trái: Quốc Ngữ) |
| T2w | T2 nhưng viết tay Nôm (w là writing) |
| T2b | Quốc ngữ và Nôm chung trang |
| Tref | Tham khảo |
| Tx | Loại khác |
| Tqn | Chỉ có Quốc ngữ |
| Tnom | Chỉ có Nôm |
| Tnomw | Chỉ có viết tay Nôm (w là writing) |
| Chưa phân loại | Chưa phân loại |
