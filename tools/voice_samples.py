"""Make voice samples to compare edge-tts (current) with Kokoro (local), and time them.

    .venv\\Scripts\\python.exe tools\\voice_samples.py
Writes samples\\*.wav.
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from nova.tts import Voice  # noqa: E402

OUT = ROOT / "samples"
LINES = [
    "Hey! The capital of France is Paris.",
    "Berlin has more people, with about three point seven million residents, compared with around two point one million in Paris.",
    "Sure. I've set a timer for ten minutes, and I'll let you know when it's done.",
    "Hmm, I'm not totally sure about that one, but I think it was around nineteen sixty-nine.",
]
KOKORO_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "bf_emma", "am_michael", "am_fenrir", "bm_george"]


def save(path: Path, audio: np.ndarray, sr: int):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def gap(sr, s=0.6):
    return np.zeros(int(sr * s), np.float32)


def main():
    OUT.mkdir(exist_ok=True)
    print("Current voice (edge-tts, Ava):")
    v = Voice("en-US-AvaMultilingualNeural", "+5%")
    v.synth("Ready.")
    parts, times = [], []
    for line in LINES:
        t = time.perf_counter()
        a = v.synth(line)
        times.append(time.perf_counter() - t)
        parts += [a, gap(24000)]
    save(OUT / "0_current_edge_ava.wav", np.concatenate(parts), 24000)
    print(f"   per line: {' '.join(f'{x:.2f}s' for x in times)}")

    from kokoro_onnx import Kokoro
    for model in ("kokoro-v1.0.onnx",):
        t = time.perf_counter()
        k = Kokoro(str(ROOT / "models" / "kokoro" / model), str(ROOT / "models" / "kokoro" / "voices-v1.0.bin"))
        k.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")       # warm-up
        print(f"\nKokoro {model} loaded + warmed in {time.perf_counter() - t:.1f}s")
        voices = KOKORO_VOICES if model == "kokoro-v1.0.onnx" else ["af_heart"]
        for vn in voices:
            parts, times, dur = [], [], 0.0
            for line in LINES:
                t = time.perf_counter()
                a, sr = k.create(line, voice=vn, speed=1.05, lang="en-gb" if vn.startswith("b") else "en-us")
                times.append(time.perf_counter() - t)
                dur += len(a) / sr
                parts += [a.astype(np.float32), gap(sr)]
            tag = "int8_" if "int8" in model else ""
            save(OUT / f"kokoro_{tag}{vn}.wav", np.concatenate(parts), sr)
            print(f"   {vn:10s} per line: {' '.join(f'{x:.2f}s' for x in times)}   "
                  f"(made {dur:.1f}s of speech in {sum(times):.1f}s)")
    print(f"\nSamples in {OUT}")


if __name__ == "__main__":
    main()
