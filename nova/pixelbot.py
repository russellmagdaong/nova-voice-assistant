"""The robot mascot (pixel art): a little robot with a monitor for a head, its face on the blue
screen, two antennae, peeking up from the bottom edge with its hands on the edge. Drawn here as
text, one character per pixel (see pixelcat.py for the shared layout and pixelart.py).
"""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render

NAME = "Robot"
COLS, ROWS = 21, 19
HEAD_X, HEAD_Y = 1, 3           # the head (19 x 12) starts here; antennae above it

PEEK = {"idle": 11, "sleep": 11, "hover": 15, "active": 18}
ZZZ = (6, 2)

PALETTE = {
    "K": "#1d1a22",     # outline
    "W": "#e3edf0",     # white shell
    "L": "#aebcc4",     # side of the monitor
    "G": "#8d9ca5",     # shading
    "D": "#5f8db4",     # screen edge (inset)
    "B": "#9cc4de",     # screen
    "E": "#2f4170",     # eyes, mouth
    "P": "#e8a0a6",     # blush
    "Y": "#f6d38e",     # antenna tip (yellow)
    "R": "#e84a4a",     # antenna tip (red), chest light
    "N": "#4a6884",     # screen, asleep (dim)
    "M": "#3a5470",     # screen edge, asleep
    "C": "#8fb6d2",     # closed eyes on a dim screen
    "r": "#ff9a9a",     # antenna tip, lit (listening)
}

ANTENNAE = [           # rows 0-2, full width
    ".....Y...............",
    ".....K.R.............",
    ".....K.K.............",
]

HEAD = [               # rows 3-14, 19 wide
    ".KKKKKKKKKKKKKKKKK.",
    "KLLLWWWWWWWWWWWWWWK",
    "KLLLWDDDDDDDDDDDDWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWDBBBBBBBBBBBWK",
    "KLLLWWWWWWWWWWWWWWK",
    ".KKKKKKKKKKKKKKKKK.",
]

BODY = [               # rows 15-18, full width: chest with a red light, hands over the edge
    "....KGWWWWWWWWWGK....",
    ".KKKKGWWWWWWWRWGKKKK.",
    "KWWWKGWWWWWWWWWGKWWWK",
    "KWLWKGWWWWWWWWWGKWLWK",
]

# The face, in head coordinates (the screen is columns 6-16, rows 3-9; its centre is column 11).
# Each kind: {(x, y): colour}.
def _bar(x, y0, y1, c="E"):
    return {(x, y): c for y in range(y0, y1 + 1)}


def _line(x0, x1, y, c="E"):
    return {(x, y): c for x in range(x0, x1 + 1)}


BLUSH = {(7, 7): "P", (15, 7): "P"}
FACES = {
    "idle":       {**BLUSH, **_bar(8, 5, 7), **_bar(14, 5, 7), (11, 7): "E"},
    "blink":      {**BLUSH, **_line(7, 9, 6), **_line(13, 15, 6), (11, 7): "E"},
    "talk":       {**BLUSH, **_bar(8, 5, 7), **_bar(14, 5, 7), **_line(10, 12, 7), **_line(10, 12, 8)},
    "listen":     {**BLUSH, **_bar(8, 4, 7), **_bar(14, 4, 7), (11, 7): "E"},
    "think":      {**BLUSH, **_bar(9, 4, 5), **_bar(15, 4, 5), (11, 7): "E"},
    "look_left":  {**BLUSH, **_bar(7, 5, 7), **_bar(13, 5, 7), (11, 7): "E"},
    "look_right": {**BLUSH, **_bar(9, 5, 7), **_bar(15, 5, 7), (11, 7): "E"},
    "sleep":      {**_line(7, 9, 7, "C"), **_line(13, 15, 7, "C")},
    "sad":        {**BLUSH, **_bar(8, 6, 7), **_bar(14, 6, 7), (9, 5): "E", (13, 5): "E",
                   (10, 8): "E", (11, 7): "E", (12, 8): "E"},
}
FRAMES = {name: name for name in FACES}


def grid(name: str) -> list[list[str]]:
    head = [list(r) for r in HEAD]
    if name == "sleep":                         # standby: the screen dims
        head = [[{"B": "N", "D": "M"}.get(c, c) for c in r] for r in head]
    for (x, y), c in FACES[name].items():
        head[y][x] = c
    rows = [list(r) for r in ANTENNAE]
    if name == "listen":
        rows[0][5], rows[1][7] = "r", "r"       # antennae light up
    if name == "sleep":
        rows[0][5], rows[1][7] = "G", "G"
    pad = "." * HEAD_X
    rows += [list(pad + "".join(r) + pad) for r in head] + [list(r) for r in BODY]
    assert len(rows) == ROWS and all(len(r) == COLS for r in rows), name
    return rows


def image(name: str, rim: bool) -> QImage:
    """The frame at 1 pixel per art pixel. (The robot has its own dark outline: no rim needed.)"""
    return render(grid(name), PALETTE)


# The tray icon is a smaller head (14 wide) so it fits 16 x 16 with its white outline.
ICON = [
    "...Y..........",
    "...K.R........",
    ".KKKKKKKKKKKK.",
    "KLLWWWWWWWWWWK",
    "KLLWDDDDDDDDWK",
    "KLLWDBBBBBBBWK",
    "KLLWDBEBBBEBWK",
    "KLLWDBEBBBEBWK",
    "KLLWDPEBEBEPWK",
    "KLLWDBBBBBBBWK",
    "KLLWWWWWWWWWWK",
    ".KKKKKKKKKKKK.",
]
ICON_ASLEEP = [        # standby: dim screen, closed eyes, antennae off
    "...G..........",
    "...K.G........",
    ".KKKKKKKKKKKK.",
    "KLLWWWWWWWWWWK",
    "KLLWMMMMMMMMWK",
    "KLLWMNNNNNNNWK",
    "KLLWMNNNNNNNWK",
    "KLLWMNNNNNNNWK",
    "KLLWMCCCNCCCWK",
    "KLLWMNNNNNNNWK",
    "KLLWWWWWWWWWWK",
    ".KKKKKKKKKKKK.",
]


def icon_image(muted: bool = False) -> QImage:
    """16x16 tray icon with a white outline (grey and in standby when muted)."""
    return outlined_icon(ICON_ASLEEP if muted else ICON, PALETTE, None, dx=1, dy=2)   # no white outline


# ---- Looking around: the eyes follow the cursor (NOVA's window and the notch). The eye bars
# move about the screen; looking down, the mouth and blush move down with them.
_BLUSH_DOWN = {(7, 8): "P", (15, 8): "P"}
_GAZE = {
    "look_up":         {**BLUSH, **_bar(8, 4, 6), **_bar(14, 4, 6), (11, 7): "E"},
    "look_up_left":    {**BLUSH, **_bar(7, 4, 6), **_bar(13, 4, 6), (11, 7): "E"},
    "look_up_right":   {**BLUSH, **_bar(9, 4, 6), **_bar(15, 4, 6), (11, 7): "E"},
    "look_down":       {**_BLUSH_DOWN, **_bar(8, 6, 8), **_bar(14, 6, 8), (11, 9): "E"},
    "look_down_left":  {**_BLUSH_DOWN, **_bar(7, 6, 8), **_bar(13, 6, 8), (11, 9): "E"},
    "look_down_right": {**_BLUSH_DOWN, **_bar(9, 6, 8), **_bar(15, 6, 8), (11, 9): "E"},
}
FACES.update(_GAZE)
FRAMES.update({name: name for name in _GAZE})
