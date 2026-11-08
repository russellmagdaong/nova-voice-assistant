"""The orb mascot: a wobbling wireframe sphere (blue -> violet -> magenta) with a soft glow and
drifting specks, drawn in code. It lives inside the expanded notch; collapsed, the notch is a
thin pill."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

NAME = "Orb"
COLORS = ("#2a7bff", "#8b4dff", "#ff3dcf")      # top left -> bottom right
GREY = ("#8a8f9c", "#767b88", "#9a9fab")
RED = ("#ff6b6b", "#ff3b5c", "#ff8a6b")
ORANGE = ("#ffb347", "#ff9f43", "#ffcf6b")

_N_LAT, _N_LON, _SEG = 8, 14, 32
_LAT = np.linspace(-math.pi / 2, math.pi / 2, _N_LAT + 2)[1:-1]          # rings (no poles)
_LON = np.linspace(0, 2 * math.pi, _N_LON, endpoint=False)                # meridians
_RING = np.linspace(0, 2 * math.pi, _SEG + 1)
_MERID = np.linspace(-math.pi / 2, math.pi / 2, _SEG // 2 + 1)
_rng = np.random.default_rng(7)
_SPECK_LAT = _rng.uniform(-1.3, 1.3, 40)
_SPECK_LON = _rng.uniform(0, 2 * math.pi, 40)
_SPECK_R = _rng.uniform(1.12, 1.42, 40)
_SPECK_PH = _rng.uniform(0, 2 * math.pi, 40)


def style(state: str, mic: float, spk: float) -> tuple[float, float, float, tuple[str, str, str]]:
    """(wobble, spin, glow, colours) for a notch state."""
    if state == "listening":
        return 0.05 + 0.3 * mic, 0.6, 0.7 + 0.3 * min(1.0, mic * 2), COLORS
    if state == "thinking":
        return 0.08, 1.8, 0.75, COLORS
    if state == "speaking":
        return 0.05 + 0.3 * spk, 0.7, 0.65 + 0.35 * min(1.0, spk * 2), COLORS
    if state in ("muted", "loading"):
        return 0.03, 0.12, 0.2, GREY
    if state == "error":
        return 0.06, 0.4, 0.6, RED
    if state == "alert":
        return 0.1, 1.0, 0.9, ORANGE
    return 0.05, 0.3, 0.5, COLORS


def _xyz(lat, lon, t, wobble, spin):
    """Points on the wobbling sphere, turned and tilted towards the viewer."""
    r = 1 + wobble * (0.6 * np.sin(3 * lon + 1.7 * t) * np.cos(2 * lat - 1.1 * t)
                      + 0.4 * np.sin(5 * lat + 2 * lon + 2.3 * t))
    x = r * np.cos(lat) * np.cos(lon)
    y = r * np.sin(lat)
    z = r * np.cos(lat) * np.sin(lon)
    a = spin * t
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    tilt = 0.45
    y, z = y * math.cos(tilt) - z * math.sin(tilt), y * math.sin(tilt) + z * math.cos(tilt)
    return x, y, z


def _gradient(c: QPointF, R: float, colors, alpha: float) -> QLinearGradient:
    g = QLinearGradient(QPointF(c.x() - R, c.y() - R), QPointF(c.x() + R, c.y() + R))
    for i, col in enumerate(colors):
        q = QColor(col)
        q.setAlphaF(alpha)
        g.setColorAt(i / (len(colors) - 1), q)
    return g


def paint(qp: QPainter, c: QPointF, R: float, t: float, wobble: float = 0.05, spin: float = 0.3,
          glow: float = 0.5, colors=COLORS):
    """Draw the orb centred on `c` with radius `R` at time `t` (seconds)."""
    qp.save()
    qp.setRenderHint(QPainter.Antialiasing)
    # Glow behind it.
    g = QRadialGradient(c, R * 1.9)
    mid = QColor(colors[1])
    mid.setAlphaF(0.45 * glow)
    g.setColorAt(0.0, mid)
    g.setColorAt(0.55, QColor(mid.red(), mid.green(), mid.blue(), int(60 * glow)))
    g.setColorAt(1.0, QColor(mid.red(), mid.green(), mid.blue(), 0))
    qp.setPen(Qt.NoPen)
    qp.setBrush(g)
    qp.drawEllipse(c, R * 1.9, R * 1.9)

    # The mesh, split by depth: the far side faint, the near side bright. All lines are worked
    # out at once (numpy); each line is then added as a few runs of the same depth.
    lat = np.concatenate([np.repeat(_LAT, len(_RING)), np.tile(_MERID, len(_LON))])
    lon = np.concatenate([np.tile(_RING, len(_LAT)), np.repeat(_LON, len(_MERID))])
    x, y, z = _xyz(lat, lon, t, wobble, spin)
    px, py = (c.x() + R * x).tolist(), (c.y() - R * y).tolist()
    depth = np.digitize(z, (-0.25, 0.3)).tolist()                     # 0 far, 1 middle, 2 near
    paths = [QPainterPath(), QPainterPath(), QPainterPath()]
    start = 0
    for n in [len(_RING)] * len(_LAT) + [len(_MERID)] * len(_LON):
        i = start
        while i < start + n - 1:
            d, j = depth[i], i
            p = paths[d]
            p.moveTo(px[i], py[i])
            while j < start + n - 1 and depth[j] == d:
                j += 1
                p.lineTo(px[j], py[j])
            i = j
        start += n
    qp.setBrush(Qt.NoBrush)
    for path, alpha, width in zip(paths, (0.2, 0.5, 0.95), (0.6, 0.7, 0.85)):
        qp.setPen(QPen(_gradient(c, R, colors, alpha), width))
        qp.drawPath(path)

    # Specks drifting around it (twinkling: two brightness groups).
    x, y, z = _xyz(_SPECK_LAT, _SPECK_LON, t * 0.6, 0.0, spin * 0.7)
    bright = (np.sin(t * 2.2 + _SPECK_PH) > 0) & (z > 0)
    sx, sy = (c.x() + R * _SPECK_R * x).tolist(), (c.y() - R * _SPECK_R * y).tolist()
    for group, alpha in ((~bright, 0.35), (bright, 0.85)):
        pts = [QPointF(sx[i], sy[i]) for i in np.nonzero(group)[0]]
        qp.setPen(QPen(_gradient(c, R * 1.4, colors, alpha), 1.3, Qt.SolidLine, Qt.RoundCap))
        qp.drawPoints(pts)
    qp.restore()


def icon_image(size: int, muted: bool = False) -> QImage:
    """The orb for the tray: a gradient ball with a few mesh lines and a light outline."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    qp = QPainter(img)
    qp.setRenderHint(QPainter.Antialiasing)
    colors = GREY if muted else COLORS
    c = QPointF(size / 2, size / 2)
    R = size * 0.42
    qp.setPen(Qt.NoPen)
    qp.setBrush(_gradient(c, R, colors, 1.0))
    qp.drawEllipse(c, R, R)
    qp.setBrush(Qt.NoBrush)
    qp.setPen(QPen(QColor(255, 255, 255, 110), max(0.6, size / 40)))
    qp.drawLine(QPointF(c.x() - R, c.y()), QPointF(c.x() + R, c.y()))   # equator
    qp.drawEllipse(c, R, R * 0.45)                                       # a ring, seen at an angle
    qp.drawEllipse(c, R * 0.45, R)                                       # a meridian
    qp.setPen(QPen(QColor(255, 255, 255, 235 if not muted else 160), max(1.0, size / 16)))
    qp.drawEllipse(c, R + size / 32, R + size / 32)               # outline so it shows on the taskbar
    qp.end()
    return img
