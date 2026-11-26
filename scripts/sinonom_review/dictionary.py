"""Từ điển Nôm ↔ Việt và từ điển chữ Nôm giống hình.

- QuocNgu_SinoNom_TongHop3.csv  (cột QuocNgu, SinoNom): một dòng = một cặp (âm, chữ).
- SinoNom_Similar_Dic_v2.csv    (cột Input Character, Top 20 Similar Characters): 20 chữ giống hình nhất, xếp hạng.
- SinoNom_Variants_Unihan.csv   (cột Input Character, Variant, Source): chữ cùng một chữ khác dạng viết (giản thể, phồn thể, dị thể) theo Unihan,
  sinh bằng scripts/build_variants.py. SinoNom_Variants_Manual.csv cùng cột, ghi tay các quan hệ Unihan không có.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

import pandas as pd

from .text import nfc, orthographic_variants, ortho_distance, regional_forms, split_tone

TRANSLATION_FILE = "QuocNgu_SinoNom_TongHop3.csv"
SIMILAR_FILE = "SinoNom_Similar_Dic_v2.csv"
VARIANT_FILES = ("SinoNom_Variants_Unihan.csv", "SinoNom_Variants_Manual.csv")


class NomVietDict:
    def __init__(self, viet2nom: dict[str, set[str]], nom2viet: dict[str, set[str]],
                 similar: dict[str, list[str]], use_variants: bool = True, nom_variants: dict[str, set[str]] | None = None):
        self.viet2nom = viet2nom
        self.nom2viet = nom2viet
        self.similar = similar          # chữ -> danh sách chữ giống hình, xếp hạng (không gồm chính nó)
        self.use_variants = use_variants
        self.nom_variants = nom_variants or {}   # chữ -> các chữ cùng một chữ khác dạng viết (hai chiều, một bước)
        # chỉ mục theo (vần không dấu thanh, thanh): không phụ thuộc vị trí đặt dấu (hoà/hòa)
        self.canon2nom: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        for q, cs in viet2nom.items():
            self.canon2nom[split_tone(q)].update(cs)
        self._cache: dict[str, frozenset[str]] = {}

    # ------------------------------------------------------------------ nạp
    @classmethod
    def load(cls, dict_dir: str | Path | None = None, translation_csv: str | Path | None = None,
             similar_csv: str | Path | None = None, extra_csvs=(), use_variants: bool = True,
             encoding: str = "utf-8-sig", nom_variants: bool = True) -> "NomVietDict":
        """dict_dir chứa 2 file mặc định; có thể chỉ định đường dẫn riêng. extra_csvs: các file bổ sung (QuocNgu, SinoNom).
        nom_variants: nạp quan hệ dị thể chữ Nôm (VARIANT_FILES trong dict_dir) để sinh ứng viên NOM; False thì không có ứng viên dị thể."""
        dict_dir = Path(dict_dir) if dict_dir else None
        translation_csv = Path(translation_csv) if translation_csv else dict_dir / TRANSLATION_FILE
        similar_csv = Path(similar_csv) if similar_csv else (dict_dir / SIMILAR_FILE if dict_dir else None)
        frames = [pd.read_csv(translation_csv, encoding=encoding, dtype=str)]
        for p in extra_csvs or ():
            if Path(p).exists():
                frames.append(pd.read_csv(p, encoding=encoding, dtype=str))
        df = pd.concat(frames, ignore_index=True).dropna(subset=["QuocNgu", "SinoNom"])
        viet2nom: dict[str, set[str]] = collections.defaultdict(set)
        nom2viet: dict[str, set[str]] = collections.defaultdict(set)
        for q, c in zip(df["QuocNgu"], df["SinoNom"]):
            q, c = nfc(q.strip().lower()), c.strip()
            if q and c:
                viet2nom[q].add(c)
                nom2viet[c].add(q)
        similar: dict[str, list[str]] = {}
        if similar_csv and Path(similar_csv).exists():
            sd = pd.read_csv(similar_csv, encoding=encoding, dtype=str)
            for c, lst in zip(sd["Input Character"], sd["Top 20 Similar Characters"]):
                if not isinstance(c, str) or not isinstance(lst, str):
                    continue
                try:
                    similar[c.strip()] = [x for x in ast.literal_eval(lst) if x != c.strip()]
                except (ValueError, SyntaxError):
                    pass
        variants: dict[str, set[str]] = collections.defaultdict(set)
        for name in (VARIANT_FILES if nom_variants and dict_dir else ()):
            path = dict_dir / name
            if not path.exists():
                continue
            vd = pd.read_csv(path, encoding=encoding, dtype=str)
            for a, b in zip(vd["Input Character"], vd["Variant"]):
                if isinstance(a, str) and isinstance(b, str) and a.strip() and b.strip() and a.strip() != b.strip():
                    variants[a.strip()].add(b.strip())
                    variants[b.strip()].add(a.strip())
        return cls(dict(viet2nom), dict(nom2viet), similar, use_variants, dict(variants))

    # ------------------------------------------------------------------ tra cứu
    def viet_to_nom(self, syl: str, regional: bool = True) -> frozenset[str]:
        """Việt → Nôm: mọi chữ Nôm đọc được là âm `syl` (tính cả vị trí dấu và biến thể chính tả cổ).
        regional=False: không tính dạng viết theo vùng d <-> r (dõi / rõi)."""
        s = nfc(syl.lower())
        cache_key = s if regional else "!" + s
        hit = self._cache.get(cache_key)
        if hit is None:
            out = set(self.viet2nom.get(s, ()))
            for v in (orthographic_variants(s, regional) if self.use_variants else {split_tone(s)}):
                out |= self.canon2nom.get(v, set())
            hit = self._cache[cache_key] = frozenset(out)
        return hit

    def nom_to_viet(self, char: str) -> set[str]:
        """Nôm → Việt: mọi âm đọc của chữ `char` trong từ điển."""
        return set(self.nom2viet.get(char, ()))

    def compatible(self, char: str, syl: str) -> bool:
        """Chữ `char` đọc được là âm `syl` theo từ điển."""
        return char in self.viet_to_nom(syl)

    def similar_chars(self, char: str) -> list[str]:
        return list(self.similar.get(char, ()))

    def visual_rank(self, c: str, c2: str) -> int | None:
        """Thứ hạng (1..20) của c2 trong danh sách giống hình của c (hoặc ngược lại); None nếu không có."""
        best = None
        for a, b in ((c, c2), (c2, c)):
            lst = self.similar.get(a)
            if lst and b in lst:
                r = lst.index(b) + 1
                best = r if best is None else min(best, r)
        return best

    # ------------------------------------------------------------------ ứng viên
    def nom_candidates(self, char: str, syl: str) -> list[tuple[str, int]]:
        """Ứng viên phía Nôm: chữ đọc được là `syl` VÀ giống hình với `char`. Trả về [(chữ, hạng hình)] tăng dần theo hạng."""
        out = [(c2, r) for c2 in self.viet_to_nom(syl) if c2 != char and (r := self.visual_rank(char, c2))]
        return sorted(out, key=lambda x: (x[1], x[0]))

    def variant_candidates(self, char: str, syl: str) -> list[str]:
        """Ứng viên dị thể phía Nôm: chữ cùng một chữ với `char` khác dạng viết (Unihan) VÀ đọc được là `syl`. Dạng viết khác mà không đọc được
        là `syl` thì không là ứng viên, nên chữ không liên quan tới cặp không bao giờ bị ảnh hưởng."""
        readable = self.viet_to_nom(syl)
        return sorted(c2 for c2 in self.nom_variants.get(char, ()) if c2 != char and c2 in readable)

    def viet_candidates(self, char: str, syl: str, max_dist: float = 1.2) -> list[tuple[str, float]]:
        """Ứng viên phía Quốc ngữ: âm đọc của `char`, khác `syl` (không tính biến thể chính tả), cách `syl` <= max_dist."""
        skip = orthographic_variants(syl)
        out = []
        for r in self.nom_to_viet(char):
            if r == syl.lower() or split_tone(r) in skip:
                continue
            d = ortho_distance(syl.lower(), r)
            if d <= max_dist:
                out.append((r, d))
        return sorted(out, key=lambda x: (x[1], x[0]))

    def attested(self, syl: str) -> bool:
        """Âm tiết (không phân biệt vị trí đặt dấu) có trong từ điển: loại các dạng vùng sinh ra mà không phải tiếng Việt (như giõi)."""
        return split_tone(nfc(syl.lower())) in self.canon2nom

    def regional_candidates(self, char: str, syl: str, max_dist: float = 1.2) -> list[tuple[str, float]]:
        """Ứng viên Quốc ngữ là dạng viết theo vùng (d <-> r, gi <-> tr, gi <-> d; text.regional_forms) của một âm đọc của `char`, chưa có trong
        viet_candidates. Dạng vùng đọc được cùng chữ với âm gốc nên là cách sửa hợp lệ, và có thể gần `syl` hơn âm gốc. Khi một dạng vùng được chọn
        thì chính âm đọc gốc (dạng có trong từ điển) cũng được thêm ngay sau nó (cách `syl` xa hơn 0,1 so với dạng vùng), để người duyệt chọn được
        dạng chuẩn. Ví dụ 唯 đọc dõi, văn bản ghi "noi": rõi cách 1,1 và dõi được thêm với 1,2 dù thực tế cách 1,5; 𠻀 đọc dò, văn bản ghi "gió":
        giò cách 0,5 và dò được thêm với 0,6 dù thực tế cách 2,3."""
        skip = orthographic_variants(syl)
        target = syl.lower()
        have = {r for r, _ in self.viet_candidates(char, syl, max_dist)}
        readings = self.nom_to_viet(char)
        out: dict[str, float] = {}
        for r in readings:
            if r == target or split_tone(r) in skip:
                continue
            best = None
            for form in regional_forms(r):
                if form == target or split_tone(form) in skip or form in have or form in readings or not self.attested(form):
                    continue
                d = ortho_distance(target, form)
                if d <= max_dist:
                    out[form] = min(d, out.get(form, 9.0))
                    best = d if best is None else min(best, d)
            if best is not None and r not in have:
                out[r] = min(round(best + 0.1, 2), out.get(r, 9.0))
        return sorted(out.items(), key=lambda x: (x[1], x[0]))

    def relaxed_candidates(self, char: str, syl: str, max_dist: float = 1.2) -> list[tuple[str, float]]:
        """Ứng viên Quốc ngữ chỉ có khi tính khoảng cách nới lỏng (text.ortho_distance relaxed=True: hỏi / ngã nhầm, bỏ sót i y u o cuối vần),
        chưa có trong viet_candidates và regional_candidates. Ví dụ chữ 待 đọc đãi, văn bản ghi "đả": đãi cách 1,5 (thiếu i và khác thanh) nên
        ngoài ngưỡng, nhưng nới lỏng chỉ còn 0,75. Xét cả âm đọc và dạng vùng của nó."""
        skip = orthographic_variants(syl)
        target = syl.lower()
        have = {r for r, _ in self.viet_candidates(char, syl, max_dist)} | {r for r, _ in self.regional_candidates(char, syl, max_dist)}
        out: dict[str, float] = {}
        for r in self.nom_to_viet(char):
            if r == target or split_tone(r) in skip:
                continue
            for form in (r, *regional_forms(r)):
                if form == target or split_tone(form) in skip or form in have or not self.attested(form):
                    continue
                d = ortho_distance(target, form, relaxed=True)
                if d <= max_dist and d < out.get(form, 9.0):
                    out[form] = d
        return sorted(out.items(), key=lambda x: (x[1], x[0]))

    def reading_candidates(self, char: str, syl: str) -> list[tuple[str, float]]:
        """Mọi âm đọc còn lại của `char` trong từ điển (không giới hạn khoảng cách), để người duyệt luôn thấy đủ các âm của chữ. Chỉ gồm âm chưa là
        ứng viên ở các lớp khác: bỏ âm giống `syl` (kể cả biến thể chính tả); khoảng cách tính theo cách thường. Gọi sau khi đã lấy các lớp khác."""
        skip = orthographic_variants(syl)
        target = syl.lower()
        return sorted(((r, ortho_distance(target, r)) for r in self.nom_to_viet(char) if r != target and split_tone(r) not in skip),
                      key=lambda x: (x[1], x[0]))

    def stats(self) -> dict:
        return {"syllables": len(self.viet2nom), "nom_chars": len(self.nom2viet),
                "pairs": sum(len(v) for v in self.viet2nom.values()), "similar_entries": len(self.similar),
                "variant_pairs": sum(len(v) for v in self.nom_variants.values())}
