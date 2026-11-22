"""The debug-command palette window: the game's debug commands as a searchable list with typed argument fields, sent
to the running game over the live link.

``CPaletteWindow(parent, state, on_state)`` is the window; ``send`` and ``fetch`` are what it asks of the link (a
function each, so a test can call them without a window). Only ids of ``debugcommands`` with typed values leave the
suite; offline the window shows the catalogue and says "not connected". docs/debug-palette.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from . import debugcommands as dc
from . import livelink
from .background import CBackground
from .debugcommands import SCommand


def send(link: livelink.CLiveLink, command: SCommand, values: dict) -> livelink.SReply:
    """Send ``command`` with ``values`` over ``link``: checked first, never sent when the values do not fit its
    types or the link has no debug requests (docs/debug-palette.md section 5)."""
    typed, why = dc.check_values(command, values)
    if why:
        return livelink.SReply(livelink.REFUSED, why)
    call = getattr(link, "debug_run", None)
    if call is None:
        return livelink.SReply(livelink.UNAVAILABLE, tr("common.link_no_debug_commands_yet"))
    return call(command.id, typed)


def send_cheat(link: livelink.CLiveLink, name: str, known: list[str]) -> livelink.SReply:
    """Run one of the game's own cheats: only a name the game itself listed."""
    call = getattr(link, "debug_cheat", None)
    if name not in known:
        return livelink.SReply(livelink.REFUSED, tr("common.not_cheat_game_listed", name=name))
    if call is None:
        return livelink.SReply(livelink.UNAVAILABLE, tr("common.link_no_debug_commands_yet"))
    return call(name)


def fetch(link: livelink.CLiveLink) -> livelink.SReply:
    """What the game binds now: ``data.commands`` (id to ``available`` and ``state``) and ``data.cheats`` (names)."""
    call = getattr(link, "debug_list", None)
    if call is None or not link.connected:
        return livelink.SReply(livelink.NOT_CONNECTED if not link.connected else livelink.UNAVAILABLE)
    return call()


def state_text(state) -> str:
    if state is None:
        return ""
    return tr("mods.on") if state is True else tr("mods.off") if state is False else str(state)


class CPaletteWindow(QWidget):
    """The palette. ``state`` is the remembered dict (``history``, ``favourites``); ``on_state()`` is called when it
    changed, so the owner can save it."""

    answered = Signal(str, str)  # status, message: a send came back

    def __init__(self, parent=None, state: dict | None = None, on_state: Callable[[], None] | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(tr("common.debug_commands"))
        self.m_state = state if state is not None else {}
        self._m_on_state = on_state or (lambda: None)
        self.m_game = dc.GAME
        self.m_background = CBackground(self)
        self.m_available: dict[str, dict] = {}  # id -> {"available", "state"} as the game last said
        self.m_cheats: list[str] = []
        self.m_current: SCommand | None = None
        self.m_fields: dict[str, QWidget] = {}
        self._build()
        self._m_removers: list[Callable[[], None]] = []
        self._follow_link()
        self.answered.connect(self._link_changed)
        removers = self._m_removers  # not ``self``: the window is gone when the signal comes
        self.destroyed.connect(lambda _o=None: [remove() for remove in removers])
        self.refresh_list()
        self.refresh_connection()

    def _follow_link(self) -> None:
        """Hear of the link's status from now on (once)."""
        if not self._m_removers:
            self._m_removers.append(livelink.on("status", lambda _s: self.answered.emit("", "")))

    def showEvent(self, event) -> None:
        self._follow_link()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        """A closed window stops listening: the suite's window keeps it, and it would ask the game the commands again
        at every change of the link."""
        for remove in self._m_removers:
            remove()
        self._m_removers.clear()
        super().closeEvent(event)

    # building

    def _build(self) -> None:
        self.m_search = QLineEdit(placeholderText=tr("common.search_commands"))
        self.m_search.textChanged.connect(lambda _t: self.refresh_list())
        self.m_categories = QListWidget()
        self.m_categories.setMaximumWidth(180)
        for key, title in self._category_rows():
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.m_categories.addItem(item)
        self.m_categories.setCurrentRow(0)
        self.m_categories.currentRowChanged.connect(lambda _r: self.refresh_list())
        self.m_list = QListWidget()
        self.m_list.currentItemChanged.connect(lambda item, _p: self.choose(item))
        self.m_title = QLabel(wordWrap=True)
        self.m_effect = QLabel(wordWrap=True)
        self.m_call = QLabel(wordWrap=True)
        self.m_call.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.m_form = QFormLayout()
        self.m_run = QPushButton(tr("common.send_to_game"))
        self.m_run.clicked.connect(lambda _c=False: self.run_current())
        self.m_star = QPushButton(tr("common.favourite"))
        self.m_star.clicked.connect(lambda _c=False: self.star_current())
        self.m_note = QLabel(wordWrap=True)
        self.m_connection = QLabel(wordWrap=True)
        self.m_history = QListWidget()
        self.m_history.itemDoubleClicked.connect(self.reuse)
        buttons = QHBoxLayout()
        for button in (self.m_run, self.m_star):
            buttons.addWidget(button)
        buttons.addStretch(1)
        right = QWidget()
        side = QVBoxLayout(right)
        for part in (self.m_title, self.m_effect, self.m_call):
            side.addWidget(part)
        side.addLayout(self.m_form)
        side.addLayout(buttons)
        side.addWidget(self.m_note)
        side.addWidget(QLabel(tr("common.history_double_click_fill_form")))
        side.addWidget(self.m_history, 1)
        middle = QWidget()
        column = QVBoxLayout(middle)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.m_search)
        column.addWidget(self.m_list, 1)
        splitter = QSplitter()
        for part in (self.m_categories, middle, right):
            splitter.addWidget(part)
        splitter.setSizes([160, 320, 460])
        layout = QVBoxLayout(self)
        layout.addWidget(self.m_connection)
        layout.addWidget(splitter, 1)
        self.resize(980, 560)
        self.fill_history()

    def _category_rows(self) -> list[tuple[str, str]]:
        rows = [("", tr("common.all")), (dc.FAVOURITES, dc.category_title(dc.FAVOURITES))]
        rows += [(key, dc.category_title(key, self.m_game)) for key in dc.categories(self.m_game)]
        return [*rows, (dc.CHEATS, dc.category_title(dc.CHEATS))]

    # the list

    def _category_row(self, key: str) -> int:
        return next((i for i in range(self.m_categories.count()) if self.m_categories.item(i).data(256) == key), 0)

    def category(self) -> str:
        item = self.m_categories.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def refresh_list(self) -> None:
        """Fill the list for the search and the category; the selection stays when the command is still there."""
        keep = self.m_current.id if self.m_current else ""
        self.m_list.blockSignals(True)
        self.m_list.clear()
        if self.category() == dc.CHEATS:
            for name in self.m_cheats:
                self._add_row(name, name, f"cheat:{name}")
        else:
            stars = tuple(self.m_state.get("favourites", []))
            for command in dc.find(self.m_game, self.m_search.text(), self.category(), stars):
                self._add_row(command.id, self._row_text(command, command.id in stars), command.id)
        self.m_list.blockSignals(False)
        row = next(
            (i for i in range(self.m_list.count()) if self.m_list.item(i).data(Qt.ItemDataRole.UserRole) == keep), 0
        )
        self.m_list.setCurrentRow(row if self.m_list.count() else -1)
        self.choose(self.m_list.currentItem())

    def _add_row(self, _id: str, text: str, key: str) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.m_list.addItem(item)

    def _row_text(self, command: SCommand, starred: bool) -> str:
        known = self.m_available.get(command.id)
        text = ("* " if starred else "") + tr(command.title)
        if known is None:
            return text
        if not known.get("available", True):
            return f"{text}  ({tr('common.not_game')})"
        shown = state_text(known.get("state"))
        return f"{text}  [{shown}]" if shown else text

    # the chosen command

    def choose(self, item: QListWidgetItem | None) -> None:
        key = item.data(Qt.ItemDataRole.UserRole) if item else ""
        while self.m_form.rowCount():
            self.m_form.removeRow(0)
        self.m_fields = {}
        self.m_current = None if not key or key.startswith("cheat:") else dc.commands(self.m_game).get(key)
        if key.startswith("cheat:"):
            self.m_title.setText(key[6:])
            self.m_effect.setText(tr("common.one_cheats_game_itself_registered"))
            self.m_call.setText("")
        elif self.m_current:
            self.m_title.setText(tr(self.m_current.title))
            self.m_effect.setText(f"{tr(self.m_current.effect)}\n{dc.safety_text(self.m_current)}")
            self._fill_form(self.m_current)
            self.update_call()
        else:
            self.m_title.setText(tr("common.no_command"))
            self.m_effect.setText("")
            self.m_call.setText("")
        self.m_star.setEnabled(self.m_current is not None)
        self.update_buttons()

    def _fill_form(self, command: SCommand) -> None:
        for arg in command.args:
            if arg.kind == dc.FLAG:
                widget: QWidget = QCheckBox(checked=True)
                widget.toggled.connect(lambda _v: self.update_call())
            else:
                widget = QSpinBox()
                widget.setRange(0, 100 if arg.kind == dc.PERCENT else 2**31 - 1)
                widget.setValue(100 if arg.kind == dc.PERCENT else 0)
                widget.valueChanged.connect(lambda _v: self.update_call())
            self.m_fields[arg.name] = widget
            self.m_form.addRow(tr(arg.title), widget)

    def values(self) -> dict:
        """What the argument fields hold now, typed."""
        out: dict = {}
        for name, widget in self.m_fields.items():
            out[name] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.value()
        return out

    def update_call(self) -> None:
        if self.m_current:
            self.m_call.setText(dc.preview(self.m_current, self.values()))

    # connection and sending

    def refresh_connection(self) -> None:
        link = livelink.current()
        self.m_connection.setText(link.status().text if link.connected else tr("common.not_connected_nothing_sent"))
        self.m_game = (link.status().game if link.connected else "") or dc.GAME  # the commands of the linked game
        if not link.connected:  # what the last game said (states, cheats) is not true of the next one
            self.m_available, self.m_cheats = {}, []
        self.refresh_list()
        if link.connected:
            self.m_background.run(lambda: fetch(link), self._listed)
        self.update_buttons()

    def _link_changed(self, _status: str, _message: str) -> None:
        if not _status:
            self.refresh_connection()

    def _listed(self, reply) -> None:
        if isinstance(reply, livelink.SReply) and reply.is_ok:
            self.m_available = {
                k: command for k, command in (reply.data.get("commands") or {}).items() if isinstance(command, dict)
            }
            self.m_cheats = [str(cheat) for cheat in reply.data.get("cheats") or []]
            self.refresh_list()

    def update_buttons(self) -> None:
        link = livelink.current()
        runnable = bool(link.connected and (self.m_current or self.m_list.currentItem()))
        self.m_run.setEnabled(runnable)
        self.m_run.setToolTip("" if link.connected else tr("common.not_connected_nothing_sent"))

    def run_current(self) -> None:
        item = self.m_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if key.startswith("cheat:"):
            self._dispatch(key[6:], {}, lambda link: send_cheat(link, key[6:], self.m_cheats))
        elif self.m_current:
            command, values = self.m_current, self.values()
            if command.confirm and not self._confirmed(command):
                return
            self._dispatch(command.id, values, lambda link: send(link, command, values))

    def _confirmed(self, command: SCommand) -> bool:
        answer = QMessageBox.question(
            self, tr("common.debug_commands"), tr("common.really_send_title", title=tr(command.title))
        )
        return answer == QMessageBox.StandardButton.Yes

    def _dispatch(self, name: str, values: dict, call: Callable[[livelink.CLiveLink], livelink.SReply]) -> None:
        link = livelink.current()
        self.m_note.setText(tr("common.sending"))
        self.m_run.setEnabled(False)
        self.m_background.run(lambda: call(link), lambda reply: self._answered(name, values, reply))

    def _answered(self, name: str, values: dict, reply) -> None:
        if isinstance(reply, Exception):
            reply = livelink.SReply(livelink.UNAVAILABLE, str(reply))
        text = f"{reply.status}: {reply.message}" if reply.message else reply.status
        self.m_note.setText(text)
        if "state" in reply.data and name in dc.commands(self.m_game):
            self.m_available.setdefault(name, {})["state"] = reply.data["state"]
            self.refresh_list()
        dc.push_history(
            self.m_state, {"command": name, "values": values, "status": reply.status, "message": reply.message}
        )
        self._m_on_state()
        self.fill_history()
        self.update_buttons()
        self.answered.emit(reply.status, reply.message)

    # favourites and history

    def star_current(self) -> None:
        if self.m_current:
            dc.toggle_favourite(self.m_state, self.m_current.id)
            self._m_on_state()
            self.refresh_list()

    def fill_history(self) -> None:
        self.m_history.clear()
        for entry in self.m_state.get("history", []):
            shown = f"{entry.get('command', '')} {entry.get('values') or ''}".strip()
            item = QListWidgetItem(f"{shown}  -> {entry.get('status', '')}")
            item.setToolTip(str(entry.get("message", "")))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.m_history.addItem(item)

    def reuse(self, item: QListWidgetItem) -> None:
        """Select the command of a history entry and put its values back into the form."""
        entry = item.data(Qt.ItemDataRole.UserRole) or {}
        name = entry.get("command")
        # a cheat is in the history under its bare name but listed as ``cheat:<name>``, in the cheats category
        cheat = name not in dc.commands(self.m_game)
        key = f"cheat:{name}" if cheat else name
        self.m_categories.setCurrentRow(self._category_row(dc.CHEATS if cheat else ""))
        self.m_search.setText("")
        for row in range(self.m_list.count()):
            if self.m_list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.m_list.setCurrentRow(row)
                break
        for name, value in (entry.get("values") or {}).items():
            widget = self.m_fields.get(name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox) and isinstance(value, int):
                widget.setValue(value)
