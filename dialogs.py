"""Dialogs for the Acrobat-style document tools."""
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QCheckBox, QPushButton, QColorDialog, QRadioButton, QButtonGroup,
    QFileDialog, QSlider, QWidget, QGridLayout, QFrame, QListWidget, QListWidgetItem,
    QTextEdit,
)

import fonts
import pdftools
from version import APP_NAME, VERSION


class ToolDialog(QDialog):
    """A titled dialog with a form and OK/Cancel; subclasses add rows."""

    def __init__(self, parent, title, subtitle="", ok_text="Apply"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 18)
        self.lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("dlgTitle")
        self.lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("subtitle")
            sub.setWordWrap(True)
            self.lay.addWidget(sub)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lay.addLayout(self.form)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        self.lay.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(ok_text)
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("primarySmall")
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        self.lay.addSpacing(4)
        self.lay.addWidget(self.buttons)

    def fail(self, msg):
        self.error.setText(msg)
        self.error.show()

    def validate(self):
        return True

    def _ok(self):
        try:
            if self.validate():
                self.accept()
        except ValueError as e:
            self.fail(str(e))


class ColorButton(QPushButton):
    def __init__(self, color):
        super().__init__()
        self.setFixedSize(46, 28)
        self.setObjectName("swatch")
        self.set_color(QColor(color))
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.color = c
        self.setStyleSheet(f"QPushButton#swatch {{ background: {c.name()}; "
                           "border: 2px solid #8A90A2; border-radius: 7px; }}")

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, "Colour")
        if c.isValid():
            self.set_color(c)

    def rgb(self):
        return (self.color.redF(), self.color.greenF(), self.color.blueF())


def font_combo(default="Arial"):
    box = QComboBox()
    box.setEditable(True)
    box.addItems(fonts.FAMILY_NAMES)
    box.setCurrentText(default if default in fonts.FAMILIES else fonts.DEFAULT_FAMILY)
    return box


class PagesField(QWidget):
    """'All pages' or a range like 1-3, 5."""

    def __init__(self, count, current=0):
        super().__init__()
        self.count = count
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["All pages", "Current page", "Pages…"])
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(f"e.g. 1-3, 5 (of {count})")
        self.edit.setEnabled(False)
        self.current = current
        self.mode.currentIndexChanged.connect(lambda i: self.edit.setEnabled(i == 2))
        h.addWidget(self.mode)
        h.addWidget(self.edit, 1)

    def pages(self):
        i = self.mode.currentIndex()
        if i == 0:
            return list(range(self.count))
        if i == 1:
            return [self.current]
        if not self.edit.text().strip():
            raise ValueError("Type the pages to use, for example 1-3, 5.")
        return pdftools.parse_pages(self.edit.text(), self.count)


class HeaderFooterDialog(ToolDialog):
    def __init__(self, parent, count, current, filename):
        super().__init__(parent, "Page numbers & header/footer",
                         "Adds text to the top or bottom of pages. Use {page}, {total}, "
                         "{date} and {file} as placeholders.", "Add")
        self.text = QComboBox()
        self.text.setEditable(True)
        self.text.addItems(["Page {page} of {total}", "{page}", "Page {page}",
                            "{file} — {date}", "Confidential"])
        self.pos = QComboBox()
        self.pos.addItems(pdftools.POSITIONS)
        self.pos.setCurrentText("Bottom center")
        self.font = font_combo("Arial")
        self.size = QSpinBox()
        self.size.setRange(5, 72)
        self.size.setValue(10)
        self.size.setSuffix(" pt")
        self.color = ColorButton("#000000")
        self.margin = QSpinBox()
        self.margin.setRange(0, 200)
        self.margin.setValue(36)
        self.margin.setSuffix(" pt")
        self.margin.setToolTip("Distance from the page edge (72 pt = 1 inch)")
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(1)
        self.pages = PagesField(count, current)
        style = QHBoxLayout()
        style.addWidget(self.font, 1)
        style.addWidget(self.size)
        style.addWidget(self.color)
        self.form.addRow("Text", self.text)
        self.form.addRow("Position", self.pos)
        self.form.addRow("Font", style)
        self.form.addRow("Margin", self.margin)
        self.form.addRow("Start numbering at", self.start)
        self.form.addRow("Apply to", self.pages)
        self.filename = filename

    def validate(self):
        if not self.text.currentText().strip():
            raise ValueError("Type the text to add.")
        self.result_pages = self.pages.pages()
        return True

    def values(self):
        return dict(template=self.text.currentText(), position=self.pos.currentText(),
                    family=self.font.currentText(), size=self.size.value(),
                    color=self.color.rgb(), margin=self.margin.value(),
                    start=self.start.value(), pages=self.result_pages, filename=self.filename)


class WatermarkDialog(ToolDialog):
    def __init__(self, parent, count, current):
        super().__init__(parent, "Watermark", "Puts see-through text across your pages.", "Add")
        self.text = QComboBox()
        self.text.setEditable(True)
        self.text.addItems(["CONFIDENTIAL", "DRAFT", "COPY", "SAMPLE", "DO NOT COPY", "VOID"])
        self.font = font_combo("Arial")
        self.size = QSpinBox()
        self.size.setRange(0, 400)
        self.size.setValue(0)
        self.size.setSpecialValueText("Fit page")
        self.size.setSuffix(" pt")
        self.color = ColorButton("#9AA0A6")
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(30)
        self.op_lbl = QLabel("30%")
        self.opacity.valueChanged.connect(lambda v: self.op_lbl.setText(f"{v}%"))
        self.angle = QComboBox()
        self.angle.addItems(["Diagonal (45°)", "Horizontal", "Vertical"])
        self.behind = QCheckBox("Place behind the page content")
        self.pages = PagesField(count, current)
        style = QHBoxLayout()
        style.addWidget(self.font, 1)
        style.addWidget(self.size)
        style.addWidget(self.color)
        op = QHBoxLayout()
        op.addWidget(self.opacity, 1)
        op.addWidget(self.op_lbl)
        self.form.addRow("Text", self.text)
        self.form.addRow("Font", style)
        self.form.addRow("Opacity", op)
        self.form.addRow("Direction", self.angle)
        self.form.addRow("", self.behind)
        self.form.addRow("Apply to", self.pages)

    def validate(self):
        if not self.text.currentText().strip():
            raise ValueError("Type the watermark text.")
        self.result_pages = self.pages.pages()
        return True

    def values(self):
        return dict(text=self.text.currentText().strip(), family=self.font.currentText(),
                    size=self.size.value(), color=self.color.rgb(),
                    opacity=self.opacity.value() / 100,
                    angle={0: 45, 1: 0, 2: 90}[self.angle.currentIndex()],
                    behind=self.behind.isChecked(), pages=self.result_pages)


class SplitDialog(ToolDialog):
    def __init__(self, parent, count, has_bookmarks, folder, stem):
        super().__init__(parent, "Split PDF",
                         f"Saves parts of this {count}-page PDF as separate files. "
                         "The open document isn't changed.", "Split")
        self.group = QButtonGroup(self)
        self.every_rb = QRadioButton("Every")
        self.every = QSpinBox()
        self.every.setRange(1, max(1, count))
        self.every.setValue(1)
        self.every.setSuffix(" page(s)")
        self.ranges_rb = QRadioButton("Page ranges")
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("e.g. 1-3, 4-10, 11-   (each range becomes a file)")
        self.bm_rb = QRadioButton("At each top-level bookmark")
        self.bm_rb.setEnabled(has_bookmarks)
        if not has_bookmarks:
            self.bm_rb.setToolTip("This PDF has no bookmarks")
        for b in (self.every_rb, self.ranges_rb, self.bm_rb):
            self.group.addButton(b)
        self.every_rb.setChecked(True)
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        g.addWidget(self.every_rb, 0, 0)
        g.addWidget(self.every, 0, 1)
        g.addWidget(self.ranges_rb, 1, 0)
        g.addWidget(self.ranges, 1, 1)
        g.addWidget(self.bm_rb, 2, 0, 1, 2)
        self.ranges.textEdited.connect(lambda: self.ranges_rb.setChecked(True))
        self.every.valueChanged.connect(lambda: self.every_rb.setChecked(True))
        g.setColumnStretch(1, 1)
        self.form.addRow("Split", box)
        self.name = QLineEdit(stem)
        self.form.addRow("File names start with", self.name)
        self.folder = QLineEdit(folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        fl = QHBoxLayout()
        fl.addWidget(self.folder, 1)
        fl.addWidget(browse)
        self.form.addRow("Save to", fl)
        self.count = count

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save the parts to", self.folder.text())
        if d:
            self.folder.setText(d)

    def validate(self):
        if not self.folder.text().strip():
            raise ValueError("Choose a folder to save the parts to.")
        if self.ranges_rb.isChecked():
            if not self.ranges.text().strip():
                raise ValueError("Type the page ranges, for example 1-3, 4-10.")
            for part in self.ranges.text().split(","):
                if part.strip():
                    pdftools.parse_pages(part, self.count)
        if not self.name.text().strip():
            raise ValueError("Type a file name.")
        return True

    def values(self):
        mode = "every" if self.every_rb.isChecked() else \
            "ranges" if self.ranges_rb.isChecked() else "bookmarks"
        return dict(mode=mode, every=self.every.value(), ranges=self.ranges.text(),
                    out_dir=self.folder.text().strip(), stem=self.name.text().strip())


class OcrDialog(ToolDialog):
    def __init__(self, parent, count, current, languages):
        super().__init__(parent, "Recognize text (OCR)",
                         "Reads the text in scanned pages so it can be searched, selected, "
                         "copied and converted. The pages look exactly the same afterwards. "
                         "Runs on this computer — nothing is uploaded.", "Recognize")
        self.pages = PagesField(count, current)
        self.lang = QComboBox()
        for tag, name in languages:
            self.lang.addItem(name, tag)
        self.skip = QCheckBox("Skip pages that already have text")
        self.skip.setChecked(True)
        self.form.addRow("Pages", self.pages)
        self.form.addRow("Language", self.lang)
        self.form.addRow("", self.skip)

    def validate(self):
        self.result_pages = self.pages.pages()
        return True

    def values(self):
        return dict(pages=self.result_pages, lang=self.lang.currentData(),
                    skip_text=self.skip.isChecked())


class NewPdfDialog(ToolDialog):
    def __init__(self, parent, sizes):
        super().__init__(parent, "New blank PDF", "Creates a new PDF with empty pages.", "Create")
        self.size = QComboBox()
        self.size.addItems(sizes)
        self.orient = QComboBox()
        self.orient.addItems(["Portrait", "Landscape"])
        self.pages = QSpinBox()
        self.pages.setRange(1, 500)
        self.pages.setValue(1)
        self.form.addRow("Page size", self.size)
        self.form.addRow("Orientation", self.orient)
        self.form.addRow("Pages", self.pages)

    def values(self):
        return dict(size=self.size.currentText(), landscape=self.orient.currentIndex() == 1,
                    pages=self.pages.value())


class PropertiesDialog(ToolDialog):
    def __init__(self, parent, doc, path, password):
        super().__init__(parent, "Document properties", "", "Save")
        meta = doc.metadata or {}
        self.title = QLineEdit(meta.get("title", ""))
        self.author = QLineEdit(meta.get("author", ""))
        self.subject = QLineEdit(meta.get("subject", ""))
        self.keywords = QLineEdit(meta.get("keywords", ""))
        for label, w in (("Title", self.title), ("Author", self.author),
                         ("Subject", self.subject), ("Keywords", self.keywords)):
            self.form.addRow(label, w)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("sep")
        self.form.addRow(line)
        r = doc[0].rect if doc.page_count else None
        size = ""
        if r:
            size = f'{r.width / 72:.2f} × {r.height / 72:.2f} in  ({_paper_name(r)})'
        fsize = ""
        if path and Path(path).exists():
            n = Path(path).stat().st_size
            fsize = f"{n / 1048576:.1f} MB" if n > 1048576 else f"{n / 1024:.0f} KB"
        info = [("File", Path(path).name if path else "Not saved yet"),
                ("Location", str(Path(path).parent) if path else "—"),
                ("File size", fsize or "—"), ("Pages", str(doc.page_count)),
                ("Page size", size or "—"), ("PDF version", meta.get("format", "—")),
                ("Created by", meta.get("creator", "") or meta.get("producer", "") or "—"),
                ("Password", "Protected" if password else "None")]
        for label, value in info:
            v = QLabel(value)
            v.setObjectName("subtitle")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setWordWrap(True)
            self.form.addRow(label, v)

    def values(self):
        return dict(title=self.title.text(), author=self.author.text(),
                    subject=self.subject.text(), keywords=self.keywords.text())


def _paper_name(r):
    w, h = sorted((round(r.width), round(r.height)))
    for name, (a, b) in (("Letter", (612, 792)), ("Legal", (612, 1008)), ("A4", (595, 842)),
                         ("Tabloid", (792, 1224)), ("A3", (842, 1191)), ("A5", (420, 595))):
        if abs(w - a) <= 2 and abs(h - b) <= 2:
            return name + (" landscape" if r.width > r.height else "")
    return "custom"


SHORTCUTS = [
    ("Ctrl+O", "Open a PDF"), ("Ctrl+S", "Save"), ("Ctrl+Shift+S", "Save as"),
    ("Ctrl+P", "Print"), ("Ctrl+F", "Find text"), ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
    ("Ctrl+C", "Copy selected text"), ("Delete", "Delete the selected item"),
    ("V", "Select tool"), ("E", "Edit text tool"), ("T", "Add text tool"),
    ("H", "Highlight tool"), ("N", "Sticky note"), ("Esc", "Back to Select / cancel"),
    ("Ctrl+0 / 1 / 2", "Fit page / actual size / fit width"),
    ("Ctrl + mouse wheel", "Zoom"), ("Home / End", "First / last page"),
    ("Page Up / Page Down", "Previous / next screen"),
]


class AboutDialog(QDialog):
    def __init__(self, parent, update_folder="", locked=False, check=None):
        super().__init__(parent)
        self.check = check
        self.setWindowTitle(f"About {APP_NAME}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        t = QLabel(f"<span style='font-size:22px;font-weight:700'>Dr.</span>"
                   f"<span style='font-size:22px;font-weight:700;color:#7C83FF'>PDF</span>"
                   f"&nbsp;&nbsp;<span style='color:#8A90A2'>version {VERSION}</span>")
        lay.addWidget(t)
        d = QLabel("Edit, sign, organize and convert PDFs and office files. Everything runs "
                   "on this computer — no files are ever uploaded.")
        d.setObjectName("subtitle")
        d.setWordWrap(True)
        lay.addWidget(d)
        h = QLabel("KEYBOARD SHORTCUTS")
        h.setObjectName("section")
        lay.addSpacing(6)
        lay.addWidget(h)
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(5)
        for r, (k, v) in enumerate(SHORTCUTS):
            kl = QLabel(k)
            kl.setObjectName("kbd")
            g.addWidget(kl, r // 2, (r % 2) * 2, Qt.AlignLeft)
            vl = QLabel(v)
            g.addWidget(vl, r // 2, (r % 2) * 2 + 1)
        lay.addLayout(g)
        if check is not None:
            h2 = QLabel("UPDATES")
            h2.setObjectName("section")
            lay.addSpacing(6)
            lay.addWidget(h2)
            u = QLabel("Dr.PDF can offer new versions that your IT team places in a shared "
                       "folder (nothing is downloaded from the internet).")
            u.setObjectName("subtitle")
            u.setWordWrap(True)
            lay.addWidget(u)
            row = QHBoxLayout()
            self.folder = QLineEdit(update_folder)
            self.folder.setPlaceholderText(r"e.g. \\server\software\Dr.PDF")
            self.folder.setReadOnly(locked)
            browse = QPushButton("Browse…")
            browse.setEnabled(not locked)
            browse.clicked.connect(self._browse)
            now = QPushButton("Check now")
            now.clicked.connect(self._check_now)
            row.addWidget(self.folder, 1)
            row.addWidget(browse)
            row.addWidget(now)
            lay.addLayout(row)
            self.result = QLabel("Set by your IT department." if locked else "")
            self.result.setObjectName("subtitle")
            self.result.setWordWrap(True)
            lay.addWidget(self.result)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addSpacing(6)
        lay.addWidget(bb)


# ====================================================================== find & redact
def _about_browse(self):
    d = QFileDialog.getExistingDirectory(self, "Folder with new Dr.PDF versions",
                                         self.folder.text())
    if d:
        self.folder.setText(d)


def _about_check(self):
    self.result.setText(self.check(self.folder.text().strip()))


AboutDialog._browse = _about_browse
AboutDialog._check_now = _about_check


class FindRedactDialog(QDialog):
    """Search for text and patterns, review the matches, then redact the ticked ones."""

    def __init__(self, parent, editor):
        super().__init__(parent)
        import redact
        self.redact = redact
        self.editor = editor
        self.matches = []
        self.setWindowTitle("Find & redact")
        self.setMinimumSize(560, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Find & redact")
        head.setObjectName("dlgTitle")
        lay.addWidget(head)
        sub = QLabel("Find words or sensitive information, check the results, then black them "
                     "out permanently. Scanned pages are searched too once text is recognized.")
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        row = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Words or a name to find (optional)")
        self.text.returnPressed.connect(self.find)
        row.addWidget(self.text, 1)
        self.whole = QCheckBox("Whole words")
        self.case = QCheckBox("Match case")
        row.addWidget(self.whole)
        row.addWidget(self.case)
        lay.addLayout(row)
        grid = QGridLayout()
        self.pattern_boxes = {}
        for k, (key, (label, _, _)) in enumerate(redact.PATTERNS.items()):
            cb = QCheckBox(label.replace("&", "&&"))  # a single & would be read as a shortcut key
            self.pattern_boxes[key] = cb
            grid.addWidget(cb, k // 2, k % 2)
        lay.addLayout(grid)
        frow = QHBoxLayout()
        fb = QPushButton("Find")
        fb.setObjectName("primarySmall")
        fb.clicked.connect(self.find)
        frow.addWidget(fb)
        self.count = QLabel("")
        self.count.setObjectName("subtitle")
        frow.addWidget(self.count, 1)
        self.all_btn = QPushButton("Select all")
        self.none_btn = QPushButton("Select none")
        self.all_btn.clicked.connect(lambda: self._check_all(True))
        self.none_btn.clicked.connect(lambda: self._check_all(False))
        frow.addWidget(self.all_btn)
        frow.addWidget(self.none_btn)
        lay.addLayout(frow)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._show)
        self.list.itemChanged.connect(lambda *_: self._update_buttons())
        lay.addWidget(self.list, 1)
        hint = QLabel("Double-click a result to see it on the page.")
        hint.setObjectName("subtitle")
        lay.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Redact selected")
        self.ok.setObjectName("primarySmall")
        self.ok.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def find(self):
        pats = [k for k, cb in self.pattern_boxes.items() if cb.isChecked()]
        if not self.text.text().strip() and not pats:
            self.count.setText("Type something to find or tick a kind of information.")
            return
        self.matches = self.redact.find(self.editor.doc, self.text.text(), pats,
                                        self.case.isChecked(), self.whole.isChecked())
        self.list.blockSignals(True)
        self.list.clear()
        names = dict((k, v[0]) for k, v in self.redact.PATTERNS.items())
        for k, m in enumerate(self.matches):
            kind = names.get(m.kind, "Text")
            it = QListWidgetItem(f"Page {m.page + 1}   ·   {m.text}   ({kind})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        self.list.blockSignals(False)
        n = len(self.matches)
        self.count.setText(f"{n} result(s) on {len({m.page for m in self.matches})} page(s)"
                           if n else "Nothing found.")
        self.editor.preview_redactions(self.matches)
        self._update_buttons()

    def _check_all(self, on):
        for k in range(self.list.count()):
            self.list.item(k).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _update_buttons(self):
        n = len(self.selected())
        self.ok.setEnabled(n > 0)
        self.ok.setText(f"Redact {n} selected" if n else "Redact selected")

    def _show(self, it):
        m = self.matches[it.data(Qt.UserRole)]
        self.editor.goto_match(m)

    def selected(self):
        return [self.matches[self.list.item(k).data(Qt.UserRole)]
                for k in range(self.list.count())
                if self.list.item(k).checkState() == Qt.Checked]


class SanitizeDialog(ToolDialog):
    def __init__(self, parent):
        import redact
        super().__init__(parent, "Remove hidden information",
                         "Cleans the document before you share it. Choose what to remove:",
                         "Remove")
        self.boxes = {}
        for key, label, default in redact.SANITIZE_OPTIONS:
            cb = QCheckBox(label.replace("&", "&&"))  # a single & would be read as a shortcut key
            cb.setChecked(default)
            self.boxes[key] = cb
            self.form.addRow("", cb)

    def validate(self):
        if not self.values():
            raise ValueError("Tick at least one thing to remove.")
        return True

    def values(self):
        return {k for k, cb in self.boxes.items() if cb.isChecked()}


# ====================================================================== compare
class FilePicker(QWidget):
    def __init__(self, text="", title="Choose a PDF"):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(text)
        b = QPushButton("Browse…")
        b.clicked.connect(lambda: self._browse(title))
        h.addWidget(self.edit, 1)
        h.addWidget(b)

    def _browse(self, title):
        p, _ = QFileDialog.getOpenFileName(self, title, self.edit.text(), "PDF files (*.pdf)")
        if p:
            self.edit.setText(p)

    def path(self):
        return self.edit.text().strip()


class CompareDialog(ToolDialog):
    def __init__(self, parent, current_path=""):
        super().__init__(parent, "Compare files",
                         "Shows what changed between two versions of a PDF: removed text in "
                         "red, added text in green, page by page.", "Compare")
        self.old = FilePicker(current_path or "", "Choose the older version")
        self.new = FilePicker("", "Choose the newer version")
        swap = QPushButton("Swap")
        swap.clicked.connect(self._swap)
        self.form.addRow("Older version", self.old)
        self.form.addRow("Newer version", self.new)
        self.form.addRow("", swap)

    def _swap(self):
        a, b = self.old.path(), self.new.path()
        self.old.edit.setText(b)
        self.new.edit.setText(a)

    def validate(self):
        for label, p in (("older", self.old.path()), ("newer", self.new.path())):
            if not p or not Path(p).is_file():
                raise ValueError(f"Choose the {label} version.")
        if Path(self.old.path()).resolve() == Path(self.new.path()).resolve():
            raise ValueError("Choose two different files.")
        return True

    def values(self):
        return self.old.path(), self.new.path()


# ====================================================================== batch
class BatchDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        import batch
        import redact
        self.batch = batch
        self.setWindowTitle("Batch process PDFs")
        self.setMinimumSize(640, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Batch process PDFs")
        head.setObjectName("dlgTitle")
        lay.addWidget(head)
        sub = QLabel("Apply the same steps to many PDFs at once. The originals are not changed; "
                     "new copies are saved.")
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        frow = QHBoxLayout()
        af = QPushButton("Add files…")
        ad = QPushButton("Add folder…")
        cl = QPushButton("Clear")
        af.clicked.connect(self._add_files)
        ad.clicked.connect(self._add_folder)
        cl.clicked.connect(lambda: (self.files.clear(), self._refresh()))
        for b in (af, ad, cl):
            frow.addWidget(b)
        frow.addStretch()
        self.count = QLabel("No files yet")
        self.count.setObjectName("subtitle")
        frow.addWidget(self.count)
        lay.addLayout(frow)
        self.files_list = QListWidget()
        self.files_list.setMaximumHeight(130)
        lay.addWidget(self.files_list)
        self.files = []
        sec = QLabel("STEPS")
        sec.setObjectName("section")
        lay.addWidget(sec)
        self.checks = {}
        g = QGridLayout()
        g.setVerticalSpacing(6)
        row = 0
        self.redact_patterns = {}
        for key, label in batch.ACTIONS:
            cb = QCheckBox(label.replace("&", "&&"))  # a single & would be read as a shortcut key
            self.checks[key] = cb
            g.addWidget(cb, row, 0)
            if key == "redact":
                box = QWidget()
                h = QHBoxLayout(box)
                h.setContentsMargins(0, 0, 0, 0)
                for pk, short in (("ssn", "SSN"), ("phone", "Phone"), ("email", "Email"),
                                  ("card", "Card")):
                    pb = QCheckBox(short)
                    pb.setToolTip(redact.PATTERNS[pk][0])
                    pb.setChecked(pk == "ssn")
                    self.redact_patterns[pk] = pb
                    h.addWidget(pb)
                g.addWidget(box, row, 1)
            elif key == "page_numbers":
                self.pn_text = QLineEdit("Page {page} of {total}")
                g.addWidget(self.pn_text, row, 1)
            elif key == "watermark":
                self.wm_text = QLineEdit("CONFIDENTIAL")
                g.addWidget(self.wm_text, row, 1)
            elif key == "password":
                self.pw = QLineEdit()
                self.pw.setEchoMode(QLineEdit.Password)
                self.pw.setPlaceholderText("Password for the new copies")
                g.addWidget(self.pw, row, 1)
            row += 1
        g.setColumnStretch(1, 1)
        lay.addLayout(g)
        sec2 = QLabel("SAVE")
        sec2.setObjectName("section")
        lay.addWidget(sec2)
        form = QFormLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Same folder as each file")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_out)
        fr = QHBoxLayout()
        fr.addWidget(self.folder, 1)
        fr.addWidget(browse)
        form.addRow("Save to", fr)
        self.suffix = QLineEdit(" (processed)")
        form.addRow("Add to file names", self.suffix)
        lay.addLayout(form)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Start")
        bb.button(QDialogButtonBox.Ok).setObjectName("primarySmall")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self, paths):
        known = set(self.files)
        for f in self.batch.pdf_files(paths):
            if f not in known:
                self.files.append(f)
                known.add(f)
        self._refresh()

    def _refresh(self):
        self.files_list.clear()
        for f in self.files:
            self.files_list.addItem(str(f))
        self.count.setText(f"{len(self.files)} PDF(s)" if self.files else "No files yet")

    def _add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs", "", "PDF files (*.pdf)")
        if ps:
            self._add(ps)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder of PDFs")
        if d:
            self._add([d])

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save the new copies to")
        if d:
            self.folder.setText(d)

    def steps(self):
        st = {}
        for key, cb in self.checks.items():
            if not cb.isChecked():
                continue
            if key == "redact":
                st[key] = {"patterns": [k for k, b in self.redact_patterns.items() if b.isChecked()]}
            elif key == "sanitize":
                st[key] = {"options": ["metadata", "comments", "attachments", "javascript"]}
            elif key == "page_numbers":
                st[key] = {"template": self.pn_text.text() or "Page {page} of {total}"}
            elif key == "watermark":
                st[key] = {"text": self.wm_text.text() or "CONFIDENTIAL"}
            elif key == "password":
                st[key] = {"password": self.pw.text()}
            else:
                st[key] = {}
        return st

    def _ok(self):
        st = self.steps()
        if not self.files:
            self.error.setText("Add some PDFs first.")
        elif not st:
            self.error.setText("Tick at least one step.")
        elif "password" in st and len(st["password"]["password"]) < 4:
            self.error.setText("Type a password of at least 4 characters.")
        elif "redact" in st and not st["redact"]["patterns"]:
            self.error.setText("Tick what to redact (for example SSN).")
        elif not self.folder.text().strip() and not self.suffix.text().strip():
            self.error.setText("Choose another folder or add something to the file names, so "
                               "the originals aren't overwritten.")
        else:
            self.accept()

    def values(self):
        return dict(files=list(self.files), steps=self.steps(),
                    out_dir=self.folder.text().strip() or None, suffix=self.suffix.text())


# ====================================================================== scan
class ScanDialog(ToolDialog):
    def __init__(self, parent, devices, ocr_ok, has_doc):
        import scanner
        super().__init__(parent, "Scan", "Scan paper straight into a PDF.", "Scan")
        self.device = QComboBox()
        for dev_id, name in devices:
            self.device.addItem(name, dev_id)
        self.source = QComboBox()
        for key, label, _ in scanner.SOURCES:
            self.source.addItem(label, key)
        self.color = QComboBox()
        for key, label, _ in scanner.COLORS:
            self.color.addItem(label, key)
        self.color.setCurrentIndex(1)
        self.dpi = QComboBox()
        for d in (150, 200, 300, 600):
            self.dpi.addItem(f"{d} dpi" + ("  (recommended)" if d == 300 else ""), d)
        self.dpi.setCurrentIndex(2)
        self.ocr = QCheckBox("Recognize text so the scan can be searched and copied")
        self.ocr.setChecked(ocr_ok)
        self.ocr.setEnabled(ocr_ok)
        self.target = QComboBox()
        self.target.addItem("A new PDF", "new")
        if has_doc:
            self.target.addItem("The end of the open PDF", "append")
        self.form.addRow("Scanner", self.device)
        self.form.addRow("Paper from", self.source)
        self.form.addRow("Colour", self.color)
        self.form.addRow("Quality", self.dpi)
        self.form.addRow("", self.ocr)
        self.form.addRow("Put pages in", self.target)

    def values(self):
        return dict(device=self.device.currentData(), source=self.source.currentData(),
                    color=self.color.currentData(), dpi=self.dpi.currentData(),
                    ocr=self.ocr.isChecked(), target=self.target.currentData())


# ====================================================================== digital signatures
class DigitalSignDialog(ToolDialog):
    def __init__(self, parent, ids):
        super().__init__(parent, "Sign with a digital ID",
                         "A digital signature proves who signed and shows if the document is "
                         "changed later. The signed copy is saved as a new file.", "Sign")
        self.id_box = QComboBox()
        for p in ids:
            self.id_box.addItem(Path(p).stem, str(p))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        create = QPushButton("Create one…")
        create.clicked.connect(self._create)
        r = QHBoxLayout()
        r.addWidget(self.id_box, 1)
        r.addWidget(browse)
        r.addWidget(create)
        self.form.addRow("Digital ID", r)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.form.addRow("Password", self.pw)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(["I approve this document", "I am the author of this document",
                              "I have reviewed this document", "I agree to the terms", ""])
        self.form.addRow("Reason", self.reason)
        self.location = QLineEdit()
        self.location.setPlaceholderText("Optional, e.g. Miami, FL")
        self.form.addRow("Location", self.location)
        self.info = QLabel("")
        self.info.setObjectName("subtitle")
        self.info.setWordWrap(True)
        self.form.addRow("", self.info)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a digital ID", "",
                                           "Digital IDs (*.pfx *.p12)")
        if p:
            self.id_box.insertItem(0, Path(p).stem, p)
            self.id_box.setCurrentIndex(0)

    def _create(self):
        dlg = CreateIdDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.created:
            self.id_box.insertItem(0, dlg.created.stem, str(dlg.created))
            self.id_box.setCurrentIndex(0)
            self.pw.setText(dlg.password.text())

    def validate(self):
        import signing
        if not self.id_box.currentData():
            raise ValueError("Choose a digital ID, or create one.")
        name, issuer, until = signing.id_info(self.id_box.currentData(), self.pw.text())
        import datetime
        if until < datetime.datetime.now(datetime.timezone.utc):
            raise ValueError(f"This digital ID expired on {until:%m/%d/%Y}.")
        self.signer = name
        return True

    def values(self):
        return dict(pfx=self.id_box.currentData(), password=self.pw.text(),
                    reason=self.reason.currentText().strip(), location=self.location.text().strip())


class CreateIdDialog(ToolDialog):
    def __init__(self, parent):
        super().__init__(parent, "Create a digital ID",
                         "Makes a self-signed digital ID for signing PDFs. Others can see it's "
                         "unchanged since you signed, but your identity isn't verified by a "
                         "certificate authority. For that, ask IT for your organization's ID.",
                         "Create")
        self.name = QLineEdit(os.environ.get("USERNAME", ""))
        self.email = QLineEdit()
        self.org = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password2 = QLineEdit()
        self.password2.setEchoMode(QLineEdit.Password)
        for label, w in (("Your name", self.name), ("Email", self.email),
                         ("Organization", self.org), ("Password", self.password),
                         ("Password again", self.password2)):
            self.form.addRow(label, w)
        self.created = None

    def validate(self):
        import signing
        if self.password.text() != self.password2.text():
            raise ValueError("The passwords don't match.")
        self.created = signing.create_digital_id(self.name.text(), self.email.text(),
                                                 self.org.text(), self.password.text())
        return True


class SignaturesDialog(QDialog):
    def __init__(self, parent, results):
        super().__init__(parent)
        self.setWindowTitle("Digital signatures")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Digital signatures")
        head.setObjectName("dlgTitle")
        lay.addWidget(head)
        if not results:
            t = QLabel("This PDF has no digital signatures.")
            t.setObjectName("subtitle")
            lay.addWidget(t)
        for r in results:
            ok = r["intact"]
            box = QFrame()
            box.setObjectName("panel")
            v = QVBoxLayout(box)
            v.setContentsMargins(14, 10, 14, 10)
            mark = "✔" if ok and r["trusted"] else ("✔" if ok else "✖")
            when = r["time"].astimezone().strftime("%m/%d/%Y %I:%M %p") if r["time"] else "unknown time"
            title = QLabel(f"<b>{mark}  {r['signer'] or 'Unknown signer'}</b> &nbsp; "
                           f"<span style='color:#8A90A2'>signed {when}</span>")
            v.addWidget(title)
            s = QLabel(r["summary"])
            s.setWordWrap(True)
            s.setObjectName("subtitle" if ok else "error")
            v.addWidget(s)
            lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ====================================================================== form fields
class FieldDialog(ToolDialog):
    def __init__(self, parent, kind, name, tooltip="", choices=None):
        import forms
        super().__init__(parent, "Field properties", forms.LABELS.get(kind, "Field"), "Save")
        self.name = QLineEdit(name)
        self.tooltip = QLineEdit(tooltip)
        self.tooltip.setPlaceholderText("Shown when someone points at the field")
        self.form.addRow("Name", self.name)
        self.form.addRow("Tooltip", self.tooltip)
        self.choices = None
        if kind == "dropdown":
            self.choices = QTextEdit()
            self.choices.setAcceptRichText(False)
            # set as plain text: QTextEdit("...") would read it as HTML and join the lines
            self.choices.setPlainText("\n".join(choices or []))
            self.choices.setFixedHeight(110)
            self.form.addRow("Choices (one per line)", self.choices)

    def validate(self):
        if not self.name.text().strip():
            raise ValueError("Give the field a name.")
        if self.choices is not None and not self.values()["choices"]:
            raise ValueError("Add at least one choice.")
        return True

    def values(self):
        ch = None
        if self.choices is not None:
            ch = [c.strip() for c in self.choices.toPlainText().splitlines() if c.strip()]
        return dict(name=self.name.text().strip(), tooltip=self.tooltip.text().strip(),
                    choices=ch)
