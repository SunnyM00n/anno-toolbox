"""The object builder's window, laid out as the studio's object builder: the game's files and the object tree on the
left, the 3D view in the middle (the viewer's, redrawn a moment after each edit), the properties on the right, the
sequence editor and the feedback timeline along the bottom; the info and feedback files drawn over the view.

``ObjectWindow(settings, settings_path)`` is the entry (the suite's ``Objects`` app, or ``run`` alone); ``open_file``
is what another app calls. The user's guide is docs/object-builder.md; the design docs/tool-suite.md section 23.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMessageBox

from ..infra.i18n import N_, number, tr
from ..models import cfgdoc, cfgparts, fileresolve
from ..models.cfgdoc import Doc
from ..render import rhiview
from ..sources import gametree
from ..suite import gameopen, links, project, widgets
from ..suite.background import Background
from ..viewer.browser import FileBrowser
from ..viewer.modelfiles import ModelFiles
from ..viewer.modelscene import Options, Scene
from ..viewer.modelview import make_view
from ..viewer.renderingmenu import toggle_action
from . import projectpart, shapes, writeback
from .feedbackline import FeedbackLine
from .figuremenu import FigureMenu
from .handletool import HandleTool, Host
from .objdoc import ObjectDoc
from .objprops import PropertyEditor
from .objtree import ObjectTree, Row
from .overlay import Overlay
from .timeline import SequenceEditor

APP = "objects"  # the key of its remembered state in the settings
TITLE = N_("Object builder")
OBJECT_FILES = (".cfg", ".mcf", ".prp", ".ifo", ".fc")
REDRAW_MS = 150  # after the last edit, the 3D view is redrawn


class ObjectWindow(QMainWindow):
    """``hosts_gl``: the window also holds OpenGL views (the suite's), so the 3D view composes with OpenGL."""

    def __init__(self, settings, settings_path: Path | None = None, hosts_gl: bool = False) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.tree = None
        self.background = Background(self)
        self.obj: ObjectDoc | None = None
        self.files: ModelFiles | None = None
        self.scene: Scene | None = None
        self.browser, self.objects, self.props = FileBrowser(), ObjectTree(), PropertyEditor()
        self.sequences, self.feedback = SequenceEditor(), FeedbackLine()
        self.view = make_view(rhiview.composable(rhiview.chosen(settings.renderer), hosts_gl))
        self.overlay = Overlay(self.view)
        self.tool = HandleTool(self.view, self.overlay, Host(
            lambda: self.obj, self._apply, self._preview, lambda text: self.statusBar().showMessage(text, 8000)
        ))  # fmt: skip
        self.setCentralWidget(self.view)
        self.docks = {}
        for name, widget, area in (
            (N_("Files"), self.browser, Qt.DockWidgetArea.LeftDockWidgetArea),
            (N_("Object"), self.objects, Qt.DockWidgetArea.LeftDockWidgetArea),
            (N_("Properties"), self.props, Qt.DockWidgetArea.RightDockWidgetArea),
            (N_("Sequences"), self.sequences, Qt.DockWidgetArea.BottomDockWidgetArea),
            (N_("Feedback loops"), self.feedback, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"objects{name}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[name] = dock
        self.resizeDocks([self.docks["Files"], self.docks["Properties"]], [340, 380], Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.docks["Sequences"], self.docks["Feedback loops"])
        self.docks["Sequences"].raise_()
        self.redraw = QTimer(self, singleShot=True, interval=REDRAW_MS, timeout=self._redraw)
        self._menus()
        self._wire()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("No game open"))

    # building the window
    def _menus(self) -> None:
        file = self.menuBar().addMenu(tr("&File"))
        games = file.addMenu(tr("Open &game"))
        for key, name in gametree.GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        file.addAction(tr("&Open a file by its path..."), self._ask_path, QKeySequence("Ctrl+O"))
        file.addAction(tr("&Save into the project..."), self.save, QKeySequence.StandardKey.Save)
        file.addAction(tr("Show in the &viewer"), self._to_viewer)
        file.addAction(tr("Save the &picture..."), self._save_picture, QKeySequence("Ctrl+Shift+S"))
        edit, self.undo_action, self.redo_action = widgets.edit_menu(self, self.undo, self.redo)
        edit.addSeparator()
        edit.addAction(tr("&Add a part"), self.add_part, QKeySequence("Ins"))
        edit.addAction(tr("Add a part from &another file..."), self.add_from_file)
        edit.addAction(tr("&Duplicate"), self.duplicate_part, QKeySequence("Ctrl+D"))
        edit.addAction(tr("&Remove"), self.remove_part, QKeySequence.StandardKey.Delete)
        edit.addAction(tr("Move &up"), lambda: self.move_part(-1), QKeySequence("Alt+Up"))
        edit.addAction(tr("Move do&wn"), lambda: self.move_part(1), QKeySequence("Alt+Down"))
        edit.addSeparator()
        self._handle_menu(edit.addMenu(tr("&Handles")))
        self.footprint_action = edit.addAction(tr("Draw a &footprint"))
        self.footprint_action.setCheckable(True)
        self.footprint_action.triggered.connect(self.tool.draw_footprint)
        self.tool.drawing_changed.connect(self.footprint_action.setChecked)
        view = self.menuBar().addMenu(tr("&View"))
        self.toggles: dict[str, QAction] = {}
        for key, text, on in (("boxes", N_("The info file's &boxes"), True), ("areas", N_("The info file's &areas"), True),
                              ("dummies", N_("The feedback's &dummies"), True), ("shadows", N_("&Shadows"), True),
                              ("ground", N_("G&round"), True), ("particles", N_("&Effects"), True),
                              ("cloth", N_("Simulate &cloth"), False), ("torn", N_("&Torn cloth (damaged)"), False),
                              ("figures", N_("Walking &figures"), False)):  # fmt: skip
            self.toggles[key] = toggle_action(view, tr(text), on, lambda checked, k=key: self._toggle(k, checked))
        self.figures = FigureMenu(self, view.addMenu(tr("Figures' &state")))
        self.shared_action = view.addAction(tr("Read the baked &shared/ copy (as the game does)"), self._reopen)
        self.shared_action.setCheckable(True)
        self.shared_action.setChecked(True)
        lods = view.addMenu(tr("&Level of detail"))
        self.lod = 0
        for n in range(5):
            lods.addAction(tr("LOD {n}", n=n), lambda n=n: self._set_lod(n))
        view.addAction(tr("&Frame the model"), lambda: self.view.frame_model(), QKeySequence("F"))
        windows = self.menuBar().addMenu(tr("&Window"))
        for dock in self.docks.values():
            windows.addAction(dock.toggleViewAction())

    def _handle_menu(self, menu) -> None:
        """Off, Move (W), Rotate (E), Scale (R): the handles over the selected part; the keys work in the view."""
        group, self.handle_actions = QActionGroup(self), {}
        for mode, text, key in (("off", N_("&Off"), ""), ("move", N_("&Move"), "W"), ("rotate", N_("&Rotate"), "E"),
                                ("scale", N_("&Scale"), "R")):  # fmt: skip
            action = menu.addAction(tr(text))
            action.setCheckable(True)
            action.setChecked(mode == "off")
            action.triggered.connect(lambda _c=False, m=mode: self.tool.set_mode(m))
            if key:
                action.setShortcut(QKeySequence(key))
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                self.view.addAction(action)
            group.addAction(action)
            self.handle_actions[mode] = action

    def _wire(self) -> None:
        self.browser.opened.connect(self.open_path)
        self.objects.picked.connect(self._picked)
        self.props.edited.connect(lambda node, value: self._fields_edited([(node, value)]))
        self.props.edited_many.connect(self._fields_edited)
        self.props.refused.connect(lambda why: self.statusBar().showMessage(why, 8000))
        self.props.open_file.connect(self._open_named)
        self.sequences.edits.connect(lambda edits: self._apply("cfg", *edits))
        self.sequences.select.connect(lambda path: self.objects.select("cfg", path))
        self.sequences.changed.connect(self._seek)
        self.sequences.playing.connect(lambda on: setattr(self.view, "playing", on))
        self.sequences.speed.valueChanged.connect(lambda v: setattr(self.view, "speed", v))
        self.feedback.edits.connect(lambda edits: self._apply("fc", *edits))
        self.feedback.select.connect(lambda path: self.objects.select("fc", path))
        self.feedback.said.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.view.picked.connect(lambda node: node and self.objects.select("cfg", node))
        self.view.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.view.frameSwapped.connect(self._follow_clock)

    # games and files
    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game in the background; ``then``: an object to open in it."""

        def opened(tree) -> None:
            if isinstance(tree, Exception):
                self.statusBar().showMessage(
                    tr("{game} did not open: {error}", game=gametree.GAMES.get(key, key), error=tree)
                )
                return
            self.set_tree(tree)
            self.settings.apps.setdefault(APP, {})["game"] = key
            if then:
                self.open_path(then)

        gameopen.open_installed(self, self.settings, key, self.background, then=opened, wait=wait)

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``sources.gametree.GameTree``) and edit its objects."""
        self.tree = tree
        self.browser.set_tree(tree)
        self.props.game, self.props.files = tree.game, lambda: tree.paths
        self.props.source_names = tree.game in fileresolve.NEW_ENGINE
        self.statusBar().showMessage(
            tr("{name}: {count} files; double-click a model config", name=tree.name, count=number(len(tree.paths)))
        )

    def open_file(self, game: str, path: str) -> None:
        """Edit an object of a game, opening that game first when another one is open."""
        if self.tree is not None and self.tree.game == game:
            self.open_path(path)
        else:
            self.open_game(game, path)

    def open_path(self, path: str) -> bool:
        """Open the object a file belongs to (its config, info and feedback files)."""
        if self.tree is None:
            return False
        if not path.lower().endswith(OBJECT_FILES):
            self.statusBar().showMessage(tr("{path} is no model config, info or feedback file", path=path), 8000)
            return False
        if not self.confirm_discard():
            return False
        saved = projectpart.EDITS.saved_files(project.current(self.settings), self.tree.game)
        try:
            obj = ObjectDoc.open(
                self.tree, path, self.tree.game, self.shared_action.isChecked(),
                lambda p: saved[p].read_bytes() if p in saved else None,
            )  # fmt: skip
        except (FileNotFoundError, ValueError, OSError) as exc:
            self.statusBar().showMessage(tr("{path} does not open: {error}", path=path, error=exc), 8000)
            return False
        self.set_object(obj)
        self.settings.apps.setdefault(APP, {})["file"] = path
        if obj.restored or obj.notes:
            files = ", ".join(o.read for role, o in obj.opened.items() if role in obj.restored)
            said = [tr("opened with your saved edits from the project: {files}", files=files)] if files else []
            self.statusBar().showMessage("; ".join(said + obj.notes), 15000)
        return True

    def set_object(self, obj: ObjectDoc) -> None:
        self.obj, self.files = obj, ModelFiles(obj.files)
        obj.listeners.append(self._changed)
        obj.undo.listeners.append(self._title)
        self.objects.show_object(obj)
        self.objects.setCurrentItem(self.objects.topLevelItem(0))
        self.feedback.show_doc(obj.doc("fc"))
        self._redraw(frame=True)
        self._title()
        stuck = [o.read for o in obj.opened.values() if not o.doc.exact]
        if stuck:
            self.statusBar().showMessage(
                tr("read only: {files} does not read back byte for byte", files=", ".join(stuck)), 12000
            )

    def confirm_discard(self) -> bool:
        """Whether the open object's unsaved edits may go (asked only when there are some)."""
        if self.obj is None or not self.obj.undo.dirty:
            return True
        answer = QMessageBox.question(
            self, tr(TITLE), tr("{title} has unsaved edits. Discard them?", title=self.obj.title)
        )
        return answer == QMessageBox.StandardButton.Yes

    def _reopen(self) -> None:
        if self.obj is not None:
            self.open_path(self.obj.main.asked)

    def _ask_path(self) -> None:
        if self.tree is None:
            return
        path, ok = QInputDialog.getText(self, tr(TITLE), tr("A game path (data/.../name.cfg):"))
        if ok and path.strip():
            self.open_path(path.strip())

    def _open_named(self, name: str) -> None:
        """A file field double-clicked: in the viewer (the suite's), or as an object when it is one."""
        if self.tree is None:
            return
        target = name.replace("\\", "/").lower()
        if target.endswith(OBJECT_FILES):
            self.open_path(target)
        elif not links.view_file(self.tree.game, target):
            self.statusBar().showMessage(tr("the viewer shows that file in the suite's window"), 8000)

    def _to_viewer(self) -> None:
        if self.obj is not None and not links.view_file(self.obj.game, self.obj.main.asked):
            self.statusBar().showMessage(tr("the viewer is in the suite's window"), 8000)

    # the object
    def _changed(self, roles: set[str]) -> None:
        self.objects.show_object(self.obj)
        self._picked(self.objects.current())
        if "cfg" in roles:
            self.sequences.show_doc(self.obj.doc("cfg"), self.scene)
        if "fc" in roles:
            self.feedback.show_doc(self.obj.doc("fc"))
        self.redraw.start()
        self._title()

    def _title(self) -> None:
        if self.obj is None:
            return
        mark = " *" if self.obj.undo.dirty else ""
        self.setWindowTitle(tr("{name} - {title}", name=self.obj.title + mark, title=tr(TITLE)))
        self.undo_action.setEnabled(self.obj.undo.index > 0)
        self.redo_action.setEnabled(self.obj.undo.index < len(self.obj.undo.commands))

    def options(self) -> Options:
        return Options(lod=self.lod, shared=self.shared_action.isChecked())

    def _redraw(self, frame: bool = False) -> None:
        """The 3D view, the sequences and the overlay from the object as it is now."""
        if self.obj is None:
            return
        try:
            self.scene = self.obj.scene(self.options())
        except Exception as exc:  # an edit that leaves the config unreadable: say so, keep the last picture
            self.statusBar().showMessage(
                tr("the config does not draw: {kind}: {error}", kind=type(exc).__name__, error=exc), 8000
            )
            return
        said = self.figures.graft(self.scene, self.obj)
        if said:
            self.statusBar().showMessage(said, 8000)
        self.view.show_scene(self.scene, self.files, frame=frame)
        self.sequences.show_doc(self.obj.doc("cfg"), self.scene)
        self._overlay()

    def _overlay(self) -> None:
        found: list[shapes.Shape] = []
        info, feedback = self.obj.doc("ifo"), self.obj.doc("fc")
        if info is not None:
            kinds = {k for k, key in (("box", "boxes"), ("dummy", "boxes"), ("area", "areas"))
                     if self.toggles[key].isChecked()}  # fmt: skip
            found += [s for s in shapes.info_shapes(info, self.obj.origin) if s.kind in kinds]
        if feedback is not None and self.toggles["dummies"].isChecked():
            found += shapes.feedback_shapes(feedback)
        row = self.objects.current()
        self.overlay.set_shapes(found, row.path if row is not None and row.role != "cfg" else None)

    def _toggle(self, key: str, on: bool) -> None:
        if key in ("shadows", "ground", "particles", "cloth", "torn"):
            setattr(self.view.state, key, on)
            self.view.update()
        elif key == "figures":
            self.figures.set_shown(on)
        elif self.obj is not None:
            self._overlay()

    def _set_lod(self, n: int) -> None:
        self.lod = n
        self._redraw()

    def _picked(self, row: Row | None) -> None:
        doc = self._doc(row)
        self.tool.select(row)
        if row is None or doc is None:
            self.props.show_fields([])
            return
        part = doc.find(row.path) if row.kind != "empty" else None
        shown = part is not None and row.kind == "part"
        self.props.show_fields(cfgdoc.fields(part.node) if shown else [], not doc.exact, part.kind if shown else "")
        self.view.state.selected = row.path if row.role == "cfg" and row.kind == "part" else None
        self.view.update()
        if self.obj is not None:
            self._overlay()

    def _doc(self, row: Row | None) -> Doc | None:
        return self.obj.doc(row.role) if self.obj is not None and row is not None else None

    def _apply(self, role: str, *edits) -> None:
        doc = self.obj.doc(role) if self.obj is not None else None
        if doc is None or not doc.exact:
            self.statusBar().showMessage(tr("this file is read only (it does not read back byte for byte)"), 8000)
            return
        self.obj.apply(role, *edits)

    def _preview(self, role: str) -> None:
        """A handle drag in progress: the view reads the edited bytes and is drawn again when the mouse pauses."""
        self.obj.sync({role})
        self.redraw.start()

    def _fields_edited(self, changes: list) -> None:
        """Fields of the current part set, as one undoable step."""
        row = self.objects.current()
        if row is not None:
            self._apply(row.role, *(cfgdoc.set_value(node, value) for node, value in changes))

    def undo(self) -> None:
        if self.obj is not None and (label := self.obj.undo.undo()):
            self.statusBar().showMessage(tr("undone: {label}", label=label), 5000)

    def redo(self) -> None:
        if self.obj is not None and (label := self.obj.undo.redo()):
            self.statusBar().showMessage(tr("redone: {label}", label=label), 5000)

    # adding, removing, moving parts
    def _target(self) -> tuple[Row, Doc, object, object] | None:
        """The current row, its document, the list node a new part goes into and the part the row is."""
        row = self.objects.current()
        doc = self._doc(row)
        if row is None or doc is None:
            return None
        part = doc.find(row.path)
        if part is None:
            return None
        if row.kind == "part" and part.parent is not None:
            return row, doc, part.parent, part
        return row, doc, part.node, None

    def add_part(self, kind: str = "") -> None:
        """A new part in the list of the current row: of the kind it takes (asked when it takes several)."""
        found = self._target()
        if found is None:
            return
        row, doc, holder, part = found
        section = row.section or (part.section if part is not None else "")
        if row.kind == "part" and part is not None and not part.section:
            self.statusBar().showMessage(tr("pick a list (Models, Lights ...) or a part in one"), 8000)
            return
        kinds = cfgparts.kinds_for(section) or ((part.kind,) if part is not None else ())
        if not kind:
            kind = self._choose(kinds, tr("Add a part of which kind?"))
        node = cfgparts.new_part(doc, kind) if kind else None
        if node is None:
            self.statusBar().showMessage(
                tr(
                    "no {kind} in this file to copy: Edit > Add a part from another file",
                    kind=kind or tr("part"),
                ),
                8000,
            )
            return
        self._insert(row, doc, holder, part, section, node)

    def _insert(self, row: Row, doc: Doc, holder, part, section: str, node) -> None:
        edits = []
        if row.kind == "empty":
            holder, made = cfgparts.list_node(doc, holder, section)
            edits.append(made)
        at = (
            len(holder.children)
            if part is None
            else next(i for i, c in enumerate(holder.children) if c is part.node) + 1
        )
        edits.append(cfgdoc.insert(holder, at, [node], tr("add {kind}", kind=cfgdoc.kind_of(node))))
        self._apply(row.role, *edits)
        list_path = row.path if row.kind != "part" else row.path.rsplit("/", 1)[0]
        if row.kind == "empty":
            list_path = f"{row.path}/{section}".lstrip("/")
        self.objects.select(row.role, f"{list_path}/{at}")

    def add_from_file(self) -> None:
        """A part copied from another file of the game (a material of another config, say)."""
        found = self._target()
        if found is None or self.tree is None:
            return
        row, doc, holder, part = found
        section = row.section or (part.section if part is not None else "")
        kinds = cfgparts.kinds_for(section)
        path, ok = QInputDialog.getText(
            self,
            tr(TITLE),
            tr("Copy a {kind} from which file?", kind=tr(" or ").join(kinds) or tr("part")),
        )
        if not ok or not self.tree.exists(path.strip()):
            return
        other = Doc.read(self.tree.read(path.strip()), path.strip())
        choices = [p for p in other.parts().walk() if p.kind in kinds and not p.is_list]
        names = [f"{p.path}  {p.name}" for p in choices]
        pick = self._choose(tuple(names), tr("Which part?")) if names else ""
        if pick:
            node = cfgdoc.restyled(choices[names.index(pick)].node, doc.newline)
            self._insert(row, doc, holder, part, section, node)

    def _choose(self, options: tuple[str, ...], question: str) -> str:
        if len(options) <= 1:
            return options[0] if options else ""
        found, ok = QInputDialog.getItem(self, tr(TITLE), question, list(options), 0, False)
        return found if ok else ""

    def duplicate_part(self) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _doc, holder, part = found
            self._apply(row.role, cfgdoc.duplicate(holder, part.node))

    def remove_part(self) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _doc, holder, part = found
            self._apply(row.role, cfgdoc.remove(holder, part.node))

    def move_part(self, step: int) -> None:
        found = self._target()
        if found is not None and found[3] is not None:
            row, _doc, holder, part = found
            self._apply(row.role, cfgdoc.move(holder, part.node, step))

    # saving
    def save(self) -> None:
        """The save dialog; the chosen way's files go into the open project (``objbuild.projectpart``)."""
        if self.obj is None:
            return
        dialog = writeback.SaveDialog(self.obj, self, self._held())
        if dialog.exec() and dialog.result_plan is not None:
            self.write(dialog.result_plan)

    def _held(self) -> list[str]:
        """What the open project already holds of this object (a second save replaces it)."""
        saved = projectpart.EDITS.saved_files(project.current(self.settings), self.obj.game)
        return writeback.held(self.obj, saved)

    def write(self, plan: writeback.Plan) -> None:
        current = project.current(self.settings)
        notice = projectpart.EDITS.save_announced(current, self.obj.game, plan.files, plan.stale)
        self.obj.undo.mark_clean()
        other = (
            ""
            if current.game == self.obj.game
            else tr(" (the project builds {game}: they wait there)", game=current.game)
        )
        self.statusBar().showMessage(
            tr(
                "saved {count} files into {folder}{other}; {text}",
                count=len(plan.files),
                folder=current.folder.name,
                other=other,
                text=notice.text,
            ),
            15000,
        )

    # the view
    def _seek(self, seconds: float, animations: dict) -> None:
        self.view.state.animation = {**animations, **self.figures.animations(seconds)}
        self.view.set_time(seconds)

    def _follow_clock(self) -> None:
        self.overlay.update()
        t = self.view.state.time
        self.sequences.tick(t)
        if self.view.playing:
            self.view.state.animation = {**self.sequences.animations(t), **self.figures.animations(t)}

    def _save_picture(self) -> None:
        name, _f = QFileDialog.getSaveFileName(
            self, tr("Save the picture"), "", "PNG (*.png)"
        )  # i18n: ok (a file filter)
        if name:
            image = self.view.image()
            self.overlay.picture(image, self.view.camera)
            image.save(name)

    def closeEvent(self, event) -> None:
        if not self.confirm_discard():
            event.ignore()
            return
        self.view.release()
        super().closeEvent(event)


def run(game: str | None = None, path: str = "") -> int:
    """The object builder alone in a window of its own, on the suite's settings."""
    from ..render.glformat import prepare

    prepare()
    return widgets.run_alone(ObjectWindow, APP, (1600, 980), game, path)
