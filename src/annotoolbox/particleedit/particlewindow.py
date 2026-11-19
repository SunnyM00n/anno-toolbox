"""The particle editor's window: the game's effect files and the emitter tree on the left, the viewer's 3D view with the
play bar in the middle, the selected row's fields on the right and its curve below.

``ParticleWindow(settings, settings_path)`` is the entry (the suite's ``Particles`` app);
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
from ..models.cfgdoc import Doc
from ..render import rhiview
from ..sources import gametree
from ..sources.overlay import normalize
from ..suite import appfolders, gameopen, gamesync, links, project, widgets
from ..suite.background import Background
from ..viewer import modelscene
from ..viewer.browser import FileBrowser
from ..viewer.modelfiles import ModelFiles
from ..viewer.modelscene import PlacedEffect, Scene
from ..viewer.modelview import make_view
from ..viewer.renderingmenu import RenderingMenu, toggle_action
from . import emitters, outline, projectpart, rdptools
from .curveview import CurveView
from .effectdoc import EFFECT_ENDINGS, RDP, EffectDoc, config_read_path
from .fieldform import FieldForm
from .playbar import PlayBar
from .savedialog import SaveDialog

APP = "particles"  # the key of its remembered state in the settings
TITLE = N_("islands.particles")
REDRAW_MS = 150  # after the last edit, the preview is drawn again
KEY = Qt.ItemDataRole.UserRole + 1


class ParticleWindow(QMainWindow):
    """``hosts_gl``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL; ``sync``:
    the work that runs off the window's thread runs at once (tests)."""

    def __init__(self, settings, settings_path: Path | None = None, hosts_gl: bool = False, sync: bool = False) -> None:
        super().__init__()
        self.settings, self.settings_path, self.hosts_gl, self.sync = settings, settings_path, hosts_gl, sync
        self.tree = None
        self._donors: dict[str, list | None] = {}  # game -> the first emitter of each kind (``game_kinds``)
        self.background = Background(self)
        self.effect: EffectDoc | None = None
        self._opening = 0  # counts the effects asked for: only the last one's result is shown
        self.files: ModelFiles | None = None
        self.scene: Scene | None = None
        self.model_scene: Scene | None = None
        self._model_drawn: bytes | None = None  # the model config's bytes the model scene was built from
        self.items: dict[str, outline.Item] = {}
        self._wanted: str | None = None  # the tree row to select when the tree is next filled (a new emitter)
        self.browser, self.form, self.curve = FileBrowser(), FieldForm(), CurveView()
        self.emitters = QTreeWidget(headerHidden=True)
        self.view = make_view(rhiview.composable(rhiview.chosen(settings.renderer), hosts_gl))
        self.model = self.view  # what the viewer's Rendering menu drives
        self.play = PlayBar(self.view)
        self._layout()
        self.redraw = QTimer(self, singleShot=True, interval=REDRAW_MS, timeout=self._redraw)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("common.no_game_open"))

    # building the window
    def _layout(self) -> None:
        middle = QWidget()
        lay = QVBoxLayout(middle)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.play)
        self.setCentralWidget(middle)
        fields = QWidget()
        box = QVBoxLayout(fields)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.form, 1)
        tools = QHBoxLayout()
        self.tint_button = QPushButton(tr("particles.tint"), toolTip=tr("particles.tip.every_ticks_colour_alpha_times"))
        self.fade_button = QPushButton(tr("particles.fade"), toolTip=tr("particles.tip.first_last_tick_particle_see"))
        for b in (self.tint_button, self.fade_button):
            tools.addWidget(b)
        box.addLayout(tools)
        self.docks = {}
        for key, name, widget, area in (
            ("Files", N_("common.files"), self.browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Emitters", N_("particles.emitters"), self.emitters, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Fields", N_("ui.fields"), fields, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Curve", N_("particles.curve"), self.curve, Qt.DockWidgetArea.RightDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"particles{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[key] = dock
        self.resizeDocks([self.docks["Files"], self.docks["Fields"]], [320, 400], Qt.Orientation.Horizontal)

    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("common.menu.file"))
        games = file.addMenu(tr("common.menu.open_game"))
        for key, name in gametree.GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        file.addAction(tr("objects.open_file_path"), self._ask_path, QKeySequence("Ctrl+O"))
        file.addAction(tr("particles.open_model"), self._ask_model, QKeySequence("Ctrl+M"))
        file.addAction(tr("objects.save_project"), self.save, QKeySequence.StandardKey.Save)
        file.addAction(tr("objects.show_viewer"), self._to_viewer)
        edit, self.undo_action, self.redo_action = widgets.edit_menu(self, self.undo, self.redo)
        edit.addSeparator()
        self.add_like_action = edit.addAction(tr("particles.add_emitter_like_selected_one"), lambda: self.add_emitter())
        self.add_kind_menu = edit.addMenu(tr("particles.menu.add_emitter_kind_file"))
        self.add_kind_menu.aboutToShow.connect(self._fill_kinds)
        self.add_other_menu = edit.addMenu(tr("particles.menu.add_emitter_kind_file_lacks"))
        self.add_other_menu.aboutToShow.connect(self._fill_other_kinds)
        self.remove_action = edit.addAction(tr("particles.remove_selected_emitter"), lambda: self.remove_emitter())
        view = self.menuBar().addMenu(tr("common.menu.view"))
        self.with_model = toggle_action(view, tr("particles.show_model"), True, lambda _on: self._redraw())
        toggle_action(view, tr("particles.ground"), True, lambda on: self._set_state(ground=on))
        toggle_action(view, tr("common.shadows"), True, lambda on: self._set_state(shadows=on))
        view.addAction(tr("particles.frame_effect"), lambda: self.view.frame_model(), QKeySequence("F"))
        self.rendering = RenderingMenu(self, view.addMenu(tr("common.menu.rendering")))
        windows = self.menuBar().addMenu(tr("common.menu.window"))
        widgets.add_docks(windows, self.docks.values())

    def _wire(self) -> None:
        widgets.find_key(self, self.browser.search)
        self.browser.opened.connect(self.open_path)
        self.emitters.currentItemChanged.connect(lambda _now, _before: self._picked())
        self.form.edited.connect(self._row_edited)
        self.form.open_file.connect(self._open_named)
        self.curve.points_edited.connect(lambda pts: self._curve_edit(lambda m: m.set_points(pts)))
        self.curve.stops_edited.connect(lambda stops: self._curve_edit(lambda m: m.set_stops(stops)))
        self.tint_button.clicked.connect(self.tint)
        self.fade_button.clicked.connect(lambda: self._apply(rdptools.fade(self.effect.doc)))
        self.view.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.view.frameSwapped.connect(self.play.follow)

    # what the viewer's Rendering menu asks of its window
    def set_state_quietly(self, **change) -> None:
        self._set_state(**change)

    def _set_state(self, **change) -> None:
        for key, value in change.items():
            setattr(self.view.state, key, value)
        self.view.update()

    def set_renderer(self, name: str, remember: bool = True) -> None:
        if remember:
            self.settings.renderer = name
        self.statusBar().showMessage(tr("particles.graphics_api_changes_next_start"), 8000)

    # games and files
    def open_game(self, key: str, then: str = "", model: str = "", wait: bool = False) -> None:
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
            self.set_tree(appfolders.laid(tree, project.current(self.settings), projectpart.EDITS))
            self.settings.apps.setdefault(APP, {})["game"] = key
            if then:
                self.open_path(then, model)

        gameopen.open_installed(self, self.settings, key, self.background, then=opened, wait=wait or self.sync)

    def close_effect(self) -> None:
        """Let the open effect go (its game was left; asked about before): the tree, form and view are emptied."""
        self._opening += 1  # an effect still being read belongs to the game before
        self.effect, self.files, self.scene, self.model_scene = None, None, None, None
        self.emitters.clear()
        self.items = {}
        self._picked()
        self.view.clear()
        self.setWindowTitle(tr(TITLE))

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``sources.gametree.GameTree``) and edit its effects."""
        self.tree = tree
        self.browser.set_tree(tree)
        at = self.browser.kind.findData("Effects")
        if at >= 0:
            self.browser.kind.setCurrentIndex(at)
        self.rendering.apply()
        count = sum(1 for p in tree.paths if p.endswith(EFFECT_ENDINGS))
        self.statusBar().showMessage(tr("particles.effect_files_double_click_one", name=tree.name, count=number(count)))

    def open_effect(self, game: str, path: str, model: str = "") -> None:
        """Edit an effect of a game (placed by the config ``model``, or ""), opening that game first when another
        one is open."""
        if self.tree is not None and self.tree.game == game:
            self.open_path(path, model)
        else:
            self.open_game(game, path, model)

    def open_path(self, path: str, model: str = "") -> bool:
        """Open an effect file of the open game (in the background); False when it cannot be one."""
        if self.tree is None:
            return False
        if not path.lower().endswith(EFFECT_ENDINGS):
            self.statusBar().showMessage(tr("particles.no_effect_file_rdp_efc", path=path), 8000)
            return False
        if not self.confirm_discard():
            return False
        tree, game = self.tree, self.tree.game
        saved = appfolders.edited(project.current(self.settings), projectpart.EDITS, game)  # not the imported base
        self._opening += 1
        asked = self._opening

        def work():
            effect = EffectDoc.open(tree, game, path, model, lambda p: saved[p].read_bytes() if p in saved else None)
            scene = modelscene.build(effect.files, model, game) if model else None
            return effect, scene

        def done(result) -> None:
            if asked != self._opening:
                return  # another effect was asked for meanwhile: that one is shown
            if isinstance(result, Exception):
                self.statusBar().showMessage(
                    tr("particles.path_not_open_kind_error", path=path, kind=type(result).__name__, error=result),
                    12000,
                )
                return
            self.set_effect(*result)

        self.statusBar().showMessage(tr("particles.opening_path", path=path))
        self.background.run(work, done, wait=self.sync)
        return True

    def set_effect(self, effect: EffectDoc, model_scene: Scene | None = None) -> None:
        self.effect, self.model_scene, self.files = effect, model_scene, ModelFiles(effect.files)
        self._model_drawn = effect.model.doc.to_bytes() if effect.model is not None else None
        effect.listeners.append(self._changed)
        effect.undo.listeners.append(self._title)
        self.settings.apps.setdefault(APP, {})["file"] = effect.path
        self._fill_tree()
        self.play.set_length(effect.seconds())
        self._redraw(frame=True)
        self._title()
        said = list(effect.notes)
        if not effect.exact:
            said.append(tr("particles.read_only_file_not_back"))
        self.statusBar().showMessage("; ".join(said) or tr("particles.edit_field_curve", path=effect.path), 12000)

    def confirm_discard(self) -> bool:
        if self.effect is None or not self.effect.undo.dirty:
            return True
        return widgets.ask_keep(
            self, tr(TITLE), tr("objects.unsaved_edits_save", title=self.effect.title), self._save_kept
        )

    def _save_kept(self) -> bool:
        """The save dialog; whether the open effect's edits are kept afterwards."""
        self.save()
        return not self.effect.undo.dirty

    def _ask_path(self) -> None:
        if self.tree is None:
            return
        path, ok = QInputDialog.getText(self, tr(TITLE), tr("particles.game_path_data_name_rdp"))
        if ok and path.strip():
            self.open_path(path.strip())

    def _ask_model(self) -> None:
        """A model config, then one of the effects it names (the config is read in the background)."""
        if self.tree is None:
            return
        cfg, ok = QInputDialog.getText(self, tr(TITLE), tr("particles.model_config_game_data_name"))
        if ok and cfg.strip():
            tree, cfg = self.tree, normalize(cfg.strip())
            self.background.run(lambda: effects_of_model(tree, tree.game, cfg), lambda found: self._pick_effect(cfg, found),
                                wait=self.sync)  # fmt: skip

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
        if self.tree is not None and not links.view_file(self.tree.game, normalize(name)):
            self.statusBar().showMessage(tr("objects.viewer_shows_file_suites_window"), 8000)

    def _to_viewer(self) -> None:
        if self.effect is not None and not links.view_file(self.effect.game, self.effect.path):
            self.statusBar().showMessage(tr("objects.viewer_suites_window"), 8000)

    # the tree and the forms
    def _fill_tree(self) -> None:
        current = self.emitters.currentItem()
        keep = self._wanted or (current.data(0, KEY) if current is not None else None)
        self._wanted = None
        self.emitters.blockSignals(True)
        self.emitters.clear()
        self.items = {}
        select = None
        for item in outline.outline(self.effect):
            node = self._tree_item(item)
            self.emitters.addTopLevelItem(node)
            select = select or node
        self.emitters.expandToDepth(0)
        for k in range(self.emitters.topLevelItemCount()):
            for found in _walk(self.emitters.topLevelItem(k)):
                if found.data(0, KEY) == keep:
                    select = found
        self.emitters.blockSignals(False)
        if select is not None:
            self.emitters.setCurrentItem(select)
        self._picked()

    def _tree_item(self, item: outline.Item) -> QTreeWidgetItem:
        node = QTreeWidgetItem([item.title])
        node.setData(0, KEY, item.key)
        if item.note:
            node.setToolTip(0, item.note)  # said in the interface language where it is made
        self.items[item.key] = item
        for child in item.children:
            node.addChild(self._tree_item(child))
        return node

    def current_item(self) -> outline.Item | None:
        node = self.emitters.currentItem()
        return self.items.get(node.data(0, KEY)) if node is not None else None

    def _picked(self) -> None:
        item = self.current_item()
        read_only = self.effect is None or not self.effect.editable
        self.form.show_rows(item.rows if item is not None else [], read_only)
        self.curve.set_curve(item.curve if item is not None else None, read_only)
        is_rdp = self.effect is not None and self.effect.syntax == RDP and self.effect.doc is not None
        self.tint_button.setVisible(is_rdp)
        self.fade_button.setVisible(is_rdp)
        self.tint_button.setEnabled(is_rdp and not read_only)
        self.fade_button.setEnabled(is_rdp and not read_only)
        self.add_like_action.setEnabled(not read_only and bool(emitters.count(self.effect) if self.effect else 0))
        for widget in (self.add_kind_menu, self.add_other_menu, self.remove_action):
            widget.setEnabled(self.add_like_action.isEnabled())

    # edits
    def _apply(self, *edits) -> None:
        if self.effect is None or not self.effect.editable:
            self.statusBar().showMessage(tr("objects.file_read_only_not_back"), 8000)
            return
        if not any(e is not None for e in edits):
            self.statusBar().showMessage(tr("particles.nothing_to_change"), 5000)
            return
        self.effect.apply(*edits)

    def _row_edited(self, row: outline.Row, text: str) -> None:
        found = outline.set_row(self.effect, row, text)
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
        if self.effect is None:
            return
        if like is None:
            item = self.current_item()
            like = emitters.emitter_index(item.key) if item is not None else None
            like = 0 if like is None else like
        found = emitters.add_like(self.effect, like)
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._wanted = f"emitter/{emitters.count(self.effect)}"
        self._apply(found)

    def _fill_kinds(self) -> None:
        self.add_kind_menu.clear()
        for name, k in emitters.kinds(self.effect) if self.effect is not None else []:
            self.add_kind_menu.addAction(name, lambda k=k: self.add_emitter(k))

    def remove_emitter(self, k: int | None = None) -> None:
        """Remove emitter ``k`` (default: the selected one) and select the one before it."""
        if self.effect is None:
            return
        if k is None:
            item = self.current_item()
            k = emitters.emitter_index(item.key) if item is not None else None
        found = emitters.remove(self.effect, -1 if k is None else k)
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._wanted = f"emitter/{max(k - 1, 0)}"
        self._apply(found)

    def game_kinds(self) -> list | None:
        """The first emitter of each kind in the open game's effect files, None while they are read (in the
        background, once per game)."""
        tree = self.effect.files if self.effect is not None else self.tree  # the effect's game, not the browsed
        if tree is None:
            return []
        if tree.game not in self._donors:
            self._donors[tree.game] = None
            paths = list(tree.paths)
            self.background.run(
                lambda: emitters.game_kinds(tree, tree.game, paths),
                lambda found: self._donors.__setitem__(tree.game, [] if isinstance(found, Exception) else found),
                wait=self.sync,
            )
        return self._donors.get(tree.game)

    def _fill_other_kinds(self) -> None:
        self.add_other_menu.clear()
        donors = self.game_kinds() if self.effect is not None else []
        if donors is None:
            self.add_other_menu.addAction(tr("particles.reading_games_effects_open_menu")).setEnabled(False)
            return
        for donor in emitters.lacking(self.effect, donors):
            self.add_other_menu.addAction(f"{donor.kind}  ({donor.path})", lambda d=donor: self.add_donor(d))
        if self.add_other_menu.isEmpty():
            self.add_other_menu.addAction(tr("particles.game_no_other_kind")).setEnabled(False)

    def add_donor(self, donor: emitters.Donor) -> None:
        """Append a copy of the first emitter of a kind the file lacks, from the game file that has it."""
        found = emitters.add_donor(self.effect, donor) if self.effect is not None else ""
        if isinstance(found, str):
            self.statusBar().showMessage(found, 8000)
            return
        self._wanted = f"emitter/{emitters.count(self.effect)}"
        self._apply(found)

    def tint(self, rgba: tuple[float, float, float, float] | None = None) -> None:
        """Every tick's colour times a picked colour (``rgba`` 0..1, else the colour dialog asks)."""
        if self.effect is None or self.effect.syntax != RDP or self.effect.doc is None:
            return
        if rgba is None:
            picked = widgets.pick_qcolor(self, QColor("white"), alpha=True)
            if picked is None:
                return
            rgba = (picked.redF(), picked.greenF(), picked.blueF(), picked.alphaF())
        self._apply(rdptools.tint(self.effect.doc, rgba))

    def undo(self) -> None:
        if self.effect is not None and (label := self.effect.undo.undo()):
            self.statusBar().showMessage(tr("common.undone_label", label=label), 5000)

    def redo(self) -> None:
        if self.effect is not None and (label := self.effect.undo.redo()):
            self.statusBar().showMessage(tr("common.redone_label", label=label), 5000)

    def _changed(self) -> None:
        self._fill_tree()
        self.play.set_length(self.effect.seconds())
        model = self.effect.model
        drawn = model.doc.to_bytes() if model is not None else None
        if self.model_scene is not None and drawn != self._model_drawn:
            self._model_drawn = drawn
            self._rebuild_model_scene()
        self.redraw.start()
        self._title()

    def _title(self) -> None:
        if self.effect is None:
            return
        mark = " *" if self.effect.undo.dirty else ""
        self.setWindowTitle(tr("common.title.name_title", name=self.effect.title + mark, title=tr(TITLE)))
        self.undo_action.setEnabled(self.effect.undo.index > 0)
        self.redo_action.setEnabled(self.effect.undo.index < len(self.effect.undo.commands))

    # the preview
    def _rebuild_model_scene(self) -> None:
        effect = self.effect

        def done(scene) -> None:
            if effect is self.effect and isinstance(scene, Scene):
                self.model_scene = scene
                self.redraw.start()

        self.background.run(lambda: modelscene.build(effect.files, effect.model.asked, effect.game), done,
                            wait=self.sync)  # fmt: skip

    def preview_scene(self) -> Scene:
        """The scene the view draws: the effect alone, or its model with the effect's file under its preview name."""
        effect = self.effect
        shown = effect.preview_path()
        if self.model_scene is not None and self.with_model.isChecked():
            effects = [replace(e, file=shown) if normalize(e.file) == effect.path else e
                       for e in self.model_scene.effects]  # fmt: skip
            return replace(self.model_scene, effects=effects)
        return Scene(shown, effect.game, shown, effects=[PlacedEffect("", shown, np.identity(4))])

    def _redraw(self, frame: bool = False) -> None:
        if self.effect is None or self.files is None:
            return
        self.scene = self.preview_scene()
        self.view.show_scene(self.scene, self.files, frame=frame)

    # saving
    def save(self) -> None:
        """The save dialog; the chosen way's files go into the open project (``particleedit.projectpart``)."""
        if self.effect is None:
            return
        dialog = SaveDialog(self.effect, self)
        accepted = dialog.exec()
        dialog.deleteLater()  # a child of the window otherwise stays until the window closes
        if accepted and dialog.result_plan is not None:
            self.write(dialog.result_plan)

    def write(self, plan) -> None:
        if plan.problems:
            self.statusBar().showMessage("; ".join(plan.problems), 12000)
            return
        current = project.current(self.settings)
        try:
            notice = projectpart.EDITS.save_announced(current, self.effect.game, plan.files, plan.stale)
        except OSError as exc:  # the project's folder takes no file: the edits stay unsaved
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return
        self.effect.undo.mark_clean()
        self.statusBar().showMessage(
            tr("particles.saved_count_files_folder_text", count=len(plan.files), folder=current.folder.name,
               text=notice.text), 15000)  # fmt: skip

    def closeEvent(self, event) -> None:
        if not self.confirm_discard():
            event.ignore()
            return
        self.view.release()
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
    doc = Doc.read(files.read(read), read)
    found = []
    for n in doc.root.iter():
        name = normalize(n.text) if not n.children and n.text else ""
        if name.endswith(EFFECT_ENDINGS) and name not in found and files.exists(name):
            found.append(name)
    return found
