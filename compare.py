"""Compare two versions of a PDF and build a report: a summary page, then each pair of
pages side by side with removed text marked red (old) and added text green (new)."""
import difflib
from pathlib import Path

import pymupdf as fitz

RED = (0.92, 0.25, 0.3)
GREEN = (0.15, 0.7, 0.35)


def _words(doc):
    """Every word in reading order: (page, text, rect)."""
    out = []
    for i, page in enumerate(doc):
        for w in page.get_text("words", sort=True):
            out.append((i, w[4], fitz.Rect(w[:4])))
    return out


def diff(old, new):
    """Word-level differences. Returns (removed, added, changed_pages) where removed/added
    are lists of (page, text, rect) and changed_pages is a sorted list of page numbers
    (of the new document, or the old one for removals past its end)."""
    a, b = _words(old), _words(new)
    sm = difflib.SequenceMatcher(a=[w[1] for w in a], b=[w[1] for w in b], autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            removed.extend(a[i1:i2])
        if tag in ("insert", "replace"):
            added.extend(b[j1:j2])
    pages = sorted({w[0] for w in added} | {w[0] for w in removed})
    return removed, added, pages


def _chunks(words, size=12):
    """Group changed words into short snippets (same page, at most `size` words)."""
    chunk = []
    for w in words:
        if chunk and (w[0] != chunk[0][0] or len(chunk) >= size):
            yield chunk
            chunk = []
        chunk.append(w)
    if chunk:
        yield chunk


def _mark(page, words, color):
    for _, _, r in words:
        a = page.add_highlight_annot(r)
        a.set_colors(stroke=color)
        a.set_opacity(0.45)
        a.update()


def report(old_path, new_path):
    """Build the comparison report (a new, unsaved document)."""
    old, new = fitz.open(str(old_path)), fitz.open(str(new_path))
    try:
        removed, added, changed = diff(old, new)
        mo, mn = fitz.open(), fitz.open()
        mo.insert_pdf(old)
        mn.insert_pdf(new)
        for i in range(mo.page_count):
            _mark(mo[i], [w for w in removed if w[0] == i], RED)
        for i in range(mn.page_count):
            _mark(mn[i], [w for w in added if w[0] == i], GREEN)
        # flatten the marks so they show in any viewer
        mo.bake()
        mn.bake()

        out = fitz.open()
        cover = out.new_page(width=792, height=612)
        y = 70
        cover.insert_text((50, y), "Comparison report", fontsize=24, fontname="hebo")
        y += 34
        for line in (f"Old version:  {Path(old_path).name}  ({old.page_count} pages)",
                     f"New version:  {Path(new_path).name}  ({new.page_count} pages)"):
            cover.insert_text((50, y), line, fontsize=12)
            y += 20
        y += 14
        if not removed and not added:
            cover.insert_text((50, y), "No differences found in the text.", fontsize=14,
                              fontname="hebo", color=GREEN)
        else:
            cover.insert_text((50, y), f"{len(removed)} word(s) removed   |   {len(added)} word(s) "
                              f"added   |   {len(changed)} page(s) changed", fontsize=14,
                              fontname="hebo")
            y += 22
            cover.insert_text((50, y), "Pages with changes: " +
                              ", ".join(str(p + 1) for p in changed[:60]) +
                              (" …" if len(changed) > 60 else ""), fontsize=11)
            y += 30
            for color, label in ((RED, "Removed (shown on the old version, left)"),
                                 (GREEN, "Added (shown on the new version, right)")):
                cover.draw_rect(fitz.Rect(50, y - 10, 64, y + 2), color=color, fill=color,
                                fill_opacity=0.45)
                cover.insert_text((72, y), label, fontsize=11)
                y += 20
            y += 10
            cover.insert_text((50, y), "First changes:", fontsize=11, fontname="hebo")
            y += 18
            # list the first changes as readable snippets
            for kind, words, color in (("-", removed, RED), ("+", added, GREEN)):
                for chunk in list(_chunks(words))[:6]:
                    if y > 585:
                        break
                    text = " ".join(c[1] for c in chunk)
                    cover.insert_text((60, y), f"{kind} p.{chunk[0][0] + 1}: {text[:110]}",
                                      fontsize=10, color=color)
                    y += 15
        # side-by-side pages
        for i in range(max(mo.page_count, mn.page_count)):
            ro = mo[i].rect if i < mo.page_count else None
            rn = mn[i].rect if i < mn.page_count else None
            h = max(r.height for r in (ro, rn) if r)
            w = sum(r.width for r in (ro, rn) if r) + 36
            pg = out.new_page(width=max(w, 400), height=h + 40)
            x = 12
            for label, src, r, k in (("Old", mo, ro, i), ("New", mn, rn, i)):
                if r is None:
                    pg.insert_text((x, 26), f"{label}: (no page {i + 1})", fontsize=10,
                                   color=(0.5, 0.5, 0.5))
                    continue
                tag = "  (changed)" if i in changed else ""
                pg.insert_text((x, 20), f"{label} - page {k + 1}{tag}", fontsize=10,
                               fontname="hebo")
                box = fitz.Rect(x, 28, x + r.width, 28 + r.height)
                pg.draw_rect(box, color=(0.75, 0.75, 0.75), width=0.5)
                pg.show_pdf_page(box, src, k)
                x += r.width + 12
        mo.close()
        mn.close()
        out.set_metadata({"title": f"Comparison of {Path(old_path).name} and {Path(new_path).name}"})
        return out, len(removed), len(added), changed
    finally:
        old.close()
        new.close()
