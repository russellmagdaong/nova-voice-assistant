"""Compare speech-to-text models on YOUR voice.

    .venv\\Scripts\\python.exe tools\\stt_compare.py              # read the phrases, then compare
    .venv\\Scripts\\python.exe tools\\stt_compare.py --reuse      # compare again on the saved recordings
    .venv\\Scripts\\python.exe tools\\stt_compare.py --models base.en large-v3-turbo@int8_float16

Shows a phrase, you read it at your normal speed (it stops after a second of quiet). The audio
goes through the same mic processing NOVA uses (noise suppression, echo cancellation), is saved
to samples\\my_voice\\ (stays on this PC; delete it any time), and each model transcribes it.
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova  # noqa: E402,F401  (adds the CUDA DLL folders)
from nova import config  # noqa: E402
from nova.audio import MIC_SR, Mic, make_echo_canceller, rms  # noqa: E402
from nova.stt import Transcriber  # noqa: E402
from stt_benchmark import PHRASES, wer  # noqa: E402

OUT = ROOT / "samples" / "my_voice"
MORE = ["Hey Nova, what time is it in Tokyo right now?",
        "Give me three facts about the moon.",
        "Can you set a test schedule in my Notion?"]


def record(mic: Mic, noise: float) -> np.ndarray | None:
    """Record one phrase: wait up to 8 s for speech, stop after 1 s of quiet."""
    mic.flush()
    blocks, started, silent, t0 = [], False, 0.0, time.monotonic()
    threshold = max(noise * 2.5, 300)
    while True:
        b = mic.read(timeout=0.5)
        if b is None:
            continue
        blocks.append(b)
        if rms(b) >= threshold:
            started, silent = True, 0.0
        else:
            silent += 0.1
        if started and silent >= 1.0:
            return np.concatenate(blocks[-int(30 / 0.1):]).astype(np.float32) / 32768
        if not started and time.monotonic() - t0 > 8:
            return None
        if not started:
            blocks = blocks[-10:]                    # keep 1 s before you start


def save(path: Path, audio: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MIC_SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="skip recording; use samples\\my_voice")
    ap.add_argument("--models", nargs="+",
                    default=["base.en", "small.en", "large-v3-turbo@int8_float16"])
    args = ap.parse_args()
    cfg = config.load()
    phrases = PHRASES + MORE
    OUT.mkdir(parents=True, exist_ok=True)

    if not args.reuse:
        conv = cfg["conversation"]
        aec = make_echo_canceller(conv["echo_cancellation"] and conv["talk_over"], conv["noise_suppression"])
        mic = Mic(cfg["mic"]["device"], aec=aec)
        mic.start()
        print(f"Mic: {mic.name}. Quit NOVA first if it's running (it would hear you too).")
        print("Measuring room noise, stay quiet for 2 s...")
        time.sleep(0.3)
        mic.flush()
        levels = [rms(b) for b in (mic.read(timeout=1) for _ in range(20)) if b is not None]
        noise = float(np.median(levels)) if levels else 200.0
        print(f"Read each phrase at your normal speed. ({len(phrases)} phrases)\n")
        for i, text in enumerate(phrases):
            print(f"[{i + 1}/{len(phrases)}]  {text}")
            audio = record(mic, noise)
            if audio is None:
                print("   (didn't hear anything, skipping)")
                continue
            save(OUT / f"{i:02d}.wav", audio)
            print(f"   got {len(audio) / MIC_SR:.1f}s")
        mic.stop()

    clips = [(phrases[int(f.stem)], load(f)) for f in sorted(OUT.glob("*.wav")) if int(f.stem) < len(phrases)]
    if not clips:
        sys.exit("No recordings in samples\\my_voice yet. Run without --reuse first.")
    vocab = cfg["speech_to_text"]["vocabulary"]
    summary = []
    for name in args.models:
        model, _, compute = name.partition("@")
        print(f"\n== {name} (loading...)", flush=True)
        stt = Transcriber(model, "cuda", vocab, compute or "float16")
        errs = words = 0
        times = []
        for text, audio in clips:
            t = time.perf_counter()
            hyp = stt.transcribe_scored(audio)[0]
            times.append(time.perf_counter() - t)
            e, n = wer(text, hyp)
            errs, words = errs + e, words + n
            mark = "  " if e == 0 else "XX"
            print(f"   {mark} {hyp}")
        summary.append((name, errs / max(words, 1), float(np.median(times))))
        del stt
        gc.collect()
    print("\nOn your voice (lower is better):")
    for name, w, t in summary:
        print(f"   {name:30s} {100 * w:5.1f}% of words wrong   {t:.2f}s per phrase")


if __name__ == "__main__":
    main()
