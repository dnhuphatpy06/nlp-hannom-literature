"""Xuất kết quả gán nhãn ra thư mục output.

  mismatch_pairs_before.csv/.xlsx  các cặp lệch ban đầu (kèm id ảnh, thứ tự câu, thứ tự cặp trong câu)
  candidates_review.csv/.xlsx      mọi ứng viên của từng cặp, giảm dần theo điểm, kèm điểm, confidence và lựa chọn
  mismatch_before.xlsx             toàn bộ câu, tô đỏ cặp lệch TRƯỚC khi gán nhãn
  mismatch_after.xlsx              toàn bộ câu SAU khi áp dụng lựa chọn (đỏ: còn lệch, cam: KEEP, xanh: đã sửa)
  statistics.json/.csv             số cặp lệch và phần trăm trước/sau, theo sách và tổng
  confirmed_keep_pairs.csv         các cặp (âm, chữ) được giữ nguyên — dùng làm từ điển bổ sung (QuocNgu, SinoNom)
"""
from __future__ import annotations

import collections
import json
import time
from pathlib import Path

import pandas as pd

from .alignment import EXTRA_NOM, EXTRA_QN, MATCH, MISMATCH

RED, ORANGE, GREEN, GRAY = "#C00000", "#C55A11", "#00873E", "#8C8C8C"


def _image_info(sess, s):
    ref = sess.images.lookup(s.image_id)
    return (ref.path.name if ref.path else ""), ("" if ref.line_index is None else ref.line_index)


def mismatch_pairs_table(sess) -> pd.DataFrame:
    page_order = {i: k + 1 for k, i in enumerate(sess.pages)}
    rows = []
    for s, aln in zip(sess.sentences, sess.alignments):
        img, line = _image_info(sess, s)
        k = 0
        for p in aln:
            if p.status == MATCH:
                continue
            if p.status == MISMATCH:
                k += 1
            rows.append({
                "book": s.book, "sentence_order": s.idx + 1, "review_page": page_order.get(s.idx, ""),
                "ID": s.sid, "image_id": s.image_id, "image_file": img, "line_in_image": line,
                "pair_order": k if p.status == MISMATCH else "", "type": p.status,
                "aln_pos": p.aln_pos + 1,
                "nom_pos": "" if p.nom_pos is None else p.nom_pos + 1,
                "qn_pos": "" if p.qn_pos is None else p.qn_pos + 1,
                "nom": p.nom, "qn": p.qn,
                "nom_readings_in_dict": ", ".join(sorted(sess.dic.nom_to_viet(p.nom)))[:200] if p.nom else "",
                "sentence_nom": s.nom, "sentence_qn": s.qn})
    return pd.DataFrame(rows)


def candidates_table(sess) -> pd.DataFrame:
    rows = []
    for st in sess.sites:
        s = sess.sentences[st.sent_idx]
        d = sess.decisions.get(st.key)
        decision = d["side"] if d else "UNREVIEWED"
        cands = sess.candidates.get(st.key, [])
        base = {"book": s.book, "sentence_order": s.idx + 1, "ID": s.sid, "image_id": s.image_id,
                "pair_order": st.pair_order, "nom_pos": st.nom_pos + 1, "qn_pos": st.qn_pos + 1,
                "nom": st.nom, "qn": st.qn, "n_candidates": sum(1 for h in cands if h["side"] != "KEEP")}
        fin = {"decision": decision, "final_nom": d["nom"] if d else st.nom, "final_qn": d["qn"] if d else st.qn,
               "decided_at": d["time"] if d else ""}
        for h in cands:
            sel = bool(d) and d["side"] == h["side"] and d["nom"] == h["nom"] and d["qn"] == h["qn"]
            rows.append({**base, "rank": h["rank"], "side": h["side"], "cand_nom": h["nom"], "cand_qn": h["qn"],
                         "score": h["score"], "confidence": round(h["confidence"], 4),
                         "evidence": json.dumps(h["evidence"], ensure_ascii=False),
                         "selected": "x" if sel else "", **fin})
        if not cands:
            rows.append({**base, "rank": "", "side": "", "cand_nom": "", "cand_qn": "", "score": "",
                         "confidence": "", "evidence": "chưa chấm điểm", "selected": "", **fin})
    return pd.DataFrame(rows)


def statistics(sess) -> dict:
    per = collections.OrderedDict()

    def blank():
        return collections.Counter()

    for s in sess.sentences:
        c = per.setdefault(s.book, blank())
        c["sentences"] += 1
        after = sess.after_alignment(s.idx)
        c["sentences_with_mismatch"] += any(a["status_before"] == MISMATCH for a in after)
        for a in after:
            c["positions"] += 1
            if a["status_before"] in (MATCH, MISMATCH):
                c["aligned_pairs"] += 1
                c["mismatch_before"] += a["status_before"] == MISMATCH
                c["mismatch_after"] += a["status_after"] == MISMATCH
                c["mismatch_after_unconfirmed"] += a["status_after"] == MISMATCH and a["how"] != "KEEP"
            else:
                c[a["status_before"]] += 1
            if a["status_before"] == MISMATCH:
                c["decision_" + (a["how"] or "UNREVIEWED")] += 1
                if sess.candidates and not sess.top_candidates(f"{s.book}|{s.row}|{a['aln_pos']}"):
                    c["no_candidate"] += 1
    total = blank()
    for c in per.values():
        total.update(c)
    per["TOTAL"] = total

    def finish(c):
        ap, pos = c["aligned_pairs"], c["positions"]
        gaps = c[EXTRA_NOM] + c[EXTRA_QN]
        pct = lambda a, b: round(100 * a / b, 2) if b else 0.0
        return {
            "sentences": c["sentences"], "sentences_with_mismatch": c["sentences_with_mismatch"],
            "aligned_pairs": ap, "alignment_positions": pos,
            "mismatch_before": c["mismatch_before"], "pct_before": pct(c["mismatch_before"], ap),
            "mismatch_after": c["mismatch_after"], "pct_after": pct(c["mismatch_after"], ap),
            "mismatch_reduced": c["mismatch_before"] - c["mismatch_after"],
            "mismatch_after_unconfirmed": c["mismatch_after_unconfirmed"],
            "pct_after_unconfirmed": pct(c["mismatch_after_unconfirmed"], ap),
            "extra_nom": c[EXTRA_NOM], "extra_qn": c[EXTRA_QN],
            "red_incl_gaps_before": c["mismatch_before"] + gaps,
            "pct_red_incl_gaps_before": pct(c["mismatch_before"] + gaps, pos),
            "red_incl_gaps_after": c["mismatch_after"] + gaps,
            "pct_red_incl_gaps_after": pct(c["mismatch_after"] + gaps, pos),
            "mismatch_no_candidate": c["no_candidate"],
            "pairs_to_review": c["mismatch_before"] - c["no_candidate"],
            "reviewed_pairs": c["decision_KEEP"] + c["decision_NOM"] + c["decision_QN"],
            "decision_KEEP": c["decision_KEEP"], "decision_NOM": c["decision_NOM"], "decision_QN": c["decision_QN"],
            "decision_UNREVIEWED": c["decision_UNREVIEWED"] - c["no_candidate"],
        }

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "definitions": {
            "mismatch": "cặp chữ Nôm – âm Quốc ngữ được dóng với nhau nhưng chữ không đọc được là âm đó theo từ điển "
                        "(không tính chữ thừa/âm thừa)",
            "pct_before/pct_after": "mismatch / aligned_pairs (số cặp chữ–âm được dóng, không tính vị trí thừa)",
            "mismatch_after": "sau khi áp dụng lựa chọn; cặp KEEP vẫn tính là lệch vì không có trong từ điển",
            "mismatch_after_unconfirmed": "mismatch_after trừ các cặp người gán nhãn đã xác nhận KEEP",
            "mismatch_no_candidate": "cặp lệch không có ứng viên sửa nào: không hiển thị cho người gán nhãn, vẫn tính là lệch",
            "pairs_to_review": "cặp lệch có ít nhất một ứng viên (được hiển thị để duyệt) = mismatch_before - mismatch_no_candidate",
            "red_incl_gaps_*": "lệch + chữ thừa + âm thừa, chia cho mọi vị trí dóng hàng (cách tính của align_texts.py)",
        },
        "settings": sess.score_settings,
        "books": {k: finish(v) for k, v in per.items()},
    }


def _write_tokens(ws, r, c, tokens, fmts, sep, default):
    """Ghi một ô rich-text; tokens và fmts cùng độ dài; gộp các mảnh liền kề cùng định dạng."""
    frags = []
    for i, (t, f) in enumerate(zip(tokens, fmts)):
        txt = t + (sep if i < len(tokens) - 1 else "")
        f = f or default
        if frags and frags[-2] is f:
            frags[-1] += txt
        else:
            frags += [f, txt]
    if not frags:
        ws.write_string(r, c, "")
    elif len(frags) == 2:
        ws.write_string(r, c, frags[1], frags[0])
    else:
        ws.write_rich_string(r, c, *frags)


def write_highlight_xlsx(sess, path: Path, after: bool):
    from xlsxwriter import Workbook

    with Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False}) as wb:
        default = wb.add_format({})
        red = wb.add_format({"font_color": RED, "bold": True})
        orange = wb.add_format({"font_color": ORANGE, "bold": True})
        green = wb.add_format({"font_color": GREEN, "bold": True})
        gray = wb.add_format({"font_color": GRAY, "italic": True})
        head = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1})
        ws = wb.add_worksheet("after" if after else "before")
        if after:
            cols = ["Sách", "ID", "Patch ID (id ảnh)", "Nôm (sau)", "Quốc ngữ (sau)", "Lệch (sau)", "Đã sửa",
                    "KEEP", "Chưa duyệt", "Nôm (trước)", "Quốc ngữ (trước)"]
            widths = [24, 8, 32, 30, 44, 10, 8, 8, 10, 30, 44]
        else:
            cols = ["Sách", "ID", "Patch ID (id ảnh)", "Ảnh", "Dòng trong ảnh", "Nôm", "Quốc ngữ", "Số cặp lệch",
                    "Chữ Nôm thừa", "Âm thừa"]
            widths = [24, 8, 32, 30, 8, 30, 44, 10, 10, 10]
        for j, (cname, w) in enumerate(zip(cols, widths)):
            ws.write(0, j, cname, head)
            ws.set_column(j, j, w)
        ws.freeze_panes(1, 0)
        for r, s in enumerate(sess.sentences, start=1):
            al = sess.after_alignment(s.idx)
            nom_f, qn_f = [None] * len(s.chars), [None] * len(s.syls)
            if not after:
                for a in al:
                    f = red if a["status_before"] == MISMATCH else gray if a["status_before"] in (EXTRA_NOM, EXTRA_QN) \
                        else None
                    if a["nom_pos"] is not None:
                        nom_f[a["nom_pos"]] = f
                    if a["qn_pos"] is not None:
                        qn_f[a["qn_pos"]] = f
                img, line = _image_info(sess, s)
                n = collections.Counter(a["status_before"] for a in al)
                ws.write(r, 0, s.book)
                ws.write(r, 1, s.sid)
                ws.write(r, 2, s.image_id)
                ws.write(r, 3, img)
                ws.write(r, 4, line)
                _write_tokens(ws, r, 5, s.chars, nom_f, "", default)
                _write_tokens(ws, r, 6, s.syls, qn_f, " ", default)
                ws.write(r, 7, n[MISMATCH])
                ws.write(r, 8, n[EXTRA_NOM])
                ws.write(r, 9, n[EXTRA_QN])
            else:
                chars, syls = list(s.chars), list(s.syls)
                nom_b, qn_b = [None] * len(s.chars), [None] * len(s.syls)
                for a in al:
                    if a["status_before"] == MISMATCH:
                        if a["how"] == "KEEP":
                            f = orange
                        elif a["how"] and a["status_after"] == MATCH:
                            f = green
                        elif a["status_after"] == MISMATCH:
                            f = red
                        else:
                            f = green
                    elif a["status_before"] in (EXTRA_NOM, EXTRA_QN):
                        f = gray
                    else:
                        f = None
                    if a["nom_pos"] is not None:
                        chars[a["nom_pos"]] = a["nom"]
                        nom_f[a["nom_pos"]] = f
                        nom_b[a["nom_pos"]] = red if a["status_before"] == MISMATCH else None
                    if a["qn_pos"] is not None:
                        syls[a["qn_pos"]] = a["qn"]
                        qn_f[a["qn_pos"]] = f
                        qn_b[a["qn_pos"]] = red if a["status_before"] == MISMATCH else None
                mm = [a for a in al if a["status_before"] == MISMATCH]
                ws.write(r, 0, s.book)
                ws.write(r, 1, s.sid)
                ws.write(r, 2, s.image_id)
                _write_tokens(ws, r, 3, chars, nom_f, "", default)
                _write_tokens(ws, r, 4, syls, qn_f, " ", default)
                ws.write(r, 5, sum(1 for a in al if a["status_after"] == MISMATCH))
                ws.write(r, 6, sum(1 for a in mm if a["how"] in ("NOM", "QN")))
                ws.write(r, 7, sum(1 for a in mm if a["how"] == "KEEP"))
                ws.write(r, 8, sum(1 for a in mm if not a["how"]))
                _write_tokens(ws, r, 9, s.chars, nom_b, "", default)
                _write_tokens(ws, r, 10, s.syls, qn_b, " ", default)
        ws.autofilter(0, 0, len(sess.sentences), len(cols) - 1)
        lg = wb.add_worksheet("legend")
        lg.set_column(0, 0, 14)
        lg.set_column(1, 1, 90)
        items = ([("đỏ", red, "còn lệch từ điển (chưa duyệt, không có ứng viên, hoặc ứng viên được chọn vẫn không có trong từ điển)"),
                  ("cam", orange, "người gán nhãn chọn KEEP (giữ nguyên; vẫn không có trong từ điển)"),
                  ("xanh", green, "đã sửa theo lựa chọn của người gán nhãn"),
                  ("xám nghiêng", gray, "chữ Nôm thừa / âm Quốc ngữ thừa (không tính là cặp lệch)")]
                 if after else
                 [("đỏ", red, "cặp lệch: chữ Nôm không đọc được là âm Quốc ngữ được dóng với nó theo từ điển"),
                  ("xám nghiêng", gray, "chữ Nôm thừa / âm Quốc ngữ thừa (không tính là cặp lệch)")])
        for i, (name, f, desc) in enumerate(items):
            lg.write(i, 0, name, f)
            lg.write(i, 1, desc)


def _to_csv_xlsx(df: pd.DataFrame, out: Path, stem: str, sheet: str):
    csv = out / f"{stem}.csv"
    xlsx = out / f"{stem}.xlsx"
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(xlsx, engine="xlsxwriter") as xw:
        df.to_excel(xw, sheet_name=sheet, index=False)
        ws = xw.sheets[sheet]
        ws.freeze_panes(1, 0)
        if len(df.columns):
            ws.autofilter(0, 0, len(df), len(df.columns) - 1)
    return csv, xlsx


def export_all(sess, verbose=True) -> dict:
    out = sess.output_dir
    paths = {}
    paths["mismatch_pairs"] = _to_csv_xlsx(mismatch_pairs_table(sess), out, "mismatch_pairs_before", "pairs")
    paths["candidates"] = _to_csv_xlsx(candidates_table(sess), out, "candidates_review", "candidates")
    for name, after in (("mismatch_before.xlsx", False), ("mismatch_after.xlsx", True)):
        write_highlight_xlsx(sess, out / name, after)
        paths[name] = out / name
    st = statistics(sess)
    (out / "statistics.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame.from_dict(st["books"], orient="index").rename_axis("book").to_csv(
        out / "statistics.csv", encoding="utf-8-sig")
    paths["statistics"] = (out / "statistics.json", out / "statistics.csv")
    keep = collections.Counter((d["orig_qn"].lower(), d["orig_nom"]) for d in sess.decisions.values()
                               if d["side"] == "KEEP")
    pd.DataFrame([{"QuocNgu": q, "SinoNom": c, "count": n} for (q, c), n in keep.most_common()],
                 columns=["QuocNgu", "SinoNom", "count"]).to_csv(out / "confirmed_keep_pairs.csv", index=False,
                                                                  encoding="utf-8-sig")
    paths["confirmed_keep_pairs"] = out / "confirmed_keep_pairs.csv"
    if verbose:
        t = st["books"]["TOTAL"]
        print(f"Đã lưu vào {out}")
        print(f"  Lệch trước: {t['mismatch_before']}/{t['aligned_pairs']} ({t['pct_before']}%)  ->  "
              f"sau: {t['mismatch_after']} ({t['pct_after']}%), "
              f"không tính KEEP: {t['mismatch_after_unconfirmed']} ({t['pct_after_unconfirmed']}%)")
        print(f"  Đã duyệt {t['reviewed_pairs']}/{t['pairs_to_review']} cặp có ứng viên "
              f"({t['mismatch_no_candidate']} cặp không có ứng viên, không hiển thị)")
    return paths
