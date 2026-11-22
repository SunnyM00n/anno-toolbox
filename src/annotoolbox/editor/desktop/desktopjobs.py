"""The workbench's jobs (a mixin of ``workbench.CMainWindow``): the island lists' builds, ports and restores started
through ``editorapp.CApp`` (one job at a time), their log followed in the Log page (``poll``, on the window's timer),
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
}  # what ``editorapp.CJob`` reports -> how it is shown
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


class CJobRunner:
    """The jobs' and tools' methods; ``CMainWindow`` brings ``app``, ``log``, ``tool_windows`` and the pages."""

    def open_tool(self, name: str) -> None:
        """Open the window of the tool registered as ``name`` (``features.TOOLS``)."""
        tool = features.extension(features.TOOLS, name)
        if tool is None:
            return
        try:
            window = tool.open(self)
        except Exception as exc:
            QMessageBox.warning(self, self.m_title, f"{type(exc).__name__}: {exc}")
            return
        self.m_tool_windows = [tool_window for tool_window in self.m_tool_windows if tool_window.isVisible()] + [window]
        window.show()

    def _start(self, start, open_after: str | None = None) -> None:
        """Start a job (``editorapp.CApp.build`` and the like) and follow it in the Log; ``open_after``: the built
        island to open when it is done."""
        try:
            start()
        except Exception as exc:
            QMessageBox.warning(self, self.m_title, str(exc))
            return
        self._m_log_next, self._m_open_after, self._m_job_state = 0, open_after, "running"
        self.m_log.clear()
        self.show_page("Log")

    def convert(self, source: str, island: str, target: str) -> None:
        name = island.rsplit("/", 1)[-1].removesuffix(".isd").lower()
        self._start(lambda: self.m_app.convert(source, island, target), open_after=name)

    def convert_region(self, key: str, path: str) -> None:
        """Ask what the island becomes (``islandconvert.targets``) and whether to install it, then convert it."""
        from ...islands.regions import GENERATION
        from ..islandconvert import targets

        offered = targets(key, path)
        if not offered:
            QMessageBox.information(self, self.m_title, tr("islands.no_port_offered_island"))
            return
        labels = [text for _id, text in offered]
        text, accepted = QInputDialog.getItem(
            self, self.m_title, tr("islands.port_island_to", island=path.rsplit("/", 1)[-1]), labels, 0, False
        )
        if not accepted:
            return
        target = offered[labels.index(text)][0]
        ways = [tr("islands.place_in_pool"), tr("islands.place_on_maps")]  # islandconvert.FITS after the pool
        way, accepted = QInputDialog.getItem(self, self.m_title, tr("islands.port_how_placed"), ways, 0, False)
        if not accepted:
            return
        fit = "" if ways.index(way) == 0 else "map"
        yes_button, no_button = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        reskin = (  # off unless the person says yes (No is the default button)
            key in GENERATION
            and QMessageBox.question(
                self, self.m_title, tr("islands.reskin_question"), yes_button | no_button, no_button
            )
            == yes_button
        )
        answer = QMessageBox.question(self, self.m_title, tr("islands.install_ported_island_mods_folder"))
        install = answer == QMessageBox.StandardButton.Yes
        self._start(lambda: self.m_app.convert_region(key, path, target, install, fit, reskin))

    def convert_new(self, size_tiles: int) -> None:
        self._start(lambda: self.m_app.build(f"new{size_tiles}", install_enabled=False), open_after=f"new{size_tiles}")

    def rebuild(self, name: str, install: bool) -> None:
        self._start(lambda: self.m_app.build(name, install_enabled=install))

    def build_kind(self, kind: str, install: bool) -> None:
        if kind == "anno117":
            self._start(lambda: self.m_app.build117(install))
        elif kind == "anno1800":
            self._start(lambda: self.m_app.build1800(install))
        elif kind in archivedelivery.ISLAND_DELIVERIES:
            self._start(lambda: self.m_app.build_archives(kind, install))

    def build_opened(self, island_info: dict) -> None:
        """Build (and install) what the open island is, from its saved edit file."""
        if island_info["kind"] == "built":
            self.rebuild(island_info["name"], True)
        else:
            self.build_kind(island_info["kind"], True)

    def restore(self, key: str) -> None:
        """Give a game its own files back (``SIslandDelivery.restore``)."""
        self._start(lambda: self.m_app.restore(key))

    def ask_running_job(self) -> str:
        """The window is closed: ``WAIT`` when no job runs, else what the person answers (``ask_stop_wait_cancel``).
        An island being read is waited for without a question: it takes seconds and writes nothing."""
        job = self.m_app.m_job
        if job is None or job.wait(0):
            return WAIT
        return ask_stop_wait_cancel(self, self.m_title, tr("islands.job_running_stop_wait", name=job.m_name))

    def finish_work(self) -> None:
        """The window closes: wait for the running job and for the island being read, then half a second for the
        background reads of the island just closed (a continental island's took 4.5 s: the window closed late). They
        only read and fill caches (written whole or not at all); a job cut off mid-write left a half-written build or
        install behind. The window keeps painting and takes no input; the status line says what it waits for
        (island-editor "Closing the window")."""
        self._m_open_after = None  # a build that ends now opens nothing into the closing window
        self.m_closing, said = True, None
        try:
            while (name := self.busy_with()) is not None:
                if name != said:
                    said = name
                    self.statusBar().showMessage(tr("islands.waiting_before_closing", name=name))
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                time.sleep(WAIT_STEP)
        finally:
            self.m_closing = False
        self.m_background.join(CLOSE_WAIT)  # reads of the island just closed: cut off, they write nothing but caches

    def poll(self) -> None:
        """The running job's new log lines; when it has ended, the island lists are read again and the island it
        built is opened."""
        job = self.m_app.m_job
        if job is None:
            return
        status = job.status(self._m_log_next)
        for line in status["lines"]:
            self.m_log.appendPlainText(line)
        self._m_log_next = status["next"]
        if status["state"] != self._m_job_state:
            self.statusBar().showMessage(
                tr(
                    "islands.name_state",
                    name=status["name"],
                    state=tr(JOB_STATES.get(status["state"], status["state"])),
                )
            )
            if self._m_job_state == "running":  # just finished: its island is built (or not)
                QTimer.singleShot(0, self.reload)
                if status["state"] == "done" and self._m_open_after:
                    name, self._m_open_after = self._m_open_after, None
                    QTimer.singleShot(0, lambda: self.open("built", name))
            self._m_job_state = status["state"]
