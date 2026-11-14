"""Notch and tray icon. Runs on the Qt main thread.

The notch's look is picked in tray > Mascot (remembered):
- a pixel-art mascot peeking up from the bottom edge of the screen (cat, robot, fox, frog, ghost,
  axolotl: pixel*.py), with a card above it for what you say and what NOVA answers;
- "orb": the classic notch, a thin pill that opens into a panel with an animated wireframe orb
  (orb.py) beside the text.
The card is its own small window. In dark mode it's black glass (translucent black, no blur);
in light mode Windows blurs what's behind it (acrylic, like Start and the flyouts), or where that
isn't available it's simply translucent.

The worker thread talks to the UI only through Bridge signals (Qt queues them across threads).
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import random
import time

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor, QFont, QFontMetricsF, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import orb, pixelart, pixelaxolotl, pixelbot, pixelcat, pixelfox, pixelfrog, pixelghost, themes

log = logging.getLogger("nova.ui")

# States sent by the assistant.
LOADING, IDLE, LISTENING, THINKING, SEARCHING, SPEAKING, ALERT, MUTED, ERROR = (
    "loading", "idle", "listening", "thinking", "searching", "speaking", "alert", "muted", "error")
ACTIVE = (LISTENING, THINKING, SEARCHING, SPEAKING, ALERT, ERROR)

STATUS_TEXT = {LOADING: "Waking up", LISTENING: "Listening", THINKING: "Thinking", SEARCHING: "Searching the web",
               SPEAKING: "Nova", ALERT: "Reminder", MUTED: "Asleep", ERROR: "Something went wrong", IDLE: ""}

MASCOTS = {"cat": pixelcat, "robot": pixelbot, "fox": pixelfox, "frog": pixelfrog, "ghost": pixelghost,
           "axolotl": pixelaxolotl, "orb": orb}
MASCOT_MENU = [(key, m.NAME) for key, m in MASCOTS.items()]


class Bridge(QObject):
    """Signals from the worker thread to the UI, and user actions back."""
    state = Signal(str)
    heard = Signal(str)          # what the user said (final, from Whisper)
    partial = Signal(str)        # live caption while the user is talking ("" clears it)
    answer = Signal(str)         # answer text so far (replaces previous)
    activity = Signal(str)       # what NOVA is looking up ("Checking the weather"), shown while SEARCHING
    countdown = Signal(float)    # 1..0 time left to reply in a conversation; -1 hides it
    # user actions (connected by the assistant / main)
    stop_requested = Signal()
    mute_toggled = Signal(bool)
    new_conversation = Signal()
    voice_selected = Signal(str)
    notch_toggled = Signal(bool)
    mascot_selected = Signal(str)
    settings_requested = Signal(str)   # tray > Settings (page key, "" = Home)
    restart_requested = Signal()       # tray > Restart NOVA (picks up a new build)
    hud_requested = Signal()           # tray > Open NOVA (its own window, hud.py)
    talk_requested = Signal()          # click on the mascot in NOVA's window: listen, like "Hey Nova"
    quit_requested = Signal()


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def font(size: float, weight=QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    # Words are drawn one at a time; without hinting their measured and drawn widths agree.
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


# ---- Windows look ------------------------------------------------------------------------------
def windows_theme() -> tuple[bool, QColor | None]:
    """(light taskbar theme?, accent colour in the shade Windows uses on that theme)."""
    light, accent = False, None
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Themes\Personalize") as k:
            light = winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Explorer\Accent") as k:
            pal = bytes(winreg.QueryValueEx(k, "AccentPalette")[0])    # 8 RGBA shades, lightest first
        i = 4 if light else 1        # darker shade on light surfaces, lighter one on dark (like Windows)
        accent = QColor(pal[i * 4], pal[i * 4 + 1], pal[i * 4 + 2])
    except (OSError, ImportError, IndexError):
        pass
    return light, accent


class Theme:
    """Windows 11 (Fluent) colours, light or dark to match the taskbar."""

    def __init__(self, light: bool, accent: QColor | None, override: str = ""):
        self.light = light
        self.accent = QColor(override) if override else (accent or QColor("#005FB8" if light else "#60CDFF"))
        if light:
            # Acrylic tint (Windows blurs behind it). Its colour, not its alpha, decides how much shows
            # through: Windows pulls the blur's brightness towards it. Closer to mid-grey = clearer.
            self.tint = QColor(215, 215, 215, 40)
            self.card_blur = QColor(255, 255, 255, 10)      # painted over the acrylic
            self.card_plain = QColor(249, 249, 249, 175)    # no acrylic: just translucent
            self.pill, self.stroke = QColor(238, 238, 238, 175), QColor(0, 0, 0, 30)
            self.text, self.text2, self.text3 = QColor(0, 0, 0, 228), QColor(0, 0, 0, 158), QColor(0, 0, 0, 114)
            self.track = QColor(0, 0, 0, 36)
            self.critical, self.caution = QColor("#C42B1C"), QColor("#9D5D00")
        else:
            # Black glass, no blur (the user's choice): with acrylic, Windows pulls a black tint to
            # solid black, so nothing would show through.
            self.tint = None
            self.card_plain = QColor(0, 0, 0, 165)
            self.card_blur = self.card_plain
            self.pill, self.stroke = QColor(0, 0, 0, 175), QColor(255, 255, 255, 30)
            self.text, self.text2, self.text3 = (QColor(255, 255, 255), QColor(255, 255, 255, 200),
                                                 QColor(255, 255, 255, 139))
            self.track = QColor(255, 255, 255, 46)
            self.critical, self.caution = QColor("#FF99A4"), QColor("#FCE100")


def set_acrylic(hwnd: int, tint: QColor | None) -> bool:
    """Windows' acrylic (blur + tint behind the window, like Start and the flyouts) and rounded
    corners; tint None switches the blur off. False if there's no blur (off, or this Windows
    can't do it)."""
    try:
        from ctypes import wintypes

        class Accent(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class Data(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(Accent)),
                        ("SizeOfData", ctypes.c_size_t)]

        user32, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")
        user32.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.POINTER(Data)]
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        if tint is None:
            accent = Accent(0, 0, 0, 0)                       # 0 = off
        else:
            abgr = (tint.alpha() << 24) | (tint.blue() << 16) | (tint.green() << 8) | tint.red()
            accent = Accent(4, 2, abgr, 0)                    # 4 = acrylic blur behind
        data = Data(19, ctypes.pointer(accent), ctypes.sizeof(accent))   # 19 = accent policy
        ok = bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
        round_ = ctypes.c_int(2)                              # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_), 4)
        return ok and tint is not None
    except (OSError, AttributeError):
        return False


def fullscreen_app_in_front(hwnd: int, fg: int | None = None) -> bool:
    """True if a fullscreen app (a game, a video, a presentation) is in front on the monitor that
    holds window `hwnd` (the notch). A maximised window is not fullscreen; the desktop isn't.
    `fg`: check this window instead of the one in front (for tests)."""
    try:
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32, shell32 = ctypes.WinDLL("user32"), ctypes.WinDLL("shell32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

        # Games in exclusive fullscreen and presentation mode (Windows knows these directly).
        quns = ctypes.c_int(0)
        if fg is None:
            if shell32.SHQueryUserNotificationState(ctypes.byref(quns)) == 0 and quns.value in (3, 4):
                return True
            fg = user32.GetForegroundWindow()
        if not fg:
            return False
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(fg, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False                                  # the desktop or the taskbar
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False                                  # NOVA's own windows
        mon = user32.MonitorFromWindow(fg, 2)             # MONITOR_DEFAULTTONEAREST
        if mon != user32.MonitorFromWindow(hwnd, 2):
            return False                                  # fullscreen on another monitor
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        r = wintypes.RECT()
        if not user32.GetMonitorInfoW(mon, ctypes.byref(info)) or not user32.GetWindowRect(fg, ctypes.byref(r)):
            return False
        m = info.rcMonitor
        if not (r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom):
            return False
        caption = (user32.GetWindowLongW(fg, -16) & 0x00C00000) == 0x00C00000   # GWL_STYLE, WS_CAPTION
        return not (caption and user32.IsZoomed(fg))     # (maximised with an auto-hidden taskbar)
    except (OSError, AttributeError):
        return False


# ---- pixel mascots -------------------------------------------------------------------------------
GAZE_S = 5.0             # the eyes follow the cursor for this long after it last moved
GAZE_FRAMES = {(-1, -1): "look_up_left", (0, -1): "look_up", (1, -1): "look_up_right",
               (-1, 0): "look_left", (0, 0): "idle", (1, 0): "look_right",
               (-1, 1): "look_down_left", (0, 1): "look_down", (1, 1): "look_down_right"}


def gaze_toward(eye_x: float, eye_y: float, x: float, y: float, near: float) -> tuple[int, int]:
    """Which way to look at (x, y) from eyes at (eye_x, eye_y): (dx, dy), each -1, 0 or 1. Straight
    ahead when the point is close; otherwise the nearest of eight directions."""
    vx, vy = x - eye_x, y - eye_y
    d = math.hypot(vx, vy)
    if d < near:
        return 0, 0
    nx, ny = vx / d, vy / d
    return (0 if abs(nx) < 0.38 else 1 if nx > 0 else -1), (0 if abs(ny) < 0.38 else 1 if ny > 0 else -1)


class Sprite:
    """A pixel-art mascot (pixelcat / pixelbot): picks its frame (state, blinks, glances, talking
    mouth) and draws it on whole screen pixels, scaled up without smoothing."""

    PX = 3               # one art pixel, in Windows' scaled pixels (rounded to whole screen pixels)

    def __init__(self, art):
        self.art = art
        self.rim = True                  # outline for dark themes (if the mascot uses one)
        self._cache: dict = {}
        self.cur = "sleep"
        now = time.monotonic()
        self.next_blink = now + random.uniform(2, 5)
        self.blink_until = 0.0
        self.next_glance = now + random.uniform(6, 12)
        self.glance_until = 0.0
        self.glance = "look_left"
        self.mouth = False
        self.mouth_t = 0.0
        self.gaze: tuple[int, int] | None = None     # towards the cursor (set by the window), or None

    def gaze_frame(self) -> str:
        """The look for self.gaze; a mascot without that one looks the nearest way it can."""
        dx, dy = self.gaze
        for key in ((dx, dy), (dx, 0)):
            if GAZE_FRAMES[key] in self.art.FRAMES:
                return GAZE_FRAMES[key]
        return "idle"

    @property
    def height(self) -> int:
        return self.art.ROWS * self.PX

    @property
    def width(self) -> int:
        return self.art.COLS * self.PX

    def scale(self, dpr: float) -> int:
        """Screen pixels per art pixel."""
        return max(1, round(self.PX * dpr))

    def pixmap(self, name: str, dpr: float) -> QPixmap:
        s = self.scale(dpr)
        key = (name, self.rim, s)
        pm = self._cache.get(key)
        if pm is None:
            img = self.art.image(name, self.rim)
            pm = QPixmap.fromImage(img.scaled(img.width() * s, img.height() * s,
                                              Qt.IgnoreAspectRatio, Qt.FastTransformation))
            pm.setDevicePixelRatio(dpr)
            self._cache[key] = pm
        return pm

    def update(self, state: str, hovered: bool, spk: float, now: float) -> bool:
        """Advance blinks, glances and the mouth. True if the frame changed."""
        if now >= self.next_blink:
            self.blink_until = now + 0.13
            # Now and then a double blink.
            self.next_blink = now + (0.32 if random.random() < 0.2 else random.uniform(2.5, 6.5))
        if state == IDLE and not hovered and self.gaze is None and now >= self.next_glance:
            self.glance = random.choice(("look_left", "look_right"))
            self.glance_until = now + random.uniform(0.9, 1.8)
            self.next_glance = now + random.uniform(7, 16)
        if state == SPEAKING:
            # Open on louder speech, close when it quietens; at most ~12 changes a second.
            want = spk > (0.05 if self.mouth else 0.1)
            if want != self.mouth and now - self.mouth_t > 0.085:
                self.mouth, self.mouth_t = want, now
        else:
            self.mouth = False
        f = self._pick(state, hovered, now)
        changed, self.cur = f != self.cur, f
        return changed

    def _pick(self, state: str, hovered: bool, now: float) -> str:
        blinking = now < self.blink_until
        if state in (MUTED, LOADING):
            return "sleep"
        if state == ERROR:
            return "sad"
        if state in (LISTENING, ALERT):
            return "listen"                          # attentive: no blinking
        if state == SPEAKING:
            return "talk" if self.mouth else "blink" if blinking else "idle"
        if state == THINKING:
            return "blink" if blinking else "think"
        if state == SEARCHING:                       # looks around, as if reading
            return ("look_left", "look_right")[int(now / 0.6) % 2]
        if hovered:
            return "listen"
        if self.gaze is not None:                    # following the cursor
            return "blink" if blinking else self.gaze_frame()
        if now < self.glance_until:
            return self.glance
        return "blink" if blinking else "idle"

    def paint(self, qp: QPainter, cx: float, bottom: float, sink: int, dx: int = 0):
        """Draw centred on `cx` with the bottom row on `bottom`, moved down `sink` and right `dx`
        art pixels. Snapped to whole screen pixels so every art pixel stays a crisp square."""
        dpr = qp.device().devicePixelRatioF()
        s = self.scale(dpr)
        x = round(cx * dpr - self.art.COLS * s / 2) + dx * s
        y = round(bottom * dpr) - self.art.ROWS * s + sink * s
        qp.drawPixmap(QPointF(x / dpr, y / dpr), self.pixmap(self.cur, dpr))


# ---- text that flows in ------------------------------------------------------------------------
class WordFlow:
    """Text whose new words fade and rise in. Words already shown keep their place."""

    FADE = 0.22        # seconds for a word to fade in
    STAGGER = 0.035    # delay between words that arrive together

    def __init__(self):
        self.words: list[str] = []
        self.born: list[float] = []

    def set(self, text: str, animate: bool = True):
        new = text.split()
        now = time.monotonic()
        keep = 0
        while keep < min(len(new), len(self.words)) and new[keep].lower() == self.words[keep].lower():
            keep += 1
        born = self.born[:keep]
        start = max([now] + [b for b in born[-1:]])
        for i in range(len(new) - keep):
            born.append(start + i * self.STAGGER if animate else now - 10)
        self.words, self.born = new, born

    def clear(self):
        self.words, self.born = [], []

    @property
    def text(self) -> str:
        return " ".join(self.words)

    def settled(self) -> bool:
        return not self.born or time.monotonic() - self.born[-1] > self.FADE

    def layout(self, fm: QFontMetricsF, width: float) -> list[list[tuple[int, float]]]:
        """Greedy word wrap -> lines of (word index, x)."""
        space = fm.horizontalAdvance(" ")
        lines: list[list[tuple[int, float]]] = [[]]
        x = 0.0
        for i, w in enumerate(self.words):
            wa = fm.horizontalAdvance(w)
            if lines[-1] and x + wa > width:
                lines.append([])
                x = 0.0
            lines[-1].append((i, x))
            x += wa + space
        return lines if self.words else []

    def draw(self, qp: QPainter, f: QFont, color: QColor, x0: float, y0: float, width: float,
             max_lines: int) -> float:
        """Paint the last `max_lines` lines (an earlier line cut off shows faded). Returns the height."""
        fm = QFontMetricsF(f)
        lines = self.layout(fm, width)
        cut = len(lines) > max_lines
        lines = lines[-max_lines:]
        now = time.monotonic()
        qp.setFont(f)
        ls = fm.lineSpacing()
        for li, line in enumerate(lines):
            line_alpha = 0.45 if (cut and li == 0) else 1.0
            baseline = y0 + li * ls + fm.ascent()
            if all(now - self.born[idx] >= self.FADE for idx, _ in line):
                # Settled: draw the line as one run (per-word drawing makes the gaps uneven).
                c = QColor(color)
                c.setAlphaF(color.alphaF() * line_alpha)
                qp.setPen(c)
                qp.drawText(QPointF(x0, baseline), " ".join(self.words[idx] for idx, _ in line))
                continue
            first = line[0][0]
            for idx, _ in line:
                # Position from the run so far, so the line doesn't shift when it settles.
                x = fm.horizontalAdvance(" ".join(self.words[first:idx] + [""])) if idx > first else 0.0
                t = clamp((now - self.born[idx]) / self.FADE)
                ease = 1 - (1 - t) ** 3
                c = QColor(color)
                c.setAlphaF(color.alphaF() * ease * line_alpha)
                qp.setPen(c)
                qp.drawText(QPointF(x0 + x, baseline + (1 - ease) * 5), self.words[idx])
        return len(lines) * ls

    def height(self, f: QFont, width: float, max_lines: int) -> float:
        fm = QFontMetricsF(f)
        return min(len(self.layout(fm, width)), max_lines) * fm.lineSpacing()


# ---- the card (its own window, so Windows can blur behind it) ------------------------------------
class CardWindow(QWidget):
    def __init__(self, notch: "Notch"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.notch = notch
        self.blur = False
        self._tint = "unset"

    def set_backdrop(self, tint: QColor | None):
        key = tint.rgba() if tint is not None else None
        if key != self._tint:
            self._tint = key
            self.blur = set_acrylic(int(self.winId()), tint)
            log.debug("card backdrop: %s", "acrylic" if self.blur else "translucent")
            self.update()

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        self.notch.paint_card(qp, QRectF(self.rect()), self.blur)

    def enterEvent(self, e):
        self.notch.set_hover(card=True)

    def leaveEvent(self, e):
        self.notch.set_hover(card=False)

    def mousePressEvent(self, e):
        self.notch.mousePressEvent(e)


# ---- the notch ---------------------------------------------------------------------------------
class Notch(QWidget):
    W, H = 240, 100                    # the mascot's window (transparent); the card has its own
    GAP = 8                            # between a peeking mascot and its card
    CARD_W, CARD_R = 340, 8
    PAD_X, PAD_TOP, PAD_BOTTOM = 12, 9, 11
    MIN_CARD_H, MAX_CARD_H = 38, 150
    CAPTION_LINES, ANSWER_LINES = 2, 4
    # The orb's notch: a pill that opens into a panel, the orb at its left.
    PILL_W, PILL_H = 110, 10
    PANEL_W, PANEL_MIN_H, EDGE = 380, 64, 10
    ORB_X, ORB_R, TEXT_X = 38, 20, 74

    F_STATUS = font(8.5)               # Windows "Caption"
    F_CAPTION = font(11)               # your words while you talk
    F_ANSWER = font(10)                # Windows "Body"
    F_YOU = font(8.5)                  # your question, small, above the answer
    F_HINT = font(10)

    FAST_MS, SLOW_MS = 16, 66

    def __init__(self, bridge: Bridge, levels, accent: str = "", mascot: str = "cat",
                 hide_in_fullscreen: bool = True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.bridge = bridge
        self.levels = levels           # callable -> (mic_level, speaker_level)
        self.accent_override = accent
        self.theme = Theme(*windows_theme(), accent)
        self._theme_checked = time.monotonic()
        self.card_win = CardWindow(self)
        self.card_win.set_backdrop(self.theme.tint)
        self.shown = False             # on screen now (set by _apply_shown)
        self.user_shown = True         # tray > Show notch
        self.fullscreen = False        # a fullscreen app (game, video) is in front: stay out of the way
        self.hud_front = False         # NOVA's own window is in front (it shows all this, bigger)
        self.hide_in_fullscreen = hide_in_fullscreen

        self.state = LOADING
        self.state_t = time.monotonic()
        self.you = WordFlow()          # what you said (final), or the live caption
        self.live = False              # self.you is a live caption
        self.answer = WordFlow()
        self.activity = ""             # what it's looking up, while SEARCHING
        self.upcoming = None           # callable -> "pasta timer 3:12 · call Mum, 5:00 PM" (or "")
        self.hover_main = self.hover_card = False
        self.hover_until = 0.0         # moving from the mascot to the card crosses a gap
        self.linger_until = 0.0
        self.p, self.pv = 0.0, 0.0     # card shown 0..1 (spring) and its velocity
        self.peek, self.peek_v = 0.6, 0.0
        self.h = float(self.MIN_CARD_H)  # card height and width (ease to the content)
        self.cw = float(self.CARD_W)
        self.mic_l = self.spk_l = 0.0  # smoothed levels
        self.bars = [0.0] * 4
        self.countdown = -1.0
        self.t0 = time.monotonic()
        self._last_tick = time.monotonic()
        self._frame = 0
        self._cursor = QCursor.pos()   # where the cursor was, and when it last moved (the eyes follow it)
        self._cursor_t = -1e9

        self.tick = QTimer(self, interval=self.FAST_MS)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start()
        self.collapse_timer = QTimer(self, singleShot=True)
        self.collapse_timer.timeout.connect(self._wake)
        self.fs_timer = QTimer(self, interval=1000)
        self.fs_timer.timeout.connect(self._check_fullscreen)
        self.fs_timer.start()
        self.set_mascot(mascot)

        bridge.state.connect(self.set_state)
        bridge.heard.connect(self._set_heard)
        bridge.partial.connect(self._set_partial)
        bridge.answer.connect(self._set_answer)
        bridge.activity.connect(self._set_activity)
        bridge.countdown.connect(self._set_countdown)

        self.resize(self.W, self.H)
        self._place()
        QApplication.primaryScreen().availableGeometryChanged.connect(lambda *_: self._place())

    # ---- mascot --------------------------------------------------------------------------
    def set_mascot(self, name: str):
        """Switch between the cat, the robot and the orb (tray > Mascot)."""
        if name not in MASCOTS:
            name = "cat"
        self.mascot = name
        self.sprite = None if name == "orb" else Sprite(MASCOTS[name])
        if self.sprite:
            self.sprite.rim = not self.theme.light
            self.peek, self.peek_v = self._peek_target(), 0.0
        self.card_win.hide()
        self.update()
        self._wake()

    @property
    def orb_mode(self) -> bool:
        return self.sprite is None

    def set_shown(self, on: bool):
        """Show or hide the notch (tray > Show notch)."""
        self.user_shown = on
        self._apply_shown()

    def set_hud_front(self, on: bool):
        """NOVA's window is in front (active, maximised or fullscreen): the notch steps aside."""
        if on != self.hud_front:
            self.hud_front = on
            self._apply_shown()

    def _check_fullscreen(self):
        fs = self.hide_in_fullscreen and fullscreen_app_in_front(int(self.winId()))
        if fs != self.fullscreen:
            self.fullscreen = fs
            log.info("fullscreen app %s: notch %s", "in front" if fs else "gone", "hidden" if fs else "back")
            self._apply_shown()

    def _apply_shown(self):
        """Shown if the user wants it and no fullscreen app is in front. Hidden, it also stops
        animating."""
        on = self.user_shown and not self.fullscreen and not self.hud_front
        self.shown = on
        if on:
            self.tick.start()
            self.show()
        else:
            self.tick.stop()
            self.hide()
            self.card_win.hide()

    # ---- placement / expansion -------------------------------------------------
    def _place(self):
        # The window sits on the bottom of the work area (the screen edge, or the top of a bottom
        # taskbar): the mascot peeks up over it.
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.x() + (g.width() - self.W) // 2, g.y() + g.height() - self.H)

    @property
    def hovered(self) -> bool:
        return self.hover_main or self.hover_card or time.monotonic() < self.hover_until

    def set_hover(self, main: bool | None = None, card: bool | None = None):
        if main is not None:
            self.hover_main = main
        if card is not None:
            self.hover_card = card
        if not (self.hover_main or self.hover_card):
            self.hover_until = time.monotonic() + 0.35
        self._wake()

    def _want_card(self) -> bool:
        busy = self.state in (LOADING,) + ACTIVE
        return busy or self.hovered or time.monotonic() < self.linger_until

    def _peek_target(self) -> float:
        art = self.sprite.art
        if self.state in ACTIVE:
            rows = art.PEEK["active"]
        elif self.hovered or time.monotonic() < self.linger_until:
            rows = art.PEEK["hover"]
        else:
            rows = art.PEEK["sleep" if self.state in (MUTED, LOADING) else "idle"]
        return rows / art.ROWS

    # ---- signals -------------------------------------------------------------------
    def set_state(self, state: str):
        prev, self.state = self.state, state
        if state != prev:
            self.state_t = time.monotonic()
        if state == LISTENING and prev in (IDLE, MUTED, LOADING):
            self.you.clear()                              # a new conversation
            self.answer.clear()
            self.live = False
        if state != LISTENING and self.live:
            self.you.clear()                              # a caption that never became a question
            self.live = False
        if state == IDLE and prev in (SPEAKING, ERROR, ALERT) and self.answer.words:
            self.linger_until = time.monotonic() + 5.0    # keep the answer visible briefly
            self.collapse_timer.start(5100)
        self.update()
        self._wake()

    def _set_partial(self, text: str):
        if not text:
            if self.live:
                self.you.clear()
                self.live = False
        else:
            if not self.live:
                self.you.clear()
            self.live = True
            self.you.set(text)
        self._wake()

    def _set_heard(self, text: str):
        # Whisper's text replaces the live caption; words that match stay put.
        self.you.set(text, animate=True)
        self.live = False
        self.answer.clear()                               # new turn
        self._wake()

    def _set_countdown(self, v: float):
        self.countdown = v
        self._wake()

    def _set_answer(self, text: str):
        self.answer.set(text)
        self._wake()

    def _set_activity(self, text: str):
        self.activity = text
        self._wake()

    # ---- mouse ----------------------------------------------------------------------
    def enterEvent(self, e):
        self.set_hover(main=True)

    def leaveEvent(self, e):
        self.set_hover(main=False)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.bridge.stop_requested.emit()          # stop talking / cancel listening
        elif e.button() == Qt.RightButton and getattr(self, "menu", None):
            self.menu.popup(e.globalPosition().toPoint())

    # ---- animation tick -------------------------------------------------------------------
    def _on_tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last_tick)
        self._last_tick = now
        if now - self._theme_checked > 10:              # follow light/dark and accent changes
            self._theme_checked = now
            self.theme = Theme(*windows_theme(), self.accent_override)
            self.card_win.set_backdrop(self.theme.tint)
            if self.sprite:
                self.sprite.rim = not self.theme.light
        mic, spk = self.levels()
        self.mic_l += (mic - self.mic_l) * 0.35
        self.spk_l += (spk - self.spk_l) * 0.45

        # Card: Windows-like (quick, barely any bounce).
        target = 1.0 if self._want_card() else 0.0
        k = 260.0
        self.pv += (k * (target - self.p) - 2 * math.sqrt(k) * 0.9 * self.pv) * dt
        self.p += self.pv * dt
        if abs(target - self.p) < 0.002 and abs(self.pv) < 0.01:
            self.p, self.pv = target, 0.0

        # Peeking mascot: pops up with a little overshoot, sinks back without.
        moving = False
        if self.sprite:
            goal = self._peek_target()
            k = 170.0
            ratio = 0.5 if goal > self.peek else 0.95
            self.peek_v += (k * (goal - self.peek) - 2 * math.sqrt(k) * ratio * self.peek_v) * dt
            self.peek += self.peek_v * dt
            moving = abs(goal - self.peek) > 0.001 or abs(self.peek_v) > 0.005
            if not moving:
                self.peek, self.peek_v = goal, 0.0

        card = target > 0 or self.p > 0
        if card:                                        # (measuring the text is the costly part)
            self.h += (self._content_height() - self.h) * min(1.0, dt * 12)
            self.cw += (self._content_width() - self.cw) * min(1.0, dt * 14)

        level = self.mic_l if self.state == LISTENING else self.spk_l if self.state == SPEAKING else 0.0
        for i in range(len(self.bars)):
            wobble = 0.55 + 0.45 * math.sin(now * (9 + i * 2.3) + i * 1.7)
            goal_b = clamp(level * 2.2 * wobble) if level > 0.02 else 0.0
            self.bars[i] += (goal_b - self.bars[i]) * 0.4

        if self.sprite:
            self.sprite.gaze = self._gaze(now)
        frame_changed = self.sprite.update(self.state, self.hovered, self.spk_l, now) if self.sprite else False
        self._sync_card()
        busy = card or self.state in ACTIVE or moving
        if busy or frame_changed:
            self.update()
        elif self.sprite and self.state in (MUTED, LOADING):
            self.update(self._zzz_rect())               # asleep: repaint just the drifting z's
        elif self.orb_mode:
            self.update(self._pill_rect().toAlignedRect().adjusted(-2, -2, 2, 2))   # the pill breathes
        # 60 fps while anything moves; 15 fps at rest (enough for blinks, z's and the pill).
        interval = self.FAST_MS if busy else self.SLOW_MS
        if self.tick.interval() != interval:
            self.tick.setInterval(interval)

    def _gaze(self, now: float) -> tuple[int, int] | None:
        """Towards the cursor, for a few seconds after it moves (None: look around as usual)."""
        p = QCursor.pos()
        if p != self._cursor:
            self._cursor, self._cursor_t = p, now
        if now - self._cursor_t > GAZE_S:
            return None
        art = self.sprite.art
        top = self.H - round(self.peek * art.ROWS) * Sprite.PX
        return gaze_toward(self.x() + self.W / 2, self.y() + top + art.ROWS * 0.5 * Sprite.PX, p.x(), p.y(),
                           near=art.COLS * Sprite.PX * 0.6)

    def _wake(self):
        """Something changed: animate at full speed from the next frame."""
        if self.shown and self.tick.interval() != self.FAST_MS:
            self.tick.setInterval(self.FAST_MS)

    # ---- the card's window -----------------------------------------------------------------
    def _sync_card(self):
        """Place, fade and repaint the card's window (its own window, for the blur behind it)."""
        if self.p <= 0.01 or not self.shown:
            if self.card_win.isVisible():
                self.card_win.hide()
            return
        pc = clamp(self.p)
        if self.orb_mode:
            r = self._panel_rect()                      # grows out of the pill
            opacity = 1.0
        else:
            r = self._card_rect().translated(0, (1 - pc) * 8)   # rises a little as it fades in
            opacity = clamp(self.p * 1.4)
        g = QRect(self.x() + round(r.x()), self.y() + round(r.y()), max(1, round(r.width())),
                  max(1, round(r.height())))
        if self.card_win.geometry() != g:
            self.card_win.setGeometry(g)
        if abs(self.card_win.windowOpacity() - opacity) > 0.01:
            self.card_win.setWindowOpacity(opacity)
        if not self.card_win.isVisible():
            self.card_win.show()
        # Once it has opened and its words have settled, 30 fps is plenty (the orb turns slowly).
        self._frame += 1
        settled = self.p == 1.0 and self.you.settled() and self.answer.settled()
        if not settled or self._frame % 2 == 0:
            self.card_win.update()

    # ---- layout ---------------------------------------------------------------------------
    def _text_w(self) -> float:
        if self.orb_mode:
            return self.PANEL_W - self.TEXT_X - self.PAD_X
        return self.CARD_W - 2 * self.PAD_X

    def _mode(self) -> str:
        if self.live and self.you.words:
            return "caption"
        if self.answer.words:
            return "answer"
        if self.you.words:
            return "question"
        return "hint"

    def _progress(self) -> str:
        """The thin bar along the card's bottom: thinking (moving) or time left to reply."""
        if self.state in (THINKING, SEARCHING):
            return "busy"
        if self.state == LISTENING and self.countdown >= 0 and not self.live:
            return "countdown"
        return ""

    def _status(self) -> str:
        if self.state == LISTENING and self.countdown >= 0 and not self.live:
            return "Your turn"
        if self.state == IDLE and self.answer.words:
            return "Nova"
        if self.state in (SEARCHING, ALERT) and self.activity:
            return self.activity
        return STATUS_TEXT.get(self.state, "")

    def _hint(self) -> str:
        if self.state in (IDLE, MUTED) and self.upcoming:     # the next timer / reminder, if any
            nxt = self.upcoming()
            if nxt:
                return f"Next: {nxt}"
        return {IDLE: "Say “Hey Nova”", MUTED: "Click the tray icon to wake me up.",
                LOADING: "Loading voices and models…", LISTENING: "Go ahead, I'm listening…"}.get(self.state, "")

    def _content_height(self) -> float:
        w = self._text_w()
        mode = self._mode()
        if mode == "caption":
            body = self.you.height(self.F_CAPTION, w, self.CAPTION_LINES)
        elif mode == "answer":
            body = self.answer.height(self.F_ANSWER, w, self.ANSWER_LINES)
            if self.you.words:
                body += QFontMetricsF(self.F_YOU).lineSpacing() + 4
        elif mode == "question":
            body = self.you.height(self.F_CAPTION, w, self.CAPTION_LINES)
        else:
            body = QFontMetricsF(self.F_HINT).lineSpacing()
        status = QFontMetricsF(self.F_STATUS).lineSpacing() + 5 if self._status() else 0
        extra = 4 if self._progress() else 0
        lo = self.PANEL_MIN_H if self.orb_mode else self.MIN_CARD_H
        return clamp(self.PAD_TOP + status + body + self.PAD_BOTTOM + extra, lo, self.MAX_CARD_H)

    def _content_width(self) -> float:
        """Full width for text; a short hint gets a card that fits it (like a Windows teaching tip)."""
        if self.orb_mode:
            return self.PANEL_W
        if self._mode() != "hint":
            return self.CARD_W
        widths = [QFontMetricsF(self.F_HINT).horizontalAdvance(self._hint())]
        if self._status():
            widths.append(QFontMetricsF(self.F_STATUS).horizontalAdvance(self._status()) + 30)
        return clamp(max(widths) + 2 * self.PAD_X + 2, 120, self.CARD_W)

    def _card_rect(self) -> QRectF:
        """The card above a peeking mascot (in this window's coordinates; it may go above it)."""
        bottom = self.H - self.sprite.height - self.GAP
        return QRectF((self.W - self.cw) / 2, bottom - self.h, self.cw, self.h)

    def _pill_rect(self) -> QRectF:
        return QRectF((self.W - self.PILL_W) / 2, self.H - self.EDGE - self.PILL_H, self.PILL_W, self.PILL_H)

    def _panel_rect(self) -> QRectF:
        """The orb's notch, between the pill (p=0) and the open panel (p=1)."""
        pc = clamp(self.p)
        w, h = lerp(self.PILL_W, self.PANEL_W, pc), lerp(self.PILL_H, self.h, pc)
        return QRectF((self.W - w) / 2, self.H - self.EDGE - h, w, h)

    # ---- painting: the mascot window -------------------------------------------------------
    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        # Invisible hover/click zone (alpha 0 would be click-through).
        if self.orb_mode:
            zone = QRectF((self.W - 220) / 2, self.H - 34, 220, 34)
        else:
            shown = max(self.peek * self.sprite.height, 30)
            zone = QRectF((self.W - self.sprite.width) / 2 - 8, self.H - shown, self.sprite.width + 16, shown)
        qp.fillRect(zone, QColor(0, 0, 0, 1))
        if self.orb_mode:
            if self.p <= 0.01:                          # (open, the card's window takes over)
                self._paint_pill(qp, self._pill_rect(), now, 1.0)
            return
        self._paint_sprite(qp, now)

    def _paint_sprite(self, qp: QPainter, now: float):
        since = now - self.state_t
        sink = round((1 - self.peek) * self.sprite.art.ROWS)   # whole art pixels, like a sprite
        dx = 0
        if self.state == LISTENING and self.mic_l > 0.12:
            sink -= 1                                      # hops while you talk
        elif self.state == SPEAKING and self.sprite.mouth:
            sink -= 1                                      # bobs as it talks
        elif self.state == ALERT and int(since / 0.25) % 2:
            sink -= 1
        elif self.state == ERROR and since < 0.5:
            dx = (1, -1, 1, -1, 1, 0)[min(int(since / 0.08), 5)]   # a quick shake
        self.sprite.paint(qp, self.W / 2, self.H, max(sink, 0), dx)
        if self.state in (MUTED, LOADING):
            self._paint_zzz(qp, now)

    def _zzz_origin(self) -> QPointF:
        """Where the z's start: beside the top of the sleeping mascot's head."""
        art = self.sprite.art
        top = self.H - round(self.peek * art.ROWS) * Sprite.PX
        return QPointF(self.W / 2 + art.ZZZ[0] * Sprite.PX, top + art.ZZZ[1] * Sprite.PX)

    def _zzz_rect(self):
        o = self._zzz_origin()
        return QRectF(o.x() - 2, o.y() - 30, 22, 34).toAlignedRect()

    def _paint_zzz(self, qp: QPainter, now: float):
        """Three small pixel z's drifting up from the sleeping mascot."""
        dpr = qp.device().devicePixelRatioF()
        zp = max(1, round(dpr)) / dpr                  # one z pixel: one screen pixel at 100%
        o = self._zzz_origin()
        c0 = QColor(self.theme.text2 if not self.theme.light else QColor(70, 70, 80))
        for i in range(3):
            ph = ((now - self.t0) / 3.6 + i / 3) % 1.0
            x = round((o.x() + ph * 10) * dpr) / dpr
            y = round((o.y() - 6 - ph * 20) * dpr) / dpr
            c = QColor(c0)
            c.setAlphaF(c0.alphaF() * (1.0 if ph < 0.7 else (1 - ph) / 0.3))
            for zy, row in enumerate(pixelart.Z):
                for zx, ch in enumerate(row):
                    if ch != ".":
                        qp.fillRect(QRectF(x + zx * zp, y + zy * zp, zp, zp), c)

    def _orb_colors(self):
        return orb.style(self.state, self.mic_l, self.spk_l)[3]

    def _paint_pill(self, qp: QPainter, r: QRectF, now: float, alpha: float, fill: bool = True):
        """The orb's collapsed notch: a small translucent pill with a line in the orb's colours."""
        qp.save()
        if fill:
            path = QPainterPath()
            path.addRoundedRect(r, r.height() / 2, r.height() / 2)
            qp.fillPath(path, self.theme.pill)
            qp.setPen(QPen(self.theme.stroke, 1.0))
            qp.drawPath(path)
        line = QRectF(r.center().x() - 26, r.center().y() - 1.5, 52, 3)
        g = QLinearGradient(line.topLeft(), line.topRight())
        breathe = 0.6 + 0.4 * math.sin((now - self.t0) * 1.6)
        for i, col in enumerate(self._orb_colors()):
            c = QColor(col)
            c.setAlphaF(alpha * (breathe if self.state != MUTED else 0.8))
            g.setColorAt(i / 2, c)
        lp = QPainterPath()
        lp.addRoundedRect(line, 1.5, 1.5)
        qp.fillPath(lp, g)
        qp.restore()

    # ---- painting: the card ------------------------------------------------------------------
    def paint_card(self, qp: QPainter, r: QRectF, blur: bool):
        """The card's window: a translucent (or acrylic) card with the status and text; in orb
        mode, the open notch with the orb beside the text."""
        th = self.theme
        now = time.monotonic()
        radius = min(self.CARD_R, r.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        qp.fillPath(path, th.card_blur if blur else th.card_plain)
        qp.setPen(QPen(th.stroke, 1.0))
        qp.drawPath(path)
        if not self.orb_mode:
            self._paint_content(qp, r, self.PAD_X, now)
            return
        pc = clamp(self.p)
        if pc < 0.45:                                    # the pill's line, fading as it opens
            self._paint_pill(qp, r, now, 1 - pc / 0.45, fill=False)
        if pc > 0.35:
            qp.save()
            qp.setOpacity(clamp((pc - 0.35) / 0.45))
            wobble, spin, glow, colors = orb.style(self.state, self.mic_l, self.spk_l)
            orb.paint(qp, QPointF(r.left() + self.ORB_X, r.center().y()), self.ORB_R, now - self.t0,
                      wobble, spin, glow, colors)
            self._paint_content(qp, r, self.TEXT_X, now)
            qp.restore()

    def _paint_content(self, qp: QPainter, r: QRectF, left: float, now: float):
        th = self.theme
        qp.save()
        qp.setClipRect(r.adjusted(0, 0, 0, -2))
        x = r.left() + left
        w = r.right() - self.PAD_X - x
        y = r.top() + self.PAD_TOP

        status = self._status()
        if status:
            fm = QFontMetricsF(self.F_STATUS)
            qp.setFont(self.F_STATUS)
            color = th.critical if self.state == ERROR else th.caution if self.state == ALERT else th.text2
            qp.setPen(color)
            qp.drawText(QPointF(x, y + fm.ascent()), status)
            if self.state in (LISTENING, SPEAKING):
                bx = x + fm.horizontalAdvance(status) + 8
                for i, b in enumerate(self.bars):
                    bh = 2.5 + b * 8
                    br = QRectF(bx + i * 4.5, y + fm.height() / 2 - bh / 2, 2.5, bh)
                    bp = QPainterPath()
                    bp.addRoundedRect(br, 1.25, 1.25)
                    qp.fillPath(bp, th.accent)
            y += fm.lineSpacing() + 5

        mode = self._mode()
        if mode == "caption":
            self.you.draw(qp, self.F_CAPTION, th.text, x, y, w, self.CAPTION_LINES)
            self._paint_caret(qp, x, y, w, now)
        elif mode == "question":
            self.you.draw(qp, self.F_CAPTION, th.text, x, y, w, self.CAPTION_LINES)
        elif mode == "answer":
            fy = QFontMetricsF(self.F_YOU)
            if self.you.words:
                qp.setFont(self.F_YOU)
                qp.setPen(th.text3)
                qp.drawText(QPointF(x, y + fy.ascent()), fy.elidedText("“" + self.you.text + "”", Qt.ElideRight, w))
                y += fy.lineSpacing() + 4
            self.answer.draw(qp, self.F_ANSWER, th.text, x, y, w, self.ANSWER_LINES)
        else:
            hint = self._hint()
            if hint:
                fh = QFontMetricsF(self.F_HINT)
                qp.setFont(self.F_HINT)
                qp.setPen(th.text2)
                if not status:
                    y = r.center().y() - fh.height() / 2          # alone: centre it in the card
                qp.drawText(QPointF(x, y + fh.ascent()), fh.elidedText(hint, Qt.ElideRight, w))
        qp.restore()
        self._paint_progress(qp, QRectF(x, r.top(), r.right() - self.PAD_X - x, r.height()), now)

    def _paint_progress(self, qp: QPainter, r: QRectF, now: float):
        """Windows-style progress bar along the bottom of the card (r: the text column)."""
        kind = self._progress()
        if not kind:
            return
        track = QRectF(r.left(), r.bottom() - 9, r.width(), 3)
        qp.save()
        qp.setPen(Qt.NoPen)
        tp = QPainterPath()
        tp.addRoundedRect(track.adjusted(0, 1, 0, -1), 0.5, 0.5)
        qp.fillPath(tp, self.theme.track)
        if kind == "countdown":
            seg = QRectF(track.left(), track.top(), track.width() * clamp(self.countdown), track.height())
        else:
            # Indeterminate: a segment that sweeps across, easing in and out.
            ph = ((now - self.t0) / 1.6) % 1.0
            e = ph * ph * (3 - 2 * ph)
            start = lerp(-0.45, 1.0, e)
            seg = QRectF(track.left() + track.width() * max(start, 0.0), track.top(),
                         track.width() * (min(start + 0.45, 1.0) - max(start, 0.0)), track.height())
        if seg.width() > 0.5:
            sp = QPainterPath()
            sp.addRoundedRect(seg, 1.5, 1.5)
            qp.fillPath(sp, self.theme.accent)
        qp.restore()

    def _paint_caret(self, qp: QPainter, x: float, y: float, w: float, now: float):
        """Blinking caret after the last live word."""
        fm = QFontMetricsF(self.F_CAPTION)
        lines = self.you.layout(fm, w)[-self.CAPTION_LINES:]
        if not lines or not lines[-1]:
            return
        idx, wx = lines[-1][-1]
        cx = x + wx + fm.horizontalAdvance(self.you.words[idx]) + 3
        cy = y + (len(lines) - 1) * fm.lineSpacing()
        c = QColor(self.theme.accent)
        c.setAlphaF(1.0 if (now - self.t0) % 1.0 < 0.55 else 0.0)    # on/off like a text caret
        qp.fillRect(QRectF(cx, cy + fm.ascent() * 0.12, 1.5, fm.ascent() * 1.0), c)


# ---- tray ----------------------------------------------------------------------------------
def nova_icon() -> QIcon:
    """NOVA's own logo in the current colour theme (themes.py, assets/nova.svg): for its windows.
    (The tray shows your mascot instead.)"""
    return themes.logo_icon()


def mascot_icon(name: str, muted: bool = False) -> QIcon:
    """The tray icon for a mascot (asleep / grey when muted). The pixel ones are drawn 16x16, the
    tray's size at 100% scaling, and scaled by whole numbers so they stay crisp."""
    icon = QIcon()
    if name == "orb":
        for s in (16, 20, 24, 32, 48, 64):
            icon.addPixmap(QPixmap.fromImage(orb.icon_image(s, muted)))
        return icon
    art = MASCOTS.get(name, pixelcat).icon_image(muted)
    for s in (1, 2, 3, 4):
        icon.addPixmap(QPixmap.fromImage(art.scaled(16 * s, 16 * s, Qt.IgnoreAspectRatio, Qt.FastTransformation)))
    return icon


class Tray(QSystemTrayIcon):
    def __init__(self, bridge: Bridge, voices: list | None = None, voice: str = "", notch_shown: bool = True,
                 mascot: str = "cat"):
        self.mascot = mascot if mascot in MASCOTS else "cat"
        super().__init__(mascot_icon(self.mascot))
        self.bridge = bridge
        self.setToolTip("NOVA: say “Hey Nova”")
        self.menu = QMenu()
        self.mute_action = QAction("Mute wake word", self.menu, checkable=True)
        self.mute_action.toggled.connect(self._mute)
        self.menu.addAction(self.mute_action)
        self.menu.addAction("New conversation", bridge.new_conversation.emit)
        self.notch_action = QAction("Show notch", self.menu, checkable=True)
        self.notch_action.setChecked(notch_shown)
        self.notch_action.toggled.connect(bridge.notch_toggled.emit)
        self.menu.addAction(self.notch_action)
        mm = self.menu.addMenu("Mascot")
        group = QActionGroup(mm)
        group.setExclusive(True)
        self.mascot_actions = {}
        for key, label in MASCOT_MENU:
            act = QAction(mascot_icon(key), label, mm, checkable=True)
            act.setChecked(key == self.mascot)
            act.triggered.connect(lambda _=False, k=key: bridge.mascot_selected.emit(k))
            group.addAction(act)
            mm.addAction(act)
            self.mascot_actions[key] = act
        self.voice = voice
        self.voice_actions = {}
        if voices:
            vm = self.menu.addMenu("Voice")
            group = QActionGroup(vm)
            group.setExclusive(True)
            for vid, label in voices:
                act = QAction(label, vm, checkable=True)
                act.setChecked(vid == voice)
                act.triggered.connect(lambda _=False, v=vid: self._pick_voice(v))
                group.addAction(act)
                vm.addAction(act)
                self.voice_actions[vid] = act
        self.menu.addSeparator()
        hud = self.menu.addAction("Open NOVA", bridge.hud_requested.emit)
        f = hud.font()
        f.setBold(True)
        hud.setFont(f)
        self.menu.addAction("Settings", lambda: bridge.settings_requested.emit(""))
        self.menu.addSeparator()
        self.menu.addAction("Restart NOVA", bridge.restart_requested.emit)
        self.menu.addAction("Quit NOVA", bridge.quit_requested.emit)
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)

    def set_mascot(self, name: str):
        self.mascot = name if name in MASCOTS else "cat"
        self.mascot_actions[self.mascot].setChecked(True)
        self.setIcon(mascot_icon(self.mascot, self.mute_action.isChecked()))

    def _pick_voice(self, vid: str):
        self.voice = vid
        self.bridge.voice_selected.emit(vid)

    def set_voice(self, vid: str):
        """Pick a voice (from the settings window): tick it here and tell the assistant."""
        if vid in self.voice_actions:
            self.voice_actions[vid].setChecked(True)
        self._pick_voice(vid)

    def _mute(self, on: bool):
        self.setIcon(mascot_icon(self.mascot, muted=on))
        self.setToolTip("NOVA: muted" if on else "NOVA: say “Hey Nova”")
        self.bridge.mute_toggled.emit(on)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:          # left click toggles mute
            self.mute_action.toggle()
