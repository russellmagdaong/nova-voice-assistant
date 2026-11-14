"""NOVA's window, its main app (tray > Open NOVA, or Settings).

- Your mascot in the centre. The pixel mascots peek up over a pixel-art scene of their own
  (scenes.py); the orb floats on a plain, dark background.
- The colours follow the mascot (the orb: your colour theme), and so does the font: Pixelify Sans
  with the pixel mascots, Outfit with the orb (assets/fonts, both SIL Open Font License).
- While you talk with NOVA: what you said and its answer, under the mascot.
- Move the mouse or press a key and a few things fade in: the time, date and weather (top left),
  what's next and what Spotify is playing (bottom corners), and four buttons: talk, mute,
  settings, fullscreen.
- Idle (no conversation, no mouse or keys for 3 s): only the mascot stays; the pointer hides.
- Settings slide in from the right (settings_window.SettingsPanel).

Click the mascot (or press Space) to talk without saying "Hey Nova"; click again to stop it.
F11 or a double-click: fullscreen; Esc: closes settings, then leaves fullscreen; Ctrl+,: settings.
Right-click: the tray menu. Closing the window only hides it (NOVA keeps running in the tray).
While the window is open the notch steps aside.
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QFontMetricsF, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QFrame, QVBoxLayout, QWidget

from . import orb, scenes, themes, tools_server, ui
from .fluent import Colors, apply_backdrop, stylesheet
from .ui import (ACTIVE, ALERT, ERROR, IDLE, LISTENING, LOADING, MUTED, SEARCHING, SPEAKING, THINKING, Sprite,
                 WordFlow, clamp)

log = logging.getLogger("nova.hud")

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PIXEL_FONT, CLEAN_FONT = "Pixelify Sans", "Outfit"

# The UI's colours (dark, middle, light) for each pixel mascot, from its own art. The orb uses the
# colour theme (and grey when muted, red on an error, like the orb itself).
MASCOT_COLORS = {
    "cat": ("#a07816", "#e8a812", "#ffd43b"),        # its eyes
    "robot": ("#2f4170", "#5f8db4", "#9cc4de"),      # its screen
    "fox": ("#c76530", "#ec8736", "#ffb653"),        # its fur
    "frog": ("#3f7f4a", "#79bd62", "#a8db7a"),       # its skin
    "ghost": ("#6a6f9e", "#9aa3d6", "#dce4ef"),      # its periwinkle shadows
    "axolotl": ("#ce568d", "#ed85b1", "#ffdae0"),    # its gills
}

GLYPH = {"talk": "", "muted": "", "settings": "", "full": "", "unfull": "",
         "close": ""}

CHROME_S = 3.0           # the clock, readouts and buttons stay this long after the last mouse move or key
LINGER_S = 6.0           # the last answer stays this long after NOVA finishes
FAST_MS, ORB_MS, PIXEL_MS = 16, 33, 66

STATUS = {LOADING: "Starting up", IDLE: "", LISTENING: "Listening", THINKING: "Thinking",
          SEARCHING: "Looking it up", SPEAKING: "Nova", ALERT: "Alert", MUTED: "Muted",
          ERROR: "Something went wrong"}

_fonts_loaded = False


def load_fonts():
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    for name in ("PixelifySans.ttf", "Outfit.ttf"):
        path = FONTS / name
        if not path.exists() or QFontDatabase.addApplicationFont(str(path)) < 0:
            log.warning("couldn't load the font %s (using Segoe UI)", name)


def with_alpha(c, a: float) -> QColor:
    q = QColor(c)
    q.setAlphaF(clamp(q.alphaF() * a))
    return q


def approach(v: float, target: float, dt: float, up: float, down: float) -> float:
    """Ease v towards target (quick to appear, slower to fade, and never a lingering tail)."""
    step = (target - v) * min(1.0, dt * (up if target > v else down))
    floor = dt * 0.6
    if abs(step) < floor:
        step = math.copysign(min(abs(target - v), floor), target - v)
    v += step
    return target if abs(target - v) < 0.004 else v


def draw_centered(flow: WordFlow, qp: QPainter, f: QFont, color: QColor, cx: float, y0: float, width: float,
                  max_lines: int) -> float:
    """Like WordFlow.draw, but each line centred on cx. Returns the height used."""
    fm = QFontMetricsF(f)
    lines = flow.layout(fm, width)
    cut = len(lines) > max_lines
    lines = lines[-max_lines:]
    now = time.monotonic()
    qp.setFont(f)
    ls = fm.lineSpacing()
    for li, line in enumerate(lines):
        line_a = 0.45 if (cut and li == 0) else 1.0
        first, last = line[0][0], line[-1][0]
        run = " ".join(flow.words[first:last + 1])
        x0 = cx - fm.horizontalAdvance(run) / 2
        base = y0 + li * ls + fm.ascent()
        if all(now - flow.born[i] >= flow.FADE for i, _ in line):
            qp.setPen(with_alpha(color, line_a))
            qp.drawText(QPointF(x0, base), run)
            continue
        for idx, _ in line:
            x = fm.horizontalAdvance(" ".join(flow.words[first:idx] + [""])) if idx > first else 0.0
            t = clamp((now - flow.born[idx]) / flow.FADE)
            ease = 1 - (1 - t) ** 3
            qp.setPen(with_alpha(color, ease * line_a))
            qp.drawText(QPointF(x0 + x, base + (1 - ease) * 6), flow.words[idx])
    return len(lines) * ls


# ---- readouts -----------------------------------------------------------------------------------
class SpotifyWatch:
    """What Spotify is playing, read from its window title ("Artist - Song"; just "Spotify ..." when
    paused). In-process and cheap: it never runs spotify_cli, so it can't start Spotify."""

    def __init__(self):
        self.text = ""
        self.hwnd = 0
        self._scanned = -1e9
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.IsWindow.argtypes = [wintypes.HWND]
        u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

    @staticmethod
    def _title(hwnd) -> str:
        u32 = ctypes.windll.user32
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    @staticmethod
    def _exe(hwnd) -> str:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value).lower() if ok else ""
        finally:
            k32.CloseHandle(h)

    def _find(self) -> int:
        found = []
        u32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "Chrome_WidgetWin_1" and self._title(hwnd) and self._exe(hwnd) == "spotify.exe":
                found.append(hwnd)
                return False
            return True
        u32.EnumWindows(each, 0)
        return found[0] if found else 0

    def poll(self):
        try:
            u32 = ctypes.windll.user32
            if not (self.hwnd and u32.IsWindow(self.hwnd)):
                self.hwnd = 0
                if time.monotonic() - self._scanned > 10:            # look for Spotify every 10 s
                    self._scanned = time.monotonic()
                    self.hwnd = self._find()
            t = self._title(self.hwnd) if self.hwnd else ""
            self.text = t.replace(" - ", " — ", 1) if " - " in t and not t.lower().startswith("spotify") else ""
        except (OSError, AttributeError):
            self.text = ""


class WeatherWatch:
    """The weather in the user's city, from Open-Meteo every 20 minutes (in the background)."""

    def __init__(self):
        self.data: dict = {}
        self._next = 0.0
        self._busy = False

    def poll(self, place: str, allowed: bool):
        if not (allowed and place) or self._busy or time.monotonic() < self._next:
            return
        self._busy = True
        threading.Thread(target=self._fetch, args=(place,), daemon=True, name="hud-weather").start()

    def _fetch(self, place: str):
        try:
            self.data = tools_server.current_weather(place)
            self._next = time.monotonic() + 1200
        except Exception as e:  # noqa: BLE001 (offline, or the service is down: try again soon)
            log.info("weather for the window: %s", e)
            self._next = time.monotonic() + 120
        finally:
            self._busy = False


# ---- buttons and the settings drawer -----------------------------------------------------------
class HudButton(QAbstractButton):
    SIZE = 44

    def __init__(self, hud: "HudWindow", glyph: str, tip: str, parent: QWidget | None = None):
        super().__init__(parent or hud)
        self.hud, self.glyph = hud, glyph
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(QSize(self.SIZE, self.SIZE))
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, e):
        a = 1.0 if self.parent() is not self.hud else self.hud.ui_a
        if a <= 0.01:
            return
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.hud.colors[1])
        hot = self.underMouse() or self.isChecked()
        fill = with_alpha(c, 0.45 * a) if self.isChecked() else \
            with_alpha(c, 0.28 * a) if self.underMouse() else QColor(0, 0, 0, round(110 * a))
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        if self.hud.pixel:                               # square, with a 2-pixel border, like the art
            qp.setRenderHint(QPainter.Antialiasing, False)
            qp.fillRect(r, fill)
            edge = with_alpha(c, (0.95 if hot else 0.55) * a)
            for b in (QRectF(r.left(), r.top(), r.width(), 2), QRectF(r.left(), r.bottom() - 1, r.width(), 2),
                      QRectF(r.left(), r.top(), 2, r.height()), QRectF(r.right() - 1, r.top(), 2, r.height())):
                qp.fillRect(b, edge)
            qp.setRenderHint(QPainter.Antialiasing)
        else:
            qp.setBrush(fill)
            qp.setPen(QPen(with_alpha(c, (0.9 if hot else 0.35) * a), 1.0))
            qp.drawEllipse(r)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(15)
        qp.setFont(f)
        qp.setPen(with_alpha("#ffffff" if hot else "#e4e8ee", a))
        qp.drawText(r, Qt.AlignCenter, self.glyph)

    def enterEvent(self, e):
        self.hud.poke()
        self.update()

    def leaveEvent(self, e):
        self.update()


class Drawer(QFrame):
    """NOVA settings, slid in from the right."""

    def __init__(self, hud: "HudWindow"):
        super().__init__(hud)
        from .settings_window import SettingsPanel          # (only when settings are first opened)
        self.hud = hud
        self.c = Colors(dark=True, accent=QColor(hud.colors[1]))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 6, 0, 0)
        self.panel = SettingsPanel(hud.ctx, self.c, parent=self)
        lay.addWidget(self.panel)
        self.close_btn = HudButton(hud, GLYPH["close"], "Close settings (Esc)", parent=self)
        self.close_btn.clicked.connect(hud.close_settings)
        self.restyle()

    def restyle(self):
        """The accent follows the mascot (or, with the orb, the colour theme)."""
        self.c.accent = QColor(self.hud.colors[1])
        self.setStyleSheet(stylesheet(self.c, mica=True)
                           + "QToolTip { color: #ffffff; background: #15181e; border: 1px solid #333a44; }")
        for w in self.findChildren(QWidget):
            w.update()

    def resizeEvent(self, e):
        self.close_btn.move(self.width() - self.close_btn.width() - 14, 12)
        self.close_btn.raise_()

    def mousePressEvent(self, e):
        e.accept()                     # (clicks inside settings never reach NOVA's window behind them)

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(10, 11, 15))
        qp.fillRect(QRectF(0, 0, 1, self.height()), with_alpha(self.hud.colors[1], 0.6))


# ---- the window ---------------------------------------------------------------------------------
class HudWindow(QWidget):
    def __init__(self, ctx):
        super().__init__(None)
        load_fonts()
        self.ctx, self.bridge = ctx, ctx.bridge
        self.setWindowTitle("NOVA")
        self.setWindowIcon(ui.nova_icon())
        self.setMinimumSize(560, 420)
        self.resize(1200, 760)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

        self.state = getattr(ctx.notch, "state", LOADING)
        self.state_t = time.monotonic()
        self.you, self.answer = WordFlow(), WordFlow()
        self.live = False
        self.activity = ""
        self.countdown = -1.0
        self.linger_until = 0.0
        self.mic_l = self.spk_l = 0.0
        self.mic_a = 0.0                 # the mic indicator (top right)
        self.mic_threshold = 0.075       # a mic level NOVA counts as speech (from the room's noise)
        self.mic_until = 0.0
        self.ui_a = 1.0                  # clock, readouts and buttons (mouse / keys)
        self.conv_a = 0.0                # the conversation (NOVA busy, or just finished)
        self.last_input = time.monotonic()
        self.cursor_hidden = False
        self.hover_mascot = False
        self._cursor, self._cursor_t = QCursor.pos(), -1e9    # the eyes follow the cursor after it moves
        self.look = (0.0, 0.0)           # the orb's core, leaning towards the cursor
        self.mascot = ""
        self.sprite: Sprite | None = None
        self.colors = themes.current_colors()
        self._pix: dict = {}
        self._bg: tuple | None = None    # (key, pixmap, y offset)
        self.t0 = self._last = time.monotonic()
        self._slow_at = 0.0
        self.upcoming = ""
        self.spotify, self.weather = SpotifyWatch(), WeatherWatch()
        self.drawer: Drawer | None = None
        self.drawer_open = False
        self.drawer_p = 0.0
        self._was_max = False
        self._styled = False

        self.buttons = {
            "talk": HudButton(self, GLYPH["talk"], "Talk to NOVA (Space)"),
            "mute": HudButton(self, GLYPH["muted"], "Mute the wake word"),
            "settings": HudButton(self, GLYPH["settings"], "Settings (Ctrl+,)"),
            "full": HudButton(self, GLYPH["full"], "Fullscreen (F11)"),
        }
        self.buttons["mute"].setCheckable(True)
        self.buttons["mute"].setChecked(ctx.tray.mute_action.isChecked())
        self.buttons["mute"].clicked.connect(lambda: ctx.tray.mute_action.toggle())
        ctx.tray.mute_action.toggled.connect(self.buttons["mute"].setChecked)
        self.buttons["talk"].clicked.connect(self.talk)
        self.buttons["settings"].clicked.connect(lambda: self.close_settings() if self.drawer_open
                                                 else self.open_settings())
        self.buttons["full"].clicked.connect(self.toggle_fullscreen)

        b = self.bridge
        b.state.connect(self.set_state)
        b.heard.connect(self._set_heard)
        b.partial.connect(self._set_partial)
        b.answer.connect(self._set_answer)
        b.activity.connect(self._set_activity)
        b.countdown.connect(self._set_countdown)

        self.tick = QTimer(self, interval=FAST_MS)
        self.tick.timeout.connect(self._on_tick)
        self._sync_mascot()

    # ---- NOVA's state ----------------------------------------------------------------------
    def set_state(self, state: str):
        prev, self.state = self.state, state
        if state != prev:
            self.state_t = time.monotonic()
        if state == LISTENING and prev in (IDLE, MUTED, LOADING):
            self.you.clear()
            self.answer.clear()
            self.live = False
        if state != LISTENING and self.live:
            self.you.clear()
            self.live = False
        if state == IDLE and prev in (SPEAKING, ERROR, ALERT) and self.answer.words:
            self.linger_until = time.monotonic() + LINGER_S
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
        self.you.set(text, animate=True)
        self.live = False
        self.answer.clear()
        self._wake()

    def _set_answer(self, text: str):
        self.answer.set(text)
        if self.state not in ACTIVE:                     # (e.g. "Starting a new conversation.")
            self.linger_until = time.monotonic() + LINGER_S
        self._wake()

    def _set_activity(self, text: str):
        self.activity = text
        self._wake()

    def _set_countdown(self, v: float):
        self.countdown = v
        self._wake()

    @property
    def pixel(self) -> bool:
        return self.sprite is not None

    def _palette(self) -> tuple[str, str, str]:
        if self.sprite is None:
            return orb.style(self.state, self.mic_l, self.spk_l)[3]
        if self.state == ERROR:
            return orb.RED
        return MASCOT_COLORS.get(self.mascot, MASCOT_COLORS["cat"])

    def _sync_mascot(self):
        """Follow the mascot picked anywhere (settings, tray): its scene, colours and font."""
        name = getattr(self.ctx.notch, "mascot", "cat")
        if name != self.mascot:
            self.mascot = name
            self.sprite = None if name == "orb" else Sprite(ui.MASCOTS.get(name, ui.pixelcat))
            if self.sprite:
                self.sprite.rim = True                   # (the black cat's outline, on a night sky)
            self._pix.clear()
            self._bg = None
            self.update()

    def ui_font(self, px: float, weight=QFont.Normal, spacing: float = 100) -> QFont:
        """The UI font: Pixelify Sans with the pixel mascots, Outfit with the orb."""
        f = QFont()
        f.setFamilies([PIXEL_FONT if self.pixel else CLEAN_FONT, "Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(max(1, round(px)))
        f.setWeight(weight)
        if spacing != 100:
            f.setLetterSpacing(QFont.PercentageSpacing, spacing)
        return f

    # ---- actions -------------------------------------------------------------------------------
    def talk(self):
        """Talk without the wake word; while NOVA is busy, stop it instead (like clicking the notch)."""
        if self.state in ACTIVE:
            self.bridge.stop_requested.emit()
        elif self.state == MUTED:
            self.ctx.tray.mute_action.setChecked(False)
        else:
            self.bridge.talk_requested.emit()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self._was_max else self.showNormal()
        else:
            self._was_max = self.isMaximized()
            self.showFullScreen()

    def open_settings(self, page: str = ""):
        if self.drawer is None:
            self.drawer = Drawer(self)
            self.drawer.setGeometry(self.width(), 0, self._drawer_w(), self.height())
        if page:
            self.drawer.panel.show_page(page)
        self.drawer_open = True
        self.drawer.show()
        self.drawer.raise_()
        self.buttons["settings"].setChecked(True)
        self.poke()

    def close_settings(self):
        self.drawer_open = False
        self.buttons["settings"].setChecked(False)
        self.setFocus()
        self.poke()

    def poke(self):
        """The user did something: show the clock, readouts and buttons (and the pointer)."""
        self.last_input = time.monotonic()
        if self.cursor_hidden:
            self.cursor_hidden = False
            self.setCursor(Qt.PointingHandCursor if self.hover_mascot else Qt.ArrowCursor)
        self._wake()

    def _wake(self):
        if self.isVisible() and self.tick.interval() != FAST_MS:
            self.tick.setInterval(FAST_MS)

    # ---- window events ---------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if not self._styled:
            self._styled = True
            apply_backdrop(int(self.winId()), dark=True, mica=False)      # dark title bar
            g = self.screen().availableGeometry()
            w, h = min(1200, g.width() - 60), min(760, g.height() - 60)
            if not (self.isMaximized() or self.isFullScreen()):
                self.setGeometry(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2, w, h)
        self.tick.start()
        self.poke()
        self._open_changed()

    def hideEvent(self, e):
        self.tick.stop()
        self.ctx.notch.set_hud_front(False)
        super().hideEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self.buttons["full"].glyph = GLYPH["unfull" if self.isFullScreen() else "full"]
            self.buttons["full"].setToolTip("Leave fullscreen (Esc)" if self.isFullScreen() else "Fullscreen (F11)")
            self._open_changed()
            self.poke()
        super().changeEvent(e)

    def _open_changed(self):
        """The notch steps aside whenever this window is open (and not minimised)."""
        self.ctx.notch.set_hud_front(self.isVisible() and not self.isMinimized())

    def resizeEvent(self, e):
        self._place_buttons()
        if self.drawer:
            self._place_drawer()
        self.update()

    def _place_buttons(self):
        keys = list(self.buttons)
        gap = 12
        total = len(keys) * HudButton.SIZE + (len(keys) - 1) * gap
        x = (self.width() - total) // 2
        y = self.height() - HudButton.SIZE - 24
        for i, k in enumerate(keys):
            self.buttons[k].move(x + i * (HudButton.SIZE + gap), y)

    def _drawer_w(self) -> int:
        w = self.width()
        return int(min(w, max(980, min(1100, w * 0.62))))

    def _place_drawer(self):
        dw = self._drawer_w()
        g = QRect(round(self.width() - dw * self.drawer_p), 0, dw, self.height())
        if self.drawer.geometry() != g:
            self.drawer.setGeometry(g)
        visible = self.drawer_p > 0.002
        if self.drawer.isVisible() != visible:
            self.drawer.setVisible(visible)

    # ---- input -------------------------------------------------------------------------------------
    def mouseMoveEvent(self, e):
        over = self._mascot_hit(e.position())
        if over != self.hover_mascot:
            self.hover_mascot = over
            self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        self.poke()

    def leaveEvent(self, e):
        self.hover_mascot = False

    def _in_drawer(self, p: QPointF) -> bool:
        return bool(self.drawer and self.drawer.isVisible() and self.drawer.geometry().contains(p.toPoint()))

    def mousePressEvent(self, e):
        self.poke()
        if self._in_drawer(e.position()):
            return
        if e.button() == Qt.LeftButton:
            if self._mascot_hit(e.position()):
                self.talk()
            elif self.drawer_open:
                self.close_settings()
        elif e.button() == Qt.RightButton:
            self.ctx.tray.menu.popup(e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if (e.button() == Qt.LeftButton and not self._mascot_hit(e.position()) and not self.drawer_open
                and not self._in_drawer(e.position())):
            self.toggle_fullscreen()

    def wheelEvent(self, e):
        self.poke()

    def keyPressEvent(self, e):
        self.poke()
        k = e.key()
        if k == Qt.Key_F11:
            self.toggle_fullscreen()
        elif k == Qt.Key_Escape:
            if self.drawer_open:
                self.close_settings()
            elif self.isFullScreen():
                self.toggle_fullscreen()
        elif k == Qt.Key_Space and not e.isAutoRepeat():
            self.talk()
        elif k == Qt.Key_Comma and e.modifiers() & Qt.ControlModifier:
            self.open_settings()
        else:
            super().keyPressEvent(e)

    # ---- animation ---------------------------------------------------------------------------------
    def _on_tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now

        self._sync_mascot()
        colors = self._palette()
        if colors != self.colors:
            self.colors = colors
            if self.sprite is None:
                self._bg = None                          # (the orb's glow follows the theme)
            if self.drawer:
                self.drawer.restyle()
            for b in self.buttons.values():
                b.update()
        mic, spk = self.ctx.assistant.levels()
        self.mic_l += (mic - self.mic_l) * 0.35
        self.spk_l += (spk - self.spk_l) * 0.45
        # The mic indicator: on while the mic picks up sound loud enough for NOVA to count as speech
        # (the same threshold it records with: 2.5x the room's noise), and all the time it listens.
        noise = float(getattr(self.ctx.assistant, "noise", 200.0) or 200.0)
        self.mic_threshold = max(noise * 2.5, 300.0) / 4000          # (Mic.level is rms / 4000)
        if mic > self.mic_threshold and self.state != MUTED:
            self.mic_until = now + 0.8
        want_mic = self.state != MUTED and (now < self.mic_until or self.state == LISTENING)
        mic_a = approach(self.mic_a, 1.0 if want_mic else 0.0, dt, 12.0, 3.0)
        mic_moving = mic_a != self.mic_a or mic_a > 0
        self.mic_a = mic_a

        chrome = (now - self.last_input < CHROME_S or self.drawer_open
                  or any(b.underMouse() for b in self.buttons.values()))
        convo = self.state in ACTIVE or self.state == LOADING or now < self.linger_until
        old = (self.ui_a, self.conv_a)
        self.ui_a = approach(self.ui_a, 1.0 if chrome else 0.0, dt, 9.0, 2.2)
        self.conv_a = approach(self.conv_a, 1.0 if convo else 0.0, dt, 7.0, 1.8)
        fading = old != (self.ui_a, self.conv_a)

        moving = False
        if self.drawer:
            self.drawer_p = approach(self.drawer_p, 1.0 if self.drawer_open else 0.0, dt, 11.0, 13.0)
            moving = self.drawer_p not in (0.0, 1.0)
            self._place_drawer()

        self._follow_cursor(now)
        frame_changed = self.sprite.update(self.state, self.hover_mascot, self.spk_l, now) if self.sprite else False

        if self.ui_a > 0 and now >= self._slow_at:              # readouts, once a second while shown
            self._slow_at = now + 1.0
            self.spotify.poll()
            c = self.ctx.cfg.get("claude", {})
            self.weather.poll(c.get("location", ""), c.get("web_search", True))
            try:
                self.upcoming = self.ctx.assistant.upcoming() or ""
            except Exception:  # noqa: BLE001
                self.upcoming = ""

        show_buttons = self.ui_a > 0.02 and self.drawer_p < 0.9
        for b in self.buttons.values():
            if b.isVisible() != show_buttons:
                b.setVisible(show_buttons)
            elif fading:
                b.update()

        if not chrome and self.underMouse() and not self.drawer_open and not self.cursor_hidden:
            self.cursor_hidden = True
            self.setCursor(Qt.BlankCursor)

        busy = self.ui_a > 0 or self.conv_a > 0 or fading or moving or mic_moving
        if busy:
            self.update()
        elif self.sprite is None or frame_changed or self.state in (MUTED, LOADING):
            self.update(self._mascot_rect())                 # only the mascot moves
        interval = FAST_MS if busy else ORB_MS if self.sprite is None else PIXEL_MS
        if self.tick.interval() != interval:
            self.tick.setInterval(interval)

    def _follow_cursor(self, now: float):
        """For a few seconds after the cursor moves (anywhere on screen): the pixel mascot looks at
        it, and the orb's core leans towards it."""
        p = QCursor.pos()
        if p != self._cursor:
            self._cursor, self._cursor_t = p, now
        following = now - self._cursor_t < ui.GAZE_S
        if self.sprite:
            x, y, s = self._sprite_geom()
            dpr = self.devicePixelRatioF()
            art = self.sprite.art
            eye = self.mapToGlobal(QPoint(round((x + art.COLS * s / 2) / dpr), round((y + art.ROWS * s * 0.5) / dpr)))
            self.sprite.gaze = (ui.gaze_toward(eye.x(), eye.y(), p.x(), p.y(), near=art.COLS * s / dpr * 0.6)
                                if following else None)
        else:
            c = self.mapToGlobal(QPoint(round(self._area() / 2), round(self.height() * 0.44)))
            R = self._orb_r()
            want = (clamp((p.x() - c.x()) / (R * 4), -1, 1), clamp((p.y() - c.y()) / (R * 4), -1, 1))                 if following else (0.0, 0.0)
            self.look = (self.look[0] + (want[0] - self.look[0]) * 0.12, self.look[1] + (want[1] - self.look[1]) * 0.12)

    # ---- layout --------------------------------------------------------------------------------------
    def _area(self) -> float:
        """The width left of the settings drawer."""
        return self.width() - (self._drawer_w() * self.drawer_p if self.drawer else 0)

    def _scale(self) -> int:
        """A pixel mascot's size: device pixels per art pixel (whole, so it stays crisp)."""
        art = self.sprite.art
        return max(3, round(min(self.width() * 0.55, self.height()) * 0.30 * self.devicePixelRatioF() / art.ROWS))

    def _grid(self) -> tuple[int, int, int, int, int]:
        """The pixel grid shared by a pixel mascot and its scene (device pixels): (scale, grid origin x
        and y (both <= 0), the mascot's home corner x and y). The scene's pixels are the mascot's
        pixels: the same size, on the same lines."""
        art = self.sprite.art
        dpr = self.devicePixelRatioF()
        s = self._scale()
        x = round(self.width() / 2 * dpr - art.COLS * s / 2)
        y = round(self.height() * 0.44 * dpr - art.ROWS * s / 2)
        return s, (x % s) - s if x % s else 0, (y % s) - s if y % s else 0, x, y

    def _sprite_geom(self) -> tuple[int, int, int]:
        """A pixel mascot's top-left corner (device pixels) and scale. Centred in the area left of
        the settings, snapped to the scene's grid; it never moves up or down (its bottom edge is
        the scene's ground line)."""
        art = self.sprite.art
        s, ox, oy, x, y = self._grid()
        want = self._area() / 2 * self.devicePixelRatioF() - art.COLS * s / 2
        return ox + round((want - ox) / s) * s, y, s

    def _orb_r(self) -> float:
        return min(self.width() * 0.55, self.height()) * 0.15

    def _mascot_bottom(self) -> float:
        """Where the conversation starts (window pixels)."""
        if self.sprite:
            x, y, s = self._sprite_geom()
            return (y + self.sprite.art.ROWS * s) / self.devicePixelRatioF()
        return self.height() * 0.44 + self._orb_r() * 1.55

    def _mascot_rect(self) -> QRect:
        if self.sprite:
            x, y, s = self._sprite_geom()
            dpr = self.devicePixelRatioF()
            w, h = self.sprite.art.COLS * s / dpr, self.sprite.art.ROWS * s / dpr
            return QRectF(x / dpr - 8, y / dpr - h * 0.5, w * 1.35 + 16, h * 1.5 + 8).toAlignedRect()
        R = self._orb_r()
        cx, cy = self._area() / 2, self.height() * 0.44
        return QRectF(cx - R * 1.6, cy - R * 1.6, R * 3.2, R * 3.2).toAlignedRect()

    def _mascot_hit(self, p: QPointF) -> bool:
        if self.sprite:
            x, y, s = self._sprite_geom()
            dpr = self.devicePixelRatioF()
            art = self.sprite.art
            return QRectF(x / dpr, y / dpr, art.COLS * s / dpr, art.ROWS * s / dpr).contains(p)
        return math.hypot(p.x() - self._area() / 2, p.y() - self.height() * 0.44) < self._orb_r() * 1.1

    # ---- painting ------------------------------------------------------------------------------------
    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        self._paint_background(qp, e.rect())
        if self.sprite is None:
            c, R = QPointF(self._area() / 2, self.height() * 0.44), self._orb_r()
            if self.conv_a > 0.01:
                self._paint_orb_ring(qp, c, R, now)
            wobble, spin, glow, colors = orb.style(self.state, self.mic_l, self.spk_l)
            orb.paint(qp, c, R, now - self.t0, wobble, spin, glow, colors, look=self.look)
        else:
            self._paint_sprite(qp, now)
        if self.conv_a > 0.01:
            self._paint_conversation(qp, now)
        if self.ui_a * (1 - self.drawer_p) > 0.01:            # (settings open: just the mascot beside them)
            self._paint_chrome(qp, now)
        if self.mic_a > 0.01:
            self._paint_mic(qp)

    def _paint_background(self, qp: QPainter, clip: QRect):
        dpr = self.devicePixelRatioF()
        W, H = max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr))
        key = (self.mascot, W, H, dpr, None if self.sprite else self.colors)
        if self._bg is None or self._bg[0] != key:
            self._bg = (key, *self._make_background(W, H, dpr))
        _, pm, at = self._bg
        qp.save()
        qp.setClipRect(clip)
        qp.drawPixmap(at, pm)
        qp.restore()

    def _make_background(self, W: int, H: int, dpr: float) -> tuple[QPixmap, QPointF]:
        """The pixel mascot's scene, at the mascot's own pixel size and on its grid, with its ground
        row on the mascot's bottom edge; or, for the orb, a plain dark background with a soft glow
        in the theme's colour."""
        if self.sprite:
            art = self.sprite.art
            s, ox, oy, x, y = self._grid()
            gw, gh = -(-(W - ox) // s) + 1, -(-(H - oy) // s) + 1
            ground = (y + art.ROWS * s - oy) // s
            t = time.perf_counter()
            img = scenes.render(self.mascot, gw, gh, ground, (x - ox) // s + art.COLS / 2, art.COLS / 2)
            if img is not None:
                pm = QPixmap.fromImage(img.scaled(gw * s, gh * s, Qt.IgnoreAspectRatio, Qt.FastTransformation))
                pm.setDevicePixelRatio(dpr)
                log.debug("scene %s %dx%d in %.0f ms", self.mascot, gw, gh, (time.perf_counter() - t) * 1000)
                return pm, QPointF(ox / dpr, oy / dpr)
        pm = QPixmap(W, H)
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor("#05060a"))
        p = QPainter(pm)
        w, h = W / dpr, H / dpr
        g = QRadialGradient(QPointF(w / 2, h * 0.44), max(w, h) * 0.6)
        g.setColorAt(0.0, with_alpha(self.colors[1], 0.12))
        g.setColorAt(0.5, with_alpha(self.colors[0], 0.03))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w, h), g)
        p.end()
        return pm, QPointF(0, 0)

    def _paint_orb_ring(self, qp: QPainter, c: QPointF, R: float, now: float):
        """The orb's only ring, while you talk with NOVA: a short arc turns while it thinks, and it
        fills in while NOVA waits for your reply."""
        a = self.conv_a
        c0, c1, c2 = (QColor(x) for x in self.colors)
        r = R * 1.42
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        qp.save()
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(with_alpha(c1, 0.22 * a), 1.0))
        qp.drawEllipse(c, r, r)
        qp.setPen(QPen(with_alpha(c2, 0.9 * a), 2.0, Qt.SolidLine, Qt.RoundCap))
        if self.state in (THINKING, SEARCHING):
            qp.drawArc(box, int(-((now - self.t0) * 220) % 360 * 16), 60 * 16)
        elif self.state == LISTENING and self.countdown >= 0 and not self.live:
            qp.drawArc(box, 90 * 16, int(-360 * clamp(self.countdown) * 16))
        qp.restore()

    def _paint_sprite(self, qp: QPainter, now: float):
        art = self.sprite.art
        dpr = self.devicePixelRatioF()
        x, y, s = self._sprite_geom()
        key = (self.sprite.cur, s)
        pm = self._pix.get(key)
        if pm is None:
            img = art.image(self.sprite.cur, True)
            pm = QPixmap.fromImage(img.scaled(img.width() * s, img.height() * s, Qt.IgnoreAspectRatio,
                                              Qt.FastTransformation))
            pm.setDevicePixelRatio(dpr)
            self._pix[key] = pm
        since = now - self.state_t
        dy = dx = 0
        if self.state == LISTENING and self.mic_l > 0.12:
            dy = -1                                          # hops while you talk
        elif self.state == SPEAKING and self.sprite.mouth:
            dy = -1                                          # bobs as it talks
        elif self.state == ALERT and int(since / 0.25) % 2:
            dy = -1
        elif self.state == ERROR and since < 0.5:
            dx = (1, -1, 1, -1, 1, 0)[min(int(since / 0.08), 5)]
        # Hops show one more row above the ground line rather than lifting off it: the bottom stays put.
        src = QRectF(0, 0, pm.width(), pm.height() + dy * s)
        qp.drawPixmap(QPointF((x + dx * s) / dpr, (y + dy * s) / dpr), pm, src)
        if self.state in (MUTED, LOADING):
            self._paint_zzz(qp, x + round(art.COLS * 0.75) * s, y - 2 * s, s, now)

    ZZZ = ["zzzz", "..z.", ".z..", "zzzz"]               # a small z, drawn at the mascot's pixel size

    def _paint_zzz(self, qp: QPainter, x0: int, y0: int, s: int, now: float):
        """Two z's drifting up from the sleeping mascot, in whole art pixels on the scene's grid."""
        dpr = self.devicePixelRatioF()
        qp.save()
        qp.setRenderHint(QPainter.Antialiasing, False)
        for i in range(2):
            ph = ((now - self.t0) / 3.2 + i / 2) % 1.0
            x, y = x0 + int(ph * 4) * s, y0 - int(ph * 7) * s
            col = QColor(225, 230, 240)
            col.setAlphaF(0.9 if ph < 0.75 else 0.45)
            for zy, row in enumerate(self.ZZZ):
                for zx, ch in enumerate(row):
                    if ch != ".":
                        qp.fillRect(QRectF((x + zx * s) / dpr, (y + zy * s) / dpr, s / dpr, s / dpr), col)
        qp.restore()

    def _status(self, now: float) -> str:
        if self.state == LISTENING and self.countdown >= 0 and not self.live:
            s = "Your turn"
        elif self.state in (SEARCHING, ALERT) and self.activity:
            s = self.activity
        elif self.state == IDLE and self.answer.words:
            s = "Nova"
        else:
            s = STATUS.get(self.state, "")
        if self.state in (THINKING, SEARCHING):
            s += "." * (1 + int((now - self.t0) * 2.5) % 3)
        return s

    def _shadow_text(self, qp: QPainter, p: QPointF, text: str, color: QColor):
        """Text with a soft shadow (readable over the scenes)."""
        d = 2.0 if self.pixel else 1.0
        qp.setPen(QColor(0, 0, 0, 150))
        qp.drawText(QPointF(p.x(), p.y() + d), text)
        qp.setPen(color)
        qp.drawText(p, text)

    def _paint_conversation(self, qp: QPainter, now: float):
        a = self.conv_a
        cx = self._area() / 2
        light = QColor(self.colors[2])
        width = min(760.0, self._area() - 64)
        pixel = self.pixel
        pad_x, pad_y = (22.0, 16.0) if pixel else (0.0, 0.0)
        inner = width - 2 * pad_x
        top = self._mascot_bottom() + (18 if pixel else 8)
        bottom = self.height() - 100
        fs = self.ui_font(15 if pixel else 13, QFont.DemiBold, 106 if pixel else 120)
        big = self.ui_font(22 if pixel else 25, QFont.Normal if pixel else QFont.Light)
        small = self.ui_font(15)
        fsm, bm, smm = QFontMetricsF(fs), QFontMetricsF(big), QFontMetricsF(small)
        status = self._status(now)

        # What to show, and how tall it is.
        room = bottom - top - 2 * pad_y - (fsm.lineSpacing() + 8 if status else 0)
        lines_fit = max(1, int(room // bm.lineSpacing()))
        quote = ""
        if self.live and self.you.words:
            flow, n = self.you, min(3, lines_fit)
        elif self.answer.words:
            flow = self.answer
            if self.you.words and lines_fit > 2:
                quote = smm.elidedText("“" + self.you.text + "”", Qt.ElideRight, inner)
                lines_fit = max(1, int((room - smm.lineSpacing() - 8) // bm.lineSpacing()))
            n = min(5, lines_fit)
        elif self.you.words:
            flow, n = self.you, min(3, lines_fit)
        else:
            flow, n = None, 0
        body = flow.height(big, inner, n) if flow else 0.0
        if not status and not body:
            return
        h = (2 * pad_y + (fsm.lineSpacing() if status else 0) + (8 if status and body else 0)
             + (smm.lineSpacing() + 8 if quote else 0) + body)

        qp.save()
        qp.setOpacity(a)
        box = QRectF(cx - width / 2, top, width, h)
        if pixel:                                        # a pixel text box over the scene
            qp.setRenderHint(QPainter.Antialiasing, False)
            qp.fillRect(box, QColor(6, 7, 12, 205))
            edge = with_alpha(self.colors[1], 0.7)
            for r in (QRectF(box.left(), box.top(), box.width(), 2), QRectF(box.left(), box.bottom() - 2, box.width(), 2),
                      QRectF(box.left(), box.top(), 2, box.height()), QRectF(box.right() - 2, box.top(), 2, box.height())):
                qp.fillRect(r, edge)
            qp.setRenderHint(QPainter.Antialiasing)
        y = top + pad_y
        if status:
            qp.setFont(fs)
            qp.setPen(QColor("#ff8a95") if self.state == ERROR else light)
            sw = fsm.horizontalAdvance(status.rstrip("."))
            qp.drawText(QPointF(cx - sw / 2, y + fsm.ascent()), status)
            y += fsm.lineSpacing() + 8
        if quote:
            qp.setFont(small)
            qp.setPen(QColor(178, 186, 198))
            qp.drawText(QPointF(cx - smm.horizontalAdvance(quote) / 2, y + smm.ascent()), quote)
            y += smm.lineSpacing() + 8
        if flow:
            draw_centered(flow, qp, big, QColor("#ffffff"), cx, y, inner, n)
        if self.state == LISTENING and self.countdown >= 0 and not self.live and pixel:
            bar = QRectF(box.left() + 2, box.bottom() - 6, (box.width() - 4) * clamp(self.countdown), 4)
            qp.fillRect(bar, with_alpha(self.colors[1], 0.9))     # time left to reply
        qp.restore()

    MIC = ["..###..",                                   # the pixel microphone (7 x 9)
           "..###..",
           "..###..",
           "#.###.#",
           "#.###.#",
           ".#...#.",
           "..###..",
           "...#...",
           ".#####."]

    def _paint_mic(self, qp: QPainter):
        """Top right: a microphone and four signal bars, lit while the mic picks up your voice."""
        lvl, thr = self.mic_l, self.mic_threshold
        hearing = lvl > thr
        v = clamp((lvl - thr * 0.6) / (thr * 4))                  # 0 at the threshold, 1 when loud
        light = QColor(self.colors[2])
        mic_col = light if hearing else QColor(235, 238, 244, 190)
        off = QColor(255, 255, 255, 60)
        dpr = self.devicePixelRatioF()
        qp.save()
        qp.setOpacity(self.mic_a)
        if self.pixel:                                   # in pixels, like everything else on the scene
            u = max(2, round(self._scale() / 3)) / dpr
            w, h = 24 * u, 13 * u
            x0, y0 = self._area() - 30 - w, 30.0
            qp.setRenderHint(QPainter.Antialiasing, False)
            qp.fillRect(QRectF(x0, y0, w, h), QColor(0, 0, 0, 110))
            for yy, row in enumerate(self.MIC):
                for xx, ch in enumerate(row):
                    if ch == "#":
                        qp.fillRect(QRectF(x0 + (2 + xx) * u, y0 + (2 + yy) * u, u, u), mic_col)
            for i, bh in enumerate((3, 5, 7, 9)):
                lit = hearing and v >= i / 4
                qp.fillRect(QRectF(x0 + (11 + i * 3) * u, y0 + (11 - bh) * u, 2 * u, bh * u), light if lit else off)
        else:
            w, h = 76.0, 38.0
            x0, y0 = self._area() - 30 - w, 30.0
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(0, 0, 0, 90))
            qp.drawRoundedRect(QRectF(x0, y0, w, h), h / 2, h / 2)
            f = QFont("Segoe Fluent Icons")
            f.setPixelSize(17)
            qp.setFont(f)
            qp.setPen(mic_col)
            qp.drawText(QRectF(x0 + 8, y0, 26, h), Qt.AlignCenter, GLYPH["talk"])
            for i, bh in enumerate((7, 11, 15, 19)):
                lit = hearing and v >= i / 4
                qp.setBrush(light if lit else off)
                qp.drawRoundedRect(QRectF(x0 + 40 + i * 7, y0 + h / 2 + 9.5 - bh, 4, bh), 2, 2)
        qp.restore()

    def _paint_chrome(self, qp: QPainter, now: float):
        a = self.ui_a * (1 - self.drawer_p)
        area, h = self._area(), self.height()
        light, text, dim = QColor(self.colors[2]), QColor("#f2f4f7"), QColor(205, 210, 220)
        m = 30
        qp.save()
        qp.setOpacity(a)

        # Top left: the time, the date, the weather.
        nowdt = datetime.now()
        ft = self.ui_font(46 if self.pixel else 52, QFont.Normal if self.pixel else QFont.Light)
        ftm = QFontMetricsF(ft)
        hm = nowdt.strftime("%I:%M").lstrip("0")
        qp.setFont(ft)
        self._shadow_text(qp, QPointF(m, m + ftm.ascent()), hm, text)
        qp.setFont(self.ui_font(16, QFont.Medium))
        self._shadow_text(qp, QPointF(m + ftm.horizontalAdvance(hm) + 8, m + ftm.ascent()), nowdt.strftime("%p"), light)
        f2 = self.ui_font(16 if self.pixel else 15)
        f2m = QFontMetricsF(f2)
        qp.setFont(f2)
        y = m + ftm.height() + 2
        self._shadow_text(qp, QPointF(m + 2, y + f2m.ascent()), nowdt.strftime("%A, %d %B").replace(" 0", " "), dim)
        wd = self.weather.data
        if wd:
            y += f2m.lineSpacing() + 2
            self._shadow_text(qp, QPointF(m + 2, y + f2m.ascent()),
                              f"{wd['temp']}°  {wd['text'].capitalize()} · {wd['place']}", dim)

        # Bottom corners: what's next, what's playing (each only when there's something).
        lab = self.ui_font(13 if self.pixel else 12, QFont.DemiBold, 104 if self.pixel else 115)
        labm = QFontMetricsF(lab)
        val = self.ui_font(16 if self.pixel else 15)
        valm = QFontMetricsF(val)
        col_w = max(120.0, (area - 4 * HudButton.SIZE - 3 * 12) / 2 - m - 24)
        base = h - m
        for name, value, right in (("Next", self.upcoming, False), ("Now playing", self.spotify.text, True)):
            if not value:
                continue
            v = valm.elidedText(value, Qt.ElideRight, col_w)
            vx = area - m - valm.horizontalAdvance(v) if right else m
            lx = area - m - labm.horizontalAdvance(name) if right else m
            qp.setFont(lab)
            self._shadow_text(qp, QPointF(lx, base - valm.height() - 4 - labm.descent()), name, light)
            qp.setFont(val)
            self._shadow_text(qp, QPointF(vx, base - valm.descent()), v, text)

        # Under the mascot, when nothing is being said: how to talk to it.
        if self.conv_a < 0.98 and self.state in (IDLE, MUTED):
            hint = ("Click me to wake me up" if self.state == MUTED else
                    "Click to talk" if self.hover_mascot else "Say “Hey Nova” or click me")
            fh = self.ui_font(15 if self.pixel else 14)
            fhm = QFontMetricsF(fh)
            qp.setOpacity(a * (1 - self.conv_a))
            qp.setFont(fh)
            self._shadow_text(qp, QPointF(area / 2 - fhm.horizontalAdvance(hint) / 2,
                                          self._mascot_bottom() + (22 if self.pixel else 14) + fhm.ascent()), hint, dim)
        qp.restore()
