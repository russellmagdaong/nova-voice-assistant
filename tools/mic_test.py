"""Live mic test for "Hey Nova" detection. Prints what happens; no Claude, no speech output.

    .venv\\Scripts\\python.exe tools\\mic_test.py                 # default mic
    .venv\\Scripts\\python.exe tools\\mic_test.py --list          # show input devices
    .venv\\Scripts\\python.exe tools\\mic_test.py --mic PD100X    # pick a mic by part of its name
    .venv\\Scripts\\python.exe tools\\mic_test.py --grammar minimal --verbose

Ctrl+C to quit.
"""
from __future__ import annotations

import argparse
import collections
import json
import queue
import re
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from vosk import KaldiRecognizer, Model, SetLogLevel

ROOT = Path(__file__).resolve().parent.parent
VOSK_MODEL = ROOT / "models" / "vosk-model-small-en-us-0.15"
SR = 16000
BLOCK = 1600  # 100 ms
PREROLL_S = 3.0
MAX_RECORD_S = 15.0

GRAMMARS = {
    "full": None,
    "minimal": ["hey nova", "[unk]"],
    "decoys": ["hey nova", "hey", "no", "nova", "over", "know", "noah", "nora",
               "bossa", "casa", "super", "scotia", "nobody", "way", "[unk]"],
}
# Whisper's spellings of "Hey Nova" seen in testing: Hey Nova, He Nova, Hej Nava, Himova, Pinova. ("Inova" is excluded: Whisper writes the car brand Innova that way.)
WAKE_RE = re.compile(
    r"^\W*(?:(?:hey|hi|hay|he|hej|pay|a|eh)\W+n[oa]v[ao]h?|(?:hi|pi)[nm]ova)\b\W*", re.IGNORECASE)


def find_device(name: str | None):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    sys.exit(f"No input device matching '{name}'. Use --list.")


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list input devices and exit")
    ap.add_argument("--mic", help="part of the input device name")
    ap.add_argument("--grammar", choices=GRAMMARS, default="full")
    ap.add_argument("--whisper", default="base.en")
    ap.add_argument("--verbose", action="store_true", help="show what Vosk hears")
    ap.add_argument("--wait", type=float, default=6.0,
                    help="seconds to wait for you to start the command after 'Hey Nova'")
    ap.add_argument("--end-silence", type=float, default=1.0,
                    help="seconds of quiet that end the command once you've started")
    ap.add_argument("--vocab", default="Claude, Spotify, Zen Browser, File Pilot",
                    help="comma-separated words Whisper should expect (names, apps)")
    args = ap.parse_args()
    # Naming the words in Whisper's prompt fixed "Claude" in testing (cloud/clod/quad -> Claude).
    prompt = f"Nova is my voice assistant. I use {args.vocab}."

    if args.list:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                star = "*" if i == sd.default.device[0] else " "
                print(f"{star} [{i}] {d['name']}  ({sd.query_hostapis(d['hostapi'])['name']})")
        return

    SetLogLevel(-1)
    print("Loading models...")
    vosk = Model(str(VOSK_MODEL))
    grammar = GRAMMARS[args.grammar]

    def new_rec():
        return KaldiRecognizer(vosk, SR) if grammar is None else KaldiRecognizer(vosk, SR, json.dumps(grammar))

    wm = WhisperModel(args.whisper, device="cpu", compute_type="int8")
    wm.transcribe(np.zeros(SR, np.float32), language="en")

    q: queue.Queue[np.ndarray] = queue.Queue()
    dev = find_device(args.mic)

    def cb(indata, frames, t, status):
        q.put(indata[:, 0].copy())

    ring = collections.deque(maxlen=int(PREROLL_S * SR / BLOCK))
    noise = 200.0
    rec = new_rec()
    name = sd.query_devices(dev if dev is not None else sd.default.device[0])["name"]
    print(f"Mic: {name} | wake filter: {args.grammar} | whisper: {args.whisper}")
    print('Listening. Say "Hey Nova, ..." (Ctrl+C to quit)\n')

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK, device=dev, callback=cb):
        last_partial = ""
        while True:
            block = q.get()
            ring.append(block)
            level = rms(block)
            noise = 0.98 * noise + 0.02 * min(level, noise * 3)  # slow background-noise estimate

            if rec.AcceptWaveform(block.tobytes()):
                heard = json.loads(rec.Result()).get("text", "")
                if args.verbose and heard:
                    print(f"  vosk: {heard}")
                last_partial = ""
            else:
                heard = json.loads(rec.PartialResult()).get("partial", "")
                if args.verbose and heard and heard != last_partial:
                    print(f"  ...  {heard}", end="\r")
                last_partial = heard
            if "hey nova" not in heard:
                continue

            # Wake candidate: keep the pre-roll, then record in two phases (like Google):
            #   1. waiting: up to --wait seconds for you to start the command
            #   2. command: once you're talking, stop after --end-silence seconds of quiet
            t_wake = time.perf_counter()
            print(f"\n[wake candidate]  noise floor {noise:.0f}, listening for command...")
            audio = list(ring)
            threshold = max(noise * 2.5, 300)
            after = 0.0          # seconds recorded since the wake candidate
            loud_run = 0.0       # current stretch of speech
            silent = 0.0         # current stretch of quiet
            started = False
            while after < MAX_RECORD_S:
                b = q.get()
                audio.append(b)
                after += BLOCK / SR
                if rms(b) >= threshold:
                    loud_run += BLOCK / SR
                    silent = 0.0
                    # Ignore the tail of "nova" right after detection; need 0.3 s of speech to count.
                    if not started and after > 0.2 and loud_run >= 0.3:
                        started = True
                        print(f"  command started {after - loud_run:.1f}s after wake")
                else:
                    silent += BLOCK / SR
                    if silent >= 0.15:
                        loud_run = 0.0
                if started and silent >= args.end_silence:
                    break
                if not started and after >= args.wait:
                    print(f"  no command within {args.wait:.0f}s")
                    break
            t_end = time.perf_counter()

            pcm = np.concatenate(audio).astype(np.float32) / 32768.0
            segs, _ = wm.transcribe(pcm, language="en", beam_size=1, vad_filter=True,
                                    initial_prompt=prompt,
                                    condition_on_previous_text=False)
            text = " ".join(s.text for s in segs).strip()
            t_stt = time.perf_counter()
            m = WAKE_RE.match(text)
            if m:
                cmd = text[m.end():].strip()
                verdict = f"CONFIRMED  command: '{cmd}'" if cmd else "CONFIRMED  (no command: would chime and listen)"
            else:
                verdict = "rejected as false alarm"
            print(f"  whisper: '{text}'")
            print(f"  {verdict}")
            print(f"  recorded {t_end - t_wake:.1f}s after wake, transcribed in {t_stt - t_end:.2f}s\n")

            rec = new_rec()
            ring.clear()
            with q.mutex:
                q.queue.clear()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
