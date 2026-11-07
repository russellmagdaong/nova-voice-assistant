"""Render the notch in each state to one image (for checking the design without talking to it).
Left column: your Windows theme; right column: the other one (light/dark). Also writes every
frame of the pixel cat to samples\\cat_frames.png.

    .venv\\Scripts\\python.exe tools\\notch_preview.py [out.png]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nova import pixelcat, ui  # noqa: E402

Q = "What's the weather going to be like in Auckland tomorrow afternoon?"
SCENES = [
    ("idle", ui.IDLE, dict()),
    ("idle, glancing", ui.IDLE, dict(frame="look_left")),
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
TASKBAR = 48


def render(n: ui.Notch, qp: QPainter, x0: int, y0: int, title: str, state: str, o: dict, light: bool):
    n.theme = ui.Theme(light, None)
    n.state, n.state_t = state, time.monotonic() - 5
    n.hovered = o.get("hover", False)
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
    n.peek, n.peek_v = n._peek_target(), 0.0
    n.h, n.cw = n._content_height(), n._content_width()
    n.cat.rim = not light
    n.cat.cur = o.get("frame") or n.cat._pick(state, n.hovered, 0.0)
    n.t0 = time.monotonic() - o.get("t", 0.0) * 1.6
    # A plain desktop with the taskbar under the notch.
    desk = QColor("#dfe6ee") if light else QColor("#1f2a36")
    qp.fillRect(QRectF(x0, y0, n.W, n.H), desk)
    qp.fillRect(QRectF(x0, y0 + n.H, n.W, TASKBAR), QColor("#eeeeee") if light else QColor("#1c1c1c"))
    n.render(qp, QPoint(x0, y0), renderFlags=n.RenderFlag.DrawChildren)
    qp.setPen(QColor(0, 0, 0, 160) if light else QColor(255, 255, 255, 170))
    qp.drawText(QRectF(x0 + 10, y0 + 6, n.W, 16), Qt.AlignLeft, title)


def frame_sheet(path: Path, scale: int = 8):
    """Every frame of the pixel cat, big, on dark (with its outline) and light backgrounds."""
    names = list(pixelcat.FRAMES)
    cw, ch = pixelcat.COLS * scale + 16, pixelcat.ROWS * scale + 28
    img = QImage(cw * len(names), ch * 2, QImage.Format_ARGB32_Premultiplied)
    qp = QPainter(img)
    for row, (bg, rim) in enumerate([("#1f2a36", True), ("#dfe6ee", False)]):
        qp.fillRect(QRectF(0, row * ch, img.width(), ch), QColor(bg))
        for i, name in enumerate(names):
            art = pixelcat.image(name, rim)
            qp.drawImage(QPointF(i * cw + 8, row * ch + 4),
                         art.scaled(art.width() * scale, art.height() * scale, Qt.IgnoreAspectRatio,
                                    Qt.FastTransformation))
            qp.setPen(QColor("#999999"))
            qp.drawText(QPointF(i * cw + 8, row * ch + ch - 8), name)
    qp.end()
    img.save(str(path))


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "notch_preview.png"
    app = QApplication(sys.argv)  # noqa: F841
    bridge = ui.Bridge()
    n = ui.Notch(bridge, lambda: (0.0, 0.0))
    n.tick.stop()
    frame_sheet(out.parent / "cat_frames.png")
    mine = n.theme.light
    cell_h = n.H + TASKBAR + 6
    img = QImage(n.W * 2 + 6, cell_h * len(SCENES), QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#888888"))
    qp = QPainter(img)
    for i, (title, state, o) in enumerate(SCENES):
        render(n, qp, 0, i * cell_h, title, state, o, mine)
        render(n, qp, n.W + 6, i * cell_h, title, state, o, not mine)
    qp.end()
    out.parent.mkdir(exist_ok=True)
    img.save(str(out))
    print(out)


if __name__ == "__main__":
    main()
