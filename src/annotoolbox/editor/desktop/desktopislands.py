"""The island editor's Islands panel: per game a tab with its Native, Edited and From-other-games island lists and the
dialog for the game folders. The panel is a dock of the workbench (``desktop``); the tab that builds an island
for another game, where there is one, is ``desktopconvert``. Plan and use: ``docs/island-editor.md``.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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

from ...infra.i18n import N_, languages, tr
from ...islands import regions
from ...othergames import installs
from ...suite import features, theme
from ...suite.settings import THEMES, Settings
from .. import archivedelivery, editorapp
from ..editlists import group_rows
from ..islandgroups import OTHER

OPTIONAL_INSTALL = N_("{game} install (optional)")
REQUIRED_INSTALL = N_("{game} install")  # the optional one, for an application that needs the game
FOLDERS = (  # (settings key, label text; the {game} place takes the name of ``installs.GAMES[key]``)
    ("anno117", N_("Anno 117 install")),
    ("anno2070", N_("Anno 2070 install")),
    *((key, OPTIONAL_INSTALL) for key in installs.GAMES if key not in ("anno117", "anno2070")),
    ("mods", N_("Anno 117 mods folder")),
    ("mods1800", N_("Anno 1800 mods folder (Build and install copies there)")),
    ("patch2205", N_("Anno 2205 folder to patch (Build and install changes its maindata/file.db)")),
    ("workspace", N_("Workspace")),
)
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1  # on the header rows of a grouped list
THEME_LABELS = {"system": N_("System (follows Windows)"), "light": N_("Light"), "dark": N_("Dark")}
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

    return key in regions.GENERATION or any(targets(key, r if isinstance(r, str) else r[1]) for r in rows)


def folders_for(games: tuple[str, ...] | None) -> tuple:
    """The folders the settings ask for: all, or (an application restricted to ``games``) theirs and the workspace."""
    if games is None:
        return FOLDERS
    return tuple(
        (key, REQUIRED_INSTALL if label == OPTIONAL_INSTALL else label)
        for key, label in FOLDERS
        if key in games or key in ("workspace", "mods1800")
    )


class SettingsDialog(QDialog):
    """The game folders, the workspace and the theme, each folder with a picker."""

    def __init__(self, settings: Settings, parent=None, games: tuple[str, ...] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Games and folders"))
        self.edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for key, label in folders_for(games):
            edit = QLineEdit(getattr(settings, key))
            pick = QPushButton(tr("Browse…"))
            pick.clicked.connect(lambda _=False, e=edit: self._pick(e))
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(pick)
            form.addRow(tr(label, game=installs.GAMES[key].name if key in installs.GAMES else ""), row)
            self.edits[key] = edit
        self.theme = QComboBox()
        for key in THEMES:
            self.theme.addItem(tr(THEME_LABELS[key]), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        form.addRow(tr("Theme"), self.theme)
        self.language = QComboBox()
        for code, name in languages().items():
            self.language.addItem(name, code)  # i18n: ok (each language's own name)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.language)))
        self.language.setToolTip(tr("The language of the apps' windows; a change applies when an app is next started"))
        form.addRow(tr("Language"), self.language)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(760, 0)

    def _pick(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder"), edit.text())
        if folder:
            edit.setText(folder)

    def values(self) -> dict[str, str]:
        return {
            **{k: e.text().strip() for k, e in self.edits.items()},
            "theme": self.theme.currentData(),
            "language": self.language.currentData(),
        }


class IslandList(QWidget):
    """A list of islands with a search; ``actions`` (label, callback(value)[, enabled]) act on the selected
    one. ``rows``: names, or (shown text, value, tooltip) triples. ``group_of(value)``: the group a row belongs
    to; the rows then sit under group headers that a click folds (the search unfolds the groups it finds in).
    ``disabled``: the values of rows that are listed but cannot be picked (greyed, the tooltip says why)."""

    LIMIT = 2000  # rows shown at once: the search narrows the rest

    def __init__(self, rows: list, actions: list, note: str = "", group_of=None, order=(), disabled=()) -> None:
        super().__init__()
        self.rows = [(r.rsplit("/", 1)[-1], r, r) if isinstance(r, str) else r for r in rows]
        self.names = [r[1] for r in self.rows]
        self.group_of, self.order, self.disabled = group_of, order, frozenset(disabled)
        self.problems: dict = {}  # value -> why it did not open (``mark``): listed all the same
        self.collapsed: set[str] = set()
        self.search = QLineEdit(placeholderText=tr("search"))
        self.list = QListWidget()
        layout = QVBoxLayout(self)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.buttons = []
        for action in actions:
            label, act = action[:2]
            b = QPushButton(label)
            b.setEnabled(action[2] if len(action) > 2 else True)
            b.clicked.connect(lambda _=False, act=act: self._act(act))
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch(1)
        layout.addLayout(row)
        self.search.textChanged.connect(self.fill)
        first = next((a[1] for a in actions if len(a) < 3 or a[2]), None)
        self.list.itemDoubleClicked.connect(lambda _item: first and self._act(first))
        self.list.itemClicked.connect(self._toggle)
        self.fill()

    def fill(self) -> None:
        q = self.search.text().strip().lower()
        shown = [r for r in self.rows if not q or q in r[0].lower() or q in str(r[1]).lower()][: self.LIMIT]
        bar = self.list.verticalScrollBar()
        keep, scroll = self.current_value(), bar.value()
        self.list.clear()
        groups = group_rows(shown, lambda r: self.group_of(r[1]), self.order) if self.group_of else []
        if not groups or [g for g, _ in groups] == [OTHER]:  # nothing to group by: a plain list
            groups = [("", shown)]
        for group, rows in groups:
            closed = bool(group) and group in self.collapsed and not q
            if group:
                self._add_header(group, len(rows), closed)
            for row in () if closed else rows:
                self._add_row(*row)
        for i in range(self.list.count()):  # the island that was selected stays so, if it is still shown
            if keep is not None and self.list.item(i).data(Qt.ItemDataRole.UserRole) == keep:
                self.list.setCurrentRow(i)
        bar.setValue(scroll)

    def mark(self, value, reason: str) -> None:
        """Say on the list that an island did not open (it stays listed, with the reason as text and tooltip)."""
        self.problems[value] = reason
        self.fill()

    def _add_row(self, text: str, value, tip: str) -> None:
        if value in self.problems:
            reason = self.problems[value]
            text, tip = tr("{text}  ·  cannot be opened: {reason}", text=text, reason=reason), reason
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setToolTip(tip)
        if value in self.disabled:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.addItem(item)

    def _add_header(self, group: str, count: int, closed: bool) -> None:
        shown = tr(group) if group == OTHER else group  # the one group name that is ours, not a game's
        item = QListWidgetItem(f"{'▸' if closed else '▾'}  {shown}  ({count})")
        item.setData(GROUP_ROLE, group)
        colors = theme.colors()  # not the palette: the style sheet paints the list's own
        item.setBackground(QColor(colors["raised"]))
        item.setForeground(QColor(colors["text"]))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.list.addItem(item)

    def _toggle(self, item: QListWidgetItem) -> None:
        group = item.data(GROUP_ROLE)
        if group is not None and not self.search.text().strip():  # a search shows every group it found in
            self.collapsed ^= {group}
            self.fill()

    def current_value(self):
        item = self.list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _act(self, act) -> None:
        value = self.current_value()
        if value is not None:
            act(value)


class GameTab(QWidget):
    """One game: its Native, Edited and From-other-games islands."""

    def __init__(self, main, key: str, entry: dict) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        if entry.get("note"):
            note = QLabel(entry["note"])
            note.setWordWrap(True)
            layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        build_note = (
            "" if entry["buildable"] else tr("Builds: not available for this game yet (the edit file is kept).")
        )
        native_kind = {"anno117": "anno117", "anno2070": "anno2070"}.get(key, key)
        groups, order = entry.get("groups", {}), entry.get("group_order", ())
        native_group = (lambda v: groups.get(v) or groups.get(file_stem(v)) or OTHER) if groups else None
        native_actions = [(tr("Edit"), lambda n: main.open(native_kind, n, wait=False))]
        if key in editorapp.BLANKABLE:  # a blank island: this one with its objects taken off (``App.open_blank``)
            native_actions.append((tr("New blank island"), lambda n: main.open_blank(key, n)))
            native_actions.append((tr("New island (shaped)"), lambda n: main.open_blank(key, n, shaped=True)))
        if offers_ports(key, entry["native"]):  # another region, the other game, an extension's (islandconvert)
            native_actions.append((tr("Port to region / game…"), lambda n: main.convert_region(key, n)))
        if key == "anno117":  # a new island from scratch at a grid size asked for (blankisland)
            native_actions.append((tr("New island…"), lambda _n: main.open_blank117()))
        if key == "anno2070":  # the same in 2070's own island form (formats.isd.blank)
            native_actions.append((tr("New island…"), lambda _n: main.open_blank2070()))
        self.native = IslandList(
            entry["native"], native_actions,
            tr("Opening one starts an edit of it; the game's own file is never changed: the edits go into an edit file."),
            native_group, order,
        )  # fmt: skip
        edited_rows = [
            (
                e["name"],
                e.get("open", e["source"]),
                e["source"] + (" · " + tr("saved as {target}", target=e["target"]) if e.get("target") else ""),
            )
            for e in entry["edited"]
        ]
        edit_actions = [(tr("Edit"), lambda src: main.open(native_kind, src, wait=False))]
        if key in editorapp.BUILDABLE:
            install = tr("Build && install into 2070") if key == "anno2070" else tr("Build && install")
            edit_actions += [
                (install, lambda _s: main.build_kind(key, True)),
                (tr("Build"), lambda _s: main.build_kind(key, False)),
            ]
        else:
            edit_actions += [(tr("Build (not available yet)"), lambda _s: None, False)]
        self.edited = IslandList(edited_rows, edit_actions, build_note, native_group, order)
        conv_rows = [
            (c["name"] + "  ·  "
             + tr("from {game} {source}", game=GAME_NAMES.get(c["source_game"], c["source_game"]), source=c["source"])
             + ("  ·  " + tr("hand edits") if c.get("edited") else ""),
             c["name"],
             tr("built {time}", time=datetime.fromtimestamp(c["built"]).strftime("%Y-%m-%d %H:%M")) if c.get("built") else c["name"])
            for c in entry["converted"]
        ]  # fmt: skip
        converted_groups = {c["name"]: c["group"] for c in entry["converted"] if c.get("group")}
        rebuild = [
            (tr("Rebuild && install"), lambda n: main.rebuild(n, True)),
            (tr("Rebuild"), lambda n: main.rebuild(n, False)),
        ]
        self.converted = IslandList(
            conv_rows,
            [(tr("Edit"), lambda n: main.open("built", n, wait=False)), *(rebuild if features.extensions(features.BUILDERS) else [])],
            tr("Islands built for this game from another one (your workspace's builds)."),
            (lambda v: converted_groups.get(v)) if converted_groups else None,
            sorted({*converted_groups.values()} - {OTHER}),
        )  # fmt: skip
        self.tabs.addTab(self.native, tr("Native ({n})", n=len(entry["native"])))
        self.tabs.addTab(self.edited, tr("Edited ({n})", n=len(entry["edited"])))
        if entry["converted"] or features.extensions(features.BUILDERS):  # an edition without a build has none to list
            self.tabs.addTab(self.converted, tr("From other games ({n})", n=len(entry["converted"])))
        self.lists = (self.native, self.edited, self.converted)
        if main.installed.get(key):
            b = QPushButton(tr(archivedelivery.ISLAND_DELIVERIES[key].restore_label))
            b.clicked.connect(lambda: main.restore(key))
            layout.addWidget(b)
