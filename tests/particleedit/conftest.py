"""The particle editor's fixtures: the trees of the 2070 and 1701 games it opens effects from."""

from __future__ import annotations

import pytest
from tests.particleedit.particleeditfakes import BRACE_TEXT, CFG2070, CFG2070_TEXT, EFC, EFC1701, EFC_TEXT


@pytest.fixture
def tree2070(game):
    return game("anno2070", {EFC: EFC_TEXT, CFG2070: CFG2070_TEXT})


@pytest.fixture
def tree1701(game):
    return game("anno1701", {EFC1701: BRACE_TEXT})
