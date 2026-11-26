"""Dóng hàng chữ Nôm ↔ âm tiết Quốc ngữ bằng Levenshtein, độ tương thích tra theo từ điển.

Chi phí mặc định: khớp từ điển = 0, lệch (substitution) = 1, chữ Nôm thừa = 1, âm Quốc ngữ thừa = 1.
Khi hoà điểm, ưu tiên ghép chéo (D), rồi âm thừa (L), rồi chữ thừa (U) — giống align_texts.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

MATCH, MISMATCH, EXTRA_NOM, EXTRA_QN = "match", "mismatch", "extra_nom", "extra_qn"
DEFAULT_COSTS = {"match": 0, "mismatch": 1, "insertion": 1, "deletion": 1}


@dataclass
class AlignedPair:
    aln_pos: int            # vị trí trong chuỗi dóng hàng (0-based)
    nom_pos: int | None     # chỉ số chữ Nôm trong câu (0-based), None nếu là âm thừa
    qn_pos: int | None      # chỉ số âm Quốc ngữ trong câu (0-based), None nếu là chữ thừa
    nom: str                # "" nếu thiếu
    qn: str                 # "" nếu thiếu
    status: str             # match | mismatch | extra_nom | extra_qn

    def to_dict(self):
        return asdict(self)


def levenshtein_align(chars: list[str], syls: list[str], compatible: Callable[[str, str], bool],
                      costs: dict | None = None) -> list[AlignedPair]:
    c = {**DEFAULT_COSTS, **(costs or {})}
    m, n = len(chars), len(syls)
    comp = [[compatible(chars[i], syls[j]) for j in range(n)] for i in range(m)]
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    bt = [[""] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0], bt[i][0] = dp[i - 1][0] + c["deletion"], "U"
    for j in range(1, n + 1):
        dp[0][j], bt[0][j] = dp[0][j - 1] + c["insertion"], "L"
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            sub = c["match"] if comp[i - 1][j - 1] else c["mismatch"]
            dp[i][j], bt[i][j] = min((dp[i - 1][j - 1] + sub, "D"),
                                     (dp[i][j - 1] + c["insertion"], "L"),
                                     (dp[i - 1][j] + c["deletion"], "U"))
    out: list[AlignedPair] = []
    i, j = m, n
    while i > 0 or j > 0:
        move = bt[i][j]
        if move == "D":
            ok = comp[i - 1][j - 1]
            out.append(AlignedPair(0, i - 1, j - 1, chars[i - 1], syls[j - 1], MATCH if ok else MISMATCH))
            i, j = i - 1, j - 1
        elif move == "U":
            out.append(AlignedPair(0, i - 1, None, chars[i - 1], "", EXTRA_NOM))
            i -= 1
        else:
            out.append(AlignedPair(0, None, j - 1, "", syls[j - 1], EXTRA_QN))
            j -= 1
    out.reverse()
    for k, p in enumerate(out):
        p.aln_pos = k
    return out


def count_status(pairs: list[AlignedPair]) -> dict:
    out = {MATCH: 0, MISMATCH: 0, EXTRA_NOM: 0, EXTRA_QN: 0}
    for p in pairs:
        out[p.status] += 1
    return out
