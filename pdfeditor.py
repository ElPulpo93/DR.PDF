"""Dr.PDF editor: view, edit text/images, comment, sign, organize pages."""
import functools
import math
import re
import os
import shutil
import tempfile
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import (Qt, QPoint, QPointF, QRectF, QTimer, Signal, QThread, QSize, QUrl,
                            QEventLoop, QMimeData, QBuffer, QIODevice)
from PySide6.QtGui import (QColor, QPen, QBrush, QImage, QPixmap, QPainter, QFont,
                           QPainterPath, QIcon, QKeySequence, QShortcut, QTransform,
                           QDesktopServices, QTextCharFormat, QTextCursor, QTextBlockFormat,
                           QPaintEngine, QCursor)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QComboBox, QLabel,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem,
    QGraphicsPathItem, QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsProxyWidget,
    QTextEdit, QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QInputDialog,
    QColorDialog, QSpinBox, QMenu, QFrame, QLineEdit, QDialog, QDialogButtonBox,
    QAbstractItemView, QApplication, QButtonGroup, QStackedWidget, QToolButton,
    QTreeWidget, QTreeWidgetItem, QProgressDialog, QTabBar, QGraphicsSimpleTextItem,
)

import compare
import converters as cv
import dialogs
import forms
import fonts
import icons
import ocr
import pdftools
import redact
import scanner
import signing
import textrepair
import textunits
import theme
import version

GAP = 14            # points between pages
EDIT_SCALE = 4.0    # text editor overlay is drawn at 4x and scaled down for crisp text
UNDO_BYTES = 400 * 1024 * 1024
APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "Dr.PDF"
SIGNATURE_FILE = APP_DATA / "signature.png"

TOOLBAR = [  # (id, label, icon, tooltip) | ("menu", label, icon, tooltip, items) | "|"
    # in menus, an id starting with "@" runs that editor method instead of choosing a tool
    ("select", "Select", "select", "Select text, comments, pictures and form fields  (V)"),
    ("edit", "Edit", "edit", "Edit text and pictures: click a paragraph or a picture  (E)"),
    ("addtext", "Add text", "addtext", "Click anywhere to type new text  (T)"),
    "|",
    # one tool for highlighting and redacting: black (the default) removes the text for
    # good, any other colour highlights it; the arrow has the other redaction tools
    ("split", "highlight", "Highlight", "highlight",
     "Drag across text: black redacts it, other colours highlight it  (H)",
     [("redact", "Redact an area"), ("whiteout", "Whiteout (cover with white)"),
      ("@find_redact_dialog", "Find && redact…"),
      ("@sanitize_dialog", "Remove hidden information…")]),
    ("menu", "Comment", "comment", "Sticky notes, text boxes, underline, strikethrough",
     [("note", "Sticky note  (N)"), ("textbox", "Text box"), ("underline", "Underline text"),
      ("strike", "Strike through text")]),
    ("menu", "Draw", "draw", "Pen, shapes, lines, arrows and pictures",
     [("pen", "Pen (freehand)"), ("rect", "Rectangle"), ("ellipse", "Ellipse"),
      ("line", "Line"), ("arrow", "Arrow"), ("image", "Picture")]),
    ("menu", "Stamp", "stamp", "Approved, Draft, Confidential and other stamps", []),
    "|",
    ("menu", "Form", "form", "Create fillable form fields",
     [("form:text", "Text field"), ("form:multiline", "Multi-line text box"),
      ("form:checkbox", "Checkbox"), ("form:dropdown", "Dropdown list"),
      ("form:date", "Date field"), ("form:signature", "Signature field"),
      ("@detect_fields", "Detect fields automatically…")]),
    ("menu", "Sign", "sign", "Sign by drawing/typing, or with a digital ID",
     [("sign", "Draw or type a signature"), ("certsign", "Sign with a digital ID…"),
      ("@signatures_dialog", "Check digital signatures"),
      ("@create_id_dialog", "Create a digital ID…")]),
]
TOOL_ICONS = {"note": "comment", "textbox": "addtext", "underline": "addtext",
              "strike": "addtext", "pen": "draw", "rect": "crop", "ellipse": "draw",
              "line": "draw", "arrow": "draw", "redact": "redact", "whiteout": "whiteout",
              "image": "image", "form:text": "form", "form:multiline": "form",
              "form:checkbox": "form", "form:dropdown": "form", "form:date": "form",
              "form:signature": "sign", "@detect_fields": "search", "sign": "sign",
              "certsign": "certificate", "@signatures_dialog": "certificate",
              "@create_id_dialog": "plus", "@find_redact_dialog": "search",
              "@sanitize_dialog": "trash"}
TOOL_HINTS = {
    "edit": "Click a paragraph to type in it, or drag it to move it. Ctrl+Enter applies, "
            "Esc cancels.",
    "addtext": "Click where the text should start. Drag text you added to move it.",
    "highlight": "Drag across the text to highlight it.",
    "underline": "Drag across the text to underline.",
    "strike": "Drag across the text to strike through.",
    "note": "Click where the note should go.",
    "textbox": "Drag a box, then type the text.",
    "pen": "Draw with the mouse.",
    "rect": "Drag to draw.", "ellipse": "Drag to draw.", "line": "Drag to draw.",
    "arrow": "Drag to draw.",
    "stamp": "Click where the stamp should go.",
    "image": "Drag a box or click where the picture should go.",
    "sign": "Click where your signature should go.",
    "whiteout": "Drag over what should be covered.",
    "redact": "Drag over what should be removed permanently.",
    "crop": "Drag a box around the part of the page to keep.",
    "form": "Drag a box (or click) where the field should go. Click a field to move or "
            "resize it; double-click to rename it.",
    "certsign": "Drag a box where the signature should appear, or click a signature field.",
}
DEFAULT_COLORS = {
    "addtext": QColor(0, 0, 0), "edit": QColor(0, 0, 0), "highlight": QColor(0, 0, 0),
    "underline": QColor(30, 110, 255), "strike": QColor(230, 30, 30),
    "note": QColor(255, 210, 0), "pen": QColor(220, 30, 30), "rect": QColor(220, 30, 30),
    "ellipse": QColor(220, 30, 30), "line": QColor(220, 30, 30), "arrow": QColor(220, 30, 30),
    "textbox": QColor(0, 0, 0),
}
TEXT_TOOLS = {"edit", "addtext"}
HIGHLIGHT_COLORS = [("Black (redact)", "#000000"), ("Yellow", "#FFE600"), ("Green", "#7CFC6B"),
                    ("Blue", "#6EC6FF"), ("Pink", "#FF8AD8"), ("Orange", "#FFB347")]


def swatch_icon(col):
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(QPen(QColor("#8A90A2"), 1))
    qp.setBrush(QColor(col))
    qp.drawRoundedRect(1, 1, 14, 14, 3, 3)
    qp.end()
    return QIcon(pm)


def is_redact_color(c):
    """Black highlighting means 'remove this' (a black highlight would still leave the text
    readable underneath, which is not safe for private information)."""
    return QColor(c).value() < 40
STROKE_TOOLS = {"pen", "rect", "ellipse", "line", "arrow"}
DRAG_RECT_TOOLS = {"rect", "ellipse", "whiteout", "redact", "image", "textbox", "crop",
                   "form", "certsign"}
TEXT_MARK_TOOLS = {"highlight", "underline", "strike"}
MOVABLE_ANNOTS = {fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_TEXT,
                  fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_STAMP}
RESIZABLE_ANNOTS = MOVABLE_ANNOTS - {fitz.PDF_ANNOT_TEXT}  # sticky-note icons have a fixed size


def needs_doc(fn):
    """Actions that work on the open PDF do nothing when no PDF is open (instead of
    failing), whichever button, menu or shortcut started them."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if self.doc is None:
            return None
        return fn(self, *args, **kwargs)
    return wrapper


def rgb(qc: QColor):
    return (qc.redF(), qc.greenF(), qc.blueF())


def qcolor_from_int(c):
    return QColor((c >> 16) & 255, (c >> 8) & 255, c & 255)


def wrap_lines(text, font, size, width):
    """Greedy word-wrap of each paragraph to the given width in points."""
    out = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            trial = w if not line else line + " " + w
            if line and font.text_length(trial, size) > width:
                out.append(line)
                line = w
            else:
                line = trial
        out.append(line)
    return out


def write_text(page, origin, lines, family, bold, italic, size, color, rotate, line_h):
    """Write lines of text starting at a baseline origin (unrotated coordinates)."""
    name, path, _ = fonts.font_object(family, bold, italic)
    a = math.radians(rotate)
    down = fitz.Point(math.sin(a), math.cos(a))  # perpendicular to the text direction
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        pt = origin + down * (i * line_h)
        kw = {"fontname": name, "fontsize": size, "color": color, "rotate": rotate}
        if path:
            kw["fontfile"] = path
        page.insert_text(pt, ln, **kw)
    if path:
        fonts.fix_space_mapping(page, name, fonts.font_object(family, bold, italic)[2])


NUM_RE = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
CM_RE = re.compile(rb"(%s)\s+(%s)\s+(%s)\s+(%s)\s+(%s)\s+(%s)\s+cm(?![A-Za-z])" % ((NUM_RE,) * 6))


def write_runs(page, origin, lines, rotate, line_h, base_size):
    """Write styled lines: lines = [[(text, (family, bold, italic, size, '#rrggbb')), ...]].
    The first baseline is at `origin` (page coordinates); taller lines get more room."""
    a = math.radians(rotate)
    along = fitz.Point(math.cos(a), -math.sin(a))
    down = fitz.Point(math.sin(a), math.cos(a))
    used = {}
    offset = 0.0
    for k, line in enumerate(lines):
        biggest = max((st[3] for _, st in line), default=base_size)
        if k:
            offset += line_h * max(1.0, biggest / max(base_size, 0.1))
        # neighbouring pieces with the same style are written together
        chunks = []
        for text, st in line:
            if chunks and chunks[-1][1] == st:
                chunks[-1] = (chunks[-1][0] + text, st)
            else:
                chunks.append((text, st))
        x = 0.0
        for text, st in chunks:
            family, bold, italic, size, color = st
            name, path, font = fonts.font_object(family, bold, italic)
            if text.strip():
                c = QColor(color)
                kw = {"fontname": name, "fontsize": size, "rotate": rotate,
                      "color": (c.redF(), c.greenF(), c.blueF())}
                if path:
                    kw["fontfile"] = path
                    used[name] = font
                page.insert_text(origin + down * offset + along * x, text, **kw)
            x += font.text_length(text, size)
    for name, font in used.items():
        fonts.fix_space_mapping(page, name, font)


def pdf_num(v):
    """Format a number for a PDF content stream."""
    if abs(v) < 1e-9:
        return "0"
    t = f"{v:.5f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def log_error(where, e):
    """Note an error that was handled (so it doesn't interrupt anyone) in the error log."""
    import time
    import traceback
    try:
        d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / version.APP_NAME / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {where} (handled)\n"
                    + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except OSError:
        pass


def pixmap_to_qimage(pix):
    fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


# ======================================================================== widgets
class PageItem(QGraphicsRectItem):
    def __init__(self, index, w, h):
        super().__init__(0, 0, w, h)
        self.index = index
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.NoPen))
        self.pix = QGraphicsPixmapItem(self)
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.rendered = 0.0


MARKUP_ANNOTS = {fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE, fitz.PDF_ANNOT_STRIKE_OUT,
                 fitz.PDF_ANNOT_SQUIGGLY}


def _pdf_date(s):
    """'D:20261015143000-04'00'' -> 'Oct 15, 2026 2:30 PM' ('' if it can't be read)."""
    import datetime
    s = (s or "").strip()
    if s.startswith("D:"):
        s = s[2:]
    try:
        d = datetime.datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return d.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


class NotePreview(QFrame):
    """The comment card shown while the mouse rests on a note or a commented highlight
    (like Acrobat): author, date and the note's text. Double-click still edits it."""

    def __init__(self, parent):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("notePreview")
        self.setStyleSheet(
            "#notePreview { background: #FFFFFF; border: 1px solid #C9CCD8; border-radius: 6px; }"
            "#noteBar { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }"
            "QLabel { background: transparent; color: #1F2233; }"
            "#noteWho { font-weight: 600; } #noteWhen { color: #5F6576; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = QFrame()
        self.bar.setObjectName("noteBar")
        self.bar.setFixedWidth(5)
        lay.addWidget(self.bar)
        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 12, 9)
        body.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.who = QLabel()
        self.who.setObjectName("noteWho")
        self.when = QLabel()
        self.when.setObjectName("noteWhen")
        head.addWidget(self.who)
        head.addStretch()
        head.addWidget(self.when)
        body.addLayout(head)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.PlainText)
        self.text.setMaximumWidth(340)
        body.addWidget(self.text)
        lay.addLayout(body)
        self.key = None

    def show_note(self, key, info, color, global_pos):
        if key != self.key:
            self.key = key
            who = (info.get("title") or "").strip()
            self.who.setText(who or "Comment")
            self.when.setText(_pdf_date(info.get("modDate") or info.get("creationDate")))
            text = info.get("content") or ""
            if len(text) > 1500:
                text = text[:1500].rstrip() + "…"
            self.text.setText(text)
            self.text.setMinimumWidth(min(340, max(160, self.text.fontMetrics().horizontalAdvance(text) + 4)))
            self.bar.setStyleSheet(f"background: {color};")
            self.adjustSize()
        # just below and right of the pointer, kept on the screen
        pos = global_pos + QPoint(14, 18)
        scr = QApplication.screenAt(global_pos)
        if scr is not None:
            g = scr.availableGeometry()
            if pos.x() + self.width() > g.right():
                pos.setX(max(g.left(), global_pos.x() - self.width() - 8))
            if pos.y() + self.height() > g.bottom():
                pos.setY(max(g.top(), global_pos.y() - self.height() - 10))
        self.move(pos)
        if not self.isVisible():
            self.show()

    def hide_note(self):
        self.key = None
        self.hide()


class PageList(QListWidget):
    """Page thumbnails that can be put in a new order by dragging them (a line shows where
    they'll go). PDFs and other files dragged in from Explorer are added at that spot."""
    pages_dropped = Signal(list, int)   # rows that were dragged, row they go before
    files_dropped = Signal(list, int)   # file paths, row they go before

    def __init__(self, horizontal=False):
        super().__init__()
        self.horizontal = horizontal
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight if horizontal else QListWidget.TopToBottom)
        self.setWrapping(horizontal)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAutoScroll(True)
        self.drop_row = None

    def _row_for(self, pos):
        if self.count() == 0:
            return 0
        best, dist = None, None
        for r in range(self.count()):
            rect = self.visualItemRect(self.item(r))
            c = rect.center()
            dd = (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2
            if dist is None or dd < dist:
                best, dist = r, dd
        rect = self.visualItemRect(self.item(best))
        after = pos.x() > rect.center().x() if self.horizontal else pos.y() > rect.center().y()
        return best + (1 if after else 0)

    @staticmethod
    def _files(e):
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()]

    def dragEnterEvent(self, e):
        if e.source() is self or self._files(e):
            e.setDropAction(Qt.MoveAction if e.source() is self else Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is not self and not self._files(e):
            e.ignore()
            return
        super().dragMoveEvent(e)  # (scrolls near the edges)
        self.drop_row = self._row_for(e.position().toPoint())
        self.viewport().update()
        e.setDropAction(Qt.MoveAction if e.source() is self else Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, e):
        self.drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        row = self._row_for(e.position().toPoint())
        self.drop_row = None
        self.viewport().update()
        if e.source() is self:
            rows = sorted(self.row(it) for it in self.selectedItems())
            # CopyAction: the list must not remove the items itself; the pages are put
            # in their new order and the list is rebuilt from the PDF
            e.setDropAction(Qt.CopyAction)
            e.accept()
            if rows:
                QTimer.singleShot(0, lambda: self.pages_dropped.emit(rows, row))
            return
        files = self._files(e)
        if files:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            QTimer.singleShot(0, lambda: self.files_dropped.emit(files, row))
            return
        e.ignore()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_row is None or self.count() == 0:
            return
        if self.drop_row < self.count():
            r = self.visualItemRect(self.item(self.drop_row))
            a = (QPoint(r.left() - 3, r.top()), QPoint(r.left() - 3, r.bottom())) if self.horizontal \
                else (QPoint(r.left(), r.top() - 3), QPoint(r.right(), r.top() - 3))
        else:
            r = self.visualItemRect(self.item(self.count() - 1))
            a = (QPoint(r.right() + 3, r.top()), QPoint(r.right() + 3, r.bottom())) if self.horizontal \
                else (QPoint(r.left(), r.bottom() + 3), QPoint(r.right(), r.bottom() + 3))
        painter = QPainter(self.viewport())
        pen = QPen(QColor(theme.color("accent")), 4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(*a)
        painter.end()


class OrganizeDialog(QDialog):
    """Organize pages (like Acrobat): every page large, in a grid. Drag pages to put them in
    order; rotate or delete the selected ones. Changes apply straight away (Undo works)."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle("Organize pages")
        self.resize(1100, 760)
        v = QVBoxLayout(self)
        head = QHBoxLayout()
        tip = QLabel("Drag pages to change their order. Select several with Ctrl or Shift.")
        tip.setObjectName("muted")
        head.addWidget(tip)
        head.addStretch()
        for text, fn in (("Rotate left", lambda: self._act(lambda: editor.rotate_pages(-90))),
                         ("Rotate right", lambda: self._act(lambda: editor.rotate_pages(90))),
                         ("Delete", self._delete), ("Add files…", self._add)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            head.addWidget(b)
        v.addLayout(head)
        self.grid = PageList(horizontal=True)
        self.grid.setObjectName("thumbs")
        self.grid.setIconSize(QSize(170, 220))
        self.grid.setSpacing(14)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.pages_dropped.connect(self._moved)
        self.grid.files_dropped.connect(self._files)
        v.addWidget(self.grid, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Close).setText("Done")
        v.addWidget(bb)
        self.fill()

    def fill(self, select=None):
        ed = self.editor
        self.grid.clear()
        if ed.doc is None:
            return
        for i in range(ed.doc.page_count):
            page = ed.doc[i]
            z = 170 / max(page.rect.width, page.rect.height * 170 / 220, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            it = QListWidgetItem(QIcon(QPixmap.fromImage(pixmap_to_qimage(pix))), str(i + 1))
            it.setTextAlignment(Qt.AlignHCenter)
            self.grid.addItem(it)
            if select and i in select:
                it.setSelected(True)

    def _rows(self):
        return sorted(self.grid.row(it) for it in self.grid.selectedItems())

    def _sync_selection(self):
        rows = self._rows()
        self.editor.thumbs.clearSelection()
        for r in rows:
            if r < self.editor.thumbs.count():
                self.editor.thumbs.item(r).setSelected(True)
        return rows

    def _act(self, fn):
        rows = self._sync_selection()
        if not rows:
            return
        fn()
        self.fill(set(rows))

    def _delete(self):
        rows = self._sync_selection()
        if rows:
            self.editor.delete_pages()
            self.fill()

    def _moved(self, rows, before):
        new = self.editor.reorder_pages(rows, before)
        self.fill(new)

    def _files(self, paths, before):
        self.editor.add_files(paths, at=before)
        self.fill()

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files", "", self.editor.ADD_FILTER)
        if paths:
            rows = self._rows()
            self.editor.add_files(paths, at=(rows[-1] + 1) if rows else self.editor.doc.page_count)
            self.fill()


class Canvas(QGraphicsView):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setScene(QGraphicsScene(self))
        self.apply_theme()
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setAcceptDrops(False)
        self.verticalScrollBar().valueChanged.connect(editor.schedule_render)
        self.horizontalScrollBar().valueChanged.connect(editor.schedule_render)

    def wheelEvent(self, e):
        self.editor.hide_note_preview()
        if e.modifiers() & Qt.ControlModifier:
            vp = e.position()
            self.editor.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15,
                                anchor=(self.mapToScene(vp.toPoint()), vp))
        else:
            super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.editor.on_resize()

    def mousePressEvent(self, e):
        self.editor.hide_note_preview()
        if self.editor.on_press(e, self.mapToScene(e.position().toPoint())):
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.editor.on_move(e, self.mapToScene(e.position().toPoint()))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.editor.hide_note_preview()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.editor.on_release(e, self.mapToScene(e.position().toPoint())):
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        if self.editor.on_context_menu(self.mapToScene(e.pos()), e.globalPos()):
            e.accept()
            return
        super().contextMenuEvent(e)  # e.g. the text editor's own menu

    def mouseDoubleClickEvent(self, e):
        self.editor.hide_note_preview()
        if self.editor.on_double(e, self.mapToScene(e.position().toPoint())):
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if self.editor.on_key(e):
            return
        super().keyPressEvent(e)

    def apply_theme(self):
        self.setBackgroundBrush(QColor(theme.color("canvas")))

    def sizeHint(self):
        # by default a view asks for room for the whole document, which made the
        # window want to be taller than the screen; it fills whatever space it gets
        return QSize(640, 480)


class PageBox(QLineEdit):
    """Small 'page number' field: type a number and press Enter to jump there."""
    valueChanged = Signal(int)

    def __init__(self):
        super().__init__("1")
        self._max = 1
        self.setObjectName("pageBox")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(52)
        self.setToolTip("Type a page number and press Enter")
        self.editingFinished.connect(self._done)

    def setMaximum(self, n):
        self._max = max(1, int(n))

    def value(self):
        try:
            return min(max(int(self.text()), 1), self._max)
        except ValueError:
            return 1

    def setValue(self, v):
        self.setText(str(v))

    def _done(self):
        v = self.value()
        self.setText(str(v))
        self.valueChanged.emit(v)


class TextBox(QTextEdit):
    """The live text editor shown on top of the page while editing text."""
    commit = Signal()
    cancel = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancel.emit()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            self.commit.emit()
            return
        super().keyPressEvent(e)


class SignatureDialog(QDialog):
    """Draw (or type) a signature; returns PNG bytes with a transparent background."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Your signature")
        self.img = QImage(640, 220, QImage.Format_ARGB32)
        self.img.fill(Qt.transparent)
        self.last = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Draw your signature below, or type it."))
        self.pad = QLabel()
        self.pad.setFixedSize(640, 220)
        self.pad.setStyleSheet("background: white; border-radius: 10px;")
        self.pad.mousePressEvent = self._press
        self.pad.mouseMoveEvent = self._move
        lay.addWidget(self.pad)
        row = QHBoxLayout()
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("…or type your name")
        self.typed.textChanged.connect(self._type)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        row.addWidget(self.typed, 1)
        row.addWidget(clear)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if SIGNATURE_FILE.exists():  # start from the saved signature
            saved = QImage(str(SIGNATURE_FILE))
            if not saved.isNull():
                size = saved.size()
                if size.width() > 640 or size.height() > 220:
                    size = size.scaled(640, 220, Qt.KeepAspectRatio)
                target = QRectF((640 - size.width()) / 2, (220 - size.height()) / 2,
                                size.width(), size.height())
                p = QPainter(self.img)
                p.drawImage(target, saved, QRectF(saved.rect()))
                p.end()
        self._show()

    def _show(self):
        bg = QImage(self.img.size(), QImage.Format_ARGB32)
        bg.fill(Qt.white)
        p = QPainter(bg)
        p.drawImage(0, 0, self.img)
        p.end()
        self.pad.setPixmap(QPixmap.fromImage(bg))

    def _press(self, e):
        self.last = e.position()

    def _move(self, e):
        if self.last is None:
            return
        p = QPainter(self.img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(20, 30, 90), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(self.last, e.position())
        p.end()
        self.last = e.position()
        self._show()

    def _clear(self):
        self.img.fill(Qt.transparent)
        self.typed.blockSignals(True)
        self.typed.clear()
        self.typed.blockSignals(False)
        self._show()

    def _type(self, text):
        self.img.fill(Qt.transparent)
        p = QPainter(self.img)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont("Segoe Script" if "Segoe Script" in fonts.FAMILIES else "Brush Script MT")
        f.setPixelSize(80)
        p.setFont(f)
        p.setPen(QColor(20, 30, 90))
        p.drawText(QRectF(0, 0, 640, 220), Qt.AlignCenter, text)
        p.end()
        self._show()

    def png(self):
        # crop to the inked area
        xs, ys = [], []
        w, h = self.img.width(), self.img.height()
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if self.img.pixelColor(x, y).alpha() > 20:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        r = QRectF(min(xs) - 6, min(ys) - 6, max(xs) - min(xs) + 12, max(ys) - min(ys) + 12)
        img = self.img.copy(r.toRect())
        APP_DATA.mkdir(parents=True, exist_ok=True)
        img.save(str(SIGNATURE_FILE), "PNG")
        return SIGNATURE_FILE.read_bytes(), img.width(), img.height()


class _TabRef:
    """Holds one tab's state; Qt keeps this object itself (not a copy)."""
    __slots__ = ("state",)

    def __init__(self, state):
        self.state = state


class OcrWorker(QThread):
    """Recognizes text on a copy of the document; the editor adds it to the real one."""
    page_done = Signal(int, object)   # page index, recognized lines
    progress = Signal(int, int)
    failed = Signal(str)

    def __init__(self, data, pages, lang, skip_text):
        super().__init__()
        self.data, self.pages, self.lang, self.skip_text = data, pages, lang, skip_text

    def run(self):
        doc = fitz.open("pdf", self.data)
        try:
            for k, i in enumerate(self.pages):
                if self.isInterruptionRequested():
                    break
                self.progress.emit(k, len(self.pages))
                page = doc[i]
                if self.skip_text and not ocr.page_needs_ocr(page):
                    continue
                self.page_done.emit(i, ocr.recognize(page, self.lang))
            self.progress.emit(len(self.pages), len(self.pages))
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)
        finally:
            doc.close()


class BatchWorker(QThread):
    progress = Signal(int, int, str)

    def __init__(self, v):
        super().__init__()
        self.v = v
        self.results = []

    def run(self):
        import batch
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        self.results = batch.run(self.v["files"], self.v["steps"], self.v["out_dir"],
                                 self.v["suffix"], progress=self.progress.emit,
                                 cancelled=self.isInterruptionRequested)


class ScanWorker(QThread):
    progress = Signal(int)

    def __init__(self, v):
        super().__init__()
        self.v = v
        self.data, self.pages, self.error = None, 0, None

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            images = scanner.scan(self.v["device"], dpi=self.v["dpi"], color=self.v["color"],
                                  source=self.v["source"], progress=self.progress.emit,
                                  cancelled=self.isInterruptionRequested)
            if not images:
                return
            doc = scanner.images_to_pdf(images, self.v["dpi"])
            if self.v.get("ocr") and ocr.available():
                ocr.ocr_pages(doc, range(doc.page_count), skip_text=False,
                              cancelled=self.isInterruptionRequested)
            self.data, self.pages = doc.tobytes(garbage=3, deflate=True), len(images)
        except Exception as e:
            self.error = str(e) or e.__class__.__name__


class ExportWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, data, stem, label, out_dir):
        super().__init__()
        self.data, self.stem, self.label, self.out_dir = data, stem, label, out_dir

    def run(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            src = tmp / (self.stem + ".pdf")
            src.write_bytes(self.data)
            outs = cv.convert(src, self.label, Path(self.out_dir), {"quality": 90}, None)
            self.done.emit(True, outs[0].name if len(outs) == 1 else f"{len(outs)} files")
        except Exception as e:
            self.done.emit(False, str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================== editor
class PdfEditor(QWidget):
    title_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("editor")
        self.doc = None
        self.path = None
        self.password = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.items: list[PageItem] = []
        self.zoom = 1.0
        self.fit_mode = "width"
        self.tool = "select"
        self.colors = {k: QColor(v) for k, v in DEFAULT_COLORS.items()}
        self.drag = None
        self.overlay = []          # temporary scene items (selection, hover, ...)
        self.hover_item = None
        self.selected = None       # ('annot', page, xref) | ('image', page, xref, rect) | ('text', page, words)
        self.editing = None        # dict describing the open text editor
        self.search_hits = []
        self.search_pos = -1
        self.export_worker = None
        self.block_cache = {}
        self.handles = []
        self.ocr_progress = None
        self.ocr_worker = None

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render_visible)

        self.stamp_id = fitz.STAMP_Approved
        self.stamp_label = "Approved"
        self.settings = version.settings()
        self.comments_stale = True
        self.thumb_queue = []
        self.thumb_timer = QTimer(self)
        self.thumb_timer.setInterval(15)
        self.thumb_timer.timeout.connect(self._render_some_thumbs)
        # repairing damaged text (see textrepair), a page at a time in the background
        self.repair_todo = []
        self.repair_done = 0
        self.repair_timer = QTimer(self)
        self.repair_timer.setInterval(0)
        self.repair_timer.timeout.connect(self._repair_some)

        self.doc_name = None
        self.orig_bytes = None
        self.orig_password = None
        self.signed_count = 0
        self.sign_warned = False
        self.form_kind = "text"
        self.form_items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._build_tab_bar())
        lay.addWidget(self._build_top_bar())
        lay.addWidget(self._build_tool_bar())
        lay.addWidget(self._build_props_bar())
        lay.addWidget(self._build_search_bar())
        lay.addWidget(self._build_banner())

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.canvas = Canvas(self)
        self.stack.addWidget(self._build_start_page())
        self.stack.addWidget(self.canvas)
        body.addWidget(self.stack, 1)
        lay.addLayout(body, 1)
        lay.addWidget(self._build_status_bar())

        for seq, fn in ((QKeySequence.Open, self.open_dialog), (QKeySequence.Save, self.save),
                        ("Ctrl+Shift+S", self.save_as), (QKeySequence.Undo, self.undo),
                        ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        (QKeySequence.Find, self.show_search), (QKeySequence.Print, self.print_doc),
                        ("Ctrl+Shift+P", self.print_preview),
                        ("Ctrl+=", lambda: self.zoom_by(1.2)), ("Ctrl++", lambda: self.zoom_by(1.2)),
                        ("Ctrl+-", lambda: self.zoom_by(1 / 1.2)),
                        ("Ctrl+0", lambda: self.on_zoom_text("Fit page")),
                        ("Ctrl+1", lambda: self.on_zoom_text("100%")),
                        ("Ctrl+2", lambda: self.on_zoom_text("Fit width")),
                        (QKeySequence.Copy, self.copy_text), (QKeySequence.Delete, self.delete_selected),
                        (QKeySequence.Cut, self.cut_selected), (QKeySequence.Paste, self.paste),
                        (QKeySequence.New, self.new_blank_pdf)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.update_ui()

    # ------------------------------------------------------------------ UI build
    @staticmethod
    def _bar(name="bar"):
        f = QFrame()
        f.setObjectName(name)
        h = QHBoxLayout(f)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(4)
        return f, h

    @staticmethod
    def _vsep():
        s = QFrame()
        s.setObjectName("vsep")
        s.setFixedSize(1, 22)
        return s

    def _tbtn(self, text, icon_name, tip, fn=None, checkable=False, text_beside=True):
        b = QToolButton()
        b.setText(text)
        b.setIcon(icons.icon(icon_name))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text_beside and text
                             else Qt.ToolButtonIconOnly)
        b.setCheckable(checkable)
        b.setProperty("icon_name", icon_name)
        b.setCursor(Qt.PointingHandCursor)
        if fn:
            b.clicked.connect(fn)
        return b

    def _build_top_bar(self):
        f, bar = self._bar()
        bar.addWidget(self._tbtn("Open", "open", "Open a PDF (Ctrl+O)", self.open_dialog))
        self.add_btn = self._tbtn("Add PDFs", "add", "Add the pages of other PDFs (or Word, Excel, "
                                  "PowerPoint files and pictures) into this PDF",
                                  self.add_files_dialog)
        self.add_btn.setObjectName("accentTool")
        bar.addWidget(self.add_btn)
        self.scan_btn = self._tbtn("Scan", "scan", "Scan paper into a PDF", self.scan_dialog)
        bar.addWidget(self.scan_btn)
        # Save is not in this bar: the main window shows it at the top right, next to
        # "100% offline" (see MainWindow)
        self.save_btn = self._tbtn("Save", "save", "Save (Ctrl+S)", self.save)
        self.save_btn.setObjectName("accentTool")
        bar.addWidget(self._vsep())
        self.undo_btn = self._tbtn("", "undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_btn = self._tbtn("", "redo", "Redo (Ctrl+Y)", self.redo)
        self.print_btn = self._tbtn("", "print", "Print (Ctrl+P) - the arrow has Print preview",
                                    self.print_doc)
        self.print_btn.setPopupMode(QToolButton.MenuButtonPopup)
        pm = QMenu(self)
        pm.addAction(icons.icon("print"), "Print…", self.print_doc)
        pm.addAction(icons.icon("search"), "Print preview…", self.print_preview)
        self.print_btn.setMenu(pm)
        self.find_btn = self._tbtn("", "search", "Find text (Ctrl+F)", self.show_search)
        self.more_btn = self._tbtn("", "more", "More file options")
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.file_menu = QMenu(self)
        self.file_menu.aboutToShow.connect(self._fill_file_menu)
        self.more_btn.setMenu(self.file_menu)
        for b in (self.undo_btn, self.redo_btn, self.print_btn, self.find_btn, self.more_btn):
            bar.addWidget(b)
        bar.addStretch()
        self.prev_btn = self._tbtn("", "chevleft", "Previous page",
                                   lambda: self.goto_page(self.current_page() - 1))
        self.next_btn = self._tbtn("", "chevright", "Next page",
                                   lambda: self.goto_page(self.current_page() + 1))
        self.page_spin = PageBox()
        self.page_spin.valueChanged.connect(lambda v: self.goto_page(v - 1))
        self.page_total = QLabel("/ 0")
        self.page_total.setObjectName("subtitle")
        for w in (self.prev_btn, self.page_spin, self.page_total, self.next_btn):
            bar.addWidget(w)
        bar.addWidget(self._vsep())
        self.zoom_out_btn = self._tbtn("", "zoomout", "Zoom out (Ctrl+−)", lambda: self.zoom_by(1 / 1.2))
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.addItems(["Fit width", "Fit page", "50%", "75%", "100%", "125%",
                                "150%", "200%", "300%", "400%"])
        self.zoom_box.setFixedWidth(104)
        self.zoom_box.textActivated.connect(self.on_zoom_text)
        self.zoom_in_btn = self._tbtn("", "zoomin", "Zoom in (Ctrl+=)", lambda: self.zoom_by(1.2))
        for w in (self.zoom_out_btn, self.zoom_box, self.zoom_in_btn):
            bar.addWidget(w)
        return f

    def _build_tool_bar(self):
        f, bar = self._bar()
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons = {}
        self.menu_buttons = {}
        for entry in TOOLBAR:
            if entry[0] == "|":
                bar.addWidget(self._vsep())
                continue
            if entry[0] == "menu":
                _, label, icon_name, tip, members = entry
                b = self._tbtn(label, icon_name, tip, checkable=True)
                b.setPopupMode(QToolButton.InstantPopup)
                b.setObjectName("menuTool")
                m = QMenu(self)
                if label == "Stamp":
                    for text, sid in pdftools.STAMPS:
                        m.addAction(text, lambda s=sid, t=text: self.choose_stamp(s, t))
                    ids = {"stamp"}
                else:
                    for tid, text in members:
                        if tid.startswith("@"):
                            if tid == "@detect_fields" or tid == "@signatures_dialog" or \
                                    tid == "@find_redact_dialog":
                                m.addSeparator()
                            m.addAction(icons.icon(TOOL_ICONS.get(tid, icon_name)), text,
                                        lambda _=False, f=tid[1:]: getattr(self, f)())
                        else:
                            m.addAction(icons.icon(TOOL_ICONS.get(tid, icon_name)), text,
                                        lambda _=False, t=tid: self.set_tool(t))
                    ids = {t.split(":")[0] for t, _ in members if not t.startswith("@")}
                b.setMenu(m)
                self.tool_group.addButton(b)
                self.menu_buttons[label] = (b, ids)
                bar.addWidget(b)
                continue
            if entry[0] == "split":  # click = the tool, arrow = its menu
                _, tid, label, icon_name, tip, members = entry
                b = self._tbtn(label, icon_name, tip, lambda _=False, t=tid: self.set_tool(t), True)
                b.setPopupMode(QToolButton.MenuButtonPopup)
                m = QMenu(self)
                hl = m.addMenu(icons.icon(icon_name), "Highlight colour")
                for text, col in HIGHLIGHT_COLORS:
                    hl.addAction(swatch_icon(col), text, lambda c=col: self.set_highlight_color(c))
                m.addSeparator()
                for mid, text in members:
                    if mid.startswith("@"):
                        if mid == "@find_redact_dialog":
                            m.addSeparator()
                        m.addAction(icons.icon(TOOL_ICONS.get(mid, icon_name)), text,
                                    lambda _=False, f=mid[1:]: getattr(self, f)())
                    else:
                        m.addAction(icons.icon(TOOL_ICONS.get(mid, icon_name)), text,
                                    lambda _=False, x=mid: self.set_tool(x))
                b.setMenu(m)
                self.tool_group.addButton(b)
                self.menu_buttons[label] = (b, {tid} | {x for x, _ in members if not x.startswith("@")})
                self.tool_buttons[tid] = b
                bar.addWidget(b)
                continue
            tid, label, icon_name, tip = entry
            b = self._tbtn(label, icon_name, tip, lambda _=False, t=tid: self.set_tool(t), True)
            self.tool_group.addButton(b)
            self.tool_buttons[tid] = b
            bar.addWidget(b)
        bar.addStretch()
        self.all_tools_btn = self._tbtn("All tools", "tools", "Page numbers, watermark, split, "
                                        "crop, flatten, protect, export and more")
        self.all_tools_btn.setObjectName("accentTool")
        self.all_tools_btn.setPopupMode(QToolButton.InstantPopup)
        self.tools_menu = QMenu(self)
        self.tools_menu.aboutToShow.connect(self._fill_tools_menu)
        self.all_tools_btn.setMenu(self.tools_menu)
        bar.addWidget(self.all_tools_btn)
        return f

    def _build_props_bar(self):
        f, bar = self._bar("props")
        self.props_label = QLabel("")
        self.props_label.setObjectName("section")
        bar.addWidget(self.props_label)
        bar.addSpacing(6)
        self.font_box = QComboBox()
        self.font_box.setEditable(True)
        self.font_box.addItems(fonts.FAMILY_NAMES)
        self.font_box.setCurrentText(fonts.DEFAULT_FAMILY)
        self.font_box.setFixedWidth(190)
        self.font_box.setToolTip("Font")
        self.font_box.currentTextChanged.connect(self.on_text_props)
        self.size_box = QComboBox()
        self.size_box.setEditable(True)
        self.size_box.addItems([str(s) for s in fonts.SIZES])
        self.size_box.setCurrentText("12")
        self.size_box.setFixedWidth(64)
        self.size_box.setToolTip("Font size (points)")
        self.size_box.currentTextChanged.connect(self.on_text_props)
        self.bold_btn = QToolButton()
        self.italic_btn = QToolButton()
        for b, txt, tip in ((self.bold_btn, "B", "Bold"), (self.italic_btn, "I", "Italic")):
            b.setText(txt)
            b.setObjectName("fmt")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedSize(30, 30)
            b.toggled.connect(self.on_text_props)
        fb = self.bold_btn.font()
        fb.setBold(True)
        self.bold_btn.setFont(fb)
        fi = self.italic_btn.font()
        fi.setItalic(True)
        self.italic_btn.setFont(fi)
        self.width_label = QLabel("Thickness")
        self.width_label.setObjectName("subtitle")
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20)
        self.width_spin.setValue(2)
        self.width_spin.setSuffix(" pt")
        self.width_spin.setToolTip("Line thickness")
        self.color_label = QLabel("Colour")
        self.color_label.setObjectName("subtitle")
        self.color_btn = QPushButton()
        self.color_btn.setObjectName("swatch")
        self.color_btn.setFixedSize(30, 26)
        self.color_btn.setToolTip("Colour")
        self.color_btn.clicked.connect(self.pick_color)
        for w in (self.font_box, self.size_box, self.bold_btn, self.italic_btn,
                  self.width_label, self.width_spin, self.color_label, self.color_btn):
            bar.addWidget(w)
        bar.addStretch()
        self.props_hint = QLabel("")
        self.props_hint.setObjectName("subtitle")
        bar.addWidget(self.props_hint)
        self.props_bar = f
        return f

    def _build_sidebar(self):
        wrap = QFrame()
        wrap.setObjectName("sidebar")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(4, 6, 4, 6)
        h.setSpacing(6)
        rail = QVBoxLayout()
        rail.setSpacing(4)
        self.side_group = QButtonGroup(self)
        self.side_buttons = []
        for k, (name, tip) in enumerate((("pages", "Pages"), ("bookmark", "Bookmarks"),
                                         ("comments", "Comments"))):
            b = self._tbtn("", name, tip, checkable=True, text_beside=False)
            b.setObjectName("rail")
            b.setProperty("icon_soft", True)
            b.setIcon(icons.icon(name, soft=True))
            b.setFixedSize(36, 36)
            b.clicked.connect(lambda _=False, i=k: self.show_side_panel(i))
            self.side_group.addButton(b, k)
            self.side_buttons.append(b)
            rail.addWidget(b)
        rail.addStretch()
        h.addLayout(rail)
        self.side_stack = QStackedWidget()
        self.side_stack.setFixedWidth(176)
        self.side_stack.addWidget(self._build_pages_panel())
        self.side_stack.addWidget(self._build_bookmarks_panel())
        self.side_stack.addWidget(self._build_comments_panel())
        h.addWidget(self.side_stack)
        self.side_buttons[0].setChecked(True)
        return wrap

    def _panel(self, title, buttons=()):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        head = QHBoxLayout()
        lbl = QLabel(title.upper())
        lbl.setObjectName("section")
        head.addWidget(lbl)
        head.addStretch()
        for icon_name, tip, fn in buttons:
            b = self._tbtn("", icon_name, tip, fn, text_beside=False)
            b.setObjectName("mini")
            b.setFixedSize(26, 26)
            head.addWidget(b)
            if not hasattr(self, "panel_buttons"):
                self.panel_buttons = []
            if fn != self.add_files_dialog:  # "+" stays on: with no PDF it opens the chosen files
                self.panel_buttons.append(b)
        v.addLayout(head)
        return w, v

    def _build_pages_panel(self):
        w, v = self._panel("Pages", [("rotate", "Rotate selected pages right",
                                      lambda: self.rotate_pages(90)),
                                     ("plus", "Import a PDF (or Word, Excel, picture) into this one",
                                      self.add_files_dialog),
                                     ("trash", "Delete selected pages", self.delete_pages),
                                     ("more", "More page options", lambda: self.page_menu(None, None))])
        self.thumbs = PageList()
        self.thumbs.setObjectName("thumbs")
        self.thumbs.setIconSize(QSize(120, 150))
        self.thumbs.setSpacing(4)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self.page_menu)
        self.thumbs.itemClicked.connect(lambda it: self.goto_page(self.thumbs.row(it)))
        self.thumbs.pages_dropped.connect(self.reorder_pages)
        self.thumbs.files_dropped.connect(lambda paths, at: self.add_files(paths, at=at))
        self.thumbs.setToolTip("Drag pages to put them in a new order (or drop PDFs here to add "
                               "them). Right-click for more.")
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbs.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        v.addWidget(self.thumbs, 1)
        return w

    def _build_bookmarks_panel(self):
        w, v = self._panel("Bookmarks", [("plus", "Add a bookmark for the current page",
                                          self.add_bookmark),
                                         ("rename", "Rename the selected bookmark",
                                          self.rename_bookmark),
                                         ("trash", "Delete the selected bookmark",
                                          self.delete_bookmark)])
        self.bm_tree = QTreeWidget()
        self.bm_tree.setObjectName("tree")
        self.bm_tree.setHeaderHidden(True)
        self.bm_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bm_tree.setTextElideMode(Qt.ElideRight)
        self.bm_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.bm_tree.setEditTriggers(QAbstractItemView.EditKeyPressed |
                                     QAbstractItemView.SelectedClicked)
        self.bm_tree.itemClicked.connect(self._bookmark_clicked)
        self.bm_tree.itemChanged.connect(lambda *_: self._commit_bookmarks("Bookmark renamed."))
        self.bm_tree.model().rowsMoved.connect(
            lambda *_: QTimer.singleShot(0, lambda: self._commit_bookmarks("Bookmarks reordered.")))
        v.addWidget(self.bm_tree, 1)
        self.bm_empty = QLabel("No bookmarks yet.\nClick ＋ to bookmark the current page.")
        self.bm_empty.setObjectName("subtitle")
        self.bm_empty.setAlignment(Qt.AlignCenter)
        self.bm_empty.setWordWrap(True)
        v.addWidget(self.bm_empty)
        return w

    def _build_comments_panel(self):
        w, v = self._panel("Comments", [("trash", "Delete the selected comment",
                                         self.delete_comment)])
        self.comments_list = QListWidget()
        self.comments_list.setObjectName("comments")
        self.comments_list.setWordWrap(True)
        self.comments_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.comments_list.setResizeMode(QListWidget.Adjust)
        self.comments_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.comments_list.itemClicked.connect(self._comment_clicked)
        v.addWidget(self.comments_list, 1)
        self.comments_empty = QLabel("No comments in this PDF.")
        self.comments_empty.setObjectName("subtitle")
        self.comments_empty.setAlignment(Qt.AlignCenter)
        v.addWidget(self.comments_empty)
        return w

    def _build_start_page(self):
        empty = QFrame()
        empty.setObjectName("drop")
        el = QVBoxLayout(empty)
        el.setSpacing(8)
        el.addStretch(2)
        ic = QLabel()
        ic.setPixmap(icons.icon("pages", "#7C83FF").pixmap(56, 56))
        ic.setAlignment(Qt.AlignCenter)
        el.addWidget(ic)
        for text, name in (("Open a PDF to get started", "dropTitle"),
                           ("Edit text and pictures · comment · sign · fill forms · "
                            "organize pages · add page numbers and watermarks", "dropHint")):
            l = QLabel(text)
            l.setObjectName(name)
            l.setAlignment(Qt.AlignCenter)
            l.setWordWrap(True)
            el.addWidget(l)
        row = QHBoxLayout()
        row.addStretch()
        ob = QPushButton("  Open PDF…")
        ob.setIcon(icons.icon("open"))
        ob.setObjectName("primarySmall")
        ob.clicked.connect(self.open_dialog)
        cb = QPushButton("  Combine files…")
        cb.setIcon(icons.icon("add"))
        cb.setToolTip("Pick several files: they are joined into one PDF you can edit and save")
        cb.clicked.connect(self.combine_files_dialog)
        sb = QPushButton("  Scan…")
        sb.setIcon(icons.icon("scan"))
        sb.setProperty("icon_name", "scan")
        sb.setToolTip("Scan paper straight into a PDF")
        sb.clicked.connect(self.scan_dialog)
        nb = QPushButton("  New blank PDF…")
        nb.setIcon(icons.icon("plus"))
        nb.setProperty("icon_name", "plus")
        nb.setToolTip("Start a new PDF with blank pages (Ctrl+N)")
        nb.clicked.connect(self.new_blank_pdf)
        row.addWidget(ob)
        row.addWidget(nb)
        row.addWidget(cb)
        row.addWidget(sb)
        row.addStretch()
        el.addSpacing(8)
        el.addLayout(row)
        el.addSpacing(18)
        self.recent_label = QLabel("RECENT FILES")
        self.recent_label.setObjectName("section")
        self.recent_label.setAlignment(Qt.AlignCenter)
        el.addWidget(self.recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recent")
        self.recent_list.setFixedWidth(520)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent_list.setTextElideMode(Qt.ElideMiddle)
        self.recent_list.setMaximumHeight(190)
        self.recent_list.itemActivated.connect(lambda it: self.open_file(it.data(Qt.UserRole)))
        self.recent_list.itemClicked.connect(lambda it: self.open_file(it.data(Qt.UserRole)))
        rl = QHBoxLayout()
        rl.addStretch()
        rl.addWidget(self.recent_list)
        rl.addStretch()
        el.addLayout(rl)
        el.addStretch(3)
        return empty

    def _build_status_bar(self):
        f = QFrame()
        f.setObjectName("statusbar")
        h = QHBoxLayout(f)
        h.setContentsMargins(4, 0, 4, 0)
        self.status = QLabel("")
        self.status.setObjectName("status")
        h.addWidget(self.status, 1)
        self.size_label = QLabel("")
        self.size_label.setObjectName("status")
        h.addWidget(self.size_label)
        return f

    def _build_search_bar(self):
        self.search_bar = QFrame()
        sl = QHBoxLayout(self.search_bar)
        sl.setContentsMargins(0, 0, 0, 0)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find in document…")
        self.search_edit.returnPressed.connect(self.search_next)
        self.search_edit.textChanged.connect(lambda: setattr(self, "search_hits", []))
        self.search_info = QLabel("")
        self.search_info.setObjectName("subtitle")
        self.search_bar.setObjectName("bar")
        sl.setContentsMargins(8, 4, 8, 4)
        prev_b = self._tbtn("", "chevleft", "Previous match (Shift+Enter)")
        next_b = self._tbtn("", "chevright", "Next match (Enter)")
        close_b = self._tbtn("", "close", "Close (Esc)")
        prev_b.clicked.connect(lambda: self.search_next(-1))
        next_b.clicked.connect(lambda: self.search_next(1))
        close_b.clicked.connect(self.hide_search)
        for w in (self.search_edit, self.search_info, prev_b, next_b, close_b):
            sl.addWidget(w)
        sl.setStretch(0, 1)
        self.search_bar.hide()
        return self.search_bar

    # ------------------------------------------------------------------ state
    def update_ui(self):
        has = self.doc is not None
        self.stack.setCurrentIndex(1 if has else 0)
        for w in self.panel_buttons + [self.save_btn, self.page_spin, self.zoom_box, self.thumbs,
                  self.print_btn, self.find_btn, self.prev_btn, self.next_btn, self.zoom_in_btn,
                  self.zoom_out_btn, self.all_tools_btn, self.bm_tree, self.comments_list] + \
                list(self.tool_buttons.values()) + [b for b, _ in self.menu_buttons.values()]:
            w.setEnabled(has)
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))
        n = self.doc.page_count if has else 0
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(n, 1))
        self.page_spin.blockSignals(False)
        self.page_total.setText(f"of {n}")
        name = (Path(self.path).name if self.path else (self.doc_name or "Untitled.pdf")) \
            if has else ""
        self.title_changed.emit((name + (" •" if self.dirty else "")) if has else "")
        self._update_tab_title()
        self.save_btn.setText("Save •" if self.dirty else "Save")
        # the active tool: exactly one tool button (or menu button) lights up
        t = self.tool
        self.tool_group.setExclusive(False)
        for tid, b in self.tool_buttons.items():
            b.setChecked(tid == t)
        for _, (b, members) in self.menu_buttons.items():
            b.setChecked(t in members)
        self.tool_group.setExclusive(True)
        # the properties bar shows what the tool needs
        is_text = t in TEXT_TOOLS
        for w in (self.font_box, self.size_box, self.bold_btn, self.italic_btn):
            w.setVisible(is_text)
        self.width_label.setVisible(t in STROKE_TOOLS)
        self.width_spin.setVisible(t in STROKE_TOOLS)
        has_color = t in self.colors
        self.color_label.setVisible(has_color and not is_text)
        self.color_btn.setVisible(has_color)
        if has_color:
            self.color_btn.setStyleSheet(
                f"QPushButton#swatch {{ background: {self.colors[t].name()}; "
                "border: 2px solid #3B4260; border-radius: 7px; }}")
        label = {"edit": "TEXT", "addtext": "TEXT",
                 "highlight": "REDACT" if is_redact_color(self.colors["highlight"]) else "HIGHLIGHT",
                 "underline": "UNDERLINE", "strike": "STRIKETHROUGH", "note": "NOTE",
                 "textbox": "TEXT BOX", "pen": "PEN", "rect": "RECTANGLE", "ellipse": "ELLIPSE",
                 "line": "LINE", "arrow": "ARROW", "stamp": "STAMP", "crop": "CROP",
                 "redact": "REDACT", "whiteout": "WHITEOUT", "image": "PICTURE",
                 "sign": "SIGN", "form": "FORM: " + forms.LABELS.get(self.form_kind, "").upper(),
                 "certsign": "DIGITAL SIGNATURE"}.get(t, "")
        self.props_label.setText(label)
        hint = TOOL_HINTS.get(t, "") if t not in ("select",) else ""
        if t == "highlight":
            hint = ("Black removes the text you drag across for good (redaction). Pick a "
                    "colour to highlight instead." if is_redact_color(self.colors[t])
                    else "Drag across the text to highlight it. Pick black to redact instead.")
        self.props_hint.setText(hint)
        self.props_bar.setVisible(has and t != "select")
        self.recent_label.setVisible(bool(self.recent_files()))
        self.recent_list.setVisible(bool(self.recent_files()))
        if not has:
            self._fill_recent_list()
        self.update_size_label()

    def set_status(self, text):
        self.status.setText(text)

    def update_size_label(self):
        if self.doc is None or not self.items:
            self.size_label.setText("")
            return
        r = self.doc[self.current_page()].rect
        self.size_label.setText(f"{r.width / 72:.2f} × {r.height / 72:.2f} in   ·   "
                                f"{round(self.zoom * 100)}%")

    # ------------------------------------------------------------------ menus
    def _fill_file_menu(self):
        m = self.file_menu
        m.clear()
        m.addAction(icons.icon("plus"), "New blank PDF…", self.new_blank_pdf).setShortcut("Ctrl+N")
        m.addAction(icons.icon("save"), "Save as…", self.save_as).setShortcut("Ctrl+Shift+S")
        rec = m.addMenu(icons.icon("open"), "Open recent")
        files = self.recent_files()
        for p in files:
            rec.addAction(Path(p).name, lambda p=p: self.open_file(p)).setToolTip(p)
        rec.setEnabled(bool(files))
        if files:
            rec.addSeparator()
            rec.addAction("Clear list", self.clear_recent)
        m.addSeparator()
        m.addAction(icons.icon("info"), "Document properties…", self.properties_dialog)
        m.addAction(icons.icon("print"), "Print…", self.print_doc).setShortcut("Ctrl+P")
        m.addAction(icons.icon("search"), "Print preview…", self.print_preview).setShortcut("Ctrl+Shift+P")
        m.addSeparator()
        m.addAction(icons.icon("close"), "Close PDF", self.close_doc)

    def _fill_tools_menu(self):
        m = self.tools_menu
        m.clear()

        def sec(title):
            a = m.addAction(title)
            a.setEnabled(False)
            f = a.font()
            f.setBold(True)
            a.setFont(f)

        sec("SCAN")
        m.addAction(icons.icon("scan"), "Scan paper…", self.scan_dialog)
        a = m.addAction(icons.icon("ocr"), "Recognize text (OCR)…", self.ocr_dialog)
        if not ocr.available():
            a.setEnabled(False)
            a.setToolTip("Needs Windows 10 or 11 with a text-recognition language installed")
        m.addSeparator()
        sec("ORGANIZE")
        org = m.addMenu(icons.icon("pages"), "Organize pages")
        self._fill_page_menu(org)
        m.addAction(icons.icon("add"), "Combine files…", self.combine_files_dialog)
        m.addAction(icons.icon("convert"), "Split PDF…", self.split_dialog)
        m.addAction(icons.icon("search"), "Compare files…", self.compare_dialog)
        m.addAction(icons.icon("tools"), "Batch process PDFs…", self.batch_dialog)
        m.addAction(icons.icon("crop"), "Crop pages", lambda: self.set_tool("crop"))
        m.addSeparator()
        sec("ADD")
        m.addAction(icons.icon("addtext"), "Page numbers / header && footer…",
                    self.header_footer_dialog)
        m.addAction(icons.icon("stamp"), "Watermark…", self.watermark_dialog)
        st = m.addMenu(icons.icon("stamp"), "Stamp")
        for text, sid in pdftools.STAMPS:
            st.addAction(text, lambda s=sid, t=text: self.choose_stamp(s, t))
        m.addAction(icons.icon("bookmark"), "Bookmark this page", self.add_bookmark)
        m.addSeparator()
        sec("FORMS")
        m.addAction(icons.icon("search"), "Detect form fields…", self.detect_fields)
        m.addAction(icons.icon("form"), "Add form fields", lambda: self.set_tool("form:text"))
        m.addSeparator()
        sec("PROTECT")
        m.addAction(icons.icon("redact"), "Redact an area", lambda: self.set_tool("redact"))
        m.addAction(icons.icon("search"), "Find && redact…", self.find_redact_dialog)
        m.addAction(icons.icon("trash"), "Remove hidden information…", self.sanitize_dialog)
        m.addAction(icons.icon("certificate"), "Sign with a digital ID…",
                    lambda: self.set_tool("certsign"))
        m.addAction(icons.icon("certificate"), "Check digital signatures", self.signatures_dialog)
        m.addAction(icons.icon("lock"), "Password protect…", self.protect)
        a = m.addAction("Remove password", self.unprotect)
        a.setEnabled(bool(self.password))
        m.addAction(icons.icon("edit"), "Flatten comments && form fields…", self.flatten_doc)
        m.addSeparator()
        sec("SHARE")
        m.addAction(icons.icon("save"), "Reduce file size…", self.save_compressed)
        exp = m.addMenu(icons.icon("convert"), "Export PDF to")
        for label, name in (("DOCX", "Word (.docx)"), ("XLSX", "Excel (.xlsx)"),
                            ("TXT", "Text (.txt)"), ("PNG", "Pictures (.png)"),
                            ("JPG", "Pictures (.jpg)")):
            exp.addAction(name, lambda l=label: self.export(l))
        m.addAction(icons.icon("info"), "Document properties…", self.properties_dialog)

    # ------------------------------------------------------------------ recent files
    def recent_files(self):
        v = self.settings.value("recent", []) or []
        if isinstance(v, str):
            v = [v]
        return [p for p in v if Path(p).exists()][:8]

    def add_recent(self, path):
        if not path:
            return
        files = [p for p in self.recent_files() if Path(p) != Path(path)]
        self.settings.setValue("recent", [str(path)] + files[:7])

    def clear_recent(self):
        self.settings.setValue("recent", [])
        self.update_ui()

    def _fill_recent_list(self):
        self.recent_list.clear()
        for p in self.recent_files():
            it = QListWidgetItem(icons.icon("pages"), f"{Path(p).name}    —    {Path(p).parent}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.recent_list.addItem(it)

    # ------------------------------------------------------------------ side panels
    def show_side_panel(self, i):
        self.side_stack.setCurrentIndex(i)
        self.side_buttons[i].setChecked(True)
        if i == 1:
            self.load_bookmarks()
        elif i == 2 and self.comments_stale:
            self.refresh_comments()

    def load_bookmarks(self):
        self.bm_tree.blockSignals(True)
        self.bm_tree.clear()
        toc = self.doc.get_toc() if self.doc is not None else []
        parents = {0: self.bm_tree.invisibleRootItem()}
        for lvl, title, page in toc:
            parent = parents.get(lvl - 1, self.bm_tree.invisibleRootItem())
            it = QTreeWidgetItem(parent, [title])
            it.setData(0, Qt.UserRole, page - 1)
            it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            it.setToolTip(0, f"Page {page}" if page > 0 else "")
            parents[lvl] = it
        self.bm_tree.expandAll()
        self.bm_tree.blockSignals(False)
        self.bm_empty.setVisible(not toc)

    def _tree_to_toc(self):
        toc = []

        def walk(item, lvl):
            for k in range(item.childCount()):
                c = item.child(k)
                page = c.data(0, Qt.UserRole)
                toc.append([lvl, c.text(0).strip() or "Untitled", (page if page is not None else -1) + 1])
                walk(c, lvl + 1)

        walk(self.bm_tree.invisibleRootItem(), 1)
        return toc

    def _commit_bookmarks(self, msg):
        if self.doc is None:
            return
        toc = self._tree_to_toc()
        if toc == [list(t) for t in self.doc.get_toc()]:
            return
        self.checkpoint()
        self.doc.set_toc(toc)
        self.mark_dirty()
        self.bm_empty.setVisible(not toc)
        self.set_status(msg)

    @needs_doc
    def add_bookmark(self):
        if self.doc is None:
            return
        self.show_side_panel(1)
        i = self.current_page()
        default = f"Page {i + 1}"
        if self.selected and self.selected[0] == "text":
            default = " ".join(w[4] for w in self.selected[2])[:80]
        title, ok = QInputDialog.getText(self, "Add bookmark", "Bookmark name:", text=default)
        if not ok or not title.strip():
            return
        toc = [list(t) for t in self.doc.get_toc()]
        # keep bookmarks in page order at the top level
        pos = next((k for k, t in enumerate(toc) if t[0] == 1 and t[2] - 1 > i), len(toc))
        toc.insert(pos, [1, title.strip(), i + 1])
        self.checkpoint()
        self.doc.set_toc(toc)
        self.load_bookmarks()
        if self.tool == "form":
            self._show_form_outlines()
        self.mark_dirty()
        self.set_status(f"Bookmark “{title.strip()}” added for page {i + 1}.")

    @needs_doc
    def rename_bookmark(self):
        it = self.bm_tree.currentItem()
        if it is not None:
            self.bm_tree.editItem(it, 0)

    @needs_doc
    def delete_bookmark(self):
        it = self.bm_tree.currentItem()
        if it is None:
            return
        (it.parent() or self.bm_tree.invisibleRootItem()).removeChild(it)
        self._commit_bookmarks("Bookmark deleted.")
        self.load_bookmarks()

    def _bookmark_clicked(self, it, _col=0):
        page = it.data(0, Qt.UserRole)
        if page is not None and page >= 0:
            self.goto_page(page)

    COMMENT_TYPES = {fitz.PDF_ANNOT_TEXT: "Note", fitz.PDF_ANNOT_HIGHLIGHT: "Highlight",
                     fitz.PDF_ANNOT_UNDERLINE: "Underline", fitz.PDF_ANNOT_STRIKE_OUT: "Strikethrough",
                     fitz.PDF_ANNOT_FREE_TEXT: "Text box", fitz.PDF_ANNOT_INK: "Drawing",
                     fitz.PDF_ANNOT_SQUARE: "Rectangle", fitz.PDF_ANNOT_CIRCLE: "Ellipse",
                     fitz.PDF_ANNOT_LINE: "Line", fitz.PDF_ANNOT_STAMP: "Stamp",
                     fitz.PDF_ANNOT_POLYGON: "Shape", fitz.PDF_ANNOT_POLY_LINE: "Shape",
                     fitz.PDF_ANNOT_SQUIGGLY: "Squiggly", fitz.PDF_ANNOT_CARET: "Caret",
                     fitz.PDF_ANNOT_FILE_ATTACHMENT: "Attachment"}

    def refresh_comments(self):
        self.comments_list.clear()
        self.comments_stale = False
        if self.doc is None:
            self.comments_empty.show()
            return
        n = 0
        for i, page in enumerate(self.doc):
            for a in page.annots():
                kind = self.COMMENT_TYPES.get(a.type[0])
                if not kind:
                    continue
                info = a.info
                text = (info.get("content") or "").strip()
                if not text and a.type[0] in (fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE,
                                              fitz.PDF_ANNOT_STRIKE_OUT):
                    # whole words whose centre lies inside the marked area
                    ar = fitz.Rect(a.rect)
                    text = " ".join(w[4] for w in page.get_text("words")
                                    if ar.contains(fitz.Rect(w[:4]).tl + (fitz.Rect(w[:4]).br - fitz.Rect(w[:4]).tl) * 0.5))
                if a.type[0] == fitz.PDF_ANNOT_STAMP and not text:
                    text = info.get("subject", "") or ""
                author = info.get("title", "")
                head = f"{kind}  ·  page {i + 1}" + (f"  ·  {author}" if author else "")
                body = (text[:120] + "…") if len(text) > 120 else text
                it = QListWidgetItem(head + ("\n" + body if body else ""))
                it.setData(Qt.UserRole, (i, a.xref))
                it.setSizeHint(self._card_size(it.text()))
                self.comments_list.addItem(it)
                n += 1
        self.comments_empty.setVisible(n == 0)

    def _card_size(self, text):
        """Height of a comment card so its whole text shows at the panel's width."""
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtCore import QRect
        width = max(self.side_stack.width(), 176) - 30
        fm = QFontMetrics(self.comments_list.font())
        r = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, text)
        return QSize(width, r.height() + 22)

    def _comment_clicked(self, it):
        i, xref = it.data(Qt.UserRole)
        if self.tool != "select":
            self.set_tool("select")
        self.goto_page(i)
        a = self.find_annot(self.doc[i], xref)
        if a is not None:
            self.canvas.centerOn(self.to_scene_rect(i, a.rect).center())
            self.select_annot(i, a)

    @needs_doc
    def delete_comment(self):
        it = self.comments_list.currentItem()
        if it is None:
            return
        i, xref = it.data(Qt.UserRole)
        page = self.doc[i]
        if self.find_annot(page, xref) is None:
            return
        self.checkpoint()
        page = self.doc[i]
        page.delete_annot(self.find_annot(page, xref))
        self.selected = None
        self.clear_overlay()
        self.page_changed(i)
        self.refresh_comments()
        self.set_status("Comment deleted.")

    # ------------------------------------------------------------------ stamps, tools
    @needs_doc
    def choose_stamp(self, sid, label):
        self.stamp_id, self.stamp_label = sid, label
        self.set_tool("stamp")
        self.set_status(f"Click where the “{label}” stamp should go.")

    def place_stamp(self, i, sp):
        w = 170 * (self.items[i].rect().width() / 612)
        h = w * 0.3
        a, b = QPointF(sp.x() - w / 2, sp.y() - h / 2), QPointF(sp.x() + w / 2, sp.y() + h / 2)
        r = self.to_pdf_rect(i, a, b)
        self.checkpoint()
        annot = pdftools.add_stamp(self.doc[i], r, self.stamp_id)
        if self.doc[i].rotation:
            annot.set_rotation(self.doc[i].rotation)
            annot.update()
        xref = annot.xref
        self.page_changed(i)
        self.set_tool("select")
        self.select_annot(i, self.find_annot(self.doc[i], xref))
        self.set_status(f"“{self.stamp_label}” stamp added. Drag it to move, drag a handle to resize.")

    def add_textbox(self, i, r):
        text, ok = QInputDialog.getMultiLineText(self, "Text box", "Text:")
        if not ok or not text.strip():
            return
        page = self.doc[i]
        try:
            size = max(6.0, float(self.size_box.currentText()))
        except ValueError:
            size = 11.0
        self.checkpoint()
        page = self.doc[i]
        a = page.add_freetext_annot(r, text, fontsize=min(size, 24), fontname="helv",
                                    text_color=rgb(self.colors["textbox"]),
                                    fill_color=(1, 1, 0.86), border_width=0.8,
                                    rotate=page.rotation)
        a.set_info(title=pdftools._user_name())
        a.update()
        xref = a.xref
        self.page_changed(i)
        self.set_tool("select")
        self.select_annot(i, self.find_annot(self.doc[i], xref))

    @needs_doc
    def apply_crop(self, i, r):
        box = QMessageBox(self)
        box.setWindowTitle("Crop pages")
        box.setText("Crop to the box you drew?")
        box.setInformativeText("Everything outside the box is hidden. You can undo this.")
        this = box.addButton("This page", QMessageBox.AcceptRole)
        sel = None
        pages = self.selected_pages()
        if len(pages) > 1:
            sel = box.addButton(f"Selected pages ({len(pages)})", QMessageBox.AcceptRole)
        allp = box.addButton("All pages", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        c = box.clickedButton()
        if c is this:
            targets = [i]
        elif sel is not None and c is sel:
            targets = pages
        elif c is allp:
            targets = list(range(self.doc.page_count))
        else:
            return
        self.checkpoint()
        n = pdftools.crop_pages(self.doc, r, targets)
        self.set_tool("select")
        self._after_pages(f"Cropped {n} page(s).")

    def _doc_changed_all(self, msg):
        """After a change that touches every page: redraw everything."""
        self.block_cache = {}
        for it in self.items:
            it.rendered = 0
        self.render_visible()
        self.build_thumbs()
        self.comments_stale = True
        if self.side_stack.currentIndex() == 2:
            self.refresh_comments()
        self.mark_dirty()
        self.set_status(msg)

    def _dialog_ok(self, dlg):
        self.commit_edit()
        return dlg.exec() == QDialog.Accepted

    @needs_doc
    def header_footer_dialog(self):
        if self.doc is None:
            return
        dlg = dialogs.HeaderFooterDialog(self, self.doc.page_count, self.current_page(),
                                         Path(self.path).stem if self.path else "Untitled")
        if not self._dialog_ok(dlg):
            return
        self.checkpoint()
        n = pdftools.add_header_footer(self.doc, **dlg.values())
        self._doc_changed_all(f"Added “{dlg.values()['template']}” to {n} page(s).")

    @needs_doc
    def watermark_dialog(self):
        if self.doc is None:
            return
        dlg = dialogs.WatermarkDialog(self, self.doc.page_count, self.current_page())
        if not self._dialog_ok(dlg):
            return
        self.checkpoint()
        n = pdftools.add_watermark(self.doc, **dlg.values())
        self._doc_changed_all(f"Watermark added to {n} page(s).")

    @needs_doc
    def split_dialog(self):
        if self.doc is None:
            return
        folder = str(Path(self.path).parent) if self.path else str(Path.home() / "Documents")
        stem = Path(self.path).stem if self.path else "Document"
        dlg = dialogs.SplitDialog(self, self.doc.page_count, bool(self.doc.get_toc()), folder, stem)
        if not self._dialog_ok(dlg):
            return
        v = dlg.values()
        try:
            files = pdftools.split_pdf(self.doc, v["out_dir"], v["stem"], mode=v["mode"],
                                       every=v["every"], ranges=v["ranges"])
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Split PDF", str(e))
            return
        r = QMessageBox.information(self, "Split PDF",
                                    f"Saved {len(files)} file(s) to\n{v['out_dir']}",
                                    QMessageBox.Open | QMessageBox.Ok)
        if r == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(v["out_dir"]))
        self.set_status(f"Split into {len(files)} file(s).")

    @needs_doc
    def flatten_doc(self):
        if self.doc is None:
            return
        if QMessageBox.question(
                self, "Flatten", "Make all comments, stamps, signatures and form fields a "
                "permanent part of the pages?\n\nThey will look the same but can no longer be "
                "edited, moved or filled in. You can undo this until you close the file.") \
                != QMessageBox.Yes:
            return
        self.checkpoint()
        pdftools.flatten(self.doc)
        self._after_pages("Comments and form fields flattened.")

    @needs_doc
    def properties_dialog(self):
        if self.doc is None:
            return
        dlg = dialogs.PropertiesDialog(self, self.doc, self.path, self.password)
        if not self._dialog_ok(dlg):
            return
        meta = {k: v for k, v in (self.doc.metadata or {}).items()
                if k not in ("format", "encryption") and v is not None}
        new = dict(meta, **dlg.values())
        if new == meta:
            return
        self.checkpoint()
        self.doc.set_metadata(new)
        self.mark_dirty()
        self.set_status("Document properties updated.")

    # ------------------------------------------------------------------ thumbnails (lazy)
    def thumb_icon(self, i):
        page = self.doc[i]
        z = 120 / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        return QIcon(QPixmap.fromImage(pixmap_to_qimage(pix)))

    def _placeholder_icon(self, i):
        r = self.doc[i].rect
        h = int(120 * r.height / max(r.width, 1))
        pm = QPixmap(120, max(10, min(h, 220)))
        pm.fill(QColor(theme.color("thumb")))
        return QIcon(pm)

    def build_thumbs(self):
        self.thumb_timer.stop()
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(self.doc.page_count):
            it = QListWidgetItem(self._placeholder_icon(i), str(i + 1))
            it.setData(Qt.UserRole, i)
            it.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(it)
        self.thumbs.blockSignals(False)
        # draw the first few now, the rest a few at a time so big PDFs open instantly
        self.thumb_queue = list(range(self.doc.page_count))
        self._render_some_thumbs(12)
        if self.thumb_queue:
            self.thumb_timer.start()

    def _render_some_thumbs(self, n=6):
        if self.doc is None:
            self.thumb_queue = []
        for _ in range(n):
            if not self.thumb_queue:
                break
            i = self.thumb_queue.pop(0)
            if i < self.thumbs.count() and i < self.doc.page_count:
                self.thumbs.item(i).setIcon(self.thumb_icon(i))
        if not self.thumb_queue:
            self.thumb_timer.stop()

    def update_thumb(self, i):
        if 0 <= i < self.thumbs.count():
            self.thumbs.item(i).setIcon(self.thumb_icon(i))
            if i in self.thumb_queue:
                self.thumb_queue.remove(i)

    # ------------------------------------------------------------------ files
    def open_dialog(self):
        if not self.maybe_save():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if p:
            self.open_file(p)

    def open_file(self, path):
        """Open a PDF in a new tab (or switch to it if it's already open)."""
        path = str(path)
        for k in range(self.tabbar.count()):
            st = self._tab_state(k)
            p = self.path if st is self._active_state else st.get("path")
            if p and Path(p).resolve() == Path(path).resolve():
                self.tabbar.setCurrentIndex(k)
                return True
        try:
            data = Path(path).read_bytes()
            doc = fitz.open("pdf", data)
        except Exception as e:
            QMessageBox.warning(self, "Can't open", f"This PDF could not be opened.\n\n{e}")
            return False
        password = None
        if doc.needs_pass:
            while True:
                pw, ok = QInputDialog.getText(self, "Password required",
                                              f"{Path(path).name} is protected. Password:",
                                              QLineEdit.Password)
                if not ok:
                    return False
                if doc.authenticate(pw):
                    password = pw
                    break
            # keep an unlocked working copy; the password is re-applied on save
            doc = fitz.open("pdf", doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE))
        self._open_in_tab(doc, path, password, orig=data)
        self.set_status(f"Opened {Path(path).name} — {doc.page_count} page(s).")
        return True

    @needs_doc
    def ocr_dialog(self):
        if self.doc is None:
            return
        if not ocr.available():
            QMessageBox.information(
                self, "Recognize text",
                "Text recognition uses the OCR built into Windows 10 and 11, and no "
                "recognition language is installed on this computer.\n\nAdd one in Windows "
                "Settings → Time & language → Language & region → (your language) → "
                "Language options → Optical character recognition.")
            return
        dlg = dialogs.OcrDialog(self, self.doc.page_count, self.current_page(), ocr.languages())
        if not self._dialog_ok(dlg):
            return
        v = dlg.values()
        self.run_ocr(v["pages"], v["lang"], v["skip_text"])

    @needs_doc
    def run_ocr(self, pages, lang=None, skip_text=True):
        """Recognize text on `pages` in the background, with a progress window."""
        self.commit_edit()
        self.checkpoint()
        self.ocr_count = 0
        self.ocr_error = None
        self.ocr_progress = QProgressDialog("Recognizing text…", "Stop", 0, len(pages), self)
        self.ocr_progress.setWindowTitle("Recognize text")
        self.ocr_progress.setMinimumDuration(300)
        self.ocr_progress.setAutoClose(False)  # closed by _ocr_finished
        self.ocr_progress.setAutoReset(False)
        self.ocr_progress.setWindowModality(Qt.WindowModal)
        self.ocr_progress.setValue(0)
        self.ocr_worker = OcrWorker(self.doc.tobytes(), pages, lang, skip_text)
        self.ocr_worker.page_done.connect(self._ocr_page_done)
        self.ocr_worker.progress.connect(self._ocr_progress)
        self.ocr_worker.failed.connect(lambda msg: setattr(self, "ocr_error", msg))
        self.ocr_worker.finished.connect(self._ocr_finished)
        self.ocr_progress.canceled.connect(self.ocr_worker.requestInterruption)
        self.ocr_worker.start()

    def _ocr_progress(self, k, n):
        prog = self.ocr_progress
        if prog is None:
            return
        # setting the value can process events (and finish OCR), so label first
        prog.setLabelText(f"Recognizing text… page {min(k + 1, n)} of {n}")
        prog.setValue(k)

    def _ocr_page_done(self, i, lines):
        if lines and ocr.add_text_layer(self.doc[i], lines):
            self.ocr_count += 1
            self.block_cache.pop(i, None)
            self.update_thumb(i)

    def _ocr_finished(self):
        if self.ocr_progress is not None:
            self.ocr_progress.close()
            self.ocr_progress = None
        if not self.ocr_count:
            self.undo_stack.pop()  # nothing was added
            msg = self.ocr_error or "No pages needed text recognition (they already have text)."
            QMessageBox.information(self, "Recognize text", msg)
            self.set_status(msg)
            self.update_ui()
            return
        self.banner.hide()
        self._doc_changed_all(f"Recognized text on {self.ocr_count} page(s). You can now search, "
                              "select and copy it.")
        if self.ocr_error:
            QMessageBox.warning(self, "Recognize text", f"Stopped early: {self.ocr_error}")

    def _show_doc(self, doc, path, password=None, dirty=False):
        """Show a document (opened or newly made) in a new tab."""
        self._open_in_tab(doc, path, password, dirty)

    def maybe_save(self):
        """Ask about unsaved changes. Returns False if the user cancelled."""
        self.commit_edit()
        if self.doc is None or not self.dirty:
            return True
        name = Path(self.path).name if self.path else (self.doc_name or "this PDF")
        r = QMessageBox.question(self, "Unsaved changes",
                                 f"Save changes to {name} first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def _final_bytes(self, compress=False):
        tmp = fitz.open("pdf", self.doc.tobytes())
        try:
            try:
                tmp.subset_fonts()
            except Exception:
                pass
            if compress:
                try:
                    tmp.rewrite_images(dpi_threshold=160, dpi_target=150, quality=70)
                except Exception:
                    pass
            kw = {"garbage": 4 if compress else 3, "deflate": True}
            if compress:
                kw.update(deflate_images=True, deflate_fonts=True, clean=True)
            if self.password:
                kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=self.password,
                          owner_pw=self.password)
            return tmp.tobytes(**kw)
        finally:
            tmp.close()

    def _write(self, path, compress=False):
        self.commit_edit()
        try:
            if not self.dirty and not compress and self.orig_bytes is not None and \
                    self.password == self.orig_password:
                data = self.orig_bytes  # unchanged: keep the file byte-for-byte (signatures stay valid)
            else:
                data = self._final_bytes(compress)
            tmp = Path(str(path) + ".drtmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except PermissionError:
            QMessageBox.warning(self, "Can't save",
                                "The file is open in another program or is read-only. "
                                "Close it there, or use Save as.")
            return False
        except Exception as e:
            QMessageBox.warning(self, "Can't save", str(e))
            return False
        return True

    @needs_doc
    def save(self):
        if self.doc is None:
            return False
        if not self.path:
            return self.save_as()
        if not self.dirty:
            self.set_status("No changes to save.")
            return True
        if self._write(self.path):
            self.dirty = False
            self.orig_bytes, self.orig_password, self.signed_count = None, self.password, 0
            self.update_ui()
            self.set_status(f"Saved {Path(self.path).name}.")
            return True
        return False

    def _ask_path(self, title, suffix=""):
        start = self.path or str(Path.home() / "Documents" / (self.doc_name or "Untitled.pdf"))
        if suffix:
            start = str(Path(start).with_name(Path(start).stem + suffix + ".pdf"))
        p, _ = QFileDialog.getSaveFileName(self, title, start, "PDF files (*.pdf)")
        if p and not p.lower().endswith(".pdf"):
            p += ".pdf"
        return p

    @needs_doc
    def save_as(self):
        if self.doc is None:
            return False
        p = self._ask_path("Save PDF as")
        if p and self._write(p):
            if self.dirty:
                self.orig_bytes, self.signed_count = None, 0
            self.orig_password = self.password
            self.path = p
            self.doc_name = None
            self.dirty = False
            self.add_recent(p)
            self.update_ui()
            self.set_status(f"Saved {Path(p).name}.")
            return True
        return False

    @needs_doc
    def save_compressed(self):
        if self.doc is None:
            return
        p = self._ask_path("Save a smaller copy", " (smaller)")
        if p and self._write(p, compress=True):
            before = len(self.doc.tobytes(garbage=3, deflate=True))
            after = Path(p).stat().st_size
            self.set_status(f"Saved {Path(p).name}: {after / 1024:.0f} KB "
                            f"(was about {before / 1024:.0f} KB).")

    @needs_doc
    def protect(self):
        if self.doc is None:
            return
        pw, ok = QInputDialog.getText(self, "Password protect",
                                      "Password needed to open this PDF:", QLineEdit.Password)
        if not ok or not pw:
            return
        pw2, ok = QInputDialog.getText(self, "Password protect", "Type it again:",
                                       QLineEdit.Password)
        if not ok:
            return
        if pw != pw2:
            QMessageBox.warning(self, "Password protect", "The passwords didn't match.")
            return
        self.password = pw
        self.mark_dirty()
        self.set_status("Password will be applied when you save.")

    @needs_doc
    def unprotect(self):
        if self.doc is None:
            return
        if self.password:
            self.password = None
            self.mark_dirty()
            self.set_status("Password will be removed when you save.")
        else:
            self.set_status("This PDF has no password.")

    @needs_doc
    def export(self, label):
        if self.doc is None or (self.export_worker and self.export_worker.isRunning()):
            return
        self.commit_edit()
        start = str(Path(self.path).parent) if self.path else ""
        d = QFileDialog.getExistingDirectory(self, "Save the exported file to", start)
        if not d:
            return
        stem = Path(self.path).stem if self.path else "Untitled"
        self.export_worker = ExportWorker(self.doc.tobytes(garbage=3, deflate=True),
                                          stem, label, d)
        self.export_worker.done.connect(
            lambda ok, msg: self.set_status(f"Exported {msg} to {d}." if ok
                                            else f"Export failed: {msg}"))
        self.export_worker.start()
        self.set_status(f"Exporting to {label}…")

    @needs_doc
    def print_doc(self):
        if self.doc is None:
            return
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        self.commit_edit()
        printer = QPrinter(QPrinter.HighResolution)
        printer.setFromTo(1, self.doc.page_count)
        self._match_orientation(printer)
        dlg = QPrintDialog(printer, self)
        dlg.setMinMax(1, self.doc.page_count)
        if dlg.exec() != QDialog.Accepted:
            return
        self._paint_pages(printer)
        self.set_status("Sent to printer.")

    def _match_orientation(self, printer):
        """Paper the same way round as the PDF (whatever the printer was last set to)."""
        from PySide6.QtGui import QPageLayout
        r = self.doc[0].rect
        if self.doc[0].rotation % 180:
            r = fitz.Rect(0, 0, r.height, r.width)
        printer.setPageOrientation(QPageLayout.Landscape if r.width > r.height
                                   else QPageLayout.Portrait)

    def _paint_pages(self, printer):
        """Draw the pages (the range chosen in the print dialog, or all) on a printer -
        or on the print preview, which gets lighter pictures of them."""
        first = max(printer.fromPage(), 1) if printer.fromPage() else 1
        last = min(printer.toPage() or self.doc.page_count, self.doc.page_count)
        painter = QPainter(printer)
        preview = painter.paintEngine().type() == QPaintEngine.Picture
        dpi = 110 if preview else min(printer.resolution(), 300)
        try:
            for i in range(first - 1, last):
                if i > first - 1:
                    printer.newPage()
                pix = self.doc[i].get_pixmap(dpi=dpi, alpha=False)
                img = pixmap_to_qimage(pix)
                target = painter.viewport()
                size = img.size().scaled(target.size(), Qt.KeepAspectRatio)
                # centred on the paper, like the printer will put it
                x = target.x() + (target.width() - size.width()) // 2
                y = target.y() + (target.height() - size.height()) // 2
                painter.setViewport(x, y, size.width(), size.height())
                painter.setWindow(img.rect())
                painter.drawImage(0, 0, img)
                painter.setViewport(target)
                painter.setWindow(0, 0, target.width(), target.height())
        finally:
            painter.end()

    @needs_doc
    def print_preview(self):
        """See the pages as they will print (paper, orientation, margins) before printing."""
        if self.doc is None:
            return
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
        self.commit_edit()
        printer = QPrinter(QPrinter.HighResolution)
        self._match_orientation(printer)
        dlg = QPrintPreviewDialog(printer, self)
        name = Path(self.path).name if self.path else (self.doc_name or "Untitled.pdf")
        dlg.setWindowTitle(f"Print preview - {name}")
        dlg.paintRequested.connect(self._paint_pages)
        dlg.resize(1100, 820)
        self.print_preview_dlg = dlg  # (so tests can look at it)
        if dlg.exec() == QDialog.Accepted:
            self.set_status("Sent to printer.")

    # ------------------------------------------------------------------ undo
    def checkpoint(self):
        """Call before every change: remembers the document for Undo."""
        if self.signed_count and not self.sign_warned:
            self.sign_warned = True
            QMessageBox.information(
                self, "Signed document",
                "This PDF is digitally signed. If you save changes, the existing signature(s) "
                "will no longer be valid.\n\nTip: use Save as to keep the signed original.")
        self.undo_stack.append(self.doc.tobytes())
        self.redo_stack.clear()
        # keep at most 40 steps and ~400 MB, so huge PDFs can't exhaust memory
        while len(self.undo_stack) > 1 and (
                len(self.undo_stack) > 40 or sum(map(len, self.undo_stack)) > UNDO_BYTES):
            self.undo_stack.pop(0)

    def mark_dirty(self):
        self.dirty = True
        self.comments_stale = True
        if self.side_stack.currentIndex() == 2:
            QTimer.singleShot(0, self.refresh_comments)
        self.update_ui()

    def _reload(self, data):
        keep = self.current_page()
        self.doc.close()
        self.doc = fitz.open("pdf", data)
        self.selected = None
        self.clear_overlay()
        self.layout_pages(keep_view=True)
        self.build_thumbs()
        self.load_bookmarks()
        if self.tool == "form":
            self._show_form_outlines()
        self.goto_page(min(keep, self.doc.page_count - 1), scroll=False)
        self.mark_dirty()
        if self._active_state is not None and self._active_state.get("repair_seen"):
            self._start_repair(self._active_state, again=True)  # an older copy: check again

    @needs_doc
    def undo(self):
        self.cancel_edit()
        if self.doc is None or not self.undo_stack:
            return
        self.redo_stack.append(self.doc.tobytes())
        self._reload(self.undo_stack.pop())
        self.set_status("Undone.")

    @needs_doc
    def redo(self):
        self.cancel_edit()
        if self.doc is None or not self.redo_stack:
            return
        self.undo_stack.append(self.doc.tobytes())
        self._reload(self.redo_stack.pop())
        self.set_status("Redone.")

    def page_changed(self, index):
        """Re-render one page and its thumbnail after a change."""
        self.block_cache.pop(index, None)
        if 0 <= index < len(self.items):
            self.items[index].rendered = 0
            self.render_visible()
            self.update_thumb(index)
        self.mark_dirty()

    # ------------------------------------------------------------------ layout & render
    def view_scale(self):
        return self.zoom * self.logicalDpiX() / 72.0

    def layout_pages(self, keep_view=False):
        scene = self.canvas.scene()
        frac = 0
        if keep_view and scene.sceneRect().height() > 0:
            frac = self.canvas.verticalScrollBar().value() / max(
                1, self.canvas.verticalScrollBar().maximum())
        self.editing = None
        self.block_cache = {}
        self.selected = None
        self.handles = []
        self.drag = None
        self.form_items = []
        self.edit_items = []
        scene.clear()
        self.overlay = []
        self.hover_item = None
        self.items = []
        y = GAP
        maxw = 0
        for i, page in enumerate(self.doc):
            r = page.rect
            it = PageItem(i, r.width, r.height)
            it.setPos(-r.width / 2, y)
            scene.addItem(it)
            self.items.append(it)
            y += r.height + GAP
            maxw = max(maxw, r.width)
        scene.setSceneRect(-maxw / 2 - GAP, 0, maxw + 2 * GAP, y)
        if not keep_view:
            self.canvas.verticalScrollBar().setValue(0)
        self.apply_zoom()
        if not keep_view:
            self.canvas.verticalScrollBar().setValue(0)
        if keep_view:
            sb = self.canvas.verticalScrollBar()
            sb.setValue(int(frac * sb.maximum()))
        self.schedule_render()

    def apply_zoom(self, anchor=None):
        """Apply self.zoom (or the fit mode). `anchor` = (scene point, viewport point) that
        should stay put; by default the top-centre of the view stays where it is."""
        if self.doc is None:
            return
        if anchor is None:
            vp_pt = QPointF(self.canvas.viewport().width() / 2, 0)
            anchor = (self.canvas.mapToScene(vp_pt.toPoint()), vp_pt)
        vp = self.canvas.viewport().size()
        dpi = self.logicalDpiX() / 72.0
        if self.fit_mode and self.items:
            pr = self.doc[self.current_page()].rect
            zw = (vp.width() - 2 * GAP * dpi) / (pr.width * dpi)
            zh = (vp.height() - GAP * dpi) / ((pr.height + GAP) * dpi)
            self.zoom = max(0.1, zw if self.fit_mode == "width" else min(zw, zh))
        s = self.view_scale()
        self.canvas.setTransform(QTransformScale(s))
        sc_pt, vp_pt = anchor
        now = self.canvas.mapFromScene(sc_pt)
        hb, vb = self.canvas.horizontalScrollBar(), self.canvas.verticalScrollBar()
        hb.setValue(hb.value() + round(now.x() - vp_pt.x()))
        vb.setValue(vb.value() + round(now.y() - vp_pt.y()))
        self.zoom_box.blockSignals(True)
        self.zoom_box.setEditText({"width": "Fit width", "page": "Fit page"}.get(
            self.fit_mode, f"{round(self.zoom * 100)}%"))
        self.zoom_box.blockSignals(False)
        self.schedule_render()

    def on_resize(self):
        if self.fit_mode:
            self.apply_zoom()
        self.schedule_render()

    @needs_doc
    def zoom_by(self, f, anchor=None):
        if self.doc is None:
            return
        self.fit_mode = None
        self.zoom = min(8.0, max(0.1, self.zoom * f))
        self.apply_zoom(anchor)

    @needs_doc
    def on_zoom_text(self, text):
        t = text.strip().lower()
        if t.startswith("fit w"):
            self.fit_mode = "width"
        elif t.startswith("fit p"):
            self.fit_mode = "page"
        else:
            try:
                self.zoom = min(8.0, max(0.1, float(t.rstrip("%").strip()) / 100))
                self.fit_mode = None
            except ValueError:
                return
        self.apply_zoom()

    def schedule_render(self, *_):
        self.render_timer.start(30)
        if self.doc is not None and self.items:
            self.page_spin.blockSignals(True)
            self.page_spin.setValue(self.current_page() + 1)
            self.page_spin.blockSignals(False)
            self.update_size_label()

    def visible_rect(self):
        return self.canvas.mapToScene(self.canvas.viewport().rect()).boundingRect()

    def render_visible(self):
        if self.doc is None:
            return
        vis = self.visible_rect()
        margin = vis.height()
        near = vis.adjusted(0, -margin, 0, margin)
        scale = min(self.view_scale() * self.devicePixelRatioF(), 6.0)
        for it in self.items:
            r = it.sceneBoundingRect()
            if r.intersects(near):
                if abs(it.rendered - scale) > 1e-3:
                    pix = self.doc[it.index].get_pixmap(matrix=fitz.Matrix(scale, scale),
                                                        alpha=False)
                    it.pix.setPixmap(QPixmap.fromImage(pixmap_to_qimage(pix)))
                    it.pix.setScale(1 / scale)
                    it.rendered = scale
            elif it.rendered and not r.intersects(vis.adjusted(0, -4 * margin, 0, 4 * margin)):
                it.pix.setPixmap(QPixmap())  # free memory for far-away pages
                it.rendered = 0
        if self.tool == "edit" and not self.editing:
            self._show_edit_outlines()

    def current_page(self):
        if not self.items:
            return 0
        c = self.visible_rect().center().y()
        top = self.visible_rect().top()
        probe = top + min(self.visible_rect().height() * 0.3, 200)
        for it in self.items:
            r = it.sceneBoundingRect()
            if r.top() <= probe <= r.bottom() + GAP:
                return it.index
        return 0 if c < self.items[0].y() else len(self.items) - 1

    @needs_doc
    def goto_page(self, index, scroll=True):
        if not self.items or not (0 <= index < len(self.items)):
            return
        if scroll:
            it = self.items[index]
            sb = self.canvas.verticalScrollBar()
            y = self.canvas.mapFromScene(QPointF(0, it.y() - GAP / 2)).y()
            sb.setValue(sb.value() + y)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        if 0 <= index < self.thumbs.count():
            self.thumbs.setCurrentRow(index)
        self.update_size_label()

    # ------------------------------------------------------------------ thumbnails & pages
    def selected_pages(self):
        rows = sorted(self.thumbs.row(it) for it in self.thumbs.selectedItems())
        return rows or [self.current_page()]

    def reorder_pages(self, rows, before):
        """Move the pages `rows` so they come before page `before` (0-based; page count =
        at the end). Returns the new positions of the moved pages."""
        if self.doc is None or not rows:
            return []
        n = self.doc.page_count
        rows = sorted(set(r for r in rows if 0 <= r < n))
        rest = [p for p in range(n) if p not in rows]
        at = max(0, min(before, n)) - sum(1 for r in rows if r < before)
        order = rest[:at] + rows + rest[at:]
        new_pos = list(range(at, at + len(rows)))
        if order == list(range(n)):
            return new_pos
        self.cancel_edit()
        self.checkpoint()
        self.doc.select(order)
        self._after_pages(f"Moved {len(rows)} page(s) to position {at + 1}." if len(rows) > 1
                          else f"Moved page {rows[0] + 1} to position {at + 1}.")
        self.thumbs.clearSelection()
        for r in new_pos:
            self.thumbs.item(r).setSelected(True)
        self.goto_page(new_pos[0])
        return new_pos

    @needs_doc
    def organize_pages(self):
        self.commit_edit()
        OrganizeDialog(self).exec()

    def _after_pages(self, msg):
        self.selected = None
        self.clear_overlay()
        self.layout_pages(keep_view=True)
        self.build_thumbs()
        self.load_bookmarks()
        if self.tool == "form":
            self._show_form_outlines()
        self.mark_dirty()
        self.set_status(msg)

    PAPER = {"Letter": (612, 792), "Legal": (612, 1008), "A4": (595, 842),
             "Tabloid": (792, 1224), "A5": (420, 595)}

    def new_blank_pdf(self):
        """Create a new PDF of blank pages in its own tab."""
        dlg = dialogs.NewPdfDialog(self, list(self.PAPER))
        if not self._dialog_ok(dlg):
            return None
        v = dlg.values()
        w, h = self.PAPER[v["size"]]
        if v["landscape"]:
            w, h = h, w
        doc = fitz.open()
        for _ in range(v["pages"]):
            doc.new_page(width=w, height=h)
        self._open_in_tab(doc, None, dirty=True, name="Untitled.pdf")
        self.set_status(f"New blank PDF: {v['pages']} {v['size']} page(s). Use Save to keep it.")
        return doc

    def _fill_page_menu(self, m):
        m.addAction(icons.icon("pages"), "Organize pages…", self.organize_pages)
        m.addSeparator()
        m.addAction(icons.icon("rotate"), "Rotate right", lambda: self.rotate_pages(90))
        m.addAction("Rotate left", lambda: self.rotate_pages(-90))
        m.addSeparator()
        m.addAction(icons.icon("plus"), "Insert blank page after", self.insert_blank)
        m.addAction(icons.icon("add"), "Add pages from other files…", self.add_files_dialog)
        m.addAction("Extract selected pages to a new PDF…", self.extract_pages)
        m.addSeparator()
        m.addAction(icons.icon("up"), "Move up", lambda: self.move_pages(-1))
        m.addAction(icons.icon("down"), "Move down", lambda: self.move_pages(1))
        m.addSeparator()
        m.addAction(icons.icon("crop"), "Crop pages", lambda: self.set_tool("crop"))
        m.addAction("Remove crop from selected pages", self.uncrop)
        m.addSeparator()
        m.addAction(icons.icon("trash"), "Delete selected pages", self.delete_pages)

    @needs_doc
    def page_menu(self, pos=None, anchor=None):
        if self.doc is None:
            return
        m = QMenu(self)
        self._fill_page_menu(m)
        m.exec(self.thumbs.mapToGlobal(pos) if pos is not None else QCursor.pos())

    @needs_doc
    def uncrop(self):
        pages = self.selected_pages()
        self.checkpoint()
        pdftools.uncrop_pages(self.doc, pages)
        self._after_pages(f"Crop removed from {len(pages)} page(s).")

    @needs_doc
    def rotate_pages(self, deg):
        self.cancel_edit()
        self.checkpoint()
        for i in self.selected_pages():
            p = self.doc[i]
            p.set_rotation((p.rotation + deg) % 360)
        self._after_pages("Rotated.")

    @needs_doc
    def delete_pages(self):
        pages = self.selected_pages()
        if len(pages) >= self.doc.page_count:
            QMessageBox.information(self, "Delete pages", "A PDF needs at least one page.")
            return
        self.cancel_edit()
        self.checkpoint()
        self.doc.delete_pages(pages)
        self._after_pages(f"Deleted {len(pages)} page(s).")

    def insert_blank(self):
        if self.doc is None:
            return self.new_blank_pdf()
        self.cancel_edit()
        at = self.selected_pages()[-1]
        r = self.doc[at].rect
        self.checkpoint()
        self.doc.new_page(at + 1, width=r.width, height=r.height)
        self._after_pages("Blank page inserted.")

    # ------------------------------------------------------------------ adding other files
    ADD_FILTER = ("PDFs and other files (*.pdf *.docx *.doc *.rtf *.odt *.txt *.xlsx *.xls *.csv "
                  "*.pptx *.ppt *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.heic *.gif);;"
                  "PDF files (*.pdf);;All files (*)")

    def add_files_dialog(self):
        start = str(Path(self.path).parent) if self.path else ""
        title = "Add files to this PDF" if self.doc is not None else "Choose files to combine"
        paths, _ = QFileDialog.getOpenFileNames(self, title, start, self.ADD_FILTER)
        if paths:
            self.add_files(paths)

    def _ask_position(self, paths, offer_open):
        """Where should the new pages go? Returns a page index, 'open', or None."""
        names = Path(paths[0]).name if len(paths) == 1 else f"{len(paths)} files"
        cur = self.current_page()
        box = QMessageBox(self)
        box.setWindowTitle("Add pages")
        box.setText(f"Where should the pages from {names} go?")
        end = box.addButton("At the end", QMessageBox.AcceptRole)
        after = box.addButton(f"After page {cur + 1}", QMessageBox.AcceptRole)
        begin = box.addButton("At the beginning", QMessageBox.AcceptRole)
        opener = box.addButton("Open it instead", QMessageBox.AcceptRole) if offer_open else None
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(end)
        box.exec()
        b = box.clickedButton()
        if b is end:
            return self.doc.page_count
        if b is after:
            return cur + 1
        if b is begin:
            return 0
        if opener is not None and b is opener:
            return "open"
        return None

    def _load_as_pdf(self, path, tmp, office):
        """Open any supported file as a PDF document (converting it if needed)."""
        src = Path(path)
        if src.suffix.lower() != ".pdf":
            if not cv.targets_for(src.suffix) or "PDF" not in cv.targets_for(src.suffix):
                raise RuntimeError("this kind of file can't be turned into PDF pages")
            src = cv.convert(src, "PDF", tmp / str(len(list(tmp.iterdir()))), {}, office)[0]
        doc = fitz.open(str(src))
        if doc.needs_pass:
            while True:
                pw, ok = QInputDialog.getText(self, "Password required",
                                              f"{Path(path).name} is protected. Password:",
                                              QLineEdit.Password)
                if not ok:
                    doc.close()
                    raise RuntimeError("no password given")
                if doc.authenticate(pw):
                    break
        return doc

    def combine_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to combine", "",
                                                self.ADD_FILTER)
        if paths:
            self.add_files(paths, combine=True)

    def add_files(self, paths, offer_open=False, combine=False, at=None):
        """Insert the pages of other files into the open PDF. With `combine` (or no PDF
        open) they are joined into a new PDF in its own tab, like Acrobat's Combine."""
        paths = [p for p in paths if Path(p).is_file()]
        if not paths:
            return
        self.commit_edit()
        if combine and len(paths) == 1 and paths[0].lower().endswith(".pdf"):
            self.open_file(paths[0])
            return
        if combine:
            at = 0
        elif self.doc is not None and at is not None:  # dropped at a spot in the page list
            at = max(0, min(int(at), self.doc.page_count))
        elif self.doc is not None:
            at = self._ask_position(paths, offer_open and len(paths) == 1
                                    and paths[0].lower().endswith(".pdf"))
            if at is None:
                return
            if at == "open":
                self.open_file(paths[0])
                return
        elif len(paths) == 1 and paths[0].lower().endswith(".pdf"):
            self.open_file(paths[0])
            return
        else:
            at = 0
        tmp = Path(tempfile.mkdtemp())
        office = cv.Office()
        added, pages, failed = [], 0, []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            new_doc = combine or self.doc is None
            target = fitz.open() if new_doc else self.doc
            if not new_doc:
                self.checkpoint()
            for p in paths:
                self.set_status(f"Adding {Path(p).name}…")
                QApplication.processEvents()
                try:
                    other = self._load_as_pdf(p, tmp, office)
                except Exception as e:
                    failed.append(f"{Path(p).name}: {e}")
                    continue
                with other:
                    target.insert_pdf(other, start_at=at)
                    at += other.page_count
                    pages += other.page_count
                added.append(Path(p).name)
        finally:
            office.close()
            QApplication.restoreOverrideCursor()
            shutil.rmtree(tmp, ignore_errors=True)
        if failed:
            QMessageBox.warning(self, "Add pages", "Some files couldn't be added:\n\n"
                                + "\n".join(failed))
        if not added:
            if not new_doc:
                self.undo_stack.pop()
            elif target.page_count == 0:
                target.close()
            return
        msg = f"Added {pages} page(s) from {len(added)} file(s)."
        if new_doc:
            self._open_in_tab(target, None, dirty=True, name="Combined.pdf")
            self.set_status(f"Combined {len(added)} file(s) into a new PDF ({pages} pages). "
                            "Use Save to keep it.")
        else:
            first_new = at - pages
            self._after_pages(msg)
            self.goto_page(first_new)

    @needs_doc
    def extract_pages(self):
        pages = self.selected_pages()
        p = self._ask_path("Save extracted pages as", " (extract)")
        if not p:
            return
        out = fitz.open()
        for i in pages:
            out.insert_pdf(self.doc, from_page=i, to_page=i)
        out.save(p, garbage=3, deflate=True)
        out.close()
        self.set_status(f"Saved {len(pages)} page(s) to {Path(p).name}.")

    @needs_doc
    def move_pages(self, step):
        pages = self.selected_pages()
        order = list(range(self.doc.page_count))
        if step < 0 and pages[0] == 0 or step > 0 and pages[-1] == len(order) - 1:
            return
        for i in (pages if step < 0 else reversed(pages)):
            order[i], order[i + step] = order[i + step], order[i]
        self.cancel_edit()
        self.checkpoint()
        self.doc.select(order)
        self._after_pages("Moved.")
        self.thumbs.clearSelection()
        for i in pages:
            self.thumbs.item(i + step).setSelected(True)

    # ------------------------------------------------------------------ geometry
    def page_at(self, sp):
        for it in self.items:
            if it.sceneBoundingRect().contains(sp):
                return it.index
        return None

    def to_pdf(self, i, sp):
        """scene point -> unrotated PDF point on page i"""
        it = self.items[i]
        v = fitz.Point(sp.x() - it.x(), sp.y() - it.y())
        return v * self.doc[i].derotation_matrix

    def to_pdf_rect(self, i, a, b):
        r = fitz.Rect(self.to_pdf(i, a), self.to_pdf(i, a))
        r.include_point(self.to_pdf(i, b))
        return r.normalize()

    def to_scene_rect(self, i, rect):
        it = self.items[i]
        r = fitz.Rect(rect) * self.doc[i].rotation_matrix
        return QRectF(r.x0 + it.x(), r.y0 + it.y(), r.width, r.height)

    def clamp_to_page(self, i, sp):
        r = self.items[i].sceneBoundingRect()
        return QPointF(min(max(sp.x(), r.left()), r.right()), min(max(sp.y(), r.top()), r.bottom()))

    # ------------------------------------------------------------------ overlay helpers
    def clear_overlay(self):
        for o in self.overlay:
            if o.scene():
                o.scene().removeItem(o)
        self.overlay = []
        if self.hover_item and self.hover_item.scene():
            self.hover_item.scene().removeItem(self.hover_item)
        self.hover_item = None

    def add_overlay_rect(self, r, pen=None, brush=None):
        item = QGraphicsRectItem(r)
        item.setPen(pen or QPen(Qt.NoPen))
        item.setBrush(brush or QBrush(Qt.NoBrush))
        item.setZValue(10)
        self.canvas.scene().addItem(item)
        self.overlay.append(item)
        return item

    def dashed(self, color="#7C83FF"):
        p = QPen(QColor(color), 0)
        p.setCosmetic(True)
        p.setWidth(2)
        p.setStyle(Qt.DashLine)
        return p

    # ------------------------------------------------------------------ tools
    def set_tool(self, tid):
        self.commit_edit()
        if tid.startswith("form:"):
            self.form_kind = tid.split(":", 1)[1]
            tid = "form"
        if tid == "form" and self.doc is not None:
            self._show_form_outlines()
        else:
            self._hide_form_outlines()
        self.tool = tid
        if tid == "edit":
            self._show_edit_outlines()
        else:
            self._hide_edit_outlines()
        self.selected = None
        self.clear_overlay()
        self.drag = None
        self.canvas.viewport().setCursor(self.tool_cursor())
        self.set_status(TOOL_HINTS.get(tid, "Select text, comments, pictures and form fields."))
        if tid == "sign":
            self.new_signature()
            if not SIGNATURE_FILE.exists():
                self.set_tool("select")
                return
            self.set_status("Click where your signature goes. (Click Sign again to redraw it.)")
        self.update_ui()

    def on_key(self, e):
        """Single-key tool shortcuts (like Acrobat) and page navigation on the canvas."""
        if self.doc is None or self.editing or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return False
        k = e.key()
        tools = {Qt.Key_V: "select", Qt.Key_E: "edit", Qt.Key_T: "addtext",
                 Qt.Key_H: "highlight", Qt.Key_N: "note"}
        if k in tools:
            self.set_tool(tools[k])
            return True
        if k == Qt.Key_Escape:
            if self.drag:
                self.drag = None
                self.clear_overlay()
            elif self.selected:
                self.selected = None
                self.clear_overlay()
            elif self.tool != "select":
                self.set_tool("select")
            return True
        if k == Qt.Key_Home:
            self.goto_page(0)
            return True
        if k == Qt.Key_End:
            self.goto_page(self.doc.page_count - 1)
            return True
        return False

    def set_highlight_color(self, col):
        self.colors["highlight"] = QColor(col)
        self.set_tool("highlight")

    def pick_color(self):
        t = self.tool
        c = QColorDialog.getColor(self.colors.get(t, QColor(0, 0, 0)), self, "Colour")
        if c.isValid():
            self.colors[t] = c
            if t in TEXT_TOOLS and self.editing:
                self.editing["color"] = c
                self.style_editor()
            self.update_ui()

    def on_text_props(self, *_):
        if self.editing:
            ed = self.editing
            ed["family"] = self.font_box.currentText()
            ed["bold"] = int(self.bold_btn.isChecked())
            ed["italic"] = int(self.italic_btn.isChecked())
            try:
                ed["size"] = max(1.0, float(self.size_box.currentText()))
            except ValueError:
                return
            ed["style_changed"] = True
            self.style_editor()

    # ------------------------------------------------------------------ mouse
    def on_press(self, e, sp):
        if self.doc is None or e.button() != Qt.LeftButton:
            return False
        if self.editing:
            ed = self.editing
            proxy = ed["proxy"]
            if proxy.sceneBoundingRect().contains(sp):
                return False  # click inside the text editor
            # on the box's frame: finish typing and move the box
            grab = not ed["new"] and \
                ed["frame"].rect().adjusted(-7, -7, 7, 7).contains(sp)
            self.commit_edit()
            if grab:
                i = ed["page"]
                b = self.block_at(self.doc[i], self.to_pdf(i, sp), margin=9)
                if b is not None:
                    self.drag = {"kind": "blockpress", "page": i, "block": b, "sp": sp}
                    return True
            if self.tool == "addtext":
                return True
        i = self.page_at(sp)
        if i is None:
            self.selected = None
            self.clear_overlay()
            return True
        t = self.tool
        pt = self.to_pdf(i, sp)
        page = self.doc[i]
        if t == "form":
            h = self.handle_at(sp)
            sel = self.selected
            if h:
                self._start_image_drag(i, sp, h)
                return True
            w = self.widget_at(page, pt)
            if w is not None:
                self.select_widget(i, w)
                self._start_image_drag(i, sp, "move")
                return True
        if t == "certsign":
            w = self.widget_at(page, pt)
            if w is not None and w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE:
                if w.is_signed:
                    self.signatures_dialog()
                else:
                    self.digital_sign(field=w.field_name)
                return True
        if t in ("select", "edit"):
            h = self.handle_at(sp)
            sel = self.selected
            if h:
                self._start_image_drag(i, sp, h)
                return True
            if sel and sel[0] in ("image", "annot") and sel[1] == i and len(sel) > 3 and \
                    self.to_scene_rect(i, sel[3]).contains(sp) and \
                    (sel[0] == "image" or self._annot_movable(i, sel[2])):
                self._start_image_drag(i, sp, "move")
                return True
        if t == "select":
            annot = self.annot_at(page, pt)
            if annot is not None:
                self.select_annot(i, annot)
                if annot.type[0] in MOVABLE_ANNOTS:
                    self._start_image_drag(i, sp, "move")
                return True
            w = self.widget_at(page, pt)
            if w is not None:
                self.fill_widget(i, w)
                return True
            img = self._image_hit(i, pt, skip_background=True)
            if img and not any(fitz.Rect(wd[:4]).contains(pt) for wd in page.get_text("words")):
                self.select_image(i, *img)
                self._start_image_drag(i, sp, "move")
                return True
            self.drag = {"kind": "textsel", "page": i, "start": pt, "sp": sp}
            self.selected = None
            self.clear_overlay()
            return True
        if t == "edit":
            img = self._image_hit(i, pt)
            block = self.block_at(page, pt)
            if block is not None:
                # a click types in it (on release); a drag moves it
                self.drag = {"kind": "blockpress", "page": i, "block": block, "sp": sp}
            elif img is not None:
                self.select_image(i, *img)
                self._start_image_drag(i, sp, "move")
            else:
                self.selected = None
                self.clear_overlay()
            return True
        if t == "addtext":
            block = self.block_at(page, pt)
            if block is not None:  # text that's already there: type in it or move it
                self.drag = {"kind": "blockpress", "page": i, "block": block, "sp": sp}
            else:
                self.open_new_text(i, sp)
            return True
        if t in TEXT_MARK_TOOLS:
            self.drag = {"kind": "textsel", "page": i, "start": pt, "sp": sp}
            return True
        if t == "note":
            self.add_note(i, pt)
            return True
        if t == "sign":
            self.place_signature(i, sp)
            return True
        if t == "stamp":
            self.place_stamp(i, sp)
            return True
        if t == "pen":
            path = QPainterPath(sp)
            item = QGraphicsPathItem(path)
            pen = QPen(self.colors["pen"], self.width_spin.value())
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            item.setPen(pen)
            item.setZValue(10)
            self.canvas.scene().addItem(item)
            self.overlay.append(item)
            self.drag = {"kind": "pen", "page": i, "points": [sp], "item": item, "path": path}
            return True
        if t in DRAG_RECT_TOOLS or t in ("line", "arrow"):
            if t in ("line", "arrow"):
                item = QGraphicsLineItem(sp.x(), sp.y(), sp.x(), sp.y())
                item.setPen(QPen(self.colors[t], self.width_spin.value()))
            elif t == "ellipse":
                item = QGraphicsEllipseItem(QRectF(sp, sp))
                item.setPen(QPen(self.colors[t], self.width_spin.value()))
            else:
                item = QGraphicsRectItem(QRectF(sp, sp))
                if t == "rect":
                    item.setPen(QPen(self.colors[t], self.width_spin.value()))
                elif t == "whiteout":
                    item.setBrush(QBrush(Qt.white))
                    item.setPen(self.dashed("#999999"))
                elif t == "redact":
                    item.setBrush(QBrush(QColor(0, 0, 0, 170)))
                    item.setPen(QPen(Qt.NoPen))
                elif t == "textbox":
                    item.setBrush(QBrush(QColor(255, 255, 220, 200)))
                    item.setPen(QPen(QColor("#555555"), 0))
                elif t == "crop":
                    item.setBrush(QBrush(QColor(124, 131, 255, 40)))
                    item.setPen(self.dashed())
                else:
                    item.setPen(self.dashed())
            item.setZValue(10)
            self.canvas.scene().addItem(item)
            self.overlay.append(item)
            self.drag = {"kind": "shape", "page": i, "start": sp, "item": item}
            return True
        return False

    def on_move(self, e, sp):
        if self.doc is None:
            return
        d = self.drag
        if d is None:
            if not self.editing:
                self.update_note_preview(sp, e.globalPosition().toPoint())
            if self.tool == "edit" and not self.editing:
                self.update_hover(sp)
            if self.tool in ("select", "edit", "form"):
                self.update_cursor(sp)
            return
        i = d["page"]
        if d["kind"] in ("blockpress", "blockmove"):
            self._drag_block(d, sp)
            return
        if d["kind"] == "textsel":
            moved = sp - d["sp"]
            if abs(moved.x()) + abs(moved.y()) < 3:
                return
            self.show_text_selection(i, d["start"], self.to_pdf(i, self.clamp_to_page(i, sp)))
        elif d["kind"] == "image":
            free = bool(e.modifiers() & Qt.ShiftModifier)
            self._show_drag_rect(d, self._drag_rect(d, sp, free))
        elif d["kind"] == "pen":
            p = self.clamp_to_page(i, sp)
            d["points"].append(p)
            d["path"].lineTo(p)
            d["item"].setPath(d["path"])
        elif d["kind"] == "shape":
            p = self.clamp_to_page(i, sp)
            s = d["start"]
            if isinstance(d["item"], QGraphicsLineItem):
                d["item"].setLine(s.x(), s.y(), p.x(), p.y())
            else:
                d["item"].setRect(QRectF(s, p).normalized())

    def on_release(self, e, sp):
        d, self.drag = self.drag, None
        if d is None or self.doc is None:
            return False
        i = d["page"]
        page = self.doc[i]
        k = d["kind"]
        if k == "blockpress":  # a click: type in the box
            self.open_block_editor(i, d["block"], d["sp"])
            return True
        if k == "blockmove":
            off = d.get("offset", QPointF(0, 0))
            self.clear_overlay()
            self.canvas.viewport().setCursor(self.tool_cursor())
            delta = self.to_pdf(i, d["sp"] + off) - self.to_pdf(i, d["sp"])
            if abs(delta.x) + abs(delta.y) >= 0.5:
                self.move_block(i, d["block"], delta)
            return True
        if k == "textsel":
            moved = sp - d["sp"]
            if abs(moved.x()) + abs(moved.y()) < 3:  # a click, not a drag
                self.clear_overlay()
                self.selected = None
                return True
            words = self.selection_words(i, d["start"], self.to_pdf(i, self.clamp_to_page(i, sp)))
            if self.tool in TEXT_MARK_TOOLS:
                self.clear_overlay()
                if words:
                    self.mark_text(i, words)
            else:
                self.selected = ("text", i, words) if words else None
                if words:
                    self.set_status(f"{len(words)} word(s) selected — Ctrl+C to copy.")
            return True
        if k == "image":
            self._finish_image_drag(d)
            return True
        if k == "pen":
            self.clear_overlay()
            pts = [self.to_pdf(i, p) for p in d["points"]]
            if len(pts) < 2:
                return True
            self.checkpoint()
            a = page.add_ink_annot([[(p.x, p.y) for p in pts]])
            a.set_border(width=self.width_spin.value())
            a.set_colors(stroke=rgb(self.colors["pen"]))
            a.update()
            self.page_changed(i)
            return True
        if k == "shape":
            self.clear_overlay()
            p = self.clamp_to_page(i, sp)
            s = d["start"]
            small = abs(p.x() - s.x()) < 4 and abs(p.y() - s.y()) < 4
            t = self.tool
            if t == "image":
                self.place_image(i, s, None if small else p)
                return True
            if t == "form":
                self.create_field_at(i, s, None if small else p)
                return True
            if small:
                return True
            if t == "textbox":
                self.add_textbox(i, self.to_pdf_rect(i, s, p))
                return True
            if t == "crop":
                self.apply_crop(i, self.to_pdf_rect(i, s, p))
                return True
            if t == "certsign":
                self.digital_sign(i, self.to_pdf_rect(i, s, p))
                return True
            a, b = self.to_pdf(i, s), self.to_pdf(i, p)
            r = self.to_pdf_rect(i, s, p)
            col = rgb(self.colors.get(t, QColor(0, 0, 0)))
            if t == "redact":
                if QMessageBox.question(
                        self, "Redact",
                        "Permanently remove all text, pictures and drawings in this area?\n"
                        "This can be undone until you close the file.") != QMessageBox.Yes:
                    return True
                self.checkpoint()
                page.add_redact_annot(r, fill=(0, 0, 0))
                page.apply_redactions()
            elif t == "whiteout":
                self.checkpoint()
                page.draw_rect(r, color=None, fill=(1, 1, 1), overlay=True)
            else:
                self.checkpoint()
                if t == "rect":
                    an = page.add_rect_annot(r)
                elif t == "ellipse":
                    an = page.add_circle_annot(r)
                else:
                    an = page.add_line_annot(a, b)
                    if t == "arrow":
                        an.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
                an.set_border(width=self.width_spin.value())
                an.set_colors(stroke=col)
                an.update()
            self.page_changed(i)
            return True
        return False

    def on_double(self, e, sp):
        if self.doc is not None and self.tool == "form":
            i = self.page_at(sp)
            if i is not None:
                page = self.doc[i]
                w = self.widget_at(page, self.to_pdf(i, sp))
                if w is not None:
                    w._page_ref = page
                    self.field_properties(i, w)
                    return True
            return False
        if self.doc is None or self.tool != "select":
            return False
        i = self.page_at(sp)
        if i is None:
            return False
        page = self.doc[i]
        annot = self.annot_at(page, self.to_pdf(i, sp))
        if annot is None:
            return False
        info = annot.info
        title = "Text box" if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT else "Comment"
        text, ok = QInputDialog.getMultiLineText(self, title, "Text:", info.get("content", ""))
        if ok:
            xref = annot.xref
            self.checkpoint()
            annot = self.find_annot(self.doc[i], xref)
            annot.set_info(content=text)
            annot.update()
            self.page_changed(i)
        return True

    # ------------------------------------------------------------------ note previews
    def note_at(self, page, pt):
        """The comment under the point that has text: a note, or a highlight (underline,
        strike-out...) with a note on it. Returns (xref, info, '#rrggbb') or None."""
        hit = None
        for a in page.annots():
            kind = a.type[0]
            if kind in (fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_WIDGET):
                continue
            info = a.info
            if not (info.get("content") or "").strip():
                continue
            if kind == fitz.PDF_ANNOT_FREE_TEXT:
                continue  # its text is already on the page
            v = a.vertices if kind in MARKUP_ANNOTS else None
            if v and len(v) >= 4:  # only the marked words, not the whole box around them
                inside = any(fitz.Quad(v[k:k + 4]).rect.contains(pt)
                             for k in range(0, len(v) - 3, 4))
            else:
                r = fitz.Rect(a.rect)
                if r.width < 6 or r.height < 6:
                    r = r + (-4, -4, 4, 4)
                inside = r.contains(pt)
            if inside:
                c = (a.colors or {}).get("stroke") or (a.colors or {}).get("fill") or (1, 0.82, 0)
                color = QColor.fromRgbF(*[max(0.0, min(1.0, float(x))) for x in c[:3]]).name()
                hit = (a.xref, info, color)  # topmost (last) wins
        return hit

    def update_note_preview(self, sp, global_pos):
        i = self.page_at(sp)
        note = None
        if i is not None:
            note = self.note_at(self.doc[i], self.to_pdf(i, sp))
        if note is None:
            self.hide_note_preview()
            return
        if getattr(self, "note_preview", None) is None:
            self.note_preview = NotePreview(self.canvas)
        xref, info, color = note
        self.note_preview.show_note((id(self.doc), i, xref, info.get("content")), info, color, global_pos)

    def hide_note_preview(self):
        p = getattr(self, "note_preview", None)
        if p is not None and p.isVisible():
            p.hide_note()

    # ------------------------------------------------------------------ hit tests
    def annot_at(self, page, pt):
        hit = None
        for a in page.annots():
            if a.type[0] == fitz.PDF_ANNOT_POPUP:
                continue
            r = fitz.Rect(a.rect)
            if r.width < 6 or r.height < 6:
                r = r + (-4, -4, 4, 4)
            if r.contains(pt):
                hit = a  # topmost (last) wins
        if hit is not None:
            hit._page_ref = page  # see find_annot
        return hit

    def find_annot(self, page, xref):
        for a in page.annots():
            if a.xref == xref:
                # an annotation only refers weakly to its page; keep the page alive while
                # the annotation is used, or a temporary page (doc[i]) frees it -> crash
                a._page_ref = page
                return a
        return None

    def widget_at(self, page, pt):
        for w in page.widgets():
            if fitz.Rect(w.rect).contains(pt):
                w._page_ref = page  # keep the page alive while the field is used
                return w
        return None

    def text_blocks(self, page):
        key = page.number
        if key in self.repair_todo:  # about to be edited: repair this page's text first
            self.repair_todo.remove(key)
            self._repair_page(key)
        if key not in self.block_cache:
            # boxes as a person sees them: table cells, labels, values and paragraphs
            self.block_cache[key] = textunits.units(page)
        return self.block_cache[key]

    def block_at(self, page, pt, margin=0):
        for b in self.text_blocks(page):
            if (fitz.Rect(b["bbox"]) + (-margin, -margin, margin, margin)).contains(pt):
                return b
        return None

    # ------------------------------------------------------------------ moving text
    def _drag_block(self, d, sp):
        """While a text box is dragged: a see-through copy of it follows the mouse."""
        i = d["page"]
        moved = sp - d["sp"]
        if d["kind"] == "blockpress":
            if abs(moved.x()) + abs(moved.y()) < 4:
                return  # still a click
            d["kind"] = "blockmove"
            self.clear_overlay()
            rect = fitz.Rect(d["block"]["bbox"])
            srect = self.to_scene_rect(i, rect)
            d["srect"] = srect
            page = self.doc[i]
            items = []
            if page.rotation == 0:  # a picture of the text itself
                z = max(2.0, self.zoom * 2)
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), clip=rect + (-1, -1, 1, 1), alpha=False)
                img = pixmap_to_qimage(pix)
                pm = QPixmap.fromImage(img)
                ghost = QGraphicsPixmapItem(pm)
                ghost.setOpacity(0.75)
                k = srect.width() / max(rect.width, 0.1)  # screen units per PDF point
                ghost.setScale(k / z)
                ghost.setPos(srect.topLeft() - QPointF(k, k))  # (1 point of margin)
                ghost.setZValue(11)
                items.append(ghost)
            box = QGraphicsRectItem(srect.adjusted(-2, -2, 2, 2))
            pen = QPen(QColor(theme.color("accent")), 0)
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
            box.setPen(pen)
            box.setBrush(QBrush(QColor(124, 131, 255, 25)))
            box.setZValue(12)
            items.append(box)
            # where it was: a dashed outline
            was = QGraphicsRectItem(srect.adjusted(-2, -2, 2, 2))
            was.setPen(self.dashed())
            was.setZValue(10)
            for it_ in items + [was]:
                self.canvas.scene().addItem(it_)
                self.overlay.append(it_)
            d["ghost"] = items
            d["ghost_pos"] = [x.pos() for x in items]
            self.canvas.viewport().setCursor(Qt.ClosedHandCursor)
        # keep the box on its page
        page_r = self.items[i].sceneBoundingRect()
        r = d["srect"].translated(moved)
        dx = max(0.0, page_r.left() - r.left()) - max(0.0, r.right() - page_r.right())
        dy = max(0.0, page_r.top() - r.top()) - max(0.0, r.bottom() - page_r.bottom())
        moved = moved + QPointF(dx, dy)
        d["offset"] = moved
        for x, p0 in zip(d["ghost"], d["ghost_pos"]):
            x.setPos(p0 + moved)

    def move_block(self, i, block, delta):
        """Move a text box: the text is taken out and written again at the new place, with
        the same fonts, sizes and colours."""
        self.open_block_editor(i, block)
        ed = self.editing
        if not ed:
            return
        ed["move"] = delta
        self.commit_edit()
        self.set_status("Text moved. Undo (Ctrl+Z) puts it back.")

    def _hide_edit_outlines(self):
        for it in getattr(self, "edit_items", []):
            if it.scene():
                it.scene().removeItem(it)
        self.edit_items = []

    def _show_edit_outlines(self):
        """Thin outlines around every paragraph that can be edited (on the pages in view)."""
        self._hide_edit_outlines()
        if self.doc is None or self.tool != "edit":
            return
        vis = self.visible_rect()
        near = vis.adjusted(0, -vis.height(), 0, vis.height())
        pen = QPen(QColor(124, 131, 255, 110), 0)
        pen.setCosmetic(True)
        for it in self.items:
            if not it.sceneBoundingRect().intersects(near):
                continue
            for b in self.text_blocks(self.doc[it.index]):
                box = QGraphicsRectItem(self.to_scene_rect(it.index, fitz.Rect(b["bbox"])).adjusted(-1.5, -1.5, 1.5, 1.5))
                box.setPen(pen)
                box.setZValue(7)
                self.canvas.scene().addItem(box)
                self.edit_items.append(box)

    def update_hover(self, sp):
        i = self.page_at(sp)
        rect = None
        if i is not None:
            page = self.doc[i]
            pt = self.to_pdf(i, sp)
            b = self.block_at(page, pt)
            if b is not None:
                rect = fitz.Rect(b["bbox"])
            else:
                img = self._image_hit(i, pt)
                if img:
                    rect = img[1]
        if rect is None:
            if self.hover_item and self.hover_item.scene():
                self.hover_item.scene().removeItem(self.hover_item)
            self.hover_item = None
            return
        r = self.to_scene_rect(i, rect).adjusted(-2, -2, 2, 2)
        if self.hover_item is None or not self.hover_item.scene():
            self.hover_item = QGraphicsRectItem()
            pen = QPen(QColor(theme.color("accent")), 0)
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
            self.hover_item.setPen(pen)
            self.hover_item.setBrush(QBrush(QColor(124, 131, 255, 18)))
            self.hover_item.setZValue(9)
            self.canvas.scene().addItem(self.hover_item)
        self.hover_item.setRect(r)

    # ------------------------------------------------------------------ select tool
    def select_annot(self, i, annot):
        self.clear_overlay()
        if annot is None:
            self.selected = None
            return
        rect = fitz.Rect(annot.rect)
        resizable = annot.type[0] in RESIZABLE_ANNOTS
        self._show_selection(i, rect, resizable)
        self.selected = ("annot", i, annot.xref, rect)
        kind = self.COMMENT_TYPES.get(annot.type[0], annot.type[1])
        tips = ["Delete removes it"]
        if annot.type[0] in MOVABLE_ANNOTS:
            tips.append("drag to move it")
        if resizable:
            tips.append("drag a handle to resize it")
        tips.append("double-click to edit its text")
        self.set_status(f"{kind} selected — " + ", ".join(tips) + ".")

    def _annot_movable(self, i, xref):
        a = self.find_annot(self.doc[i], xref)
        return a is not None and a.type[0] in MOVABLE_ANNOTS

    def _show_selection(self, i, rect, handles=True):
        sr = self.to_scene_rect(i, rect)
        self.add_overlay_rect(sr, self.dashed())
        self.handles = []
        if not handles:
            return
        for name, p in self._handle_points(sr).items():
            h = QGraphicsRectItem(-4.5, -4.5, 9, 9)
            h.setFlag(QGraphicsRectItem.ItemIgnoresTransformations)
            h.setBrush(QBrush(Qt.white))
            h.setPen(QPen(QColor("#7C83FF"), 1.5))
            h.setPos(p)
            h.setZValue(12)
            self.canvas.scene().addItem(h)
            self.overlay.append(h)
            self.handles.append((name, h))

    def selection_words(self, i, a, b):
        words = self.doc[i].get_text("words")
        if not words:
            return []

        def nearest(p):
            best, bd = 0, 1e18
            for k, w in enumerate(words):
                r = fitz.Rect(w[:4])
                dx = max(r.x0 - p.x, 0, p.x - r.x1)
                dy = max(r.y0 - p.y, 0, p.y - r.y1)
                d = dx * dx + dy * dy * 4
                if d < bd:
                    best, bd = k, d
            return best

        s, e = sorted((nearest(a), nearest(b)))
        return words[s:e + 1]

    def show_text_selection(self, i, a, b):
        self.clear_overlay()
        brush = QBrush(QColor(124, 131, 255, 90))
        for w in self.selection_words(i, a, b):
            self.add_overlay_rect(self.to_scene_rect(i, fitz.Rect(w[:4])), brush=brush)

    @needs_doc
    def copy_text(self):
        if self.editing:
            self.editing["box"].copy()
            return
        if self.selected and self.selected[0] == "image":
            self.copy_picture(self.selected[1], self.selected[2], self.selected[3])
            return
        if self.selected and self.selected[0] == "text":
            words = self.selected[2]
            lines, cur = [], None
            for w in words:
                key = (w[5], w[6])
                if key != cur:
                    lines.append([])
                    cur = key
                lines[-1].append(w[4])
            QApplication.clipboard().setText("\n".join(" ".join(l) for l in lines))
            self.set_status("Copied.")

    def mark_text(self, i, words):
        page = self.doc[i]
        quads = [fitz.Rect(w[:4]).quad for w in words]
        if self.tool == "highlight" and is_redact_color(self.colors["highlight"]):
            return self.redact_words(i, words)
        self.checkpoint()
        page = self.doc[i]
        t = self.tool
        if t == "highlight":
            a = page.add_highlight_annot(quads)
        elif t == "underline":
            a = page.add_underline_annot(quads)
        else:
            a = page.add_strikeout_annot(quads)
        a.set_colors(stroke=rgb(self.colors[t]))
        a.update()
        self.page_changed(i)

    def _confirm_redact(self, what):
        """Ask before removing text for good (with a 'don't ask again' box)."""
        s = version.settings()
        if s.value("redact_no_confirm", False, type=bool):
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Redact")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Permanently remove {what}?")
        box.setInformativeText("It is blacked out and taken out of the file, so it can't be "
                               "copied or searched. You can Undo until you close the file.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Yes)
        from PySide6.QtWidgets import QCheckBox
        cb = QCheckBox("Don't ask me again")
        box.setCheckBox(cb)
        if box.exec() != QMessageBox.Yes:
            return False
        if cb.isChecked():
            s.setValue("redact_no_confirm", True)
        return True

    def redact_words(self, i, words):
        n = len(words)
        if not self._confirm_redact(f"the {n} selected word{'s' if n != 1 else ''}"):
            return
        self.checkpoint()
        page = self.doc[i]
        for w in words:
            page.add_redact_annot(fitz.Rect(w[:4]), fill=(0, 0, 0))
        # only the words go: table lines and pictures around them stay
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        self.page_changed(i)
        self.set_status(f"Redacted {n} word(s). Undo (Ctrl+Z) brings them back until you "
                        "close the file.")

    # ------------------------------------------------------------------ copy, cut, paste
    CLIP_MIME = "application/x-drpdf-clip"

    def _set_clip(self, clip, text=None, image=None):
        """Keep what was copied (for pasting into any PDF open here) and put a plain copy
        on the Windows clipboard too (for Word, email, ...)."""
        import uuid
        clip["id"] = uuid.uuid4().hex
        self.clip = clip
        md = QMimeData()
        if text is not None:
            md.setText(text)
        if image is not None:
            md.setImageData(image)
        md.setData(self.CLIP_MIME, clip["id"].encode())
        QApplication.clipboard().setMimeData(md)

    def _clip_from_clipboard(self):
        """What Ctrl+V would paste: our own copy, or a picture / text from another program."""
        md = QApplication.clipboard().mimeData()
        if md is None:
            return None
        own = getattr(self, "clip", None)
        if own and md.hasFormat(self.CLIP_MIME) and \
                bytes(md.data(self.CLIP_MIME)).decode(errors="ignore") == own["id"]:
            return own
        if md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                img.save(buf, "PNG")
                # screen pictures: 96 per inch -> PDF points
                return {"kind": "picture", "data": bytes(buf.data()), "px": (img.width(), img.height()),
                        "size": (img.width() * 0.75, img.height() * 0.75)}
        if md.hasText() and md.text().strip():
            return {"kind": "text", "text": md.text()}
        return None

    def _text_box_clip(self, i, block):
        """A text box as styled lines (fonts, sizes, colours kept)."""
        self.open_block_editor(i, block)
        ed = self._remove_editor()
        if not ed:
            return None
        lines = self._layout_runs(ed["orig_runs"], ed["extent"] if ed["wrap"] else None)
        text = "\n".join("".join(tx for tx, _ in line) for line in lines)
        return {"kind": "textbox", "lines": lines, "line_h": ed["line_h"],
                "base_size": ed["base_size"], "text": text}

    def copy_text_box(self, i, block, cut=False):
        clip = self._text_box_clip(i, block)
        if clip is None:
            return
        self._set_clip(clip, text=clip["text"])
        if cut:
            self.delete_text_box(i, block, status=False)
            self.set_status("Text cut. Right-click where it should go (on any page or PDF) "
                            "and choose Paste, or press Ctrl+V.")
        else:
            self.set_status("Text copied. Right-click where it should go (on any page or PDF) "
                            "and choose Paste, or press Ctrl+V.")

    def delete_text_box(self, i, block, status=True):
        self.cancel_edit()
        self.checkpoint()
        page = self.doc[i]
        dx, dy = block["lines"][0]["dir"]
        rotate = round(math.degrees(math.atan2(-dy, dx))) % 360
        for line in block["lines"]:
            page.add_redact_annot(self._line_inner(fitz.Rect(line["bbox"]), rotate), fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        self.page_changed(i)
        if self.tool == "edit":
            QTimer.singleShot(0, self._show_edit_outlines)
        if status:
            self.set_status("Text removed.")

    def _picture_png(self, xref):
        """The picture's own pixels (transparency kept) as PNG."""
        pix = fitz.Pixmap(self.doc, xref)
        smask = self.doc.xref_get_key(xref, "SMask")
        if smask[0] == "xref":
            try:
                pix = fitz.Pixmap(pix, fitz.Pixmap(self.doc, int(smask[1].split()[0])))
            except Exception:
                pass
        if pix.colorspace and pix.colorspace.n not in (1, 3):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix

    def copy_picture(self, i, xref, rect, cut=False):
        try:
            pix = self._picture_png(xref)
        except Exception as e:
            QMessageBox.warning(self, "Copy picture", f"This picture couldn't be copied.\n\n{e}")
            return
        data = pix.tobytes("png")
        img = QImage.fromData(data, "PNG")
        r = fitz.Rect(rect)
        self._set_clip({"kind": "picture", "data": data, "px": (pix.width, pix.height),
                        "size": (r.width, r.height)}, image=img)
        if cut:
            self.delete_selected()
            self.set_status("Picture cut. Right-click where it should go (on any page or PDF) "
                            "and choose Paste, or press Ctrl+V.")
        else:
            self.set_status("Picture copied. Right-click where it should go (on any page or PDF) "
                            "and choose Paste, or press Ctrl+V.")

    @needs_doc
    def cut_selected(self):
        if self.editing:
            self.editing["box"].cut()
            return
        s = self.selected
        if s and s[0] == "image":
            self.copy_picture(s[1], s[2], s[3], cut=True)
        elif s and s[0] == "text":
            self.copy_text()
            self.set_status("Copied. (To cut text, right-click it with the Edit tool and choose "
                            "Cut text box.)")

    def _paste_point(self):
        """Where Ctrl+V pastes: under the mouse if it is over a page, else near the top of
        the page in view."""
        vp = self.canvas.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        if vp.rect().contains(pos):
            sp = self.canvas.mapToScene(pos)
            if self.page_at(sp) is not None:
                return sp
        i = self.current_page()
        it = self.items[i]
        vis = self.visible_rect() & it.sceneBoundingRect()
        top = vis.top() if not vis.isEmpty() else it.y()
        return QPointF(it.x() + it.rect().width() * 0.15, top + 40)

    @needs_doc
    def paste(self, sp=None):
        if self.editing:
            self.editing["box"].paste()
            return
        clip = self._clip_from_clipboard()
        if clip is None:
            self.set_status("Nothing to paste: copy text or a picture first.")
            return
        sp = sp or self._paste_point()
        i = self.page_at(sp)
        if i is None:
            return
        page = self.doc[i]
        if clip["kind"] == "picture":
            # same size as it was (at most the page), its top-left where you pasted
            w, h = clip["size"]
            it = self.items[i]
            scale_w = it.rect().width() / max(page.rect.width, 1)
            w, h = w * scale_w, h * scale_w
            fit = min(1.0, it.rect().width() * 0.95 / max(w, 1), it.rect().height() * 0.95 / max(h, 1))
            w, h = w * fit, h * fit
            x = min(max(sp.x(), it.x()), it.x() + it.rect().width() - w)
            y = min(max(sp.y(), it.y()), it.y() + it.rect().height() - h)
            self._insert_picture(i, QPointF(x, y), QPointF(x + w, y + h), clip["data"], *clip["px"])
            self.set_status("Picture pasted. Drag it to move it, drag the handles to resize it.")
            return
        if clip["kind"] == "text":
            # text from another program: a new text box in the current text settings
            try:
                size = max(1.0, float(self.size_box.currentText()))
            except ValueError:
                size = 12.0
            family = self.font_box.currentText()
            if family not in fonts.FAMILIES:
                family = fonts.DEFAULT_FAMILY
            st = (family, int(self.bold_btn.isChecked()), int(self.italic_btn.isChecked()), size,
                  QColor(self.colors["addtext"]).name())
            lines = [[(ln, st)] for ln in clip["text"].replace("\r\n", "\n").split("\n")]
            clip = {"lines": lines, "line_h": size * 1.2, "base_size": size}
        # a text box: first baseline one ascent below the paste point
        first = clip["lines"][0][0][1] if clip["lines"] and clip["lines"][0] else None
        size = first[3] if first else clip["base_size"]
        asc = 0.8
        if first:
            f = fonts.font_object(first[0], first[1], first[2])[2]
            asc = f.ascender if f.ascender > 0 else 0.8
        origin = self.to_pdf(i, QPointF(sp.x(), sp.y() + asc * size * self.items[i].rect().width()
                                        / max(page.rect.width, 1)))
        self.checkpoint()
        try:
            write_runs(page, origin, clip["lines"], page.rotation, clip["line_h"], clip["base_size"])
        except Exception as e:
            self._drop_checkpoint()
            QMessageBox.warning(self, "Paste", f"Couldn't paste that text.\n\n{e}")
            return
        try:
            self.doc.reload_page(page)
        except Exception:
            pass
        self.page_changed(i)
        if self.tool == "edit":
            QTimer.singleShot(0, self._show_edit_outlines)
        self.set_status("Text pasted. With the Edit tool you can drag it to move it or click to "
                        "change it.")

    def on_context_menu(self, sp, global_pos):
        """Right-click on a page: copy, cut, delete or paste text boxes and pictures."""
        if self.doc is None:
            return False
        if self.editing and self.editing["proxy"].sceneBoundingRect().contains(sp):
            return False  # the text editor's own menu (cut / copy / paste of words)
        i = self.page_at(sp)
        if i is None:
            return False
        self.commit_edit()
        page = self.doc[i]
        pt = self.to_pdf(i, sp)
        m = QMenu(self)
        s = self.selected
        if s and s[0] == "text" and s[1] == i and \
                any(fitz.Rect(w[:4]).contains(pt) for w in s[2]):
            m.addAction("Copy", self.copy_text)
        img = self._image_hit(i, pt)
        block = self.block_at(page, pt)
        if img is not None and block is None:
            xref, rect, occ = img
            self.select_image(i, xref, rect, occ)
            m.addAction("Copy picture", lambda: self.copy_picture(i, xref, rect))
            m.addAction("Cut picture", lambda: self.copy_picture(i, xref, rect, cut=True))
            m.addAction("Delete picture", self.delete_selected)
        elif block is not None:
            m.addAction("Copy text box", lambda: self.copy_text_box(i, block))
            m.addAction("Cut text box", lambda: self.copy_text_box(i, block, cut=True))
            m.addAction("Delete text box", lambda: self.delete_text_box(i, block))
        if m.actions():
            m.addSeparator()
        clip = self._clip_from_clipboard()
        what = {"picture": "Paste picture here", "textbox": "Paste text here",
                "text": "Paste text here"}.get(clip["kind"] if clip else "", "Paste here")
        a = m.addAction(what, lambda: self.paste(sp))
        a.setEnabled(clip is not None)
        m.exec(global_pos)
        return True

    @needs_doc
    def delete_selected(self):
        if self.editing:
            return
        focus = QApplication.focusWidget()
        if focus is self.bm_tree:
            return self.delete_bookmark()
        if focus is self.comments_list:
            return self.delete_comment()
        if focus is self.thumbs and self.thumbs.selectedItems():
            n = len(self.thumbs.selectedItems())
            if QMessageBox.question(self, "Delete pages", f"Delete {n} selected page(s)?") \
                    == QMessageBox.Yes:
                self.delete_pages()
            return
        s = self.selected
        if not s or self.doc is None:
            return
        i = s[1]
        page = self.doc[i]
        if s[0] == "widget":
            self.checkpoint()
            page = self.doc[i]
            w = self._find_widget(page, s[2])
            if w is not None:
                page.delete_widget(w)
            if self.tool == "form":
                QTimer.singleShot(0, self._show_form_outlines)
        elif s[0] == "annot":
            if self.find_annot(page, s[2]) is None:
                return
            self.checkpoint()
            page = self.doc[i]
            page.delete_annot(self.find_annot(page, s[2]))
        elif s[0] == "image":
            self.checkpoint()
            if not self._remove_image(i, s[2], s[4]):
                self._drop_checkpoint()
                self.set_status("This picture is shared with other pages, so it wasn't deleted. "
                                "Use Whiteout to cover it on this page.")
                return
        else:
            return
        self.selected = None
        self.clear_overlay()
        self.page_changed(i)
        self.set_status("Deleted.")

    def fill_widget(self, i, w):
        t = w.field_type
        name = w.field_label or w.field_name or "Field"
        xref = w.xref
        if t in (fitz.PDF_WIDGET_TYPE_TEXT,):
            val, ok = QInputDialog.getText(self, "Fill in", name, text=str(w.field_value or ""))
            if not ok:
                return
            new = val
        elif t in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            on = w.on_state()
            checked = w.field_value not in (False, "Off", "", None)
            new = "Off" if checked and t == fitz.PDF_WIDGET_TYPE_CHECKBOX else on
        elif t == fitz.PDF_WIDGET_TYPE_SIGNATURE:
            if w.is_signed:
                self.signatures_dialog()
            elif QMessageBox.question(self, "Signature field",
                                      f"Sign “{name}” with your digital ID?") == QMessageBox.Yes:
                self.digital_sign(field=w.field_name)
            return
        elif t in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
            choices = [c if isinstance(c, str) else c[1] for c in (w.choice_values or [])]
            if not choices:
                return
            cur = choices.index(w.field_value) if w.field_value in choices else 0
            val, ok = QInputDialog.getItem(self, "Choose", name, choices, cur, False)
            if not ok:
                return
            new = val
        else:
            return
        self.checkpoint()
        for w2 in self.doc[i].widgets():
            if w2.xref == xref:
                w2.field_value = new
                w2.update()
                break
        self.page_changed(i)

    # ------------------------------------------------------------------ notes, images, signatures
    def add_note(self, i, pt):
        text, ok = QInputDialog.getMultiLineText(self, "Sticky note", "Note:")
        if not ok or not text.strip():
            return
        self.checkpoint()
        a = self.doc[i].add_text_annot(pt, text, icon="Note")
        a.set_colors(stroke=rgb(self.colors["note"]))
        a.update()
        self.page_changed(i)

    def _insert_picture(self, i, a, b, data, w, h):
        """Place picture bytes on page i, inside the scene box a-b (or at a)."""
        page = self.doc[i]
        it = self.items[i]
        if b is None:  # clicked: natural size, at most half the page width
            width = min(w * 0.75, it.rect().width() / 2)
            height = width * h / max(w, 1)
            a = QPointF(a.x() - width / 2, a.y() - height / 2)
            b = QPointF(a.x() + width, a.y() + height)
        r = self.to_pdf_rect(i, a, b)
        self.checkpoint()
        page.insert_image(r, stream=data, rotate=page.rotation, keep_proportion=True)
        self.page_changed(i)
        self.set_tool("select")
        self._reselect_image(i, r)
        self.set_status("Picture added. Drag it to move it, drag the handles to resize it.")

    def place_image(self, i, a, b):
        p, _ = QFileDialog.getOpenFileName(
            self, "Choose a picture", "",
            "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.heic)")
        if not p:
            return
        try:
            from PIL import Image
            import io
            with Image.open(p) as im:
                im.load()
                from PIL import ImageOps
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA", "L"):
                    im = im.convert("RGBA")
                buf = io.BytesIO()
                im.save(buf, "PNG")
                self._insert_picture(i, a, b, buf.getvalue(), im.width, im.height)
        except Exception as e:
            QMessageBox.warning(self, "Picture", f"Couldn't add that picture.\n\n{e}")

    def new_signature(self):
        dlg = SignatureDialog(self)
        if dlg.exec() == QDialog.Accepted:
            dlg.png()

    def place_signature(self, i, sp):
        if not SIGNATURE_FILE.exists():
            self.new_signature()
            if not SIGNATURE_FILE.exists():
                return
        img = QImage(str(SIGNATURE_FILE))
        width = 150 * (self.items[i].rect().width() / 612)
        height = width * img.height() / max(img.width(), 1)
        a = QPointF(sp.x() - width / 2, sp.y() - height / 2)
        b = QPointF(sp.x() + width / 2, sp.y() + height / 2)
        self._insert_picture(i, a, b, SIGNATURE_FILE.read_bytes(), img.width(), img.height())
        self.set_status("Signature added. Drag it to move it, drag the handles to resize it.")

    # ------------------------------------------------------------------ pictures: select, move, resize, delete
    def _image_infos(self, page, xref):
        return [x for x in page.get_image_info(xrefs=True) if x.get("xref") == xref]

    def _image_hit(self, i, pt, skip_background=False):
        """(xref, bbox, occurrence) of the top-most picture under pt, or None."""
        page = self.doc[i]
        area = abs(page.rect)
        hit, seen = None, {}
        for info in page.get_image_info(xrefs=True):
            x = info.get("xref") or 0
            occ = seen.get(x, 0)
            seen[x] = occ + 1
            r = fitz.Rect(info["bbox"])
            if not x or r.is_empty or not r.contains(pt):
                continue
            if skip_background and abs(r) > 0.85 * area:
                continue  # a full-page scan/background: leave it to the Edit tool
            hit = (x, r, occ)
        return hit

    def select_image(self, i, xref, rect, occ):
        self.clear_overlay()
        self._show_selection(i, rect)
        self.selected = ("image", i, xref, rect, occ)
        self.set_status("Picture selected: drag to move it, drag a handle to resize it "
                        "(hold Shift to stretch freely), Delete removes it.")

    @staticmethod
    def _handle_points(r):
        cx, cy = r.center().x(), r.center().y()
        return {"tl": r.topLeft(), "t": QPointF(cx, r.top()), "tr": r.topRight(),
                "r": QPointF(r.right(), cy), "br": r.bottomRight(), "b": QPointF(cx, r.bottom()),
                "bl": r.bottomLeft(), "l": QPointF(r.left(), cy)}

    def handle_at(self, sp):
        if not (self.selected and self.selected[0] in ("image", "annot", "widget") and self.handles):
            return None
        vp = self.canvas.mapFromScene(sp)
        for name, h in self.handles:
            if not h.scene():
                continue
            hp = self.canvas.mapFromScene(h.pos())
            if abs(hp.x() - vp.x()) <= 7 and abs(hp.y() - vp.y()) <= 7:
                return name
        return None

    def update_cursor(self, sp):
        h = self.handle_at(sp)
        s = self.selected
        if h:
            cur = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                   "bl": Qt.SizeBDiagCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                   "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor}[h]
        elif s and s[0] in ("image", "annot", "widget") and len(s) > 3 and \
                self.to_scene_rect(s[1], s[3]).contains(sp) and \
                (s[0] in ("image", "widget") or self._annot_movable(s[1], s[2])):
            cur = Qt.SizeAllCursor
        else:
            cur = self.tool_cursor()
        if self.canvas.viewport().cursor().shape() != cur:
            self.canvas.viewport().setCursor(cur)

    def tool_cursor(self):
        return {"select": Qt.ArrowCursor, "edit": Qt.IBeamCursor, "addtext": Qt.IBeamCursor,
                "highlight": Qt.IBeamCursor, "underline": Qt.IBeamCursor,
                "strike": Qt.IBeamCursor}.get(self.tool, Qt.CrossCursor)

    def _start_image_drag(self, i, sp, mode):
        """Start moving / resizing the selected picture or comment box."""
        rect = self.selected[3]
        orig = self.to_scene_rect(i, rect)
        it = self.items[i]
        cover = QGraphicsRectItem(orig)
        cover.setBrush(QBrush(QColor(255, 255, 255, 200)))
        cover.setPen(QPen(Qt.NoPen))
        cover.setZValue(10.5)
        self.canvas.scene().addItem(cover)
        self.overlay.append(cover)
        ghost = None
        pm = it.pix.pixmap()
        if not pm.isNull() and it.rendered:
            s = it.rendered
            crop = pm.copy(int((orig.x() - it.x()) * s), int((orig.y() - it.y()) * s),
                           max(1, int(orig.width() * s)), max(1, int(orig.height() * s)))
            ghost = QGraphicsPixmapItem(crop)
            ghost.setTransformationMode(Qt.SmoothTransformation)
            ghost.setOpacity(0.9)
            ghost.setZValue(11)
            self.canvas.scene().addItem(ghost)
            self.overlay.append(ghost)
        self.drag = {"kind": "image", "mode": mode, "page": i, "start": sp, "orig": orig,
                     "ghost": ghost, "rect": orig}

    def _show_drag_rect(self, d, r):
        d["rect"] = r
        self.overlay[0].setRect(r)
        points = self._handle_points(r)
        for name, h in self.handles:
            h.setPos(points[name])
        g = d["ghost"]
        if g is not None:
            pm = g.pixmap()
            g.setPos(r.topLeft())
            g.setTransform(QTransform.fromScale(r.width() / max(pm.width(), 1),
                                                r.height() / max(pm.height(), 1)))

    def _drag_rect(self, d, sp, free):
        o = d["orig"]
        delta = sp - d["start"]
        mode = d["mode"]
        if mode == "move":
            return o.translated(delta)
        minsz = 6.0
        l, t, r, b = o.left(), o.top(), o.right(), o.bottom()
        if mode in ("tl", "tr", "bl", "br") and not free:
            ax = r if "l" in mode else l  # the opposite corner stays put
            ay = b if "t" in mode else t
            mx = (l if "l" in mode else r) + delta.x()
            my = (t if "t" in mode else b) + delta.y()
            dx0, dy0 = (l if "l" in mode else r) - ax, (t if "t" in mode else b) - ay
            s = max(((mx - ax) * dx0 + (my - ay) * dy0) / max(dx0 * dx0 + dy0 * dy0, 1e-6), 0)
            w = max(o.width() * s, minsz)
            h = max(o.height() * s, minsz)
            x0 = ax - w if "l" in mode else ax
            y0 = ay - h if "t" in mode else ay
            return QRectF(x0, y0, w, h)
        if "l" in mode:
            l = min(l + delta.x(), r - minsz)
        if "r" in mode:
            r = max(r + delta.x(), l + minsz)
        if "t" in mode:
            t = min(t + delta.y(), b - minsz)
        if "b" in mode:
            b = max(b + delta.y(), t + minsz)
        return QRectF(QPointF(l, t), QPointF(r, b))

    def _finish_image_drag(self, d):
        if self.selected and self.selected[0] == "annot":
            return self._finish_annot_drag(d)
        if self.selected and self.selected[0] == "widget":
            return self._finish_widget_drag(d)
        i = d["page"]
        r, o = d["rect"], d["orig"]
        _, _, xref, rect, occ = self.selected
        if max(abs(r.left() - o.left()), abs(r.top() - o.top()),
               abs(r.right() - o.right()), abs(r.bottom() - o.bottom())) < 0.5:
            self.select_image(i, xref, rect, occ)  # just a click: keep it selected
            return
        new = self.to_pdf_rect(i, r.topLeft(), r.bottomRight())
        self.checkpoint()
        try:
            ok = self._set_image_rect(i, xref, occ, new)
        except Exception:
            ok = False
        if not ok:
            self._drop_checkpoint()
            self.select_image(i, xref, rect, occ)
            QMessageBox.information(
                self, "Picture",
                "This picture is drawn in a way Dr.PDF can't change safely (for example it is "
                "shared with other pages), so it was left as it was.")
            return
        self.page_changed(i)
        self._reselect_image(i, new)
        self.set_status("Picture moved." if d["mode"] == "move" else "Picture resized.")

    def _finish_annot_drag(self, d):
        i = d["page"]
        r, o = d["rect"], d["orig"]
        xref = self.selected[2]
        page = self.doc[i]
        annot = self.find_annot(page, xref)
        if annot is None:
            return
        if max(abs(r.left() - o.left()), abs(r.top() - o.top()),
               abs(r.right() - o.right()), abs(r.bottom() - o.bottom())) < 0.5:
            self.select_annot(i, annot)  # just a click
            return
        new = self.to_pdf_rect(i, r.topLeft(), r.bottomRight())
        self.checkpoint()
        page = self.doc[i]
        annot = self.find_annot(page, xref)
        # shapes re-add half their border width on set_rect, so take it off first
        bw = 0
        if annot.type[0] in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE):
            bw = (annot.border.get("width") or 0) / 2
        annot.set_rect(fitz.Rect(new.x0 + bw, new.y0 + bw, new.x1 - bw, new.y1 - bw))
        annot.update()
        self.page_changed(i)
        self.select_annot(i, self.find_annot(self.doc[i], xref))
        self.set_status("Moved." if d["mode"] == "move" else "Resized.")

    def _drop_checkpoint(self):
        """Undo a checkpoint whose change didn't happen (restores the saved copy)."""
        self.doc.close()
        self.doc = fitz.open("pdf", self.undo_stack.pop())
        self.block_cache = {}

    def _reselect_image(self, i, rect):
        best, bd = None, 1e18
        seen = {}
        for info in self.doc[i].get_image_info(xrefs=True):
            x = info.get("xref") or 0
            occ = seen.get(x, 0)
            seen[x] = occ + 1
            if not x:
                continue
            b = fitz.Rect(info["bbox"])
            dist = sum(abs(p - q) for p, q in zip(b, rect))
            if dist < bd:
                best, bd = (x, b, occ), dist
        if best:
            self.select_image(i, *best)
        else:
            self.selected = None
            self.clear_overlay()

    def _placement(self, page, xref, occ):
        """Find the 'cm' that positions one use of a picture in the page's content.

        Returns (content xref, data, cm start, cm end, Do start, Do end, matrix), or None
        when the picture is drawn in a way that can't safely be changed on its own."""
        doc = page.parent
        names = [im[7] for im in page.get_images(full=True) if im[0] == xref and im[9] == 0]
        if len(names) != 1:
            return None
        do_re = re.compile(rb"/" + re.escape(names[0].encode()) + rb"\s+Do(?![A-Za-z])")
        uses = []
        for cx in page.get_contents():
            data = doc.xref_stream(cx) or b""
            for m in do_re.finditer(data):
                uses.append((cx, data, m))
        if len(uses) != len(self._image_infos(page, xref)) or occ >= len(uses):
            return None
        cx, data, m = uses[occ]
        cms = list(CM_RE.finditer(data, max(0, m.start() - 4000), m.start()))
        if not cms:
            return None
        cm = cms[-1]
        if re.search(rb"(^|\s)(q|Q|Do|BT|ET|cm)(\s|$)", data[cm.end():m.start()]):
            return None
        if not re.match(rb"\s*Q(?![A-Za-z])", data[m.end():]):
            return None  # something else is drawn with the same position
        mat = fitz.Matrix(*[float(g) for g in cm.groups()])
        return cx, data, cm.start(), cm.end(), m.start(), m.end(), mat

    _TOKENS = re.compile(rb"(?:(?<=\s)|^)(q|Q|W\*|W|cm)(?=\s|$)")
    _RE_OP = re.compile(rb"(%s)\s+(%s)\s+(%s)\s+(%s)\s+re(?=\s*$)" % ((NUM_RE,) * 4))

    def _block_clips(self, data, cm_start):
        """Clipping rectangles set in the same q...Q block as the picture, before its cm.

        Returns [(start, end, rect)] for each 'x y w h re' that is used as a clip, an
        empty list when there is none, or None when the clipping can't be adjusted."""
        toks = list(self._TOKENS.finditer(data, max(0, cm_start - 20000), cm_start))
        depth, block_start = 0, None
        for t in reversed(toks):
            op = t.group(1)
            if op == b"Q":
                depth += 1
            elif op == b"q":
                if depth == 0:
                    block_start = t.end()
                    break
                depth -= 1
        if block_start is None:
            return []  # not inside its own block: nothing local to adjust
        clips, depth = [], 0
        for t in self._TOKENS.finditer(data, block_start, cm_start):
            op = t.group(1)
            if op == b"q":
                depth += 1
            elif op == b"Q":
                depth -= 1
            elif depth == 0 and op in (b"W", b"W*"):
                if clips is None:
                    continue
                m = self._RE_OP.search(data, max(block_start, t.start() - 200), t.start())
                if not m:
                    return None  # a clip shape other than a rectangle
                clips.append((m.start(), m.end(), fitz.Rect(
                    float(m.group(1)), float(m.group(2)),
                    float(m.group(1)) + float(m.group(3)), float(m.group(2)) + float(m.group(4)))))
            elif depth == 0 and op == b"cm" and clips:
                return None  # the clip was drawn in a different coordinate system
        return clips

    def _image_use_count(self, xref):
        n = 0
        for page in self.doc:
            if any(im[0] == xref for im in page.get_images(full=True)):
                n += len(self._image_infos(page, xref)) or 1
                if n > 1:
                    break
        return n

    def _forget_inserted_images(self):
        # PyMuPDF re-uses identical pictures it inserted earlier. After one is blanked
        # that would bring back the blank copy (the "picture disappears" bug).
        if hasattr(self.doc, "InsertedImages"):
            self.doc.InsertedImages = {}

    def _set_image_rect(self, i, xref, occ, new):
        page = self.doc[i]
        infos = self._image_infos(page, xref)
        if occ >= len(infos):
            return False
        total = fitz.Matrix(infos[occ]["transform"])
        old = fitz.Rect(0, 0, 1, 1) * total
        if old.width < 1e-6 or old.height < 1e-6 or new.is_empty:
            return False
        t = (fitz.Matrix(1, 0, 0, 1, -old.x0, -old.y0)
             * fitz.Matrix(new.width / old.width, 0, 0, new.height / old.height, 0, 0)
             * fitz.Matrix(1, 0, 0, 1, new.x0, new.y0))
        pl = self._placement(page, xref, occ)
        clips = self._block_clips(pl[1], pl[2]) if pl else None
        if pl and clips is not None:
            cx, data, s, e, _, _, local = pl
            # MuPDF's transform also flips the image vertically (PDF image rows run
            # bottom-up): total = FLIP * local * outer
            flip = fitz.Matrix(1, 0, 0, -1, 0, 1)
            outer = ~(flip * local) * total   # everything applied around this picture
            m = flip * total * t * ~outer     # new local matrix that lands it on `new`
            edits = [(s, e, " ".join(pdf_num(v) for v in (m.a, m.b, m.c, m.d, m.e, m.f)) + " cm")]
            # a clipping box around the picture (Word and others add one) must move with
            # it, or the picture ends up outside its own window and becomes invisible
            move = ~local * m
            for cs, ce, rect in clips:
                r = (rect * move).normalize()
                edits.append((cs, ce, " ".join(pdf_num(v) for v in
                                               (r.x0, r.y0, r.width, r.height)) + " re"))
            for a, b, text in sorted(edits, reverse=True):
                data = data[:a] + text.encode() + data[b:]
            self.doc.update_stream(cx, data)
            return True
        if self._image_use_count(xref) != 1:
            return False
        data = self._image_png(xref)  # fallback: redraw a picture that is used only here
        page.delete_image(xref)
        self._forget_inserted_images()
        page.insert_image(new, stream=data, keep_proportion=False, rotate=page.rotation)
        return True

    def _remove_image(self, i, xref, occ):
        page = self.doc[i]
        pl = self._placement(page, xref, occ)
        if pl:
            cx, data, _, _, ds, de, _ = pl
            self.doc.update_stream(cx, data[:ds] + data[de:])
            return True
        if self._image_use_count(xref) != 1:
            return False
        page.delete_image(xref)
        self._forget_inserted_images()
        return True

    def _image_png(self, xref):
        pix = fitz.Pixmap(self.doc, xref)
        info = self.doc.extract_image(xref)
        smask = info.get("smask") if info else 0
        if smask:
            pix = fitz.Pixmap(pix, fitz.Pixmap(self.doc, smask))
        if pix.colorspace and pix.colorspace.n > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png")

    # ------------------------------------------------------------------ text editing
    # ------------------------------------------------------------------ text editing (rich)
    # A paragraph is edited as "runs": pieces of text that share one style
    # (family, bold, italic, size, colour). Mixed styles survive editing.
    @staticmethod
    def _fmt(style):
        family, bold, italic, size, color = style
        f = QTextCharFormat()
        f.setFontFamilies([family])
        f.setFontPointSize(max(1.0, size * EDIT_SCALE * 0.75))  # 96-dpi widget: px = pt * 4/3
        f.setFontWeight(QFont.Bold if bold else QFont.Normal)
        f.setFontItalic(bool(italic))
        f.setForeground(QColor(color))
        return f

    @staticmethod
    def _style_of(fmt, fallback):
        fams = fmt.fontFamilies() or []
        family = fams[0] if fams else fallback[0]
        size = fmt.fontPointSize() / (EDIT_SCALE * 0.75) if fmt.fontPointSize() > 0 else fallback[3]
        size = round(size * 2) / 2
        color = fmt.foreground().color().name() if fmt.foreground().style() != Qt.NoBrush \
            else fallback[4]
        return (family, int(fmt.fontWeight() >= QFont.DemiBold), int(fmt.fontItalic()),
                size, color)

    def _runs_from_editor(self, box, fallback):
        """[[(text, style), ...] per paragraph] from the editor, adjacent runs merged."""
        paras = []
        block = box.document().begin()
        while block.isValid():
            runs = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.text():
                    st = self._style_of(frag.charFormat(), fallback)
                    if runs and runs[-1][1] == st:
                        runs[-1] = (runs[-1][0] + frag.text(), st)
                    else:
                        runs.append((frag.text(), st))
                it += 1
            paras.append(runs)
            block = block.next()
        while paras and not "".join(t for t, _ in paras[-1]).strip():
            paras.pop()  # trailing empty lines
        return paras

    @staticmethod
    def _merge(paras):
        out = []
        for runs in paras:
            m = []
            for t, st in runs:
                if m and m[-1][1] == st:
                    m[-1] = (m[-1][0] + t, st)
                elif t:
                    m.append((t, st))
            out.append(m)
        return out

    def _fill_editor(self, box, paras):
        box.clear()
        cur = box.textCursor()
        for k, runs in enumerate(paras):
            if k:
                cur.insertBlock()
            for text, st in runs:
                cur.insertText(text, self._fmt(st))
        if paras and paras[0]:
            box.setCurrentCharFormat(self._fmt(paras[0][0][1]))

    def _make_editor(self, i, scene_rect, paras, wrap, default_style):
        box = TextBox()
        box.setAcceptRichText(False)
        box.setFrameShape(QFrame.NoFrame)
        box.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        box.document().setDocumentMargin(0)
        box.setLineWrapMode(QTextEdit.WidgetWidth if wrap else QTextEdit.NoWrap)
        # transparent: the page shows through, so the text looks exactly as it does in the PDF
        box.setAttribute(Qt.WA_TranslucentBackground, True)
        box.viewport().setAutoFillBackground(False)
        box.setStyleSheet("QTextEdit { background: transparent; border: none;"
                          " selection-background-color: rgba(91, 99, 240, 90); }")
        if paras:
            self._fill_editor(box, paras)
        else:
            box.setCurrentCharFormat(self._fmt(default_style))
        box.commit.connect(self.commit_edit)
        box.cancel.connect(self.cancel_edit)
        box.textChanged.connect(self.fit_editor)
        box.currentCharFormatChanged.connect(self._editor_format_changed)
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(box)
        proxy.setScale(1 / EDIT_SCALE)
        proxy.setPos(scene_rect.topLeft())
        proxy.setZValue(20)
        self.canvas.scene().addItem(proxy)
        frame = QGraphicsRectItem()
        pen = QPen(QColor(theme.color("accent")), 0)
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        frame.setPen(pen)
        frame.setZValue(21)
        self.canvas.scene().addItem(frame)
        return box, proxy, frame

    def _align_editor(self, i):
        """Move the editor so its first line sits exactly on the PDF's first baseline
        (the text must not jump when editing starts)."""
        ed = self.editing
        page = self.doc[i]
        if (ed["rotate"] - page.rotation) % 360:
            return  # text that runs sideways on screen: keep the box position
        box = ed["box"]
        doc = box.document()
        doc.documentLayout().documentSize()  # make sure the text is laid out
        block = doc.firstBlock()
        lay = block.layout()
        if lay is None or lay.lineCount() == 0:
            return
        line = lay.lineAt(0)
        base_px = lay.position().y() + line.y() + line.ascent()
        left_px = lay.position().x() + line.x()
        it = self.items[i]
        want = fitz.Point(ed["origin"]) * page.rotation_matrix  # baseline start on screen
        proxy = ed["proxy"]
        dx = (it.x() + want.x) - (proxy.pos().x() + left_px / EDIT_SCALE)
        dy = (it.y() + want.y) - (proxy.pos().y() + base_px / EDIT_SCALE)
        if ed.get("align", "left") != "left":
            dx = 0.0
        if abs(dx) < 20 and abs(dy) < 20:  # only small corrections; anything bigger is odd
            proxy.setPos(proxy.pos() + QPointF(dx, dy))
            self.fit_editor()

    @staticmethod
    def _line_inner(r, rotate):
        """The part of a text line to remove (a margin keeps neighbouring lines safe)."""
        h = r.height
        if rotate in (0, 180):
            return fitz.Rect(r.x0 - 0.5, r.y0 + h * 0.2, r.x1 + 0.5, r.y1 - h * 0.2)
        return fitz.Rect(r.x0 + r.width * 0.2, r.y0 - 0.5, r.x1 - r.width * 0.2, r.y1 + 0.5)

    def _edit_patch(self, i, line_rects, rotate, bbox):
        """A picture of this part of the page *without* the text being edited, laid over
        it while editing, so only the live, editable text is visible (no white box)."""
        it = self.items[i]
        scale = it.rendered
        if not scale:
            return None
        try:
            tmp = fitz.open()
            tmp.insert_pdf(self.doc, from_page=i, to_page=i)
            pg = tmp[0]
            for r in line_rects:
                pg.add_redact_annot(self._line_inner(r, rotate), fill=False)
            pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            tmp.close()
        except Exception:
            return None
        sr = self.to_scene_rect(i, bbox).adjusted(-2, -2, 2, 2) & it.sceneBoundingRect()
        img = pixmap_to_qimage(pix)
        crop = QPixmap.fromImage(img).copy(int((sr.x() - it.x()) * scale), int((sr.y() - it.y()) * scale),
                                           max(1, int(sr.width() * scale)), max(1, int(sr.height() * scale)))
        patch = QGraphicsPixmapItem(crop)
        patch.setTransformationMode(Qt.SmoothTransformation)
        patch.setPos(QPointF(int((sr.x() - it.x()) * scale) / scale + it.x(),
                             int((sr.y() - it.y()) * scale) / scale + it.y()))
        patch.setScale(1 / scale)
        patch.setZValue(19)
        self.canvas.scene().addItem(patch)
        return patch

    @staticmethod
    def _match_line_spacing(box, line_h):
        """Use the PDF's own line spacing, so the text doesn't jump when editing starts."""
        if not line_h:
            return
        fmt = QTextBlockFormat()
        fmt.setLineHeight(line_h * EDIT_SCALE, QTextBlockFormat.FixedHeight.value
                          if hasattr(QTextBlockFormat.FixedHeight, "value") else 2)
        cur = QTextCursor(box.document())
        cur.select(QTextCursor.Document)
        cur.mergeBlockFormat(fmt)

    def style_editor(self):
        """Apply the font controls to the selected text (or all text if nothing is selected)."""
        ed = self.editing
        if not ed:
            return
        box = ed["box"]
        st = (ed["family"], ed["bold"], ed["italic"], ed["size"], ed["color"].name())
        fmt = self._fmt(st)
        cur = box.textCursor()
        if cur.hasSelection():
            cur.mergeCharFormat(fmt)
        else:
            all_ = QTextCursor(box.document())
            all_.select(QTextCursor.Document)
            all_.mergeCharFormat(fmt)
            box.setCurrentCharFormat(fmt)
        self.fit_editor()

    def _editor_format_changed(self, fmt):
        """Show the style of the text at the cursor in the font controls."""
        ed = self.editing
        if not ed:
            return
        st = self._style_of(fmt, (ed["family"], ed["bold"], ed["italic"], ed["size"],
                                  ed["color"].name()))
        ed["family"], ed["bold"], ed["italic"], ed["size"] = st[0], st[1], st[2], st[3]
        ed["color"] = QColor(st[4])
        self._sync_props(ed)

    def fit_editor(self):
        ed = self.editing
        if not ed:
            return
        box = ed["box"]
        doc = box.document()
        if ed["wrap"]:
            w = ed["width"] * EDIT_SCALE
            box.setFixedWidth(int(w))
        else:
            doc.adjustSize()
            w = max(doc.idealWidth() + 8 * EDIT_SCALE, 60 * EDIT_SCALE)
            box.setFixedWidth(int(w))
        h = max(doc.size().height() + 4, ed["min_h"] * EDIT_SCALE)
        box.setFixedHeight(int(h))
        if ed.get("align", "left") != "left" and "anchor_x" in ed:
            it = self.items[ed["page"]]
            ax = it.x() + ed["anchor_x"]
            x = ax - (w / 2 if ed["align"] == "center" else w) / EDIT_SCALE
            ed["proxy"].setPos(QPointF(x, ed["proxy"].pos().y()))
        ed["frame"].setRect(QRectF(ed["proxy"].pos(), ed["proxy"].pos() +
                                   QPointF(w / EDIT_SCALE, h / EDIT_SCALE)).adjusted(-2, -2, 2, 2))

    def _sync_props(self, ed):
        for w in (self.font_box, self.size_box, self.bold_btn, self.italic_btn):
            w.blockSignals(True)
        if self.font_box.findText(ed["family"]) < 0:
            self.font_box.addItem(ed["family"])
        self.font_box.setCurrentText(ed["family"])
        s = ed["size"]
        self.size_box.setCurrentText(str(int(s)) if abs(s - round(s)) < 0.05 else f"{s:.1f}")
        self.bold_btn.setChecked(bool(ed["bold"]))
        self.italic_btn.setChecked(bool(ed["italic"]))
        for w in (self.font_box, self.size_box, self.bold_btn, self.italic_btn):
            w.blockSignals(False)
        self.colors[self.tool] = QColor(ed["color"])
        self.update_ui()

    @staticmethod
    def _span_style(span):
        family, bold, italic = fonts.match(span["font"], span["flags"])
        return (family, bold, italic, round(span["size"] * 2) / 2,
                qcolor_from_int(span["color"]).name())

    def open_block_editor(self, i, block, click=None):
        self.clear_overlay()
        if self.hover_item and self.hover_item.scene():
            self.hover_item.scene().removeItem(self.hover_item)
        self.hover_item = None
        lines = block["lines"]
        spans = [s for l in lines for s in l["spans"] if s["text"].strip() and not ocr.is_ocr_span(s)]
        first = spans[0]
        base = self._span_style(first)
        dx, dy = lines[0]["dir"]
        rotate = round(math.degrees(math.atan2(-dy, dx))) % 360
        horiz = rotate in (0, 180)
        bbox = fitz.Rect(block["bbox"])
        extent = bbox.width if horiz else bbox.height

        # rebuild paragraphs as styled runs: lines that run (nearly) the full width were
        # wrapped, so they join the next line with a space
        line_runs, widths = [], []
        for l in lines:
            # (no-break spaces and soft hyphens here are really spaces and hyphens: see
            # fonts.fix_space_mapping; a soft hyphen would be invisible in the editor)
            runs = [(s["text"].replace("\xa0", " ").replace("\xad", "-"), self._span_style(s))
                    for s in l["spans"]
                    if s["text"] and not ocr.is_ocr_span(s)]
            # drop trailing spaces of the line
            while runs and not runs[-1][0].rstrip():
                runs.pop()
            if runs:
                runs[-1] = (runs[-1][0].rstrip(), runs[-1][1])
            line_runs.append(runs)
            r = fitz.Rect(l["bbox"])
            widths.append(r.width if horiz else r.height)
        paras = [[]]
        for k, runs in enumerate(line_runs):
            paras[-1].extend(runs)
            if k < len(line_runs) - 1:
                t = "".join(x for x, _ in runs)
                wrapped = widths[k] > 0.7 * extent and not t.endswith((".", ":", ";"))
                if wrapped and t.endswith("-") and paras[-1]:
                    last = paras[-1][-1]
                    paras[-1][-1] = (last[0][:-1], last[1])
                elif wrapped and paras[-1]:
                    paras[-1].append((" ", paras[-1][-1][1]))
                else:
                    paras.append([])
        paras = self._merge(paras)
        origin = fitz.Point(lines[0]["spans"][0]["origin"])
        size = base[3]
        if len(lines) > 1:
            o2 = fitz.Point(lines[1]["spans"][0]["origin"])
            a = math.radians(rotate)
            line_h = abs((o2 - origin).x * math.sin(a) + (o2 - origin).y * math.cos(a))
            line_h = line_h if 0.8 * size < line_h < 3 * size else size * 1.2
        else:
            line_h = size * 1.2
        scene_rect = self.to_scene_rect(i, bbox)
        wrap = len(lines) > 1
        box, proxy, frame = self._make_editor(i, scene_rect, paras, wrap, base)
        self.editing = {
            "page": i, "box": box, "proxy": proxy, "frame": frame, "new": False,
            "family": base[0], "bold": base[1], "italic": base[2], "size": base[3],
            "color": QColor(base[4]), "origin": origin, "rotate": rotate, "base": base,
            "line_h": line_h, "base_size": size, "line_rects": [fitz.Rect(l["bbox"]) for l in lines],
            "width": max(scene_rect.width(), 40) * 1.02, "wrap": wrap,
            "min_h": scene_rect.height(), "orig_runs": paras, "style_changed": False,
            "extent": extent * 1.02,
        }
        self._match_line_spacing(box, line_h if wrap else None)
        # centred / right-aligned text (e.g. in table cells) stays that way while typing
        page_ = self.doc[i]
        align = block.get("align", "left")
        if wrap or rotate != 0 or page_.rotation != 0:
            align = "left"
        self.editing["align"] = align
        if align != "left":
            old_w = sum(fonts.font_object(st[0], st[1], st[2])[2].text_length(tx, st[3])
                        for tx, st in (paras[0] if paras else []))
            self.editing["old_w"] = old_w
            self.editing["anchor_x"] = origin.x + (old_w / 2 if align == "center" else old_w)
            fmt = QTextBlockFormat()
            fmt.setAlignment(Qt.AlignHCenter if align == "center" else Qt.AlignRight)
            cur_ = QTextCursor(box.document())
            cur_.select(QTextCursor.Document)
            cur_.mergeBlockFormat(fmt)
        self._sync_props(self.editing)
        self.fit_editor()
        self._align_editor(i)
        # hide the original text under the editor (the page shows through, like Acrobat)
        self.editing["patch"] = self._edit_patch(i, self.editing["line_rects"], rotate, bbox)
        box.setFocus()
        cur = box.textCursor()
        if click is not None:  # caret exactly where the user clicked
            local = proxy.mapFromScene(click)
            cur = box.cursorForPosition(QPointF(local).toPoint())
        else:
            cur.movePosition(QTextCursor.End)
        box.setTextCursor(cur)
        styles = {st for p in paras for _, st in p}
        mixed = " (several styles)" if len(styles) > 1 else ""
        self.set_status(f"Editing text — {base[0]} {base[3]:g} pt{mixed}. Type to change it, drag "
                        "to select. Click elsewhere to finish, Esc to cancel.")

    def open_new_text(self, i, sp):
        self.clear_overlay()
        try:
            size = max(1.0, float(self.size_box.currentText()))
        except ValueError:
            size = 12.0
        family = self.font_box.currentText()
        if family not in fonts.FAMILIES:
            family = fonts.DEFAULT_FAMILY
        bold, italic = int(self.bold_btn.isChecked()), int(self.italic_btn.isChecked())
        color = QColor(self.colors["addtext"])
        base = (family, bold, italic, size, color.name())
        box, proxy, frame = self._make_editor(i, QRectF(sp, sp), [], False, base)
        page = self.doc[i]
        self.editing = {
            "page": i, "box": box, "proxy": proxy, "frame": frame, "new": True,
            "family": family, "bold": bold, "italic": italic, "size": size, "base": base,
            "color": color, "anchor": sp, "rotate": page.rotation, "base_size": size,
            "line_h": size * 1.2, "wrap": False, "min_h": size * 1.25,
            "orig_runs": [], "style_changed": False,
        }
        self.fit_editor()
        box.setFocus()
        self.set_status("Type your text. Click outside or press Ctrl+Enter to place it, Esc to cancel.")

    def _remove_editor(self):
        ed, self.editing = self.editing, None
        if not ed:
            return None
        for item in (ed["proxy"], ed["frame"], ed.get("patch")):
            if item is not None and item.scene():
                item.scene().removeItem(item)
        return ed

    def cancel_edit(self):
        if self.editing:
            self._remove_editor()
            self.set_status("Edit cancelled.")

    @staticmethod
    def _layout_runs(paras, width):
        """Break styled paragraphs into lines no wider than `width` (None = no wrapping).
        Returns [[(text, style), ...] per line]."""
        out = []
        for runs in paras:
            pieces = []
            for text, st in runs:
                for tok in re.split(r"( +)", text):
                    if tok:
                        pieces.append((tok, st))
            line, w = [], 0.0
            for tok, st in pieces:
                _, _, f = fonts.font_object(st[0], st[1], st[2])
                tw = f.text_length(tok, st[3])
                if width and line and not tok.isspace() and w + tw > width:
                    while line and line[-1][0].isspace():
                        line.pop()
                    out.append(line)
                    line, w = [], 0.0
                if not line and tok.isspace():
                    continue  # no leading spaces on a wrapped line
                line.append((tok, st))
                w += tw
            out.append(line)
        return out

    def commit_edit(self):
        ed = self.editing
        if not ed:
            return
        paras = self._runs_from_editor(ed["box"], ed["base"])
        self._remove_editor()
        i = ed["page"]
        text = "\n".join("".join(t for t, _ in p) for p in paras)
        if ed["new"] and not text.strip():
            return
        move = ed.get("move")
        if not ed["new"] and not move and self._merge(paras) == self._merge(ed["orig_runs"]):
            return  # nothing changed
        self.checkpoint()
        page = self.doc[i]
        size0 = ed["base_size"]
        if ed["new"]:
            sp = ed["anchor"]
            st0 = paras[0][0][1] if paras and paras[0] else ed["base"]
            _, _, f0 = fonts.font_object(st0[0], st0[1], st0[2])
            asc = f0.ascender if f0.ascender > 0 else 0.8
            origin = self.to_pdf(i, QPointF(sp.x(), sp.y() + asc * st0[3]))
            lines = self._layout_runs(paras, None)
        else:
            # remove the original lines (text only; pictures and drawings stay)
            for r in ed["line_rects"]:
                page.add_redact_annot(self._line_inner(r, ed["rotate"]), fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            lines = self._layout_runs(paras, ed["extent"] if ed["wrap"] else None)
            origin = ed["origin"]
            if ed.get("align", "left") != "left" and len(lines) == 1:
                new_w = sum(fonts.font_object(st[0], st[1], st[2])[2].text_length(tx, st[3])
                            for tx, st in lines[0])
                start = ed["anchor_x"] - (new_w / 2 if ed["align"] == "center" else new_w)
                origin = fitz.Point(start, origin.y)
            if move:
                origin = fitz.Point(origin.x + move.x, origin.y + move.y)
        try:
            write_runs(page, origin, lines, ed["rotate"], ed["line_h"], size0)
        except Exception as e:
            QMessageBox.warning(self, "Text", f"Couldn't write that text.\n\n{e}")
        # re-read the page so search and copy see the corrected spaces straight away
        # (PyMuPDF only allows this when nothing else holds the page; otherwise the saved
        # file is still correct)
        try:
            self.doc.reload_page(page)
        except Exception:
            pass
        self.page_changed(i)
        self.set_status("Text added." if ed["new"] else "Text moved." if move else "Text updated.")

    # ================================================================== tabs (several PDFs)
    def _build_tab_bar(self):
        self.tabbar = QTabBar()
        self.tabbar.setObjectName("docTabs")
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDocumentMode(True)
        self.tabbar.setElideMode(Qt.ElideMiddle)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabCloseRequested.connect(self._close_tab_at)
        self.tabbar.hide()
        self._active_state = None
        self._switching = False
        return self.tabbar

    def _tab_state(self, i):
        """The state dict of tab i. (Qt copies dicts stored in tabs, so each tab stores a
        small holder object and the dict lives in it.)"""
        ref = self.tabbar.tabData(i)
        return ref.state if ref is not None else None

    def _set_tab_state(self, i, st):
        self.tabbar.setTabData(i, _TabRef(st))

    def _state(self):
        """The open document's state, so another tab can be shown and this one restored."""
        sb, hb = self.canvas.verticalScrollBar(), self.canvas.horizontalScrollBar()
        return dict(doc=self.doc, path=self.path, name=self.doc_name, password=self.password,
                    dirty=self.dirty, undo=self.undo_stack, redo=self.redo_stack,
                    zoom=self.zoom, fit_mode=self.fit_mode, scroll=(hb.value(), sb.value()),
                    orig=self.orig_bytes, orig_password=self.orig_password,
                    signed=self.signed_count, sign_warned=self.sign_warned)

    def _clear_view(self):
        self.cancel_edit()
        self.clear_overlay()
        self._hide_form_outlines()
        self.selected = None
        self.handles = []
        self.drag = None
        self.search_hits = []
        self.canvas.scene().clear()
        self.items = []
        self.form_items = []
        self.thumb_timer.stop()
        self.thumb_queue = []
        self.repair_timer.stop()
        self.banner.hide()
        self.thumbs.clear()
        self.bm_tree.clear()
        self.comments_list.clear()

    def _load_state(self, st):
        self._clear_view()
        self.doc, self.path, self.doc_name = st["doc"], st["path"], st.get("name")
        self.password, self.dirty = st["password"], st["dirty"]
        self.undo_stack, self.redo_stack = st["undo"], st["redo"]
        self.zoom, self.fit_mode = st["zoom"], st["fit_mode"]
        self.orig_bytes, self.orig_password = st.get("orig"), st.get("orig_password")
        self.signed_count, self.sign_warned = st.get("signed", 0), st.get("sign_warned", False)
        self._active_state = st
        if self.doc is None:
            self.update_ui()
            return
        self.layout_pages()
        h, v = st.get("scroll", (0, 0))
        self.canvas.horizontalScrollBar().setValue(h)
        self.canvas.verticalScrollBar().setValue(v)
        self.build_thumbs()
        self.load_bookmarks()
        self.refresh_comments()
        self._update_banner()
        if self.tool == "form":
            self._show_form_outlines()
        self.update_ui()
        self._start_repair(st)

    def _tab_changed(self, index):
        if self._switching or index < 0:
            return
        st = self._tab_state(index)
        if st is None or st is self._active_state:
            return
        if self._active_state is not None and self.doc is not None:
            self._active_state.update(self._state())
        self._load_state(st)

    def _open_in_tab(self, doc, path, password=None, dirty=False, name=None, orig=None):
        st = dict(doc=doc, path=path, name=name, password=password, dirty=dirty, undo=[],
                  redo=[], zoom=1.0, fit_mode="width", scroll=(0, 0), orig=orig,
                  orig_password=password, signed=0, sign_warned=False)
        try:
            st["signed"] = signing.count_signatures(doc)
        except Exception:
            st["signed"] = 0
        if self.doc is not None and self._active_state is not None:
            self._active_state.update(self._state())
        self._switching = True
        i = self.tabbar.addTab(icons.icon("pages"), "")
        self._set_tab_state(i, st)
        self.tabbar.setCurrentIndex(i)
        self._switching = False
        self.tabbar.show()
        self._load_state(st)
        if path:
            self.add_recent(path)

    def _replace_current(self, doc, path, password=None, orig=None):
        """Show another document in the current tab (e.g. the signed copy)."""
        old = self.doc
        st = dict(doc=doc, path=path, name=None, password=password, dirty=False, undo=[],
                  redo=[], zoom=self.zoom, fit_mode=self.fit_mode,
                  scroll=(self.canvas.horizontalScrollBar().value(),
                          self.canvas.verticalScrollBar().value()),
                  orig=orig, orig_password=password, signed=0, sign_warned=False)
        try:
            st["signed"] = signing.count_signatures(doc)
        except Exception:
            pass
        i = self.tabbar.currentIndex()
        if i < 0:
            return self._open_in_tab(doc, path, password, orig=orig)
        self._set_tab_state(i, st)
        self._load_state(st)
        if old is not None and old is not doc:
            old.close()
        if path:
            self.add_recent(path)

    def _close_tab_at(self, index):
        if index != self.tabbar.currentIndex():
            self.tabbar.setCurrentIndex(index)
        self.close_doc()

    def close_doc(self, force=False):
        """Close the PDF in the current tab (asking to save changes unless `force`)."""
        if self.doc is None:
            return True
        if not force and not self.maybe_save():
            return False
        doc = self.doc
        self._clear_view()
        self.doc = None
        doc.close()
        self._switching = True
        i = self.tabbar.currentIndex()
        if i >= 0:
            self.tabbar.removeTab(i)
        self._switching = False
        self._active_state = None
        self.path = self.doc_name = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        if self.tabbar.count():
            self._load_state(self._tab_state(self.tabbar.currentIndex()))
        else:
            self.tabbar.hide()
            self.orig_bytes = None
            self.signed_count = 0
            self.update_ui()
        return True

    def maybe_save_all(self):
        """Before quitting: ask about every tab with unsaved changes."""
        self.commit_edit()
        for k in range(self.tabbar.count()):
            st = self._tab_state(k)
            dirty = self.dirty if st is self._active_state else st.get("dirty")
            if dirty:
                self.tabbar.setCurrentIndex(k)
                if not self.maybe_save():
                    return False
        return True

    def open_documents(self):
        """Paths of all open tabs (for tests and 'already open' checks)."""
        out = []
        for k in range(self.tabbar.count()):
            st = self._tab_state(k)
            out.append(self.path if st is self._active_state else st.get("path"))
        return out

    def _update_tab_title(self):
        i = self.tabbar.currentIndex()
        if i < 0 or self.doc is None:
            return
        name = Path(self.path).name if self.path else (self.doc_name or "Untitled.pdf")
        self.tabbar.setTabText(i, name + (" •" if self.dirty else ""))
        self.tabbar.setTabToolTip(i, self.path or name)

    # ================================================================== damaged text
    def _start_repair(self, st, again=False):
        """Some PDFs show readable letters but don't say which letters they are (their
        text copies and edits as gibberish). Repair that in the background, starting with
        the pages on screen."""
        self.repair_timer.stop()
        if self.doc is None:
            return
        if not again and st.get("repair_seen"):
            if st.get("repair_left"):
                self.repair_todo = list(st["repair_left"])
                self.repair_timer.start()
            return
        st["repair_seen"] = True
        st.setdefault("repair_done", 0)
        self.repair_todo = list(range(self.doc.page_count))
        st["repair_left"] = self.repair_todo
        self.repair_timer.start()

    def _repair_page(self, i):
        st = self._active_state or {}
        try:
            rep = textrepair.repair(self.doc, [i])
        except Exception as e:  # never let a repair stop the PDF from being used
            log_error("text repair", e)
            return False
        if not rep["fonts"]:
            return False
        st["repair_done"] = st.get("repair_done", 0) + 1
        self.block_cache.pop(i, None)
        if self.tool == "edit":
            QTimer.singleShot(0, self._show_edit_outlines)
        return True

    def _repair_some(self):
        if self.doc is None or not self.repair_todo:
            self.repair_timer.stop()
            self._repair_banner(final=True)
            return
        # the pages being looked at first
        cur = self.current_page()
        i = min(self.repair_todo, key=lambda p: abs(p - cur))
        self.repair_todo.remove(i)
        if self._repair_page(i):
            self._repair_banner()
        if not self.repair_todo:
            self.repair_timer.stop()
            self._repair_banner(final=True)

    def _repair_banner(self, final=False):
        st = self._active_state or {}
        n = st.get("repair_done", 0)
        if not n:
            return
        if final and not st.get("repair_settled"):
            # letters that weren't certain: the words around them decide (whole document)
            st["repair_settled"] = True
            try:
                if textrepair.settle_unsure(self.doc):
                    self.block_cache.clear()
            except Exception as e:
                log_error("text repair (words)", e)
        if not final:
            self.show_banner("ocr", f"This PDF's text is damaged (it would copy and edit as "
                             f"gibberish). Repairing it… ({n} page{'s' if n != 1 else ''} so far)",
                             "", None)
            self.banner_btn.hide()
            return
        self.banner_btn.show()
        self.show_banner("ocr", f"Dr.PDF repaired this PDF's damaged text on {n} page"
                         f"{'s' if n != 1 else ''}: it can now be searched, copied and edited "
                         "normally. What the pages show hasn't changed. Save to keep the repair.",
                         "Save", self.save)
        self.set_status(f"Damaged text repaired on {n} page(s).")

    # ================================================================== banner
    def _build_banner(self):
        f, h = self._bar("banner")
        self.banner_icon = QLabel()
        h.addWidget(self.banner_icon)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        h.addWidget(self.banner_text, 1)
        self.banner_btn = QPushButton("")
        self.banner_btn.setObjectName("primarySmall")
        self.banner_btn.clicked.connect(lambda: self._banner_action and self._banner_action())
        h.addWidget(self.banner_btn)
        x = self._tbtn("", "close", "Hide", lambda: self.banner.hide(), text_beside=False)
        h.addWidget(x)
        self.banner = f
        self._banner_action = None
        f.hide()
        return f

    def show_banner(self, icon_name, text, button, action):
        self.banner_icon.setPixmap(icons.icon(icon_name, theme.color("accent")).pixmap(20, 20))
        self.banner_text.setText(text)
        self.banner_btn.setText(button)
        self._banner_action = action
        self.banner.show()

    def _update_banner(self):
        self.banner.hide()
        if self.doc is None:
            return
        if self.signed_count:
            n = self.signed_count
            self.show_banner("certificate", f"This PDF is digitally signed ({n} signature"
                             f"{'s' if n != 1 else ''}). Changing it will make the signature"
                             f"{'s' if n != 1 else ''} invalid.", "Check signatures",
                             self.signatures_dialog)
        elif ocr.available() and ocr.doc_needs_ocr(self.doc):
            self.show_banner("ocr", "This PDF looks like a scan, so its text can't be searched, "
                             "selected or copied yet.", "Recognize text", self.ocr_dialog)

    # ================================================================== forms
    FIELD_SIZES = {"text": (180, 20), "multiline": (240, 60), "checkbox": (12, 12),
                   "dropdown": (160, 20), "date": (110, 20), "signature": (200, 45)}

    def _hide_form_outlines(self):
        for it in getattr(self, "form_items", []):
            if it.scene():
                it.scene().removeItem(it)
        self.form_items = []

    def _show_form_outlines(self):
        self._hide_form_outlines()
        if self.doc is None:
            return
        pen = QPen(QColor(theme.color("accent")), 0)
        pen.setCosmetic(True)
        pen.setWidth(1)
        pen.setStyle(Qt.DashLine)
        for i in range(self.doc.page_count):
            page = self.doc[i]
            for w in page.widgets():
                r = self.to_scene_rect(i, w.rect)
                box = QGraphicsRectItem(r)
                box.setPen(pen)
                box.setBrush(QBrush(QColor(124, 131, 255, 28)))
                box.setZValue(8)
                self.canvas.scene().addItem(box)
                label = QGraphicsSimpleTextItem(w.field_name or "")
                label.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
                label.setBrush(QBrush(QColor(theme.color("accent"))))
                f = label.font()
                f.setPointSize(8)
                label.setFont(f)
                label.setPos(r.left(), r.top() - 14 / max(self.view_scale(), 0.1))
                label.setZValue(8)
                self.canvas.scene().addItem(label)
                self.form_items += [box, label]

    def select_widget(self, i, w):
        self.clear_overlay()
        self._show_selection(i, w.rect, True)
        self.selected = ("widget", i, w.xref, fitz.Rect(w.rect))
        self.set_status(f"Field “{w.field_name}” — drag to move, drag a handle to resize, "
                        "double-click for its name and options, Delete removes it.")

    def _find_widget(self, page, xref):
        for w in page.widgets():
            if w.xref == xref:
                w._page_ref = page
                return w
        return None

    def _finish_widget_drag(self, d):
        i = d["page"]
        r, o = d["rect"], d["orig"]
        xref = self.selected[2]
        if max(abs(r.left() - o.left()), abs(r.top() - o.top()),
               abs(r.right() - o.right()), abs(r.bottom() - o.bottom())) < 0.5:
            page = self.doc[i]
            w = self._find_widget(page, xref)
            if w is not None:
                self.select_widget(i, w)
            return
        new = self.to_pdf_rect(i, r.topLeft(), r.bottomRight())
        self.checkpoint()
        page = self.doc[i]
        w = self._find_widget(page, xref)
        if w is None:
            return
        w.rect = new
        w.update()
        self.page_changed(i)
        page = self.doc[i]
        w = self._find_widget(page, xref)
        if w is not None:
            self.select_widget(i, w)
        if self.tool == "form":
            self._show_form_outlines()

    def create_field_at(self, i, a, b=None):
        """Create a field of the chosen kind in the scene box a-b (or at a)."""
        kind = self.form_kind
        if b is None:
            w, h = self.FIELD_SIZES[kind]
            b = QPointF(a.x() + w, a.y() + h)
        r = self.to_pdf_rect(i, a, b)
        page = self.doc[i]
        words = page.get_text("words")
        label = forms._label_left(words, r) or forms._label_above(words, r) or forms.LABELS[kind]
        self.checkpoint()
        page = self.doc[i]
        name = forms.unique_name(self.doc, label)
        xref = forms.create_field(page, kind, r, name)
        self.page_changed(i)
        self._show_form_outlines()
        page = self.doc[i]
        w = self._find_widget(page, xref)
        if w is not None:
            self.select_widget(i, w)
        self.set_status(f"Added “{name}”. Double-click it to rename it or set its options.")

    def field_properties(self, i, w):
        kind = forms.kind_of(w)
        choices = [c if isinstance(c, str) else c[1] for c in (w.choice_values or [])]
        dlg = dialogs.FieldDialog(self, kind, w.field_name, w.field_label or "", choices)
        if not self._dialog_ok(dlg):
            return
        v = dlg.values()
        xref = w.xref
        self.checkpoint()
        page = self.doc[i]
        w = self._find_widget(page, xref)
        if v["name"] != w.field_name:
            v["name"] = forms.unique_name(self.doc, v["name"])
        w.field_name = v["name"]
        w.field_label = v["tooltip"] or v["name"]
        if v["choices"] is not None:
            w.choice_values = v["choices"]
            if w.field_value not in v["choices"]:
                w.field_value = v["choices"][0]
        w.update()
        self.page_changed(i)
        self._show_form_outlines()

    @needs_doc
    def detect_fields(self):
        if self.doc is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Detect form fields")
        box.setText("Find blanks, answer lines and checkboxes and turn them into fillable "
                    "fields?")
        this = box.addButton(f"This page ({self.current_page() + 1})", QMessageBox.AcceptRole)
        allp = box.addButton("All pages", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        c = box.clickedButton()
        if c is this:
            pages = [self.current_page()]
        elif c is allp:
            pages = list(range(self.doc.page_count))
        else:
            return
        self.commit_edit()
        self.checkpoint()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            scanned = [i for i in pages if ocr.page_needs_ocr(self.doc[i])]
            if scanned and ocr.available():
                # labels on scanned forms come from recognized text
                for i in scanned:
                    page = self.doc[i]
                    ocr.add_text_layer(page, ocr.recognize(page))
            n = forms.add_detected(self.doc, pages)
        finally:
            QApplication.restoreOverrideCursor()
        if not n:
            self._drop_checkpoint()
            self.layout_pages(keep_view=True)
            QMessageBox.information(self, "Detect form fields",
                                    "No blanks, answer lines or checkboxes were found. You can "
                                    "add fields yourself with Form → Text field, Checkbox, …")
            return
        self._doc_changed_all(f"Added {n} form field(s).")
        self.set_tool("form:text")
        QMessageBox.information(self, "Detect form fields",
                                f"Added {n} fillable field(s). Check them: drag to move or "
                                "resize, double-click to rename, Delete to remove.")

    # ================================================================== digital signatures
    def _id_paths(self):
        paths = [str(p) for p in signing.known_ids()]
        for p in self.settings.value("digital_ids", []) or []:
            if p and Path(p).exists() and p not in paths:
                paths.append(p)
        return paths

    @needs_doc
    def digital_sign(self, i=None, rect=None, field=None):
        if self.doc is None:
            return
        self.commit_edit()
        dlg = dialogs.DigitalSignDialog(self, self._id_paths())
        if not self._dialog_ok(dlg):
            return
        v = dlg.values()
        ids = list(self.settings.value("digital_ids", []) or [])
        if v["pfx"] not in ids:
            self.settings.setValue("digital_ids", [v["pfx"]] + ids[:9])
        start = self.path or str(Path.home() / "Documents" / (self.doc_name or "Document.pdf"))
        default = str(Path(start).with_name(Path(start).stem + " (signed).pdf"))
        out, _ = QFileDialog.getSaveFileName(self, "Save the signed PDF as", default,
                                             "PDF files (*.pdf)")
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data = self.orig_bytes if (not self.dirty and self.orig_bytes is not None and
                                       self.password == self.orig_password) else self._final_bytes()
            kw = dict(reason=v["reason"], location=v["location"], doc_password=self.password)
            if field:
                kw["field_name"] = field
            else:
                kw.update(page_index=i, rect=rect, page_matrix=self.doc[i].transformation_matrix)
            signed = signing.sign(data, out, v["pfx"], v["password"], **kw)
            new = fitz.open("pdf", signed)
            if new.needs_pass:
                new.authenticate(self.password)
                new = fitz.open("pdf", new.tobytes(encryption=fitz.PDF_ENCRYPT_NONE))
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Sign", f"The document couldn't be signed.\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._replace_current(new, out, self.password, orig=signed)
        self.set_tool("select")
        self.set_status(f"Signed by {dlg.signer} and saved as {Path(out).name}.")

    @needs_doc
    def signatures_dialog(self):
        if self.doc is None:
            return
        data = self.orig_bytes if self.orig_bytes is not None else self._final_bytes()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = signing.signatures(data, self.orig_password or self.password)
        except Exception as e:
            res = []
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Signatures", f"The signatures couldn't be checked.\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        if self.dirty and res:
            for r in res:
                r["summary"] += " (Checked against the last saved version.)"
        dialogs.SignaturesDialog(self, res).exec()

    def create_id_dialog(self):
        dlg = dialogs.CreateIdDialog(self)
        if self._dialog_ok(dlg) and dlg.created:
            QMessageBox.information(self, "Digital ID",
                                    f"Your digital ID was saved as:\n{dlg.created}\n\nKeep the "
                                    "password safe: it's needed every time you sign.")

    # ================================================================== find & redact, sanitize
    @needs_doc
    def find_redact_dialog(self):
        if self.doc is None:
            return
        self.commit_edit()
        dlg = dialogs.FindRedactDialog(self, self)
        ok = dlg.exec() == QDialog.Accepted
        chosen = dlg.selected() if ok else []
        self.clear_overlay()
        if not chosen:
            return
        self.checkpoint()
        n = redact.redact(self.doc, chosen)
        self._doc_changed_all(f"Redacted {n} item(s). They are removed permanently when you save.")

    def preview_redactions(self, matches):
        self.clear_overlay()
        pen = QPen(QColor(230, 40, 60), 0)
        pen.setCosmetic(True)
        pen.setWidth(2)
        for m in matches:
            for r in m.rects:
                if m.page < len(self.items):
                    self.add_overlay_rect(self.to_scene_rect(m.page, r).adjusted(-1, -1, 1, 1),
                                          pen, QBrush(QColor(230, 40, 60, 40)))

    def goto_match(self, m):
        self.goto_page(m.page)
        if m.rects:
            self.canvas.centerOn(self.to_scene_rect(m.page, m.rects[0]).center())

    @needs_doc
    def sanitize_dialog(self):
        if self.doc is None:
            return
        dlg = dialogs.SanitizeDialog(self)
        if not self._dialog_ok(dlg):
            return
        self.checkpoint()
        done = redact.sanitize(self.doc, dlg.values())
        self._after_pages("Hidden information removed.")
        QMessageBox.information(self, "Remove hidden information",
                                ("Removed: " + ", ".join(done) + "." if done else
                                 "Nothing to remove was found.") +
                                "\n\nSave the file to keep the cleaned version.")

    # ================================================================== compare
    def compare_dialog(self):
        cur = self.path if (self.doc is not None and self.path and not self.dirty) else ""
        dlg = dialogs.CompareDialog(self, cur)
        if not self._dialog_ok(dlg):
            return
        old, new = dlg.values()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report, nr, na, pages = compare.report(old, new)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Compare", f"These files couldn't be compared.\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._open_in_tab(report, None, dirty=False,
                          name=f"Comparison - {Path(new).stem}.pdf")
        self.set_status("No differences found." if not nr and not na else
                        f"{nr} word(s) removed, {na} added, on {len(pages)} page(s).")

    # ================================================================== batch
    def batch_dialog(self):
        dlg = dialogs.BatchDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        prog = QProgressDialog("Processing…", "Stop", 0, len(v["files"]), self)
        prog.setWindowTitle("Batch process")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        worker = BatchWorker(v)
        worker.progress.connect(lambda k, n, name: (prog.setValue(k),
                                                    prog.setLabelText(f"{name}  ({k + 1} of {n})")))
        prog.canceled.connect(worker.requestInterruption)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()
        prog.close()
        res = worker.results
        ok = [r for r in res if r[1]]
        bad = [r for r in res if not r[1]]
        text = f"{len(ok)} of {len(v['files'])} file(s) processed."
        if bad:
            text += "\n\nNot processed:\n" + "\n".join(f"• {f.name}: {msg}" for f, _, msg, _ in bad[:12])
        folder = v["out_dir"] or (str(ok[0][3].parent) if ok else "")
        box = QMessageBox(self)
        box.setWindowTitle("Batch process")
        box.setText(text)
        opener = box.addButton("Open folder", QMessageBox.AcceptRole) if folder and ok else None
        box.addButton(QMessageBox.Ok)
        box.exec()
        if opener is not None and box.clickedButton() is opener:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        self.set_status(text.splitlines()[0])

    # ================================================================== scan
    def scan_dialog(self):
        devs = scanner.devices()
        if not devs:
            box = QMessageBox(self)
            box.setWindowTitle("Scan")
            box.setText("No scanner was found.")
            box.setInformativeText("Make sure the scanner is plugged in, switched on and its "
                                   "Windows driver is installed. You can also try the standard "
                                   "Windows scanning window.")
            win = box.addButton("Use the Windows scan window", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is win:
                try:
                    images = scanner.scan_with_windows_dialog()
                except Exception as e:
                    QMessageBox.warning(self, "Scan", f"Scanning didn't work.\n\n{e}")
                    return
                if images:
                    self._scanned(scanner.images_to_pdf(images).tobytes(), len(images), "new",
                                  ocr.available())
            return
        dlg = dialogs.ScanDialog(self, devs, ocr.available(), self.doc is not None)
        if not self._dialog_ok(dlg):
            return
        v = dlg.values()
        prog = QProgressDialog("Scanning…", "Stop", 0, 0, self)
        prog.setWindowTitle("Scan")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        worker = ScanWorker(v)
        worker.progress.connect(lambda n: prog.setLabelText(f"Scanning page {n + 1}…"))
        prog.canceled.connect(worker.requestInterruption)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()
        prog.close()
        if worker.error:
            QMessageBox.warning(self, "Scan", worker.error)
            return
        if worker.data:
            self._scanned(worker.data, worker.pages, v["target"], False)

    def _scanned(self, data, pages, target, run_ocr):
        new = fitz.open("pdf", data)
        if target == "append" and self.doc is not None:
            self.checkpoint()
            at = self.doc.page_count
            self.doc.insert_pdf(new)
            self._after_pages(f"Added {pages} scanned page(s).")
            self.goto_page(at)
        else:
            import datetime
            self._open_in_tab(new, None, dirty=True,
                              name=f"Scan {datetime.datetime.now():%Y-%m-%d %H%M}.pdf")
            self.set_status(f"Scanned {pages} page(s). Use Save to keep them.")
        if run_ocr and ocr.available():
            self.run_ocr(list(range(self.doc.page_count)), skip_text=True)

    # ------------------------------------------------------------------ search
    @needs_doc
    def show_search(self):
        if self.doc is None:
            return
        self.search_bar.show()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def hide_search(self):
        self.search_bar.hide()
        self.search_hits = []
        self.clear_overlay()

    @needs_doc
    def search_next(self, step=1):
        q = self.search_edit.text().strip()
        if not q or self.doc is None:
            return
        if not self.search_hits:
            self.search_hits = [(i, r) for i, page in enumerate(self.doc)
                                for r in page.search_for(q)]
            self.search_pos = -1 if step > 0 else 0
            if not self.search_hits:
                self.search_info.setText("No matches")
                return
        self.search_pos = (self.search_pos + step) % len(self.search_hits)
        i, r = self.search_hits[self.search_pos]
        self.clear_overlay()
        sr = self.to_scene_rect(i, r)
        self.add_overlay_rect(sr.adjusted(-1, -1, 1, 1), brush=QBrush(QColor(255, 170, 0, 120)))
        self.canvas.centerOn(sr.center())
        self.search_info.setText(f"{self.search_pos + 1} of {len(self.search_hits)}")


def QTransformScale(s):
    return QTransform.fromScale(s, s)
