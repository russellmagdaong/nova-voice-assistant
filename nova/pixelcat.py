"""NOVA's pixel-art cat. Every frame is drawn here as text, one character per pixel, and turned
into an image at runtime. To change the art, edit the grids below and run
tools\\notch_preview.py (it also writes samples\\cat_frames.png).

The cat peeks up from the bottom edge: the last rows are its paws on the edge and its chest,
cut off by the edge. The top rows are left free for perked-up ears.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage

COLS, ROWS = 24, 20

PALETTE = {
    "k": "#141418",     # fur
    "g": "#2a2a33",     # the line between a paw and the chest
    "w": "#ffffff",     # eyes
    "e": "#9a9aa8",     # closed eyes
    "m": "#6e1d33",     # open mouth
    "p": "#ff8aa5",     # tongue, nose
}
RIM = "#40404c"         # outline on dark themes, so the black cat doesn't vanish on a dark desktop

# A pixel "z" for the sleeping cat (drawn at half the cat's pixel size, like a small game font).
Z = ["zzzzz",
     "...z.",
     "..z..",
     ".z...",
     "zzzzz"]


def sym(half: str) -> str:
    """A row from its left half (the cat is symmetric)."""
    assert len(half) == COLS // 2, half
    return half + half[::-1]


# Head and paws (rows 6-19), no ears or face.
BODY = [sym(r) for r in [
    "...kkkkkkkkk",     # 6  top of the head
    "..kkkkkkkkkk",     # 7
    ".kkkkkkkkkkk",     # 8
    ".kkkkkkkkkkk",     # 9
    ".kkkkkkkkkkk",     # 10
    ".kkkkkkkkkkk",     # 11
    ".kkkkkkkkkkk",     # 12
    "..kkkkkkkkkk",     # 13
    "...kkkkkkkkk",     # 14
    "....kkkkkkkk",     # 15 chin
    ".....kkkkkkk",     # 16 neck
    "..kkkgkkkkkk",     # 17 paws over the edge, chest between them
    ".kkkkgkkkkkk",     # 18
    ".kkkkgkkkkkk",     # 19 (cut off by the screen edge)
]]

# Ears (rows 0-5).
EARS = {
    "up": [sym(r) for r in [
        "............",
        "...k........",
        "...kk.......",
        "...kkk......",
        "..kkkkk.....",
        "..kkkkkk....",
    ]],
    "perked": [sym(r) for r in [   # listening: taller and narrower, pointing straight up
        "...k........",
        "...k........",
        "...kk.......",
        "...kkk......",
        "..kkkkk.....",
        "..kkkkkk....",
    ]],
    "relaxed": [sym(r) for r in [  # asleep: tilted out
        "............",
        "............",
        "............",
        ".k..........",
        ".kkk........",
        "..kkkkkk....",
    ]],
    "flat": [sym(r) for r in [     # sad: flattened to the sides
        "............",
        "............",
        "............",
        "............",
        "k...........",
        "kkkkkkk.....",
    ]],
}


def eyes(kind: str) -> dict[tuple[int, int], str]:
    """Eye pixels {(x, y): colour}. The eyes are 2x2 white squares at columns 7-8 and 15-16."""
    px: dict[tuple[int, int], str] = {}

    def square(x, y, w=2, h=2, c="w"):
        for dx in range(w):
            for dy in range(h):
                px[(x + dx, y + dy)] = c

    if kind == "open":
        square(7, 10), square(15, 10)
    elif kind == "wide":                 # listening: taller eyes
        square(7, 9, h=3), square(15, 9, h=3)
    elif kind == "left":                 # looking to the viewer's left
        square(6, 10), square(14, 10)
    elif kind == "right":
        square(8, 10), square(16, 10)
    elif kind == "up":                   # thinking: up and to the right
        square(8, 9), square(16, 9)
    elif kind in ("closed", "blink"):
        square(7, 11, h=1, c="e"), square(15, 11, h=1, c="e")
    elif kind == "sad":                  # lower, drooping at the outer corners
        square(7, 11), square(15, 11)
        del px[(7, 11)], px[(16, 11)]
    return px


NOSE = {(11, 12): "p", (12, 12): "p"}
MOUTH = {(11, 13): "m", (12, 13): "m", (11, 14): "p", (12, 14): "p"}

# frame -> (ears, eyes, extra pixels)
FRAMES = {
    "idle": ("up", "open", {}),
    "blink": ("up", "blink", {}),
    "talk": ("up", "open", MOUTH),
    "listen": ("perked", "wide", {}),
    "think": ("up", "up", {}),
    "look_left": ("up", "left", {}),
    "look_right": ("up", "right", {}),
    "sleep": ("relaxed", "closed", {}),
    "sad": ("flat", "sad", {}),
}


def grid(name: str) -> list[list[str]]:
    ears, eye_kind, extra = FRAMES[name]
    rows = [list(r) for r in EARS[ears] + BODY]
    for (x, y), c in {**NOSE, **eyes(eye_kind), **extra}.items():
        rows[y][x] = c
    return rows


def image(name: str, rim: bool) -> QImage:
    """The frame at 1 pixel per art pixel (scale it up without smoothing)."""
    rows = grid(name)
    img = QImage(COLS, ROWS, QImage.Format_ARGB32)
    img.fill(0)
    filled = lambda x, y: y >= ROWS or (0 <= x < COLS and y >= 0 and rows[y][x] != ".")  # noqa: E731
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == ".":
                continue
            if rim and c == "k" and not all(filled(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                c = "r"
            img.setPixelColor(x, y, QColor(RIM if c == "r" else PALETTE[c]))
    return img
