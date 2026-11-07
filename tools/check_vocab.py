"""Check which words the Vosk model knows. Vosk logs a warning for each grammar word missing from its vocabulary."""
import json, sys
from vosk import Model, KaldiRecognizer, SetLogLevel

SetLogLevel(0)
words = sys.argv[1:] or ["hey", "nova", "novah", "claude"]
model = Model("models/vosk-model-small-en-us-0.15")
KaldiRecognizer(model, 16000, json.dumps(words + ["[unk]"]))
