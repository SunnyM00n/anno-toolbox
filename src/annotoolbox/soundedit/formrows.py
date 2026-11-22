"""What the sound editor's form panels share: a comma list read into its entries and a column of buttons made from
(text, function) rows, a number as the shortest text that reads back as itself. ``entries(text)``,
``number_text(value, single)`` and ``add_buttons(layout, rows)``; the panels are ``curvepanel`` and
``musicpanel``, the window ``soundwindow``.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable

from PySide6.QtWidgets import QFormLayout, QLayout, QPushButton

from ..infra.i18n import tr


def entries(text: str) -> list[str]:
    """The entries of a comma list, each stripped, the empty ones left out."""
    return [part.strip() for part in text.split(",") if part.strip()]


def number_text(value: float, single: bool = False) -> str:
    """The shortest text that reads back as ``value`` (as a 32-bit float when ``single``): ``%g`` alone keeps six
    digits, so applying a form unchanged would round what the bank holds."""
    if abs(value) < 1e15 and value == int(value):  # (false for nan; inf is past the limit)
        return str(int(value))  # a whole number in plain digits, not ``1e+04``
    for digits in range(1, 18):
        text = f"{value:.{digits}g}"
        try:
            back = struct.unpack("<f", struct.pack("<f", float(text)))[0] if single else float(text)
        except OverflowError:
            continue
        if back == value:
            return text
    return repr(value)


def add_buttons(layout: QLayout, rows: Iterable[tuple[str, Callable]]) -> list[QPushButton]:
    """One button per (text, function) row, its text an ``N_`` text shown translated, added to a form or box layout."""
    buttons = []
    for text, callback in rows:
        button = QPushButton(tr(text))
        button.clicked.connect(callback)
        layout.addRow(button) if isinstance(layout, QFormLayout) else layout.addWidget(button)
        buttons.append(button)
    return buttons
