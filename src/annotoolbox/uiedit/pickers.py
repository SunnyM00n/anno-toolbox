"""Choosing a reference instead of typing a GUID (Qt): a dialog that lists the game's and the project's UI files of
the kind a field takes - image assets for ``Image``, text styles for ``TextStyle``, widget files for a container's
``Template`` - by name, with a filter; and the drag of a palette entry into the preview.

``kind_of_field(name)`` is the kind a reference field takes; ``pick(parent, files, kinds, title)`` asks for one
(``entries`` lists the candidates).
docs/ui-editor.md "Editing".
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData

from ..infra.i18n import tr
from ..suite.widgets import pick_from_list
from . import uifiles

FIELD_KINDS = {"Image": ("IM",), "TextStyle": ("TS",), "Template": ("WI",), "LocalizedFont": ("LF",),
               "ColorPaletteAsset": ("PA",), "EffectTemplate": ("EF",), "ComponentTemplate": ("CP",),
               "WidgetType": ("BE",)}  # fmt: skip
MIME = "application/x-ui-palette"  # a palette entry dragged into the preview: "what|which"
SHOWN = 2000  # the most entries a filter lists


def kind_of_field(name: str) -> tuple[str, ...] | None:
    return FIELD_KINDS.get(name)


def palette_mime(what: str, which: str) -> QMimeData:
    data = QMimeData()
    data.setData(MIME, f"{what}|{which}".encode())
    return data


def read_mime(data: QMimeData) -> tuple[str, str] | None:
    if not data.hasFormat(MIME):
        return None
    what, _, which = bytes(data.data(MIME)).decode().partition("|")
    return what, which


def entries(files: uifiles.CUiFiles, kinds: tuple[str, ...], text: str = "") -> list[tuple[str, str]]:
    """(title, key) of the files of ``kinds`` that match ``text``, by title."""
    words = text.lower().split()
    out = []
    for entry in files.of_kind(*kinds):
        title = files.title(entry.key)
        if all(word in f"{title} {entry.key} {entry.path}".lower() for word in words):
            out.append((title, entry.key))
    return sorted(out, key=lambda row: row[0].lower())[:SHOWN]


def pick(parent, files: uifiles.CUiFiles, kinds: tuple[str, ...], title: str) -> str | None:
    """Ask for a file of ``kinds`` among the game's and the project's, by name; its key, or None when cancelled."""

    def rows(text: str) -> list[tuple[str, object]]:
        return [(name if name == key else f"{name}  ({key})", key) for name, key in entries(files, kinds, text)]

    return pick_from_list(parent, title, "", rows, size=(520, 600), placeholder=tr("ui.filter_name_guid"))
