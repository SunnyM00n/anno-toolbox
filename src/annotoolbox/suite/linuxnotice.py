"""The note that Linux support is experimental, where a person meets it: the setup page's first lines and ``Help >
About`` (``attach``). Off Linux there is no note; the link is the project's issue tracker where this build has a
release page (``updates``), else the text alone. docs/linux.md "Phase 3 done".
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMessageBox

from .. import __version__
from ..infra.i18n import tr
from . import centralmenu, updates


def applies() -> bool:
    return sys.platform.startswith("linux")


def issues_url() -> str:
    return f"{updates.source_url()}/issues" if updates.enabled() else ""


def text(html: bool = False) -> str:
    """ "Linux support is experimental" with the way to report a problem; ``html``: the link as a link."""
    url = issues_url()
    if not url:
        return tr("common.linux_experimental_report_here")
    link = f'<a href="{url}">{url}</a>' if html else url
    return tr("common.linux_experimental_report", link=link)


def label() -> QLabel | None:
    """The note as a label for a page (None off Linux)."""
    if not applies():
        return None
    note = QLabel(text(html=True))
    note.setWordWrap(True)
    note.setOpenExternalLinks(True)
    return note


def about(window) -> None:
    body = f"<p>{tr('common.about_toolbox_text', version=__version__)}</p>"
    if applies():
        body += f"<p>{text(html=True)}</p>"
    QMessageBox.about(window, tr("common.about_toolbox"), body)


def attach(window) -> QAction:
    """``Help > About...`` of a suite window (the menu is the update checker's or the setup's, else made here)."""
    menu = centralmenu.menu_of(window, centralmenu.HELP)
    action = QAction(tr("common.about_toolbox_dots"), window)
    action.triggered.connect(lambda _c=False: about(window))
    menu.addAction(action)
    return action
