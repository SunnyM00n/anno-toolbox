"""The Game dump dock of the UI editor (Qt): which dump is held against the open file, the tolerance, and every
widget whose rectangle or visibility differs from the preview's; a click on a row selects the widget.

``DumpPanel`` shows what ``gamedump.compare`` found and reports a picked row as ``picked(node id)`` and a changed
tolerance as ``changed()``; ``compare_dump``, ``live_dump`` and ``save_label_table`` are the window's
commands. docs/ui-editor.md, "Comparing with the game".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import linkscenes, livelink
from . import gamedump

ROLE = Qt.ItemDataRole.UserRole
KIND_LABELS = {  # a Difference's kind -> its words
    "rect": N_("rect"),
    "only in the game": N_("only in the game"),
    "only in the preview": N_("only in the preview"),
}


def _rect(r: gamedump.Rect | None) -> str:
    return "" if r is None else f"{r[0]:.0f}, {r[1]:.0f} - {r[2]:.0f}, {r[3]:.0f}"


def _shown(v: bool | None) -> str:
    return "" if v is None else tr("shown") if v else tr("hidden")


class DumpPanel(QWidget):
    picked = Signal(int)
    changed = Signal()
    cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.dump: gamedump.Dump | None = None
        self.label = QLabel(
            tr("File > Compare with a game dump... holds a dump of the running game against the preview.")
        )
        self.label.setWordWrap(True)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 200.0)
        self.tolerance.setValue(2.0)
        self.tolerance.setSuffix(" px")  # i18n: ok (a unit)
        self.tolerance.valueChanged.connect(lambda _v: self.changed.emit())
        clear = QPushButton(tr("Clear"))
        clear.clicked.connect(self.clear)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Tolerance")))
        row.addWidget(self.tolerance)
        row.addStretch(1)
        row.addWidget(clear)
        self.table = QTreeWidget()
        self.table.setHeaderLabels([tr("Widget"), tr("Difference"), tr("Game"), tr("Preview"), tr("Off by")])
        self.table.setRootIsDecorated(False)
        self.table.itemClicked.connect(self._clicked)
        box = QVBoxLayout(self)
        box.addWidget(self.label)
        box.addLayout(row)
        box.addWidget(self.table)

    def hold(self, dump: gamedump.Dump) -> None:
        self.dump = dump
        self.changed.emit()

    def clear(self) -> None:
        self.dump = None
        self.table.clear()
        self.label.setText(tr("No dump held."))
        self.cleared.emit()

    def show_comparison(self, c: gamedump.Comparison) -> None:
        """Show a dump's comparison with the open scene: the head line, then a row per element that differs."""
        d = self.dump
        head = (
            tr(
                "{scene} ({stamp}, UI scale {sx:g} x {sy:g})",
                scene=d.scene,
                stamp=d.stamp,
                sx=d.ui_scale[0],
                sy=d.ui_scale[1],
            )
            if d
            else ""
        )
        self.label.setText(f"{head}: {c.note}" if head else c.note)
        self.table.clear()
        for r in c.differences:
            what = (
                tr(KIND_LABELS.get(r.kind, r.kind))
                if r.kind != "visibility"
                else tr(
                    "visibility: game {game}, preview {preview}",
                    game=_shown(r.game_shown),
                    preview=_shown(r.preview_shown),
                )
            )
            item = QTreeWidgetItem([r.name, what, _rect(r.game), _rect(r.preview), f"{r.delta:.1f}" if r.delta else ""])
            item.setData(0, ROLE, r.node_id)
            item.setToolTip(0, r.guid or "")
            self.table.addTopLevelItem(item)
        for i in range(5):
            self.table.resizeColumnToContents(i)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        node_id = item.data(0, ROLE)
        if node_id:
            self.picked.emit(int(node_id))


# The window's dump commands (``window`` is the ``uiwindow.UiWindow``)


def compare_dump(window) -> None:
    """A dump file picked from the game's dump folder, held against the preview."""
    start = gametree.user_dir("anno117") / "log" / "ui-dump"
    name, _ = QFileDialog.getOpenFileName(
        window, tr("A layout dump of the game"), str(start), tr("Layout dumps (*.json)")
    )
    if name:  # a dump runs to megabytes: read off the window's thread
        window.background.run(lambda: gamedump.load(name), lambda dump: _hold(window, dump, warn=True))


def live_dump(window) -> None:
    """U8: the running game writes the live widget tree of the scene the open file shows (its own state, or the
    states of the scenes that place a widget file); the newest of those dumps is held against the preview."""
    if window.game is None or window.doc is None:
        return
    entry = window.game.files.entries.get(window.doc.key)
    path = entry.path if entry else ""
    states = linkscenes.states_for([path]) or window.game.files.scene_states([path])
    if not livelink.current().connected:
        window.statusBar().showMessage(
            tr(
                "no game is connected: install the link helper, or set live_link=1 in the full helper library's ini "
                "(docs/live-link.md)"
            )
        )
        return
    window.statusBar().showMessage(tr("asking the game for the live values ..."))
    window.background.run(lambda: livelink.current().ui_dump(states), lambda reply: live_dumped(window, reply))


def live_dumped(window, reply) -> None:
    """The game's answer to ``live_dump``: the dump it wrote, held."""
    if isinstance(reply, Exception) or not reply.ok:
        window.statusBar().showMessage(tr("the game gave no dump: {why}", why=getattr(reply, "message", reply) or "?"))
        return
    try:
        name = gametree.user_dir("anno117") / "log" / "ui-dump" / reply.data["files"][0]
    except (KeyError, IndexError, TypeError) as exc:
        window.statusBar().showMessage(tr("The dump could not be read: {error}", error=exc))
        return
    window.background.run(lambda: gamedump.load(name), lambda dump: _hold(window, dump, live=True))


def _hold(window, dump, warn: bool = False, live: bool = False) -> None:
    """A dump read (or the exception reading it raised) held against the preview."""
    if isinstance(dump, Exception):
        message = tr("The dump could not be read: {error}", error=dump)
        if warn:
            QMessageBox.warning(window, window.windowTitle(), message)
        else:
            window.statusBar().showMessage(message)
        return
    if live:
        window.statusBar().showMessage(tr("the game's live values are held against the preview"))
    window.dump_panel.hold(dump)
    window.docks["Game dump"].show()
    window.docks["Game dump"].raise_()


def save_label_table(window) -> None:
    """The held dump's labels with what their text placement is read from, as a CSV for calibrating the preview."""
    dump = window.dump_panel.dump
    if dump is None:
        QMessageBox.information(
            window, window.windowTitle(), tr("Hold a game dump first (Compare with a game dump...).")
        )
        return
    name, _ = QFileDialog.getSaveFileName(
        window, tr("Save the label table"), f"{dump.scene or 'labels'}-labels.csv", "CSV (*.csv)"
    )  # i18n: ok (a file filter)
    if name:
        try:
            Path(name).write_bytes(gamedump.label_csv(dump))
        except OSError as exc:
            QMessageBox.warning(window, window.windowTitle(), tr("The table could not be written: {error}", error=exc))
