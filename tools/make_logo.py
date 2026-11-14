"""Render NOVA's logo, Slingshot (assets/nova.svg, nova-small.svg), in the default colour theme into the
files the README and the startup shortcut use. (The app itself draws the logo in the chosen theme.)

    .venv\\Scripts\\python.exe tools\\make_logo.py

Writes to assets/:
  nova-<size>.png    16 ... 512 (16-24 px from nova-small.svg, so the orbit stays crisp)
  nova.ico           Windows icon with every size (the startup shortcut uses it)
  wordmark-dark.png  the mark + "NOVA" for dark backgrounds (README), and wordmark-light.png
Also samples/logo_preview.png: every size on dark and light, next to a fake taskbar.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nova import themes  # noqa: E402

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512]
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(size: int, theme: str = themes.DEFAULT) -> QImage:
    return themes.logo_image(size, theme)


def themes_sheet(path: Path):
    """The logo in every colour theme (for choosing)."""
    names = [k for k in themes.THEMES]
    img = QImage(150 * len(names) + 20, 230, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#1c1c1c"))
    qp = QPainter(img)
    qp.setFont(QFont("Segoe UI", 10))
    for i, k in enumerate(names):
        x = 10 + i * 150
        qp.drawImage(QPointF(x + 11, 14), render(128, k))
        qp.drawImage(QPointF(x + 30, 156), render(32, k))
        qp.drawImage(QPointF(x + 80, 164), render(16, k))
        qp.setPen(QColor("#dddddd"))
        qp.drawText(QPointF(x + 12, 214), themes.THEMES[k][0])
    qp.end()
    img.save(str(path))


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path, images: list[QImage]):
    """A multi-size .ico with PNG-compressed entries (Windows Vista and later)."""
    blobs = [png_bytes(i) for i in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for img, blob in zip(images, blobs):
        w = img.width()
        entries += struct.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def wordmark(dark: bool, scale: int = 2) -> QImage:
    """The mark and "NOVA", with "Not Only a Voice Assistant" underneath."""
    mark = 112 * scale
    f = QFont()
    f.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
    f.setPixelSize(76 * scale)
    f.setWeight(QFont.DemiBold)
    f.setLetterSpacing(QFont.PercentageSpacing, 108)
    sub = QFont()
    sub.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    sub.setPixelSize(19 * scale)
    sub.setLetterSpacing(QFont.PercentageSpacing, 104)
    fm, sm = QFontMetricsF(f), QFontMetricsF(sub)
    gap = 26 * scale
    text_w = max(fm.horizontalAdvance("NOVA"), sm.horizontalAdvance("Not Only a Voice Assistant"))
    w, h = int(mark + gap + text_w + 8 * scale), int(mark + 8 * scale)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    qp = QPainter(img)
    qp.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    QSvgRenderer(QByteArray(themes.logo_svg(mark))).render(qp, QRectF(0, 4 * scale, mark, mark))
    x = mark + gap
    top = 4 * scale + (mark - (fm.height() * 0.78 + sm.height())) / 2
    qp.setFont(f)
    qp.setPen(QColor("#ffffff" if dark else "#14121c"))
    qp.drawText(QPointF(x - 2 * scale, top + fm.ascent() * 0.86), "NOVA")
    qp.setFont(sub)
    qp.setPen(QColor("#c8cbd2" if dark else "#5a5f6b"))
    qp.drawText(QPointF(x, top + fm.height() * 0.78 + sm.ascent()), "Not Only a Voice Assistant")
    qp.end()
    return img


def preview(path: Path, dark_wm: QImage, light_wm: QImage):
    W, H = 1100, 640
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#777777"))
    qp = QPainter(img)
    qp.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    for row, bg in enumerate(("#1c1c1c", "#f3f3f3")):
        y0 = 10 + row * 315
        qp.fillRect(QRectF(10, y0, W - 20, 305), QColor(bg))
        x = 30
        for s in (256, 128, 64, 48, 32, 24, 20, 16):
            qp.drawImage(QPointF(x, y0 + 20 + (256 - s)), render(s))
            x += s + 24
        wm = dark_wm if row == 0 else light_wm
        wm_s = wm.scaledToHeight(90, Qt.SmoothTransformation)
        qp.drawImage(QPointF(W - 30 - wm_s.width(), y0 + 200), wm_s)
        # a taskbar strip with the 24 px icon among others
        bar = QRectF(30, y0 + 285 - 8, 360, 40)
        qp.fillRect(bar.adjusted(0, -12, 0, 0), QColor("#202020" if row == 0 else "#e8e8e8"))
        for i in range(5):
            r = QRectF(bar.left() + 14 + i * 44, bar.top() - 4, 24, 24)
            if i == 2:
                qp.drawImage(r.topLeft(), render(24))
            else:
                qp.fillRect(r.adjusted(4, 4, -4, -4), QColor(120, 120, 130, 160))
    qp.end()
    img.save(str(path))


def main():
    app = QApplication(sys.argv[:1])  # noqa: F841
    for s in SIZES:
        render(s).save(str(ASSETS / f"nova-{s}.png"))
    write_ico(ASSETS / "nova.ico", [render(s) for s in ICO_SIZES])
    dark, light = wordmark(True), wordmark(False)
    dark.save(str(ASSETS / "wordmark-dark.png"))
    light.save(str(ASSETS / "wordmark-light.png"))
    (ROOT / "samples").mkdir(exist_ok=True)
    preview(ROOT / "samples" / "logo_preview.png", dark, light)
    themes_sheet(ROOT / "samples" / "logo_themes.png")
    print("wrote", ", ".join(p.name for p in sorted(ASSETS.iterdir()) if p.suffix in (".png", ".ico")))


if __name__ == "__main__":
    main()
