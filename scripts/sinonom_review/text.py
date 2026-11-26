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
_OCR_PAIRS = ["ct", "ce", "nu", "nh", "rn", "li", "lt", "hb", "mn", "ao", "eo", "gq", "sx", "vu"]   # rn: r <-> n là biến thể chính tả, cũng hay bị OCR nhầm
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


_GLIDES = "iyuo"          # bán nguyên âm cuối vần (đãi, tay, sau, cao): hay bị bỏ sót khi OCR hoặc chép
_RELAXED_PAIRS = {("r", "s"), ("s", "r")}   # r <-> s hay bị chép nhầm (sỏ / rõ, sót / rót, sâu / rầu): chỉ tính rẻ ở bản nới lỏng
_HOI_NGA = {"hoi", "nga"}  # hỏi / ngã: nhiều vùng không phân biệt nên rất hay viết nhầm


def ortho_distance(a: str, b: str, tone_cost: float = 0.5, relaxed: bool = False) -> float:
    """Khoảng cách chính tả có trọng số giữa hai âm tiết Quốc ngữ (Levenshtein trên chữ cái + phạt khác thanh).
    relaxed=True tính rẻ hơn các lỗi chép và biến thể chính tả hay gặp: khác thanh hỏi / ngã chỉ phạt một nửa, bỏ sót hay thêm bán nguyên âm cuối
    (i, y, u, o) tốn 0,5, âm đầu nh <-> l (nhời / lời, nhăm / lăm, nhài / lài, nhoáng / loáng) tốn 0,7, và r <-> s tốn 0,6."""
    if a == b:
        return 0.0
    ba, ta = split_tone(a)
    bb, tb = split_tone(b)

    def core(wa: str, wb: str) -> float:
        n, m = len(wa), len(wb)

        def indel(word: str, k: int) -> float:       # chi phí bỏ / thêm chữ thứ k (1-based) của word
            c = word[k - 1]
            if relaxed and k == len(word) and c in _GLIDES and len(word) > 1:
                return 0.5
            return _CHEAP_INDEL.get(c, 1.0)

        dp = [[0.0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            dp[i][0] = dp[i - 1][0] + indel(wa, i)
        for j in range(1, m + 1):
            dp[0][j] = dp[0][j - 1] + indel(wb, j)
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                x, y = wa[i - 1], wb[j - 1]
                if x == y:
                    sub = 0.0
                elif relaxed and (x, y) in _RELAXED_PAIRS:
                    sub = 0.6
                else:
                    sub = _SUB_COST.get((x, y), 1.0)
                dp[i][j] = min(
                    dp[i - 1][j - 1] + sub,
                    dp[i - 1][j] + indel(wa, i),
                    dp[i][j - 1] + indel(wb, j),
                )
        return dp[n][m]

    dist = core(ba, bb)
    if relaxed:
        for long, short in ((ba, bb), (bb, ba)):    # nh <-> l ở âm đầu: xét phần vần còn lại với chi phí cố định 0,7 cho cả âm đầu
            if long.startswith("nh") and short.startswith("l") and len(long) > 2 and len(short) > 1:
                dist = min(dist, 0.7 + core(ba[2:] if long is ba else ba[1:], bb[1:] if long is ba else bb[2:]))
    if ta == tb:
        tone = 0.0
    elif relaxed and {ta, tb} == _HOI_NGA:
        tone = tone_cost / 2
    else:
        tone = tone_cost
    return dist + tone


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


_VOWELS = "aăâeêioôơuưy"


def orthographic_variants(syl: str, regional: bool = True) -> set[tuple[str, str]]:
    """Biến thể chính tả Quốc ngữ cổ hoặc theo vùng, KHÔNG phải lỗi OCR (gi <-> d, d <-> r, i <-> y; vị trí dấu được xử lý qua split_tone).
    gi <-> tr: giời / trời, giăng / trăng, giò / trò (chỉ khi sau đó là nguyên âm khác i).
    d <-> r: nhiều vùng viết dõi là rõi, dày là rày... Trên 4 sách đã duyệt, cặp chữ Nôm đọc r.../văn bản d... (và ngược lại) được giữ nguyên (KEEP) ở 23/28 lần.
    regional=False bỏ quan hệ d <-> r (để biết một chữ đọc được âm đó nhờ dạng vùng hay đọc đúng).
    r <-> n KHÔNG vào đây vì là lỗi viết nhầm: cặp đó đã có ứng viên Quốc ngữ với khoảng cách 0,6 (r, n nằm trong _OCR_PAIRS)."""
    base, tone = split_tone(syl)
    out = {(base, tone)}
    if base.startswith("gi") and len(base) > 2:
        out.add(("d" + base[2:], tone))
    elif base == "gi":
        out.add(("di", tone))
    if base.startswith("d"):
        out.add(("gi" + base[1:], tone))
        if regional and len(base) > 1:
            out.add(("r" + base[1:], tone))
    if regional and base.startswith("r") and len(base) > 1:
        out.add(("d" + base[1:], tone))
    if regional:          # gi <-> tr: giời / trời, giăng / trăng, giò / trò
        if base.startswith("gi") and len(base) > 2 and base[2] in _VOWELS and base[2] != "i":
            out.add(("tr" + base[2:], tone))
        if base.startswith("tr") and len(base) > 2 and base[2] in _VOWELS and base[2] != "i":
            out.add(("gi" + base[2:], tone))
    # i/y chỉ khi vần chỉ gồm i hoặc y (lí/lý, quí/quý, mĩ/mỹ) — không đụng tay/tai
    for b, t in list(out):
        r = final_of(b)
        if r in ("i", "y") and len(b) > 1:
            out.add((b[:-1] + ("y" if r == "i" else "i"), t))
    return out


def regional_forms(syl: str) -> list[str]:
    """Các dạng viết theo vùng hoặc theo chính tả cổ của một âm tiết (d <-> r, gi <-> tr, gi <-> d ở âm đầu: dõi / rõi, giò / trò, giò / dò), không gồm
    chính nó. Giữ nguyên dấu thanh và dấu mũ. Dùng khi sinh ứng viên sửa âm Quốc ngữ: đo khoảng cách tới cả các dạng này, vì chúng đọc được cùng
    các chữ Nôm (xem orthographic_variants)."""
    s = nfc(syl.lower())
    base, _ = split_tone(s)
    out = []
    if len(base) >= 2 and base[0] == "d":
        out.append("r" + s[1:])
        out.append(("g" if base[1] == "i" else "gi") + s[1:])       # dì -> gì, dò -> giò (dấu thanh nằm trên i thì chỉ thêm g)
    if len(base) >= 2 and base[0] == "r":
        out.append("d" + s[1:])
    if len(base) > 2 and base[2] in _VOWELS and base[2] != "i":
        if s[:2] == "gi":                     # dấu thanh không nằm trên chữ i của gi
            out += ["tr" + s[2:], "d" + s[2:]]
        elif s[:2] == "tr":
            out.append("gi" + s[2:])
    elif len(base) > 1 and base[0] == "g" and base[1] == "i" and s[:2] != "gi":
        out.append("d" + s[1:])               # gì -> dì: dấu thanh nằm trên i
    return out


def match_case(new: str, ref: str) -> str:
    return new[:1].upper() + new[1:] if ref[:1].isupper() else new
