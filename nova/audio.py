"""Mic input, speaker output, chimes and audio decoding."""
from __future__ import annotations

import io
import collections
import logging
import queue
import threading

import av
import numpy as np
import sounddevice as sd

log = logging.getLogger("nova.audio")

MIC_SR = 16000
MIC_BLOCK = 1600            # 100 ms
OUT_SR = 24000


def find_input_device(name: str) -> int | None:
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    log.warning("no input device matching %r; using the default", name)
    return None


class EchoCanceller:
    """WebRTC echo cancellation (via livekit): removes NOVA's own voice from the mic so you
    can talk over it on speakers. Tested: ~45 dB echo reduction after ~1 s of settling.

    With no echo or only a faint one (headphones), WebRTC's echo suppressor mutes YOU while
    NOVA talks: your voice kept 2 s into a reply 80%, after 12 s 1-2% (echo at 0-2% of NOVA's
    level). With a real echo (5%+, speakers) it keeps 57-90%. So it measures how strongly NOVA's
    voice comes back through the mic, and when the echo is faint or absent it hands out audio
    that skipped the echo canceller (noise suppression only). Faint echo is harmless there:
    your voice is far louder, and the talk-over check sorts out the rest.
    """

    FRAME = MIC_SR // 100     # WebRTC works in 10 ms frames
    HISTORY = 80              # blocks (8 s) used to measure the echo
    # Echo strength (mic echo level / NOVA's level), with hysteresis.
    GAIN_ON, GAIN_OFF = 0.06, 0.04

    def __init__(self, noise_suppression: bool = True):
        from livekit import rtc          # imported here so NOVA still runs without it
        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True,
                                              noise_suppression=noise_suppression)
        # Same clean-up minus the echo canceller, for when there's no echo (headphones).
        self._plain = rtc.AudioProcessingModule(echo_cancellation=False, high_pass_filter=True,
                                                noise_suppression=noise_suppression)
        self._lock = threading.Lock()
        self._ref = np.zeros(0, np.float32)
        self._hist: collections.deque = collections.deque(maxlen=self.HISTORY)   # (speaker, mic) levels
        self._blocks = 0
        self.echo = True          # assume an echo until measured (the safe choice on speakers)
        self.correlation: float | None = None
        self.echo_gain: float | None = None

    def process_mic(self, block: np.ndarray) -> np.ndarray:
        """int16 mic block (multiple of 10 ms) -> cleaned int16 block.

        WebRTC needs the speaker signal as a steady stream in step with the mic: for every
        10 ms of mic audio it gets 10 ms of what NOVA was playing (silence if nothing).
        Feeding it in bursts with gaps made it lose track (2.8 dB vs 45 dB in testing)."""
        out = np.empty_like(block)
        plain = np.empty_like(block)
        F = self.FRAME
        ref_energy = 0.0
        with self._lock:
            for i in range(0, len(block) - F + 1, F):
                if len(self._ref) >= F:
                    ref = self._ref[:F]
                    self._ref = self._ref[F:]
                else:
                    ref = np.zeros(F, np.float32)
                rf = (np.clip(ref, -1, 1) * 32767).astype(np.int16)
                ref_energy += float(np.sum(rf.astype(np.float32) ** 2))
                self._apm.process_reverse_stream(self._rtc.AudioFrame(rf.tobytes(), MIC_SR, 1, F))
                fr = self._rtc.AudioFrame(block[i:i + F].tobytes(), MIC_SR, 1, F)
                self._apm.process_stream(fr)
                out[i:i + F] = np.frombuffer(bytes(fr.data), np.int16)
                pf = self._rtc.AudioFrame(block[i:i + F].tobytes(), MIC_SR, 1, F)
                self._plain.process_stream(pf)
                plain[i:i + F] = np.frombuffer(bytes(pf.data), np.int16)
        self._track_echo(float(np.sqrt(ref_energy / max(len(block), 1))), rms(block))
        return out if self.echo else plain

    def _track_echo(self, speaker: float, mic: float):
        """How strongly does NOVA's voice come back through the mic? Every second, over the
        last 8 s: find the delay (0-0.6 s: sound card, Bluetooth) at which the mic's loudness
        best follows NOVA's, then the typical ratio of the mic level above the room's noise
        to NOVA's level. Your voice and room noise don't follow NOVA, so they barely count."""
        self._hist.append((speaker, mic))
        self._blocks += 1
        if self._blocks % 10:
            return
        h = np.array(self._hist, np.float32)
        playing = h[:, 0] > 300
        if len(h) < 40 or np.sum(playing) < 25:            # need ~2.5 s of NOVA talking
            return
        spk, mic_l = np.log(h[:, 0] + 30.0), np.log(h[:, 1] + 30.0)
        best, lag_at = -1.0, 0
        for lag in range(0, 7):
            a, b = spk[:len(spk) - lag], mic_l[lag:]
            if np.std(a) > 1e-3 and np.std(b) > 1e-3:
                c = float(np.corrcoef(a, b)[0, 1])
                if c > best:
                    best, lag_at = c, lag
        quiet = h[:, 1][h[:, 0] < 100]
        floor = float(np.percentile(quiet if len(quiet) >= 10 else h[:, 1], 30))
        s_al, m_al = h[:len(h) - lag_at, 0], h[lag_at:, 1]
        sel = s_al > 300
        gain = float(np.median(np.maximum(m_al[sel] - floor, 0.0) / s_al[sel]))
        self.correlation, self.echo_gain = best, gain
        if self.echo and gain < self.GAIN_OFF:
            self.echo = False
            log.info("NOVA's voice barely reaches the mic (echo %.3f, correlation %.2f): headphones? "
                     "Echo canceller bypassed so it can't mute you", gain, best)
        elif not self.echo and gain > self.GAIN_ON and best > 0.45:
            self.echo = True
            log.info("NOVA's voice is echoing into the mic (echo %.3f, correlation %.2f): echo canceller on",
                     gain, best)

    def feed_speaker(self, chunk: np.ndarray):
        """float32 audio at OUT_SR, called as it is sent to the speakers. Queued and handed to
        WebRTC in step with the mic by process_mic()."""
        n_out = int(len(chunk) * MIC_SR / OUT_SR)
        if n_out <= 0:
            return
        x = np.interp(np.linspace(0, len(chunk) - 1, n_out), np.arange(len(chunk)), chunk)
        with self._lock:
            self._ref = np.concatenate([self._ref, x.astype(np.float32)])
            if len(self._ref) > MIC_SR:              # never let the lag grow past 1 s
                self._ref = self._ref[-MIC_SR:]


def make_echo_canceller(enabled: bool, noise_suppression: bool = True) -> EchoCanceller | None:
    if not enabled:
        return None
    try:
        return EchoCanceller(noise_suppression)
    except Exception as e:
        log.warning("echo cancellation unavailable (%s); talking over NOVA is off", e)
        return None


class Mic:
    """Always-open mic stream delivering 100 ms int16 blocks through a queue."""

    def __init__(self, device_name: str = "", aec: EchoCanceller | None = None):
        self.aec = aec
        self.device = find_input_device(device_name)
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=600)
        self.level = 0.0          # 0..1 loudness of the latest block (for the notch)
        self.paused = False       # while True, blocks are dropped (NOVA is speaking)
        self._stream = sd.InputStream(samplerate=MIC_SR, channels=1, dtype="int16",
                                      blocksize=MIC_BLOCK, device=self.device, callback=self._cb)

    @property
    def name(self) -> str:
        idx = self.device if self.device is not None else sd.default.device[0]
        return sd.query_devices(idx)["name"]

    def _cb(self, indata, frames, t, status):
        block = indata[:, 0].copy()
        if self.aec is not None:
            block = self.aec.process_mic(block)
        self.level = min(1.0, rms(block) / 4000)
        if self.paused:
            return
        try:
            self.q.put_nowait(block)
        except queue.Full:
            pass

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        with self.q.mutex:
            self.q.queue.clear()


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) if len(block) else 0.0


class Player:
    """Plays float32 mono audio at OUT_SR. stop() interrupts from any thread."""

    def __init__(self, aec: EchoCanceller | None = None):
        self.aec = aec
        self.level = 0.0
        self.played_s = 0.0       # seconds played since the last reset()
        self.gain = 1.0           # lowered briefly to tell your voice from NOVA's echo
        self._stop = threading.Event()
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()

    def open(self):
        with self._lock:
            if self._stream is None:
                self._stream = sd.OutputStream(samplerate=OUT_SR, channels=1, dtype="float32",
                                               blocksize=0, latency="low")
                self._stream.start()

    def reset(self):
        """Clear a previous stop() before a new answer."""
        self._stop.clear()
        self.played_s = 0.0
        self.gain = 1.0

    def close(self):
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                finally:
                    self._stream = None
        self.level = 0.0

    def stop(self):
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def play(self, audio: np.ndarray) -> bool:
        """Play to the end (blocking). Returns False if interrupted."""
        if self._stream is None:
            self.open()
        chunk = OUT_SR // 25   # 40 ms
        for i in range(0, len(audio), chunk):
            if self._stop.is_set():
                self.level = 0.0
                return False
            part = np.ascontiguousarray(audio[i:i + chunk], dtype=np.float32) * self.gain
            self.level = min(1.0, float(np.sqrt(np.mean(part ** 2))) * 6 / max(self.gain, 0.05))
            if self.aec is not None:
                self.aec.feed_speaker(part)
            self.played_s += len(part) / OUT_SR
            self._stream.write(part.reshape(-1, 1))
        self.level = 0.0
        return True


def decode(data: bytes, sr: int = OUT_SR) -> np.ndarray:
    """Decode MP3/WAV bytes to mono float32 at `sr` (PyAV directly; faster_whisper.decode_audio
    is incompatible with PyAV >= 16)."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
    out = []
    with av.open(io.BytesIO(data)) as c:
        for frame in c.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0


def _tones(freqs: list[float], dur: float = 0.09, gap: float = 0.02, vol: float = 0.25) -> np.ndarray:
    parts = []
    n = int(dur * OUT_SR)
    t = np.arange(n) / OUT_SR
    env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.03)).clip(0, 1)
    for f in freqs:
        tone = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
        parts += [(tone * env * vol).astype(np.float32), np.zeros(int(gap * OUT_SR), np.float32)]
    return np.concatenate(parts)


def _bell(freqs: list[float], step: float = 0.085, length: float = 0.17, vol: float = 0.3) -> np.ndarray:
    """Soft bell-like notes `step` s apart: quick (not clicky) attack, smooth fade. The overtone
    fades fastest, so each note starts clear and settles mellow (no beep)."""
    n = int(length * OUT_SR)
    t = np.arange(n) / OUT_SR
    env = np.minimum(1, t / 0.006) * np.exp(-t / 0.06) * np.minimum(1, (length - t) / 0.03)
    out = np.zeros(int(step * (len(freqs) - 1) * OUT_SR) + n, np.float32)
    for i, f in enumerate(freqs):
        tone = np.sin(2 * np.pi * f * t) + 0.35 * np.exp(-t / 0.03) * np.sin(4 * np.pi * f * t)
        s = int(i * step * OUT_SR)
        out[s:s + n] += (tone * env * vol).astype(np.float32)
    return out


# The listen/sleep chimes were 660->880 Hz beeps ("too pitchy"): now an octave lower and soft.
CHIME_LISTEN = _bell([330, 440])           # rising E4 -> A4: "I'm listening"
CHIME_SLEEP = _bell([440, 330])            # falling: "going back to sleep"
CHIME_ERROR = _tones([300, 300], vol=0.2)  # low double beep
