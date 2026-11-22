"""2205's and 1800's post chains by their own ``posteffect_aa_bloom.fx`` (``render.postforward``): the pass order, the
full-screen triangle, 1800's colour table and lite tone map, and with an installed game (``ANNO2205_DIR``,
``ANNO1800_DIR``) the techniques compile with the game's own compiler."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.render import postforward as pf


def test_each_pass_reads_the_picture_or_a_pass_before_it_and_the_combine_comes_last():
    made = {"source"}
    for name, div, inputs in pf.PASSES:
        assert set(inputs.values()) <= made and div in (1, 2, 4)
        made.add(name)
    assert pf.TECHNIQUES[-1] == "BloomCombineAA" and dict(pf.PASSES[0][2]) == {"cTexture4": "source"}


def test_the_triangle_covers_the_screen_with_direct3ds_coordinates():
    from annotoolbox.render.gamedraw import screen_triangle

    mesh = screen_triangle()
    v = np.frombuffer(mesh.m_data, "<f4").reshape(3, 5)
    clip, uv = v[:, :2], v[:, 3:]
    assert np.allclose(uv, (clip * (0.5, -0.5)) + 0.5)  # the top of the screen reads row 0
    assert mesh.m_stride == 20 and mesh.m_indices.tolist() == [0, 1, 2]


@pytest.mark.skipif(not os.environ.get("ANNO2205_DIR"), reason="needs an installed Anno 2205 (ANNO2205_DIR)")
def test_the_installed_games_post_chain_compiles_with_its_own_compiler():
    from annotoolbox.infra import fxc
    from annotoolbox.models import shaderforward
    from annotoolbox.sources import gametree

    folder = Path(os.environ["ANNO2205_DIR"])
    source = shaderforward.CForwardSource(gametree.open_game("anno2205", folder), fxc.CFxc.find(folder), "anno2205")
    stages = {t: source.effect_pass(pf.FX, (), t) for t in pf.TECHNIQUES}
    _vs, ps = stages["BloomCombineAA"]
    names = {b.name for b in ps.reflection.bindings}
    assert {"cTexture0", "cTexture1", "cTexture2", "cTexture3", "cbPostEffect"} <= names
    assert "cTexture4" in {b.name for b in stages["Bright"][1].reflection.bindings}


def test_1800s_chain_reads_the_picture_as_its_bright_pass_does_and_grades_last():
    chain = pf.CHAINS["anno1800"]
    made = {"source"}
    for name, _div, inputs in chain.passes:
        assert set(inputs.values()) <= made
        made.add(name)
    assert chain.techniques[-1] == "ColorGrade" and dict(chain.passes[0][2]) == {"cTexture2": "source"}
    assert chain.engine["cGammaAdjust"] == 1.0  # GammaAdjust is AbsPow(x, cGammaAdjust): 0 turns the picture white
    assert pf.CHAINS["anno2205"].passes == pf.PASSES and pf.CHAINS["anno2205"].engine == {}


def test_the_chains_share_one_base_that_makes_a_target_per_pass_at_its_divisor_once_per_size():
    """2205's and 1800's chain and 2070's and 1404's sequence are ``fxcdraw.CScreenChain``s (the audit of
    2026-11-13 folded their copies of targets, pass and release into it)."""
    from annotoolbox.render import fxcdraw, post2070

    made = []

    class FakeTex:
        def __init__(self, size):
            self.size, self.released = size, False

        def release(self):
            self.released = True

    ctx = type("Ctx", (), {"rhi": None, "native_texture": lambda self, size, fmt: made.append(FakeTex(size)) or made[-1],
                           "framebuffer": lambda self, textures: FakeTex(None)})()  # fmt: skip
    chain = fxcdraw.CScreenChain(ctx, {}, None)
    chain.ensure_targets((101, 50), {"a": 1, "b": 2, "c": 4})
    assert [t.size for t in made] == [(101, 50), (50, 25), (25, 12)] and chain.target_size("b") == (50, 25)
    chain.ensure_targets((101, 50), {"a": 1, "b": 2, "c": 4})
    assert len(made) == 3  # the same size: kept
    chain.ensure_targets((8, 8), {"a": 1})
    assert all(t.released for t in made[:3]) and chain.target_size("a") == (8, 8)
    assert issubclass(pf.CPostChain, fxcdraw.CScreenChain) and issubclass(post2070.CPostChain2070, fxcdraw.CScreenChain)


def test_every_pass_reads_its_targets_size_as_the_viewport_by_both_games_names():
    """The blur's texel size is the viewport's over the crop factor: 2205 reads ``cViewPort``, 1800 ``cViewport``;
    at 0 1800's taps went to the picture's edges and a glint at the bottom ran up its whole column."""
    chain = object.__new__(pf.CPostChain)
    chain.m_passes, frames, sizes = pf.CHAINS["anno1800"].passes, [], {}
    chain.ensure_targets = lambda size, divisors: sizes.update(
        {n: (size[0] // d, size[1] // d) for n, d in divisors.items()}
    )
    chain.target_size = sizes.__getitem__
    chain.run = lambda name, maps, frame: frames.append((name, frame)) or type("T", (), {"native": name})()
    chain.render(type("T", (), {"native": "source"})(), (640, 360), {"cBloomBias": 1.0})
    for name, frame in frames:
        port = [0, 0, *sizes[name]]
        assert frame["cViewport"] == frame["cViewPort"] == port and frame["cViewportCropFactor"][:2] == [1, 1]
    assert [n for n, _ in frames] == list(pf.CHAINS["anno1800"].techniques)


def test_a_graded_pictures_top_left_strip_is_the_colour_tables_volume():
    n = pf.LUT_SIZE
    g, b, r = np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing="ij")
    strip = np.zeros((n + 3, n * n + 5, 4), np.uint8)  # a picture larger than the strip
    strip[:n, : n * n, 0], strip[:n, : n * n, 1] = (r * 8).reshape(n, n * n), (g * 8).reshape(n, n * n)
    strip[:n, : n * n, 2], strip[..., 3] = (b * 8).reshape(n, n * n), 255
    volume = np.frombuffer(pf.lut_volume(_png_of(strip)), np.uint8).reshape(n, n, n, 4)
    assert tuple(volume[5, 7, 9, :3]) == (9 * 8, 7 * 8, 5 * 8)  # slice by blue, row by green, texel by red
    with pytest.raises(ValueError):
        pf.lut_volume(_png_of(np.zeros((4, 4, 4), np.uint8)))  # too small to hold the strip


def _png_of(rgba: np.ndarray) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    h, w = rgba.shape[:2]
    img = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_1800s_setting_gives_the_lite_tone_map_as_stored_and_2205s_none():
    import xml.etree.ElementTree as ET

    from annotoolbox.render import ambientforward as af

    info = ET.fromstring("<Info><Exposure>0.63</Exposure><ToneMappingEnabled>1</ToneMappingEnabled>"
                         "<ToneMappingMaxLinear>0.8</ToneMappingMaxLinear><ToneMappingWhite>1.3</ToneMappingWhite></Info>")  # fmt: skip
    assert af.lite_values(info) == {"cExposure": 0.63, "cToneMappingEnabled": 1.0, "cToneMappingMaxLinear": 0.8,
                                    "cToneMappingWhite": 1.3}  # fmt: skip
    assert af.lite_values(ET.fromstring("<Info><ToneMappingWhite>1.8</ToneMappingWhite></Info>")) == {}


@pytest.mark.skipif(not os.environ.get("ANNO1800_DIR"), reason="needs an installed Anno 1800 (ANNO1800_DIR)")
def test_the_installed_1800s_post_chain_and_colour_grade_compile_and_its_table_reads():
    from annotoolbox.infra import fxc
    from annotoolbox.models import shaderforward
    from annotoolbox.render import ambientforward as af
    from annotoolbox.sources import gametree

    folder = Path(os.environ["ANNO1800_DIR"])
    tree = gametree.open_game("anno1800", folder)
    stages = pf.compile_chain(
        shaderforward.CForwardSource(tree, fxc.CFxc.find(folder), "anno1800"), pf.CHAINS["anno1800"]
    )
    assert "cLUTTexture0" in {b.name for b in stages["ColorGrade"][1].reflection.bindings}
    assert "cTexture2" in {b.name for b in stages["Bright"][1].reflection.bindings}
    light = af.read_light(tree, af.LOCALS["anno1800"])
    assert light.lut and light.lite["cExposure"] > 0
    assert len(pf.lut_volume(tree.read(light.lut))) == pf.LUT_SIZE**3 * 4
