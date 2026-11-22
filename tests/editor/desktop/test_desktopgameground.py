"""The session maps of the island view's game look: the coarse displacement, the grit map, the set's grit values;
and the loader jobs of its decals' materials."""

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopgamedecals
from annotoolbox.editor.desktop import desktopgameground as gg


class FakeTag:
    def __init__(self, **found) -> None:
        self.found = found

    def find(self, name):
        return self.found.get(name)

    def path(self, name):
        return self.found[name]


def _view(displacement: bytes, grit: bytes, blocks: int):
    width = SimpleNamespace(data=np.uint32(blocks).tobytes())
    rd = FakeTag(**{"Terrain/CoarseDisplacementMap": FakeTag(map=SimpleNamespace(data=displacement)),
                 "Terrain/GritMap2": FakeTag(width=width, map=SimpleNamespace(data=grit))})  # fmt: skip
    return SimpleNamespace(rd3d=rd)


def test_the_coarse_displacement_and_the_grit_map_are_turned_over_into_the_views_axes():
    raw = np.arange(16, dtype=np.uint8).reshape(4, 4)
    grit = bytes(range(16)) + bytes(16)  # two BC5 blocks in a row: 8 x 4 texels
    view = _view(raw.tobytes(), grit, 2)
    assert np.array_equal(gg.coarse_displacement(view), raw[:, ::-1])
    mirrored, side = gg.grit_map(view)
    assert side == 8 and mirrored[:16] == bytes(16)  # the blocks swapped within their row


def test_a_material_sets_grit_values_are_the_shaders_constants_and_its_textures_paths():
    text = ("<GritDiffuseTexture>data\\a\\grit_diff.psd</>\n<GritTiling>4.000000</>\n<GritAlphaSharpness>5</>\n"
            "<GritSlope>0.2</>\n<CliffDiffuseTexture></>\n<CliffTiling>8</>\n")  # fmt: skip
    values, textures = gg.set_grit(text)
    assert values == {"cGritTilingRec": 0.25, "cGritAlphaSharpness": 5.0, "cGritSlope": 0.2, "cCliffTilingRec": 0.125}
    assert textures == {"cGritDiffuseTex": "data/a/grit_diff.psd"}


class FakeNoSet:
    def read(self, path: str) -> bytes:
        raise KeyError(path)


def test_an_island_whose_material_set_does_not_ship_keeps_its_own_maps():
    raw = np.arange(16, dtype=np.uint8).reshape(4, 4)
    view = _view(raw.tobytes(), bytes(32), 2)
    view.files, view.material_set = FakeNoSet(), "data/sets/gone.xml"
    got = gg.read_ground(SimpleNamespace(m_view=view))
    assert np.array_equal(got["displacement"], raw[:, ::-1]) and got["grit"][1] == 8
    assert got["values"] == {} and got["textures"] == {}


def test_a_decal_materials_loader_job_is_the_same_in_a_look_made_again_whatever_the_order():
    """The loader outlives the look: a job named by the order the materials were met in handed a look made again
    (another rendering quality) another material's shaders."""
    stone, moss = (("ShaderID", "9"), ("cDiff", "stone.dds")), (("ShaderID", "9"), ("cDiff", "moss.dds"))
    island = SimpleNamespace(m_renderer=SimpleNamespace())
    first, again = desktopgamedecals.CGameDecals(island), desktopgamedecals.CGameDecals(island)
    first._regroup({stone: {}, moss: {}})
    again._regroup({moss: {}, stone: {}})
    assert first.m_looks[stone].m_job == again.m_looks[stone].m_job != again.m_looks[moss].m_job
    assert first.m_looks[moss].m_job == again.m_looks[moss].m_job and again.m_looks[moss].m_values == dict(moss)
