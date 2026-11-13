"""Shared helpers for the pixel-art mascots (pixelcat.py, pixelbot.py): their frames are text
grids, one character per pixel ("." = transparent)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage

# A pixel "z" for a sleeping mascot.
Z = ["zzzzz",
     "...z.",
     "..z..",
     ".z...",
     "zzzzz"]


def sym(half: str) -> str:
    """A row from its left half (for symmetric art)."""
    return half + half[::-1]


def render(rows: list[str] | list[list[str]], palette: dict[str, str], rim: str | None = None,
           rim_keys: str = "") -> QImage:
    """Rows -> image at 1 pixel per character. With `rim`, pixels of `rim_keys` colours on the
    silhouette's edge get that colour instead (an outline that keeps a dark mascot visible on a
    dark desktop). The bottom edge counts as cut off by the screen edge, not as an edge."""
    h, w = len(rows), len(rows[0])
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)

    def filled(x, y):
        return y >= h or (0 <= x < w and y >= 0 and rows[y][x] != ".")

    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == ".":
                continue
            if rim and c in rim_keys and not all(filled(x + dx, y + dy)
                                                 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                img.setPixelColor(x, y, QColor(rim))
            else:
                img.setPixelColor(x, y, QColor(palette[c]))
    return img


def outlined_icon(rows: list[str] | list[list[str]], palette: dict[str, str], outline: str | None,
                  dx: int, dy: int, size: int = 16) -> QImage:
    """A size x size tray icon: `rows` placed at (dx, dy) with a solid outline around them
    (8 neighbours, corners included, so it reads at 16 px). `outline` None: no outline (only
    the black cat needs one, to show on a dark taskbar)."""
    canvas = [["."] * size for _ in range(size)]
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c != "." and 0 <= dy + y < size and 0 <= dx + x < size:
                canvas[dy + y][dx + x] = c
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(0)
    for y in range(size):
        for x in range(size):
            c = canvas[y][x]
            if c != ".":
                img.setPixelColor(x, y, QColor(palette[c]))
            elif outline and any(0 <= x + ox < size and 0 <= y + oy < size and canvas[y + oy][x + ox] != "."
                                 for ox in (-1, 0, 1) for oy in (-1, 0, 1)):
                img.setPixelColor(x, y, QColor(outline))
    return img
