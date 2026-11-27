#!/usr/bin/env python3
"""Add rendered glyph similarity features for NOM candidates in the label workbook.

Uses the same 256px render, Dice, blurred cosine, SSIM, Chamfer, and mean-rank
definitions as the user's xep_hang_chu.py snippet. HanaMin fonts are tried first;
the other Hán Nôm fonts in the repository are fallback fonts for rare glyphs.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import math

import numpy as np
from fontTools.ttLib import TTFont
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_erosion, distance_transform_edt, gaussian_filter
from scipy.stats import rankdata

try:
    from skimage.metrics import structural_similarity
except ImportError:  # Match skimage's default 7x7 uniform-window SSIM.
    from scipy.ndimage import uniform_filter

    def structural_similarity(a: np.ndarray, b: np.ndarray, data_range: float = 1.0) -> float:
        win = 7
        ux, uy = uniform_filter(a, win), uniform_filter(b, win)
        cov_norm = win**2 / (win**2 - 1)
        vx = (uniform_filter(a * a, win) - ux * ux) * cov_norm
        vy = (uniform_filter(b * b, win) - uy * uy) * cov_norm
        vxy = (uniform_filter(a * b, win) - ux * uy) * cov_norm
        c1, c2 = (0.01 * data_range) ** 2, (0.03 * data_range) ** 2
        ssim = ((2 * ux * uy + c1) * (2 * vxy + c2)
                / ((ux * ux + uy * uy + c1) * (vx + vy + c2)))
        return float(ssim[3:-3, 3:-3].mean())


SIZE, FONT_SIZE, BLUR = 256, 224, 4
ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "data" / "ranker_candidates_labels.xlsx"
OUTPUT = ROOT / "data" / "ranker_candidates_labels_glyph_similarity.xlsx"
FEATURES = ["glyph_dice", "glyph_cosblur", "glyph_ssim", "glyph_chamfer", "glyph_rank_mean"]


def load_fonts(font_dir: Path):
    files = [font_dir / "HanaMinA.ttf", font_dir / "HanaMinB.ttf"]
    files += sorted(p for p in [*font_dir.glob("*.ttf"), *font_dir.glob("*.otf")]
                    if p.name not in {"HanaMinA.ttf", "HanaMinB.ttf"})
    fonts = []
    for path in files:
        if path.exists():
            fonts.append((set(TTFont(str(path), fontNumber=0).getBestCmap()),
                          ImageFont.truetype(str(path), FONT_SIZE), path.name))
    return fonts


def render(ch: str, fonts) -> np.ndarray:
    for cmap, font, _ in fonts:
        if ord(ch) in cmap:
            im = Image.new("L", (SIZE, SIZE), 0)
            ImageDraw.Draw(im).text((SIZE / 2, SIZE / 2), ch, font=font,
                                    fill=255, anchor="mm")
            return np.asarray(im, dtype=float) / 255
    raise ValueError(f"No font covers U+{ord(ch):04X}")


def metrics(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    A, B = a > .5, b > .5
    dice = 2 * (A & B).sum() / (A.sum() + B.sum()) if (A.sum() + B.sum()) else 0.0
    ga, gb = gaussian_filter(a, BLUR), gaussian_filter(b, BLUR)
    denom = np.linalg.norm(ga) * np.linalg.norm(gb)
    cos = float((ga * gb).sum() / denom) if denom else 0.0
    ssim = structural_similarity(a, b, data_range=1)
    ea, eb = A ^ binary_erosion(A), B ^ binary_erosion(B)
    if not ea.any() or not eb.any():
        # A font may map a codepoint to an empty glyph. Keep the feature finite
        # and rank this pair as maximally dissimilar instead of emitting NaN.
        chamfer = math.hypot(SIZE, SIZE)
    else:
        chamfer = (distance_transform_edt(~eb)[ea].mean()
                   + distance_transform_edt(~ea)[eb].mean()) / 2
    return {"glyph_dice": float(dice), "glyph_cosblur": cos,
            "glyph_ssim": float(ssim), "glyph_chamfer": float(chamfer)}


def changed_pair(original: str, candidate: str):
    opcodes = [op for op in SequenceMatcher(a=original, b=candidate,
                                           autojunk=False).get_opcodes()
               if op[0] != "equal"]
    if len(opcodes) != 1:
        return None
    _, i1, i2, j1, j2 = opcodes[0]
    if i2 - i1 == 1 and j2 - j1 == 1:
        return original[i1], candidate[j1]
    return None


def main() -> None:
    fonts = load_fonts(ROOT / "font")
    wb = load_workbook(WORKBOOK)
    ws = wb["Candidates"]
    headers = [c.value for c in ws[1]]
    col = {name: headers.index(name) + 1 for name in headers}
    for name in FEATURES:
        if name not in col:
            ws.cell(1, ws.max_column + 1, name)
            col[name] = ws.max_column

    row_data: dict[int, tuple[str, str, str]] = {}
    site_rows: dict[str, list[int]] = defaultdict(list)
    pairs: set[tuple[str, str]] = set()
    unsupported_rows: set[int] = set()
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, col["candidate_type"]).value != "NOM":
            continue
        pair = changed_pair(ws.cell(r, col["text_nôm_origin"]).value or "",
                            ws.cell(r, col["text_nôm"]).value or "")
        if pair is None:
            unsupported_rows.add(r)
            continue
        query, candidate = pair
        row_data[r] = (ws.cell(r, col["site_id"]).value, query, candidate)
        site_rows[ws.cell(r, col["site_id"]).value].append(r)
        pairs.add(pair)

    glyph_cache: dict[str, np.ndarray] = {}
    covered: set[str] = set()
    for query, candidate in pairs:
        for ch in (query, candidate):
            if ch not in glyph_cache:
                try:
                    glyph_cache[ch] = render(ch, fonts)
                    covered.add(ch)
                except ValueError:
                    pass

    pair_metrics: dict[tuple[str, str], dict[str, float]] = {}
    for number, pair in enumerate(pairs, start=1):
        query, candidate = pair
        if query not in glyph_cache or candidate not in glyph_cache:
            continue
        pair_metrics[pair] = metrics(glyph_cache[query], glyph_cache[candidate])
        if number % 2000 == 0:
            print(f"Rendered comparisons: {number}/{len(pairs)}", flush=True)

    group_metric_rows: dict[str, list[tuple[int, dict[str, float]]]] = defaultdict(list)
    for r, (site_id, query, candidate) in row_data.items():
        values = pair_metrics.get((query, candidate))
        if values is None:
            unsupported_rows.add(r)
            continue
        for name, value in values.items():
            ws.cell(r, col[name], round(value, 6))
        group_metric_rows[site_id].append((r, values))

    # Mean of four per-site candidate ranks; Chamfer is lower-is-better.
    for site_id, entries in group_metric_rows.items():
        ranks = np.vstack([
            rankdata([-v["glyph_dice"] for _, v in entries], method="average"),
            rankdata([-v["glyph_cosblur"] for _, v in entries], method="average"),
            rankdata([-v["glyph_ssim"] for _, v in entries], method="average"),
            rankdata([v["glyph_chamfer"] for _, v in entries], method="average"),
        ])
        mean_ranks = ranks.mean(axis=0)
        for (r, _), rank in zip(entries, mean_ranks):
            ws.cell(r, col["glyph_rank_mean"], round(float(rank), 4))

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ws.cell(1, ws.max_column).column_letter}{ws.max_row}"
    wb.save(OUTPUT)
    print(f"Workbook: {OUTPUT}")
    print(f"Fonts loaded: {len(fonts)}; unique glyphs covered: {len(covered)}")
    print(f"Unique source-target pairs: {len(pairs)}; scored pairs: {len(pair_metrics)}")
    print(f"NOM rows scored: {sum(len(v) for v in group_metric_rows.values())}; "
          f"rows without a one-character replacement or covered glyph: {len(unsupported_rows)}")
    print("Columns added: " + ", ".join(FEATURES))


if __name__ == "__main__":
    main()
