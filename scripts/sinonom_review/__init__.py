"""sinonom_review — dóng hàng Nôm ↔ Quốc ngữ, đề xuất sửa lỗi top-k và tô màu cặp lệch.

Chép từ dự án Alignment (giữ nguyên mã); chỉ bỏ giao diện Gradio (app.py), config.py và workflow.py: bước 3 của web (scripts/review_pipeline.py)
thay các phần đó. Thiết lập (mô hình, top-k, từ điển bổ sung) đọc từ config_review.yaml ở gốc dự án; từ điển ở resources/dictionaries/.

Các mô-đun:
  text        tách chữ Nôm / âm tiết Quốc ngữ, dấu thanh, khoảng cách chính tả
  dictionary  từ điển Nôm ↔ Việt (viet_to_nom, nom_to_viet), chữ giống hình, sinh ứng viên
  alignment   dóng hàng Levenshtein (khớp từ điển = 0, lệch/thừa = 1)
  lm          mô hình ngôn ngữ chấm điểm (SinoNomBERT, Qwen2.5-0.5B), tự chọn CPU/GPU
  scoring     chấm điểm KEEP / NOM / QN, softmax -> confidence, xếp hạng top-k
  data        Sentence, ImageRef và các hàm đọc xlsx của Alignment (web dựng Sentence từ các hàng dóng hàng nên không đọc xlsx)
  session     phiên gán nhãn (lựa chọn, tự lưu, khôi phục)
  export      xuất các file kết quả
"""
__version__ = "1.0.0"

from .alignment import AlignedPair, levenshtein_align  # noqa: E402,F401
from .dictionary import NomVietDict  # noqa: E402,F401
from .scoring import DEFAULT_WEIGHTS, CandidateScorer  # noqa: E402,F401
from .session import ReviewSession  # noqa: E402,F401
