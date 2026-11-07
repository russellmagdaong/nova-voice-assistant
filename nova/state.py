"""Small remembered settings changed from the tray (e.g. the chosen voice). Stored in state.json
so config.toml (with its comments) is never rewritten."""
from __future__ import annotations

import json
import logging
import threading

from .config import ROOT

PATH = ROOT / "state.json"
log = logging.getLogger("nova.state")
_lock = threading.Lock()


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("state.json unreadable (%s); ignoring it", e)
        return {}


def save(**values):
    with _lock:
        data = load()
        data.update(values)
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
