"""Small line icons for the toolbars, drawn from inline SVG so they stay crisp at any scale."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

NORMAL = "#C9CDDA"
ACTIVE = "#FFFFFF"
DISABLED = "#555B6E"
SOFT_ON = "#FFFFFF"  # icon colour on a soft (pale) highlight, e.g. the selected tab

# 24x24 stroke paths (stroke = currentColor). "F:" marks a filled shape.
PATHS = {
    "open": "M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z",
    "add": "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5 M12 11v6 M9 14h6",
    "save": "M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z M8 3v5h7V3 M7 21v-6h10v6",
    "undo": "M9 14 4 9l5-5 M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
    "redo": "M15 14l5-5-5-5 M20 9H9.5a5.5 5.5 0 0 0 0 11H13",
    "print": "M7 9V3h10v6 M7 18H5a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2 M7 14h10v7H7z",
    "search": "M10.5 4a6.5 6.5 0 1 1 0 13a6.5 6.5 0 0 1 0-13z M20 20l-4.8-4.8",
    "more": "F:M5 10.5a1.5 1.5 0 1 1 0 3a1.5 1.5 0 0 1 0-3z M12 10.5a1.5 1.5 0 1 1 0 3a1.5 1.5 0 0 1 0-3z M19 10.5a1.5 1.5 0 1 1 0 3a1.5 1.5 0 0 1 0-3z",
    "select": "M6 3l12 7.5-5.2 1.3 3.2 6.2-2.2 1.1-3.2-6.2L6 17z",
    "edit": "M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z M14.5 7.5l2 2 M13 20h7",
    "addtext": "M5 6V4h14v2 M12 4v16 M9 20h6",
    "highlight": "M8.5 12.5 15 6l3 3-6.5 6.5z M8.5 12.5 7 17l1.5 1.5L13 17 M3 21h8 M16.5 4.5 18 3l3 3-1.5 1.5",
    "comment": "M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-5 4v-4h0.5A1.5 1.5 0 0 1 4 14.5z M8 9h8 M8 12h5",
    "draw": "M3 18c2.5-4 4.5-9 7-9s.5 7 3 7 3-6 5.5-6 2 3 2.5 4 M3 21h18",
    "stamp": "M9.5 3h5l-.5 6 3 2v2H7v-2l3-2z M5 16h14v2.5H5z M7 21h10",
    "image": "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z M3 16l5-5 4.5 4.5 2.5-2.5 6 6 M15.5 8.5a1.2 1.2 0 1 1 0 .1z",
    "sign": "M3 16c2.5-1 3.5-7 6-7 2 0 0 6 2.5 6s2.5-4 4.5-4 1.5 2.5 3 2.5 M3 20h18 M17 5l2 2",
    "redact": "M3 7h18 M3 17h7 M14 17h7 F:M3 10h18v4H3z",
    "whiteout": "M15 4l5 5-9 9H6l-3-3z M9 10l5 5 M12 20h9",
    "crop": "M6 2v16h16 M2 6h16v16",
    "tools": "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
    "zoomin": "M10.5 4a6.5 6.5 0 1 1 0 13a6.5 6.5 0 0 1 0-13z M20 20l-4.8-4.8 M10.5 8v5 M8 10.5h5",
    "zoomout": "M10.5 4a6.5 6.5 0 1 1 0 13a6.5 6.5 0 0 1 0-13z M20 20l-4.8-4.8 M8 10.5h5",
    "pages": "M8 3h9a2 2 0 0 1 2 2v12 M5 7h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z",
    "bookmark": "M6.5 3h11a.5.5 0 0 1 .5.5V21l-6-4-6 4V3.5a.5.5 0 0 1 .5-.5z",
    "comments": "M3 5h13v9H8l-5 4z M16 9h5v9l-3-2.5h-7v-1.5",
    "close": "M6 6l12 12 M18 6L6 18",
    "plus": "M12 5v14 M5 12h14",
    "minus": "M5 12h14",
    "trash": "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6",
    "rotate": "M20 12a8 8 0 1 1-2.4-5.7 M20 4v5h-5",
    "up": "M12 19V5 M6 11l6-6 6 6",
    "down": "M12 5v14 M6 13l6 6 6-6",
    "rename": "M4 20h4L18.5 9.5l-4-4L4 16z M13 7l4 4",
    "info": "M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M12 11v6 F:M11 7h2v2h-2z",
    "chevleft": "M15 5l-7 7 7 7",
    "chevright": "M9 5l7 7-7 7",
    "fitwidth": "M3 5v14 M21 5v14 M7 12h10 M9 9l-3 3 3 3 M15 9l3 3-3 3",
    "convert": "M4 8h14l-4-4 M20 16H6l4 4",
    "lock": "M6 11h12v9H6z M8.5 11V8a3.5 3.5 0 0 1 7 0v3",
    "sun": "M12 8a4 4 0 1 1 0 8a4 4 0 0 1 0-8z M12 2.5v2 M12 19.5v2 M5.3 5.3l1.4 1.4 M17.3 17.3l1.4 1.4 M2.5 12h2 M19.5 12h2 M5.3 18.7l1.4-1.4 M17.3 6.7l1.4-1.4",
    "moon": "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
    "ocr": "M4 8V5a1 1 0 0 1 1-1h3 M16 4h3a1 1 0 0 1 1 1v3 M20 16v3a1 1 0 0 1-1 1h-3 M8 20H5a1 1 0 0 1-1-1v-3 M8 9h8 M12 9v7",
    "scan": "M3 13.5h18v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z M5 13.5 7 5h10l2 8.5 M7 16.5h4 M16.5 16.5h.5",
    "form": "M5 3h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M7.5 8h4 M7.5 12.5h4 M7.5 17h4 M14 7h3.5v2H14z M14 11.5h3.5v2H14z M14 16h3.5v2H14z",
    "certificate": "M4 4h16v11H4z M7.5 8h9 M7.5 11h5 M16 13.5a2.5 2.5 0 1 1 0 .1z M14.8 15.8 14 21l2-1.2 2 1.2-.8-5.2",
}

_cache = {}


def set_palette(normal, disabled, soft_on="#FFFFFF"):
    """Icon colours for the current theme (checked buttons stay white on the accent)."""
    global NORMAL, DISABLED, SOFT_ON
    if (normal, disabled, soft_on) != (NORMAL, DISABLED, SOFT_ON):
        NORMAL, DISABLED, SOFT_ON = normal, disabled, soft_on
        _cache.clear()


def _svg(name, color):
    # everything before the first "F:" is stroked; each "F:" segment is filled
    first, *fills = PATHS[name].split("F:")
    stroke = f'<path d="{first.strip()}"/>' if first.strip() else ""
    fill = "".join(f'<path d="{p.strip()}" fill="{color}" stroke="none"/>' for p in fills)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round">{stroke}{fill}</svg>')


def _pixmap(name, color, size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(_svg(name, color).encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name, color=None, soft=False):
    """QIcon with normal, checked and disabled variants. `soft`: the button's checked
    look is a pale highlight, so the checked icon uses SOFT_ON instead of white."""
    color = color or NORMAL
    on = SOFT_ON if soft else ACTIVE
    key = (name, color, on)
    if key not in _cache:
        ic = QIcon()
        for size in (18, 36):  # 36 = sharp on high-DPI screens
            ic.addPixmap(_pixmap(name, color, size), QIcon.Normal, QIcon.Off)
            ic.addPixmap(_pixmap(name, on, size), QIcon.Normal, QIcon.On)
            ic.addPixmap(_pixmap(name, on, size), QIcon.Active, QIcon.On)
            ic.addPixmap(_pixmap(name, DISABLED, size), QIcon.Disabled, QIcon.Off)
            ic.addPixmap(_pixmap(name, DISABLED, size), QIcon.Disabled, QIcon.On)
        _cache[key] = ic
    return _cache[key]
