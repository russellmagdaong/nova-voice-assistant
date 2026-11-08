"""The black cat mascot (pixel art). Every frame is drawn here as text, one character per pixel,
and turned into an image at runtime. To change the art, edit the grids below and run
tools\\notch_preview.py (it also writes samples\\cat_frames.png).

The cat peeks up from the bottom edge of the screen: a small square head with tall ears and
yellow eyes, its paws on the edge. Row 0 is left free for perked-up ears.

Every pixel mascot module has: COLS, ROWS, FRAMES, PEEK, ZZZ, image(), icon_image().
"""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render, sym

NAME = "Black cat"
COLS, ROWS = 18, 19
HEAD_X = 2              # the head (14 wide) starts at this column

# Rows showing (from the top): ears + eyes at rest, the head on hover, all but the last row
# when busy (that row shows when it hops).
PEEK = {"idle": 13, "sleep": 13, "hover": 16, "active": 18}
ZZZ = (4, 3)            # where the sleeping z's start: columns right of centre, row

PALETTE = {
    "k": "#0b0b0f",     # fur
    "g": "#20202a",     # the line between a paw and the chest
    "y": "#ffd43b",     # eyes
    "o": "#e8a812",     # eyes, lower row
    "c": "#a07816",     # closed eyes
    "p": "#ef7c86",     # inside the ears, tongue
    "q": "#f9b2b8",     # inside the ears, top
    "m": "#5a1626",     # open mouth
}
RIM = "#34343f"         # faint outline on dark themes, so the black cat doesn't vanish

# Ears and the top of the head: rows 0-5, 14 wide.
EARS = {
    "up": [sym(r) for r in [
        ".......",
        ".k.....",
        ".kk....",
        ".kqk...",
        ".kppk..",
        ".kppkkk",
    ]],
    "perked": [sym(r) for r in [   # listening: one pixel taller
        ".k.....",
        ".kk....",
        ".kqk...",
        ".kqpk..",
        ".kppk..",
        ".kppkkk",
    ]],
    "relaxed": [sym(r) for r in [  # asleep: shorter, drooping
        ".......",
        ".......",
        ".k.....",
        ".kqk...",
        ".kppk..",
        ".kppkkk",
    ]],
    "flat": [sym(r) for r in [     # sad: folded out to the sides
        ".......",
        ".......",
        ".......",
        ".......",
        "kqk....",
        "kkpkkkk",
    ]],
}

# The rest of the head: rows 6-15 (eyes on rows 10-12), 14 wide.
FACE = [sym(r) for r in [
    ".kkkkkk",          # 6
    ".kkkkkk",          # 7
    "kkkkkkk",          # 8
    "kkkkkkk",          # 9
    "kkkkkkk",          # 10 eyes
    "kkkkkkk",          # 11 eyes
    "kkkkkkk",          # 12 eyes
    "kkkkkkk",          # 13
    "kkkkkkk",          # 14
    ".kkkkkk",          # 15 chin
]]

# Chest and paws over the edge: rows 16-18, the full 18 wide.
CHEST = [sym(r) for r in [
    "...kkkkkk",        # 16
    ".kkkkgkkk",        # 17 paws
    ".kkkkgkkk",        # 18 (cut off by the screen edge)
]]

# Eyes: 4x3 each, left eye first ("." keeps the fur), then how far both eyes move (x, y).
# Pupils point inwards, like the reference. Looking around moves the eyes a pixel that way.
EYES = {
    "open":   (["yyky", "yyky", "oooo"], ["ykyy", "ykyy", "oooo"], 0, 0),
    "wide":   (["ykky", "ykky", "oooo"], ["ykky", "ykky", "oooo"], 0, 0),   # listening: big pupils
    "left":   (["ykyy", "ykyy", "oooo"], ["ykyy", "ykyy", "oooo"], -1, 0),
    "right":  (["yyky", "yyky", "oooo"], ["yyky", "yyky", "oooo"], 1, 0),
    "up":     (["yyky", "yyyy", "oooo"], ["yyky", "yyyy", "oooo"], 1, -1),  # thinking: up and right
    "blink":  (["....", "cccc", "...."], ["....", "cccc", "...."], 0, 0),
    "closed": (["....", "c..c", ".cc."], ["....", "c..c", ".cc."], 0, 0),   # asleep, content
    "sad":    ([".yyy", "yyky", "ooko"], ["yyy.", "ykyy", "okoo"], 0, 0),   # outer corners droop
}
EYE_ROW, EYE_COLS = 10, (2, 8)          # in head columns

MOUTH = {(6, 13): "m", (7, 13): "m", (6, 14): "p", (7, 14): "p"}   # head columns

# frame -> (ears, eyes, mouth open)
FRAMES = {
    "idle": ("up", "open", False),
    "blink": ("up", "blink", False),
    "talk": ("up", "open", True),
    "listen": ("perked", "wide", False),
    "think": ("up", "up", False),
    "look_left": ("up", "left", False),
    "look_right": ("up", "right", False),
    "sleep": ("relaxed", "closed", False),
    "sad": ("flat", "sad", False),
}


def grid(name: str) -> list[list[str]]:
    ears, eye_kind, mouth = FRAMES[name]
    pad = "." * HEAD_X
    rows = [list(pad + r + pad) for r in EARS[ears] + FACE] + [list(r) for r in CHEST]
    left, right, ex, ey = EYES[eye_kind]
    for eye, x0 in zip((left, right), EYE_COLS):
        for dy, line in enumerate(eye):
            for dx, c in enumerate(line):
                if c != ".":
                    rows[EYE_ROW + ey + dy][HEAD_X + x0 + ex + dx] = c
    if mouth:
        for (x, y), c in MOUTH.items():
            rows[y][HEAD_X + x] = c
    assert len(rows) == ROWS and all(len(r) == COLS for r in rows), name
    return rows


def image(name: str, rim: bool) -> QImage:
    """The frame at 1 pixel per art pixel (scale it up without smoothing)."""
    return render(grid(name), PALETTE, RIM if rim else None, "k")


def icon_image(muted: bool = False) -> QImage:
    """16x16 tray icon: the head (ears to chin) with a white outline (grey when muted)."""
    head = [r[HEAD_X:HEAD_X + 14] for r in grid("sleep" if muted else "idle")[1:16]]
    return outlined_icon(head, PALETTE, "#b8b8c0" if muted else "#ffffff", dx=1, dy=1)
