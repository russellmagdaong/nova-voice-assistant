"""NOVA's logo and its colour themes (NOVA settings > Appearance > Colour theme).

The logo is "Slingshot" (assets/nova.svg, and nova-small.svg simplified for 16-24 px): a planet
with an orbit swinging around it. A theme is three colours: start, middle and end of a gradient.
The SVGs are drawn with three placeholder colours that are swapped for the theme's, and the orb's
mesh, glow and pill use the same three. "Windows accent" takes them from your Windows accent colour.
"""
from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PLACEHOLDERS = ("#0000F1", "#0000F2", "#0000F3")      # in assets/nova.svg and nova-small.svg
LOGO, LOGO_SMALL = ASSETS / "nova.svg", ASSETS / "nova-small.svg"

THEMES: dict[str, tuple[str, tuple[str, str, str] | None]] = {
    "sunrise":  ("Sunrise",  ("#ff7a1a", "#ff4d5a", "#ffb020")),
    "ocean":    ("Ocean",    ("#1d6cf2", "#10a5e3", "#1fd1c1")),
    "aurora":   ("Aurora",   ("#2fd18c", "#20c4d8", "#3b82f6")),
    "forest":   ("Forest",   ("#0f8a6a", "#2fb35a", "#9bd43c")),
    "graphite": ("Graphite", ("#454c58", "#7f8999", "#aeb6c1")),
    "nebula":   ("Nebula",   ("#2a7bff", "#8b4dff", "#ff3dcf")),
    "accent":   ("Windows accent", None),
}
DEFAULT = "sunrise"

current = DEFAULT
_colors: tuple[str, str, str] = THEMES[DEFAULT][1]
_icons: dict = {}


def accent_colors() -> tuple[str, str, str]:
    """Three shades of the Windows accent colour (dark -> accent -> light)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            pal = bytes(winreg.QueryValueEx(k, "AccentPalette")[0])   # 8 shades, lightest first
        shade = lambda i: "#%02x%02x%02x" % (pal[i * 4], pal[i * 4 + 1], pal[i * 4 + 2])  # noqa: E731
        return shade(5), shade(3), shade(1)
    except (OSError, ImportError, IndexError):
        return THEMES[DEFAULT][1]


def colors(name: str | None = None) -> tuple[str, str, str]:
    name = name or current
    if name == "accent":
        return accent_colors()
    return (THEMES.get(name) or THEMES[DEFAULT])[1]


def set_current(name: str):
    global current, _colors
    current = name if name in THEMES else DEFAULT
    _colors = colors(current)


def current_colors() -> tuple[str, str, str]:
    return _colors


def recolor(svg: str, cols: tuple[str, str, str]) -> str:
    for ph, c in zip(PLACEHOLDERS, cols):
        svg = re.sub(re.escape(ph), c, svg, flags=re.I)
    return svg


def logo_svg(size: int, name: str | None = None) -> bytes:
    """The logo's SVG in a theme's colours (the simplified version at 16-24 px)."""
    f = LOGO_SMALL if size <= 24 and LOGO_SMALL.exists() else LOGO
    return recolor(f.read_text(encoding="utf-8"), colors(name)).encode("utf-8")


def logo_image(size: int, name: str | None = None):
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    qp = QPainter(img)
    qp.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    QSvgRenderer(QByteArray(logo_svg(size, name))).render(qp, QRectF(0, 0, size, size))
    qp.end()
    return img


def logo_icon(name: str | None = None):
    """NOVA's logo as a QIcon (in the current theme by default), at every size."""
    from PySide6.QtGui import QIcon, QPixmap
    key = (name or current, colors(name))
    if key not in _icons:
        icon = QIcon()
        for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
            icon.addPixmap(QPixmap.fromImage(logo_image(s, name)))
        _icons[key] = icon
    return _icons[key]
