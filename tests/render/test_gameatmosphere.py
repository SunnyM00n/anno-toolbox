"""117's visible sky and haze by the game's environment shaders (``render.gameatmosphere``): the passes and the
viewer's values; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the sky above the horizon is the
game's (a blue that deepens upwards), not the view's own."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameatmosphere as ga


def test_the_passes_and_the_values_of_ours():
    assert [ga.STAGES[k][2] for k in ("quad", "down", "clouds", "end", "fog", "blur.h", "blur.v", "up")] == [
        "vsCommonFullScreenQuad", "psDownsampleDepth", "csVolumetricClouds", "csComputeAtmosphereEnd",
        "csVolumetricFog", "csBlurScattering", "csBlurScattering#1", "psUpsampleScatteringVolumes"]  # fmt: skip
    assert ga.DOWNSAMPLE == 2 and ga.GROUP == 16
    # the fade near the camera: the engine's seven values in the shader's order; off, the full physical haze
    assert list(ga.FADE.values()) == [0.35, 0.1, -0.4, 0.2, 0.05, 100.0, 40.0]
    assert ga.NO_FADE["cScatterFadeMinScatter"] == 1.0 and ga.ENVIRONMENT["cFogDensity"] == 0.0
    assert ga.CLOUD_LAYER["cCloudDomeRadius"] > 370  # a dome wider than the layer is high (CLOUD_LAYER_UPPERBOUND)


def test_an_islands_fog_map_is_two_layers_turned_into_the_views_axes_and_sets_the_fog_values(monkeypatch):
    """``rd3d`` ``Fog``: per texel the ground and sea-level density and height bytes, then the colour and type; the
    shader's layer 0 and 1, x turned over as every island map the view reads (island-117-view.md "Phase 7")."""
    import numpy as np

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gameshader as gs

    raw = np.zeros((4, 4, 8), np.uint8)
    raw[1, 0] = (121, 36, 0, 0, 96, 100, 105, 255)  # row 1 (z), column 0 (x): ground fog, grey, morning only
    layers = ga.fog_layers(raw.tobytes())
    assert layers.shape == (2, 4, 4, 4)
    assert tuple(layers[0, 1, 3]) == (121, 36, 0, 0) and tuple(layers[1, 1, 3]) == (96, 100, 105, 255)
    atmosphere = ga.CAtmosphere.__new__(ga.CAtmosphere)
    atmosphere.m_atmosphere, atmosphere.m_fog, atmosphere.m_fog_size, atmosphere.m_heights = {}, None, 64.0, None
    names = [("cFogDensity", 0), ("cFogHeight", 4), ("cTerrainSize", 16)]
    c = gs.CConstants(SConstantBuffer("cbEnvironment", 32, [SVariable(n, o, 4 if o < 16 else 16, "float", 1, 1)
                                                          for n, o in names]))  # fmt: skip
    camera = gs.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    sky = type("Sky", (), {"sun": (0.0, 1.0, 0.0), "night": False})()
    monkeypatch.setattr(gs, "fill_frame", lambda c, camera: None)
    monkeypatch.setattr(gs, "fill_sun", lambda c, sun, night: None)
    atmosphere.fill(c, camera, sky, ga.SLook())
    assert np.frombuffer(bytes(c.m_data[:4]), "<f4")[0] == 0.0  # no fog map: no fog
    atmosphere.m_fog = object()
    atmosphere.fill(c, camera, sky, ga.SLook())
    density, height = np.frombuffer(bytes(c.m_data[:8]), "<f4")
    assert density == np.float32(ga.FOG["cFogDensity"]) and height == ga.FOG["cFogHeight"]
    assert np.allclose(np.frombuffer(bytes(c.m_data[16:20]), "<f4"), 64.0)


INSTALL_SKY = """
import json, math, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import CRhiModelView
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/base/graphics/library/props/storage/barrels/barrel_01.cfg", "anno117")
out = {}
for key, switches in (("ours", {"atmosphere": False}), ("game", {})):
    view = CRhiModelView("d3d12")
    view.m_state = SViewState(look="shaders", particles=False, switches=switches)
    view.show_scene(scene, CModelFiles(tree))
    start = time.monotonic()
    while time.monotonic() - start < 600:
        img = view.picture(160, 120)
        r = view.m_renderer
        if r is not None and r.m_box is not None and not r.busy() and len(r.m_items) == len(scene.m_parts) and time.monotonic() - start > 2:
            view.frame_model()
            view.m_camera.m_pitch_rad = math.radians(5)  # the sky in the upper half
            for _ in range(3):
                img = view.picture(160, 120)
            break
        time.sleep(0.03)
    out[key] = [img.pixelColor(4, y).getRgb()[:3] for y in (2, 36)]  # the left edge: sky, beside the barrel
    out.setdefault("errors", []).extend(list(view.m_ctx.m_errors))
    view.release()
print(json.dumps(out))
"""


def test_the_sky_above_the_horizon_is_the_games():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_SKY.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    top, lower = got["game"]
    assert top != got["ours"][0]  # the game's sky, not the view's
    assert top[2] > top[0] + 30  # blue
    assert lower[0] + lower[1] + lower[2] > top[0] + top[1] + top[2]  # paler towards the horizon


def test_a_fog_map_cut_short_reads_its_whole_rows_and_one_without_a_texel_is_no_fog():
    assert ga.fog_layers(bytes(15 * 8)).shape == (2, 3, 3, 4)  # 15 texels: 3 x 3 and a rest (rounded: 4 x 4)
    air = ga.CAtmosphere.__new__(ga.CAtmosphere)
    air.m_fog = None
    air.set_fog(bytes(5), 64.0)
    assert air._m_fog_layers is None and air.m_fog is None


def _camera(x: float = 0.0):
    import numpy as np

    from annotoolbox.render import gameshader as gs

    view = np.identity(4)
    view[3, 0] = x
    return gs.SCamera(np.zeros(3), view, np.identity(4), 0.1, 100.0, (8, 8))


def test_the_accumulation_settles_in_four_still_frames_and_starts_again_when_the_camera_moves_or_the_size():
    """``CAccumulation`` (the native passes' history and ``CAtmosphere``'s): each still frame marches the next pixel
    of every quad, so four frames after the last move the picture is whole and the view stops drawing."""
    acc = ga.CAccumulation()
    left, previous = [], []
    for camera, half in ((_camera(), (4, 4)), (_camera(5), (4, 4)), *[(_camera(5), (4, 4))] * 5, (_camera(5), (6, 4))):
        previous.append(acc.advance(camera, half)[0][3, 0])
        left.append(acc.m_settle_left)
    assert left == [4, 4, 3, 2, 1, 0, 0, 4]  # a moved camera and a new size start the count again
    assert previous[:3] == [0.0, 0.0, 5.0]  # each frame reprojects from the last one's view
    assert acc.m_frame_counter == 8
    assert acc.settling() is True and acc.settling() is False  # asked once per accumulated frame
    acc.advance(_camera(5), (6, 4))
    acc.forget()
    assert acc.settling() is False  # a frame that marched every pixel has nothing to settle


def test_a_frame_of_the_clouds_and_fog_over_frames_picks_marches_reprojects_copies_and_ends_in_that_order():
    named = {"cScatteringTarget": "target", "cDepthBuffer": "depth"}
    steps = ga.quad_march((20, 12), named, "packed", "rays", "history", "cameras")
    assert [key for key, _bound, _grid in steps] == ["select", "clouds", "fog", "accumulate", ga.HISTORY_COPY, "end"]
    by_key = {key: (bound, grid) for key, bound, grid in steps}
    assert by_key["select"] == ({"cScatteringTarget": "packed", "cDepthBuffer": "depth", "cQuadRayIDs": "rays"},
                                (2, 1, 1))  # 10 x 6 quads in groups of 8  # fmt: skip
    assert by_key["clouds"][1] == (1, 1, 1) and by_key["clouds"][0]["cScatteringTarget"] == "packed"
    accumulated = by_key["accumulate"][0]
    assert accumulated["cScatteringAccumulation"] == "target" and accumulated["cScatteringHistory"] == "history"
    assert accumulated["cCameraBuffer"] == "cameras" and by_key["end"][0] is named
    assert "cCameraBuffer" not in ga.quad_march((4, 4), named, "p", "r", "h")[3][1]  # the passes' own camera


class FakeTexture:
    def __init__(self, name: str, size=(8, 4)) -> None:
        self.native, self.size = name, size

    def release(self) -> None:
        pass


def _atmosphere(monkeypatch, temporal_stages: bool):
    """A ``CAtmosphere`` whose passes are logged (key, its fill's key, whether its pass ends with a copy) instead of
    recorded, its fill counting the frames it worked out."""
    from types import SimpleNamespace

    log, worked = [], []
    stages = ("clouds", "end", "fog", "blur.h", "blur.v", *(("select", "accumulate") if temporal_stages else ()))
    air = ga.CAtmosphere.__new__(ga.CAtmosphere)
    air.m_rhi, air.m_atmosphere = None, {}
    air.m_stages = {key: SimpleNamespace(key=key, m_error="", m_share=None) for key in stages}
    air.m_down, air.m_up = SimpleNamespace(key="down"), SimpleNamespace(key="up")
    air.m_target, air.m_blurred, air.m_half = FakeTexture("target"), FakeTexture("blurred"), FakeTexture("half")
    air.m_history = air.m_rays = air.m_half_fbo = None
    air.m_fog, air.m_fog_size, air.m_heights, air._m_fog_layers = None, 0.0, None, None
    air.m_accumulation, air._m_history_blank = ga.CAccumulation(), False
    air._m_filled = __import__("weakref").WeakKeyDictionary()
    monkeypatch.setattr(air, "targets", lambda ctx, size: None, raising=False)
    monkeypatch.setattr(air, "fill", lambda constants, *args: worked.append(constants), raising=False)

    def history(ctx, half) -> None:
        air.m_history, air.m_rays = FakeTexture("history"), FakeTexture("rays")

    monkeypatch.setattr(air, "_history", history, raising=False)

    def record(ctx, stage, named, fill, grid, after=None, before=None):
        log.append((stage.key, fill, after is not None))

    monkeypatch.setattr(ga, "record", record)
    monkeypatch.setattr(
        ga, "full_screen", lambda ctx, post, fbo, size, named, fill, **_kw: log.append((post.key, fill, False))
    )
    return air, log, worked


def test_the_viewers_atmosphere_marches_a_quarter_of_the_pixels_and_fills_only_the_counter_while_still(monkeypatch):
    """With the stages of ``TEMPORAL_STAGES`` the QRhi passes accumulate as the native ones do; the passes that read
    the frame counter get it, a still frame writes nothing else into them, the others are left as they are."""
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gameshader as gs

    air, log, worked = _atmosphere(monkeypatch, temporal_stages=True)
    sky = SimpleNamespace(sun=(0.0, 1.0, 0.0), night=False)
    lit = SimpleNamespace(color_attachments=[1, 2])
    air.run(None, lit, (16, 8), _camera(), sky, {}, ga.SLook(temporal=True))
    assert [(key, copies) for key, _fill, copies in log] == [
        ("down", False), ("select", False), ("clouds", False), ("fog", False), ("accumulate", True), ("end", False),
        ("blur.h", False), ("blur.v", False), ("up", False)]  # fmt: skip
    fills = {key: fill for key, fill, _copies in log}
    assert fills["down"] is fills["clouds"] and fills["end"] is fills["up"] and fills["end"] is not fills["down"]
    counter = SConstantBuffer("cb", 16, [SVariable("cFrameCounter", 0, 4, "uint")])
    marched = gs.CConstants(counter)
    fills["clouds"](marched)
    first_keys = {key: fill.key for key, fill in fills.items()}
    log.clear()
    air.run(None, lit, (16, 8), _camera(), sky, {}, ga.SLook(temporal=True))  # a still frame
    fills = {key: fill for key, fill, _copies in log}
    assert fills["end"].key == first_keys["end"]  # left as it is (gamebind.keyed)
    assert fills["clouds"].key != first_keys["clouds"]  # the counter moved
    worked.clear()
    fills["clouds"](marched)
    assert worked == [] and int.from_bytes(bytes(marched.m_data[:4]), "little") == 2  # only the counter written
    assert air.settling()


def test_a_frame_that_marched_every_pixel_leaves_the_next_accumulated_frame_to_fill_its_buffers_again(monkeypatch):
    """A frame without the accumulation fills the counted passes' buffers with its own values (no frame flags), so
    the next accumulated frame of the same camera must fill them again, not only move the counter."""
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gameshader as gs

    air, log, worked = _atmosphere(monkeypatch, temporal_stages=True)
    sky = SimpleNamespace(sun=(0.0, 1.0, 0.0), night=False)
    lit = SimpleNamespace(color_attachments=[1])
    marched = gs.CConstants(SConstantBuffer("cb", 16, [SVariable("cFrameCounter", 0, 4, "uint")]))
    for look in (ga.SLook(temporal=True), ga.SLook(temporal=False), ga.SLook(temporal=True)):
        log.clear()
        worked.clear()
        air.run(None, lit, (16, 8), _camera(), sky, {}, look)
        {key: fill for key, fill, _copies in log}["clouds"](marched)
        assert worked == [marched], look  # every frame's values written


def test_an_atmosphere_without_the_stages_over_frames_marches_every_pixel_as_before(monkeypatch):
    """The island view's QRhi passes compile ``STAGES`` alone: its look asks for the frames' accumulation, which
    only its native passes do, so these march every pixel every frame and nothing settles."""
    from types import SimpleNamespace

    air, log, _worked = _atmosphere(monkeypatch, temporal_stages=False)
    sky = SimpleNamespace(sun=(0.0, 1.0, 0.0), night=False)
    air.run(None, SimpleNamespace(color_attachments=[1]), (16, 8), _camera(), sky, {}, ga.SLook(temporal=True))
    assert [key for key, _fill, _copies in log] == ["down", "clouds", "end", "fog", "blur.h", "blur.v", "up"]
    assert len({id(fill) for _key, fill, _copies in log}) == 1 and air.m_history is None
    assert not air.settling()
