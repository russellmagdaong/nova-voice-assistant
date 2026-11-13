"""Tray > Apps NOVA can open: tick the apps NOVA may open by voice ("Hey Nova, open Spotify").

The list comes from the Start menu (Get-StartApps). Ticked apps are saved in state.json ("apps")
with their Start-menu ID; that ID is all NOVA ever launches. Terminals, system and admin tools and
uninstallers are shown greyed out and can't be ticked (pc_tools.is_blocked).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from . import pc_tools, state

log = logging.getLogger("nova.apps")


class AppsWindow(QDialog):
    found = Signal(list)          # apps discovered on a worker thread

    def __init__(self, load: Callable[[], dict] = state.load, save: Callable[..., None] = state.save,
                 discover: Callable[[], list[dict]] = pc_tools.discover_apps):
        super().__init__(None)
        self._load, self._save, self._discover = load, save, discover
        self.setWindowTitle("Apps NOVA can open")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        intro = QLabel("Tick the apps NOVA may open when you ask, for example “Hey Nova, open Spotify”. "
                       "Under “Also called”, add other names you use for an app, separated by commas "
                       "(for example “browser” for Zen). Terminals, system tools and uninstallers can't be "
                       "allowed. NOVA can only open apps: it never closes them or changes anything in them.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.search = QLineEdit(placeholderText="Search apps")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["App", "Also called"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                   | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.status = QLabel("Finding your apps…")
        row.addWidget(self.status, 1)
        self.refresh = QPushButton("Refresh list")
        self.refresh.clicked.connect(self._start_discovery)
        row.addWidget(self.refresh)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.found.connect(self._populate)
        self._populate([])                                 # what's allowed now, while the scan runs
        self._start_discovery()

    def _start_discovery(self):
        self.refresh.setEnabled(False)
        self.status.setText("Finding your apps…")

        def work():
            try:
                apps = self._discover()
            except Exception as e:  # noqa: BLE001
                log.warning("couldn't list apps: %s", e)
                apps = []
            self.found.emit(apps)
        threading.Thread(target=work, daemon=True, name="apps-scan").start()

    def _current(self) -> dict[str, dict]:
        """What's ticked in the table now (or saved, before the table is filled)."""
        if self.table.rowCount() == 0:
            return {a["id"]: a for a in self._load().get("apps", []) if a.get("id")}
        out = {}
        for r in range(self.table.rowCount()):
            it, al = self.table.item(r, 0), self.table.item(r, 1)
            if it.checkState() == Qt.Checked:
                aliases = [a.strip() for a in (al.text() if al else "").split(",") if a.strip()]
                out[it.data(Qt.UserRole)] = {"name": it.text(), "id": it.data(Qt.UserRole), "aliases": aliases}
        return out

    def _populate(self, discovered: list[dict]):
        allowed = self._current()
        apps = {a["id"]: a for a in discovered}
        for app_id, a in allowed.items():                 # keep allowed apps even if not found now
            apps.setdefault(app_id, a)
        rows = sorted(apps.values(), key=lambda a: (a["id"] not in allowed, a["name"].lower()))
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, a in enumerate(rows):
            name = QTableWidgetItem(a["name"])
            name.setData(Qt.UserRole, a["id"])
            name.setToolTip(a["id"])
            alias = QTableWidgetItem(", ".join(allowed.get(a["id"], {}).get("aliases", [])))
            if pc_tools.is_blocked(a["name"]):
                name.setFlags(Qt.ItemIsEnabled)
                name.setCheckState(Qt.Unchecked)
                name.setToolTip("A system tool, terminal or uninstaller: NOVA never opens these.")
                name.setForeground(self.palette().placeholderText())
                alias.setFlags(Qt.NoItemFlags)
            else:
                name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                name.setCheckState(Qt.Checked if a["id"] in allowed else Qt.Unchecked)
                alias.setFlags(Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, alias)
        self._filter(self.search.text())
        if discovered:
            self.status.setText(f"{len(discovered)} apps found; {len(allowed)} allowed.")
            self.refresh.setEnabled(True)
        elif self.refresh.isEnabled():
            self.status.setText("Couldn't list your apps. Try Refresh list.")

    def _filter(self, text: str):
        t = text.strip().lower()
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, bool(t) and t not in self.table.item(r, 0).text().lower())

    def _save_and_close(self):
        apps = list(self._current().values())
        self._save(apps=apps)
        log.info("allowed apps: %s", ", ".join(a["name"] for a in apps) or "none")
        self.accept()
