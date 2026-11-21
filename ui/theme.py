"""Colours and theme switching. Each colour is a (light, dark) pair."""

from PySide6.QtGui import QColor
from qfluentwidgets import Theme, setTheme, setThemeColor

ACCENT = "#e5484d"

MUTED = ("#6b7280", "#9a9eaa")
GOOD = ("#15803d", "#4ade80")
BAD = ("#b91c1c", "#f87171")
WARN = ("#b45309", "#fbbf24")
TEXT = ("#16171a", "#ececf1")

# Settings value -> Fluent theme, and the names shown in Settings
THEMES = {"light": Theme.LIGHT, "dark": Theme.DARK, "system": Theme.AUTO}
THEME_NAMES = {"light": "Light", "dark": "Dark", "system": "Use system setting"}


def apply_theme(name):
    """Switch the whole app to "light", "dark" or "system"."""

    setTheme(THEMES.get(name, Theme.AUTO))
    setThemeColor(ACCENT)


def paint(label, color):
    """Colour a Fluent label with a (light, dark) pair."""

    label.setTextColor(QColor(color[0]), QColor(color[1]))
