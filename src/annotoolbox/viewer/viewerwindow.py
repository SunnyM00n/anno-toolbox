"""The asset viewer's window: a game's files on the left, the file in the middle (a model in 3D, a texture, a sound
bank or clip, a data file), its parts, fields and sequences around it as the studio's object builder lays them out.

``CViewerWindow(settings, settings_path)`` is the entry (the suite's ``Viewer`` app, or ``run`` alone); ``open_game``
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
from ..suite import centralmenu, gamesync, links, widgets
from ..suite.background import CBackground
from ..suite.gameopen import open_installed
from . import modelscene, states
from .browser import KINDS, CFileBrowser
from .dataview import CDataView
from .effectlink import effect_to_edit
from .exporttools import CExportTools
from .modelfiles import CModelFiles
from .modelscene import CScene, SPlacedEffect
from .modelview import make_view, save_picture
from .panels import NODE_ROLE, CChecksPanel, CPartsPanel, CPropertiesPanel, CSequencePanel, CUsersPanel
from .referencetools import CReferenceTools
from .renderingmenu import CRenderingMenu, toggle_action
from .sounddb import CSoundDbView
from .soundview import CSoundView
from .textureview import CTextureView, describe

APP = "viewer"  # the key of its remembered state in the settings
TITLE = N_("common.viewer")
HISTORY = 30  # files to go back through


def game_file(tree, name: str) -> str:
    """The file the game loads for a name an asset or a config writes: as it is, else by the rules of
    ``fileresolve`` (a texture's ``_0.dds``, a mesh's ``.rdm``; the name as a game path when nothing is there)."""
    return fileresolve.loaded_file(name, tree.exists, tree.m_game) or normalize(name, studio=True)


class CViewerWindow(QMainWindow):
    """``is_gl_host``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL."""

    def __init__(self, settings, settings_path: Path | None = None, is_gl_host: bool = False) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        nativelook.apply_hardware(settings.hardware)  # read when the window makes its device
        self.m_is_gl_host = is_gl_host
        self.m_tree = None
        self.m_files: CModelFiles | None = None
        self.m_scene: CScene | None = None
        self.m_current = ""
        self.m_history: list[str] = []
        self.m_held = 0  # the condition bits held by hand (View > Condition bits), kept from model to model
        self.m_background = CBackground(self)
        self.m_cache = Path(settings.workspace) / "viewer-cache" if settings.workspace else None
        self.m_browser = CFileBrowser()
        self.m_model = make_view(self._backend(rhiview.chosen(settings.renderer)))
        self.m_texture = CTextureView()
        self.m_sound = CSoundView(cache=self.m_cache)
        self.m_sounddb = CSoundDbView(cache=self.m_cache)
        self.m_data = CDataView()
        self.m_message = QLabel(tr("viewer.file_gt_open_game_pick"), alignment=Qt.AlignmentFlag.AlignCenter)
        self.m_pages = QStackedWidget()
        for page in (self.m_message, self.m_model, self.m_texture, self.m_sound, self.m_sounddb, self.m_data):
            self.m_pages.addWidget(page)
        self.setCentralWidget(self.m_pages)
        self.m_parts, self.m_props, self.m_sequences = CPartsPanel(), CPropertiesPanel(), CSequencePanel()
        self.m_users = CUsersPanel()
        self.m_checks = CChecksPanel()
        self.m_docks = {}
        for key, name, widget, area in (
            ("Files", N_("common.files"), self.m_browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Parts", N_("viewer.parts"), self.m_parts, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Properties", N_("common.properties"), self.m_props, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Used by", N_("viewer.used_by"), self.m_users, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Checks", N_("common.checks"), self.m_checks, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Sequences", N_("common.sequences"), self.m_sequences, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"viewer{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.m_docks[key] = dock
        self.resizeDocks([self.m_docks["Files"], self.m_docks["Properties"]], [340, 320], Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.m_docks["Used by"], self.m_docks["Checks"])
        self.m_exports = CExportTools(self)  # before the menus: it adds its dock and its File entries
        self.m_references = CReferenceTools(self)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("common.no_game_open"))

    # building the window
    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("common.menu.file"))
        games = centralmenu.mark(file.addMenu(tr("common.menu.open_game")), centralmenu.GAME)
        self.m_game_actions = {}
        for key, name in gametree.GAMES.items():
            action = games.addAction(name, lambda k=key: self.open_game(k))
            self.m_game_actions[key] = action
        file.addAction(tr("common.open_folder_loose_files"), self._open_folder)
        file.addAction(tr("viewer.lay_mod_folder_over_game"), self._lay_mod)
        file.addSeparator()
        file.addAction(tr("viewer.back"), self.back, QKeySequence(QKeySequence.StandardKey.Back))
        file.addAction(tr("viewer.who_uses_file"), self.find_users, QKeySequence("Ctrl+U"))
        self.m_place_action = file.addAction(tr("viewer.place_open_island"), self._place)
        self.m_place_action.setVisible(links.available(links.PLACE_MODEL))  # alone, the viewer has no island editor
        self.m_edit_action = file.addAction(tr("viewer.edit_object_builder"), self._edit_object)
        self.m_edit_action.setVisible(links.available(links.EDIT_OBJECT))
        self.m_effect_action = file.addAction(tr("viewer.edit_effect_particle_editor"), self._edit_effect)
        self.m_effect_action.setVisible(links.available(links.EDIT_EFFECT))
        file.addAction(tr("common.save_picture_dots"), self._save_picture, QKeySequence("Ctrl+Shift+S"))
        self.m_references.add_actions(file)
        self.m_exports.add_actions(file)
        view = self.menuBar().addMenu(tr("common.menu.view"))
        self.m_rendering = CRenderingMenu(self, view.addMenu(tr("common.menu.rendering")))
        view.addSeparator()
        self.m_toggles = {}
        for key, text, is_on in (
            ("shadows", N_("common.shadows"), True),
            ("ground", N_("common.ground"), True),
            ("particles", N_("common.effects_accel"), True),
            ("wireframe", N_("viewer.wireframe"), False),
            ("cloth", N_("viewer.simulate_cloth"), False),
            ("night", N_("viewer.night_lights_shine_games_look"), False),
        ):
            self.m_toggles[key] = toggle_action(
                view, tr(text), is_on, lambda is_on, k=key: self._set_state(**{k: is_on})
            )
        self.m_bits_menu = view.addMenu(tr("viewer.menu.condition_bits"))
        self.m_bits_menu.aboutToShow.connect(self._fill_bits)
        view.addSeparator()
        lods = view.addMenu(tr("common.menu.level_detail"))
        group = QActionGroup(self, exclusive=True)
        self.m_lod_actions = []
        for n in range(configtree.LOD_COUNT):
            action = lods.addAction(tr("common.lod_n", n=n), lambda n=n: self._reload_model(lod=n))
            action.setCheckable(True)
            action.setChecked(n == 0)
            group.addAction(action)
            self.m_lod_actions.append(action)
        self.m_shared_action = toggle_action(
            view, tr("common.read_baked_shared_copy_game"), True, lambda is_on: self._reload_model(shared=is_on)
        )
        view.addAction(tr("common.frame_model"), lambda: self.m_model.frame_model(), QKeySequence("F"))
        windows = self.menuBar().addMenu(tr("common.menu.window"))
        widgets.add_docks(windows, self.m_docks.values())

    def _wire(self) -> None:
        widgets.find_key(self, self.m_browser.m_search)
        self.m_browser.opened.connect(self.open_path)
        self.m_browser.picked.connect(self._file_facts)
        self.m_parts.selected.connect(self._node_picked)
        self.m_parts.hidden_changed.connect(lambda hidden: self._set_state(hidden=frozenset(hidden)))
        self.m_props.open_file.connect(self.open_path)
        self.m_users.open_file.connect(self.open_path)
        self.m_checks.open_file.connect(self.open_path)
        self.m_users.show_asset.connect(self._show_asset)
        self.m_sequences.changed.connect(self._seek)
        self.m_sequences.playing.connect(self._play)
        self.m_sequences.m_speed.valueChanged.connect(lambda value: setattr(self.m_model, "m_speed", value))
        self._wire_model()

    def _wire_model(self) -> None:
        self.m_model.status.connect(lambda message: self.statusBar().showMessage(message, 8000))
        self.m_model.picked.connect(self._part_clicked)
        self.m_model.frameSwapped.connect(self._follow_clock)

    def _backend(self, name: str) -> str:
        return rhiview.composable(name, self.m_is_gl_host)

    def set_renderer(self, name: str, remember: bool = True) -> None:
        """Draw the 3D view with another device (``render.rhiview.BACKENDS``), remembered in the settings as the
        device of our plain look (``remember``); one of another API than the window shows takes effect when the
        viewer opens next."""
        if remember:
            self.m_settings.renderer = name
        backend = self._backend(name)
        if backend == self.m_model.m_backend:
            return
        if rhiview.api_of(backend) != rhiview.api_of(self.m_model.m_backend):
            self.statusBar().showMessage(
                tr(
                    "viewer.next_start_viewer_window_keeps",
                    renderer=rhiview.LABELS[name],
                ),
                12000,
            )
            return
        old, shown = self.m_model, self.m_pages.currentWidget() is self.m_model
        self.m_model = make_view(backend)
        self.m_model.m_camera, self.m_model.m_state = old.m_camera, old.m_state
        self.m_model.m_playing, self.m_model.m_speed = old.m_playing, old.m_speed
        self._wire_model()
        self.m_pages.insertWidget(self.m_pages.indexOf(old), self.m_model)
        if self.m_scene is not None and self.m_files is not None:
            self.m_model.show_scene(self.m_scene, self.m_files)
        if shown:
            self.m_pages.setCurrentWidget(self.m_model)
        old.release()
        self.m_pages.removeWidget(old)
        old.deleteLater()
        note = tr("viewer.backend_in_this_window", backend=backend) if backend != name else ""
        self.statusBar().showMessage(tr("viewer.3d_view_draws", renderer=rhiview.LABELS[name], note=note), 8000)

    # games and files
    def open_game(self, key: str, then: str = "", is_waiting: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a file to show."""
        if not gamesync.may_switch(self, key):  # the suite's other apps follow (suite.md "One game")
            return
        open_installed(
            self,
            self.m_settings,
            key,
            self.m_background,
            then=lambda tree: self._opened(tree, key, then),
            is_waiting=is_waiting,
        )

    def _opened(self, tree, key: str, then: str) -> None:
        if isinstance(tree, Exception):
            self.statusBar().showMessage(
                tr("common.game_not_open_error", game=gametree.GAMES.get(key, key), error=tree)
            )
            return
        self.set_tree(tree)
        self.m_settings.apps.setdefault(APP, {})["game"] = key
        if then:
            self.open_path(then)

    def set_tree(self, tree) -> None:
        """Browse and show the files of ``tree`` (a ``sources.gametree.CGameTree``)."""
        if self.m_tree is None or tree.m_game != self.m_tree.m_game:
            self.m_history = []  # another game's paths: Back would look them up in this one
        self.m_tree, self.m_files = tree, CModelFiles(tree)
        self.m_current, self.m_scene = "", None  # nothing of the tree before is open any more
        self.m_parts.clear()
        self.m_props.show_rows([])  # the side panels showed the file of the tree before
        self.m_sequences.show_scene(CScene("", tree.m_game))
        self.m_checks.show_checks(None, {})
        self.m_exports.file_shown("")
        self.m_users.note(tr("viewer.file_gt_uses_ctrl_plus"))
        self.m_browser.set_tree(tree)
        self.m_references.tree_changed()
        self.m_rendering.apply()
        self.m_model.clear()
        self.m_pages.setCurrentWidget(self.m_message)
        self.m_message.setText(tr("viewer.pick_file_left_models_textures", name=tree.m_name))
        self.setWindowTitle(tr("common.title.name_title", name=tree.m_name, title=tr(TITLE)))
        self.statusBar().showMessage(tr("viewer.name_count_files", name=tree.m_name, count=number(len(tree.m_paths))))

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("viewer.folder_loose_game_files"))
        if folder:
            self.open_folder(Path(folder))

    def open_folder(self, folder: Path, is_waiting: bool = False) -> None:
        """Browse a folder of loose files as files of the open game; the folder is listed off the window's thread
        (an unpacked game holds hundreds of thousands of files)."""
        self.statusBar().showMessage(tr("viewer.reading_every_file_folder", folder=folder))
        self.m_background.run(
            lambda: gametree.loose_tree(folder), lambda tree: self._folder_read(tree, folder), is_waiting
        )

    def _folder_read(self, tree, folder: Path) -> None:
        if isinstance(tree, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=folder, error=tree))
            return
        tree.m_game = self.m_tree.m_game if self.m_tree else ""
        self.set_tree(tree)

    def _lay_mod(self) -> None:
        if self.m_tree is None:
            QMessageBox.information(self, tr(TITLE), tr("viewer.open_game_first_sentence"))
            return
        folder = QFileDialog.getExistingDirectory(self, tr("viewer.mod_folder_files_win_over"))
        if folder:
            self.set_tree(self.m_tree.over(Path(folder)))

    def open_file(self, game: str, path: str) -> None:
        """Show a file of a game, opening that game first when another is open (the suite's ``view_file``)."""
        if self.m_tree is not None and self.m_tree.m_game == game:
            self.open_path(path)
        else:
            self.open_game(game, path)

    def export_files(self, game: str, paths: list[str]) -> bool:
        """Export game files for another app (the suite's ``export_files``): the export dialog of the open game; when
        another game is open, that one opens first (its files are then exported with File > Export)."""
        if self.m_tree is None or self.m_tree.m_game != game:
            self.open_game(game, paths[0] if paths else "")
            self.statusBar().showMessage(tr("viewer.opens_file_export", game=game), 8000)
            return bool(paths)
        return self.m_exports.export_paths([game_file(self.m_tree, path) for path in paths], tr("common.export"))

    def open_path(self, path: str, remember: bool = True) -> None:
        """Show a game file in the view its kind needs; the browser selects it."""
        if self.m_tree is None:
            return
        path = game_file(self.m_tree, path)
        if not self.m_tree.exists(path):
            self.statusBar().showMessage(tr("viewer.path_not_files", path=path), 8000)
            return
        if remember and self.m_current and self.m_current != path:
            self.m_history = [*self.m_history, self.m_current][-HISTORY:]
        self.m_current = path
        self.m_browser.reveal(path)
        try:
            self._show(path)
        except Exception as exc:  # a file the views do not take: say so, keep the window usable
            self.m_pages.setCurrentWidget(self.m_message)
            self.m_message.setText(tr("viewer.could_not_shown", path=path, kind=type(exc).__name__, error=exc))
        self.m_exports.file_shown(path)
        self.m_references.file_shown(path)
        self.m_settings.apps.setdefault(APP, {})["file"] = path

    def find_users(self) -> None:
        """Who names the open file: the reference graph (one read of every file, ``viewer.referencetools``) answers
        for this panel and the References panel."""
        self.m_references.find_users()

    def _show_asset(self, guid: int) -> None:
        if self.m_tree is None or not links.available(links.SHOW_ASSET):
            self.statusBar().showMessage(tr("viewer.asset_editor_opens_assets_suites"), 8000)
            return
        if not links.follow(links.SHOW_ASSET, self.m_tree.m_game, guid):
            self.statusBar().showMessage(tr("viewer.asset_not_editors_game", guid=guid), 8000)

    def _edit_object(self) -> None:
        if self.m_tree is None or not self.m_current.endswith((".cfg", ".mcf", ".prp")):
            self.statusBar().showMessage(tr("viewer.open_model_config_edit"), 8000)
        elif not links.follow(links.EDIT_OBJECT, self.m_tree.m_game, self.m_current):
            self.statusBar().showMessage(tr("viewer.object_builder_opens_suites_window"), 8000)

    def _edit_effect(self) -> None:
        row = self.m_parts.currentItem()
        found = effect_to_edit(self.m_scene, (row.data(0, NODE_ROLE) if row is not None else "") or "")
        if self.m_tree is None or found is None:
            self.statusBar().showMessage(tr("viewer.select_part_places_effect_open"), 8000)
        elif not links.follow(links.EDIT_EFFECT, self.m_tree.m_game, *found):
            self.statusBar().showMessage(tr("viewer.particle_editor_opens_suites_window"), 8000)

    def _place(self) -> None:
        if self.m_tree is None or not self.m_current.endswith(".cfg"):
            self.statusBar().showMessage(tr("viewer.open_model_config_place"), 8000)
            return
        why = links.follow(links.PLACE_MODEL, self.m_tree.m_game, self.m_current)
        if why is None:
            why = tr("viewer.island_editor_places_models_suites")
        if why:
            self.statusBar().showMessage(why, 8000)

    def _show(self, path: str) -> None:
        data_kinds = {k: value for k, value in KINDS.items() if value}
        self._file_facts(path)
        self.m_scene = None
        self.m_parts.clear()
        self.m_sequences.show_scene(CScene(path, self.m_tree.m_game))
        if path.endswith(data_kinds["Models"]):
            self._show_model(modelscene.build(self.m_tree, path, self.m_tree.m_game, self._options()))
        elif path.endswith(data_kinds["Effects"]):
            scene = CScene(path, self.m_tree.m_game, path, m_effects=[SPlacedEffect("", path, np.identity(4))])
            self._show_model(scene)
        elif path.endswith(data_kinds["Textures"]):
            self.m_texture.show_texture(path, self.m_tree.read(path))
            self.m_pages.setCurrentWidget(self.m_texture)
        elif path.endswith("/sounddatabase.xml") or path.endswith((".snd", ".sp")):
            show = {".snd": self.m_sounddb.show_timeline, ".sp": self.m_sounddb.show_speech}.get(
                path[path.rfind(".") :], self.m_sounddb.show_database
            )
            show(path, self.m_tree.read(path), self.m_tree)
            self.m_pages.setCurrentWidget(self.m_sounddb)
        elif path.endswith(".bnk"):
            self.m_sound.show_bank(path, self.m_tree.read(path), self.m_tree)
            self.m_pages.setCurrentWidget(self.m_sound)
        elif path.endswith(data_kinds["Sounds"]):
            self.m_sound.show_clip(path, self.m_tree.read(path))
            self.m_pages.setCurrentWidget(self.m_sound)
        else:
            self.m_data.show_file(path, self.m_tree.read(path))
            self.m_pages.setCurrentWidget(self.m_data)

    def _options(self, **change) -> modelscene.SOptions:
        lod = next((index for index, action in enumerate(self.m_lod_actions) if action.isChecked()), 0)
        values = {"lod": lod, "shared": self.m_shared_action.isChecked(), **change}
        return modelscene.SOptions(**values)

    def _show_model(self, scene: CScene) -> None:
        self.m_scene = scene
        self.m_model.m_state.hidden, self.m_model.m_state.selected = frozenset(), None
        self.m_model.show_scene(scene, self.m_files)
        self.m_parts.show_scene(scene)
        self.m_sequences.show_scene(scene)
        self.m_checks.show_checks(scene, {})
        self.m_pages.setCurrentWidget(self.m_model)
        for note in scene.m_notes[:3]:
            self.statusBar().showMessage(note, 8000)

    def _reload_model(self, **change) -> None:
        if self.m_scene is not None and self.m_current and modelscene.is_model(self.m_current):
            self._show_model(modelscene.build(self.m_tree, self.m_current, self.m_tree.m_game, self._options(**change)))

    def back(self) -> None:
        if self.m_history:
            path, self.m_history = self.m_history[-1], self.m_history[:-1]
            self.open_path(path, remember=False)

    # the panels
    def _file_facts(self, path: str) -> None:
        if self.m_tree is None or not self.m_tree.exists(path):
            return
        data = self.m_tree.read(path)
        found = self.m_tree.origins(path)
        rows = [(tr("common.path"), path), (tr("viewer.read_from"), found[0] if found else "-")]
        rows += [(tr("viewer.also_overridden"), ", ".join(found[1:]))] if len(found) > 1 else []
        rows.append((tr("viewer.size"), tr("viewer.n_bytes", n=number(len(data)))))
        if path.endswith(KINDS["Textures"]):
            rows += describe(data)
        elif path.endswith((".wem", ".wav")):
            try:
                rows += [(k, str(value)) for k, value in banks.wave_info(data).items()]
            except banks.CUnplayable as exc:
                rows.append((tr("viewer.format"), str(exc)))
        shared = configtree.shared_config(path) if path.endswith(".cfg") else ""
        if shared and self.m_tree.exists(shared):
            rows.append((tr("viewer.baked_copy"), tr("viewer.game_reads_one", path=shared)))
        self.m_props.show_rows(rows)

    def _node_picked(self, path) -> None:
        self._set_state(selected=path if path else None)
        if path is None or self.m_scene is None or self.m_scene.m_tree is None:
            return
        node = self.m_scene.m_tree.find(path) if path else self.m_scene.m_tree
        if node is None:  # a nested config's part: its own file has the fields
            self.m_props.show_rows(
                [(tr("common.part"), path), (tr("viewer.open"), tr("viewer.double_click_file_entrys_filename"))]
            )
            return
        self.m_props.show_node(node)

    def _part_clicked(self, node: str) -> None:
        if node:
            self.m_parts.select_node(node)
            self.statusBar().showMessage(tr("viewer.part_node", node=node), 8000)
        else:
            self.m_parts.setCurrentItem(self.m_parts.topLevelItem(0))

    def set_state_quietly(self, **change) -> None:
        """The view's state changed by the menus (``renderingmenu``)."""
        self._set_state(**change)

    def _set_state(self, **change) -> None:
        state = self.m_model.m_state
        if "night" in change and change["night"] != state.night:  # the time slices fade from now (viewer.states)
            state.night_changed = time.monotonic()
        for key, value in change.items():
            setattr(state, key, value)
        self.m_model.update()

    def _fill_bits(self) -> None:
        """View > Condition bits: the bits the open model reads and the engine's own, each held while ticked
        (``viewer.states``; engine-animation "Time of day and the states of effects, lights and decals")."""
        self.m_bits_menu.clear()
        game = self.m_scene.m_game if self.m_scene is not None else ""
        found = states.bits_read(self.m_scene) if self.m_scene is not None else []
        for bit in sorted({*found, *(bit_index for bit_index in range(32) if self.m_held >> bit_index & 1)}):
            name = tr("viewer.bit_name", bit=bit, name=states.bit_name(bit, game)) if game == "anno117" else None
            toggle_action(self.m_bits_menu, name or tr("viewer.bit", bit=bit), bool(self.m_held >> bit & 1),
                          lambda is_on, bit_index=bit: self.hold_bits(self.m_held | 1 << bit_index if is_on else self.m_held & ~(1 << bit_index)))  # fmt: skip
        if not found:
            self.m_bits_menu.addAction(tr("viewer.open_model_reads_no_condition")).setEnabled(False)
        self.m_bits_menu.addSeparator()
        self.m_bits_menu.addAction(tr("viewer.release_all"), lambda: self.hold_bits(0))

    def hold_bits(self, held: int) -> None:
        """Hold these condition bits by hand from now on (with the sequence's)."""
        self.m_held = held
        self.m_model.m_state.states = self.m_sequences.states(self.m_model.m_state.time, held)
        self.m_model.update()

    def _seek(self, seconds: float, animations: dict) -> None:
        self.m_model.m_state.animation = animations
        self.m_model.m_state.states = self.m_sequences.states(seconds, self.m_held)
        self.m_model.set_time(seconds)

    def _play(self, is_on: bool) -> None:
        self.m_model.m_playing = is_on

    def _follow_clock(self) -> None:
        if self.m_scene is None:
            return
        renderer = getattr(self.m_model, "m_renderer", None)
        if renderer is not None and renderer.m_scene is self.m_scene:
            self.m_checks.show_checks(self.m_scene, renderer.m_failed)
        seconds = self.m_model.m_state.time
        self.m_sequences.tick(seconds)
        if self.m_model.m_playing:
            self.m_model.m_state.animation = self.m_sequences.animations(seconds)
            self.m_model.m_state.states = self.m_sequences.states(seconds, self.m_held)

    def _save_picture(self) -> None:
        if self.m_pages.currentWidget() is not self.m_model:
            return
        save_picture(self, self.m_model.image)

    def closeEvent(self, event) -> None:
        self.m_sound._stop()
        self.m_sounddb._stop()
        self.m_model.release()
        super().closeEvent(event)


def run(game: str | None = None, path: str = "") -> int:
    """The viewer alone in a window of its own, on the suite's settings."""
    from ..render.glformat import prepare

    prepare()
    return widgets.run_alone(CViewerWindow, APP, (1600, 980), game, path)
