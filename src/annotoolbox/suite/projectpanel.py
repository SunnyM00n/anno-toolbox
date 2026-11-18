"""The suite's Project window: which project is open, the mod it makes (id, name, version, game, GUID range), what
each app holds for it, and the one build and install of the mod (``suite.project``; docs/tool-suite.md, C4).

``ProjectDialog(shell, parts)`` is the window; the shell opens it from the switcher. Switching the project changes
where the apps read and write their work from the next island or game they open.
"""

from __future__ import annotations

from pathlib import Path

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
from . import guidrangedialog, importdialog, projectdeps
from . import guidranges as gr
from . import project as projects
from .background import Background, JobTalk


class ProjectDialog(QDialog):
    def __init__(self, shell, parts: list[projects.Part]) -> None:
        super().__init__(shell)
        self.shell, self.parts = shell, parts
        self.setWindowTitle(tr("common.project"))
        self.resize(720, 620)
        self.background = Background(self)
        self.folder = QLabel()
        self.mod_id, self.name, self.version = QLineEdit(), QLineEdit(), QLineEdit()
        self.game = QComboBox()
        for key in projects.ALL_GAMES:
            self.game.addItem(gametree.GAMES[key], key)
        self.guids = QLabel()
        self.guid_sel = guidrangedialog.Selection(gr.CUSTOM, "", projects.DEFAULT_RANGE)
        self.deps = {kind: QLineEdit() for kind in DEPENDENCY_KINDS}
        self.bundle = QListWidget()
        self.contents = QListWidget()
        self.log = QPlainTextEdit(readOnly=True)
        self.buttons: dict[str, QPushButton] = {}
        self.talk: JobTalk | None = None  # the running import's progress and stop
        self.stop_button = QPushButton(tr("common.stop"))
        self.stop_button.clicked.connect(self.stop_import)
        self.stop_button.hide()
        self._layout()
        self.load_current()

    def _layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self.folder, 1)
        for label, slot in (
            (N_("common.open"), self.ask_open),
            (N_("common.new"), self.ask_new),
            (N_("common.use_workspace"), self.use_workspace),
        ):
            top.addWidget(self._button(label, slot))
        top.addWidget(self._import_button())
        form = QFormLayout()
        form.addRow(tr("common.mod_id"), self.mod_id)
        form.addRow(tr("common.name"), self.name)
        form.addRow(tr("common.version"), self.version)
        form.addRow(tr("common.game"), self.game)
        guids = QHBoxLayout()
        guids.addWidget(self.guids, 1)
        guids.addWidget(self._button(N_("common.choose"), self.choose_range))
        form.addRow(tr("common.guid_range"), guids)
        for kind, edit in self.deps.items():
            edit.setPlaceholderText(
                tr("common.mod_ids_separated_commas_loads")
                if kind == "LoadAfter"
                else tr("common.mod_ids_separated_commas")
            )
            form.addRow(
                tr("common.load_after") if kind == "LoadAfter" else kind, edit
            )  # i18n: ok (the other kinds are the loader's keys)
        carried = QHBoxLayout()
        carried.addWidget(self.bundle, 1)
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
        actions.addWidget(self.stop_button)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(form)
        lay.addWidget(QLabel(tr("common.what_apps_hold_projects_game")))
        lay.addWidget(self.contents)
        lay.addLayout(actions)
        lay.addWidget(self.log, 1)

    def _button(self, label: str, slot) -> QPushButton:
        button = QPushButton(tr(label))
        button.clicked.connect(lambda _c=False: slot())
        self.buttons[english(label).replace("&&", "&")] = button  # the buttons' keys: their English words
        return button

    def _import_button(self) -> QPushButton:
        """ "Import a mod as a project...": a menu of the two sources, a mod folder and a ``.zip``."""
        button = QPushButton(tr("common.import_mod_button"))
        menu = QMenu(button)
        menu.addAction(tr("common.import_mod_folder")).triggered.connect(lambda _c=False: self.ask_import())
        menu.addAction(tr("common.import_mod_zip")).triggered.connect(lambda _c=False: self.ask_import(True))
        button.setMenu(menu)
        self.buttons[english(N_("common.import_mod_button"))] = button
        return button

    # the project shown
    def load_current(self) -> None:
        """The settings' project; one whose file does not read is shown as such, with Open, New and the workspace
        still there to choose another (Save, Build and Install wait for a project that reads)."""
        try:
            self.load(projects.current(self.shell.settings))
        except ValueError as exc:
            self.load(projects.Project(projects.folder_of(self.shell.settings)))
            self.folder.setText(tr("common.open_another_project_use_workspace", error=exc))
            for key in ("Save", "Build mod", "Build & install"):
                self.buttons[key].setEnabled(False)
            self.say(str(exc))

    def load(self, project: projects.Project) -> None:
        self.project = project
        self._busy(False)
        if project.defined:
            self.folder.setText(str(project.folder))
        else:
            self.folder.setText(tr("common.no_project_file_yet_defaults", folder=project.folder))
        self.mod_id.setText(project.mod_id)
        self.name.setText(project.name)
        self.version.setText(project.version)
        self.game.setCurrentIndex(max(0, self.game.findData(project.game)))
        self.guid_sel = guidrangedialog.Selection(project.guid_choice, project.guid_source, project.guid_range)
        for kind, edit in self.deps.items():
            edit.setText(", ".join(project.dependencies.get(kind, [])))
        self.bundle.clear()
        self.bundle.addItems([str(b) for b in project.bundle])
        self.show_range()
        self.show_contents()

    def add_bundle(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("common.mod_folder_modinfo_json_build"))
        if name:
            self.bundle.addItem(name)

    def remove_bundle(self) -> None:
        for item in self.bundle.selectedItems():
            self.bundle.takeItem(self.bundle.row(item))

    def show_range(self) -> None:
        lo, hi = self.guid_sel.span
        how = {
            gr.OWN: N_("common.my_own_range_lower"),
            gr.PUBLIC: N_("common.declared_free_range"),
            gr.CUSTOM: N_("common.typed_in"),
        }
        self.guids.setText(f"{number(lo)} - {number(hi)} ({tr(how[self.guid_sel.choice])})")

    def choose_range(self) -> None:
        """The three ways to a range (``guidrangedialog``): own, publicly declared, typed in."""
        settings, game = self.shell.settings, self.game.currentData()
        others = projects.other_ranges(settings, self.project.folder, game)
        picked = guidrangedialog.ask(self, game, settings, others, self.guid_sel)
        if picked is not None:
            self.guid_sel = picked
            self.shell.save_settings()  # the own reserve is made on first use
            self.show_range()

    def show_contents(self) -> None:
        self.contents.clear()
        for part in self.parts:
            title = tr(part.title)
            try:
                has = part.has_work(self.project)
            except (OSError, ValueError) as exc:
                note = tr("common.part_not_read_error", part=title, error=exc)
            else:
                if has:
                    note = tr("common.edits_to_build", part=title)
                else:
                    note = tr("common.part_nothing_game", part=title)
            QListWidgetItem(note, self.contents)

    def edited(self) -> projects.Project:
        """The project as the fields say (``ValueError`` with the reason when they do not make one)."""
        p = projects.Project(
            self.project.folder,
            mod_id=self.mod_id.text().strip(),
            name=self.name.text().strip(),
            version=self.version.text().strip() or "0.1.0",
            game=self.game.currentData(),
            category=self.project.category,
            description=self.project.description,
            guid_range=self.guid_sel.span,
            guid_choice=self.guid_sel.choice,
            guid_source=self.guid_sel.source,
            dependencies=projectdeps.clean({k: _ids(e.text()) for k, e in self.deps.items()}),
            bundle=[self.bundle.item(k).text() for k in range(self.bundle.count())],
            defined=self.project.defined,
        )
        p.validate()
        problems = gr.check(p.game, p.guid_range, projects.other_ranges(self.shell.settings, p.folder, p.game))
        if gr.blocking(problems):
            raise ValueError(gr.blocking(problems)[0].text)
        for problem in problems:
            self.say(tr("common.guid_range_problem", problem=problem.text))
        return p

    # choosing a project
    def use(self, folder: Path) -> None:
        """Open the project in ``folder``; one that does not read is said and not made the settings' project."""
        try:
            project = projects.Project.open(folder)
        except ValueError as exc:
            QMessageBox.warning(self, tr("common.project"), str(exc))
            return
        self.shell.settings.project = str(folder)
        projects.remember(self.shell.settings, folder)
        self.shell.save_settings()
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
            made = projects.Project.open(folder)
            try:
                made.choose_range(gr.OWN, "", made.guid_range, self.shell.settings)
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

    def import_mod(self, source: Path, folder: Path, wait: bool = False) -> None:
        """The import off the window's thread, its steps in the log, Stop ends it; the project opens when it is done
        (``wait``: in this thread, for a test)."""
        self._busy(True)
        self.talk = JobTalk(self)
        self.talk.said.connect(self.say)
        self.stop_button.setEnabled(True)
        self.stop_button.show()
        importdialog.start(self.talk, self.background, source, folder, self.shell.settings, self._imported, wait)

    def stop_import(self) -> None:
        if self.talk is not None:
            self.talk.stop()
            self.stop_button.setEnabled(False)

    def _imported(self, result) -> None:
        self.stop_button.hide()
        if self.talk is not None:
            self.talk.said.disconnect()
            self.talk.deleteLater()
            self.talk = None
        self._busy(False)
        for line in importdialog.explain(result):
            self.say(line)
        if not isinstance(result, Exception):
            self.shell.save_settings()  # the remembered project and the own GUID reserve
            self.use(result.folder)

    def use_workspace(self) -> None:
        self.shell.settings.project = ""
        self.shell.save_settings()
        self.load_current()

    # saving, building
    def save(self) -> bool:
        try:
            self.project = self.edited()
            self.project.save()
            projects.remember(self.shell.settings, self.project.folder)
            self.shell.save_settings()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("common.project"), str(exc))
            return False
        self.load(self.project)
        self.say(tr("common.saved_path", path=self.project.folder / projects.FILE))
        return True

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)

    def build(self, then_install: bool = False, wait: bool = False) -> None:
        """Save, then build the mod in the background (``wait``: in this thread, for a test)."""
        if not self.save():
            return
        project, settings = self.project, self.shell.settings
        lines: list[str] = []

        def work():
            result = projects.build(project, self.parts, settings, lines.append, self.shell.deliveries)
            if then_install:
                lines.append(projects.install_built(project, settings, self.shell.deliveries, lines.append))
            return result

        self._busy(True)
        if wait:
            return self._built(work_safely(work), lines)
        self.background.run(work, lambda result: self._built(result, lines))

    def install(self) -> None:
        self.build(then_install=True)

    def _busy(self, busy: bool) -> None:
        for button in self.buttons.values():
            button.setEnabled(not busy)

    def _built(self, result, lines: list[str]) -> None:
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
    except Exception as exc:  # shown in the log, as the background run does
        return exc
