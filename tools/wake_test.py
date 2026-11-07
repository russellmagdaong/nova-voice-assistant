"""Phase 0: offline test of "Hey Nova" wake-word detection.

1. Generates speech clips with edge-tts (cached in tools/clips/).
2. Runs each clip (clean and with background noise) through Vosk, fed in
   100 ms chunks like a live mic, and records whether / when it fires.
3. For clips where Vosk fires, runs faster-whisper to confirm the wake phrase
   and strip it from the command.

Usage:  .venv\\Scripts\\python.exe tools\\wake_test.py [--whisper base.en,small.en]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import edge_tts
import numpy as np
import av
from faster_whisper import WhisperModel
from vosk import KaldiRecognizer, Model, SetLogLevel

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "tools" / "clips"
VOSK_MODEL = ROOT / "models" / "vosk-model-small-en-us-0.15"
SR = 16000

VOICES = [
    "en-US-AvaMultilingualNeural",
    "en-US-AndrewMultilingualNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-AU-NatashaNeural",
]
RATES = ["+0%", "+15%"]

# (text, expected command after the wake phrase or None for "should not trigger")
POSITIVES = [
    ("Hey Nova, what's the capital of France?", "what's the capital of france"),
    ("Hey Nova.", ""),
    ("Hey Nova, open Spotify.", "open spotify"),
    ("Hey Nova, set a timer for ten minutes.", "set a timer for ten minutes"),
    ("Hey Nova, what's the weather like today?", "what's the weather like today"),
    ("Hey Nova, remind me to call Mum at five.", "remind me to call mum at five"),
]
NEGATIVES = [
    "What's the capital of France?",
    "I think we should order pizza tonight.",
    "Can you send me that file before the meeting?",
    "The weather is supposed to be nice tomorrow.",
    "Let me know when you're ready to go.",
    "I love listening to bossa nova on Sunday mornings.",
    "He was a real Casanova back in the day.",
    "We drove all the way to Nova Scotia last summer.",
    "The supernova was visible for weeks.",
    "Hey, no way, that's amazing!",
    "Hey, I know what you mean.",
    "Hey Noah, come over here.",
    "Hey Nora, how was your weekend?",
    "Hey, nobody told me about that.",
    "Hey, over here!",
    "Innova is a popular car in some countries.",
]

GRAMMARS = {
    # No grammar: full vocabulary + language model, so "noah", "nora", "no way" have real alternatives.
    "full": None,
    # Only the wake phrase; everything else must map to [unk].
    "minimal": ["hey nova", "[unk]"],
    # Decoy words give near-misses somewhere else to go.
    "decoys": ["hey nova", "hey", "no", "nova", "over", "know", "noah", "nora",
               "bossa", "casa", "super", "scotia", "nobody", "way", "[unk]"],
}

# Whisper's spellings of "Hey Nova" seen in testing: Hey Nova, He Nova, Hej Nava, Himova, Pinova. ("Inova" is excluded: Whisper writes the car brand Innova that way.)
WAKE_RE = re.compile(
    r"^\W*(?:(?:hey|hi|hay|he|hej|pay|a|eh)\W+n[oa]v[ao]h?|(?:hi|pi)[nm]ova)\b\W*", re.IGNORECASE)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


async def make_clips() -> list[dict]:
    CLIPS.mkdir(parents=True, exist_ok=True)
    items = []
    for voice in VOICES:
        for rate in RATES:
            for text, cmd in POSITIVES:
                items.append(dict(text=text, cmd=cmd, positive=True, voice=voice, rate=rate))
            for text in NEGATIVES:
                items.append(dict(text=text, cmd=None, positive=False, voice=voice, rate=rate))
    sem = asyncio.Semaphore(3)

    async def gen(it):
        path = CLIPS / f"{it['voice']}_{it['rate'].replace('%', '').replace('+', 'p')}_{slug(it['text'])}.mp3"
        it["path"] = path
        if path.exists() and path.stat().st_size > 0:
            return
        async with sem:
            for attempt in range(6):
                try:
                    await edge_tts.Communicate(it["text"], it["voice"], rate=it["rate"]).save(str(path))
                    return
                except Exception:
                    path.unlink(missing_ok=True)
                    if attempt == 5:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))

    await asyncio.gather(*(gen(it) for it in items))
    return items


def decode_audio(path: str, sampling_rate: int = SR) -> np.ndarray:
    """Decode any audio file to mono float32 at the given rate (PyAV directly;
    faster_whisper.decode_audio is incompatible with PyAV >= 16)."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    out = []
    with av.open(path) as c:
        for frame in c.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) / 32768.0


def with_padding(audio: np.ndarray, before=0.8, after=1.5) -> np.ndarray:
    return np.concatenate([np.zeros(int(before * SR), np.float32), audio, np.zeros(int(after * SR), np.float32)])


def add_noise(audio: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    # Pink-ish noise (white noise with a simple low-pass) at the given SNR.
    white = rng.standard_normal(len(audio)).astype(np.float32)
    pink = np.convolve(white, np.ones(8, np.float32) / 8, mode="same")
    sig_p = np.mean(audio[np.abs(audio) > 1e-3] ** 2) if np.any(np.abs(audio) > 1e-3) else 1e-6
    noise_p = np.mean(pink ** 2)
    pink *= np.sqrt(sig_p / (noise_p * 10 ** (snr_db / 10)))
    return np.clip(audio + pink, -1, 1)


def vosk_detect(model: Model, grammar: list[str] | None, audio: np.ndarray) -> float | None:
    """Return the time (s into the clip) when 'hey nova' first appears, else None."""
    rec = KaldiRecognizer(model, SR) if grammar is None else KaldiRecognizer(model, SR, json.dumps(grammar))
    pcm = (audio * 32767).astype(np.int16).tobytes()
    chunk = int(0.1 * SR) * 2
    for i in range(0, len(pcm), chunk):
        if rec.AcceptWaveform(pcm[i:i + chunk]):
            text = json.loads(rec.Result()).get("text", "")
        else:
            text = json.loads(rec.PartialResult()).get("partial", "")
        if "hey nova" in text:
            return (i + chunk) / 2 / SR
    if "hey nova" in json.loads(rec.FinalResult()).get("text", ""):
        return len(audio) / SR
    return None


NUMS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
        "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"}


def normalise(s: str) -> str:
    s = re.sub(r"[^a-z0-9' ]+", "", s.lower()).strip()
    return " ".join(NUMS.get(w, w) for w in s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default="base.en,small.en")
    ap.add_argument("--prompts", default="none|Nova is my voice assistant.",
                    help="'|'-separated initial prompts; 'none' = no prompt")
    ap.add_argument("--prefilter", default="decoys", choices=list(GRAMMARS))
    args = ap.parse_args()

    SetLogLevel(-1)
    t = time.perf_counter()
    items = asyncio.run(make_clips())
    print(f"{len(items)} clips ready ({time.perf_counter() - t:.1f}s)")

    rng = np.random.default_rng(0)
    for it in items:
        raw = decode_audio(str(it["path"]), sampling_rate=SR)
        it["speech_len"] = len(raw) / SR
        it["clean"] = with_padding(raw)
        it["noisy"] = add_noise(it["clean"], 10, rng)

    model = Model(str(VOSK_MODEL))
    print("\n=== Vosk stage ===")
    best = None
    for gname, grammar in GRAMMARS.items():
        for cond in ("clean", "noisy"):
            tp = fp = 0
            pos = sum(it["positive"] for it in items)
            neg = len(items) - pos
            misses, falses, lat = [], [], []
            for it in items:
                hit = vosk_detect(model, grammar, it[cond])
                it[f"vosk_{gname}_{cond}"] = hit
                if it["positive"]:
                    if hit is not None:
                        tp += 1
                    else:
                        misses.append(it)
                elif hit is not None:
                    fp += 1
                    falses.append(it)
            print(f"[{gname:7s} {cond:5s}] caught {tp}/{pos} ({100 * tp / pos:.0f}%), "
                  f"false triggers {fp}/{neg} ({100 * fp / neg:.0f}%)")
            for it in misses[:6]:
                print(f"     miss : {it['voice']} {it['rate']} | {it['text']}")
            for it in falses[:10]:
                print(f"     false: {it['voice']} {it['rate']} | {it['text']}")
            score = tp - 2 * fp
            if cond == "noisy" and (best is None or score > best[0]):
                best = (score, gname)

    # The prefilter must not miss real wake-ups; Whisper removes its false triggers.
    gname = args.prefilter
    print(f"\nUsing Vosk prefilter '{gname}' for the Whisper stage.")

    prompts = [None if p == "none" else p for p in args.prompts.split("|")]
    for wname in [w for w in args.whisper.split(",") if w]:
      t = time.perf_counter()
      wm = WhisperModel(wname, device="cpu", compute_type="int8")
      print(f"\n[{wname} loaded in {time.perf_counter() - t:.1f}s]")
      wm.transcribe(np.zeros(SR, np.float32), language="en")  # warm-up
      for prompt in prompts:
        print(f"\n=== Whisper confirm: {wname} (CPU int8), prompt={prompt!r} ===")
        shown = 0
        stats = dict(confirmed=0, rejected_false=0, missed=0, leaked=0, cmd_ok=0, cmd_total=0)
        times, rtf = [], []
        for cond in ("clean", "noisy"):
            for it in items:
                if it[f"vosk_{gname}_{cond}"] is None:
                    continue
                audio = it[cond]
                t = time.perf_counter()
                segs, _ = wm.transcribe(audio, language="en", beam_size=1,
                                        initial_prompt=prompt, vad_filter=True,
                                        condition_on_previous_text=False)
                text = " ".join(s.text for s in segs).strip()
                dt = time.perf_counter() - t
                times.append(dt)
                rtf.append(dt / it["speech_len"])
                m = WAKE_RE.match(text)
                if it["positive"]:
                    if m:
                        stats["confirmed"] += 1
                        stats["cmd_total"] += 1
                        cmd = normalise(text[m.end():])
                        if cmd == normalise(it["cmd"]):
                            stats["cmd_ok"] += 1
                        else:
                            shown += 1
                            if shown <= 12: print(f"   cmd  : want '{it['cmd']}' got '{cmd}'  ({it['voice']}, {cond})")
                    else:
                        stats["missed"] += 1
                        shown += 1
                        if shown <= 12: print(f"   miss : '{text}'  ({it['voice']}, {cond})")
                else:
                    if m:
                        stats["leaked"] += 1
                        shown += 1
                        if shown <= 12: print(f"   LEAK : '{text}'  ({it['voice']}, {cond})")
                    else:
                        stats["rejected_false"] += 1
        print(f"positives confirmed {stats['confirmed']}, wrongly rejected {stats['missed']}; "
              f"Vosk false triggers rejected {stats['rejected_false']}, let through {stats['leaked']}")
        print(f"command text exact {stats['cmd_ok']}/{stats['cmd_total']}")
        if times:
            print(f"transcribe time: median {np.median(times):.2f}s, max {max(times):.2f}s "
                  f"(clips include ~2.3s padding)")


if __name__ == "__main__":
    main()
