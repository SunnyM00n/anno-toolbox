"""The progression tabs of the preview settings panel: what is unlocked (the preset, the tiers reached per region, a
checklist of the assets locked at the start) and the research tree (a preset, each node not researched, researched or a
count for the repeatable ones). The values are ``Defaults.progression``, ``tiers``, ``locked``, ``research`` and the
``options.research.<node>`` of each node the person set.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from . import previewdefaults as pd
from .previewcatalogue import UNUSED, Option
from .previewwidgets import IconCombo, Section, icon_at

DATA = Qt.ItemDataRole.UserRole
CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked


def _filter_tree(tree: QTreeWidget, text: str) -> None:
    """Show the leaves whose name holds every word of ``text`` and the groups that still have one."""
    words = text.lower().split()
    for g in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(g)
        shown = 0
        for i in range(group.childCount()):
            leaf = group.child(i)
            hide = not all(w in f"{leaf.text(0)} {group.text(0)}".lower() for w in words)
            leaf.setHidden(hide)
            shown += not hide
        group.setHidden(not shown)
        group.setExpanded(bool(words) or group.isExpanded())


class ProgressionSection(Section):
    def build(self) -> None:
        lay = QVBoxLayout(self)
        top = QFormLayout()
        tiers = self.cat.tiers or [Option(g, str(g), region) for region, rows in pd.TIERS.items() for g, _n in rows]
        self.preset = IconCombo()
        self.preset.fill([Option("all", tr("ui.preview_prog_all")), Option("start", tr("ui.preview_prog_start")),
                          *(Option(f"tier:{o.guid}", tr("ui.preview_prog_tier", tier=o.name, region=o.group))
                            for o in tiers)])  # fmt: skip
        self.preset.picked.connect(
            lambda v: self.panel.busy or self.store.set_many({"progression": None if v == "all" else v, "tiers": None})
        )  # a preset also drops the tiers set by hand
        self.row(top, tr("ui.preview_prog_preset"), self.preset, ("progression", "tiers"), tr("ui.preview_prog_tip"))
        lay.addLayout(top)
        box = QGroupBox(tr("ui.preview_tiers"))
        inner = QVBoxLayout(box)
        head = QHBoxLayout()
        note = QLabel(tr("ui.preview_tiers_tip"))
        note.setWordWrap(True)
        head.addWidget(note, 1)
        head.addWidget(self.panel.reset_button(("tiers",)))
        inner.addLayout(head)
        self.tiers: dict[int, tuple[QCheckBox, QSpinBox]] = {}
        residents = {g: n for rows in pd.TIERS.values() for g, n in rows}
        form = QFormLayout()
        for o in tiers:
            check, spin = QCheckBox(o.name), QSpinBox()
            spin.setRange(0, 1_000_000)
            spin.setValue(residents.get(o.guid, 0))
            spin.setToolTip(tr("ui.preview_residents_tip"))
            check.setToolTip(tr("ui.preview_tier_tip", region=o.group))
            check.toggled.connect(lambda _on: self._tiers_changed())
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda _v: self._tiers_changed())
            row = side_by_side(check, spin)
            form.addRow(o.group, row)
            self.tiers[o.guid] = (check, spin)
        inner.addLayout(form)
        lay.addWidget(box)
        self._build_locked(lay)

    def _build_locked(self, lay: QVBoxLayout) -> None:
        box = QGroupBox(tr("ui.preview_locked"))
        inner = QVBoxLayout(box)
        note = QLabel(tr("ui.preview_locked_tip"))
        note.setWordWrap(True)
        bar = QHBoxLayout()
        self.find = QLineEdit(placeholderText=tr("ui.preview_locked_find"))
        self.find.setToolTip(tr("ui.preview_locked_find_tip"))
        self.lock_shown = QPushButton(tr("ui.preview_lock_shown"))
        self.unlock_shown = QPushButton(tr("ui.preview_unlock_shown"))
        for button, on in ((self.lock_shown, True), (self.unlock_shown, False)):
            button.setToolTip(tr(N_("ui.preview_lock_shown_tip") if on else N_("ui.preview_unlock_shown_tip")))
            button.clicked.connect(lambda _c=False, on=on: self._set_shown(on))
            self.keyed.append(button)
        for widget in (self.find, self.lock_shown, self.unlock_shown, self.panel.reset_button(("locked",))):
            bar.addWidget(widget, 1 if widget is self.find else 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(24, 24))
        self.tree.setMinimumHeight(220)
        self.tree.setToolTip(tr("ui.preview_locked_list_tip"))
        groups: dict[str, QTreeWidgetItem] = {}
        for o in self.cat.unlocks:
            group = groups.get(o.group) or groups.setdefault(o.group, QTreeWidgetItem(self.tree, [o.group or "?"]))
            leaf = QTreeWidgetItem(group, [o.name])
            leaf.setData(0, DATA, o.guid)
            leaf.setData(0, DATA + 1, o.icon)
            leaf.setToolTip(0, f"{o.name} ({o.guid})")
            leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            leaf.setCheckState(0, UNCHECKED)
        self.tree.expandAll()
        self.tree.itemChanged.connect(lambda _item, _c: self._locked_changed())
        self.find.textChanged.connect(lambda text: _filter_tree(self.tree, text))
        inner.addWidget(note)
        inner.addLayout(bar)
        inner.addWidget(self.tree)
        lay.addWidget(box, 1)

    def _leaves(self):
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            for i in range(group.childCount()):
                yield group.child(i)

    def _set_shown(self, on: bool) -> None:
        self.panel.busy += 1
        try:
            for leaf in self._leaves():
                if not leaf.isHidden():
                    leaf.setCheckState(0, CHECKED if on else UNCHECKED)
        finally:
            self.panel.busy -= 1
        self._locked_changed()

    def _locked_changed(self) -> None:
        if self.panel.busy:
            return
        locked = sorted(leaf.data(0, DATA) for leaf in self._leaves() if leaf.checkState(0) == CHECKED)
        self.put("locked", locked or None)

    def _tiers_changed(self) -> None:
        if self.panel.busy:
            return
        reached = {g: spin.value() for g, (check, spin) in self.tiers.items() if check.isChecked()}
        self.put("tiers", reached or None)

    def refresh(self) -> None:
        d = self.d
        self.preset.select(d.progression)
        reached = d.reached()
        for guid, (check, spin) in self.tiers.items():
            check.setChecked(guid in reached)
            if reached.get(guid):
                spin.setValue(reached[guid])
        locked = set(d.locked)
        for leaf in self._leaves():
            leaf.setCheckState(0, CHECKED if leaf.data(0, DATA) in locked else UNCHECKED)
        self.update_icons()

    def update_icons(self) -> None:
        for leaf in self._leaves():
            leaf.setIcon(0, icon_at(self.panel.icons, leaf.data(0, DATA + 1)))


def side_by_side(*widgets):
    """The widgets side by side as one widget (a form row's field)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return box


class ResearchSection(Section):
    def build(self) -> None:
        lay = QVBoxLayout(self)
        top = QFormLayout()
        tiers = sorted({o.tier for o in self.cat.research})
        self.preset = IconCombo()
        self.preset.fill([Option("all", tr("ui.preview_research_all")), Option("none", tr("ui.preview_research_none")),
                          *(Option(f"tier:{t}", tr("ui.preview_research_tier", tier=t)) for t in tiers)])  # fmt: skip
        self.preset.picked.connect(self._preset)
        self.row(top, tr("ui.preview_research_preset"), self.preset, ("research",), tr("ui.preview_research_tip"))
        lay.addLayout(top)
        bar = QHBoxLayout()
        self.find = QLineEdit(placeholderText=tr("ui.preview_research_find"))
        self.find.setToolTip(tr("ui.preview_research_find_tip"))
        bar.addWidget(self.find, 1)
        bar.addWidget(self.panel.reset_button(("research", "options.research.*")))
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("ui.preview_node"), tr("ui.preview_row"), tr("ui.preview_count")])
        self.tree.setIconSize(QSize(24, 24))
        self.tree.setMinimumHeight(300)
        self.tree.setToolTip(tr("ui.preview_research_list_tip"))
        self.counts: dict[int, QSpinBox] = {}
        groups: dict[str, QTreeWidgetItem] = {}
        for o in sorted(
            self.cat.research, key=lambda o: (o.group == UNUSED, not o.group, o.dlc != 0, o.group, o.tier, o.name)
        ):
            if o.group not in groups:  # the base trees, then the DLCs' own, then what is in no tree
                shown = (
                    tr("ui.preview_research_unused")
                    if o.group == UNUSED
                    else o.group or tr("ui.preview_research_other")
                )
                groups[o.group] = QTreeWidgetItem(self.tree, [shown])
                groups[o.group].setData(0, DATA, shown)
                groups[o.group].setData(0, DATA + 1, o.dlc)
            group = groups[o.group]
            leaf = QTreeWidgetItem(group, [o.name, str(o.tier)])
            leaf.setData(0, DATA, o)
            leaf.setToolTip(0, f"{o.name} ({o.guid})")
            if o.repeatable:
                spin = QSpinBox()
                spin.setRange(0, 99)
                spin.setToolTip(tr("ui.preview_count_tip"))
                spin.valueChanged.connect(lambda v, o=o: self._node(o, v))
                self.tree.setItemWidget(leaf, 2, spin)
                self.counts[o.guid] = spin
            else:
                leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                leaf.setCheckState(0, UNCHECKED)
        self.tree.expandAll()
        self.tree.header().resizeSection(0, 280)
        self.tree.itemChanged.connect(self._checked)
        self.find.textChanged.connect(lambda text: _filter_tree(self.tree, text))
        lay.addWidget(self.tree, 1)

    def _leaves(self):
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            for i in range(group.childCount()):
                yield group.child(i)

    def _checked(self, item, column: int) -> None:
        o = item.data(0, DATA)
        if column == 0 and isinstance(o, Option) and not o.repeatable:
            self._node(o, 1 if item.checkState(0) == CHECKED else 0)

    def _node(self, o: Option, value: int) -> None:
        """A node's state: only what differs from the preset's is kept."""
        if self.panel.busy:
            return
        base = pd.Defaults(research=self.d.research).researched(o.guid, o.tier)
        self.put(f"options.research.{o.guid}", None if float(value) == base else float(value))

    def _preset(self, value: str) -> None:
        if self.panel.busy:
            return
        mine = {f"options.{k}": None for k in self.d.options if k.startswith("research.")}
        self.store.set_many({"research": None if value == "all" else value, **mine})  # a preset sets every node

    def refresh(self) -> None:
        d = self.d
        self.preset.select(d.research)
        for leaf in self._leaves():
            o = leaf.data(0, DATA)
            value = d.researched(o.guid, o.tier)
            if o.repeatable:
                self.counts[o.guid].setValue(int(value))
            else:
                leaf.setCheckState(0, CHECKED if value >= 1 else UNCHECKED)
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            done = sum(1 for i in range(group.childCount()) if self._done(group.child(i)))
            dlc = group.data(0, DATA + 1)
            owned = not dlc or d.dlcs is None or dlc in d.dlcs  # a DLC's tree is greyed while the DLC is not owned
            words = N_("ui.preview_group_count") if owned else N_("ui.preview_group_not_owned")
            group.setText(0, tr(words, name=group.data(0, DATA), done=done, total=group.childCount()))
            for item in (group, *(group.child(i) for i in range(group.childCount()))):
                item.setDisabled(not owned)
            for i in range(group.childCount()):
                spin = self.tree.itemWidget(group.child(i), 2)
                if spin is not None:
                    spin.setEnabled(owned)
        self.update_icons()

    def _done(self, leaf) -> bool:
        o = leaf.data(0, DATA)
        return self.counts[o.guid].value() > 0 if o.repeatable else leaf.checkState(0) == CHECKED

    def update_icons(self) -> None:
        for leaf in self._leaves():
            leaf.setIcon(0, icon_at(self.panel.icons, leaf.data(0, DATA).icon))
