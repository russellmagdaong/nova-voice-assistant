"""Compare Whisper models for accuracy and speed on accented, fast, noisy speech.

    .venv\\Scripts\\python.exe tools\\stt_benchmark.py [model ...]

Makes test clips with edge-tts in several English accents (sped up, with background noise),
runs them through NOVA's real mic processing (WebRTC noise suppression + high-pass filter), then
transcribes them with each model the way NOVA does. Reports word error rate (WER), time per clip
and video memory. Synthetic speech is cleaner than a real voice, so real-world WERs are higher;
the ranking is what matters. For your own voice, use tools\\stt_compare.py.
"""
from __future__ import annotations

import asyncio
import io
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova  # noqa: E402,F401  (adds the CUDA DLL folders)
from nova.audio import MIC_BLOCK, MIC_SR, EchoCanceller  # noqa: E402
from nova.stt import Transcriber  # noqa: E402

CACHE = ROOT / "samples" / "stt_bench"

# Includes phrases base.en got wrong on the user's real voice (from logs\nova.log).
PHRASES = [
    "Mic check, one two three.",
    "Can you count from ten to one?",
    "What day is it today?",
    "Is this session separate from my desktop?",
    "Open Claude and play my liked songs on Spotify.",
    "Set a reminder to call Mum at five thirty.",
    "Is it not possible to run voice mode in Claude using Nova?",
    "Open Zen Browser and search for cheap flights to Manila.",
    "How much memory does File Pilot use on my computer?",
    "That's too fast, can you say it slower?",
    "Remind me to submit my assignment on Friday morning.",
    "What's the weather going to be like in Auckland this weekend?",
]
VOICES = [
    ("en-PH-JamesNeural", "Filipino English (m)"),
    ("en-PH-RosaNeural", "Filipino English (f)"),
    ("en-IN-PrabhatNeural", "Indian English (m)"),
    ("en-NZ-MitchellNeural", "NZ English (m)"),
    ("en-AU-WilliamNeural", "Australian English (m)"),
    ("en-GB-RyanNeural", "British English (m)"),
]
RATE = "+20%"            # people talk to assistants quickly
SNR_DB = 12              # fan/room noise

NUM = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
NUM.update({"thirty": "30", "forty": "40", "fifty": "50", "to": "to"})


def norm(text: str) -> list[str]:
    t = text.lower().replace("-", " ")
    t = re.sub(r"[^a-z0-9' ]", " ", t)
    words = [NUM.get(w, w) for w in t.split()]
    out: list[str] = []
    for w in words:                     # "1 2 3" == "123", "5 30" == "530" ("5:30")
        if w.isdigit() and out and out[-1].isdigit():
            out[-1] += w
        else:
            out.append(w)
    return [w.replace("'", "") for w in out]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)], len(r)


async def _tts(text: str, voice: str) -> bytes:
    import edge_tts
    b = b""
    async for c in edge_tts.Communicate(text, voice, rate=RATE).stream():
        if c["type"] == "audio":
            b += c["data"]
    return b


def _decode(mp3: bytes) -> np.ndarray:
    import av
    c = av.open(io.BytesIO(mp3))
    r = av.AudioResampler(format="flt", layout="mono", rate=MIC_SR)
    return np.concatenate([f.to_ndarray().ravel() for fr in c.decode(audio=0) for f in r.resample(fr)])


def make_clips() -> list[tuple[str, str, np.ndarray]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    clips = []
    for vi, (voice, _) in enumerate(VOICES):
        for pi, text in enumerate(PHRASES):
            f = CACHE / f"{vi}_{pi}.npy"
            if f.exists():
                clips.append((voice, text, np.load(f)))
                continue
            for attempt in range(4):
                try:
                    speech = _decode(asyncio.run(_tts(text, voice)))
                    break
                except Exception:
                    time.sleep(1.5 * (attempt + 1))
            else:
                raise RuntimeError(f"edge-tts failed for {voice}")
            # Room: 0.4 s of lead-in/out, brown-ish noise at SNR_DB, through the real mic processing.
            pad = np.zeros(int(0.4 * MIC_SR), np.float32)
            x = np.concatenate([pad, speech * 0.5, pad])
            noise = np.cumsum(rng.standard_normal(len(x))).astype(np.float32)
            noise -= np.convolve(noise, np.ones(400) / 400, mode="same")       # remove drift
            sp = np.sqrt(np.mean((speech * 0.5) ** 2))
            noise *= sp / (np.sqrt(np.mean(noise ** 2)) * 10 ** (SNR_DB / 20))
            pcm = (np.clip(x + noise, -1, 1) * 32767).astype(np.int16)
            aec = EchoCanceller(noise_suppression=True)
            n = len(pcm) // MIC_BLOCK * MIC_BLOCK
            out = np.concatenate([aec.process_mic(pcm[i:i + MIC_BLOCK]) for i in range(0, n, MIC_BLOCK)])
            audio = out.astype(np.float32) / 32768.0
            np.save(f, audio)
            clips.append((voice, text, audio))
    return clips


def vram() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip() or 0)


def main():
    models = sys.argv[1:] or ["base.en", "small.en", "distil-large-v3.5", "large-v3-turbo"]
    print("Making test clips (cached after the first run)...", flush=True)
    clips = make_clips()
    print(f"{len(clips)} clips: {len(VOICES)} accents x {len(PHRASES)} phrases, rate {RATE}, noise SNR {SNR_DB} dB\n")
    vocab = ["Claude", "Spotify", "Zen Browser", "File Pilot", "Mum"]
    results = {}
    for name in models:
        v0 = vram()
        t = time.perf_counter()
        model, _, compute = name.partition("@")         # e.g. large-v3-turbo@int8_float16
        stt = Transcriber(model, "cuda", vocab, compute or "float16")
        load = time.perf_counter() - t
        mem = vram() - v0
        errs = words = 0
        per_voice: dict[str, list[int]] = {}
        times, bad = [], []
        for voice, text, audio in clips:
            t = time.perf_counter()
            hyp = stt.transcribe_scored(audio)[0]
            times.append(time.perf_counter() - t)
            e, n = wer(text, hyp)
            errs += e
            words += n
            pv = per_voice.setdefault(voice, [0, 0])
            pv[0] += e
            pv[1] += n
            if e:
                bad.append((voice, text, hyp))
        mem = max(mem, vram() - v0)
        results[name] = (errs / words, float(np.median(times)), max(times), mem, load)
        print(f"== {name}: WER {100 * errs / words:.1f}%  median {np.median(times):.2f}s/clip  "
              f"max {max(times):.2f}s  VRAM +{mem} MiB  load {load:.1f}s")
        print("   per accent: " + ",  ".join(f"{dict(VOICES)[v]} {100 * e / n:.0f}%"
                                          for v, (e, n) in per_voice.items()))
        for voice, text, hyp in bad[:6]:
            print(f"   {voice[:5]}  said: {text}\n          heard: {hyp}")
        del stt
        import gc
        gc.collect()
        time.sleep(1)
    print("\nSummary (lower WER is better):")
    for name, (w, med, mx, mem, load) in results.items():
        print(f"   {name:20s} WER {100 * w:5.1f}%   {med:.2f}s per clip   VRAM +{mem} MiB")


if __name__ == "__main__":
    main()
