"""The debug-command palette window: the game's debug commands as a searchable list with typed argument fields, sent
to the running game over the live link.

``PaletteWindow(parent, state, on_state)`` is the window; ``send`` and ``fetch`` are what it asks of the link (a
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
from .background import Background
from .debugcommands import Command


def send(link: livelink.LiveLink, command: Command, values: dict) -> livelink.Reply:
    """Send ``command`` with ``values`` over ``link``: checked first, never sent when the values do not fit its
    types or the link has no debug requests (docs/debug-palette.md section 5)."""
    typed, why = dc.check_values(command, values)
    if why:
        return livelink.Reply(livelink.REFUSED, why)
    call = getattr(link, "debug_run", None)
    if call is None:
        return livelink.Reply(livelink.UNAVAILABLE, tr("this link has no debug commands yet"))
    return call(command.id, typed)


def send_cheat(link: livelink.LiveLink, name: str, known: list[str]) -> livelink.Reply:
    """Run one of the game's own cheats: only a name the game itself listed."""
    call = getattr(link, "debug_cheat", None)
    if name not in known:
        return livelink.Reply(livelink.REFUSED, tr("not a cheat the game listed: {name}", name=name))
    if call is None:
        return livelink.Reply(livelink.UNAVAILABLE, tr("this link has no debug commands yet"))
    return call(name)


def fetch(link: livelink.LiveLink) -> livelink.Reply:
    """What the game binds now: ``data.commands`` (id to ``available`` and ``state``) and ``data.cheats`` (names)."""
    call = getattr(link, "debug_list", None)
    if call is None or not link.connected:
        return livelink.Reply(livelink.NOT_CONNECTED if not link.connected else livelink.UNAVAILABLE)
    return call()


def state_text(state) -> str:
    if state is None:
        return ""
    return tr("on") if state is True else tr("off") if state is False else str(state)


class PaletteWindow(QWidget):
    """The palette. ``state`` is the remembered dict (``history``, ``favourites``); ``on_state()`` is called when it
    changed, so the owner can save it."""

    answered = Signal(str, str)  # status, message: a send came back

    def __init__(self, parent=None, state: dict | None = None, on_state: Callable[[], None] | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(tr("Debug commands"))
        self.state = state if state is not None else {}
        self._on_state = on_state or (lambda: None)
        self.game = dc.GAME
        self.background = Background(self)
        self.available: dict[str, dict] = {}  # id -> {"available", "state"} as the game last said
        self.cheats: list[str] = []
        self.current: Command | None = None
        self.fields: dict[str, QWidget] = {}
        self._build()
        self._removers = [livelink.on("status", lambda _s: self.answered.emit("", ""))]
        self.answered.connect(self._link_changed)
        self.destroyed.connect(lambda _o=None: [remove() for remove in self._removers])
        self.refresh_list()
        self.refresh_connection()

    # building

    def _build(self) -> None:
        self.search = QLineEdit(placeholderText=tr("Search the commands"))
        self.search.textChanged.connect(lambda _t: self.refresh_list())
        self.categories = QListWidget()
        self.categories.setMaximumWidth(180)
        for key, title in self._category_rows():
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.categories.addItem(item)
        self.categories.setCurrentRow(0)
        self.categories.currentRowChanged.connect(lambda _r: self.refresh_list())
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda item, _p: self.choose(item))
        self.title = QLabel(wordWrap=True)
        self.effect = QLabel(wordWrap=True)
        self.call = QLabel(wordWrap=True)
        self.call.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form = QFormLayout()
        self.run = QPushButton(tr("Send to the game"))
        self.run.clicked.connect(lambda _c=False: self.run_current())
        self.star = QPushButton(tr("Favourite"))
        self.star.clicked.connect(lambda _c=False: self.star_current())
        self.note = QLabel(wordWrap=True)
        self.connection = QLabel(wordWrap=True)
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self.reuse)
        buttons = QHBoxLayout()
        for button in (self.run, self.star):
            buttons.addWidget(button)
        buttons.addStretch(1)
        right = QWidget()
        side = QVBoxLayout(right)
        for part in (self.title, self.effect, self.call):
            side.addWidget(part)
        side.addLayout(self.form)
        side.addLayout(buttons)
        side.addWidget(self.note)
        side.addWidget(QLabel(tr("History (double click to fill the form again)")))
        side.addWidget(self.history, 1)
        middle = QWidget()
        column = QVBoxLayout(middle)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.search)
        column.addWidget(self.list, 1)
        splitter = QSplitter()
        for part in (self.categories, middle, right):
            splitter.addWidget(part)
        splitter.setSizes([160, 320, 460])
        layout = QVBoxLayout(self)
        layout.addWidget(self.connection)
        layout.addWidget(splitter, 1)
        self.resize(980, 560)
        self.fill_history()

    @staticmethod
    def _category_rows() -> list[tuple[str, str]]:
        rows = [("", tr("All")), (dc.FAVOURITES, dc.category_title(dc.FAVOURITES))]
        rows += [(key, dc.category_title(key)) for key in dc.CATEGORIES]
        return [*rows, (dc.CHEATS, dc.category_title(dc.CHEATS))]

    # the list

    def category(self) -> str:
        item = self.categories.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def refresh_list(self) -> None:
        """Fill the list for the search and the category; the selection stays when the command is still there."""
        keep = self.current.id if self.current else ""
        self.list.blockSignals(True)
        self.list.clear()
        if self.category() == dc.CHEATS:
            for name in self.cheats:
                self._add_row(name, name, f"cheat:{name}")
        else:
            stars = tuple(self.state.get("favourites", []))
            for command in dc.find(self.game, self.search.text(), self.category(), stars):
                self._add_row(command.id, self._row_text(command, command.id in stars), command.id)
        self.list.blockSignals(False)
        row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == keep), 0)
        self.list.setCurrentRow(row if self.list.count() else -1)
        self.choose(self.list.currentItem())

    def _add_row(self, _id: str, text: str, key: str) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.list.addItem(item)

    def _row_text(self, command: Command, starred: bool) -> str:
        known = self.available.get(command.id)
        text = ("* " if starred else "") + tr(command.title)
        if known is None:
            return text
        if not known.get("available", True):
            return f"{text}  ({tr('not in this game')})"
        shown = state_text(known.get("state"))
        return f"{text}  [{shown}]" if shown else text

    # the chosen command

    def choose(self, item: QListWidgetItem | None) -> None:
        key = item.data(Qt.ItemDataRole.UserRole) if item else ""
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        self.current = None if not key or key.startswith("cheat:") else dc.commands(self.game).get(key)
        if key.startswith("cheat:"):
            self.title.setText(key[6:])
            self.effect.setText(tr("one of the cheats the game itself registered"))
            self.call.setText("")
        elif self.current:
            self.title.setText(tr(self.current.title))
            self.effect.setText(tr(self.current.effect))
            self._fill_form(self.current)
            self.update_call()
        else:
            self.title.setText(tr("no command"))
            self.effect.setText("")
            self.call.setText("")
        self.star.setEnabled(self.current is not None)
        self.update_buttons()

    def _fill_form(self, command: Command) -> None:
        for arg in command.args:
            if arg.kind == dc.FLAG:
                widget: QWidget = QCheckBox(checked=True)
                widget.toggled.connect(lambda _v: self.update_call())
            else:
                widget = QSpinBox()
                widget.setRange(0, 100 if arg.kind == dc.PERCENT else 2**31 - 1)
                widget.setValue(100 if arg.kind == dc.PERCENT else 0)
                widget.valueChanged.connect(lambda _v: self.update_call())
            self.fields[arg.name] = widget
            self.form.addRow(tr(arg.title), widget)

    def values(self) -> dict:
        """What the argument fields hold now, typed."""
        out: dict = {}
        for name, widget in self.fields.items():
            out[name] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.value()
        return out

    def update_call(self) -> None:
        if self.current:
            self.call.setText(dc.preview(self.current, self.values()))

    # connection and sending

    def refresh_connection(self) -> None:
        link = livelink.current()
        self.connection.setText(link.status().text if link.connected else tr("not connected: nothing is sent"))
        if link.connected:
            self.background.run(lambda: fetch(link), self._listed)
        self.update_buttons()

    def _link_changed(self, _status: str, _message: str) -> None:
        if not _status:
            self.refresh_connection()

    def _listed(self, reply) -> None:
        if isinstance(reply, livelink.Reply) and reply.ok:
            self.available = {k: v for k, v in (reply.data.get("commands") or {}).items() if isinstance(v, dict)}
            self.cheats = [str(n) for n in reply.data.get("cheats") or []]
            self.refresh_list()

    def update_buttons(self) -> None:
        link = livelink.current()
        runnable = bool(link.connected and (self.current or self.list.currentItem()))
        self.run.setEnabled(runnable)
        self.run.setToolTip("" if link.connected else tr("not connected: nothing is sent"))

    def run_current(self) -> None:
        item = self.list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if key.startswith("cheat:"):
            self._dispatch(key[6:], {}, lambda link: send_cheat(link, key[6:], self.cheats))
        elif self.current:
            command, values = self.current, self.values()
            if command.confirm and not self._confirmed(command):
                return
            self._dispatch(command.id, values, lambda link: send(link, command, values))

    def _confirmed(self, command: Command) -> bool:
        answer = QMessageBox.question(self, tr("Debug commands"), tr("Really send: {title}?", title=tr(command.title)))
        return answer == QMessageBox.StandardButton.Yes

    def _dispatch(self, name: str, values: dict, call: Callable[[livelink.LiveLink], livelink.Reply]) -> None:
        link = livelink.current()
        self.note.setText(tr("sending ..."))
        self.run.setEnabled(False)
        self.background.run(lambda: call(link), lambda reply: self._answered(name, values, reply))

    def _answered(self, name: str, values: dict, reply) -> None:
        if isinstance(reply, Exception):
            reply = livelink.Reply(livelink.UNAVAILABLE, str(reply))
        text = f"{reply.status}: {reply.message}" if reply.message else reply.status
        self.note.setText(text)
        if "state" in reply.data and name in dc.commands(self.game):
            self.available.setdefault(name, {})["state"] = reply.data["state"]
            self.refresh_list()
        dc.push_history(
            self.state, {"command": name, "values": values, "status": reply.status, "message": reply.message}
        )
        self._on_state()
        self.fill_history()
        self.update_buttons()
        self.answered.emit(reply.status, reply.message)

    # favourites and history

    def star_current(self) -> None:
        if self.current:
            dc.toggle_favourite(self.state, self.current.id)
            self._on_state()
            self.refresh_list()

    def fill_history(self) -> None:
        self.history.clear()
        for entry in self.state.get("history", []):
            shown = f"{entry.get('command', '')} {entry.get('values') or ''}".strip()
            item = QListWidgetItem(f"{shown}  -> {entry.get('status', '')}")
            item.setToolTip(str(entry.get("message", "")))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.history.addItem(item)

    def reuse(self, item: QListWidgetItem) -> None:
        """Select the command of a history entry and put its values back into the form."""
        entry = item.data(Qt.ItemDataRole.UserRole) or {}
        self.categories.setCurrentRow(0)
        self.search.setText("")
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == entry.get("command"):
                self.list.setCurrentRow(row)
        for name, value in (entry.get("values") or {}).items():
            widget = self.fields.get(name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox) and isinstance(value, int):
                widget.setValue(value)
