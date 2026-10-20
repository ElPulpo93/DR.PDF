"""Splits a page's text into the boxes a person would edit, like Acrobat's Edit tool:
one box per table cell, per label and per value, and one box per real paragraph.

PyMuPDF's own "blocks" often lump a whole table (or a column of rows) together; here
text is split at table borders and wide gaps, and lines are only joined into a paragraph
when they are aligned, close together and not separated by a ruled line."""
import pymupdf as fitz

import textrepair

BULLETS = "☐□❏❑▢◻■▪•◦‣–-✓✔✗✘"


def _rules(page):
    """Drawn lines on the page: (horizontal [(y, x0, x1)], vertical [(x, y0, y1)])."""
    hs, vs = [], []

    def add_line(p1, p2):
        if abs(p1.y - p2.y) < 1.5 and abs(p2.x - p1.x) > 6:
            hs.append(((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x)))
        elif abs(p1.x - p2.x) < 1.5 and abs(p2.y - p1.y) > 6:
            vs.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))

    def add_rect(r):
        r = fitz.Rect(r)
        if r.height < 2.5 and r.width > 6:      # a line drawn as a thin rectangle
            hs.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
        elif r.width < 2.5 and r.height > 6:
            vs.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
        elif r.width > 6 and r.height > 6:      # a box: its four edges
            hs.extend([(r.y0, r.x0, r.x1), (r.y1, r.x0, r.x1)])
            vs.extend([(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)])

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                add_line(item[1], item[2])
            elif kind == "re":
                add_rect(item[1])
            elif kind == "qu":
                add_rect(item[1].rect)
    return hs, vs


def _vrule_between(vs, x0, x1, y0, y1):
    """Is there a vertical line between x0 and x1 that crosses the height y0..y1?"""
    cy = (y0 + y1) / 2
    return any(x0 - 1 <= x <= x1 + 1 and a - 1 <= cy <= b + 1 for x, a, b in vs)


def _hrule_between(hs, y_top, y_bottom, x0, x1):
    """Is there a horizontal line between two text lines (over their width)?"""
    for y, a, b in hs:
        if y_top - 1.5 <= y <= y_bottom + 1.5:
            overlap = min(b, x1) - max(a, x0)
            if overlap > 0.3 * max(1.0, x1 - x0):
                return True
    return False


def _split_line(line, vs, styles=None):
    """Split one text line into pieces at wide gaps and at vertical lines (table cells)."""
    pieces, cur = [], None
    prev = None
    prev_size = None
    for span in line["spans"]:
        if span.get("alpha", 255) == 0:  # invisible OCR text is not edited
            continue
        size = span["size"] or 10
        for ch in span["chars"]:
            r = fitz.Rect(ch["bbox"])
            if ch["c"].isspace():
                # spaces belong to the text but not to the measuring: the PDF engine makes
                # one space fill a whole gap, which would hide that gap
                if cur is not None and cur["spans"]:
                    cur["spans"][-1]["text"] += ch["c"]
                continue
            if prev is not None:
                gap = r.x0 - prev.x1
                wide = gap > max(0.9 * size, 6)
                # a small label next to a large value ("Name:  Maykel Lopez")
                resized = gap > 1.5 and max(size, prev_size) / max(min(size, prev_size), 0.1) > 1.35
                if wide or resized or (gap > 0.5 and _vrule_between(vs, prev.x1, r.x0, r.y0, r.y1)):
                    cur = None  # start a new piece
            if cur is None:
                cur = {"spans": [], "bbox": fitz.Rect(r)}
                pieces.append(cur)
            sp = cur["spans"][-1] if cur["spans"] else None
            # repaired fonts mix typefaces (e.g. Arial and Arial Bold) in one font
            style = styles.get((round(ch["origin"][0], 1), round(ch["origin"][1], 1))) \
                if styles else None
            if sp is None or sp["_src"] is not span or sp.get("_style") != style:
                sp = {k: span[k] for k in ("size", "flags", "font", "color")}
                if style:
                    sp["font"] = style
                    sp["flags"] = (sp["flags"] & ~(16 | 2)) | (16 if "Bold" in style else 0) | \
                        (2 if "Italic" in style else 0)
                sp["_style"] = style
                sp.update(_src=span, text="", origin=tuple(ch["origin"]), bbox=fitz.Rect(r),
                          alpha=span.get("alpha", 255))
                cur["spans"].append(sp)
            sp["text"] += ch["c"]
            sp["bbox"] |= r
            cur["bbox"] |= r
            prev = r
            prev_size = size
    out = []
    for p in pieces:
        spans = [dict(s, bbox=tuple(s["bbox"])) for s in p["spans"]]
        for s in spans:
            s.pop("_src", None)
            s.pop("_style", None)
        if "".join(s["text"] for s in spans).strip():
            out.append({"bbox": tuple(p["bbox"]), "dir": line["dir"], "wmode": line.get("wmode", 0),
                        "spans": spans})
    return out


def _cell(vs, hs, r):
    """The table cell around a piece of text (left, right), if it is in one."""
    cy = (r.y0 + r.y1) / 2
    lefts = [x for x, a, b in vs if x <= r.x0 + 1 and a - 1 <= cy <= b + 1]
    rights = [x for x, a, b in vs if x >= r.x1 - 1 and a - 1 <= cy <= b + 1]
    if not lefts or not rights:
        return None
    left, right = max(lefts), min(rights)
    if right - left < r.width or right - left > 1000:
        return None
    return left, right


def _alignment(cell, r):
    if cell is None:
        return "left"
    left, right = cell
    lg, rg = r.x0 - left, right - r.x1
    if lg > 1 and abs(lg - rg) <= max(2.0, 0.15 * (lg + rg)):
        return "center"
    if rg < 5 and lg > 10:
        return "right"
    return "left"


def units(page):
    """The editable text boxes of a page, in reading order. Each is shaped like a PyMuPDF
    text block: {"bbox", "lines": [{"bbox", "dir", "spans": [...]}], plus "align" ('left',
    'center' or 'right') and "cell" ((left, right) of the table cell, or None)}."""
    hs, vs = _rules(page)
    styles = textrepair.char_styles(page)
    raw = page.get_text("rawdict", flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    pieces = []
    for bno, b in enumerate(raw["blocks"]):
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            for p in _split_line(line, vs, styles):
                p["_block"] = bno
                p["_bw"] = fitz.Rect(b["bbox"]).width
                pieces.append(p)
    pieces.sort(key=lambda p: (round(p["bbox"][1], 1), p["bbox"][0]))

    paras = []  # each: list of pieces (lines)
    for p in pieces:
        r = fitz.Rect(p["bbox"])
        size = max(s["size"] for s in p["spans"])
        first = "".join(s["text"] for s in p["spans"]).lstrip()[:1]
        home = None
        if first not in BULLETS:
            for para in reversed(paras[-40:]):
                last = para[-1]
                lr = fitz.Rect(last["bbox"])
                lsize = max(s["size"] for s in last["spans"])
                if last["dir"] != p["dir"] or not (0.8 <= size / max(lsize, 0.1) <= 1.25):
                    continue
                if abs(r.x0 - lr.x0) > 3:          # paragraphs are left-aligned
                    continue
                gap = r.y0 - lr.y1
                if not (-0.35 * size <= gap <= 0.6 * size):
                    continue
                if _hrule_between(hs, lr.y1 - 0.5, r.y0 + 0.5, min(r.x0, lr.x0), max(r.x1, lr.x1)):
                    continue                       # a table row border separates them
                if _vrule_between(vs, min(r.x0, lr.x0), max(r.x1, lr.x1), lr.y0, r.y1) and \
                        _cell(vs, hs, r) != _cell(vs, hs, lr):
                    continue                       # different table cells
                # a wrapped paragraph: the line above runs to the edge of its column (or
                # cell), or this line carries on the sentence in lower case. Short lines
                # that start with a capital are separate items (lists, labels, fields).
                nxt = "".join(s["text"] for s in p["spans"]).lstrip()
                carries_on = nxt[:1].islower() or nxt[:1] in ",.;:)]'\"%"
                cl = _cell(vs, hs, lr)
                column = (cl[1] - cl[0] - 6) if cl else last["_bw"]
                if not (carries_on or lr.width >= 0.85 * column):
                    continue
                home = para
                break
        if home is None:
            paras.append([p])
        else:
            home.append(p)

    out = []
    for para in paras:
        box = fitz.Rect(para[0]["bbox"])
        for ln in para[1:]:
            box |= fitz.Rect(ln["bbox"])
        lines = [{"bbox": ln["bbox"], "dir": ln["dir"], "wmode": ln.get("wmode", 0),
                  "spans": ln["spans"]} for ln in para]
        cell = _cell(vs, hs, box) if len(para) == 1 else None
        horizontal = abs(para[0]["dir"][0] - 1) < 1e-3
        align = _alignment(cell, box) if horizontal else "left"
        out.append({"type": 0, "bbox": tuple(box), "lines": lines, "align": align, "cell": cell})
    # centred/right-aligned only when the text is alone in its cell (not next to a label)
    for a in out:
        if a["align"] == "left" or a["cell"] is None:
            continue
        ra = fitz.Rect(a["bbox"])
        left, right = a["cell"]
        for b in out:
            if b is a:
                continue
            rb = fitz.Rect(b["bbox"])
            if rb.y1 > ra.y0 and rb.y0 < ra.y1 and rb.x0 >= left - 1 and rb.x1 <= right + 1:
                a["align"] = "left"
                break
    out.sort(key=lambda u: (round(u["bbox"][1] / 3), u["bbox"][0]))
    return out
