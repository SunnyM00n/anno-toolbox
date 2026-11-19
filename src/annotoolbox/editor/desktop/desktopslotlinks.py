"""The slot links dialog (Slots tab > Slot links): per slot asset of the open island the slot asset of the other game
(Anno 117 and Anno 1800) its objects become in a port, the rules' suggestion preselected and the person's own choice
kept with the project (``slotlinks``). Plan and use: ``docs/island-regions.md`` "Slots between the games".
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...infra.i18n import N_, tr
from .. import slotlinks

if TYPE_CHECKING:
    from .desktopeditor import IslandDoc

OTHER_GAME = {"anno117": "anno1800", "anno1800": "anno117"}
GAME_NAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}


def read_sides(app, game: str):
    """(the island game's slot assets and resources, the other game's): read off the window's thread."""
    src, dst = app.port_side(game), app.port_side(OTHER_GAME[game])
    return *src.slots(), *dst.slots()


class SlotLinksDialog(QDialog):
    """The links of the island's slot assets; ``save`` keeps the ones that differ from the rules' suggestions."""

    def __init__(
        self, parent, game: str, used: Counter, src: dict, dst: dict, suggested: dict, saved: dict, path
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.slot_links_title", game=GAME_NAMES[OTHER_GAME[game]]))
        self.path, self.suggested, self.src, self.dst = path, suggested, src, dst
        self.guids = [g for g in sorted(used) if g in src]
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("islands.slot_links_intro"), wordWrap=True))
        heads = (
            N_("islands.slot_kind"),
            N_("common.name"),
            N_("islands.slot_guid"),
            N_("islands.count"),
            N_("common.target"),
            N_("islands.slot_link_state"),
        )
        self.table = QTableWidget(len(self.guids), len(heads))
        self.table.setHorizontalHeaderLabels([tr(h) for h in heads])
        self.boxes: list[QComboBox] = []
        links = slotlinks.resolve(suggested, saved)
        for r, g in enumerate(self.guids):
            for c, text in enumerate((src[g].kind, src[g].name, str(g), str(used[g]))):
                self.table.setItem(r, c, QTableWidgetItem(text))
            box = self._box(src[g], links.get(g))
            box.currentIndexChanged.connect(lambda _i, r=r: self._state(r))
            self.boxes.append(box)
            self.table.setCellWidget(r, 4, box)
            self.table.setItem(r, 5, QTableWidgetItem())
            self._state(r)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        reset = QPushButton(tr("islands.slot_link_reset"))
        reset.clicked.connect(self._reset)
        layout.addWidget(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("common.save"))
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("common.close"))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(900, 420)

    def _box(self, asset, target: int | None) -> QComboBox:
        box = QComboBox()
        box.addItem(tr("islands.slot_link_left_out"), None)
        for a in sorted((a for a in self.dst.values() if a.family == asset.family), key=lambda a: (a.kind, a.guid)):
            box.addItem(f"{a.kind} · {a.name} · {a.guid}", a.guid)
        box.setCurrentIndex(max(0, box.findData(target)))
        return box

    def chosen(self) -> dict:
        return {g: box.currentData() for g, box in zip(self.guids, self.boxes, strict=True)}

    def _state(self, row: int) -> None:
        g = self.guids[row]
        same = self.boxes[row].currentData() == self.suggested.get(g)
        self.table.item(row, 5).setText(tr("islands.slot_link_suggested") if same else tr("islands.slot_link_chosen"))

    def _reset(self) -> None:
        for g, box in zip(self.guids, self.boxes, strict=True):
            box.setCurrentIndex(max(0, box.findData(self.suggested.get(g))))

    def save(self) -> None:
        slotlinks.save(self.path, slotlinks.merge_saved(slotlinks.load(self.path), self.chosen(), self.suggested))
        self.accept()


def show(doc: IslandDoc) -> None:
    """Read both games' slots in the background, then open the dialog for the open island."""
    game, app = doc.game_key(), doc.shell.app
    if game not in OTHER_GAME:
        return
    used = Counter(e.base.get("guid") for e in doc.session.entries if e.kind == "object" and e.alive and e.visible)
    doc.say(tr("islands.slot_links_reading"))

    def done(got) -> None:
        doc.say("")
        if isinstance(got, Exception):
            QMessageBox.warning(doc.shell, tr("islands.slot_links"), tr("islands.slot_links_failed", error=got))
            return
        src, mine, dst, theirs = got
        suggested = slotlinks.suggest(src, dst, mine, theirs)
        path = slotlinks.path_for(app.workspace, game, OTHER_GAME[game])
        SlotLinksDialog(doc.shell, game, used, src, dst, suggested, slotlinks.load(path), path).exec()

    doc.background.run(lambda: read_sides(app, game), done)
