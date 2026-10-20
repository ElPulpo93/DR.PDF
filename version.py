"""Single place for the app's name and version (used by the app, the build and the installer)."""
APP_NAME = "Dr.PDF"
VERSION = "3.4.3"
DESCRIPTION = "Dr.PDF - PDF editor and file converter"


def settings():
    """The app's saved settings (registry on Windows). Uses QSettings.defaultFormat(), so
    tests can redirect it to a scratch file with QSettings.setDefaultFormat(IniFormat)."""
    from PySide6.QtCore import QSettings
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, APP_NAME, APP_NAME)
