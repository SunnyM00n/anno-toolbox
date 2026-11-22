"""The removal of the game look's compiled shaders that are no longer needed (``render.shaderclean``): a pop-up after
an update that moved a phase to the native backend or after a game update, and an entry of the Help menu that offers
it at any time with its size. The search and the removal run off the window's thread, the removal with a line in the
status bar; a file that does not go is said, nothing else fails.

``attach(window, install)`` is the entry (``suite.shell``). docs/tool-suite.md section 21, "The start-up stage".
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMessageBox

from .. import __version__
from ..infra.i18n import N_, tr
from ..render import shaderclean
from . import centralmenu, widgets

SETTING = "shadercleanup"  # Settings.apps' entry: what was asked (``shaderclean.answered``)
PHASES = {
    "terrain": N_("common.shader_cleanup_phase_terrain"),
    "ocean": N_("common.shader_cleanup_phase_ocean"),
    "post": N_("common.shader_cleanup_phase_post"),
}  # a native phase as the question names it


def _size(size_bytes: int) -> str:
    return f"{size_bytes / 1e6:,.0f} MB" if size_bytes >= 1e6 else f"{size_bytes / 1e3:,.0f} kB"


def find(install: str) -> shaderclean.SOffer:
    """What could go for the install (its game build: an earlier build's entries go too), the phases the native
    backend now draws with no need of their earlier compiles."""
    from pathlib import Path

    from ..models import shadernative
    from ..render import shaderstart
    from ..sources.gametree import open_game

    build = shadernative.install_build(open_game("anno117", Path(install))) if install else ""
    native = shaderstart.native_phases()
    got = shaderclean.offer(
        build, {phase.name for phase in shaderstart.phases() if phase.native and phase.native in native}
    )
    got.natives = sorted({phase.native for phase in shaderstart.phases() if phase.name in got.phases})
    return got


class CCleanup(QObject):
    """The pop-up and the menu entry of one window (module docstring)."""

    found = Signal(object, bool)  # (offer or an error, asked by the menu), from the search's thread
    step = Signal(int, int)
    ended = Signal(object)

    def __init__(self, window, install: str) -> None:
        super().__init__(window)
        self.m_window, self.m_install = window, install
        self.m_label = QLabel()
        self.m_label.hide()
        window.statusBar().addWidget(self.m_label)
        self.found.connect(self._found)
        self.step.connect(lambda done, total: self._line(tr("common.shader_cleanup_progress", done=done,
                                                                  total=total)))  # fmt: skip
        self.ended.connect(self._ended)
        self.m_busy = False

    def look(self, menu: bool = False) -> None:
        """Search in the background; the pop-up asks when there is something (``menu``: always answer)."""
        if self.m_busy:
            return
        self.m_busy = True
        if menu:
            self._line(tr("common.shader_cleanup_reading"))

        def work() -> None:
            try:
                got = find(self.m_install)
            except Exception as exc:  # an unreadable install: nothing offered
                got = exc
            self._emit(self.found, got, menu)

        threading.Thread(target=work, name="shader-cleanup", daemon=True).start()

    def _found(self, got, menu: bool) -> None:
        self.m_busy = False
        self.m_label.hide()
        state = self.m_window.m_settings.apps.setdefault(SETTING, {})
        if isinstance(got, Exception) or not got:
            if menu:
                QMessageBox.information(self.m_window, tr("common.shader_cleanup_title"),
                                        tr("common.shader_cleanup_none"))  # fmt: skip
            return
        if not menu and not shaderclean.should_ask(state, got, __version__):
            return
        choice = self._ask(got, menu)
        if not menu:
            self.m_window.m_settings.apps[SETTING] = shaderclean.answered(state, got, __version__, choice)
        if choice == "remove":
            self._remove(got)

    def _ask(self, got: shaderclean.SOffer, menu: bool) -> str:
        """The question with Remove, Not now and (after an update) Don't ask again, each with its access key."""
        names = [tr(PHASES[phase_name]) if phase_name in PHASES else phase_name for phase_name in got.natives]
        lines = [tr("common.shader_cleanup_phases", phases=", ".join(names))] if names else []
        lines += [tr("common.shader_cleanup_builds")] if got.builds else []
        lines.append(tr("common.shader_cleanup_question", count=f"{len(got.keys):,}", size=_size(got.size)))
        roles = QMessageBox.ButtonRole
        choices = [
            ("remove", tr("common.shader_cleanup_remove"), roles.AcceptRole),
            ("later", tr("common.shader_cleanup_not_now"), roles.RejectRole),
        ]
        if not menu:
            choices.append(("never", tr("common.shader_cleanup_never"), roles.NoRole))
        title = tr("common.shader_cleanup_title")
        return widgets.ask_choice(self.m_window, title, "\n\n".join(lines), choices, default="later", escape="later")

    def _remove(self, got: shaderclean.SOffer) -> None:
        self.m_busy = True

        def work() -> None:
            done = shaderclean.remove(got, lambda n, total: self._emit(self.step, n, total) if n % 50 == 0 else None)
            self._emit(self.ended, done)

        threading.Thread(target=work, name="shader-cleanup", daemon=True).start()

    def _ended(self, done: shaderclean.SRemoved) -> None:
        self.m_busy = False
        text = tr("common.shader_cleanup_done", count=f"{done.entries:,}", size=_size(done.size))
        if done.failed:
            text += "\n" + tr("common.shader_cleanup_failed", count=len(done.failed), first=done.failed[0])
        self._line(text.splitlines()[0])
        QMessageBox.information(self.m_window, tr("common.shader_cleanup_title"), text)
        self.m_label.hide()

    def _line(self, text: str) -> None:
        self.m_label.setText(text)
        self.m_label.show()

    @staticmethod
    def _emit(signal, *arguments) -> None:
        try:
            signal.emit(*arguments)
        except RuntimeError:  # the window closed meanwhile
            pass


def attach(window, install: str, ask: bool = True) -> CCleanup:
    """The Settings menu's entry and, with ``ask``, the search whose
    pop-up asks after an update."""
    cleanup = CCleanup(window, install)
    menu = centralmenu.menu_of(window, centralmenu.SETTINGS)
    action = QAction(tr("common.shader_cleanup_menu"), window)
    action.triggered.connect(lambda _c=False: cleanup.look(menu=True))
    menu.addAction(action)
    state = window.m_settings.apps.setdefault(SETTING, {})
    if ask and install and state.get("looked") != (seen := looked_for(install)):
        state["looked"] = seen  # once per program version and game build: a later start reads nothing
        cleanup.look()
    return cleanup


def looked_for(install: str) -> str:
    """The program version and the game build by its archives (a few file stats): what a search was made for."""
    from pathlib import Path

    from ..render import shaderstart

    return f"{__version__}:{shaderstart.quick_key(Path(install), 'cleanup')}"
