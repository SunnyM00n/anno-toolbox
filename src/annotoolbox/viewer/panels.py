"""The viewer's panels around the 3D view, laid out after the studio's object builder: the config's parts as a tree on
the left (``CPartsPanel``: the lists of a config and their parts, a tick to show or hide each), the selected part's
fields on the right (``CPropertiesPanel``: a file name opens that file), the sequences on a timeline below
(``CSequencePanel``: play, pause, seek, the track elements as bars), and who uses the open file (``CUsersPanel``).

docs/toolone-public-sources.md (the object builder's tree, a section 1 diary) and docs/tool-suite.md "The viewer".
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, number, tr
from ..models import configtree, fileresolve
from ..models.configtree import SNode, SSequence
from ..sources.overlay import normalize
from ..suite import theme
from .modelscene import CScene
from .states import SStates

NODE_ROLE = 0x0100 + 1  # Qt.UserRole + 1: a row's node path
SHOWN = ("Models", "Clothes", "Files", "Particles", "BillboardClusters")  # sections a tick shows or hides
FILE_VALUE = re.compile(r"\.(psd|png|tga|dds|rdm|fbx|cfg|rdp|efc|gr2)$", re.I)
TRACK_COLORS = {0: "#4f9fd9", 1: "#e0a030", 2: "#8a8a8a"}  # animation, effect, condition


def node_label(node: SNode) -> str:
    """A tree row's text: the part's kind and its name or file."""
    what = node.name or normalize(node.get("FileName")).rsplit("/", 1)[-1] or node.get("SequenceID")
    return f"{node.kind or node.section}  {what}".rstrip()


def file_link(name: str, value: str) -> str:
    """The game path a field's value names, as the game loads it ("" for a value that names no file)."""
    if not FILE_VALUE.search(value.strip()):
        return ""
    if name.lower().endswith("tex") or value.lower().endswith((".psd", ".png", ".tga")):
        return fileresolve.texture_name(value)
    return re.sub(r"\.fbx$", ".rdm", normalize(value))


class CPartsPanel(QTreeWidget):
    """The config's parts: ``selected`` (a node path), ``hidden_changed`` (the set of node paths unticked)."""

    selected = Signal(str)
    hidden_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.m_scene: CScene | None = None
        self.itemChanged.connect(lambda _item, _col: self.hidden_changed.emit(self.hidden()))
        self.currentItemChanged.connect(
            lambda current, _prev: current is not None and self.selected.emit(current.data(0, NODE_ROLE))
        )

    def show_scene(self, scene: CScene) -> None:
        self.blockSignals(True)
        self.clear()
        self.m_scene = scene
        root_item = QTreeWidgetItem([scene.m_config.rsplit("/", 1)[-1] or scene.m_path])
        root_item.setData(0, NODE_ROLE, "")
        root_item.setToolTip(0, scene.m_config)
        self.addTopLevelItem(root_item)
        if scene.m_tree is not None:
            self._add(root_item, scene.m_tree, "")
        else:  # a mesh alone or a model without a tree: its parts
            for part in scene.m_parts:
                item = QTreeWidgetItem([f"MESH  {part.mesh.rsplit('/', 1)[-1]}"])  # i18n: ok (a config node kind)
                item.setData(0, NODE_ROLE, part.node)
                root_item.addChild(item)
        root_item.setExpanded(True)
        self.blockSignals(False)

    def _add(self, parent: QTreeWidgetItem, node: SNode, prefix: str) -> None:
        sections: dict[str, QTreeWidgetItem] = {}
        for child in node.children:
            if child.section == "Transformer":
                continue
            group = sections.get(child.section)
            if group is None:
                group = sections[child.section] = QTreeWidgetItem([child.section])
                group.setData(0, NODE_ROLE, None)
                parent.addChild(group)
            item = QTreeWidgetItem([node_label(child)])
            item.setData(0, NODE_ROLE, prefix + child.path)
            if child.section in SHOWN:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
            group.addChild(item)
            self._add(item, child, prefix)
        for group in sections.values():
            group.setText(0, tr("common.title_count", title=group.text(0), count=group.childCount()))
        if node.kind == "MAIN" or not prefix and node is (self.m_scene.m_tree if self.m_scene else None):
            for group in sections.values():
                group.setExpanded(group.childCount() <= 12)

    def select_node(self, path: str) -> None:
        """Make the row of ``path`` current, or of its nearest part listed (a nested config's part: its FILE row)."""
        rows = {}
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if isinstance(item.data(0, NODE_ROLE), str):
                rows[item.data(0, NODE_ROLE)] = item
            stack += [item.child(i) for i in range(item.childCount())]
        parts = path.split("/")
        for end in range(len(parts), -1, -1):
            found = rows.get("/".join(parts[:end]))
            if found is not None:
                self.setCurrentItem(found)
                self.scrollToItem(found)
                return

    def hidden(self) -> set[str]:
        """The node paths whose tick is off."""
        out = set()
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            path = item.data(0, NODE_ROLE)
            if path and item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.checkState(0) != Qt.CheckState.Checked:
                out.add(path)
            stack += [item.child(i) for i in range(item.childCount())]
        return out


class CPropertiesPanel(QTableWidget):
    """Fields as rows of name and value; a value that names a file opens it on a double-click (``open_file``)."""

    open_file = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels([tr("common.field"), tr("common.value_lower")])
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().hide()
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cellDoubleClicked.connect(self._open)

    def show_rows(self, rows: list[tuple[str, str]]) -> None:
        self.setRowCount(0)
        for name, value in rows:
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, 0, QTableWidgetItem(name))
            cell = QTableWidgetItem(value)
            link = file_link(name, value)
            if link:
                cell.setToolTip(tr("viewer.tip.double_click_open", path=link))
                cell.setForeground(QColor("#4f9fd9"))
            self.setItem(row, 1, cell)
        self.resizeColumnToContents(0)

    def show_node(self, node: SNode) -> None:
        rows = (
            [(tr("viewer.kind"), node.kind), (tr("viewer.list"), f"{node.section} [{node.index}]")] if node.kind else []
        )
        rows += [(k[1:] if k == configtree.GROUP else k, value) for k, value in node.fields]
        rows += [
            (
                k,
                " ".join((element.text or "").strip() for element in value.iter() if (element.text or "").strip())[
                    :200
                ],
            )
            for k, value in node.blocks.items()
        ]
        self.show_rows(rows)

    def _open(self, row: int, col: int) -> None:
        name, value = self.item(row, 0), self.item(row, 1)
        if name is not None and value is not None and (link := file_link(name.text(), value.text())):
            self.open_file.emit(link)


class CUsersPanel(QTreeWidget):
    """The files and assets that name the open file (``refgraph.graph.CRefGraph.users``): a file opens on a
    double-click (``open_file``), an asset asks for the asset editor (``show_asset``)."""

    open_file = Signal(str)
    show_asset = Signal(object)  # a GUID (may exceed a Qt int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels([tr("viewer.used_by_lower"), tr("common.field")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemDoubleClicked.connect(self._open)
        self.note(tr("viewer.file_gt_uses_ctrl_plus"))

    def note(self, text: str) -> None:
        self.clear()
        self.addTopLevelItem(QTreeWidgetItem([text]))

    def show_users(self, path: str, configs: list[str], assets: list) -> None:
        self.clear()
        for title, rows in ((N_("common.files"), [(config, "", config) for config in configs]),
                            (N_("common.assets"), [(f"{asset.guid}  {asset.name}", asset.field, asset.guid) for asset in assets])):  # fmt: skip
            root_item = QTreeWidgetItem([tr("common.title_count", title=tr(title), count=len(rows))])
            self.addTopLevelItem(root_item)
            for text, where, target in rows:
                item = QTreeWidgetItem([text, where])
                item.setData(0, NODE_ROLE, target)
                item.setToolTip(
                    0,
                    tr("viewer.double_click_open") if isinstance(target, str) else tr("viewer.double_click_show_asset"),
                )
                root_item.addChild(item)
            root_item.setExpanded(len(rows) <= 50)
        if not configs and not assets:
            self.note(tr("viewer.nothing_names_name", name=path.rsplit("/", 1)[-1]))

    def _open(self, item, _col) -> None:
        target = item.data(0, NODE_ROLE)
        if isinstance(target, str) and target:
            self.open_file.emit(target)
        elif isinstance(target, int):
            self.show_asset.emit(target)


class CChecksPanel(QTreeWidget):
    """The files the open model names that the view cannot draw: names that find no file (the reason by the game's
    own index, ``fileresolve``) and files that do not load or decode. A double-click opens the config that names it
    (``open_file``); the view draws a missing colour map as a magenta checker."""

    open_file = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels([tr("common.file_lower"), tr("viewer.why"), tr("viewer.named_by_lower")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemDoubleClicked.connect(
            lambda item, _col: item.data(0, NODE_ROLE) and self.open_file.emit(item.data(0, NODE_ROLE))
        )
        self._m_scene: CScene | None = None  # the scene the rows were made for, and how many failures they list
        self._m_failures = -1

    def show_checks(self, scene: CScene | None, failed: dict[str, str]) -> None:
        """The rows of a scene and the files its renderer failed to load (``CModelRenderer.failed``)."""
        if self._m_scene is scene and self._m_failures == len(failed):  # by identity: a scene's parts hold arrays
            return
        self._m_scene, self._m_failures = scene, len(failed)
        self.clear()
        missing = scene.m_resolve.missing() if scene is not None and scene.m_resolve is not None else []
        for title, rows in (
            (
                N_("viewer.not_games_files"),
                [
                    (check.name, check.reason, check.owner, f"{check.kind} {check.slot} {check.node}")
                    for check in missing
                ],
            ),
            (N_("viewer.not_load"), [(path, why, "", "") for path, why in sorted(failed.items())]),
        ):
            root_item = QTreeWidgetItem([tr("common.title_count", title=tr(title), count=len(rows))])
            self.addTopLevelItem(root_item)
            for name, why, owner, where in rows:
                item = QTreeWidgetItem([name, why, owner])
                item.setData(0, NODE_ROLE, owner)
                item.setToolTip(0, where.strip() or name)
                root_item.addChild(item)
            root_item.setExpanded(True)


def sequence_animations(scene: CScene, seq: SSequence, seconds: float) -> dict:
    """Per part node the clip a sequence plays at ``seconds`` as (file, view time it started at, scale, loops): its
    type 0 elements (``Models[model]``'s ``Animations[animation]``), its ``Speed`` running its clock."""
    speed = seq.speed if seq.speed > 0 else 1.0
    local_time = (seconds * speed) % max(seq.duration_ms / 1000, 0.001)
    out = {}
    for track in seq.tracks:
        for entry in track:
            if entry.kind == 0 and entry.start_ms / 1000 <= local_time < max(entry.end_ms, entry.start_ms + 1) / 1000:
                part = next(
                    (scene_part for scene_part in scene.m_parts if scene_part.node == f"Models/{entry.model}"), None
                )
                if part is not None and entry.animation < len(part.animations):
                    clip, since = part.animations[entry.animation], (local_time - entry.start_ms / 1000) / speed
                    out[part.node] = (clip.file, seconds - since, clip.scale / speed, clip.loops)
    return out


class CTimeline(QWidget):
    """A sequence's tracks as rows of bars over its length, a line at the time; a click seeks (``seek``)."""

    seek = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_sequence: SSequence | None = None
        self.m_length, self.m_time, self.m_speed = 1.0, 0.0, 1.0
        self.setMinimumHeight(48)

    def set_sequence(self, sequence: SSequence | None, length: float) -> None:
        self.m_sequence, self.m_length = sequence, max(length, 0.001)
        self.m_speed = sequence.speed if sequence is not None and sequence.speed > 0 else 1.0
        self.update()

    def set_time(self, seconds: float) -> None:
        self.m_time = seconds
        self.update()

    def paintEvent(self, _e) -> None:
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor(theme.colors()["field"]))
        tracks = self.m_sequence.tracks if self.m_sequence else []
        row = (height - 4) / max(1, len(tracks))
        for k, track in enumerate(tracks):
            for e in track:
                x0, x1 = e.start_ms / 1000 / self.m_length * width, e.end_ms / 1000 / self.m_length * width
                painter.fillRect(
                    QRectF(x0, 2 + k * row, max(2.0, x1 - x0), row - 2), QColor(TRACK_COLORS.get(e.kind, "#888"))
                )
        x = (self.m_time * self.m_speed % self.m_length) / self.m_length * width
        painter.setPen(QColor("#f28c28"))
        painter.drawLine(int(x), 0, int(x), height)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.seek.emit(event.position().x() / max(1, self.width()) * self.m_length / self.m_speed)


class CSequencePanel(QWidget):
    """The model's sequences and animations: choose one, play, pause, seek. ``changed`` gives the view's
    ``(time, animations)`` - per part node the animation file and the time it starts at."""

    changed = Signal(float, object)
    playing = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_scene: CScene | None = None
        self.m_choice = QComboBox(sizeAdjustPolicy=QComboBox.SizeAdjustPolicy.AdjustToContents, minimumWidth=240)
        self.m_play = QPushButton(tr("common.play"), checkable=True)
        self.m_speed = QDoubleSpinBox(minimum=0.1, maximum=4.0, singleStep=0.1, value=1.0, prefix=tr("common.speed"))
        self.m_clock = QLabel(tr("common.seconds_s", seconds=number(0, 2)))
        self.m_timeline = CTimeline()
        button_row = QHBoxLayout()
        for widget in (QLabel(tr("common.sequence")), self.m_choice, self.m_play, self.m_speed, self.m_clock):
            button_row.addWidget(widget)
        button_row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self.m_timeline)
        self.m_choice.currentIndexChanged.connect(lambda _i: self._chosen())
        self.m_play.toggled.connect(self.playing.emit)
        self.m_timeline.seek.connect(lambda t: self.changed.emit(t, self.animations(t)))

    def show_scene(self, scene: CScene) -> None:
        self.m_scene = scene
        self.m_choice.blockSignals(True)
        self.m_choice.clear()
        self.m_choice.addItem(tr("viewer.rest_pose"), None)
        for k, sequence in enumerate(scene.m_sequences):
            seconds = number(sequence.duration_ms / 1000, 2)
            text = (
                tr("viewer.label_seconds_s_looped", label=sequence.label, seconds=seconds)
                if sequence.looped
                else tr("viewer.label_seconds_s", label=sequence.label, seconds=seconds)
            )
            self.m_choice.addItem(text, ("seq", k))  # i18n: ok (a datum)
        for part in scene.m_parts:
            for k, clip in enumerate(part.animations):
                self.m_choice.addItem(
                    f"{part.node}: {clip.file.rsplit('/', 1)[-1]}", ("anim", part.node, k)
                )  # i18n: ok (names)
        self.m_choice.setCurrentIndex(
            1 if scene.m_sequences and any(sequence.tracks for sequence in scene.m_sequences) else 0
        )
        self.m_choice.blockSignals(False)
        self._chosen()

    def _sequence(self) -> SSequence | None:
        data = self.m_choice.currentData()
        return self.m_scene.m_sequences[data[1]] if self.m_scene and data and data[0] == "seq" else None

    def length(self) -> float:
        data = self.m_choice.currentData()
        if data and data[0] == "seq":
            return max(self.m_scene.m_sequences[data[1]].duration_ms / 1000, 0.001)
        return 10.0

    def _chosen(self) -> None:
        self.m_timeline.set_sequence(self._sequence(), self.length())
        self.changed.emit(0.0, self.animations(0.0))

    def animations(self, seconds: float) -> dict:
        """Per part node the clip playing at ``seconds`` as (file, view time it started at, scale, loops), by the chosen
        sequence's type 0 elements (``Models[model]``'s ``Animations[animation]``; the sequence's ``Speed`` runs its
        clock) or the clip chosen alone."""
        data = self.m_choice.currentData()
        if not data or self.m_scene is None:
            return {}
        if data[0] == "anim":
            part = next(scene_part for scene_part in self.m_scene.m_parts if scene_part.node == data[1])
            clip = part.animations[data[2]]
            return {part.node: (clip.file, 0.0, clip.scale, clip.loops)}
        return sequence_animations(self.m_scene, self.m_scene.m_sequences[data[1]], seconds)

    def states(self, seconds: float, held: int = 0) -> SStates:
        """The condition bits the chosen sequence's condition tracks hold at ``seconds`` (``viewer.states``; none for
        a clip played alone), with the bits ``held`` by hand."""
        return SStates.of(self._sequence(), seconds, held)

    def tick(self, seconds: float) -> None:
        """The view's clock moved: the timeline and the label follow."""
        self.m_timeline.set_time(seconds)
        seq = self._sequence()
        speed = seq.speed if seq is not None and seq.speed > 0 else 1.0
        self.m_clock.setText(tr("common.seconds_s", seconds=number((seconds * speed) % self.length(), 2)))
