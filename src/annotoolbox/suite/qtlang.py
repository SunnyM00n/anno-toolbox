"""Qt's own texts (the standard buttons, the file dialog, the context menus) in the interface language, from the
translations the PySide6 install ships. ``install()`` is called by ``theme.apply``, which every window passes through;
nothing is shown in English-only when the files are missing. Plan: ``docs/tool-suite.md`` section 25.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

from ..infra import i18n

_installed: list[QTranslator] = []  # what is installed now, so a change of language removes it


def install() -> None:
    """Make Qt's texts follow ``i18n.current()``; a language without a file there stays as Qt has it."""
    app = QCoreApplication.instance()
    if app is None:
        return
    for translator in _installed:
        app.removeTranslator(translator)
    _installed.clear()
    code = i18n.current()
    if code == i18n.DEFAULT:
        return
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in ("qtbase", "qt"):
        translator = QTranslator(app)
        if translator.load(QLocale(code), name, "_", folder):
            app.installTranslator(translator)
            _installed.append(translator)
