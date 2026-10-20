# -*- mode: python ; coding: utf-8 -*-
# Build:  .venv\Scripts\python -m PyInstaller Dr.PDF.spec --noconfirm  (or run build.bat)
# Output: dist\Dr.PDF\Dr.PDF.exe (folder is packaged by installer.iss)
import sys
sys.path.insert(0, SPECPATH)
import version as V
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct)

_nums = tuple(int(x) for x in V.VERSION.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", V.APP_NAME),
        StringStruct("FileDescription", V.DESCRIPTION),
        StringStruct("FileVersion", V.VERSION),
        StringStruct("InternalName", V.APP_NAME),
        StringStruct("OriginalFilename", "Dr.PDF.exe"),
        StringStruct("ProductName", V.APP_NAME),
        StringStruct("ProductVersion", V.VERSION)])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])

a = Analysis(
    ['drpdf.py'],
    pathex=[],
    binaries=[],
    # digital signatures (pyHanko) ship data files and read their own package versions
    datas=[('drpdf.ico', '.'), ('check.png', '.')] + collect_data_files('pyhanko')
          + copy_metadata('pyHanko') + copy_metadata('pyhanko-certvalidator'),
    # Windows OCR bindings (winrt) are loaded partly by name, so collect them all
    hiddenimports=['win32com.client', 'pythoncom', 'pywintypes', 'win32api']
                  + collect_submodules('winrt') + collect_submodules('pyhanko')
                  + collect_submodules('pyhanko_certvalidator'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf',
              'tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False,
    optimize=0,
)
# software OpenGL fallback is not needed by a widgets app
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0]]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Dr.PDF',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['drpdf.ico'],
    version=version_info,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Dr.PDF',
)
