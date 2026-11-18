"""The game mirror (Qt): a window beside the UI editor that shows what the running game shows, every UI scene it
has open stacked in the game's order, each laid out by the preview's code and filled with the game's live values;
and the push of edits into the running game.

``open_mirror(editor)`` opens it (View > Game mirror); the editor calls ``edited`` and ``kept`` on it after an edit
and a save. What is open and what is asked next is ``follow``; the values are ``liverecord``'s. docs/ui-editor.md
"The game mirror"; the link's side is docs/live-link.md sections 17 and 18.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from ..modloader.mods import GAME_NUMBER, read_mod
from ..suite import livelink, project
from ..suite.background import Background
from . import canvas, elements, follow, layout, liverecord, livevalues, viewmodels
from .pictures import Pictures

TICK_MS = 100  # how often the mirror looks whether a request is due
CLEAR = (0, 0, 0, 0)  # a scene's picture is transparent where it draws nothing: the scenes under it show


class _Events(QObject):
    """The link's events, handed over to the window's thread."""

    scene = Signal(dict)
    status = Signal(object)


class MirrorView(QWidget):
    """The scenes' pictures over each other, bottom first; a click names the topmost scene that draws there."""

    picked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.layers: list[tuple[str, QImage]] = []
        self.window = follow.WINDOW
        self.fit = True
        self.setMinimumSize(320, 180)

    def _scale(self) -> float:
        w, h = self.window
        return min(self.width() / w, self.height() / h) if self.fit else 1.0

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        s, (w, h) = self._scale(), self.window
        target = QRectF(0, 0, w * s, h * s)
        p.fillRect(target, QColor(40, 44, 52))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for _state, image in self.layers:
            p.drawImage(target, image)
        p.end()

    def state_at(self, x: float, y: float) -> str:
        """The topmost scene whose picture is not transparent at the widget's point (x, y); "" for none."""
        s = self._scale()
        for state, image in reversed(self.layers):
            px, py = int(x / s * image.width() / self.window[0]), int(y / s * image.height() / self.window[1])
            if 0 <= px < image.width() and 0 <= py < image.height() and image.pixelColor(px, py).alpha() > 0:
                return state
        return ""

    def mousePressEvent(self, event) -> None:
        pos: QPointF = event.position()
        found = self.state_at(pos.x(), pos.y())
        if found:
            self.picked.emit(found)

    def show_layers(self, layers: list[tuple[str, QImage]], window: tuple[int, int]) -> None:
        self.layers, self.window = layers, window
        if not self.fit:
            self.setFixedSize(*window)
        self.update()


class MirrorWindow(QMainWindow):
    """The game mirror of one UI editor window (``editor``); it follows the game while it is open and not paused."""

    def __init__(self, editor) -> None:
        super().__init__(editor)
        self.editor = editor
        self.scenes, self.poller, self.pushes = follow.GameScenes(), follow.Poller(), follow.Pushes()
        self.held: dict[follow.Part, liverecord.Held] = {}
        self.parts: dict[str, list[follow.Part]] = {}  # state -> what is asked of its scene
        self.images: dict[str, QImage] = {}
        self.contexts: dict[str, layout.Context] = {}
        self.dirty: list[str] = []  # scenes to lay out again, in order
        self.notes: dict[str, str] = {}  # state -> why its scene shows nothing
        self._forget: list[dict] = []  # edited trees whose kept results the next layout drops (off the window's thread)
        self._laying, self._game, self._pictures = False, None, None
        self.following, self.paused, self.sync = False, False, False  # sync: requests and layouts here (tests)
        self.background = Background(self)
        self._events = _Events()
        self._events.scene.connect(self._on_scene)
        self._events.status.connect(lambda _status: self.start())
        self._unhook = [
            livelink.on(livelink.SCENE, self._events.scene.emit),
            livelink.on("status", self._events.status.emit),
        ]
        self._build()
        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(lambda: self.tick(time.monotonic()))
        self.timer.start()
        self.setWindowTitle(tr("ui.mirror_title"))
        self.resize(1280, 760)

    def _build(self) -> None:
        self.view = MirrorView()
        self.view.picked.connect(self.edit_scene)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setWidget(self.view)
        side = QWidget()
        box = QVBoxLayout(side)
        box.addWidget(QLabel(tr("ui.mirror_open_scenes")))
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("ui.mirror_scene"), tr("ui.mirror_layer"), tr("ui.mirror_parent")])
        self.list.setToolTip(tr("ui.mirror_list_tip"))
        self.list.itemChanged.connect(self._ticked)
        self.list.itemDoubleClicked.connect(lambda it, _c: self.edit_scene(it.data(0, Qt.ItemDataRole.UserRole)))
        box.addWidget(self.list, 1)
        self.size_box = QComboBox()
        self.size_box.addItems([tr("ui.mirror_size_fit"), tr("ui.mirror_size_game")])
        self.size_box.currentIndexChanged.connect(self._size_changed)
        size_label = QLabel(tr("ui.mirror_size"))
        size_label.setBuddy(self.size_box)
        row = QHBoxLayout()
        row.addWidget(size_label)
        row.addWidget(self.size_box, 1)
        box.addLayout(row)
        self.push_box = QCheckBox(tr("ui.mirror_push_edits"), toolTip=tr("ui.mirror_push_tip"))
        box.addWidget(self.push_box)
        buttons = QHBoxLayout()
        self.push_button = QPushButton(tr("ui.mirror_push_now"), toolTip=tr("ui.mirror_push_now_tip"))
        self.push_button.clicked.connect(self.push_now)
        self.pause_button = QPushButton(tr("ui.mirror_pause"), toolTip=tr("ui.mirror_pause_tip"))
        self.pause_button.clicked.connect(self.toggle_pause)
        buttons.addWidget(self.push_button)
        buttons.addWidget(self.pause_button)
        box.addLayout(buttons)
        split = QSplitter()
        split.addWidget(self.scroll)
        split.addWidget(side)
        split.setStretchFactor(0, 4)
        self.setCentralWidget(split)

    # following

    def start(self) -> None:
        """Follow the game when one is linked that sends scene events, unless paused; say why not otherwise."""
        link = livelink.current()
        if self.paused:
            self._say(tr("ui.mirror_paused"))
            return
        if not link.connected:
            self.following = False
            self._say(tr("ui.mirror_no_game"))
            return
        if not link.supports(livelink.FOLLOW):
            self.following = False
            self._say(tr("ui.mirror_no_events"))
            return
        if self.following:
            return
        self.following = True
        self.scenes.clear()
        self._say(tr("ui.mirror_waiting"))
        self.background.run(lambda: link.subscribe([livelink.SCENE]), self._subscribed, self.sync)

    def _subscribed(self, reply) -> None:
        if isinstance(reply, Exception) or not reply.ok:
            self.following = False
            self._say(tr("ui.mirror_not_followed", why=getattr(reply, "message", "") or str(reply)))

    def stop(self) -> None:
        """Follow no more: the subscription ends, no value is asked; the last picture stays."""
        link, was = livelink.current(), self.following
        self.following = False
        self.poller.waiting = None
        if was and link.connected:
            self.background.run(lambda: link.unsubscribe([livelink.SCENE]), lambda _r: None, self.sync)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.setText(tr("ui.mirror_resume") if self.paused else tr("ui.mirror_pause"))
        if self.paused:
            self.stop()
            self._say(tr("ui.mirror_paused"))
        else:
            self.start()

    def _on_scene(self, event: dict) -> None:
        """A scene opened or closed in the game: the list, the stack and what is asked follow."""
        if not self.following:
            return
        state = self.scenes.apply(event)
        if not state:
            return
        if state in self.scenes.scenes:
            self._lay_out(state)
        else:
            self.images.pop(state, None)
            self.parts.pop(state, None)
            self.held = {p: h for p, h in self.held.items() if p.state != state}
        self._follow_parts()
        self._fill_list()
        self._show()
        if self.scenes.last in self.scenes.scenes:
            self._say(tr("ui.mirror_following", scene=self.scenes.scenes[self.scenes.last].scene))

    def _follow_parts(self) -> None:
        shown = self.scenes.shown()
        self.poller.follow([p for s in shown for p in self.parts.get(s.state, [follow.Part(s.state)])])

    def tick(self, now: float) -> None:
        """Ask the next values that are due, and push the edits whose quiet time is over."""
        for key in self.pushes.due(now):
            self._save(key)
        if not self.following or self.paused:
            return
        part = self.poller.next(now)
        if part is None:
            return
        link, game = livelink.current(), self._game_key()
        key = part.file or (self.scenes.scenes[part.state].scene if part.state in self.scenes.scenes else "")
        build, before = link.status().build, self.held.get(part)

        def ask():
            reply = link.ui_values([part.state], part.file)
            if not reply.ok or not key:
                return reply, None
            held = liverecord.from_reply(reply.data, key, game, build, datetime.now().isoformat(timespec="seconds"))
            if before is not None and before.widgets == held.widgets:
                return reply, held  # the same values as recorded: nothing written four times a second
            try:
                livevalues.recordings(self.editor).save(held)  # as Take the live values records it
            except OSError:
                pass
            return reply, held

        self.background.run(ask, lambda result: self._valued(part, result), self.sync)

    def _valued(self, part: follow.Part, result) -> None:
        now = time.monotonic()
        if isinstance(result, Exception) or result[1] is None:
            self.poller.answered(part, now, False)
            return
        held = result[1]
        before = self.held.get(part)
        changed = before is None or before.widgets != held.widgets
        self.poller.answered(part, now, changed)
        self.held[part] = held
        if changed and part.state in self.scenes.scenes:
            self._lay_out(part.state)

    # laying out and drawing

    def _game_key(self) -> str:
        return self.editor.game.key if self.editor.game is not None else ""

    def _lay_out(self, state: str) -> None:
        if state not in self.dirty:
            self.dirty.append(state)
        self._lay_next()

    def _context(self, state: str) -> layout.Context:
        game = self.editor.game
        if self._game is not game:  # another game was opened in the editor: nothing of the one before holds
            self._game, self.contexts = game, {}
            self._pictures = Pictures(game.files)
        ctx = self.contexts.get(state)
        if ctx is None:
            fonts = getattr(self.editor, "fonts", None)
            ctx = layout.Context(
                game.files, viewmodels.Samples(), canvas.measure, fonts=fonts.lookup if fonts else None
            )
            ctx.defaults = getattr(self.editor.ctx, "defaults", None)
            self.contexts[state] = ctx
        return ctx

    def _lay_next(self) -> None:
        """Lay out the next scene off the window's thread, one at a time."""
        if self._laying or not self.dirty or self.editor.game is None:
            return
        state = self.dirty.pop(0)
        scene = self.scenes.scenes.get(state)
        if scene is None:
            self._lay_next()
            return
        ctx, pictures, files = self._context(state), self._pictures, self.editor.game.files
        texts, values = {}, {}
        for part in self.parts.get(state, [follow.Part(state)]):
            held = self.held.get(part)
            if held is not None:
                texts.update(held.texts())
                values.update(held.overrides())
        window, forget, self._forget = self.scenes.window, self._forget, []

        def work():
            for root in forget:
                for each in self.contexts.values():
                    each.forget(root)
            root = files.root(scene.scene)
            if root is None:
                return None
            ctx.live_text, ctx.live_values, ctx.window_aspect = texts, values, window[0] / window[1]
            frame = layout.place(scene.scene, root, ctx)
            image = canvas.render(frame, pictures, window[1] / frame.size[1], background=CLEAR)
            return image, follow.parts_of(state, root, _templates)

        self._laying = True
        self.background.run(work, lambda result: self._laid(state, result), self.sync)

    def _laid(self, state: str, result) -> None:
        self._laying = False
        if state in self.scenes.scenes:
            if isinstance(result, Exception) or result is None:
                self.notes[state] = str(result) if result is not None else tr("ui.mirror_no_file")
                self.images.pop(state, None)
            else:
                self.images[state], parts = result
                self.notes.pop(state, None)
                if parts != self.parts.get(state):
                    self.parts[state] = parts
                    self._follow_parts()
            self._fill_list()
            self._show()
        self._lay_next()

    def _show(self) -> None:
        layers = [(s.state, self.images[s.state]) for s in self.scenes.shown() if s.state in self.images]
        self.view.show_layers(layers, self.scenes.window)

    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for i, s in enumerate(reversed(self.scenes.stack())):  # the top first, as a layer list reads
            it = QTreeWidgetItem([s.scene, str(len(self.scenes.scenes) - i), s.parent])
            it.setData(0, Qt.ItemDataRole.UserRole, s.state)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Unchecked if s.hidden else Qt.CheckState.Checked)
            if s.state in self.notes:
                it.setToolTip(0, self.notes[s.state])
            self.list.addTopLevelItem(it)
        self.list.blockSignals(False)

    def _ticked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.scenes.hide(item.data(0, Qt.ItemDataRole.UserRole), item.checkState(0) != Qt.CheckState.Checked)
        self._follow_parts()
        self._show()

    def _size_changed(self, index: int) -> None:
        self.view.fit = index == 0
        self.scroll.setWidgetResizable(self.view.fit)
        if self.view.fit:
            self.view.setMinimumSize(320, 180)
            self.view.setMaximumSize(16777215, 16777215)
        self._show()

    def edit_scene(self, state: str) -> None:
        """Open a scene the game shows in the editor; the mirror goes on following."""
        scene = self.scenes.scenes.get(state)
        if scene is not None:
            self.editor.open_file(scene.scene)

    def _say(self, text: str) -> None:
        self.statusBar().showMessage(text)

    # the edit loop

    def showing(self, key: str) -> list[str]:
        """The open states whose scenes show the file ``key``: the scene itself, a file it places, or deeper."""
        game = self.editor.game
        if game is None:
            return []
        entry = game.files.entries.get(key)
        placed = set(game.files.scene_states([entry.path])) if entry is not None and not entry.is_scene else set()
        return [s.state for s in self.scenes.stack() if s.scene == key or s.state in placed or
                any(p.file == key for p in self.parts.get(s.state, []))]  # fmt: skip

    def edited(self, doc) -> None:
        """An edit of ``doc`` in the editor: the scenes that show it are laid out again (its tree is the editor's), and
        with Push edits on, it is pushed once no further edit came for a moment."""
        states = self.showing(doc.key)
        if not states:
            return
        self._forget.append(doc.root)
        for state in states:
            self._lay_out(state)
        if self.push_box.isChecked() and self.following:
            self.pushes.edit(doc.key, time.monotonic())

    def _save(self, key: str) -> None:
        """Keep the file as Save does (which pushes it, ``kept``); only the file open in the editor is saved so."""
        doc = self.editor.doc
        if doc is not None and doc.key == key:
            self.editor.save()
        else:
            self._say(tr("ui.mirror_push_open_only", name=key))

    def push_now(self) -> None:
        doc = self.editor.doc
        self.pushes.due(time.monotonic(), at_once=True)
        if doc is None or not self.showing(doc.key):
            self._say(tr("ui.mirror_push_not_open"))
            return
        if not self.push_box.isChecked():
            self.push_box.setChecked(True)
        self.editor.save()

    def kept(self, doc, path: str, data: bytes) -> None:
        """The editor kept ``doc`` in the project: with Push edits on and a scene of the game showing it, it goes into
        the installed mod and the game reloads the scenes that show it."""
        if not self.push_box.isChecked() or not self.following:
            return
        states = self.showing(doc.key)
        if not states:
            self._say(tr("ui.mirror_push_not_open"))
            return
        p, settings, link = self.editor.project(), self.editor.settings, livelink.current()

        def push():
            mods = project.mods_folder_of(p.game, settings)
            folder = follow.installed_mod(mods, p.mod_id, lambda f: _mod_id(f, p.game))
            if folder is None:
                return None
            follow.write_into(folder, path, data)
            return link.reload(livelink.SCENES, [path], mod=p.mod_id, states=states)

        self._say(tr("ui.mirror_pushing", name=path.rsplit("/", 1)[-1]))
        self.background.run(push, lambda reply: self._pushed(states, reply), self.sync)

    def _pushed(self, states: list[str], reply) -> None:
        if reply is None:
            self._say(tr("ui.mirror_push_install_first"))
        elif isinstance(reply, Exception):
            self._say(tr("ui.mirror_push_failed", why=reply))
        elif not reply.ok:
            self._say(tr("ui.mirror_push_refused", why=reply.message or reply.status))
        else:
            for state in states:
                self.poller.refresh(state)  # the reloaded scene's values are taken again
            self._say(tr("ui.mirror_pushed", states=", ".join(states)))

    def closeEvent(self, event) -> None:
        self.stop()
        self.timer.stop()
        for remove in self._unhook:
            remove()
        if getattr(self.editor, "mirror", None) is self:
            self.editor.mirror = None
        super().closeEvent(event)


def _templates(root: dict) -> list[str]:
    """The widget files a scene's own elements place, in the file's order."""
    return [el.template for el in elements.tree(root).walk() if el.kind == "container" and el.template]


def _mod_id(folder, game: str) -> str:
    found, _ = read_mod(folder, GAME_NUMBER.get(game))
    return found.mod_id if found is not None else ""


def open_mirror(editor) -> MirrorWindow:
    """Show the editor's game mirror (made on first use) and let it follow the game."""
    mirror = getattr(editor, "mirror", None)
    if mirror is None:
        mirror = editor.mirror = MirrorWindow(editor)
    mirror.show()
    mirror.raise_()
    mirror.start()
    return mirror
