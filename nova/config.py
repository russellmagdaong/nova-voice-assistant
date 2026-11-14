"""Load config.toml with defaults for anything missing."""
from __future__ import annotations

import copy
import logging
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("nova.config")

DEFAULTS = {
    "wake": {"wait_for_command": 6.0, "end_silence": 1.0, "preroll": 3.0, "max_record": 15.0},
    "conversation": {"enabled": True, "follow_up_timeout": 10.0, "talk_over": True,
                     "echo_cancellation": True, "noise_suppression": True},
    "mic": {"device": ""},
    "speech_to_text": {"model": "base.en", "device": "cuda", "compute_type": "float16", "check_model": "base.en",
                       "vocabulary": ["Claude", "Spotify", "Zen Browser", "File Pilot"]},
    "claude": {"model": "haiku", "path": "", "conversation_timeout": 300, "location": "", "web_search": True},
    "voice": {"name": "kokoro:af_bella", "rate": "+5%", "kokoro_speed": 1.05, "menu": [
        ["kokoro:af_bella", "Bella (Kokoro, US)"],
        ["kokoro:af_heart", "Heart (Kokoro, US)"],
        ["kokoro:af_sarah", "Sarah (Kokoro, US)"],
        ["kokoro:af_nicole", "Nicole (Kokoro, US, soft)"],
        ["kokoro:bf_emma", "Emma (Kokoro, UK)"],
        ["kokoro:am_michael", "Michael (Kokoro, US)"],
        ["kokoro:am_fenrir", "Fenrir (Kokoro, US)"],
        ["kokoro:bm_george", "George (Kokoro, UK)"],
        ["edge:en-US-AvaMultilingualNeural", "Ava (Microsoft, online)"],
        ["edge:en-US-AndrewMultilingualNeural", "Andrew (Microsoft, online)"],
    ], "pronunciations": {}},
    "ui": {"mascot": "cat", "theme": "sunrise", "accent": "", "live_captions": True, "notch": True,
           "hide_in_fullscreen": True},
    "pc": {"enabled": True},
    "reminders": {"enabled": True, "data_dir": ""},
}


def load(path: Path = ROOT / "config.toml") -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            with open(path, "rb") as f:
                user = tomllib.load(f)
            for section, values in user.items():
                if isinstance(values, dict):
                    cfg.setdefault(section, {}).update(values)
        except tomllib.TOMLDecodeError as e:
            log.error("config.toml has an error (%s); using defaults", e)
    return cfg


# What the settings window may change (saved in state.json "settings", applied over config.toml).
SETTABLE = {"claude.location": str, "claude.model": str, "claude.web_search": bool, "pc.enabled": bool,
            "reminders.enabled": bool, "conversation.follow_up_timeout": float, "voice.kokoro_speed": float,
            "ui.live_captions": bool, "ui.hide_in_fullscreen": bool, "ui.theme": str}


def apply_overrides(cfg: dict, settings: dict) -> dict:
    """Settings changed in the settings window win over config.toml (known keys and types only)."""
    for key, value in (settings or {}).items():
        kind = SETTABLE.get(key)
        if kind is None:
            continue
        try:
            value = kind(value)
        except (TypeError, ValueError):
            continue
        if key == "claude.model" and value not in ("haiku", "sonnet"):
            continue
        section, name = key.split(".")
        cfg[section][name] = value
    return cfg
