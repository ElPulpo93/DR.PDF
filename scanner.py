"""Scan paper straight into a PDF through Windows Image Acquisition (WIA), the scanner
support built into Windows. Works with any scanner that has a Windows driver."""
import io

import pymupdf as fitz

WIA_FORMAT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
# WIA property ids
DOC_HANDLING_SELECT = 3088   # device: 1 = feeder, 2 = flatbed, 4 = duplex
DOC_HANDLING_STATUS = 3087   # device: 1 = feeder has paper
CURRENT_INTENT = 6146        # item: 1 colour, 2 greyscale, 4 black & white text
X_RES, Y_RES = 6147, 6148
PAPER_EMPTY = (-2145320957, 0x80210003)  # WIA_ERROR_PAPER_EMPTY
NO_PAGES = (-2145320954, 0x80210006)     # WIA_ERROR_PAPER_PROBLEM / nothing to scan

COLORS = [("color", "Colour", 1), ("gray", "Greyscale", 2), ("bw", "Black & white", 4)]
SOURCES = [("flatbed", "Flatbed (glass)", 2), ("feeder", "Document feeder", 1),
           ("duplex", "Feeder, both sides", 5)]


class ScanError(Exception):
    pass


def _manager():
    import win32com.client
    return win32com.client.Dispatch("WIA.DeviceManager")


def devices():
    """[(device id, name)] of the scanners Windows knows about."""
    try:
        dm = _manager()
    except Exception:
        return []
    out = []
    for k in range(1, dm.DeviceInfos.Count + 1):
        info = dm.DeviceInfos.Item(k)
        if info.Type == 1:  # scanner
            out.append((info.DeviceID, info.Properties("Name").Value))
    return out


def _set(props, pid, value):
    for k in range(1, props.Count + 1):
        p = props.Item(k)
        if p.PropertyID == pid:
            try:
                p.Value = value
            except Exception:
                pass  # this scanner doesn't support the setting
            return True
    return False


def _hresult(e):
    for a in getattr(e, "args", ()):
        if isinstance(a, tuple):
            for x in a:
                if isinstance(x, int):
                    return x
    h = getattr(e, "hresult", None)
    return h if isinstance(h, int) else None


def _is(e, codes):
    h = _hresult(e)
    if h is None:
        return any(str(c) in str(e) or hex(c & 0xFFFFFFFF) in str(e).lower() for c in codes)
    return h in codes or (h & 0xFFFFFFFF) in codes


def scan(device_id, dpi=200, color="gray", source="flatbed", max_pages=500,
         progress=None, cancelled=None, manager=None):
    """Scan pages and return them as a list of image bytes (JPEG/PNG).

    With the feeder, pages are scanned until the tray is empty."""
    dm = manager or _manager()
    info = None
    for k in range(1, dm.DeviceInfos.Count + 1):
        if dm.DeviceInfos.Item(k).DeviceID == device_id:
            info = dm.DeviceInfos.Item(k)
    if info is None:
        raise ScanError("The scanner isn't connected or is switched off.")
    try:
        device = info.Connect()
    except Exception as e:
        raise ScanError(f"Couldn't connect to the scanner ({e}).")
    _set(device.Properties, DOC_HANDLING_SELECT, dict((k, v) for k, _, v in SOURCES)[source])
    item = device.Items(1)
    _set(item.Properties, CURRENT_INTENT, dict((k, v) for k, _, v in COLORS)[color])
    _set(item.Properties, X_RES, dpi)
    _set(item.Properties, Y_RES, dpi)
    pages = []
    feeder = source in ("feeder", "duplex")
    while len(pages) < max_pages:
        if cancelled and cancelled():
            break
        if progress:
            progress(len(pages))
        try:
            img = item.Transfer(WIA_FORMAT_JPEG if color != "bw" else WIA_FORMAT_PNG)
        except Exception as e:
            if feeder and pages and _is(e, PAPER_EMPTY + NO_PAGES):
                break  # tray is empty: done
            if _is(e, PAPER_EMPTY):
                raise ScanError("There's no paper in the document feeder.")
            raise ScanError(f"The scanner reported a problem: {e}")
        pages.append(bytes(img.FileData.BinaryData))
        if not feeder:
            break
    return pages


def scan_with_windows_dialog():
    """Fallback: the standard Windows scanning window (one page). Returns [image bytes]."""
    import win32com.client
    dlg = win32com.client.Dispatch("WIA.CommonDialog")
    img = dlg.ShowAcquireImage(1, 0, 0, WIA_FORMAT_JPEG, False, True, False)
    if img is None:
        return []
    return [bytes(img.FileData.BinaryData)]


def images_to_pdf(images, dpi=200):
    """Build a PDF with one page per scanned image, at the scanned size."""
    from PIL import Image
    doc = fitz.open()
    for data in images:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            res = im.info.get("dpi", (dpi, dpi))[0] or dpi
            res = res if 50 <= res <= 1200 else dpi
            if im.format not in ("JPEG", "PNG"):
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=85)
                data = buf.getvalue()
        page = doc.new_page(width=w / res * 72, height=h / res * 72)
        page.insert_image(page.rect, stream=data)
    return doc
