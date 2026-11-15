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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import N_, number, tr
from ..modloader.mods import DEPENDENCY_KINDS
from ..sources import gametree
from . import guidrangedialog, projectdeps
from . import guidranges as gr
from . import project as projects
from .background import Background


class ProjectDialog(QDialog):
    def __init__(self, shell, parts: list[projects.Part]) -> None:
        super().__init__(shell)
        self.shell, self.parts = shell, parts
        self.setWindowTitle(tr("Project"))
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
        self._layout()
        self.load_current()

    def _layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self.folder, 1)
        for label, slot in (
            (N_("Open…"), self.ask_open),
            (N_("New…"), self.ask_new),
            (N_("Use the workspace"), self.use_workspace),
        ):
            top.addWidget(self._button(label, slot))
        form = QFormLayout()
        form.addRow(tr("Mod id"), self.mod_id)
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Version"), self.version)
        form.addRow(tr("Game"), self.game)
        guids = QHBoxLayout()
        guids.addWidget(self.guids, 1)
        guids.addWidget(self._button(N_("Choose…"), self.choose_range))
        form.addRow(tr("GUID range"), guids)
        for kind, edit in self.deps.items():
            edit.setPlaceholderText(
                tr("mod ids, separated by commas (* loads it last)")
                if kind == "LoadAfter"
                else tr("mod ids, separated by commas")
            )
            form.addRow(
                tr("Load after") if kind == "LoadAfter" else kind, edit
            )  # i18n: ok (the other kinds are the loader's keys)
        carried = QHBoxLayout()
        carried.addWidget(self.bundle, 1)
        side = QVBoxLayout()
        side.addWidget(self._button(N_("Add mod folder…"), self.add_bundle))
        side.addWidget(self._button(N_("Remove"), self.remove_bundle))
        carried.addLayout(side)
        form.addRow(tr("Mods built in (nested)"), carried)
        actions = QHBoxLayout()
        for label, slot in (
            (N_("Save"), self.save),
            (N_("Build mod"), self.build),
            (N_("Build && install"), self.install),
        ):
            actions.addWidget(self._button(label, slot))
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(form)
        lay.addWidget(QLabel(tr("What the apps hold for this project's game:")))
        lay.addWidget(self.contents)
        lay.addLayout(actions)
        lay.addWidget(self.log, 1)

    def _button(self, label: str, slot) -> QPushButton:
        button = QPushButton(tr(label))
        button.clicked.connect(lambda _c=False: slot())
        self.buttons[label.replace("&&", "&")] = button
        return button

    # the project shown
    def load_current(self) -> None:
        """The settings' project; one whose file does not read is shown as such, with Open, New and the workspace
        still there to choose another (Save, Build and Install wait for a project that reads)."""
        try:
            self.load(projects.current(self.shell.settings))
        except ValueError as exc:
            self.load(projects.Project(projects.folder_of(self.shell.settings)))
            self.folder.setText(tr("{error}: open another project or use the workspace", error=exc))
            for key in ("Save", "Build mod", "Build & install"):
                self.buttons[key].setEnabled(False)
            self.say(str(exc))

    def load(self, project: projects.Project) -> None:
        self.project = project
        self._busy(False)
        if project.defined:
            self.folder.setText(str(project.folder))
        else:
            self.folder.setText(
                tr("{folder} (no project file yet: the defaults; Save writes one)", folder=project.folder)
            )
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
        name = QFileDialog.getExistingDirectory(self, tr("A mod folder (with its modinfo.json) to build in"))
        if name:
            self.bundle.addItem(name)

    def remove_bundle(self) -> None:
        for item in self.bundle.selectedItems():
            self.bundle.takeItem(self.bundle.row(item))

    def show_range(self) -> None:
        lo, hi = self.guid_sel.span
        how = {gr.OWN: N_("my own range"), gr.PUBLIC: N_("a declared free range"), gr.CUSTOM: N_("typed in")}
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
                note = tr("{part}: does not read ({error})", part=title, error=exc)
            else:
                if has:
                    note = tr("{part}: edits to build", part=title)
                else:
                    note = tr("{part}: nothing for this game", part=title)
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
            self.say(tr("GUID range: {problem}", problem=problem.text))
        return p

    # choosing a project
    def use(self, folder: Path) -> None:
        """Open the project in ``folder``; one that does not read is said and not made the settings' project."""
        try:
            project = projects.Project.open(folder)
        except ValueError as exc:
            QMessageBox.warning(self, tr("Project"), str(exc))
            return
        self.shell.settings.project = str(folder)
        projects.remember(self.shell.settings, folder)
        self.shell.save_settings()
        self.load(project)

    def ask_open(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("A project folder"))
        if name:
            self.use(Path(name))

    def ask_new(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("An empty folder for the new project"))
        if not name:
            return
        folder = Path(name)
        if (folder / projects.FILE).is_file():
            QMessageBox.information(self, tr("Project"), tr("That folder holds a project already: it is opened."))
        else:
            made = projects.Project.open(folder)
            try:
                made.choose_range(gr.OWN, "", made.guid_range, self.shell.settings)
                made.save()
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, tr("Project"), str(exc))
                return
        self.use(folder)

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
            QMessageBox.warning(self, tr("Project"), str(exc))
            return False
        self.load(self.project)
        self.say(tr("saved {path}", path=self.project.folder / projects.FILE))
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
            self.say(tr("the build failed: {error}", error=f"{type(result).__name__}: {result}"))
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
