"""The Changes tab's tree: the files of a report, below them the targets, below them the value changes. A model made on
demand (``TargetModel``): a row exists only as a number until the view asks for its text, so a report of a hundred
thousand targets shows at once and no row is left out. A filter box narrows it (worked out off the window's thread).

``DiffTree.set_report(report)`` fills it, ``set_filter(text)`` narrows it, ``describe(index)`` is the text of the
selected row for the detail pane, ``select_target(file, key)`` jumps to one. ``docs/mod-diff.md`` "The views".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import groupby

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import QTreeView

from ..infra.i18n import N_, tr
from ..suite.background import Background
from .export import target_lines
from .model import (
    ADDED,
    CHANGED,
    DIFFERS,
    NEW,
    ONLY_A,
    ONLY_B,
    REMOVED,
    REPLACED,
    SAME,
    FileChange,
    Report,
    Target,
)  # fmt: skip

ROLE = Qt.ItemDataRole.UserRole
NO_PARENT = QModelIndex()  # the default parent of a model's questions: the top level
SETTLE_MS = 250  # the filter is worked out this long after the last key
EXPAND_ROWS = 20000  # a file with more targets than this is not opened by itself
BITS, MASK = 20, (1 << 20) - 1  # an index id holds the level and three row numbers
KINDS = {
    ADDED: N_("moddiff.added"), REMOVED: N_("moddiff.removed"), CHANGED: N_("common.changed"), ONLY_A: N_("moddiff.only_first"),
    ONLY_B: N_("moddiff.only_second"), DIFFERS: N_("moddiff.differs"), NEW: N_("moddiff.new_file"),
    REPLACED: N_("moddiff.replaces_games_file"), SAME: N_("moddiff.games_own_file"),
}  # fmt: skip


def kind_text(kind: str) -> str:
    return tr(KINDS[kind]) if kind in KINDS else kind


def matches(target: Target, needle: str) -> bool:
    """Whether the filter text is in the target's file, key, name, template, mods or one of its paths and values."""
    if not needle:
        return True

    def hay() -> Iterator[str]:
        yield from (target.file, target.key, target.name, target.template, *target.mods)
        for c in target.changes:
            yield c.path
            yield c.after or ""

    return any(needle in h.lower() for h in hay())


def describe_target(target: Target) -> str:
    """The detail text of a target: its lines, and an added target's values in full."""
    lines = target_lines(target)
    if target.kind == ADDED and target.packed:
        lines += ["", json.dumps(target.values(), indent=2, ensure_ascii=False)]
    return "\n".join(lines)


def describe_file(change: FileChange) -> str:
    mods = f" ({', '.join(change.mods)})" if change.mods else ""
    return f"{change.path}\n{kind_text(change.kind)}, {change.size} bytes{mods}"


@dataclass
class Group:
    """The first level: a file (its targets) or the carried files; ``rows`` are what shows, ``total`` before the filter."""

    title: str
    rows: list = field(default_factory=list)
    total: int = 0


def build_groups(report: Report | None, needle: str) -> list[Group]:
    """The groups of a report for a filter text (lower case; empty: all). Plain data, made on any thread."""
    if report is None:
        return []
    out = []
    for file, found in groupby(report.targets, key=lambda t: t.file):
        every = list(found)
        rows = [t for t in every if matches(t, needle)] if needle else every
        if rows:
            out.append(Group(file, rows, len(every)))
    files = [f for f in report.files if not needle or needle in f.path.lower()]
    if files:
        out.append(Group(tr("common.files"), files, len(report.files)))
    return out


def _id(level: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    return level << (3 * BITS) | a << (2 * BITS) | b << BITS | c


def _parts(ident: int) -> tuple[int, int, int, int]:
    return ident >> (3 * BITS), ident >> (2 * BITS) & MASK, ident >> BITS & MASK, ident & MASK


class TargetModel(QAbstractItemModel):
    """Groups, then each group's targets (or files), then a target's value changes; rows are numbers until asked for."""

    HEADERS = (N_("common.target"), N_("ui.kind"), N_("moddiff.before"), N_("moddiff.after"), N_("common.mods"))

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[Group] = []
        self._where: dict[tuple[str, str], tuple[int, int]] | None = None

    def set_groups(self, groups: list[Group]) -> None:
        self.beginResetModel()
        self.groups, self._where = groups, None
        self.endResetModel()

    def columnCount(self, _parent=NO_PARENT) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.HEADERS[section])
        return None

    def index(self, row, column, parent=NO_PARENT):
        if not parent.isValid():
            return self.createIndex(row, column, _id(1, row)) if row < len(self.groups) else QModelIndex()
        level, g, r, _c = _parts(parent.internalId())
        if level == 1:
            return self.createIndex(row, column, _id(2, g, row))
        return self.createIndex(row, column, _id(3, g, r, row)) if level == 2 else QModelIndex()

    def parent(self, index):
        level, g, r, _c = _parts(index.internalId()) if index.isValid() else (0, 0, 0, 0)
        if level == 2:
            return self.createIndex(g, 0, _id(1, g))
        return self.createIndex(r, 0, _id(2, g, r)) if level == 3 else QModelIndex()

    def rowCount(self, parent=NO_PARENT) -> int:
        if not parent.isValid():
            return len(self.groups)
        level, g, r, _c = _parts(parent.internalId())
        if level == 1:
            return len(self.groups[g].rows)
        item = self.item(parent) if level == 2 else None
        return len(item.changes) if isinstance(item, Target) else 0

    def hasChildren(self, parent=NO_PARENT) -> bool:
        return self.rowCount(parent) > 0

    def item(self, index: QModelIndex):
        """What a row stands for: a ``Group``, a ``Target``, a ``FileChange``, or a ``Change`` of a target."""
        level, g, r, c = _parts(index.internalId()) if index.isValid() else (0, 0, 0, 0)
        if level == 1:
            return self.groups[g]
        if level == 2:
            return self.groups[g].rows[r]
        return self.groups[g].rows[r].changes[c] if level == 3 else None

    def target_of(self, index: QModelIndex) -> Target | FileChange | None:
        """The target (or file) a row belongs to: a value row's is the target above it."""
        if not index.isValid():
            return None
        level, g, r, _c = _parts(index.internalId())
        return self.groups[g].rows[r] if level >= 2 else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        level, col = _parts(index.internalId())[0], index.column()
        item = self.item(index)
        if role == ROLE:
            return self.target_of(index)
        if role == Qt.ItemDataRole.ToolTipRole and isinstance(item, Target) and col == 0:
            return item.template or None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if level == 1:
            return (item.title, f"{len(item.rows):,}")[col] if col < 2 else ""
        if isinstance(item, Target):
            return (item.title, kind_text(item.kind), "", "", ", ".join(item.mods))[col]
        if isinstance(item, FileChange):
            return (item.path, kind_text(item.kind), "", "", ", ".join(item.mods))[col]
        return (item.path, "", item.before or "", item.after or "")[col] if col < 4 else ""

    def locate(self, file: str, key: str) -> QModelIndex:
        """The row of the target ``(file, key)``, an invalid index where the shown rows have none."""
        if self._where is None:
            self._where = {
                t.ident: (g, r)
                for g, group in enumerate(self.groups)
                for r, t in enumerate(group.rows)
                if isinstance(t, Target)
            }
        found = self._where.get((file, key))
        return self.index(found[1], 0, self.index(found[0], 0)) if found else QModelIndex()


class DiffTree(QTreeView):
    selected = Signal(str)  # the detail text of the row chosen

    def __init__(self, sync: bool = False) -> None:
        super().__init__()
        self.sync = sync  # work the filter out here and now (a test)
        self.report: Report | None = None
        self.needle = ""
        self.background = Background(self)
        self._wanted = 0  # the filter run whose answer is the one to show
        self.timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self.timer.timeout.connect(self.rebuild)
        self.model_ = TargetModel()
        self.setModel(self.model_)
        self.setUniformRowHeights(True)
        self.setColumnWidth(0, 420)
        self.selectionModel().currentChanged.connect(lambda cur, _old: self.selected.emit(self.describe(cur)))

    def set_report(self, report: Report | None) -> None:
        self.report = report
        self.rebuild()

    def set_filter(self, text: str) -> None:
        self.needle = text.strip().lower()
        if self.sync:
            self.rebuild()
        else:
            self.timer.start()

    def settle(self) -> None:
        """Work out a filter typed a moment ago now (a jump to a target needs the rows the text now allows)."""
        if self.timer.isActive():
            self.timer.stop()
            self.rebuild()

    def rebuild(self) -> None:
        """Show the report through the filter: at once without one, else after the work off this thread."""
        self._wanted += 1
        wanted, report, needle = self._wanted, self.report, self.needle
        if not needle or self.sync:
            self._show(build_groups(report, needle))
            return
        self.background.run(lambda: build_groups(report, needle), lambda groups: self._shown(wanted, groups))

    def _shown(self, wanted: int, groups) -> None:
        if wanted == self._wanted and not isinstance(groups, Exception):
            self._show(groups)

    def _show(self, groups: list[Group]) -> None:
        self.model_.set_groups(groups)
        if len(groups) <= 12 or self.needle:
            for n, group in enumerate(groups):
                if len(group.rows) <= EXPAND_ROWS:
                    self.expand(self.model_.index(n, 0))

    def describe(self, index: QModelIndex | None) -> str:
        found = self.model_.target_of(index) if index is not None else None
        if isinstance(found, Target):
            return describe_target(found)
        return describe_file(found) if isinstance(found, FileChange) else ""

    def select_target(self, file: str, key: str) -> bool:
        """Select the row of a target; False where the shown report has none."""
        index = self.model_.locate(file, key)
        if not index.isValid():
            return False
        self.setCurrentIndex(index)
        self.scrollTo(index)
        return True
