"""The mod manager's window (Qt): the installed mods of a game as a tree with a check box each, the profile open
above it, the details of the selected mod beside it, and the load order, the problems and the last start's log below.

``ModManagerWindow(settings, settings_path)`` is the suite's Mods app; ``run`` starts it alone. All logic is in
``manager.ModManager``; this module shows it and asks before anything is written. docs/mod-manager.md is the guide.
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
from ..modloader.loadlog import LoadLog
from ..modloader.profilefile import ProfileText
from ..suite import gamesync, importdialog, links, project, widgets
from ..suite.background import Background, JobTalk, Stopped
from . import catalog, details, gameguard, install
from .checks import PROBLEM, Issue, Plan
from .installspage import InstallsPage
from .manager import GAMES, ModManager, for_settings
from .modtree import ModTree
from .orderview import IssueView, LogView, OrderView
from .profiles import ProfileError

APP = "mods"  # the key of its remembered state in the settings
TITLE = N_("mods.mod_manager")
GUARD_MS = 5000  # how often the game is looked for
WARN_STYLE = "background:#fff3cd;color:#664d03;padding:6px;border:1px solid #e0c36a"


class ModManagerWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None, sync: bool = False, **manager_args) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.manager_args = manager_args  # the process lister and the trash of a test
        self.manager: ModManager | None = None
        self.profile = ""  # the profile open (not always the active one)
        self.deep: list[Issue] = []  # the last deep check's findings, for the profile shown as it is now
        self.deep_stamp = 0  # counts what makes them stale: another profile, game or scan, an edit of the profile
        self.talk: JobTalk | None = None  # the running install's or check's progress and stop
        self.background = Background(self)
        self.sync = sync  # run the background jobs here and now (a test that wants the result at once)
        self.checking = False  # a slow job (check, install) is running
        self.plan: Plan | None = None
        self.text: ProfileText | None = None  # the open profile and the last start's log as the last refresh read
        self.log: LoadLog | None = None  # them: a click on a mod shows from these, it reads no file
        self._building = False
        self._guarding = False  # a look for the game is on its way (it takes half a second)
        self._warned = ""  # what the last look said
        self._build()
        self._menus()
        saved = settings.apps.get(APP, {})
        game = saved.get("game") if saved.get("game") in GAMES else self._first_game()
        self.open_game(game, saved.get("profile", ""))
        self.setWindowTitle(tr(TITLE))
        self.timer = QTimer(self, interval=GUARD_MS)
        self.timer.timeout.connect(self.update_guard)

    # the window

    def _build(self) -> None:
        bar = QToolBar(tr("mods.profile"), self, movable=False)
        self.addToolBar(bar)
        self.game_box = QComboBox()
        for key, name in GAMES.items():
            self.game_box.addItem(name, key)
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(200)
        self.new_mods = QCheckBox("EnableNewMods")  # i18n: ok (the loader's setting name)
        self.new_mods.setToolTip(tr("mods.tip.mods_profile_not_list_start"))
        for before, label, widget in (
            ("", N_("common.game"), self.game_box),
            (" ", N_("mods.profile"), self.profile_box),
        ):
            bar.addWidget(QLabel(f"{before}{tr(label)} "))
            bar.addWidget(widget)
        bar.addWidget(QLabel("  "))
        bar.addWidget(self.new_mods)
        self.guard = QLabel(objectName="guard", wordWrap=True)
        self.guard.setStyleSheet(WARN_STYLE)
        self.guard.hide()
        self.tree = ModTree()
        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.tree)
        top.addWidget(self.detail)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        self.order, self.issues, self.logview = OrderView(), IssueView(), LogView()
        self.log_summary = QLabel(wordWrap=True)
        log_page = QWidget()
        lay = QVBoxLayout(log_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.log_summary)
        lay.addWidget(self.logview)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.order, tr("mods.load_order"))
        self.tabs.addTab(self.issues, tr("mods.problems"))
        self.tabs.addTab(log_page, tr("mods.last_start"))
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        body = QWidget()
        box = QVBoxLayout(body)
        box.addWidget(self.guard)
        box.addWidget(split)
        self.toolbar = bar
        self.installs = InstallsPage(self.settings, self.background, self.statusBar().showMessage, self.sync)
        self.pages = QTabWidget()
        self.pages.addTab(body, tr("common.mods"))
        self.pages.addTab(self.installs, tr("common.installs"))
        self.pages.currentChanged.connect(lambda n: self.toolbar.setVisible(n == 0))  # the bar is the mods' own
        self.setCentralWidget(self.pages)
        self.game_box.currentIndexChanged.connect(lambda _n: self._game_chosen())
        self.profile_box.currentIndexChanged.connect(lambda _n: self._profile_chosen())
        self.new_mods.toggled.connect(self._enable_new_toggled)
        self.tree.toggled.connect(self._mod_toggled, Qt.ConnectionType.QueuedConnection)  # the tree is rebuilt in it
        self.tree.selected.connect(self._show_card)
        self.statusBar().showMessage("")
        self.stop_button = widgets.stop_button(self, self.stop_job)

    def _menus(self) -> None:
        def add(menu, text: str, slot, keys: str = "", tip: str = "") -> QAction:
            act = menu.addAction(text)
            act.triggered.connect(lambda _c=False: slot())
            if keys:
                act.setShortcut(QKeySequence(keys))
            if tip:
                act.setStatusTip(tip)
            return act

        profile = self.menuBar().addMenu(tr("mods.menu.profile"))
        add(profile, tr("mods.new"), self.new_profile)
        add(profile, tr("mods.copy"), self.copy_profile)
        add(profile, tr("mods.rename"), self.rename_profile)
        add(profile, tr("mods.delete"), self.delete_profile)
        profile.addSeparator()
        self.activate_action = add(
            profile, tr("mods.make_active"), self.make_active, tip=tr("mods.tip.profile_game_starts")
        )
        add(
            profile,
            tr("mods.no_mods_next_start"),
            self.deactivate,
            tip=tr("mods.tip.empty_activeprofile_game_starts_clean"),
        )
        mods = self.menuBar().addMenu(tr("mods.menu.mods"))
        self.install_actions = [
            add(mods, tr("mods.install_mod_folder"), self.install_folder),
            add(mods, tr("mods.install_zip"), self.install_zip),
            add(
                mods,
                tr("mods.install_built_project"),
                self.install_project,
                tip=tr("mods.tip.open_projects_build_project_window"),
            ),
        ]
        self.remove_action = add(
            mods, tr("mods.remove_selected_mod"), self.remove_selected, "Del", tip=tr("mods.tip.to_recycle_bin")
        )
        self.import_action = add(
            mods,
            tr("common.import_selected_mod"),
            self.import_selected,
            tip=tr("common.import_selected_mod_tip"),
        )
        self.menu_tips = {a: a.statusTip() for a in (*self.install_actions, self.remove_action, self.import_action)}
        mods.addSeparator()
        add(mods, tr("mods.rescan"), self.rescan, "F5")
        add(mods, tr("mods.open_mods_folder"), self.open_folder)
        check = self.menuBar().addMenu(tr("common.menu.check"))
        self.check_action = add(
            check,
            tr("mods.check_mods"),
            self.deep_check,
            "F6",
            tr("mods.mod_lint_every_operation_games"),
        )
        check.addSeparator()
        self.diff_mod_action = add(
            check, tr("mods.compare_selected_mod_diff"), self.diff_mod, tip=tr("mods.tip.what_changes_game")
        )
        self.diff_profile_action = add(
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
        have = [k for k in GAMES if str(getattr(self.settings, k, "") or "").strip()]
        return (have or list(GAMES))[0]

    # a game and its profiles

    def open_game(self, key: str, profile: str = "") -> None:
        """Show the mods of ``key`` (a new manager from the settings), the profile ``profile`` if it exists."""
        if not gamesync.may_switch(self, key):  # another app's question was cancelled: the box goes back
            self._building = True
            self.game_box.setCurrentIndex(self.game_box.findData(self.manager.game))
            self._building = False
            return
        self._drop_deep()
        self.manager = for_settings(self.settings, key, **self.manager_args)
        self.manager.deferred = (self.background, self.statusBar().showMessage)  # the game's answer takes its time
        self._building = True
        self.game_box.setCurrentIndex(self.game_box.findData(key))
        self._building = False
        self.manager.scan()
        self._fill_profiles(profile)
        self.refresh()

    def _game_chosen(self) -> None:
        if not self._building:
            self.open_game(self.game_box.currentData())

    def _fill_profiles(self, want: str = "") -> None:
        m = self.manager
        names, active = m.profile_names(), m.active()
        self._building = True
        self.profile_box.clear()
        for name in names:
            self.profile_box.addItem(name + ("  " + tr("mods.active") if name.lower() == active.lower() else ""), name)
        spelled = {n.lower(): n for n in names}  # ``profile.txt`` may spell the name in another case
        pick = next((spelled[n.lower()] for n in (want, active, *names) if n.lower() in spelled), "")
        if pick != self.profile:
            self._drop_deep()
        self.profile = pick
        if pick:
            self.profile_box.setCurrentIndex(self.profile_box.findData(pick))
        self._building = False

    def _profile_chosen(self) -> None:
        if not self._building:
            self.profile = self.profile_box.currentData() or ""
            self._drop_deep()
            self.refresh()

    def _drop_deep(self) -> None:
        """The deep check's findings are of the profile as it was checked: gone with any change of it."""
        self.deep, self.deep_stamp = [], self.deep_stamp + 1

    # showing

    def refresh(self) -> None:
        """Read everything again for the open game and profile and show it."""
        m = self.manager
        text = m.read(self.profile)
        plan = m.plan(self.profile)
        plan.issues = plan.issues + self.deep
        log = m.log()
        self.plan, self.text, self.log = plan, text, log
        self.tree.set_data(m.catalog, text, plan, log, editable=text is not None)
        self.order.set_plan(plan)
        self.issues.set_issues(plan.issues)
        problems = len(plan.problems)
        self.tabs.setTabText(1, tr("mods.problems_n", n=problems) if problems else tr("mods.problems"))
        self.log_summary.setText(self.logview.set_log(log))
        self._building = True
        self.new_mods.setChecked(bool(text and text.enable_new))
        self.new_mods.setEnabled(text is not None)
        self._building = False
        is_active = self.profile.lower() == m.active().lower()
        self.activate_action.setEnabled(bool(self.profile) and not is_active)
        self.statusBar().showMessage(self._summary(plan, is_active))
        self.update_guard()
        self._show_card(self.tree.current_card())

    def _summary(self, plan: Plan, is_active: bool) -> str:
        """The status line of a refresh: the mods installed and on, the open profile, the problems, the folder."""
        if not self.profile:
            where = tr("mods.no_profile_open")
        elif is_active:
            where = tr("mods.profile_name_active", name=self.profile)
        else:
            where = tr("mods.profile_name", name=self.profile)
        return tr(
            "mods.mods_installed_problems_folder",
            mods=len(self.manager.catalog.mods),
            on=len([r for r in plan.rows if r.mod.parent is None]),
            where=where,
            problems=len(plan.problems),
            folder=self.manager.mods_dir,
        )

    @property
    def checking(self) -> bool:
        """A slow job (check, install, remove) is running; the actions that would start another say so."""
        return self._checking

    @checking.setter
    def checking(self, running: bool) -> None:
        self._checking = running
        self._actions_state()

    def _actions_state(self) -> None:
        """Install and Remove are on when no job runs (Remove: and a mod is picked) and say why they are not."""
        if not hasattr(self, "menu_tips"):
            return  # the menus are not made yet
        busy = tr("mods.tip.job_running") if self._checking else ""
        for act in self.install_actions:
            widgets.enable_unless(act, busy, self.menu_tips[act])
        picked = self.tree.current_card() is not None
        widgets.enable_unless(
            self.remove_action,
            busy or ("" if picked else tr("mods.tip.pick_mod_first")),
            self.menu_tips[self.remove_action],
        )
        widgets.enable_unless(
            self.import_action,
            busy or ("" if picked else tr("mods.tip.pick_mod_first")),
            self.menu_tips[self.import_action],
        )

    def _show_card(self, card: catalog.ModCard | None) -> None:
        """The details of ``card`` from what the last refresh read (no file is read for a click)."""
        self._actions_state()
        if card is None:
            self.detail.setHtml(tr("mods.pick_mod_left"))
            return
        run = self.log.run_of(card.mod_id) if self.log is not None and card.mod_id else None
        issues = self.plan.issues_of(card.mod_id) if self.plan and card.mod_id else []
        self.detail.setHtml(details.html(details.rows(card, catalog.state_of(card, self.text), issues, run)))

    def update_guard(self) -> None:
        """The banner that says the game is running (and what a profile change then does). The look (the process
        list, the live link) runs off the window's thread; one at a time."""
        if self.manager is None or self._guarding:
            return
        self._guarding = True
        manager, profile = self.manager, self.profile
        self.background.run(
            lambda: manager.warning(profile), lambda text: self._guard_found(manager, text), wait=self.sync
        )

    def _guard_found(self, manager: ModManager, text) -> None:
        self._guarding = False
        if manager is not self.manager or isinstance(text, Exception):  # another game is open now: the next look
            return
        self._warned = text
        self.guard.setText(text)
        self.guard.setVisible(bool(text))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    # changes: each asks first while the game runs

    def _allowed(self) -> bool:
        warn = self._warned  # the last look, at most one timer tick old: asking the system here would block the window
        if not warn:
            return True
        answer = QMessageBox.question(self, tr(TITLE), warn + "\n\n" + tr("mods.change_profile_anyway"))
        return answer == QMessageBox.StandardButton.Yes

    def _failed(self, exc: Exception) -> None:
        QMessageBox.warning(self, tr(TITLE), str(exc))
        self.statusBar().showMessage(str(exc))

    def _change_profile(self, change) -> None:
        """Run ``change(profile)`` (a manager call that writes the open profile) after the game-is-running question,
        show the result and the game link's sentence."""
        if self._building or not self.profile:
            return
        if not self._allowed():
            self.refresh()
            return
        notice = ""
        self._drop_deep()
        try:
            notice = change(self.profile)
        except ProfileError as exc:
            self._failed(exc)
        self.refresh()
        if notice:
            self.statusBar().showMessage(notice)

    def _mod_toggled(self, mod_id: str, on: bool) -> None:
        self._change_profile(lambda profile: self.manager.set_mod(profile, mod_id, on))

    def _enable_new_toggled(self, on: bool) -> None:
        self._change_profile(lambda profile: self.manager.set_enable_new(profile, on))

    # the profiles

    def _ask_name(self, title: str, label: str, start: str = "") -> str:
        name, ok = QInputDialog.getText(self, title, label, text=start)
        return name.strip() if ok else ""

    def _profile_job(self, job: Callable[[], object], then: str = "") -> bool:
        """Run a change of the profiles folder, then show the profile ``then`` (default: the open one). False when
        it failed (said in a box and the status line)."""
        try:
            job()
        except (ProfileError, OSError) as exc:
            self._failed(exc)
            return False
        self._fill_profiles(then or self.profile)
        self.refresh()
        return True

    def new_profile(self) -> None:
        name = self._ask_name(tr("mods.new_profile"), tr("mods.name_new_empty_profile"))
        if name:
            self._profile_job(lambda: self.manager.create_profile(name), name)

    def copy_profile(self) -> None:
        if not self.profile:
            return
        name = self._ask_name(
            tr("mods.copy_profile"),
            tr("mods.name_copy_colon", name=self.profile),
            tr("mods.name_copy", name=self.profile),
        )
        if name:
            self._profile_job(lambda: self.manager.create_profile(name, self.profile), name)

    def rename_profile(self) -> None:
        if not self.profile or not self._allowed():
            return
        name = self._ask_name(tr("mods.rename_profile"), tr("mods.new_name", name=self.profile), self.profile)
        if name and name != self.profile:
            self._profile_job(lambda: self.manager.store.rename(self.profile, name), name)

    def delete_profile(self) -> None:
        if not self.profile:
            return
        ask = QMessageBox.question(self, tr(TITLE), tr("mods.delete_profile_cannot_undone", name=self.profile))
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.manager.store.delete(self.profile), "")

    def make_active(self) -> None:
        if self.profile and self._allowed():
            if self._profile_job(lambda: self.manager.store.set_active(self.profile)):
                self.statusBar().showMessage(tr("mods.active_profile_game_uses_next", name=self.profile))

    def deactivate(self) -> None:
        if not self._allowed():
            return
        ask = QMessageBox.question(self, tr(TITLE), tr("mods.leave_activeprofile_empty_game_starts"))
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.manager.store.set_active(""))

    # the mods

    def rescan(self) -> None:
        self.open_game(self.game_box.currentData(), self.profile)

    def _installed(self, results: list[install.Installed]) -> None:
        notes = [n for r in results for n in r.notes]
        names = ", ".join(f"{r.mod_id}{' ' + tr('mods.replaced') if r.replaced else ''}" for r in results)
        self.rescan()
        said = " ".join(dict.fromkeys(r.notice for r in results if r.notice))
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
        manager, placed = self.manager, []
        talk = self._job_started(tr("mods.installing_name", name=source.name))
        self.background.run(
            lambda: manager.install(source, talk.say, placed),
            lambda found: self._install_done(found, placed),
            self.sync,
        )

    def _install_done(self, found, placed: list[install.Installed] | None = None) -> None:
        self._job_ended()
        if isinstance(found, Stopped):
            self.rescan()
            names = ", ".join(one.mod_id for one in placed or [])
            said = tr("mods.install_stopped_installed", names=names) if names else tr("mods.install_stopped")
            self.statusBar().showMessage(said)
        elif isinstance(found, Exception):
            self._failed(found)
        else:
            self._installed(found)

    def _job_started(self, text: str) -> JobTalk:
        """A slow job begins: its progress goes to the status line, and the Stop button is there for it."""
        self.checking = True
        self.talk = JobTalk(self)
        self.talk.said.connect(self.statusBar().showMessage)
        self.stop_button.setEnabled(True)
        self.stop_button.show()
        self.statusBar().showMessage(text)
        return self.talk

    def _job_ended(self) -> None:
        self.checking = False
        self.stop_button.hide()
        if self.talk is not None:
            self.talk.said.disconnect()  # a report still on its way must not write over the result
            self.talk.deleteLater()
            self.talk = None

    def stop_job(self) -> None:
        """Stop the running install or check at its next step."""
        if self.talk is not None:
            self.talk.stop()
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage(tr("mods.stopping"))

    def install_project(self) -> None:
        proj = project.current(self.settings)
        if proj.game != self.manager.game:
            self._failed(
                ValueError(
                    tr(
                        "mods.open_project_s_mods_folder",
                        project_game=proj.game,
                        game=self.manager.game,
                    )
                )
            )
            return
        if self.checking:
            return
        self.checking = True
        mods_dir = self.manager.mods_dir
        self.statusBar().showMessage(tr("mods.installing_projects_build"))
        # hashing and copying the mod, then the game's answer to the announcement, all off the window's thread
        self.background.run(
            lambda: project.install(proj, mods_dir, lambda _t: None), self._project_installed, self.sync
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
        card = self.tree.current_card()
        if card is None or self.checking:
            return
        target = target or importdialog.ask_target(self, card.folder)
        if target is None:
            return
        talk = self._job_started(tr("common.import_reading", name=card.folder.name))
        importdialog.start(talk, self.background, card.folder, target, self.settings, self._imported, self.sync)

    def _imported(self, result) -> None:
        self._job_ended()
        lines = importdialog.explain(result)
        self.statusBar().showMessage(lines[0])
        if isinstance(result, Exception):
            if not isinstance(result, Stopped):
                self._failed(result)
            return
        self.settings.project = str(result.folder)  # the apps work on the imported project from now on
        if self.settings_path:
            self.settings.save(self.settings_path)
        QMessageBox.information(self, tr(TITLE), "\n".join(lines))

    def remove_selected(self) -> None:
        card = self.tree.current_card()
        if card is None:
            return
        while card.mod is not None and card.mod.parent is not None:  # a nested mod goes with its whole mod
            card = next(c for c in self.manager.catalog.cards if c.mod is card.mod.top)
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
        manager = self.manager
        self.statusBar().showMessage(tr("common.moving_recycle_bin", name=card.folder.name))
        # the move can take a while for a big mod; the announcement to the game is made back here, off no thread of ours
        self.background.run(
            lambda: manager.remove(card, announce=False),
            lambda where: self._removed(manager, card, where),
            self.sync,
        )

    def _removed(self, manager: ModManager, card: catalog.ModCard, where: str | Exception) -> None:
        """``manager``: the one the mod was removed from (another game may be open by now)."""
        self.checking = False
        if isinstance(where, Exception):
            self._failed(where)
            return
        manager.announce_removal(card)
        self.rescan()
        moved = tr("mods.name_moved_to_where", name=card.folder.name, where=where)
        self.statusBar().showMessage(f"{moved} {manager.notice}".rstrip())

    def open_folder(self) -> None:
        self.manager.mods_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.manager.mods_dir)))

    # the slow check and the running game

    def deep_check(self) -> None:
        """The slow checks of the profile shown, off the window's thread: the status line names the mod it is at,
        Stop ends it before the next one."""
        if self.checking or not self.profile:
            return
        self.check_action.setEnabled(False)
        manager, profile, stamp = self.manager, self.profile, self.deep_stamp
        talk = self._job_started(tr("mods.checking_mods_lint_operations_games"))
        self.background.run(
            lambda: manager.deep_check(profile, talk.say), lambda found: self._checked(stamp, found), self.sync
        )

    def _checked(self, stamp: int, found) -> None:
        self._job_ended()
        self.check_action.setEnabled(True)
        if isinstance(found, Stopped):
            self.statusBar().showMessage(tr("mods.check_stopped"))
            return
        if isinstance(found, Exception):
            self._failed(found)
            return
        if stamp != self.deep_stamp:
            self.statusBar().showMessage(tr("mods.check_of_changed_profile"))
            return
        self.deep = found
        self.refresh()
        bad = len([i for i in found if i.level == PROBLEM])
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(tr("mods.check_done_problems_notes_deep", bad=bad, notes=len(found) - bad))

    def _diff_actions(self) -> None:
        """The Mod diff entries show only where the suite has a Mod diff to answer them."""
        there = links.available(links.DIFF_MODS)
        card = self.tree.current_card()
        self.diff_mod_action.setVisible(there)
        self.diff_profile_action.setVisible(there)
        self.diff_mod_action.setEnabled(card is not None and card.mod is not None)
        self.diff_profile_action.setEnabled(self.manager is not None and bool(self.plan and self.plan.order))

    def diff_mod(self) -> None:
        """The selected mod against the game, in the Mod diff."""
        card = self.tree.current_card()
        if card is not None and card.mod is not None:
            self._ask_diff(links.MODE_MOD, a=card.mod_id)

    def diff_profile(self) -> None:
        """The mods the open profile switches on, run in the loader's order, in the Mod diff (1800: all mods that are on)."""
        profile = self.profile if self.manager.game != "anno1800" else ACTIVATIONS
        self._ask_diff(links.MODE_PROFILE, profile=profile)

    def _ask_diff(self, mode: str, a: str = "", profile: str = "") -> None:
        if not links.diff_mods(mode, self.manager.game, a, "", profile):
            self.statusBar().showMessage(tr("mods.mod_diff_not_part_window"))

    def reload_game(self) -> None:
        active = self.manager.active()
        self.statusBar().showMessage(tr("mods.asking_game_reload"))  # it answers within seconds, up to minutes
        self.background.run(gameguard.reload_in_game, lambda reply: self._reloaded(reply, active), self.sync)

    def _reloaded(self, reply, active: str) -> None:
        if isinstance(reply, Exception):
            self._failed(reply)
            return
        message = gameguard.reload_text(reply, active)
        self.statusBar().showMessage(message)
        QMessageBox.information(self, tr(TITLE), message)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        if self.manager is not None:
            self.settings.apps[APP] = {"game": self.manager.game, "profile": self.profile}
        super().closeEvent(event)


def run() -> int:
    from ..suite.settings import Settings, settings_file

    qt = QApplication.instance() or QApplication([sys.argv[0]])
    path = settings_file()
    window = ModManagerWindow(Settings.load(path), path)
    window.resize(1400, 900)
    window.show()
    return qt.exec()
