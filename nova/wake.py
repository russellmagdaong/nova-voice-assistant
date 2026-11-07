"""Wake word: Vosk prefilter (cheap, offline, always on) + the rule Whisper's text must pass.

Phase 0 testing: the decoy grammar catches every real "Hey Nova" but also fires on about a
third of near-misses ("Hey Noah", "Hey, no way"). Whisper then rejects those: 0/129 let through.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from vosk import KaldiRecognizer, Model, SetLogLevel

from .audio import MIC_SR

ROOT = Path(__file__).resolve().parent.parent
VOSK_MODEL = ROOT / "models" / "vosk-model-small-en-us-0.15"

# Decoy words give near-misses somewhere else to go.
GRAMMAR = ["hey nova", "hey", "no", "nova", "over", "know", "noah", "nora",
           "bossa", "casa", "super", "scotia", "nobody", "way", "[unk]"]

# Whisper's spellings of "Hey Nova" seen in testing: Hey Nova, He Nova, Hej Nava, Pay Nova,
# Himova, Pinova, and "Inova," with a comma (without one it's usually the car brand Innova).
WAKE_RE = re.compile(
    r"^\W*(?:(?:hey|hi|hay|he|hej|pay|a|eh)\W+n[oa]v[ao]h?|(?:hi|pi)[nm]ova|i[nm]ova(?=\s*,))\b\W*", re.IGNORECASE)


class WakeDetector:
    def __init__(self, model_path: Path = VOSK_MODEL):
        if not model_path.exists():
            raise FileNotFoundError(f"Vosk model missing at {model_path}. Run setup.ps1.")
        SetLogLevel(-1)
        self.model = Model(str(model_path))
        self.reset()

    def reset(self):
        self._rec = KaldiRecognizer(self.model, MIC_SR, json.dumps(GRAMMAR))

    def feed(self, block: np.ndarray) -> bool:
        """Feed one int16 block; True when 'hey nova' is heard."""
        if self._rec.AcceptWaveform(block.tobytes()):
            text = json.loads(self._rec.Result()).get("text", "")
        else:
            text = json.loads(self._rec.PartialResult()).get("partial", "")
        return "hey nova" in text


def split_wake(transcript: str, lax: bool = False) -> tuple[bool, str]:
    """(is a real wake-up, command text after the wake phrase).

    lax=True is used to confirm a Vosk wake-up. The user's own "Hey Nova" often comes out of
    Whisper as "And over", "Hang over", "Hain over", "Clean over" or "And Nova", so accept a
    first or second word ending in nova/over, or a first two words that sound like "hey nova".
    The user prefers occasional false wake-ups (they mute NOVA when not needed) to misses.
    """
    m = WAKE_RE.match(transcript)
    if m:
        return True, transcript[m.end():].strip()
    if not lax:
        return False, ""
    words = [(w.group().lower(), w.end()) for w in re.finditer(r"[A-Za-z']+", transcript)][:3]
    for word, end in words[:2]:
        if re.search(r"(nova|nava|novah|nover|over)$", word):
            return True, transcript[end:].lstrip(" ,.!?;:-")
    if len(words) >= 2 and SequenceMatcher(None, words[0][0] + words[1][0], "heynova").ratio() >= 0.6:
        return True, transcript[words[1][1]:].lstrip(" ,.!?;:-")
    return False, ""
