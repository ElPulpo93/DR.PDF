"""Find & redact (search for text or patterns such as Social Security numbers and black them
out everywhere) and Sanitize (remove hidden information before sharing)."""
import re
from dataclasses import dataclass, field

import pymupdf as fitz

# key -> (label, regular expression, extra check)
PATTERNS = {
    "ssn": ("Social Security numbers", r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)", None),
    "phone": ("Phone numbers",
              r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])", None),
    "email": ("Email addresses", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", None),
    "date": ("Dates (e.g. 01/31/1980)",
             r"(?<!\d)(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})(?!\d)", None),
    "card": ("Credit card numbers", r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", "luhn"),
}


def _luhn_ok(s):
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for k, d in enumerate(reversed(digits)):
        if k % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class Match:
    page: int
    text: str
    kind: str
    rects: list = field(default_factory=list)


def _page_lines(page):
    """[(line text, [char rect, ...])] for every text line on the page, OCR text included."""
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            text, rects = [], []
            for span in line["spans"]:
                for ch in span["chars"]:
                    text.append(ch["c"])
                    rects.append(fitz.Rect(ch["bbox"]))
            if text:
                out.append(("".join(text), rects))
    return out


def find(doc, text="", patterns=(), match_case=False, whole_words=False, pages=None):
    """Find every occurrence of `text` and of the chosen `patterns` (keys of PATTERNS)."""
    searches = []
    if text.strip():
        rx = re.escape(text.strip())
        if whole_words:
            rx = r"(?<!\w)" + rx + r"(?!\w)"
        searches.append(("text", re.compile(rx, 0 if match_case else re.IGNORECASE), None))
    for key in patterns:
        label, rx, check = PATTERNS[key]
        searches.append((key, re.compile(rx), check))
    matches = []
    for i in (range(doc.page_count) if pages is None else pages):
        page = doc[i]
        for line, rects in _page_lines(page):
            for kind, rx, check in searches:
                for m in rx.finditer(line):
                    s = m.group(0)
                    if not s.strip() or (check == "luhn" and not _luhn_ok(s)):
                        continue
                    box = fitz.Rect()
                    for r in rects[m.start():m.end()]:
                        if not r.is_empty:
                            box |= r
                    if box.is_empty:
                        continue
                    # one match can be found by two searches (e.g. a name inside an email)
                    if any(x.page == i and x.rects and abs(x.rects[0] & box) > 0.8 * abs(box)
                           for x in matches):
                        continue
                    matches.append(Match(i, s, kind, [box]))
    return matches


def redact(doc, matches, fill=(0, 0, 0)):
    """Permanently remove the matched text (and the picture pixels under it, for scans).
    Returns the number of areas redacted."""
    by_page = {}
    for m in matches:
        by_page.setdefault(m.page, []).extend(m.rects)
    n = 0
    for i, rects in by_page.items():
        page = doc[i]
        for r in rects:
            page.add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5), fill=fill)
            n += 1
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    return n


SANITIZE_OPTIONS = [  # key, label, default
    ("metadata", "Document information (author, title, software, dates)", True),
    ("comments", "Comments, notes, highlights and stamps", True),
    ("attachments", "Attached and embedded files", True),
    ("javascript", "Scripts (JavaScript) and automatic actions", True),
    ("links", "Links to websites and other files", False),
    ("bookmarks", "Bookmarks", False),
    ("form_values", "Text typed into form fields", False),
    ("hidden_text", "Hidden text — also removes text added by OCR", False),
]


def sanitize(doc, options):
    """Remove hidden information. `options` is a set of SANITIZE_OPTIONS keys.
    Returns a list of what was removed (for the summary)."""
    done = []
    if "comments" in options:
        n = 0
        for page in doc:
            for a in list(page.annots()):
                page.delete_annot(a)
                n += 1
        if n:
            done.append(f"{n} comment(s)")
    if "attachments" in options:
        n = doc.embfile_count()
        for name in list(doc.embfile_names()):
            doc.embfile_del(name)
        for page in doc:
            for a in list(page.annots(types=[fitz.PDF_ANNOT_FILE_ATTACHMENT])):
                page.delete_annot(a)
                n += 1
        if n:
            done.append(f"{n} attachment(s)")
    if "bookmarks" in options and doc.get_toc():
        doc.set_toc([])
        done.append("bookmarks")
    doc.scrub(attached_files="attachments" in options, clean_pages=True,
              embedded_files="attachments" in options, hidden_text="hidden_text" in options,
              javascript="javascript" in options, metadata="metadata" in options,
              redactions=True, redact_images=0, remove_links="links" in options,
              reset_fields="form_values" in options, reset_responses="comments" in options,
              thumbnails=True, xml_metadata="metadata" in options)
    if "metadata" in options:
        doc.set_metadata({})
        done.append("document information")
    for key, label in (("javascript", "scripts"), ("links", "links"),
                       ("form_values", "form entries"), ("hidden_text", "hidden text")):
        if key in options:
            done.append(label)
    return done
