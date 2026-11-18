"""The asset editor's window: a game's assets in a tree on the left, a template chooser and the tabs Properties,
BalancingView, TextView, Icons, Templates and Usage on the right, the check's findings and the history below; menus
to open a game and a change set, import a mod or a profile, save, build the mod or write the game's own files, check,
and move tables of values and texts out and in.

``AssetWindow(settings, path)`` is the window (a page of the suite's shell, or alone with ``run``); the game folders
come from the suite's settings, the change set of each game lives in the workspace (``asset-edits/<game>.json``).
Use: ``docs/tool-suite.md``, "The asset editor".
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, number, tr
from ..othergames import installs
from ..suite import gamesync, guidrangedialog, linkannounce, links, livelink, project, theme, widgets
from ..suite.background import Background
from ..suite.settings import Settings, settings_file
from . import assetgame, checks, compare, modops, native
from .assetdoc import AssetDocument
from .assetform import AssetForm
from .assetgame import Game
from .assettabs import BalancingPanel, FindingsList, HistoryList, IconPanel, TemplatePanel, TextPanel, UsagePanel
from .assettree import AssetTree
from .changeset import ChangeSet
from .gamefiles import LAYOUTS
from .icons import IconSource
from .views import ASSET_VIEWS, ViewStore
from .windowexchange import TITLE, ExchangeActions

TABS = (
    N_("common.properties"),
    N_("assets.balancingview"),
    N_("assets.textview"),
    N_("assets.icons"),
    N_("assets.templates"),
    N_("assets.usage"),
)


class AssetWindow(ExchangeActions, QMainWindow):
    def __init__(self, settings: Settings, settings_path: Path) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.game: Game | None = None
        self.doc: AssetDocument | None = None
        self.guid: int | None = None
        self.opening = ""  # the game being opened in the background
        self.working = ""  # what a build, write or check does off this thread (one at a time)
        self.pending: tuple[str, int] | None = None  # the asset a link asked for before its game was open
        self.clipboard: tuple[list[int], bool] | None = None  # assets cut or copied: (GUIDs, cut)
        self.last_import = None  # the summary of the last import (``modimport.Imported``)
        self.background = Background(self)
        self.setWindowTitle(tr(TITLE))
        self.form = AssetForm()
        self.tree = AssetTree()
        self.balancing = BalancingPanel()
        self.texts = TextPanel(lambda lang: self.game.texts(lang) if self.game else {}, group=self.tree.selected_group)
        self.icon_source = IconSource(lambda path: self.game.files.read(path))
        self.tree.icons = self.icon_source
        self.icons = IconPanel(self.icon_source)
        self.templates = TemplatePanel()
        self.usage = UsagePanel()
        self.findings = FindingsList()
        self.history = HistoryList()
        self._layout()
        self._menus()
        widgets.find_key(self, self.tree.search)
        self.tree.picked.connect(self.show_asset)
        for jumper in (self.form, self.usage, self.findings):
            jumper.jump.connect(self.go_to)
        self.tabs.currentChanged.connect(lambda _i: self._fill_tab())
        self._refresh = QTimer(self, singleShot=True, interval=0, timeout=self._changed_now)
        self._touched: set = set()
        theme.apply(self, settings.theme)
        self.sync()

    # layout
    def _layout(self) -> None:
        right = QWidget()
        lay = QVBoxLayout(right)
        row = QHBoxLayout()
        self.template_box = QComboBox(editable=True, minimumWidth=260)
        self.template_box.setToolTip(tr("assets.tip.template_new_asset_made_edit"))
        row.addWidget(QLabel(tr("common.template")))
        row.addWidget(self.template_box, 1)
        lay.addLayout(row)
        self._build_tabs(lay)
        self.setCentralWidget(right)
        self._build_docks()

    def _build_tabs(self, lay: QVBoxLayout) -> None:
        self.tabs = QTabWidget()
        for name, widget in zip(TABS, (self.form, self.balancing, self.texts, self.icons, self.templates, self.usage),
                                strict=True):  # fmt: skip
            self.tabs.addTab(widget, tr(name))
        lay.addWidget(self.tabs, 1)

    def _build_docks(self) -> None:
        self.docks = {}
        for key, name, widget, area in (
            ("Assets", N_("common.assets"), self.tree, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Checks", N_("common.checks"), self.findings, Qt.DockWidgetArea.BottomDockWidgetArea),
            ("History", N_("common.history"), self.history, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"assets-{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[key] = dock
        self.tabifyDockWidget(self.docks["Checks"], self.docks["History"])
        self.docks["Checks"].raise_()
        QTimer.singleShot(0, self, self._dock_sizes)  # after the window has its size; dropped if it closes first

    def _dock_sizes(self) -> None:
        self.resizeDocks([self.docks["Assets"]], [420], Qt.Orientation.Horizontal)
        self.resizeDocks([self.docks["Checks"]], [110], Qt.Orientation.Vertical)

    def _menus(self) -> None:
        self.acts: dict[str, QAction] = {}
        bar = self.menuBar()
        self._file_menu(bar.addMenu(tr("common.menu.file")))
        self._edit_menu()
        self._view_menu(bar.addMenu(tr("common.menu.view")))
        check = bar.addMenu(tr("common.menu.check"))
        self._add(check, "check", tr("assets.sanity_check"), self.run_check, "F7")
        self._add(check, "final", tr("assets.final_check_other_mods"), lambda: self.run_check(final=True))

    def _add(self, menu, key: str, label: str, slot, shortcut=None, needs_doc: bool = True) -> None:
        act = menu.addAction(label, slot)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.setData(needs_doc)
        self.acts[key] = act

    def _file_menu(self, file) -> None:
        add = self._add
        self.open_menu = file.addMenu(tr("assets.menu.open_game"))
        for key, lay in LAYOUTS.items():
            self.open_menu.addAction(lay.name, lambda k=key: self.open(k))
        add(file, "load", tr("assets.open_change_set"), self.load_changes)
        add(file, "save", tr("assets.save_change_set"), self.save, QKeySequence.StandardKey.Save)
        add(file, "save_as", tr("assets.save_change_set_dots"), self.save_as)
        file.addSeparator()
        add(file, "import", tr("assets.import_mod"), self.import_mod)
        add(file, "import_profile", tr("assets.import_profile"), self.import_profile)
        add(file, "import_archive", tr("assets.import_archive_mod"), self.import_archive)
        file.addSeparator()
        add(file, "mod", tr("assets.build_mod_modops"), self.build_mod, "Ctrl+B")
        add(file, "native", tr("assets.write_games_own_files"), self.write_native)
        file.addSeparator()
        add(file, "csv_out", tr("assets.export_balancing_table_csv"), self.export_csv)
        add(file, "csv_in", tr("assets.import_balancing_table_csv"), self.import_csv)
        add(file, "texts_out", tr("assets.export_texts_csv"), self.export_texts)
        add(file, "texts_in", tr("assets.import_texts_csv"), self.import_texts)
        file.addSeparator()
        add(file, "print", tr("assets.print_asset"), lambda: self.print_asset(), "Ctrl+P")
        add(file, "print_pdf", tr("assets.print_asset_pdf_file"), lambda: self.print_asset(to_pdf=True))
        add(file, "print_table", tr("assets.print_balancing_table"), lambda: self.print_table())

    def _edit_menu(self) -> None:
        add = self._add
        edit, undo, redo = widgets.edit_menu(
            self, lambda: self.doc and self.doc.undo.undo(), lambda: self.doc and self.doc.undo.redo()
        )
        for key, action in (("undo", undo), ("redo", redo)):
            action.setData(True)  # needs a document (sync)
            self.acts[key] = action
        edit.addSeparator()
        add(edit, "new", tr("assets.new_asset_template"), self.new_from_template, "Ctrl+N")
        add(edit, "child", tr("assets.new_asset_one_base"), lambda: self.new_from(base=True))
        add(edit, "copy", tr("assets.copy_asset"), lambda: self.new_from(base=False), "Ctrl+D")
        add(edit, "rename", tr("assets.rename"), self.rename)
        add(edit, "cut", tr("assets.cut_selected_assets"), lambda: self.clip(cut=True), "Ctrl+Shift+X")
        add(edit, "copy_sel", tr("assets.copy_selected_assets"), lambda: self.clip(cut=False), "Ctrl+Shift+C")
        add(edit, "paste", tr("assets.paste_assets_after_selected_one"), self.paste, "Ctrl+Shift+V")
        add(edit, "delete", tr("assets.delete_asset"), self.delete, QKeySequence.StandardKey.Delete)
        edit.addSeparator()
        add(edit, "range", tr("assets.guid_range"), self.set_range)
        add(edit, "place", tr("assets.place_open_island"), self.place_on_island, "Ctrl+Shift+P")
        self.acts["place"].setVisible(links.available(links.PLACE_ASSET))  # alone, the asset editor has no island

    def _view_menu(self, view) -> None:
        self.expert = view.addAction(tr("assets.expert_values"))
        self.expert.setCheckable(True)
        self.expert.toggled.connect(self._expert)
        for flag, label, tip in (
            ("secondary", N_("assets.secondary_template_values"), N_("assets.values_template_lists_hidden")),
            (
                "conditional",
                N_("assets.values_hidden_conditions"),
                N_("assets.values_whose_visibility_conditions_1800s"),
            ),
            ("original", N_("assets.original_data"), N_("assets.beside_changed_value_games_own")),
        ):
            act = view.addAction(tr(label))
            act.setCheckable(True)
            act.setToolTip(tr(tip))
            act.toggled.connect(lambda on, f=flag: self._form_flag(f, on))
        for dock in self.docks.values():
            view.addAction(dock.toggleViewAction())

    def clip(self, cut: bool) -> None:
        """Remember the selected assets (several: select a group's rows) for Paste; a cut asset of the change set moves
        with its GUID, a cut game asset is copied and removed (its GUID changes)."""
        guids = self.tree.selected_guids() or ([self.guid] if self.guid is not None else [])
        self.clipboard = (guids, cut)
        self.say(
            tr("assets.assets_cut_pick_asset_paste", n=len(guids))
            if cut
            else tr("assets.assets_copied_pick_asset_paste", n=len(guids))
        )

    def paste(self) -> None:
        if not self.clipboard or self.doc is None or self.guid is None:
            return self.say(tr("assets.nothing_paste_no_asset_picked"))
        guids, cut = self.clipboard
        made = self._made(lambda: self.doc.paste([g for g in guids if self.doc.exists(g)], self.guid, cut))
        if not made:
            return
        if cut:
            self.clipboard = (list(made), False)
        group = self.doc.group_range(self.guid)
        self.say(
            tr("assets.assets_pasted_guids_groups_range", n=len(made), lo=group[0], hi=group[1])
            if group
            else tr("assets.n_assets_pasted", n=len(made))
        )
        self.go_to(made[0])

    def sync(self) -> None:
        has = self.doc is not None
        for act in self.acts.values():
            act.setEnabled(has or not act.data())
        if has:
            self.acts["undo"].setEnabled(self.doc.undo.index > 0)
            self.acts["redo"].setEnabled(self.doc.undo.index < len(self.doc.undo.commands))
            outputs = self.game.layout.outputs
            self.acts["mod"].setEnabled("modops" in outputs)
            self.acts["import"].setEnabled("modops" in outputs)
            self.acts["import_profile"].setEnabled(self.game.layout.key == "anno117")
            self.acts["import_archive"].setEnabled("native" in outputs and "modops" not in outputs)
            picked = self.guid is not None and self.doc.exists(self.guid)
            for key in ("child", "copy", "rename", "delete", "cut", "copy_sel", "paste"):
                self.acts[key].setEnabled(picked)
        title = tr(TITLE) if not has else f"{self.game.layout.name}{' *' if self.doc.dirty else ''} · {tr(TITLE)}"
        self.setWindowTitle(title)

    def say(self, text: str) -> None:
        self.statusBar().showMessage(text, 20000)

    def place_on_island(self) -> None:
        """Hand the asset to the island editor's Place tool (``links.PLACE_ASSET``)."""
        if self.game is None or self.guid is None:
            return self.say(tr("assets.pick_asset_first"))
        why = links.follow(links.PLACE_ASSET, self.game.layout.key, self.guid)
        if why is None:
            why = tr("assets.island_editor_places_assets_suites")
        self.say(why or tr("assets.asset_click_island_place", guid=self.guid))

    # the app's settings
    @property
    def state(self) -> dict:
        return self.settings.apps.setdefault("assets", {})

    def edits_path(self, key: str) -> Path:
        return Path(
            self.state.get("edits", {}).get(key)
            or project.folder_of(self.settings) / project.ASSET_EDITS / f"{key}.json"
        )

    def views_path(self, key: str) -> Path:
        """The saved balancing views of a game: beside the change sets, in the project (else the workspace)."""
        return project.folder_of(self.settings) / ASSET_VIEWS / f"{key}.json"

    # opening
    def show_asset_of(self, key: str, guid: int) -> str:
        """Show the asset ``guid`` of the game ``key`` (a link from another app, the game's edit request): "shown", or
        "pending" when that game is not open yet - it opens in the background and the asset is shown when it is in
        (a later request replaces the pending one); "missing" when the open game has no such asset."""
        if self.game is not None and self.game.layout.key == key:
            if self.doc is None or not self.doc.exists(int(guid)):
                return "missing"
            self.go_to(int(guid))
            return "shown"
        self.pending = (key, int(guid))
        if not self.opening:
            self.open(key)
        return "pending"

    def open(self, key: str, folder: Path | None = None, wait: bool = False) -> None:
        """Open the game ``key`` (from its folder in the settings, or ``folder``) in the background; ``wait``: in
        this thread (a test, the screenshots)."""
        if not gamesync.may_switch(self, key, self.confirm_close):  # asks; the suite's other apps follow
            self.pending = None
            return
        named = folder or getattr(self.settings, key, "") or ""
        folder = Path(named)
        if not str(named).strip() or not folder.is_dir():  # an empty name is no folder (Path("") is the current one)
            asked_by_link = self.pending is not None  # a link never opens a dialog over the user's work
            self.pending = None
            text = tr("assets.set_folder_first_islands_games", game=LAYOUTS[key].name)
            if asked_by_link:
                self.say(text)
            else:
                QMessageBox.warning(self, tr(TITLE), text)
            return
        donor_key = LAYOUTS[key].donor
        donor = assetgame.installed_folder(self.settings, donor_key) if donor_key else None
        self.say(tr("assets.opening_game", game=LAYOUTS[key].name))
        self.opening = key
        self.open_menu.setEnabled(False)  # a second game opened meanwhile would race this one for the window

        def work():
            return assetgame.open_game(key, folder, assetgame.cache_dir(self.settings.workspace), donor)

        if wait:
            result = work()
            return self._opened(key, result)
        self.background.run(work, lambda result: self._opened(key, result))

    def _opened(self, key: str, result) -> None:
        self.opening = ""
        self.open_menu.setEnabled(True)
        if isinstance(result, Exception):
            asked_by_link, self.pending = self.pending is not None, None
            text = f"{type(result).__name__}: {result}"
            if asked_by_link:
                self.say(text)
            else:
                QMessageBox.warning(self, tr(TITLE), text)
            return
        if self.game is not None and not self.working:  # (a running job still reads the game it started on)
            self.game.index.close()
        self.game = result
        self.icon_source.use_atlases(*result.icon_data)
        self.background.run(lambda: result.files.tree, lambda _opened: None)  # the archives open off this thread
        self.texts.set_languages(result.layout.language_names)
        path = self.edits_path(key)
        try:
            changes = (
                ChangeSet.load(path)
                if path.is_file()
                else ChangeSet.for_project(key, project.current(self.settings), self.settings)
            )
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(
                self,
                tr(TITLE),
                tr("assets.change_set_not_read_starting", path=path, exc=exc),
            )
            changes = ChangeSet(key)
        self._attach(self.game.document(changes, path))
        self.state["game"] = key
        notes = "; ".join(self.game.notes)
        counts = tr(
            "assets.assets_templates",
            assets=len(self.game.index.entries),
            templates=len(self.game.schema.templates),
        )
        self.say(counts + (f" · {notes}" if notes else ""))
        self._show_pending(key)

    def _show_pending(self, opened: str) -> None:
        """Serve the asset a link asked for while the game ``opened`` opened - once, here, never through the link again:
        the asset when it is of that game, else (a request for another game came in meanwhile) that game opens next."""
        wanted, self.pending = self.pending, None
        if wanted is None:
            return
        if wanted[0] == opened:
            if self.doc is not None and self.doc.exists(wanted[1]):
                self.go_to(wanted[1])
        else:
            self.pending = wanted
            self.open(wanted[0])

    def _attach(self, doc: AssetDocument) -> None:
        self.doc, self.guid = doc, None
        self.clipboard = None  # its GUIDs are the document's that was open
        self.icon_source.edits = doc.changes.icons
        doc.listeners.append(self._changed)
        doc.undo.listeners.append(self.sync)
        doc.undo.listeners.append(lambda: self.history.present(doc))
        self.template_box.clear()
        self.template_box.addItems(sorted(doc.schema.templates))
        self.balancing.set_store(ViewStore.load(self.views_path(doc.changes.game)))
        self.tree.set_doc(doc)
        self.history.present(doc)
        self.form.show_asset(doc, None)
        self._fill_tab()  # the tab in front shows the new document too, not the one that was open
        self.sync()

    def go_to(self, guid: int) -> None:
        if self.doc is not None and self.doc.exists(guid):
            self.tree.select(guid)
            self.show_asset(guid)

    def show_asset(self, guid: int) -> None:
        self.guid = guid
        self._fill_tab()
        self.sync()

    def _fill_tab(self) -> None:
        if self.doc is None:
            return
        widget, g = self.tabs.currentWidget(), self.guid
        if widget is self.form:
            self.form.show_asset(self.doc, g)
        elif widget is self.balancing:
            self.balancing.present(self.doc, self.tree.selected_group() or ([g] if g else []))
        else:
            widget.present(self.doc, g)

    def _changed(self, guid) -> None:
        if guid is not None:
            self.icon_source.forget(guid)
        self._touched.add(guid)
        self._refresh.start()  # once per batch of edits (an import makes many)

    def _changed_now(self) -> None:
        touched, self._touched = self._touched, set()
        if None in touched:
            self.tree.fill()
        else:
            self.tree.mark(touched)
        if self.guid in touched or None in touched:
            if self.tabs.currentWidget() is self.form:
                self.form.refresh()
            else:
                self._fill_tab()
        self.sync()

    def _expert(self, on: bool) -> None:
        self._form_flag("expert", on)

    def _form_flag(self, flag: str, on: bool) -> None:
        """A switch of the View menu that changes what the form shows (``AssetForm.expert`` and its kin)."""
        setattr(self.form, flag, on)
        self.form.refresh()

    # editing
    def _made(self, make):
        """What ``make()`` returns (the new assets), None when it could not make them: the GUID range is used up."""
        try:
            return make()
        except ValueError as exc:
            QMessageBox.warning(self, tr(TITLE), str(exc))
            return None

    def new_from_template(self) -> None:
        template = self.template_box.currentText().strip()
        if template not in self.doc.schema.templates:
            return self.say(tr("assets.pick_template_above_first"))
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("assets.name_new_template", template=template))
        new = self._made(lambda: self.doc.new_asset(template, name.strip())) if ok and name.strip() else None
        if new is not None:
            self.go_to(new)

    def new_from(self, base: bool) -> None:
        g = self.guid
        label = (
            tr("assets.name_new_asset_made", name=self.doc.name(g))
            if base
            else tr("assets.name_new_asset_copy", name=self.doc.name(g))
        )
        name, ok = QInputDialog.getText(self, tr(TITLE), label)
        if ok and name.strip():
            new = self._made(
                lambda: self.doc.new_asset("", name.strip(), base=g if base else None, copy_of=None if base else g)
            )
            if new is not None:
                self.go_to(new)

    def rename(self) -> None:
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("assets.new_name"), text=self.doc.name(self.guid))
        if ok and name.strip():
            self.doc.rename(self.guid, name.strip())

    def delete(self) -> None:
        users = compare.usage(self.doc, self.guid)
        if (
            users
            and QMessageBox.question(self, tr(TITLE), tr("assets.assets_name_one_delete_anyway", n=len(users)))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.doc.remove_asset(self.guid)
        self.guid = None
        self._fill_tab()

    def set_range(self) -> None:
        """Edit > GUID range: own, publicly declared or typed in (``suite.guidrangedialog``); new assets take
        their numbers from it, assets made before keep theirs."""
        c = self.doc.changes
        others = project.other_ranges(self.settings, project.folder_of(self.settings), c.game)
        now = guidrangedialog.Selection(c.guid_choice, c.guid_source, tuple(c.guid_range))
        picked = guidrangedialog.ask(self, c.game, self.settings, others, now, self.doc.index.by_guid)
        if picked is None:
            return
        self.doc.set_guid_range(picked.span, picked.choice, picked.source)
        self.settings.save(self.settings_path)  # the own reserve is made on first use
        self.say(
            tr(
                "assets.guid_range_new_assets",
                lo=number(picked.span[0]),
                hi=number(picked.span[1]),
                choice=picked.choice,
            )
        )
        if c.outside_range():
            self.say(tr("assets.new_assets_made_earlier_lie", n=len(c.outside_range())))

    # saving and building
    def save(self) -> bool:
        try:
            path = self.doc.save()
        except OSError as exc:
            QMessageBox.warning(self, tr(TITLE), tr("assets.could_not_save", exc=exc))
            return False
        self.say(tr("common.saved_path", path=path))
        self.sync()
        return True

    def save_as(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, tr(TITLE), str(self.doc.path or ""), tr("assets.change_sets_json"))
        if name:
            self.doc.path = Path(name)
            self.state.setdefault("edits", {})[self.doc.changes.game] = name
            self.save()

    def load_changes(self) -> None:
        if not self.confirm_close():
            return
        name, _ = QFileDialog.getOpenFileName(self, tr(TITLE), str(self.doc.path or ""), tr("assets.change_sets_json"))
        if not name:
            return
        try:
            changes = ChangeSet.load(Path(name))
        except (OSError, ValueError, TypeError) as exc:
            return QMessageBox.warning(self, tr(TITLE), tr("assets.not_change_set", exc=exc))
        if changes.game != self.game.layout.key:
            return QMessageBox.warning(self, tr(TITLE), tr("assets.change_set_game", game=changes.game))
        self.state.setdefault("edits", {})[changes.game] = name
        self._attach(self.game.document(changes, Path(name)))

    def mods_folder(self) -> Path | None:
        key = self.doc.changes.game
        folder = self.settings.mods if key == "anno117" else self.settings.mods1800 if key == "anno1800" else ""
        if folder:
            return Path(folder)
        name = QFileDialog.getExistingDirectory(
            self, tr("assets.title_mods_folder_game", title=tr(TITLE), game=self.game.layout.name)
        )
        return Path(name) if name else None

    def _run(self, what: str, work, done, wait: bool = False) -> bool:
        """``work()`` off the window's thread under a status line ``what``, then ``done(result)`` here (the exception
        when it raised). One job at a time: a second is said, not started; ``wait``: run it here (a test)."""
        if self.working and not wait:
            self.say(tr("assets.still_busy_what", what=self.working))
            return False
        self.working = what
        self.say(tr("assets.what", what=what))

        def finish(result) -> None:
            self.working = ""
            done(result)

        self.background.run(work, finish, wait)
        return True

    def _failed(self, error: Exception) -> None:
        QMessageBox.warning(self, tr(TITLE), tr("islands.failed_error", error=f"{type(error).__name__}: {error}"))

    def build_mod(self, mods: Path | None = None, wait: bool = False) -> Path | None:
        """The ModOps mod into the game's mods folder (after the check), both off the window's thread; ``wait``: here,
        and the mod's folder comes back. The check and the write read the same snapshot of the document."""
        built: list[Path] = []
        snapshot = self.doc.snapshot()

        def checked(found) -> None:
            if isinstance(found, Exception):
                return self._failed(found)
            errors = [f for f in found if f.severity == checks.ERROR]
            if errors:
                self.findings.present(errors)
                self.docks["Checks"].raise_()
                answer = QMessageBox.question(
                    self, tr(TITLE), tr("assets.check_found_errors_build_anyway", n=len(errors))
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            target = mods or self.mods_folder()
            if target is not None:
                self._write_mod(snapshot, target, wait, built)

        self._run(tr("assets.checking"), lambda: checks.final_check(snapshot), checked, wait)
        return built[0] if built else None

    def _write_mod(self, doc: AssetDocument, mods: Path, wait: bool, built: list[Path]) -> None:
        def written(result) -> None:
            if isinstance(result, ValueError):  # the imported mod's own folder: never written over
                name = QFileDialog.getExistingDirectory(
                    self, tr("assets.another_folder_mod", title=tr(TITLE), reason=result)
                )
                if name and Path(name).resolve() != Path(mods).resolve():
                    self._write_mod(doc, Path(name), wait, built)
                return
            if isinstance(result, Exception):
                return self._failed(result)
            folder, problems = result
            built.append(folder)
            self.findings.present([checks.Finding(None, "mod", checks.WARNING, p) for p in problems])
            self.say(
                tr(
                    "assets.wrote_asset_values_texts_follow",
                    folder=folder,
                )
            )
            linkannounce.announce_in_background(
                self.background,
                self.say,
                lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS), mod=folder.name),
            )

        self._run(tr("assets.writing_mod"), lambda: modops.write_mod(doc, mods, game=self.game.files), written, wait)

    def write_native(self, folder: Path | None = None, wait: bool = False) -> None:
        """The game's own files of the change set into ``folder`` (asked for), written off the window's thread from a
        snapshot of the document: an edit made meanwhile is not in them."""
        if folder is None:
            name = QFileDialog.getExistingDirectory(self, tr("assets.folder_games_files", title=tr(TITLE)))
            if not name:
                return
            folder = Path(name)
        doc = self.doc.snapshot()

        def work() -> list[Path] | None:
            files = native.render(doc)
            if not files:
                return None
            written = native.write_tree(files, folder)
            if doc.changes.game in installs.keys(lambda g: g.family == installs.OLD):  # the RDA V2.0 games
                native.write_archive(files, folder / f"{doc.changes.mod_id}.rda")
            return written

        def done(written) -> None:
            if isinstance(written, Exception):
                return self._failed(written)
            if written is None:
                return self.say(tr("assets.change_set_changes_no_asset"))
            self.say(
                tr(
                    "assets.wrote_files_under_not_loaded",
                    n=len(written),
                    folder=folder,
                )
            )

        self._run(tr("assets.writing_files"), work, done, wait)

    def run_check(self, final: bool = False, wait: bool = False) -> list | None:
        """The sanity check (``final``: with the other mods too) off the window's thread; the findings come back with
        ``wait`` only."""
        found: list = []

        def done(result) -> None:
            if isinstance(result, Exception):
                return self._failed(result)
            found.extend(result)
            self.findings.present(result)
            self.docks["Checks"].raise_()
            self.say(tr("assets.n_findings", n=len(result)))

        mods = self.mods_folder() if final and "modops" in self.game.layout.outputs else None
        doc = self.doc.snapshot()  # the window goes on editing the document while the check reads
        work = (lambda: checks.final_check(doc, mods)) if final else (lambda: checks.check(doc))
        self._run(tr("assets.checking"), work, done, wait)
        return found if wait else None

    # closing
    def confirm_close(self) -> bool:
        if self.doc is None or not self.doc.dirty:
            return True
        return widgets.ask_keep(self, tr(TITLE), tr("assets.save_change_set_first"), self.save)

    def closeEvent(self, event) -> None:
        if not self.confirm_close():
            event.ignore()
            return
        if self.game is not None:
            self.game.index.close()
        super().closeEvent(event)


def run(game: str | None = None, settings_path: Path | None = None) -> int:
    """The asset editor alone (the suite's shell holds it as a page otherwise)."""
    qt = QApplication.instance() or QApplication([sys.argv[0]])
    qt.setStyle("Fusion")
    path = settings_path or settings_file()
    window = AssetWindow(Settings.load(path), path)
    window.show()
    if game:
        QTimer.singleShot(0, lambda: window.open(game))
    code = qt.exec()
    window.settings.save(path)
    return code
