"""Hậu xử lý OCR cho cặp dòng Nôm - Quốc ngữ: làm sạch, dóng từng chữ, sửa lỗi, tô màu.

Chỉ có hàm thuần (không đọc/ghi file, không gọi mạng) để test được; từ điển truyền vào là scripts.nom_dict.NomDict.

Các bước (xem scripts/compare_book.py để chạy cả sách):
  B0  clean_qn / clean_nom   chuẩn hoá: NFC, chữ thường, bỏ dấu câu, bỏ số câu ở lề và ký tự lạ (đếm là "nhiễu")
  B1  align_tokens           dóng chữ Nôm thứ i với tiếng Quốc ngữ thứ j bằng quy hoạch động (cho phép bỏ trống)
  T1  correct_qn             sửa tiếng Quốc ngữ theo âm đọc của chữ Nôm (từ điển + âm theo ngữ cảnh của API)
  T2  correct_nom            đổi chữ Nôm giản thể sang phồn thể khi âm Quốc ngữ chỉ khớp với dạng phồn thể
      color_line             tô màu: đen = khớp âm, xanh = khớp qua dị thể, đỏ = không khớp, * = tiếng thiếu chữ Nôm
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------- âm tiết Quốc ngữ
TONE_MARKS = {"́": "sac", "̀": "huyen", "̉": "hoi", "̃": "nga", "̣": "nang"}
VOWEL_MARKS = {"̂", "̆", "̛"}   # mũ, trăng, móc
LETTERS = "a-zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
WORD_RE = re.compile(rf"[{LETTERS}]+")
# Cấu trúc âm tiết tiếng Việt (bỏ dấu thanh): phụ âm đầu? + vần + phụ âm cuối?
SYLLABLE_RE = re.compile(r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdđghklmnpqrstvx])?"
                         r"([aăâeêioôơuưy]{1,3})(ch|ng|nh|[cmnpt])?$")
# Số câu ở lề lẫn vào dòng Quốc ngữ: "15 - Chân hôn ...", "Gió ở đó ..., - 25"
LEAD_NUM_RE = re.compile(r"^\s*\d+\s*[-.–]?\s*")
TAIL_NUM_RE = re.compile(r"\s*[-.–]?\s*\d+\s*[.,]?\s*$")


def split_tone(syl: str) -> tuple[str, str]:
    """'hoà' -> ('hoa', 'huyen'); 'thương' -> ('thương', ''). Không phụ thuộc vị trí đặt dấu (hòa = hoà)."""
    tone, base = "", []
    for ch in unicodedata.normalize("NFD", syl.lower()):
        if ch in TONE_MARKS:
            tone = TONE_MARKS[ch]
        else:
            base.append(ch)
    return unicodedata.normalize("NFC", "".join(base)), tone


def canon(syl: str) -> str:
    base, tone = split_tone(syl)
    return f"{base}|{tone}"


def strip_marks(ch: str) -> str:
    """Bỏ dấu mũ/trăng/móc và đ->d của một chữ cái (đã bỏ dấu thanh)."""
    if ch == "đ":
        return "d"
    return "".join(c for c in unicodedata.normalize("NFD", ch) if c not in VOWEL_MARKS)


def syl_dist(a: str, b: str) -> float:
    """Khoảng cách hai âm tiết: khác dấu thanh 0.3; một chữ cái khác dấu mũ/móc (o/ơ, d/đ) 0.5; khác chữ cái 1."""
    (ba, ta), (bb, tb) = split_tone(a), split_tone(b)
    prev = [float(j) for j in range(len(bb) + 1)]
    for i, ca in enumerate(ba, 1):
        cur = [float(i)]
        for j, cb in enumerate(bb, 1):
            sub = 0.0 if ca == cb else (0.5 if strip_marks(ca) == strip_marks(cb) else 1.0)
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub))
        prev = cur
    return prev[-1] + (0.3 if ta != tb else 0.0)


def is_valid_syllable(syl: str) -> bool:
    return bool(SYLLABLE_RE.match(split_tone(syl)[0]))


def letters(syl: str) -> str:
    """Bộ khung chữ cái: bỏ dấu thanh, dấu mũ/trăng/móc, đ->d. 'mà', 'ma', 'mạ' -> 'ma'; 'có', 'cố' -> 'co'."""
    return "".join(strip_marks(c) for c in split_tone(syl)[0])


def near(a: str, b: str) -> bool:
    """Hai âm chỉ khác dấu thanh / dấu mũ: thường là âm Nôm biến từ âm Hán-Việt (麻 ma -> mà, 固 cố -> có)."""
    return letters(a) == letters(b)


# ---------------------------------------------------------------- B0: làm sạch
@dataclass
class Noise:
    kind: str     # "so_cau" (số câu ở lề) | "ky_tu_la" (ký tự không phải chữ Quốc ngữ)
    text: str


def clean_qn(text: str) -> tuple[list[str], list[Noise]]:
    """Dòng Quốc ngữ OCR -> danh sách tiếng (chữ thường, không dấu câu) và các đoạn nhiễu đã bỏ."""
    noise: list[Noise] = []
    text = unicodedata.normalize("NFC", text).strip()
    for regex in (LEAD_NUM_RE, TAIL_NUM_RE):
        m = regex.search(text)
        if m and re.search(r"\d", m.group()):
            noise.append(Noise("so_cau", m.group().strip()))
            text = text[:m.start()] + text[m.end():]
    words: list[str] = []
    for raw in text.lower().split():
        found = WORD_RE.findall(raw)
        leftover = WORD_RE.sub("", raw)
        leftover = re.sub(r"[\s.,;:!?\"'’‘“”()\[\]\-–—…]+", "", leftover)
        if leftover:
            noise.append(Noise("ky_tu_la", leftover))
        words.extend(found)
    return words, noise


def is_nom_char(ch: str) -> bool:
    """Chữ Hán/Nôm (kể cả vùng riêng PUA mà font Nôm dùng); loại dấu câu, khoảng trắng, chữ Latin."""
    if ch.isspace():
        return False
    cat = unicodedata.category(ch)
    if cat == "Co" or 0x20000 <= ord(ch) <= 0x3FFFF:   # PUA của font Nôm; mặt phẳng 2-3 (kể cả khối mới hơn Python)
        return True
    return cat.startswith("L") and not ch.isascii() and "CJK" in unicodedata.name(ch, "CJK")


def clean_nom(text: str) -> list[str]:
    return [ch for ch in unicodedata.normalize("NFC", text) if is_nom_char(ch)]


# ---------------------------------------------------------------- tô màu / độ khớp
BLACK, BLUE, RED, STAR = "den", "xanh", "do", "*"


def match_kind(nomdict, ch: str, syl: str) -> str:
    """den: syl là âm của ch.
    xanh: syl là âm của một dị thể của ch, hoặc chỉ khác một âm của ch (hay dị thể) ở dấu thanh/dấu mũ
          (từ điển chủ yếu có âm Hán-Việt, âm Nôm thường biến dấu: 麻 ma/mà, 羅 la/là, 㐌 đà/đã).
    do: không khớp, hoặc chữ không có trong từ điển."""
    key = canon(syl)
    own = nomdict.readings(ch)
    if any(canon(r) == key for r in own):
        return BLACK
    var = set().union(*nomdict.variant_readings(ch).values())
    if any(canon(r) == key for r in var) or any(near(syl, r) for r in own | var):
        return BLUE
    return RED


def best_distance(nomdict, ch: str, syl: str) -> float:
    cands = set(nomdict.readings(ch))
    for rs in nomdict.variant_readings(ch).values():
        cands |= rs
    return min((syl_dist(syl, r) for r in cands), default=9.0)


GAP = -0.3


def pair_score(nomdict, ch: str, syl: str) -> float:
    kind = match_kind(nomdict, ch, syl)
    if kind == BLACK:
        return 1.0
    if kind == BLUE:
        return 0.8
    return max(0.0, 0.6 - 0.3 * best_distance(nomdict, ch, syl))


def align_tokens(nomdict, nom: list[str], qn: list[str]) -> list[tuple[int | None, int | None]]:
    """Dóng chữ Nôm với tiếng Quốc ngữ (Needleman-Wunsch). Trả danh sách (chỉ số Nôm | None, chỉ số QN | None)."""
    n, m = len(nom), len(qn)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], move[i][0] = score[i - 1][0] + GAP, "u"
    for j in range(1, m + 1):
        score[0][j], move[0][j] = score[0][j - 1] + GAP, "l"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [(score[i - 1][j - 1] + pair_score(nomdict, nom[i - 1], qn[j - 1]), "d"),
                       (score[i - 1][j] + GAP, "u"),
                       (score[i][j - 1] + GAP, "l")]
            score[i][j], move[i][j] = max(options, key=lambda o: o[0])   # hoà điểm: ưu tiên đường chéo
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        mv = move[i][j]
        if mv == "d":
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif mv == "u":
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    return pairs[::-1]


def color_line(nomdict, nom: list[str], qn: list[str]) -> tuple[list[str], dict[str, int]]:
    """Màu từng chữ Nôm và số đếm {den, xanh, do, *}. Chữ Nôm không có tiếng tương ứng tô đỏ."""
    colors = [RED] * len(nom)
    counts = {BLACK: 0, BLUE: 0, RED: 0, STAR: 0}
    for i, j in align_tokens(nomdict, nom, qn):
        if i is None:
            counts[STAR] += 1
            continue
        colors[i] = match_kind(nomdict, nom[i], qn[j]) if j is not None else RED
        counts[colors[i]] += 1
    return colors, counts


# ---------------------------------------------------------------- dữ liệu một dòng
@dataclass
class Line:
    id: str                       # image8_1 (giống file mẫu)
    sentence_id: str              # <img_id bỏ .jpg>.<stt> (định dạng nộp bài)
    img_id: str
    box: list                     # 4 điểm box chữ Nôm
    nom: list[str]
    qn: list[str]
    context: list[str] | None = None   # âm của từng chữ Nôm do API phiên theo cả dòng (None nếu lệch số chữ)
    qn_source: str = "ocr"             # "ocr" | "may_phien"
    noise: list[Noise] = field(default_factory=list)

    @property
    def nom_text(self) -> str:
        return "".join(self.nom)

    @property
    def qn_text(self) -> str:
        return " ".join(self.qn)


@dataclass
class Edit:
    line_id: str
    layer: str        # "T1" (Quốc ngữ) | "T2" (chữ Nôm)
    pos: int          # vị trí tiếng QN (T1) hoặc chữ Nôm (T2); -1 = cả dòng
    old: str
    new: str
    source: str


# ---------------------------------------------------------------- T1: sửa Quốc ngữ
MAX_FIX_DIST = 1.3      # tiếng hợp lệ: chỉ sửa khi âm từ điển cách <= 1 chữ cái (+ dấu thanh): chương->thương, the->thu
MAX_CTX_DIST = 2.0      # tiếng sai cấu trúc âm tiết: nhận cả âm của API nếu cách <= 2
MACHINE_RATIO = 0.5     # quá nửa số tiếng sai cấu trúc -> thay cả dòng bằng bản máy phiên


def _pick_reading(nomdict, ch: str, syl: str, ctx: str | None) -> tuple[str, str] | None:
    """Chọn âm thay cho tiếng OCR `syl` (đang tô đỏ với chữ `ch`). Trả (âm mới, nguồn) hoặc None.

    - syl là âm tiết hợp lệ: chỉ tin âm từ điển Unihan (chữ + dị thể), sai khác <= MAX_FIX_DIST, phải duy nhất.
    - syl sai cấu trúc (rác OCR): dùng thêm âm API (từng chữ và theo ngữ cảnh), sai khác <= MAX_CTX_DIST.
    Khi hoà điểm, ưu tiên âm ngữ cảnh của API.
    """
    if is_valid_syllable(syl):
        cands, limit, source = nomdict.dict_readings(ch), MAX_FIX_DIST, "Âm chữ Nôm (từ điển, khớp ngữ cảnh)"
    else:
        cands = set(nomdict.readings(ch)) | nomdict.dict_readings(ch) | ({ctx} if ctx else set())
        limit, source = MAX_CTX_DIST, "Âm chữ Nôm (từ điển + Kim Hán Nôm)"
    if not cands:
        return None
    scored = sorted((syl_dist(syl, r), r) for r in cands)
    best = scored[0][0]
    if best > limit:
        return None
    ties = {canon(r): r for d, r in scored if d == best}
    if len(ties) == 1:
        pick = next(iter(ties.values()))
    elif ctx and canon(ctx) in ties:
        pick = ctx
    else:
        return None
    # Tiếng hợp lệ: cần hai nguồn đồng ý - âm từ điển phải trùng (hoặc chỉ khác dấu) âm API phiên theo ngữ cảnh dòng.
    # Tránh đổi tiếng đúng sang âm Hán-Việt khi chữ có âm Nôm khác (𢜝: từ điển "sực", ảnh/ngữ cảnh "sợ").
    if is_valid_syllable(syl) and ctx and not near(pick, ctx):
        return None
    return pick, source


def correct_qn(nomdict, line: Line) -> tuple[Line, list[Edit]]:
    edits: list[Edit] = []
    qn = list(line.qn)
    ctx = line.context

    invalid = sum(not is_valid_syllable(s) for s in qn)
    if ctx and (not qn or invalid > MACHINE_RATIO * len(qn)):
        edits.append(Edit(line.id, "T1", -1, line.qn_text, " ".join(ctx), "Máy phiên (Kim Hán Nôm)"))
        return replace(line, qn=list(ctx), qn_source="may_phien"), edits

    for i, j in align_tokens(nomdict, line.nom, qn):
        if i is None or j is None:
            continue
        ch, syl = line.nom[i], qn[j]
        if match_kind(nomdict, ch, syl) != RED:
            continue
        picked = _pick_reading(nomdict, ch, syl, ctx[i] if ctx else None)
        if picked and canon(picked[0]) != canon(syl):
            edits.append(Edit(line.id, "T1", j, syl, picked[0], picked[1]))
            qn[j] = picked[0]
    return replace(line, qn=qn), edits


# ---------------------------------------------------------------- T2: sửa chữ Nôm theo dị thể
def correct_nom(nomdict, line: Line) -> tuple[Line, list[Edit]]:
    edits: list[Edit] = []
    nom = list(line.nom)
    for i, j in align_tokens(nomdict, nom, line.qn):
        if i is None or j is None or match_kind(nomdict, nom[i], line.qn[j]) != BLUE:
            continue
        key = canon(line.qn[j])
        hits = [t for t in nomdict.traditional_of(nom[i]) if any(canon(r) == key for r in nomdict.readings(t))]
        if len(hits) == 1:
            edits.append(Edit(line.id, "T2", i, nom[i], hits[0], "Dị thể Unihan (giản → phồn)"))
            nom[i] = hits[0]
    return replace(line, nom=nom), edits


# ---------------------------------------------------------------- thống kê
def count_colors(nomdict, lines: list[Line]) -> dict[str, int]:
    total = {BLACK: 0, BLUE: 0, RED: 0, STAR: 0}
    for ln in lines:
        for k, v in color_line(nomdict, ln.nom, ln.qn)[1].items():
            total[k] += v
    return total


def similarity(counts: dict[str, int]) -> float:
    """(Đen + Xanh) / (Đen + Xanh + Đỏ + *), giống cột 'Đen+Xanh' của file mẫu."""
    total = sum(counts.values())
    return (counts[BLACK] + counts[BLUE]) / total if total else 0.0
