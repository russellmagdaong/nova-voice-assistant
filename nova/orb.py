"""The orb mascot: a glowing energy core inside a living mesh, in the colour theme's three colours
(themes.py), drawn in code. It lives inside the expanded notch; collapsed, the notch is a thin pill.

The mesh is a network of points scattered over a sphere, each linked to its nearest neighbours,
so it has no latitude/longitude lines (it doesn't look like a globe). The points drift on smooth
waves, so the outline keeps gently changing even at rest, and swell with your voice and NOVA's.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, Qt
from . import themes
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

NAME = "Orb"
GREY = ("#8a8f9c", "#767b88", "#9a9fab")
RED = ("#ff6b6b", "#ff3b5c", "#ff8a6b")
ORANGE = ("#ffb347", "#ff9f43", "#ffcf6b")


def _mesh(n: int = 64, k: int = 5, seed: int = 11):
    """Points spread evenly but irregularly over a sphere (a jittered Fibonacci spiral), and the
    links from each point to its k nearest neighbours."""
    rng = np.random.default_rng(seed)
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    p = np.stack([np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)], axis=1)
    p += rng.normal(0, 0.09, p.shape)
    p /= np.linalg.norm(p, axis=1, keepdims=True)
    d = ((p[:, None, :] - p[None, :, :]) ** 2).sum(-1)
    near = np.argsort(d, axis=1)[:, 1:k + 1]
    links = sorted({(min(a, b), max(a, b)) for a in range(n) for b in near[a].tolist()})
    return p, np.array(links), rng.uniform(0, 2 * math.pi, n)


_P, _LINKS, _PHASE = _mesh()
_rng = np.random.default_rng(5)
_SPECKS = _rng.normal(size=(26, 3))
_SPECKS /= np.linalg.norm(_SPECKS, axis=1, keepdims=True)
_SPECK_R = _rng.uniform(1.15, 1.45, len(_SPECKS))
_SPECK_PH = _rng.uniform(0, 2 * math.pi, len(_SPECKS))


def style(state: str, mic: float, spk: float) -> tuple[float, float, float, tuple[str, str, str]]:
    """(wobble, spin, glow, colours) for a notch state."""
    if state == "listening":
        return 0.09 + 0.35 * mic, 0.55, 0.75 + 0.25 * min(1.0, mic * 2), themes.current_colors()
    if state in ("thinking", "searching"):
        return 0.1, 1.6, 0.8, themes.current_colors()
    if state == "speaking":
        return 0.09 + 0.35 * spk, 0.6, 0.7 + 0.3 * min(1.0, spk * 2), themes.current_colors()
    if state in ("muted", "loading"):
        return 0.05, 0.12, 0.25, GREY
    if state == "error":
        return 0.08, 0.35, 0.6, RED
    if state == "alert":
        return 0.14, 1.0, 0.9, ORANGE
    return 0.08, 0.3, 0.55, themes.current_colors()


def _shape(p: np.ndarray, t: float, wobble: float, spin: float):
    """The mesh's points at time t: pushed in and out by slow waves, turned and tipped towards the
    viewer. Returns x, y, z (z towards the viewer)."""
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    r = 1 + wobble * (0.55 * np.sin(2.3 * x + 1.3 * t) * np.cos(1.9 * y - 0.9 * t)
                      + 0.45 * np.sin(2.7 * z + 1.6 * y + 1.7 * t))
    x, y, z = x * r, y * r, z * r
    a = spin * t
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    tilt = 0.35 + 0.18 * math.sin(0.23 * t)                 # slowly rocks, so no fixed "equator"
    y, z = y * math.cos(tilt) - z * math.sin(tilt), y * math.sin(tilt) + z * math.cos(tilt)
    return x, y, z


def _gradient(c: QPointF, R: float, colors, alpha: float) -> QLinearGradient:
    g = QLinearGradient(QPointF(c.x() - R, c.y() - R), QPointF(c.x() + R, c.y() + R))
    for i, col in enumerate(colors):
        q = QColor(col)
        q.setAlphaF(alpha)
        g.setColorAt(i / (len(colors) - 1), q)
    return g


def _radial(c: QPointF, R: float, stops) -> QRadialGradient:
    g = QRadialGradient(c, R)
    for at, col, a in stops:
        q = QColor(col)
        q.setAlphaF(max(0.0, min(1.0, a)))
        g.setColorAt(at, q)
    return g


def paint(qp: QPainter, c: QPointF, R: float, t: float, wobble: float = 0.08, spin: float = 0.3,
          glow: float = 0.55, colors=None, look: tuple[float, float] = (0.0, 0.0)):
    """Draw the orb centred on `c` with radius `R` at time `t` (seconds). `look` (-1..1 each way)
    leans its bright core that way (towards the cursor, in NOVA's window)."""
    colors = colors or themes.current_colors()
    qp.save()
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(Qt.NoPen)
    pulse = 0.5 + 0.5 * math.sin(t * 1.4)
    # Halo behind it.
    qp.setBrush(_radial(c, R * 1.9, [(0.0, colors[1], 0.42 * glow), (0.5, colors[1], 0.2 * glow),
                                     (1.0, colors[1], 0.0)]))
    qp.drawEllipse(c, R * 1.9, R * 1.9)
    # The core: a bright heart that breathes (and swells with the voice, via glow).
    core = R * (0.5 + 0.12 * pulse + 0.25 * max(0.0, glow - 0.6))
    cc = QPointF(c.x() + look[0] * R * 0.2, c.y() + look[1] * R * 0.2)
    qp.setBrush(_radial(cc, core, [(0.0, "#ffffff", 0.75 * glow), (0.3, colors[2], 0.55 * glow),
                                   (0.7, colors[0], 0.25 * glow), (1.0, colors[0], 0.0)]))
    qp.drawEllipse(cc, core, core)

    # The mesh, split by depth: the far side faint, the near side bright.
    x, y, z = _shape(_P, t, wobble, spin)
    persp = 1 + 0.12 * z
    px, py = c.x() + R * x * persp, c.y() - R * y * persp
    a, b = _LINKS[:, 0], _LINKS[:, 1]
    depth = np.digitize((z[a] + z[b]) / 2, (-0.3, 0.3))       # 0 far, 1 middle, 2 near
    ax, ay, bx, by = px[a].tolist(), py[a].tolist(), px[b].tolist(), py[b].tolist()
    paths = [QPainterPath(), QPainterPath(), QPainterPath()]
    for i, d in enumerate(depth.tolist()):
        paths[d].moveTo(ax[i], ay[i])
        paths[d].lineTo(bx[i], by[i])
    qp.setBrush(Qt.NoBrush)
    for path, alpha, width in zip(paths, (0.16, 0.42, 0.85), (0.55, 0.7, 0.85)):
        qp.setPen(QPen(_gradient(c, R, colors, alpha), width))
        qp.drawPath(path)

    # The points: small and faint at the back, brighter in front; a few twinkle white.
    front = z > 0.15
    twinkle = front & (np.sin(t * 1.8 + _PHASE * 3) > 0.82)
    pxl, pyl = px.tolist(), py.tolist()
    for group, alpha, size in (((~front), 0.35, 1.1), (front & ~twinkle, 0.95, 1.7)):
        pts = [QPointF(pxl[i], pyl[i]) for i in np.nonzero(group)[0]]
        qp.setPen(QPen(_gradient(c, R, colors, alpha), size, Qt.SolidLine, Qt.RoundCap))
        qp.drawPoints(pts)
    pts = [QPointF(pxl[i], pyl[i]) for i in np.nonzero(twinkle)[0]]
    if pts:
        qp.setPen(QPen(QColor(255, 255, 255, 230), 2.0, Qt.SolidLine, Qt.RoundCap))
        qp.drawPoints(pts)

    # Specks drifting around it.
    sx, sy, sz = _shape(_SPECKS, t * 0.5, 0.0, spin * 0.6)
    lit = (np.sin(t * 2.0 + _SPECK_PH) > 0) & (sz > -0.2)
    sxl, syl = (c.x() + R * _SPECK_R * sx).tolist(), (c.y() - R * _SPECK_R * sy).tolist()
    for group, alpha in ((~lit, 0.3), (lit, 0.8)):
        pts = [QPointF(sxl[i], syl[i]) for i in np.nonzero(group)[0]]
        qp.setPen(QPen(_gradient(c, R * 1.4, colors, alpha), 1.2, Qt.SolidLine, Qt.RoundCap))
        qp.drawPoints(pts)
    qp.restore()


def _sparkle(c: QPointF, s: float) -> QPainterPath:
    """A four-pointed star (a "nova")."""
    w = s * 0.26
    path = QPainterPath(QPointF(c.x(), c.y() - s))
    for px, py in ((w, -w), (s, 0), (w, w), (0, s), (-w, w), (-s, 0), (-w, -w), (0, -s)):
        path.lineTo(QPointF(c.x() + px, c.y() + py))
    return path


def icon_image(size: int, muted: bool = False) -> QImage:
    """The orb for the tray: a gradient ball with a bright four-pointed star at its heart."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    qp = QPainter(img)
    qp.setRenderHint(QPainter.Antialiasing)
    colors = GREY if muted else themes.current_colors()
    c = QPointF(size / 2, size / 2)
    R = size * 0.47
    qp.setPen(Qt.NoPen)
    qp.setBrush(_gradient(c, R, colors, 1.0))
    qp.drawEllipse(c, R, R)
    qp.setBrush(_radial(c, R, [(0.0, "#ffffff", 0.55 if not muted else 0.25), (1.0, "#ffffff", 0.0)]))
    qp.drawEllipse(c, R, R)
    qp.setBrush(QColor(255, 255, 255, 245 if not muted else 170))
    qp.drawPath(_sparkle(c, R * 0.72))
    qp.end()
    return img
