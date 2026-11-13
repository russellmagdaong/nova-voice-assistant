"""Fox: original NOVA pixel mascot, with hands resting on the screen edge."""
from __future__ import annotations

from PySide6.QtGui import QImage

from .pixelart import outlined_icon, render, sym

NAME = 'Fox'
COLS, ROWS = 20, 19
PEEK = {'idle': 12, 'sleep': 12, 'hover': 16, 'active': 18}
ZZZ = (6, 2)

PALETTE = {
    'K': '#553329',  # warm dark outline and ear tips
    'O': '#ec8736',  # orange fur
    'L': '#ffb653',  # sunlit forehead
    'S': '#c76530',  # fur shadow and paw creases
    'C': '#fff0d2',  # cream muzzle and ear lining
    'H': '#ffffff',  # eye glints
    'E': '#302c38',  # eyes and nose
    'P': '#e79989',  # inner ears and tongue
    'M': '#713b47',  # open mouth
    'G': '#baae9f',  # muted cream
}

# Mirror the left half: every character is one pixel.
BASE = [sym(r) for r in [
    '..........',  # 0
    '..K.......',  # 1
    '..KK......',  # 2
    '..KCK.....',  # 3
    '..KCPK....',  # 4
    '..KCPOKKKK',  # 5
    '..KCOOOOOO',  # 6
    '..KOOOLLLL',  # 7
    '.KOOOOOOOO',  # 8
    '.KOOOOOOOO',  # 9
    '.KOOOOOOOO',  # 10
    'KCCOOOOOOO',  # 11
    'KCCCCOOOCE',  # 12
    '.KCCCCCCCC',  # 13
    '..KCCCCCCC',  # 14
    '...KKCCCCC',  # 15
    '...KOSKCCC',  # 16
    '..KCOCSKCC',  # 17
    '..KCSCSKCC',  # 18
]]

EYE_COLS = (4, 12)

EYE_ROW = 9

EYES = {'open': ['OEEO', 'OHEO', 'OOOO'],
 'wide': ['OEEO', 'OHEO', 'OEEO'],
 'left': ['EEOO', 'HEOO', 'OOOO'],
 'right': ['OOEE', 'OOHE', 'OOOO'],
 'up': ['OOHE', 'OOEE', 'OOOO'],
 'blink': ['OOOO', 'EEEE', 'OOOO'],
 'closed': ['OOOO', 'EOOE', 'OEEO'],
 'sad': ['OOOO', 'OEEO', 'OHEO']}

MOUTHS = {'idle': {(9, 14): 'E', (10, 14): 'E'},
 'talk': {(9, 14): 'M', (10, 14): 'M', (9, 15): 'P', (10, 15): 'P'},
 'sad': {(9, 14): 'E', (10, 14): 'E', (8, 15): 'E', (11, 15): 'E'}}

POSES = {'listen': {(2, 0): 'K', (17, 0): 'K', (3, 3): 'C', (16, 3): 'C'},
 'sleep': {(2, 1): '.', (17, 1): '.', (3, 3): 'P', (16, 3): 'P'},
 'sad': {(2, 1): '.',
         (17, 1): '.',
         (2, 2): '.',
         (17, 2): '.',
         (1, 4): 'K',
         (18, 4): 'K',
         (5, 8): 'S',
         (6, 7): 'S',
         (13, 7): 'S',
         (14, 8): 'S'}}

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
    '.K..........K.',
    '.KK........KK.',
    '.KCK......KCK.',
    '.KCOKKKKKKOCK.',
    '.KOOOOOOOOOOK.',
    'KOOOLLLLLLOOOK',
    'KOOEOOOOOOEOOK',
    'KOOEOOOOOOEOOK',
    'KCCCOOEEOOCCCK',
    '.KCCCCCCCCCCK.',
    '..KCCCEECCCK..',
    '...KKCCCCKK...',
]

ICON_SLEEP = {(3, 6): 'O',
 (10, 6): 'O',
 (2, 7): 'E',
 (3, 7): 'E',
 (4, 7): 'E',
 (9, 7): 'E',
 (10, 7): 'E',
 (11, 7): 'E'}

MUTED = {'O': '#a18b7d', 'L': '#beab95', 'S': '#82746e', 'C': '#d3c9bc', 'P': '#ad9596'}


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
