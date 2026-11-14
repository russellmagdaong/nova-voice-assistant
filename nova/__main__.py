"""Entry point: python -m nova  (or pythonw -m nova for no console)."""
from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

from PySide6.QtCore import QLockFile, QDir, Qt
from PySide6.QtWidgets import QApplication

from . import config, state, themes, ui
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
    if not lock.tryLock(20000 if "--restarted" in sys.argv else 100):   # (a restart waits for the old one)
        log.info("NOVA is already running")
        return

    cfg = config.load()
    remembered = state.load()
    config.apply_overrides(cfg, remembered.get("settings", {}))     # changed in NOVA settings
    themes.set_current(cfg["ui"]["theme"])
    app.setWindowIcon(ui.nova_icon())
    if remembered.get("voice"):
        cfg["voice"]["name"] = remembered["voice"]
    bridge = ui.Bridge()
    assistant = Assistant(cfg, bridge)
    mascot = remembered.get("mascot", cfg["ui"]["mascot"])
    notch = ui.Notch(bridge, assistant.levels, cfg["ui"]["accent"], mascot, cfg["ui"]["hide_in_fullscreen"])
    notch_shown = remembered.get("notch", cfg["ui"]["notch"])
    tray = ui.Tray(bridge, cfg["voice"]["menu"], cfg["voice"]["name"], notch_shown, mascot)
    notch.menu = tray.menu
    notch.set_shown(notch_shown)
    tray.show()

    def toggle_notch(on: bool):
        notch.set_shown(on)
        state.save(notch=on)
        log.info("notch %s", "shown" if on else "hidden")

    def select_mascot(name: str):
        notch.set_mascot(name)
        tray.set_mascot(name)
        state.save(mascot=name)
        log.info("mascot: %s", name)

    def restart():
        """Start a new NOVA (it waits for this one to let go of the lock), then quit: tray > Restart
        NOVA (to pick up a new build) or the settings' Restart button. NOVA's window comes back as it
        was (normal, maximised or fullscreen) if it was open."""
        log.info("restarting")
        args = [sys.executable, "-m", "nova", "--restarted"]
        w = hud_win[0]
        if w is not None and w.isVisible() and not w.isMinimized():
            args += ["--window", "full" if w.isFullScreen() else "max" if w.isMaximized() else "normal"]
        flags = 0x00000008 | 0x08000000 if os.name == "nt" else 0      # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(args, cwd=str(config.ROOT), creationflags=flags)
        quit_app()

    def set_theme(name: str):
        """Colour theme (NOVA settings > Appearance): the logo, the orb and the orb's tray icon."""
        themes.set_current(name)
        cfg["ui"]["theme"] = themes.current
        app.setWindowIcon(ui.nova_icon())
        tray.set_mascot(tray.mascot)                 # (re-draws the tray icon: the orb's follows the theme)
        notch.update()
        notch.card_win.update()
        log.info("colour theme: %s", themes.current)

    ctx = SimpleNamespace(cfg=cfg, bridge=bridge, assistant=assistant, notch=notch, tray=tray,
                          select_mascot=lambda name: bridge.mascot_selected.emit(name),
                          select_voice=tray.set_voice, current_voice=lambda: tray.voice,
                          set_theme=set_theme, restart=restart)
    hud_win: list = [None]

    def show_hud():
        """NOVA's own window (tray > Open NOVA)."""
        from .hud import HudWindow
        if hud_win[0] is None:
            hud_win[0] = HudWindow(ctx)
        w = hud_win[0]
        if w.isMinimized():
            w.setWindowState(w.windowState() & ~Qt.WindowMinimized)
        w.show()
        w.raise_()
        w.activateWindow()

    def show_settings(page: str = ""):
        """Tray > Settings: NOVA's window, with settings open."""
        show_hud()
        hud_win[0].open_settings(page)

    ctx.show_hud = show_hud
    notch.upcoming = assistant.upcoming
    bridge.hud_requested.connect(show_hud)
    bridge.notch_toggled.connect(toggle_notch)
    bridge.mascot_selected.connect(select_mascot)
    bridge.settings_requested.connect(show_settings)
    bridge.restart_requested.connect(restart)
    if "--window" in sys.argv:                       # restarted with NOVA's window open: open it again
        mode = sys.argv[sys.argv.index("--window") + 1] if sys.argv.index("--window") + 1 < len(sys.argv) else ""
        show_hud()
        if mode == "full":
            hud_win[0].showFullScreen()
        elif mode == "max":
            hud_win[0].showMaximized()

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
