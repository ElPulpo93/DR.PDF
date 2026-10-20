"""Create fillable form fields (Acrobat's "Prepare a form"), including automatic detection
of blanks, answer lines and checkboxes on typed and scanned forms."""
import re

import pymupdf as fitz

FIELD_TYPES = [  # key, label
    ("text", "Text field"),
    ("multiline", "Multi-line text box"),
    ("checkbox", "Checkbox"),
    ("dropdown", "Dropdown list"),
    ("date", "Date field"),
    ("signature", "Signature field"),
]
LABELS = dict(FIELD_TYPES)
BOX_GLYPHS = "☐□❏❑▢◻"


def existing_names(doc):
    names = set()
    for page in doc:
        for w in page.widgets():
            names.add(w.field_name)
    return names


def unique_name(doc, base, taken=None):
    taken = existing_names(doc) if taken is None else taken
    base = re.sub(r"[^\w .-]", "", base).strip(" .-_") or "Field"
    base = base[:40]
    name, k = base, 2
    while name in taken:
        name = f"{base} {k}"
        k += 1
    taken.add(name)
    return name


def create_field(page, kind, rect, name, choices=None, fontsize=0, tooltip=""):
    """Add one field. Returns its xref."""
    w = fitz.Widget()
    w.rect = fitz.Rect(rect)
    w.field_name = name
    w.field_label = tooltip or name
    w.text_fontsize = fontsize
    w.border_color = (0.55, 0.6, 0.75)
    w.border_width = 0.8
    w.fill_color = (0.93, 0.95, 1.0)
    if kind in ("text", "date"):
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        if kind == "date":
            w.field_label = tooltip or "Date (MM/DD/YYYY)"
    elif kind == "multiline":
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
    elif kind == "checkbox":
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_value = False
    elif kind == "dropdown":
        w.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX
        w.choice_values = list(choices or ["Option 1", "Option 2"])
        w.field_value = w.choice_values[0]
    elif kind == "signature":
        w.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
        w.fill_color = (1.0, 0.98, 0.88)
    else:
        raise ValueError(kind)
    annot = page.add_widget(w)
    return annot.xref


def kind_of(widget):
    t = widget.field_type
    if t == fitz.PDF_WIDGET_TYPE_TEXT:
        return "multiline" if widget.field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE else "text"
    return {fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox", fitz.PDF_WIDGET_TYPE_COMBOBOX: "dropdown",
            fitz.PDF_WIDGET_TYPE_LISTBOX: "dropdown", fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
            fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "checkbox"}.get(t, "text")


# ------------------------------------------------------------------ automatic detection
def _label_left(words, rect, max_gap=220):
    """Text just left of `rect` on the same line, e.g. 'Name:'."""
    cy = (rect.y0 + rect.y1) / 2
    row = [w for w in words if w[0] < rect.x0 + 2 and w[2] <= rect.x0 + 4
           and abs((w[1] + w[3]) / 2 - cy) < max(8, rect.height)]
    row.sort(key=lambda w: w[0])
    picked = []
    edge = rect.x0
    for w in reversed(row):
        if edge - w[2] > (max_gap if not picked else 14):
            break
        picked.insert(0, w[4])
        edge = w[0]
        if len(picked) >= 5 or w[4].endswith(":") and len(picked) > 1:
            break
    return " ".join(picked).strip().rstrip(":").strip()


def _label_above(words, rect, max_gap=20):
    """The line of text just above `rect` (labels written over a box), e.g. 'Notes'."""
    cands = [w for w in words if rect.y0 - max_gap <= w[3] <= rect.y0 + 2
             and w[2] > rect.x0 - 4 and w[0] < rect.x0 + min(rect.width, 200)]
    if not cands:
        return ""
    bottom = max(w[3] for w in cands)
    line = sorted((w for w in cands if abs(w[3] - bottom) < 3), key=lambda w: w[0])
    return " ".join(w[4] for w in line[:6]).strip().rstrip(":").strip()


def _label_right(words, rect):
    cy = (rect.y0 + rect.y1) / 2
    row = sorted((w for w in words if w[0] >= rect.x1 - 2 and w[0] - rect.x1 < 40
                  and abs((w[1] + w[3]) / 2 - cy) < max(8, rect.height)), key=lambda w: w[0])
    out, edge = [], rect.x1
    for w in row:
        if w[0] - edge > 14 and out:
            break
        out.append(w[4])
        edge = w[2]
        if len(out) >= 5:
            break
    return " ".join(out).strip()


def _free_above(words, r, height):
    """Is the band just above an answer line empty of text?"""
    band = fitz.Rect(r.x0 + 2, r.y0 - height, r.x1 - 2, r.y0 - 1)
    return not any(fitz.Rect(w[:4]).intersects(band) for w in words)


def _vector_suggestions(page, words):
    out = []
    # 1) blanks typed as underscores: "Name: ________"
    for w in words:
        for m in re.finditer(r"_{4,}", w[4]):
            r = fitz.Rect(w[:4])
            per = r.width / max(len(w[4]), 1)
            rr = fitz.Rect(r.x0 + per * m.start(), r.y0, r.x0 + per * m.end(), r.y1)
            label = (w[4][:m.start()].strip().rstrip(":") or _label_left(words, rr))
            out.append(("text", fitz.Rect(rr.x0, rr.y1 - 16, rr.x1, rr.y1 + 1), label))
    # 2) drawn answer lines and checkbox squares
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 1.2 and abs(p2.x - p1.x) >= 50:
                    r = fitz.Rect(min(p1.x, p2.x), p1.y, max(p1.x, p2.x), p1.y)
                    if _free_above(words, r, 14):
                        out.append(("text", fitz.Rect(r.x0, r.y0 - 16, r.x1, r.y0 - 0.5),
                                    _label_left(words, fitz.Rect(r.x0, r.y0 - 14, r.x1, r.y0))))
            elif item[0] == "re":
                r = fitz.Rect(item[1])
                if 6 <= r.width <= 22 and abs(r.width - r.height) < 2.5:
                    out.append(("checkbox", r, _label_right(words, r)))
                elif r.height <= 2.5 and r.width >= 50 and _free_above(words, r, 14):
                    out.append(("text", fitz.Rect(r.x0, r.y0 - 16, r.x1, r.y0 - 0.5),
                                _label_left(words, fitz.Rect(r.x0, r.y0 - 14, r.x1, r.y0))))
    # 3) box characters: "☐ Yes"
    for w in words:
        if w[4] and w[4][0] in BOX_GLYPHS:
            r = fitz.Rect(w[:4])
            size = min(r.height, 14)
            box = fitz.Rect(r.x0, r.y1 - size, r.x0 + size, r.y1)
            label = w[4][1:].strip() or _label_right(words, box)
            out.append(("checkbox", box, label))
    return out


def _image_suggestions(page, words):
    """Answer lines and squares drawn in a scanned picture (found with OpenCV)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    zoom = 2.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    bw = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    out = []
    # horizontal lines at least ~50 pt long
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (int(50 * zoom), 1))
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    to_page = ~fitz.Matrix(zoom, zoom) * page.derotation_matrix
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h > 6 * zoom:
            continue
        r = fitz.Rect(x, y, x + w, y + h) * to_page
        r = fitz.Rect(r.x0, r.y0, r.x1, r.y0)
        vis = fitz.Rect(x, y, x + w, y) / zoom
        if _free_above(words, vis, 14):
            out.append(("text", fitz.Rect(r.x0, r.y0 - 16, r.x1, r.y0 - 0.5),
                        _label_left(words, fitz.Rect(vis.x0, vis.y0 - 14, vis.x1, vis.y0))))
    # small squares (checkboxes)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    seen = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (7 * zoom <= w <= 24 * zoom and abs(w - h) <= 3 * zoom):
            continue
        approx = cv2.approxPolyDP(c, 0.08 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or cv2.contourArea(c) < 0.6 * w * h:
            continue
        vis = fitz.Rect(x, y, x + w, y + h) / zoom
        if any(abs(vis & s) > 0.5 * abs(vis) for s in seen):
            continue  # inner/outer edge of the same box
        # a square inside a real word is a letter ('o', 'D'); OCR reads an empty checkbox
        # as a one-character "word" (often 'Ü' or 'O'), so those are kept
        if any(abs(vis & fitz.Rect(wd[:4])) > 0.2 * abs(vis)
               and sum(ch.isalnum() for ch in wd[4]) >= 2 for wd in words):
            continue
        seen.append(vis)
        out.append(("checkbox", vis * page.derotation_matrix, _label_right(words, vis)))
    return out


def detect(page):
    """Suggest fields for a page: [(kind, rect, label)], skipping places that already
    have a field."""
    words = page.get_text("words")  # includes OCR text, so scanned forms get labels
    if page.get_images() and len(page.get_text().strip()) < 400 or not words:
        sugg = _image_suggestions(page, words) + _vector_suggestions(page, words)
    else:
        sugg = _vector_suggestions(page, words)
    taken = [fitz.Rect(w.rect) for w in page.widgets()]
    out = []
    for kind, r, label in sugg:
        r = fitz.Rect(r)
        if r.is_empty or r.width < 6:
            continue
        if any(abs(r & t) > 0.3 * abs(r) for t in taken):
            continue
        taken.append(r)
        out.append((kind, r, label))
    out.sort(key=lambda s: (round(s[1].y0 / 6), s[1].x0))
    return out


def add_detected(doc, pages=None):
    """Detect and create fields on `pages`. Returns the number of fields created."""
    taken = existing_names(doc)
    n = 0
    for i in (range(doc.page_count) if pages is None else pages):
        page = doc[i]
        for kind, r, label in detect(page):
            create_field(page, kind, r, unique_name(doc, label or LABELS[kind], taken))
            n += 1
    return n
