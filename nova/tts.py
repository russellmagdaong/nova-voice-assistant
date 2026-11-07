"""Text to speech: edge-tts (natural voice, online) with the offline Windows voice as fallback.

speak_stream() takes Claude's text as it streams in, splits it into sentences, synthesises the
next sentence while the current one plays, and can be interrupted with Player.stop().
"""
from __future__ import annotations

import asyncio
import io
import gc
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

import av
import edge_tts
import numpy as np

from .audio import OUT_SR, Player, decode

log = logging.getLogger("nova.tts")
CREATE_NO_WINDOW = 0x08000000

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")


class SentenceSplitter:
    """Feed streamed text; get back complete sentences."""

    def __init__(self, first_max: int = 70):
        # A long opening sentence is split at a comma so speech starts sooner.
        self.first_max = first_max
        self.buf = ""
        self.first = True

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = _SENTENCE_END.search(self.buf)
            if not m:
                break
            sentence = self.buf[:m.end()].strip()
            self.buf = self.buf[m.end():]
            if sentence:
                out.append(sentence)
                self.first = False
        if self.first and len(self.buf) > self.first_max:
            cut = self.buf.rfind(", ", 15)
            if cut > 0:
                out.append(self.buf[:cut + 1].strip())
                self.buf = self.buf[cut + 2:]
                self.first = False
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


class _QueueReader(io.RawIOBase):
    """File-like object PyAV can read from while bytes are still arriving."""

    def __init__(self, q: queue.Queue):
        self.q = q
        self.buf = b""
        self.eof = False
        self.got_data = False

    def readable(self):
        return True

    def readinto(self, b) -> int:
        while not self.buf and not self.eof:
            item = self.q.get()
            if item is None:
                self.eof = True
            elif isinstance(item, Exception):
                raise item
            else:
                self.buf += item
                self.got_data = True
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        return n


def clean_for_speech(s: str) -> str:
    s = re.sub(r"[*_#`>]+", "", s)                 # stray markdown
    s = re.sub(r"https?://\S+", "", s)              # URLs
    return re.sub(r"\s+", " ", s).strip()


def compile_pronunciations(pron: dict[str, str] | None) -> list[tuple[re.Pattern, str]]:
    """[voice.pronunciations] in config.toml: word -> how to say it, spelled the way it sounds.
    Only the voice uses the respelling; the text on screen keeps the real spelling."""
    return [(re.compile(rf"\b{re.escape(w)}\b", re.I), say) for w, say in (pron or {}).items() if w and say]


def respell(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    for pattern, say in rules:
        text = pattern.sub(say, text)
    return text


ROOT = Path(__file__).resolve().parent.parent
KOKORO_DIR = ROOT / "models" / "kokoro"
DEFAULT_EDGE = "en-US-AvaMultilingualNeural"


class _ShrinkingSession:
    """Passes run options that return onnxruntime's GPU memory pool to its starting size after
    each run (kokoro-onnx calls sess.run(None, inputs) without any)."""

    def __init__(self, sess, ort):
        self._sess = sess
        self._ro = ort.RunOptions()
        self._ro.add_run_config_entry("memory.enable_memory_arena_shrinkage", "gpu:0")

    def __getattr__(self, name):
        return getattr(self._sess, name)

    def run(self, output_names, inputs, run_options=None):
        return self._sess.run(output_names, inputs, run_options or self._ro)


class Voice:
    """Speaks with either a local Kokoro voice ("kokoro:af_bella") or a Microsoft edge-tts voice
    ("edge:en-US-AvaMultilingualNeural"). Switch any time with set_voice().

    If the chosen engine fails, it falls back to the other one, then to the Windows voice."""

    def __init__(self, voice_id: str = "kokoro:af_bella", rate: str = "+5%", kokoro_speed: float = 1.05,
                 pronunciations: dict[str, str] | None = None):
        self.rate = rate
        self.kokoro_speed = kokoro_speed
        self.say_as = compile_pronunciations(pronunciations)
        self.name = DEFAULT_EDGE               # edge voice used when edge is chosen or as fallback
        self.kokoro_voice = "af_bella"
        self.engine = "kokoro"
        self.voice_id = ""
        self._kokoro = None
        self._kokoro_lock = threading.Lock()
        self._synth_lock = threading.Lock()        # one Kokoro synthesis at a time
        self._kokoro_failed = False
        self._kokoro_gpu = False
        self._offline_until = 0.0
        self.set_voice(voice_id)

    # ---- choosing a voice ------------------------------------------------------
    def set_voice(self, voice_id: str):
        if ":" not in voice_id:                  # old config style: a bare edge voice name
            voice_id = "edge:" + voice_id
        engine, name = voice_id.split(":", 1)
        self.voice_id, self.engine = voice_id, engine
        if engine == "kokoro":
            self.kokoro_voice = name
            threading.Thread(target=self._load_kokoro, daemon=True, name="kokoro-load").start()
        else:
            self.name = name
        log.info("voice: %s", voice_id)

    def _load_kokoro(self):
        with self._kokoro_lock:
            if self._kokoro is not None or self._kokoro_failed:
                return
            t = time.perf_counter()
            try:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
                ort.set_default_logger_severity(3)   # hide harmless "Memcpy nodes added" warnings
                so = ort.SessionOptions()
                so.intra_op_num_threads = 4      # fastest on this i5-12450H (8 threads was slower)
                providers: list = ["CPUExecutionProvider"]
                if "CUDAExecutionProvider" in ort.get_available_providers():
                    # Every sentence has a different length; an exhaustive cuDNN search per new
                    # shape would make each first-of-its-length sentence slow.
                    # Grow the GPU memory pool only as needed and shrink it back after every
                    # sentence: 505 MB instead of 1121 MB after a long sentence, same speed.
                    providers.insert(0, ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                                                   "arena_extend_strategy": "kSameAsRequested"}))
                sess = ort.InferenceSession(str(KOKORO_DIR / "kokoro-v1.0.onnx"), so, providers=providers)
                k = Kokoro.from_session(sess, str(KOKORO_DIR / "voices-v1.0.bin"))
                if sess.get_providers()[0] == "CUDAExecutionProvider":
                    k.sess = _ShrinkingSession(sess, ort)
                k.create("Ready.", voice=self.kokoro_voice, speed=1.0, lang="en-us")   # warm-up
                self._kokoro = k
                self._kokoro_gpu = sess.get_providers()[0] == "CUDAExecutionProvider"
                log.info("kokoro ready on %s in %.1fs", sess.get_providers()[0], time.perf_counter() - t)
            except Exception as e:
                self._kokoro_failed = True
                log.error("kokoro unavailable (%s); using edge-tts", e)

    def unload_gpu(self) -> bool:
        """Free Kokoro's video memory (NOVA muted). True if there was anything to free."""
        with self._kokoro_lock, self._synth_lock:
            if self._kokoro is None or not self._kokoro_gpu:
                return False
            self._kokoro = None
        gc.collect()
        return True

    def reload(self):
        """Load Kokoro again in the background (until it's ready, answers use the edge voice)."""
        if self.engine == "kokoro":
            threading.Thread(target=self._load_kokoro, daemon=True, name="kokoro-load").start()

    def _kokoro_synth(self, text: str) -> np.ndarray:
        lang = "en-gb" if self.kokoro_voice[:1] == "b" else "en-us"
        t = time.perf_counter()
        with self._synth_lock:
            audio, sr = self._kokoro.create(text, voice=self.kokoro_voice, speed=self.kokoro_speed, lang=lang)
        audio = audio.astype(np.float32)
        if sr != OUT_SR:
            n = int(len(audio) * OUT_SR / sr)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        log.debug("kokoro %.2fs for %.1fs of speech", time.perf_counter() - t, len(audio) / OUT_SR)
        return audio

    # ---- synthesis -----------------------------------------------------------
    def synth(self, text: str) -> np.ndarray:
        """Whole sentence at once (used for short fixed phrases)."""
        parts = list(self.synth_stream(text))
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def synth_stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield audio for one sentence in the chosen voice, falling back if it fails."""
        text = respell(clean_for_speech(text), self.say_as)
        if not text:
            return
        if self.engine == "kokoro" and self._kokoro is not None:
            try:
                yield self._kokoro_synth(text)
                return
            except Exception as e:
                log.warning("kokoro failed (%s); using edge-tts for this sentence", e)
        # edge-tts: the chosen edge voice, or the default one while Kokoro is loading / failed
        if time.monotonic() >= self._offline_until:
            got = False
            try:
                for chunk in self._edge_stream(text):
                    got = True
                    yield chunk
                return
            except Exception as e:
                log.warning("edge-tts failed (%s); offline voice for 60s", e)
                self._offline_until = time.monotonic() + 60
                if got:              # part of the sentence was already spoken; don't repeat it
                    return
        if self._kokoro is not None:
            yield self._kokoro_synth(text)
        else:
            yield self._offline(text)

    HEDGE_AFTER = (0.7, 1.6)   # seconds without audio before sending a duplicate request

    def _download(self, text: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()

        def run():
            async def go():
                comm = edge_tts.Communicate(text, self.name, rate=self.rate)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        q.put(chunk["data"])
            try:
                asyncio.run(go())
                q.put(None)
            except Exception as e:      # noqa: BLE001 - forwarded to the decoder thread
                q.put(e)

        threading.Thread(target=run, daemon=True, name="tts-download").start()
        return q

    def _first_responder(self, text: str) -> queue.Queue:
        """edge-tts usually answers in 0.25-0.5 s but about 1 in 3 requests stalls for 2-4 s
        (measured). If no audio arrives within 0.7 s, send the same request again (and once more
        at 1.6 s) and use whichever answers first."""
        t0 = time.monotonic()
        attempts = [self._download(text)]
        pending = list(self.HEDGE_AFTER)
        errors = 0
        while True:
            for i, q in enumerate(attempts):
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    continue
                if isinstance(item, Exception) or item is None:
                    errors += 1
                    attempts[i] = queue.Queue()      # dead; never yields
                    if errors == len(attempts) and not pending:
                        if isinstance(item, Exception):
                            raise item
                        raise RuntimeError("no audio received")
                    continue
                if i:
                    log.info("edge-tts: duplicate request %d answered first (%.1fs)", i, time.monotonic() - t0)
                # Put the first chunk back in front of the rest of that stream.
                out: queue.Queue = queue.Queue()
                out.put(item)

                def pump(src=q, dst=out):
                    while True:
                        x = src.get()
                        dst.put(x)
                        if x is None or isinstance(x, Exception):
                            return
                threading.Thread(target=pump, daemon=True, name="tts-pump").start()
                return out
            if pending and time.monotonic() - t0 >= pending[0]:
                pending.pop(0)
                attempts.append(self._download(text))
            if time.monotonic() - t0 > 12:
                raise RuntimeError("edge-tts timed out")
            time.sleep(0.01)

    def _edge_stream(self, text: str) -> Iterator[np.ndarray]:
        """Download MP3 from edge-tts on a thread; decode it incrementally with PyAV."""
        q = self._first_responder(text)
        reader = _QueueReader(q)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_SR)
        # Small probe so decoding starts after the first few frames, not the whole file.
        with av.open(reader, format="mp3", options={"probesize": "2048", "analyzeduration": "0"}) as c:
            for frame in c.decode(audio=0):
                for f in resampler.resample(frame):
                    yield f.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
            for f in resampler.resample(None):
                yield f.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
        if not reader.got_data:
            raise RuntimeError("no audio received")

    @staticmethod
    def _offline(text: str) -> np.ndarray:
        """Windows System.Speech via PowerShell (slow to start, but always available)."""
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            script = ("Add-Type -AssemblyName System.Speech;"
                      "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                      f"$s.SetOutputToWaveFile('{path}');"
                      "$s.Speak([Console]::In.ReadToEnd());$s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text,
                           text=True, encoding="utf-8", timeout=30, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            return decode(Path(path).read_bytes())
        except Exception as e:
            log.error("offline voice failed: %s", e)
            return np.zeros(0, np.float32)
        finally:
            Path(path).unlink(missing_ok=True)

    # ---- streaming playback -------------------------------------------------
    def speak_stream(self, deltas: Iterable[str], player: Player,
                     on_sentence: Callable[[str], None] = lambda s: None,
                     on_first_audio: Callable[[], None] = lambda: None) -> bool:
        """Speak streamed text. Returns True if it played to the end, False if stopped.

        Three stages run at once: the caller's thread splits Claude's text into sentences,
        synth_worker downloads each sentence's audio in order (one ahead of playback), and
        play_worker plays chunks as they arrive.
        """
        sentences: queue.Queue[str | None] = queue.Queue()
        # Each item: (sentence, queue of audio chunks ending with None)
        clips: queue.Queue[tuple[str, queue.Queue] | None] = queue.Queue(maxsize=2)

        def synth_worker():
            while (s := sentences.get()) is not None:
                if player.stopped:
                    continue
                chunks: queue.Queue = queue.Queue()
                clips.put((s, chunks))
                try:
                    for c in self.synth_stream(s):
                        if player.stopped:
                            break
                        chunks.put(c)
                except Exception:
                    log.exception("synthesis failed")
                finally:
                    chunks.put(None)
            clips.put(None)

        def play_worker():
            first = True
            while (item := clips.get()) is not None:
                s, chunks = item
                shown = False
                while (c := chunks.get()) is not None:
                    if player.stopped:
                        continue
                    if not shown:
                        on_sentence(s)
                        shown = True
                    if first and len(c):
                        on_first_audio()
                        first = False
                    player.play(c)

        t_synth = threading.Thread(target=synth_worker, daemon=True, name="tts-synth")
        t_play = threading.Thread(target=play_worker, daemon=True, name="tts-play")
        t_synth.start()
        t_play.start()
        # Kokoro on the CPU takes ~1 s per short phrase, so give it a shorter first chunk.
        splitter = SentenceSplitter(40 if self.engine == "kokoro" and self._kokoro is not None else 70)
        try:
            for d in deltas:
                for s in splitter.feed(d):
                    sentences.put(s)
            for s in splitter.flush():
                sentences.put(s)
        finally:
            sentences.put(None)
            t_synth.join()
            t_play.join()
        return not player.stopped

    def say(self, text: str, player: Player) -> bool:
        return self.speak_stream([text], player)
