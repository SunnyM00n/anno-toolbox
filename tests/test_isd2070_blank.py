import struct

import numpy as np
import pytest

from annotoolbox.formats import isd2070
from annotoolbox.islands.islandgrid import fixed_pairs


def test_blank_island_is_land_in_the_middle_and_sea_at_the_edge():
    isl = isd2070.blank_island(128, seed=3)
    hm = np.array(isl.heightmap())
    assert hm.shape == (129, 129) and isl.objects() == []
    assert hm[64, 64] == pytest.approx(isd2070.BLANK_LAND) and hm[0, 0] < -30
    layers = isl.texture_layers()
    assert list(layers) == [0, 78, 52]  # water under all, then beach, then grass
    assert np.array(layers[52])[64, 64] == 255 and np.array(layers[52])[0, 0] == 0


def test_blank_island_has_harbour_coast_all_round():
    isl = isd2070.blank_island(160)
    lines = isl.root.path("CoastBuildingLines").children
    assert len(lines) == isd2070.BLANK_COAST_LINES
    hm = np.array(isl.heightmap())
    for line in lines:
        for x, z in fixed_pairs(line.path("Points").children):
            assert 0 <= hm[round(z), round(x)] <= isd2070.BLANK_LAND  # on the beach, at the water's edge


def test_blank_island_sizes():
    with pytest.raises(ValueError):
        isd2070.blank_island(100)  # not whole chunks


def test_logic_heights_are_signed():
    """``m_HeightMap_v2`` holds int16: the sea floor reads as -31744, not 33792."""
    isl = isd2070.blank_island(128)
    grid = isd2070.Node(
        "m_GRIDManager", children=[isd2070.Node("m_HeightMap_v2", data=struct.pack("<3h", -31744, 0, 717))]
    )
    isl.root.children.append(isd2070.Node("m_GOPManager", children=[grid]))
    assert isl.heightmap_v2() == [-31744, 0, 717]


def test_a_chunks_layers_come_in_splat_order():
    """2070 lays a chunk's layers on in ascending SplatID, not in the file's order."""
    isl = isd2070.blank_island(64)
    assert {tuple(i for i, _ in layers) for *_, layers in isl.texture_chunks()} >= {(0, 78, 52)}  # as written
    isl.order = {0: 1, 52: 800, 78: 770}  # water, gras_01, beach_01 (GroundTextures.xml)
    assert {tuple(i for i, _ in layers) for *_, layers in isl.texture_chunks()} >= {(0, 78, 52)}
    isl.order = {0: 1, 52: 770, 78: 800}
    chunks = {tuple(i for i, _ in layers) for *_, layers in isl.texture_chunks()}
    assert (0, 52, 78) in chunks and (0, 78, 52) not in chunks and list(isl.texture_layers()) == [0, 52, 78]
