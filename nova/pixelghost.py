"""Ghost: original NOVA pixel mascot, with hands resting on the screen edge."""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render, sym

NAME = 'Ghost'
COLS, ROWS = 18, 19
PEEK = {'idle': 11, 'sleep': 11, 'hover': 15, 'active': 18}
ZZZ = (6, 3)

PALETTE = {
    'K': '#8993a5',  # soft slate outline
    'W': '#fffaf2',  # warm white body
    'L': '#ffffff',  # crown highlight
    'S': '#dce4ef',  # soft blue grey shadow
    'E': '#41445c',  # ink oval eyes
    'H': '#ffffff',  # eye glints
    'P': '#f1b0be',  # rose blush and tongue
    'M': '#82718e',  # mouth interior
}

# Mirror the left half: every character is one pixel.
BASE = [sym(r) for r in [
    '.........',  # 0
    '.........',  # 1
    '......KKK',  # 2
    '....KKLLL',  # 3
    '...KLLWWW',  # 4
    '..KLWWWWW',  # 5
    '..KWWWWWW',  # 6
    '.KWWWWWWW',  # 7
    '.KWWWWWWW',  # 8
    '.KWWWWWWW',  # 9
    '.KWWWWWWW',  # 10
    '.KWWPPWWW',  # 11
    '.KWWWWWWW',  # 12
    '..KWWWWWW',  # 13
    '.KKWWWWWW',  # 14
    'KLWKWWWWW',  # 15
    'KWWKWWWWW',  # 16
    '.KSKSWWWW',  # 17
    '..K.KSSWW',  # 18
]]

EYE_COLS = (4, 11)

EYE_ROW = 8

EYES = {'open': ['WEEW', 'WHEW', 'WEEW'],
 'wide': ['WEEW', 'EHEE', 'WEEW'],
 'left': ['EEWW', 'HEWW', 'EEWW'],
 'right': ['WWEE', 'WWHE', 'WWEE'],
 'up': ['WWHE', 'WWEE', 'WWWW'],
 'blink': ['WWWW', 'EEEE', 'WWWW'],
 'closed': ['WWWW', 'EWWE', 'WEEW'],
 'sad': ['WWWW', 'WEEW', 'WHEW']}

MOUTHS = {'idle': {(8, 12): 'E', (9, 12): 'E'},
 'talk': {(8, 12): 'E', (9, 12): 'E', (8, 13): 'M', (9, 13): 'P'},
 'sad': {(7, 13): 'E', (8, 12): 'E', (9, 12): 'E', (10, 13): 'E'}}

POSES = {'listen': {(5, 4): 'L', (12, 4): 'L'},
 'sleep': {(3, 5): 'S', (14, 5): 'S'},
 'sad': {(1, 15): 'S', (16, 15): 'S'}}

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
    '.....KKKK.....',
    '...KKLLLLKK...',
    '..KLWWWWWWLK..',
    '.KLWWWWWWWWLK.',
    '.KWWWWWWWWWWK.',
    'KWWEEWWWWEEWWK',
    'KWWHEWWWWHEWWK',
    'KWWEEWWWWEEWWK',
    'KWPPWWWWWWPPWK',
    '.KWWWWEEWWWWK.',
    '.KSWWWWWWWWSK.',
    '..KSSWWWWSSK..',
    '...KKKKKKKK...',
]

ICON_SLEEP = {(3, 5): 'W',
 (4, 5): 'W',
 (9, 5): 'W',
 (10, 5): 'W',
 (3, 7): 'W',
 (4, 7): 'W',
 (9, 7): 'W',
 (10, 7): 'W',
 (2, 6): 'E',
 (3, 6): 'E',
 (4, 6): 'E',
 (8, 6): 'E',
 (9, 6): 'E',
 (10, 6): 'E'}

MUTED = {'W': '#d1d3d7', 'L': '#e2e2e4', 'S': '#b1b8c5', 'P': '#b5a3af'}


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
