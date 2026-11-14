"""Reminders and notes (phase 4), kept on this PC in data\\reminders.json and data\\notes.json.

NOVA's tools can only add, list and remove NOVA's own reminders and notes in those two files,
never anything else on the PC. A deleted note goes to the bin for 30 days (restore it from
NOVA settings > Notes), so a misheard "delete" can't lose anything for good. Files are written
atomically (a crash can't leave half a file), and an unreadable file is set aside, not overwritten.

Reminders survive restarts: one that came due while NOVA was off is announced when it starts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import ROOT

log = logging.getLogger("nova.memory")
DATA = ROOT / "data"
REPEATS = ("none", "daily", "weekdays", "weekly")
BIN_DAYS = 30


# ---- files ----------------------------------------------------------------------------------------
def _read(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        keep = path.with_name(f"{path.stem}.unreadable-{int(time.time())}{path.suffix}")
        log.error("%s is unreadable (%s); kept it as %s and started a new one", path.name, e, keep.name)
        try:
            path.replace(keep)
        except OSError:
            pass
        return []


def _write(path: Path, items: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _clean(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("the text is empty")
    return text[:limit]


# ---- time words -----------------------------------------------------------------------------------
def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def spoken_when(ts: float, now: float | None = None) -> str:
    """'today at 5:00 PM', 'tomorrow at 9:00 AM', 'Monday 17 November at 9:00 AM'."""
    now = now or time.time()
    d, today = datetime.fromtimestamp(ts).date(), datetime.fromtimestamp(now).date()
    if d == today:
        day = "today"
    elif d == today + timedelta(days=1):
        day = "tomorrow"
    elif 0 < (d - today).days < 7:
        day = d.strftime("%A")
    else:
        day = d.strftime("%A %d %B").replace(" 0", " ")
    return f"{day} at {clock(ts)}"


def _left(seconds: float) -> str:
    if seconds < 60:
        return f"{max(1, round(seconds))} seconds"
    m = max(1, round(seconds / 60))
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    days, h = divmod(h, 24)
    parts = [f"{days} day{'s' if days != 1 else ''}"] if days else []
    parts += [f"{h} hour{'s' if h != 1 else ''}"] if h else []
    parts += [f"{m} minute{'s' if m != 1 else ''}"] if m and not days else []
    return " ".join(parts)


def next_time(due: float, repeat: str, now: float) -> float:
    """The next occurrence of a repeating reminder after `now` (wall-clock time kept)."""
    dt = datetime.fromtimestamp(due)
    while dt.timestamp() <= now:
        if repeat == "daily":
            dt += timedelta(days=1)
        elif repeat == "weekly":
            dt += timedelta(days=7)
        else:                                            # weekdays
            dt += timedelta(days=1)
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
    return dt.timestamp()


# ---- reminders ------------------------------------------------------------------------------------
class Reminders:
    MAX = 200
    AHEAD = 366 * 24 * 3600

    def __init__(self, path: Path, on_due: Callable[[str], None]):
        self.path, self.on_due = path, on_due
        self._lock = threading.Lock()
        self._items: list[dict] = [r for r in _read(path) if isinstance(r, dict) and "due" in r and "text" in r]
        self._wake = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="reminders").start()

    def _save(self):
        _write(self.path, self._items)

    def add(self, text: str, when: str, repeat: str = "none") -> str:
        text = _clean(text, 200)
        repeat = repeat or "none"
        if repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
        try:
            dt = datetime.fromisoformat(str(when).strip().replace(" ", "T"))
        except ValueError:
            raise ValueError("when must be a local date and time like 2026-11-14T17:00") from None
        due = (dt if dt.tzinfo else dt.astimezone()).timestamp()
        now = time.time()
        if due < now - 60:
            raise ValueError(f"that time has already passed (it's {spoken_when(now, now)} now)")
        if due > now + self.AHEAD:
            raise ValueError("reminders can be at most a year ahead")
        with self._lock:
            if len(self._items) >= self.MAX:
                raise ValueError(f"there are already {self.MAX} reminders")
            rid = max([r["id"] for r in self._items] + [0]) + 1
            self._items.append({"id": rid, "text": text, "due": max(due, now + 1), "repeat": repeat,
                                "created": now})
            self._save()
        self._wake.set()
        log.info("reminder %d: %r %s%s", rid, text, spoken_when(due), f" ({repeat})" if repeat != "none" else "")
        again = {"none": "", "daily": ", every day", "weekdays": ", every weekday", "weekly": ", every week"}[repeat]
        return f"Reminder {rid} set for {spoken_when(due, now)} (in {_left(due - now)}){again}: {text}."

    def items(self) -> list[dict]:
        with self._lock:
            return sorted((dict(r) for r in self._items), key=lambda r: r["due"])

    def list(self) -> str:
        items, now = self.items(), time.time()
        if not items:
            return "No reminders."
        rep = {"none": "", "daily": ", every day", "weekdays": ", every weekday", "weekly": ", every week"}
        return "\n".join(f"Reminder {r['id']}: {spoken_when(r['due'], now)}{rep.get(r['repeat'], '')}: {r['text']}"
                         for r in items)

    def _match(self, which: str) -> list[dict]:
        which = (which or "").strip().lower()
        if which.isdigit():
            return [r for r in self._items if r["id"] == int(which)]
        words = [w for w in re.findall(r"\w+", which) if w not in ("the", "my", "reminder", "a", "to", "about")]
        return [r for r in self._items if words and all(w in r["text"].lower() for w in words)]

    def cancel(self, which: str) -> str:
        with self._lock:
            if (which or "").strip().lower() in ("all", "everything", "all reminders"):
                gone = list(self._items)
            else:
                gone = self._match(which)
                if len(gone) > 1:
                    raise ValueError("several reminders match: " + "; ".join(
                        f"{r['id']}: {r['text']}" for r in gone) + ". Say which (by number).")
            if not gone:
                return "No matching reminder."
            self._items = [r for r in self._items if r not in gone]
            self._save()
        self._wake.set()
        log.info("reminders cancelled: %s", [r["id"] for r in gone])
        return "Cancelled: " + "; ".join(r["text"] for r in gone) + "."

    def delete_id(self, rid: int):
        with self._lock:
            self._items = [r for r in self._items if r["id"] != rid]
            self._save()
        self._wake.set()

    def soonest(self) -> dict | None:
        items = self.items()
        return items[0] if items else None

    def _run(self):
        while not self._stop.is_set():
            now = time.time()
            due, wait = [], 60.0
            with self._lock:
                for r in self._items:
                    if r["due"] <= now:
                        due.append(dict(r))
                    else:
                        wait = min(wait, r["due"] - now)
                if due:
                    keep = []
                    for r in self._items:
                        if r["due"] > now:
                            keep.append(r)
                        elif r["repeat"] != "none":
                            r["due"] = next_time(r["due"], r["repeat"], now)
                            keep.append(r)
                    self._items = keep
                    self._save()
            for r in due:
                late = now - r["due"]
                text = (f"You missed a reminder from {spoken_when(r['due'], now)}: {r['text']}." if late > 120
                        else f"Reminder: {r['text']}.")
                log.info("reminder %d due%s", r["id"], f" ({late / 60:.0f} min late)" if late > 120 else "")
                try:
                    self.on_due(text)
                except Exception:  # noqa: BLE001
                    log.exception("reminder alert failed")
            self._wake.wait(timeout=max(0.2, min(wait, 60.0)))
            self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()


# ---- notes ----------------------------------------------------------------------------------------
def list_key(name: str) -> str:
    """'Shopping List' / 'shopping' -> 'shopping'."""
    name = " ".join(re.findall(r"[\w']+", (name or "").lower()))
    return re.sub(r"\s*\blist$", "", name).strip()[:40]


class Notes:
    MAX = 2000

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        items = [n for n in _read(path) if isinstance(n, dict) and "text" in n]
        cutoff = time.time() - BIN_DAYS * 86400
        self._items = [n for n in items if not n.get("deleted") or n["deleted"] > cutoff]   # empty the bin
        if len(self._items) != len(items):
            _write(self.path, self._items)

    def _save(self):
        _write(self.path, self._items)

    def _live(self, list_name: str = "") -> list[dict]:
        key = list_key(list_name)
        return [n for n in self._items if not n.get("deleted") and (not key or n.get("list") == key)]

    @staticmethod
    def _line(n: dict) -> str:
        where = f" [{n['list']} list]" if n.get("list") else ""
        return f"Note {n['id']} ({datetime.fromtimestamp(n['created']).strftime('%d %b')}){where}: {n['text']}"

    def add(self, text: str, list: str = "") -> str:      # noqa: A002 (tool argument name)
        text, key = _clean(text, 500), list_key(list)
        with self._lock:
            if len(self._live()) >= self.MAX:
                raise ValueError("too many notes")
            nid = max([n["id"] for n in self._items] + [0]) + 1
            self._items.append({"id": nid, "text": text, "list": key, "created": time.time(), "deleted": None})
            self._save()
        log.info("note %d added%s", nid, f" to {key}" if key else "")
        return f"Saved note {nid}" + (f" to the {key} list" if key else "") + "."

    def list(self, list: str = "", count: int = 10) -> str:     # noqa: A002
        with self._lock:
            items = sorted(self._live(list), key=lambda n: -n["created"])
            lists = sorted({n["list"] for n in self._live() if n.get("list")})
        if not items:
            return f"The {list_key(list)} list is empty." if list else "No notes."
        count = max(1, min(int(count or 10), 50))
        more = f"\n({len(items) - count} older notes not shown.)" if len(items) > count else ""
        tail = f"\nLists: {', '.join(lists)}." if lists and not list else ""
        return "\n".join(self._line(n) for n in items[:count]) + more + tail

    def search(self, query: str, count: int = 10) -> str:
        words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
        with self._lock:
            hits = [n for n in self._live() if words and any(w in (n["text"] + " " + n.get("list", "")).lower()
                                                           for w in words)]
        hits.sort(key=lambda n: (-sum(w in n["text"].lower() for w in words), -n["created"]))
        return "\n".join(self._line(n) for n in hits[:max(1, min(int(count or 10), 50))]) or "No matching notes."

    def delete(self, which: str) -> str:
        which = (which or "").strip().lower()
        with self._lock:
            live = self._live()
            if which.isdigit():
                hits = [n for n in live if n["id"] == int(which)]
            else:
                words = [w for w in re.findall(r"\w+", which) if w not in ("the", "my", "note", "a", "about")]
                hits = [n for n in live if words and all(w in n["text"].lower() for w in words)]
            if len(hits) > 1:
                raise ValueError("several notes match: " + "; ".join(self._line(n) for n in hits[:5])
                                 + ". Say which (by number).")
            if not hits:
                return "No matching note."
            hits[0]["deleted"] = time.time()
            self._save()
        log.info("note %d moved to the bin", hits[0]["id"])
        return f"Moved note {hits[0]['id']} to the bin ({hits[0]['text']}). It can be restored for {BIN_DAYS} days."

    def clear_list(self, list: str) -> str:              # noqa: A002
        key = list_key(list)
        if not key:
            raise ValueError("say which list to clear (all notes can only be removed by hand)")
        with self._lock:
            hits = self._live(key)
            for n in hits:
                n["deleted"] = time.time()
            if hits:
                self._save()
        log.info("list %r cleared (%d notes to the bin)", key, len(hits))
        return f"Cleared the {key} list ({len(hits)} items moved to the bin)." if hits else f"The {key} list is empty."

    # for the settings window
    def update(self, nid: int, text: str):
        text = _clean(text, 500)
        with self._lock:
            for n in self._items:
                if n["id"] == nid:
                    n["text"] = text
            self._save()

    def lists(self) -> list[str]:
        with self._lock:
            return sorted({n["list"] for n in self._live() if n.get("list")})

    def items(self, deleted: bool = False) -> list[dict]:
        with self._lock:
            return sorted((dict(n) for n in self._items if bool(n.get("deleted")) == deleted),
                          key=lambda n: -n["created"])

    def set_deleted(self, nid: int, on: bool):
        with self._lock:
            for n in self._items:
                if n["id"] == nid:
                    n["deleted"] = time.time() if on else None
            self._save()


# ---- the tools --------------------------------------------------------------------------------------
class Memory:
    def __init__(self, on_due: Callable[[str], None], data_dir: Path | str | None = None):
        d = Path(data_dir) if data_dir else DATA
        self.reminders = Reminders(d / "reminders.json", on_due)
        self.notes = Notes(d / "notes.json")

    def upcoming(self) -> str:
        r = self.reminders.soonest()
        if not r or r["due"] - time.time() > 24 * 3600:
            return ""
        return f"{r['text']}, {clock(r['due'])}"

    def registry(self) -> tuple[list[dict], dict[str, Callable]]:
        tools = [
            {"name": "add_reminder", "description": "Remind the user of something at a time; NOVA says it out "
                                                    "loud then (or when it next starts, if the PC was off).",
             "inputSchema": {"type": "object", "required": ["text", "when"], "properties": {
                 "text": {"type": "string", "maxLength": 200, "description": "What to remind about, e.g. 'call Mum'."},
                 "when": {"type": "string", "maxLength": 25,
                          "description": "Local date and time, YYYY-MM-DDTHH:MM (24-hour), e.g. 2026-11-14T17:00."},
                 "repeat": {"type": "string", "enum": list(REPEATS), "description": "Default none."}}}},
            {"name": "list_reminders", "description": "The user's upcoming reminders.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "cancel_reminder", "description": "Cancel a reminder by number or by words in it, or 'all'.",
             "inputSchema": {"type": "object", "required": ["which"], "properties": {
                 "which": {"type": "string", "maxLength": 100}}}},
            {"name": "add_note", "description": "Save a note, optionally in a named list (e.g. 'shopping', 'todo').",
             "inputSchema": {"type": "object", "required": ["text"], "properties": {
                 "text": {"type": "string", "maxLength": 500},
                 "list": {"type": "string", "maxLength": 40, "description": "List name, or empty for a plain note."}}}},
            {"name": "list_notes", "description": "The newest notes, or the items of one list.",
             "inputSchema": {"type": "object", "properties": {
                 "list": {"type": "string", "maxLength": 40},
                 "count": {"type": "integer", "minimum": 1, "maximum": 50}}}},
            {"name": "search_notes", "description": "Find notes containing these words (e.g. 'wifi password').",
             "inputSchema": {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "maxLength": 100}}}},
            {"name": "delete_note", "description": "Move one note to the bin (restorable for 30 days), by number "
                                                   "or by words in it.",
             "inputSchema": {"type": "object", "required": ["which"], "properties": {
                 "which": {"type": "string", "maxLength": 100}}}},
            {"name": "clear_list", "description": "Move every item of one named list to the bin (e.g. after "
                                                  "shopping).",
             "inputSchema": {"type": "object", "required": ["list"], "properties": {
                 "list": {"type": "string", "maxLength": 40}}}},
        ]
        funcs = {"add_reminder": self.reminders.add, "list_reminders": self.reminders.list,
                 "cancel_reminder": self.reminders.cancel, "add_note": self.notes.add,
                 "list_notes": self.notes.list, "search_notes": self.notes.search,
                 "delete_note": self.notes.delete, "clear_list": self.notes.clear_list}
        return tools, funcs

    def close(self):
        self.reminders.close()
