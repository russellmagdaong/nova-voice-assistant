"""The voice loop (worker thread).

Asleep:        Vosk listens for "Hey Nova" (cheap, offline).
Conversation:  "Hey Nova ..." starts a conversation. After each answer NOVA keeps listening
               without the wake word, until 10 s of silence, "that's all"/"goodbye", or a click.
Talking over:  while NOVA thinks or speaks, the mic stays on (echo-cancelled). If you start
               talking, NOVA stops and listens.
"""
from __future__ import annotations

import collections
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import state, ui
from .audio import (CHIME_ERROR, CHIME_LISTEN, CHIME_SLEEP, MIC_BLOCK, MIC_SR, Mic, Player,
                    make_echo_canceller, rms)
from .brain import Brain, ClaudeError
from .caption import LiveCaption, NoCaption
from .stt import Transcriber
from .tts import Voice
from .wake import WakeDetector, split_wake

log = logging.getLogger("nova.assistant")


def trim_ram():
    """Hand idle memory back to Windows (used when muted; pages come back as they're needed)."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Typed, or the 64-bit process handle gets mangled and the call silently fails.
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        k32.SetProcessWorkingSetSize.restype = wintypes.BOOL
        all_of_it = ctypes.c_size_t(-1).value
        if not k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), all_of_it, all_of_it):
            log.debug("couldn't trim memory (error %d)", ctypes.get_last_error())
    except Exception as e:
        log.debug("couldn't trim memory: %s", e)
BLOCK_S = MIC_BLOCK / MIC_SR

CANCEL_RE = re.compile(r"^\W*(never ?mind|cancel|stop|forget it|nothing)\W*$", re.I)
NEW_CONV_RE = re.compile(r"^\W*(new conversation|start over|start a new conversation|reset)\W*$", re.I)
END_RE = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|thanks|thank you|alright|cool|great)\W+)*"
    r"(?:that'?s all|that is all|that'?s it|good ?bye|bye(?: bye)?|stop listening|i'?m done|"
    r"we'?re done|nothing else|go to sleep|see you(?: later)?|talk (?:to you )?later)"
    r"(?:\W+(?:nova|thanks|thank you))*\W*$", re.I)
# Words people start an interruption with. One of these is enough to stop NOVA.
INTERRUPT_WORDS = {"wait", "stop", "hold", "hang", "no", "nope", "actually", "sorry", "pause", "quiet",
                   "enough", "cancel", "okay", "ok", "nova", "excuse", "shush", "shh", "hey", "um", "uh",
                   "but", "what", "huh", "nevermind"}
# Whisper sometimes "hears" these in near-silence.
JUNK = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye", "so", "uh", "um"}


@dataclass
class Recording:
    blocks: list = field(default_factory=list)
    t_end: float = 0.0           # when the speaker stopped talking (perf_counter)
    speech_s: float = 0.0        # seconds of loud audio (to spot junk transcripts)


class Assistant:
    def __init__(self, cfg: dict, bridge: ui.Bridge):
        self.cfg = cfg
        self.conv = cfg["conversation"]
        self.bridge = bridge
        self.muted = False
        self._abort = threading.Event()      # user clicked the notch
        self._quit = threading.Event()
        self.mic: Mic | None = None
        self.aec = None
        self.player = Player()
        self.noise = 200.0
        self.speaker_s = 0.0                 # seconds of NOVA speech the echo canceller has seen
        self.current_answer: list[str] = []
        bridge.stop_requested.connect(self.stop_requested)
        bridge.mute_toggled.connect(self.set_muted)
        bridge.new_conversation.connect(self.new_conversation)
        bridge.voice_selected.connect(self.select_voice)
        self._busy = False                   # in a conversation

    # ---- UI actions (called on the Qt thread) ----------------------------------------
    def stop_requested(self):
        """Click: stop talking if speaking; otherwise end the conversation."""
        if self.player.level > 0 or self._speaking:
            self.player.stop()
        else:
            self._abort.set()

    _speaking = False

    def set_muted(self, on: bool):
        self.muted = on
        if on:
            self._abort.set()
            self.player.stop()
        if self.mic:
            self.mic.paused = on
            self.mic.flush()
        self.bridge.state.emit(ui.MUTED if on else ui.IDLE)

    def select_voice(self, voice_id: str):
        state.save(voice=voice_id)
        if not hasattr(self, "voice"):
            self.cfg["voice"]["name"] = voice_id     # still starting up
            return
        self.voice.set_voice(voice_id)
        if not self._busy and not self.muted:
            threading.Thread(target=self._preview_voice, daemon=True, name="voice-preview").start()

    def _preview_voice(self):
        # Give a freshly chosen Kokoro voice a moment to load (first time only).
        for _ in range(100):
            if self.voice.engine != "kokoro" or self.voice._kokoro is not None or self.voice._kokoro_failed:
                break
            time.sleep(0.1)
        if self._busy:
            return
        self.mic.paused = True
        self.player.reset()
        try:
            self.voice.say("Hi, I'm Nova.", self.player)
        finally:
            self.player.close()
            time.sleep(0.3)
            self.mic.flush()
            self.mic.paused = self.muted

    def new_conversation(self):
        if hasattr(self, "brain"):
            self.brain.reset()
        self.bridge.answer.emit("Starting a new conversation.")

    def levels(self):
        return (self.mic.level if self.mic and not self.muted else 0.0), self.player.level

    def quit(self):
        self._quit.set()
        self._abort.set()
        self.player.stop()

    # ---- startup ---------------------------------------------------------------------------
    def run(self):
        try:
            self._load()
        except Exception as e:
            log.exception("startup failed")
            self.bridge.answer.emit(f"Couldn't start: {e}")
            self.bridge.state.emit(ui.ERROR)
            return
        try:
            self._loop()
        except Exception:
            log.exception("voice loop crashed")
            self.bridge.answer.emit("NOVA hit an error. See logs\\nova.log.")
            self.bridge.state.emit(ui.ERROR)
        finally:
            if self.mic:
                self.mic.stop()
            self.brain.close()

    def _load(self):
        c = self.cfg
        self.bridge.state.emit(ui.LOADING)
        t = time.perf_counter()
        self.brain = Brain(c["claude"]["path"] or None, c["claude"]["model"],
                           c["claude"]["conversation_timeout"], c["claude"]["location"])
        self.brain.start()                      # warm up Claude while the models load
        self.voice = Voice(c["voice"]["name"], c["voice"]["rate"], c["voice"]["kokoro_speed"],
                           c["voice"]["pronunciations"])
        # The first edge-tts request is slow (1.5-4 s: connection setup); later ones ~0.3 s.
        threading.Thread(target=self.voice.synth, args=("Ready.",), daemon=True, name="tts-warmup").start()
        self.wake = WakeDetector()
        if c["ui"].get("live_captions", True):
            self.caption = LiveCaption(self.wake.model, self.bridge.partial.emit)
            self.caption.start()
        else:
            self.caption = NoCaption()
        stt = c["speech_to_text"]
        self.stt = Transcriber(stt["model"], stt["device"], stt["vocabulary"], stt["compute_type"])
        # The "was that you or NOVA's echo?" check (talking over NOVA) needs a model whose
        # confidence scores mean "guessing" when it's guessing. base.en's do (the thresholds in
        # _BargeIn were tuned on it); large-v3-turbo invents confident phrases from faint echo
        # ("I can't believe you.") and always reports no-speech 0.00. base.en is also 4x faster.
        if stt["check_model"] and stt["check_model"] != stt["model"]:
            self.stt_check = Transcriber(stt["check_model"], stt["device"], stt["vocabulary"], "float16")
        else:
            self.stt_check = self.stt
        if self.conv["talk_over"]:
            self.aec = make_echo_canceller(self.conv["echo_cancellation"], self.conv["noise_suppression"])
        self.player.aec = self.aec
        self.mic = Mic(c["mic"]["device"], aec=self.aec)
        self.mic.start()
        self._ready_at = time.monotonic()
        log.info("ready in %.1fs (mic: %s, talk over: %s)", time.perf_counter() - t, self.mic.name,
                 "on" if self.aec else "off")
        self.bridge.state.emit(ui.MUTED if self.muted else ui.IDLE)

    # ---- asleep: wait for "Hey Nova" ----------------------------------------------------------
    def _loop(self):
        w = self.cfg["wake"]
        self.ring = collections.deque(maxlen=int(w["preroll"] / BLOCK_S))
        while not self._quit.is_set():
            block = self.mic.read(timeout=0.5)
            self._sync_gpu()
            self._trim_after_startup()
            if block is None or self.muted:
                continue
            self.ring.append(block)
            self._track_noise(block)
            if self.wake.feed(block):
                self._busy = True
                try:
                    self._handle_wake()
                finally:
                    self._busy = False
                    self._back_to_sleep()

    _gpu_freed = False

    def _stt(self) -> Transcriber:
        """The main model, or the small one while the main one is reloading after an unmute."""
        if self.stt.ready or self.stt_check is self.stt:
            return self.stt
        log.info("%s still loading; using %s for now", self.stt.name, self.stt_check.name)
        return self.stt_check
    _ready_at = 0.0
    _trimmed = False

    _last_active = 0.0
    _trimmed_idle = True
    IDLE_TRIM_S = 600

    def _trim_after_startup(self):
        """Loading the CUDA libraries and models touches ~1.5 GB of RAM that's never needed again;
        a running NOVA only uses ~0.2-0.3 GB. Hand the rest back once everything has loaded, and
        again after 10 minutes without a conversation. (Not after every conversation: the first
        reply after a trim starts ~0.5 s later while pages come back.)"""
        if self._trimmed and not self._trimmed_idle and time.monotonic() - self._last_active > self.IDLE_TRIM_S:
            self._trimmed_idle = True
            trim_ram()
            return
        if self._trimmed or time.monotonic() - self._ready_at < 3:
            return
        v = self.voice
        if v.engine == "kokoro" and v._kokoro is None and not v._kokoro_failed:
            return                               # Kokoro still loading
        self._trimmed = True
        trim_ram()
        log.info("startup finished; released start-up memory")

    def _sync_gpu(self):
        """While muted, unload Whisper and Kokoro (frees their video memory and RAM; the CUDA
        driver keeps ~100 MB until NOVA quits). On unmute, load them back in the background:
        the small check model is back in ~1 s and transcribes until large-v3-turbo is (~11 s),
        and answers use the edge voice until Kokoro is (~2 s)."""
        if self.muted:
            models = [("whisper", self.stt), ("kokoro", self.voice)]
            if self.stt_check is not self.stt:
                models.append(("echo check", self.stt_check))
            freed = [n for n, m in models if m.unload_gpu()]
            if freed:
                log.info("muted: freed video memory (%s)", ", ".join(freed))
                trim_ram()
            self._gpu_freed = self._gpu_freed or bool(freed)
        elif self._gpu_freed:
            self._gpu_freed = False
            log.info("unmuted: loading models back onto the GPU")
            self.voice.reload()
            def reload_whisper():
                self.stt_check.ensure_loaded()     # small model first (~1 s): it fills in
                self.stt.ensure_loaded()           # while large-v3-turbo reloads (~11 s)
            threading.Thread(target=reload_whisper, daemon=True, name="whisper-load").start()

    def _track_noise(self, block):
        level = rms(block)
        self.noise = 0.98 * self.noise + 0.02 * min(level, self.noise * 3)

    def _back_to_sleep(self):
        self._last_active = time.monotonic()
        self._trimmed_idle = False
        self.bridge.countdown.emit(-1.0)
        self.wake.reset()
        self.ring.clear()
        self.mic.flush()
        self.mic.paused = self.muted
        self._abort.clear()
        if not self.muted:
            self.bridge.state.emit(ui.IDLE)

    def _chime(self, sound: np.ndarray):
        """Play a chime without the mic hearing it. Queued mic audio is kept (it may hold
        the start of the question)."""
        self.mic.paused = True
        self.player.reset()
        self.player.play(sound)
        self.player.close()
        time.sleep(0.12)
        self.mic.paused = self.muted

    # ---- recording one utterance ---------------------------------------------------------------
    def _record(self, *args, **kwargs) -> Recording | str | None:
        try:
            return self._record_inner(*args, **kwargs)
        finally:
            self.caption.end()

    def _record_inner(self, blocks: list, start_deadline: float, started: bool = False,
                ignore_first_s: float = 0.0, on_pause: Callable[[list], str | None] | None = None,
                countdown: bool = False, strip_wake: bool = False) -> Recording | str | None:
        """Record until the speaker finishes.

        Returns a Recording; None if nobody started talking before `start_deadline` or the user
        clicked; or whatever string `on_pause` returned (used by the wake step).
        Live captions are shown from the start after a wake word (strip_wake), otherwise once
        you've started talking (so room noise doesn't flash words on the notch).
        """
        w = self.cfg["wake"]
        threshold = max(self.noise * 2.5, 300)
        rec = Recording(list(blocks))
        after = loud_run = silent = 0.0
        paused_checked = False
        t_start = time.perf_counter()
        last_cd = 0.0
        self.caption.begin(strip_wake=strip_wake, hold=not (started or strip_wake))
        for b in blocks:
            self.caption.feed(b)
        while after < w["max_record"]:
            if self._abort.is_set() or self._quit.is_set():
                log.info("listening cancelled")
                return None
            b = self.mic.read(timeout=0.5)
            now = time.perf_counter()
            if countdown and not started and now - last_cd > 0.1:
                total = max(0.001, start_deadline - t_start)
                self.bridge.countdown.emit(max(0.0, (start_deadline - now) / total))
                last_cd = now
            if b is None:
                if not started and now >= start_deadline:
                    return None
                continue
            rec.blocks.append(b)
            self.caption.feed(b)
            after += BLOCK_S
            if rms(b) >= threshold:
                loud_run += BLOCK_S
                rec.speech_s += BLOCK_S
                silent = 0.0
                if not started and after > ignore_first_s and loud_run >= 0.3:
                    started = True
                    self.caption.release()
                    if countdown:
                        self.bridge.countdown.emit(-1.0)
            else:
                silent += BLOCK_S
                if silent >= 0.15:
                    loud_run = 0.0
                if not started:
                    self._track_noise(b)
            if started and silent >= w["end_silence"]:
                rec.t_end = now - silent
                return rec
            if not started:
                if on_pause and not paused_checked and silent >= 0.7:
                    paused_checked = True
                    result = on_pause(rec.blocks)
                    if result is not None:
                        return result
                    silent = 0.0
                if now >= start_deadline:
                    return None
        rec.t_end = time.perf_counter()
        return rec

    @staticmethod
    def _pcm(blocks) -> np.ndarray:
        return np.concatenate(blocks).astype(np.float32) / 32768.0

    # ---- waking up ----------------------------------------------------------------------------------
    def _handle_wake(self):
        w = self.cfg["wake"]
        self._abort.clear()
        self.bridge.state.emit(ui.LISTENING)
        log.info("wake candidate (noise floor %.0f)", self.noise)
        # Vosk fires right after "nova", so 2 s of pre-roll covers "Hey Nova"; less audio = faster Whisper.
        keep = int(min(2.0, w["preroll"]) / BLOCK_S)
        state = {"confirmed": False}

        def on_pause(blocks):
            # "Hey Nova" then a pause: confirm it now, so the chime comes during the pause.
            text = self._stt().transcribe(self._pcm(blocks))
            ok, cmd = split_wake(text, lax=True)
            if not ok:
                log.info("false alarm: %r", text)
                return "false"
            if cmd:
                return "cmd:" + cmd           # they'd already said the question
            state["confirmed"] = True
            self._chime(CHIME_LISTEN)
            return None

        res = self._record(list(self.ring)[-keep:], time.perf_counter() + w["wait_for_command"],
                           ignore_first_s=0.2, on_pause=on_pause, strip_wake=True)
        if res == "false":
            self.bridge.partial.emit("")
            return
        if isinstance(res, str):
            return self._conversation(res[4:], time.perf_counter())
        if res is None:
            if state["confirmed"]:
                log.info("no question after wake")
                self._chime(CHIME_SLEEP)
            return
        text = self._stt().transcribe(self._pcm(res.blocks))
        ok, cmd = split_wake(text, lax=True)
        if not ok:
            if not state["confirmed"]:
                log.info("false alarm: %r", text)
                return
            cmd = text
        if not cmd:
            self._chime(CHIME_SLEEP)
            return
        self._conversation(cmd, res.t_end)

    # ---- the conversation ----------------------------------------------------------------------------
    def _conversation(self, cmd: str, t_end: float):
        follow = self.conv["enabled"]
        while True:
            self.bridge.heard.emit(cmd)
            if END_RE.match(cmd) or CANCEL_RE.match(cmd):
                log.info("conversation ended by %r", cmd)
                self._chime(CHIME_SLEEP)
                return
            if NEW_CONV_RE.match(cmd):
                self.brain.reset()
                self.bridge.answer.emit("Okay, new conversation.")
                self._answer_fixed("Okay, starting fresh.")
                interrupted = ([], "", [])
            else:
                interrupted = self._answer(cmd, t_end)

            if not follow or self._quit.is_set():
                return
            # Listen for the next turn (no wake word).
            self._abort.clear()
            self.bridge.state.emit(ui.LISTENING)
            deadline = time.perf_counter() + self.conv["follow_up_timeout"]
            if interrupted[0]:
                blocks, prefix, _ = interrupted
                res = self._record(blocks, deadline, started=True)
                text, _, logprob = self._stt().transcribe_scored(self._pcm(res.blocks)) if res else ("", 1.0, -10.0)
                if res and not self._is_junk(text, res, logprob):
                    said = self._strip_wake(text)
                    # "That's all" / "never mind" on its own wins; otherwise join the two parts.
                    ends = END_RE.match(said) or CANCEL_RE.match(said)
                    cmd = f"{prefix} {said}".strip() if prefix and not ends else said
                    t_end = res.t_end
                    continue
                if res is None:
                    return
                if prefix:                   # a noise cut in before NOVA said anything: ask again
                    log.info("interruption was noise; asking again")
                    cmd, t_end = prefix, time.perf_counter()
                    continue
            got = self._listen_once(deadline, interrupted[2])
            if got is None:
                if not self._abort.is_set():
                    log.info("conversation ended after %.0fs of silence", self.conv["follow_up_timeout"])
                    self._chime(CHIME_SLEEP)
                return
            cmd, t_end = got

    def _listen_once(self, deadline: float, initial: list | None = None) -> tuple[str, float] | None:
        initial = initial or []
        while True:
            res = self._record(initial, deadline, countdown=True)
            initial = []
            if res is None:
                return None
            text, _, logprob = self._stt().transcribe_scored(self._pcm(res.blocks))
            if not self._is_junk(text, res, logprob):
                return self._strip_wake(text), res.t_end
            self.bridge.partial.emit("")
            log.info("ignored %r (%.1fs of sound)", text, res.speech_s)

    @staticmethod
    def _strip_wake(text: str) -> str:
        ok, rest = split_wake(text)          # people often say "Hey Nova" again mid-conversation
        return rest if ok and rest else text

    @staticmethod
    def _is_junk(text: str, rec: Recording, logprob: float = 0.0) -> bool:
        t = re.sub(r"[^a-z' ]", "", text.lower()).strip()
        if not t or (t in JUNK and rec.speech_s < 0.8):
            return True
        # A few words Whisper isn't sure of: on speakers, NOVA's echo tail came out of
        # large-v3-turbo as "Really nice." (logprob -1.10). Real speech scored -0.04 to -0.8.
        return len(t.split()) <= 3 and logprob < -1.0

    # ---- answering ----------------------------------------------------------------------------------
    def _answer(self, cmd: str, t_end: float) -> tuple[list, str, list]:
        """Ask Claude and speak the answer. Returns (captured, prefix, tail):
        captured = audio of the user talking over NOVA (empty if they didn't);
        prefix = the unanswered question if they cut in before NOVA said anything
                 (they were probably still talking, so the two parts are joined);
        tail = the last 1 s of mic audio when not interrupted."""
        self.bridge.state.emit(ui.THINKING)
        self.player.reset()
        talk_over = self.aec is not None
        if not talk_over:
            self.mic.paused = True
        answer: list[str] = []
        self.current_answer = answer          # the interruption check compares against this
        timing: dict = {}
        barge = _BargeIn(self) if talk_over else None

        def on_sentence(s):
            answer.append(s)
            self.bridge.answer.emit(" ".join(answer))

        def on_first_audio():
            timing["first"] = time.perf_counter()
            self.bridge.state.emit(ui.SPEAKING)

        def timed(deltas):
            for d in deltas:
                timing.setdefault("claude", time.perf_counter())
                yield d

        timing["asked"] = time.perf_counter()
        self._speaking = True
        if barge:
            barge.start()
        try:
            deltas = self.brain.ask(cmd, cancelled=lambda: self.player.stopped)
            self.voice.speak_stream(timed(deltas), self.player, on_sentence, on_first_audio)
            if "first" in timing:
                log.info("LATENCY end of speech -> first spoken word: %.2fs "
                         "(silence wait + transcribe %.2fs, Claude first text %.2fs, first sentence voiced %.2fs)",
                         timing["first"] - t_end, timing["asked"] - t_end,
                         timing.get("claude", timing["first"]) - timing["asked"],
                         timing["first"] - timing.get("claude", timing["asked"]))
        except ClaudeError as e:
            log.error("claude: %s", e)
            self.bridge.state.emit(ui.ERROR)
            self.bridge.answer.emit("Couldn't reach Claude. Try again in a moment.")
            self.player.reset()
            self.player.play(CHIME_ERROR)
            time.sleep(1.5)
        finally:
            self._speaking = False
            self.speaker_s += self.player.played_s
            self.player.close()
            captured, tail = barge.finish() if barge else (None, [])
            if not talk_over:
                time.sleep(0.4)                  # let the room go quiet before listening again
                self.mic.flush()
                self.mic.paused = self.muted
        if captured is not None:
            log.info("interrupted by the user")
            return captured, (cmd if "first" not in timing else ""), []
        return [], "", tail          # not interrupted; tail = last 1 s, may hold the start of a reply

    def _answer_fixed(self, text: str):
        self.player.reset()
        self.mic.paused = True
        self.bridge.state.emit(ui.SPEAKING)
        try:
            self.voice.say(text, self.player)
        finally:
            self.player.close()
            time.sleep(0.3)
            self.mic.flush()
            self.mic.paused = self.muted


class _BargeIn(threading.Thread):
    """Watches the (echo-cancelled) mic while NOVA thinks/speaks; stops NOVA when you talk.

    While NOVA is audible, a loud sound could be you or NOVA's own echo leaking past the echo
    canceller. NOVA ducks itself to 20% for 0.5 s: echo falls away, your voice doesn't. If the
    level dropped (you may have said one short word, or paused), Whisper decides: words that
    aren't in what NOVA was just saying mean it's you.
    Live log, headphones: real interruptions dropped to 0.30-0.41 of their trigger level and
    were wrongly ignored by a level-only check; simulated echo dropped to 0.02-0.24.
    """

    DUCK = 0.2               # NOVA's volume while checking
    CHECK_BLOCKS = 5         # 0.5 s check

    def __init__(self, a: Assistant):
        super().__init__(daemon=True, name="barge-in")
        self.a = a
        self._done = threading.Event()
        self.blocks: list | None = None
        self.recent: list = []

    def run(self):
        a = self.a
        recent = collections.deque(maxlen=15)        # last 1.5 s, so the start of your words is kept
        loud = collections.deque(maxlen=5)           # loud flags for the last 0.5 s
        playing_levels = collections.deque(maxlen=30)  # mic levels while NOVA plays (last 3 s)
        checking: list | None = None                 # levels seen while ducked
        trigger_level = trigger_threshold = 0.0
        last_audio = 0.0                             # last time NOVA was audibly playing
        # After a check says "echo", keep listening closely for 2.5 s: same loudness bar (your
        # voice mustn't raise the echo estimate), and if you keep talking, check again on
        # everything since the first trigger. Simulated "Wait. ... How long did it last?" was
        # lost when the first check heard "Good luck." and the raised bar hid the rest.
        retry_until = 0.0
        retry_blocks: collections.deque | None = None
        while not self._done.is_set():
            b = a.mic.read(timeout=0.1)
            if b is None:
                continue
            level = rms(b)
            recent.append(b)
            now = time.perf_counter()
            in_retry = now < retry_until
            if in_retry and retry_blocks is not None:
                retry_blocks.append(b)
            elif not in_retry:
                retry_blocks = None
            if a.player.level > 0:
                last_audio = now
            # Count the gaps between sentences (and the room's echo tail) as speaking too.
            speaking = time.perf_counter() - last_audio < 0.6 or checking is not None

            if checking is not None:
                checking.append(level)
                if len(checking) < self.CHECK_BLOCKS:
                    continue
                kept = float(np.median(checking[1:]))
                ratio = kept / max(trigger_level, 1.0)
                audio = list(retry_blocks) if retry_blocks is not None else list(recent)
                if ratio >= 0.45 and kept >= 250:
                    why = f"level held {trigger_level:.0f} -> {kept:.0f} while ducked"
                else:
                    why = self._words_check(audio, trigger_level, kept)
                if why:
                    log.info("user started talking (%s)", why)
                    a.player.stop()
                    a.player.gain = 1.0
                    self.blocks = audio
                    break
                a.player.gain = 1.0
                checking = None
                loud.clear()
                if retry_blocks is None:             # first "echo": one more chance for 2.5 s
                    retry_blocks = collections.deque(recent, maxlen=40)     # up to 4 s
                    retry_until = time.perf_counter() + 2.5
                else:                                # echo twice: back to the normal, adaptive bar
                    retry_blocks, retry_until = None, 0.0
                continue

            if in_retry:
                threshold = trigger_threshold        # don't let your voice raise the echo estimate
            else:
                if speaking:
                    playing_levels.append(level)
                # Typical leftover echo: the median mic level while NOVA plays (≈ noise on headphones).
                residual = float(np.median(playing_levels)) if len(playing_levels) >= 5 else 0.0
                # Until the echo canceller has heard ~1.5 s of NOVA it isn't fully settled: be stricter.
                settled = (not a.aec.echo) or a.speaker_s + a.player.played_s > 1.5   # bypassed: nothing to settle
                threshold = max(a.noise * 3, 350, residual * 3 if speaking else 0)
                if speaking and not settled:
                    threshold *= 2
            loud.append(level >= threshold)
            if level >= 500 and log.isEnabledFor(logging.DEBUG):
                log.debug("barge-in: level %.0f threshold %.0f speaking %s retry %s noise %.0f echo canceller %s",
                          level, threshold, speaking, in_retry, a.noise, "on" if a.aec.echo else "bypassed")
            if sum(loud) >= 2:                       # ~0.2 s of sound: catches "wait" / "stop"
                if speaking:                         # NOVA is talking: duck and check
                    trigger_threshold = threshold
                    trigger_level = max(level, threshold)
                    a.player.gain = self.DUCK
                    checking = []
                else:                                # NOVA is thinking/silent: no echo possible
                    log.info("user started talking while NOVA was thinking (level %.0f)", level)
                    a.player.stop()
                    self.blocks = list(retry_blocks) if retry_blocks is not None else list(recent)
                    break
        self.recent = list(recent)
        # Everything after the interruption stays in the mic queue for the main loop.

    def _words_check(self, blocks: list, trigger: float, kept: float) -> str | None:
        """Ask Whisper what the sound was. Returns a reason if it was you, None if it was echo."""
        a = self.a
        # No vocabulary prompt here: it makes Whisper more likely to invent words from echo.
        text, no_speech, logprob = a.stt_check.transcribe_scored(
            np.concatenate(blocks).astype(np.float32) / 32768.0, prompt=False, quick=True)
        heard = re.findall(r"[a-z']+", text.lower())
        if not heard or " ".join(heard) in JUNK:
            log.info("ignored echo (%.0f -> %.0f while ducked; heard %r)", trigger, kept, text)
            return None
        # One word 4+ times ("Okay. Okay. Okay. Okay. Okay.") is Whisper looping on faint echo.
        # ("no no no" is a real interruption, so 3 is still allowed.)
        if len(heard) >= 4 and len(set(heard)) == 1:
            log.info("ignored echo (%.0f -> %.0f while ducked; looping guess %r)", trigger, kept, text)
            return None
        nova_words = set(re.findall(r"[a-z']+", " ".join(a.current_answer[-3:]).lower()))
        new = [w for w in heard if w not in nova_words]
        cue = [w for w in new if w in INTERRUPT_WORDS]            # not echo of NOVA saying "no"
        # "Wait." / "Stop." on their own: the level collapses when you stop after one word, and
        # Whisper is less sure of a lone word (-0.69 to -0.83 in the headphones test), so an
        # interruption word gets a looser confidence bar. NOVA's own words are already excluded.
        if cue and no_speech <= 0.5 and logprob >= -1.0:
            return f"heard {text!r} ({cue[0]!r} is an interruption word)"
        # Whisper guessing at faint echo: low confidence, or the level collapsed when NOVA ducked.
        if no_speech > 0.5 or logprob < -0.9 or (kept / max(trigger, 1.0) < 0.1 and logprob < -0.6):
            log.info("ignored echo (%.0f -> %.0f while ducked; unsure guess %r, no-speech %.2f, logprob %.2f)",
                     trigger, kept, text, no_speech, logprob)
            return None
        # Two or more words NOVA didn't just say. One isn't enough: echo of "a curious tabby
        # cat" came out as "I'm curious." (one new word) in the speakers simulation.
        if len(new) >= 2 and len(new) / len(heard) >= 0.5:
            return f"heard {text!r}, not NOVA's words"
        log.info("ignored echo (%.0f -> %.0f while ducked; heard %r, %d new word%s)", trigger, kept, text,
                 len(new), "" if len(new) == 1 else "s")
        return None

    def finish(self) -> tuple[list | None, list]:
        self._done.set()
        self.join(timeout=3)
        return self.blocks, ([] if self.blocks else self.recent)
