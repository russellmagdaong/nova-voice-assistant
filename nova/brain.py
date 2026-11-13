"""Claude connection: one persistent Claude Code process in stream-json mode.

Uses the signed-in Claude subscription via the Claude Code CLI (no API key).
Its only tools: WebSearch, and NOVA's own fixed tools served by tool_host.py (weather and news
lookups, and harmless PC controls: media keys, volume, timers, opening allowed apps). Never Bash,
PowerShell or file tools, so a misheard sentence can't run anything or touch files.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import random
import shutil
import subprocess
import sys
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
- Each message starts with the current local date and time in square brackets; use it when relevant, never read it out."""

WEB_PROMPT = """
- For the weather, use the get_weather tool (live conditions and forecast), never web search. If no place is mentioned, call it without a place: it uses the user's city.
- For news, use the get_news tool (fresh headlines, each with its age), not web search. Summarise the two or three biggest stories in a few sentences. If a story is from a previous day, say so. Use web search only for more detail on a story or news the tool doesn't have.
- For other current information (sports results, prices, opening hours, recent events) or facts you aren't sure of, search the web. Don't search for things you know well.
- Speed matters: the user is waiting in silence. Do ONE quick search (standard mode, never extended) and answer from it; only search again if the first results are useless.
- Don't announce that you're searching: a short "let me check" is already played for you. Go straight to the answer.
- Answer from the results in your own words with just the key facts. Don't mention sources, websites or links, don't add caveats about where the information came from, and don't tell the user to check elsewhere, unless they ask."""

OFFLINE_PROMPT = """
- You have no internet access (no web search, weather or news); if asked, say so briefly."""

PC_PROMPT = """
- On the user's PC you can do these things with NOVA's tools, and nothing else: media playback (play/pause, next, previous), the speaker volume, timers, and opening the apps the user has allowed. When asked, use the tool, then confirm in a few words, for example "Done, volume at thirty percent." or "Ten minute timer started." For timers, give the length in seconds.
- You can't delete, move or edit files, run commands, install or remove software, change any setting other than the volume, close apps, or shut down, restart or sleep the PC. If asked, say briefly that you can't do that.
- If an app isn't on the allowed list, say so, and that the user can allow it from NOVA's tray menu under "Apps NOVA can open"."""

NO_PC_PROMPT = """
- You can't control the computer, open apps or set timers; if asked, say so briefly."""

# Said while a lookup runs (if Claude hasn't said anything yet), so the wait isn't silent.
SEARCH_FILLERS = ["Let me check.", "Let me look that up.", "One sec, let me check.", "Checking now."]
LOOKUP_TOOLS = {"WebSearch", "get_weather", "get_news"}       # (PC actions are instant: no filler)

CREATE_NO_WINDOW = 0x08000000


def windows_region() -> str:
    """The country set in Windows (Settings > Time & language > Region), e.g. "New Zealand"."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International\Geo") as k:
            code = winreg.QueryValueEx(k, "Name")[0]
        buf = ctypes.create_unicode_buffer(128)
        if ctypes.windll.kernel32.GetGeoInfoEx(code, 8, buf, 128):      # GEO_FRIENDLYNAME
            return buf.value
        return code
    except (OSError, AttributeError, ImportError):
        return ""


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
                 idle_reset_s: float = 300, location: str = "", web_search: bool = True, tools=None):
        """`tools`: NOVA's ToolHost (weather, news, PC controls), or None."""
        self.exe = find_claude(exe)
        self.model = model
        self.idle_reset_s = idle_reset_s
        self.location = location
        self.web_search = web_search
        self.tools = tools
        self._proc: subprocess.Popen | None = None
        self._events: queue.Queue = queue.Queue()
        self._lock = threading.Lock()          # one turn at a time; also guards restarts
        self._last_used = time.monotonic()
        self._turns = 0
        self._stop = threading.Event()
        threading.Thread(target=self._idle_watchdog, daemon=True, name="brain-idle").start()

    # ---- process management -------------------------------------------------
    def _system_prompt(self) -> str:
        names = self.tools.names if self.tools else []
        p = SYSTEM_PROMPT + (WEB_PROMPT if self.web_search else OFFLINE_PROMPT)
        p += PC_PROMPT if "set_timer" in names else NO_PC_PROMPT
        if self.location:
            p += f"\n- The user is in {self.location}. Use that for local questions such as the weather."
        elif region := windows_region():
            p += (f"\n- The user's city isn't set; their Windows region is {region}. For local questions "
                  "such as the weather, ask which city if it matters.")
        return p

    def start(self):
        with self._lock:
            self._start_locked()

    def _start_locked(self):
        self._kill_locked()
        # The only built-in tool is WebSearch (--tools); the only MCP server is NOVA's own tool host
        # (--strict-mcp-config). Those are pre-approved (--allowedTools), and anything else that
        # would ask for permission is refused (--permission-prompts none).
        allowed = ["WebSearch"] if self.web_search else []
        tools = ["--tools", "WebSearch" if self.web_search else ""]
        if self.tools:
            allowed += [f"mcp__nova__{n}" for n in self.tools.names]
            tools += ["--mcp-config", json.dumps({"mcpServers": {"nova": self.tools.mcp_config()}})]
        if allowed:
            tools += ["--allowedTools", ",".join(allowed)]
        args = [self.exe, "-p", "--model", self.model, *tools, "--permission-prompts", "none",
                "--strict-mcp-config", "--no-session-persistence",
                "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages",
                "--system-prompt", self._system_prompt()]
        log.info("starting claude (%s; tools: %s)", self.model, ", ".join(allowed) or "none")
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
    def ask(self, text: str, cancelled: Callable[[], bool] = lambda: False,
            on_search: Callable[[str], None] = lambda tool: None) -> Iterator[str]:
        """Send one question; yield text deltas as they stream in.

        When Claude starts a lookup (web search, weather, news), a short filler ("Let me check.")
        is yielded first if it hasn't said anything yet, then `on_search(tool)` is called with the
        tool's name, e.g. "get_weather" (the answer's text follows later).
        If `cancelled()` becomes true, stops yielding but still drains the turn so the process
        stays in sync for the next question.
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
            got_text = said = False                     # Claude's text / anything yielded
            searched_at = 0.0
            while True:
                try:
                    ev = self._events.get(timeout=90)
                except queue.Empty:
                    self._start_locked()
                    raise ClaudeError("Claude took too long to answer")
                t = ev.get("type")
                if t == "stream_event":
                    e = ev.get("event", {})
                    et = e.get("type")
                    if et == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                        got_text = True
                        if searched_at:
                            log.info("answer started %.1fs after the lookup began", time.monotonic() - searched_at)
                            searched_at = 0.0
                        if not cancelled():
                            said = True
                            yield e["delta"]["text"]
                    elif et == "content_block_start":
                        block = e.get("content_block", {})
                        kind = block.get("type")
                        if kind in ("tool_use", "server_tool_use"):
                            tool = block.get("name", "").removeprefix("mcp__nova__")
                            if tool in LOOKUP_TOOLS:
                                searched_at = time.monotonic()
                                if not said and not cancelled():
                                    said = True
                                    yield random.choice(SEARCH_FILLERS) + " "
                            on_search(tool)
                        elif kind == "text" and said and not cancelled():
                            yield " "                   # text after a search: keep sentences apart
                elif t == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") in ("tool_use", "server_tool_use"):
                            log.info("tool %s: %s", block.get("name"), json.dumps(block.get("input", {}))[:200])
                elif t == "user":
                    for block in (ev.get("message", {}).get("content") or []):
                        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                            log.warning("tool failed: %s", str(block.get("content"))[:300])
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
    # Quick manual test: python -m nova.brain ["question" ...]
    # (PC tools on, with a pretend allowed-apps list: nothing is written to state.json.)
    logging.basicConfig(level=logging.INFO)
    from . import config, tools_server
    from .pc_tools import PCTools
    from .tool_host import ToolHost
    loc = config.load()["claude"]["location"]
    tools_server.LOCATION = loc
    pc = PCTools(on_timer_done=lambda text: print(f"\n   >>> TIMER ALERT: {text}\n"),
                 allowed_apps=lambda: [{"name": "Calculator", "id": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                                        "aliases": []}])
    tl, fn = pc.registry()
    host = ToolHost(tools_server.TOOLS + tl, {**tools_server.FUNCS, **fn})
    b = Brain(location=loc, tools=host)
    b.start()
    qs = sys.argv[1:] or ["What's the capital of France?", "And what about Germany?", "What time is it?",
                          "What's the weather like?", "What's the latest news today?"]
    for q in qs:
        t = time.perf_counter()
        first = None
        searched: list[str] = []
        out = []
        for d in b.ask(q, on_search=lambda tool: searched.append(f"{tool} at {time.perf_counter() - t:.2f}s")):
            if first is None:
                first = time.perf_counter() - t
            out.append(d)
        s = f", tools: {', '.join(searched)}" if searched else ""
        print(f"\nQ: {q}\nA: {''.join(out)}\n   first text {first:.2f}s{s}, done {time.perf_counter() - t:.2f}s")
    b.close()
