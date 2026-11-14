"""A few Windows 11 (Fluent) style building blocks for NOVA's settings window: the theme's
colours, a stylesheet for cards and headings, a sidebar button, a toggle switch, a settings row,
and the Mica backdrop. Inputs (text boxes, combo boxes, buttons) keep Qt's own windows11 style.

Icons are glyphs from the Segoe Fluent Icons font that ships with Windows 11."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

ICON_FONT = "Segoe Fluent Icons"

# Segoe Fluent Icons code points.
G = {"home": "", "timer": "", "reminder": "", "notes": "", "apps": "",
     "appearance": "", "voice": "", "assistant": "", "privacy": "",
     "add": "", "delete": "", "edit": "", "save": "", "cancel": "",
     "restore": "", "search": "", "refresh": "", "lock": "", "play": "",
     "folder": "", "info": "", "check": "", "repeat": "", "list": "",
     "globe": "", "pc": "", "bin": "", "mic": "", "warning": ""}


def ui_font(size: float, weight: QFont.Weight = QFont.Normal, display: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Display" if display else "Segoe UI Variable Text", "Segoe UI"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def icon_font(size: float) -> QFont:
    f = QFont(ICON_FONT)
    f.setPointSizeF(size)
    return f


@dataclass
class Colors:
    dark: bool
    accent: QColor

    @property
    def text(self) -> str:
        return "#ffffff" if self.dark else "#1a1a1a"

    @property
    def text2(self) -> str:
        return "#c8c8c8" if self.dark else "#5f5f5f"

    @property
    def card(self) -> str:
        return "rgba(255,255,255,0.055)" if self.dark else "rgba(255,255,255,0.72)"

    @property
    def card_hover(self) -> str:
        return "rgba(255,255,255,0.085)" if self.dark else "rgba(255,255,255,0.95)"

    @property
    def stroke(self) -> str:
        return "rgba(255,255,255,0.08)" if self.dark else "rgba(0,0,0,0.07)"

    @property
    def solid(self) -> str:                     # window background without Mica
        return "#202020" if self.dark else "#f3f3f3"

    @property
    def critical(self) -> str:
        return "#ff99a4" if self.dark else "#c42b1c"

    def accent_text(self) -> str:
        """Readable text on the accent colour."""
        a = self.accent
        return "#000000" if (0.299 * a.red() + 0.587 * a.green() + 0.114 * a.blue()) > 140 else "#ffffff"


def stylesheet(c: Colors, mica: bool) -> str:
    acc = c.accent.name()
    return f"""
    #root {{ background: {'transparent' if mica else c.solid}; }}
    #content, #page, #scrollbody {{ background: transparent; }}
    QScrollArea {{ background: transparent; border: none; }}
    #card {{ background: {c.card}; border: 1px solid {c.stroke}; border-radius: 8px; }}
    #tile {{ background: {c.card}; border: 1px solid {c.stroke}; border-radius: 8px; }}
    #tile:hover {{ background: {c.card_hover}; }}
    #tile[selected="true"] {{ border: 2px solid {acc}; }}
    #title {{ color: {c.text}; }}
    #subtitle {{ color: {c.text}; }}
    #body {{ color: {c.text}; }}
    #caption {{ color: {c.text2}; }}
    #error {{ color: {c.critical}; }}
    #glyph {{ color: {c.text}; }}
    #banner {{ background: {c.card}; border: 1px solid {acc}; border-radius: 8px; }}
    QPushButton#accent {{ background: {acc}; color: {c.accent_text()}; border: 1px solid {acc};
                          border-radius: 5px; padding: 5px 16px; }}
    QPushButton#accent:hover {{ background: {c.accent.lighter(110).name()}; }}
    QPushButton#accent:disabled {{ background: {c.stroke}; color: {c.text2}; border-color: {c.stroke}; }}
    QToolButton#icon {{ border: none; border-radius: 5px; padding: 4px; color: {c.text}; background: transparent; }}
    QToolButton#icon:hover {{ background: {c.card_hover}; }}
    QToolButton#chip {{ border: 1px solid {c.stroke}; border-radius: 13px; padding: 3px 12px; color: {c.text};
                        background: {c.card}; }}
    QToolButton#chip:checked {{ background: {acc}; color: {c.accent_text()}; border-color: {acc}; }}
    """


# ---- window backdrop ----------------------------------------------------------------------------
def apply_backdrop(hwnd: int, dark: bool, mica: bool = True) -> bool:
    """Dark or light title bar to match, and Windows 11's Mica backdrop (the desktop's colours,
    softly, behind the window). True if Mica is on."""
    try:
        from ctypes import wintypes

        class Margins(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(Margins)]
        v = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)            # DWMWA_USE_IMMERSIVE_DARK_MODE
        if not mica:
            return False
        m = Margins(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        b = ctypes.c_int(2)                                                # DWMSBT_MAINWINDOW (Mica)
        return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(b), 4) == 0  # DWMWA_SYSTEMBACKDROP_TYPE
    except (OSError, AttributeError):
        return False


# ---- widgets ------------------------------------------------------------------------------------
def label(text: str, role: str = "body", size: float | None = None, weight=QFont.Normal,
          wrap: bool = False, display: bool = False) -> QLabel:
    sizes = {"title": 20, "subtitle": 13, "body": 10, "caption": 9, "error": 9}
    w = QLabel(text)
    w.setObjectName(role)
    w.setFont(ui_font(size or sizes.get(role, 10),
                      QFont.DemiBold if role in ("title", "subtitle") else weight,
                      display or role == "title"))
    w.setWordWrap(wrap)
    if wrap:
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


def glyph(name: str, size: float = 12) -> QLabel:
    w = QLabel(G.get(name, name))
    w.setObjectName("glyph")
    w.setFont(icon_font(size))
    w.setFixedWidth(int(size * 2.2))
    w.setAlignment(Qt.AlignCenter)
    return w


def icon_button(name: str, tip: str, size: float = 11) -> QToolButton:
    b = QToolButton()
    b.setObjectName("icon")
    b.setText(G.get(name, name))
    b.setFont(icon_font(size))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(QSize(32, 32))
    return b


def accent_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("accent")
    b.setCursor(Qt.PointingHandCursor)
    return b


class Card(QFrame):
    """A rounded panel; add widgets to .lay (vertical)."""

    def __init__(self, spacing: int = 10, margins=(16, 14, 16, 14)):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)


class Toggle(QAbstractButton):
    """A Windows 11 toggle switch (with "On"/"Off" beside it)."""

    def __init__(self, colors: Colors, checked: bool = False):
        super().__init__()
        self.c = colors
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setFixedSize(QSize(80, 24))

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get(self) -> float:
        return self._pos

    def _set(self, v: float):
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0.5, 2.5, 40, 19)
        on = self.isChecked()
        enabled = self.isEnabled()
        acc, txt = QColor(self.c.accent), QColor(self.c.text)
        if not enabled:
            acc.setAlphaF(0.35)
            txt.setAlphaF(0.35)
        path = QPainterPath()
        path.addRoundedRect(track, 9.5, 9.5)
        if on:
            qp.fillPath(path, acc)
        else:
            qp.setPen(txt if enabled else QColor(self.c.text2))
            qp.drawPath(path)
        r = 7 if (self.underMouse() and enabled) else 6
        x = track.left() + 10 + self._pos * 20
        knob = QColor(self.c.accent_text()) if on else txt
        qp.setPen(Qt.NoPen)
        qp.setBrush(knob)
        qp.drawEllipse(QRectF(x - r, track.center().y() - r, 2 * r, 2 * r))
        qp.setPen(txt)
        qp.setFont(ui_font(10))
        qp.drawText(QRectF(50, 0, 30, 24), Qt.AlignVCenter | Qt.AlignLeft, "On" if on else "Off")

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


def setting_row(title: str, desc: str, control: QWidget | None, icon: str | None = None) -> QFrame:
    """Title + description on the left, a control on the right (like Windows Settings)."""
    row = QFrame()
    row.setObjectName("card")
    h = QHBoxLayout(row)
    h.setContentsMargins(16, 12, 16, 12)
    h.setSpacing(14)
    if icon:
        h.addWidget(glyph(icon, 14))
    v = QVBoxLayout()
    v.setSpacing(1)
    v.addWidget(label(title, "body"))
    if desc:
        v.addWidget(label(desc, "caption", wrap=True))
    h.addLayout(v, 1)
    if control is not None:
        h.addWidget(control, 0, Qt.AlignVCenter)
    return row


class NavButton(QAbstractButton):
    """A sidebar entry: icon + label, with the accent pill when selected."""

    def __init__(self, icon: str, text: str, colors: Colors):
        super().__init__()
        self.c, self.icon_glyph = colors, G.get(icon, icon)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self.setMinimumWidth(200)

    def sizeHint(self):
        return QSize(220, 38)

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        bg = QColor(255, 255, 255, 15) if self.c.dark else QColor(0, 0, 0, 10)
        if self.isChecked() or self.underMouse():
            path = QPainterPath()
            path.addRoundedRect(r, 5, 5)
            qp.fillPath(path, bg if not self.isChecked() else QColor(bg.red(), bg.green(), bg.blue(),
                                                                         bg.alpha() + 10))
        if self.isChecked():
            pill = QRectF(r.left(), r.center().y() - 8, 3, 16)
            path = QPainterPath()
            path.addRoundedRect(pill, 1.5, 1.5)
            qp.fillPath(path, QColor(self.c.accent))
        qp.setPen(QColor(self.c.text))
        qp.setFont(icon_font(12))
        qp.drawText(QRectF(r.left() + 12, r.top(), 24, r.height()), Qt.AlignCenter, self.icon_glyph)
        qp.setFont(ui_font(10))
        qp.drawText(QRectF(r.left() + 46, r.top(), r.width() - 50, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                    self.text())

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class Tile(QFrame):
    """A selectable card (mascot / voice pickers)."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        e.accept()                     # (so the release comes here, and the press doesn't reach the window)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def text_width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)
