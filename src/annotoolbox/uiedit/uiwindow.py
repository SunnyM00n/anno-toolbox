"""The UI editor's window (Qt): a game's UI files on the left with the open file's elements, the preview in the
middle, the selected element's properties, the palette, the view models and the sample values around it, the states
and the bindings as a node graph below; edits go on the undo stack and are kept in the open project.

``CUiWindow(settings, settings_path)`` is the suite's UI app; ``run`` starts it alone. ``open_game`` and
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
from ..suite import appfolders, gameopen, gamesync, linkannounce, linkscenes, livelink, project, widgets
from ..suite.background import CBackground
from . import (
    animations,
    baseline,
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
from .pictures import CPictures
from .uiframe import APP, GAMES, ROLE

TITLE = N_("ui.ui_editor")


class _CGame:
    """What an open game gives the editor, read off the window's thread."""

    def __init__(self, key: str, tree, cache: Path | None) -> None:
        self.m_key, self.m_tree = key, tree
        self.m_files = uifiles.CUiFiles(tree, cache)
        self.m_library = library.CLibrary.read(tree)
        self.m_models = viewmodels.CViewModels.read(tree)


class CUiWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.m_settings_path = settings, settings_path
        self.game: _CGame | None = None
        self.m_pictures: CPictures | None = None
        self.m_docs: dict[str, uidoc.CUiDoc] = {}
        self.doc: uidoc.CUiDoc | None = None
        self.m_top: elements.SElement | None = None
        self.m_selected: elements.SElement | None = None
        self.m_scripts: viewmodels.CScriptList | None = None
        self.m_samples, self.m_names = viewmodels.CSamples(), viewmodels.CFieldNames()
        self.m_ctx: layout.CContext | None = None
        self.m_frame: layout.SFrame | None = None
        self.m_background = CBackground(self)
        self._m_placing, self._m_again = False, False  # a layout is running / was asked for while it ran
        self._m_queued: list = []  # (file or None, action) asked for while a layout runs: it reads the files, so they wait
        self._m_view: dict[str, object] = {}  # the preview's switches changed meanwhile, taken over by the next layout
        self.m_sync_layout = False  # place on this thread (tests)
        self._m_forget_states = lambda: None  # removes the scene resolver of the open game
        self._m_own: dict[int, tuple[str, int]] = {}  # the open file's own properties (``library.CNamer.own``)
        self._m_opening: str | None = None  # the file asked for last (an older one decoded later is not shown)
        self.m_world_menu = worldmenu.CWorldMenu(self)  # the scene behind the preview (View menu)
        self.m_mirror = None  # the game mirror once opened (View menu; ``mirror``)
        uiframe.build(self)
        uiframe.menus(self)
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("ui.file_gt_open_game_pick"))

    # games and files

    def project(self) -> project.CProject:
        return project.current(self.settings)

    def open_game(self, key: str, then: str = "", is_waiting: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a file to open."""
        if not gamesync.may_switch(self, key, self.may_leave):  # asks; the suite's other apps follow
            return

        def prepare(tree):
            index = self.settings.index_dir(key) / "ui"
            return _CGame(key, tree, index)

        gameopen.open_installed(
            self,
            self.settings,
            key,
            self.m_background,
            then=lambda game: self._opened(key, then, game),
            prepare=prepare,
            is_waiting=is_waiting,
        )

    def _open_folder(self) -> None:
        if not self.may_leave():
            return
        folder = QFileDialog.getExistingDirectory(self, tr("ui.folder_loose_files_unpacked_game"))
        if folder:
            self.open_folder(Path(folder))

    def open_folder(self, folder: Path, is_waiting: bool = False) -> None:
        """Open a folder of loose 117 files; they are listed and read off the window's thread."""
        self.m_background.run(
            lambda: _CGame("anno117", gametree.loose_tree(folder), None),
            lambda game: self._opened("anno117", "", game),
            is_waiting,
        )

    def _opened(self, key: str, then: str, game) -> None:
        if isinstance(game, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=GAMES.get(key, key), error=game))
            return
        self.game, self.m_docs, self.doc, self._m_opening = game, {}, None, None
        self._m_queued, self._m_view = [], {}  # what waited was for the game before
        self.m_top = self.m_selected = self.m_frame = None  # nothing of the game before stays to be picked or saved
        self.element_tree.clear()
        self.preview.show_frame(None, None)
        self.setWindowTitle(tr(TITLE))
        self.centre.setCurrentWidget(self.preview)
        for name in ("Files", "Properties"):
            self.docks[name].show()
        self._m_forget_states()
        self._m_forget_states = linkscenes.register(game.m_files.scene_states)  # the live link's scene reload
        self.m_pictures = CPictures(game.m_files)
        current_project = self.project()
        game.m_files.lay_over(
            {
                path: saved_file.read_bytes()
                for path, saved_file in projectpart.EDITS.saved_files(current_project, key).items()
            }
        )
        self.m_samples = viewmodels.CSamples(projectpart.sidecar(current_project, key, "samples"))
        self.m_samples.m_fill = self.fill_action.isChecked()
        self.m_names = viewmodels.CFieldNames(projectpart.sidecar(current_project, key, "names"))
        self.m_fonts = fonts.CGameFonts(game.m_files)
        self.m_ctx = layout.CContext(game.m_files, self.m_samples, canvas.measure, m_fonts=self.m_fonts.lookup)
        self.m_ctx.m_editor_hidden = self.hidden_action.isChecked()
        self.m_ctx.m_baseline = self._baseline()
        previewdefaults.attach(self, key)  # what the preview assumes of the player's game (``preview_store``)
        gamedataview.game_opened(self, game)  # its languages; the game's data, built in the background
        self.m_namer = library.CNamer(game.m_library, game.m_files.root, self.m_names.short())
        self.palette_panel.show_library(game.m_library)
        self.settings.apps.setdefault(APP, {})["game"] = key
        self._fill_files()
        file_count = len(game.m_files.of_kind(*uifiles.EDITABLE))
        name = GAMES.get(key, key)
        self.statusBar().showMessage(tr("ui.scenes_widget_files_reading_names", game=name, n=file_count))

        def scanned(_result) -> None:
            if game is self.game:  # another game may be open by now
                self._fill_files()
                self.statusBar().showMessage(tr("ui.scenes_widget_files", game=name, n=file_count))

        self.m_background.run(game.m_files.scan_users, scanned)
        if unread := [kept.m_error for kept in (self.m_samples, self.m_names) if kept.m_error]:
            QMessageBox.warning(self, tr(TITLE), "\n".join(unread))  # they start empty and are not written over
        self.m_world_menu.game_opened()
        if then:
            self.open_file(then)

    def _fill_files(self) -> None:
        self.file_list.clear()
        if self.game is None:
            return
        saved = set(appfolders.edited(self.project(), projectpart.EDITS, self.game.m_key))  # not the imported base
        uiframe.fill_files(self.file_list, self.game.m_files, self.file_filter.text(), saved)

    def open_file(self, key: str) -> None:
        """Open a UI file of the game (the project's copy where it has one) and show it; after the edits that wait for
        a layout, so that they are made in the file they were made in. A file not open yet is decoded off the window's
        thread; the one asked for last is shown."""
        if self._m_placing and self._m_queued:
            self._m_queued.append((None, lambda: self.open_file(key)))
            return
        if self.game is None or key not in self.game.m_files.m_entries:
            self.statusBar().showMessage(tr("ui.no_such_ui_file", key=key))
            return
        self._m_opening = key
        if key in self.m_docs:
            self._show_open(key)
            return
        files, game = self.game.m_files, self.game
        origin = "project" if key in files.m_extra else "game"

        def decode():
            doc = uidoc.CUiDoc(files, key, files.read(key), origin)
            files.names_of(doc.m_root)  # the History Editions' names: an editor document read once
            return doc

        self.statusBar().showMessage(tr("ui.opening_name", name=files.title(key)))
        self.m_background.run(decode, lambda doc: self._decoded(game, key, doc), self.m_sync_layout)

    def _decoded(self, game: _CGame, key: str, doc) -> None:
        """A file decoded off the window's thread: shown unless another file or game was asked for meanwhile. It goes
        live once no layout runs (the layout reads the files)."""
        if game is not self.game or self._m_opening != key:
            return
        if isinstance(doc, Exception):
            QMessageBox.warning(self, tr(TITLE), tr("ui.key_not_decode_error", key=key, error=doc))
            return
        self._when_idle(lambda: self._went_live(game, key, doc))

    def _went_live(self, game: _CGame, key: str, doc: uidoc.CUiDoc) -> None:
        if game is not self.game or self._m_opening != key:
            return
        if key not in self.m_docs:
            doc.m_listeners.append(lambda changed=doc: self._doc_changed(changed))
            self.m_docs[key] = doc
            game.m_files.m_live[key] = doc.m_root  # the preview of other files places this tree, edits and all
            if self.m_ctx is not None:
                self.m_ctx.forget_file(key)  # what they placed of the game's tree must follow the edits too
        self._show_open(key)

    def _show_open(self, key: str) -> None:
        """Show an open file in every panel."""
        self.doc = self.m_docs[key]
        self.m_selected = None
        self.preview.m_fit = True
        self._show_doc()
        exact = "" if self.doc.m_exact else " - " + tr("ui.file_not_re_encode_byte")
        origin = tr("ui.project") if self.doc.m_origin == "project" else tr("ui.game")
        self.statusBar().showMessage(f"{self.game.m_files.title(key)} ({origin}){exact}")

    # showing the open file

    def _doc_changed(self, doc: uidoc.CUiDoc) -> None:
        """An edit (or its undo) changed ``doc``: what was kept of it is out of date, the other files' stays."""
        if self.m_ctx is not None:
            self.m_ctx.forget(doc.m_root)
        if self.m_mirror is not None:
            self.m_mirror.edited(doc)  # the game mirror's scenes that show it, and its push
        if doc is self.doc:
            self._show_doc()
        else:
            self._redraw()  # the open file may place it

    def _show_doc(self) -> None:
        """The open file in every panel, and its preview laid out again."""
        if self.doc is None:
            return
        selected_node = self.m_selected.node if self.m_selected else None
        names = self.game.m_files.names_of(
            self.doc.m_root
        )  # the History Editions' names live in the editor's documents
        self.m_top, self._m_own = elements.tree(self.doc.m_root, names), library.CNamer.own(self.doc.m_root)
        self.m_selected = elements.find(self.m_top, selected_node) if selected_node is not None else None
        self._fill_elements()
        self._redraw()
        self.props.show_node(
            self.m_selected.node if self.m_selected else self.doc.m_root,
            self.m_selected.kind if self.m_selected else "template",
            self.m_namer,
        )
        self._show_graph()
        self._show_states()
        self._show_view_models()
        self.undo_action.setEnabled(bool(self.doc.m_undo.m_index))
        self.redo_action.setEnabled(self.doc.m_undo.m_index < len(self.doc.m_undo.m_commands))
        mark = " *" if self.doc.dirty else ""
        self.setWindowTitle(f"{self.game.m_files.title(self.doc.m_key)}{mark} - {tr(TITLE)}")

    def _fill_elements(self) -> None:
        self.element_tree.blockSignals(True)
        self.element_tree.clear()

        def add(parent, element: elements.SElement) -> None:
            item = QTreeWidgetItem(
                parent,
                [
                    element.name,
                    element.kind
                    + (
                        f" -> {self.game.m_files.title(element.template)}"
                        if element.kind == "container" and element.template
                        else ""
                    ),
                ],
            )
            item.setData(0, ROLE, element)
            if not fields.get(element.node, "Visible", True):
                item.setForeground(0, self.palette().placeholderText())
            if element is self.m_selected:
                self.element_tree.setCurrentItem(item)
            for child in element.children:
                add(item, child)

        add(self.element_tree, self.m_top)
        self.element_tree.expandToDepth(1)
        self.element_tree.blockSignals(False)

    def _redraw(self) -> None:
        """Place the open file again (off the window's thread; one layout runs at a time, the newest request wins)."""
        if self.doc is None or self.m_ctx is None:
            return
        if self._m_placing:
            self._m_again = True
            return
        doc, ctx = self.doc, self.m_ctx
        ctx.m_live_text, ctx.m_live_values = self._live_texts(), livevalues.overrides(self)
        gamedataview.before_layout(self)  # the scene and the language of the game's texts
        for name, value in self._m_view.items():
            setattr(ctx, f"m_{name}", value)  # the context fields
        self._m_view.clear()
        self._m_placing, self._m_again = True, False
        self.statusBar().showMessage(tr("ui.laying_out_preview"))
        self.m_background.run(
            lambda: layout.place(doc.m_key, doc.m_root, ctx),
            lambda result: self._placed(doc, result),
            self.m_sync_layout,
        )

    def _placed(self, doc: uidoc.CUiDoc, result) -> None:
        """The layout of ``doc`` is done: show it unless the window moved on; run what was asked meanwhile, in order
        (an action that lays out again makes the ones after it wait for that layout)."""
        self._m_placing = False
        queued, self._m_queued = self._m_queued, []
        if doc is self.doc:
            self._show_result(result)
        lost = 0
        for owner, action in queued:
            if owner is None or owner in self.m_docs.values():
                self._when_idle(action, owner)
            else:
                lost += 1
        if lost:
            self.statusBar().showMessage(trn("ui.edit_not_made_file_closed", lost))  # fmt: skip
        if (self._m_again or doc is not self.doc) and not self._m_placing:
            self._redraw()

    def _show_result(self, result) -> None:
        if isinstance(result, Exception):  # a file the placing does not follow still opens: its properties and bindings
            self.m_frame = None
            self.statusBar().showMessage(tr("ui.preview_failed_kind_error", kind=type(result).__name__, error=result))
        else:
            self.m_frame = result
            self.statusBar().clearMessage()
        self._show_frame()

    def _when_idle(self, action, doc: uidoc.CUiDoc | None = None) -> None:
        """Run ``action`` now, or when the running layout is done (it reads the files); ``doc``: the file it is for,
        whose closing drops it (it is made in that file even when another one is shown by then)."""
        if self._m_placing:
            self._m_queued.append((doc, action))
        else:
            action()

    def _set_view(self, **values) -> None:
        """Change the preview's switches (``CContext`` fields without ``m_``); a running layout keeps the ones it began with."""
        self._m_view.update(values)
        self._redraw()

    def show_dock(self, key: str) -> None:
        """Show a dock and bring it to the front of its tab group."""
        self.docks[key].show()
        self.docks[key].raise_()

    def _show_frame(self) -> None:
        self.preview.show_frame(self.m_frame, self.m_pictures)
        self.preview_panel.refresh_note()  # live values win over the preview settings
        self.preview.select(id(self.m_selected.node) if self.m_selected else None)
        self.sample_panel.show_samples(self.m_samples, self._describe_key)
        self._show_checks()
        if self.compare:
            self.compare.show(self)

    def _live_texts(self) -> dict[str, str]:
        """The label texts the game's live values (or their recording) and an optional comparison panel hold, in
        place of sample values; the panel's win."""
        return {**livevalues.texts(self), **(self.compare.texts(self) if self.compare else {})}

    def _pick(self, element: elements.SElement | None) -> None:
        self.m_selected = element if element is not None and element.kind != "template" else None
        self.preview.select(id(element.node) if self.m_selected else None)
        node = element.node if element is not None else (self.doc.m_root if self.doc else None)
        self.props.show_node(node, element.kind if element is not None else "template", self.m_namer)
        self.props.show_origins(gamedataview.origin_rows(self, self.m_selected))  # live, recorded, game, placeholder
        self._show_graph()

    def _picked_in_preview(self, node_id: int) -> None:
        element = elements.by_node_id(self.m_top, node_id)
        if element is not None:
            self.m_selected = element
            self._fill_elements()
            self._pick(element)

    # names

    def _describe_end(self, end: bindings.SEnd) -> str:
        return self.m_namer.end(end, self.m_top, self._m_own)

    def _describe_key(self, key: str) -> str:
        return self.m_namer.key(key, self.game.m_files.title if self.game else str)

    # the graph and the view models

    def _show_graph(self) -> None:
        if self.doc is None:
            return
        graph = bindings.read(self.doc.m_root)
        guid = None if self.all_bindings.isChecked() or self.m_selected is None else self.m_selected.guid
        shown = graph.touching(guid)
        self.graph.m_empty_text = tr("ui.no_binding_names_element") if guid else tr("ui.file_no_bindings")
        self.graph.show_graph(shown, self._describe_end)
        self.docks["Bindings"].setWindowTitle(
            tr("ui.title.bindings_shown_of_total", shown=len(shown), total=len(graph.bindings))
        )

    def _show_view_models(self) -> None:
        if self.game is None or self.doc is None:
            return
        guess = self.game.m_models.class_for_scene(self.doc.m_key) if not uifiles.is_guid(self.doc.m_key) else None
        reads, seen = [], set()
        for binding in bindings.read(self.doc.m_root).bindings:
            for end in binding.sources:
                if end.kind == "path" and end.mode == bindings.SELF and len(end.ids) > 1 and tuple(end.ids) not in seen:
                    seen.add(tuple(end.ids))
                    reads.append((self._describe_end(end), list(end.ids)))
        self.vm.show_models(self.game.m_models, guess, reads, self.m_names.m_names, self.m_scripts)

    def _name_id(self, uid: int, name: str) -> None:
        self.m_names.set(uid, name)
        self.m_names.save()
        self.m_namer.m_names.clear()
        self.m_namer.m_names.update(self.m_names.short())
        self._show_doc()

    def _load_scripts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ui.script_list"), "", tr("ui.script_lists_json"))
        if path:
            try:
                self.m_scripts = viewmodels.CScriptList.read(Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, tr(TITLE), tr("common.path_not_read_error", path=path, error=exc))
                return
            self._show_view_models()

    def _add_readout(self, readout: viewmodels.SReadout) -> None:
        kind = (readout.returns or ("number",))[0]
        property_type, code = {"boolean": (0, fields.BOOL), "string": (4, fields.STRING)}.get(kind, (3, fields.FLOAT))
        self._edit(
            lambda doc: doc.add_property(
                readout.name, property_type, code, {fields.BOOL: False, fields.STRING: ""}.get(code, 0.0)
            )
        )

    def _fill_toggled(self) -> None:
        fill = self.fill_action.isChecked()
        self._when_idle(lambda: (setattr(self.m_samples, "m_fill", fill), self._redraw()))

    def _baseline(self) -> baseline.CBaseline | None:
        """The game's baseline over the open game's files (117's only; None with the View switch off)."""
        found = baseline.shipped() if self.game is not None and self.game.m_key == "anno117" else None
        return found.for_install(self.game.m_files) if found and self.baseline_action.isChecked() else None

    def _baseline_toggled(self) -> None:
        if self.m_ctx is not None:
            self._set_view(baseline=self._baseline())

    def _play_toggled(self) -> None:
        self.preview.set_play_effects(self.play_action.isChecked())

    def _hidden_toggled(self) -> None:
        if self.m_ctx is not None:
            self._set_view(editor_hidden=self.hidden_action.isChecked())

    def _window_shape(self, aspect: float) -> None:
        if self.m_ctx is not None:
            self._set_view(window_aspect=aspect)

    def _pick_reference(self, node: dict, field: str, kinds: tuple) -> None:
        if self.game is not None:
            key = pickers.pick(self, self.game.m_files, kinds, tr("ui.choose_the_field", field=field))
            if key:
                self._edit(lambda doc: doc.set_field(node, field, key))

    def _show_checks(self) -> None:
        """What the preview could not resolve: widget files and pictures the game does not have, placeholders."""
        self.checks.clear()
        notes = list(self.m_frame.notes) if self.m_frame is not None else []
        missing = sorted(self.m_pictures.m_missing) if self.m_pictures is not None else []
        notes += [tr("ui.picture_not_game", name=name) for name in missing]
        notes += (
            [tr("ui.picture_slot_no_icon", name=name) for name in sorted(self.m_pictures.m_standins)]
            if self.m_pictures
            else []
        )
        notes += self.m_fonts.notes() if self.m_frame is not None else []  # the game's fonts that did not load, and why
        made = sum(1 for key in self.m_samples.m_seen if key in self.m_samples.m_generated)
        if made:
            notes.append(tr("ui.values_placeholders_sample_grey", n=made))
        self.checks.addItems(notes or [tr("ui.every_reference_open_file_resolves")])
        self.docks["Checks"].setWindowTitle(tr("ui.title.checks_n", n=len(notes)))

    def _sample_changed(self, key: str, value) -> None:
        def change() -> None:
            self.m_samples.set(key, value)
            self.m_samples.save()
            self._redraw()

        self._when_idle(change)

    def _show_states(self) -> None:
        """The timeline's choices (the file's animations, its tweens together) and the sequence editor."""
        file_animations = animations.read(self.doc.m_root)
        self.m_tween_list = tweens.read(self.doc.m_root, self.game.m_library, self.game.m_files.root)
        run = [tweens.SRun(self.m_tween_list)] if self.m_tween_list else []
        previous = self.states.current()
        self.states.show_states([*file_animations, *run], bindings.read(self.doc.m_root), self._describe_end)
        keep = next(
            (
                i
                for i, animation in enumerate(self.states.m_anims)
                if previous is not None and animation.guid == previous.guid
            ),
            None,
        )
        if keep is not None:
            self.states.m_combo.setCurrentIndex(keep + 1)
        self._show_sequence()

    def _show_sequence(self) -> None:
        animation = self.states.current()
        graph = bindings.read(self.doc.m_root)

        def describe(track) -> str:
            end = graph.ends[track.end] if track.end < len(graph.ends) else None
            return self._describe_end(end) if end is not None else tr("ui.end_number", number=track.end)

        shown = animation if isinstance(animation, animations.SAnimation) else None
        self.sequence.show_sequence(shown, describe, self.m_tween_list, self.game.m_library.enum_named("EaseType"))

    def _animation(self, guid, time_s: float) -> None:
        if self.m_ctx is None:
            return
        eases = self.game.m_library.enum_named("EaseType")
        current = self.states.current()
        if getattr(self.sequence.m_animation, "guid", None) != (current.guid if current is not None else None):
            self._show_sequence()
        self.sequence.set_time(time_s)
        self._set_view(animation=(guid, time_s) if guid and guid != tweens.RUN else None,
                       tweens=tweens.writes(self.m_tween_list, time_s, eases) if guid == tweens.RUN else None)  # fmt: skip

    def _design(self, is_on: bool) -> None:
        if self.m_ctx is not None:
            self._set_view(design=is_on)

    def _binding_picked(self, binding: bindings.SBinding) -> None:
        self.statusBar().showMessage(f"{binding.op_name}: {', '.join(self._describe_end(end) for end in binding.sources)} -> "
                                     f"{', '.join(self._describe_end(end) for end in binding.targets)}")  # fmt: skip

    # edits

    def _edit(self, edit) -> None:
        """Make ``edit(file)`` in the open file, now or when the running layout is done (it reads the file); it reads
        what it changes when it is made, so edits that waited add up."""
        doc = self.doc
        if doc is None:
            return
        if self._m_placing:
            self.statusBar().showMessage(tr("ui.laying_out_preview_edit_made"))
        self._when_idle(lambda: self._make(edit, doc), doc)

    def _make(self, edit, doc: uidoc.CUiDoc) -> None:
        try:
            edit(doc)
        except (KeyError, ValueError, TypeError, IndexError, OverflowError, struct.error) as exc:  # the last two: a
            QMessageBox.warning(self, tr(TITLE), tr("ui.edit_not_made", error=exc))  # number its type does not hold

    def undo(self) -> None:
        self._edit(lambda doc: doc.m_undo.undo() is not None and self.statusBar().showMessage(tr("ui.undone")))

    def redo(self) -> None:
        self._edit(lambda doc: doc.m_undo.redo() is not None and self.statusBar().showMessage(tr("ui.redone")))

    def _moved(self, node_id: int, delta_x: float, delta_y: float) -> None:
        element = elements.by_node_id(self.m_top, node_id)
        if element is not None:
            self._edit(
                lambda doc: doc.move(element.node, _moved_by(fields.get(element.node, "Position"), delta_x, delta_y))
            )

    def _sized(self, node_id: int, factor_x: float, factor_y: float) -> None:
        element = elements.by_node_id(self.m_top, node_id)
        if element is not None:
            self._edit(
                lambda doc: doc.set_field(
                    element.node, "Scale", _scaled_by(fields.get(element.node, "Scale"), factor_x, factor_y)
                )
            )

    def duplicate(self) -> None:
        element = self.m_selected
        if element is not None:
            self._edit(lambda doc: doc.duplicate(element))

    def remove(self) -> None:
        if self.m_selected is None:
            return
        element, self.m_selected = self.m_selected, None
        self._edit(
            lambda doc: self.statusBar().showMessage(
                tr("ui.removed_bindings_naming", name=element.name, n=doc.remove(element))
            )
        )

    def _add_from_palette(self, what: str, which: str, drop_point: tuple[float, float] | None = None) -> None:
        """A palette entry added: a new instance (at a point of the preview when dropped there), or a component or
        an effect onto the selected element."""
        if self.doc is None or self.m_top is None or self.doc.kind not in uifiles.EDITABLE:
            return
        parent = self.m_selected if self.m_selected is not None and self.m_selected.kind == "group" else self.m_top
        if drop_point is not None:
            parent = self.m_top  # a drop places in the file's own space
        if what == "instance":
            name, ok = QInputDialog.getText(
                self, tr(TITLE), tr("ui.new_kinds_name", kind=which), text=f"New{which.title()}"
            )  # i18n: ok (the default name is a datum: the game's element kind)
            if not ok or not name:
                return
            node = self.doc.new_instance(which, name)
            if which == "container":
                template = pickers.pick(self, self.game.m_files, ("WI",), tr("ui.widget_file_places"))
                if template:
                    fields.set_value(node, "Template", template)
            if drop_point is not None:
                fields.set_value(node, "Position", (float(drop_point[0]), float(drop_point[1]), 0.0))
            self._edit(lambda doc: doc.add_instance(parent, node, name))
        elif what in ("Component", "Effect") and self.m_selected is not None:
            item = self.game.m_library.m_items.get(which)
            name = item.name if item else which[:8]
            selected_node = self.m_selected.node
            self._edit(
                lambda doc: (doc.add_component if what == "Component" else doc.add_effect)(selected_node, which, name)
            )
        else:
            self.statusBar().showMessage(tr("ui.select_element_first_widget_types"))

    def new_property(self) -> None:
        asked = bindingdialogs.ask_property(self) if self.doc is not None else None
        if asked is not None:
            name, property_type, code, default = asked
            self._edit(lambda doc: doc.add_property(name, property_type, code, default))

    def new_binding(self) -> None:
        """A binding from a source to a target through an operator, each chosen from what the file offers."""
        asked = bindingdialogs.ask_binding(self, self._m_own, self.m_top) if self.doc is not None else None
        if asked is not None:
            source, op_hash, target = asked
            self._edit(lambda doc: doc.add_binding([source], [target], op_hash))

    def _bind_read(self, path: list[int]) -> None:
        """A field the file reads already, bound to a built-in property of the selected element."""
        if self.m_selected is None or not self.m_selected.guid:
            self.statusBar().showMessage(tr("ui.select_element_preview_list_first"))
            return
        target = bindingdialogs.ask_bind_read(self, self.m_selected)
        if target is not None:
            source_end = bindings.SEndSpec("path", None, tuple(path))
            target_end = bindings.SEndSpec("path", self.m_selected.guid, (target.uid,))
            self._edit(lambda doc: doc.add_binding([source_end], [target_end], operators.ASSIGN))

    # files of the project

    def save(self) -> bool:
        """Keep the open file in the project (``ui-edits/<game>/``), as the mod's build takes it. False (and the
        reason shown) when it could not be written."""
        if self.doc is None or self.game is None:
            return True
        entry = self.game.m_files.m_entries.get(self.doc.m_key)
        path = entry.path if entry else f"{self.game.m_files.m_prefix}{self.doc.m_key}"
        try:
            data = self.doc.to_bytes()
            destination = projectpart.save_file(self.project(), self.game.m_key, path, data)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return False
        self.game.m_files.m_extra[self.doc.m_key] = data
        self.doc.m_undo.mark_clean()
        self.doc.m_origin = "project"
        self._fill_files()
        # deployed=False sends nothing, so there is no answer to wait for
        notice = linkannounce.announce((livelink.SCENES,), self.project().mod_id, [path], is_deployed=False)
        self.statusBar().showMessage(tr("ui.kept_project_path_notice", path=destination, notice=notice.text))
        if self.m_mirror is not None:
            self.m_mirror.kept(self.doc, path, data)  # with Push edits on: into the installed mod, the game reloads
        return True

    def revert(self) -> None:
        """Drop the open file's edits and the project's copy of it (after a running layout: it reads the file)."""
        if self.doc is not None and self.game is not None:
            doc = self.doc
            self._when_idle(lambda: self._revert(doc), doc)

    def _revert(self, doc: uidoc.CUiDoc) -> None:
        entry = self.game.m_files.m_entries.get(doc.m_key)
        saved = appfolders.edited(self.project(), projectpart.EDITS, self.game.m_key).get(entry.path) if entry else None
        key = doc.m_key
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
        self.game.m_files.drop_copy(key)
        if self.m_ctx is not None:
            self.m_ctx.forget_file(key)  # what the other files placed of the edited tree goes with it
        self.m_docs.pop(key, None)
        shown = self.doc is doc
        if shown:
            self.doc = None
        if not self.game.m_tree.exists(entry.path if entry else ""):  # a file of the project only: gone with it
            self.game.m_files.m_entries.pop(key, None)
        self._fill_files()
        if shown and key in self.game.m_files.m_entries:
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
        data = uidoc.empty_widget_file(self.game.m_files, guid)
        path = f"{self.game.m_files.m_prefix}{guid}"
        try:
            projectpart.save_file(self.project(), self.game.m_key, path, data)
        except OSError as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return
        self.game.m_files.lay_over({path: data})
        self.game.m_files.m_users.setdefault(guid, []).append(("", name))
        self._fill_files()
        self.open_file(guid)

    def check_references(self) -> None:
        if self.game is None:
            return
        found = projectpart.check(self.project(), self.game.m_key, self.game.m_tree.exists, self.game.m_files.m_schema)
        shown = "\n".join(found[:40]) if found else tr("ui.every_file_projects_ui_files")
        QMessageBox.information(self, tr(TITLE), shown)

    def _save_picture(self) -> None:
        if self.m_frame is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("ui.save_preview"), "", "PNG (*.png)")  # i18n: ok (a filter)
        if name:
            canvas.render(self.m_frame, self.m_pictures, 1.0, world=self.preview.m_world).save(name)

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another game opened, the window closed): False when the person
        cancelled or a file could not be kept."""
        dirty = [key for key, doc in self.m_docs.items() if doc.dirty]
        if not dirty:
            return True

        def keep_all() -> bool:
            shown = self.doc  # ``save`` keeps the open file: the panels must go on showing the one they show
            try:
                for key in dirty:
                    self.doc = self.m_docs[key]
                    if not self.save():
                        return False
                return True
            finally:
                self.doc = shown

        return widgets.ask_keep(self, tr(TITLE), trn("ui.file_edits_not_kept_project", len(dirty)), keep_all)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about unkept edits, nothing released."""
        return self.may_leave()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        self._m_forget_states()
        self.m_world_menu.close()
        if self.m_mirror is not None:
            self.m_mirror.close()
        super().closeEvent(event)


def run(game: str | None = None, key: str = "") -> int:
    """The UI editor alone in a window of its own, on the suite's settings."""
    return widgets.run_alone(CUiWindow, APP, (1700, 1000), game, key)


def _moved_by(position, delta_x: float, delta_y: float) -> tuple[float, float, float]:
    x, y, z = layout.vector(position, (0.0, 0.0, 0.0))
    return (x + delta_x, y + delta_y, z)


def _scaled_by(scale, factor_x: float, factor_y: float) -> tuple[float, float, float]:
    scale_x, scale_y, scale_z = layout.vector(scale, (1.0, 1.0, 1.0))
    return (scale_x * factor_x, scale_y * factor_y, scale_z)
