"""The workbench's jobs (a mixin of ``workbench.MainWindow``): the island lists' builds, ports and restores started
through ``editorapp.App`` (one job at a time), their log followed in the Log page (``poll``, on the window's timer),
the island a build made opened when it is done; and the tool windows registered at ``features.TOOLS``. Plan and
use: ``docs/island-editor.md``.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ...infra.i18n import N_, tr
from ...suite import features
from .. import archivedelivery

JOB_STATES = (N_("running"), N_("done"), N_("failed"))  # what ``editorapp.Job`` reports, shown with tr


class JobRunner:
    """The jobs' and tools' methods; ``MainWindow`` brings ``app``, ``log``, ``tool_windows`` and the pages."""

    def open_tool(self, name: str) -> None:
        """Open the window of the tool registered as ``name`` (``features.TOOLS``)."""
        tool = features.extension(features.TOOLS, name)
        if tool is None:
            return
        try:
            window = tool.open(self)
        except Exception as exc:
            QMessageBox.warning(self, self.title, f"{type(exc).__name__}: {exc}")
            return
        self.tool_windows = [w for w in self.tool_windows if w.isVisible()] + [window]
        window.show()

    def _start(self, start, open_after: str | None = None) -> None:
        """Start a job (``editorapp.App.build`` and the like) and follow it in the Log; ``open_after``: the built
        island to open when it is done."""
        try:
            start()
        except Exception as exc:
            QMessageBox.warning(self, self.title, str(exc))
            return
        self._log_next, self._open_after, self._job_state = 0, open_after, "running"
        self.log.clear()
        self.show_page("Log")

    def convert(self, source: str, island: str, target: str) -> None:
        name = island.rsplit("/", 1)[-1].removesuffix(".isd").lower()
        self._start(lambda: self.app.convert(source, island, target), open_after=name)

    def convert_region(self, key: str, path: str) -> None:
        """Ask what the island becomes (``islandconvert.targets``) and whether to install it, then convert it."""
        from ..islandconvert import targets

        offered = targets(key, path)
        if not offered:
            QMessageBox.information(self, self.title, tr("No port is offered for this island."))
            return
        labels = [text for _id, text in offered]
        text, ok = QInputDialog.getItem(
            self, self.title, tr("Port {island} to:", island=path.rsplit("/", 1)[-1]), labels, 0, False
        )
        if not ok:
            return
        target = offered[labels.index(text)][0]
        answer = QMessageBox.question(self, self.title, tr("Install the ported island into the mods folder too?"))
        install = answer == QMessageBox.StandardButton.Yes
        self._start(lambda: self.app.convert_region(key, path, target, install))

    def convert_new(self, size: int) -> None:
        self._start(lambda: self.app.build(f"new{size}", install=False), open_after=f"new{size}")

    def rebuild(self, name: str, install: bool) -> None:
        self._start(lambda: self.app.build(name, install=install))

    def build_kind(self, kind: str, install: bool) -> None:
        if kind == "anno117":
            self._start(lambda: self.app.build117(install))
        elif kind == "anno1800":
            self._start(lambda: self.app.build1800(install))
        elif kind in archivedelivery.ISLAND_DELIVERIES:
            self._start(lambda: self.app.build_archives(kind, install))

    def build_opened(self, info: dict) -> None:
        """Build (and install) what the open island is, from its saved edit file."""
        if info["kind"] == "built":
            self.rebuild(info["name"], True)
        else:
            self.build_kind(info["kind"], True)

    def restore(self, key: str) -> None:
        """Give a game its own files back (``IslandDelivery.restore``)."""
        self._start(lambda: self.app.restore(key))

    def poll(self) -> None:
        """The running job's new log lines; when it has ended, the island lists are read again and the island it
        built is opened."""
        job = self.app.job
        if job is None:
            return
        status = job.status(self._log_next)
        for line in status["lines"]:
            self.log.appendPlainText(line)
        self._log_next = status["next"]
        if status["state"] != self._job_state:
            self.statusBar().showMessage(tr("{name}: {state}", name=status["name"], state=tr(status["state"])))
            if self._job_state == "running":  # just finished: its island is built (or not)
                QTimer.singleShot(0, self.reload)
                if status["state"] == "done" and self._open_after:
                    name, self._open_after = self._open_after, None
                    QTimer.singleShot(0, lambda: self.open("built", name))
            self._job_state = status["state"]
