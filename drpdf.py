"""Dr.PDF - a private, offline PDF editor and file converter.

Everything happens locally. This program contains no networking code and
never sends your files anywhere.
"""
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSize, QTimer
from PySide6.QtGui import QIcon, QDesktopServices, QFont, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFileDialog, QListWidget, QListWidgetItem, QSlider, QCheckBox,
    QProgressBar, QFrame, QButtonGroup, QSizePolicy, QStackedWidget, QToolButton, QMessageBox,
    QMenu,
)

import converters as cv
import dialogs
import icons
import theme
import updates
from pdfeditor import PdfEditor
import version
from version import APP_NAME, VERSION
PILLS_PER_ROW = 8


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def common_targets(files):
    """Formats that every file in the list can be converted to, in order."""
    if not files:
        return []
    order = cv.targets_for(files[0].suffix)
    allowed = set(order)
    for f in files[1:]:
        allowed &= set(cv.targets_for(f.suffix))
    return [t for t in order if t in allowed]


class Worker(QThread):
    progress = Signal(int, bool, str)   # row, ok, message
    finished_all = Signal(int, int, str)  # ok, failed, combined file name

    def __init__(self, files, target, out_dir, opts, combine):
        super().__init__()
        self.files, self.target, self.out_dir = files, target, out_dir
        self.opts, self.combine = opts, combine

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pythoncom = None
        office = cv.Office()
        tmp = Path(tempfile.mkdtemp()) if self.combine else None
        pdfs = []
        ok = fail = 0
        combined_name = ""
        try:
            for row, f in enumerate(self.files):
                try:
                    if self.combine:
                        if f.suffix.lower() == ".pdf":
                            outs = [f]
                        else:
                            outs = cv.convert(f, "PDF", tmp / str(row), self.opts, office)
                        pdfs.extend(outs)
                        msg = "added"
                    else:
                        target = Path(self.out_dir) if self.out_dir else f.parent
                        outs = cv.convert(f, self.target, target, self.opts, office)
                        msg = outs[0].name if len(outs) == 1 else f"{len(outs)} files"
                    ok += 1
                    self.progress.emit(row, True, msg)
                except Exception as e:
                    fail += 1
                    self.progress.emit(row, False, str(e) or e.__class__.__name__)
            if self.combine and pdfs:
                dest = Path(self.out_dir) if self.out_dir else self.files[0].parent
                out = cv.unique_path(dest / "Combined.pdf")
                cv.merge_pdfs(pdfs, out)
                combined_name = out.name
        except Exception as e:
            combined_name = ""
            fail = max(fail, 1)
            self.progress.emit(len(self.files) - 1, False, f"Combining failed: {e}")
        finally:
            office.close()
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            if pythoncom:
                pythoncom.CoUninitialize()
        self.finished_all.emit(ok, fail, combined_name)


class UpdateCheck(QThread):
    """Looks for a newer installer in the update folder without freezing the window
    (network folders can be slow or offline)."""
    found = Signal(str, str)

    def __init__(self, folder):
        super().__init__()
        self.folder = folder

    def run(self):
        res = updates.find_update(self.folder)
        if res:
            self.found.emit(res[0], str(res[1]))


STYLE = """
* { font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; color: #E7E9F0; }
QWidget#root { background: #0F1117; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#titleAccent { font-size: 26px; font-weight: 700; color: #7C83FF; }
QLabel#subtitle { color: #8A90A2; font-size: 13px; }
QLabel#badge { background: #16251E; color: #5FD39A; border: 1px solid #23432F;
    border-radius: 11px; padding: 3px 10px; font-size: 12px; }
QFrame#drop { background: #161923; border: 2px dashed #2D3244; border-radius: 18px; }
QFrame#drop[hover="true"] { background: #1A1D33; border-color: #7C83FF; }
QLabel#dropIcon { font-size: 40px; color: #7C83FF; }
QLabel#dropTitle { font-size: 17px; font-weight: 600; }
QLabel#dropHint { color: #8A90A2; font-size: 12px; }
QLabel#section { color: #8A90A2; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#hint { color: #8A90A2; font-size: 13px; padding: 6px 0; }
QPushButton { background: #1E2230; border: 1px solid #2D3244; border-radius: 10px;
    padding: 8px 16px; font-size: 13px; }
QPushButton:hover { background: #262B3D; border-color: #3B4260; }
QPushButton:disabled { color: #5A6070; }
QPushButton#pill { padding: 7px 0; min-width: 58px; font-weight: 600; }
QPushButton#pill:checked { background: #7C83FF; border-color: #7C83FF; color: white; }
QPushButton#primary { background: #7C83FF; border: none; color: white;
    font-size: 15px; font-weight: 600; padding: 12px 28px; border-radius: 12px; }
QPushButton#primary:hover { background: #8F95FF; }
QPushButton#primary:disabled { background: #2D3244; color: #6A7085; }
QPushButton#link { background: transparent; border: none; color: #8A90A2; padding: 4px 8px; }
QPushButton#link:hover { color: #E7E9F0; }
QListWidget { background: #161923; border: 1px solid #22263A; border-radius: 14px;
    padding: 6px; font-size: 13px; outline: none; }
QListWidget::item { padding: 4px 10px; border-radius: 8px; }
QListWidget::item:selected { background: #22263A; }
QSlider::groove:horizontal { height: 4px; background: #2D3244; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #7C83FF; border-radius: 2px; }
QSlider::handle:horizontal { background: white; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px; }
QCheckBox { font-size: 13px; spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid #3B4260; background: #1E2230; }
QCheckBox::indicator:checked { background: #7C83FF; border-color: #7C83FF;
    image: url(CHECK_ICON); }
QProgressBar { background: #1E2230; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #7C83FF; border-radius: 3px; }
QLabel#status { color: #8A90A2; font-size: 12px; }
QPushButton#tab { background: transparent; border: 1px solid transparent; color: #8A90A2;
    font-weight: 600; padding: 6px 14px; border-radius: 10px; }
QPushButton#tab:hover { color: #E7E9F0; }
QPushButton#tab:checked { background: #1E2230; border-color: #2D3244; color: #E7E9F0; }
QPushButton#tool { padding: 6px 11px; }
QPushButton#accent { background: #2A2F55; border: 1px solid #7C83FF; color: #FFFFFE; font-weight: 600; }
QPushButton#accent:hover { background: #343A6B; }
QPushButton#tool:checked { background: #7C83FF; border-color: #7C83FF; color: white; }
QPushButton::menu-indicator { width: 0; image: none; }
QComboBox, QSpinBox, QLineEdit { background: #1E2230; border: 1px solid #2D3244;
    border-radius: 8px; padding: 5px 8px; font-size: 13px; min-height: 20px; }
QComboBox:focus, QSpinBox:focus, QLineEdit:focus { border-color: #7C83FF; }
QComboBox QAbstractItemView { background: #1E2230; border: 1px solid #2D3244;
    selection-background-color: #7C83FF; outline: none; }
QComboBox::drop-down { border: none; width: 18px; }
QMenu { background: #1E2230; border: 1px solid #2D3244; padding: 6px; border-radius: 8px; }
QMenu::item { padding: 6px 22px; border-radius: 6px; }
QMenu::item:selected { background: #7C83FF; color: white; }
QMenu::separator { height: 1px; background: #2D3244; margin: 5px 8px; }
QListWidget#thumbs { padding: 8px; }
QListWidget#thumbs::item { padding: 4px; border-radius: 8px; color: #8A90A2; }
QListWidget#thumbs::item:selected { background: #2A2F55; color: #FFFFFE; }
QDialog, QMessageBox, QInputDialog { background: #161923; }
QToolTip { background: #1E2230; color: #E7E9F0; border: 1px solid #2D3244; padding: 4px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2D3244; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2D3244; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QFrame#panel { background: #131620; border: 1px solid #1F2333; border-radius: 16px; }
QFrame#bar { background: #131620; border: 1px solid #1F2333; border-radius: 12px; }
QFrame#props { background: #171A26; border: 1px solid #23273A; border-radius: 10px; }
QFrame#vsep { background: #2A2F42; border: none; margin: 0 6px; }
QFrame#sidebar { background: #131620; border: 1px solid #1F2333; border-radius: 12px; }
QFrame#statusbar { background: transparent; }
QFrame#sep { background: #2A2F42; max-height: 1px; border: none; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 5px 8px; font-size: 13px; color: #D5D8E2; }
QToolButton:hover { background: #22263A; border-color: #2D3244; }
QToolButton:pressed { background: #2A2F45; }
QToolButton:checked { background: #7C83FF; border-color: #7C83FF; color: white; }
QToolButton:disabled { color: #555B6E; }
QToolButton::menu-indicator { image: none; width: 0; }
QToolButton#menuTool { padding-right: 10px; }
QToolButton#accentTool { background: #2A2F55; border: 1px solid #4A50A0; color: #FFFFFE; font-weight: 600; }
QToolButton#accentTool:hover { background: #343A6B; border-color: #7C83FF; }
QToolButton#accentTool:disabled { background: transparent; border-color: #2D3244; color: #555B6E; }
QToolButton#rail { border-radius: 10px; padding: 0; }
QToolButton#rail:checked { background: #2A2F55; border-color: #4A50A0; }
QToolButton#mini { padding: 2px; border-radius: 6px; }
QToolButton#fmt { padding: 0; font-size: 14px; }
QToolButton#about { padding: 4px; border-radius: 10px; }
QPushButton#primarySmall { background: #7C83FF; border: none; color: white; font-weight: 600; }
QPushButton#primarySmall:hover { background: #8F95FF; }
QTreeWidget { background: transparent; border: none; font-size: 13px; outline: none; }
QTreeWidget::item { padding: 4px 2px; border-radius: 6px; }
QTreeWidget::item:selected { background: #2A2F55; color: #FFFFFE; }
QTreeWidget::item:hover { background: #1E2230; }
QListWidget#thumbs, QListWidget#comments { background: transparent; border: none; }
QListWidget#comments::item { padding: 8px; margin: 2px 0; border-radius: 8px;
    background: #181C28; color: #C9CDDA; }
QListWidget#comments::item:selected { background: #2A2F55; color: #FFFFFE; }
QFrame#banner { background: #1A1D33; border: 1px solid #4A50A0; border-radius: 10px; }
QProgressDialog QLabel { font-size: 13px; }
QAbstractScrollArea::corner { background: transparent; border: none; }
QTabBar#docTabs { background: transparent; }
QTabBar#docTabs::tab { background: #161923; border: 1px solid #1F2333; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 6px 10px 6px 12px;
    margin-right: 3px; color: #8A90A2; min-width: 90px; max-width: 260px; }
QTabBar#docTabs::tab:selected { background: #22263A; color: #E7E9F0; border-color: #2D3244; }
QTabBar#docTabs::tab:hover { color: #E7E9F0; }
QTabBar QToolButton { background: #1E2230; border: 1px solid #2D3244; border-radius: 6px; }
QListWidget#recent { background: transparent; border: none; }
QListWidget#recent::item { padding: 8px 10px; border-radius: 8px; color: #C9CDDA; }
QListWidget#recent::item:hover { background: #1E2230; }
QLabel#dlgTitle { font-size: 18px; font-weight: 700; }
QLabel#error { color: #FF6B7A; font-size: 12px; }
QLabel#kbd { background: #1E2230; border: 1px solid #2D3244; border-radius: 5px; padding: 1px 7px;
    font-size: 12px; color: #C9CDDA; }
QRadioButton { font-size: 13px; spacing: 8px; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px;
    border: 1px solid #3B4260; background: #1E2230; }
QRadioButton::indicator:checked { background: #7C83FF; border: 4px solid #1E2230;
    outline: 1px solid #7C83FF; }
QRadioButton::indicator:disabled { background: #161923; border-color: #2D3244; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }
QLineEdit#pageBox { padding: 4px 2px; font-weight: 600; }
QMessageBox QLabel { font-size: 13px; }
"""


class DropZone(QFrame):
    def __init__(self, on_files, on_browse_files, on_browse_folder):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.on_files = on_files
        self.setMinimumHeight(180)
        self.setCursor(Qt.PointingHandCursor)

        icon = QLabel("⬇")
        icon.setObjectName("dropIcon")
        title = QLabel("Drop files or folders here")
        title.setObjectName("dropTitle")
        hint = QLabel("PDF · Word · Excel · PowerPoint · CSV · Text · Pictures")
        hint.setObjectName("dropHint")

        b1 = QPushButton("Choose files")
        b2 = QPushButton("Choose folder")
        b1.clicked.connect(on_browse_files)
        b2.clicked.connect(on_browse_folder)
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(b1)
        btns.addWidget(b2)
        btns.addStretch()

        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addStretch()
        for w in (icon, title, hint):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)
        lay.addSpacing(10)
        lay.addLayout(btns)
        lay.addStretch()
        self._browse = on_browse_files

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._browse()

    def _set_hover(self, v):
        self.setProperty("hover", "true" if v else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.on_files(paths)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(f"{APP_NAME} — Edit & Convert Files")
        icon_file = resource_path("drpdf.ico")
        if os.path.exists(icon_file):
            self.setWindowIcon(QIcon(icon_file))
        self.resize(1240, 860)
        self.setMinimumSize(980, 680)
        self.settings = version.settings()
        self.setAcceptDrops(True)

        self.files: list[Path] = []
        self.out_dir: str | None = None
        self.last_out: str | None = None
        self.worker = None
        self.targets: list[str] = []
        self.selected: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 12)
        outer.setSpacing(12)

        # Header
        head = QHBoxLayout()
        head.setSpacing(0)
        t1 = QLabel("Dr.")
        t1.setObjectName("title")
        t2 = QLabel("PDF")
        t2.setObjectName("titleAccent")
        head.addWidget(t1)
        head.addWidget(t2)
        head.addSpacing(10)
        head.addSpacing(24)
        self.tab_group = QButtonGroup(self)
        for i, (name, ic) in enumerate((("Convert", "convert"), ("Edit PDF", "edit"))):
            b = QPushButton("  " + name)
            b.setIcon(icons.icon(ic, soft=True))
            b.setProperty("icon_name", ic)
            b.setProperty("icon_soft", True)
            b.setIconSize(QSize(16, 16))
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setCursor(Qt.PointingHandCursor)
            self.tab_group.addButton(b, i)
            head.addWidget(b, 0, Qt.AlignVCenter)
            head.addSpacing(4)
        self.tab_group.idClicked.connect(self.show_tab)
        head.addStretch()
        badge = QLabel("🔒  100% offline")
        badge.setObjectName("badge")
        badge.setToolTip("Dr.PDF never connects to the internet.\n"
                         "Your files never leave this computer.")
        self.head, self.badge = head, badge
        head.addWidget(badge)
        head.addSpacing(8)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("about")
        self.theme_btn.setIconSize(QSize(20, 20))
        self.theme_btn.setToolTip("Appearance: dark or light")
        self.theme_btn.setPopupMode(QToolButton.InstantPopup)
        self.theme_btn.setIcon(icons.icon("moon"))  # updated when the theme is applied
        self.theme_btn.setProperty("icon_name", "moon")
        tm = QMenu(self)
        self.theme_actions = {}
        for key, label in theme.CHOICES:
            a = tm.addAction(label, lambda k=key: self.set_theme(k))
            a.setCheckable(True)
            self.theme_actions[key] = a
        self.theme_btn.setMenu(tm)
        head.addWidget(self.theme_btn)
        about = QToolButton()
        about.setObjectName("about")
        about.setIcon(icons.icon("info"))
        about.setProperty("icon_name", "info")
        about.setIconSize(QSize(20, 20))
        about.setToolTip(f"About {APP_NAME} {VERSION} and keyboard shortcuts")
        about.clicked.connect(self.about)
        head.addWidget(about)
        outer.addLayout(head)

        # "a new version is available" bar (shown when IT has put one in the update folder)
        self.update_bar = QFrame()
        self.update_bar.setObjectName("banner")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(12, 6, 8, 6)
        self.update_text = QLabel("")
        ub.addWidget(self.update_text, 1)
        self.update_install = QPushButton("Install now")
        self.update_install.setObjectName("primarySmall")
        self.update_install.clicked.connect(self.install_update)
        later = QPushButton("Later")
        later.clicked.connect(self.update_bar.hide)
        ub.addWidget(self.update_install)
        ub.addWidget(later)
        self.update_bar.hide()
        self.update_found = None
        outer.addWidget(self.update_bar)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        conv = QWidget()
        self.pages.addWidget(conv)
        self.editor = PdfEditor()
        self._arrange_editor_buttons()
        self.editor.title_changed.connect(self.on_editor_title)
        self.pages.addWidget(self.editor)

        root = QVBoxLayout(conv)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # Drop zone
        self.drop = DropZone(self.add_paths, self.browse_files, self.browse_folder)
        root.addWidget(self.drop)

        # File list header
        lh = QHBoxLayout()
        self.count_lbl = QLabel("FILES")
        self.count_lbl.setObjectName("section")
        lh.addWidget(self.count_lbl)
        lh.addStretch()
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.setObjectName("link")
        self.clear_btn.clicked.connect(self.clear_files)
        lh.addWidget(self.clear_btn)
        root.addLayout(lh)

        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self.list.setToolTip("Press Delete to remove selected files. "
                             "Double-click a PDF to edit it.")
        self.list.itemDoubleClicked.connect(self.edit_from_list)
        root.addWidget(self.list, 1)

        # Options panel
        panel = QFrame()
        panel.setObjectName("panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(18, 16, 18, 16)
        pl.setSpacing(12)

        s1 = QLabel("CONVERT TO")
        s1.setObjectName("section")
        pl.addWidget(s1)
        self.pill_hint = QLabel()
        self.pill_hint.setObjectName("hint")
        self.pill_hint.setWordWrap(True)
        pl.addWidget(self.pill_hint)
        self.pills = QGridLayout()
        self.pills.setSpacing(6)
        for c in range(PILLS_PER_ROW):  # keep pills the same size however many there are
            self.pills.setColumnStretch(c, 1)
        pl.addLayout(self.pills)
        self.fmt_group = QButtonGroup(self)
        self.fmt_group.buttonClicked.connect(self.on_pill)

        opts = QHBoxLayout()
        opts.setSpacing(14)
        self.q_lbl = QLabel("Quality")
        self.q_slider = QSlider(Qt.Horizontal)
        self.q_slider.setRange(10, 100)
        self.q_slider.setValue(90)
        self.q_slider.setFixedWidth(160)
        self.q_val = QLabel("90")
        self.q_val.setFixedWidth(28)
        self.q_slider.valueChanged.connect(lambda v: self.q_val.setText(str(v)))
        opts.addWidget(self.q_lbl)
        opts.addWidget(self.q_slider)
        opts.addWidget(self.q_val)
        self.strip = QCheckBox("Remove hidden photo data (GPS, camera info)")
        self.strip.setChecked(True)
        self.strip.setToolTip("Strips EXIF metadata such as location from converted pictures.")
        opts.addWidget(self.strip)
        self.combine = QCheckBox("Combine everything into one PDF")
        self.combine.setToolTip("Puts all files, in list order, into a single Combined.pdf.")
        self.combine.toggled.connect(self.refresh)
        opts.addWidget(self.combine)
        opts.addStretch()
        pl.addLayout(opts)

        dest = QHBoxLayout()
        dl = QLabel("Save to")
        self.dest_lbl = QLabel("Same folder as each file")
        self.dest_lbl.setObjectName("subtitle")
        self.dest_btn = QPushButton("Change…")
        self.dest_btn.clicked.connect(self.choose_dest)
        self.dest_reset = QPushButton("Reset")
        self.dest_reset.setObjectName("link")
        self.dest_reset.clicked.connect(self.reset_dest)
        self.dest_reset.hide()
        dest.addWidget(dl)
        dest.addSpacing(8)
        dest.addWidget(self.dest_lbl, 1)
        dest.addWidget(self.dest_reset)
        dest.addWidget(self.dest_btn)
        pl.addLayout(dest)
        root.addWidget(panel)

        # Footer
        foot = QHBoxLayout()
        st = QVBoxLayout()
        st.setSpacing(6)
        self.status = QLabel("Add some files to get started.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        st.addWidget(self.status)
        st.addWidget(self.bar)
        foot.addLayout(st, 1)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(self.open_output)
        self.open_btn.hide()
        foot.addWidget(self.open_btn)
        self.go = QPushButton("Convert")
        self.go.setObjectName("primary")
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.clicked.connect(self.start)
        foot.addWidget(self.go)
        root.addLayout(foot)

        self.rebuild_targets()

    # ---------- file handling ----------
    def add_paths(self, paths):
        added = skipped = needs_office = 0
        known = set(self.files)
        for p in paths:
            p = Path(p)
            candidates = []
            if p.is_dir():
                candidates = sorted(x for x in p.rglob("*") if x.is_file())
            elif p.is_file():
                candidates = [p]
            for c in candidates:
                if c in known or c.name.startswith("~$"):
                    continue
                if cv.is_supported(c):
                    self.files.append(c)
                    known.add(c)
                    self._add_item(c)
                    added += 1
                elif cv.needs_office_hint(c):
                    needs_office += 1
                else:
                    skipped += 1
        parts = []
        if added:
            parts.append(f"Added {added} file{'s' if added != 1 else ''}.")
        elif paths:
            parts.append("No new supported files found.")
        if needs_office:
            parts.append(f"{needs_office} Office file(s) need Microsoft Office or "
                         "LibreOffice installed to convert.")
        if skipped and paths:
            parts.append(f"{skipped} unsupported file(s) skipped.")
        if parts:
            self.status.setText("  ".join(parts))
        self.open_btn.hide()
        self.rebuild_targets()

    def _add_item(self, p: Path):
        try:
            size = human_size(p.stat().st_size)
        except OSError:
            size = ""
        it = QListWidgetItem(f"{p.name}    ·  {size}")
        it.setToolTip(str(p))
        self.list.addItem(it)

    def browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose files", "",
            "Supported files (*.pdf *.docx *.doc *.rtf *.odt *.txt *.md *.xlsx *.xls "
            "*.xlsm *.ods *.csv *.tsv *.pptx *.ppt *.odp *.html *.htm *.png *.jpg "
            "*.jpeg *.webp *.heic *.gif *.bmp *.tif *.tiff *.ico);;All files (*)")
        if paths:
            self.add_paths(paths)

    def browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if d:
            self.add_paths([d])

    def clear_files(self):
        if self.busy():
            return
        self.files.clear()
        self.list.clear()
        self.bar.hide()
        self.open_btn.hide()
        self.status.setText("Add some files to get started.")
        self.rebuild_targets()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and not self.busy():
            rows = sorted((self.list.row(it) for it in self.list.selectedItems()), reverse=True)
            for row in rows:
                self.list.takeItem(row)
                del self.files[row]
            self.rebuild_targets()
        else:
            super().keyPressEvent(e)

    # whole window accepts drops too
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if self.pages.currentIndex() == 1:  # editor: open it, or add its pages
            self.editor.add_files(paths, offer_open=True)
            return
        self.add_paths(paths)

    # ---------- tabs / editor ----------
    def _arrange_editor_buttons(self):
        """The PDF's Save button sits at the top right, next to "100% offline" (shown on
        the Edit PDF tab)."""
        save = self.editor.save_btn
        i = self.head.indexOf(self.badge)
        self.head.insertWidget(i, save, 0, Qt.AlignVCenter)
        self.head.insertSpacing(i + 1, 8)
        save.setVisible(False)

    def show_tab(self, i):
        self.tab_group.button(i).setChecked(True)
        self.pages.setCurrentIndex(i)
        self.editor.save_btn.setVisible(i == 1)
        self.settings.setValue("tab", i)

    def edit_from_list(self, item):
        p = self.files[self.list.row(item)]
        if p.suffix.lower() == ".pdf":
            self.show_tab(1)
            self.editor.open_file(str(p))

    def on_editor_title(self, name):
        self.setWindowTitle(f"{name} — {APP_NAME}" if name
                            else f"{APP_NAME} — Edit & Convert Files")

    def set_theme(self, choice):
        self.settings.setValue("theme", choice)
        name = theme.apply(QApplication.instance(), app_style(), choice)
        for k, a in self.theme_actions.items():
            a.setChecked(k == choice)
        icon_name = "moon" if name == "dark" else "sun"
        self.theme_btn.setIcon(icons.icon(icon_name))
        self.theme_btn.setProperty("icon_name", icon_name)
        self.refresh()  # list colours

    def restore_window(self):
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        try:
            self.show_tab(int(self.settings.value("tab", 0)))
        except (TypeError, ValueError):
            pass
        self.set_theme(self.settings.value("theme", "dark") or "dark")

    # ---------- updates ----------
    def update_folder(self):
        return updates.policy_folder() or (self.settings.value("update_folder", "") or "")

    def start_update_check(self):
        folder = self.update_folder()
        if not folder:
            return
        self._upd = UpdateCheck(folder)
        self._upd.found.connect(self._update_available)
        self._upd.start()

    def _update_available(self, ver, path):
        self.update_found = (ver, path)
        self.update_text.setText(f"Dr.PDF {ver} is available (you have {VERSION}).")
        self.update_bar.show()

    def install_update(self):
        if not self.update_found or not self.editor.maybe_save_all():
            return
        from PySide6.QtCore import QProcess
        ok = QProcess.startDetached(self.update_found[1], ["/SILENT", "/SP-", "/NOCANCEL"])
        if ok:
            self.settings.setValue("geometry", self.saveGeometry())
            QApplication.instance().quit()
        else:
            QMessageBox.warning(self, "Update", "The installer couldn't be started. Ask your IT "
                                "department to install the new version.")

    def about(self):
        locked = bool(updates.policy_folder())

        def check(folder):
            if not locked:
                self.settings.setValue("update_folder", folder)
            if not folder:
                return "No update folder set."
            found = updates.find_update(folder)
            if found:
                self._update_available(*found)
                return f"Version {found[0]} is available — use Install now at the top."
            return f"You have the latest version ({VERSION})."

        dialogs.AboutDialog(self, self.update_folder(), locked, check).exec()

    def closeEvent(self, e):
        if self.editor.maybe_save_all():
            self.settings.setValue("geometry", self.saveGeometry())
            e.accept()
        else:
            e.ignore()

    # ---------- options ----------
    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def rebuild_targets(self):
        """Show only the formats that every file in the list can become."""
        self.targets = common_targets(self.files)
        for b in self.fmt_group.buttons():
            self.fmt_group.removeButton(b)
            b.deleteLater()
        if self.selected not in self.targets:
            self.selected = self.targets[0] if self.targets else None
        for i, label in enumerate(self.targets):
            b = QPushButton(label)
            b.setObjectName("pill")
            b.setCheckable(True)
            b.setToolTip(cv.TARGETS[label][1])
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setChecked(label == self.selected)
            self.fmt_group.addButton(b, i)
            self.pills.addWidget(b, i // PILLS_PER_ROW, i % PILLS_PER_ROW)
        if not self.files:
            self.pill_hint.setText("Add files and the formats they can become will appear here.")
        elif not self.targets:
            self.pill_hint.setText("These files don't share a format they can all become. "
                                   "Try converting different kinds of files separately.")
        self.pill_hint.setVisible(not self.targets)
        self.update_options()

    def on_pill(self, b):
        self.selected = b.text()
        self.update_options()

    def update_options(self, *_):
        t = self.selected
        has_q = t in cv.QUALITY_TARGETS
        for w in (self.q_lbl, self.q_slider, self.q_val):
            w.setVisible(bool(self.files) and has_q)
        has_images = any(cv.kind_of(f.suffix) == "image" for f in self.files)
        self.strip.setVisible(has_images and t in cv.IMAGE_FMT)
        self.combine.setVisible(t == "PDF" and len(self.files) > 1)
        self.refresh()

    def choose_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Save converted files to")
        if d:
            self.out_dir = d
            self.dest_lbl.setText(d)
            self.dest_reset.show()

    def reset_dest(self):
        self.out_dir = None
        self.dest_lbl.setText("Same folder as each file")
        self.dest_reset.hide()

    def combining(self):
        return self.combine.isVisible() and self.combine.isChecked()

    def refresh(self):
        n = len(self.files)
        self.count_lbl.setText(f"FILES  ·  {n}" if n else "FILES")
        self.clear_btn.setVisible(n > 0)
        self.go.setEnabled(n > 0 and bool(self.selected) and not self.busy())
        if not n or not self.selected:
            self.go.setText("Convert")
        elif self.combining():
            self.go.setText(f"Combine {n} into one PDF")
        else:
            self.go.setText(f"Convert {n} to {self.selected}")

    # ---------- conversion ----------
    def start(self):
        if not self.files or not self.selected:
            return
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setText(it.text().split("    →")[0].lstrip("✓✗ ").strip())
            it.setForeground(QColor(theme.color("text")))
            it.setToolTip(str(self.files[i]))
        self.bar.setRange(0, len(self.files))
        self.bar.setValue(0)
        self.bar.show()
        self.open_btn.hide()
        self.status.setText("Converting… (Office files can take a few seconds each)")
        opts = {"quality": self.q_slider.value(), "strip_meta": self.strip.isChecked()}
        self.worker = Worker(list(self.files), self.selected, self.out_dir, opts,
                             self.combining())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_all.connect(self.on_done)
        self.worker.start()
        self.refresh()
        for w in (self.drop, self.clear_btn, self.dest_btn):
            w.setEnabled(False)
        for b in self.fmt_group.buttons():
            b.setEnabled(False)

    def on_progress(self, row, ok, msg):
        it = self.list.item(row)
        base = it.text().split("    →")[0].lstrip("✓✗ ").strip()
        if ok:
            it.setText(f"✓  {base}    →  {msg}")
            it.setForeground(QColor(theme.color("ok")))
        else:
            it.setText(f"✗  {base}    →  failed (hover for details)")
            it.setToolTip(msg)
            it.setForeground(QColor(theme.color("error")))
        self.bar.setValue(row + 1)
        self.status.setText(f"Converting… {row + 1} of {len(self.files)}")

    def on_done(self, ok, fail, combined):
        for w in (self.drop, self.clear_btn, self.dest_btn):
            w.setEnabled(True)
        for b in self.fmt_group.buttons():
            b.setEnabled(True)
        if combined:
            msg = f"Done! Combined {ok} file{'s' if ok != 1 else ''} into {combined}"
        else:
            msg = f"Done! {ok} converted"
        if fail:
            msg += f", {fail} failed (hover for details)"
        self.status.setText(msg + ".")
        self.last_out = self.out_dir or (str(self.files[0].parent) if self.files else None)
        self.open_btn.setVisible(bool(self.last_out) and ok > 0)
        self.refresh()

    def open_output(self):
        if self.last_out:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_out))


def app_style():
    """The dark style sheet with the checkbox tick filled in (theme.py derives light)."""
    return STYLE.replace("CHECK_ICON", resource_path("check.png").replace("\\", "/"))


LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME / "logs"


def install_crash_handler():
    """Unexpected errors are written to a log and shown politely instead of closing the app."""
    import time
    import traceback
    last = [0.0]

    def handler(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {APP_NAME} {VERSION}\n{text}")
        except OSError:
            pass
        if kind is KeyboardInterrupt or time.time() - last[0] < 3:
            return
        last[0] = time.time()
        try:
            box = QMessageBox(QApplication.activeWindow())
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(APP_NAME)
            box.setText("Something went wrong, but Dr.PDF is still running.")
            box.setInformativeText(f"{value}\n\nIf this keeps happening, send the file "
                                   f"errors.log from {LOG_DIR} to your IT support.")
            box.exec()
        except Exception:
            pass

    sys.excepthook = handler


def handle_args(w, args):
    """Open what Dr.PDF was started with (or what a second launch passed on)."""
    if "--edit" in args:  # "Edit with Dr.PDF" / Open with: each PDF in its own tab
        w.show_tab(1)
        for pdf in [a for a in args if a.lower().endswith(".pdf")]:
            w.editor.open_file(pdf)
    elif args:  # files dropped onto the .exe or sent via "Send to"
        w.add_paths(args)


def bring_to_front(w):
    if w.isMinimized():
        w.showNormal()
    w.show()
    w.raise_()
    w.activateWindow()


SERVER = "DrPDF-" + (os.environ.get("USERNAME") or "user")


def pass_to_running(args):
    """If Dr.PDF is already open, hand it the files (they open as new tabs there) and
    return True; otherwise return False and this launch becomes the running copy."""
    from PySide6.QtCore import QLockFile, QDir
    from PySide6.QtNetwork import QLocalSocket
    lock = QLockFile(QDir.temp().filePath(SERVER + ".lock"))
    lock.setStaleLockTime(0)
    if lock.tryLock(0):
        pass_to_running.lock = lock  # held for as long as this copy runs
        return False
    payload = json.dumps(args).encode("utf-8")
    for _ in range(50):  # the running copy may still be starting up
        s = QLocalSocket()
        s.connectToServer(SERVER)
        if s.waitForConnected(200):
            if sys.platform == "win32":
                try:  # let the running copy come to the front
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)
                except Exception:
                    pass
            s.write(payload)
            s.flush()
            s.waitForBytesWritten(2000)
            s.disconnectFromServer()
            return True
        if lock.tryLock(0):  # the other copy closed meanwhile
            pass_to_running.lock = lock
            return False
        QThread.msleep(100)
    return False


def listen_for_launches(w):
    """Files opened while Dr.PDF is running arrive here and open in tabs."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer(w)
    QLocalServer.removeServer(SERVER)
    if not server.listen(SERVER):
        return

    def arrived():
        sock = server.nextPendingConnection()
        buf = bytearray()

        def read():
            buf.extend(bytes(sock.readAll()))

        def done():
            read()
            sock.deleteLater()
            try:
                args = json.loads(bytes(buf).decode("utf-8"))
            except ValueError:
                return
            bring_to_front(w)
            handle_args(w, [str(a) for a in args])

        sock.readyRead.connect(read)
        sock.disconnected.connect(done)

    server.newConnection.connect(arrived)
    w._launch_server = server


def main():
    multiprocessing.freeze_support()
    # windowed builds have no console; give libraries that print somewhere to write
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DrPDF.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    # paths are passed on as full paths: the running copy has its own working folder
    args = [a if a.startswith("--") else os.path.abspath(a) for a in sys.argv[1:]]
    if pass_to_running(args):
        return
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    install_crash_handler()
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(app_style())
    w = MainWindow()
    w.restore_window()
    w.show()
    QTimer.singleShot(3000, w.start_update_check)
    listen_for_launches(w)
    handle_args(w, args)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
