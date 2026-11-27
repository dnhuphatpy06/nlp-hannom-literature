"""Tự dò trọng số chấm điểm ứng viên từ các nhãn đã duyệt và báo cáo độ chính xác (held-out theo sách).

    python -m scripts.tune_review                       # dùng mọi sách trong data/, ghi vào resources/tuning/
    python -m scripts.tune_review --trials 800 --objective mrr --target-precision 0.97

Kết quả: resources/tuning/tuned_weights.json (trọng số được chọn tự động giữa mặc định và đã dò, ngưỡng tự chọn) và tuning_report.md.
Giao diện duyệt tự dùng tuned_weights.json khi `review.use_tuned_weights: true` trong config_review.yaml, không cần chạy lại LM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out-dir", default=str(ROOT / "resources" / "tuning"))
    ap.add_argument("--objective", choices=("nll", "mrr", "top1"), default="nll")
    ap.add_argument("--trials", type=int, default=400, help="số bộ trọng số ngẫu nhiên thử mỗi lần dò")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--target-precision", type=float, default=0.95, help="precision tối thiểu khi chọn ngưỡng tự chọn")
    ap.add_argument("--min-fold", type=int, default=50, help="sách có ít hơn từng này nhãn không làm fold kiểm tra")
    ap.add_argument("--book", action="append", help="chỉ dùng sách này (tên thư mục trong data/), lặp lại được")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from .sinonom_review import tuning
    try:
        tuning.run(a.data_dir, a.out_dir, a.objective, a.trials, a.seed, a.target_precision, a.min_fold, a.book)
    except ValueError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
