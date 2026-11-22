"""The forward games' frame for the viewer without a GPU: the sun's shadow camera fitted to the models (the engine's
shadow method 0), the local lights' buffer rows and cell textures, the render states of an effect's blocks, the
switches an effect tests, which materials go into the blended pass, and the light the view's own ground and sky
take under 2205's post chain."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.models import shaderforward
from annotoolbox.render import lightsforward


def test_the_shadow_camera_holds_every_corner_with_depth_0_to_1_along_the_light():
    pytest.importorskip("PySide6")
    from annotoolbox.render import shadowforward

    pts = np.array([[x, y, z] for x in (-1, 2) for y in (0, 3) for z in (-2, 1)], float)
    down = np.array([0.3, -1.0, 0.2])
    found = shadowforward.fit(down, pts)
    clip = np.c_[pts, np.ones(len(pts))] @ found.view_proj
    assert np.all(np.abs(clip[:, :2]) <= 1.0)  # inside the map
    depth = np.c_[pts, np.ones(len(pts))] @ np.asarray(found.values["cVecShadowVPDN"])
    assert depth.min() > 0 and depth.max() < 1  # nearer the sun, smaller
    top, bottom = np.array([0.5, 3, -0.5, 1]), np.array([0.5, 0, -0.5, 1])
    assert top @ found.values["cVecShadowVPDN"] < bottom @ found.values["cVecShadowVPDN"]
    uv = (np.c_[pts, np.ones(len(pts))] @ found.values["cMtxShadowVPT0"])[:, :2]
    assert np.all((uv >= 0) & (uv <= 1))  # the colour pass reads the map in texture coordinates
    assert found.values["cShadowMethod"] == 0 and found.values["cShadowBias"][1] > 0


def test_1800s_cascades_split_the_casters_view_depth_and_hold_their_slices_in_left_handed_view_space():
    pytest.importorskip("PySide6")
    from annotoolbox.render import gameshader as gs
    from annotoolbox.render import gameshadow, shadowforward

    pts = np.array([[x, y, z] for x in (-4, 4) for y in (0, 3) for z in (-30, 2)], float)
    cam = gs.direct_camera((800, 600), (0, 5, 10), (0, 0, 0), 0.8, 0.5, 200)
    v = shadowforward.cascades(cam, [0.3, -1.0, 0.2], pts)
    splits = [v[f"cShadowInformation[{i}].SplitDistance"] for i in range(4)]
    assert v["cUsedShadowPartitions"] == 4 and all(a < b for a, b in zip(splits, splits[1:], strict=False))
    assert shadowforward.cascades(cam, [0.3, -1.0, 0.2], pts, 2048)["cShadowMapSize"] == [2048, 2048]  # the quality's
    fewer = shadowforward.cascades(
        cam, [0.3, -1.0, 0.2], pts, 2048, 2
    )  # Low reads two: the splits stay where they were
    assert fewer["cUsedShadowPartitions"] == 2 and fewer["cShadowInformation[3].SplitDistance"] == splits[-1]
    # the filter's taps in texels of the map: the read adds them x 1.5 to the map's coordinate
    taps = np.asarray(fewer["cShadowSampleOffsets"])[:, :2]
    assert taps == pytest.approx(np.asarray(gameshadow.SAMPLES) / 2048) and np.abs(taps).max() <= 1.0 / 2048
    view = gs.left_handed_values(cam)["cMtxView"]
    ahead = np.c_[pts, np.ones(len(pts))] @ view
    assert ahead[:, 2].min() > 0 and splits[-1] == pytest.approx(ahead[:, 2].max())  # +z ahead, the last ends there
    m = v["cShadowInformation[0].ShadowMatrix"]
    near = pts[np.argmin(ahead[:, 2])]
    assert np.all(np.abs((np.r_[near, 1.0] @ m)[:2]) <= 1.0)  # the nearest corner in the first cascade
    lo, hi = v["cShadowInformation[0].DepthRange"]
    depth = pts @ np.asarray(v["cShadowInformation[0].DepthTransform"])[:3]
    assert lo <= depth.min() and depth.max() <= hi
    assert gs.left_handed_values(cam)["cMtxView"] @ gs.left_handed_values(cam)["cMtxProj"] == pytest.approx(
        cam.view @ cam.projection
    )


def test_the_local_lights_fill_two_rows_a_point_and_three_a_spot_and_every_cell_holds_them_all():
    point = lightsforward.SLamp((1, 2, 3), 5.0, (2, 2, 2), 1.5)
    spot = lightsforward.SLamp((0, 4, 0), 8.0, (1, 0, 0), 2.0, (0, -1, 0), 0.9, 0.7)
    p, s = lightsforward.rows([point], [spot])
    assert p == [[1, 2, 3, 5.0], [2, 2, 2, 1.5]]
    assert s[0] == [0, 4, 0, 2.0] and s[1] == [1, 0, 0, 0.7]
    assert s[2][:3] == [0.0, 8.0, 0.0] and abs(s[2][3] - 1 / 0.2) < 1e-6  # back to the light, the cone's 1 / width
    grid, index = lightsforward.cells(1, 1)
    assert grid.shape == (16, 20, 30, 2) and int(grid[3, 5, 7, 1]) == 1 | (1 << 16) and int(grid[0, 0, 0, 0]) == 0
    assert index.shape == (1, 512) and index[0, :2].tolist() == [0, 0]  # point 0, then spot 0
    buffer = lightsforward.buffer_cells(3, 2)  # 1800: points in bits 0-10, spots 11-20, as its shader unpacks them
    assert len(buffer) == lightsforward.BUFFER_CELLS and buffer[100] == [0, 3 | (2 << 11)]


def test_a_block_gives_the_pipeline_states_and_the_blended_pass_is_picked_by_effect_and_opacity():
    alpha = {"ZFunc": "LESS_EQUAL", "ZWriteEnable": "TRUE", "AlphaBlendEnable": "TRUE", "SrcBlend": "ONE",
             "DstBlend": "INV_SRC_ALPHA", "SrcBlendAlpha": "INV_DST_ALPHA", "DstBlendAlpha": "ONE"}  # fmt: skip
    s = shaderforward.block_state(alpha)
    assert s.blend and (s.source, s.dest, s.source_alpha, s.dest_alpha) == (
        "one",
        "inv_src_alpha",
        "inv_dst_alpha",
        "one",
    )
    plain = shaderforward.block_state({"CullMode": "CW"})
    assert not plain.blend and plain.cull == "front" and plain.depth_func == "less_equal" and plain.depth_write
    assert shaderforward.blended({}, "model_glass") and shaderforward.blended({"cOpacity": "0.5"}, "model_pbr")
    assert not shaderforward.blended({"cOpacity": "1.000000"}, "model_pbr")


def test_the_switches_an_effect_tests_with_the_preprocessor_are_its_material_options():
    text = "#ifdef NORMAL_ENABLED\n#endif\n#if defined(MASK_ENABLED) && !defined(FOO)\n#elif defined(GLOW_ENABLED)\n"
    assert shaderforward.preprocessor_switches(text) == ("GLOW_ENABLED", "MASK_ENABLED", "NORMAL_ENABLED")
    got = shaderforward.material_defines({"MASK_ENABLED": "1"}, "model_glass", names=("MASK_ENABLED",))
    assert got[1:] == [("MASK_ENABLED", "")]


def test_a_cloth_option_written_as_zero_with_decimals_stays_off():
    """The configs write a switched-off option as ``0.000000``: the cloth's variant must not take it as on."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import clothforward

    got = clothforward.defines({"NORMAL_ENABLED": "0.000000", "DYE_MASK_ENABLED": "1.000000", "LOGO_ATLAS_ENABLED": ""})
    assert got == (("NUM_BONES_PER_VERTEX", "0"), ("DYE_MASK_ENABLED", ""))


def test_the_views_ground_and_sky_take_the_settings_light_in_the_games_formula_while_the_post_chain_runs():
    pytest.importorskip("PySide6")
    from annotoolbox.render import ambientforward as af
    from annotoolbox.viewer import forwardpass as fp

    p = fp.CForwardPass.__new__(fp.CForwardPass)
    source = type("S", (), {"m_light": af.SLight(ambient=(0.2, 0.2, 0.2), diffuse=(4.0, 3.0, 2.0))})()
    p.why_not, p._ready, p.m_switches = (lambda: ""), (lambda: source), {}
    ours = {"sunDir": (0.0, 1.0, 0.0), "sunColor": (9.0, 9.0, 9.0), "ambientMode": 1, "finish": 1}
    lt = p.view_light(ours, False)
    assert lt["sunColor"] == pytest.approx((3.8, 2.8, 1.8)) and lt["flatAmbient"] == (0.2, 0.2, 0.2)  # sun - ambient
    assert lt["ambientMode"] == 2 and lt["sky"][0] == 2 and lt["sunDir"] == ours["sunDir"]
    p.m_switches = {"post": False}
    assert p.view_light(ours, False) is ours  # the chain off: the view's own look


def test_without_the_post_chain_1800s_quarter_stored_colour_is_multiplied_back_and_2205s_kept():
    pytest.importorskip("PySide6")
    from annotoolbox.viewer import forwardpass as fp

    for cls, off in ((fp.CForwardPass1800, 4.0), (fp.CForwardPass, 1.0)):
        p = cls.__new__(cls)
        p.m_switches = {}
        assert p._unstored() == 1.0  # the chain multiplies it back itself
        p.m_switches = {"post": False}
        assert p._unstored() == off


def test_2205s_sea_lies_round_the_model_as_the_open_sea_and_only_with_ocean_waves_on():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace as N

    from annotoolbox.render import nativelook, ocean2070
    from annotoolbox.viewer import forwardpass as fp

    assert (
        nativelook.GAMES["anno2205"].features["waves"] == nativelook.YES and "waves" in nativelook.GAMES["anno2205"].off
    )
    seen = {}

    class FakeSea:
        size = 0.0

        def release(self):
            seen["released"] = True

        def run(self, ctx, seconds):
            seen["seconds"] = seconds

        def frame_values(self):  # what the sea puts into the frame's constants (2205's: nothing)
            return {}

        def models(self, eye, sun):
            seen["eye"] = np.asarray(eye)
            return [
                N(
                    m_rows=np.array([[0.0, 0.0, 16.0, 1e9]], np.float32),
                    m_world=np.identity(4),
                    m_draw_call=N(m_fallbacks={}),
                )
            ]

    p = fp.CForwardPass.__new__(fp.CForwardPass)
    p.m_switches, p.m_sea, p.m_sea_broken, p.m_renderer = {}, FakeSea(), False, N(m_ctx=None)
    ground = np.diag([20.0, 1.0, 20.0, 1.0])
    ground[:3, 3] = (-5.0, 0.0, 7.0)  # a part place in Anno's axes: the view's x mirrored
    cam = N(eye=(5.0, 30.0, 7.0))
    assert p._sea(ground, cam, {}, 2.0) == [] and p.m_ground is False  # Ocean waves starts off
    assert p.m_sea is None and seen["released"]  # and the sea goes with it
    p.m_sea, p.m_switches = FakeSea(), {"waves": True}
    draws = p._sea(ground, cam, {"cLgtDirection": (0, -1, 0)}, 2.0)
    half = ocean2070.MIN_REACH  # the ground's reach is smaller than the sea's least
    assert p.m_ground is True and seen["seconds"] == 2.0
    assert tuple(draws[0].m_rows[0, :2]) == (5.0 - half, 7.0 - half)  # the square's corner moved under the model
    assert np.allclose(seen["eye"], (half, 30.0, half))  # the eye in the square's own frame
    assert draws[0].m_draw_call.m_fallbacks["cWaterInfoMap"] == fp.OPEN_SEA
    assert p._sea(None, cam, {}, 2.0) == [] and p.m_ground is False  # no ground, no sea


def test_the_frame_writes_both_games_screen_values():
    """1800's hull scales its factors by ``cBackBufferSize.y`` and its shadow fades by ``cViewport``; 2205 reads
    ``cViewPort`` (island-1800-2205-view.md 6)."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import frameforward
    from annotoolbox.render import gameshader as gs

    class FakeBuffer(dict):
        def set(self, name, value):
            self[name] = value

        def set_rows(self, name, value):
            self[name] = value

    buffer = FakeBuffer()
    frameforward.fill_frame({"cb": buffer}, gs.direct_camera((640, 360), (0, 5, 10), (0, 0, 0), 0.8, 0.5, 200), {})
    assert buffer["cBackBufferSize"] == [640, 360] and buffer["cBackBufferSizeRec"] == [1 / 640, 1 / 360]
    assert buffer["cViewport"] == buffer["cViewPort"] == [0, 0, 640, 360]


def test_view_rendering_switches_take_the_haze_and_the_cloud_shadows_out_by_the_shaders_own_terms():
    pytest.importorskip("PySide6")
    import math

    from annotoolbox.render import ambientforward as af
    from annotoolbox.viewer import forwardpass as fp

    setting = af.SLight(scatter_intensity=0.7, clouds=(1.0, 0.5, 0.3, 0.8), invert_clouds=True)
    on = fp.frame_values(setting, (0.0, 1.0, 0.0), False, 2.0, 0.1, 500.0)
    assert on["cScatteringIntensity"] == 0.7 and on["cInvertCloudShadows"] == 0.0
    off = fp.frame_values(setting, (0.0, 1.0, 0.0), False, 2.0, 0.1, 500.0, lambda part: False)
    assert off["cScatteringIntensity"] == 0.0  # saturate(depth x 0): no haze
    # CalculateCloudShadow with the stand-in noise 0: saturate(|1 - saturate(lerp(1, 0, 2.75 z))| ^ ((2 + 1) w))
    z, w = off["cCloudShadows"][2], off["cCloudShadows"][3]
    lit = min(1.0, abs(off["cInvertCloudShadows"] - min(1.0, max(0.0, 1.0 - 2.75 * z))) ** (3 * w))
    # the stand-in a missing or switched-off cloud map reads (white would be a cloud over every point)
    from annotoolbox.render.frameforward import FALLBACKS

    assert lit == 1.0 and not math.isnan(lit) and FALLBACKS["cCloudTex"] == (0, 0, 0, 0)


def test_the_skys_light_switched_off_keeps_1800s_brdf_table_and_stands_in_a_grey_sky_for_both_games():
    """The audit of 2026-11-13: with the sky's light off 1800 lost its BRDF table (the white stand-in made every
    surface's environment term F0 + 1) and 2205's sky cubes read the white stand-in (a fully lit reflection)."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import ambientforward as af
    from annotoolbox.render.frameforward import FALLBACKS
    from annotoolbox.viewer import forwardpass as fp

    p = fp.CForwardPass1800.__new__(fp.CForwardPass1800)
    p.m_switches = {"sky": False}
    p._map = lambda path, missing=False: f"map:{path}"
    size = type("Size", (), {"width": lambda self: 256})()
    p._cube = lambda path: type("Cube", (), {"pixelSize": lambda self: size})()
    light = af.SLight(skybox="sky.dds", specular_cubes=("a.dds", "b.dds"))
    maps, _values = p._frame_maps(light, p._on)
    assert maps == {"cPrefilteredBRDF": f"map:{af.BRDF_1800}"}  # the cubes are off, the material's table is not
    p.m_switches = {}
    assert {"cPrefilteredSpecularCube0", "cPrefilteredBRDF"} <= set(p._frame_maps(light, p._on)[0])
    grey = FALLBACKS["cPrefilteredSpecularCube0"]
    assert FALLBACKS["cSkyRenderTarget"] == FALLBACKS["cEnvironmentCubeTex"] == grey != (255, 255, 255, 255)


def test_a_switch_the_games_record_does_not_offer_never_takes_a_part_out():
    from annotoolbox.render import nativelook as nl

    assert not nl.part_on("anno2205", {"water": False}, "water")  # offered and off
    assert nl.part_on("anno2205", {"eye": False}, "eye")  # a stale setting of a part 2205 does not offer
    assert nl.part_on("anno1404", {}, "shadows") and nl.part_on("nogame", {"sky": False}, "sky")


def test_2205s_water_refracts_copies_of_the_opaque_scene_and_a_frame_without_water_keeps_one_pass(monkeypatch):
    """``model_water.fx`` reads ``cColorBufferCopy`` / ``cDepthBufferCopy`` and projects by ``cMtxViewProjTex``: with a
    sheet the opaque models are drawn, copied, then the blended layers over the copies (shader-compile.md 11)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace as N

    from annotoolbox.render import frameforward as ff
    from annotoolbox.render import gameshader as gs

    passes, taken = [], []
    frame = ff.CForwardFrame.__new__(ff.CForwardFrame)
    frame.m_ctx, frame.m_copies, frame.m_color, frame.m_depth = (
        N(m_flip_offscreen=False, ONE=1, ONE_MINUS_SRC_ALPHA=2),
        None,
        "colour",
        "depth",
    )
    frame._targets, frame.m_shared = (lambda size: None), N(begin=lambda: None)
    frame.m_shared_prepass, frame.m_scale = frame.m_shared, 1.0
    frame._shadow = lambda models, light, camera, on: ({}, {})
    frame.m_composite = N(draw=lambda *a, **k: passes.append("composite"))
    frame._draw = lambda order, size, camera, values, maps, clear: passes.append(
        ([m.name for m in order], set(maps), clear)
    )
    monkeypatch.setattr(ff, "CSceneCopies", lambda ctx: N(take=lambda c, d, s: taken.append((c, d)) or ("C", "D")))
    place = np.identity(4)
    cam = gs.direct_camera((64, 32), (3.0, 4.0, 5.0), (0.0, 0.0, 0.0), 0.8, 0.1, 100.0)
    wall = N(name="wall", m_blended=False, m_water=False, m_world=place)
    sheet = N(name="sheet", m_blended=True, m_water=True, m_world=place)
    glass = N(name="glass", m_blended=True, m_water=False, m_world=place)
    frame.render(None, (8, 8), [sheet, wall, glass], cam, {}, {"cSkyBox": 1})
    assert passes[:2] == [(["wall"], {"cSkyBox"}, True), (["sheet", "glass"], {"cSkyBox", *ff.WATER_COPIES}, False)]
    assert taken == [("colour", "depth")]
    passes.clear()
    frame.render(None, (8, 8), [wall, glass], cam, {}, {})
    assert passes[0] == (["wall", "glass"], set(), True) and len(passes) == 2  # one pass and the composite
    p = np.array([0.0, 0.0, 0.0, 1.0]) @ (cam.view @ cam.projection)
    tex = np.array([0.0, 0.0, 0.0, 1.0]) @ (cam.view @ cam.projection @ ff.TEXTURE)
    assert tex[:2] / tex[3] == pytest.approx((p[0] / p[3] * 0.5 + 0.5, -p[1] / p[3] * 0.5 + 0.5))  # texture space


def test_the_models_share_the_passs_frame_buffers_written_by_the_first_draw_of_a_frame(monkeypatch):
    """WP6's request (island-1800-2205-view.md 6): each draw wrote the frame's values into its own copy of every
    buffer; the frame's buffers (``FRAME_BUFFERS``) are now one per pass and layout, the model's stay its own."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace as N

    from annotoolbox.render import fxcdraw

    made = []
    monkeypatch.setattr(fxcdraw, "uniform_buffer", lambda rhi, size: made.append(size) or f"buffer{len(made)}")

    def reflection(name, size):
        return N(name=name, size=size, variables=[N(name="cX", offset=0)], members=[])

    frame, model = reflection("cbPerFrame", 64), reflection("cbModel", 32)
    shared = fxcdraw.CSharedBuffers(N(m_rhi=None))
    draws = []
    for _ in range(3):
        d = fxcdraw.CFxcDraw.__new__(fxcdraw.CFxcDraw)
        d.m_rhi, d.m_keep, d.m_constants, d.m_buffers = None, [], {}, {}
        d.m_program, d.m_shared = N(m_cbuffers={"cbPerFrame": frame, "cbModel": model}), shared
        draws.append(d)
    shared.begin()
    due = [set(d._shared_due()) for d in draws]
    assert due == [{"cbPerFrame"}, set(), set()]  # the frame's written once, by the first draw
    assert len({d.m_buffers["cbPerFrame"] for d in draws}) == 1 and len({d.m_buffers["cbModel"] for d in draws}) == 3
    shared.begin()
    assert set(draws[1]._shared_due()) == {"cbPerFrame"}  # a new frame: the first draw to bind it writes it
    alone = fxcdraw.CFxcDraw.__new__(fxcdraw.CFxcDraw)
    alone.m_rhi, alone.m_keep, alone.m_constants, alone.m_buffers, alone.m_shared = None, [], {}, {}, None
    alone.m_program = N(m_cbuffers={"cbPerFrame": frame})
    assert alone._shared_due() == {} and "cbPerFrame" in alone.m_constants  # without a holder: its own, as before


def test_1701s_haze_switch_pushes_its_depth_fog_beyond_any_distance():
    pytest.importorskip("PySide6")
    from annotoolbox.render.frame1701 import SLight1701
    from annotoolbox.viewer import fxcpass1701

    p = fxcpass1701.CFxcPass1701.__new__(fxcpass1701.CFxcPass1701)
    p.m_switches = {}
    light = SLight1701((0.0, 1.0, 0.0))
    assert p._light_now(light, (1.0, 0.0, 0.0)).fog_end == light.fog_end
    p.m_switches = {"atmosphere": False}
    off = p._light_now(light, (1.0, 0.0, 0.0))
    assert off.direction == (1.0, 0.0, 0.0) and off.fog_start >= fxcpass1701.NO_FOG


@pytest.mark.parametrize("game, var", [("anno2205", "ANNO2205_DIR"), ("anno1800", "ANNO1800_DIR")])
def test_the_installed_games_sky_compiles_with_the_boxes_its_setting_names(game, var):
    """shader-compile.md 11 "The visible sky": ``SkyBoxAtmosphere`` compiles from the install and reads the boxes
    ``skyforward.box_maps`` gives it, which the temperate setting names."""
    import os
    import sys
    from pathlib import Path

    root = os.environ.get(var)
    if not root or sys.platform != "win32":
        pytest.skip(f"needs an installed {game} ({var}) on Windows")
    pytest.importorskip("PySide6")
    from annotoolbox.infra import fxc
    from annotoolbox.render import ambientforward, skyforward
    from annotoolbox.sources import gametree
    from annotoolbox.viewer import forwardpass

    tree = gametree.open_game(game, Path(root))
    try:
        compiler = fxc.CFxc.find(getattr(tree, "m_folder", None))
    except fxc.CFxcMissing as e:
        pytest.skip(str(e))
    source = type("Source", (), {"m_shaders": shaderforward.CForwardSource(tree, compiler, game)})()
    (vs, ps), state = forwardpass.sky_work(source)
    assert state.depth_func == "less" and not state.depth_write
    light = ambientforward.read_light(tree, ambientforward.LOCALS[game])
    boxes = skyforward.box_maps(light, game)
    textures = {b.name for b in ps.reflection.bindings if b.name.startswith("c") and "Sampler" not in b.name}
    assert set(boxes) <= textures and all(tree.exists(p) for p in boxes.values())
