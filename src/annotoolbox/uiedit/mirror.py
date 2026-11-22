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
from ..suite.background import CBackground
from . import canvas, elements, follow, layout, liverecord, livevalues, viewmodels
from .pictures import CPictures

TICK_MS = 100  # how often the mirror looks whether a request is due
CLEAR = (0, 0, 0, 0)  # a scene's picture is transparent where it draws nothing: the scenes under it show


class _CEvents(QObject):
    """The link's events, handed over to the window's thread."""

    scene = Signal(dict)
    status = Signal(object)


class CMirrorView(QWidget):
    """The scenes' pictures over each other, bottom first; a click names the topmost scene that draws there."""

    picked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.m_layers: list[tuple[str, QImage]] = []
        self.m_window = follow.WINDOW
        self.m_fit = True
        self.setMinimumSize(320, 180)

    def _scale(self) -> float:
        width_px, height_px = self.m_window
        return min(self.width() / width_px, self.height() / height_px) if self.m_fit else 1.0

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 24))
        scale, (width_px, height_px) = self._scale(), self.m_window
        target = QRectF(0, 0, width_px * scale, height_px * scale)
        painter.fillRect(target, QColor(40, 44, 52))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for _state, image in self.m_layers:
            painter.drawImage(target, image)
        painter.end()

    def state_at(self, x: float, y: float) -> str:
        """The topmost scene whose picture is not transparent at the widget's point (x, y); "" for none."""
        scale = self._scale()
        for state, image in reversed(self.m_layers):
            pixel_x, pixel_y = (
                int(x / scale * image.width() / self.m_window[0]),
                int(y / scale * image.height() / self.m_window[1]),
            )
            if (
                0 <= pixel_x < image.width()
                and 0 <= pixel_y < image.height()
                and image.pixelColor(pixel_x, pixel_y).alpha() > 0
            ):
                return state
        return ""

    def mousePressEvent(self, event) -> None:
        """Name the scene under the click."""
        position: QPointF = event.position()
        found = self.state_at(position.x(), position.y())
        if found:
            self.picked.emit(found)

    def show_layers(self, layers: list[tuple[str, QImage]], window: tuple[int, int]) -> None:
        """Show these pictures, bottom first, in a game window of ``window`` pixels."""
        self.m_layers, self.m_window = layers, window
        if not self.m_fit:
            self.setFixedSize(*window)
        self.update()


class CMirrorWindow(QMainWindow):
    """The game mirror of one UI editor window (``editor``); it follows the game while it is open and not paused."""

    def __init__(self, editor) -> None:
        super().__init__(editor)
        self.m_editor = editor
        self.m_scenes, self.m_poller, self.m_pushes = follow.CGameScenes(), follow.CPoller(), follow.CPushes()
        self.m_held: dict[follow.SPart, liverecord.SHeld] = {}
        self.m_parts: dict[str, list[follow.SPart]] = {}  # state -> what is asked of its scene
        self.m_images: dict[str, QImage] = {}
        self.m_contexts: dict[str, layout.CContext] = {}
        self.m_dirty: list[str] = []  # scenes to lay out again, in order
        self.m_notes: dict[str, str] = {}  # state -> why its scene shows nothing
        self._m_forget: list[
            dict
        ] = []  # edited trees whose kept results the next layout drops (off the window's thread)
        self._m_laying, self._m_game, self._m_pictures = False, None, None
        self.m_following, self.m_paused, self.m_is_synchronous = (
            False,
            False,
            False,
        )  # sync: requests and layouts here (tests)
        self.m_background = CBackground(self)
        self._m_events = _CEvents()
        self._m_events.scene.connect(self._on_scene)
        self._m_events.status.connect(lambda _status: self.start())
        self._m_unhook = [
            livelink.on(livelink.SCENE, self._m_events.scene.emit),
            livelink.on("status", self._m_events.status.emit),
        ]
        self._build()
        self.m_timer = QTimer(self, interval=TICK_MS)
        self.m_timer.timeout.connect(lambda: self.tick(time.monotonic()))
        self.m_timer.start()
        self.setWindowTitle(tr("ui.mirror_title"))
        self.resize(1280, 760)

    def _build(self) -> None:
        """Build the window: the view, the scene list, the size box and the push controls."""
        self.m_view = CMirrorView()
        self.m_view.picked.connect(self.edit_scene)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setWidget(self.m_view)
        side = QWidget()
        box = QVBoxLayout(side)
        box.addWidget(QLabel(tr("ui.mirror_open_scenes")))
        self.m_list = QTreeWidget()
        self.m_list.setHeaderLabels([tr("ui.mirror_scene"), tr("ui.mirror_layer"), tr("ui.mirror_parent")])
        self.m_list.setToolTip(tr("ui.mirror_list_tip"))
        self.m_list.itemChanged.connect(self._ticked)
        self.m_list.itemDoubleClicked.connect(lambda item, _c: self.edit_scene(item.data(0, Qt.ItemDataRole.UserRole)))
        box.addWidget(self.m_list, 1)
        self.m_size_box = QComboBox()
        self.m_size_box.addItems([tr("ui.mirror_size_fit"), tr("ui.mirror_size_game")])
        self.m_size_box.currentIndexChanged.connect(self._size_changed)
        size_label = QLabel(tr("ui.mirror_size"))
        size_label.setBuddy(self.m_size_box)
        row = QHBoxLayout()
        row.addWidget(size_label)
        row.addWidget(self.m_size_box, 1)
        box.addLayout(row)
        self.m_push_box = QCheckBox(tr("ui.mirror_push_edits"), toolTip=tr("ui.mirror_push_tip"))
        box.addWidget(self.m_push_box)
        buttons = QHBoxLayout()
        self.m_push_button = QPushButton(tr("ui.mirror_push_now"), toolTip=tr("ui.mirror_push_now_tip"))
        self.m_push_button.clicked.connect(self.push_now)
        self.m_pause_button = QPushButton(tr("ui.mirror_pause"), toolTip=tr("ui.mirror_pause_tip"))
        self.m_pause_button.clicked.connect(self.toggle_pause)
        buttons.addWidget(self.m_push_button)
        buttons.addWidget(self.m_pause_button)
        box.addLayout(buttons)
        splitter = QSplitter()
        splitter.addWidget(self.scroll)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 4)
        self.setCentralWidget(splitter)

    # following

    def start(self) -> None:
        """Follow the game when one is linked that sends scene events, unless paused; say why not otherwise."""
        link = livelink.current()
        if self.m_paused:
            self._say(tr("ui.mirror_paused"))
            return
        if not link.connected:
            self.m_following = False
            self._say(tr("ui.mirror_no_game"))
            return
        if not link.supports(livelink.FOLLOW):
            self.m_following = False
            self._say(tr("ui.mirror_no_events"))
            return
        if self.m_following:
            return
        self.m_following = True
        self.m_scenes.clear()
        self._say(tr("ui.mirror_waiting"))
        self.m_background.run(lambda: link.subscribe([livelink.SCENE]), self._subscribed, self.m_is_synchronous)

    def _subscribed(self, reply) -> None:
        if isinstance(reply, Exception) or not reply.is_ok:
            self.m_following = False
            self._say(tr("ui.mirror_not_followed", why=getattr(reply, "message", "") or str(reply)))

    def stop(self) -> None:
        """Follow no more: the subscription ends, no value is asked; the last picture stays."""
        link, was = livelink.current(), self.m_following
        self.m_following = False
        self.m_poller.m_waiting = None
        if was and link.connected:
            self.m_background.run(lambda: link.unsubscribe([livelink.SCENE]), lambda _r: None, self.m_is_synchronous)

    def toggle_pause(self) -> None:
        self.m_paused = not self.m_paused
        self.m_pause_button.setText(tr("ui.mirror_resume") if self.m_paused else tr("ui.mirror_pause"))
        if self.m_paused:
            self.stop()
            self._say(tr("ui.mirror_paused"))
        else:
            self.start()

    def _on_scene(self, event: dict) -> None:
        """A scene opened or closed in the game: the list, the stack and what is asked follow."""
        if not self.m_following:
            return
        state = self.m_scenes.apply(event)
        if not state:
            return
        if state in self.m_scenes.m_scenes:
            self._lay_out(state)
        else:
            self.m_images.pop(state, None)
            self.m_parts.pop(state, None)
            self.m_held = {part: held_values for part, held_values in self.m_held.items() if part.state != state}
        self._follow_parts()
        self._fill_list()
        self._show()
        if self.m_scenes.m_last in self.m_scenes.m_scenes:
            self._say(tr("ui.mirror_following", scene=self.m_scenes.m_scenes[self.m_scenes.m_last].scene))

    def _follow_parts(self) -> None:
        shown = self.m_scenes.shown()
        self.m_poller.follow(
            [part for scene in shown for part in self.m_parts.get(scene.state, [follow.SPart(scene.state)])]
        )

    def tick(self, now_s: float) -> None:
        """Ask the next values that are due, and push the edits whose quiet time is over."""
        for key in self.m_pushes.due(now_s):
            self._save(key)
        if not self.m_following or self.m_paused:
            return
        part = self.m_poller.next(now_s)
        if part is None:
            return
        link, game = livelink.current(), self._game_key()
        key = part.file or (self.m_scenes.m_scenes[part.state].scene if part.state in self.m_scenes.m_scenes else "")
        build, before = link.status().build, self.m_held.get(part)

        def ask():
            reply = link.ui_values([part.state], part.file)
            if not reply.is_ok or not key:
                return reply, None
            held = liverecord.from_reply(reply.data, key, game, build, datetime.now().isoformat(timespec="seconds"))
            if before is not None and before.widgets == held.widgets:
                return reply, held  # the same values as recorded: nothing written four times a second
            try:
                livevalues.recordings(self.m_editor).save(held)  # as Take the live values records it
            except OSError:
                pass
            return reply, held

        self.m_background.run(ask, lambda result: self._valued(part, result), self.m_is_synchronous)

    def _valued(self, part: follow.SPart, result) -> None:
        now_s = time.monotonic()
        if isinstance(result, Exception) or result[1] is None:
            self.m_poller.answered(part, now_s, False)
            return
        held = result[1]
        before = self.m_held.get(part)
        changed = before is None or before.widgets != held.widgets
        self.m_poller.answered(part, now_s, changed)
        self.m_held[part] = held
        if changed and part.state in self.m_scenes.m_scenes:
            self._lay_out(part.state)

    # laying out and drawing

    def _game_key(self) -> str:
        return self.m_editor.game.m_key if self.m_editor.game is not None else ""

    def _lay_out(self, state: str) -> None:
        """Queue a scene to be laid out again."""
        if state not in self.m_dirty:
            self.m_dirty.append(state)
        self._lay_next()

    def _context(self, state: str) -> layout.CContext:
        """The layout context of a scene's state, made once per game and state."""
        game = self.m_editor.game
        if self._m_game is not game:  # another game was opened in the editor: nothing of the one before holds
            self._m_game, self.m_contexts = game, {}
            self._m_pictures = CPictures(game.m_files)
        ctx = self.m_contexts.get(state)
        if ctx is None:
            fonts = getattr(self.m_editor, "m_fonts", None)
            ctx = layout.CContext(
                game.m_files, viewmodels.CSamples(), canvas.measure, m_fonts=fonts.lookup if fonts else None
            )
            ctx.m_defaults = getattr(self.m_editor.m_ctx, "m_defaults", None)
            self.m_contexts[state] = ctx
        return ctx

    def _lay_next(self) -> None:
        """Lay out the next scene off the window's thread, one at a time."""
        if self._m_laying or not self.m_dirty or self.m_editor.game is None:
            return
        state = self.m_dirty.pop(0)
        scene = self.m_scenes.m_scenes.get(state)
        if scene is None:
            self._lay_next()
            return
        ctx, pictures, files = self._context(state), self._m_pictures, self.m_editor.game.m_files
        texts, values = {}, {}
        for part in self.m_parts.get(state, [follow.SPart(state)]):
            held = self.m_held.get(part)
            if held is not None:
                texts.update(held.texts())
                values.update(held.overrides())
        window, forget, self._m_forget = self.m_scenes.m_window, self._m_forget, []

        def work():
            for root in forget:
                for each in self.m_contexts.values():
                    each.forget(root)
            root = files.root(scene.scene)
            if root is None:
                return None
            ctx.m_live_text, ctx.m_live_values, ctx.m_window_aspect = texts, values, window[0] / window[1]
            frame = layout.place(scene.scene, root, ctx)
            zoom = window[1] / frame.size[1] if frame.size[1] > 0 else 1.0  # a scene file may state no height
            image = canvas.render(frame, pictures, zoom, background=CLEAR)
            return image, follow.parts_of(state, root, _templates)

        self._m_laying = True
        self.m_background.run(work, lambda result: self._laid(state, result), self.m_is_synchronous)

    def _laid(self, state: str, result) -> None:
        self._m_laying = False
        if state in self.m_scenes.m_scenes:
            if isinstance(result, Exception) or result is None:
                self.m_notes[state] = str(result) if result is not None else tr("ui.mirror_no_file")
                self.m_images.pop(state, None)
            else:
                self.m_images[state], parts = result
                self.m_notes.pop(state, None)
                if parts != self.m_parts.get(state):
                    self.m_parts[state] = parts
                    self._follow_parts()
            self._fill_list()
            self._show()
        self._lay_next()

    def _show(self) -> None:
        """Show the pictures of the scenes that are not hidden."""
        layers = [
            (scene.state, self.m_images[scene.state]) for scene in self.m_scenes.shown() if scene.state in self.m_images
        ]
        self.m_view.show_layers(layers, self.m_scenes.m_window)

    def _fill_list(self) -> None:
        """The scene list, the top scene first, with each scene's check state and why it shows nothing."""
        self.m_list.blockSignals(True)
        self.m_list.clear()
        for i, scene in enumerate(reversed(self.m_scenes.stack())):  # the top first, as a layer list reads
            item = QTreeWidgetItem([scene.scene, str(len(self.m_scenes.m_scenes) - i), scene.parent])
            item.setData(0, Qt.ItemDataRole.UserRole, scene.state)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked if scene.hidden else Qt.CheckState.Checked)
            if scene.state in self.m_notes:
                item.setToolTip(0, self.m_notes[scene.state])
            self.m_list.addTopLevelItem(item)
        self.m_list.blockSignals(False)

    def _ticked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.m_scenes.hide(item.data(0, Qt.ItemDataRole.UserRole), item.checkState(0) != Qt.CheckState.Checked)
        self._follow_parts()
        self._show()

    def _size_changed(self, index: int) -> None:
        self.m_view.m_fit = index == 0
        self.scroll.setWidgetResizable(self.m_view.m_fit)
        if self.m_view.m_fit:
            self.m_view.setMinimumSize(320, 180)
            self.m_view.setMaximumSize(16777215, 16777215)
        self._show()

    def edit_scene(self, state: str) -> None:
        """Open a scene the game shows in the editor; the mirror goes on following."""
        scene = self.m_scenes.m_scenes.get(state)
        if scene is not None:
            self.m_editor.open_file(scene.scene)

    def _say(self, text: str) -> None:
        """Show a line in the status bar."""
        self.statusBar().showMessage(text)

    # the edit loop

    def showing(self, key: str) -> list[str]:
        """The open states whose scenes show the file ``key``: the scene itself, a file it places, or deeper."""
        game = self.m_editor.game
        if game is None:
            return []
        entry = game.m_files.m_entries.get(key)
        placed = set(game.m_files.scene_states([entry.path])) if entry is not None and not entry.is_scene else set()
        return [scene.state for scene in self.m_scenes.stack() if scene.scene == key or scene.state in placed or
                any(part.file == key for part in self.m_parts.get(scene.state, []))]  # fmt: skip

    def edited(self, doc) -> None:
        """An edit of ``doc`` in the editor: the scenes that show it are laid out again (its tree is the editor's), and
        with Push edits on, it is pushed once no further edit came for a moment."""
        states = self.showing(doc.m_key)
        if not states:
            return
        self._m_forget.append(doc.m_root)
        for state in states:
            self._lay_out(state)
        if self.m_push_box.isChecked() and self.m_following:
            self.m_pushes.edit(doc.m_key, time.monotonic())

    def _save(self, key: str) -> None:
        """Keep the file as Save does (which pushes it, ``kept``); only the file open in the editor is saved so."""
        doc = self.m_editor.doc
        if doc is not None and doc.m_key == key:
            self.m_editor.save()
        else:
            self._say(tr("ui.mirror_push_open_only", name=key))

    def push_now(self) -> None:
        """Push the open file now: switch Push edits on and save it."""
        doc = self.m_editor.doc
        self.m_pushes.due(time.monotonic(), at_once=True)
        if doc is None or not self.showing(doc.m_key):
            self._say(tr("ui.mirror_push_not_open"))
            return
        if not self.m_push_box.isChecked():
            self.m_push_box.setChecked(True)
        self.m_editor.save()

    def kept(self, doc, path: str, data: bytes) -> None:
        """The editor kept ``doc`` in the project: with Push edits on and a scene of the game showing it, it goes into
        the installed mod and the game reloads the scenes that show it."""
        if not self.m_push_box.isChecked() or not self.m_following:
            return
        states = self.showing(doc.m_key)
        if not states:
            self._say(tr("ui.mirror_push_not_open"))
            return
        current_project, settings, link = self.m_editor.project(), self.m_editor.settings, livelink.current()

        def push():
            mods = project.mods_folder_of(current_project.game, settings)
            folder = follow.installed_mod(
                mods, current_project.mod_id, lambda candidate: _mod_id(candidate, current_project.game)
            )
            if folder is None:
                return None
            follow.write_into(folder, path, data)
            return link.reload(livelink.SCENES, [path], mod=current_project.mod_id, states=states)

        self._say(tr("ui.mirror_pushing", name=path.rsplit("/", 1)[-1]))
        self.m_background.run(push, lambda reply: self._pushed(states, reply), self.m_is_synchronous)

    def _pushed(self, states: list[str], reply) -> None:
        if reply is None:
            self._say(tr("ui.mirror_push_install_first"))
        elif isinstance(reply, Exception):
            self._say(tr("ui.mirror_push_failed", why=reply))
        elif not reply.is_ok:
            self._say(tr("ui.mirror_push_refused", why=reply.message or reply.status))
        else:
            for state in states:
                self.m_poller.refresh(state)  # the reloaded scene's values are taken again
            self._say(tr("ui.mirror_pushed", states=", ".join(states)))

    def closeEvent(self, event) -> None:
        self.stop()
        self.m_timer.stop()
        for remove in self._m_unhook:
            remove()
        if getattr(self.m_editor, "m_mirror", None) is self:
            self.m_editor.m_mirror = None
        super().closeEvent(event)


def _templates(root: dict) -> list[str]:
    """The widget files a scene's own elements place, in the file's order."""
    return [
        element.template for element in elements.tree(root).walk() if element.kind == "container" and element.template
    ]


def _mod_id(folder, game: str) -> str:
    found, _ = read_mod(folder, GAME_NUMBER.get(game))
    return found.m_mod_id if found is not None else ""


def open_mirror(editor) -> CMirrorWindow:
    """Show the editor's game mirror (made on first use) and let it follow the game."""
    mirror = getattr(editor, "m_mirror", None)
    if mirror is None:
        mirror = editor.m_mirror = CMirrorWindow(editor)
    mirror.show()
    mirror.raise_()
    mirror.start()
    return mirror
