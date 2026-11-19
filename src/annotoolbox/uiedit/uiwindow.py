"""The UI editor's window (Qt): a game's UI files on the left with the open file's elements, the preview in the
middle, the selected element's properties, the palette, the view models and the sample values around it, the states
and the bindings as a node graph below; edits go on the undo stack and are kept in the open project.

``UiWindow(settings, settings_path)`` is the suite's UI app; ``run`` starts it alone. ``open_game`` and
``open_file`` are the entry points other code calls. docs/ui-editor.md is the user's guide.
"""

from __future__ import annotations

import struct
import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QTreeWidgetItem,
)

from ..infra.i18n import N_, tr, trn
from ..sources import gametree
from ..suite import gameopen, gamesync, linkannounce, linkscenes, livelink, project, widgets
from ..suite.background import Background
from . import (
    animations,
    bindingdialogs,
    bindings,
    canvas,
    elements,
    fields,
    fonts,
    gamedataview,
    layout,
    library,
    livevalues,
    operators,
    pickers,
    previewdefaults,
    projectpart,
    tweens,
    uidoc,
    uifiles,
    uiframe,
    viewmodels,
    worldmenu,
)
from .pictures import Pictures
from .uiframe import APP, GAMES, ROLE

TITLE = N_("ui.ui_editor")


class _Game:
    """What an open game gives the editor, read off the window's thread."""

    def __init__(self, key: str, tree, cache: Path | None) -> None:
        self.key, self.tree = key, tree
        self.files = uifiles.UiFiles(tree, cache)
        self.library = library.Library.read(tree)
        self.models = viewmodels.ViewModels.read(tree)


class UiWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.game: _Game | None = None
        self.pictures: Pictures | None = None
        self.docs: dict[str, uidoc.UiDoc] = {}
        self.doc: uidoc.UiDoc | None = None
        self.top: elements.Element | None = None
        self.selected: elements.Element | None = None
        self.scripts: viewmodels.ScriptList | None = None
        self.samples, self.names = viewmodels.Samples(), viewmodels.FieldNames()
        self.ctx: layout.Context | None = None
        self.frame: layout.Frame | None = None
        self.background = Background(self)
        self._placing, self._again = False, False  # a layout is running / was asked for while it ran
        self._queued: list = []  # (file or None, action) asked for while a layout runs: it reads the files, so they wait
        self._view: dict[str, object] = {}  # the preview's switches changed meanwhile, taken over by the next layout
        self.sync_layout = False  # place on this thread (tests)
        self._forget_states = lambda: None  # removes the scene resolver of the open game
        self._own: dict[int, tuple[str, int]] = {}  # the open file's own properties (``library.Namer.own``)
        self._opening: str | None = None  # the file asked for last (an older one decoded later is not shown)
        self.world_menu = worldmenu.WorldMenu(self)  # the scene behind the preview (View menu)
        self.mirror = None  # the game mirror once opened (View menu; ``mirror``)
        uiframe.build(self)
        uiframe.menus(self)
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("ui.file_gt_open_game_pick"))

    # games and files

    def project(self) -> project.Project:
        return project.current(self.settings)

    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a file to open."""
        if not gamesync.may_switch(self, key, self.may_leave):  # asks; the suite's other apps follow
            return

        def prepare(tree):
            index = self.settings.index_dir(key) / "ui"
            return _Game(key, tree, index)

        gameopen.open_installed(
            self,
            self.settings,
            key,
            self.background,
            then=lambda game: self._opened(key, then, game),
            prepare=prepare,
            wait=wait,
        )

    def _open_folder(self) -> None:
        if not self.may_leave():
            return
        folder = QFileDialog.getExistingDirectory(self, tr("ui.folder_loose_files_unpacked_game"))
        if folder:
            self.open_folder(Path(folder))

    def open_folder(self, folder: Path, wait: bool = False) -> None:
        """Open a folder of loose 117 files; they are listed and read off the window's thread."""
        self.background.run(
            lambda: _Game("anno117", gametree.loose_tree(folder), None),
            lambda game: self._opened("anno117", "", game),
            wait,
        )

    def _opened(self, key: str, then: str, game) -> None:
        if isinstance(game, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=GAMES.get(key, key), error=game))
            return
        self.game, self.docs, self.doc, self._opening = game, {}, None, None
        self._queued, self._view = [], {}  # what waited was for the game before
        self.top = self.selected = self.frame = None  # nothing of the game before stays to be picked or saved
        self.element_tree.clear()
        self.preview.show_frame(None, None)
        self.setWindowTitle(tr(TITLE))
        self.centre.setCurrentWidget(self.preview)
        for name in ("Files", "Properties"):
            self.docks[name].show()
        self._forget_states()
        self._forget_states = linkscenes.register(game.files.scene_states)  # the live link's scene reload
        self.pictures = Pictures(game.files)
        p = self.project()
        game.files.lay_over({path: f.read_bytes() for path, f in projectpart.EDITS.saved_files(p, key).items()})
        self.samples = viewmodels.Samples(projectpart.sidecar(p, key, "samples"))
        self.samples.fill = self.fill_action.isChecked()
        self.names = viewmodels.FieldNames(projectpart.sidecar(p, key, "names"))
        self.fonts = fonts.GameFonts(game.files)
        self.ctx = layout.Context(game.files, self.samples, canvas.measure, fonts=self.fonts.lookup)
        self.ctx.editor_hidden = self.hidden_action.isChecked()
        previewdefaults.attach(self, key)  # what the preview assumes of the player's game (``preview_store``)
        gamedataview.game_opened(self, game)  # its languages; the game's data, built in the background
        self.namer = library.Namer(game.library, game.files.root, self.names.short())
        self.palette_panel.show_library(game.library)
        self.settings.apps.setdefault(APP, {})["game"] = key
        self._fill_files()
        n = len(game.files.of_kind(*uifiles.EDITABLE))
        name = GAMES.get(key, key)
        self.statusBar().showMessage(tr("ui.scenes_widget_files_reading_names", game=name, n=n))

        def scanned(_result) -> None:
            if game is self.game:  # another game may be open by now
                self._fill_files()
                self.statusBar().showMessage(tr("ui.scenes_widget_files", game=name, n=n))

        self.background.run(game.files.scan_users, scanned)
        if unread := [kept.error for kept in (self.samples, self.names) if kept.error]:
            QMessageBox.warning(self, tr(TITLE), "\n".join(unread))  # they start empty and are not written over
        self.world_menu.game_opened()
        if then:
            self.open_file(then)

    def _fill_files(self) -> None:
        self.file_list.clear()
        if self.game is None:
            return
        saved = set(projectpart.EDITS.saved_files(self.project(), self.game.key))
        uiframe.fill_files(self.file_list, self.game.files, self.file_filter.text(), saved)

    def open_file(self, key: str) -> None:
        """Open a UI file of the game (the project's copy where it has one) and show it; after the edits that wait for
        a layout, so that they are made in the file they were made in. A file not open yet is decoded off the window's
        thread; the one asked for last is shown."""
        if self._placing and self._queued:
            self._queued.append((None, lambda: self.open_file(key)))
            return
        if self.game is None or key not in self.game.files.entries:
            self.statusBar().showMessage(tr("ui.no_such_ui_file", key=key))
            return
        self._opening = key
        if key in self.docs:
            self._show_open(key)
            return
        files, game = self.game.files, self.game
        origin = "project" if key in files.extra else "game"

        def decode():
            doc = uidoc.UiDoc(files, key, files.read(key), origin)
            files.names_of(doc.root)  # the History Editions' names: an editor document read once
            return doc

        self.statusBar().showMessage(tr("ui.opening_name", name=files.title(key)))
        self.background.run(decode, lambda doc: self._decoded(game, key, doc), self.sync_layout)

    def _decoded(self, game: _Game, key: str, doc) -> None:
        """A file decoded off the window's thread: shown unless another file or game was asked for meanwhile. It goes
        live once no layout runs (the layout reads the files)."""
        if game is not self.game or self._opening != key:
            return
        if isinstance(doc, Exception):
            QMessageBox.warning(self, tr(TITLE), tr("ui.key_not_decode_error", key=key, error=doc))
            return
        self._when_idle(lambda: self._went_live(game, key, doc))

    def _went_live(self, game: _Game, key: str, doc: uidoc.UiDoc) -> None:
        if game is not self.game or self._opening != key:
            return
        if key not in self.docs:
            doc.listeners.append(lambda d=doc: self._doc_changed(d))
            self.docs[key] = doc
            game.files.live[key] = doc.root  # the preview of other files places this tree, edits and all
            if self.ctx is not None:
                self.ctx.forget_file(key)  # what they placed of the game's tree must follow the edits too
        self._show_open(key)

    def _show_open(self, key: str) -> None:
        """Show an open file in every panel."""
        self.doc = self.docs[key]
        self.selected = None
        self.preview.fit = True
        self._show_doc()
        exact = "" if self.doc.exact else " - " + tr("ui.file_not_re_encode_byte")
        origin = tr("ui.project") if self.doc.origin == "project" else tr("ui.game")
        self.statusBar().showMessage(f"{self.game.files.title(key)} ({origin}){exact}")

    # showing the open file

    def _doc_changed(self, doc: uidoc.UiDoc) -> None:
        """An edit (or its undo) changed ``doc``: what was kept of it is out of date, the other files' stays."""
        if self.ctx is not None:
            self.ctx.forget(doc.root)
        if self.mirror is not None:
            self.mirror.edited(doc)  # the game mirror's scenes that show it, and its push
        if doc is self.doc:
            self._show_doc()
        else:
            self._redraw()  # the open file may place it

    def _show_doc(self) -> None:
        """The open file in every panel, and its preview laid out again."""
        if self.doc is None:
            return
        selected_node = self.selected.node if self.selected else None
        names = self.game.files.names_of(self.doc.root)  # the History Editions' names live in the editor's documents
        self.top, self._own = elements.tree(self.doc.root, names), library.Namer.own(self.doc.root)
        self.selected = elements.find(self.top, selected_node) if selected_node is not None else None
        self._fill_elements()
        self._redraw()
        self.props.show_node(
            self.selected.node if self.selected else self.doc.root,
            self.selected.kind if self.selected else "template",
            self.namer,
        )
        self._show_graph()
        self._show_states()
        self._show_view_models()
        self.undo_action.setEnabled(bool(self.doc.undo.index))
        self.redo_action.setEnabled(self.doc.undo.index < len(self.doc.undo.commands))
        mark = " *" if self.doc.dirty else ""
        self.setWindowTitle(f"{self.game.files.title(self.doc.key)}{mark} - {tr(TITLE)}")

    def _fill_elements(self) -> None:
        self.element_tree.blockSignals(True)
        self.element_tree.clear()

        def add(parent, el: elements.Element) -> None:
            it = QTreeWidgetItem(
                parent,
                [
                    el.name,
                    el.kind
                    + (f" -> {self.game.files.title(el.template)}" if el.kind == "container" and el.template else ""),
                ],
            )
            it.setData(0, ROLE, el)
            if not fields.get(el.node, "Visible", True):
                it.setForeground(0, self.palette().placeholderText())
            if el is self.selected:
                self.element_tree.setCurrentItem(it)
            for c in el.children:
                add(it, c)

        add(self.element_tree, self.top)
        self.element_tree.expandToDepth(1)
        self.element_tree.blockSignals(False)

    def _redraw(self) -> None:
        """Place the open file again (off the window's thread; one layout runs at a time, the newest request wins)."""
        if self.doc is None or self.ctx is None:
            return
        if self._placing:
            self._again = True
            return
        doc, ctx = self.doc, self.ctx
        ctx.live_text, ctx.live_values = self._live_texts(), livevalues.overrides(self)
        gamedataview.before_layout(self)  # the scene and the language of the game's texts
        for name, value in self._view.items():
            setattr(ctx, name, value)
        self._view.clear()
        self._placing, self._again = True, False
        self.statusBar().showMessage(tr("ui.laying_out_preview"))
        self.background.run(
            lambda: layout.place(doc.key, doc.root, ctx), lambda result: self._placed(doc, result), self.sync_layout
        )

    def _placed(self, doc: uidoc.UiDoc, result) -> None:
        """The layout of ``doc`` is done: show it unless the window moved on; run what was asked meanwhile, in order
        (an action that lays out again makes the ones after it wait for that layout)."""
        self._placing = False
        queued, self._queued = self._queued, []
        if doc is self.doc:
            self._show_result(result)
        lost = 0
        for owner, action in queued:
            if owner is None or owner in self.docs.values():
                self._when_idle(action, owner)
            else:
                lost += 1
        if lost:
            self.statusBar().showMessage(trn("ui.edit_not_made_file_closed", lost))  # fmt: skip
        if (self._again or doc is not self.doc) and not self._placing:
            self._redraw()

    def _show_result(self, result) -> None:
        if isinstance(result, Exception):  # a file the placing does not follow still opens: its properties and bindings
            self.frame = None
            self.statusBar().showMessage(tr("ui.preview_failed_kind_error", kind=type(result).__name__, error=result))
        else:
            self.frame = result
            self.statusBar().clearMessage()
        self._show_frame()

    def _when_idle(self, action, doc: uidoc.UiDoc | None = None) -> None:
        """Run ``action`` now, or when the running layout is done (it reads the files); ``doc``: the file it is for,
        whose closing drops it (it is made in that file even when another one is shown by then)."""
        if self._placing:
            self._queued.append((doc, action))
        else:
            action()

    def _set_view(self, **values) -> None:
        """Change the preview's switches (``layout.Context`` fields); a running layout keeps the ones it began with."""
        self._view.update(values)
        self._redraw()

    def show_dock(self, key: str) -> None:
        """Show a dock and bring it to the front of its tab group."""
        self.docks[key].show()
        self.docks[key].raise_()

    def _show_frame(self) -> None:
        self.preview.show_frame(self.frame, self.pictures)
        self.preview_panel.refresh_note()  # live values win over the preview settings
        self.preview.select(id(self.selected.node) if self.selected else None)
        self.sample_panel.show_samples(self.samples, self._describe_key)
        self._show_checks()
        if self.compare:
            self.compare.show(self)

    def _live_texts(self) -> dict[str, str]:
        """The label texts the game's live values (or their recording) and an optional comparison panel hold, in
        place of sample values; the panel's win."""
        return {**livevalues.texts(self), **(self.compare.texts(self) if self.compare else {})}

    def _pick(self, el: elements.Element | None) -> None:
        self.selected = el if el is not None and el.kind != "template" else None
        self.preview.select(id(el.node) if self.selected else None)
        node = el.node if el is not None else (self.doc.root if self.doc else None)
        self.props.show_node(node, el.kind if el is not None else "template", self.namer)
        self.props.show_origins(gamedataview.origin_rows(self, self.selected))  # live, recorded, game, placeholder
        self._show_graph()

    def _picked_in_preview(self, node_id: int) -> None:
        el = elements.by_node_id(self.top, node_id)
        if el is not None:
            self.selected = el
            self._fill_elements()
            self._pick(el)

    # names

    def _describe_end(self, e: bindings.End) -> str:
        return self.namer.end(e, self.top, self._own)

    def _describe_key(self, key: str) -> str:
        return self.namer.key(key, self.game.files.title if self.game else str)

    # the graph and the view models

    def _show_graph(self) -> None:
        if self.doc is None:
            return
        graph = bindings.read(self.doc.root)
        guid = None if self.all_bindings.isChecked() or self.selected is None else self.selected.guid
        shown = graph.touching(guid)
        self.graph.empty_text = tr("ui.no_binding_names_element") if guid else tr("ui.file_no_bindings")
        self.graph.show_graph(shown, self._describe_end)
        self.docks["Bindings"].setWindowTitle(
            tr("ui.title.bindings_shown_of_total", shown=len(shown), total=len(graph.bindings))
        )

    def _show_view_models(self) -> None:
        if self.game is None or self.doc is None:
            return
        guess = self.game.models.class_for_scene(self.doc.key) if not uifiles.is_guid(self.doc.key) else None
        reads, seen = [], set()
        for b in bindings.read(self.doc.root).bindings:
            for e in b.sources:
                if e.kind == "path" and e.mode == bindings.SELF and len(e.ids) > 1 and tuple(e.ids) not in seen:
                    seen.add(tuple(e.ids))
                    reads.append((self._describe_end(e), list(e.ids)))
        self.vm.show_models(self.game.models, guess, reads, self.names.names, self.scripts)

    def _name_id(self, uid: int, name: str) -> None:
        self.names.set(uid, name)
        self.names.save()
        self.namer.names.clear()
        self.namer.names.update(self.names.short())
        self._show_doc()

    def _load_scripts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ui.script_list"), "", tr("ui.script_lists_json"))
        if path:
            try:
                self.scripts = viewmodels.ScriptList.read(Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, tr(TITLE), tr("common.path_not_read_error", path=path, error=exc))
                return
            self._show_view_models()

    def _add_readout(self, readout: viewmodels.Readout) -> None:
        kind = (readout.returns or ("number",))[0]
        ptype, code = {"boolean": (0, fields.BOOL), "string": (4, fields.STRING)}.get(kind, (3, fields.FLOAT))
        self._edit(
            lambda d: d.add_property(readout.name, ptype, code, {fields.BOOL: False, fields.STRING: ""}.get(code, 0.0))
        )

    def _fill_toggled(self) -> None:
        fill = self.fill_action.isChecked()
        self._when_idle(lambda: (setattr(self.samples, "fill", fill), self._redraw()))

    def _play_toggled(self) -> None:
        self.preview.set_play_effects(self.play_action.isChecked())

    def _hidden_toggled(self) -> None:
        if self.ctx is not None:
            self._set_view(editor_hidden=self.hidden_action.isChecked())

    def _window_shape(self, aspect: float) -> None:
        if self.ctx is not None:
            self._set_view(window_aspect=aspect)

    def _pick_reference(self, node: dict, field: str, kinds: tuple) -> None:
        if self.game is not None:
            key = pickers.pick(self, self.game.files, kinds, tr("ui.choose_the_field", field=field))
            if key:
                self._edit(lambda d: d.set_field(node, field, key))

    def _show_checks(self) -> None:
        """What the preview could not resolve: widget files and pictures the game does not have, placeholders."""
        self.checks.clear()
        notes = list(self.frame.notes) if self.frame is not None else []
        missing = sorted(self.pictures.missing) if self.pictures is not None else []
        notes += [tr("ui.picture_not_game", name=name) for name in missing]
        notes += (
            [tr("ui.picture_slot_no_icon", name=name) for name in sorted(self.pictures.standins)]
            if self.pictures
            else []
        )
        notes += self.fonts.notes() if self.frame is not None else []  # the game's fonts that did not load, and why
        made = sum(1 for k in self.samples.seen if k in self.samples.generated)
        if made:
            notes.append(tr("ui.values_placeholders_sample_grey", n=made))
        self.checks.addItems(notes or [tr("ui.every_reference_open_file_resolves")])
        self.docks["Checks"].setWindowTitle(tr("ui.title.checks_n", n=len(notes)))

    def _sample_changed(self, key: str, value) -> None:
        def change() -> None:
            self.samples.set(key, value)
            self.samples.save()
            self._redraw()

        self._when_idle(change)

    def _show_states(self) -> None:
        """The timeline's choices (the file's animations, its tweens together) and the sequence editor."""
        anims = animations.read(self.doc.root)
        self.tween_list = tweens.read(self.doc.root, self.game.library, self.game.files.root)
        run = [tweens.Run(self.tween_list)] if self.tween_list else []
        previous = self.states.current()
        self.states.show_states([*anims, *run], bindings.read(self.doc.root), self._describe_end)
        keep = next(
            (i for i, a in enumerate(self.states.anims) if previous is not None and a.guid == previous.guid), None
        )
        if keep is not None:
            self.states.combo.setCurrentIndex(keep + 1)
        self._show_sequence()

    def _show_sequence(self) -> None:
        anim = self.states.current()
        graph = bindings.read(self.doc.root)

        def describe(track) -> str:
            end = graph.ends[track.end] if track.end < len(graph.ends) else None
            return self._describe_end(end) if end is not None else tr("ui.end_number", number=track.end)

        shown = anim if isinstance(anim, animations.Animation) else None
        self.sequence.show_sequence(shown, describe, self.tween_list, self.game.library.enum_named("EaseType"))

    def _animation(self, guid, t: float) -> None:
        if self.ctx is None:
            return
        eases = self.game.library.enum_named("EaseType")
        current = self.states.current()
        if getattr(self.sequence.animation, "guid", None) != (current.guid if current is not None else None):
            self._show_sequence()
        self.sequence.set_time(t)
        self._set_view(animation=(guid, t) if guid and guid != tweens.RUN else None,
                       tweens=tweens.writes(self.tween_list, t, eases) if guid == tweens.RUN else None)  # fmt: skip

    def _design(self, on: bool) -> None:
        if self.ctx is not None:
            self._set_view(design=on)

    def _binding_picked(self, b: bindings.Binding) -> None:
        self.statusBar().showMessage(f"{b.op_name}: {', '.join(self._describe_end(e) for e in b.sources)} -> "
                                     f"{', '.join(self._describe_end(e) for e in b.targets)}")  # fmt: skip

    # edits

    def _edit(self, edit) -> None:
        """Make ``edit(file)`` in the open file, now or when the running layout is done (it reads the file); it reads
        what it changes when it is made, so edits that waited add up."""
        doc = self.doc
        if doc is None:
            return
        if self._placing:
            self.statusBar().showMessage(tr("ui.laying_out_preview_edit_made"))
        self._when_idle(lambda: self._make(edit, doc), doc)

    def _make(self, edit, doc: uidoc.UiDoc) -> None:
        try:
            edit(doc)
        except (KeyError, ValueError, TypeError, IndexError, OverflowError, struct.error) as exc:  # the last two: a
            QMessageBox.warning(self, tr(TITLE), tr("ui.edit_not_made", error=exc))  # number its type does not hold

    def undo(self) -> None:
        self._edit(lambda d: d.undo.undo() is not None and self.statusBar().showMessage(tr("ui.undone")))

    def redo(self) -> None:
        self._edit(lambda d: d.undo.redo() is not None and self.statusBar().showMessage(tr("ui.redone")))

    def _moved(self, node_id: int, dx: float, dy: float) -> None:
        el = elements.by_node_id(self.top, node_id)
        if el is not None:
            self._edit(lambda d: d.move(el.node, _moved_by(fields.get(el.node, "Position"), dx, dy)))

    def _sized(self, node_id: int, fx: float, fy: float) -> None:
        el = elements.by_node_id(self.top, node_id)
        if el is not None:
            self._edit(lambda d: d.set_field(el.node, "Scale", _scaled_by(fields.get(el.node, "Scale"), fx, fy)))

    def duplicate(self) -> None:
        el = self.selected
        if el is not None:
            self._edit(lambda d: d.duplicate(el))

    def remove(self) -> None:
        if self.selected is None:
            return
        el, self.selected = self.selected, None
        self._edit(
            lambda d: self.statusBar().showMessage(tr("ui.removed_bindings_naming", name=el.name, n=d.remove(el)))
        )

    def _add_from_palette(self, what: str, which: str, at: tuple[float, float] | None = None) -> None:
        """A palette entry added: a new instance (at a point of the preview when dropped there), or a component or
        an effect onto the selected element."""
        if self.doc is None or self.top is None or self.doc.kind not in uifiles.EDITABLE:
            return
        parent = self.selected if self.selected is not None and self.selected.kind == "group" else self.top
        if at is not None:
            parent = self.top  # a drop places in the file's own space
        if what == "instance":
            name, ok = QInputDialog.getText(
                self, tr(TITLE), tr("ui.new_kinds_name", kind=which), text=f"New{which.title()}"
            )  # i18n: ok (the default name is a datum: the game's element kind)
            if not ok or not name:
                return
            node = self.doc.new_instance(which, name)
            if which == "container":
                tpl = pickers.pick(self, self.game.files, ("WI",), tr("ui.widget_file_places"))
                if tpl:
                    fields.set_value(node, "Template", tpl)
            if at is not None:
                fields.set_value(node, "Position", (float(at[0]), float(at[1]), 0.0))
            self._edit(lambda d: d.add_instance(parent, node, name))
        elif what in ("Component", "Effect") and self.selected is not None:
            item = self.game.library.items.get(which)
            name = item.name if item else which[:8]
            on = self.selected.node
            self._edit(lambda d: (d.add_component if what == "Component" else d.add_effect)(on, which, name))
        else:
            self.statusBar().showMessage(tr("ui.select_element_first_widget_types"))

    def new_property(self) -> None:
        asked = bindingdialogs.ask_property(self) if self.doc is not None else None
        if asked is not None:
            name, ptype, code, default = asked
            self._edit(lambda d: d.add_property(name, ptype, code, default))

    def new_binding(self) -> None:
        """A binding from a source to a target through an operator, each chosen from what the file offers."""
        asked = bindingdialogs.ask_binding(self, self._own, self.top) if self.doc is not None else None
        if asked is not None:
            source, op_hash, target = asked
            self._edit(lambda d: d.add_binding([source], [target], op_hash))

    def _bind_read(self, path: list[int]) -> None:
        """A field the file reads already, bound to a built-in property of the selected element."""
        if self.selected is None or not self.selected.guid:
            self.statusBar().showMessage(tr("ui.select_element_preview_list_first"))
            return
        target = bindingdialogs.ask_bind_read(self, self.selected)
        if target is not None:
            src = bindings.EndSpec("path", None, tuple(path))
            dst = bindings.EndSpec("path", self.selected.guid, (target.uid,))
            self._edit(lambda d: d.add_binding([src], [dst], operators.ASSIGN))

    # files of the project

    def save(self) -> bool:
        """Keep the open file in the project (``ui-edits/<game>/``), as the mod's build takes it. False (and the
        reason shown) when it could not be written."""
        if self.doc is None or self.game is None:
            return True
        entry = self.game.files.entries.get(self.doc.key)
        path = entry.path if entry else f"{self.game.files.prefix}{self.doc.key}"
        try:
            data = self.doc.to_bytes()
            dest = projectpart.save_file(self.project(), self.game.key, path, data)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return False
        self.game.files.extra[self.doc.key] = data
        self.doc.undo.mark_clean()
        self.doc.origin = "project"
        self._fill_files()
        # deployed=False sends nothing, so there is no answer to wait for
        notice = linkannounce.announce((livelink.SCENES,), self.project().mod_id, [path], deployed=False)
        self.statusBar().showMessage(tr("ui.kept_project_path_notice", path=dest, notice=notice.text))
        if self.mirror is not None:
            self.mirror.kept(self.doc, path, data)  # with Push edits on: into the installed mod, the game reloads
        return True

    def revert(self) -> None:
        """Drop the open file's edits and the project's copy of it (after a running layout: it reads the file)."""
        if self.doc is not None and self.game is not None:
            doc = self.doc
            self._when_idle(lambda: self._revert(doc), doc)

    def _revert(self, doc: uidoc.UiDoc) -> None:
        entry = self.game.files.entries.get(doc.key)
        saved = projectpart.EDITS.saved_files(self.project(), self.game.key).get(entry.path) if entry else None
        key = doc.key
        if saved is not None:
            if (
                QMessageBox.question(self, tr(TITLE), tr("ui.delete_projects_copy", path=saved))
                != QMessageBox.StandardButton.Yes
            ):
                return
            try:
                saved.unlink()
            except OSError as exc:
                QMessageBox.warning(self, tr(TITLE), tr("texts.path_not_written_error", path=saved, error=exc))
                return
        self.game.files.drop_copy(key)
        if self.ctx is not None:
            self.ctx.forget_file(key)  # what the other files placed of the edited tree goes with it
        self.docs.pop(key, None)
        shown = self.doc is doc
        if shown:
            self.doc = None
        if not self.game.tree.exists(entry.path if entry else ""):  # a file of the project only: gone with it
            self.game.files.entries.pop(key, None)
        self._fill_files()
        if shown and key in self.game.files.entries:
            self.open_file(key)
        elif not shown:
            self._redraw()

    def new_widget_file(self) -> None:
        """An empty widget file of the project (its GUID from the project's id and the name given)."""
        if self.game is None:
            return
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("ui.new_widget_files_name_guid"))
        if not ok or not name:
            return
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.project().mod_id}/ui/{name}"))
        data = uidoc.empty_widget_file(self.game.files, guid)
        path = f"{self.game.files.prefix}{guid}"
        try:
            projectpart.save_file(self.project(), self.game.key, path, data)
        except OSError as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return
        self.game.files.lay_over({path: data})
        self.game.files.users.setdefault(guid, []).append(("", name))
        self._fill_files()
        self.open_file(guid)

    def check_references(self) -> None:
        if self.game is None:
            return
        found = projectpart.check(self.project(), self.game.key, self.game.tree.exists, self.game.files.schema)
        QMessageBox.information(
            self,
            tr(TITLE),
            "\n".join(found[:40]) if found else tr("ui.every_file_projects_ui_files"),
        )

    def _save_picture(self) -> None:
        if self.frame is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, tr("ui.save_preview"), "", "PNG (*.png)"
        )  # i18n: ok (a file filter)
        if name:
            canvas.render(self.frame, self.pictures, 1.0, world=self.preview.world).save(name)

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another game opened, the window closed): False when the person
        cancelled or a file could not be kept."""
        dirty = [k for k, d in self.docs.items() if d.dirty]
        if not dirty:
            return True

        def keep_all() -> bool:
            for k in dirty:
                self.doc = self.docs[k]
                if not self.save():
                    return False
            return True

        return widgets.ask_keep(self, tr(TITLE), trn("ui.file_edits_not_kept_project", len(dirty)), keep_all)

    def closeEvent(self, event) -> None:
        if not self.may_leave():
            event.ignore()
            return
        self._forget_states()
        self.world_menu.close()
        if self.mirror is not None:
            self.mirror.close()
        super().closeEvent(event)


def run(game: str | None = None, key: str = "") -> int:
    """The UI editor alone in a window of its own, on the suite's settings."""
    return widgets.run_alone(UiWindow, APP, (1700, 1000), game, key)


def _moved_by(position, dx: float, dy: float) -> tuple[float, float, float]:
    x, y, z = layout.vector(position, (0.0, 0.0, 0.0))
    return (x + dx, y + dy, z)


def _scaled_by(scale, fx: float, fy: float) -> tuple[float, float, float]:
    sx, sy, sz = layout.vector(scale, (1.0, 1.0, 1.0))
    return (sx * fx, sy * fy, sz)
