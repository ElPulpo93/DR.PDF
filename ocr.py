"""Text recognition (OCR) for scanned PDFs, using the OCR engine built into Windows 10/11.

Everything runs on this computer. Recognized words are added to the page as an invisible
text layer (like Acrobat's "Recognize Text"): the page looks the same, but its text can be
searched, selected, copied and converted."""
import asyncio
import math
import re

import pymupdf as fitz

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import Buffer
    _OK = True
except Exception:  # not Windows 10+, or the bindings are missing
    _OK = False

DPI = 300


def available():
    return _OK and bool(languages())


def languages():
    """[(tag, name)] of the OCR languages installed in Windows, e.g. ('en-US', 'English')."""
    if not _OK:
        return []
    try:
        return [(l.language_tag, l.display_name) for l in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def _engine(lang=None):
    if lang:
        e = OcrEngine.try_create_from_language(Language(lang))
        if e is not None:
            return e
    return OcrEngine.try_create_from_user_profile_languages()


def _bitmap(pix):
    buf = Buffer(len(pix.samples))
    buf.length = len(pix.samples)
    memoryview(buf)[:] = pix.samples
    return SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.GRAY8,
                                                  pix.width, pix.height)


def page_needs_ocr(page):
    """True for a page that shows a picture of text but has (almost) no real text."""
    if len(page.get_text().strip()) > 20:
        return False
    area = abs(page.rect) or 1
    covered = sum(abs(fitz.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    return covered > 0.3 * area


def doc_needs_ocr(doc, max_pages=10):
    return any(page_needs_ocr(doc[i]) for i in range(min(max_pages, doc.page_count)))


_VOWELS = set("aeiouyAEIOUYáéíóúàèìòùâêîôûäëïöüãõåæøœÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÃÕÅÆØŒ")
_NUMBER = re.compile(r"^[$€£(+-]?\d[\d,./:%)-]*$")


def _plausible(word):
    """Does a recognized word look like a real word or number? (Upside-down text comes back
    as gibberish such as 'ep!101d' or 'Z9tOL'.)"""
    w = word.strip(".,;:!?\"'()[]")
    if not w:
        return False
    if _NUMBER.match(w):
        return True
    if not w.isalpha():
        return False
    shape_ok = w.islower() or w.isupper() or (w[0].isupper() and w[1:].islower())
    return shape_ok and (len(w) <= 2 or any(ch in _VOWELS for ch in w))


def _score(lines):
    """(characters in plausible words, share of plausible words)."""
    words = [t for ws in lines for t, _ in ws]
    good = [t for t in words if _plausible(t)]
    return sum(len(t) for t in good), (len(good) / len(words) if words else 0.0)


def _recognize_at(page, engine, zoom, angle):
    m = fitz.Matrix(zoom, zoom).prerotate(angle)
    pix = page.get_pixmap(matrix=m, colorspace=fitz.csGRAY, alpha=False)

    async def run():
        return await engine.recognize_async(_bitmap(pix))

    result = asyncio.run(run())
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            if not w.text.strip("� □"):
                continue  # an unreadable symbol (often a checkbox), not text
            r = w.bounding_rect
            words.append((w.text, fitz.Rect(r.x, r.y, r.x + r.width, r.y + r.height)))
        if words:
            lines.append(words)
    # image pixels -> page (unrotated PDF) coordinates
    to_page = fitz.Matrix(1, 0, 0, 1, pix.x, pix.y) * ~m * page.derotation_matrix
    return {"lines": lines, "to_page": to_page, "angle": angle}


def recognize(page, lang=None, dpi=DPI, auto_rotate=True):
    """OCR one page. Returns {'lines': [[(word, rect in image pixels), ...], ...],
    'to_page': matrix from image pixels to page coordinates, 'angle': extra rotation}.

    With auto_rotate, a page whose text runs sideways or upside down is read too."""
    limit = OcrEngine.max_image_dimension
    zoom = dpi / 72
    biggest = max(page.rect.width, page.rect.height) * zoom
    if biggest > limit:
        zoom *= limit / biggest * 0.98
    engine = _engine(lang)
    if engine is None:
        raise RuntimeError("Windows text recognition isn't available for this language.")
    best = _recognize_at(page, engine, zoom, 0)
    chars, ratio = _score(best["lines"])
    # little text, or text that reads like gibberish: the page may be sideways/upside down
    if auto_rotate and (chars < 40 or ratio < 0.75):
        for angle in (90, 270, 180):
            other = _recognize_at(page, engine, zoom, angle)
            o_chars, o_ratio = _score(other["lines"])
            if o_chars > max(1.3 * chars, 10) and o_ratio >= min(ratio, 0.75):
                best, chars, ratio = other, o_chars, o_ratio
    return best


def add_text_layer(page, result, visible=False, to_target=None):
    """Write recognized text onto `page` (invisible unless `visible`). `to_target`
    overrides where the text goes (used to copy text onto a fresh page). Returns the
    number of words written."""
    lines = result["lines"]
    t = fitz.Matrix(to_target if to_target is not None else result["to_page"])
    lin = fitz.Matrix(t)
    lin.e = lin.f = 0
    scale = math.sqrt(abs(lin.a * lin.d - lin.b * lin.c)) or 1
    u = fitz.Point(1, 0) * lin
    u = u / abs(u)                       # reading direction on the page
    n = fitz.Point(0, 1) * lin
    n = n / abs(n)                       # "down" for the text
    to_dir = fitz.Matrix(u.x, u.y, n.x, n.y, 0, 0)
    from_dir = ~to_dir
    rot = (round(math.degrees(math.atan2(-u.y, u.x)) / 90) * 90) % 360
    font = fitz.Font("helv")
    count = 0
    for words in lines:
        box = fitz.Rect(words[0][1])
        for _, r in words[1:]:
            box |= r
        # one size per line, so the words read as a line when copied
        fs_px = max(4.0 / scale, box.height / (font.ascender - font.descender))
        fs = fs_px * scale
        base_y = box.y1 + font.descender * fs_px
        if visible:
            # for converting: one string per line with real spaces (Word/Excel need them)
            words = [(" ".join(t_ for t_, _ in words), box)]
        for text, r in words:
            width = font.text_length(text, fs)
            if width <= 0:
                continue
            sx = r.width * scale / width   # stretch the word to cover its picture exactly
            origin = fitz.Point(r.x0, base_y) * t
            morph = (origin, from_dir * fitz.Matrix(sx, 0, 0, 1, 0, 0) * to_dir)
            page.insert_text(origin, text, fontsize=fs, fontname="helv",
                             render_mode=0 if visible else 3, rotate=rot, morph=morph)
            count += 1
    return count


def ocr_pages(doc, pages, lang=None, skip_text=True, progress=None, cancelled=None):
    """OCR the given pages in place. Returns the number of pages that got text."""
    done = 0
    for k, i in enumerate(pages):
        if cancelled and cancelled():
            break
        if progress:
            progress(k, len(pages))
        page = doc[i]
        if skip_text and not page_needs_ocr(page):
            continue
        result = recognize(page, lang)
        if result["lines"] and add_text_layer(page, result):
            done += 1
    return done


def text_copy(doc, lang=None):
    """A copy of doc for converting: scanned pages are replaced by their recognized text
    (visible, in the same place), other pages are kept as they are."""
    out = fitz.open()
    for i, page in enumerate(doc):
        if page_needs_ocr(page):
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            res = recognize(page, lang)
            # same spot as on the original page *as displayed* (the new page isn't rotated)
            add_text_layer(new, res, visible=True,
                           to_target=res["to_page"] * page.rotation_matrix)
        else:
            out.insert_pdf(doc, from_page=i, to_page=i)
    return out


def is_ocr_span(span):
    """Invisible (OCR) text is reported with alpha 0."""
    return span.get("alpha", 255) == 0
