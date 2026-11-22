"""The Changes tab's tree: the files of a report, below them the targets, below them the value changes. A model made on
demand (``CTargetModel``): a row exists only as a number until the view asks for its text, so a report of a hundred
thousand targets shows at once and no row is left out. A filter box narrows it (worked out off the window's thread).

``CDiffTree.set_report(report)`` fills it, ``set_filter(text)`` narrows it, ``describe(index)`` is the text of the
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
from ..suite.background import CBackground
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
    CTarget,
    SFileChange,
    SReport,
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


def matches(target: CTarget, needle: str) -> bool:
    """Whether the filter text is in the target's file, key, name, template, mods or one of its paths and values."""
    if not needle:
        return True

    def hay() -> Iterator[str]:
        yield from (target.m_file, target.m_key, target.m_name, target.m_template, *target.m_mods)
        for change in target.m_changes:
            yield change.path
            yield change.after or ""

    return any(needle in text.lower() for text in hay())


def describe_target(target: CTarget) -> str:
    """The detail text of a target: its lines, and an added target's values in full."""
    lines = target_lines(target)
    if target.m_kind == ADDED and target.m_packed:
        lines += ["", json.dumps(target.values(), indent=2, ensure_ascii=False)]
    return "\n".join(lines)


def describe_file(change: SFileChange) -> str:
    mods = f" ({', '.join(change.mods)})" if change.mods else ""
    return f"{change.path}\n{kind_text(change.kind)}, {change.size_bytes} bytes{mods}"


@dataclass
class SGroup:
    """The first level: a file (its targets) or the carried files; ``rows`` are what shows, ``total`` before the filter."""

    title: str
    rows: list = field(default_factory=list)
    total: int = 0


def build_groups(report: SReport | None, needle: str) -> list[SGroup]:
    """The groups of a report for a filter text (lower case; empty: all). Plain data, made on any thread."""
    if report is None:
        return []
    out = []
    for file, found in groupby(report.targets, key=lambda target: target.m_file):
        every = list(found)
        rows = [target for target in every if matches(target, needle)] if needle else every
        if rows:
            out.append(SGroup(file, rows, len(every)))
    files = [file_change for file_change in report.files if not needle or needle in file_change.path.lower()]
    if files:
        out.append(SGroup(tr("common.files"), files, len(report.files)))
    return out


def _id(level: int, group_index: int = 0, row_index: int = 0, change_index: int = 0) -> int:
    return level << (3 * BITS) | group_index << (2 * BITS) | row_index << BITS | change_index


def _parts(ident: int) -> tuple[int, int, int, int]:
    return ident >> (3 * BITS), ident >> (2 * BITS) & MASK, ident >> BITS & MASK, ident & MASK


class CTargetModel(QAbstractItemModel):
    """Groups, then each group's targets (or files), then a target's value changes; rows are numbers until asked for."""

    HEADERS = (N_("common.target"), N_("ui.kind"), N_("moddiff.before"), N_("moddiff.after"), N_("common.mods"))

    def __init__(self) -> None:
        super().__init__()
        self.m_groups: list[SGroup] = []
        self._m_where: dict[tuple[str, str], tuple[int, int]] | None = None

    def set_groups(self, groups: list[SGroup]) -> None:
        self.beginResetModel()
        self.m_groups, self._m_where = groups, None
        self.endResetModel()

    def columnCount(self, _parent=NO_PARENT) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.HEADERS[section])
        return None

    def index(self, row, column, parent=NO_PARENT):
        if not parent.isValid():
            return self.createIndex(row, column, _id(1, row)) if row < len(self.m_groups) else QModelIndex()
        level, group_index, row_index, _change_index = _parts(parent.internalId())
        if level == 1:
            return self.createIndex(row, column, _id(2, group_index, row))
        return self.createIndex(row, column, _id(3, group_index, row_index, row)) if level == 2 else QModelIndex()

    def parent(self, index):
        level, group_index, row_index, _change_index = _parts(index.internalId()) if index.isValid() else (0, 0, 0, 0)
        if level == 2:
            return self.createIndex(group_index, 0, _id(1, group_index))
        return self.createIndex(row_index, 0, _id(2, group_index, row_index)) if level == 3 else QModelIndex()

    def rowCount(self, parent=NO_PARENT) -> int:
        if not parent.isValid():
            return len(self.m_groups)
        level, group_index, row_index, _change_index = _parts(parent.internalId())
        if level == 1:
            return len(self.m_groups[group_index].rows)
        item = self.item(parent) if level == 2 else None
        return len(item.m_changes) if isinstance(item, CTarget) else 0

    def hasChildren(self, parent=NO_PARENT) -> bool:
        return self.rowCount(parent) > 0

    def item(self, index: QModelIndex):
        """What a row stands for: a ``SGroup``, a ``CTarget``, a ``SFileChange``, or a ``Change`` of a target."""
        level, group_index, row_index, change_index = _parts(index.internalId()) if index.isValid() else (0, 0, 0, 0)
        if level == 1:
            return self.m_groups[group_index]
        if level == 2:
            return self.m_groups[group_index].rows[row_index]
        return self.m_groups[group_index].rows[row_index].m_changes[change_index] if level == 3 else None

    def target_of(self, index: QModelIndex) -> CTarget | SFileChange | None:
        """The target (or file) a row belongs to: a value row's is the target above it."""
        if not index.isValid():
            return None
        level, group_index, row_index, _change_index = _parts(index.internalId())
        return self.m_groups[group_index].rows[row_index] if level >= 2 else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        level, column = _parts(index.internalId())[0], index.column()
        item = self.item(index)
        if role == ROLE:
            return self.target_of(index)
        if role == Qt.ItemDataRole.ToolTipRole and isinstance(item, CTarget) and column == 0:
            return item.m_template or None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if level == 1:
            return (item.title, f"{len(item.rows):,}")[column] if column < 2 else ""
        if isinstance(item, CTarget):
            return (item.title, kind_text(item.m_kind), "", "", ", ".join(item.m_mods))[column]
        if isinstance(item, SFileChange):
            return (item.path, kind_text(item.kind), "", "", ", ".join(item.mods))[column]
        return (item.path, "", item.before or "", item.after or "")[column] if column < 4 else ""

    def locate(self, file: str, key: str) -> QModelIndex:
        """The row of the target ``(file, key)``, an invalid index where the shown rows have none."""
        if self._m_where is None:
            self._m_where = {
                target.ident: (group_index, row_index)
                for group_index, group in enumerate(self.m_groups)
                for row_index, target in enumerate(group.rows)
                if isinstance(target, CTarget)
            }
        found = self._m_where.get((file, key))
        return self.index(found[1], 0, self.index(found[0], 0)) if found else QModelIndex()


class CDiffTree(QTreeView):
    selected = Signal(str)  # the detail text of the row chosen

    def __init__(self, is_synchronous: bool = False) -> None:
        super().__init__()
        self.m_is_synchronous = is_synchronous  # work the filter out here and now (a test)
        self.m_report: SReport | None = None
        self.m_needle = ""
        self.m_background = CBackground(self)
        self._m_wanted = 0  # the filter run whose answer is the one to show
        self.m_timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self.m_timer.timeout.connect(self.rebuild)
        self.m_model = CTargetModel()
        self.setModel(self.m_model)
        self.setUniformRowHeights(True)
        self.setColumnWidth(0, 420)
        self.selectionModel().currentChanged.connect(lambda current, _old: self.selected.emit(self.describe(current)))

    def set_report(self, report: SReport | None) -> None:
        self.m_report = report
        self.rebuild()

    def set_filter(self, text: str) -> None:
        self.m_needle = text.strip().lower()
        if self.m_is_synchronous:
            self.rebuild()
        else:
            self.m_timer.start()

    def settle(self) -> None:
        """Work out a filter typed a moment ago now (a jump to a target needs the rows the text now allows)."""
        if self.m_timer.isActive():
            self.m_timer.stop()
            self.rebuild()

    def rebuild(self) -> None:
        """Show the report through the filter: at once without one, else after the work off this thread."""
        self._m_wanted += 1
        wanted, report, needle = self._m_wanted, self.m_report, self.m_needle
        if not needle or self.m_is_synchronous:
            self._show(build_groups(report, needle))
            return
        self.m_background.run(lambda: build_groups(report, needle), lambda groups: self._shown(wanted, groups))

    def _shown(self, wanted: int, groups) -> None:
        if wanted == self._m_wanted and not isinstance(groups, Exception):
            self._show(groups)

    def _show(self, groups: list[SGroup]) -> None:
        self.m_model.set_groups(groups)
        if len(groups) <= 12 or self.m_needle:
            for position, group in enumerate(groups):
                if len(group.rows) <= EXPAND_ROWS:
                    self.expand(self.m_model.index(position, 0))

    def describe(self, index: QModelIndex | None) -> str:
        found = self.m_model.target_of(index) if index is not None else None
        if isinstance(found, CTarget):
            return describe_target(found)
        return describe_file(found) if isinstance(found, SFileChange) else ""

    def select_target(self, file: str, key: str) -> bool:
        """Select the row of a target; False where the shown report has none."""
        index = self.m_model.locate(file, key)
        if not index.isValid():
            return False
        self.setCurrentIndex(index)
        self.scrollTo(index)
        return True
