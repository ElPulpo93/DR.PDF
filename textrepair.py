"""Repairs PDFs whose text is "broken": the letters look right on screen, but the file
doesn't say which letter each shape is (typically Type 3 fonts made by Acrobat Distiller /
PScript from printed documents). Such text copies, searches and edits as gibberish.

Each letter shape is drawn and compared with the letters of the common Windows fonts
(Arial, Times New Roman, Calibri, ...). The best match tells both the letter and the font
it came from (including bold and italic). The font then gets
  - a proper letter map (ToUnicode), so the text becomes real text,
  - its real letter widths and a sensible size box, so words and lines are found where
    they really are (these fonts often claim every letter is a whole em wide and many
    lines tall).
What is printed does not change: only letters drawn one at a time get new widths.

repair(doc, pages) -> report   - repairs the fonts used on those pages
broken_pages(doc, pages)       - pages whose text is unreadable
char_styles(page)              - {(x, y): 'Arial-Bold', ...} for text in repaired fonts
"""
import collections
import os
import re
import statistics
from pathlib import Path

import numpy as np
import pymupdf as fitz

try:
    import cv2
except Exception:  # only needed for letters that are squeezed or stretched
    cv2 = None

EM = 48                    # glyph size (pixels) for comparing shapes
CELL_W, CELL_H, BASE, LEFT = 84, 80, 58, 18  # each glyph's cell; baseline and start in it
COLS = 24
SW, SH = 16, 18            # size of the quick first comparison
MIN_SCORE = 0.86           # exact comparison
MIN_SCORE_SCALED = 0.80    # size-free comparison (condensed / expanded fonts)
NW, NH = 20, 28            # size of the size-free comparison
MARK = "DrPDFRepair"       # key in the font dictionary: what was found (per code)

# (style name the editor understands, file in C:\Windows\Fonts)
FONT_FILES = [
    ("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf"), ("Arial-Italic", "ariali.ttf"),
    ("Arial-BoldItalic", "arialbi.ttf"),
    ("TimesNewRoman", "times.ttf"), ("TimesNewRoman-Bold", "timesbd.ttf"),
    ("TimesNewRoman-Italic", "timesi.ttf"), ("TimesNewRoman-BoldItalic", "timesbi.ttf"),
    ("Calibri", "calibri.ttf"), ("Calibri-Bold", "calibrib.ttf"), ("Calibri-Italic", "calibrii.ttf"),
    ("Calibri-BoldItalic", "calibriz.ttf"),
    ("CourierNew", "cour.ttf"), ("CourierNew-Bold", "courbd.ttf"),
    ("Verdana", "verdana.ttf"), ("Verdana-Bold", "verdanab.ttf"),
    ("Tahoma", "tahoma.ttf"), ("Tahoma-Bold", "tahomabd.ttf"),
    ("Georgia", "georgia.ttf"), ("Georgia-Bold", "georgiab.ttf"),
    ("Cambria", "cambria.ttc"), ("Cambria-Bold", "cambriab.ttf"),
    ("SegoeUI", "segoeui.ttf"), ("SegoeUI-Bold", "segoeuib.ttf"),
    ("CenturyGothic", "GOTHIC.TTF"), ("CenturyGothic-Bold", "GOTHICB.TTF"),
    ("Symbol", "symbol.ttf"), ("Wingdings", "wingding.ttf"),
]
SYMBOL_FONTS = {"Symbol", "Wingdings"}
CHARSET = ([chr(c) for c in range(33, 127)] +
           list("•–—“”‘’©®°±µ§¶·×÷≤≥≈≠∞αβγδεθλμπσφωΔΣΩ€£¥¢…‰†‡™¼½¾²³¹ºª¿¡«»"
                "äöüßÄÖÜéèêëáàâãåçñíìîïóòôõúùûýÿÉÈÊËÁÀÂÃÅÇÑÍÌÎÏÓÒÔÕÚÙÛÝøØæÆœŒ"
                "●○■□▪▫◦✓✔✗✘➢➤►▶◄▲▼→←↑↓⇒−"))
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

_cands = None              # the letters of the Windows fonts, drawn (see _candidates)


def family_of(style):
    return (style or "").split("-")[0]


def _bad(u):
    return u < 32 or u == 0xFFFD or 0xE000 <= u <= 0xF8FF


def _ink_box(a):
    ys, xs = np.nonzero(a > 0.25)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _cells(pix, n):
    a = 1.0 - np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) / 255.0
    out = []
    for k in range(n):
        r, c = divmod(k, COLS)
        out.append(a[r * CELL_H:(r + 1) * CELL_H, c * CELL_W:(c + 1) * CELL_W].astype(np.float32))
    return out


def _small(a):
    if cv2 is not None:
        return cv2.resize(a, (SW, SH), interpolation=cv2.INTER_AREA).reshape(-1)
    return a[::CELL_H // SH, ::CELL_W // SW][:SH, :SW].reshape(-1)


def _normalized(a, box):
    """The letter's ink stretched to a fixed size (so a squeezed 'A' still looks like 'A')."""
    x0, y0, x1, y1 = box
    crop = a[y0:y1 + 1, x0:x1 + 1]
    if cv2 is not None:
        return cv2.resize(crop, (NW, NH), interpolation=cv2.INTER_AREA)
    ys = (np.arange(NH) * crop.shape[0] / NH).astype(int)
    xs = (np.arange(NW) * crop.shape[1] / NW).astype(int)
    return crop[ys][:, xs]


def _candidates():
    """Every letter of every Windows font in CHARSET, drawn the same way as the PDF's."""
    global _cands
    if _cands is not None:
        return _cands
    imgs, labels, advances = [], [], []
    for style, fname in FONT_FILES:
        path = FONT_DIR / fname
        if not path.exists():
            continue
        try:
            font = fitz.Font(fontfile=str(path))
        except Exception:
            continue
        chars = [ch for ch in CHARSET if font.has_glyph(ord(ch))]
        if not chars:
            continue
        rows = (len(chars) + COLS - 1) // COLS
        d = fitz.open()
        p = d.new_page(width=COLS * CELL_W, height=rows * CELL_H)
        tw = fitz.TextWriter(p.rect)
        for k, ch in enumerate(chars):
            r, c = divmod(k, COLS)
            tw.append((c * CELL_W + LEFT, r * CELL_H + BASE), ch, font=font, fontsize=EM)
        tw.write_text(p)
        pix = p.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
        for ch, a in zip(chars, _cells(pix, len(chars))):
            if a.sum() > 1:
                imgs.append(a)
                labels.append((ch, style))
                advances.append(font.text_length(ch, 1))
    boxes = np.array([_ink_box(a) for a in imgs], dtype=np.float32)
    _cands = {
        "imgs": np.stack(imgs), "labels": labels, "boxes": boxes,
        "small": np.stack([_small(a) for a in imgs]),
        "norm": np.stack([_normalized(a, tuple(int(v) for v in b)) for a, b in zip(imgs, boxes)]),
        "families": np.array([family_of(s) for _, s in labels]),
        "adv": np.array(advances, dtype=np.float32),
    }
    return _cands


def _iou(sub, b, axes):
    return np.minimum(sub, b).sum(axis=axes) / (np.maximum(sub, b).sum(axis=axes) + 1e-6)


def _identify(a, allowed=None):
    """(letter, style, score) for one glyph image; (' ', None, 1) for an empty glyph and
    (None, None, score) when no letter matches well enough."""
    box = _ink_box(a)
    if box is None:
        return " ", None, 1.0
    C = _candidates()
    ok = np.abs(C["boxes"] - np.array(box, dtype=np.float32)).max(axis=1) <= 3
    if allowed is not None:
        ok &= allowed
    best = (None, None, 0.0)
    idx = np.nonzero(ok)[0]
    if len(idx):
        # quick comparison on small pictures, then an exact one for the closest few
        quick = _iou(C["small"][idx], _small(a), 1)
        top = idx[np.argsort(-quick)[:12]]
        sub = C["imgs"][top]
        score = np.zeros(len(top), dtype=np.float32)
        for dy in (-1, 0, 1):          # the same letter can land a pixel apart
            for dx in (-1, 0, 1):
                score = np.maximum(score, _iou(sub, np.roll(np.roll(a, dy, 0), dx, 1), (1, 2)))
        k = int(score.argmax())
        best = (*C["labels"][top[k]], float(score[k]))
        # small marks (quotes, commas, dots) lose a lot from a single pixel: accept one
        # when nothing else comes close
        # ...and within a known typeface (a slightly different version of it, say), a
        # letter that is clearly closer than any other letter is taken too
        x0, y0, x1, y1 = box
        small = (x1 - x0 + 1) * (y1 - y0 + 1) <= 120
        if best[2] < MIN_SCORE and best[2] >= (0.68 if small else 0.70) and \
                (small or allowed is not None):
            others = [s for j, s in enumerate(score) if C["labels"][top[j]][0] != best[0]]
            if not others or best[2] - max(others) >= (0.1 if small else 0.08):
                return best
    if best[2] >= MIN_SCORE:
        return best
    # size-free comparison: same height and place on the line, any width
    x0, y0, x1, y1 = box
    w = x1 - x0 + 1
    cb = C["boxes"]
    cw = cb[:, 2] - cb[:, 0] + 1
    ok2 = (np.abs(cb[:, 1] - y0) <= 2) & (np.abs(cb[:, 3] - y1) <= 2) & \
        (w / cw >= 0.55) & (w / cw <= 1.45)
    if allowed is not None:
        ok2 &= allowed
    idx = np.nonzero(ok2)[0]
    if len(idx):
        score = _iou(C["norm"][idx], _normalized(a, box), (1, 2))
        k = int(score.argmax())
        if score[k] >= MIN_SCORE_SCALED and score[k] > best[2]:
            return (*C["labels"][idx[k]], float(score[k]))
    return None, None, best[2]


def _ranked(a, allowed=None, n=5):
    """The closest few distinct letters for a glyph picture: [(letter, style, score)]."""
    box = _ink_box(a)
    if box is None:
        return []
    C = _candidates()
    ok = np.abs(C["boxes"] - np.array(box, dtype=np.float32)).max(axis=1) <= 4
    if allowed is not None:
        ok &= allowed
    idx = np.nonzero(ok)[0]
    if not len(idx):
        return []
    quick = _iou(C["small"][idx], _small(a), 1)
    top = idx[np.argsort(-quick)[:24]]
    sub = C["imgs"][top]
    score = np.zeros(len(top), dtype=np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            score = np.maximum(score, _iou(sub, np.roll(np.roll(a, dy, 0), dx, 1), (1, 2)))
    out, seen = [], set()
    for j in np.argsort(-score):
        ch, style = C["labels"][top[j]]
        if ch not in seen:
            seen.add(ch)
            out.append((ch, style, float(score[j])))
        if len(out) >= n:
            break
    return out


def _stretch(a, sx):
    """Undo a horizontal squeeze sx (glyph width / normal width) around the letter's start."""
    if cv2 is None or abs(sx - 1) < 1e-3:
        return a
    m = np.float32([[1 / sx, 0, LEFT - LEFT / sx], [0, 1, 0]])
    return cv2.warpAffine(a, m, (a.shape[1], a.shape[0]), flags=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- which text is broken
def _strip(name):
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def _page_fonts(page):
    """font name as text extraction reports it -> font xref (Type 3 fonts: their glyph
    numbers are their codes, so each letter can be drawn again exactly)."""
    out = {}
    for xref, ext, kind, base, ref, enc in [f[:6] for f in page.get_fonts()]:
        if kind != "Type3":
            continue
        for n in (base, ref):
            if n:
                out.setdefault(_strip(n), xref)
    return out


def _glyph_names(doc, xref):
    """{code: glyph name} from the font's /Encoding /Differences."""
    kind, val = doc.xref_get_key(xref, "Encoding")
    obj = doc.xref_object(int(val.split()[0])) if kind == "xref" else (val if kind == "dict" else "")
    m = re.search(r"/Differences\s*\[(.*?)\]", obj, re.S)
    out = {}
    if m:
        code = 0
        for tok in re.findall(r"\d+|/[^\s/\[\]()<>]+", m.group(1)):
            if tok.startswith("/"):
                out[code] = tok[1:]
                code += 1
            else:
                code = int(tok)
    return out


def _names_meaningless(doc, xref, codes):
    """No letter map, and glyph names such as /12 or /g7 that say nothing about the letter."""
    if doc.xref_get_key(xref, "ToUnicode")[0] != "null" and not _read_mark(doc, xref):
        return False
    names = _glyph_names(doc, xref)
    used = [names.get(c) for c in codes]
    unknown = sum(1 for n in used if not n or fitz.glyph_name_to_unicode(n) == 0xFFFD)
    return bool(used) and unknown >= 0.5 * len(used)


def _font_usage(page):
    """{font xref: {codes used}} for fonts on this page whose text is unreadable."""
    fonts = _page_fonts(page)
    if not fonts:
        return {}
    stats = {}
    try:
        trace = page.get_texttrace()
    except Exception:
        return {}
    for span in trace:
        xref = fonts.get(_strip(span.get("font")))
        if xref is None:
            continue
        st = stats.setdefault(xref, [0, 0, set()])
        for ch in span["chars"]:
            u, gid = ch[0], ch[1]
            st[0] += 1
            st[1] += _bad(u)
            if 0 <= gid < 256:
                st[2].add(gid)
    doc = page.parent
    out = {}
    for x, (n, bad, codes) in stats.items():
        if n < 3:
            continue
        mark = _read_mark(doc, x)
        if mark:
            if not mark.keys() >= codes and "tried" not in doc.xref_get_key(x, MARK + "Done")[1]:
                out[x] = codes  # repaired before, but some letters on this page are new
        elif bad >= 0.3 * n or _names_meaningless(doc, x, codes):
            out[x] = codes
    return out


def broken_pages(doc, pages=None):
    pages = range(doc.page_count) if pages is None else pages
    return [i for i in pages if _font_usage(doc[i])]


# ---------------------------------------------------------------- reading the page's drawing
_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
        ord("("): 40, ord(")"): 41, ord("\\"): 92}
_TOKENS = re.compile(rb"/([^\s/\[\]()<>{}%]+)\s+[-\d.]+\s+Tf|\(((?:[^()\\]|\\.)*)\)\s*Tj"
                     rb"(?:\s+(-?[\d.]+)\s+(-?[\d.]+)\s+T[Dd])?|\[((?:[^\]\\]|\\.)*)\]\s*TJ",
                     re.S)


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == 92 and i + 1 < len(s):
            m = re.match(rb"\\([0-7]{1,3})", s[i:])
            if m:
                out.append(int(m.group(1), 8) & 255)
                i += len(m.group(0))
                continue
            if s[i + 1] in _ESC:
                out.append(_ESC[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return out


def _advances(doc, pages, names):
    """How far the page moves on after each letter of the fonts named in `names`
    ({resource name: xref}). Returns {xref: {code: advance in em}} and
    {xref: {codes whose width the drawing relies on}}."""
    adv = collections.defaultdict(lambda: collections.defaultdict(list))
    keep = collections.defaultdict(set)
    for i in pages:
        try:
            data = doc[i].read_contents()
        except Exception:
            continue
        font = None
        for m in _TOKENS.finditer(data):
            if m.group(1) is not None:
                font = names.get(i, {}).get(m.group(1).decode("latin-1"))
                continue
            if font is None:
                continue
            if m.group(5) is not None:  # [...] TJ: every letter but the last one relies
                for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)", m.group(5)):
                    keep[font].update(_unescape(s))
                continue
            codes = _unescape(m.group(2))
            if len(codes) > 1:
                keep[font].update(codes[:-1])
            elif len(codes) == 1 and m.group(3) is not None and abs(float(m.group(4))) < 1e-6:
                adv[font][codes[0]].append(float(m.group(3)))
    med = {x: {c: statistics.median(v) for c, v in d.items() if v} for x, d in adv.items()}
    return med, keep


# ---------------------------------------------------------------- drawing the PDF's glyphs
def _render_codes(doc, scratch, xref, codes):
    """Draw the given codes of one font in a grid on the scratch page (see repair)."""
    page = doc[scratch]
    codes = sorted(codes)
    rows = (len(codes) + COLS - 1) // COLS
    w, h = COLS * CELL_W, rows * CELL_H
    page.set_mediabox(fitz.Rect(0, 0, w, h))
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /DrF {xref} 0 R >> >>")
    ops = ["0 g 0 Tr"]
    for k, code in enumerate(codes):
        r, c = divmod(k, COLS)
        ops.append(f"BT /DrF {EM} Tf 1 0 0 1 {c * CELL_W + LEFT} {h - (r * CELL_H + BASE)} Tm "
                   f"<{code:02x}> Tj ET")
    stream = "\n".join(ops).encode()
    xs = page.get_contents()
    if xs:
        doc.update_stream(xs[0], stream)
    else:
        x = doc.get_new_xref()
        doc.update_object(x, "<<>>")
        doc.update_stream(x, stream)
        doc.xref_set_key(page.xref, "Contents", f"{x} 0 R")
    pix = doc[scratch].get_pixmap(colorspace=fitz.csGRAY, alpha=False)
    return dict(zip(codes, _cells(pix, len(codes))))


# ---------------------------------------------------------------- one font
def _identify_font(imgs, adv, seen):
    """{code: (letter, style)} for one font's glyph pictures. The font is first read
    letter by letter; then everything is read again knowing which typeface it is (and
    how much it is squeezed), which settles look-alikes such as l / I or Arial / Verdana."""
    C = _candidates()
    first = {}
    for code, a in imgs.items():
        key = (a > 0.5).tobytes()
        if key not in seen:
            seen[key] = _identify(a)
        first[code] = seen[key]
    votes = collections.Counter(family_of(s) for ch, s, sc in first.values()
                                if s and sc >= 0.84 and family_of(s) not in SYMBOL_FONTS)
    if not votes:
        return {c: (ch, s) for c, (ch, s, sc) in first.items() if ch}, {}
    family = votes.most_common(1)[0][0]
    allowed = np.isin(C["families"], [family] + sorted(SYMBOL_FONTS))
    # squeeze: how wide the font's broad letters are against the same letters of the
    # typeface (narrow letters such as i or l say little about it)
    ratios = []
    for code, (ch, s, sc) in first.items():
        if s and family_of(s) == family and ch.strip():
            box = _ink_box(imgs[code])
            cb = C["boxes"][C["labels"].index((ch, s))]
            cw = cb[2] - cb[0] + 1
            if box and cw >= 12:
                ratios.append((box[2] - box[0] + 1) / cw)
    # one font can hold normal and squeezed copies of letters: the usual squeezes found
    squeezes = [1.0]
    for part in ([r for r in ratios if r < 0.95], [r for r in ratios if r > 1.05]):
        if len(part) >= 3:
            m = statistics.median(part)
            if 0.6 <= m <= 1.5:
                squeezes += [m - 0.02, m, m + 0.02]
    out, unsure = {}, {}
    for code, a in imgs.items():
        ch, s, sc = first[code]
        if ch == " ":
            out[code] = (" ", None)
            continue
        if ch is not None and sc >= 0.95 and family_of(s) in (family, *SYMBOL_FONTS):
            out[code] = (ch, s)  # a clear match already
            continue
        best_sx = 1.0
        for sx in squeezes:
            b = _stretch(a, sx) if abs(sx - 1) > 1e-3 else a
            ch2, s2, sc2 = _identify(b, allowed)
            if ch2 is not None and (ch is None or sc2 > sc or
                                    (family_of(s) != family and sc2 >= sc - 0.03)):
                ch, s, sc, best_sx = ch2, s2, sc2, sx
        if ch is None:
            # a letter from another typeface mixed into the font: any typeface, any of
            # the usual squeezes, as long as one letter is clearly closest
            for sx in squeezes:
                b = _stretch(a, sx) if abs(sx - 1) > 1e-3 else a
                r = _ranked(b, None, 2)
                if r and r[0][2] >= 0.6 and (len(r) == 1 or r[0][2] - r[1][2] >= 0.1) and \
                        (ch is None or r[0][2] > sc):
                    ch, s, sc, best_sx = r[0][0], r[0][1], r[0][2], sx
        if ch is not None:
            out[code] = (ch, s)
        if ch is None or sc < MIN_SCORE:
            # not certain: the words it appears in decide later (see settle_unsure)
            b = _stretch(a, best_sx) if abs(best_sx - 1) > 1e-3 else a
            cands = [(c2, s2) for c2, s2, sc2 in _ranked(b, allowed) if sc2 >= 0.45]
            if ch is not None and (ch, s) not in cands:
                cands.insert(0, (ch, s))
            if cands:
                unsure[code] = cands[:5]
    return out, unsure


LOOKALIKES = {"I", "l", "|"}  # the same upright bar in many sans-serif fonts
UNKNOWN = "\u25a1"            # a box, for a letter that couldn't be identified


def _read_unsure(doc, xref):
    kind, val = doc.xref_get_key(xref, MARK + "Unsure")
    out = {}
    if kind == "string":
        for item in val.split():
            code, _, rest = item.partition("=")
            try:
                out[int(code, 16)] = [(chr(int(c, 16)), s or None) for c, _, s in
                                      (x.partition(":") for x in rest.split(","))]
            except ValueError:
                pass
    return out


def _write_unsure(doc, xref, unsure):
    if not unsure:
        doc.xref_set_key(xref, MARK + "Unsure", "null")
        return
    note = " ".join(f"{c:02X}=" + ",".join(f"{ord(ch):04X}:{s or ''}" for ch, s in cands)
                    for c, cands in sorted(unsure.items()))
    doc.xref_set_key(xref, MARK + "Unsure", fitz.get_pdf_str(note))


def settle_unsure(doc, progress=None):
    """Letters that weren't certain are settled by the words they are in: the reading that
    turns those words into words found elsewhere in the document wins (for example with
    'Inv?stigational', ? = e gives a word used many times in the document, ? = c gives one
    used nowhere). Returns how many letters changed."""
    fonts = {}           # xref -> unsure dict
    for i in range(doc.page_count):
        for f in doc[i].get_fonts():
            if f[0] not in fonts and f[2] == "Type3":
                u = _read_unsure(doc, f[0])
                if u:
                    fonts[f[0]] = u
    if not fonts:
        return 0
    # every word in the document as (font, code, letter) sequences, and the vocabulary of
    # words made only of certain letters
    words = []
    for i in range(doc.page_count):
        page = doc[i]
        names = _page_fonts(page)
        try:
            trace = page.get_texttrace()
        except Exception:
            continue
        for span in trace:
            x = names.get(_strip(span.get("font")))
            cur = []
            for ch in span["chars"]:
                u = chr(ch[0]) if 0 < ch[0] < 0x110000 else " "
                if not (u.isalnum() or (x in fonts and ch[1] in fonts[x])):
                    if cur:
                        words.append(cur)
                    cur = []
                    continue
                cur.append((x, ch[1], u))
            if cur:
                words.append(cur)
        if progress:
            progress(i + 1, doc.page_count)
    vocab = collections.Counter()
    for w in words:
        if not any(x in fonts and c in fonts[x] for x, c, _ in w):
            vocab["".join(u for _, _, u in w).lower()] += 1
    changed = 0
    for x, unsure in fonts.items():
        found = _read_mark(doc, x)
        for code, cands in unsure.items():
            uses = [w for w in words if any(wx == x and wc == code for wx, wc, _ in w)]
            best, best_hits = None, 0
            for ch, style in cands:
                hits = 0
                for w in uses:
                    word = "".join(ch if (wx == x and wc == code) else u for wx, wc, u in w)
                    hits += vocab[word.lower()] > 0
                if hits > best_hits:
                    best, best_hits = (ch, style), hits
            if best is not None and found.get(code) != best:
                found[code] = best
                changed += 1
        _write_fix(doc, x, found, {}, set())
        _write_unsure(doc, x, {})
    return changed


def _settle_lookalikes(found, runs):
    """Decide I / l by the letters around them: a bar between lower-case letters is an 'l',
    one next to capitals (or alone) is an 'I'. runs: lists of codes as printed."""
    votes = collections.defaultdict(collections.Counter)
    for codes in runs:
        text = [found.get(c, (None, None))[0] for c in codes]
        for k, c in enumerate(codes):
            if text[k] not in LOOKALIKES:
                continue
            prev = text[k - 1] if k > 0 else " "
            nxt = text[k + 1] if k + 1 < len(text) else " "
            prev = " " if prev is None else prev
            nxt = " " if nxt is None else nxt
            if prev in LOOKALIKES or nxt in LOOKALIKES:
                p2 = text[k - 2] if k > 1 and text[k - 2] else " "
                n2 = text[k + 2] if k + 2 < len(text) and text[k + 2] else " "
                prev = p2 if prev in LOOKALIKES else prev
                nxt = n2 if nxt in LOOKALIKES else nxt
            if prev.islower():
                votes[c]["l"] += 2
            elif prev.isupper():
                votes[c]["I"] += 2
            elif nxt.isupper() or not nxt.isalpha():
                votes[c]["I"] += 1
            elif nxt in "aeiouy":       # la, le, li, lo, lu, ly
                votes[c]["l"] += 1
            else:                       # In, It, If, Is
                votes[c]["I"] += 1
    for c, v in votes.items():
        ch, style = found[c]
        best = v.most_common(1)[0][0]
        if best != ch:
            found[c] = (best, style)


# ---------------------------------------------------------------- writing the fix
def _read_mark(doc, xref):
    kind, val = doc.xref_get_key(xref, MARK)
    out = {}
    if kind == "string":
        for item in val.split():
            code, _, rest = item.partition("=")
            ch, _, style = rest.partition(":")
            try:
                out[int(code, 16)] = (chr(int(ch, 16)), style or None)
            except ValueError:
                pass
    return out


def _numbers(doc, xref, key):
    return [float(v) for v in re.findall(r"-?[\d.]+(?:e-?\d+)?", doc.xref_get_key(xref, key)[1])]


def _write_fix(doc, xref, found, adv, keep):
    """ToUnicode + widths + FontBBox, and a note of what was found."""
    entries = sorted((c, ch) for c, (ch, _) in found.items() if ch)
    blocks = []
    for k in range(0, len(entries), 100):
        part = entries[k:k + 100]
        body = "\n".join(f"<{c:02X}> <{''.join(f'{b:02X}' for b in ch.encode('utf-16-be'))}>"
                         for c, ch in part)
        blocks.append(f"{len(part)} beginbfchar\n{body}\nendbfchar")
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /DrPDF-Repaired def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n" + "\n".join(blocks) +
            "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    kind, val = doc.xref_get_key(xref, "ToUnicode")
    if kind == "xref":
        tu = int(val.split()[0])
    else:
        tu = doc.get_new_xref()
        doc.update_object(tu, "<<>>")
        doc.xref_set_key(xref, "ToUnicode", f"{tu} 0 R")
    doc.update_stream(tu, cmap.encode("ascii"))
    note = " ".join(f"{c:02X}={ord(ch):04X}:{style or ''}" for c, (ch, style) in sorted(found.items())
                    if ch)
    doc.xref_set_key(xref, MARK, fitz.get_pdf_str(note))
    fm = _numbers(doc, xref, "FontMatrix")
    if len(fm) != 6 or not fm[0] or not fm[3]:
        return
    # widths: these fonts often say every letter is 1 em wide, so the text seems to step
    # back after each narrow letter (and splits into odd lines). Letters drawn one at a
    # time get their real width; the drawing does not depend on it.
    widths = _numbers(doc, xref, "Widths")
    first = int((_numbers(doc, xref, "FirstChar") or [0])[0])
    if widths and adv:
        changed = False
        for code, em in adv.items():
            k = code - first
            if 0 <= k < len(widths) and code not in keep and em > 0:
                widths[k] = em / fm[0]
                changed = True
        if changed:
            doc.xref_set_key(xref, "Widths", "[%s]" % " ".join(f"{w:.5g}" for w in widths))
    # size box: often claimed many lines tall, so every line seems to cover its neighbours
    bb = _numbers(doc, xref, "FontBBox")
    if len(bb) == 4:
        height_em = abs((bb[3] - bb[1]) * fm[3])
        if height_em > 2.5 or height_em < 0.3:
            a, d = fm[0], fm[3]
            new = [-0.1 / a, -0.22 / d, 1.1 / a, 0.92 / d]
            doc.xref_set_key(xref, "FontBBox", "[%s]" % " ".join(f"{v:g}" for v in new))


# ---------------------------------------------------------------- the whole job
def repair(doc, pages=None, progress=None):
    """Repair the unreadable fonts used on `pages` (all pages if None).
    progress(done, total) is called now and then. Returns a report dict."""
    pages = list(range(doc.page_count)) if pages is None else list(pages)
    need = {}            # font xref -> codes used
    names = {}           # page -> {resource name: font xref}
    for i in pages:
        use = _font_usage(doc[i])
        if not use:
            continue
        for xref, codes in use.items():
            need.setdefault(xref, set()).update(codes)
        names[i] = {f[4]: f[0] for f in doc[i].get_fonts() if f[0] in use}
    report = {"fonts": 0, "letters": 0, "unknown": 0, "pages": sorted(names), "styles": {}}
    if not need:
        return report
    _candidates()
    adv, keep = _advances(doc, sorted(names), names)
    runs = collections.defaultdict(list)  # font xref -> printed code sequences
    for i, nm in names.items():
        by_name = {_strip(n): x for n, x in nm.items()}
        by_name.update({k: v for k, v in _page_fonts(doc[i]).items() if v in need})
        try:
            for span in doc[i].get_texttrace():
                x = by_name.get(_strip(span.get("font")))
                if x is not None:
                    runs[x].append([ch[1] for ch in span["chars"]])
        except Exception:
            pass
    seen = {}  # glyph picture -> first answer (the same letters repeat in many fonts)
    scratch = doc.page_count  # a scratch page to draw on; removed again below
    doc.new_page(-1, width=100, height=100)
    try:
        for n, (xref, codes) in enumerate(need.items()):
            found = _read_mark(doc, xref)
            todo = sorted(c for c in codes if c not in found)
            if todo:
                imgs = _render_codes(doc, scratch, xref, todo)
                got, unsure = _identify_font(imgs, adv.get(xref, {}), seen)
                report["letters"] += len(got)
                report["unknown"] += len(todo) - len(got)
                for ch, s in got.values():
                    if s:
                        report["styles"][s] = report["styles"].get(s, 0) + 1
                found.update(got)
                _settle_lookalikes(found, runs.get(xref, []))
                # a letter that couldn't be read shows as a box rather than as some other
                # character that would look like a real (wrong) letter
                for c in todo:
                    if c not in found:
                        found[c] = (UNKNOWN, None)
                if unsure:
                    old = _read_unsure(doc, xref)
                    old.update(unsure)
                    _write_unsure(doc, xref, old)
                if len(got) < len(todo):  # don't try the same letters again on every open
                    doc.xref_set_key(xref, MARK + "Done", "/tried")
            if found:
                _write_fix(doc, xref, found, adv.get(xref, {}), keep.get(xref, set()))
                report["fonts"] += 1
            if progress:
                progress(n + 1, len(need))
    finally:
        doc.delete_page(scratch)
    return report


def char_styles(page):
    """For text in repaired fonts: {(x, y) of each letter: style such as 'Arial-Bold'}."""
    doc = page.parent
    marks = {}
    for name, xref in _page_fonts(page).items():
        m = _read_mark(doc, xref)
        if m:
            marks[name] = m
    if not marks:
        return {}
    out = {}
    try:
        trace = page.get_texttrace()
    except Exception:
        return {}
    for span in trace:
        m = marks.get(_strip(span.get("font")))
        if not m:
            continue
        for ch in span["chars"]:
            info = m.get(ch[1])
            if info and info[1]:
                out[(round(ch[2][0], 1), round(ch[2][1], 1))] = info[1]
    return out
