"""The island editor's Islands panel: per game a tab with its Native, Edited and From-other-games island lists and the
dialog for the game folders. The panel is a dock of the workbench (``desktop``); the tab that builds an island
for another game, where there is one, is ``desktopconvert``. Plan and use: ``docs/island-editor.md``.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import tr
from ...islands import regions
from ...othergames import installs
from ...suite import features, theme
from ...suite.widgets import access_keys, access_letters
from .. import archivedelivery, editorapp
from ..editlists import group_rows
from ..islandgroups import OTHER, OTHER_TITLE

GROUP_ROLE = Qt.ItemDataRole.UserRole + 1  # on the header rows of a grouped list
GAME_NAMES = {k: g.name for k, g in installs.GAMES.items()}


def tab_title(name: str) -> str:
    """A game's short tab text ("Anno 1404 (with Venice)" -> "1404"); the full name is the tab's tooltip."""
    return name.removeprefix("Anno ").split(" (")[0]


def file_stem(path: str) -> str:
    """An island's file name without folders and extension, lower case: the key of 2070's lists."""
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def offers_ports(key: str, rows: list) -> bool:
    """Whether a game's islands have "Port to region / game": another region of its generation, or an island of its
    list (``rows``: paths or (text, path, tooltip)) that an extension takes (``islandconvert.targets``)."""
    from ..islandconvert import targets

    return key in regions.GENERATION or any(
        targets(key, entry if isinstance(entry, str) else entry[1]) for entry in rows
    )


class CIslandList(QWidget):
    """A list of islands with a search; ``actions`` (label, callback(value)[, enabled]) act on the selected
    one. ``rows``: names, or (shown text, value, tooltip) triples. ``group_of(value)``: the group a row belongs
    to; the rows then sit under group headers that a click folds (the search unfolds the groups it finds in).
    ``disabled``: the values of rows that are listed but cannot be picked (greyed, the tooltip says why).
    ``avoid``: the access keys the window around it has given away (its menu bar's): no button takes one."""

    LIMIT = 2000  # rows shown at once: the search narrows the rest

    def __init__(
        self, rows: list, actions: list, note: str = "", group_of=None, order=(), disabled=(), avoid: str = ""
    ) -> None:
        super().__init__()
        self.m_rows = [(entry.rsplit("/", 1)[-1], entry, entry) if isinstance(entry, str) else entry for entry in rows]
        self.m_names = [entry[1] for entry in self.m_rows]
        self.m_group_of, self.m_order, self.m_disabled = group_of, order, frozenset(disabled)
        self.m_problems: dict = {}  # value -> why it did not open (``mark``): listed all the same
        self.m_collapsed: set[str] = set()
        self.m_search = QLineEdit(placeholderText=tr("common.search"))
        self.m_list = QListWidget()
        layout = QVBoxLayout(self)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addWidget(self.m_search)
        layout.addWidget(self.m_list, 1)
        row = QHBoxLayout()
        self.m_buttons = []
        keyed = access_keys((spec[0] for spec in actions), avoid)  # each button: Alt and a letter of its own
        for action, label in zip(actions, keyed, strict=True):
            act = action[1]
            button = QPushButton(label)
            button.setEnabled(action[2] if len(action) > 2 else True)
            button.clicked.connect(lambda _=False, act=act: self._act(act))
            row.addWidget(button)
            self.m_buttons.append(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.m_search.textChanged.connect(self.fill)
        first = next((spec[1] for spec in actions if len(spec) < 3 or spec[2]), None)
        self.m_list.itemDoubleClicked.connect(lambda _item: first and self._act(first))
        self.m_list.itemClicked.connect(self._toggle)
        self.fill()

    def fill(self) -> None:
        query = self.m_search.text().strip().lower()
        shown = [
            entry for entry in self.m_rows if not query or query in entry[0].lower() or query in str(entry[1]).lower()
        ][: self.LIMIT]
        bar = self.m_list.verticalScrollBar()
        keep, scroll = self.current_value(), bar.value()
        self.m_list.clear()
        groups = group_rows(shown, lambda entry: self.m_group_of(entry[1]), self.m_order) if self.m_group_of else []
        if not groups or [name for name, _ in groups] == [OTHER]:  # nothing to group by: a plain list
            groups = [("", shown)]
        for group, rows in groups:
            closed = bool(group) and group in self.m_collapsed and not query
            if group:
                self._add_header(group, len(rows), closed)
            for row in () if closed else rows:
                self._add_row(*row)
        for i in range(self.m_list.count()):  # the island that was selected stays so, if it is still shown
            if keep is not None and self.m_list.item(i).data(Qt.ItemDataRole.UserRole) == keep:
                self.m_list.setCurrentRow(i)
        bar.setValue(scroll)

    def mark(self, value, reason: str) -> None:
        """Say on the list that an island did not open (it stays listed, with the reason as text and tooltip)."""
        self.m_problems[value] = reason
        self.fill()

    def _add_row(self, text: str, value, tip: str) -> None:
        if value in self.m_problems:
            reason = self.m_problems[value]
            text, tip = tr("islands.text_cannot_opened_reason", text=text, reason=reason), reason
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setToolTip(tip)
        if value in self.m_disabled:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.m_list.addItem(item)

    def _add_header(self, group: str, count: int, closed: bool) -> None:
        shown = tr(OTHER_TITLE) if group == OTHER else group  # the one group name that is ours, not a game's
        item = QListWidgetItem(f"{'▸' if closed else '▾'}  {shown}  ({count})")
        item.setData(GROUP_ROLE, group)
        colors = theme.colors()  # not the palette: the style sheet paints the list's own
        item.setBackground(QColor(colors["raised"]))
        item.setForeground(QColor(colors["text"]))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.m_list.addItem(item)

    def _toggle(self, item: QListWidgetItem) -> None:
        group = item.data(GROUP_ROLE)
        if group is not None and not self.m_search.text().strip():  # a search shows every group it found in
            self.m_collapsed ^= {group}
            self.fill()

    def current_value(self):
        item = self.m_list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _act(self, act) -> None:
        value = self.current_value()
        if value is not None:
            act(value)


def taken_keys(main) -> str:
    """The access keys the window's menu bar and ribbon tabs carry: a button beside them takes none of these."""
    ribbon = getattr(main, "m_ribbon", None)
    tabs = [ribbon.m_bar.tabText(k) for k in range(ribbon.m_bar.count())] if ribbon is not None else []
    return access_letters([*(action.text() for action in main.menuBar().actions()), *tabs])


class CGameTab(QWidget):
    """One game: its Native, Edited and From-other-games islands."""

    def __init__(self, main, key: str, entry: dict) -> None:
        super().__init__()
        self.m_key = key
        layout = QVBoxLayout(self)
        if entry.get("note"):
            note = QLabel(entry["note"])
            note.setWordWrap(True)
            layout.addWidget(note)
        self.m_tabs = QTabWidget()
        layout.addWidget(self.m_tabs, 1)
        taken = taken_keys(main)
        build_note = "" if entry["buildable"] else tr("islands.builds_not_available_game_yet")
        native_kind = key
        groups, order = entry.get("groups", {}), entry.get("group_order", ())
        native_group = (lambda value: groups.get(value) or groups.get(file_stem(value)) or OTHER) if groups else None
        native_actions = [(tr("common.edit"), lambda name: main.open(native_kind, name, is_waiting=False))]
        if key in editorapp.BLANKABLE:  # a blank island: this one with its objects taken off (``App.open_blank``)
            native_actions.append((tr("islands.new_blank_island"), lambda name: main.open_blank(key, name)))
            native_actions.append(
                (tr("islands.new_island_shaped"), lambda name: main.open_blank(key, name, is_shaped=True))
            )
        if offers_ports(key, entry["native"]):  # another region, the other game, an extension's (islandconvert)
            native_actions.append((tr("islands.port_to_region_game"), lambda name: main.convert_region(key, name)))
        if key == "anno117":  # a new island from scratch at a grid size asked for (blankisland)
            native_actions.append((tr("islands.new_island_dots"), lambda _n: main.open_blank117()))
        if key == "anno2070":  # the same in 2070's own island form (formats.isd.blank)
            native_actions.append((tr("islands.new_island_dots"), lambda _n: main.open_blank2070()))
        self.m_native = CIslandList(
            entry["native"], native_actions,
            tr("islands.opening_one_starts_edit_games"),
            native_group, order, avoid=taken,
        )  # fmt: skip
        edited_rows = [
            (
                edited_entry["name"],
                edited_entry.get("open", edited_entry["source"]),
                edited_entry["source"]
                + (
                    " · " + tr("islands.saved_as_target", target=edited_entry["target"])
                    if edited_entry.get("target")
                    else ""
                ),
            )
            for edited_entry in entry["edited"]
        ]
        edit_actions = [(tr("common.edit"), lambda source: main.open(native_kind, source, is_waiting=False))]
        if key in editorapp.BUILDABLE:
            install = tr("islands.build_install_2070") if key == "anno2070" else tr("common.build_install")
            edit_actions += [
                (install, lambda _s: main.build_kind(key, True)),
                (tr("common.build"), lambda _s: main.build_kind(key, False)),
            ]
        else:
            edit_actions += [(tr("islands.build_not_available_yet"), lambda _s: None, False)]
        self.m_edited = CIslandList(edited_rows, edit_actions, build_note, native_group, order, avoid=taken)
        conv_rows = [
            (item["name"] + "  ·  "
             + tr("islands.from_game_source", game=GAME_NAMES.get(item["source_game"], item["source_game"]), source=item["source"])
             + ("  ·  " + tr("islands.hand_edits") if item.get("edited") else ""),
             item["name"],
             tr("islands.built_time", time=datetime.fromtimestamp(item["built"]).strftime("%Y-%m-%d %H:%M")) if item.get("built") else item["name"])
            for item in entry["converted"]
        ]  # fmt: skip
        converted_groups = {item["name"]: item["group"] for item in entry["converted"] if item.get("group")}
        rebuild = [
            (tr("islands.rebuild_install"), lambda name: main.rebuild(name, True)),
            (tr("islands.rebuild"), lambda name: main.rebuild(name, False)),
        ]
        self.m_converted = CIslandList(
            conv_rows,
            [(tr("common.edit"), lambda name: main.open("built", name, is_waiting=False)), *(rebuild if features.extensions(features.BUILDERS) else [])],
            tr("islands.islands_built_game_another_one"),
            (lambda value: converted_groups.get(value)) if converted_groups else None,
            sorted({*converted_groups.values()} - {OTHER}),
            avoid=taken,
        )  # fmt: skip
        self.m_tabs.addTab(self.m_native, tr("islands.native_n", n=len(entry["native"])))
        self.m_tabs.addTab(self.m_edited, tr("islands.edited_n", n=len(entry["edited"])))
        if entry["converted"] or features.extensions(features.BUILDERS):  # an edition without a build has none to list
            self.m_tabs.addTab(self.m_converted, tr("islands.other_games_n", n=len(entry["converted"])))
        self.m_lists = (self.m_native, self.m_edited, self.m_converted)
        if main.m_installed.get(key):
            button = QPushButton(tr(archivedelivery.ISLAND_DELIVERIES[key].restore_label))
            button.clicked.connect(lambda: main.restore(key))
            layout.addWidget(button)
