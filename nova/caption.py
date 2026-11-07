"""Live captions: your words on the notch while you're still talking.

Vosk (offline, the same small model as the wake word, but with its full vocabulary) gives a rough
transcript every 100 ms. When you finish, Whisper's more accurate text replaces it.

One recognizer is reused (Reset between turns): a new one stalls ~0.9 s on its first speech, a
reused one handles 5 s of speech in ~0.4 s. It runs on its own thread so it can never hold up
the end-of-speech detection.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from typing import Callable

import numpy as np
from vosk import KaldiRecognizer

from .audio import MIC_SR

log = logging.getLogger("nova.caption")

# Vosk's spellings of "Hey Nova" (and of it half-heard), stripped from the start of a caption.
# While the caption is nothing but these words, nothing is shown (it used to flash "Hey").
WAKE_WORDS = {"hey", "hi", "hay", "he", "a", "and", "eh", "hello",
              "nova", "no", "va", "over", "nover", "noah", "nora", "innova", "know", "of", "novel"}


def strip_wake(text: str) -> str:
    words = text.split()
    i = 0
    while i < min(len(words), 4) and words[i] in WAKE_WORDS:
        i += 1
    return " ".join(words[i:])


_RESET = object()
_RELEASE = object()


class LiveCaption(threading.Thread):
    def __init__(self, model, emit: Callable[[str], None]):
        super().__init__(daemon=True, name="live-caption")
        self._rec = KaldiRecognizer(model, MIC_SR)
        self._emit = emit
        self._q: queue.Queue = queue.Queue()
        self._gen = 0                # bumps on every reset, so stale text is never shown
        self._strip_wake = False
        self._hold = False
        self._done: list[str] = []
        self._text = ""
        self._last = ""
        self._warm_up()

    def _warm_up(self):
        t = time.perf_counter()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(MIC_SR * 2) * 2000).astype(np.int16)
        for i in range(0, len(noise), 1600):
            self._rec.AcceptWaveform(noise[i:i + 1600].tobytes())
        self._rec.FinalResult()
        self._rec.Reset()
        log.info("live captions ready in %.1fs", time.perf_counter() - t)

    # ---- called from the voice loop ----------------------------------------------------
    def begin(self, strip_wake: bool = False, hold: bool = False):
        """Start a new caption (one utterance). hold=True: transcribe but show nothing until
        release() (used until the speaker has clearly started talking)."""
        self._gen += 1
        self._q.put((_RESET, self._gen, (strip_wake, hold)))

    def release(self):
        self._q.put((_RELEASE, self._gen, None))

    def end(self):
        """The utterance is over: drop anything still queued, show nothing more."""
        self._gen += 1

    def feed(self, block: np.ndarray):
        self._q.put((block, self._gen, None))

    # ---- worker -------------------------------------------------------------------------------
    def run(self):
        while True:
            item, gen, arg = self._q.get()
            if item is _RESET:
                self._rec.Reset()
                self._done, self._text, self._last = [], "", ""
                self._strip_wake, self._hold = arg
                continue
            if gen != self._gen:
                continue                         # audio from a turn that has already finished
            if item is _RELEASE:
                self._hold = False
                self._show(self._text, gen)
                continue
            try:
                if self._rec.AcceptWaveform(item.tobytes()):
                    text = json.loads(self._rec.Result()).get("text", "")
                    if text:
                        self._done.append(text)
                    partial = ""
                else:
                    partial = json.loads(self._rec.PartialResult()).get("partial", "")
            except Exception:
                log.exception("caption failed")
                continue
            text = " ".join(self._done + ([partial] if partial else []))
            if self._strip_wake:
                text = strip_wake(text)
            text = re.sub(r"\bi\b", "I", text)
            self._text = text[:1].upper() + text[1:]
            if not self._hold:
                self._show(self._text, gen)

    def _show(self, text: str, gen: int):
        if text and text != self._last and gen == self._gen:
            self._last = text
            self._emit(text)


class NoCaption:
    """Live captions turned off ([ui] live_captions = false)."""

    def begin(self, strip_wake: bool = False, hold: bool = False):
        pass

    def release(self):
        pass

    def end(self):
        pass

    def feed(self, block):
        pass
