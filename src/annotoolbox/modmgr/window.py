"""The mod manager's window (Qt): the installed mods of a game as a tree with a check box each, the profile open
above it, the details of the selected mod beside it, and the load order, the problems and the last start's log below.

``CModManagerWindow(settings, settings_path)`` is the suite's Mods app; ``run`` starts it alone. All logic is in
``manager.CModManager``; this module shows it and asks before anything is written. docs/mod-manager.md is the guide.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..modloader.activations import ACTIVATIONS
from ..modloader.loadlog import SLoadLog
from ..modloader.profilefile import CProfileText
from ..suite import gamesync, importdialog, links, project, widgets
from ..suite.background import CBackground, CJobTalk, CStopped
from . import catalog, details, gameguard, install
from .checks import PROBLEM, SIssue, SPlan
from .installspage import CInstallsPage
from .manager import GAMES, CModManager, for_settings
from .modtree import CModTree
from .orderview import CIssueView, CLogView, COrderView
from .profiles import CProfileError

APP = "mods"  # the key of its remembered state in the settings
TITLE = N_("mods.mod_manager")
GUARD_MS = 5000  # how often the game is looked for
WARN_STYLE = "background:#fff3cd;color:#664d03;padding:6px;border:1px solid #e0c36a"


class CModManagerWindow(QMainWindow):
    def __init__(
        self, settings, settings_path: Path | None = None, is_synchronous: bool = False, **manager_args
    ) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_manager_args = manager_args  # the process lister and the trash of a test
        self.m_manager: CModManager | None = None
        self.m_profile = ""  # the profile open (not always the active one)
        self.m_deep: list[SIssue] = []  # the last deep check's findings, for the profile shown as it is now
        self.m_deep_stamp = 0  # counts what makes them stale: another profile, game or scan, an edit of the profile
        self.m_talk: CJobTalk | None = None  # the running install's or check's progress and stop
        self.m_background = CBackground(self)
        self.m_is_synchronous = (
            is_synchronous  # run the background jobs here and now (a test that wants the result at once)
        )
        self.checking = False  # a slow job (check, install) is running
        self.m_plan: SPlan | None = None
        self.m_text: CProfileText | None = None  # the open profile and the last start's log as the last refresh read
        self.m_log: SLoadLog | None = None  # them: a click on a mod shows from these, it reads no file
        self._m_building = False
        self._m_guarding = False  # a look for the game is on its way (it takes half a second)
        self._m_warned = ""  # what the last look said
        self._build()
        self._menus()
        saved = settings.apps.get(APP, {})
        game = saved.get("game") if saved.get("game") in GAMES else self._first_game()
        self.open_game(game, saved.get("profile", ""))
        self.setWindowTitle(tr(TITLE))
        self.m_timer = QTimer(self, interval=GUARD_MS)
        self.m_timer.timeout.connect(self.update_guard)

    # the window

    def _build(self) -> None:
        bar = QToolBar(tr("mods.profile"), self, movable=False)
        self.addToolBar(bar)
        self.m_game_box = QComboBox()
        for key, name in GAMES.items():
            self.m_game_box.addItem(name, key)
        self.m_profile_box = QComboBox()
        self.m_profile_box.setMinimumWidth(200)
        self.m_new_mods = QCheckBox("EnableNewMods")  # i18n: ok (the loader's setting name)
        self.m_new_mods.setToolTip(tr("mods.tip.mods_profile_not_list_start"))
        for before, label, widget in (
            ("", N_("common.game"), self.m_game_box),
            (" ", N_("mods.profile"), self.m_profile_box),
        ):
            bar.addWidget(QLabel(f"{before}{tr(label)} "))
            bar.addWidget(widget)
        bar.addWidget(QLabel("  "))
        bar.addWidget(self.m_new_mods)
        self.m_guard = QLabel(objectName="guard", wordWrap=True)
        self.m_guard.setStyleSheet(WARN_STYLE)
        self.m_guard.hide()
        self.m_tree = CModTree()
        self.m_detail = QTextBrowser()
        self.m_detail.setOpenLinks(False)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.m_tree)
        top.addWidget(self.m_detail)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        self.m_order, self.m_issues, self.m_logview = COrderView(), CIssueView(), CLogView()
        self.m_log_summary = QLabel(wordWrap=True)
        log_page = QWidget()
        layout = QVBoxLayout(log_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.m_log_summary)
        layout.addWidget(self.m_logview)
        self.m_tabs = QTabWidget()
        self.m_tabs.addTab(self.m_order, tr("mods.load_order"))
        self.m_tabs.addTab(self.m_issues, tr("mods.problems"))
        self.m_tabs.addTab(log_page, tr("mods.last_start"))
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.m_tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        body = QWidget()
        box = QVBoxLayout(body)
        box.addWidget(self.m_guard)
        box.addWidget(split)
        self.m_toolbar = bar
        self.m_installs = CInstallsPage(
            self.m_settings, self.m_background, self.statusBar().showMessage, self.m_is_synchronous
        )
        self.m_pages = QTabWidget()
        self.m_pages.addTab(body, tr("common.mods"))
        self.m_pages.addTab(self.m_installs, tr("common.installs"))
        self.m_pages.currentChanged.connect(
            lambda page: self.m_toolbar.setVisible(page == 0)
        )  # the bar is the mods' own
        self.setCentralWidget(self.m_pages)
        self.m_game_box.currentIndexChanged.connect(lambda _n: self._game_chosen())
        self.m_profile_box.currentIndexChanged.connect(lambda _n: self._profile_chosen())
        self.m_new_mods.toggled.connect(self._enable_new_toggled)
        self.m_tree.toggled.connect(self._mod_toggled, Qt.ConnectionType.QueuedConnection)  # the tree is rebuilt in it
        self.m_tree.selected.connect(self._show_card)
        self.statusBar().showMessage("")
        self.m_stop_button = widgets.stop_button(self, self.stop_job)

    def _menus(self) -> None:
        def add(menu, text: str, slot, keys: str = "", tip: str = "") -> QAction:
            action = menu.addAction(text)
            action.triggered.connect(lambda _c=False: slot())
            if keys:
                action.setShortcut(QKeySequence(keys))
            if tip:
                action.setStatusTip(tip)
            return action

        profile = self.menuBar().addMenu(tr("mods.menu.profile"))
        add(profile, tr("mods.new"), self.new_profile)
        add(profile, tr("mods.copy"), self.copy_profile)
        add(profile, tr("mods.rename"), self.rename_profile)
        add(profile, tr("mods.delete"), self.delete_profile)
        profile.addSeparator()
        self.m_activate_action = add(
            profile, tr("mods.make_active"), self.make_active, tip=tr("mods.tip.profile_game_starts")
        )
        add(
            profile,
            tr("mods.no_mods_next_start"),
            self.deactivate,
            tip=tr("mods.tip.empty_activeprofile_game_starts_clean"),
        )
        mods = self.menuBar().addMenu(tr("mods.menu.mods"))
        self.m_install_actions = [
            add(mods, tr("mods.install_mod_folder"), self.install_folder),
            add(mods, tr("mods.install_zip"), self.install_zip),
            add(
                mods,
                tr("mods.install_built_project"),
                self.install_project,
                tip=tr("mods.tip.open_projects_build_project_window"),
            ),
        ]
        self.m_remove_action = add(
            mods, tr("mods.remove_selected_mod"), self.remove_selected, "Del", tip=tr("mods.tip.to_recycle_bin")
        )
        self.m_import_action = add(
            mods,
            tr("common.import_selected_mod"),
            self.import_selected,
            tip=tr("common.import_selected_mod_tip"),
        )
        self.m_menu_tips = {
            action: action.statusTip()
            for action in (*self.m_install_actions, self.m_remove_action, self.m_import_action)
        }
        mods.addSeparator()
        add(mods, tr("mods.rescan"), self.rescan, "F5")
        add(mods, tr("mods.open_mods_folder"), self.open_folder)
        check = self.menuBar().addMenu(tr("common.menu.check"))
        self.m_check_action = add(
            check,
            tr("mods.check_mods"),
            self.deep_check,
            "F6",
            tr("mods.mod_lint_every_operation_games"),
        )
        check.addSeparator()
        self.m_diff_mod_action = add(
            check, tr("mods.compare_selected_mod_diff"), self.diff_mod, tip=tr("mods.tip.what_changes_game")
        )
        self.m_diff_profile_action = add(
            check, tr("mods.compare_mods_mod_diff"), self.diff_profile, tip=tr("mods.tip.where_they_collide")
        )
        check.aboutToShow.connect(self._diff_actions)
        game = self.menuBar().addMenu(tr("mods.menu.game"))
        add(
            game,
            tr("mods.reload_running_game"),
            self.reload_game,
            "Shift+F3",
            tr("mods.asks_running_game_hot_reload"),
        )

    def _first_game(self) -> str:
        have = [k for k in GAMES if str(getattr(self.m_settings, k, "") or "").strip()]
        return (have or list(GAMES))[0]

    # a game and its profiles

    def open_game(self, key: str, profile: str = "") -> None:
        """Show the mods of ``key`` (a new manager from the settings), the profile ``profile`` if it exists."""
        if not gamesync.may_switch(self, key):  # another app's question was cancelled: the box goes back
            self._m_building = True
            self.m_game_box.setCurrentIndex(self.m_game_box.findData(self.m_manager.m_game))
            self._m_building = False
            return
        self._drop_deep()
        self.m_manager = for_settings(self.m_settings, key, **self.m_manager_args)
        self.m_manager.m_deferred = (
            self.m_background,
            self.statusBar().showMessage,
        )  # the game's answer takes its time
        self._m_building = True
        self.m_game_box.setCurrentIndex(self.m_game_box.findData(key))
        self._m_building = False
        self.m_manager.scan()
        self._fill_profiles(profile)
        self.refresh()

    def _game_chosen(self) -> None:
        if not self._m_building:
            self.open_game(self.m_game_box.currentData())

    def _fill_profiles(self, want: str = "") -> None:
        manager = self.m_manager
        names, active = manager.profile_names(), manager.active()
        self._m_building = True
        self.m_profile_box.clear()
        for name in names:
            self.m_profile_box.addItem(
                name + ("  " + tr("mods.active") if name.lower() == active.lower() else ""), name
            )
        spelled = {
            profile_name.lower(): profile_name for profile_name in names
        }  # ``profile.txt`` may spell the name in another case
        pick = next(
            (
                spelled[profile_name.lower()]
                for profile_name in (want, active, *names)
                if profile_name.lower() in spelled
            ),
            "",
        )
        if pick != self.m_profile:
            self._drop_deep()
        self.m_profile = pick
        if pick:
            self.m_profile_box.setCurrentIndex(self.m_profile_box.findData(pick))
        self._m_building = False

    def _profile_chosen(self) -> None:
        if not self._m_building:
            self.m_profile = self.m_profile_box.currentData() or ""
            self._drop_deep()
            self.refresh()

    def _drop_deep(self) -> None:
        """The deep check's findings are of the profile as it was checked: gone with any change of it."""
        self.m_deep, self.m_deep_stamp = [], self.m_deep_stamp + 1

    # showing

    def refresh(self) -> None:
        """Read everything again for the open game and profile and show it."""
        manager = self.m_manager
        text = manager.read(self.m_profile)
        plan = manager.plan(self.m_profile)
        plan.issues = plan.issues + self.m_deep
        log = manager.log()
        self.m_plan, self.m_text, self.m_log = plan, text, log
        self.m_tree.set_data(manager.m_catalog, text, plan, log, editable=text is not None)
        self.m_order.set_plan(plan)
        self.m_issues.set_issues(plan.issues)
        problems = len(plan.problems)
        self.m_tabs.setTabText(1, tr("mods.problems_n", n=problems) if problems else tr("mods.problems"))
        self.m_log_summary.setText(self.m_logview.set_log(log))
        self._m_building = True
        self.m_new_mods.setChecked(bool(text and text.enable_new))
        self.m_new_mods.setEnabled(text is not None)
        self._m_building = False
        is_active = self.m_profile.lower() == manager.active().lower()
        self.m_activate_action.setEnabled(bool(self.m_profile) and not is_active)
        self.statusBar().showMessage(self._summary(plan, is_active))
        self.update_guard()
        self._show_card(self.m_tree.current_card())

    def _summary(self, plan: SPlan, is_active: bool) -> str:
        """The status line of a refresh: the mods installed and on, the open profile, the problems, the folder."""
        if not self.m_profile:
            where = tr("mods.no_profile_open")
        elif is_active:
            where = tr("mods.profile_name_active", name=self.m_profile)
        else:
            where = tr("mods.profile_name", name=self.m_profile)
        return tr(
            "mods.mods_installed_problems_folder",
            mods=len(self.m_manager.m_catalog.mods),
            on=len([row for row in plan.rows if row.mod.m_parent is None]),
            where=where,
            problems=len(plan.problems),
            folder=self.m_manager.m_mods_dir,
        )

    @property
    def checking(self) -> bool:
        """A slow job (check, install, remove) is running; the actions that would start another say so."""
        return self._m_checking

    @checking.setter
    def checking(self, running: bool) -> None:
        self._m_checking = running
        self._actions_state()

    def _actions_state(self) -> None:
        """Install and Remove are on when no job runs (Remove: and a mod is picked) and say why they are not."""
        if not hasattr(self, "m_menu_tips"):
            return  # the menus are not made yet
        busy = tr("mods.tip.job_running") if self._m_checking else ""
        for action in self.m_install_actions:
            widgets.enable_unless(action, busy, self.m_menu_tips[action])
        picked = self.m_tree.current_card() is not None
        widgets.enable_unless(
            self.m_remove_action,
            busy or ("" if picked else tr("mods.tip.pick_mod_first")),
            self.m_menu_tips[self.m_remove_action],
        )
        widgets.enable_unless(
            self.m_import_action,
            busy or ("" if picked else tr("mods.tip.pick_mod_first")),
            self.m_menu_tips[self.m_import_action],
        )

    def _show_card(self, card: catalog.SModCard | None) -> None:
        """The details of ``card`` from what the last refresh read (no file is read for a click)."""
        self._actions_state()
        if card is None:
            self.m_detail.setHtml(tr("mods.pick_mod_left"))
            return
        run = self.m_log.run_of(card.mod_id) if self.m_log is not None and card.mod_id else None
        issues = self.m_plan.issues_of(card.mod_id) if self.m_plan and card.mod_id else []
        self.m_detail.setHtml(details.html(details.rows(card, catalog.state_of(card, self.m_text), issues, run)))

    def update_guard(self) -> None:
        """The banner that says the game is running (and what a profile change then does). The look (the process
        list, the live link) runs off the window's thread; one at a time."""
        if self.m_manager is None or self._m_guarding:
            return
        self._m_guarding = True
        manager, profile = self.m_manager, self.m_profile
        self.m_background.run(
            lambda: manager.warning(profile),
            lambda text: self._guard_found(manager, text),
            is_waiting=self.m_is_synchronous,
        )

    def _guard_found(self, manager: CModManager, text) -> None:
        self._m_guarding = False
        if manager is not self.m_manager or isinstance(text, Exception):  # another game is open now: the next look
            return
        self._m_warned = text
        self.m_guard.setText(text)
        self.m_guard.setVisible(bool(text))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.m_timer.start()

    def hideEvent(self, event) -> None:
        self.m_timer.stop()
        super().hideEvent(event)

    # changes: each asks first while the game runs

    def _allowed(self) -> bool:
        warn = (
            self._m_warned
        )  # the last look, at most one timer tick old: asking the system here would block the window
        if not warn:
            return True
        answer = QMessageBox.question(self, tr(TITLE), warn + "\n\n" + tr("mods.change_profile_anyway"))
        return answer == QMessageBox.StandardButton.Yes

    def _failed(self, error: Exception) -> None:
        QMessageBox.warning(self, tr(TITLE), str(error))
        self.statusBar().showMessage(str(error))

    def _change_profile(self, change) -> None:
        """Run ``change(profile)`` (a manager call that writes the open profile) after the game-is-running question,
        show the result and the game link's sentence."""
        if self._m_building or not self.m_profile:
            return
        if not self._allowed():
            self.refresh()
            return
        notice = ""
        self._drop_deep()
        try:
            notice = change(self.m_profile)
        except (CProfileError, OSError) as error:  # a profile file the game holds open cannot be written
            self._failed(error)
        self.refresh()
        if notice:
            self.statusBar().showMessage(notice)

    def _mod_toggled(self, mod_id: str, is_on: bool) -> None:
        self._change_profile(lambda profile: self.m_manager.set_mod(profile, mod_id, is_on))

    def _enable_new_toggled(self, is_on: bool) -> None:
        self._change_profile(lambda profile: self.m_manager.set_enable_new(profile, is_on))

    # the profiles

    def _ask_name(self, title: str, label: str, start: str = "") -> str:
        name, is_accepted = QInputDialog.getText(self, title, label, text=start)
        return name.strip() if is_accepted else ""

    def _profile_job(self, job: Callable[[], object], then: str = "") -> bool:
        """Run a change of the profiles folder, then show the profile ``then`` (default: the open one). False when
        it failed (said in a box and the status line)."""
        try:
            job()
        except (CProfileError, OSError) as error:
            self._failed(error)
            return False
        self._fill_profiles(then or self.m_profile)
        self.refresh()
        return True

    def new_profile(self) -> None:
        name = self._ask_name(tr("mods.new_profile"), tr("mods.name_new_empty_profile"))
        if name:
            self._profile_job(lambda: self.m_manager.create_profile(name), name)

    def copy_profile(self) -> None:
        if not self.m_profile:
            return
        name = self._ask_name(
            tr("mods.copy_profile"),
            tr("mods.name_copy_colon", name=self.m_profile),
            tr("mods.name_copy", name=self.m_profile),
        )
        if name:
            self._profile_job(lambda: self.m_manager.create_profile(name, self.m_profile), name)

    def rename_profile(self) -> None:
        if not self.m_profile or not self._allowed():
            return
        name = self._ask_name(tr("mods.rename_profile"), tr("mods.new_name", name=self.m_profile), self.m_profile)
        if name and name != self.m_profile:
            self._profile_job(lambda: self.m_manager.m_store.rename(self.m_profile, name), name)

    def delete_profile(self) -> None:
        if not self.m_profile:
            return
        ask = QMessageBox.question(self, tr(TITLE), tr("mods.delete_profile_cannot_undone", name=self.m_profile))
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.m_manager.m_store.delete(self.m_profile), "")

    def make_active(self) -> None:
        if self.m_profile and self._allowed():
            if self._profile_job(lambda: self.m_manager.m_store.set_active(self.m_profile)):
                self.statusBar().showMessage(tr("mods.active_profile_game_uses_next", name=self.m_profile))

    def deactivate(self) -> None:
        if not self._allowed():
            return
        ask = QMessageBox.question(self, tr(TITLE), tr("mods.leave_activeprofile_empty_game_starts"))
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.m_manager.m_store.set_active(""))

    # the mods

    def rescan(self) -> None:
        self.open_game(self.m_game_box.currentData(), self.m_profile)

    def _installed(self, results: list[install.SInstalled]) -> None:
        notes = [note for result in results for note in result.notes]
        names = ", ".join(
            f"{result.mod_id}{' ' + tr('mods.replaced') if result.replaced else ''}" for result in results
        )
        self.rescan()
        said = " ".join(dict.fromkeys(result.notice for result in results if result.notice))
        self.statusBar().showMessage(
            tr("mods.installed_names", names=names)
            + (f"; {' '.join(notes)}" if notes else "")
            + (f". {said}" if said else "")
        )
        if notes:
            QMessageBox.information(self, tr(TITLE), "\n".join(notes))

    def install_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("mods.install_mod_folder_mods"))
        if folder:
            self._install(Path(folder))

    def install_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("mods.install_mod_zip"), "", tr("mods.zip_files"))
        if path:
            self._install(Path(path))

    def _install(self, source: Path) -> None:
        """Copy off the window's thread: a large mod is many files. The status line says each step, Stop ends it
        between two of them."""
        if self.checking:
            return
        manager, placed = self.m_manager, []
        talk = self._job_started(tr("mods.installing_name", name=source.name))
        self.m_background.run(
            lambda: manager.install(source, talk.say, placed),
            lambda found: self._install_done(found, placed),
            self.m_is_synchronous,
        )

    def _install_done(self, found, placed: list[install.SInstalled] | None = None) -> None:
        self._job_ended()
        if isinstance(found, CStopped):
            self.rescan()
            names = ", ".join(one.mod_id for one in placed or [])
            said = tr("mods.install_stopped_installed", names=names) if names else tr("mods.install_stopped")
            self.statusBar().showMessage(said)
        elif isinstance(found, Exception):
            self._failed(found)
        else:
            self._installed(found)

    def _job_started(self, text: str) -> CJobTalk:
        """A slow job begins: its progress goes to the status line, and the Stop button is there for it."""
        self.checking = True
        self.m_talk = CJobTalk(self)
        self.m_talk.said.connect(self.statusBar().showMessage)
        self.m_stop_button.setEnabled(True)
        self.m_stop_button.show()
        self.statusBar().showMessage(text)
        return self.m_talk

    def _job_ended(self) -> None:
        self.checking = False
        self.m_stop_button.hide()
        if self.m_talk is not None:
            self.m_talk.said.disconnect()  # a report still on its way must not write over the result
            self.m_talk.deleteLater()
            self.m_talk = None

    def stop_job(self) -> None:
        """Stop the running install or check at its next step."""
        if self.m_talk is not None:
            self.m_talk.stop()
            self.m_stop_button.setEnabled(False)
            self.statusBar().showMessage(tr("mods.stopping"))

    def install_project(self) -> None:
        current_project = project.current(self.m_settings)
        if current_project.game != self.m_manager.m_game:
            self._failed(
                ValueError(
                    tr(
                        "mods.open_project_s_mods_folder",
                        project_game=current_project.game,
                        game=self.m_manager.m_game,
                    )
                )
            )
            return
        if self.checking:
            return
        self.checking = True
        mods_dir = self.m_manager.m_mods_dir
        self.statusBar().showMessage(tr("mods.installing_projects_build"))
        # hashing and copying the mod, then the game's answer to the announcement, all off the window's thread
        self.m_background.run(
            lambda: project.install(current_project, mods_dir, lambda _t: None),
            self._project_installed,
            self.m_is_synchronous,
        )

    def _project_installed(self, where) -> None:
        self.checking = False
        if isinstance(where, Exception):
            self._failed(where)
            return
        self.rescan()
        self.statusBar().showMessage(tr("mods.installed_projects_build", where=where))

    def import_selected(self, target: Path | None = None) -> None:
        """Import the selected mod as a new project (a copy: the mod stays as it is), off the window's thread; Stop
        ends it. ``target``: the project's folder, else asked for."""
        card = self.m_tree.current_card()
        if card is None or self.checking:
            return
        target = target or importdialog.ask_target(self, card.folder)
        if target is None:
            return
        talk = self._job_started(tr("common.import_reading", name=card.folder.name))
        importdialog.start(
            talk, self.m_background, card.folder, target, self.m_settings, self._imported, self.m_is_synchronous
        )

    def _imported(self, result) -> None:
        self._job_ended()
        lines = importdialog.explain(result)
        self.statusBar().showMessage(lines[0])
        if isinstance(result, Exception):
            if not isinstance(result, CStopped):
                self._failed(result)
            return
        self.m_settings.project = str(result.folder)  # the apps work on the imported project from now on
        if self.m_settings_path:
            self.m_settings.save(self.m_settings_path)
        QMessageBox.information(self, tr(TITLE), "\n".join(lines))

    def remove_selected(self) -> None:
        card = self.m_tree.current_card()
        if card is None:
            return
        while card.mod is not None and card.mod.m_parent is not None:  # a nested mod goes with its whole mod
            card = next(candidate for candidate in self.m_manager.m_catalog.cards if candidate.mod is card.mod.top)
        ask = QMessageBox.question(
            self,
            tr(TITLE),
            tr("mods.move_recycle_bin", name=card.folder.name) + f"\n{card.folder}",
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        if self.checking:
            return
        self.checking = True
        manager = self.m_manager
        self.statusBar().showMessage(tr("common.moving_recycle_bin", name=card.folder.name))
        # the move can take a while for a big mod; the announcement to the game is made back here, off no thread of ours
        self.m_background.run(
            lambda: manager.remove(card, announce=False),
            lambda where: self._removed(manager, card, where),
            self.m_is_synchronous,
        )

    def _removed(self, manager: CModManager, card: catalog.SModCard, where: str | Exception) -> None:
        """``manager``: the one the mod was removed from (another game may be open by now)."""
        self.checking = False
        if isinstance(where, Exception):
            self._failed(where)
            return
        manager.announce_removal(card)
        self.rescan()
        moved = tr("mods.name_moved_to_where", name=card.folder.name, where=where)
        self.statusBar().showMessage(f"{moved} {manager.m_notice}".rstrip())

    def open_folder(self) -> None:
        self.m_manager.m_mods_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.m_manager.m_mods_dir)))

    # the slow check and the running game

    def deep_check(self) -> None:
        """The slow checks of the profile shown, off the window's thread: the status line names the mod it is at,
        Stop ends it before the next one."""
        if self.checking or not self.m_profile:
            return
        self.m_check_action.setEnabled(False)
        manager, profile, stamp = self.m_manager, self.m_profile, self.m_deep_stamp
        talk = self._job_started(tr("mods.checking_mods_lint_operations_games"))
        self.m_background.run(
            lambda: manager.deep_check(profile, talk.say),
            lambda found: self._checked(stamp, found),
            self.m_is_synchronous,
        )

    def _checked(self, stamp: int, found) -> None:
        self._job_ended()
        self.m_check_action.setEnabled(True)
        if isinstance(found, CStopped):
            self.statusBar().showMessage(tr("mods.check_stopped"))
            return
        if isinstance(found, Exception):
            self._failed(found)
            return
        if stamp != self.m_deep_stamp:
            self.statusBar().showMessage(tr("mods.check_of_changed_profile"))
            return
        self.m_deep = found
        self.refresh()
        bad = len([i for i in found if i.level == PROBLEM])
        self.m_tabs.setCurrentIndex(1)
        self.statusBar().showMessage(tr("mods.check_done_problems_notes_deep", bad=bad, notes=len(found) - bad))

    def _diff_actions(self) -> None:
        """The Mod diff entries show only where the suite has a Mod diff to answer them."""
        there = links.available(links.DIFF_MODS)
        card = self.m_tree.current_card()
        self.m_diff_mod_action.setVisible(there)
        self.m_diff_profile_action.setVisible(there)
        self.m_diff_mod_action.setEnabled(card is not None and card.mod is not None)
        self.m_diff_profile_action.setEnabled(self.m_manager is not None and bool(self.m_plan and self.m_plan.order))

    def diff_mod(self) -> None:
        """The selected mod against the game, in the Mod diff."""
        card = self.m_tree.current_card()
        if card is not None and card.mod is not None:
            self._ask_diff(links.MODE_MOD, mod_id=card.mod_id)

    def diff_profile(self) -> None:
        """The mods the open profile switches on, run in the loader's order, in the Mod diff (1800: all mods that are on)."""
        profile = self.m_profile if self.m_manager.m_game != "anno1800" else ACTIVATIONS
        self._ask_diff(links.MODE_PROFILE, profile=profile)

    def _ask_diff(self, mode: str, mod_id: str = "", profile: str = "") -> None:
        if not links.diff_mods(mode, self.m_manager.m_game, mod_id, "", profile):
            self.statusBar().showMessage(tr("mods.mod_diff_not_part_window"))

    def reload_game(self) -> None:
        active = self.m_manager.active()
        self.statusBar().showMessage(tr("mods.asking_game_reload"))  # it answers within seconds, up to minutes
        self.m_background.run(
            gameguard.reload_in_game, lambda reply: self._reloaded(reply, active), self.m_is_synchronous
        )

    def _reloaded(self, reply, active: str) -> None:
        if isinstance(reply, Exception):
            self._failed(reply)
            return
        message = gameguard.reload_text(reply, active)
        self.statusBar().showMessage(message)
        QMessageBox.information(self, tr(TITLE), message)

    def closeEvent(self, event) -> None:
        self.m_timer.stop()
        if self.m_manager is not None:
            self.m_settings.apps[APP] = {"game": self.m_manager.m_game, "profile": self.m_profile}
        super().closeEvent(event)


def run() -> int:
    from ..suite.settings import CSettings, settings_file

    application = QApplication.instance() or QApplication([sys.argv[0]])
    path = settings_file()
    window = CModManagerWindow(CSettings.load(path), path)
    window.resize(1400, 900)
    window.show()
    return application.exec()
