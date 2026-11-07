"""Does Whisper write "Claude" (and other custom words) correctly, with and without a vocabulary hint?

    .venv\\Scripts\\python.exe tools\\vocab_test.py [--models base.en,small.en]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time

import numpy as np
from faster_whisper import WhisperModel

from wake_test import CLIPS, SR, VOICES, decode_audio, slug, with_padding
import edge_tts

SENTENCES = [
    ("Hey Nova, open Claude.", ["Claude"]),
    ("Hey Nova, launch Claude.", ["Claude"]),
    ("Hey Nova, show Claude.", ["Claude"]),
    ("Hey Nova, ask Claude to summarise my notes.", ["Claude"]),
    ("Hey Nova, what can Claude do?", ["Claude"]),
    ("Hey Nova, open Zen Browser.", ["Zen Browser"]),
    ("Hey Nova, open File Pilot.", ["File Pilot"]),
    ("Hey Nova, play Spotify.", ["Spotify"]),
    ("Hey Nova, what are clouds made of?", ["clouds"]),  # must NOT become "Claudes"
]
VOCAB = ["Nova", "Claude", "Spotify", "Zen Browser", "File Pilot"]
SETUPS = {
    "no hint": dict(initial_prompt="Nova is my voice assistant."),
    "prompt": dict(initial_prompt="Nova is my voice assistant. I use " + ", ".join(VOCAB[1:]) + "."),
    "hotwords": dict(initial_prompt="Nova is my voice assistant.", hotwords=" ".join(VOCAB)),
}


async def make():
    sem = asyncio.Semaphore(3)
    items = []

    async def gen(text, words, voice):
        path = CLIPS / f"vocab_{voice}_{slug(text)}.mp3"
        if not (path.exists() and path.stat().st_size):
            async with sem:
                for a in range(6):
                    try:
                        await edge_tts.Communicate(text, voice).save(str(path))
                        break
                    except Exception:
                        path.unlink(missing_ok=True)
                        await asyncio.sleep(2 * (a + 1))
        items.append(dict(text=text, words=words, voice=voice, path=path))

    CLIPS.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(*(gen(t, w, v) for t, w in SENTENCES for v in VOICES))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="base.en,small.en")
    args = ap.parse_args()
    items = asyncio.run(make())
    for it in items:
        it["audio"] = with_padding(decode_audio(str(it["path"])))

    for name in args.models.split(","):
        wm = WhisperModel(name, device="cpu", compute_type="int8")
        wm.transcribe(np.zeros(SR, np.float32), language="en")
        for sname, opts in SETUPS.items():
            ok = 0
            wrong = []
            times = []
            for it in items:
                t = time.perf_counter()
                segs, _ = wm.transcribe(it["audio"], language="en", beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False, **opts)
                text = " ".join(s.text for s in segs).strip()
                times.append(time.perf_counter() - t)
                good = all(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in it["words"])
                # Exact case for Claude so "cloud" / "Clyde" etc. count as wrong.
                good = good and all(w in text for w in it["words"] if w == "Claude")
                if good:
                    ok += 1
                else:
                    wrong.append(f"{it['text']!r} -> {text!r}")
            print(f"\n[{name} | {sname}] correct {ok}/{len(items)}  (median {np.median(times):.2f}s)")
            for w in sorted(set(wrong))[:10]:
                print("   ", w)


if __name__ == "__main__":
    main()
