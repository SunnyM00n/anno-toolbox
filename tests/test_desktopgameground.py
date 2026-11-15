"""The session maps of the island view's game look: the coarse displacement, the grit map, the set's grit values."""

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopgameground as gg


class _Tag:
    def __init__(self, **found) -> None:
        self.found = found

    def find(self, name):
        return self.found.get(name)

    def path(self, name):
        return self.found[name]


def _view(displacement: bytes, grit: bytes, blocks: int):
    width = SimpleNamespace(data=np.uint32(blocks).tobytes())
    rd = _Tag(**{"Terrain/CoarseDisplacementMap": _Tag(map=SimpleNamespace(data=displacement)),
                 "Terrain/GritMap2": _Tag(width=width, map=SimpleNamespace(data=grit))})  # fmt: skip
    return SimpleNamespace(rd=rd)


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
