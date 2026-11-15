"""The asset viewer's window: a game's files on the left, the file in the middle (a model in 3D, a texture, a sound
bank or clip, a data file), its parts, fields and sequences around it as the studio's object builder lays them out.

``ViewerWindow(settings, settings_path)`` is the entry (the suite's ``Viewer`` app, or ``run`` alone); ``open_game``
and ``open_path`` are what another app calls to show a file. The game folders are the suite's settings.
docs/tool-suite.md "The viewer" has the user's guide.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox, QStackedWidget

from ..exporting import banks
from ..infra.i18n import N_, number, tr
from ..models import configtree, fileresolve
from ..render import nativelook, rhiview
from ..sources import gametree
from ..sources.overlay import normalize
from ..suite import links, widgets
from ..suite.background import Background
from ..suite.gameopen import open_installed
from . import modelscene, states
from .browser import KINDS, FileBrowser
from .dataview import DataView
from .effectlink import effect_to_edit
from .exporttools import ExportTools
from .modelfiles import ModelFiles
from .modelscene import PlacedEffect, Scene
from .modelview import make_view
from .panels import NODE_ROLE, ChecksPanel, PartsPanel, PropertiesPanel, SequencePanel, UsersPanel
from .referencetools import ReferenceTools
from .renderingmenu import BACKENDS as RENDERERS
from .renderingmenu import RenderingMenu, toggle_action
from .sounddb import SoundDbView
from .soundview import SoundView
from .textureview import TextureView, describe

APP = "viewer"  # the key of its remembered state in the settings
TITLE = N_("Viewer")
HISTORY = 30  # files to go back through


def game_file(tree, name: str) -> str:
    """The file the game loads for a name an asset or a config writes: as it is, else by the rules of
    ``fileresolve`` (a texture's ``_0.dds``, a mesh's ``.rdm``; the name as a game path when nothing is there)."""
    return fileresolve.loaded_file(name, tree.exists, tree.game) or normalize(name, studio=True)


class ViewerWindow(QMainWindow):
    """``hosts_gl``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL."""

    def __init__(self, settings, settings_path: Path | None = None, hosts_gl: bool = False) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        nativelook.apply_hardware(settings.hardware)  # read when the window makes its device
        self.hosts_gl = hosts_gl
        self.tree = None
        self.files: ModelFiles | None = None
        self.scene: Scene | None = None
        self.current = ""
        self.history: list[str] = []
        self.held = 0  # the condition bits held by hand (View > Condition bits), kept from model to model
        self.background = Background(self)
        self.cache = Path(settings.workspace) / "viewer-cache" if settings.workspace else None
        self.browser = FileBrowser()
        self.model = make_view(self._backend(rhiview.chosen(settings.renderer)))
        self.texture = TextureView()
        self.sound = SoundView(cache=self.cache)
        self.sounddb = SoundDbView(cache=self.cache)
        self.data = DataView()
        self.message = QLabel(
            tr("File > Open game, then pick a file on the left."), alignment=Qt.AlignmentFlag.AlignCenter
        )
        self.pages = QStackedWidget()
        for page in (self.message, self.model, self.texture, self.sound, self.sounddb, self.data):
            self.pages.addWidget(page)
        self.setCentralWidget(self.pages)
        self.parts, self.props, self.sequences = PartsPanel(), PropertiesPanel(), SequencePanel()
        self.users = UsersPanel()
        self.checks = ChecksPanel()
        self.docks = {}
        for name, widget, area in (
            (N_("Files"), self.browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            (N_("Parts"), self.parts, Qt.DockWidgetArea.LeftDockWidgetArea),
            (N_("Properties"), self.props, Qt.DockWidgetArea.RightDockWidgetArea),
            (N_("Used by"), self.users, Qt.DockWidgetArea.RightDockWidgetArea),
            (N_("Checks"), self.checks, Qt.DockWidgetArea.RightDockWidgetArea),
            (N_("Sequences"), self.sequences, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"viewer{name}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[name] = dock
        self.resizeDocks([self.docks["Files"], self.docks["Properties"]], [340, 320], Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.docks["Used by"], self.docks["Checks"])
        self.exports = ExportTools(self)  # before the menus: it adds its dock and its File entries
        self.references = ReferenceTools(self)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("No game open"))

    # building the window
    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("&File"))
        games = file.addMenu(tr("Open &game"))
        self.game_actions = {}
        for key, name in gametree.GAMES.items():
            act = games.addAction(name, lambda k=key: self.open_game(k))
            self.game_actions[key] = act
        file.addAction(tr("Open a &folder of loose files..."), self._open_folder)
        file.addAction(tr("Lay a &mod folder over the game..."), self._lay_mod)
        file.addSeparator()
        file.addAction(tr("&Back"), self.back, QKeySequence(QKeySequence.StandardKey.Back))
        file.addAction(tr("&Who uses the file"), self.find_users, QKeySequence("Ctrl+U"))
        self.place_action = file.addAction(tr("&Place on the open island"), self._place)
        self.place_action.setVisible(links.available(links.PLACE_MODEL))  # alone, the viewer has no island editor
        self.edit_action = file.addAction(tr("&Edit in the object builder"), self._edit_object)
        self.edit_action.setVisible(links.available(links.EDIT_OBJECT))
        self.effect_action = file.addAction(tr("Edit the effect in the &particle editor"), self._edit_effect)
        self.effect_action.setVisible(links.available(links.EDIT_EFFECT))
        file.addAction(tr("Save the &picture..."), self._save_picture, QKeySequence("Ctrl+Shift+S"))
        self.references.add_actions(file)
        self.exports.add_actions(file)
        view = self.menuBar().addMenu(tr("&View"))
        self.rendering = RenderingMenu(self, view.addMenu(tr("&Rendering")))
        view.addSeparator()
        self.toggles = {}
        for key, text, on in (
            ("shadows", N_("&Shadows"), True),
            ("ground", N_("G&round"), True),
            ("particles", N_("&Effects"), True),
            ("wireframe", N_("&Wireframe"), False),
            ("cloth", N_("Simulate &cloth"), False),
            ("night", N_("&Night: the lights shine (the game's look)"), False),
        ):
            self.toggles[key] = toggle_action(view, tr(text), on, lambda on, k=key: self._set_state(**{k: on}))
        self.bits_menu = view.addMenu(tr("Condition &bits"))
        self.bits_menu.aboutToShow.connect(self._fill_bits)
        view.addSeparator()
        lods = view.addMenu(tr("&Level of detail"))
        group = QActionGroup(self, exclusive=True)
        self.lod_actions = []
        for n in range(configtree.LOD_COUNT):
            act = lods.addAction(tr("LOD {n}", n=n), lambda n=n: self._reload_model(lod=n))
            act.setCheckable(True)
            act.setChecked(n == 0)
            group.addAction(act)
            self.lod_actions.append(act)
        self.shared_action = toggle_action(
            view, tr("Read the baked &shared/ copy (as the game does)"), True, lambda on: self._reload_model(shared=on)
        )
        view.addAction(tr("&Frame the model"), lambda: self.model.frame_model(), QKeySequence("F"))
        windows = self.menuBar().addMenu(tr("&Window"))
        for dock in self.docks.values():
            windows.addAction(dock.toggleViewAction())

    def _wire(self) -> None:
        self.browser.opened.connect(self.open_path)
        self.browser.picked.connect(self._file_facts)
        self.parts.selected.connect(self._node_picked)
        self.parts.hidden_changed.connect(lambda hidden: self._set_state(hidden=frozenset(hidden)))
        self.props.open_file.connect(self.open_path)
        self.users.open_file.connect(self.open_path)
        self.checks.open_file.connect(self.open_path)
        self.users.show_asset.connect(self._show_asset)
        self.sequences.changed.connect(self._seek)
        self.sequences.playing.connect(self._play)
        self.sequences.speed.valueChanged.connect(lambda v: setattr(self.model, "speed", v))
        self._wire_model()

    def _wire_model(self) -> None:
        self.model.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.model.picked.connect(self._part_clicked)
        self.model.frameSwapped.connect(self._follow_clock)

    def _backend(self, name: str) -> str:
        return rhiview.composable(name, self.hosts_gl)

    def set_renderer(self, name: str, remember: bool = True) -> None:
        """Draw the 3D view with another device (``render.rhiview.BACKENDS``), remembered in the settings as the
        device of our plain look (``remember``); one of another API than the window shows takes effect when the
        viewer opens next."""
        if remember:
            self.settings.renderer = name
        backend = self._backend(name)
        if backend == self.model.backend:
            return
        if rhiview.api_of(backend) != rhiview.api_of(self.model.backend):
            self.statusBar().showMessage(
                tr(
                    "{renderer}: from the next start of the viewer (a window keeps the graphics API it opened with)",
                    renderer=RENDERERS[name],
                ),
                12000,
            )
            return
        old, shown = self.model, self.pages.currentWidget() is self.model
        self.model = make_view(backend)
        self.model.camera, self.model.state = old.camera, old.state
        self.model.playing, self.model.speed = old.playing, old.speed
        self._wire_model()
        self.pages.insertWidget(self.pages.indexOf(old), self.model)
        if self.scene is not None and self.files is not None:
            self.model.show_scene(self.scene, self.files)
        if shown:
            self.pages.setCurrentWidget(self.model)
        old.release()
        self.pages.removeWidget(old)
        old.deleteLater()
        note = tr(" ({backend} in this window)", backend=backend) if backend != name else ""
        self.statusBar().showMessage(
            tr("The 3D view draws with {renderer}{note}", renderer=RENDERERS[name], note=note), 8000
        )

    # games and files
    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a file to show."""
        open_installed(
            self, self.settings, key, self.background, then=lambda tree: self._opened(tree, key, then), wait=wait
        )

    def _opened(self, tree, key: str, then: str) -> None:
        if isinstance(tree, Exception):
            self.statusBar().showMessage(
                tr("{game} did not open: {error}", game=gametree.GAMES.get(key, key), error=tree)
            )
            return
        self.set_tree(tree)
        self.settings.apps.setdefault(APP, {})["game"] = key
        if then:
            self.open_path(then)

    def set_tree(self, tree) -> None:
        """Browse and show the files of ``tree`` (a ``sources.gametree.GameTree``)."""
        self.tree, self.files = tree, ModelFiles(tree)
        self.users.note(tr("File > Who uses the file (Ctrl+U) reads every file once."))
        self.browser.set_tree(tree)
        self.references.tree_changed()
        self.rendering.apply()
        self.model.clear()
        self.pages.setCurrentWidget(self.message)
        self.message.setText(
            tr("{name}: pick a file on the left (models, textures, effects, sounds, data).", name=tree.name)
        )
        self.setWindowTitle(tr("{name} - {title}", name=tree.name, title=tr(TITLE)))
        self.statusBar().showMessage(tr("{name}: {count} files", name=tree.name, count=number(len(tree.paths))))

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("A folder of loose game files"))
        if folder:
            tree = gametree.loose_tree(Path(folder))
            tree.game = self.tree.game if self.tree else ""
            self.set_tree(tree)

    def _lay_mod(self) -> None:
        if self.tree is None:
            QMessageBox.information(self, tr(TITLE), tr("Open a game first."))
            return
        folder = QFileDialog.getExistingDirectory(self, tr("A mod folder: its files win over the game's"))
        if folder:
            self.set_tree(self.tree.over(Path(folder)))

    def open_file(self, game: str, path: str) -> None:
        """Show a file of a game, opening that game first when another is open (the suite's ``view_file``)."""
        if self.tree is not None and self.tree.game == game:
            self.open_path(path)
        else:
            self.open_game(game, path)

    def export_files(self, game: str, paths: list[str]) -> bool:
        """Export game files for another app (the suite's ``export_files``): the export dialog of the open game; when
        another game is open, that one opens first (its files are then exported with File > Export)."""
        if self.tree is None or self.tree.game != game:
            self.open_game(game, paths[0] if paths else "")
            self.statusBar().showMessage(tr("{game} opens; then File > Export...", game=game), 8000)
            return bool(paths)
        return self.exports.export_paths([game_file(self.tree, p) for p in paths], tr("Export"))

    def open_path(self, path: str, remember: bool = True) -> None:
        """Show a game file in the view its kind needs; the browser selects it."""
        if self.tree is None:
            return
        path = game_file(self.tree, path)
        if not self.tree.exists(path):
            self.statusBar().showMessage(tr("{path} is not in the files", path=path), 8000)
            return
        if remember and self.current and self.current != path:
            self.history = [*self.history, self.current][-HISTORY:]
        self.current = path
        self.browser.reveal(path)
        try:
            self._show(path)
        except Exception as exc:  # a file the views do not take: say so, keep the window usable
            self.pages.setCurrentWidget(self.message)
            self.message.setText(
                tr("{path} could not be shown:\n{kind}: {error}", path=path, kind=type(exc).__name__, error=exc)
            )
        self.exports.file_shown(path)
        self.references.file_shown(path)
        self.settings.apps.setdefault(APP, {})["file"] = path

    def find_users(self) -> None:
        """Who names the open file: the reference graph (one read of every file, ``viewer.referencetools``) answers
        for this panel and the References panel."""
        self.references.find_users()

    def _show_asset(self, guid: int) -> None:
        if self.tree is None or not links.available(links.SHOW_ASSET):
            self.statusBar().showMessage(tr("the asset editor opens assets in the suite's window"), 8000)
            return
        if not links.follow(links.SHOW_ASSET, self.tree.game, guid):
            self.statusBar().showMessage(tr("asset {guid} is not in the asset editor's game", guid=guid), 8000)

    def _edit_object(self) -> None:
        if self.tree is None or not self.current.endswith((".cfg", ".mcf", ".prp")):
            self.statusBar().showMessage(tr("open a model config to edit it"), 8000)
        elif not links.follow(links.EDIT_OBJECT, self.tree.game, self.current):
            self.statusBar().showMessage(tr("the object builder opens in the suite's window"), 8000)

    def _edit_effect(self) -> None:
        row = self.parts.currentItem()
        found = effect_to_edit(self.scene, (row.data(0, NODE_ROLE) if row is not None else "") or "")
        if self.tree is None or found is None:
            self.statusBar().showMessage(tr("select the part that places an effect, or open an effect file"), 8000)
        elif not links.follow(links.EDIT_EFFECT, self.tree.game, *found):
            self.statusBar().showMessage(tr("the particle editor opens in the suite's window"), 8000)

    def _place(self) -> None:
        if self.tree is None or not self.current.endswith(".cfg"):
            self.statusBar().showMessage(tr("open a model config to place it"), 8000)
            return
        why = links.follow(links.PLACE_MODEL, self.tree.game, self.current)
        if why is None:
            why = tr("the island editor places models in the suite's window")
        if why:
            self.statusBar().showMessage(why, 8000)

    def _show(self, path: str) -> None:
        data_kinds = {k: v for k, v in KINDS.items() if v}
        self._file_facts(path)
        self.scene = None
        self.parts.clear()
        self.sequences.show_scene(Scene(path, self.tree.game))
        if path.endswith(data_kinds["Models"]):
            self._show_model(modelscene.build(self.tree, path, self.tree.game, self._options()))
        elif path.endswith(data_kinds["Effects"]):
            scene = Scene(path, self.tree.game, path, effects=[PlacedEffect("", path, np.identity(4))])
            self._show_model(scene)
        elif path.endswith(data_kinds["Textures"]):
            self.texture.show_texture(path, self.tree.read(path))
            self.pages.setCurrentWidget(self.texture)
        elif path.endswith("/sounddatabase.xml") or path.endswith((".snd", ".sp")):
            show = {".snd": self.sounddb.show_timeline, ".sp": self.sounddb.show_speech}.get(
                path[path.rfind(".") :], self.sounddb.show_database
            )
            show(path, self.tree.read(path), self.tree)
            self.pages.setCurrentWidget(self.sounddb)
        elif path.endswith(".bnk"):
            self.sound.show_bank(path, self.tree.read(path), self.tree)
            self.pages.setCurrentWidget(self.sound)
        elif path.endswith(data_kinds["Sounds"]):
            self.sound.show_clip(path, self.tree.read(path))
            self.pages.setCurrentWidget(self.sound)
        else:
            self.data.show_file(path, self.tree.read(path))
            self.pages.setCurrentWidget(self.data)

    def _options(self, **change) -> modelscene.Options:
        lod = next((n for n, act in enumerate(self.lod_actions) if act.isChecked()), 0)
        values = {"lod": lod, "shared": self.shared_action.isChecked(), **change}
        return modelscene.Options(**values)

    def _show_model(self, scene: Scene) -> None:
        self.scene = scene
        self.model.state.hidden, self.model.state.selected = frozenset(), None
        self.model.show_scene(scene, self.files)
        self.parts.show_scene(scene)
        self.sequences.show_scene(scene)
        self.checks.show_checks(scene, {})
        self.pages.setCurrentWidget(self.model)
        for note in scene.notes[:3]:
            self.statusBar().showMessage(note, 8000)

    def _reload_model(self, **change) -> None:
        if self.scene is not None and self.current and modelscene.is_model(self.current):
            self._show_model(modelscene.build(self.tree, self.current, self.tree.game, self._options(**change)))

    def back(self) -> None:
        if self.history:
            path, self.history = self.history[-1], self.history[:-1]
            self.open_path(path, remember=False)

    # the panels
    def _file_facts(self, path: str) -> None:
        if self.tree is None or not self.tree.exists(path):
            return
        data = self.tree.read(path)
        found = self.tree.origins(path)
        rows = [(tr("path"), path), (tr("read from"), found[0] if found else "-")]
        rows += [(tr("also in (overridden)"), ", ".join(found[1:]))] if len(found) > 1 else []
        rows.append((tr("size"), tr("{n} bytes", n=number(len(data)))))
        if path.endswith(KINDS["Textures"]):
            rows += describe(data)
        elif path.endswith((".wem", ".wav")):
            try:
                rows += [(k, str(v)) for k, v in banks.wave_info(data).items()]
            except banks.Unplayable as exc:
                rows.append((tr("format"), str(exc)))
        shared = configtree.shared_config(path) if path.endswith(".cfg") else ""
        if shared and self.tree.exists(shared):
            rows.append((tr("baked copy"), tr("{path} (the game reads this one)", path=shared)))
        self.props.show_rows(rows)

    def _node_picked(self, path) -> None:
        self._set_state(selected=path if path else None)
        if path is None or self.scene is None or self.scene.tree is None:
            return
        node = self.scene.tree.find(path) if path else self.scene.tree
        if node is None:  # a nested config's part: its own file has the fields
            self.props.show_rows(
                [(tr("part"), path), (tr("open"), tr("double-click the FILE entry's FileName to open it"))]
            )
            return
        self.props.show_node(node)

    def _part_clicked(self, node: str) -> None:
        if node:
            self.parts.select_node(node)
            self.statusBar().showMessage(tr("part {node}", node=node), 8000)
        else:
            self.parts.setCurrentItem(self.parts.topLevelItem(0))

    def set_state_quietly(self, **change) -> None:
        """The view's state changed by the menus (``renderingmenu``)."""
        self._set_state(**change)

    def _set_state(self, **change) -> None:
        state = self.model.state
        if "night" in change and change["night"] != state.night:  # the time slices fade from now (viewer.states)
            state.night_changed = time.monotonic()
        for key, value in change.items():
            setattr(state, key, value)
        self.model.update()

    def _fill_bits(self) -> None:
        """View > Condition bits: the bits the open model reads and the engine's own, each held while ticked
        (``viewer.states``; engine-animation "Time of day and the states of effects, lights and decals")."""
        self.bits_menu.clear()
        game = self.scene.game if self.scene is not None else ""
        found = states.bits_read(self.scene) if self.scene is not None else []
        for bit in sorted({*found, *(b for b in range(32) if self.held >> b & 1)}):
            name = tr("Bit {bit}: {name}", bit=bit, name=states.bit_name(bit, game)) if game == "anno117" else None
            toggle_action(self.bits_menu, name or tr("Bit {bit}", bit=bit), bool(self.held >> bit & 1),
                          lambda on, b=bit: self.hold_bits(self.held | 1 << b if on else self.held & ~(1 << b)))  # fmt: skip
        if not found:
            self.bits_menu.addAction(tr("(the open model reads no condition bits)")).setEnabled(False)
        self.bits_menu.addSeparator()
        self.bits_menu.addAction(tr("&Release all"), lambda: self.hold_bits(0))

    def hold_bits(self, held: int) -> None:
        """Hold these condition bits by hand from now on (with the sequence's)."""
        self.held = held
        self.model.state.states = self.sequences.states(self.model.state.time, held)
        self.model.update()

    def _seek(self, seconds: float, animations: dict) -> None:
        self.model.state.animation = animations
        self.model.state.states = self.sequences.states(seconds, self.held)
        self.model.set_time(seconds)

    def _play(self, on: bool) -> None:
        self.model.playing = on

    def _follow_clock(self) -> None:
        if self.scene is None:
            return
        renderer = getattr(self.model, "renderer", None)
        if renderer is not None and renderer.scene is self.scene:
            self.checks.show_checks(self.scene, renderer.failed)
        t = self.model.state.time
        self.sequences.tick(t)
        if self.model.playing:
            self.model.state.animation = self.sequences.animations(t)
            self.model.state.states = self.sequences.states(t, self.held)

    def _save_picture(self) -> None:
        if self.pages.currentWidget() is not self.model:
            return
        name, _f = QFileDialog.getSaveFileName(
            self, tr("Save the picture"), "", "PNG (*.png)"
        )  # i18n: ok (a file filter)
        if name:
            self.model.image().save(name)

    def closeEvent(self, event) -> None:
        self.sound._stop()
        self.sounddb._stop()
        self.model.release()
        super().closeEvent(event)


def run(game: str | None = None, path: str = "") -> int:
    """The viewer alone in a window of its own, on the suite's settings."""
    from ..render.glformat import prepare

    prepare()
    return widgets.run_alone(ViewerWindow, APP, (1600, 980), game, path)
