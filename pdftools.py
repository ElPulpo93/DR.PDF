"""Whole-document PDF tools (Acrobat-style): page numbers, headers & footers, watermarks,
splitting, cropping, flattening and stamps. No user interface here, so it can be tested
on its own; the editor calls these and handles undo."""
import datetime
import math
import re
from pathlib import Path

import pymupdf as fitz

import fonts

POSITIONS = ["Top left", "Top center", "Top right",
             "Bottom left", "Bottom center", "Bottom right"]

STAMPS = [  # label, PyMuPDF stamp id
    ("Approved", fitz.STAMP_Approved), ("Not Approved", fitz.STAMP_NotApproved),
    ("Draft", fitz.STAMP_Draft), ("Final", fitz.STAMP_Final),
    ("Confidential", fitz.STAMP_Confidential), ("For Comment", fitz.STAMP_ForComment),
    ("As Is", fitz.STAMP_AsIs), ("Departmental", fitz.STAMP_Departmental),
    ("Experimental", fitz.STAMP_Experimental), ("Expired", fitz.STAMP_Expired),
    ("For Public Release", fitz.STAMP_ForPublicRelease),
    ("Not For Public Release", fitz.STAMP_NotForPublicRelease),
    ("Sold", fitz.STAMP_Sold), ("Top Secret", fitz.STAMP_TopSecret),
]


def parse_pages(text, count):
    """'1-3, 5, 8-' -> sorted 0-based page indexes. Empty or 'all' means every page."""
    text = (text or "").strip().lower()
    if text in ("", "all"):
        return list(range(count))
    pages = set()
    for part in re.split(r"[,;\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else count
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"'{part}' isn't a page number or range")
        if a < 1 or b > count or a > b:
            raise ValueError(f"'{part}' is outside pages 1-{count}")
        pages.update(range(a - 1, b))
    return sorted(pages)


def _visible_to_page(page, pt):
    """Point on the page as the reader sees it -> PDF page coordinates."""
    return fitz.Point(pt) * page.derotation_matrix


def _write_line(page, visible_origin, text, family, size, color, opacity=1.0):
    """Write one upright line of text at a point in visible (on-screen) coordinates."""
    name, path, font = fonts.font_object(family)
    kw = {"fontname": name, "fontsize": size, "color": color, "rotate": page.rotation,
          "fill_opacity": opacity}
    if path:
        kw["fontfile"] = path
    page.insert_text(_visible_to_page(page, visible_origin), text, **kw)
    if path:
        fonts.fix_space_mapping(page, name, font)
    return font


def format_template(template, page_no, total, filename=""):
    now = datetime.date.today()
    return (template.replace("{page}", str(page_no)).replace("{total}", str(total))
            .replace("{date}", now.strftime("%m/%d/%Y")).replace("{file}", filename))


def add_header_footer(doc, template, position="Bottom center", pages=None, family="Arial",
                      size=10, color=(0, 0, 0), margin=36, start=1, filename=""):
    """Write text such as 'Page {page} of {total}' on each page. Returns pages changed."""
    pages = list(range(doc.page_count)) if pages is None else pages
    total = start - 1 + len(pages)
    _, _, font = fonts.font_object(family)
    for n, i in enumerate(pages):
        page = doc[i]
        text = format_template(template, start + n, total, filename)
        w = font.text_length(text, size)
        r = page.rect  # the page as seen on screen
        vpos, hpos = position.lower().split()
        x = {"left": margin, "center": (r.width - w) / 2, "right": r.width - margin - w}[hpos]
        y = margin + size * 0.8 if vpos == "top" else r.height - margin
        _write_line(page, (x, y), text, family, size, color)
    return len(pages)


def add_watermark(doc, text, pages=None, family="Arial", size=0, color=(0.6, 0.6, 0.6),
                  opacity=0.3, angle=45, behind=False):
    """Text across the middle of each page, rising at `angle` degrees as the reader sees
    it. size=0 fits it to the page."""
    pages = list(range(doc.page_count)) if pages is None else pages
    name, path, font = fonts.font_object(family)
    a = math.radians(angle)
    for i in pages:
        page = doc[i]
        vis = page.rect
        fs = size
        if not fs:  # stretch across ~75% of the page in the chosen direction
            unit = max(font.text_length(text, 1), 1e-3)
            reach = min(vis.width / max(abs(math.cos(a)), 1e-3),
                        vis.height / max(abs(math.sin(a)), 1e-3))
            fs = max(8, min(reach * 0.75 / unit, vis.height * 0.3))
        w = font.text_length(text, fs)
        centre = fitz.Point(vis.width / 2, vis.height / 2) * page.derotation_matrix
        # the wanted on-screen direction, expressed in the page's own coordinates
        lin = fitz.Matrix(page.derotation_matrix)
        lin.e = lin.f = 0
        u = fitz.Point(math.cos(a), -math.sin(a)) * lin
        deg = math.degrees(math.atan2(u.y, u.x))
        kw = {"fontname": name, "fontsize": fs, "color": color, "fill_opacity": opacity,
              "overlay": not behind, "morph": (centre, fitz.Matrix(-deg))}
        if path:
            kw["fontfile"] = path
        page.insert_text(fitz.Point(centre.x - w / 2, centre.y + fs * 0.35), text, **kw)
        if path:
            fonts.fix_space_mapping(page, name, font)
    return len(pages)


def split_pdf(doc, out_dir, stem, mode="every", every=1, ranges="", overwrite=False):
    """Write parts of doc as separate PDFs. Returns the list of written files.

    mode: 'every' (every N pages), 'ranges' ('1-3, 4-9'), 'bookmarks' (top-level bookmarks)."""
    n = doc.page_count
    groups = []
    if mode == "every":
        every = max(1, int(every))
        groups = [list(range(a, min(a + every, n))) for a in range(0, n, every)]
    elif mode == "ranges":
        for part in re.split(r"[,;]+", ranges):
            if part.strip():
                groups.append(parse_pages(part, n))
    elif mode == "bookmarks":
        tops = [(t[1], t[2] - 1) for t in doc.get_toc() if t[0] == 1 and t[2] >= 1]
        if not tops:
            raise ValueError("This PDF has no bookmarks to split by.")
        tops.sort(key=lambda t: t[1])
        if tops[0][1] > 0:
            tops.insert(0, ("Start", 0))
        for k, (title, first) in enumerate(tops):
            last = tops[k + 1][1] - 1 if k + 1 < len(tops) else n - 1
            if last >= first:
                groups.append((title, list(range(first, last + 1))))
    if not groups:
        raise ValueError("Nothing to split.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for k, g in enumerate(groups, 1):
        title, pages = g if isinstance(g, tuple) else (None, g)
        if title:
            safe = re.sub(r'[<>:"/\\|?*\s]+', " ", title).strip()[:60] or f"part {k}"
            name = f"{stem} - {k:02d} {safe}.pdf"
        elif len(pages) == 1:
            name = f"{stem} - page {pages[0] + 1}.pdf"
        else:
            name = f"{stem} - pages {pages[0] + 1}-{pages[-1] + 1}.pdf"
        path = out_dir / name
        if path.exists() and not overwrite:
            i = 1
            while (out_dir / f"{path.stem} ({i}).pdf").exists():
                i += 1
            path = out_dir / f"{path.stem} ({i}).pdf"
        part = fitz.open()
        for i in pages:
            part.insert_pdf(doc, from_page=i, to_page=i)
        part.save(path, garbage=3, deflate=True)
        part.close()
        written.append(path)
    return written


def crop_pages(doc, rect, pages):
    """Crop pages to `rect`, given in the (unrotated) coordinates of the first page.

    Each page keeps the part inside its own media box."""
    done = 0
    for i in pages:
        page = doc[i]
        off = page.cropbox_position
        r = fitz.Rect(rect) + (off.x, off.y, off.x, off.y)
        r &= page.mediabox
        if r.is_empty or r.width < 10 or r.height < 10:
            continue
        page.set_cropbox(r)
        done += 1
    return done


def uncrop_pages(doc, pages):
    for i in pages:
        doc[i].set_cropbox(doc[i].mediabox)
    return len(pages)


def flatten(doc):
    """Bake comments and form fields into the page content (they can't be changed after)."""
    doc.bake(annots=True, widgets=True)


def add_stamp(page, rect, stamp_id):
    a = page.add_stamp_annot(rect, stamp=stamp_id)
    a.set_info(title=_user_name())
    a.update()
    return a


def _user_name():
    import os
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""
