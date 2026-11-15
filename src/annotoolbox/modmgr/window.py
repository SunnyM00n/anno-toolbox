"""The mod manager's window (Qt): the installed mods of a game as a tree with a check box each, the profile open
above it, the details of the selected mod beside it, and the load order, the problems and the last start's log below.

``ModManagerWindow(settings, settings_path)`` is the suite's Mods app; ``run`` starts it alone. All logic is in
``manager.ModManager``; this module shows it and asks before anything is written. docs/mod-manager.md is the guide.
"""

from __future__ import annotations

import sys
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
from ..suite import links, project
from ..suite.background import Background
from . import catalog, details, gameguard, install
from .checks import PROBLEM, Issue
from .installspage import InstallsPage
from .manager import GAMES, ModManager, for_settings
from .modtree import ModTree
from .orderview import IssueView, LogView, OrderView
from .profiles import ProfileError

APP = "mods"  # the key of its remembered state in the settings
TITLE = N_("Mod manager")
GUARD_MS = 5000  # how often the game is looked for
WARN_STYLE = "background:#fff3cd;color:#664d03;padding:6px;border:1px solid #e0c36a"


class ModManagerWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None, sync: bool = False, **manager_args) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.manager_args = manager_args  # the process lister and the trash of a test
        self.manager: ModManager | None = None
        self.profile = ""  # the profile open (not always the active one)
        self.deep: dict[tuple[str, str], list[Issue]] = {}  # (game, profile) -> the last deep check's findings
        self.background = Background(self)
        self.sync = sync  # run the background jobs here and now (a test that wants the result at once)
        self.checking = False  # a slow job (check, install) is running
        self.plan = None
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
        bar = QToolBar(tr("Profile"), self, movable=False)
        self.addToolBar(bar)
        self.game_box = QComboBox()
        for key, name in GAMES.items():
            self.game_box.addItem(name, key)
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(200)
        self.new_mods = QCheckBox("EnableNewMods")  # i18n: ok (the loader's setting name)
        self.new_mods.setToolTip(
            tr("mods this profile does not list start on (the mods folders, not Ubisoft's examples)")
        )
        for before, label, widget in (("", N_("Game"), self.game_box), (" ", N_("Profile"), self.profile_box)):
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
        self.tabs.addTab(self.order, tr("Load order"))
        self.tabs.addTab(self.issues, tr("Problems"))
        self.tabs.addTab(log_page, tr("Last start"))
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
        self.pages.addTab(body, tr("Mods"))
        self.pages.addTab(self.installs, tr("Installs"))
        self.pages.currentChanged.connect(lambda n: self.toolbar.setVisible(n == 0))  # the bar is the mods' own
        self.setCentralWidget(self.pages)
        self.game_box.currentIndexChanged.connect(lambda _n: self._game_chosen())
        self.profile_box.currentIndexChanged.connect(lambda _n: self._profile_chosen())
        self.new_mods.toggled.connect(self._enable_new_toggled)
        self.tree.toggled.connect(self._mod_toggled, Qt.ConnectionType.QueuedConnection)  # the tree is rebuilt in it
        self.tree.selected.connect(self._show_card)
        self.statusBar().showMessage("")

    def _menus(self) -> None:
        def add(menu, text: str, slot, keys: str = "", tip: str = "") -> QAction:
            act = menu.addAction(text)
            act.triggered.connect(lambda _c=False: slot())
            if keys:
                act.setShortcut(QKeySequence(keys))
            if tip:
                act.setStatusTip(tip)
            return act

        profile = self.menuBar().addMenu(tr("&Profile"))
        add(profile, tr("New..."), self.new_profile)
        add(profile, tr("Copy..."), self.copy_profile)
        add(profile, tr("Rename..."), self.rename_profile)
        add(profile, tr("Delete"), self.delete_profile)
        profile.addSeparator()
        self.activate_action = add(
            profile, tr("Make active"), self.make_active, tip=tr("the profile the game starts with")
        )
        add(
            profile,
            tr("No mods at the next start"),
            self.deactivate,
            tip=tr("an empty ActiveProfile: the game starts clean"),
        )
        mods = self.menuBar().addMenu(tr("&Mods"))
        add(mods, tr("Install a mod folder..."), self.install_folder)
        add(mods, tr("Install a zip..."), self.install_zip)
        add(
            mods,
            tr("Install the built project"),
            self.install_project,
            tip=tr("the open project's build, from the Project window"),
        )
        self.remove_action = add(
            mods, tr("Remove the selected mod"), self.remove_selected, tip=tr("to the recycle bin")
        )
        mods.addSeparator()
        add(mods, tr("Rescan"), self.rescan, "F5")
        add(mods, tr("Open the mods folder"), self.open_folder)
        check = self.menuBar().addMenu(tr("&Check"))
        self.check_action = add(
            check,
            tr("Check the mods that are on..."),
            self.deep_check,
            "F6",
            tr("the mod lint and every operation on the game's files"),
        )
        check.addSeparator()
        self.diff_mod_action = add(
            check, tr("Compare the selected mod in Mod diff"), self.diff_mod, tip=tr("what it changes in the game")
        )
        self.diff_profile_action = add(
            check, tr("Compare the mods that are on in Mod diff"), self.diff_profile, tip=tr("where they collide")
        )
        check.aboutToShow.connect(self._diff_actions)
        game = self.menuBar().addMenu(tr("&Game"))
        add(
            game,
            tr("Reload in the running game"),
            self.reload_game,
            "Shift+F3",
            tr("asks the running game for the hot reload"),
        )

    def _first_game(self) -> str:
        have = [k for k in GAMES if str(getattr(self.settings, k, "") or "").strip()]
        return (have or list(GAMES))[0]

    # a game and its profiles

    def open_game(self, key: str, profile: str = "") -> None:
        """Show the mods of ``key`` (a new manager from the settings), the profile ``profile`` if it exists."""
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
            self.profile_box.addItem(name + ("  " + tr("(active)") if name.lower() == active.lower() else ""), name)
        pick = next((n for n in (want, active, *names) if n in names), "")
        self.profile = pick
        if pick:
            self.profile_box.setCurrentIndex(self.profile_box.findData(pick))
        self._building = False

    def _profile_chosen(self) -> None:
        if not self._building:
            self.profile = self.profile_box.currentData() or ""
            self.refresh()

    # showing

    def refresh(self) -> None:
        """Read everything again for the open game and profile and show it."""
        m = self.manager
        text = m.read(self.profile)
        plan = m.plan(self.profile)
        plan.issues = plan.issues + self.deep.get((m.game, self.profile), [])
        log = m.log()
        self.plan = plan
        self.tree.set_data(m.catalog, text, plan, log, editable=text is not None)
        self.order.set_plan(plan)
        self.issues.set_issues(plan.issues)
        problems = len(plan.problems)
        self.tabs.setTabText(1, tr("Problems ({n})", n=problems) if problems else tr("Problems"))
        self.log_summary.setText(self.logview.set_log(log))
        self._building = True
        self.new_mods.setChecked(bool(text and text.enable_new))
        self.new_mods.setEnabled(text is not None)
        self._building = False
        active = m.active()
        self.activate_action.setEnabled(bool(self.profile) and self.profile.lower() != active.lower())
        self.remove_action.setEnabled(True)
        n_on = len([r for r in plan.rows if r.mod.parent is None])
        where = (
            (
                tr("profile {name} (active)", name=self.profile)
                if self.profile.lower() == active.lower()
                else tr("profile {name}", name=self.profile)
            )
            if self.profile
            else tr("no profile open")
        )
        self.statusBar().showMessage(
            tr(
                "{mods} mods installed, {on} on in {where}; {problems} problem(s); mods folder {folder}",
                mods=len(m.catalog.mods),
                on=n_on,
                where=where,
                problems=problems,
                folder=m.mods_dir,
            )
        )
        self.update_guard()
        self._show_card(self.tree.current_card())

    def _show_card(self, card) -> None:
        if card is None:
            self.detail.setHtml(tr("<p>Pick a mod on the left.</p>"))
            return
        text = self.manager.read(self.profile)
        run = None
        log = self.manager.log()
        if log is not None and card.mod_id:
            run = log.run_of(card.mod_id)
        issues = [i for i in self.plan.issues if i.mod == card.mod_id] if self.plan and card.mod_id else []
        self.detail.setHtml(details.html(details.rows(card, catalog.state_of(card, text), issues, run)))

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
        answer = QMessageBox.question(self, tr(TITLE), warn + "\n\n" + tr("Change the profile anyway?"))
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

    def _profile_job(self, job, then: str = "") -> None:
        try:
            job()
        except (ProfileError, OSError) as exc:
            self._failed(exc)
            return
        self._fill_profiles(then or self.profile)
        self.refresh()

    def new_profile(self) -> None:
        name = self._ask_name(tr("New profile"), tr("Name of the new, empty profile:"))
        if name:
            self._profile_job(lambda: self.manager.create_profile(name), name)

    def copy_profile(self) -> None:
        if not self.profile:
            return
        name = self._ask_name(
            tr("Copy profile"),
            tr("Name of the copy of {name}:", name=self.profile),
            tr("{name} copy", name=self.profile),
        )
        if name:
            self._profile_job(lambda: self.manager.create_profile(name, self.profile), name)

    def rename_profile(self) -> None:
        if not self.profile or not self._allowed():
            return
        name = self._ask_name(tr("Rename profile"), tr("New name of {name}:", name=self.profile), self.profile)
        if name and name != self.profile:
            self._profile_job(lambda: self.manager.store.rename(self.profile, name), name)

    def delete_profile(self) -> None:
        if not self.profile:
            return
        ask = QMessageBox.question(
            self, tr(TITLE), tr("Delete the profile {name}? This cannot be undone.", name=self.profile)
        )
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.manager.store.delete(self.profile), "")

    def make_active(self) -> None:
        if self.profile and self._allowed():
            self._profile_job(lambda: self.manager.store.set_active(self.profile))
            self.statusBar().showMessage(
                tr("{name} is the active profile: the game uses it from its next start", name=self.profile)
            )

    def deactivate(self) -> None:
        if not self._allowed():
            return
        ask = QMessageBox.question(
            self, tr(TITLE), tr("Leave ActiveProfile empty? The game then starts without any mods.")
        )
        if ask == QMessageBox.StandardButton.Yes:
            self._profile_job(lambda: self.manager.store.set_active(""))

    # the mods

    def rescan(self) -> None:
        self.open_game(self.game_box.currentData(), self.profile)

    def _installed(self, results: list[install.Installed]) -> None:
        notes = [n for r in results for n in r.notes]
        names = ", ".join(f"{r.mod_id}{' ' + tr('(replaced)') if r.replaced else ''}" for r in results)
        self.rescan()
        said = " ".join(dict.fromkeys(r.notice for r in results if r.notice))
        self.statusBar().showMessage(
            tr("installed {names}", names=names)
            + (f"; {' '.join(notes)}" if notes else "")
            + (f". {said}" if said else "")
        )
        if notes:
            QMessageBox.information(self, tr(TITLE), "\n".join(notes))

    def install_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Install a mod folder (or a folder of mods)"))
        if folder:
            self._install(Path(folder))

    def install_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Install a mod from a zip"), "", tr("Zip files (*.zip)"))
        if path:
            self._install(Path(path))

    def _install(self, source: Path) -> None:
        """Copy off the window's thread: a large mod is many files."""
        if self.checking:
            return
        self.checking = True
        manager = self.manager
        self.statusBar().showMessage(tr("installing {name}...", name=source.name))
        self.background.run(lambda: manager.install(source), self._install_done, self.sync)

    def _install_done(self, found) -> None:
        self.checking = False
        if isinstance(found, Exception):
            self._failed(found)
        else:
            self._installed(found)

    def install_project(self) -> None:
        proj = project.current(self.settings)
        if proj.game != self.manager.game:
            self._failed(
                ValueError(
                    tr(
                        "the open project is for {project_game}, this is {game}'s mods folder",
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
        self.statusBar().showMessage(tr("installing the project's build..."))
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
        self.statusBar().showMessage(tr("installed the project's build into {where}", where=where))

    def remove_selected(self) -> None:
        card = self.tree.current_card()
        if card is None:
            return
        while card.mod is not None and card.mod.parent is not None:  # a nested mod goes with its whole mod
            card = next(c for c in self.manager.catalog.cards if c.mod is card.mod.top)
        ask = QMessageBox.question(
            self,
            tr(TITLE),
            tr("Move {name} to the recycle bin?", name=card.folder.name) + f"\n{card.folder}",
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        if self.checking:
            return
        self.checking = True
        manager = self.manager
        self.statusBar().showMessage(tr("moving {name} to the recycle bin...", name=card.folder.name))
        # the move can take a while for a big mod; the announcement to the game is made back here, off no thread of ours
        self.background.run(
            lambda: manager.remove(card, announce=False), lambda where: self._removed(card, where), self.sync
        )

    def _removed(self, card, where) -> None:
        self.checking = False
        if isinstance(where, Exception):
            self._failed(where)
            return
        self.manager.announce_removal(card)
        self.rescan()
        moved = tr("{name} moved to {where}.", name=card.folder.name, where=where)
        self.statusBar().showMessage(f"{moved} {self.manager.notice}".rstrip())

    def open_folder(self) -> None:
        self.manager.mods_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.manager.mods_dir)))

    # the slow check and the running game

    def deep_check(self) -> None:
        if self.checking or not self.profile:
            return
        self.checking = True
        self.check_action.setEnabled(False)
        key = (self.manager.game, self.profile)
        manager, profile = self.manager, self.profile
        self.statusBar().showMessage(
            tr("checking the mods that are on (the lint, then the operations on the game's files)...")
        )
        self.background.run(lambda: manager.deep_check(profile), lambda found: self._checked(key, found))

    def _checked(self, key: tuple[str, str], found) -> None:
        self.checking = False
        self.check_action.setEnabled(True)
        if isinstance(found, Exception):
            self._failed(found)
            return
        self.deep[key] = found
        self.refresh()
        bad = len([i for i in found if i.level == PROBLEM])
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(
            tr("check done: {bad} problem(s), {notes} note(s) from the deep check", bad=bad, notes=len(found) - bad)
        )

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
            self.statusBar().showMessage(tr("the Mod diff is not part of this window"))

    def reload_game(self) -> None:
        active = self.manager.active()
        self.statusBar().showMessage(tr("asking the game to reload..."))  # it answers within seconds, up to minutes
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
    from ..suite.settings import SETTINGS_FILE, Settings, appdata

    qt = QApplication.instance() or QApplication([sys.argv[0]])
    path = appdata() / SETTINGS_FILE
    window = ModManagerWindow(Settings.load(path), path)
    window.resize(1400, 900)
    window.show()
    return qt.exec()
