"""The map editor's right panel, offscreen: a type the app has no name for is kept when the fields are applied."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from annotoolbox.mapedit.mappanel import CMapPanel
from annotoolbox.mapedit.template import KIND_SLOT, TYPES, SElement


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def test_a_type_without_a_name_is_shown_and_applied_as_it_is(qt):
    applied = []
    panel = CMapPanel(applied.append, lambda _v: None, lambda: None)
    panel.show_element(SElement(KIND_SLOT, 8, 16, type=12))
    assert panel.m_type.currentData() == 12 and panel.m_type.count() == len(TYPES) + 1
    panel._element_values()
    assert applied[-1]["type"] == 12
    panel.show_element(SElement(KIND_SLOT, 8, 16, type=3))  # the odd entry goes again with the next element
    assert panel.m_type.currentData() == 3 and panel.m_type.count() == len(TYPES)
