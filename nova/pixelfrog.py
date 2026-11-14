"""Frog: original NOVA pixel mascot, with hands resting on the screen edge."""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render, sym

NAME = 'Frog'
COLS, ROWS = 20, 19
PEEK = {'idle': 10, 'sleep': 10, 'hover': 16, 'active': 18}
ZZZ = (6, 1)

PALETTE = {
    'K': '#304e40',  # deep green outline
    'G': '#79bd62',  # leaf green skin
    'L': '#a8db7a',  # forehead and finger pads
    'S': '#559853',  # skin shadow
    'W': '#fff4cf',  # warm ivory eyes
    'E': '#293d38',  # pupils and smile
    'H': '#ffffff',  # eye glints
    'P': '#eea0a3',  # pink cheeks
    'B': '#d5e6a1',  # soft throat
}

# Mirror the left half: every character is one pixel.
BASE = [sym(r) for r in [
    '..........',  # 0
    '..........',  # 1
    '...KKK....',  # 2
    '..KLLLK...',  # 3
    '.KLWWWWK..',  # 4
    '.KGWWWWKKK',  # 5
    '.KGWWWWKGG',  # 6
    '.KGWWWWKGG',  # 7
    '.KGGGGGGGG',  # 8
    'KGGGGLLGGG',  # 9
    'KGGGGGGGGG',  # 10
    'KGPPGGGGGG',  # 11
    '.KGGGGGGGG',  # 12
    '.KSGBBBBBB',  # 13
    '..KSBBBBBB',  # 14
    '...KKBBBBB',  # 15
    '..KLLKBBBB',  # 16
    '.KLGLLKSBB',  # 17
    '.KLKLKKSSS',  # 18
]]

EYE_COLS = (3, 13)

EYE_ROW = 5

EYES = {'open': ['WEEW', 'WHEW', 'WWWW'],
 'wide': ['WEEW', 'WHEW', 'WEEW'],
 'left': ['EEWW', 'HEWW', 'WWWW'],
 'right': ['WWEE', 'WWHE', 'WWWW'],
 'up': ['WWHE', 'WWEE', 'WWWW'],
 'blink': ['WWWW', 'EEEE', 'WWWW'],
 'closed': ['WWWW', 'EWWE', 'WEEW'],
 'sad': ['WWWW', 'WEEW', 'WHEW']}

MOUTHS = {'idle': {(6, 12): 'E',
          (7, 13): 'E',
          (8, 13): 'E',
          (9, 13): 'E',
          (10, 13): 'E',
          (11, 13): 'E',
          (12, 13): 'E',
          (13, 12): 'E'},
 'talk': {(6, 12): 'E',
          (7, 13): 'E',
          (8, 13): 'E',
          (9, 13): 'E',
          (10, 13): 'E',
          (11, 13): 'E',
          (12, 13): 'E',
          (13, 12): 'E',
          (8, 12): 'E',
          (9, 12): 'E',
          (10, 12): 'E',
          (11, 12): 'E',
          (9, 14): 'P',
          (10, 14): 'P'},
 'sad': {(8, 13): 'E', (9, 12): 'E', (10, 12): 'E', (11, 13): 'E'}}

POSES = {'listen': {(4, 3): 'L', (15, 3): 'L'}, 'sleep': {(4, 3): 'S', (15, 3): 'S'}}

FRAMES = {'idle': ('normal', 'open', 'idle'),
 'blink': ('normal', 'blink', 'idle'),
 'talk': ('normal', 'open', 'talk'),
 'listen': ('listen', 'wide', 'idle'),
 'think': ('normal', 'up', 'idle'),
 'look_left': ('normal', 'left', 'idle'),
 'look_right': ('normal', 'right', 'idle'),
 'sleep': ('sleep', 'closed', 'idle'),
 'sad': ('sad', 'sad', 'sad')}

# A compact 14-column head, drawn separately for the tray.
ICON = [
    '..KKK....KKK..',
    '.KLWLK..KLWLK.',
    'KLWWWLKKLWWWLK',
    'KGWEWWGGWWEWGK',
    'KGWEWWGGWWEWGK',
    'KGGGGGGGGGGGGK',
    'KGPPGGGGGGPPGK',
    '.KGGEGGGGEGGK.',
    '.KSBBEEEEBBSK.',
    '..KSBBBBBBSK..',
    '...KKKKKKKK...',
]

ICON_SLEEP = {(3, 3): 'W',
 (10, 3): 'W',
 (2, 4): 'E',
 (3, 4): 'E',
 (4, 4): 'E',
 (9, 4): 'E',
 (10, 4): 'E',
 (11, 4): 'E'}

MUTED = {'G': '#8d9d86', 'L': '#b7c3a1', 'S': '#6f826f', 'P': '#b4a0a0', 'W': '#d5d6c6'}


def grid(name: str) -> list[list[str]]:
    """Compose the silhouette and expression without changing the talking body."""
    pose, eyes, mouth = FRAMES[name]
    rows = [list(r) for r in BASE]
    for (x, y), c in POSES.get(pose, {}).items():
        rows[y][x] = c
    for x0 in EYE_COLS:
        for dy, line in enumerate(EYES[eyes]):
            for dx, c in enumerate(line):
                if c != ".":
                    rows[EYE_ROW + dy][x0 + dx] = c
    for (x, y), c in MOUTHS[mouth].items():
        rows[y][x] = c
    assert len(rows) == ROWS and all(len(r) == COLS for r in rows), name
    assert all(c == "." or c in PALETTE for r in rows for c in r), name
    return rows


def image(name: str, rim: bool) -> QImage:
    """One art pixel per pixel; the coloured outline works in either theme."""
    return render(grid(name), PALETTE, None, "")


def icon_image(muted: bool = False) -> QImage:
    """A separately drawn head leaves room for the one-pixel tray outline."""
    rows = [list(r) for r in ICON]
    if muted:
        for (x, y), c in ICON_SLEEP.items():
            rows[y][x] = c
    palette = dict(PALETTE)
    if muted:
        palette.update(MUTED)
    return outlined_icon(rows, palette, None, dx=1, dy=1)     # no white outline (only the black cat has one)


# ---- Looking around: the eyes follow the cursor (NOVA's window and the notch). The pupils
# already sit at the top of the eye, so "up" looks the same as straight ahead.
EYES.update({
    "down": ["WWWW", "WEEW", "WHEW"],
    "down_left": ["WWWW", "EEWW", "HEWW"],
    "down_right": ["WWWW", "WWEE", "WWHE"],
})
FRAMES.update({
    "look_down": ("normal", "down", "idle"),
    "look_down_left": ("normal", "down_left", "idle"),
    "look_down_right": ("normal", "down_right", "idle"),
})
