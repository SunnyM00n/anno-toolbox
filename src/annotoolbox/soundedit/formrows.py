"""What the sound editor's form panels share: a comma list read into its entries and a column of buttons made from
(text, function) rows. ``entries(text)`` and ``add_buttons(layout, rows)``; the panels are ``curvepanel`` and
``musicpanel``, the window ``soundwindow``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtWidgets import QFormLayout, QLayout, QPushButton

from ..infra.i18n import tr


def entries(text: str) -> list[str]:
    """The entries of a comma list, each stripped, the empty ones left out."""
    return [part.strip() for part in text.split(",") if part.strip()]


def add_buttons(layout: QLayout, rows: Iterable[tuple[str, Callable]]) -> list[QPushButton]:
    """One button per (text, function) row, its text an ``N_`` text shown translated, added to a form or box layout."""
    buttons = []
    for text, fn in rows:
        button = QPushButton(tr(text))
        button.clicked.connect(fn)
        layout.addRow(button) if isinstance(layout, QFormLayout) else layout.addWidget(button)
        buttons.append(button)
    return buttons
