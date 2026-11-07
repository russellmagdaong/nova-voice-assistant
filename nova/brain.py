"""Claude connection: one persistent Claude Code process in stream-json mode.

Uses the signed-in Claude subscription via the Claude Code CLI (no API key).
The process gets NO tools: a misheard sentence must never be able to run anything.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("nova.brain")

SYSTEM_PROMPT = """You are Nova, a voice assistant on the user's Windows PC. Your replies are spoken aloud by a text-to-speech voice.
- This is a spoken back-and-forth conversation. The user may interrupt you or follow up without repeating context.
- Answer in one to three short spoken sentences unless the user asks for more detail.
- Plain speech only: no markdown, no lists, no headings, no emoji, no URLs.
- Write numbers, dates, times and units the way you would say them out loud.
- The user's words come from speech recognition and may contain small mistakes; work out what they most likely meant.
- Each message starts with the current local date and time in square brackets; use it when relevant, never read it out.
- If you can't do something (for example you have no internet access or can't control the computer yet), say so briefly."""

CREATE_NO_WINDOW = 0x08000000


def find_claude(configured: str | None) -> str:
    candidates = [configured, shutil.which("claude"),
                  str(Path.home() / ".local" / "bin" / "claude.exe")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Claude Code CLI not found. Set [claude] path in config.toml.")


class ClaudeError(RuntimeError):
    pass


class Brain:
    """Persistent Claude Code session. Thread-safe for one question at a time."""

    def __init__(self, exe: str | None = None, model: str = "haiku",
                 idle_reset_s: float = 300, location: str = ""):
        self.exe = find_claude(exe)
        self.model = model
        self.idle_reset_s = idle_reset_s
        self.location = location
        self._proc: subprocess.Popen | None = None
        self._events: queue.Queue = queue.Queue()
        self._lock = threading.Lock()          # one turn at a time; also guards restarts
        self._last_used = time.monotonic()
        self._turns = 0
        self._stop = threading.Event()
        threading.Thread(target=self._idle_watchdog, daemon=True, name="brain-idle").start()

    # ---- process management -------------------------------------------------
    def _system_prompt(self) -> str:
        p = SYSTEM_PROMPT
        if self.location:
            p += f"\n- The user is in {self.location}."
        return p

    def start(self):
        with self._lock:
            self._start_locked()

    def _start_locked(self):
        self._kill_locked()
        args = [self.exe, "-p", "--model", self.model,
                "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages",
                "--system-prompt", self._system_prompt()]
        log.info("starting claude (%s)", self.model)
        self._proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            cwd=str(Path.home()))
        self._events = queue.Queue()
        threading.Thread(target=self._reader, args=(self._proc, self._events), daemon=True,
                         name="brain-stdout").start()
        threading.Thread(target=self._stderr_reader, args=(self._proc,), daemon=True,
                         name="brain-stderr").start()
        self._turns = 0
        self._last_used = time.monotonic()

    def _kill_locked(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    @staticmethod
    def _reader(proc: subprocess.Popen, q: queue.Queue):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                q.put(json.loads(line))
            except json.JSONDecodeError:
                log.debug("non-json stdout: %s", line[:200])
        q.put({"type": "_exit", "code": proc.wait()})

    @staticmethod
    def _stderr_reader(proc: subprocess.Popen):
        for line in proc.stderr:
            if line.strip():
                log.warning("claude stderr: %s", line.rstrip()[:300])

    def reset(self):
        """Forget the conversation (restarts the process in the background)."""
        threading.Thread(target=self.start, daemon=True, name="brain-reset").start()

    def close(self):
        self._stop.set()
        with self._lock:
            self._kill_locked()

    def _idle_watchdog(self):
        while not self._stop.wait(10):
            if self._turns and time.monotonic() - self._last_used > self.idle_reset_s:
                if self._lock.acquire(blocking=False):
                    try:
                        log.info("idle for %ds: starting a fresh conversation", self.idle_reset_s)
                        self._start_locked()
                    finally:
                        self._lock.release()

    # ---- asking ---------------------------------------------------------------
    def ask(self, text: str, cancelled: Callable[[], bool] = lambda: False) -> Iterator[str]:
        """Send one question; yield text deltas as they stream in.

        If `cancelled()` becomes true, stops yielding but still drains the turn so
        the process stays in sync for the next question.
        """
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start_locked()
            now = datetime.now().strftime("%A %d %B %Y, %I:%M %p")
            msg = {"type": "user", "message": {"role": "user", "content": f"[{now}] {text}"}}
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except OSError as e:
                self._start_locked()
                raise ClaudeError(f"Claude process was not running ({e}); restarted it") from e
            self._turns += 1
            got_text = False
            while True:
                try:
                    ev = self._events.get(timeout=90)
                except queue.Empty:
                    self._start_locked()
                    raise ClaudeError("Claude took too long to answer")
                t = ev.get("type")
                if t == "stream_event":
                    e = ev.get("event", {})
                    if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                        got_text = True
                        if not cancelled():
                            yield e["delta"]["text"]
                elif t == "result":
                    self._last_used = time.monotonic()
                    if ev.get("is_error"):
                        raise ClaudeError(str(ev.get("result") or "Claude returned an error"))
                    if not got_text and ev.get("result") and not cancelled():
                        yield ev["result"]
                    return
                elif t == "_exit":
                    self._proc = None
                    raise ClaudeError(f"Claude process exited (code {ev.get('code')})")


if __name__ == "__main__":
    # Quick manual test: python -m nova.brain
    logging.basicConfig(level=logging.INFO)
    b = Brain()
    t0 = time.perf_counter()
    b.start()
    for q in ["What's the capital of France?", "And what about Germany?", "What time is it?"]:
        t = time.perf_counter()
        first = None
        out = []
        for d in b.ask(q):
            if first is None:
                first = time.perf_counter() - t
            out.append(d)
        print(f"\nQ: {q}\nA: {''.join(out)}\n   first text {first:.2f}s, done {time.perf_counter() - t:.2f}s")
    b.close()
