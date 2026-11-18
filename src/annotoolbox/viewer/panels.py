"""The viewer's panels around the 3D view, laid out after the studio's object builder: the config's parts as a tree on
the left (``PartsPanel``: the lists of a config and their parts, a tick to show or hide each), the selected part's
fields on the right (``PropertiesPanel``: a file name opens that file), the sequences on a timeline below
(``SequencePanel``: play, pause, seek, the track elements as bars), and who uses the open file (``UsersPanel``).

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
from ..models.configtree import Node, Sequence
from ..sources.overlay import normalize
from ..suite import theme
from .modelscene import Scene
from .states import States

NODE_ROLE = 0x0100 + 1  # Qt.UserRole + 1: a row's node path
SHOWN = ("Models", "Clothes", "Files", "Particles", "BillboardClusters")  # sections a tick shows or hides
FILE_VALUE = re.compile(r"\.(psd|png|tga|dds|rdm|fbx|cfg|rdp|efc|gr2)$", re.I)
TRACK_COLORS = {0: "#4f9fd9", 1: "#e0a030", 2: "#8a8a8a"}  # animation, effect, condition


def node_label(node: Node) -> str:
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


class PartsPanel(QTreeWidget):
    """The config's parts: ``selected`` (a node path), ``hidden_changed`` (the set of node paths unticked)."""

    selected = Signal(str)
    hidden_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.scene: Scene | None = None
        self.itemChanged.connect(lambda _item, _col: self.hidden_changed.emit(self.hidden()))
        self.currentItemChanged.connect(
            lambda cur, _prev: cur is not None and self.selected.emit(cur.data(0, NODE_ROLE))
        )

    def show_scene(self, scene: Scene) -> None:
        self.blockSignals(True)
        self.clear()
        self.scene = scene
        top = QTreeWidgetItem([scene.config.rsplit("/", 1)[-1] or scene.path])
        top.setData(0, NODE_ROLE, "")
        top.setToolTip(0, scene.config)
        self.addTopLevelItem(top)
        if scene.tree is not None:
            self._add(top, scene.tree, "")
        else:  # a mesh alone or a model without a tree: its parts
            for part in scene.parts:
                item = QTreeWidgetItem([f"MESH  {part.mesh.rsplit('/', 1)[-1]}"])  # i18n: ok (a config node kind)
                item.setData(0, NODE_ROLE, part.node)
                top.addChild(item)
        top.setExpanded(True)
        self.blockSignals(False)

    def _add(self, parent: QTreeWidgetItem, node: Node, prefix: str) -> None:
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
        if node.kind == "MAIN" or not prefix and node is (self.scene.tree if self.scene else None):
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


class PropertiesPanel(QTableWidget):
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

    def show_node(self, node: Node) -> None:
        rows = (
            [(tr("viewer.kind"), node.kind), (tr("viewer.list"), f"{node.section} [{node.index}]")] if node.kind else []
        )
        rows += [(k[1:] if k == configtree.GROUP else k, v) for k, v in node.fields]
        rows += [
            (k, " ".join((el.text or "").strip() for el in v.iter() if (el.text or "").strip())[:200])
            for k, v in node.blocks.items()
        ]
        self.show_rows(rows)

    def _open(self, row: int, col: int) -> None:
        name, value = self.item(row, 0), self.item(row, 1)
        if name is not None and value is not None and (link := file_link(name.text(), value.text())):
            self.open_file.emit(link)


class UsersPanel(QTreeWidget):
    """The files and assets that name the open file (``refgraph.graph.RefGraph.users``): a file opens on a
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
        for title, rows in ((N_("common.files"), [(c, "", c) for c in configs]),
                            (N_("common.assets"), [(f"{u.guid}  {u.name}", u.field, u.guid) for u in assets])):  # fmt: skip
            top = QTreeWidgetItem([tr("common.title_count", title=tr(title), count=len(rows))])
            self.addTopLevelItem(top)
            for text, where, target in rows:
                item = QTreeWidgetItem([text, where])
                item.setData(0, NODE_ROLE, target)
                item.setToolTip(
                    0,
                    tr("viewer.double_click_open") if isinstance(target, str) else tr("viewer.double_click_show_asset"),
                )
                top.addChild(item)
            top.setExpanded(len(rows) <= 50)
        if not configs and not assets:
            self.note(tr("viewer.nothing_names_name", name=path.rsplit("/", 1)[-1]))

    def _open(self, item, _col) -> None:
        target = item.data(0, NODE_ROLE)
        if isinstance(target, str) and target:
            self.open_file.emit(target)
        elif isinstance(target, int):
            self.show_asset.emit(target)


class ChecksPanel(QTreeWidget):
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
        self._scene: Scene | None = None  # the scene the rows were made for, and how many failures they list
        self._failures = -1

    def show_checks(self, scene: Scene | None, failed: dict[str, str]) -> None:
        """The rows of a scene and the files its renderer failed to load (``ModelRenderer.failed``)."""
        if self._scene is scene and self._failures == len(failed):  # by identity: a scene's parts hold arrays
            return
        self._scene, self._failures = scene, len(failed)
        self.clear()
        missing = scene.resolve.missing() if scene is not None and scene.resolve is not None else []
        for title, rows in (
            (
                N_("viewer.not_games_files"),
                [(r.name, r.reason, r.owner, f"{r.kind} {r.slot} {r.node}") for r in missing],
            ),
            (N_("viewer.not_load"), [(path, why, "", "") for path, why in sorted(failed.items())]),
        ):
            top = QTreeWidgetItem([tr("common.title_count", title=tr(title), count=len(rows))])
            self.addTopLevelItem(top)
            for name, why, owner, where in rows:
                item = QTreeWidgetItem([name, why, owner])
                item.setData(0, NODE_ROLE, owner)
                item.setToolTip(0, where.strip() or name)
                top.addChild(item)
            top.setExpanded(True)


def sequence_animations(scene: Scene, seq: Sequence, seconds: float) -> dict:
    """Per part node the clip a sequence plays at ``seconds`` as (file, view time it started at, scale, loops): its
    type 0 elements (``Models[model]``'s ``Animations[animation]``), its ``Speed`` running its clock."""
    speed = seq.speed if seq.speed > 0 else 1.0
    t = (seconds * speed) % max(seq.duration_ms / 1000, 0.001)
    out = {}
    for track in seq.tracks:
        for e in track:
            if e.kind == 0 and e.start_ms / 1000 <= t < max(e.end_ms, e.start_ms + 1) / 1000:
                part = next((p for p in scene.parts if p.node == f"Models/{e.model}"), None)
                if part is not None and e.animation < len(part.animations):
                    clip, since = part.animations[e.animation], (t - e.start_ms / 1000) / speed
                    out[part.node] = (clip.file, seconds - since, clip.scale / speed, clip.loops)
    return out


class Timeline(QWidget):
    """A sequence's tracks as rows of bars over its length, a line at the time; a click seeks (``seek``)."""

    seek = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.sequence: Sequence | None = None
        self.length, self.time, self.speed = 1.0, 0.0, 1.0
        self.setMinimumHeight(48)

    def set_sequence(self, sequence: Sequence | None, length: float) -> None:
        self.sequence, self.length = sequence, max(length, 0.001)
        self.speed = sequence.speed if sequence is not None and sequence.speed > 0 else 1.0
        self.update()

    def set_time(self, seconds: float) -> None:
        self.time = seconds
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.colors()["field"]))
        tracks = self.sequence.tracks if self.sequence else []
        row = (h - 4) / max(1, len(tracks))
        for k, track in enumerate(tracks):
            for e in track:
                x0, x1 = e.start_ms / 1000 / self.length * w, e.end_ms / 1000 / self.length * w
                p.fillRect(
                    QRectF(x0, 2 + k * row, max(2.0, x1 - x0), row - 2), QColor(TRACK_COLORS.get(e.kind, "#888"))
                )
        x = (self.time * self.speed % self.length) / self.length * w
        p.setPen(QColor("#f28c28"))
        p.drawLine(int(x), 0, int(x), h)
        p.end()

    def mousePressEvent(self, e) -> None:
        self.seek.emit(e.position().x() / max(1, self.width()) * self.length / self.speed)


class SequencePanel(QWidget):
    """The model's sequences and animations: choose one, play, pause, seek. ``changed`` gives the view's
    ``(time, animations)`` - per part node the animation file and the time it starts at."""

    changed = Signal(float, object)
    playing = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.scene: Scene | None = None
        self.choice = QComboBox(sizeAdjustPolicy=QComboBox.SizeAdjustPolicy.AdjustToContents, minimumWidth=240)
        self.play = QPushButton(tr("common.play"), checkable=True)
        self.speed = QDoubleSpinBox(minimum=0.1, maximum=4.0, singleStep=0.1, value=1.0, prefix=tr("common.speed"))
        self.clock = QLabel(tr("common.seconds_s", seconds=number(0, 2)))
        self.timeline = Timeline()
        bar = QHBoxLayout()
        for w in (QLabel(tr("common.sequence")), self.choice, self.play, self.speed, self.clock):
            bar.addWidget(w)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.timeline)
        self.choice.currentIndexChanged.connect(lambda _i: self._chosen())
        self.play.toggled.connect(self.playing.emit)
        self.timeline.seek.connect(lambda t: self.changed.emit(t, self.animations(t)))

    def show_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItem(tr("viewer.rest_pose"), None)
        for k, s in enumerate(scene.sequences):
            seconds = number(s.duration_ms / 1000, 2)
            text = (
                tr("viewer.label_seconds_s_looped", label=s.label, seconds=seconds)
                if s.looped
                else tr("viewer.label_seconds_s", label=s.label, seconds=seconds)
            )
            self.choice.addItem(text, ("seq", k))  # i18n: ok (a datum)
        for part in scene.parts:
            for k, clip in enumerate(part.animations):
                self.choice.addItem(
                    f"{part.node}: {clip.file.rsplit('/', 1)[-1]}", ("anim", part.node, k)
                )  # i18n: ok (names)
        self.choice.setCurrentIndex(1 if scene.sequences and any(s.tracks for s in scene.sequences) else 0)
        self.choice.blockSignals(False)
        self._chosen()

    def _sequence(self) -> Sequence | None:
        data = self.choice.currentData()
        return self.scene.sequences[data[1]] if self.scene and data and data[0] == "seq" else None

    def length(self) -> float:
        data = self.choice.currentData()
        if data and data[0] == "seq":
            return max(self.scene.sequences[data[1]].duration_ms / 1000, 0.001)
        return 10.0

    def _chosen(self) -> None:
        self.timeline.set_sequence(self._sequence(), self.length())
        self.changed.emit(0.0, self.animations(0.0))

    def animations(self, seconds: float) -> dict:
        """Per part node the clip playing at ``seconds`` as (file, view time it started at, scale, loops), by the chosen
        sequence's type 0 elements (``Models[model]``'s ``Animations[animation]``; the sequence's ``Speed`` runs its
        clock) or the clip chosen alone."""
        data = self.choice.currentData()
        if not data or self.scene is None:
            return {}
        if data[0] == "anim":
            part = next(p for p in self.scene.parts if p.node == data[1])
            clip = part.animations[data[2]]
            return {part.node: (clip.file, 0.0, clip.scale, clip.loops)}
        return sequence_animations(self.scene, self.scene.sequences[data[1]], seconds)

    def states(self, seconds: float, held: int = 0) -> States:
        """The condition bits the chosen sequence's condition tracks hold at ``seconds`` (``viewer.states``; none for
        a clip played alone), with the bits ``held`` by hand."""
        return States.of(self._sequence(), seconds, held)

    def tick(self, seconds: float) -> None:
        """The view's clock moved: the timeline and the label follow."""
        self.timeline.set_time(seconds)
        seq = self._sequence()
        speed = seq.speed if seq is not None and seq.speed > 0 else 1.0
        self.clock.setText(tr("common.seconds_s", seconds=number((seconds * speed) % self.length(), 2)))
