"""The suite's game link panel: what the status bar says in full, and the few things to do by hand about it.

``rows(link)`` is the text (connection, game and build, the reloads the game takes, the mods it has enabled, the
session file); ``LinkDialog(parent)`` shows it with the game's latest log lines, the game side's modules
(``module_items``; one switched by its tick) and the buttons "Connect now" and "Reload assets and texts". The shell opens
it from the status bar. docs/live-link.md, "The panel".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import tr
from ..sources import gametree
from . import linkannounce, linkcompanion, linkhandover, linkprotocol, livelink
from .background import Background

LOG_LINES = 200


def rows(link: livelink.LiveLink | None = None) -> list[tuple[str, str]]:
    """The panel's facts as (label, text); every text is a sentence a person can read without the protocol."""
    link = link or livelink.current()
    status = link.status()
    session = linkprotocol.session_folder() / linkprotocol.SESSION_FILE
    mods = [str(m.get("id", "")) for m in getattr(link, "mods", []) if isinstance(m, dict)]
    return [
        (tr("Connection"), status.text),
        (tr("The game takes"), ", ".join(sorted(status.kinds)) if status.connected and status.kinds else tr("none")),
        (tr("Mods the game has enabled"), ", ".join(mods) if mods else tr("none known")),
        (tr("The game's session file"), f"{session} ({tr('there') if session.is_file() else tr('not there')})"),
    ]


def module_items(data: dict) -> list[tuple[str, str, bool, bool, str]]:
    """The answer of the module list as (name, text, on, switchable, why), in the game's order."""
    out = []
    for m in data.get("modules", []) if isinstance(data, dict) else []:
        if isinstance(m, dict) and m.get("name"):
            name = str(m["name"])
            text = f"{name} ({m.get('class', '?')})"
            out.append((name, text, bool(m.get("on")), bool(m.get("switchable")), str(m.get("why", ""))))
    return out


class LinkDialog(QDialog):
    """The panel; its rows are made again when the link's status changes, the game's log lines are appended."""

    status_changed = Signal()
    line = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Game link"))
        self.background = Background(self)
        self.form = QFormLayout()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.note = QLabel(wordWrap=True)
        connect = QPushButton(tr("Connect now"))
        connect.setToolTip(tr("look for a running game (the suite also looks every few seconds)"))
        connect.clicked.connect(lambda _c=False: self.connect_now())
        reload_ = QPushButton(tr("Reload assets and texts"))
        reload_.setToolTip(tr("the mod pass of the game, as Shift+F3 in it") + ". " + tr(livelink.PAUSE_NOTE))
        reload_.clicked.connect(lambda _c=False: self.reload_now())
        handover = QPushButton(tr("Anno 1800: open in the editor ..."))
        handover.setToolTip(
            tr("put the start file for the game's own open-in-editor command into its Tools/ToolOne folder")
        )
        handover.clicked.connect(lambda _c=False: self.set_up_1800())
        companion = QPushButton(tr("Companion mod ..."))
        companion.setToolTip(tr("write the small mod with the key that sends the selected object to the suite"))
        companion.clicked.connect(lambda _c=False: self.install_companion())
        modules = QPushButton(tr("List modules"))
        modules.setToolTip(
            tr("the game side's modules; a ticked one runs, a switchable one is ticked here to switch it")
        )
        modules.clicked.connect(lambda _c=False: self.list_modules())
        self.modules = QListWidget()
        self.modules.setMaximumHeight(140)
        self.modules.itemChanged.connect(self._module_ticked)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        for button in (connect, reload_, modules, handover, companion):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.note)
        layout.addWidget(self.modules)
        layout.addWidget(QLabel(tr("The game's log")))
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)
        self.resize(720, 480)
        self.status_changed.connect(self.refresh)
        self.line.connect(self.add_line)
        self._removers = [
            livelink.on("status", lambda _status: self.status_changed.emit()),
            livelink.on(livelink.LOG, self._on_log),
            livelink.on(livelink.STALLED, self._on_stalled),
        ]
        self.finished.connect(self._closed)
        self._follow_log(True)
        self.refresh()

    def _follow_log(self, on: bool) -> None:
        """The game sends its loader's log lines only while the link is subscribed to them."""
        link = livelink.current()
        if link.connected:
            call = link.subscribe if on else link.unsubscribe
            self.background.run(lambda: call([livelink.LOG]), lambda _r: None)

    def _closed(self, _result: int) -> None:
        for remove in self._removers:
            remove()
        self._follow_log(False)

    def refresh(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        for label, text in rows():
            value = QLabel(text, wordWrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.form.addRow(label, value)

    def add_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _on_log(self, payload: dict) -> None:
        """The transport's thread: the lines go to the window's through a signal."""
        for text in payload.get("lines", []) if isinstance(payload, dict) else []:
            self.line.emit(str(text))

    def _on_stalled(self, payload: dict) -> None:
        """The game's warning that it draws no frames after an asset request, into the log's place."""
        if isinstance(payload, dict) and payload.get("message"):
            self.line.emit(str(payload["message"]))

    def connect_now(self) -> None:
        self.note.setText(tr("looking for a game ..."))
        self.background.run(livelink.connect, lambda link: self.note.setText(self._found(link)))

    @staticmethod
    def _found(result) -> str:
        if isinstance(result, Exception):
            return tr("the search failed: {reason}", reason=result)
        if livelink.current().connected:
            return tr("connected")
        # the link helper has the link on by default, the full helper library only with live_link=1
        return tr(
            "no game answered: is the link helper in the game's folder "
            "(the full helper library needs live_link=1 in its ini)?"
        )

    def set_up_1800(self) -> None:
        """Ask for Anno 1800's folder and write (or, when it is there already, remove) the hand-over's start file."""
        folder = QFileDialog.getExistingDirectory(self, tr("The folder of Anno 1800"))
        if not folder:
            return
        try:
            if linkhandover.uninstall(Path(folder)):
                self.note.setText(tr("the suite's start file was removed from {folder}", folder=folder))
            else:
                path = linkhandover.install(Path(folder))
                self.note.setText(
                    tr("written: {path}; the game's open-in-editor command now opens the asset here", path=path)
                )
        except (OSError, ValueError) as exc:  # ValueError: a name the file cannot hold
            self.note.setText(str(exc))

    def install_companion(self) -> None:
        """Ask for the mods folder (the game's own to start with) and write the companion mod into it."""
        start = gametree.user_dir("anno117") / "mods"
        folder = QFileDialog.getExistingDirectory(self, tr("The mods folder of the game"), str(start))
        if not folder:
            return
        try:
            path = linkcompanion.write_companion(Path(folder))
        except OSError as exc:
            self.note.setText(str(exc))
            return
        self.note.setText(
            tr("written: {path}; enable it in the game's mod list (delete the folder to remove it)", path=path)
        )

    def list_modules(self) -> None:
        """The game side's modules into the list (behind the game's debug commands switch)."""
        self.background.run(lambda: livelink.current().modules_list(), self._fill_modules)

    def _fill_modules(self, reply) -> None:
        if isinstance(reply, Exception) or not reply.ok:
            self.note.setText(str(reply if isinstance(reply, Exception) else reply.message))
            return
        self.modules.blockSignals(True)
        self.modules.clear()
        for name, text, on, switchable, why in module_items(reply.data):
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags if switchable else flags & ~Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            item.setToolTip(why)
            self.modules.addItem(item)
        self.modules.blockSignals(False)

    def _module_ticked(self, item: QListWidgetItem) -> None:
        name, on = item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked
        self.background.run(lambda: livelink.current().modules_set(name, on), lambda r: self._module_switched(r))

    def _module_switched(self, reply) -> None:
        self.note.setText(str(reply) if isinstance(reply, Exception) else reply.message or tr("switched"))
        self.list_modules()

    def reload_now(self) -> None:
        linkannounce.announce_in_background(
            self.background,
            self.note.setText,
            lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS)),
        )
