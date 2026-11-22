"""The progression tabs of the preview settings panel: what is unlocked (the preset, the tiers reached per region, a
checklist of the assets locked at the start) and the research tree (a preset, each node not researched, researched or a
count for the repeatable ones). The values are ``SDefaults.progression``, ``tiers``, ``locked``, ``research`` and the
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
from .previewcatalogue import UNUSED, SOption
from .previewwidgets import CIconCombo, CSection, icon_at

DATA = Qt.ItemDataRole.UserRole
CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked


def _filter_tree(tree: QTreeWidget, text: str) -> None:
    """Show the leaves whose name holds every word of ``text`` and the groups that still have one."""
    words = text.lower().split()
    for group_index in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(group_index)
        shown = 0
        for i in range(group.childCount()):
            leaf = group.child(i)
            hide = not all(word in f"{leaf.text(0)} {group.text(0)}".lower() for word in words)
            leaf.setHidden(hide)
            shown += not hide
        group.setHidden(not shown)
        group.setExpanded(bool(words) or group.isExpanded())


class _CTreeSection(CSection):
    """A section that holds a two-level tree (groups with leaves) in ``self.m_tree``."""

    def _leaves(self):
        for group_index in range(self.m_tree.topLevelItemCount()):
            group = self.m_tree.topLevelItem(group_index)
            for i in range(group.childCount()):
                yield group.child(i)


class CProgressionSection(_CTreeSection):
    def build(self) -> None:
        layout = QVBoxLayout(self)
        top = QFormLayout()
        tiers = self.cat.tiers or [
            SOption(guid, str(guid), region) for region, rows in pd.TIERS.items() for guid, _n in rows
        ]
        self.m_preset = CIconCombo()
        self.m_preset.fill([SOption("all", tr("ui.preview_prog_all")), SOption("start", tr("ui.preview_prog_start")),
                          *(SOption(f"tier:{option.guid}", tr("ui.preview_prog_tier", tier=option.name, region=option.group))
                            for option in tiers)])  # fmt: skip
        self.m_preset.picked.connect(
            lambda choice: (
                self.m_panel.m_busy
                or self.store.set_many({"progression": None if choice == "all" else choice, "tiers": None})
            )
        )  # a preset also drops the tiers set by hand
        self.row(top, tr("ui.preview_prog_preset"), self.m_preset, ("progression", "tiers"), tr("ui.preview_prog_tip"))
        layout.addLayout(top)
        box = QGroupBox(tr("ui.preview_tiers"))
        inner = QVBoxLayout(box)
        head = QHBoxLayout()
        note = QLabel(tr("ui.preview_tiers_tip"))
        note.setWordWrap(True)
        head.addWidget(note, 1)
        head.addWidget(self.m_panel.reset_button(("tiers",)))
        inner.addLayout(head)
        self.m_tiers: dict[int, tuple[QCheckBox, QSpinBox]] = {}
        residents = {guid: resident_count for rows in pd.TIERS.values() for guid, resident_count in rows}
        form = QFormLayout()
        for option in tiers:
            check, spin = QCheckBox(option.name), QSpinBox()
            spin.setRange(0, 1_000_000)
            spin.setValue(residents.get(option.guid, 0))
            spin.setToolTip(tr("ui.preview_residents_tip"))
            check.setToolTip(tr("ui.preview_tier_tip", region=option.group))
            check.toggled.connect(lambda _on: self._tiers_changed())
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda _v: self._tiers_changed())
            row = side_by_side(check, spin)
            form.addRow(option.group, row)
            self.m_tiers[option.guid] = (check, spin)
        inner.addLayout(form)
        layout.addWidget(box)
        self._build_locked(layout)

    def _build_locked(self, layout: QVBoxLayout) -> None:
        box = QGroupBox(tr("ui.preview_locked"))
        inner = QVBoxLayout(box)
        note = QLabel(tr("ui.preview_locked_tip"))
        note.setWordWrap(True)
        bar = QHBoxLayout()
        self.find = QLineEdit(placeholderText=tr("ui.preview_locked_find"))
        self.find.setToolTip(tr("ui.preview_locked_find_tip"))
        self.m_lock_shown = QPushButton(tr("ui.preview_lock_shown"))
        self.m_unlock_shown = QPushButton(tr("ui.preview_unlock_shown"))
        for button, lock in ((self.m_lock_shown, True), (self.m_unlock_shown, False)):
            button.setToolTip(tr(N_("ui.preview_lock_shown_tip") if lock else N_("ui.preview_unlock_shown_tip")))
            button.clicked.connect(lambda _c=False, lock=lock: self._set_shown(lock))
            self.m_keyed.append(button)
        for widget in (self.find, self.m_lock_shown, self.m_unlock_shown, self.m_panel.reset_button(("locked",))):
            bar.addWidget(widget, 1 if widget is self.find else 0)
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderHidden(True)
        self.m_tree.setIconSize(QSize(24, 24))
        self.m_tree.setMinimumHeight(220)
        self.m_tree.setToolTip(tr("ui.preview_locked_list_tip"))
        groups: dict[str, QTreeWidgetItem] = {}
        for option in self.cat.unlocks:
            group = groups.get(option.group) or groups.setdefault(
                option.group, QTreeWidgetItem(self.m_tree, [option.group or "?"])
            )
            leaf = QTreeWidgetItem(group, [option.name])
            leaf.setData(0, DATA, option.guid)
            leaf.setData(0, DATA + 1, option.icon)
            leaf.setToolTip(0, f"{option.name} ({option.guid})")
            leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            leaf.setCheckState(0, UNCHECKED)
        self.m_tree.expandAll()
        self.m_tree.itemChanged.connect(lambda _item, _c: self._locked_changed())
        self.find.textChanged.connect(lambda text: _filter_tree(self.m_tree, text))
        inner.addWidget(note)
        inner.addLayout(bar)
        inner.addWidget(self.m_tree)
        layout.addWidget(box, 1)

    def _set_shown(self, is_locked: bool) -> None:
        self.m_panel.m_busy += 1
        try:
            for leaf in self._leaves():
                if not leaf.isHidden():
                    leaf.setCheckState(0, CHECKED if is_locked else UNCHECKED)
        finally:
            self.m_panel.m_busy -= 1
        self._locked_changed()

    def _locked_changed(self) -> None:
        if self.m_panel.m_busy:
            return
        locked = sorted(leaf.data(0, DATA) for leaf in self._leaves() if leaf.checkState(0) == CHECKED)
        self.put("locked", locked or None)

    def _tiers_changed(self) -> None:
        if self.m_panel.m_busy:
            return
        reached = {guid: spin.value() for guid, (check, spin) in self.m_tiers.items() if check.isChecked()}
        self.put("tiers", reached or None)

    def refresh(self) -> None:
        defaults = self.d
        self.m_preset.select(defaults.progression)
        reached = defaults.reached()
        for guid, (check, spin) in self.m_tiers.items():
            check.setChecked(guid in reached)
            if reached.get(guid):
                spin.setValue(reached[guid])
        locked = set(defaults.locked)
        for leaf in self._leaves():
            leaf.setCheckState(0, CHECKED if leaf.data(0, DATA) in locked else UNCHECKED)
        self.update_icons()

    def update_icons(self) -> None:
        for leaf in self._leaves():
            leaf.setIcon(0, icon_at(self.m_panel.m_icons, leaf.data(0, DATA + 1)))


def side_by_side(*widgets):
    """The widgets side by side as one widget (a form row's field)."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return box


class CResearchSection(_CTreeSection):
    def build(self) -> None:
        layout = QVBoxLayout(self)
        top = QFormLayout()
        tiers = sorted({option.tier for option in self.cat.research})
        self.m_preset = CIconCombo()
        self.m_preset.fill([SOption("all", tr("ui.preview_research_all")), SOption("none", tr("ui.preview_research_none")),
                          *(SOption(f"tier:{tier}", tr("ui.preview_research_tier", tier=tier)) for tier in tiers)])  # fmt: skip
        self.m_preset.picked.connect(self._preset)
        self.row(top, tr("ui.preview_research_preset"), self.m_preset, ("research",), tr("ui.preview_research_tip"))
        layout.addLayout(top)
        bar = QHBoxLayout()
        self.find = QLineEdit(placeholderText=tr("ui.preview_research_find"))
        self.find.setToolTip(tr("ui.preview_research_find_tip"))
        bar.addWidget(self.find, 1)
        bar.addWidget(self.m_panel.reset_button(("research", "options.research.*")))
        layout.addLayout(bar)
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderLabels([tr("ui.preview_node"), tr("ui.preview_row"), tr("ui.preview_count")])
        self.m_tree.setIconSize(QSize(24, 24))
        self.m_tree.setMinimumHeight(300)
        self.m_tree.setToolTip(tr("ui.preview_research_list_tip"))
        self.m_counts: dict[int, QSpinBox] = {}
        groups: dict[str, QTreeWidgetItem] = {}
        for option in sorted(
            self.cat.research,
            key=lambda option: (
                option.group == UNUSED,
                not option.group,
                option.dlc != 0,
                option.group,
                option.tier,
                option.name,
            ),
        ):
            if option.group not in groups:  # the base trees, then the DLCs' own, then what is in no tree
                shown = (
                    tr("ui.preview_research_unused")
                    if option.group == UNUSED
                    else option.group or tr("ui.preview_research_other")
                )
                groups[option.group] = QTreeWidgetItem(self.m_tree, [shown])
                groups[option.group].setData(0, DATA, shown)
                groups[option.group].setData(0, DATA + 1, option.dlc)
            group = groups[option.group]
            leaf = QTreeWidgetItem(group, [option.name, str(option.tier)])
            leaf.setData(0, DATA, option)
            leaf.setToolTip(0, f"{option.name} ({option.guid})")
            if option.repeatable:
                spin = QSpinBox()
                spin.setRange(0, 99)
                spin.setToolTip(tr("ui.preview_count_tip"))
                spin.valueChanged.connect(lambda count, option=option: self._node(option, count))
                self.m_tree.setItemWidget(leaf, 2, spin)
                self.m_counts[option.guid] = spin
            else:
                leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                leaf.setCheckState(0, UNCHECKED)
        self.m_tree.expandAll()
        self.m_tree.header().resizeSection(0, 280)
        self.m_tree.itemChanged.connect(self._checked)
        self.find.textChanged.connect(lambda text: _filter_tree(self.m_tree, text))
        layout.addWidget(self.m_tree, 1)

    def _checked(self, item, column: int) -> None:
        option = item.data(0, DATA)
        if column == 0 and isinstance(option, SOption) and not option.repeatable:
            self._node(option, 1 if item.checkState(0) == CHECKED else 0)

    def _node(self, option: SOption, value: int) -> None:
        """A node's state: only what differs from the preset's is kept."""
        if self.m_panel.m_busy:
            return
        base = pd.SDefaults(research=self.d.research).researched(option.guid, option.tier)
        self.put(f"options.research.{option.guid}", None if float(value) == base else float(value))

    def _preset(self, value: str) -> None:
        if self.m_panel.m_busy:
            return
        mine = {f"options.{k}": None for k in self.d.options if k.startswith("research.")}
        self.store.set_many({"research": None if value == "all" else value, **mine})  # a preset sets every node

    def refresh(self) -> None:
        defaults = self.d
        self.m_preset.select(defaults.research)
        for leaf in self._leaves():
            option = leaf.data(0, DATA)
            value = defaults.researched(option.guid, option.tier)
            if option.repeatable:
                self.m_counts[option.guid].setValue(int(value))
            else:
                leaf.setCheckState(0, CHECKED if value >= 1 else UNCHECKED)
        for group_index in range(self.m_tree.topLevelItemCount()):
            group = self.m_tree.topLevelItem(group_index)
            done = sum(1 for i in range(group.childCount()) if self._done(group.child(i)))
            dlc = group.data(0, DATA + 1)
            owned = (
                not dlc or defaults.dlcs is None or dlc in defaults.dlcs
            )  # a DLC's tree is greyed while the DLC is not owned
            words = N_("ui.preview_group_count") if owned else N_("ui.preview_group_not_owned")
            group.setText(0, tr(words, name=group.data(0, DATA), done=done, total=group.childCount()))
            for item in (group, *(group.child(i) for i in range(group.childCount()))):
                item.setDisabled(not owned)
            for i in range(group.childCount()):
                spin = self.m_tree.itemWidget(group.child(i), 2)
                if spin is not None:
                    spin.setEnabled(owned)
        self.update_icons()

    def _done(self, leaf) -> bool:
        option = leaf.data(0, DATA)
        return self.m_counts[option.guid].value() > 0 if option.repeatable else leaf.checkState(0) == CHECKED

    def update_icons(self) -> None:
        for leaf in self._leaves():
            leaf.setIcon(0, icon_at(self.m_panel.m_icons, leaf.data(0, DATA).icon))
