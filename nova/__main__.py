"""Entry point: python -m nova  (or pythonw -m nova for no console)."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from PySide6.QtCore import QLockFile, QDir, Qt
from PySide6.QtWidgets import QApplication

from . import config, state, ui
from .assistant import Assistant

LOG_DIR = config.ROOT / "logs"


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(LOG_DIR / "nova.log", maxBytes=1_000_000,
                                                     backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:                      # pythonw has no console
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("faster_whisper", "httpx", "httpx2", "urllib3", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    setup_logging()
    log = logging.getLogger("nova")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    lock = QLockFile(QDir.tempPath() + "/nova-assistant.lock")
    if not lock.tryLock(100):
        log.info("NOVA is already running")
        return

    cfg = config.load()
    remembered = state.load()
    if remembered.get("voice"):
        cfg["voice"]["name"] = remembered["voice"]
    bridge = ui.Bridge()
    assistant = Assistant(cfg, bridge)
    notch = ui.Notch(bridge, assistant.levels, cfg["ui"]["accent"])
    notch_shown = remembered.get("notch", cfg["ui"]["notch"])
    tray = ui.Tray(bridge, cfg["voice"]["menu"], cfg["voice"]["name"], notch_shown)
    notch.menu = tray.menu
    notch.set_shown(notch_shown)
    tray.show()

    def toggle_notch(on: bool):
        notch.set_shown(on)
        state.save(notch=on)
        log.info("notch %s", "shown" if on else "hidden")

    bridge.notch_toggled.connect(toggle_notch)

    def quit_app():
        assistant.quit()
        tray.hide()
        app.quit()

    bridge.quit_requested.connect(quit_app)
    worker = threading.Thread(target=assistant.run, daemon=True, name="voice-loop")
    worker.start()
    log.info("NOVA started")
    app.exec()
    worker.join(timeout=3)


if __name__ == "__main__":
    main()
