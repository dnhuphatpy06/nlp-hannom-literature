"""Xử lý văn bản: tách chữ Nôm, tách âm tiết Quốc ngữ, dấu thanh và khoảng cách chính tả."""
from __future__ import annotations

import re
import unicodedata

DAMAGED_TOKENS = ("[MASK]",)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def tokenize_nom(text: str, damaged=DAMAGED_TOKENS) -> list[str]:
    """Tách chuỗi Nôm thành từng chữ; giữ nguyên dấu hiệu chữ hỏng (vd [MASK]) như một chữ; bỏ khoảng trắng."""
    pattern = "|".join(re.escape(t) for t in sorted(damaged, key=len, reverse=True))
    return re.findall((pattern + "|" if pattern else "") + r"\S", str(text).strip())


def tokenize_qn(text: str) -> list[str]:
    """Tách chuỗi Quốc ngữ thành âm tiết theo khoảng trắng (chuẩn hoá NFC)."""
    return nfc(str(text)).split()


# --------------------------------------------------------------------------------------
# Dấu thanh và chính tả Quốc ngữ
# --------------------------------------------------------------------------------------
TONE_MARKS = {"̀": "huyen", "́": "sac", "̉": "hoi", "̃": "nga", "̣": "nang"}


def split_tone(syl: str) -> tuple[str, str]:
    """'mất' -> ('mât', 'sac'). Phần còn lại giữ dấu mũ/móc (â, ơ, ư, đ ...)."""
    d = unicodedata.normalize("NFD", syl.lower())
    tone = "ngang"
    kept = []
    for ch in d:
        if ch in TONE_MARKS:
            tone = TONE_MARKS[ch]
        else:
            kept.append(ch)
    return nfc("".join(kept)), tone


# Các cặp chữ cái hay bị OCR nhầm trên bản in Quốc ngữ cũ, và các biến thể dấu mũ.
_FAMILIES = ["aăâ", "eê", "oôơ", "uư", "dđ", "iy"]
_OCR_PAIRS = ["ct", "ce", "nu", "nh", "rn", "li", "lt", "hb", "mn", "ao", "eo", "gq", "sx", "vu"]
_SUB_COST: dict[tuple[str, str], float] = {}
for _fam in _FAMILIES:
    for _a in _fam:
        for _b in _fam:
            if _a != _b:
                _SUB_COST[(_a, _b)] = 0.4
for _a, _b in _OCR_PAIRS:
    _SUB_COST.setdefault((_a, _b), 0.6)
    _SUB_COST.setdefault((_b, _a), 0.6)
_CHEAP_INDEL = {"h": 0.6, "g": 0.8}  # th->t, ch->c, nh->n, ng->n ...


def ortho_distance(a: str, b: str, tone_cost: float = 0.5) -> float:
    """Khoảng cách chính tả có trọng số giữa hai âm tiết Quốc ngữ (Levenshtein trên chữ cái + phạt khác thanh)."""
    if a == b:
        return 0.0
    ba, ta = split_tone(a)
    bb, tb = split_tone(b)
    n, m = len(ba), len(bb)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + _CHEAP_INDEL.get(ba[i - 1], 1.0)
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + _CHEAP_INDEL.get(bb[j - 1], 1.0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            x, y = ba[i - 1], bb[j - 1]
            sub = 0.0 if x == y else _SUB_COST.get((x, y), 1.0)
            dp[i][j] = min(
                dp[i - 1][j - 1] + sub,
                dp[i - 1][j] + _CHEAP_INDEL.get(x, 1.0),
                dp[i][j - 1] + _CHEAP_INDEL.get(y, 1.0),
            )
    return dp[n][m] + (0.0 if ta == tb else tone_cost)


_INITIALS = sorted(["ngh", "ng", "nh", "ch", "gh", "gi", "kh", "ph", "th", "tr", "qu",
                    "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x"],
                   key=len, reverse=True)


def final_of(syl: str) -> str:
    """Phần vần (bỏ phụ âm đầu và dấu thanh): 'nàng' -> 'ang', 'gì' -> 'i'."""
    base, _ = split_tone(syl)
    for ini in _INITIALS:
        if base.startswith(ini) and len(base) > len(ini):
            return base[len(ini):]
    return base


def orthographic_variants(syl: str) -> set[tuple[str, str]]:
    """Biến thể chính tả Quốc ngữ cổ, KHÔNG phải lỗi OCR (gi <-> d, i <-> y; vị trí dấu được xử lý qua split_tone)."""
    base, tone = split_tone(syl)
    out = {(base, tone)}
    if base.startswith("gi") and len(base) > 2:
        out.add(("d" + base[2:], tone))
    elif base == "gi":
        out.add(("di", tone))
    if base.startswith("d"):
        out.add(("gi" + base[1:], tone))
    # i/y chỉ khi vần chỉ gồm i hoặc y (lí/lý, quí/quý, mĩ/mỹ) — không đụng tay/tai
    for b, t in list(out):
        r = final_of(b)
        if r in ("i", "y") and len(b) > 1:
            out.add((b[:-1] + ("y" if r == "i" else "i"), t))
    return out


def match_case(new: str, ref: str) -> str:
    return new[:1].upper() + new[1:] if ref[:1].isupper() else new
