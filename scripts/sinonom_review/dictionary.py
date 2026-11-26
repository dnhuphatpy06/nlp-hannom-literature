"""Từ điển Nôm ↔ Việt và từ điển chữ Nôm giống hình.

- QuocNgu_SinoNom_TongHop3.csv  (cột QuocNgu, SinoNom): một dòng = một cặp (âm, chữ).
- SinoNom_Similar_Dic_v2.csv    (cột Input Character, Top 20 Similar Characters): 20 chữ giống hình nhất, xếp hạng.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

import pandas as pd

from .text import nfc, orthographic_variants, ortho_distance, split_tone

TRANSLATION_FILE = "QuocNgu_SinoNom_TongHop3.csv"
SIMILAR_FILE = "SinoNom_Similar_Dic_v2.csv"


class NomVietDict:
    def __init__(self, viet2nom: dict[str, set[str]], nom2viet: dict[str, set[str]],
                 similar: dict[str, list[str]], use_variants: bool = True):
        self.viet2nom = viet2nom
        self.nom2viet = nom2viet
        self.similar = similar          # chữ -> danh sách chữ giống hình, xếp hạng (không gồm chính nó)
        self.use_variants = use_variants
        # chỉ mục theo (vần không dấu thanh, thanh): không phụ thuộc vị trí đặt dấu (hoà/hòa)
        self.canon2nom: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        for q, cs in viet2nom.items():
            self.canon2nom[split_tone(q)].update(cs)
        self._cache: dict[str, frozenset[str]] = {}

    # ------------------------------------------------------------------ nạp
    @classmethod
    def load(cls, dict_dir: str | Path | None = None, translation_csv: str | Path | None = None,
             similar_csv: str | Path | None = None, extra_csvs=(), use_variants: bool = True,
             encoding: str = "utf-8-sig") -> "NomVietDict":
        """dict_dir chứa 2 file mặc định; có thể chỉ định đường dẫn riêng. extra_csvs: các file bổ sung (QuocNgu, SinoNom)."""
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
        return cls(dict(viet2nom), dict(nom2viet), similar, use_variants)

    # ------------------------------------------------------------------ tra cứu
    def viet_to_nom(self, syl: str) -> frozenset[str]:
        """Việt → Nôm: mọi chữ Nôm đọc được là âm `syl` (tính cả vị trí dấu và biến thể chính tả cổ)."""
        s = nfc(syl.lower())
        hit = self._cache.get(s)
        if hit is None:
            out = set(self.viet2nom.get(s, ()))
            for v in (orthographic_variants(s) if self.use_variants else {split_tone(s)}):
                out |= self.canon2nom.get(v, set())
            hit = self._cache[s] = frozenset(out)
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

    def stats(self) -> dict:
        return {"syllables": len(self.viet2nom), "nom_chars": len(self.nom2viet),
                "pairs": sum(len(v) for v in self.viet2nom.values()), "similar_entries": len(self.similar)}
