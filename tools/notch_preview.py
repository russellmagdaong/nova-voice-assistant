"""Render the notch in each state, for each mascot, without talking to it.

    .venv\\Scripts\\python.exe tools\\notch_preview.py

Writes samples\\notch_preview_<mascot>.png (left column: your Windows theme, right: the other one)
and samples\\mascot_frames.png (every frame of the pixel mascots). The card is drawn translucent
here; on screen Windows also blurs what's behind it.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nova import pixelbot, pixelcat, ui  # noqa: E402

Q = "What's the weather going to be like in Auckland tomorrow afternoon?"
SCENES = [
    ("idle", ui.IDLE, dict()),
    ("hover", ui.IDLE, dict(hover=True)),
    ("live caption while you talk", ui.LISTENING,
     dict(live="What's the weather going to be like in Auckland tomorrow after", mic=0.35)),
    ("thinking", ui.THINKING, dict(you=Q, t=0.55)),
    ("speaking", ui.SPEAKING, dict(you=Q, spk=0.3, frame="talk",
                                   answer="Tomorrow afternoon in Auckland looks mostly sunny, around nineteen "
                                          "degrees, with a light southwest breeze. You probably won't need an "
                                          "umbrella, but take a light jacket for the evening.")),
    ("your turn (time left to reply)", ui.LISTENING, dict(you="Open Spotify", answer="Opening Spotify now.",
                                                          countdown=0.62)),
    ("muted", ui.MUTED, dict()),
    ("error", ui.ERROR, dict(answer="Couldn't reach Claude. Try again in a moment.")),
]
CELL_W, CELL_H, EDGE = 400, 250, 24      # EDGE: a strip under the screen edge


def render(n: ui.Notch, qp: QPainter, x0: int, y0: int, title: str, state: str, o: dict, light: bool):
    n.theme = ui.Theme(light, None)
    if n.sprite:
        n.sprite.rim = not light
    n.state, n.state_t = state, time.monotonic() - 5
    n.hover_main, n.hover_card, n.hover_until = o.get("hover", False), False, 0.0
    n.you.clear(); n.answer.clear(); n.live = False; n.countdown = o.get("countdown", -1.0)
    if "live" in o:
        n.you.set(o["live"], animate=False); n.live = True
    if "you" in o:
        n.you.set(o["you"], animate=False)
    if "answer" in o:
        n.answer.set(o["answer"], animate=False)
    n.mic_l, n.spk_l = o.get("mic", 0.0), o.get("spk", 0.0)
    level = n.mic_l or n.spk_l
    n.bars = [min(1.0, level * 2.2 * f) for f in (0.7, 1.0, 0.55, 0.85)]
    n.p = 1.0 if n._want_card() else 0.0
    n.pv = 0.0
    if n.sprite:
        n.peek, n.peek_v = n._peek_target(), 0.0
        n.sprite.cur = o.get("frame") or n.sprite._pick(state, n.hovered, 0.0)
    n.h, n.cw = n._content_height(), n._content_width()
    n.t0 = time.monotonic() - o.get("t", 0.0) * 1.6
    # A plain desktop, the notch's window on its bottom edge, and the card's window above it.
    qp.fillRect(QRectF(x0, y0, CELL_W, CELL_H), QColor("#dfe6ee") if light else QColor("#1f2a36"))
    qp.fillRect(QRectF(x0, y0 + CELL_H - EDGE, CELL_W, EDGE), QColor("#eeeeee") if light else QColor("#1c1c1c"))
    mx, my = x0 + (CELL_W - n.W) // 2, y0 + CELL_H - EDGE - n.H
    n.render(qp, QPoint(mx, my), renderFlags=n.RenderFlag.DrawChildren)
    if n.p > 0.01:
        r = n._panel_rect() if n.orb_mode else n._card_rect()
        card = QImage(math.ceil(r.width()), math.ceil(r.height()), QImage.Format_ARGB32_Premultiplied)
        card.fill(Qt.transparent)
        cp = QPainter(card)
        cp.setRenderHint(QPainter.Antialiasing)
        cp.setRenderHint(QPainter.TextAntialiasing)
        n.paint_card(cp, QRectF(0, 0, card.width(), card.height()), blur=False)
        cp.end()
        qp.drawImage(QPointF(mx + r.x(), my + r.y()), card)
    qp.setPen(QColor(0, 0, 0, 160) if light else QColor(255, 255, 255, 170))
    qp.drawText(QRectF(x0 + 10, y0 + 6, CELL_W, 16), Qt.AlignLeft, title)


def frame_sheet(path: Path, scale: int = 6):
    """Every frame of each pixel mascot, big, on dark (with its outline) and light backgrounds."""
    arts = [pixelcat, pixelbot]
    cw = max(a.COLS for a in arts) * scale + 12
    ch = max(a.ROWS for a in arts) * scale + 24
    n = max(len(a.FRAMES) for a in arts)
    img = QImage(cw * n, ch * 2 * len(arts), QImage.Format_ARGB32_Premultiplied)
    qp = QPainter(img)
    for ai, art in enumerate(arts):
        for row, (bg, rim) in enumerate([("#1f2a36", True), ("#dfe6ee", False)]):
            y = (ai * 2 + row) * ch
            qp.fillRect(QRectF(0, y, img.width(), ch), QColor(bg))
            for i, name in enumerate(art.FRAMES):
                a = art.image(name, rim)
                qp.drawImage(QPointF(i * cw + 6, y + 4),
                             a.scaled(a.width() * scale, a.height() * scale, Qt.IgnoreAspectRatio,
                                      Qt.FastTransformation))
                qp.setPen(QColor("#999999"))
                qp.drawText(QPointF(i * cw + 6, y + ch - 6), name)
    qp.end()
    img.save(str(path))


def main():
    out_dir = ROOT / "samples"
    out_dir.mkdir(exist_ok=True)
    app = QApplication(sys.argv)  # noqa: F841
    frame_sheet(out_dir / "mascot_frames.png")
    n = ui.Notch(ui.Bridge(), lambda: (0.0, 0.0))
    n.tick.stop()
    mine = n.theme.light
    for mascot in ui.MASCOTS:
        n.set_mascot(mascot)
        img = QImage(CELL_W * 2 + 6, (CELL_H + 6) * len(SCENES), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("#888888"))
        qp = QPainter(img)
        for i, (title, state, o) in enumerate(SCENES):
            render(n, qp, 0, i * (CELL_H + 6), title, state, o, mine)
            render(n, qp, CELL_W + 6, i * (CELL_H + 6), title, state, o, not mine)
        qp.end()
        path = out_dir / f"notch_preview_{mascot}.png"
        img.save(str(path))
        print(path)


if __name__ == "__main__":
    main()
