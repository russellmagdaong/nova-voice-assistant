"""Mute/unmute test in the simulated room (see conversation_test.py).

Checks that muting frees NOVA's video memory and RAM, that NOVA answers straight after
unmuting (with the small speech model while large-v3-turbo reloads), and that turbo comes back.

    .venv\\Scripts\\python.exe tools\\mute_test.py
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import Qt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import conversation_test as ct                                 # noqa: E402
from nova import config, ui                                    # noqa: E402
import nova.assistant as am                                    # noqa: E402
from nova.audio import MIC_SR                                  # noqa: E402

QUESTIONS = ["Hey Nova, how many legs does a spider have?",
             "Hey Nova, what's two plus two?",
             "Hey Nova, what colour is the sky?"]


def gpu_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.split()[0])


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def ram_mb() -> tuple[int, int]:
    """(RAM in use, private memory incl. paged-out) of this process, MB."""
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize >> 20, pmc.PagefileUsage >> 20


def main():
    logging.basicConfig(level=logging.INFO, format="      %(name)s: %(message)s")
    for n in ("faster_whisper", "httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(n).setLevel(logging.WARNING)
    cap = ct.Capture()
    logging.getLogger("nova").addHandler(cap)
    clips = {q: asyncio.run(ct.tts(q)) for q in QUESTIONS}

    gpu0 = gpu_mb()
    room = ct.Room(0.6)
    cfg = config.load()
    bridge = ui.Bridge()
    events = []
    d = Qt.DirectConnection
    bridge.state.connect(lambda s: events.append(("state", s)), d)
    bridge.heard.connect(lambda s: events.append(("heard", s)), d)
    bridge.answer.connect(lambda s: events.append(("answer", s)), d)
    a = am.Assistant(cfg, bridge)
    a.player = ct.RoomPlayer(room)
    am.Mic = lambda name, aec=None: ct.FakeMic(room, aec)
    threading.Thread(target=a.run, daemon=True).start()

    def wait_state(s, timeout=60.0, mark=0) -> bool:
        end = time.perf_counter() + timeout
        while not any(e == ("state", s) for e in events[mark:]):
            if time.perf_counter() > end:
                return False
            time.sleep(0.05)
        return True

    def ask(q) -> tuple[str, str, list[str]]:
        mark, lines = len(events), len(cap.lines)
        print(f"   YOU : {q}", flush=True)
        room.add_user(clips[q])
        time.sleep(len(clips[q]) / MIC_SR)
        ok = wait_state(ui.SPEAKING, 45, mark) and wait_state(ui.IDLE, 45, mark)
        heard = next((e[1] for e in events[mark:] if e[0] == "heard"), "")
        said = [e[1] for e in events[mark:] if e[0] == "answer"]
        print(f"   NOVA heard: {heard!r}\n   NOVA said : {said[-1] if said else ''!r}"
              + ("" if ok else "   !! timed out"), flush=True)
        return heard, (said[-1] if said else ""), cap.lines[lines:]

    def snap(label):
        ws, priv = ram_mb()
        g = gpu_mb()
        print(f"-- {label:34s} RAM {ws:5d} MB (private {priv:5d} MB)   video memory +{g - gpu0:5d} MB", flush=True)
        return ws, g - gpu0

    if not wait_state(ui.IDLE, 120):
        sys.exit("NOVA failed to start")
    snap("ready")
    while not a._trimmed:
        time.sleep(0.2)
    time.sleep(0.5)
    r_idle, v_run = snap("after the start-up trim")

    print("\n=== before muting", flush=True)
    h1, _, _ = ask(QUESTIONS[0])
    r_used, _ = snap("after a conversation")

    print("\n=== mute", flush=True)
    a.set_muted(True)
    time.sleep(3)
    r_muted, v_muted = snap("muted 3 s")
    time.sleep(5)
    snap("muted 8 s")

    print("\n=== unmute and ask straight away", flush=True)
    t_unmute = time.perf_counter()
    back = {}

    def watch():                                 # when turbo is loaded again
        while not a.stt.ready and time.perf_counter() - t_unmute < 60:
            time.sleep(0.05)
        back["t"] = time.perf_counter() - t_unmute
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    a.set_muted(False)
    time.sleep(1.5)
    h2, s2, lines2 = ask(QUESTIONS[1])
    fallback = any("still loading" in l for l in lines2)
    print(f"   used the small model while turbo reloaded: {fallback}")
    watcher.join()
    turbo_back = back["t"]
    print(f"   turbo ready {turbo_back:.1f}s after unmuting", flush=True)
    snap("turbo back")

    print("\n=== after turbo is back", flush=True)
    h3, s3, lines3 = ask(QUESTIONS[2])
    used_turbo = not any("still loading" in l for l in lines3)
    r_end, v_end = snap("end")

    checks = [
        ("muting freed video memory", v_muted < 250),
        ("muting freed RAM", r_muted < 300),
        ("answered right after unmuting", "two" in h2.lower() or "2" in h2),
        ("answer mentions four", "four" in s2.lower() or "4" in s2),
        ("turbo back within 30 s", a.stt.ready and turbo_back < 30),
        ("turbo used after reload", used_turbo and "sky" in h3.lower()),
    ]
    print()
    for name, ok in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n   running: video memory +{v_run} MB, RAM {r_idle} MB idle / {r_used} MB after a conversation;"
          f" muted: video memory +{v_muted} MB, RAM {r_muted} MB", flush=True)
    a.quit()


if __name__ == "__main__":
    main()
