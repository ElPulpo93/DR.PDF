"""Maps PDF font names to the fonts installed on this Windows computer."""
import os
import re
import winreg
from pathlib import Path

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
SIZES = [6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72]

# Built-in PDF fonts, used when no installed font fits.
_BUILTIN = {
    "Helvetica": {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"},
    "Times": {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"},
    "Courier": {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"},
}
_ALIASES = {
    "helvetica": "Arial", "arialmt": "Arial", "times": "Times New Roman",
    "timesroman": "Times New Roman", "timesnewromanps": "Times New Roman",
    "courier": "Courier New", "couriernewps": "Courier New",
    "calibribody": "Calibri", "calibrilight": "Calibri Light",
    "cambriamath": "Cambria", "symbolmt": "Symbol",
}


def _scan():
    """family -> {(bold, italic): font file}"""
    fams = {}
    keys = [(winreg.HKEY_LOCAL_MACHINE, FONT_DIR),
            (winreg.HKEY_CURRENT_USER, None)]
    for hive, base in keys:
        try:
            k = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        i = 0
        while True:
            try:
                name, file, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            path = Path(file) if os.path.isabs(file) else (base or FONT_DIR) / file
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc") or not path.exists():
                continue
            name = re.sub(r"\s*\((TrueType|OpenType)\)$", "", name).strip()
            name = name.split(" & ")[0]  # font collections: the first face is the one used
            if " Math" in name and path.suffix.lower() == ".ttc":
                continue
            bold = italic = 0
            for suffix, b, it in ((" Bold Italic", 1, 1), (" Bold Oblique", 1, 1),
                                  (" Bold", 1, 0), (" Italic", 0, 1), (" Oblique", 0, 1)):
                if name.endswith(suffix):
                    name, bold, italic = name[: -len(suffix)], b, it
                    break
            styles = fams.setdefault(name, {})
            if (bold, italic) not in styles or path.suffix.lower() != ".ttc":
                styles[(bold, italic)] = str(path)
        winreg.CloseKey(k)
    return fams


FAMILIES = _scan()
FAMILY_NAMES = sorted(FAMILIES, key=str.lower) or list(_BUILTIN)
DEFAULT_FAMILY = next((f for f in ("Arial", "Calibri", "Segoe UI") if f in FAMILIES),
                      FAMILY_NAMES[0])
_NORM = {re.sub(r"[^a-z0-9]", "", f.lower()): f for f in FAMILIES}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match(pdf_font, flags=0):
    """PDF font name ('ABCDEF+TimesNewRomanPS-BoldMT') -> (family, bold, italic)."""
    name = re.sub(r"^[A-Z]{6}\+", "", pdf_font or "")
    low = name.lower()
    bold = int(bool(flags & 16) or any(w in low for w in ("bold", "black", "heavy", "semibold")))
    italic = int(bool(flags & 2) or "italic" in low or "oblique" in low)
    base = re.split(r"[-,]", name)[0]
    base = re.sub(r"(PSMT|PS|MT)$", "", base)
    for cand in (base, re.sub(r"(Bold|Italic|Regular|Oblique)+$", "", base)):
        n = _norm(cand)
        if n in _NORM:
            return _NORM[n], bold, italic
        if n in _ALIASES and _ALIASES[n] in FAMILIES:
            return _ALIASES[n], bold, italic
    n = _norm(base)
    for key, fam in _NORM.items():  # e.g. 'segoeuisemibold' starts with 'segoeui'
        if len(key) > 3 and n.startswith(key):
            return fam, bold, italic
    if flags & 8 or "mono" in low or "courier" in low:
        fallback = "Courier New"
    elif flags & 4 or "times" in low or "serif" in low and "sans" not in low:
        fallback = "Times New Roman"
    else:
        fallback = "Arial"
    return (fallback if fallback in FAMILIES else DEFAULT_FAMILY), bold, italic


def resolve(family, bold=0, italic=0):
    """-> (fontname for the PDF, font file or None for a built-in font)."""
    styles = FAMILIES.get(family)
    if styles:
        for key in ((bold, italic), (bold, 0), (0, italic), (0, 0)):
            if key in styles:
                path = styles[key]
                return "DrF" + re.sub(r"[^A-Za-z0-9]", "", Path(path).stem)[:20], path
        path = next(iter(styles.values()))
        return "DrF" + re.sub(r"[^A-Za-z0-9]", "", Path(path).stem)[:20], path
    table = _BUILTIN.get(family, _BUILTIN["Helvetica"])
    return table[(bold, italic)], None


_font_cache = {}


def font_object(family, bold=0, italic=0):
    import pymupdf as fitz
    name, path = resolve(family, bold, italic)
    if name not in _font_cache:
        _font_cache[name] = fitz.Font(fontfile=path) if path else fitz.Font(name)
    return name, path, _font_cache[name]


def fix_space_mapping(page, fontname, font):
    """Embedded fonts share one glyph between the space and the no-break space (and between
    the hyphen and the soft hyphen), and the text map then picks the wrong one: copied,
    searched or edited text would get non-breaking spaces and *invisible* hyphens.
    Point those glyphs back at a normal space and hyphen."""
    doc = page.parent
    pairs = []
    for normal, wrong in ((0x20, 0xA0), (0x2D, 0xAD)):
        gid = font.has_glyph(normal)
        if gid:
            pairs.append((b"<%04x> <%04x>" % (gid, wrong), b"<%04x> <%04x>" % (gid, normal)))
    if not pairs:
        return
    for fi in page.get_fonts():
        if fi[4] != fontname:
            continue
        kind, val = doc.xref_get_key(fi[0], "ToUnicode")
        if kind != "xref":
            continue
        x = int(val.split()[0])
        data = doc.xref_stream(x)
        fixed = data
        for bad, good in pairs:
            fixed = fixed.replace(bad, good)
        if fixed != data:
            doc.update_stream(x, fixed)
