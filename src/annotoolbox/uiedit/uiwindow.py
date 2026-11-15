"""The UI editor's window (Qt): a game's UI files on the left with the open file's elements, the preview in the
middle, the selected element's properties, the palette, the view models and the sample values around it, the states
and the bindings as a node graph below; edits go on the undo stack and are kept in the open project.

``UiWindow(settings, settings_path)`` is the suite's UI app; ``run`` starts it alone. ``open_game`` and
``open_file`` are the entry points other code calls. docs/ui-editor.md is the user's guide.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QTreeWidgetItem,
)

from ..guiold.view import OldGuiPage
from ..infra.i18n import N_, tr, trn
from ..sources import gametree
from ..suite import gameopen, linkannounce, linkscenes, livelink, project, widgets
from ..suite.background import Background
from . import (
    animations,
    bindingdialogs,
    bindings,
    canvas,
    elements,
    fields,
    fonts,
    gamedump,
    layout,
    library,
    operators,
    pickers,
    projectpart,
    tweens,
    uidoc,
    uifiles,
    uiframe,
    viewmodels,
    worldmenu,
)
from .pictures import Pictures
from .uiframe import APP, GAMES, OLD_GAMES, ROLE

TITLE = N_("UI editor")


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
        self.old_page = None  # the old interface's page (guiold.view) once one is open
        self._placing, self._again = False, False  # a layout is running / was asked for while it ran
        self._queued: list = []  # (file or None, action) asked for while a layout runs: it reads the files, so they wait
        self._view: dict[str, object] = {}  # the preview's switches changed meanwhile, taken over by the next layout
        self.sync_layout = False  # place on this thread (tests)
        self._forget_states = lambda: None  # removes the scene resolver of the open game
        self._own: dict[int, tuple[str, int]] = {}  # the open file's own properties (``library.Namer.own``)
        self._opening: str | None = None  # the file asked for last (an older one decoded later is not shown)
        self.world_menu = worldmenu.WorldMenu(self)  # the scene behind the preview (View menu)
        uiframe.build(self)
        uiframe.menus(self)
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("File > Open game, then pick a scene or a widget file on the left."))

    # games and files

    def project(self) -> project.Project:
        return project.current(self.settings)

    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a file to open."""

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

    def open_old(self, key: str, wait: bool = False) -> None:
        """Open a game's old interface (dialogs, templates, backgrounds) read only, in the background."""
        gameopen.open_installed(
            self, self.settings, key, self.background, then=lambda tree: self._opened_old(key, tree), wait=wait
        )

    def _opened_old(self, key: str, tree) -> None:
        if isinstance(tree, Exception):
            self.statusBar().showMessage(tr("{game} did not open: {error}", game=OLD_GAMES.get(key, key), error=tree))
            return
        if self.old_page is not None:
            self.centre.removeWidget(self.old_page)
        self.old_page = OldGuiPage(tree)
        self.centre.addWidget(self.old_page)
        self.centre.setCurrentWidget(self.old_page)
        for dock in self.docks.values():
            dock.hide()
        self.setWindowTitle(f"{OLD_GAMES.get(key, key)} - {tr(TITLE)}")
        self.statusBar().showMessage(tr("{game}: the old interface, read only", game=OLD_GAMES.get(key, key)))

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("A folder of loose files (an unpacked game or a mod)"))
        if folder:
            self._opened("anno117", "", _Game("anno117", gametree.loose_tree(Path(folder)), None))

    def _opened(self, key: str, then: str, game) -> None:
        if isinstance(game, Exception):
            self.statusBar().showMessage(tr("{game} did not open: {error}", game=GAMES.get(key, key), error=game))
            return
        self.game, self.docs, self.doc, self._opening = game, {}, None, None
        self._queued, self._view = [], {}  # what waited was for the game before
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
        self.namer = library.Namer(game.library, game.files.root, self.names.short())
        self.palette_panel.show_library(game.library)
        self.settings.apps.setdefault(APP, {})["game"] = key
        self._fill_files()
        n = len(game.files.of_kind(*uifiles.EDITABLE))
        name = GAMES.get(key, key)
        self.statusBar().showMessage(
            tr("{game}: {n} scenes and widget files; reading who names them...", game=name, n=n)
        )
        self.background.run(game.files.scan_users, lambda _r: (self._fill_files(), self.statusBar().showMessage(
            tr("{game}: {n} scenes and widget files", game=name, n=n))))  # fmt: skip
        self.world_menu.game_opened()
        if then:
            self.open_file(then)

    def _fill_files(self) -> None:
        self.file_list.clear()
        if self.game is None:
            return
        files, words = self.game.files, self.file_filter.text().lower().split()
        saved = set(projectpart.EDITS.saved_files(self.project(), self.game.key))
        groups = {"SC": QTreeWidgetItem(self.file_list, [tr("Scenes"), ""]), "WI": QTreeWidgetItem(self.file_list, [tr("Widget files"), ""]),
                  "": QTreeWidgetItem(self.file_list, [tr("Other files (properties only)"), ""])}  # fmt: skip
        for entry in sorted(files.entries.values(), key=lambda e: (files.title(e.key).lower(), e.key)):
            title = files.title(entry.key)
            if words and not all(w in f"{title} {entry.key}".lower() for w in words):
                continue
            group = groups.get(entry.kind, groups[""])
            if group is groups[""] and not words:
                continue
            mark = " *" if entry.path in saved else ""
            kind = uifiles.KIND_NAMES.get(entry.kind)
            it = QTreeWidgetItem(group, [title + mark, tr(kind) if kind else entry.kind])
            it.setToolTip(0, entry.path)
            it.setData(0, ROLE, entry.key)
        for g in groups.values():
            g.setText(0, f"{g.text(0)} ({g.childCount()})")
        groups["SC"].setExpanded(True)

    def open_file(self, key: str) -> None:
        """Open a UI file of the game (the project's copy where it has one) and show it; after the edits that wait for
        a layout, so that they are made in the file they were made in. A file not open yet is decoded off the window's
        thread; the one asked for last is shown."""
        if self._placing and self._queued:
            self._queued.append((None, lambda: self.open_file(key)))
            return
        if self.game is None or key not in self.game.files.entries:
            self.statusBar().showMessage(tr("no such UI file: {key}", key=key))
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

        self.statusBar().showMessage(tr("opening {name} ...", name=files.title(key)))
        self.background.run(decode, lambda doc: self._decoded(game, key, doc), self.sync_layout)

    def _decoded(self, game: _Game, key: str, doc) -> None:
        """A file decoded off the window's thread: shown unless another file or game was asked for meanwhile. It goes
        live once no layout runs (the layout reads the files)."""
        if game is not self.game or self._opening != key:
            return
        if isinstance(doc, Exception):
            QMessageBox.warning(self, tr(TITLE), tr("{key} does not decode: {error}", key=key, error=doc))
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
        exact = (
            ""
            if self.doc.exact
            else " - " + tr("this file does not re-encode byte for byte: a save writes it through the codec")
        )
        origin = tr("project") if self.doc.origin == "project" else tr("game")
        self.statusBar().showMessage(f"{self.game.files.title(key)} ({origin}){exact}")

    # showing the open file

    def _doc_changed(self, doc: uidoc.UiDoc) -> None:
        """An edit (or its undo) changed ``doc``: what was kept of it is out of date, the other files' stays."""
        if self.ctx is not None:
            self.ctx.forget(doc.root)
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
        ctx.live_text = self._live_texts()
        for name, value in self._view.items():
            setattr(ctx, name, value)
        self._view.clear()
        self._placing, self._again = True, False
        self.statusBar().showMessage(tr("laying out the preview ..."))
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
            self.statusBar().showMessage(trn("{n} edit was not made: its file is closed",
                                             "{n} edits were not made: their file is closed", lost))  # fmt: skip
        if (self._again or doc is not self.doc) and not self._placing:
            self._redraw()

    def _show_result(self, result) -> None:
        if isinstance(result, Exception):  # a file the placing does not follow still opens: its properties and bindings
            self.frame = None
            self.statusBar().showMessage(
                tr("the preview failed: {kind}: {error}", kind=type(result).__name__, error=result)
            )
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

    def _show_frame(self) -> None:
        self.preview.show_frame(self.frame, self.pictures)
        self.preview.select(id(self.selected.node) if self.selected else None)
        self.sample_panel.show_samples(self.samples, self._describe_key)
        self._show_checks()
        self._show_dump()

    def _live_texts(self) -> dict[str, str]:
        """U8: the texts the running game's labels show, from the held dump, when the view asks for them."""
        dump = self.dump_panel.dump
        if dump is None or self.top is None or self.doc is None or not self.live_action.isChecked():
            return {}
        return gamedump.live_text(dump, self.top.guid, self.doc.key)

    def _show_dump(self) -> None:
        """The held dump against the preview: the game's rectangles over the file, the differences listed."""
        if self.dump_panel.dump is None or self.top is None or self.doc is None:
            return
        c = gamedump.compare(
            self.dump_panel.dump, self.top, self.frame, self.dump_panel.tolerance.value(), self.doc.key
        )
        self.preview.overlay = c.overlay
        self.preview.update()
        self.dump_panel.show_comparison(c)

    def _pick(self, el: elements.Element | None) -> None:
        self.selected = el if el is not None and el.kind != "template" else None
        self.preview.select(id(el.node) if self.selected else None)
        node = el.node if el is not None else (self.doc.root if self.doc else None)
        self.props.show_node(node, el.kind if el is not None else "template", self.namer)
        self._show_graph()

    def _picked_in_preview(self, node_id: int) -> None:
        el = next((e for e in self.top.walk() if id(e.node) == node_id), None) if self.top else None
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
        self.graph.empty_text = tr("No binding names this element.") if guid else tr("This file has no bindings.")
        self.graph.show_graph(shown, self._describe_end)
        self.docks["Bindings"].setWindowTitle(
            tr("Bindings ({shown} of {total})", shown=len(shown), total=len(graph.bindings))
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
        path, _ = QFileDialog.getOpenFileName(self, tr("A script list"), "", tr("Script lists (*.json)"))
        if path:
            try:
                self.scripts = viewmodels.ScriptList.read(Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, tr(TITLE), tr("{path} does not read: {error}", path=path, error=exc))
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
        self.settings.apps.setdefault(APP, {})["placeholders"] = fill
        self._when_idle(lambda: (setattr(self.samples, "fill", fill), self._redraw()))

    def _play_toggled(self) -> None:
        self.preview.set_play_effects(self.play_action.isChecked())
        self.settings.apps.setdefault(APP, {})["play_effects"] = self.play_action.isChecked()

    def _hidden_toggled(self) -> None:
        if self.ctx is not None:
            self._set_view(editor_hidden=self.hidden_action.isChecked())

    def _pick_reference(self, node: dict, field: str, kinds: tuple) -> None:
        if self.game is not None:
            key = pickers.pick(self, self.game.files, kinds, tr("Choose the {field}", field=field))
            if key:
                self._edit(lambda d: d.set_field(node, field, key))

    def _show_checks(self) -> None:
        """What the preview could not resolve: widget files and pictures the game does not have, placeholders."""
        self.checks.clear()
        notes = list(self.frame.notes) if self.frame is not None else []
        missing = sorted(self.pictures.missing) if self.pictures is not None else []
        notes += [tr("picture not in the game: {name}", name=name) for name in missing]
        made = sum(1 for k in self.samples.seen if k in self.samples.generated)
        if made:
            notes.append(tr("{n} values are placeholders (Sample values, in grey)", n=made))
        self.checks.addItems(notes or [tr("Every reference of the open file resolves.")])
        self.docks["Checks"].setWindowTitle(tr("Checks ({n})", n=len(notes)))

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
            return self._describe_end(end) if end is not None else tr("end {number}", number=track.end)

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
            self.statusBar().showMessage(tr("laying out the preview; the edit is made when it is done"))
        self._when_idle(lambda: self._make(edit, doc), doc)

    def _make(self, edit, doc: uidoc.UiDoc) -> None:
        try:
            edit(doc)
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            QMessageBox.warning(self, tr(TITLE), tr("That edit was not made: {error}", error=exc))

    def undo(self) -> None:
        self._edit(lambda d: d.undo.undo() is not None and self.statusBar().showMessage(tr("undone")))

    def redo(self) -> None:
        self._edit(lambda d: d.undo.redo() is not None and self.statusBar().showMessage(tr("redone")))

    def _moved(self, node_id: int, dx: float, dy: float) -> None:
        el = next((e for e in self.top.walk() if id(e.node) == node_id), None) if self.top else None
        if el is not None:
            self._edit(lambda d: d.move(el.node, _moved_by(fields.get(el.node, "Position"), dx, dy)))

    def _sized(self, node_id: int, fx: float, fy: float) -> None:
        el = next((e for e in self.top.walk() if id(e.node) == node_id), None) if self.top else None
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
            lambda d: self.statusBar().showMessage(
                tr("removed {name} and {n} bindings naming it", name=el.name, n=d.remove(el))
            )
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
                self, tr(TITLE), tr("The new {kind}'s name:", kind=which), text=f"New{which.title()}"
            )  # i18n: ok (the default name is a datum: the game's element kind)
            if not ok or not name:
                return
            node = self.doc.new_instance(which, name)
            if which == "container":
                tpl = pickers.pick(self, self.game.files, ("WI",), tr("The widget file it places"))
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
            self.statusBar().showMessage(
                tr("select an element first; widget types, enums and game objects are shown for their descriptions")
            )

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
            self.statusBar().showMessage(tr("select an element in the preview or the element list first"))
            return
        target = bindingdialogs.ask_bind_read(self, self.selected)
        if target is not None:
            src = bindings.EndSpec("path", None, tuple(path))
            dst = bindings.EndSpec("path", self.selected.guid, (target.uid,))
            self._edit(lambda d: d.add_binding([src], [dst], operators.ASSIGN))

    # files of the project

    def save(self) -> None:
        """Keep the open file in the project (``ui-edits/<game>/``), as the mod's build takes it."""
        if self.doc is None or self.game is None:
            return
        entry = self.game.files.entries.get(self.doc.key)
        path = entry.path if entry else f"{self.game.files.prefix}{self.doc.key}"
        data = self.doc.to_bytes()
        dest = projectpart.save_file(self.project(), self.game.key, path, data)
        self.game.files.extra[self.doc.key] = data
        self.doc.undo.mark_clean()
        self.doc.origin = "project"
        self._fill_files()
        # deployed=False sends nothing, so there is no answer to wait for
        notice = linkannounce.announce((livelink.SCENES,), self.project().mod_id, [path], deployed=False)
        self.statusBar().showMessage(tr("kept in the project: {path}; {notice}", path=dest, notice=notice.text))

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
                QMessageBox.question(self, tr(TITLE), tr("Delete the project's copy {path}?", path=saved))
                != QMessageBox.StandardButton.Yes
            ):
                return
            saved.unlink()
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
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("The new widget file's name (its GUID follows from it):"))
        if not ok or not name:
            return
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.project().mod_id}/ui/{name}"))
        data = uidoc.empty_widget_file(self.game.files, guid)
        path = f"{self.game.files.prefix}{guid}"
        projectpart.save_file(self.project(), self.game.key, path, data)
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
            "\n".join(found[:40]) if found else tr("Every file the project's UI files name exists."),
        )

    def _save_picture(self) -> None:
        if self.frame is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, tr("Save the preview"), "", "PNG (*.png)"
        )  # i18n: ok (a file filter)
        if name:
            canvas.render(self.frame, self.pictures, 1.0, world=self.preview.world).save(name)

    def closeEvent(self, event) -> None:
        dirty = [k for k, d in self.docs.items() if d.dirty]
        if dirty:
            ask = trn(
                "{n} file has edits not kept in the project. Keep them now?",
                "{n} files have edits not kept in the project. Keep them now?",
                len(dirty),
            )
            buttons = QMessageBox.StandardButton
            answer = QMessageBox.question(self, tr(TITLE), ask, buttons.Yes | buttons.No | buttons.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Yes:
                for k in dirty:
                    self.doc = self.docs[k]
                    self.save()
        self._forget_states()
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
