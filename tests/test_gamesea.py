"""The sea by 117's ocean shader (``render.gamesea``): its grid, the island's maps as the shader reads them, the
coast's distance, its place around the eye and the ambient's constants; synthetic data only."""

import numpy as np
import pytest

gamesea = pytest.importorskip("annotoolbox.render.gamesea")


def test_the_grid_is_denser_at_the_middle_and_covers_minus_one_to_one():
    mesh = gamesea.grid(5)
    points = np.frombuffer(mesh.data, "<f4").reshape(-1, 2)
    assert mesh.count == 25 and mesh.stride == 8 and len(mesh.indices) == 4 * 4 * 6
    assert points[:, 0].min() == -1.0 and points[:, 0].max() == 1.0
    xs = np.unique(points[:, 0])
    assert np.allclose(xs, [-1.0, -0.25, 0.0, 0.25, 1.0])  # u |u|: the inner steps the finest
    assert mesh.indices.max() == 24


def test_the_islands_water_maps_turn_x_over_and_open_onto_the_sea_at_their_edge():
    n = 6
    cells = np.zeros((n, n, 4), np.uint8)
    cells[2, 1] = (10, 20, 30, 40)  # row 2 (z), column 1 (x)
    rg, ba = gamesea.info_maps(cells.tobytes())
    assert rg.shape == (n, n, 2) and ba.shape == (n, n, 2)
    assert tuple(rg[2, n - 2]) == (10, 20) and tuple(ba[2, n - 2]) == (30, 40)  # the view's mirrored x
    for edge in (rg[0], rg[-1], rg[:, 0], rg[:, -1]):
        assert (edge == gamesea.DEEP[:2]).all()
    assert (ba[0] == 0).all() and (ba[:, -1] == 0).all()


def test_the_coast_distance_is_positive_at_sea_and_negative_on_land():
    pytest.importorskip("scipy")
    heights = np.full((33, 33), -5.0)
    heights[10:23, 10:23] = 4.0  # a square of land in the middle
    sdf = gamesea.coast_distance(heights, 16, 64.0)  # 4 world units a cell
    assert sdf.shape == (16, 16) and sdf.dtype == np.float32
    assert sdf[8, 8] < 0 < sdf[2, 3]
    assert sdf[2, 3] > sdf[4, 4] > 0  # nearer the coast, nearer 0
    assert (sdf[0] == gamesea.OPEN_SEA).all() and (sdf[:, -1] == gamesea.OPEN_SEA).all()


def test_the_coast_distance_without_scipy_says_sea_or_land(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_scipy(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_scipy)
    heights = np.full((8, 8), -1.0)
    heights[3:5, 3:5] = 2.0
    sdf = gamesea.coast_distance(heights, 8, 8.0)
    assert sdf[3, 3] == -gamesea.NO_SCIPY and sdf[1, 2] == gamesea.NO_SCIPY


def test_an_edited_coast_goes_into_the_seas_distance_map_once_it_has_its_island():
    sea = object.__new__(gamesea.Sea)  # no device: what it would upload
    sea.size, sea._cells, sea._model, sea._pending = 0.0, 2, None, None
    heights = np.full((17, 17), -3.0)
    sea.set_coast(heights)
    assert sea._pending is None  # before the island: nothing
    sea.size, sea._cells, sea._pending = 64.0, 8, {}
    heights[4:13, 4:13] = 2.0
    sea.set_coast(heights)
    _, sdf = sea._pending["cTerrainSDF"]
    assert sdf.shape == (8, 8) and sdf[4, 4] < 0 < sdf[1, 1]


def test_the_grid_is_placed_around_the_eye_on_its_snap():
    m = gamesea.place([100.0, 50.0, 23.0], 4000.0)
    assert m[0, 0] == 4000.0 and m[2, 2] == 4000.0 and m[1, 1] == 1.0
    assert tuple(m[3, :3]) == (96.0, 0.0, 16.0)  # row vectors: the translation in the last row
    corner = np.array([1.0, 0.0, -1.0, 1.0]) @ m
    assert tuple(corner[:3]) == (4096.0, 0.0, -3984.0)


def test_the_seas_constants_come_from_the_ambients_water_block():
    text = (
        "<Water><OffshoreDistance>3</>\n<OffshoreTransitionDist>40</>\n<CausticsIntensity>0.2</>\n"
        "<WaterColorDeep><x>0.1</>\n<y>0.2</>\n<z>0.3</>\n</>\n<ViewDepth>7</>\n</>"
    )
    got = gamesea.read_water(text)
    assert got["cOffshoreDistance"] == 3.0 and got["cOffshoreTransitionDist"] == 40.0
    assert got["cCausticsIntensity"] == 0.2 and got["cViewDepth"] == 7.0
    assert tuple(got["cWaterColorDeep"]) == (0.1, 0.2, 0.3)
    assert got["cScatterStrength"] == gamesea.NUMBERS["ScatterStrength"]  # Roman's where the block lacks it


def test_the_islands_flow_map_turns_x_over_with_its_flow_and_opens_onto_the_sea():
    n = 5
    cells = np.full((n, n, 4), 127, np.uint8)
    cells[2, 1] = (200, 60, 30, 0)  # flowing to +x, near the coast
    flow = gamesea.flow_map(cells.tobytes())
    assert tuple(flow[2, n - 2]) == (54, 60, 30, 0)  # at the mirrored x, flowing to the view's -x
    assert (flow[0] == gamesea.NO_FLOW).all() and (flow[:, -1] == gamesea.NO_FLOW).all()
    assert (gamesea.flow_map(b"") == gamesea.NO_FLOW).all()  # an island without one: no flow, far from land


def test_the_coast_waves_square_keeps_to_whole_texels_and_says_how_far_it_moved():
    corner, first = gamesea.waves_settings((100.2, 50.0), None)
    side = gamesea.WAVES_TEXELS * gamesea.WAVES_CELL
    assert first["cSettings1"][:2] == [-10000.0, -10000.0]  # the first step starts empty
    assert np.allclose(corner, np.round((np.array([100.2, 50.0]) - side / 2) / gamesea.WAVES_CELL) * gamesea.WAVES_CELL)
    moved, settings = gamesea.waves_settings((101.2, 49.5), corner)
    assert settings["cSettings1"][:2] == [2.0, -1.0]  # old texels read two to the right, one down
    assert settings["cSettings0"][2:] == [side, side] and settings["cSettings1"][2] == 1 / gamesea.WAVES_TEXELS


def test_the_coast_waves_follow_where_the_view_looks_at_the_sea():
    from types import SimpleNamespace

    from annotoolbox.render import gameframe, gameshader

    down = gameshader.look_at(np.array([0.0, 10.0, 0.0]), np.array([0.0, 0.0, 10.0])) @ gameframe.LEFT_HANDED
    centre = gamesea.waves_centre(SimpleNamespace(eye=np.array([0.0, 10.0, 0.0]), view=down), 256.0)
    assert np.allclose(centre, [0.0, 10.0])  # 45 degrees down from 10 up: 10 ahead
    up = gameshader.look_at(np.array([0.0, 10.0, 0.0]), np.array([0.0, 20.0, 10.0])) @ gameframe.LEFT_HANDED
    assert np.allclose(
        gamesea.waves_centre(SimpleNamespace(eye=np.array([5.0, 10.0, 1.0]), view=up), 256.0), [5.0, 1.0]
    )
    far = gameshader.look_at(np.array([0.0, 100.0, 0.0]), np.array([0.0, 99.0, 100.0])) @ gameframe.LEFT_HANDED
    got = gamesea.waves_centre(SimpleNamespace(eye=np.array([0.0, 100.0, 0.0]), view=far), 256.0)
    assert got[1] <= 256.0 / 3 + 1e-6  # a glancing look: no farther than a third of the side
