"""Anno 2205's sea in the island view by its own shaders (``render.sea2205``, ``desktopgame2205._water``): the
setting's water block, the chunks over the session, the session's maps in the view's axes, the ordinary waves'
constants, the chunk grid's winding, the look's sea while it compiles and when it fails; installed (ANNO2205_DIR):
the sea's and the waves' techniques compile and the water maps read. docs/rendering/island-1800-2205-view.md 15."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from annotoolbox.render import sea2205

SETTING = """<AmbientInfo><Info><Water><Version>1</>
<WaveAmplitudes0>0.5</><WaveVelocities0><w>-1</><x>2</><y>3</><z>-4</></>
<WaveFrequencies1>3</><WaveChoppiness2>7</><ViewDepth>9</><WaterColor><x>0.1</><y>0.2</><z>0.3</></>
<FoamStreaking>0.25</></></></>"""


def test_the_settings_water_block_is_read_and_what_it_lacks_keeps_the_records_value():
    w = sea2205.read_water(SETTING)
    d = sea2205.Water()
    assert w.amplitudes == (0.5, d.amplitudes[1], d.amplitudes[2])
    assert w.velocities[0] == (2.0, 3.0, -4.0, -1.0) and w.velocities[1] == d.velocities[1]
    assert w.frequencies[1] == 3.0 and w.choppiness[2] == 7.0 and w.view_depth == 9.0
    assert w.color == (0.1, 0.2, 0.3) and w.foam == (d.foam[0], d.foam[1], 0.25)
    assert sea2205.read_water("<AmbientInfo><Info/></>") == d


def test_the_chunks_cover_the_session_once_finer_near_the_eye():
    size = 1024.0
    rows = sea2205.chunks((100.0, 20.0, 100.0), size)
    assert np.isclose((rows[:, 2] ** 2).sum(), size * size)  # no gap, no overlap: the quadtree's leaves
    near = rows[np.argmin(np.hypot(rows[:, 0] + rows[:, 2] / 2 - 100, rows[:, 1] + rows[:, 2] / 2 - 100))]
    assert near[2] == sea2205.NEAR_CHUNK and rows[:, 2].max() > 8 * sea2205.NEAR_CHUNK
    s = rows[:, 2]
    assert np.allclose(rows[:, 3] + s, sea2205.SPLIT * 2 * s)  # each fades where its parent would be chosen
    assert len(sea2205.chunks((5000.0, 900.0, 5000.0), size)) == 1  # far away: the whole square


def test_the_sessions_maps_are_turned_into_the_views_axes():
    raw = np.arange(2 * 2 * 4, dtype=np.uint8).reshape(2, 2, 4)
    side, data = sea2205.view_map(raw.tobytes())
    got = np.frombuffer(data, np.uint8).reshape(2, 2, 4)
    assert side == 2 and (got[:, 0] == raw[:, 1]).all() and (got[:, 1] == raw[:, 0]).all()
    assert sea2205.view_map(b"") is None and sea2205.view_map(bytes(12)) is None


def test_the_ordinary_waves_move_each_layer_by_its_velocities_and_name_every_element():
    water = sea2205.Water()
    got = sea2205.wave_values(water, 10.0, 0.0)
    assert got["cTime"] > 0  # the speed's divisor
    assert got["cWaveOffsets[1]"] == [v * 10.0 / 1024 for v in water.velocities[1]]
    assert got["cWaveAmplitudes[2]"] == water.amplitudes[2] and got["cTexelSizeTS"][1:3] == [0.0, 0.0]


def test_a_chunks_grid_turns_clockwise_with_x_right_and_z_up():
    mesh = sea2205.grid_mesh(2)
    xz = np.frombuffer(mesh.data, "<f4").reshape(-1, 2)
    tri = xz[mesh.indices.reshape(-1, 3)]
    a, b = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    # x right and z up the picture: clockwise is a negative z of x cross z
    assert (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0] < 0).all()
    assert mesh.count == 9 and len(mesh.indices) == 2 * 2 * 6


def _look(loader_result, layers=None):
    from annotoolbox.editor.desktop import desktopgame2205

    look = object.__new__(desktopgame2205.GameIsland2205)
    look.sea, look.sea_error, look.sea_waiting = None, False, False
    look.r = mock.MagicMock()
    look.r.loader.get.return_value = loader_result
    look.r.session = SimpleNamespace(island={}, size=64.0)
    opts = SimpleNamespace(layers=layers or {})
    return look, opts


def test_the_waves_start_from_still_water_their_first_frame_reads_a_cleared_target():
    sea = object.__new__(sea2205.Sea2205)
    tex = SimpleNamespace(native="tex")
    sea.waves = SimpleNamespace(targets={"even": (tex, "fbo-even"), "odd": (tex, "fbo-odd")},
                                run=mock.Mock(return_value=tex))  # fmt: skip
    sea.maps, sea.session, sea.textures, sea.water = {}, None, {}, sea2205.Water()
    sea.turn, sea.last, sea.size = 0, None, 64.0
    with mock.patch.object(sea2205, "draw_pass") as cleared:
        sea.run("ctx", 1.0)
        sea.run("ctx", 1.1)
    cleared.assert_called_once()
    assert cleared.call_args[0][:2] == ("ctx", "fbo-odd")  # the one the first frame reads its foam and speed from
    assert sea.waves.run.call_args_list[0][0][0] == "even"


def test_the_look_draws_no_sea_while_it_compiles_and_says_once_when_it_fails():
    f = SimpleNamespace(lt={})
    look, opts = _look(None)
    assert look._water(f, opts, (0, 0, 0), {}) == [] and look.sea_waiting
    look, opts = _look(RuntimeError("no water"))
    assert look._water(f, opts, (0, 0, 0), {}) == [] and look.sea_error
    assert look._water(f, opts, (0, 0, 0), {}) == []
    assert look.r._message.call_count == 1 and look.r.loader.get.call_count == 1
    look, opts = _look(None, {"water": False})
    assert look._water(f, opts, (0, 0, 0), {}) == [] and not look.r.loader.get.called


def test_the_seas_and_the_waves_techniques_compile_and_the_water_maps_read():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO2205_DIR")
    if not root:
        pytest.skip("needs an installed Anno 2205 (ANNO2205_DIR)")
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.islands.islandview import Files
    from annotoolbox.othergames.installs import GAMES
    from annotoolbox.render import terrain1800

    files = Files(GameFiles(GAMES["anno2205"].index(Path(root), None)))
    compiler = Fxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    got = sea2205.compile_stages(terrain1800.TerrainSource(files, compiler, "anno2205"))
    stages, state = got["water"]
    water = next(cb for cb in stages[0].reflection.cbuffers if cb.name == "cbWater")
    assert "cWaterChunks" in {v.name for v in water.variables} and not state.blend
    assert {"position", "normals"} <= set(got)
    assert set(sea2205.read_maps(files)) == set(sea2205.MAPS)
