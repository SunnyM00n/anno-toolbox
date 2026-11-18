"""The tables under the mod tree (Qt): the load order the loader gives the mods that are on, the problems found before
the start, and what the last start's log said per mod. ``OrderView``, ``IssueView`` and ``LogView`` are the entries;
``docs/mod-manager.md`` "The window".
"""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from ..infra.i18n import tr
from ..modloader.loadlog import LoadLog
from .checks import PROBLEM, Issue, Plan

BAD = QBrush(QColor("#c0392b"))


def _setup(tree: QTreeWidget, columns: list[str], root_decorated: bool = False) -> None:
    tree.setColumnCount(len(columns))
    tree.setHeaderLabels(columns)
    tree.setRootIsDecorated(root_decorated)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)
    tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tree.header().setStretchLastSection(True)


class OrderView(QTreeWidget):
    """The mods in the order the loader applies them: a later mod's files replace an earlier one's."""

    def __init__(self) -> None:
        super().__init__()
        _setup(self, ["#", "ModID", tr("common.name"), tr("common.version"), tr("mods.why_here")])

    def set_plan(self, plan: Plan | None) -> None:
        self.clear()
        for row in plan.rows if plan else ():
            QTreeWidgetItem(
                self, [str(row.position), row.mod.mod_id, row.mod.name or row.mod.folder.name, row.mod.version, row.why]
            )


class IssueView(QTreeWidget):
    """The problems and notes, the problems first."""

    def __init__(self) -> None:
        super().__init__()
        _setup(self, ["", tr("mods.mod"), tr("mods.rule"), tr("mods.what")])

    def set_issues(self, issues: list[Issue]) -> None:
        self.clear()
        for issue in sorted(issues, key=lambda i: (i.level != PROBLEM, i.mod, i.code)):
            item = QTreeWidgetItem(
                self,
                [
                    tr("mods.problem_lower") if issue.level == PROBLEM else tr("mods.note_lower"),
                    issue.mod,
                    issue.code,
                    issue.text,
                ],
            )
            item.setToolTip(3, issue.text)
            if issue.level == PROBLEM:
                item.setForeground(0, BAD)


class LogView(QTreeWidget):
    """The last start per mod: loaded or not, the patch time, and each problem line below its mod."""

    def __init__(self) -> None:
        super().__init__()
        _setup(
            self,
            [tr("mods.mod"), tr("common.version"), tr("mods.last_start"), tr("mods.patch_time")],
            root_decorated=True,
        )
        self.summary = ""

    def set_log(self, log: LoadLog | None) -> str:
        """Show ``log``; returns its one-line summary (what to tell the person when there is none)."""
        self.clear()
        if log is None:
            self.summary = tr("mods.no_mod_loader_log_mods")
            return self.summary
        for mod_id in log.order + [m for m in log.mods if m not in log.order]:
            run = log.mods[mod_id]
            said = tr("mods.loaded") if run.loaded else tr("mods.enabled_not_loaded")
            time = f"{run.seconds:.1f} s" if run.seconds is not None else ""
            item = QTreeWidgetItem(self, [mod_id, run.version, said, time])
            for line in run.problems:
                QTreeWidgetItem(item, [line]).setForeground(0, BAD)
            item.setExpanded(bool(run.problems))
        if log.problems:
            item = QTreeWidgetItem(self, [tr("mods.no_mod"), "", tr("mods.n_lines", n=len(log.problems)), ""])
            for line in log.problems:
                QTreeWidgetItem(item, [line]).setForeground(0, BAD)
            item.setExpanded(True)
        total = tr("mods.patched_seconds_s", seconds=log.seconds) if log.seconds is not None else ""
        counted = (
            tr("mods.n_mods_enabled", n=log.enabled) if log.enabled is not None else tr("mods.n_mods", n=len(log.mods))
        )
        self.summary = tr(
            "mods.problem_lines_log",
            counted=counted,
            total=total,
            n=log.problem_count,
            when=_when(log.modified),
        )
        return self.summary


def _when(stamp: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
