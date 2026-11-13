"""End-to-end conversation test with a simulated room (no real mic or speakers).

- "You" are a generated voice pushed into a fake mic in real time.
- NOVA's speech is NOT played; it is turned into a room echo (delay + reflections) and mixed
  into the fake mic, so the echo canceller and talk-over detection are exercised for real.
- Real Vosk, Whisper, Claude and edge-tts.

    .venv\\Scripts\\python.exe tools\\conversation_test.py [--headphones] [--only NAME]
"""
from __future__ import annotations

import argparse
import os
import asyncio
import collections
import logging
import sys
import threading
import time
from pathlib import Path

import edge_tts
import numpy as np
from PySide6.QtCore import Qt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova import config, ui                                   # noqa: E402
import nova.assistant as am                                    # noqa: E402
from nova.audio import MIC_BLOCK, MIC_SR, OUT_SR, Player, decode, rms  # noqa: E402

CLIPS = ROOT / "tools" / "clips"
USER_VOICE = "en-GB-RyanNeural"

SCENARIOS = {
    "conversation": [
        ("say", "Hey Nova, what's the capital of France?"),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2), ("say", "And what about Germany?"),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2), ("say", "Which of those two cities has more people?"),
        ("after", ui.IDLE, 0.0),                     # ends by itself after 10 s of silence
    ],
    "talk over it": [
        ("say", "Hey Nova, tell me a short story about a dragon who loves the sea."),
        ("after", ui.SPEAKING, 3.0), ("say", "Actually, make it about a cat instead."),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2), ("say", "That's all, thanks."),
        ("after", ui.IDLE, 0.0),
    ],
    "one-word interruption": [
        ("say", "Hey Nova, tell me about the history of the Roman Empire."),
        ("after", ui.SPEAKING, 3.0), ("say", "Wait."), ("silence", 0.6), ("say", "How long did it last?"),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2), ("say", "Goodbye."),
        ("after", ui.IDLE, 0.0),
    ],
    "lookups": [                                     # "Let me check." plays while it looks things up
        ("say", "Hey Nova, what's the weather like?"),           # NOVA's weather tool, your city
        ("after", ui.SEARCHING, 0), ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "What's in the news today?"),                    # NOVA's news tool
        ("after", ui.SEARCHING, 0), ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "Who won the last Formula One race?"),           # web search
        ("after", ui.SEARCHING, 0), ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "That's all, thanks."),
        ("after", ui.IDLE, 0.0),
    ],
    "pc control": [                                  # the timer rings after the conversation
        ("say", "Hey Nova, set a timer for forty seconds."),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "What's the volume at?"),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "Delete my downloads folder."),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2),
        ("say", "That's all, thanks."),
        ("after", ui.IDLE, 0.0),
        ("after", ui.ALERT, 0.0), ("after", ui.IDLE, 0.0),
    ],
    "pause then talk": [
        ("say", "Hey Nova."), ("silence", 1.8), ("say", "How many legs does a spider have?"),
        ("after", ui.SPEAKING, 0), ("after", ui.LISTENING, 1.2), ("say", "Goodbye."),
        ("after", ui.IDLE, 0.0),
    ],
}


async def tts(text: str) -> np.ndarray:
    path = CLIPS / f"conv_{USER_VOICE}_{abs(hash(text)) % 10**10}.mp3"
    if not path.exists():
        CLIPS.mkdir(parents=True, exist_ok=True)
        await edge_tts.Communicate(text, USER_VOICE).save(str(path))
    return decode(path.read_bytes(), MIC_SR)


class Room:
    """Mixes 'your' voice and NOVA's echo into the fake mic stream. The echo lands on the mic's
    own timeline at a fixed delay (output buffering + the room), like real speakers."""

    LATENCY = 1600            # 100 ms of output/capture buffering before the room delay

    def __init__(self, echo_gain: float):
        rng = np.random.default_rng(3)
        self.ir = np.zeros(int(0.25 * MIC_SR), np.float32)
        if echo_gain:
            self.ir[int(0.07 * MIC_SR)] = echo_gain
            for k in range(12):
                self.ir[int((0.08 + 0.012 * k) * MIC_SR)] += echo_gain * 0.25 * (0.75 ** k) * rng.choice([-1, 1])
        self.size = 60 * MIC_SR
        self.echo = np.zeros(self.size, np.float32)      # circular, indexed by mic sample
        self.mic_pos = 0
        self.spk_pos = None                              # where the next speaker sample lands
        self.user = collections.deque()
        self.lock = threading.Lock()
        self.rng = rng

    def add_user(self, audio: np.ndarray):
        with self.lock:
            self.user.extend(audio.tolist())

    def add_speaker(self, chunk24k: np.ndarray):
        n = int(len(chunk24k) * MIC_SR / OUT_SR)
        x = np.interp(np.linspace(0, len(chunk24k) - 1, n), np.arange(len(chunk24k)), chunk24k)
        y = np.convolve(x, self.ir).astype(np.float32)
        with self.lock:
            if self.spk_pos is None or self.spk_pos < self.mic_pos + self.LATENCY:
                self.spk_pos = self.mic_pos + self.LATENCY
            idx = (self.spk_pos + np.arange(len(y))) % self.size
            np.add.at(self.echo, idx, y)
            self.spk_pos += n

    def take(self, n: int) -> np.ndarray:
        with self.lock:
            u = [self.user.popleft() if self.user else 0.0 for _ in range(n)]
            idx = (self.mic_pos + np.arange(n)) % self.size
            e = self.echo[idx].copy()
            self.echo[idx] = 0
            self.mic_pos += n
        return np.array(u, np.float32) + e + 0.002 * self.rng.standard_normal(n).astype(np.float32)


class FakeMic:
    name = "simulated room"

    def __init__(self, room: Room, aec):
        self.room, self.aec = room, aec
        self.paused = False
        self.level = 0.0
        self.q = collections.deque()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        t_next = time.perf_counter()
        while not self._stop.is_set():
            t_next += MIC_BLOCK / MIC_SR
            time.sleep(max(0.0, t_next - time.perf_counter()))
            block = (np.clip(self.room.take(MIC_BLOCK), -1, 1) * 32767).astype(np.int16)
            if self.aec is not None:
                block = self.aec.process_mic(block)
            self.level = min(1.0, rms(block) / 4000)
            if not self.paused:
                self.q.append(block)

    def start(self): pass
    def stop(self): self._stop.set()
    def flush(self): self.q.clear()

    def read(self, timeout=None):
        end = time.perf_counter() + (timeout or 0)
        while not self.q:
            if time.perf_counter() >= end:
                return None
            time.sleep(0.01)
        return self.q.popleft()


class RoomPlayer(Player):
    """Instead of playing, sends NOVA's voice into the simulated room in real time."""

    def __init__(self, room: Room):
        super().__init__()
        self.room = room

    def open(self): pass
    def close(self): self.level = 0.0

    def play(self, audio):
        chunk = OUT_SR // 25
        t_next = time.perf_counter()
        for i in range(0, len(audio), chunk):
            if self.stopped:
                self.level = 0.0
                return False
            part = audio[i:i + chunk].astype(np.float32) * self.gain
            self.level = min(1.0, float(np.sqrt(np.mean(part ** 2))) * 6 / max(self.gain, 0.05))
            if self.aec is not None:
                self.aec.feed_speaker(part)
            self.room.add_speaker(part)
            self.played_s += len(part) / OUT_SR
            t_next += len(part) / OUT_SR
            time.sleep(max(0.0, t_next - time.perf_counter()))
        self.level = 0.0
        return True


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, r):
        self.lines.append(r.getMessage())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headphones", action="store_true", help="no echo from NOVA's voice")
    ap.add_argument("--only")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="      %(name)s: %(message)s")
    if os.environ.get("NOVA_DEBUG"):
        logging.getLogger("nova.assistant").setLevel(logging.DEBUG)
    for n in ("faster_whisper", "httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(n).setLevel(logging.WARNING)
    cap = Capture()
    logging.getLogger("nova").addHandler(cap)

    scenarios = {k: v for k, v in SCENARIOS.items() if not args.only or k == args.only}
    clips = {}
    for steps in scenarios.values():
        for st in steps:
            if st[0] == "say" and st[1] not in clips:
                clips[st[1]] = asyncio.run(tts(st[1]))

    room = Room(0.0 if args.headphones else 0.6)
    cfg = config.load()
    if os.environ.get("NOVA_NO_CAPTIONS"):
        cfg["ui"]["live_captions"] = False
    bridge = ui.Bridge()
    events = []
    d = Qt.DirectConnection
    bridge.state.connect(lambda s: events.append(("state", s, time.perf_counter())), d)
    bridge.heard.connect(lambda s: events.append(("heard", s, time.perf_counter())), d)
    bridge.answer.connect(lambda s: events.append(("answer", s, time.perf_counter())), d)
    bridge.partial.connect(lambda s: events.append(("partial", s, time.perf_counter())), d)

    a = am.Assistant(cfg, bridge)
    a.player = RoomPlayer(room)
    am.Mic = lambda name, aec=None: FakeMic(room, aec)
    threading.Thread(target=a.run, daemon=True).start()
    while not any(e[:2] == ("state", ui.IDLE) for e in events):
        if any(e[:2] == ("state", ui.ERROR) for e in events):
            sys.exit("NOVA failed to start")
        time.sleep(0.2)
    print(f"\nNOVA ready ({'headphones' if args.headphones else 'speakers, simulated echo'}).\n", flush=True)

    for name, steps in scenarios.items():
        print(f"=== {name}", flush=True)
        events.clear()
        cap.lines.clear()
        t0 = time.perf_counter()
        ok = True
        for st in steps:
            if st[0] == "say":
                print(f"   YOU : {st[1]}", flush=True)
                room.add_user(clips[st[1]])
                time.sleep(len(clips[st[1]]) / MIC_SR)
            elif st[0] == "silence":
                time.sleep(st[1])
            elif st[0] == "after":
                mark = len(events)
                deadline = time.perf_counter() + 45
                while not any(e[:2] == ("state", st[1]) for e in events[mark:]):
                    if time.perf_counter() > deadline:
                        print(f"   !! timed out waiting for {st[1]}")
                        ok = False
                        break
                    time.sleep(0.05)
                time.sleep(st[2])
            if not ok:
                break
        # transcript of the scenario
        said_at = {}
        last_caption, first_caption, captions = "", None, []
        for e in events:
            if e[0] == "partial" and e[1]:
                last_caption = e[1]
                first_caption = first_caption or e[2]
                captions.append(e[1])
            if e[0] == "heard":
                if last_caption:
                    print(f"   live caption: {' | '.join(captions[:3])} ... {last_caption}   "
                          f"(final text {e[2] - first_caption:.1f}s after the first live word)")
                last_caption, first_caption, captions = "", None, []
                print(f"   NOVA heard : {e[1]}")
        answers = [e[1] for e in events if e[0] == "answer"]
        finals, last = [], None
        for e in events:
            if e[0] == "answer":
                last = e[1]
            elif e[0] == "heard" and last:
                finals.append(last)
                last = None
        if last:
            finals.append(last)
        for f in finals:
            print(f"   NOVA said  : {f}")
        for l in cap.lines:
            if any(k in l for k in ("LATENCY", "interrupted", "user started", "ended", "ignored", "false alarm")):
                print(f"   log  : {l}")
        print(f"   took {time.perf_counter() - t0:.0f}s\n", flush=True)
        time.sleep(1.0)
    a.quit()


if __name__ == "__main__":
    main()
