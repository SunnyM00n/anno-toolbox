"""The asset editor's window: a game's assets in a tree on the left, a template chooser and the tabs Properties,
SBalancingView, TextView, Icons, Templates and Usage on the right, the check's findings and the history below; menus
to open a game and a change set, import a mod or a profile, save, build the mod or write the game's own files, check,
and move tables of values and texts out and in.

``CAssetWindow(settings, path)`` is the window (a page of the suite's shell, or alone with ``run``); the game folders
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
from ..suite import centralmenu, gamesync, guidrangedialog, linkannounce, links, livelink, project, theme, widgets
from ..suite.background import CBackground
from ..suite.settings import CSettings, settings_file
from . import assetgame, checks, compare, handover, layered, modops, native
from .assetdoc import CAssetDocument
from .assetform import CAssetForm
from .assetgame import CGame
from .assettabs import CBalancingPanel, CFindingsList, CHistoryList, CIconPanel, CTemplatePanel, CTextPanel, CUsagePanel
from .assettree import CAssetTree
from .changeset import CChangeSet
from .gamefiles import LAYOUTS
from .icons import CIconSource
from .views import ASSET_VIEWS, CViewStore
from .windowexchange import TITLE, CExchangeActions

TABS = (
    N_("common.properties"),
    N_("assets.balancingview"),
    N_("assets.textview"),
    N_("assets.icons"),
    N_("assets.templates"),
    N_("assets.usage"),
)


class CAssetWindow(CExchangeActions, QMainWindow):
    def __init__(self, settings: CSettings, settings_path: Path) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_game: CGame | None = None
        self.m_doc: CAssetDocument | None = None
        self.m_guid: int | None = None
        self.m_opening = ""  # the game being opened in the background
        self.m_working = ""  # what a build, write or check does off this thread (one at a time)
        self.m_pending: tuple[str, int] | None = None  # the asset a link asked for before its game was open
        self.m_clipboard: tuple[list[int], bool] | None = None  # assets cut or copied: (GUIDs, cut)
        self.m_last_import = None  # the summary of the last import (``modimport.SImported``)
        self.m_background = CBackground(self)
        self.setWindowTitle(tr(TITLE))
        self.m_form = CAssetForm()
        self.m_tree = CAssetTree()
        self.m_balancing = CBalancingPanel()
        self.m_texts = CTextPanel(
            lambda lang: self.m_game.texts(lang) if self.m_game else {}, group=self.m_tree.selected_group
        )
        self.m_icon_source = CIconSource(lambda path: self.m_game.m_files.read(path))
        self.m_tree.m_icons = self.m_icon_source
        self.m_icons = CIconPanel(self.m_icon_source)
        self.m_templates = CTemplatePanel()
        self.m_usage = CUsagePanel()
        self.m_findings = CFindingsList()
        self.m_history = CHistoryList()
        self._layout()
        self._menus()
        widgets.find_key(self, self.m_tree.m_search)
        self.m_tree.picked.connect(self.show_asset)
        for jumper in (self.m_form, self.m_usage, self.m_findings):
            jumper.jump.connect(self.go_to)
        self.m_tabs.currentChanged.connect(lambda _i: self._fill_tab())
        self._m_refresh = QTimer(self, singleShot=True, interval=0, timeout=self._changed_now)
        self._m_touched: set = set()
        theme.apply(self, settings.theme)
        self.sync()

    # layout
    def _layout(self) -> None:
        right = QWidget()
        layout = QVBoxLayout(right)
        row = QHBoxLayout()
        self.m_template_box = QComboBox(editable=True, minimumWidth=260)
        self.m_template_box.setToolTip(tr("assets.tip.template_new_asset_made_edit"))
        row.addWidget(QLabel(tr("common.template")))
        row.addWidget(self.m_template_box, 1)
        layout.addLayout(row)
        self._build_tabs(layout)
        self.setCentralWidget(right)
        self._build_docks()

    def _build_tabs(self, layout: QVBoxLayout) -> None:
        self.m_tabs = QTabWidget()
        for name, widget in zip(TABS, (self.m_form, self.m_balancing, self.m_texts, self.m_icons, self.m_templates, self.m_usage),
                                strict=True):  # fmt: skip
            self.m_tabs.addTab(widget, tr(name))
        layout.addWidget(self.m_tabs, 1)

    def _build_docks(self) -> None:
        self.m_docks = {}
        for key, name, widget, area in (
            ("Assets", N_("common.assets"), self.m_tree, Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Checks", N_("common.checks"), self.m_findings, Qt.DockWidgetArea.BottomDockWidgetArea),
            ("History", N_("common.history"), self.m_history, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(tr(name), self, objectName=f"assets-{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.m_docks[key] = dock
        self.tabifyDockWidget(self.m_docks["Checks"], self.m_docks["History"])
        self.m_docks["Checks"].raise_()
        QTimer.singleShot(0, self, self._dock_sizes)  # after the window has its size; dropped if it closes first

    def _dock_sizes(self) -> None:
        self.resizeDocks([self.m_docks["Assets"]], [420], Qt.Orientation.Horizontal)
        self.resizeDocks([self.m_docks["Checks"]], [110], Qt.Orientation.Vertical)

    def _menus(self) -> None:
        self.m_acts: dict[str, QAction] = {}
        bar = self.menuBar()
        self._file_menu(bar.addMenu(tr("common.menu.file")))
        self._edit_menu()
        self._view_menu(bar.addMenu(tr("common.menu.view")))
        check = bar.addMenu(tr("common.menu.check"))
        self._add(check, "check", tr("assets.sanity_check"), self.run_check, "F7")
        self._add(check, "final", tr("assets.final_check_other_mods"), lambda: self.run_check(final=True))

    def _add(self, menu, key: str, label: str, slot, shortcut=None, needs_doc: bool = True) -> None:
        action = menu.addAction(label, slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.setData(needs_doc)
        self.m_acts[key] = action

    def _file_menu(self, file) -> None:
        add = self._add
        self.m_open_menu = centralmenu.mark(file.addMenu(tr("assets.menu.open_game")), centralmenu.GAME)
        for key, layout in LAYOUTS.items():
            self.m_open_menu.addAction(layout.name, lambda key=key: self.open(key))
        add(file, "load", tr("assets.open_change_set"), self.load_changes)
        add(file, "save", tr("assets.save_change_set"), self.save, QKeySequence.StandardKey.Save)
        centralmenu.mark(self.m_acts["save"], centralmenu.SAVE)
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
            self, lambda: self.m_doc and self.m_doc.m_undo.undo(), lambda: self.m_doc and self.m_doc.m_undo.redo()
        )
        for key, action in (("undo", undo), ("redo", redo)):
            action.setData(True)  # needs a document (sync)
            self.m_acts[key] = action
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
        self.m_acts["place"].setVisible(links.available(links.PLACE_ASSET))  # alone, the asset editor has no island

    def _view_menu(self, view) -> None:
        self.m_expert = view.addAction(tr("assets.expert_values"))
        self.m_expert.setCheckable(True)
        self.m_expert.toggled.connect(self._expert)
        for flag, label, tip in (
            ("secondary", N_("assets.secondary_template_values"), N_("assets.values_template_lists_hidden")),
            (
                "conditional",
                N_("assets.values_hidden_conditions"),
                N_("assets.values_whose_visibility_conditions_1800s"),
            ),
            ("original", N_("assets.original_data"), N_("assets.beside_changed_value_games_own")),
        ):
            action = view.addAction(tr(label))
            action.setCheckable(True)
            action.setToolTip(tr(tip))
            action.toggled.connect(lambda is_on, flag=flag: self._form_flag(flag, is_on))
        for dock in self.m_docks.values():
            view.addAction(dock.toggleViewAction())

    def clip(self, cut: bool) -> None:
        """Remember the selected assets (several: select a group's rows) for Paste; a cut asset of the change set moves
        with its GUID, a cut game asset is copied and removed (its GUID changes)."""
        guids = self.m_tree.selected_assets() or ([self.m_guid] if self.m_guid is not None else [])
        self.m_clipboard = (guids, cut)
        self.say(
            tr("assets.assets_cut_pick_asset_paste", n=len(guids))
            if cut
            else tr("assets.assets_copied_pick_asset_paste", n=len(guids))
        )

    def paste(self) -> None:
        if not self.m_clipboard or self.m_doc is None or self.m_guid is None:
            return self.say(tr("assets.nothing_paste_no_asset_picked"))
        guids, cut = self.m_clipboard
        made = self._made(
            lambda: self.m_doc.paste([guid for guid in guids if self.m_doc.exists(guid)], self.m_guid, cut)
        )
        if not made:
            return
        if cut:
            self.m_clipboard = (list(made), False)
        group = self.m_doc.group_range(self.m_guid)
        self.say(
            tr("assets.assets_pasted_guids_groups_range", n=len(made), lo=group[0], hi=group[1])
            if group
            else tr("assets.n_assets_pasted", n=len(made))
        )
        self.go_to(made[0])

    def sync(self) -> None:
        has = self.m_doc is not None
        for action in self.m_acts.values():
            action.setEnabled(has or not action.data())
        if has:
            self.m_acts["undo"].setEnabled(self.m_doc.m_undo.m_index > 0)
            self.m_acts["redo"].setEnabled(self.m_doc.m_undo.m_index < len(self.m_doc.m_undo.m_commands))
            outputs = self.m_game.m_layout.outputs
            self.m_acts["mod"].setEnabled("modops" in outputs)
            self.m_acts["import"].setEnabled("modops" in outputs)
            self.m_acts["import_profile"].setEnabled(self.m_game.m_layout.key == "anno117")
            self.m_acts["import_archive"].setEnabled("native" in outputs and "modops" not in outputs)
            picked = self.m_guid is not None and self.m_doc.exists(self.m_guid)
            for key in ("child", "copy", "rename", "delete", "cut", "copy_sel", "paste"):
                self.m_acts[key].setEnabled(picked)
        title = tr(TITLE) if not has else f"{self.m_game.m_layout.name}{' *' if self.m_doc.dirty else ''} · {tr(TITLE)}"
        self.setWindowTitle(title)

    def say(self, text: str) -> None:
        self.statusBar().showMessage(text, 20000)

    def place_on_island(self) -> None:
        """Hand the asset to the island editor's Place tool (``links.PLACE_ASSET``)."""
        if self.m_game is None or self.m_guid is None:
            return self.say(tr("assets.pick_asset_first"))
        why = links.follow(links.PLACE_ASSET, self.m_game.m_layout.key, self.m_guid)
        if why is None:
            why = tr("assets.island_editor_places_assets_suites")
        self.say(why or tr("assets.asset_click_island_place", guid=self.m_guid))

    # the app's settings
    @property
    def state(self) -> dict:
        return self.m_settings.apps.setdefault("assets", {})

    def edits_path(self, key: str) -> Path:
        return Path(
            self.state.get("edits", {}).get(key)
            or project.folder_of(self.m_settings) / project.ASSET_EDITS / f"{key}.json"
        )

    def views_path(self, key: str) -> Path:
        """The saved balancing views of a game: beside the change sets, in the project (else the workspace)."""
        return project.folder_of(self.m_settings) / ASSET_VIEWS / f"{key}.json"

    # opening
    def show_asset_of(self, key: str, guid: int) -> str:
        """Show the asset ``guid`` of the game ``key`` (a link from another app, the game's edit request): "shown", or
        "pending" when that game is not open yet - it opens in the background and the asset is shown when it is in
        (a later request replaces the pending one); "missing" when the open game has no such asset."""
        if self.m_game is not None and self.m_game.m_layout.key == key:
            if self.m_doc is None or not self.m_doc.exists(int(guid)):
                return "missing"
            self.go_to(int(guid))
            return "shown"
        self.m_pending = (key, int(guid))
        if not self.m_opening:
            self.open(key)
        return "pending"

    def open(self, key: str, folder: Path | None = None, is_waiting: bool = False) -> None:
        """Open the game ``key`` (from its folder in the settings, or ``folder``) in the background; ``is_waiting``: in
        this thread (a test, the screenshots)."""
        if not gamesync.may_switch(self, key, self.confirm_close):  # asks; the suite's other apps follow
            self.m_pending = None
            return
        named = folder or getattr(self.m_settings, key, "") or ""
        folder = Path(named)
        if not str(named).strip() or not folder.is_dir():  # an empty name is no folder (Path("") is the current one)
            asked_by_link = self.m_pending is not None  # a link never opens a dialog over the user's work
            self.m_pending = None
            text = tr("assets.set_folder_first_islands_games", game=LAYOUTS[key].name)
            if asked_by_link:
                self.say(text)
            else:
                QMessageBox.warning(self, tr(TITLE), text)
            return
        donor_key = LAYOUTS[key].donor
        donor = assetgame.installed_folder(self.m_settings, donor_key) if donor_key else None
        self.say(tr("assets.opening_game", game=LAYOUTS[key].name))
        self.m_opening = key
        self.m_open_menu.setEnabled(False)  # a second game opened meanwhile would race this one for the window

        def work():
            game = assetgame.open_game(key, folder, assetgame.cache_dir(self.m_settings.workspace), donor)
            return layered.open_layered(
                project.current(self.m_settings), game, None, handover.mods_folders(self.m_settings, key)
            )

        if is_waiting:
            result = work()
            return self._opened(key, result)
        self.m_background.run(work, lambda result: self._opened(key, result))

    def _opened(self, key: str, result) -> None:
        self.m_opening = ""
        self.m_open_menu.setEnabled(True)
        if isinstance(result, Exception):
            asked_by_link, self.m_pending = self.m_pending is not None, None
            text = f"{type(result).__name__}: {result}"
            if asked_by_link:
                self.say(text)
            else:
                QMessageBox.warning(self, tr(TITLE), text)
            return
        if self.m_game is not None and not self.m_working:  # (a running job still reads the game it started on)
            self.m_game.m_index.close()
        self.m_game = result
        self.m_icon_source.use_atlases(*result.m_icon_data)
        self.m_background.run(lambda: result.m_files.tree, lambda _opened: None)  # the archives open off this thread
        self.m_texts.set_languages(result.m_layout.language_names)
        path = self.edits_path(key)
        try:
            changes = (
                CChangeSet.load(path)
                if path.is_file()
                else CChangeSet.for_project(key, project.current(self.m_settings), self.m_settings)
            )
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(
                self,
                tr(TITLE),
                tr("assets.change_set_not_read_starting", path=path, exc=error),
            )
            changes = CChangeSet(key)
        self._attach(self.m_game.document(changes, path))
        self.state["game"] = key
        notes = "; ".join(self.m_game.m_notes)
        counts = tr(
            "assets.assets_templates",
            assets=len(self.m_game.m_index.m_entries),
            templates=len(self.m_game.m_schema.templates),
        )
        self.say(counts + (f" · {notes}" if notes else ""))
        self._show_pending(key)

    def _show_pending(self, opened: str) -> None:
        """Serve the asset a link asked for while the game ``opened`` opened - once, here, never through the link again:
        the asset when it is of that game, else (a request for another game came in meanwhile) that game opens next."""
        wanted, self.m_pending = self.m_pending, None
        if wanted is None:
            return
        if wanted[0] == opened:
            if self.m_doc is not None and self.m_doc.exists(wanted[1]):
                self.go_to(wanted[1])
        else:
            self.m_pending = wanted
            self.open(wanted[0])

    def _attach(self, doc: CAssetDocument) -> None:
        self.m_doc, self.m_guid = doc, None
        self.m_clipboard = None  # its GUIDs are the document's that was open
        self.m_icon_source.m_edits = doc.m_changes.m_icons
        doc.m_listeners.append(self._changed)
        doc.m_undo.m_listeners.append(self.sync)
        doc.m_undo.m_listeners.append(lambda: self.m_history.present(doc))
        self.m_template_box.clear()
        self.m_template_box.addItems(sorted(doc.m_schema.templates))
        self.m_balancing.set_store(CViewStore.load(self.views_path(doc.m_changes.m_game)))
        self.m_tree.set_doc(doc)
        self.m_history.present(doc)
        self.m_form.show_asset(doc, None)
        self._fill_tab()  # the tab in front shows the new document too, not the one that was open
        self.sync()

    def go_to(self, guid: int) -> None:
        if self.m_doc is not None and self.m_doc.exists(guid):
            self.m_tree.select(guid)
            self.show_asset(guid)

    def show_asset(self, guid: int) -> None:
        self.m_guid = guid
        self._fill_tab()
        self.sync()

    def _fill_tab(self) -> None:
        if self.m_doc is None:
            return
        widget, guid = self.m_tabs.currentWidget(), self.m_guid
        if widget is self.m_form:
            self.m_form.show_asset(self.m_doc, guid)
        elif widget is self.m_balancing:
            self.m_balancing.present(self.m_doc, self.m_tree.selected_group() or ([guid] if guid else []))
        else:
            widget.present(self.m_doc, guid)

    def _changed(self, guid) -> None:
        if guid is not None:
            self.m_icon_source.forget(guid)
        self._m_touched.add(guid)
        self._m_refresh.start()  # once per batch of edits (an import makes many)

    def _changed_now(self) -> None:
        touched, self._m_touched = self._m_touched, set()
        if None in touched:
            self.m_tree.fill()
        else:
            self.m_tree.mark(touched)
        if self.m_guid in touched or None in touched:
            if self.m_tabs.currentWidget() is self.m_form:
                self.m_form.refresh()
            else:
                self._fill_tab()
        self.sync()

    def _expert(self, is_expert: bool) -> None:
        self._form_flag("expert", is_expert)

    def _form_flag(self, flag: str, is_enabled: bool) -> None:
        """A switch of the View menu that changes what the form shows (``CAssetForm.m_expert`` and its kin)."""
        setattr(self.m_form, f"m_{flag}", is_enabled)
        self.m_form.refresh()

    # editing
    def _made(self, make):
        """What ``make()`` returns (the new assets), None when it could not make them: the GUID range is used up."""
        try:
            return make()
        except ValueError as error:
            QMessageBox.warning(self, tr(TITLE), str(error))
            return None

    def new_from_template(self) -> None:
        template = self.m_template_box.currentText().strip()
        if template not in self.m_doc.m_schema.templates:
            return self.say(tr("assets.pick_template_above_first"))
        name, is_accepted = QInputDialog.getText(self, tr(TITLE), tr("assets.name_new_template", template=template))
        new = self._made(lambda: self.m_doc.new_asset(template, name.strip())) if is_accepted and name.strip() else None
        if new is not None:
            self.go_to(new)

    def new_from(self, base: bool) -> None:
        guid = self.m_guid
        label = (
            tr("assets.name_new_asset_made", name=self.m_doc.name(guid))
            if base
            else tr("assets.name_new_asset_copy", name=self.m_doc.name(guid))
        )
        name, is_accepted = QInputDialog.getText(self, tr(TITLE), label)
        if is_accepted and name.strip():
            new = self._made(
                lambda: self.m_doc.new_asset(
                    "", name.strip(), base=guid if base else None, copy_of=None if base else guid
                )
            )
            if new is not None:
                self.go_to(new)

    def rename(self) -> None:
        name, is_accepted = QInputDialog.getText(
            self, tr(TITLE), tr("assets.new_name"), text=self.m_doc.name(self.m_guid)
        )
        if is_accepted and name.strip():
            self.m_doc.rename(self.m_guid, name.strip())

    def delete(self) -> None:
        users = compare.usage(self.m_doc, self.m_guid)
        if (
            users
            and QMessageBox.question(self, tr(TITLE), tr("assets.assets_name_one_delete_anyway", n=len(users)))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.m_doc.remove_asset(self.m_guid)
        self.m_guid = None
        self._fill_tab()

    def set_range(self) -> None:
        """Edit > GUID range: own, publicly declared or typed in (``suite.guidrangedialog``); new assets take
        their numbers from it, assets made before keep theirs."""
        changes = self.m_doc.m_changes
        others = project.other_ranges(self.m_settings, project.folder_of(self.m_settings), changes.m_game)
        now = guidrangedialog.SSelection(changes.m_guid_choice, changes.m_guid_source, tuple(changes.m_guid_range))
        picked = guidrangedialog.ask(self, changes.m_game, self.m_settings, others, now, self.m_doc.m_index.m_by_guid)
        if picked is None:
            return
        self.m_doc.set_guid_range(picked.span, picked.choice, picked.source)
        self.m_settings.save(self.m_settings_path)  # the own reserve is made on first use
        self.say(
            tr(
                "assets.guid_range_new_assets",
                lo=number(picked.span[0]),
                hi=number(picked.span[1]),
                choice=picked.choice,
            )
        )
        if changes.outside_range():
            self.say(tr("assets.new_assets_made_earlier_lie", n=len(changes.outside_range())))

    # saving and building
    def save(self) -> bool:
        try:
            path = self.m_doc.save()
        except OSError as error:
            QMessageBox.warning(self, tr(TITLE), tr("assets.could_not_save", exc=error))
            return False
        self.say(tr("common.saved_path", path=path))
        self.sync()
        return True

    def save_as(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, tr(TITLE), str(self.m_doc.m_path or ""), tr("assets.change_sets_json")
        )
        if not name:
            return
        before, self.m_doc.m_path = self.m_doc.m_path, Path(name)
        if self.save():
            self.state.setdefault("edits", {})[self.m_doc.m_changes.m_game] = name
        else:  # nothing was written there: the next open must not look for the change set under that name
            self.m_doc.m_path = before

    def load_changes(self) -> None:
        if not self.confirm_close():
            return
        name, _ = QFileDialog.getOpenFileName(
            self, tr(TITLE), str(self.m_doc.m_path or ""), tr("assets.change_sets_json")
        )
        if not name:
            return
        try:
            changes = CChangeSet.load(Path(name))
        except (OSError, ValueError, TypeError) as error:
            return QMessageBox.warning(self, tr(TITLE), tr("assets.not_change_set", exc=error))
        if changes.m_game != self.m_game.m_layout.key:
            return QMessageBox.warning(self, tr(TITLE), tr("assets.change_set_game", game=changes.m_game))
        self.state.setdefault("edits", {})[changes.m_game] = name
        self._attach(self.m_game.document(changes, Path(name)))

    def mods_folder(self) -> Path | None:
        key = self.m_doc.m_changes.m_game
        folder = self.m_settings.mods if key == "anno117" else self.m_settings.mods1800 if key == "anno1800" else ""
        if folder:
            return Path(folder)
        name = QFileDialog.getExistingDirectory(
            self, tr("assets.title_mods_folder_game", title=tr(TITLE), game=self.m_game.m_layout.name)
        )
        return Path(name) if name else None

    def _run(self, what: str, work, done, is_waiting: bool = False) -> bool:
        """``work()`` off the window's thread under a status line ``what``, then ``done(result)`` here (the exception
        when it raised). One job at a time: a second is said, not started; ``is_waiting``: run it here (a test)."""
        if self.m_working and not is_waiting:
            self.say(tr("assets.still_busy_what", what=self.m_working))
            return False
        self.m_working = what
        self.say(tr("assets.what", what=what))

        def finish(result) -> None:
            self.m_working = ""
            done(result)

        self.m_background.run(work, finish, is_waiting)
        return True

    def _failed(self, error: Exception) -> None:
        QMessageBox.warning(self, tr(TITLE), tr("islands.failed_error", error=f"{type(error).__name__}: {error}"))

    def build_mod(self, mods: Path | None = None, is_waiting: bool = False) -> Path | None:
        """The ModOps mod into the game's mods folder (after the check), both off the window's thread; ``is_waiting``: here,
        and the mod's folder comes back. The check and the write read the same snapshot of the document."""
        built: list[Path] = []
        if (
            self.m_doc.m_layer is not None
        ):  # the edits are on top of the project's mod: its build writes them (Project window)
            self.say(tr("assets.layered_build_in_project"))
            return None
        snapshot = self.m_doc.snapshot()

        def checked(found) -> None:
            if isinstance(found, Exception):
                return self._failed(found)
            errors = [finding for finding in found if finding.severity == checks.ERROR]
            if errors:
                self.m_findings.present(errors)
                self.m_docks["Checks"].raise_()
                answer = QMessageBox.question(
                    self, tr(TITLE), tr("assets.check_found_errors_build_anyway", n=len(errors))
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            target = mods or self.mods_folder()
            if target is not None:
                self._write_mod(snapshot, target, is_waiting, built)

        self._run(tr("assets.checking"), lambda: checks.final_check(snapshot), checked, is_waiting)
        return built[0] if built else None

    def _write_mod(self, doc: CAssetDocument, mods: Path, is_waiting: bool, built: list[Path]) -> None:
        def written(result) -> None:
            if isinstance(result, ValueError):  # the imported mod's own folder: never written over
                name = QFileDialog.getExistingDirectory(
                    self, tr("assets.another_folder_mod", title=tr(TITLE), reason=result)
                )
                if name and Path(name).resolve() != Path(mods).resolve():
                    self._write_mod(doc, Path(name), is_waiting, built)
                return
            if isinstance(result, Exception):
                return self._failed(result)
            folder, problems = result
            built.append(folder)
            self.m_findings.present([checks.SFinding(None, "mod", checks.WARNING, problem) for problem in problems])
            self.say(
                tr(
                    "assets.wrote_asset_values_texts_follow",
                    folder=folder,
                )
            )
            linkannounce.announce_in_background(
                self.m_background,
                self.say,
                lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS), mod_id=folder.name),
            )

        self._run(
            tr("assets.writing_mod"), lambda: modops.write_mod(doc, mods, game=self.m_game.m_files), written, is_waiting
        )

    def write_native(self, folder: Path | None = None, is_waiting: bool = False) -> None:
        """The game's own files of the change set into ``folder`` (asked for), written off the window's thread from a
        snapshot of the document: an edit made meanwhile is not in them."""
        if folder is None:
            name = QFileDialog.getExistingDirectory(self, tr("assets.folder_games_files", title=tr(TITLE)))
            if not name:
                return
            folder = Path(name)
        doc = self.m_doc.snapshot()

        def work() -> list[Path] | None:
            files = native.render(doc)
            if not files:
                return None
            written = native.write_tree(files, folder)
            if doc.m_changes.m_game in installs.keys(lambda g: g.family == installs.OLD):  # the RDA V2.0 games
                native.write_archive(files, folder / f"{doc.m_changes.m_mod_id}.rda")
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

        self._run(tr("assets.writing_files"), work, done, is_waiting)

    def run_check(self, final: bool = False, is_waiting: bool = False) -> list | None:
        """The sanity check (``final``: with the other mods too) off the window's thread; the findings come back with
        ``is_waiting`` only."""
        found: list = []

        def done(result) -> None:
            if isinstance(result, Exception):
                return self._failed(result)
            found.extend(result)
            self.m_findings.present(result)
            self.m_docks["Checks"].raise_()
            self.say(tr("assets.n_findings", n=len(result)))

        mods = self.mods_folder() if final and "modops" in self.m_game.m_layout.outputs else None
        doc = self.m_doc.snapshot()  # the window goes on editing the document while the check reads
        work = (lambda: checks.final_check(doc, mods)) if final else (lambda: checks.check(doc))
        self._run(tr("assets.checking"), work, done, is_waiting)
        return found if is_waiting else None

    # closing
    def confirm_close(self) -> bool:
        if self.m_doc is None or not self.m_doc.dirty:
            return True
        return widgets.ask_keep(self, tr(TITLE), tr("assets.save_change_set_first"), self.save)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about a change set, and no while a job reads the index."""
        if not self.confirm_close():
            return False
        if self.m_working:  # the job reads the game's index: closing it under the job would break the job
            self.say(tr("assets.still_busy_what", what=self.m_working))
            return False
        return True

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        if self.m_game is not None:
            self.m_game.m_index.close()
        super().closeEvent(event)


def run(game: str | None = None, settings_path: Path | None = None) -> int:
    """The asset editor alone (the suite's shell holds it as a page otherwise)."""
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setStyle("Fusion")
    path = settings_path or settings_file()
    window = CAssetWindow(CSettings.load(path), path)
    window.show()
    if game:
        QTimer.singleShot(0, lambda: window.open(game))
    code = application.exec()
    window.m_settings.save(path)
    return code
