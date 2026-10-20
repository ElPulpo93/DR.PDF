"""Dark / light appearance. The style sheet is written once (in dark colours); the light
theme is produced by swapping each colour for its light counterpart."""
import re

import icons

CHOICES = [("dark", "Dark"), ("light", "Light"), ("system", "Match Windows")]

# dark colour -> light colour
LIGHT = {
    "#0F1117": "#F3F4F8",   # window background
    "#131620": "#FFFFFF",   # bars, panels
    "#161923": "#FFFFFF",   # lists, drop zone, dialogs
    "#16251E": "#E8F7EE",   # 'offline' badge
    "#171A26": "#F7F8FB",   # properties bar
    "#181C28": "#F3F4F8",   # comment cards
    "#1A1D33": "#EEF0FF",   # drop zone hover
    "#1E2230": "#F1F2F6",   # buttons, inputs, menus
    "#1F2333": "#E1E4EB",   # panel borders
    "#22263A": "#E8EAF2",   # hover / selected rows
    "#23273A": "#E1E4EB",
    "#23432F": "#B9E5C9",
    "#262B3D": "#E6E8EF",   # button hover
    "#2A2F42": "#DCDFE7",   # separators
    "#2A2F45": "#DADDE8",   # pressed
    "#2A2F55": "#E7E9FF",   # soft accent (selected page, accent buttons)
    "#2D3244": "#D3D6E0",   # borders, scroll bars
    "#343A6B": "#DADDFF",
    "#3B4260": "#C3C7D3",
    "#4A50A0": "#A9AEF5",
    "#555B6E": "#A3A8B6",   # disabled
    "#5A6070": "#A3A8B6",
    "#5FD39A": "#1E8E55",   # green text
    "#6A7085": "#8B90A0",
    "#7C83FF": "#5B63F0",   # accent
    "#8A90A2": "#5F6576",   # secondary text
    "#8F95FF": "#6E76FF",
    "#C9CDDA": "#3A3F4E",
    "#D5D8E2": "#2B3040",   # tool button text
    "#E7E9F0": "#1C2030",   # main text
    "#FF6B7A": "#D93448",   # error text
    "#FFFFFE": "#2B2F8F",   # text on the soft accent colour
}

INLINE = {  # colours used from code
    "dark": {"canvas": "#1A1D27", "thumb": "#2A2F3F", "text": "#E7E9F0", "ok": "#5FD39A",
             "error": "#FF6B7A", "icon": "#C9CDDA", "icon_disabled": "#555B6E",
             "accent": "#7C83FF", "muted": "#8A90A2", "soft_on": "#FFFFFF"},
    "light": {"canvas": "#E4E6EC", "thumb": "#E1E4EB", "text": "#1C2030", "ok": "#1E8E55",
              "error": "#D93448", "icon": "#3A3F4E", "icon_disabled": "#A3A8B6",
              "accent": "#5B63F0", "muted": "#5F6576", "soft_on": "#2B2F8F"},
}

current = "dark"


def windows_prefers_light():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k)
        return bool(v)
    except OSError:
        return False


def resolve(choice):
    if choice == "system":
        return "light" if windows_prefers_light() else "dark"
    return "light" if choice == "light" else "dark"


def stylesheet(dark_style, name):
    if name != "light":
        return dark_style
    return re.sub(r"#[0-9A-Fa-f]{6}", lambda m: LIGHT.get(m.group(0).upper(), m.group(0)),
                  dark_style)


def color(key):
    return INLINE[current][key]


def apply(app, dark_style, choice):
    """Switch the whole app to `choice` ('dark', 'light' or 'system'), live."""
    global current
    current = resolve(choice)
    icons.set_palette(color("icon"), color("icon_disabled"), color("soft_on"))
    app.setStyleSheet(stylesheet(dark_style, current))
    for w in app.allWidgets():
        name = w.property("icon_name")
        if name and hasattr(w, "setIcon"):
            w.setIcon(icons.icon(name, soft=bool(w.property("icon_soft"))))
        refresh = getattr(w, "apply_theme", None)
        if callable(refresh):
            refresh()
    return current
