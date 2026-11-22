"""The streets of a 2070 island as decals (desktopstreets): the tile's place in the view, its cell on the sheet."""

import numpy as np

from annotoolbox.editor.desktop import desktopstreets
from annotoolbox.editor.desktop.desktopdecals import CDecalLayer
from annotoolbox.render import decalmesh
from annotoolbox.sources import streetcells2070

SIZE = 64.0
STREETS = {"tiles": [[3, 5, 10], [4, 5, 10], [5, 5, 11]], "bridges": [], "sheets": {"10": "sheet_a.dds"}}


def test_the_street_pass_lies_between_the_decals_before_and_after_the_streets():
    assert decalmesh.pass_of(decalmesh.STREET_KIND, False) == decalmesh.STREET_CELLS
    assert decalmesh.BEFORE_STREETS < decalmesh.STREET_CELLS
    assert decalmesh.pass_of("Terrain", True) == decalmesh.AFTER_STREETS


def test_a_street_without_a_sheet_is_left_out_and_the_rest_is_one_decal_a_tile():
    placed = desktopstreets.entries(STREETS, SIZE)
    assert len(placed) == 2  # the asphalt tile (11) has no sheet here
    matrix, (decal,) = placed[0]
    assert decal["diffuse"] == "sheet_a.dds" and decal["kind"] == decalmesh.STREET_KIND
    assert (matrix[0, 0, 3], matrix[0, 2, 3]) == (SIZE - 3.5, 5.5)  # the view's x is mirrored: size - x
    assert desktopstreets.entries(None, SIZE) == [] and desktopstreets.entries({}, SIZE) == []


def test_the_decal_layer_lays_a_tile_with_its_cell_of_the_sheet():
    found: dict = {}
    placed = desktopstreets.entries(STREETS, SIZE)[:1]  # the tile (3, 5): the end of a row, opens to +x
    for _ in CDecalLayer._lay(placed, lambda x, z: np.zeros_like(x) + 0.7, 1.0, found):
        pass
    ((look, meshes),) = found.items()
    assert look[0] == decalmesh.STREET_CELLS
    vertices, _ = meshes[0]
    cell, turn = streetcells2070.pick(2, 3, 5)  # one neighbour, at +x: an end opening to +x, turn 0
    assert turn == 0 and cell // streetcells2070.COLS == 0
    column = cell % streetcells2070.COLS
    # the tile's +x edge is its low view x (s = 1): the right edge of the cell on the sheet
    assert np.allclose(vertices[vertices[:, 0] == vertices[:, 0].min(), 6], (column + 1) / streetcells2070.COLS)
    assert np.allclose(vertices[vertices[:, 0] == vertices[:, 0].max(), 6], column / streetcells2070.COLS)
    assert np.allclose(vertices[:, 1], 0.7 + decalmesh.LIFT)
