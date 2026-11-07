"""Speech to text with faster-whisper. CUDA with CPU fallback.

Models (tools/stt_benchmark.py; accented, fast, noisy test speech; words wrong / time per clip):
base.en 4.2% 0.11 s, small.en 2.0% 0.20 s, large-v3-turbo 1.2% 0.36 s. On the user's real voice
base.en did much worse ("mic check one two three" -> "my check one do a treat").
"""
from __future__ import annotations

import gc
import logging
import threading
import time

import numpy as np
from faster_whisper import WhisperModel

log = logging.getLogger("nova.stt")


class Transcriber:
    def __init__(self, model: str = "base.en", device: str = "cpu", vocabulary: list[str] | None = None,
                 compute_type: str = "float16"):
        vocab = ", ".join(vocabulary or [])
        # Naming expected words in the prompt fixed "Claude" (cloud/clod/Lord) in testing.
        self.prompt = "Nova is my voice assistant." + (f" I use {vocab}." if vocab else "")
        self._name, self._device, self._compute = model, device, compute_type
        self._lock = threading.Lock()
        self.on_gpu = False
        self.model: WhisperModel | None = None
        self.ensure_loaded()

    @property
    def name(self) -> str:
        return self._name

    @property
    def ready(self) -> bool:
        return self.model is not None

    def ensure_loaded(self):
        with self._lock:
            if self.model is None:
                self.model = self._load(self._name, self._device)

    def unload_gpu(self) -> bool:
        """Free Whisper's memory (NOVA muted), video memory and RAM. Reloading large-v3-turbo
        takes ~11 s (int8 is converted on load), so after unmuting NOVA uses its small check
        model until turbo is back (see Assistant._stt)."""
        with self._lock:
            if self.model is None or not self.on_gpu:
                return False
            self.model = None
        gc.collect()
        return True

    def _load(self, name: str, device: str) -> WhisperModel:
        t = time.perf_counter()
        if device == "cuda":
            try:
                m = WhisperModel(name, device="cuda", compute_type=self._compute)
                list(m.transcribe(np.zeros(16000, np.float32), language="en")[0])  # CUDA DLL errors show up here
                self.on_gpu = True
                log.info("whisper %s on CUDA %s (%.1fs)", name, self._compute, time.perf_counter() - t)
                return m
            except Exception as e:
                log.warning("CUDA unavailable (%s); using CPU", e)
        if name.startswith(("large", "turbo", "distil-large", "medium")):
            log.warning("%s is too slow on the CPU; using base.en instead", name)
            name = "base.en"
        m = WhisperModel(name, device="cpu", compute_type="int8")
        m.transcribe(np.zeros(16000, np.float32), language="en")
        self.on_gpu = False
        log.info("whisper %s on CPU int8", name)
        return m

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 mono 16 kHz."""
        return self.transcribe_scored(audio)[0]

    def transcribe_scored(self, audio: np.ndarray, prompt: bool = True,
                          quick: bool = False) -> tuple[str, float, float]:
        """(text, no_speech_prob, avg_logprob). Low avg_logprob / high no_speech_prob means
        Whisper is guessing (it invents short phrases like "Good night." from faint echo).

        quick: one greedy pass, at most 40 tokens (the "was that you?" check while NOVA talks).
        By default Whisper retries an unsure or repetitive guess at up to 5 higher temperatures;
        on faint echo that took 1.3-4.8 s (NOVA stays ducked meanwhile) and ended in guesses like
        "Hey, no, no, no." Repetitive guesses are dropped instead of retried.

        Never raises: a failure (e.g. a game has taken all the video memory) is retried once,
        then treated as "heard nothing", so the voice loop keeps running."""
        for attempt in (1, 2):
            try:
                return self._transcribe(audio, prompt, quick)
            except Exception as e:
                log.error("transcription failed (attempt %d): %s", attempt, e)
                gc.collect()
                time.sleep(0.2)
        return "", 1.0, -10.0

    def _transcribe(self, audio: np.ndarray, prompt: bool, quick: bool) -> tuple[str, float, float]:
        self.ensure_loaded()
        model = self.model
        t, cpu = time.perf_counter(), time.thread_time()
        opts = {"temperature": 0.0, "max_new_tokens": 40} if quick else {}
        segs, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                        initial_prompt=self.prompt if prompt else None,
                                        condition_on_previous_text=False, **opts)
        t_vad = time.perf_counter() - t                  # voice detection (CPU) + features
        segs = list(segs)
        if quick:
            loops = [s for s in segs if s.compression_ratio > 2.4]
            if loops:
                log.info("dropped a repetitive guess: %r", " ".join(s.text for s in loops).strip()[:80])
                segs = [s for s in segs if s.compression_ratio <= 2.4]
        text = " ".join(s.text for s in segs).strip()
        no_speech = max((s.no_speech_prob for s in segs), default=1.0)
        logprob = min((s.avg_logprob for s in segs), default=-10.0)
        took = time.perf_counter() - t
        log.info("transcribed in %.2fs: %r (no-speech %.2f, logprob %.2f)", took, text, no_speech, logprob)
        if took > 1.0:
            log.warning("slow transcription (%s, %.1fs of audio): voice detection %.2fs, decoding %.2fs, "
                        "this thread's CPU time %.2fs", self._name, len(audio) / 16000, t_vad,
                        took - t_vad, time.thread_time() - cpu)
        return text, no_speech, logprob
