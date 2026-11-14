"""NOVA settings, inside NOVA's window (hud.py: the settings button, or tray > Settings),
in the style of Windows 11 Settings. A sidebar on the left; pages on the right:

    Home        status, what's next, quick switches, things to try
    Timers      running timers (live countdown, cancel), start one
    Reminders   upcoming reminders (delete), add one
    Notes       notes and lists (edit, move to the bin, restore), add one
    Apps        which apps NOVA may open, and what else you call them
    Appearance  mascot, notch
    Voice       voice, speaking speed
    Assistant   your city, Claude's model, conversation, what NOVA can do
    Privacy     what NOVA can and can't do, where your data is

SettingsPanel is the sidebar and the pages; NOVA's window slides it in from the side.

Changes save straight away. Most apply live; the few that need a restart show a banner with a
"Restart NOVA" button. Settings go to state.json ("settings"), so config.toml is never rewritten.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDateTimeEdit, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from . import config, pc_tools, state, themes, tools_server, ui
from .fluent import (Card, Colors, NavButton, Tile, Toggle, accent_button, glyph, icon_button, label,
                     setting_row)
from .memory import BIN_DAYS, DATA, spoken_when

log = logging.getLogger("nova.settings")

REPEAT_NAMES = [("none", "Once"), ("daily", "Every day"), ("weekdays", "Weekdays"), ("weekly", "Every week")]
STATUS = {ui.IDLE: "Listening for “Hey Nova”", ui.MUTED: "Muted: the wake word is off",
          ui.LOADING: "Starting up…", ui.LISTENING: "Listening to you", ui.THINKING: "Thinking",
          ui.SEARCHING: "Looking something up", ui.SPEAKING: "Talking", ui.ALERT: "Ringing",
          ui.ERROR: "Something went wrong (see Privacy & safety > Logs)"}


def save_setting(key: str, value):
    """Remember a setting ("section.key") in state.json; applied over config.toml at startup."""
    s = state.load().get("settings", {})
    s[key] = value
    state.save(settings=s)
    log.info("setting %s = %r", key, value)


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def row_card(icon: str, title: str, caption: str, right: list[QWidget], wrap: bool = True) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    h = QHBoxLayout(f)
    h.setContentsMargins(14, 10, 10, 10)
    h.setSpacing(12)
    h.addWidget(glyph(icon, 13))
    v = QVBoxLayout()
    v.setSpacing(1)
    t = label(title, "body", wrap=wrap)
    v.addWidget(t)
    if caption:
        v.addWidget(label(caption, "caption", wrap=wrap))
    h.addLayout(v, 1)
    for w in right:
        h.addWidget(w, 0, Qt.AlignVCenter)
    f.title_label = t
    return f


# ---- pages ------------------------------------------------------------------------------------------
class Page(QScrollArea):
    def __init__(self, win: "SettingsPanel", title: str, subtitle: str = ""):
        super().__init__()
        self.win, self.c, self.ctx = win, win.c, win.ctx
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("scrollbody")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(36, 26, 36, 30)
        self.lay.setSpacing(6)
        self.lay.addWidget(label(title, "title"))
        if subtitle:
            self.lay.addWidget(label(subtitle, "caption", wrap=True))
        self.lay.addSpacing(12)
        self.setWidget(body)
        self.viewport().setAutoFillBackground(False)

    def section(self, text: str):
        self.lay.addSpacing(14)
        self.lay.addWidget(label(text, "subtitle", size=10.5))
        self.lay.addSpacing(2)

    def finish(self):
        self.lay.addStretch(1)

    @property
    def assistant(self):
        return self.ctx.assistant

    def refresh(self):
        pass


class HomePage(Page):
    def __init__(self, win):
        super().__init__(win, "Home", "NOVA, your voice assistant. Say “Hey Nova” and ask anything.")
        head = QHBoxLayout()
        self.pic = QLabel()
        head.addWidget(self.pic)
        v = QVBoxLayout()
        v.addWidget(label("NOVA", "subtitle", size=16))
        self.status = label("", "caption")
        v.addWidget(self.status)
        head.addLayout(v, 1)
        self.lay.addLayout(head)

        self.section("Up next")
        self.next = row_card("reminder", "Nothing scheduled", "Timers and reminders you set show up here.", [])
        self.lay.addWidget(self.next)

        self.section("Quick settings")
        self.wake = Toggle(self.c)
        self.wake.toggled.connect(lambda on: self.ctx.tray.mute_action.setChecked(not on))
        self.lay.addWidget(setting_row("Wake word", "Listen for “Hey Nova”. Off = muted (also frees video memory).",
                                       self.wake, "mic"))
        self.notch = Toggle(self.c)
        self.notch.toggled.connect(lambda on: self.ctx.tray.notch_action.setChecked(on))
        self.lay.addWidget(setting_row("Show the notch", "The mascot at the bottom of the screen.", self.notch,
                                       "appearance"))
        fresh = QPushButton("Start fresh")
        fresh.clicked.connect(self.ctx.bridge.new_conversation.emit)
        self.lay.addWidget(setting_row("New conversation", "Make Claude forget what you talked about.", fresh,
                                       "refresh"))

        self.section("Try saying")
        tips = Card(spacing=4)
        for t in ("“Hey Nova, what's the weather tomorrow?”", "“What's in the news?”",
                  "“Set a pasta timer for 8 minutes.”", "“Remind me at 5 to call Mum.”",
                  "“Add milk to my shopping list.”", "“Pause the music.” / “Turn the volume down.”",
                  "“Play Blinding Lights on Spotify.” / “Play my liked songs.” (once Spotify is allowed "
                  "under Apps)"):
            tips.lay.addWidget(label(t, "body"))
        self.lay.addWidget(tips)
        self.finish()

    def refresh(self):
        n = self.ctx.notch
        self.pic.setPixmap(ui.mascot_icon(n.mascot, n.state == ui.MUTED).pixmap(56, 56))
        self.status.setText(STATUS.get(n.state, ""))
        nxt = self.assistant.upcoming()
        self.next.title_label.setText(nxt.replace("  ·  ", "    ·    ") if nxt else "Nothing scheduled")
        for t, on in ((self.wake, not self.ctx.tray.mute_action.isChecked()),
                      (self.notch, self.ctx.tray.notch_action.isChecked())):
            if t.isChecked() != on:
                t.blockSignals(True)
                t.setChecked(on)
                t._pos = 1.0 if on else 0.0
                t.blockSignals(False)
                t.update()


class TimersPage(Page):
    def __init__(self, win):
        super().__init__(win, "Timers", "Timers ring with a chime, NOVA's voice and the notch, even when muted. "
                                        "They're forgotten if NOVA quits.")
        add = Card()
        add.lay.addWidget(label("Start a timer", "body"))
        h = QHBoxLayout()
        self.mins = QSpinBox(minimum=0, maximum=1440, value=5, suffix=" min")
        self.secs = QSpinBox(minimum=0, maximum=59, value=0, suffix=" s")
        self.name = QLineEdit(placeholderText="Name (optional), e.g. pasta")
        self.name.returnPressed.connect(self._start)
        go = accent_button("Start")
        go.clicked.connect(self._start)
        for w in (self.mins, self.secs):
            w.setFixedWidth(100)
            h.addWidget(w)
        h.addWidget(self.name, 1)
        h.addWidget(go)
        add.lay.addLayout(h)
        self.err = label("", "error")
        self.err.hide()
        add.lay.addWidget(self.err)
        self.lay.addWidget(add)
        self.section("Running")
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        self.lay.addLayout(self.box)
        self.rows: dict[int, QFrame] = {}
        self.finish()

    @property
    def timers(self):
        pc = self.assistant.pc
        return pc.timers if pc else None

    def _start(self):
        if not self.timers:
            return
        try:
            self.timers.set(self.mins.value() * 60 + self.secs.value(), self.name.text())
            self.name.clear()
            self.err.hide()
            self.refresh()
        except ValueError as e:
            self.err.setText(str(e).capitalize() + ".")
            self.err.show()

    def refresh(self):
        if not self.timers:
            if not self.rows.get(-1):
                clear(self.box)
                off = not self.ctx.cfg["pc"]["enabled"]
                self.rows = {-1: label("Timers are part of PC control, which is off (Assistant page)." if off
                                       else "Starting up…", "caption")}
                self.box.addWidget(self.rows[-1])
            return
        snap = self.timers.snapshot()
        if set(self.rows) != {t["id"] for t in snap} or not snap:
            clear(self.box)
            self.rows = {}
            for t in snap:
                count = label("", "subtitle", size=15)
                cancel = icon_button("cancel", "Cancel this timer")
                cancel.clicked.connect(lambda _=False, tid=t["id"]: (self.timers.cancel(str(tid)), self.refresh()))
                r = row_card("timer", t["name"], f"of {pc_tools.spoken_duration(t['seconds'])}", [count, cancel])
                r.count = count
                self.rows[t["id"]] = r
                self.box.addWidget(r)
            if not snap:
                self.rows = {}
                self.box.addWidget(label("No timers running. Start one above, or say “Hey Nova, set a timer for "
                                         "10 minutes”.", "caption", wrap=True))
        for t in snap:
            left = round(t["left"])
            h, rest = divmod(left, 3600)
            self.rows[t["id"]].count.setText(f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h
                                             else f"{rest // 60}:{rest % 60:02d}")


class RemindersPage(Page):
    def __init__(self, win):
        super().__init__(win, "Reminders", "NOVA says them out loud when they're due. If the PC was off, it tells "
                                           "you when it starts. Kept only on this PC.")
        add = Card()
        add.lay.addWidget(label("Add a reminder", "body"))
        self.text = QLineEdit(placeholderText="What should NOVA remind you about? e.g. call Mum")
        self.text.returnPressed.connect(self._add)
        add.lay.addWidget(self.text)
        h = QHBoxLayout()
        self.when = QDateTimeEdit(calendarPopup=True)
        self.when.setDisplayFormat("ddd d MMM yyyy,  h:mm AP")
        self.repeat = QComboBox()
        for _, name in REPEAT_NAMES:
            self.repeat.addItem(name)
        go = accent_button("Add")
        go.clicked.connect(self._add)
        h.addWidget(self.when, 1)
        h.addWidget(self.repeat)
        h.addWidget(go)
        add.lay.addLayout(h)
        self.err = label("", "error")
        self.err.hide()
        add.lay.addWidget(self.err)
        self.lay.addWidget(add)
        self._reset_time()
        self.section("Upcoming")
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        self.lay.addLayout(self.box)
        self._sig = None
        self.finish()

    def _reset_time(self):
        t = (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        self.when.setDateTime(QDateTime.fromSecsSinceEpoch(int(t.timestamp())))

    @property
    def mem(self):
        return self.assistant.memory

    def _add(self):
        if not self.mem:
            return
        try:
            when = self.when.dateTime().toString("yyyy-MM-ddTHH:mm")
            self.mem.reminders.add(self.text.text(), when, REPEAT_NAMES[self.repeat.currentIndex()][0])
            self.text.clear()
            self.err.hide()
            self._reset_time()
            self.refresh()
        except ValueError as e:
            self.err.setText(str(e)[:1].upper() + str(e)[1:] + ".")
            self.err.show()

    def refresh(self):
        if not self.mem:
            if self._sig != "off":
                self._sig = "off"
                clear(self.box)
                self.box.addWidget(label("Reminders and notes are off (Assistant page).", "caption"))
            return
        items = self.mem.reminders.items()
        sig = tuple((r["id"], r["due"]) for r in items)
        if sig == self._sig:
            return
        self._sig = sig
        clear(self.box)
        rep = dict(REPEAT_NAMES)
        for r in items:
            when = spoken_when(r["due"])
            cap = (when[:1].upper() + when[1:]) + ("" if r["repeat"] == "none" else f"  ·  {rep[r['repeat']]}")
            delete = icon_button("delete", "Delete this reminder")
            delete.clicked.connect(lambda _=False, rid=r["id"]: (self.mem.reminders.delete_id(rid), self.refresh()))
            self.box.addWidget(row_card("reminder", r["text"], cap, [delete]))
        if not items:
            self.box.addWidget(label("No reminders. Add one above, or say “Hey Nova, remind me at 5 to call Mum”.",
                                     "caption", wrap=True))


class NoteRow(QFrame):
    def __init__(self, page: "NotesPage", n: dict, in_bin: bool):
        super().__init__()
        self.setObjectName("card")
        self.page, self.n = page, n
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 10, 10)
        h.setSpacing(12)
        h.addWidget(glyph("list" if n.get("list") else "notes", 13))
        v = QVBoxLayout()
        v.setSpacing(1)
        self.text = label(n["text"], "body", wrap=True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.edit = QLineEdit(n["text"])
        self.edit.hide()
        self.edit.returnPressed.connect(self._save)
        v.addWidget(self.text)
        v.addWidget(self.edit)
        when = datetime.fromtimestamp(n["created"]).strftime("%d %b %Y").lstrip("0")
        cap = when + (f"  ·  {n['list']} list" if n.get("list") else "")
        if in_bin:
            until = datetime.fromtimestamp(n["deleted"]) + timedelta(days=BIN_DAYS)
            cap += f"  ·  in the bin until {until.strftime('%d %b').lstrip('0')}"
        v.addWidget(label(cap, "caption"))
        h.addLayout(v, 1)
        if in_bin:
            b = icon_button("restore", "Restore this note")
            b.clicked.connect(lambda: self._set_deleted(False))
            h.addWidget(b)
        else:
            self.edit_btn = icon_button("edit", "Edit")
            self.edit_btn.clicked.connect(self._toggle_edit)
            b = icon_button("delete", "Move to the bin")
            b.clicked.connect(lambda: self._set_deleted(True))
            h.addWidget(self.edit_btn)
            h.addWidget(b)

    def _set_deleted(self, on: bool):
        self.page.mem.notes.set_deleted(self.n["id"], on)
        self.page.refresh(force=True)

    def _toggle_edit(self):
        if self.edit.isVisible():
            self._save()
            return
        self.text.hide()
        self.edit.show()
        self.edit.setFocus()
        self.edit_btn.setText("")                     # save glyph
        self.edit_btn.setToolTip("Save")
        self.page.editing = True

    def _save(self):
        try:
            self.page.mem.notes.update(self.n["id"], self.edit.text())
        except ValueError:
            return
        self.page.editing = False
        self.page.refresh(force=True)


class NotesPage(Page):
    def __init__(self, win):
        super().__init__(win, "Notes", f"Things you asked NOVA to remember, and your lists. Deleted notes stay in the "
                                       f"bin for {BIN_DAYS} days. Kept only on this PC.")
        add = Card()
        add.lay.addWidget(label("Add a note", "body"))
        h = QHBoxLayout()
        self.text = QLineEdit(placeholderText="A note, or an item for a list (e.g. milk)")
        self.text.returnPressed.connect(self._add)
        self.list = QComboBox(editable=True)
        self.list.lineEdit().setPlaceholderText("No list")
        self.list.setMinimumWidth(150)
        go = accent_button("Add")
        go.clicked.connect(self._add)
        h.addWidget(self.text, 1)
        h.addWidget(self.list)
        h.addWidget(go)
        add.lay.addLayout(h)
        self.lay.addWidget(add)
        self.lay.addSpacing(10)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.lay.addLayout(self.chips)
        self.lay.addSpacing(4)
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        self.lay.addLayout(self.box)
        self.filter = "all"
        self.editing = False
        self._sig = None
        self.finish()

    @property
    def mem(self):
        return self.assistant.memory

    def _add(self):
        if not self.mem or not self.text.text().strip():
            return
        lst = self.list.currentText().strip()
        self.mem.notes.add(self.text.text(), "" if lst.lower() == "no list" else lst)
        self.text.clear()
        self.refresh(force=True)

    def _chips(self, lists: list[str], binned: int):
        clear(self.chips)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text in [("all", "All notes")] + [(f"list:{x}", x.capitalize()) for x in lists] + \
                         [("bin", f"Bin ({binned})")]:
            b = QToolButton()
            b.setObjectName("chip")
            b.setText(text)
            b.setCheckable(True)
            b.setChecked(key == self.filter)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            group.addButton(b)
            self.chips.addWidget(b)
        self.chips.addStretch(1)

    def _set_filter(self, key: str):
        self.filter = key
        self.refresh(force=True)

    def refresh(self, force: bool = False):
        if not self.mem:
            if self._sig != "off":
                self._sig = "off"
                clear(self.box)
                self.box.addWidget(label("Reminders and notes are off (Assistant page).", "caption"))
            return
        if self.editing and not force:
            return
        notes = self.mem.notes
        lists = notes.lists()
        binned = notes.items(deleted=True)
        if self.filter.startswith("list:") and self.filter[5:] not in lists:
            self.filter = "all"
        if self.filter == "bin":
            shown = binned
        else:
            shown = [n for n in notes.items() if self.filter == "all" or n.get("list") == self.filter[5:]]
        sig = (self.filter, tuple(lists), len(binned), tuple((n["id"], n["text"]) for n in shown))
        if sig == self._sig and not force:
            return
        self._sig = sig
        current = self.list.currentText()
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems(["No list"] + lists)
        self.list.setCurrentText(current if current else "No list")
        self.list.blockSignals(False)
        self._chips(lists, len(binned))
        clear(self.box)
        self.editing = False
        for n in shown:
            self.box.addWidget(NoteRow(self, n, self.filter == "bin"))
        if not shown:
            self.box.addWidget(label("The bin is empty." if self.filter == "bin" else
                                     "No notes yet. Add one above, or say “Hey Nova, note that…” or “add milk to "
                                     "my shopping list”.", "caption", wrap=True))


class AppRow(QFrame):
    def __init__(self, page: "AppsPage", app: dict, allowed: dict | None):
        super().__init__()
        self.setObjectName("card")
        self.page, self.app = page, app
        self.blocked = pc_tools.is_blocked(app["name"])
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 12, 8)
        h.setSpacing(12)
        h.addWidget(glyph("lock" if self.blocked else "apps", 12))
        self.v = QVBoxLayout()
        self.v.setSpacing(3)
        self.v.addWidget(label(app["name"], "body"))
        if self.blocked:
            self.v.addWidget(label("A system tool, terminal or uninstaller: NOVA never opens these.", "caption"))
        h.addLayout(self.v, 1)
        self.toggle = Toggle(page.c, bool(allowed))
        self.toggle.setEnabled(not self.blocked)
        self.toggle.toggled.connect(self._toggled)
        h.addWidget(self.toggle, 0, Qt.AlignVCenter)
        # The "also called" box only for allowed apps (made on first use: most rows never need one).
        self.alias: QLineEdit | None = None
        self._aliases = ", ".join((allowed or {}).get("aliases", []))
        if allowed:
            self._alias_box().show()

    def _alias_box(self) -> QLineEdit:
        if self.alias is None:
            # Parented to the row from the start: a parentless widget that's shown, even for a moment,
            # becomes a window of its own (the "python" windows that used to flash up).
            self.alias = QLineEdit(self._aliases, self)
            self.alias.setPlaceholderText("Also called… (other names, separated by commas)")
            self.alias.editingFinished.connect(self.page.save)
            self.alias.hide()
            self.v.addWidget(self.alias)
        return self.alias

    def _toggled(self, on: bool):
        if on:
            self._alias_box().show()
        elif self.alias is not None:
            self.alias.hide()
        self.page.save()

    def entry(self) -> dict | None:
        if not self.toggle.isChecked() or self.blocked:
            return None
        text = self.alias.text() if self.alias is not None else self._aliases
        aliases = [a.strip() for a in text.split(",") if a.strip()]
        return {"name": self.app["name"], "id": self.app["id"], "aliases": aliases}


BATCH = 30                                               # app rows built per turn of the event loop


class AppsPage(Page):
    found = Signal(list)

    def __init__(self, win):
        super().__init__(win, "Apps", "Switch on the apps NOVA may open when you ask (“Hey Nova, open Spotify”), "
                                      "and add other names you use for them. NOVA can only open apps: it never "
                                      "closes them or changes anything in them. Allowing Spotify also lets NOVA "
                                      "play music in it (it never changes your playlists or library). Terminals, system tools and "
                                      "uninstallers can't be allowed.")
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search apps")
        self.search.textChanged.connect(self._filter)
        self.only_on = QToolButton()
        self.only_on.setObjectName("chip")
        self.only_on.setText("Allowed only")
        self.only_on.setCheckable(True)
        self.only_on.toggled.connect(lambda _: self._filter(self.search.text()))
        refresh = QPushButton("Refresh list")
        refresh.clicked.connect(self._scan)
        top.addWidget(self.search, 1)
        top.addWidget(self.only_on)
        top.addWidget(refresh)
        self.lay.addLayout(top)
        self.status = label("", "caption")
        self.lay.addWidget(self.status)
        self.box = QVBoxLayout()
        self.box.setSpacing(4)
        self.lay.addLayout(self.box)
        self.rows: list[AppRow] = []
        self._build = 0
        self.found.connect(self._on_found)
        self._scanned = False
        self.finish()

    def _allowed(self) -> dict[str, dict]:
        return {a["id"]: a for a in state.load().get("apps", []) if a.get("id")}

    def refresh(self):
        if not self._scanned:
            self._scanned = True
            self._populate([])                              # the allowed ones, while the scan runs
            self._scan()

    def _scan(self):
        self.status.setText("Finding your apps…")
        threading.Thread(target=lambda: self.found.emit(self._discover()), daemon=True, name="apps-scan").start()

    def _on_found(self, found: list[dict]):
        self._populate(found)
        allowed = len(self._allowed())
        self.status.setText(f"{len(found)} apps found, {allowed} allowed." if found
                            else "Couldn't list your apps. Try Refresh list.")

    @staticmethod
    def _discover() -> list[dict]:
        try:
            return pc_tools.discover_apps()
        except Exception as e:  # noqa: BLE001
            log.warning("couldn't list apps: %s", e)
            return []

    def _populate(self, found: list[dict]):
        allowed = self._allowed()
        apps = {a["id"]: a for a in found}
        for app_id, a in allowed.items():
            apps.setdefault(app_id, a)
        order = sorted(apps.values(), key=lambda a: (a["id"] not in allowed, pc_tools.is_blocked(a["name"]),
                                                     a["name"].lower()))
        clear(self.box)
        self.rows = []
        self._build += 1
        self._add_rows(self._build, order, allowed)

    def _add_rows(self, build: int, todo: list[dict], allowed: dict):
        """A batch of rows now and the rest on the next turns of the event loop, so the window keeps
        responding while 200-odd apps go in. The allowed apps sort first, so they're always in the
        first batch, and save() (which only keeps switched-on rows) never misses one."""
        if build != self._build:                         # a newer scan replaced this list
            return
        for a in todo[:BATCH]:
            r = AppRow(self, a, allowed.get(a["id"]))
            self.rows.append(r)
            self.box.addWidget(r)
            r.setVisible(self._matches(r))
        if todo[BATCH:]:
            QTimer.singleShot(0, lambda: self._add_rows(build, todo[BATCH:], allowed))

    def _matches(self, r: AppRow) -> bool:
        t = self.search.text().strip().lower()
        return (not t or t in r.app["name"].lower()) and (not self.only_on.isChecked() or r.toggle.isChecked())

    def _filter(self, _text: str = ""):
        for r in self.rows:
            r.setVisible(self._matches(r))

    def save(self):
        apps = [e for r in self.rows if (e := r.entry())]
        state.save(apps=apps)
        self.status.setText(f"Saved: {len(apps)} app{'s' if len(apps) != 1 else ''} allowed.")
        log.info("allowed apps: %s", ", ".join(a["name"] for a in apps) or "none")


class AppearancePage(Page):
    def __init__(self, win):
        super().__init__(win, "Appearance", "How NOVA looks at the bottom of your screen and in the tray.")
        self.section("Mascot")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles: dict[str, Tile] = {}
        self.mascot_pics: dict[str, QLabel] = {}
        for i, (key, name) in enumerate(ui.MASCOT_MENU):
            t = Tile()
            v = QVBoxLayout(t)
            v.setContentsMargins(10, 12, 10, 10)
            pic = QLabel()
            pic.setPixmap(ui.mascot_icon(key).pixmap(48, 48))
            pic.setAlignment(Qt.AlignCenter)
            self.mascot_pics[key] = pic
            v.addWidget(pic)
            nm = label(name, "body")
            nm.setAlignment(Qt.AlignCenter)
            v.addWidget(nm)
            t.setFixedSize(QSize(120, 104))
            t.clicked.connect(lambda k=key: self._pick(k))
            grid.addWidget(t, i // 5, i % 5)
            self.tiles[key] = t
        wrap = QHBoxLayout()
        wrap.addLayout(grid)
        wrap.addStretch(1)
        self.lay.addLayout(wrap)

        self.section("Colour theme")
        self.lay.addWidget(label("For NOVA's logo (on its windows) and the orb.", "caption"))
        self.theme_tiles, _ = self._tile_grid(
            [(k, name) for k, (name, _) in themes.THEMES.items()],
            lambda k: themes.logo_icon(k), self._theme)

        self.section("Notch")
        self.show_notch = Toggle(self.c, self.ctx.tray.notch_action.isChecked())
        self.show_notch.toggled.connect(lambda on: self.ctx.tray.notch_action.setChecked(on))
        self.lay.addWidget(setting_row("Show the notch", "The mascot (or the orb's pill) at the bottom centre.",
                                       self.show_notch, "appearance"))
        self.fullscreen = Toggle(self.c, self.ctx.notch.hide_in_fullscreen)
        self.fullscreen.toggled.connect(self._fullscreen)
        self.lay.addWidget(setting_row("Hide over fullscreen apps", "Games, videos and presentations: NOVA still "
                                       "listens and talks.", self.fullscreen, "pc"))
        self.captions = Toggle(self.c, self.ctx.cfg["ui"]["live_captions"])
        self.captions.toggled.connect(lambda on: (save_setting("ui.live_captions", on), self.win.need_restart()))
        self.lay.addWidget(setting_row("Live captions", "Show your words on the notch as you speak (rough at first; "
                                       "applies after a restart).", self.captions, "notes"))
        self.finish()

    def _pick(self, key: str):
        self.ctx.select_mascot(key)
        self.refresh()

    def _tile_grid(self, items, icon, on_click):
        """A row of picture tiles (5 across): {key: Tile}, {key: picture label}."""
        grid = QGridLayout()
        grid.setSpacing(8)
        tiles, pics = {}, {}
        for i, (key, name) in enumerate(items):
            t = Tile()
            v = QVBoxLayout(t)
            v.setContentsMargins(10, 12, 10, 10)
            pic = QLabel()
            pic.setPixmap(icon(key).pixmap(44, 44))
            pic.setAlignment(Qt.AlignCenter)
            v.addWidget(pic)
            nm = label(name, "body")
            nm.setAlignment(Qt.AlignCenter)
            v.addWidget(nm)
            t.setFixedSize(QSize(120, 100))
            t.clicked.connect(lambda k=key: on_click(k))
            grid.addWidget(t, i // 5, i % 5)
            tiles[key], pics[key] = t, pic
        wrap = QHBoxLayout()
        wrap.addLayout(grid)
        wrap.addStretch(1)
        self.lay.addLayout(wrap)
        return tiles, pics

    def _repaint_theme(self):
        """After a theme change: the orb's tile and the window's own icon (the theme tiles each show
        their own colours already)."""
        self.mascot_pics["orb"].setPixmap(ui.mascot_icon("orb").pixmap(48, 48))   # the orb follows the theme
        self.win.window().setWindowIcon(ui.nova_icon())
        self.win.head_pic.setPixmap(ui.nova_icon().pixmap(20, 20))
        self.refresh()

    def _theme(self, key: str):
        self.ctx.set_theme(key)
        save_setting("ui.theme", key)
        self._repaint_theme()

    def _fullscreen(self, on: bool):
        self.ctx.notch.hide_in_fullscreen = on
        save_setting("ui.hide_in_fullscreen", on)

    def refresh(self):
        for k, t in self.tiles.items():
            sel = k == self.ctx.notch.mascot
            if t.property("selected") != sel:
                t.set_selected(sel)
        for k, t in self.theme_tiles.items():
            if t.property("selected") != (k == themes.current):
                t.set_selected(k == themes.current)
        on = self.ctx.tray.notch_action.isChecked()
        if self.show_notch.isChecked() != on:
            self.show_notch.blockSignals(True)
            self.show_notch.setChecked(on)
            self.show_notch._pos = 1.0 if on else 0.0
            self.show_notch.blockSignals(False)


class VoicePage(Page):
    def __init__(self, win):
        super().__init__(win, "Voice", "Pick NOVA's voice: it says hello in the new one.")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles: dict[str, Tile] = {}
        for i, (vid, name) in enumerate(self.ctx.cfg["voice"]["menu"]):
            t = Tile()
            v = QVBoxLayout(t)
            v.setContentsMargins(14, 10, 14, 10)
            v.setSpacing(1)
            title = name.split(" (")[0]
            v.addWidget(label(title, "body"))
            v.addWidget(label("Runs on this PC" if vid.startswith("kokoro:") else "Microsoft, online", "caption"))
            t.setMinimumWidth(200)
            t.clicked.connect(lambda v_=vid: self._pick(v_))
            grid.addWidget(t, i // 3, i % 3)
            self.tiles[vid] = t
        self.lay.addLayout(grid)
        self.section("Speaking speed")
        h = QWidget()
        hl = QHBoxLayout(h)
        hl.setContentsMargins(0, 0, 0, 0)
        self.speed = QSlider(Qt.Horizontal, minimum=80, maximum=130, singleStep=5, pageStep=5)
        self.speed.setFixedWidth(220)
        self.speed_label = label("", "body")
        self.speed_label.setFixedWidth(44)
        hl.addWidget(self.speed)
        hl.addWidget(self.speed_label)
        self.speed.setValue(round(self.ctx.cfg["voice"]["kokoro_speed"] * 100))
        self.speed_label.setText(f"{self.speed.value() / 100:.2f}x")
        self.speed.valueChanged.connect(self._speed)
        self.speed.sliderReleased.connect(lambda: save_setting("voice.kokoro_speed", self.speed.value() / 100))
        self.lay.addWidget(setting_row("Speed", "For the voices that run on this PC (Kokoro).", h, "voice"))
        self.finish()

    def _pick(self, vid: str):
        self.ctx.select_voice(vid)
        self.refresh()

    def _speed(self, v: int):
        self.speed_label.setText(f"{v / 100:.2f}x")
        voice = getattr(self.assistant, "voice", None)
        if voice:
            voice.kokoro_speed = v / 100
        self.ctx.cfg["voice"]["kokoro_speed"] = v / 100

    def refresh(self):
        cur = self.ctx.current_voice()
        for vid, t in self.tiles.items():
            if t.property("selected") != (vid == cur):
                t.set_selected(vid == cur)


class AssistantPage(Page):
    def __init__(self, win):
        super().__init__(win, "Assistant", "How NOVA thinks and what it's allowed to do.")
        cfg = self.ctx.cfg
        self.section("Your city")
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        self.city = QLineEdit(cfg["claude"]["location"])
        self.city.setPlaceholderText("e.g. Manila, Philippines")
        self.city.setFixedWidth(240)
        self.city.returnPressed.connect(self._city)
        save = QPushButton("Save")
        save.clicked.connect(self._city)
        hl.addWidget(self.city)
        hl.addWidget(save)
        self.lay.addWidget(setting_row("City", "For “what's the weather?” and which country's news you get.", w, "globe"))
        self.section("Claude")
        self.model = QComboBox()
        self.model.addItems(["Haiku: fastest", "Sonnet: smarter, a little slower"])
        self.model.setCurrentIndex(1 if cfg["claude"]["model"] == "sonnet" else 0)
        self.model.currentIndexChanged.connect(self._model)
        self.lay.addWidget(setting_row("Model", "Changing it starts a fresh conversation.", self.model, "assistant"))
        self.wait = QSpinBox(minimum=4, maximum=60, suffix=" s", value=int(cfg["conversation"]["follow_up_timeout"]))
        self.wait.valueChanged.connect(self._wait)
        self.lay.addWidget(setting_row("Time to reply", "How long NOVA keeps listening after it answers, before the "
                                       "conversation ends.", self.wait, "timer"))
        self.section("What NOVA can do")
        for key, title, desc, icon in (
                ("claude.web_search", "Look things up online", "Web search, weather and news.", "globe"),
                ("pc.enabled", "Control this PC", "Media keys, volume, timers, opening the apps you allow, and Spotify.", "pc"),
                ("reminders.enabled", "Reminders and notes", "Kept only on this PC.", "reminder")):
            sec, k = key.split(".")
            t = Toggle(self.c, bool(cfg[sec][k]))
            t.toggled.connect(lambda on, key_=key: (save_setting(key_, on), self.win.need_restart()))
            self.lay.addWidget(setting_row(title, desc + " Applies after a restart.", t, icon))
        self.finish()

    def _city(self):
        city = self.city.text().strip()
        self.ctx.cfg["claude"]["location"] = city
        tools_server.LOCATION = city
        brain = getattr(self.assistant, "brain", None)
        if brain:
            brain.location = city
            brain.reset()
        save_setting("claude.location", city)

    def _model(self, i: int):
        model = "sonnet" if i == 1 else "haiku"
        self.ctx.cfg["claude"]["model"] = model
        brain = getattr(self.assistant, "brain", None)
        if brain:
            brain.model = model
            brain.reset()
        save_setting("claude.model", model)

    def _wait(self, v: int):
        self.assistant.conv["follow_up_timeout"] = float(v)
        save_setting("conversation.follow_up_timeout", float(v))


class PrivacyPage(Page):
    def __init__(self, win):
        super().__init__(win, "Privacy & safety", "What NOVA can and can't do on your PC, and where your data is.")
        self.section("NOVA can never")
        never = Card(spacing=4)
        for t in ("Delete, move or edit your files", "Run commands or scripts", "Install or uninstall anything",
                  "Change settings other than the volume", "Close your apps",
                  "Shut down, restart, sleep or sign out", "Open terminals, system tools or uninstallers"):
            never.lay.addWidget(label("•  " + t, "body"))
        self.lay.addWidget(never)
        self.section("NOVA can")
        can = Card(spacing=4)
        for t in ("Answer questions, search the web, check the weather and the news",
                  "Press the media keys and change the volume",
                  "Set timers, reminders and notes (kept only on this PC)",
                  "Open the apps you switch on under Apps",
                  "Play songs, artists, albums and playlists on Spotify, if Spotify is switched on under Apps"):
            can.lay.addWidget(label("•  " + t, "body"))
        can.lay.addWidget(label("Its tools only accept requests from this PC, with a secret that's new each time "
                                "NOVA starts, and every action is written to the log.", "caption", wrap=True))
        self.lay.addWidget(can)
        self.section("Your data")
        data = self.ctx.cfg.get("reminders", {}).get("data_dir") or str(DATA)
        for title, desc, path in (("Reminders and notes", data, data),
                                  ("Log", str(config.ROOT / "logs" / "nova.log"), str(config.ROOT / "logs")),
                                  ("Settings", str(config.ROOT / "config.toml") + " and state.json", str(config.ROOT))):
            b = QPushButton("Open folder")
            b.clicked.connect(lambda _=False, p=path: self._open(p))
            self.lay.addWidget(setting_row(title, desc, b, "folder"))
        self.finish()

    @staticmethod
    def _open(path: str):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)                                   # (Explorer, on the user's click)


# ---- the window -----------------------------------------------------------------------------------
class SettingsPanel(QWidget):
    """The sidebar and the pages (inside NOVA's window: hud.Drawer)."""
    PAGES = [("home", "Home", HomePage), ("timer", "Timers", TimersPage), ("reminder", "Reminders", RemindersPage),
             ("notes", "Notes", NotesPage), ("apps", "Apps", AppsPage), ("appearance", "Appearance", AppearancePage),
             ("voice", "Voice", VoicePage), ("assistant", "Assistant", AssistantPage),
             ("privacy", "Privacy & safety", PrivacyPage)]

    def __init__(self, ctx, colors: Colors, title: str = "NOVA settings", parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx, self.c = ctx, colors
        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QVBoxLayout()
        side.setContentsMargins(10, 16, 6, 12)
        side.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(12, 0, 0, 14)
        self.head_pic = QLabel()
        self.head_pic.setPixmap(ui.nova_icon().pixmap(20, 20))
        head.addWidget(self.head_pic)
        head.addWidget(label(title, "body"))
        head.addStretch(1)
        side.addLayout(head)
        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        self.nav: dict[str, NavButton] = {}
        self.pages: dict[str, Page] = {}
        for key, name, cls in self.PAGES:
            b = NavButton(key, name, self.c)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            if key == "privacy":
                side.addStretch(1)
            side.addWidget(b)
            self.nav[key] = b
            page = cls(self)
            self.pages[key] = page
            self.stack.addWidget(page)
        root.addLayout(side)
        right = QVBoxLayout()
        right.setContentsMargins(0, 8, 8, 8)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 10, 8)
        bl.addWidget(glyph("info", 13))
        bl.addWidget(label("Some changes apply after NOVA restarts.", "body"), 1)
        rs = accent_button("Restart NOVA")
        rs.clicked.connect(ctx.restart)
        bl.addWidget(rs)
        self.banner.hide()
        right.addWidget(self.banner)
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)

        self.tick = QTimer(self, interval=500)
        self.tick.timeout.connect(self._tick)
        self.show_page("home")

    def showEvent(self, e):
        super().showEvent(e)
        self.tick.start()
        self._tick()

    def hideEvent(self, e):
        self.tick.stop()
        super().hideEvent(e)

    def need_restart(self):
        self.banner.show()

    def show_page(self, key: str):
        if key not in self.pages:
            key = "home"
        self.nav[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        self.pages[key].refresh()

    def _tick(self):
        self.stack.currentWidget().refresh()
