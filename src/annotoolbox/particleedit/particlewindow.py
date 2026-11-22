"""The particle editor's window: the game's effect files and the emitter tree on the left, the viewer's 3D view with the
play bar in the middle, the selected row's fields on the right and its curve below.

``CParticleWindow(settings, settings_path)`` is the entry (the suite's ``Particles`` app);
``open_effect(game, path, model)`` is what another app calls. Opening a file, reading a model's config and building
its scene run off the window's thread; the preview's bake runs on the view's loading threads. The guide is
docs/particle-editor.md; the design docs/tool-suite.md section 31.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, number, tr
from ..models.cfgdoc import SDoc
from ..render import rhiview
from ..sources import gametree
from ..sources.overlay import normalize
from ..suite import appfolders, centralmenu, gameopen, gamesync, links, project, widgets
from ..suite.background import CBackground
from ..viewer import modelscene
from ..viewer.browser import CFileBrowser
from ..viewer.modelfiles import CModelFiles
from ..viewer.modelscene import CScene, SPlacedEffect
from ..viewer.modelview import make_view
from ..viewer.renderingmenu import CRenderingMenu, toggle_action
from . import emitters, outline, projectpart, rdptools
from .curveview import CCurveView
from .effectdoc import EFFECT_ENDINGS, RDP, CEffectDoc, config_read_path
from .fieldform import CFieldForm
from .playbar import CPlayBar
from .savedialog import CSaveDialog

APP = "particles"  # the key of its remembered state in the settings
TITLE = N_("islands.particles")
REDRAW_MS = 150  # after the last edit, the preview is drawn again
KEY = Qt.ItemDataRole.UserRole + 1


class CParticleWindow(QMainWindow):
    """``is_gl_host``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL; ``sync``:
    the work that runs off the window's thread runs at once (tests)."""

    def __init__(
        self, settings, settings_path: Path | None = None, is_gl_host: bool = False, is_synchronous: bool = False
    ) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path, self.m_is_gl_host, self.m_is_synchronous = (
            settings,
            settings_path,
            is_gl_host,
            is_synchronous,
        )
        self.m_tree = None
        self._m_donors: dict[str, list | None] = {}  # game -> the first emitter of each kind (``game_kinds``)
        self.m_background = CBackground(self)
        self.m_effect: CEffectDoc | None = None
        self._m_opening = 0  # counts the effects asked for: only the last one's result is shown
        self.m_files: CModelFiles | None = None
        self.m_scene: CScene | None = None
        self.m_model_scene: CScene | None = None
        self._m_model_drawn: bytes | None = None  # the model config's bytes the model scene was built from
        self.m_items: dict[str, outline.SItem] = {}
        self._m_wanted: str | None = None  # the tree row to select when the tree is next filled (a new emitter)
        self.m_browser, self.m_form, self.m_curve = CFileBrowser(), CFieldForm(), CCurveView()
        self.m_emitters = QTreeWidget(headerHidden=True)
        self.m_view = make_view(rhiview.composable(rhiview.chosen(settings.renderer), is_gl_host))
        self.m_view.m_state.gate_effects = False  # no sequence player here: every effect shows
        self.m_model = self.m_view  # what the viewer's Rendering menu drives
        self.m_play = CPlayBar(self.m_view)
        self._layout()
        self.m_redraw = QTimer(self, singleShot=True, interval=REDRAW_MS, timeout=self._redraw)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("common.no_game_open"))

    # building the window
    def _layout(self) -> None:
        middle = QWidget()
        lay = QVBoxLayout(middle)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.m_view, 1)
        lay.addWidget(self.m_play)
        self.setCentralWidget(middle)
        fields = QWidget()
        box = QVBoxLayout(fields)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.m_form, 1)
        tools = QHBoxLayout()
        self.m_tint_button = QPushButton(
            tr("particles.tint"), toolTip=tr("particles.tip.every_ticks_colour_alpha_times")
        )
        self.m_fade_button = QPushButton(tr("particles.fade"), toolTip=tr("particles.tip.first_last_tick_particle_see"))
        for button in (self.m_tint_button, self.m_fade_button):
            tools.addWidget(button)
        box.addLayout(tools)
        self.m_docks = {}
        for key, name, widget, area in (
            ("Files", N_("common.files"), self.m_browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Emitters", N_("particles.emitters"), self.m_emitters, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Fields", N_("ui.fields"), fields, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Curve", N_("particles.curve"), self.m_curve, Qt.DockWidgetArea.RightDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"particles{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.m_docks[key] = dock
        self.resizeDocks([self.m_docks["Files"], self.m_docks["Fields"]], [320, 400], Qt.Orientation.Horizontal)

    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("common.menu.file"))
        games = centralmenu.mark(file.addMenu(tr("common.menu.open_game")), centralmenu.GAME)
        for key, name in gametree.GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        file.addAction(tr("objects.open_file_path"), self._ask_path, QKeySequence("Ctrl+O"))
        file.addAction(tr("particles.open_model"), self._ask_model, QKeySequence("Ctrl+M"))
        centralmenu.mark(
            file.addAction(tr("objects.save_project"), self.save, QKeySequence.StandardKey.Save), centralmenu.SAVE
        )
        file.addAction(tr("objects.show_viewer"), self._to_viewer)
        edit, self.m_undo_action, self.m_redo_action = widgets.edit_menu(self, self.undo, self.redo)
        edit.addSeparator()
        self.m_add_like_action = edit.addAction(
            tr("particles.add_emitter_like_selected_one"), lambda: self.add_emitter()
        )
        self.m_add_kind_menu = edit.addMenu(tr("particles.menu.add_emitter_kind_file"))
        self.m_add_kind_menu.aboutToShow.connect(self._fill_kinds)
        self.m_add_other_menu = edit.addMenu(tr("particles.menu.add_emitter_kind_file_lacks"))
        self.m_add_other_menu.aboutToShow.connect(self._fill_other_kinds)
        self.m_remove_action = edit.addAction(tr("particles.remove_selected_emitter"), lambda: self.remove_emitter())
        view = self.menuBar().addMenu(tr("common.menu.view"))
        self.m_with_model = toggle_action(view, tr("particles.show_model"), True, lambda _is_on: self._redraw())
        toggle_action(view, tr("particles.ground"), True, lambda is_on: self._set_state(ground=is_on))
        toggle_action(view, tr("common.shadows"), True, lambda is_on: self._set_state(shadows=is_on))
        view.addAction(tr("particles.frame_effect"), lambda: self.m_view.frame_model(), QKeySequence("F"))
        self.m_rendering = CRenderingMenu(self, view.addMenu(tr("common.menu.rendering")))
        windows = self.menuBar().addMenu(tr("common.menu.window"))
        widgets.add_docks(windows, self.m_docks.values())

    def _wire(self) -> None:
        widgets.find_key(self, self.m_browser.m_search)
        self.m_browser.opened.connect(self.open_path)
        self.m_emitters.currentItemChanged.connect(lambda _now, _before: self._picked())
        self.m_form.edited.connect(self._row_edited)
        self.m_form.open_file.connect(self._open_named)
        self.m_curve.points_edited.connect(lambda pts: self._curve_edit(lambda curve: curve.set_points(pts)))
        self.m_curve.stops_edited.connect(lambda stops: self._curve_edit(lambda curve: curve.set_stops(stops)))
        self.m_tint_button.clicked.connect(self.tint)
        self.m_fade_button.clicked.connect(lambda: self._apply(rdptools.fade(self.m_effect.m_doc)))
        self.m_view.status.connect(lambda curve: self.statusBar().showMessage(curve, 8000))
        self.m_view.frameSwapped.connect(self.m_play.follow)

    # what the viewer's Rendering menu asks of its window
    def set_state_quietly(self, **change) -> None:
        self._set_state(**change)

    def _set_state(self, **change) -> None:
        for key, value in change.items():
            setattr(self.m_view.m_state, key, value)
        self.m_view.update()

    def set_renderer(self, name: str, remember: bool = True) -> None:
        if remember:
            self.m_settings.renderer = name
        self.statusBar().showMessage(tr("particles.graphics_api_changes_next_start"), 8000)

    # games and files
    def open_game(self, key: str, then: str = "", model: str = "", is_waiting: bool = False) -> None:
        """Open an installed game in the background; ``then``: an effect to open in it (from the config ``model``)."""
        if not gamesync.may_switch(self, key, self.confirm_discard):  # asks; the suite's other apps follow
            return

        def opened(tree) -> None:
            if isinstance(tree, Exception):
                self.statusBar().showMessage(
                    tr("common.game_not_open_error", game=gametree.GAMES.get(key, key), error=tree)
                )
                return
            self.close_effect()
            self.set_tree(appfolders.laid(tree, project.current(self.m_settings), projectpart.EDITS))
            self.m_settings.apps.setdefault(APP, {})["game"] = key
            if then:
                self.open_path(then, model)

        gameopen.open_installed(
            self, self.m_settings, key, self.m_background, then=opened, is_waiting=is_waiting or self.m_is_synchronous
        )

    def close_effect(self) -> None:
        """Let the open effect go (its game was left; asked about before): the tree, form and view are emptied."""
        self._m_opening += 1  # an effect still being read belongs to the game before
        self.m_effect, self.m_files, self.m_scene, self.m_model_scene = None, None, None, None
        self.m_emitters.clear()
        self.m_items = {}
        self._picked()
        self.m_view.clear()
        self.setWindowTitle(tr(TITLE))

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``sources.gametree.CGameTree``) and edit its effects."""
        self.m_tree = tree
        self.m_browser.set_tree(tree)
        index = self.m_browser.m_kind.findData("Effects")
        if index >= 0:
            self.m_browser.m_kind.setCurrentIndex(index)
        self.m_rendering.apply()
        count = sum(1 for path in tree.m_paths if path.endswith(EFFECT_ENDINGS))
        self.statusBar().showMessage(
            tr("particles.effect_files_double_click_one", name=tree.m_name, count=number(count))
        )

    def open_effect(self, game: str, path: str, model: str = "") -> None:
        """Edit an effect of a game (placed by the config ``model``, or ""), opening that game first when another
        one is open."""
        if self.m_tree is not None and self.m_tree.m_game == game:
            self.open_path(path, model)
        else:
            self.open_game(game, path, model)

    def open_path(self, path: str, model: str = "") -> bool:
        """Open an effect file of the open game (in the background); False when it cannot be one."""
        if self.m_tree is None:
            return False
        if not path.lower().endswith(EFFECT_ENDINGS):
            self.statusBar().showMessage(tr("particles.no_effect_file_rdp_efc", path=path), 8000)
            return False
        if not self.confirm_discard():
            return False
        tree, game = self.m_tree, self.m_tree.m_game
        saved = appfolders.edited(project.current(self.m_settings), projectpart.EDITS, game)  # not the imported base
        self._m_opening += 1
        asked = self._m_opening

        def work():
            effect = CEffectDoc.open(
                tree, game, path, model, lambda held_path: saved[held_path].read_bytes() if held_path in saved else None
            )
            scene = modelscene.build(effect.m_files, model, game) if model else None
            return effect, scene

        def done(result) -> None:
            if asked != self._m_opening:
                return  # another effect was asked for meanwhile: that one is shown
            if isinstance(result, Exception):
                self.statusBar().showMessage(
                    tr("particles.path_not_open_kind_error", path=path, kind=type(result).__name__, error=result),
                    12000,
                )
                return
            self.set_effect(*result)

        self.statusBar().showMessage(tr("particles.opening_path", path=path))
        self.m_background.run(work, done, is_waiting=self.m_is_synchronous)
        return True

    def set_effect(self, effect: CEffectDoc, model_scene: CScene | None = None) -> None:
        self.m_effect, self.m_model_scene, self.m_files = effect, model_scene, CModelFiles(effect.m_files)
        self._m_model_drawn = effect.m_model.doc.to_bytes() if effect.m_model is not None else None
        effect.m_listeners.append(self._changed)
        effect.m_undo.m_listeners.append(self._title)
        self.m_settings.apps.setdefault(APP, {})["file"] = effect.m_path
        self._fill_tree()
        self.m_play.set_length(effect.seconds())
        self._redraw(frame=True)
        self._title()
        said = list(effect.m_notes)
        if not effect.m_exact:
            said.append(tr("particles.read_only_file_not_back"))
        self.statusBar().showMessage("; ".join(said) or tr("particles.edit_field_curve", path=effect.m_path), 12000)

    def confirm_discard(self) -> bool:
        if self.m_effect is None or not self.m_effect.m_undo.dirty:
            return True
        return widgets.ask_keep(
            self, tr(TITLE), tr("objects.unsaved_edits_save", title=self.m_effect.title), self._save_kept
        )

    def _save_kept(self) -> bool:
        """The save dialog; whether the open effect's edits are kept afterwards."""
        self.save()
        return not self.m_effect.m_undo.dirty

    def _ask_path(self) -> None:
        if self.m_tree is None:
            return
        path, is_accepted = QInputDialog.getText(self, tr(TITLE), tr("particles.game_path_data_name_rdp"))
        if is_accepted and path.strip():
            self.open_path(path.strip())

    def _ask_model(self) -> None:
        """A model config, then one of the effects it names (the config is read in the background)."""
        if self.m_tree is None:
            return
        cfg, is_accepted = QInputDialog.getText(self, tr(TITLE), tr("particles.model_config_game_data_name"))
        if is_accepted and cfg.strip():
            tree, cfg = self.m_tree, normalize(cfg.strip())
            self.m_background.run(lambda: effects_of_model(tree, tree.m_game, cfg), lambda found: self._pick_effect(cfg, found),
                                is_waiting=self.m_is_synchronous)  # fmt: skip

    def _pick_effect(self, cfg: str, found) -> None:
        if isinstance(found, Exception) or not found:
            self.statusBar().showMessage(tr("particles.places_no_effect_file_game", path=cfg), 8000)
            return
        pick = found[0] if len(found) == 1 else QInputDialog.getItem(
            self, tr(TITLE), tr("particles.which_effect"), found, 0, False
        )[0]  # fmt: skip
        if pick:
            self.open_path(pick, cfg)

    def _open_named(self, name: str) -> None:
        if self.m_tree is not None and not links.view_file(self.m_tree.m_game, normalize(name)):
            self.statusBar().showMessage(tr("objects.viewer_shows_file_suites_window"), 8000)

    def _to_viewer(self) -> None:
        if self.m_effect is not None and not links.view_file(self.m_effect.m_game, self.m_effect.m_path):
            self.statusBar().showMessage(tr("objects.viewer_suites_window"), 8000)

    # the tree and the forms
    def _fill_tree(self) -> None:
        current = self.m_emitters.currentItem()
        keep = self._m_wanted or (current.data(0, KEY) if current is not None else None)
        self._m_wanted = None
        self.m_emitters.blockSignals(True)
        self.m_emitters.clear()
        self.m_items = {}
        select = None
        for item in outline.outline(self.m_effect):
            node = self._tree_item(item)
            self.m_emitters.addTopLevelItem(node)
            select = select or node
        self.m_emitters.expandToDepth(0)
        for k in range(self.m_emitters.topLevelItemCount()):
            for found in _walk(self.m_emitters.topLevelItem(k)):
                if found.data(0, KEY) == keep:
                    select = found
        self.m_emitters.blockSignals(False)
        if select is not None:
            self.m_emitters.setCurrentItem(select)
        self._picked()

    def _tree_item(self, item: outline.SItem) -> QTreeWidgetItem:
        node = QTreeWidgetItem([item.title])
        node.setData(0, KEY, item.key)
        if item.note:
            node.setToolTip(0, item.note)  # said in the interface language where it is made
        self.m_items[item.key] = item
        for child in item.children:
            node.addChild(self._tree_item(child))
        return node

    def current_item(self) -> outline.SItem | None:
        node = self.m_emitters.currentItem()
        return self.m_items.get(node.data(0, KEY)) if node is not None else None

    def _picked(self) -> None:
        item = self.current_item()
        read_only = self.m_effect is None or not self.m_effect.editable
        self.m_form.show_rows(item.rows if item is not None else [], read_only)
        self.m_curve.set_curve(item.curve if item is not None else None, read_only)
        is_rdp = self.m_effect is not None and self.m_effect.m_syntax == RDP and self.m_effect.m_doc is not None
        self.m_tint_button.setVisible(is_rdp)
        self.m_fade_button.setVisible(is_rdp)
        self.m_tint_button.setEnabled(is_rdp and not read_only)
        self.m_fade_button.setEnabled(is_rdp and not read_only)
        self.m_add_like_action.setEnabled(not read_only and bool(emitters.count(self.m_effect) if self.m_effect else 0))
        for widget in (self.m_add_kind_menu, self.m_add_other_menu, self.m_remove_action):
            widget.setEnabled(self.m_add_like_action.isEnabled())

    # edits
    def _apply(self, *edits) -> None:
        if self.m_effect is None or not self.m_effect.editable:
            self.statusBar().showMessage(tr("objects.file_read_only_not_back"), 8000)
            return
        if not any(effect is not None for effect in edits):
            self.statusBar().showMessage(tr("particles.nothing_to_change"), 5000)
            return
        self.m_effect.apply(*edits)

    def _row_edited(self, row: outline.SRow, text: str) -> None:
        found = outline.set_row(self.m_effect, row, text)
        if isinstance(found, str):
            self.statusBar().showMessage(f"{row.name}: {found}", 8000)
            self._picked()
            return
        self._apply(found)

    def _curve_edit(self, make) -> None:
        item = self.current_item()
        if item is not None and item.curve is not None:
            self._apply(make(item.curve))

    def add_emitter(self, like: int | None = None) -> None:
        """Append a copy of emitter ``like`` (default: the selected one, else the first) to the effect and select it."""
        if self.m_effect is None:
            return
        if like is None:
            item = self.current_item()
            like = emitters.emitter_index(item.key) if item is not None else None
            like = 0 if like is None else like
        found = emitters.add_like(self.m_effect, like)
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._m_wanted = f"emitter/{emitters.count(self.m_effect)}"
        self._apply(found)

    def _fill_kinds(self) -> None:
        self.m_add_kind_menu.clear()
        for name, k in emitters.kinds(self.m_effect) if self.m_effect is not None else []:
            self.m_add_kind_menu.addAction(name, lambda k=k: self.add_emitter(k))

    def remove_emitter(self, k: int | None = None) -> None:
        """Remove emitter ``k`` (default: the selected one) and select the one before it."""
        if self.m_effect is None:
            return
        if k is None:
            item = self.current_item()
            k = emitters.emitter_index(item.key) if item is not None else None
        found = emitters.remove(self.m_effect, -1 if k is None else k)
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._m_wanted = f"emitter/{max(k - 1, 0)}"
        self._apply(found)

    def game_kinds(self) -> list | None:
        """The first emitter of each kind in the open game's effect files, None while they are read (in the
        background, once per game)."""
        tree = self.m_effect.m_files if self.m_effect is not None else self.m_tree  # the effect's game, not the browsed
        if tree is None:
            return []
        if tree.m_game not in self._m_donors:
            self._m_donors[tree.m_game] = None
            paths = list(tree.m_paths)
            self.m_background.run(
                lambda: emitters.game_kinds(tree, tree.m_game, paths),
                lambda found: self._m_donors.__setitem__(tree.m_game, [] if isinstance(found, Exception) else found),
                is_waiting=self.m_is_synchronous,
            )
        return self._m_donors.get(tree.m_game)

    def _fill_other_kinds(self) -> None:
        self.m_add_other_menu.clear()
        donors = self.game_kinds() if self.m_effect is not None else []
        if donors is None:
            self.m_add_other_menu.addAction(tr("particles.reading_games_effects_open_menu")).setEnabled(False)
            return
        for donor in emitters.lacking(self.m_effect, donors):
            self.m_add_other_menu.addAction(
                f"{donor.kind}  ({donor.path})", lambda donor_kind=donor: self.add_donor(donor_kind)
            )
        if self.m_add_other_menu.isEmpty():
            self.m_add_other_menu.addAction(tr("particles.game_no_other_kind")).setEnabled(False)

    def add_donor(self, donor: emitters.SDonor) -> None:
        """Append a copy of the first emitter of a kind the file lacks, from the game file that has it."""
        found = emitters.add_donor(self.m_effect, donor) if self.m_effect is not None else ""
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._m_wanted = f"emitter/{emitters.count(self.m_effect)}"
        self._apply(found)

    def tint(self, rgba: tuple[float, float, float, float] | None = None) -> None:
        """Every tick's colour times a picked colour (``rgba`` 0..1, else the colour dialog asks)."""
        if self.m_effect is None or self.m_effect.m_syntax != RDP or self.m_effect.m_doc is None:
            return
        if rgba is None:
            picked = widgets.pick_qcolor(self, QColor("white"), alpha=True)
            if picked is None:
                return
            rgba = (picked.redF(), picked.greenF(), picked.blueF(), picked.alphaF())
        self._apply(rdptools.tint(self.m_effect.m_doc, rgba))

    def undo(self) -> None:
        if self.m_effect is not None and (label := self.m_effect.m_undo.undo()):
            self.statusBar().showMessage(tr("common.undone_label", label=label), 5000)

    def redo(self) -> None:
        if self.m_effect is not None and (label := self.m_effect.m_undo.redo()):
            self.statusBar().showMessage(tr("common.redone_label", label=label), 5000)

    def _changed(self) -> None:
        self._fill_tree()
        self.m_play.set_length(self.m_effect.seconds())
        model = self.m_effect.m_model
        drawn = model.doc.to_bytes() if model is not None else None
        if self.m_model_scene is not None and drawn != self._m_model_drawn:
            self._m_model_drawn = drawn
            self._rebuild_model_scene()
        self.m_redraw.start()
        self._title()

    def _title(self) -> None:
        if self.m_effect is None:
            return
        mark = " *" if self.m_effect.m_undo.dirty else ""
        self.setWindowTitle(tr("common.title.name_title", name=self.m_effect.title + mark, title=tr(TITLE)))
        self.m_undo_action.setEnabled(self.m_effect.m_undo.m_index > 0)
        self.m_redo_action.setEnabled(self.m_effect.m_undo.m_index < len(self.m_effect.m_undo.m_commands))

    # the preview
    def _rebuild_model_scene(self) -> None:
        effect = self.m_effect

        def done(scene) -> None:
            if effect is self.m_effect and isinstance(scene, CScene):
                self.m_model_scene = scene
                self.m_redraw.start()

        self.m_background.run(lambda: modelscene.build(effect.m_files, effect.m_model.asked, effect.m_game), done,
                            is_waiting=self.m_is_synchronous)  # fmt: skip

    def preview_scene(self) -> CScene:
        """The scene the view draws: the effect alone, or its model with the effect's file under its preview name."""
        effect = self.m_effect
        shown = effect.preview_path()
        if self.m_model_scene is not None and self.m_with_model.isChecked():
            effects = [replace(scene_effect, file=shown) if normalize(scene_effect.file) == effect.m_path else scene_effect
                       for scene_effect in self.m_model_scene.m_effects]  # fmt: skip
            return replace(self.m_model_scene, m_effects=effects)
        return CScene(shown, effect.m_game, shown, m_effects=[SPlacedEffect("", shown, np.identity(4))])

    def _redraw(self, frame: bool = False) -> None:
        if self.m_effect is None or self.m_files is None:
            return
        self.m_scene = self.preview_scene()
        self.m_view.show_scene(self.m_scene, self.m_files, frame=frame)

    # saving
    def save(self) -> None:
        """The save dialog; the chosen way's files go into the open project (``particleedit.projectpart``)."""
        if self.m_effect is None:
            return
        dialog = CSaveDialog(self.m_effect, self)
        accepted = dialog.exec()
        dialog.deleteLater()  # a child of the window otherwise stays until the window closes
        if accepted and dialog.m_result_plan is not None:
            self.write(dialog.m_result_plan)

    def write(self, plan) -> None:
        if plan.problems:
            self.statusBar().showMessage("; ".join(plan.problems), 12000)
            return
        current = project.current(self.m_settings)
        try:
            notice = projectpart.EDITS.save_announced(current, self.m_effect.m_game, plan.files, plan.stale)
        except OSError as exc:  # the project's folder takes no file: the edits stay unsaved
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return
        self.m_effect.m_undo.mark_clean()
        self.statusBar().showMessage(
            tr("particles.saved_count_files_folder_text", count=len(plan.files), folder=current.folder.name,
               text=notice.text), 15000)  # fmt: skip

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about unsaved edits, nothing released."""
        return self.confirm_discard()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        self.m_view.release()
        super().closeEvent(event)


def _walk(node: QTreeWidgetItem):
    yield node
    for k in range(node.childCount()):
        yield from _walk(node.child(k))


def effects_of_model(files, game: str, cfg: str) -> list[str]:
    """The effect files a model config names (as the game reads it) that the game has, in file order."""
    read = config_read_path(files, game, cfg)
    if not files.exists(read):
        return []
    doc = SDoc.read(files.read(read), read)
    found = []
    for node in doc.root.iter():
        name = normalize(node.text) if not node.children and node.text else ""
        if name.endswith(EFFECT_ENDINGS) and name not in found and files.exists(name):
            found.append(name)
    return found
