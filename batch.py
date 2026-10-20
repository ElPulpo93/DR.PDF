"""Batch actions: apply the same steps to many PDFs (OCR, compress, page numbers,
watermark, redact, sanitize, flatten, password)."""
import os
from pathlib import Path

import pymupdf as fitz

import pdftools
import redact

ACTIONS = [  # key, label (in the order they are applied)
    ("ocr", "Recognize text (OCR) on scanned pages"),
    ("redact", "Redact sensitive information"),
    ("sanitize", "Remove hidden information"),
    ("page_numbers", "Add page numbers"),
    ("watermark", "Add a watermark"),
    ("flatten", "Flatten comments & form fields"),
    ("compress", "Reduce file size"),
    ("password", "Password protect"),
]


def pdf_files(paths):
    """Expand files and folders into a sorted list of PDF files."""
    out, seen = [], set()
    for p in paths:
        p = Path(p)
        cands = sorted(p.rglob("*.pdf")) if p.is_dir() else [p]
        for c in cands:
            if c.suffix.lower() == ".pdf" and c.is_file() and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def process(path, steps, out_path):
    """Apply `steps` ({key: options}) to one PDF and save it to out_path.
    Returns a short description of what was done."""
    done = []
    doc = fitz.open(str(path))
    try:
        if doc.needs_pass:
            raise RuntimeError("password protected — open it in the editor first")
        if "ocr" in steps:
            import ocr
            if not ocr.available():
                raise RuntimeError("Windows text recognition isn't available on this computer")
            n = ocr.ocr_pages(doc, range(doc.page_count), lang=steps["ocr"].get("lang"))
            done.append(f"OCR on {n} page(s)")
        if "redact" in steps:
            o = steps["redact"]
            m = redact.find(doc, text=o.get("text", ""), patterns=o.get("patterns", ()))
            redact.redact(doc, m)
            done.append(f"{len(m)} redaction(s)")
        if "sanitize" in steps:
            removed = redact.sanitize(doc, set(steps["sanitize"].get("options", ())))
            done.append("removed " + ", ".join(removed) if removed else "sanitized")
        if "page_numbers" in steps:
            o = steps["page_numbers"]
            pdftools.add_header_footer(doc, o.get("template", "Page {page} of {total}"),
                                       o.get("position", "Bottom center"),
                                       filename=Path(path).stem)
            done.append("page numbers")
        if "watermark" in steps:
            pdftools.add_watermark(doc, steps["watermark"].get("text", "CONFIDENTIAL"),
                                   opacity=steps["watermark"].get("opacity", 0.3))
            done.append("watermark")
        if "flatten" in steps:
            pdftools.flatten(doc)
            done.append("flattened")
        kw = {"garbage": 3, "deflate": True}
        if "compress" in steps:
            try:
                doc.rewrite_images(dpi_threshold=160, dpi_target=150, quality=70)
            except Exception:
                pass
            kw.update(garbage=4, deflate_images=True, deflate_fonts=True, clean=True)
            done.append("compressed")
        if "password" in steps:
            pw = steps["password"]["password"]
            kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw)
            done.append("password")
        try:
            doc.subset_fonts()
        except Exception:
            pass
        data = doc.tobytes(**kw)
    finally:
        doc.close()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".drtmp")
    tmp.write_bytes(data)
    os.replace(tmp, out_path)
    return ", ".join(done) or "copied"


def output_path(src, out_dir, suffix):
    src = Path(src)
    target = Path(out_dir) if out_dir else src.parent
    name = f"{src.stem}{suffix}.pdf"
    p = target / name
    k = 2
    while p.exists() and p.resolve() != src.resolve():
        p = target / f"{src.stem}{suffix} ({k}).pdf"
        k += 1
    if p.resolve() == src.resolve():
        raise RuntimeError("would overwrite the original — use a suffix or another folder")
    return p


def run(files, steps, out_dir=None, suffix=" (processed)", progress=None, cancelled=None):
    """Process every file. Returns [(file, ok, message, output path or None)]."""
    results = []
    for k, f in enumerate(files):
        if cancelled and cancelled():
            break
        if progress:
            progress(k, len(files), Path(f).name)
        try:
            out = output_path(f, out_dir, suffix)
            msg = process(f, steps, out)
            results.append((Path(f), True, msg, out))
        except Exception as e:
            results.append((Path(f), False, str(e) or e.__class__.__name__, None))
    return results
