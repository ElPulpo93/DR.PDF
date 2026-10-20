"""Update notice for IT: Dr.PDF looks in a shared folder (for example a network share)
for a newer Dr.PDF-Setup.exe and offers to install it. Nothing is downloaded from the
internet.

The folder can be set in About → Updates, or centrally by IT in the registry:
HKEY_LOCAL_MACHINE (or HKEY_CURRENT_USER)\\Software\\Policies\\Dr.PDF, value UpdateFolder."""
import re
import winreg
from pathlib import Path

from version import VERSION

INSTALLER_NAME = "Dr.PDF-Setup.exe"


def parse(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def policy_folder():
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            k = winreg.OpenKey(hive, r"Software\Policies\Dr.PDF")
            v, _ = winreg.QueryValueEx(k, "UpdateFolder")
            winreg.CloseKey(k)
            if v:
                return str(v)
        except OSError:
            pass
    return ""


def file_version(path):
    """Version stored in an .exe (what Windows shows in Properties → Details)."""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return (0,)


def find_update(folder, current=None):
    """(version text, installer path) if `folder` holds a newer installer, else None."""
    if current is None:
        current = VERSION
    if not folder:
        return None
    try:
        f = Path(folder)
        setup = f / INSTALLER_NAME
        if not setup.is_file():
            cands = sorted(f.glob("Dr.PDF*Setup*.exe"))
            if not cands:
                return None
            setup = max(cands, key=file_version)
        ver = file_version(setup)
        note = f / "version.txt"  # optional, for installers without version details
        if ver == (0,) and note.is_file():
            ver = parse(note.read_text(encoding="utf-8", errors="ignore"))
        cur = parse(current)
        if tuple(ver[:3]) > tuple((cur + (0, 0, 0))[:3]):
            return ".".join(str(x) for x in ver[:3]), setup
    except OSError:
        return None
    return None
