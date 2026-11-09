"""Từ điển chữ Hán Nôm -> âm Quốc ngữ, dùng cho hậu xử lý OCR (scripts/postocr.py) và tô màu.

Hai nguồn:
  1. Unihan (Unicode): trường kVietnamese (âm đọc) và các trường dị thể (Unihan_Variants.txt).
     Dựng một lần thành data/cache/dict/unihan_vi.json và variants.json (được git theo dõi).
  2. API phiên âm Kim Hán Nôm (sinonom-transliteration): âm theo ngữ cảnh của cả dòng và âm của từng chữ.
     Cache trong data/<Tên sách>/cache/translit.json để không gọi lại.

Dựng từ điển Unihan (cần mạng một lần):
    python scripts/nom_dict.py build
"""
from __future__ import annotations

import io
import json
import os
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.library import DEFAULT_DATA_DIR, write_json_atomic  # noqa: E402

UNIHAN_URL = "https://www.unicode.org/Public/UNIDATA/Unihan.zip"
VARIANT_FIELDS = ("kTraditionalVariant", "kSimplifiedVariant", "kSemanticVariant",
                  "kZVariant", "kSpecializedSemanticVariant")


def dict_dir(data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return data_dir / "cache" / "dict"


def _cp(token: str) -> str:
    """'U+6642<kFenn' -> '時'."""
    return chr(int(token.split("<", 1)[0][2:], 16))


# ---------------------------------------------------------------- dựng từ Unihan
def build_unihan(data_dir: Path = DEFAULT_DATA_DIR, zip_path: Path | None = None) -> tuple[Path, Path]:
    """Đọc Unihan.zip (tải về nếu chưa có) và ghi unihan_vi.json, variants.json.

    variants.json: {"variants": {chữ: [dị thể...]}, "traditional": {chữ giản thể: [chữ phồn thể...]}}.
    File zip chỉ được đọc như dữ liệu, không giải nén ra đĩa.
    """
    out = dict_dir(data_dir)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    zip_path = zip_path or raw / "Unihan.zip"
    if not zip_path.exists():
        print(f"Tải {UNIHAN_URL} ...")
        urllib.request.urlretrieve(UNIHAN_URL, zip_path)

    readings: dict[str, list[str]] = {}
    variants: dict[str, set[str]] = {}
    traditional: dict[str, set[str]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("Unihan_Readings.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3 and parts[1] == "kVietnamese":
                    readings[_cp(parts[0])] = [unicodedata.normalize("NFC", r) for r in parts[2].split()]
        with zf.open("Unihan_Variants.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3 or parts[1] not in VARIANT_FIELDS:
                    continue
                ch = _cp(parts[0])
                others = {_cp(t) for t in parts[2].split()} - {ch}
                if not others:
                    continue
                variants.setdefault(ch, set()).update(others)
                for o in others:   # dị thể là quan hệ hai chiều
                    variants.setdefault(o, set()).add(ch)
                if parts[1] == "kTraditionalVariant":
                    traditional.setdefault(ch, set()).update(others)

    vi_path, var_path = out / "unihan_vi.json", out / "variants.json"
    write_json_atomic(vi_path, readings)
    write_json_atomic(var_path, {"variants": {k: sorted(v) for k, v in sorted(variants.items())},
                                 "traditional": {k: sorted(v) for k, v in sorted(traditional.items())}})
    print(f"{len(readings)} chữ có âm, {len(variants)} chữ có dị thể -> {out}")
    return vi_path, var_path


# ---------------------------------------------------------------- cache API phiên âm
class TranslitCache:
    """Cache kết quả API phiên âm Kim Hán Nôm: {"lines": {dòng: [âm...]}, "chars": {chữ: âm}}.

    use_api=False: chỉ đọc cache, không gọi mạng (dòng/chữ chưa có trong cache trả về None).
    """

    def __init__(self, path: Path, use_api: bool = True):
        self.path = path
        self.use_api = use_api
        self.data = {"lines": {}, "chars": {}}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.data["lines"].update(loaded.get("lines", {}))
            self.data["chars"].update(loaded.get("chars", {}))
        self._service = None
        self.calls = 0

    def _api(self):
        if self._service is None:
            api_dir = str(ROOT / "scripts" / "api")
            if api_dir not in sys.path:
                sys.path.insert(0, api_dir)
            import sinonom_service   # noqa: WPS433 (nạp trễ: chỉ cần khi gọi API)
            self._service = sinonom_service
        return self._service

    def _call(self, text: str) -> list[str] | None:
        if not self.use_api:
            return None
        try:
            data = self._api().transliterate(text)
        except Exception as e:   # lỗi mạng/token: bỏ qua, pipeline vẫn chạy bằng Unihan
            print(f"  ! API phiên âm lỗi với '{text}': {e}")
            self.use_api = False
            return None
        self.calls += 1
        result = (data or {}).get("result_text_transcription") or []
        syl = " ".join(result).split()
        return [unicodedata.normalize("NFC", s).lower() for s in syl]

    def line(self, text: str) -> list[str] | None:
        if text not in self.data["lines"]:
            got = self._call(text)
            if got is None:
                return None
            self.data["lines"][text] = got
            self.save()
        return self.data["lines"][text]

    def char(self, ch: str) -> str | None:
        if ch not in self.data["chars"]:
            got = self._call(ch)
            if got is None:
                return None
            self.data["chars"][ch] = got[0] if len(got) == 1 else ""
            self.save()
        return self.data["chars"][ch] or None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, self.data)


# ---------------------------------------------------------------- từ điển hợp nhất
class NomDict:
    """readings(c): tập âm của chữ c (Unihan + API). variant_readings(c): âm của các dị thể của c."""

    def __init__(self, readings: dict[str, list[str]], variants: dict[str, list[str]] | None = None,
                 traditional: dict[str, list[str]] | None = None):
        self._dict: dict[str, set[str]] = {c: {r.lower() for r in rs} for c, rs in readings.items()}
        self._readings: dict[str, set[str]] = {c: set(rs) for c, rs in self._dict.items()}   # từ điển + API
        self.variants = variants or {}
        self.traditional = traditional or {}

    @classmethod
    def load(cls, data_dir: Path = DEFAULT_DATA_DIR) -> "NomDict":
        d = dict_dir(data_dir)
        vi_path, var_path = d / "unihan_vi.json", d / "variants.json"
        if not vi_path.exists() or not var_path.exists():
            raise SystemExit(f"[LỖI] Chưa có từ điển Unihan trong {d}. Chạy: python scripts/nom_dict.py build")
        var = json.loads(var_path.read_text(encoding="utf-8"))
        readings = json.loads(vi_path.read_text(encoding="utf-8"))
        extra = d / "nom_bo_sung.json"   # âm Nôm thông dụng soạn tay (Unihan chủ yếu có âm Hán-Việt)
        if extra.exists():
            for ch, rs in json.loads(extra.read_text(encoding="utf-8")).items():
                if not ch.startswith("_"):
                    readings[ch] = list(dict.fromkeys([*readings.get(ch, []), *rs]))
        return cls(readings, var["variants"], var["traditional"])

    def add(self, ch: str, reading: str | None) -> None:
        """Thêm âm lấy từ API (dùng để tô màu; khi sửa chữ hợp lệ chỉ tin âm từ điển, xem dict_readings)."""
        if reading:
            self._readings.setdefault(ch, set()).add(unicodedata.normalize("NFC", reading).lower())

    def knows(self, ch: str) -> bool:
        return bool(self._readings.get(ch))

    def readings(self, ch: str) -> set[str]:
        """Âm từ điển Unihan + âm API."""
        return self._readings.get(ch, set())

    def dict_readings(self, ch: str) -> set[str]:
        """Chỉ âm từ điển Unihan (của chữ và các dị thể)."""
        out = set(self._dict.get(ch, set()))
        for v in self.variants.get(ch, []):
            out |= self._dict.get(v, set())
        return out

    def variant_readings(self, ch: str) -> dict[str, set[str]]:
        """{dị thể: tập âm} của các dị thể có âm."""
        return {v: self._readings[v] for v in self.variants.get(ch, []) if self._readings.get(v)}

    def traditional_of(self, ch: str) -> list[str]:
        return [t for t in self.traditional.get(ch, []) if t != ch]

    def syllables(self) -> set[str]:
        return {r for rs in self._readings.values() for r in rs}


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        zip_arg = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        build_unihan(zip_path=zip_arg)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
