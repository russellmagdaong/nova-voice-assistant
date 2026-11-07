"""Notch and tray icon. Runs on the Qt main thread.

The notch is a pixel-art black cat peeking up from the bottom centre of the screen, with a
Windows 11 style card above it for what you say and what NOVA answers. The cat is drawn in
nova/pixelcat.py; this file animates it: how far it peeks, blinks, glances, hops, its mouth
while NOVA talks, sleeping while muted.

The worker thread talks to the UI only through Bridge signals (Qt queues them across threads).
"""
from __future__ import annotations

import logging
import math
import random
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontMetricsF, QIcon, QImage, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import pixelcat

log = logging.getLogger("nova.ui")

# States sent by the assistant.
LOADING, IDLE, LISTENING, THINKING, SPEAKING, ALERT, MUTED, ERROR = (
    "loading", "idle", "listening", "thinking", "speaking", "alert", "muted", "error")
ACTIVE = (LISTENING, THINKING, SPEAKING, ALERT, ERROR)

STATUS_TEXT = {LOADING: "Waking up", LISTENING: "Listening", THINKING: "Thinking", SPEAKING: "Nova",
               ALERT: "Reminder", MUTED: "Asleep", ERROR: "Something went wrong", IDLE: ""}


class Bridge(QObject):
    """Signals from the worker thread to the UI, and user actions back."""
    state = Signal(str)
    heard = Signal(str)          # what the user said (final, from Whisper)
    partial = Signal(str)        # live caption while the user is talking ("" clears it)
    answer = Signal(str)         # answer text so far (replaces previous)
    countdown = Signal(float)    # 1..0 time left to reply in a conversation; -1 hides it
    # user actions (connected by the assistant)
    stop_requested = Signal()
    mute_toggled = Signal(bool)
    new_conversation = Signal()
    voice_selected = Signal(str)
    notch_toggled = Signal(bool)
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
    """Windows 11 (Fluent) card colours, light or dark to match the taskbar."""

    def __init__(self, light: bool, accent: QColor | None, override: str = ""):
        self.light = light
        self.accent = QColor(override) if override else (accent or QColor("#005FB8" if light else "#60CDFF"))
        if light:
            self.card, self.stroke = QColor(249, 249, 249, 252), QColor(0, 0, 0, 24)
            self.text, self.text2, self.text3 = QColor(0, 0, 0, 228), QColor(0, 0, 0, 158), QColor(0, 0, 0, 114)
            self.track, self.shadow = QColor(0, 0, 0, 36), 22
            self.critical, self.caution = QColor("#C42B1C"), QColor("#9D5D00")
        else:
            self.card, self.stroke = QColor(44, 44, 44, 252), QColor(255, 255, 255, 24)
            self.text, self.text2, self.text3 = (QColor(255, 255, 255), QColor(255, 255, 255, 200),
                                                 QColor(255, 255, 255, 139))
            self.track, self.shadow = QColor(255, 255, 255, 46), 46
            self.critical, self.caution = QColor("#FF99A4"), QColor("#FCE100")


# ---- the cat -----------------------------------------------------------------------------------
class Cat:
    """The pixel-art cat: picks its frame (state, blinks, glances, talking mouth) and draws it on
    whole screen pixels, scaled up without smoothing, like a sprite in a pixel game."""

    PX = 4               # one art pixel, in Windows' scaled pixels (rounded to whole screen pixels)

    def __init__(self):
        self.rim = True                  # outline for dark themes
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

    def scale(self, dpr: float) -> int:
        """Screen pixels per art pixel."""
        return max(1, round(self.PX * dpr))

    def pixmap(self, name: str, dpr: float) -> QPixmap:
        s = self.scale(dpr)
        key = (name, self.rim, s)
        pm = self._cache.get(key)
        if pm is None:
            img = pixelcat.image(name, self.rim)
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
        if state == IDLE and not hovered and now >= self.next_glance:
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
        if hovered:
            return "listen"
        if now < self.glance_until:
            return self.glance
        return "blink" if blinking else "idle"

    def paint(self, qp: QPainter, cx: float, bottom: float, sink: int, dx: int = 0):
        """Draw centred on `cx` with the bottom row on `bottom`, moved down `sink` and right `dx`
        art pixels. Snapped to whole screen pixels so every art pixel stays a crisp square."""
        dpr = qp.device().devicePixelRatioF()
        s = self.scale(dpr)
        x = round(cx * dpr - pixelcat.COLS * s / 2) + dx * s
        y = round(bottom * dpr) - pixelcat.ROWS * s + sink * s
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


# ---- the notch ---------------------------------------------------------------------------------
class Notch(QWidget):
    W, H = 520, 330                    # window size (fixed, transparent); everything is painted inside
    CAT_H = pixelcat.ROWS * Cat.PX     # the cat's full height (paws on the screen edge)
    # How much of the cat shows, from the top (in rows of its 20): ears + eyes when idle, its
    # face on hover, all but the last row when busy (that row shows when it hops).
    PEEK_IDLE, PEEK_SLEEP, PEEK_HOVER, PEEK_ACTIVE = 12 / 20, 13 / 20, 16 / 20, 19 / 20
    CARD_W, CARD_R = 440, 8
    BEAK_W, BEAK_H = 18, 9             # the card's pointer down to the cat (like a Windows teaching tip)
    PAD_X, PAD_TOP, PAD_BOTTOM = 16, 11, 14
    MIN_CARD_H, MAX_CARD_H = 46, 196

    F_STATUS = font(9)                 # Windows "Caption"
    F_CAPTION = font(12)               # your words while you talk
    F_ANSWER = font(10.5)              # Windows "Body"
    F_YOU = font(9)                    # your question, small, above the answer
    F_HINT = font(10.5)
    F_Z = font(10, QFont.DemiBold)

    def __init__(self, bridge: Bridge, levels, accent: str = ""):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.bridge = bridge
        self.levels = levels           # callable -> (mic_level, speaker_level)
        self.cat = Cat()
        self.accent_override = accent
        self.theme = Theme(*windows_theme(), accent)
        self.cat.rim = not self.theme.light
        self._theme_checked = time.monotonic()

        self.state = LOADING
        self.state_t = time.monotonic()
        self.you = WordFlow()          # what you said (final), or the live caption
        self.live = False              # self.you is a live caption
        self.answer = WordFlow()
        self.hovered = False
        self.linger_until = 0.0
        self.p, self.pv = 0.0, 0.0     # card shown 0..1 (spring) and its velocity
        self.peek, self.peek_v = self.PEEK_SLEEP, 0.0
        self.h = float(self.MIN_CARD_H)  # card height and width (ease to the content)
        self.cw = float(self.CARD_W)
        self.mic_l = self.spk_l = 0.0  # smoothed levels
        self.bars = [0.0] * 4
        self.countdown = -1.0
        self.t0 = time.monotonic()
        self._last_tick = time.monotonic()

        self.tick = QTimer(self, interval=16)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start()
        self.collapse_timer = QTimer(self, singleShot=True)
        self.collapse_timer.timeout.connect(self.update)

        bridge.state.connect(self.set_state)
        bridge.heard.connect(self._set_heard)
        bridge.partial.connect(self._set_partial)
        bridge.answer.connect(self._set_answer)
        bridge.countdown.connect(self._set_countdown)

        self.resize(self.W, self.H)
        self._place()
        QApplication.primaryScreen().availableGeometryChanged.connect(lambda *_: self._place())

    def set_shown(self, on: bool):
        """Show or hide the notch (tray > Show notch). Hidden, it also stops animating."""
        if on:
            self.tick.start()
            self.show()
        else:
            self.tick.stop()
            self.hide()

    # ---- placement / expansion -------------------------------------------------
    def _place(self):
        # The window sits on the bottom of the work area (the screen edge, or the top of a bottom
        # taskbar): the cat peeks up over it.
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.x() + (g.width() - self.W) // 2, g.y() + g.height() - self.H)

    def _want_card(self) -> bool:
        busy = self.state in (LOADING,) + ACTIVE
        return busy or self.hovered or time.monotonic() < self.linger_until

    def _peek_target(self) -> float:
        if self.state in ACTIVE:
            return self.PEEK_ACTIVE
        if self.hovered or time.monotonic() < self.linger_until:
            return self.PEEK_HOVER
        return self.PEEK_SLEEP if self.state in (MUTED, LOADING) else self.PEEK_IDLE

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
        self.update()
        self._wake()

    def _set_heard(self, text: str):
        # Whisper's text replaces the live caption; words that match stay put.
        self.you.set(text, animate=True)
        self.live = False
        self.answer.clear()                               # new turn
        self.update()
        self._wake()

    def _set_countdown(self, v: float):
        self.countdown = v
        self.update()
        self._wake()

    def _set_answer(self, text: str):
        self.answer.set(text)
        self.update()
        self._wake()

    # ---- mouse ----------------------------------------------------------------------
    def enterEvent(self, e):
        self.hovered = True
        self._wake()

    def leaveEvent(self, e):
        self.hovered = False
        self._wake()

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
            self.cat.rim = not self.theme.light
        mic, spk = self.levels()
        self.mic_l += (mic - self.mic_l) * 0.35
        self.spk_l += (spk - self.spk_l) * 0.45

        # Card: Windows-like (quick, no bounce).
        target = 1.0 if self._want_card() else 0.0
        k = 260.0
        self.pv += (k * (target - self.p) - 2 * math.sqrt(k) * 0.9 * self.pv) * dt
        self.p += self.pv * dt
        if abs(target - self.p) < 0.002 and abs(self.pv) < 0.01:
            self.p, self.pv = target, 0.0

        # Cat: pops up with a little overshoot, sinks back without.
        goal = self._peek_target()
        k = 170.0
        ratio = 0.5 if goal > self.peek else 0.95
        self.peek_v += (k * (goal - self.peek) - 2 * math.sqrt(k) * ratio * self.peek_v) * dt
        self.peek += self.peek_v * dt
        cat_moving = abs(goal - self.peek) > 0.001 or abs(self.peek_v) > 0.005
        if not cat_moving:
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

        frame_changed = self.cat.update(self.state, self.hovered, self.spk_l, now)
        busy = card or self.state in ACTIVE or cat_moving
        if busy or frame_changed:
            self.update()
        elif self.state in (MUTED, LOADING):
            self.update(self._zzz_rect())               # asleep: repaint just the drifting "z"s
        # 60 fps while anything moves; 15 fps at rest (enough for blinks and the "z"s).
        interval = self.FAST_MS if busy else self.SLOW_MS
        if self.tick.interval() != interval:
            self.tick.setInterval(interval)

    FAST_MS, SLOW_MS = 16, 66

    def _wake(self):
        """Something changed: animate at full speed from the next frame."""
        if self.tick.interval() != self.FAST_MS:
            self.tick.setInterval(self.FAST_MS)

    # ---- layout ---------------------------------------------------------------------------
    def _text_w(self) -> float:
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
        if self.state == THINKING:
            return "busy"
        if self.state == LISTENING and self.countdown >= 0 and not self.live:
            return "countdown"
        return ""

    def _status(self) -> str:
        if self.state == LISTENING and self.countdown >= 0 and not self.live:
            return "Your turn"
        if self.state == IDLE and self.answer.words:
            return "Nova"
        return STATUS_TEXT.get(self.state, "")

    def _content_height(self) -> float:
        w = self._text_w()
        mode = self._mode()
        if mode == "caption":
            body = self.you.height(self.F_CAPTION, w, 3)
        elif mode == "answer":
            body = self.answer.height(self.F_ANSWER, w, 5)
            if self.you.words:
                body += QFontMetricsF(self.F_YOU).lineSpacing() + 4
        elif mode == "question":
            body = self.you.height(self.F_CAPTION, w, 3)
        else:
            body = QFontMetricsF(self.F_HINT).lineSpacing()
        status = QFontMetricsF(self.F_STATUS).lineSpacing() + 5 if self._status() else 0
        extra = 4 if self._progress() else 0
        return clamp(self.PAD_TOP + status + body + self.PAD_BOTTOM + extra, self.MIN_CARD_H, self.MAX_CARD_H)

    def _hint(self) -> str:
        return {IDLE: "Say “Hey Nova”", MUTED: "Click the tray icon to wake me up.",
                LOADING: "Loading voices and models…", LISTENING: "Go ahead, I'm listening…"}.get(self.state, "")

    def _content_width(self) -> float:
        """Full width for text; a short hint gets a card that fits it (like a Windows teaching tip)."""
        if self._mode() != "hint":
            return self.CARD_W
        widths = [QFontMetricsF(self.F_HINT).horizontalAdvance(self._hint())]
        if self._status():
            widths.append(QFontMetricsF(self.F_STATUS).horizontalAdvance(self._status()) + 30)
        return clamp(max(widths) + 2 * self.PAD_X + 2, 120, self.CARD_W)

    def _card_rect(self) -> QRectF:
        bottom = self.H - self.CAT_H - self.BEAK_H - 3
        return QRectF((self.W - self.cw) / 2, bottom - self.h, self.cw, self.h)

    def _card_path(self, r: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(r, self.CARD_R, self.CARD_R)
        cx = r.center().x()
        beak = QPainterPath()
        beak.moveTo(cx - self.BEAK_W / 2, r.bottom() - 1)
        beak.quadTo(cx - 2.5, r.bottom() + self.BEAK_H - 2, cx, r.bottom() + self.BEAK_H)
        beak.quadTo(cx + 2.5, r.bottom() + self.BEAK_H - 2, cx + self.BEAK_W / 2, r.bottom() - 1)
        beak.closeSubpath()
        return path.united(beak)

    # ---- painting --------------------------------------------------------------------------
    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        now = time.monotonic()

        # Invisible hover/click zone over the cat (alpha 0 would be click-through).
        qp.fillRect(QRectF((self.W - 150) / 2, self.H - self.CAT_H * 0.85, 150, self.CAT_H * 0.85),
                    QColor(0, 0, 0, 1))

        if self.p > 0.01:
            qp.save()
            # Windows-style entrance: fade in while rising a few pixels, scaling from the pointer.
            pc = clamp(self.p)
            anchor = QPointF(self.W / 2, self.H - self.CAT_H)
            qp.translate(anchor)
            s = 0.96 + 0.04 * pc
            qp.scale(s, s)
            qp.translate(-anchor.x(), -anchor.y() + (1 - pc) * 10)
            qp.setOpacity(clamp(self.p * 1.4))
            self._paint_card(qp, now)
            qp.restore()

        self._paint_cat(qp, now)

    def _paint_cat(self, qp: QPainter, now: float):
        since = now - self.state_t
        sink = round((1 - self.peek) * pixelcat.ROWS)     # moves in whole art pixels, like a sprite
        dx = 0
        if self.state == LISTENING and self.mic_l > 0.12:
            sink -= 1                                      # hops while you talk
        elif self.state == SPEAKING and self.cat.mouth:
            sink -= 1                                      # bobs as it talks
        elif self.state == ALERT and int(since / 0.25) % 2:
            sink -= 1
        elif self.state == ERROR and since < 0.5:
            dx = (1, -1, 1, -1, 1, 0)[min(int(since / 0.08), 5)]   # a quick shake
        self.cat.paint(qp, self.W / 2, self.H, max(sink, 0), dx)
        if self.state in (MUTED, LOADING):
            self._paint_zzz(qp, now)

    def _zzz_origin(self) -> QPointF:
        """Where the z's start: just above the sleeping cat's right ear."""
        shown = round(self.peek * pixelcat.ROWS)
        return QPointF(self.W / 2 + 5 * Cat.PX, self.H - (shown - 1) * Cat.PX)

    def _zzz_rect(self):
        o = self._zzz_origin()
        return QRectF(o.x() - 4, o.y() - 20 * Cat.PX, 16 * Cat.PX, 21 * Cat.PX).toAlignedRect()

    def _paint_zzz(self, qp: QPainter, now: float):
        """Pixel z's drifting up from the sleeping cat, a whole art pixel at a time."""
        dpr = qp.device().devicePixelRatioF()
        px = self.cat.scale(dpr) / dpr                       # one of the cat's pixels
        zp = max(1, self.cat.scale(dpr) // 2) / dpr          # one of the z's pixels
        o = self._zzz_origin()
        for i in range(3):
            ph = ((now - self.t0) / 3.6 + i / 3) % 1.0
            gx, gy = round(ph * 9), round(ph * 15) + 3     # cat pixels right of / above the origin
            x = round((o.x() + gx * px) * dpr) / dpr
            y = round((o.y() - gy * px) * dpr) / dpr
            c = QColor(self.theme.text2 if not self.theme.light else QColor(70, 70, 80))
            c.setAlphaF(c.alphaF() * (1.0 if ph < 0.75 else (1 - ph) / 0.25))
            for zy, row in enumerate(pixelcat.Z):
                for zx, ch in enumerate(row):
                    if ch != ".":
                        qp.fillRect(QRectF(x + zx * zp, y + zy * zp, zp, zp), c)

    def _paint_card(self, qp: QPainter, now: float):
        th = self.theme
        r = self._card_rect()
        path = self._card_path(r)
        # Soft shadow (stacked translucent outlines, a little below).
        qp.setBrush(Qt.NoBrush)
        for i in range(1, 13):
            sp = QPainterPath()
            sp.addRoundedRect(r.adjusted(-i, -i + 4, i, i + 4), self.CARD_R + i, self.CARD_R + i)
            qp.setPen(QPen(QColor(0, 0, 0, int(th.shadow * (1 - i / 13) ** 2)), 1.6))
            qp.drawPath(sp)
        qp.fillPath(path, th.card)
        qp.setPen(QPen(th.stroke, 1.0))
        qp.drawPath(path)

        qp.save()
        qp.setClipRect(r.adjusted(0, 0, 0, -2))
        x, w = r.left() + self.PAD_X, r.width() - 2 * self.PAD_X
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
            self.you.draw(qp, self.F_CAPTION, th.text, x, y, w, 3)
            self._paint_caret(qp, x, y, w, now)
        elif mode == "question":
            self.you.draw(qp, self.F_CAPTION, th.text, x, y, w, 3)
        elif mode == "answer":
            fy = QFontMetricsF(self.F_YOU)
            if self.you.words:
                qp.setFont(self.F_YOU)
                qp.setPen(th.text3)
                qp.drawText(QPointF(x, y + fy.ascent()), fy.elidedText("“" + self.you.text + "”", Qt.ElideRight, w))
                y += fy.lineSpacing() + 4
            self.answer.draw(qp, self.F_ANSWER, th.text, x, y, w, 5)
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
        self._paint_progress(qp, r, now)

    def _paint_progress(self, qp: QPainter, r: QRectF, now: float):
        """Windows-style progress bar along the bottom of the card."""
        kind = self._progress()
        if not kind:
            return
        track = QRectF(r.left() + self.PAD_X, r.bottom() - 9, r.width() - 2 * self.PAD_X, 3)
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
        lines = self.you.layout(fm, w)[-3:]
        if not lines or not lines[-1]:
            return
        idx, wx = lines[-1][-1]
        cx = x + wx + fm.horizontalAdvance(self.you.words[idx]) + 3
        cy = y + (len(lines) - 1) * fm.lineSpacing()
        c = QColor(self.theme.accent)
        c.setAlphaF(1.0 if (now - self.t0) % 1.0 < 0.55 else 0.0)    # on/off like a text caret
        qp.fillRect(QRectF(cx, cy + fm.ascent() * 0.12, 1.5, fm.ascent() * 1.0), c)


# ---- tray ----------------------------------------------------------------------------------
def cat_icon(muted: bool = False) -> QIcon:
    """The pixel cat for the tray (asleep when muted), at whole-pixel sizes so it stays crisp."""
    light, _ = windows_theme()
    art = pixelcat.image("sleep" if muted else "idle", rim=not light)
    icon = QIcon()
    for s in (1, 2, 3, 4):                          # 24 to 96 px
        canvas = QImage(24 * s, 24 * s, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        qp = QPainter(canvas)
        qp.drawImage(QPointF(0, 2 * s), art.scaled(art.width() * s, art.height() * s,
                                                   Qt.IgnoreAspectRatio, Qt.FastTransformation))
        qp.end()
        icon.addPixmap(QPixmap.fromImage(canvas))
    return icon


class Tray(QSystemTrayIcon):
    def __init__(self, bridge: Bridge, voices: list | None = None, voice: str = "", notch_shown: bool = True):
        super().__init__(cat_icon())
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
        if voices:
            vm = self.menu.addMenu("Voice")
            group = QActionGroup(vm)
            group.setExclusive(True)
            for vid, label in voices:
                act = QAction(label, vm, checkable=True)
                act.setChecked(vid == voice)
                act.triggered.connect(lambda _=False, v=vid: bridge.voice_selected.emit(v))
                group.addAction(act)
                vm.addAction(act)
        self.menu.addSeparator()
        self.menu.addAction("Quit NOVA", bridge.quit_requested.emit)
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)

    def _mute(self, on: bool):
        self.setIcon(cat_icon(muted=on))
        self.setToolTip("NOVA: muted" if on else "NOVA: say “Hey Nova”")
        self.bridge.mute_toggled.emit(on)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:          # left click toggles mute
            self.mute_action.toggle()
