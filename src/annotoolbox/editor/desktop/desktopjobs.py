"""The workbench's jobs (a mixin of ``workbench.MainWindow``): the island lists' builds, ports and restores started
through ``editorapp.App`` (one job at a time), their log followed in the Log page (``poll``, on the window's timer),
the island a build made opened when it is done, the question and the wait for what still runs when the window closes
(``ask_running_job``, ``finish_work``); and the tool windows registered at ``features.TOOLS``. Plan and use:
``docs/island-editor.md``.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from ...infra.i18n import N_, tr
from ...suite import features, widgets
from .. import archivedelivery

JOB_STATES = {
    "running": N_("islands.running"),
    "done": N_("islands.done"),
    "failed": N_("common.failed"),
    "stopped": N_("viewer.stopped"),
}  # what ``editorapp.Job`` reports -> how it is shown
CLOSE_WAIT = 0.5  # seconds the closing window gives the island's background reads (a continental island's took 4.5)
WAIT_STEP = 0.03  # seconds between two looks at a worker the closing window waits for


STOP, WAIT, CANCEL = "stop", "wait", "cancel"


def ask_stop_wait_cancel(parent, title: str, text: str) -> str:
    """The question of a window closed while a job runs: ``STOP`` it, ``WAIT`` for it (Enter) or ``CANCEL`` the
    closing (Esc)."""
    roles = QMessageBox.ButtonRole
    choices = ((STOP, tr("islands.stop_the_build"), roles.DestructiveRole), (WAIT, tr("common.wait"), roles.AcceptRole),
               (CANCEL, QMessageBox.StandardButton.Cancel, None))  # fmt: skip
    return widgets.ask_choice(parent, title, text, choices, default=WAIT, escape=CANCEL)


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
        from ...islands.regions import GENERATION
        from ..islandconvert import targets

        offered = targets(key, path)
        if not offered:
            QMessageBox.information(self, self.title, tr("islands.no_port_offered_island"))
            return
        labels = [text for _id, text in offered]
        text, ok = QInputDialog.getItem(
            self, self.title, tr("islands.port_island_to", island=path.rsplit("/", 1)[-1]), labels, 0, False
        )
        if not ok:
            return
        target = offered[labels.index(text)][0]
        ways = [tr("islands.place_in_pool"), tr("islands.place_on_maps")]  # islandconvert.FITS after the pool
        way, ok = QInputDialog.getItem(self, self.title, tr("islands.port_how_placed"), ways, 0, False)
        if not ok:
            return
        fit = "" if ways.index(way) == 0 else "map"
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        reskin = (  # off unless the person says yes (No is the default button)
            key in GENERATION
            and QMessageBox.question(self, self.title, tr("islands.reskin_question"), yes | no, no) == yes
        )
        answer = QMessageBox.question(self, self.title, tr("islands.install_ported_island_mods_folder"))
        install = answer == QMessageBox.StandardButton.Yes
        self._start(lambda: self.app.convert_region(key, path, target, install, fit, reskin))

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

    def ask_running_job(self) -> str:
        """The window is closed: ``WAIT`` when no job runs, else what the person answers (``ask_stop_wait_cancel``).
        An island being read is waited for without a question: it takes seconds and writes nothing."""
        job = self.app.job
        if job is None or job.wait(0):
            return WAIT
        return ask_stop_wait_cancel(self, self.title, tr("islands.job_running_stop_wait", name=job.name))

    def finish_work(self) -> None:
        """The window closes: wait for the running job and for the island being read, then half a second for the
        background reads of the island just closed (a continental island's took 4.5 s: the window closed late). They
        only read and fill caches (written whole or not at all); a job cut off mid-write left a half-written build or
        install behind. The window keeps painting and takes no input; the status line says what it waits for
        (island-editor "Closing the window")."""
        self._open_after = None  # a build that ends now opens nothing into the closing window
        self.closing, said = True, None
        try:
            while (name := self.busy_with()) is not None:
                if name != said:
                    said = name
                    self.statusBar().showMessage(tr("islands.waiting_before_closing", name=name))
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                time.sleep(WAIT_STEP)
        finally:
            self.closing = False
        self.background.join(CLOSE_WAIT)  # reads of the island just closed: cut off, they write nothing but caches

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
            self.statusBar().showMessage(
                tr(
                    "islands.name_state",
                    name=status["name"],
                    state=tr(JOB_STATES.get(status["state"], status["state"])),
                )
            )
            if self._job_state == "running":  # just finished: its island is built (or not)
                QTimer.singleShot(0, self.reload)
                if status["state"] == "done" and self._open_after:
                    name, self._open_after = self._open_after, None
                    QTimer.singleShot(0, lambda: self.open("built", name))
            self._job_state = status["state"]
