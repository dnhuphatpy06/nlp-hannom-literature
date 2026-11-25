"""Từ điển chữ Hán Nôm -> âm Quốc ngữ, dùng cho hậu xử lý OCR (scripts/postocr.py) và tô màu.

Các nguồn:
  1. Unihan (Unicode): trường kVietnamese (âm đọc) và các trường dị thể (Unihan_Variants.txt).
     Dựng thành data/cache/dict/unihan_vi.json và variants.json (được git theo dõi).
  2. Từ điển Hán-Nôm mở rộng (`data/cache/dict/nom_na_viet.json`, tùy chọn nếu đã dựng):
     - ph0ngp/hanviet-pinyin-wordlist (hanviet.csv)
     - Nôm Na Việt Rime (`nomnaviet/rime-nom-viet`, nom_viet.dict.yaml)
     - Giúp đọc Nôm và Hán Việt (`gdnhv.xml`) & Tự Điển Chữ Nôm Dẫn Giải (`tdcndg.xml`) từ `panda1835/digitizing-vietnam-website`
  3. Âm Nôm bổ sung soạn tay (`data/cache/dict/nom_bo_sung.json`).
  4. API phiên âm Kim Hán Nôm (sinonom-transliteration): âm theo ngữ cảnh của cả dòng và âm của từng chữ.
     Cache trong data/<Tên sách>/cache/translit.json để không gọi lại.

Dựng từ điển (cần mạng lần đầu):
    python scripts/nom_dict.py build
    python scripts/nom_dict.py build-full
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.library import DEFAULT_DATA_DIR, write_json_atomic  # noqa: E402

UNIHAN_URL = "https://www.unicode.org/Public/UNIDATA/Unihan.zip"
EXTENDED_DICT_SOURCES = {
    "hanviet.csv": "https://raw.githubusercontent.com/ph0ngp/hanviet-pinyin-wordlist/main/hanviet.csv",
    "nom_viet.dict.yaml": "https://raw.githubusercontent.com/nomnaviet/rime-nom-viet/main/nom_viet.dict.yaml",
    "gdnhv.xml": "https://raw.githubusercontent.com/panda1835/digitizing-vietnam-website/main/data/dictionaries/giup-doc-nom-va-han-viet/gdnhv.xml",
    "tdcndg.xml": "https://raw.githubusercontent.com/panda1835/digitizing-vietnam-website/main/data/dictionaries/tu-dien-chu-nom-dan-giai/tdcndg.xml",
}
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
        for extra_name in ("nom_na_viet.json", "nom_bo_sung.json"):
            extra = d / extra_name
            if not extra.exists():
                continue
            for ch, rs in json.loads(extra.read_text(encoding="utf-8")).items():
                if not ch.startswith("_"):
                    readings[ch] = list(dict.fromkeys([*readings.get(ch, []), *rs]))
        return cls(readings, var["variants"], var["traditional"])

    def add_translit(self, cache: TranslitCache) -> int:
        """Nạp thêm các âm ký tự từ TranslitCache (không gọi mạng). Trả về số ký tự đã nạp."""
        count = 0
        for ch, reading in cache.data.get("chars", {}).items():
            if reading:
                self.add(ch, reading)
                count += 1
        return count

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


_TELEX_TONES = {"s": "\u0301", "f": "\u0300", "r": "\u0309", "x": "\u0303", "j": "\u0323"}


def _telex_to_syllable(code: str, is_valid_fn) -> str | None:
    """Chuyển mã Telex trong nom_viet.dict.yaml sang âm tiết Quốc ngữ NFC hợp lệ."""
    s = code.strip().lower()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    if not s or not s.isalpha():
        return None
    if s.startswith("dd"):
        onset, rest = "đ", s[2:]
    else:
        m = re.match(r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnpqrstvx])?(.*)$", s)
        onset, rest = (m.group(1) or ""), m.group(2)
        if onset == "gi" and not any(c in rest for c in "aeiouyw"):
            onset, rest = "g", "i" + rest
    tone = ""
    cleaned: list[str] = []
    seen_vowel = False
    for ch in rest:
        if ch in "aeiouyw":
            seen_vowel = True
            cleaned.append(ch)
        elif seen_vowel and ch in _TELEX_TONES and not tone:
            tone = _TELEX_TONES[ch]
        else:
            cleaned.append(ch)
    r = "".join(cleaned)
    for src, dst in (("uwow", "ươ"), ("uow", "ươ"), ("oow", "ơ"), ("aa", "â"), ("aw", "ă"), ("ee", "ê"), ("oo", "ô"), ("ow", "ơ"), ("uw", "ư")):
        r = r.replace(src, dst)
    if "w" in r:
        r = r.replace("w", "ư")
    if tone:
        m = re.match(r"^([aeiouyâăêôơư]+)(.*)$", r)
        if not m:
            return None
        vs, coda = m.group(1), m.group(2)
        if len(vs) == 1:
            pos = 0
        else:
            marked = [i for i, c in enumerate(vs) if c in "âăêôơư"]
            if marked:
                pos = marked[-1]
            elif coda:
                pos = len(vs) - 1
            elif len(vs) == 2:
                pos = 0
            else:
                pos = 1
        r = vs[:pos + 1] + tone + vs[pos + 1:] + coda
    out = unicodedata.normalize("NFC", onset + r)
    return out if is_valid_fn(out) else None


def build_extended_dict(data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """Tổng hợp từ điển Hán-Nôm mở rộng từ 4 nguồn mở vào data/cache/dict/nom_na_viet.json."""
    from scripts import postocr

    out = dict_dir(data_dir)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, url in EXTENDED_DICT_SOURCES.items():
        dst = raw / name
        if not dst.exists():
            print(f"Tải {name} từ {url} ...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dst.write_bytes(r.read())

    readings: dict[str, set[str]] = {}

    def add(ch: str, syl: str | None) -> None:
        ch = unicodedata.normalize("NFC", ch).strip()
        if len(ch) != 1 or not syl:
            return
        syl = unicodedata.normalize("NFC", syl).strip().lower()
        if syl and " " not in syl and postocr.is_valid_syllable(syl):
            readings.setdefault(ch, set()).add(syl)

    with (raw / "hanviet.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ch = row.get("char", "")
            hv = row.get("hanviet", "")
            try:
                vals = ast.literal_eval(hv) if hv.startswith("[") else [hv]
            except Exception:
                vals = [hv]
            for v in vals:
                if isinstance(v, str):
                    add(ch, v)

    in_body = False
    for line in (raw / "nom_viet.dict.yaml").read_text(encoding="utf-8").splitlines():
        if line == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2 or len(unicodedata.normalize("NFC", parts[0])) != 1:
            continue
        for token in parts[1].replace("/", " ").split():
            add(parts[0], _telex_to_syllable(token, postocr.is_valid_syllable))

    for e in ET.parse(raw / "gdnhv.xml").getroot().iter("entry"):
        hd = (e.findtext("hdwd") or "").strip()
        for o in e.findall(".//orth"):
            if o.text:
                add(o.text, hd)

    for e in ET.parse(raw / "tdcndg.xml").getroot().iter("entry"):
        hn = (e.findtext("hn") or "").strip()
        qn = (e.findtext("qn") or "").strip()
        add(hn, qn)

    out_path = out / "nom_na_viet.json"
    serializable = {ch: sorted(rs) for ch, rs in sorted(readings.items())}
    write_json_atomic(out_path, serializable)
    print(f"Đã dựng từ điển mở rộng: {len(serializable)} chữ Hán-Nôm -> {out_path}")
    return out_path


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        zip_arg = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        build_unihan(zip_path=zip_arg)
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "build-full":
        build_extended_dict()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
