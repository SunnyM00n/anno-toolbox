"""The suite's game link panel: what the status bar says in full, and the few things to do by hand about it.

``rows(link)`` is the text (connection, game and build, the reloads the game takes, the mods it has enabled, the
session file); ``CLinkDialog(parent)`` shows it with the game's latest log lines, the game side's modules
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
from . import linkannounce, linkclient, linkcompanion, linkhandover, linkprotocol, livelink
from .background import CBackground

LOG_LINES = 200


def rows(link: livelink.CLiveLink | None = None) -> list[tuple[str, str]]:
    """The panel's facts as (label, text); every text is a sentence a person can read without the protocol."""
    link = link or livelink.current()
    status = link.status()
    session = (
        linkprotocol.session_folder(game=livelink.target_game() or linkprotocol.DEFAULT_GAME)
        / linkprotocol.SESSION_FILE
    )
    mods = [str(linked_mod.get("id", "")) for linked_mod in getattr(link, "m_mods", []) if isinstance(linked_mod, dict)]
    return [
        (
            tr("common.connection"),
            status.text if status.connected or linkclient.supported() else tr("common.live_link_windows_only_why"),
        ),
        (
            tr("common.game_takes"),
            ", ".join(sorted(status.kinds)) if status.connected and status.kinds else tr("common.none"),
        ),
        (tr("common.mods_game_enabled"), ", ".join(mods) if mods else tr("common.none_known")),
        (
            tr("common.games_session_file"),
            f"{session} ({tr('common.there') if session.is_file() else tr('common.not_there')})",
        ),
    ]


def module_items(data: dict) -> list[tuple[str, str, bool, bool, str]]:
    """The answer of the module list as (name, text, on, switchable, why), in the game's order."""
    out = []
    for module in data.get("modules", []) if isinstance(data, dict) else []:
        if isinstance(module, dict) and module.get("name"):
            name = str(module["name"])
            text = f"{name} ({module.get('class', '?')})"
            out.append((name, text, bool(module.get("on")), bool(module.get("switchable")), str(module.get("why", ""))))
    return out


class CLinkDialog(QDialog):
    """The panel; its rows are made again when the link's status changes, the game's log lines are appended."""

    status_changed = Signal()
    line = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("common.title.game_link"))
        self.m_background = CBackground(self)
        self.m_form = QFormLayout()
        self.m_log = QPlainTextEdit(readOnly=True)
        self.m_log.setMaximumBlockCount(LOG_LINES)
        self.m_note = QLabel(wordWrap=True)
        connect = QPushButton(tr("common.connect_now"))
        connect.setToolTip(tr("common.tip.look_running_game_suite_also"))
        connect.clicked.connect(lambda _c=False: self.connect_now())
        reload_ = QPushButton(tr("common.reload_assets_texts"))
        reload_.setToolTip(tr("common.mod_pass_game_shift_plus") + ". " + tr(livelink.PAUSE_NOTE))
        reload_.clicked.connect(lambda _c=False: self.reload_now())
        handover = QPushButton(tr("common.anno_1800_open_editor"))
        handover.setToolTip(tr("common.tip.put_start_file_games_own"))
        handover.clicked.connect(lambda _c=False: self.set_up_1800())
        companion = QPushButton(tr("common.companion_mod"))
        companion.setToolTip(tr("common.tip.write_small_mod_key_sends"))
        companion.clicked.connect(lambda _c=False: self.install_companion())
        modules = QPushButton(tr("common.list_modules"))
        modules.setToolTip(tr("common.tip.game_sides_modules_ticked_one"))
        modules.clicked.connect(lambda _c=False: self.list_modules())
        self.m_modules = QListWidget()
        self.m_modules.setMaximumHeight(140)
        self.m_modules.itemChanged.connect(self._module_ticked)
        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        for button in (connect, reload_, modules, handover, companion):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(self.m_form)
        layout.addWidget(self.m_note)
        layout.addWidget(self.m_modules)
        layout.addWidget(QLabel(tr("common.games_log")))
        layout.addWidget(self.m_log, 1)
        layout.addLayout(buttons)
        self.resize(720, 480)
        self.status_changed.connect(self.refresh)
        self.line.connect(self.add_line)
        self._m_removers = [
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
            self.m_background.run(lambda: call([livelink.LOG]), lambda _r: None)

    def _closed(self, _result: int) -> None:
        for remove in self._m_removers:
            remove()
        self._follow_log(False)

    def refresh(self) -> None:
        while self.m_form.rowCount():
            self.m_form.removeRow(0)
        for label, text in rows():
            value = QLabel(text, wordWrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.m_form.addRow(label, value)

    def add_line(self, text: str) -> None:
        self.m_log.appendPlainText(text)

    def _on_log(self, payload: dict) -> None:
        """The transport's thread: the lines go to the window's through a signal."""
        for text in payload.get("lines", []) if isinstance(payload, dict) else []:
            self.line.emit(str(text))

    def _on_stalled(self, payload: dict) -> None:
        """The game's warning that it draws no frames after an asset request, into the log's place."""
        if isinstance(payload, dict) and payload.get("message"):
            self.line.emit(str(payload["message"]))

    def connect_now(self) -> None:
        self.m_note.setText(tr("common.looking_game"))
        self.m_background.run(livelink.connect, lambda link: self.m_note.setText(self._found(link)))

    @staticmethod
    def _found(result) -> str:
        if isinstance(result, Exception):
            return tr("common.search_failed_reason", reason=result)
        if livelink.current().connected:
            return tr("common.connected")
        # the link helper has the link on by default, the full helper library only with live_link=1
        return tr("common.no_game_answered_link_helper")

    def set_up_1800(self) -> None:
        """Ask for Anno 1800's folder and write (or, when it is there already, remove) the hand-over's start file."""
        folder = QFileDialog.getExistingDirectory(self, tr("common.folder_anno_1800"))
        if not folder:
            return
        try:
            if linkhandover.uninstall(Path(folder)):
                self.m_note.setText(tr("common.suites_start_file_removed", folder=folder))
            else:
                path = linkhandover.install(Path(folder))
                self.m_note.setText(tr("common.written_games_open_editor_command", path=path))
        except (OSError, ValueError) as exc:  # ValueError: a name the file cannot hold
            self.m_note.setText(str(exc))

    def install_companion(self) -> None:
        """Ask for the mods folder (the game's own to start with) and write the companion mod into it."""
        start = gametree.user_dir("anno117") / "mods"
        folder = QFileDialog.getExistingDirectory(self, tr("common.mods_folder_game"), str(start))
        if not folder:
            return
        try:
            path = linkcompanion.write_companion(Path(folder))
        except OSError as exc:
            self.m_note.setText(str(exc))
            return
        self.m_note.setText(tr("common.written_enable_games_mod_list", path=path))

    def list_modules(self) -> None:
        """The game side's modules into the list (behind the game's debug commands switch)."""
        self.m_background.run(lambda: livelink.current().modules_list(), self._fill_modules)

    def _fill_modules(self, reply) -> None:
        if isinstance(reply, Exception) or not reply.is_ok:
            self.m_note.setText(str(reply if isinstance(reply, Exception) else reply.message))
            return
        self.m_modules.blockSignals(True)
        self.m_modules.clear()
        for name, text, on, switchable, why in module_items(reply.data):
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags if switchable else flags & ~Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            item.setToolTip(why)
            self.m_modules.addItem(item)
        self.m_modules.blockSignals(False)

    def _module_ticked(self, item: QListWidgetItem) -> None:
        name, on = item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked
        self.m_background.run(
            lambda: livelink.current().modules_set(name, on), lambda result: self._module_switched(result)
        )

    def _module_switched(self, reply) -> None:
        self.m_note.setText(str(reply) if isinstance(reply, Exception) else reply.message or tr("common.switched"))
        self.list_modules()

    def reload_now(self) -> None:
        linkannounce.announce_in_background(
            self.m_background,
            self.m_note.setText,
            lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS)),
        )
