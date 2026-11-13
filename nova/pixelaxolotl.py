"""Axolotl: original NOVA pixel mascot, with hands resting on the screen edge."""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render, sym

NAME = 'Axolotl'
COLS, ROWS = 22, 19
PEEK = {'idle': 11, 'sleep': 11, 'hover': 16, 'active': 18}
ZZZ = (7, 1)

PALETTE = {
    'K': '#854b70',  # plum outline
    'P': '#f4b3c6',  # pink skin
    'L': '#ffdae0',  # forehead highlight
    'S': '#d58fae',  # skin shadow and fingers
    'G': '#ce568d',  # raspberry gill stalks
    'F': '#ed85b1',  # frill tips
    'E': '#51334e',  # eyes and smile
    'H': '#fff8ef',  # eye glints
    'B': '#eb8da8',  # cheeks and tongue
    'C': '#ffe7df',  # chin and palms
}

# Mirror the left half: every character is one pixel.
BASE = [sym(r) for r in [
    '...........',  # 0
    '...........',  # 1
    '...........',  # 2
    '.F.........',  # 3
    '.FG........',  # 4
    '..FG..KKKKK',  # 5
    'F..GKKLLLLL',  # 6
    'FG.GKPPPPPP',  # 7
    '.FGGKPPPPPP',  # 8
    '..GKKPPPPPP',  # 9
    'FGGGKPPPPPP',  # 10
    'FF.GKPBBPPP',  # 11
    '..FGKPPPPPP',  # 12
    '..F..KPPPPP',  # 13
    '......KCCCC',  # 14
    '......KKCCC',  # 15
    '.....KPCSKC',  # 16
    '.....KCCSKC',  # 17
    '.....KSSSKS',  # 18
]]

EYE_COLS = (6, 13)

EYE_ROW = 8

EYES = {'open': ['PEEP', 'PHEP', 'PPPP'],
 'wide': ['PEEP', 'PHEP', 'PEEP'],
 'left': ['EEPP', 'HEPP', 'PPPP'],
 'right': ['PPEE', 'PPHE', 'PPPP'],
 'up': ['PPHE', 'PPEE', 'PPPP'],
 'blink': ['PPPP', 'EEEE', 'PPPP'],
 'closed': ['PPPP', 'EPPE', 'PEEP'],
 'sad': ['PPPP', 'PEEP', 'PHEP']}

MOUTHS = {'idle': {(9, 12): 'E', (10, 13): 'E', (11, 13): 'E', (12, 12): 'E'},
 'talk': {(9, 12): 'E',
          (10, 12): 'E',
          (11, 12): 'E',
          (12, 12): 'E',
          (10, 13): 'B',
          (11, 13): 'B'},
 'sad': {(9, 13): 'E', (10, 12): 'E', (11, 12): 'E', (12, 13): 'E'}}

POSES = {'listen': {(1, 2): 'F', (20, 2): 'F', (0, 5): 'F', (21, 5): 'F', (0, 9): 'F', (21, 9): 'F'},
 'sleep': {(1, 3): '.', (20, 3): '.', (1, 4): 'S', (20, 4): 'S', (0, 6): '.', (21, 6): '.'},
 'sad': {(1, 3): '.',
         (20, 3): '.',
         (1, 4): '.',
         (20, 4): '.',
         (0, 6): '.',
         (21, 6): '.',
         (1, 13): 'F',
         (20, 13): 'F',
         (1, 14): 'F',
         (20, 14): 'F'}}

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
    'F............F',
    'FG..KKKKKK..GF',
    '.GGKLLLLLLKGG.',
    'F.GKPPPPPPKG.F',
    'FGGKPEPPEPKGGF',
    '.GGKPEPPEPKGG.',
    'F.GKPBPPBPKG.F',
    '.GGKPPEEPPKGG.',
    '.F..KCCCCK..F.',
    '.....KKKK.....',
]

ICON_SLEEP = {(5, 4): 'P', (8, 4): 'P', (4, 5): 'E', (5, 5): 'E', (8, 5): 'E', (9, 5): 'E'}

MUTED = {'P': '#c2a9b8',
 'L': '#ddcbd1',
 'S': '#a18a9d',
 'G': '#99758c',
 'F': '#b99aaf',
 'B': '#b49baa',
 'C': '#dfd0ca'}


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
