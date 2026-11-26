"""Tạo resources/dictionaries/SinoNom_Variants_Unihan.csv từ Unihan_Variants.txt của Unicode.

Chỉ lấy các quan hệ cùng một chữ khác dạng viết: kSimplifiedVariant, kTraditionalVariant, kZVariant (một bước, hai chiều được dựng lúc nạp).
Bỏ kSemanticVariant và kSpecializedSemanticVariant vì đó là các chữ khác nhau chỉ gần nghĩa.

Chạy: python scripts/build_variants.py <đường dẫn Unihan_Variants.txt>
Lấy Unihan_Variants.txt trong https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip (giấy phép Unicode, https://www.unicode.org/terms_of_use.html).
Quan hệ Unihan không có thì ghi tay vào SinoNom_Variants_Manual.csv, không sửa file do script này sinh ra.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

FIELDS = ("kSimplifiedVariant", "kTraditionalVariant", "kZVariant")
OUT = Path(__file__).resolve().parent.parent / "resources" / "dictionaries" / "SinoNom_Variants_Unihan.csv"


def _char(code: str) -> str:
    return chr(int(code.split("<")[0][2:], 16))     # "U+7523" hoặc "U+7523<kMeyerWempe"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    rows = set()
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        code, field, value = line.split("\t")
        if field in FIELDS:
            for v in value.split():
                a, b = _char(code), _char(v)
                if a != b:
                    rows.add((a, b, field))
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Input Character", "Variant", "Source"])
        w.writerows(sorted(rows))
    print(f"Đã ghi {len(rows)} quan hệ vào {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
