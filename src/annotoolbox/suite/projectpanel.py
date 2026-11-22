"""The suite's Project window: which project is open, the mod it makes (id, name, version, game, GUID range), what
each app holds for it, and the one build and install of the mod (``suite.project``; docs/tool-suite.md, C4).

``CProjectDialog(shell, parts)`` is the window; the shell opens it from the switcher. Switching the project changes
where the apps read and write their work from the next island or game they open.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import N_, english, number, tr
from ..modloader.mods import DEPENDENCY_KINDS
from ..sources import gametree
from . import guidrangedialog, guidranges, importdialog, importmeta, importresume, projectdeps
from . import project as projects
from .background import CBackground, CJobTalk


class CProjectDialog(QDialog):
    """The Project window: the open project's mod fields, what each app holds for it, and its build and install."""

    def __init__(self, shell, parts: list[projects.SPart]) -> None:
        super().__init__(shell)
        self.m_shell, self.m_parts = shell, parts
        self.setWindowTitle(tr("common.project"))
        self.resize(720, 620)
        self.m_background = CBackground(self)
        self.m_folder = QLabel()
        self.m_mod_id, self.m_name, self.m_version = QLineEdit(), QLineEdit(), QLineEdit()
        self.m_game = QComboBox()
        for key in projects.ALL_GAMES:
            self.m_game.addItem(gametree.GAMES[key], key)
        self.m_guid_range = QLabel()
        self.m_guid_selection = guidrangedialog.SSelection(guidranges.CUSTOM, "", projects.DEFAULT_RANGE)
        self.m_deps = {kind: QLineEdit() for kind in DEPENDENCY_KINDS}
        self.m_bundle = QListWidget()
        self.m_contents = QListWidget()
        self.m_log = QPlainTextEdit(readOnly=True)
        self.m_buttons: dict[str, QPushButton] = {}
        self.m_talk: CJobTalk | None = None  # the running import's progress and stop
        self.m_stop_button = QPushButton(tr("common.stop"))
        self.m_stop_button.clicked.connect(self.stop_import)
        self.m_stop_button.hide()
        self._m_importing: Path | None = None  # the folder of the import that runs
        self._m_is_building = False  # a build (or build and install) runs off the window's thread
        self._layout()
        self.load_current()
        QTimer.singleShot(0, self.offer_pending)  # a half-made import of an earlier run: resume or remove it

    def _layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self.m_folder, 1)
        for label, slot in (
            (N_("common.open"), self.ask_open),
            (N_("common.new"), self.ask_new),
            (N_("common.use_workspace"), self.use_workspace),
        ):
            top.addWidget(self._button(label, slot))
        top.addWidget(self._import_button())
        form = QFormLayout()
        form.addRow(tr("common.mod_id"), self.m_mod_id)
        form.addRow(tr("common.name"), self.m_name)
        form.addRow(tr("common.version"), self.m_version)
        form.addRow(tr("common.game"), self.m_game)
        guids = QHBoxLayout()
        guids.addWidget(self.m_guid_range, 1)
        guids.addWidget(self._button(N_("common.choose"), self.choose_range))
        form.addRow(tr("common.guid_range"), guids)
        for kind, edit in self.m_deps.items():
            edit.setPlaceholderText(
                tr("common.mod_ids_separated_commas_loads")
                if kind == "LoadAfter"
                else tr("common.mod_ids_separated_commas")
            )
            form.addRow(
                tr("common.load_after") if kind == "LoadAfter" else kind, edit
            )  # i18n: ok (the other kinds are the loader's keys)
        carried = QHBoxLayout()
        carried.addWidget(self.m_bundle, 1)
        side = QVBoxLayout()
        side.addWidget(self._button(N_("common.add_mod_folder"), self.add_bundle))
        side.addWidget(self._button(N_("common.remove"), self.remove_bundle))
        carried.addLayout(side)
        form.addRow(tr("common.mods_built_nested"), carried)
        actions = QHBoxLayout()
        for label, slot in (
            (N_("common.save"), self.save),
            (N_("common.build_mod"), self.build),
            (N_("common.build_install"), self.install),
        ):
            actions.addWidget(self._button(label, slot))
        actions.addWidget(self.m_stop_button)
        for key, handover in self.handovers().items():
            actions.addWidget(self._button(handover.take_label, lambda key=key: self.take_handover(key)))
            actions.addWidget(self._button(handover.undo_label, lambda key=key: self.undo_handover(key)))
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr("common.what_apps_hold_projects_game")))
        layout.addWidget(self.m_contents)
        layout.addLayout(actions)
        layout.addWidget(self.m_log, 1)

    def _button(self, label: str, slot) -> QPushButton:
        button = QPushButton(tr(label))
        button.clicked.connect(lambda _c=False: slot())
        self.m_buttons[english(label).replace("&&", "&")] = button  # the buttons' keys: their English words
        return button

    def _import_button(self) -> QPushButton:
        """ "Import a mod as a project...": a menu of the two sources, a mod folder and a ``.zip``."""
        button = QPushButton(tr("common.import_mod_button"))
        menu = QMenu(button)
        menu.addAction(tr("common.import_mod_folder")).triggered.connect(lambda _c=False: self.ask_import())
        menu.addAction(tr("common.import_mod_zip")).triggered.connect(lambda _c=False: self.ask_import(True))
        menu.addAction(tr("common.archive_mod_import_button")).triggered.connect(
            lambda _c=False: self.ask_import_archive()
        )
        menu.addAction(tr("common.archive_mod_import_folder")).triggered.connect(
            lambda _c=False: self.ask_import_archive(True)
        )
        menu.addSeparator()
        menu.addAction(tr("common.reimport_menu")).triggered.connect(lambda _c=False: self.ask_reimport())
        button.setMenu(menu)
        self.m_buttons[english(N_("common.import_mod_button"))] = button
        return button

    # the project shown
    def load_current(self) -> None:
        """The settings' project; one whose file does not read is shown as such, with Open, New and the workspace
        still there to choose another (Save, Build and Install wait for a project that reads)."""
        try:
            self.load(projects.current(self.m_shell.m_settings))
        except ValueError as exc:
            self.load(projects.CProject(projects.folder_of(self.m_shell.m_settings)))
            self.m_folder.setText(tr("common.open_another_project_use_workspace", error=exc))
            for key in ("Save", "Build mod", "Build & install"):
                self.m_buttons[key].setEnabled(False)
            self.say(str(exc))

    def load(self, project: projects.CProject) -> None:
        self.m_project = project
        self._busy(False)
        if project.defined:
            self.m_folder.setText(str(project.folder))
        else:
            self.m_folder.setText(tr("common.no_project_file_yet_defaults", folder=project.folder))
        self.m_mod_id.setText(project.mod_id)
        self.m_name.setText(project.name)
        self.m_version.setText(project.version)
        self.m_game.setCurrentIndex(max(0, self.m_game.findData(project.game)))
        self.m_guid_selection = guidrangedialog.SSelection(project.guid_choice, project.guid_source, project.guid_range)
        for kind, edit in self.m_deps.items():
            edit.setText(", ".join(project.dependencies.get(kind, [])))
        self.m_bundle.clear()
        self.m_bundle.addItems([str(mod_folder) for mod_folder in project.bundle])
        self.show_range()
        self.show_contents()

    def add_bundle(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("common.mod_folder_modinfo_json_build"))
        if name:
            self.m_bundle.addItem(name)

    def remove_bundle(self) -> None:
        for item in self.m_bundle.selectedItems():
            self.m_bundle.takeItem(self.m_bundle.row(item))

    def show_range(self) -> None:
        first_guid, last_guid = self.m_guid_selection.span
        how = {
            guidranges.OWN: N_("common.my_own_range_lower"),
            guidranges.PUBLIC: N_("common.declared_free_range"),
            guidranges.CUSTOM: N_("common.typed_in"),
        }
        self.m_guid_range.setText(
            f"{number(first_guid)} - {number(last_guid)} ({tr(how[self.m_guid_selection.choice])})"
        )

    def choose_range(self) -> None:
        """The three ways to a range (``guidrangedialog``): own, publicly declared, typed in."""
        settings, game = self.m_shell.m_settings, self.m_game.currentData()
        others = projects.other_ranges(settings, self.m_project.folder, game)
        picked = guidrangedialog.ask(self, game, settings, others, self.m_guid_selection)
        if picked is not None:
            self.m_guid_selection = picked
            self.m_shell.save_settings()  # the own reserve is made on first use
            self.show_range()

    def show_contents(self) -> None:
        self.m_contents.clear()
        for part in self.m_parts:
            title = tr(part.title)
            try:
                has_edits = part.has_work(self.m_project)
            except (OSError, ValueError) as exc:
                note = tr("common.part_not_read_error", part=title, error=exc)
            else:
                if has_edits:
                    note = tr("common.edits_to_build", part=title)
                else:
                    note = tr("common.part_nothing_game", part=title)
            QListWidgetItem(note, self.m_contents)
        self._show_handover()

    def handovers(self) -> dict:
        """The apps' ways to take an imported mod's carried files (``SPart.handover`` and its ``more``), by the part's key
        (the further ways: ``<key>+<n>``)."""
        out = {}
        for part in self.m_parts:
            if part.handover is not None:
                out[part.key] = part.handover
                out.update({f"{part.key}+{index}": more for index, more in enumerate(part.handover.more, 1)})
        return out

    def _show_handover(self) -> None:
        """Each hand-over's two buttons: the first for an imported project that can still hand over, the other for one
        that has."""
        for handover in self.handovers().values():
            self.m_buttons[english(handover.take_label)].setEnabled(handover.can_take(self.m_project))
            self.m_buttons[english(handover.undo_label)].setEnabled(handover.can_undo(self.m_project))

    def take_handover(self, key: str = "assets", is_waiting: bool = False, confirm=None) -> None:
        """Hand the imported mod's carried files to the app ``key`` after the report of what that loses."""
        self._busy(True)
        extra = {"confirm": confirm} if confirm else {}
        self.handovers()[key].start_take(
            self,
            self.m_project,
            self.m_shell.m_settings,
            self.m_background,
            self.say,
            self._handover_done,
            is_waiting,
            **extra,
        )

    def undo_handover(self, key: str = "assets", confirm=None) -> None:
        extra = {"confirm": confirm} if confirm else {}
        self.handovers()[key].undo(self, self.m_project, self.say, **extra)
        self.show_contents()

    def _handover_done(self) -> None:
        self._busy(False)
        self.show_contents()

    def edited(self) -> projects.CProject:
        """The project as the fields say (``ValueError`` with the reason when they do not make one)."""
        edited_project = projects.CProject(
            self.m_project.folder,
            mod_id=self.m_mod_id.text().strip(),
            name=self.m_name.text().strip(),
            version=self.m_version.text().strip() or "0.1.0",
            game=self.m_game.currentData(),
            category=self.m_project.category,
            description=self.m_project.description,
            guid_range=self.m_guid_selection.span,
            guid_choice=self.m_guid_selection.choice,
            guid_source=self.m_guid_selection.source,
            dependencies=projectdeps.clean({kind: _ids(edit.text()) for kind, edit in self.m_deps.items()}),
            bundle=[self.m_bundle.item(index).text() for index in range(self.m_bundle.count())],
            defined=self.m_project.defined,
        )
        edited_project.validate()
        problems = guidranges.check(
            edited_project.game,
            edited_project.guid_range,
            projects.other_ranges(self.m_shell.m_settings, edited_project.folder, edited_project.game),
        )
        if guidranges.blocking(problems):
            raise ValueError(guidranges.blocking(problems)[0].text)
        for problem in problems:
            self.say(tr("common.guid_range_problem", problem=problem.text))
        return edited_project

    # choosing a project
    def use(self, folder: Path) -> None:
        """Open the project in ``folder``; one that does not read is said and not made the settings' project."""
        try:
            project = projects.CProject.open(folder)
        except ValueError as exc:
            QMessageBox.warning(self, tr("common.project"), str(exc))
            return
        self.m_shell.m_settings.project = str(folder)
        projects.remember(self.m_shell.m_settings, folder)
        self.m_shell.save_settings()
        self.load(project)

    def ask_open(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("common.project_folder"))
        if name:
            self.use(Path(name))

    def ask_new(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("common.empty_folder_new_project"))
        if not name:
            return
        folder = Path(name)
        if (folder / projects.FILE).is_file():
            QMessageBox.information(self, tr("common.project"), tr("common.folder_holds_project_already_opened"))
        else:
            made = projects.CProject.open(folder)
            try:
                made.choose_range(guidranges.OWN, "", made.guid_range, self.m_shell.m_settings)
                made.save()
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, tr("common.project"), str(exc))
                return
        self.use(folder)

    def ask_import(self, zip_file: bool = False) -> None:
        """Ask for a mod and an empty folder, then import the mod there as a project (the mod is not changed)."""
        source = importdialog.ask_source(self, zip_file)
        folder = importdialog.ask_target(self, source) if source else None
        if folder:
            self.import_mod(source, folder)

    def import_mod(self, source: Path, folder: Path, is_waiting: bool = False) -> None:
        """The import off the window's thread, its steps in the log, Stop ends it and keeps what was made for a
        resume; the project opens when it is done (``is_waiting``: in this thread, for a test)."""
        path = self.m_shell.m_settings_path
        self._job(
            folder,
            lambda talk, done: importdialog.start(
                talk, self.m_background, source, folder, self.m_shell.m_settings, done, is_waiting, path
            ),
        )

    def ask_import_archive(self, loose_files: bool = False) -> None:
        """Ask for an older game's archive mod (or a folder of its files), its game and an empty folder, then import
        it there as a project (docs/mod-import.md, section 16)."""
        chosen = importdialog.ask_archive(self, loose_files)
        folder = importdialog.ask_target(self, chosen[0]) if chosen else None
        if chosen and folder:
            self.import_archive(chosen[0], chosen[1], folder)

    def import_archive(self, source: Path, game: str | None, folder: Path, is_waiting: bool = False) -> None:
        """The archive import off the window's thread, as ``import_mod`` (Stop removes the half-made project)."""
        self._job(
            folder,
            lambda talk, done: importdialog.start_archive(
                talk, self.m_background, source, game, folder, self.m_shell.m_settings, done, is_waiting
            ),
        )

    def resume_import(self, folder: Path, is_waiting: bool = False) -> None:
        """Continue a half-made import where it stopped."""
        self._job(
            folder,
            lambda talk, done: importdialog.start_resume(
                talk, self.m_background, folder, self.m_shell.m_settings, done, is_waiting, self.m_shell.m_settings_path
            ),
        )

    def _job(self, folder: Path, starter) -> None:
        self._busy(True)
        self._m_importing = folder
        self.m_talk = CJobTalk(self)
        self.m_talk.said.connect(self.say)
        self.m_stop_button.setEnabled(True)
        self.m_stop_button.show()
        starter(self.m_talk, self._imported)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: a running import or build asks whether to close anyway."""
        if self.m_talk is not None:
            text = tr("common.import_running_close")
        elif self._m_is_building:
            text = tr("common.build_running_close")
        else:
            return True
        return QMessageBox.question(self, tr("common.project"), text) == QMessageBox.StandardButton.Yes

    def stop_work(self) -> None:
        """The Toolbox closes after all agreed: the import stops at its next step and keeps its work (the next start
        offers to resume it)."""
        if self.m_talk is not None:
            self.m_talk.stop()

    def stop_import(self) -> None:
        if self.m_talk is not None:
            self.m_talk.stop()
            self.m_stop_button.setEnabled(False)

    def _imported(self, result) -> None:
        self._ended()
        for line in importdialog.explain(result, self._m_importing):
            self.say(line)
        if not isinstance(result, Exception):
            self.m_shell.save_settings()  # the remembered project and the own GUID reserve
            self.use(result.folder)

    def _ended(self) -> None:
        self.m_stop_button.hide()
        if self.m_talk is not None:
            self.m_talk.said.disconnect()
            self.m_talk.deleteLater()
            self.m_talk = None
        self._busy(False)

    def offer_pending(self, is_waiting: bool = False) -> None:
        """A half-made import (a stop that kept its work, or a run that ended in the middle): resume it, remove it, or
        leave it for the next start."""
        registry = importdialog.registry_of(self.m_shell.m_settings_path)
        choice = importdialog.ask_pending(self, importresume.pending(registry))
        if choice is None:
            return
        action, pending_import = choice
        if action == "resume":
            self.resume_import(pending_import.folder, is_waiting)
        elif importresume.discard(pending_import.folder, registry):
            self.say(tr("common.import_discarded", folder=pending_import.folder))

    def ask_reimport(self) -> None:
        """Compare the project's mod with its source again and take what changed (a file both sides changed asks)."""
        meta = importmeta.read_meta(self.m_project.folder)
        if meta is None:
            QMessageBox.information(
                self, tr("common.project"), tr("common.reimport_not_imported", folder=self.m_project.folder)
            )
            return
        source = Path(meta.source) if Path(meta.source).exists() else importdialog.ask_source(self)
        if source is not None:
            self.reimport(source)

    def reimport(self, source: Path, choices=None, is_waiting: bool = False) -> None:
        """The comparison off the window's thread, then the question (``choices``: answered already, for a test), then
        the change."""
        folder = self.m_project.folder
        self._job(
            folder,
            lambda talk, _done: importdialog.start_reimport_plan(
                talk,
                self.m_background,
                folder,
                source,
                lambda made: self._planned(made, choices, is_waiting),
                is_waiting,
            ),
        )

    def _planned(self, made, choices, is_waiting: bool) -> None:
        self._ended()
        if isinstance(made, Exception):
            self.say(tr("common.import_failed", error=made))
            return
        for line in made.lines():
            self.say(line)
        if choices is None:
            choices = importdialog.ask_conflicts(self, made)
        if choices is None:
            made.dispose()
            return
        self._job(
            made.m_folder,
            lambda talk, _done: importdialog.start_reimport_apply(
                talk, self.m_background, made, choices, self._reapplied, is_waiting
            ),
        )

    def _reapplied(self, result) -> None:
        self._ended()
        for line in [tr("common.import_failed", error=result)] if isinstance(result, Exception) else result:
            self.say(line)
        self.load_current()

    def use_workspace(self) -> None:
        self.m_shell.m_settings.project = ""
        self.m_shell.save_settings()
        self.load_current()

    # saving, building
    def save(self) -> bool:
        try:
            self.m_project = self.edited()
            self.m_project.save()
            projects.remember(self.m_shell.m_settings, self.m_project.folder)
            self.m_shell.save_settings()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("common.project"), str(exc))
            return False
        self.load(self.m_project)
        self.say(tr("common.saved_path", path=self.m_project.folder / projects.FILE))
        return True

    def say(self, text: str) -> None:
        self.m_log.appendPlainText(text)

    def build(self, then_install: bool = False, is_waiting: bool = False) -> None:
        """Save, then build the mod in the background (``is_waiting``: in this thread, for a test)."""
        if not self.save():
            return
        project, settings = self.m_project, self.m_shell.m_settings
        lines: list[str] = []

        def work():
            result = projects.build(project, self.m_parts, settings, lines.append, self.m_shell.m_deliveries)
            if then_install:
                lines.append(projects.install_built(project, settings, self.m_shell.m_deliveries, lines.append))
            return result

        self._busy(True)
        self._m_is_building = True
        if is_waiting:
            return self._built(work_safely(work), lines)
        self.m_background.run(work, lambda result: self._built(result, lines))

    def install(self) -> None:
        self.build(then_install=True)

    def _busy(self, busy: bool) -> None:
        for button in self.m_buttons.values():
            button.setEnabled(not busy)

    def _built(self, result, lines: list[str]) -> None:
        self._m_is_building = False
        self._busy(False)
        for line in lines:
            self.say(line)
        if isinstance(result, Exception):
            self.say(tr("common.build_failed_error", error=f"{type(result).__name__}: {result}"))
            return
        for line in result.lines():
            self.say(line)


def _ids(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def work_safely(work):
    try:
        return work()
    except Exception as exc:  # pattern: ok (a build failure is shown in the log, as the background run does)
        return exc
