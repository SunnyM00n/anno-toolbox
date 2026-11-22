"""The object builder's window, laid out as the studio's object builder: the game's files and the object tree on the
left, the 3D view in the middle (the viewer's, redrawn a moment after each edit), the properties on the right, the
sequence editor and the feedback timeline along the bottom; the info and feedback files drawn over the view.

``CObjectWindow(settings, settings_path)`` is the entry (the suite's ``Objects`` app, or ``run`` alone); ``open_file``
is what another app calls. The user's guide is docs/object-builder.md; the design docs/tool-suite.md section 23.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QDockWidget, QInputDialog, QMainWindow, QMessageBox

from ..infra.i18n import N_, number, tr
from ..models import cfgdoc, cfgparts, fileresolve
from ..models.cfgdoc import SDoc
from ..render import rhiview
from ..sources import gametree
from ..suite import appfolders, centralmenu, gameopen, gamesync, links, project, widgets
from ..suite.background import CBackground
from ..viewer.browser import CFileBrowser
from ..viewer.modelfiles import CModelFiles
from ..viewer.modelscene import CScene, SOptions
from ..viewer.modelview import make_view, save_picture
from ..viewer.renderingmenu import toggle_action
from . import projectpart, shapes, writeback
from .feedbackline import CFeedbackLine
from .figuremenu import CFigureMenu
from .handletool import CHandleTool, SHost
from .objdoc import CObjectDoc
from .objprops import CPropertyEditor
from .objtree import CObjectTree, SRow
from .overlay import COverlay
from .timeline import CSequenceEditor

APP = "objects"  # the key of its remembered state in the settings
TITLE = N_("objects.object_builder")
OBJECT_FILES = (".cfg", ".mcf", ".prp", ".ifo", ".fc")
REDRAW_MS = 150  # after the last edit, the 3D view is redrawn


class CObjectWindow(QMainWindow):
    """``is_gl_host``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL."""

    def __init__(self, settings, settings_path: Path | None = None, is_gl_host: bool = False) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_tree = None
        self.m_background = CBackground(self)
        self.m_object_doc: CObjectDoc | None = None
        self.m_files: CModelFiles | None = None
        self.m_scene: CScene | None = None
        self.m_browser, self.m_objects, self.m_props = CFileBrowser(), CObjectTree(), CPropertyEditor()
        self.m_sequences, self.m_feedback = CSequenceEditor(), CFeedbackLine()
        self.m_view = make_view(rhiview.composable(rhiview.chosen(settings.renderer), is_gl_host))
        self.m_view.m_state.gate_effects = False  # no sequence player here: every effect shows
        self.m_overlay = COverlay(self.m_view)
        self.m_tool = CHandleTool(self.m_view, self.m_overlay, SHost(
            lambda: self.m_object_doc, self._apply, self._preview, lambda text: self.statusBar().showMessage(text, 8000)
        ))  # fmt: skip
        self.setCentralWidget(self.m_view)
        self.m_docks = {}
        for key, name, widget, area in (
            ("Files", N_("common.files"), self.m_browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Object", N_("common.object"), self.m_objects, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Properties", N_("common.properties"), self.m_props, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Sequences", N_("common.sequences"), self.m_sequences, Qt.DockWidgetArea.BottomDockWidgetArea),
            ("Feedback loops", N_("objects.feedback_loops"), self.m_feedback, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"objects{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.m_docks[key] = dock
        self.resizeDocks([self.m_docks["Files"], self.m_docks["Properties"]], [340, 380], Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.m_docks["Sequences"], self.m_docks["Feedback loops"])
        self.m_docks["Sequences"].raise_()
        self.m_redraw = QTimer(self, singleShot=True, interval=REDRAW_MS, timeout=self._redraw)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("common.no_game_open"))

    # building the window
    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("common.menu.file"))
        games = centralmenu.mark(file.addMenu(tr("common.menu.open_game")), centralmenu.GAME)
        for key, name in gametree.GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        file.addAction(tr("objects.open_file_path"), self._ask_path, QKeySequence("Ctrl+O"))
        centralmenu.mark(
            file.addAction(tr("objects.save_project"), self.save, QKeySequence.StandardKey.Save), centralmenu.SAVE
        )
        file.addAction(tr("objects.show_viewer"), self._to_viewer)
        file.addAction(tr("common.save_picture_dots"), self._save_picture, QKeySequence("Ctrl+Shift+S"))
        edit, self.m_undo_action, self.m_redo_action = widgets.edit_menu(self, self.undo, self.redo)
        edit.addSeparator()
        edit.addAction(tr("objects.add_part"), self.add_part, QKeySequence("Ins"))
        edit.addAction(tr("objects.add_part_another_file"), self.add_from_file)
        edit.addAction(tr("objects.duplicate"), self.duplicate_part, QKeySequence("Ctrl+D"))
        edit.addAction(tr("objects.remove"), self.remove_part, QKeySequence.StandardKey.Delete)
        edit.addAction(tr("objects.move_up"), lambda: self.move_part(-1), QKeySequence("Alt+Up"))
        edit.addAction(tr("objects.move_down"), lambda: self.move_part(1), QKeySequence("Alt+Down"))
        edit.addSeparator()
        self._handle_menu(edit.addMenu(tr("objects.menu.handles")))
        self.m_footprint_action = edit.addAction(tr("objects.draw_footprint"))
        self.m_footprint_action.setCheckable(True)
        self.m_footprint_action.triggered.connect(self.m_tool.draw_footprint)
        self.m_tool.drawing_changed.connect(self.m_footprint_action.setChecked)
        view = self.menuBar().addMenu(tr("common.menu.view"))
        self.m_toggles: dict[str, QAction] = {}
        for key, text, is_checked in (("boxes", N_("objects.info_files_boxes"), True), ("areas", N_("objects.info_files_areas"), True),
                              ("dummies", N_("objects.feedbacks_dummies"), True), ("shadows", N_("common.shadows"), True),
                              ("ground", N_("common.ground"), True), ("particles", N_("common.effects_accel"), True),
                              ("cloth", N_("viewer.simulate_cloth"), False), ("torn", N_("viewer.torn_cloth_damaged"), False),
                              ("figures", N_("viewer.walking_figures"), False)):  # fmt: skip
            self.m_toggles[key] = toggle_action(
                view, tr(text), is_checked, lambda checked, k=key: self._toggle(k, checked)
            )
        self.m_figures = CFigureMenu(self, view.addMenu(tr("viewer.menu.figures_state")))
        self.m_shared_action = view.addAction(tr("common.read_baked_shared_copy_game"), self._reopen)
        self.m_shared_action.setCheckable(True)
        self.m_shared_action.setChecked(True)
        lods = view.addMenu(tr("common.menu.level_detail"))
        self.m_lod = 0
        for lod in range(5):
            lods.addAction(tr("common.lod_n", n=lod), lambda lod=lod: self._set_lod(lod))
        view.addAction(tr("common.frame_model"), lambda: self.m_view.frame_model(), QKeySequence("F"))
        windows = self.menuBar().addMenu(tr("common.menu.window"))
        widgets.add_docks(windows, self.m_docks.values())

    def _handle_menu(self, menu) -> None:
        """Off, Move (W), Rotate (E), Scale (R): the handles over the selected part; the keys work in the view."""
        group, self.m_handle_actions = QActionGroup(self), {}
        for mode, text, key in (("off", N_("objects.off"), ""), ("move", N_("objects.move"), "W"), ("rotate", N_("objects.rotate"), "E"),
                                ("scale", N_("objects.scale"), "R")):  # fmt: skip
            action = menu.addAction(tr(text))
            action.setCheckable(True)
            action.setChecked(mode == "off")
            action.triggered.connect(lambda _checked=False, bound_mode=mode: self.m_tool.set_mode(bound_mode))
            if key:
                action.setShortcut(QKeySequence(key))
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                self.m_view.addAction(action)
            group.addAction(action)
            self.m_handle_actions[mode] = action

    def _wire(self) -> None:
        widgets.find_key(self, self.m_browser.m_search)
        self.m_browser.opened.connect(self.open_path)
        self.m_objects.picked.connect(self._picked)
        self.m_props.edited.connect(lambda node, value: self._fields_edited([(node, value)]))
        self.m_props.edited_many.connect(self._fields_edited)
        self.m_props.refused.connect(lambda why: self.statusBar().showMessage(why, 8000))
        self.m_props.open_file.connect(self._open_named)
        self.m_sequences.edits.connect(lambda edits: self._apply("cfg", *edits))
        self.m_sequences.select.connect(lambda path: self.m_objects.select("cfg", path))
        self.m_sequences.changed.connect(self._seek)
        self.m_sequences.playing.connect(lambda is_playing: setattr(self.m_view, "m_playing", is_playing))
        self.m_sequences.m_speed.valueChanged.connect(lambda speed: setattr(self.m_view, "m_speed", speed))
        self.m_feedback.edits.connect(lambda edits: self._apply("fc", *edits))
        self.m_feedback.select.connect(lambda path: self.m_objects.select("fc", path))
        self.m_feedback.said.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.m_view.picked.connect(lambda node: node and self.m_objects.select("cfg", node))
        self.m_view.status.connect(lambda message: self.statusBar().showMessage(message, 8000))
        self.m_view.frameSwapped.connect(self._follow_clock)

    # games and files
    def open_game(self, key: str, then: str = "", is_waiting: bool = False) -> None:
        """Open an installed game in the background; ``then``: an object to open in it."""
        if not gamesync.may_switch(self, key, self.confirm_discard):  # asks; the suite's other apps follow
            return

        def opened(tree) -> None:
            if isinstance(tree, Exception):
                self.statusBar().showMessage(
                    tr("common.game_not_open_error", game=gametree.GAMES.get(key, key), error=tree)
                )
                return
            self.close_object()
            self.set_tree(appfolders.laid(tree, project.current(self.m_settings), projectpart.EDITS))
            self.m_settings.apps.setdefault(APP, {})["game"] = key
            if then:
                self.open_path(then)

        gameopen.open_installed(self, self.m_settings, key, self.m_background, then=opened, is_waiting=is_waiting)

    def close_object(self) -> None:
        """Let the open object go (its game was left; asked about before): the panels and the view are emptied."""
        self.m_object_doc, self.m_files, self.m_scene = None, None, None
        self.m_objects.clear()
        self.m_objects.m_items = {}
        self._picked(None)
        self.m_sequences.show_doc(None, None)
        self.m_feedback.show_doc(None)
        self.m_overlay.set_shapes([], None)
        self.m_view.clear()
        self.setWindowTitle(tr(TITLE))

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``sources.gametree.CGameTree``) and edit its objects."""
        self.m_tree = tree
        self.m_browser.set_tree(tree)
        self.m_props.m_game, self.m_props.m_files = tree.m_game, lambda: tree.m_paths
        self.m_props.m_source_names = tree.m_game in fileresolve.NEW_ENGINE
        self.statusBar().showMessage(
            tr("objects.files_double_click_model_config", name=tree.m_name, count=number(len(tree.m_paths)))
        )

    def open_file(self, game: str, path: str) -> None:
        """Edit an object of a game, opening that game first when another one is open."""
        if self.m_tree is not None and self.m_tree.m_game == game:
            self.open_path(path)
        else:
            self.open_game(game, path)

    def open_path(self, path: str) -> bool:
        """Open the object a file belongs to (its config, info and feedback files)."""
        if self.m_tree is None:
            return False
        if not path.lower().endswith(OBJECT_FILES):
            self.statusBar().showMessage(tr("objects.no_model_config_info_feedback", path=path), 8000)
            return False
        if not self.confirm_discard():
            return False
        saved = appfolders.edited(
            project.current(self.m_settings), projectpart.EDITS, self.m_tree.m_game
        )  # not the imported base
        try:
            object_doc = CObjectDoc.open(
                self.m_tree, path, self.m_tree.m_game, self.m_shared_action.isChecked(),
                lambda saved_path: saved[saved_path].read_bytes() if saved_path in saved else None,
            )  # fmt: skip
        except (FileNotFoundError, ValueError, OSError) as exc:
            self.statusBar().showMessage(tr("objects.path_not_open_error", path=path, error=exc), 8000)
            return False
        self.set_object(object_doc)
        self.m_settings.apps.setdefault(APP, {})["file"] = path
        if object_doc.m_restored or object_doc.m_notes:
            files = ", ".join(
                opened.read for role, opened in object_doc.m_opened.items() if role in object_doc.m_restored
            )
            said = [tr("objects.opened_your_saved_edits_project", files=files)] if files else []
            self.statusBar().showMessage("; ".join(said + object_doc.m_notes), 15000)
        return True

    def set_object(self, object_doc: CObjectDoc) -> None:
        self.m_object_doc, self.m_files = object_doc, CModelFiles(object_doc.m_files)
        object_doc.m_listeners.append(self._changed)
        object_doc.m_undo.m_listeners.append(self._title)
        self.m_objects.show_object(object_doc)
        self.m_objects.setCurrentItem(self.m_objects.topLevelItem(0))
        self.m_feedback.show_doc(object_doc.document("fc"))
        self._redraw(frame=True)
        self._title()
        stuck = [opened.read for opened in object_doc.m_opened.values() if not opened.document.exact]
        if stuck:
            self.statusBar().showMessage(tr("objects.read_only_not_back_byte", files=", ".join(stuck)), 12000)

    def confirm_discard(self) -> bool:
        """Whether the open object's unsaved edits may go (asked only when there are some)."""
        if self.m_object_doc is None or not self.m_object_doc.m_undo.dirty:
            return True
        return widgets.ask_keep(
            self, tr(TITLE), tr("objects.unsaved_edits_save", title=self.m_object_doc.title), self._save_kept
        )

    def _save_kept(self) -> bool:
        """The save dialog; whether the open object's edits are kept afterwards."""
        self.save()
        return not self.m_object_doc.m_undo.dirty

    def _reopen(self) -> None:
        """The object read again with the other choice of copy; the tick goes back when it stays as it is open."""
        if self.m_object_doc is not None and not self.open_path(self.m_object_doc.main.asked):
            self.m_shared_action.setChecked(not self.m_shared_action.isChecked())

    def _ask_path(self) -> None:
        if self.m_tree is None:
            return
        path, is_accepted = QInputDialog.getText(self, tr(TITLE), tr("objects.game_path_data_name_cfg"))
        if is_accepted and path.strip():
            self.open_path(path.strip())

    def _open_named(self, name: str) -> None:
        """A file field double-clicked: in the viewer (the suite's), or as an object when it is one."""
        if self.m_tree is None:
            return
        target = name.replace("\\", "/").lower()
        if target.endswith(OBJECT_FILES):
            self.open_path(target)
        elif not links.view_file(self.m_tree.m_game, target):
            self.statusBar().showMessage(tr("objects.viewer_shows_file_suites_window"), 8000)

    def _to_viewer(self) -> None:
        if self.m_object_doc is not None and not links.view_file(
            self.m_object_doc.m_game, self.m_object_doc.main.asked
        ):
            self.statusBar().showMessage(tr("objects.viewer_suites_window"), 8000)

    # the object
    def _changed(self, roles: set[str]) -> None:
        self.m_objects.show_object(self.m_object_doc)
        self._picked(self.m_objects.current())
        if "cfg" in roles:
            self.m_sequences.show_doc(self.m_object_doc.document("cfg"), self.m_scene)
        if "fc" in roles:
            self.m_feedback.show_doc(self.m_object_doc.document("fc"))
        self.m_redraw.start()
        self._title()

    def _title(self) -> None:
        if self.m_object_doc is None:
            return
        mark = " *" if self.m_object_doc.m_undo.dirty else ""
        self.setWindowTitle(tr("common.title.name_title", name=self.m_object_doc.title + mark, title=tr(TITLE)))
        self.m_undo_action.setEnabled(self.m_object_doc.m_undo.m_index > 0)
        self.m_redo_action.setEnabled(self.m_object_doc.m_undo.m_index < len(self.m_object_doc.m_undo.m_commands))

    def options(self) -> SOptions:
        return SOptions(lod=self.m_lod, shared=self.m_shared_action.isChecked())

    def _redraw(self, frame: bool = False) -> None:
        """The 3D view, the sequences and the overlay from the object as it is now."""
        if self.m_object_doc is None:
            return
        try:
            self.m_scene = self.m_object_doc.scene(self.options())
        except Exception as exc:  # an edit that leaves the config unreadable: say so, keep the last picture
            self.statusBar().showMessage(tr("objects.config_not_draw", kind=type(exc).__name__, error=exc), 8000)
            return
        said = self.m_figures.graft(self.m_scene, self.m_object_doc)
        if said:
            self.statusBar().showMessage(said, 8000)
        self.m_view.show_scene(self.m_scene, self.m_files, frame=frame)
        self.m_sequences.show_doc(self.m_object_doc.document("cfg"), self.m_scene)
        self._overlay()

    def _overlay(self) -> None:
        found: list[shapes.SShape] = []
        info_doc, feedback = self.m_object_doc.document("ifo"), self.m_object_doc.document("fc")
        if info_doc is not None:
            kinds = {k for k, key in (("box", "boxes"), ("dummy", "boxes"), ("area", "areas"))
                     if self.m_toggles[key].isChecked()}  # fmt: skip
            found += [
                shape for shape in shapes.info_shapes(info_doc, self.m_object_doc.m_origin) if shape.kind in kinds
            ]
        if feedback is not None and self.m_toggles["dummies"].isChecked():
            found += shapes.feedback_shapes(feedback)
        row = self.m_objects.current()
        self.m_overlay.set_shapes(found, row.path if row is not None and row.role != "cfg" else None)

    def _toggle(self, key: str, is_checked: bool) -> None:
        if key in ("shadows", "ground", "particles", "cloth", "torn"):
            setattr(self.m_view.m_state, key, is_checked)
            self.m_view.update()
        elif key == "figures":
            self.m_figures.set_shown(is_checked)
        elif self.m_object_doc is not None:
            self._overlay()

    def _set_lod(self, lod: int) -> None:
        self.m_lod = lod
        self._redraw()

    def _picked(self, row: SRow | None) -> None:
        document = self._doc(row)
        self.m_tool.select(row)
        if row is None or document is None:
            self.m_props.show_fields([])
            return
        part = document.find(row.path) if row.kind != "empty" else None
        shown = part is not None and row.kind == "part"
        self.m_props.show_fields(
            cfgdoc.fields(part.node) if shown else [], not document.exact, part.kind if shown else ""
        )
        self.m_view.m_state.selected = row.path if row.role == "cfg" and row.kind == "part" else None
        self.m_view.update()
        if self.m_object_doc is not None:
            self._overlay()

    def _doc(self, row: SRow | None) -> SDoc | None:
        return self.m_object_doc.document(row.role) if self.m_object_doc is not None and row is not None else None

    def _apply(self, role: str, *edits) -> None:
        document = self.m_object_doc.document(role) if self.m_object_doc is not None else None
        if document is None or not document.exact:
            self.statusBar().showMessage(tr("objects.file_read_only_not_back"), 8000)
            return
        self.m_object_doc.apply(role, *edits)

    def _preview(self, role: str) -> None:
        """A handle drag in progress: the view reads the edited bytes and is drawn again when the mouse pauses."""
        self.m_object_doc.sync({role})
        self.m_redraw.start()

    def _fields_edited(self, changes: list) -> None:
        """Fields of the current part set, as one undoable step."""
        row = self.m_objects.current()
        if row is not None:
            self._apply(row.role, *(cfgdoc.set_value(node, value) for node, value in changes))

    def undo(self) -> None:
        if self.m_object_doc is not None and (label := self.m_object_doc.m_undo.undo()):
            self.statusBar().showMessage(tr("common.undone_label", label=label), 5000)

    def redo(self) -> None:
        if self.m_object_doc is not None and (label := self.m_object_doc.m_undo.redo()):
            self.statusBar().showMessage(tr("common.redone_label", label=label), 5000)

    # adding, removing, moving parts
    def _target(self) -> tuple[SRow, SDoc, object, object] | None:
        """The current row, its document, the list node a new part goes into and the part the row is."""
        row = self.m_objects.current()
        document = self._doc(row)
        if row is None or document is None:
            return None
        part = document.find(row.path)
        if part is None:
            return None
        if row.kind == "part" and part.parent is not None:
            return row, document, part.parent, part
        return row, document, part.node, None

    def add_part(self, kind: str = "") -> None:
        """A new part in the list of the current row: of the kind it takes (asked when it takes several)."""
        found = self._target()
        if found is None:
            return
        row, document, holder, part = found
        section = row.section or (part.section if part is not None else "")
        if row.kind == "part" and part is not None and not part.section:
            self.statusBar().showMessage(tr("objects.pick_list_models_lights_part"), 8000)
            return
        kinds = cfgparts.kinds_for(section) or ((part.kind,) if part is not None else ())
        if not kind:
            kind = self._choose(kinds, tr("objects.add_part_kind"))
        node = cfgparts.new_part(document, kind) if kind else None
        if node is None:
            self.statusBar().showMessage(
                tr(
                    "objects.no_file_copy_edit_add",
                    kind=kind or tr("common.part"),
                ),
                8000,
            )
            return
        self._insert(row, document, holder, part, section, node)

    def _insert(self, row: SRow, document: SDoc, holder, part, section: str, node) -> None:
        edits = []
        if row.kind == "empty":
            holder, made = cfgparts.list_node(document, holder, section)
            edits.append(made)
        index = (
            len(holder.children)
            if part is None
            else next(i for i, child in enumerate(holder.children) if child is part.node) + 1
        )
        edits.append(cfgdoc.insert(holder, index, [node], tr("objects.add_kind", kind=cfgdoc.kind_of(node))))
        self._apply(row.role, *edits)
        list_path = row.path if row.kind != "part" else row.path.rsplit("/", 1)[0]
        if row.kind == "empty":
            list_path = f"{row.path}/{section}".lstrip("/")
        self.m_objects.select(row.role, f"{list_path}/{index}")

    def add_from_file(self) -> None:
        """A part copied from another file of the game (a material of another config, say)."""
        found = self._target()
        if found is None or self.m_tree is None:
            return
        row, document, holder, part = found
        section = row.section or (part.section if part is not None else "")
        kinds = cfgparts.kinds_for(section)
        path, is_accepted = QInputDialog.getText(
            self,
            tr(TITLE),
            tr("objects.copy_kind_file", kind=tr("objects.or").join(kinds) or tr("common.part")),
        )
        if not is_accepted or not path.strip():
            return
        if not self.m_tree.exists(path.strip()):
            error = tr("islands.not_games_files")
            self.statusBar().showMessage(tr("objects.path_not_open_error", path=path.strip(), error=error), 8000)
            return
        try:
            other = SDoc.read(self.m_tree.read(path.strip()), path.strip())
        except (ValueError, OSError, KeyError) as exc:  # no config, or one that does not read
            self.statusBar().showMessage(tr("objects.path_not_open_error", path=path.strip(), error=exc), 8000)
            return
        choices = [candidate for candidate in other.parts().walk() if candidate.kind in kinds and not candidate.is_list]
        names = [f"{candidate.path}  {candidate.name}" for candidate in choices]
        pick = self._choose(tuple(names), tr("objects.which_part")) if names else ""
        if pick:
            node = cfgdoc.restyled(choices[names.index(pick)].node, document.newline)
            self._insert(row, document, holder, part, section, node)

    def _choose(self, options: tuple[str, ...], question: str) -> str:
        if len(options) <= 1:
            return options[0] if options else ""
        found, is_accepted = QInputDialog.getItem(self, tr(TITLE), question, list(options), 0, False)
        return found if is_accepted else ""

    def duplicate_part(self) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _document, holder, part = found
            self._apply(row.role, cfgdoc.duplicate(holder, part.node))

    def remove_part(self) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _document, holder, part = found
            self._apply(row.role, cfgdoc.remove(holder, part.node))

    def move_part(self, step: int) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _document, holder, part = found
            self._apply(row.role, cfgdoc.move(holder, part.node, step))

    # saving
    def save(self) -> None:
        """The save dialog; the chosen way's files go into the open project (``objbuild.projectpart``)."""
        if self.m_object_doc is None:
            return
        dialog = writeback.CSaveDialog(self.m_object_doc, self, self._held())
        accepted = dialog.exec()
        dialog.deleteLater()  # a child of the window otherwise stays until the window closes
        if accepted and dialog.m_result_plan is not None:
            self.write(dialog.m_result_plan)

    def _held(self) -> list[str]:
        """What the open project already holds of this object (a second save replaces it)."""
        saved = projectpart.EDITS.saved_files(project.current(self.m_settings), self.m_object_doc.m_game)
        return writeback.held(self.m_object_doc, saved)

    def write(self, plan: writeback.SPlan) -> None:
        current = project.current(self.m_settings)
        try:
            notice = projectpart.EDITS.save_announced(current, self.m_object_doc.m_game, plan.files, plan.stale)
        except OSError as exc:  # the project's folder takes no file: the edits stay unsaved
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return
        self.m_object_doc.m_undo.mark_clean()
        other = (
            ""
            if current.game == self.m_object_doc.m_game
            else tr("objects.project_builds_they_wait", game=current.game)
        )
        self.statusBar().showMessage(
            tr(
                "objects.saved_count_files_folder_other",
                count=len(plan.files),
                folder=current.folder.name,
                other=other,
                text=notice.text,
            ),
            15000,
        )

    # the view
    def _seek(self, seconds: float, animations: dict) -> None:
        self.m_view.m_state.animation = {**animations, **self.m_figures.animations(seconds)}
        self.m_view.set_time(seconds)

    def _follow_clock(self) -> None:
        self.m_overlay.update()
        state_time = self.m_view.m_state.time
        self.m_sequences.tick(state_time)
        if self.m_view.m_playing:
            self.m_view.m_state.animation = {
                **self.m_sequences.animations(state_time),
                **self.m_figures.animations(state_time),
            }

    def _save_picture(self) -> None:
        def picture():
            image = self.m_view.image()
            self.m_overlay.picture(image, self.m_view.m_camera)  # the info and feedback files drawn over the view
            return image

        save_picture(self, picture)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about unsaved edits, nothing released."""
        return self.confirm_discard()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        self.m_view.release()
        super().closeEvent(event)


def run(game: str | None = None, path: str = "") -> int:
    """The object builder alone in a window of its own, on the suite's settings."""
    from ..render.glformat import prepare

    prepare()
    return widgets.run_alone(CObjectWindow, APP, (1600, 980), game, path)
