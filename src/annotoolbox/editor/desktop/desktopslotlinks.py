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
    from .desktopeditor import CIslandDoc

OTHER_GAME = {"anno117": "anno1800", "anno1800": "anno117"}
GAME_NAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}


def read_sides(app, game: str):
    """(the island game's slot assets and resources, the other game's): read off the window's thread."""
    source_side, target_side = app.port_side(game), app.port_side(OTHER_GAME[game])
    return *source_side.slots(), *target_side.slots()


class CSlotLinksDialog(QDialog):
    """The links of the island's slot assets; ``save`` keeps the ones that differ from the rules' suggestions."""

    def __init__(
        self,
        parent,
        game: str,
        used: Counter,
        source_assets: dict,
        target_assets: dict,
        suggested: dict,
        saved: dict,
        path,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.slot_links_title", game=GAME_NAMES[OTHER_GAME[game]]))
        self.m_path, self.m_suggested, self.m_src, self.m_dst = path, suggested, source_assets, target_assets
        self.m_guids = sorted(
            guid for guid in used if guid in source_assets
        )  # (an object without a GUID counts under None: no asset)
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
        self.m_table = QTableWidget(len(self.m_guids), len(heads))
        self.m_table.setHorizontalHeaderLabels([tr(heading) for heading in heads])
        self.m_boxes: list[QComboBox] = []
        links = slotlinks.resolve(suggested, saved)
        for row, guid in enumerate(self.m_guids):
            for column, text in enumerate(
                (source_assets[guid].kind, source_assets[guid].name, str(guid), str(used[guid]))
            ):
                self.m_table.setItem(row, column, QTableWidgetItem(text))
            box = self._box(source_assets[guid], links.get(guid))
            box.currentIndexChanged.connect(lambda _i, row=row: self._state(row))
            self.m_boxes.append(box)
            self.m_table.setCellWidget(row, 4, box)
            self.m_table.setItem(row, 5, QTableWidgetItem())
            self._state(row)
        self.m_table.resizeColumnsToContents()
        layout.addWidget(self.m_table)
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
        for candidate in sorted(
            (candidate for candidate in self.m_dst.values() if candidate.family == asset.family),
            key=lambda candidate: (candidate.kind, candidate.guid),
        ):
            box.addItem(f"{candidate.kind} · {candidate.name} · {candidate.guid}", candidate.guid)
        box.setCurrentIndex(max(0, box.findData(target)))
        return box

    def chosen(self) -> dict:
        return {guid: box.currentData() for guid, box in zip(self.m_guids, self.m_boxes, strict=True)}

    def _state(self, row: int) -> None:
        guid = self.m_guids[row]
        same = self.m_boxes[row].currentData() == self.m_suggested.get(guid)
        self.m_table.item(row, 5).setText(tr("islands.slot_link_suggested") if same else tr("islands.slot_link_chosen"))

    def _reset(self) -> None:
        for guid, box in zip(self.m_guids, self.m_boxes, strict=True):
            box.setCurrentIndex(max(0, box.findData(self.m_suggested.get(guid))))

    def save(self) -> None:
        slotlinks.save(self.m_path, slotlinks.merge_saved(slotlinks.load(self.m_path), self.chosen(), self.m_suggested))
        self.accept()


def show(document: CIslandDoc) -> None:
    """Read both games' slots in the background, then open the dialog for the open island."""
    game, app = document.game_key(), document.m_shell.m_app
    if game not in OTHER_GAME:
        return
    used = Counter(
        entry.base.get("guid")
        for entry in document.m_session.m_entries
        if entry.kind == "object" and entry.alive and entry.visible
    )
    document.say(tr("islands.slot_links_reading"))

    def done(got) -> None:
        document.say("")
        if isinstance(got, Exception):
            QMessageBox.warning(document.m_shell, tr("islands.slot_links"), tr("islands.slot_links_failed", error=got))
            return
        source_assets, mine, target_assets, theirs = got
        suggested = slotlinks.suggest(source_assets, target_assets, mine, theirs)
        path = slotlinks.path_for(app.workspace, game, OTHER_GAME[game])
        CSlotLinksDialog(
            document.m_shell, game, used, source_assets, target_assets, suggested, slotlinks.load(path), path
        ).exec()

    document.m_background.run(lambda: read_sides(app, game), done)
