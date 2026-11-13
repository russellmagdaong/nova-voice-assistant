"""NOVA's PC controls for Claude (phase 3): a short, fixed list of harmless actions, nothing else.

- media keys: play/pause, next, previous, stop
- volume: get, set, up, down, mute, unmute
- timers: set, list, cancel (they ring through NOVA: chime, voice and the notch)
- open an app the user has allowed (tray > Apps NOVA can open), through Explorer, exactly like
  clicking it in Start

What it can't do, by design: delete, move or edit files; run commands; install or remove software;
change settings other than the volume; close apps; shut down, restart, sleep or sign out. Every
input is checked (type, range, length). Apps are launched by the ID NOVA stored when the user
allowed them, never by a name or path Claude supplies. Shells, system and admin tools and
uninstallers can't be allowed at all.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import logging
import re
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("nova.pc")
CREATE_NO_WINDOW = 0x08000000


# ---- media keys ---------------------------------------------------------------------------------
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def media_control(action: str) -> str:
    if action not in MEDIA_KEYS:
        raise ValueError(f"action must be one of {', '.join(MEDIA_KEYS)}")
    user32 = ctypes.WinDLL("user32")
    vk = MEDIA_KEYS[action]
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)                     # KEYEVENTF_KEYUP
    return f"Pressed the {action.replace('_', '/')} media key."


# ---- volume (Windows Core Audio, through COM with ctypes: no extra packages) ---------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        u = uuid.UUID(text)
        g = cls(u.fields[0], u.fields[1], u.fields[2])
        g.Data4[:] = list(u.bytes[8:])
        return g


_CLSID_ENUMERATOR = _GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_ENUMERATOR = _GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_ENDPOINT_VOLUME = _GUID.of("5CDF2C82-841E-4546-9722-0CF74078229A")
_CLSCTX_ALL = 0x17


def _method(obj: ctypes.c_void_p, index: int, *argtypes, restype=ctypes.HRESULT):
    """A COM method from obj's vtable (HRESULT failures raise OSError)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class _EndpointVolume:
    """The default speakers' volume control (IAudioEndpointVolume)."""

    def __enter__(self):
        ole32 = ctypes.WinDLL("ole32")
        hr = ole32.CoInitializeEx(None, 0x2)              # apartment-threaded; S_FALSE if already
        self._uninit = hr in (0, 1)
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = ctypes.HRESULT
        enum, dev, self.vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_CLSID_ENUMERATOR), None, _CLSCTX_ALL,
                               ctypes.byref(_IID_ENUMERATOR), ctypes.byref(enum))
        try:
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eMultimedia)
            _method(enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(enum, 0, 1, ctypes.byref(dev))
            # IMMDevice::Activate(IID_IAudioEndpointVolume)
            _method(dev, 3, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p))(dev, ctypes.byref(_IID_ENDPOINT_VOLUME), _CLSCTX_ALL,
                                                     None, ctypes.byref(self.vol))
        finally:
            for o in (dev, enum):
                if o:
                    _method(o, 2, restype=ctypes.c_ulong)(o)   # Release
        return self

    def __exit__(self, *exc):
        if self.vol:
            _method(self.vol, 2, restype=ctypes.c_ulong)(self.vol)
        if self._uninit:
            ctypes.WinDLL("ole32").CoUninitialize()

    def level(self) -> int:
        f = ctypes.c_float()
        _method(self.vol, 9, ctypes.POINTER(ctypes.c_float))(self.vol, ctypes.byref(f))   # GetMasterVolumeLevelScalar
        return round(f.value * 100)

    def set_level(self, pct: int):
        _method(self.vol, 7, ctypes.c_float, ctypes.c_void_p)(self.vol, max(0, min(100, pct)) / 100, None)

    def muted(self) -> bool:
        b = wintypes.BOOL()
        _method(self.vol, 15, ctypes.POINTER(wintypes.BOOL))(self.vol, ctypes.byref(b))    # GetMute
        return bool(b.value)

    def set_muted(self, on: bool):
        _method(self.vol, 14, wintypes.BOOL, ctypes.c_void_p)(self.vol, int(on), None)      # SetMute


def volume(action: str, level: int | None = None, step: int = 10) -> str:
    actions = ("get", "set", "up", "down", "mute", "unmute")
    if action not in actions:
        raise ValueError(f"action must be one of {', '.join(actions)}")
    step = max(1, min(int(step or 10), 50))
    with _EndpointVolume() as v:
        if action == "set":
            if level is None:
                raise ValueError("set needs a level (0-100)")
            v.set_level(int(level))
            if int(level) > 0 and v.muted():
                v.set_muted(False)
        elif action in ("up", "down"):
            v.set_level(v.level() + (step if action == "up" else -step))
            if action == "up" and v.muted():
                v.set_muted(False)
        elif action in ("mute", "unmute"):
            v.set_muted(action == "mute")
        return f"Volume is {v.level()} percent{', muted' if v.muted() else ''}."


# ---- timers ----------------------------------------------------------------------------------------
def spoken_duration(seconds: int, adjective: bool = False) -> str:
    """600 -> '10 minutes' (or '10 minute' as in 'a 10 minute timer')."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    parts = []
    for n, unit in ((h, "hour"), (m, "minute"), (s, "second")):
        if n:
            parts.append(f"{n} {unit}{'' if adjective or n == 1 else 's'}")
    return " ".join(parts) or "0 seconds"


class Timers:
    MAX_ACTIVE, MAX_SECONDS = 10, 24 * 3600

    def __init__(self, on_done: Callable[[str], None]):
        self.on_done = on_done
        self._lock = threading.Lock()
        self._items: dict[int, dict] = {}
        self._next = 1

    def set(self, seconds: int, label: str = "") -> str:
        seconds = int(seconds)
        if not 1 <= seconds <= self.MAX_SECONDS:
            raise ValueError("a timer can be 1 second to 24 hours")
        label = re.sub(r"[^\w '\-]", "", label or "").strip()[:40]
        with self._lock:
            if len(self._items) >= self.MAX_ACTIVE:
                raise ValueError(f"there are already {self.MAX_ACTIVE} timers running")
            tid, self._next = self._next, self._next + 1
            t = threading.Timer(seconds, self._fire, args=(tid,))
            t.daemon = True
            self._items[tid] = {"label": label, "seconds": seconds, "ends": time.monotonic() + seconds, "timer": t}
            t.start()
        name = f"{label} timer" if label else f"{spoken_duration(seconds, adjective=True)} timer"
        log.info("timer %d set: %s (%ds)", tid, name, seconds)
        return f"Started timer {tid}: {name}, done in {spoken_duration(seconds)}."

    def _fire(self, tid: int):
        with self._lock:
            item = self._items.pop(tid, None)
        if item:
            what = (f"Time's up for your {item['label']} timer." if item["label"]
                    else f"Your {spoken_duration(item['seconds'], adjective=True)} timer is done.")
            log.info("timer %d done", tid)
            self.on_done(what)

    def list(self) -> str:
        with self._lock:
            items = sorted(self._items.items(), key=lambda kv: kv[1]["ends"])
        if not items:
            return "No timers running."
        now = time.monotonic()
        return "\n".join(
            f"Timer {tid}: {it['label'] or spoken_duration(it['seconds'], adjective=True)} timer, "
            f"{spoken_duration(max(1, round(it['ends'] - now)))} left." for tid, it in items)

    def cancel(self, which: str = "") -> str:
        which = (which or "").strip().lower()
        with self._lock:
            if which in ("all", "every", "everything"):
                gone = list(self._items)
            elif which.isdigit():
                gone = [int(which)] if int(which) in self._items else []
            elif which:
                gone = [t for t, it in self._items.items() if which in it["label"].lower()
                        or which in spoken_duration(it["seconds"]).lower()]
            else:
                gone = list(self._items) if len(self._items) == 1 else []
                if len(self._items) > 1:
                    raise ValueError("several timers are running; say which one (number or label) or 'all'")
            for t in gone:
                self._items.pop(t)["timer"].cancel()
        if not gone:
            return "No matching timer."
        log.info("timers cancelled: %s", gone)
        return f"Cancelled {len(gone)} timer{'s' if len(gone) != 1 else ''}."

    def cancel_all(self):
        with self._lock:
            for it in self._items.values():
                it["timer"].cancel()
            self._items.clear()


# ---- apps -----------------------------------------------------------------------------------------
# Never allowed, even if ticked: shells and command runners, system/admin tools, and anything that
# installs, uninstalls, resets or repairs.
BLOCKED_APPS = re.compile(
    r"command prompt|powershell|terminal|\bcmd\b|bash|\bwsl\b|ubuntu|debian|kali|anaconda prompt|"
    r"native tools|developer command|registry|regedit|system configuration|^services$|task scheduler|"
    r"device manager|disk management|computer management|disk cleanup|defragment|recovery|reset|"
    r"memory diagnostic|security policy|group policy|component services|odbc|iscsi|print management|"
    r"windows tools|control panel|event viewer|performance monitor|resource monitor|system information|"
    r"uninstall|installer|\bsetup\b|repair|bitlocker|credential manager|firewall|hyper-v|sandbox", re.I)


def is_blocked(name: str) -> bool:
    return bool(BLOCKED_APPS.search(name or ""))


def discover_apps() -> list[dict]:
    """Every app in the Start menu: [{"name", "id"}] (Get-StartApps; ~1-2 s). Read-only."""
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    data = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    if isinstance(data, dict):
        data = [data]
    seen, apps = set(), []
    for d in data:
        name, app_id = (d.get("Name") or "").strip(), (d.get("AppID") or "").strip()
        if name and app_id and name.lower() not in seen:
            seen.add(name.lower())
            apps.append({"name": name, "id": app_id})
    return sorted(apps, key=lambda a: a["name"].lower())


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"^(the|my) |\b(app|application|program)$", "", " ".join(s.split())).strip()
    return s


def find_app(name: str, apps: list[dict]) -> dict | None:
    """The allowed app the user most likely meant ('zen' -> 'Zen Browser', aliases count)."""
    want = _norm(name)
    if not want:
        return None
    names = []
    for a in apps:
        for n in [a["name"], *a.get("aliases", [])]:
            names.append((_norm(n), a))
    for n, a in names:
        if n == want:
            return a
    for n, a in names:                                   # "zen" -> "zen browser", "spotify music" -> "spotify"
        if n and (n.startswith(want + " ") or want.startswith(n + " ") or set(want.split()) <= set(n.split())):
            return a
    close = difflib.get_close_matches(want, [n for n, _ in names], n=1, cutoff=0.8)
    return next(a for n, a in names if n == close[0]) if close else None


class Apps:
    def __init__(self, allowed: Callable[[], list[dict]]):
        self.allowed = allowed                           # -> [{"name", "id", "aliases"}] from state.json

    def _list(self) -> list[dict]:
        return [a for a in self.allowed() if a.get("id") and a.get("name") and not is_blocked(a["name"])]

    def open(self, name: str) -> str:
        apps = self._list()
        if not apps:
            return ("No apps are allowed yet. The user can allow apps from NOVA's tray menu: "
                    "Apps NOVA can open.")
        app = find_app(name, apps)
        if app is None:
            return (f"{name!r} isn't on the allowed list ({', '.join(a['name'] for a in apps)}). The user can "
                    "allow more apps from NOVA's tray menu: Apps NOVA can open.")
        app_id = app["id"]
        if any(c in app_id for c in '"\r\n'):           # (IDs come from Get-StartApps; just in case)
            raise ValueError("invalid app id")
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app_id], creationflags=CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("opened %s (%s)", app["name"], app_id)
        return f"Opened {app['name']}."

    def list(self) -> str:
        apps = self._list()
        return ("Apps NOVA may open: " + ", ".join(a["name"] for a in apps) + ".") if apps else \
            "No apps are allowed yet (tray menu: Apps NOVA can open)."


# ---- the tools, for the MCP host ------------------------------------------------------------------
class PCTools:
    def __init__(self, on_timer_done: Callable[[str], None], allowed_apps: Callable[[], list[dict]]):
        self.timers = Timers(on_timer_done)
        self.apps = Apps(allowed_apps)

    def registry(self) -> tuple[list[dict], dict[str, Callable]]:
        tools = [
            {"name": "media_control", "description": "Press a media key (controls whatever is playing: "
                                                     "Spotify, YouTube in the browser, ...).",
             "inputSchema": {"type": "object", "required": ["action"], "properties": {
                 "action": {"type": "string", "enum": list(MEDIA_KEYS)}}}},
            {"name": "volume", "description": "The PC's speaker volume: get it, set it (0-100), turn it up or "
                                              "down, mute or unmute.",
             "inputSchema": {"type": "object", "required": ["action"], "properties": {
                 "action": {"type": "string", "enum": ["get", "set", "up", "down", "mute", "unmute"]},
                 "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "For set."},
                 "step": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "For up/down, in percent (default 10)."}}}},
            {"name": "set_timer", "description": "Start a timer; NOVA announces it out loud when it's done.",
             "inputSchema": {"type": "object", "required": ["seconds"], "properties": {
                 "seconds": {"type": "integer", "minimum": 1, "maximum": Timers.MAX_SECONDS,
                             "description": "Total length in seconds (10 minutes = 600)."},
                 "label": {"type": "string", "description": "Optional name, e.g. 'pasta'."}}}},
            {"name": "list_timers", "description": "The running timers and the time left on each.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "cancel_timer", "description": "Cancel a timer by number or label, or 'all'. Empty = the "
                                                    "only running timer.",
             "inputSchema": {"type": "object", "properties": {"which": {"type": "string"}}}},
            {"name": "open_app", "description": "Open an app the user has allowed, by name (e.g. 'Spotify').",
             "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
            {"name": "list_apps", "description": "The apps the user has allowed NOVA to open.",
             "inputSchema": {"type": "object", "properties": {}}},
        ]
        funcs = {"media_control": media_control, "volume": volume, "set_timer": self.timers.set,
                 "list_timers": self.timers.list, "cancel_timer": self.timers.cancel,
                 "open_app": self.apps.open, "list_apps": self.apps.list}
        return tools, funcs
