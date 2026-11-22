"""Anno 1800's ocean in the island view by its own shaders (``render.ocean1800``, ``desktopgame1800._water``): the
setting's water block and its values in the frame's constants, a patch's matrices against the vertex shader's reads,
the patches over the island's square and around it, the engine's ocean values, the look's sea while it compiles;
installed (ANNO1800_DIR): the surface's technique compiles with its small waves and the maps read.
docs/rendering/island-1800-2205-view.md 16."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from annotoolbox.render import ocean1800, waves1800

SETTING = """<AmbientInfo><Info><Water><WaveAmplitude>0.5</><WaveSeed>7</><ViewDepth>9</><Absorption>2</>
<WaterColorDeep><x>0.1</><y>0.2</><z>0.3</></></></></>"""


def test_the_settings_water_block_is_read_into_the_frames_constants_and_what_it_lacks_keeps_the_records_value():
    w, d = ocean1800.read_water(SETTING), ocean1800.SWater()
    assert (w.amplitude, w.seed, w.view_depth, w.absorption) == (0.5, 7, 9.0, 2.0)
    assert w.deep == (0.1, 0.2, 0.3) and w.shallow == d.shallow and w.light_depth == d.light_depth
    assert ocean1800.read_water("<AmbientInfo><Info/></>") == d
    assert ocean1800.frame_values(w) == {"cWaterViewDepth": 9.0, "cWaterLightDepth": d.light_depth,
                                         "cAbsorption": 2.0, "cCausticsIntensity": d.caustics,
                                         "cWaterQuality": 1.0}  # fmt: skip


def test_a_patchs_grid_corners_land_where_the_vertex_shader_places_them_and_the_waves_run_on_across_patches():
    values = ocean1800.patch_values(32.0, 64.0, 16.0, (1.0, 2.0, 3.0))
    corner = np.array([1.0, 0.0, 0.5, 1.0])  # vsWaterFFT: float4(PositionOS.x, 0, PositionOS.y, 1)
    local = corner @ values["cMtxOceanLocal"]  # MUL(v, M): row vectors
    world = local @ values["cMtxOceanWorld"]
    assert np.allclose(world[:3], (48.0, 0.0, 72.0)) and np.allclose(local, world)  # local is the world here
    assert values["cLocalEye"] == [1.0, 2.0, 3.0]
    s = ocean1800.surface_values(ocean1800.SWater(), 768.0, 0.0)
    assert np.isclose(s["cUVScale"], 1 / 16) and np.isclose(s["cTexelLengthX2"], 16.0)  # the engine's
    assert s["cWaveTime"] == waves1800.wave_time(0.0) and s["cWaterInfoCoord"] == [1 / 768.0, 1 / 768.0]


class FakeRows:
    """A stand-in of the frame's rows: each placed patch at the next 256 bytes."""

    def __init__(self) -> None:
        self.placed: list = []

    def begin(self) -> None:
        self.placed = []

    def place_many(self, name: str, rows, reach: int) -> list:
        first = len(self.placed)
        self.placed += [(name, bytes(raw)) for raw in rows]
        return [256 * k for k in range(first, len(self.placed))]


def _ocean(size: float = 64.0, cbuffers: dict | None = None):
    ocean = object.__new__(ocean1800.COcean1800)
    ocean.m_ctx, ocean.m_mesh, ocean.m_state, ocean.m_dummies, ocean.m_shared = (mock.MagicMock(),) * 5
    ocean.m_program = SimpleNamespace(m_cbuffers=cbuffers or {})
    ocean.m_own_shared, ocean.m_rows = False, FakeRows()
    ocean.m_patches = mock.MagicMock()
    ocean.m_patches.take.side_effect = lambda: SimpleNamespace(m_slot=None)
    ocean.m_draws, ocean.m_size_tiles, ocean.m_values, ocean.m_textures = [], size, {}, {}
    return ocean


def test_the_patches_cover_the_square_and_around_it_once():
    ocean = _ocean()
    draws = ocean.models((32.0, 20.0, 32.0))
    lo = min(float(d.m_box[0][0]) for d in draws), min(float(d.m_box[0][2]) for d in draws)
    hi = max(float(d.m_box[1][0]) for d in draws), max(float(d.m_box[1][2]) for d in draws)
    span = (ocean1800.SPAN - 1) / 2 * 64.0
    assert lo == (-span, -span) and hi == (64.0 + span, 64.0 + span)
    area = sum(float(d.m_box[1][0] - d.m_box[0][0]) ** 2 for d in draws)
    assert np.isclose(area, (ocean1800.SPAN * 64.0) ** 2)  # no patch twice, no gap
    assert ocean1800.FALLBACKS["cLargeWaveGradientMap"] == (0, 0, 0, 0)  # flat where the FFT does not compile


def _area(vertices: np.ndarray, indices: np.ndarray) -> float:
    a, b, c = (vertices[indices.reshape(-1, 3)[:, k]] for k in range(3))
    u, v = b - a, c - a
    return float(np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]).sum() / 2)


def test_a_stitched_grid_covers_its_patch_and_meets_a_patch_twice_its_side_vertex_for_vertex():
    n = 8
    vertices = np.frombuffer(ocean1800.sea2205.grid_mesh(n).m_data, np.float32).reshape(-1, 2)
    for mask in range(16):
        indices = ocean1800.stitched_indices(n, mask)
        assert np.isclose(_area(vertices, indices), 1.0)  # no hole, nothing twice
        used = vertices[np.unique(indices)]
        for bit, (axis, at) in enumerate(((0, 0.0), (0, 1.0), (1, 0.0), (1, 1.0))):
            on_edge = used[np.isclose(used[:, axis], at)][:, 1 - axis]
            step = 2 / n if mask >> bit & 1 else 1 / n
            assert np.allclose(np.sort(on_edge), np.arange(0, 1 + 1e-6, step))
    mesh = ocean1800.CGridMesh.made(n)
    assert len(mesh.m_spans) == 16 and mesh.m_spans[0] == (0, n * n * 6)
    assert sum(count for _first, count in mesh.m_spans) == len(mesh.m_indices)


def test_a_patch_knows_which_edges_a_coarser_patch_lies_beyond_and_neighbours_stay_within_twice():
    rng = np.random.default_rng(5)
    for _ in range(20):
        eye = (rng.uniform(0, 320), rng.uniform(5, 80), rng.uniform(0, 320))
        cells = ocean1800.sea2205.chunks(eye, 320.0, ocean1800.NEAR_PATCH, ocean1800.SPLIT)
        masks = ocean1800.edge_masks(cells)
        for (x, z, s, _f), mask in zip(cells, masks, strict=True):
            probes = (
                (x - 0.01, z + s / 2),
                (x + s + 0.01, z + s / 2),
                (x + s / 2, z - 0.01),
                (x + s / 2, z + s + 0.01),
            )
            for bit, (px, pz) in enumerate(probes):
                beyond = [c for c in cells if c[0] <= px < c[0] + c[2] and c[1] <= pz < c[1] + c[2]]
                if not beyond:
                    assert not mask >> bit & 1  # the sea's rim
                    continue
                other = beyond[0][2]
                assert other in (s / 2, s, 2 * s)
                assert bool(mask >> bit & 1) == (other == 2 * s)


def test_a_patch_draws_its_stitched_variant_of_the_one_index_buffer():
    mesh = ocean1800.CGridMesh.made(4)
    cb = mock.MagicMock()
    mesh.m_span = mesh.m_spans[5]
    mesh.draw(cb, "pipeline", "srb", (8, 8), ([], "ibuf"))
    cb.drawIndexed.assert_called_once_with(mesh.m_spans[5][1], 1, mesh.m_spans[5][0])
    cb.setGraphicsPipeline.assert_called_once_with("pipeline")


def test_the_viewers_sea_lies_under_its_model_by_the_squares_corner():
    plain, moved = _ocean(), _ocean()
    a, b = plain.models((32.0, 20.0, 32.0)), moved.models((32.0, 20.0, 32.0), origin=(-100.0, 50.0))
    assert len(a) == len(b)
    for p, q in zip(a, b, strict=True):  # the same patches, moved; the eye in the world's axes
        assert np.allclose(q.m_box[0], p.m_box[0] + (-100.0, 0.0, 50.0))
        assert np.allclose(q.patch["cMtxOceanLocal"][3, [0, 2]], p.patch["cMtxOceanLocal"][3, [0, 2]] + (-100, 50))
        assert q.patch["cLocalEye"] == [-68.0, 20.0, 82.0]


def test_each_patchs_own_values_are_its_slot_of_the_frames_rows():
    def var(name, offset, size, type_name):
        return SimpleNamespace(name=name, offset=offset, size=size, type=type_name, elements=0, members=[])

    layout = SimpleNamespace(name=ocean1800.PATCH_BUFFER, size=160,
                             variables=[var("cMtxOceanLocal", 0, 64, "float4x4"), var("cLocalEye", 144, 12, "float3")])  # fmt: skip
    ocean = _ocean(cbuffers={ocean1800.PATCH_BUFFER: layout})
    draws = ocean.models((32.0, 20.0, 32.0))
    assert len(ocean.m_rows.placed) == len(draws) > 1
    assert [d.m_draw_call.m_slot for d in draws] == [(ocean1800.PATCH_BUFFER, 256 * k) for k in range(len(draws))]
    corners = set()
    for d, (_name, raw) in zip(draws, ocean.m_rows.placed, strict=True):
        local = np.frombuffer(raw[:64], np.float32).reshape(4, 4)
        m = d.patch["cMtxOceanLocal"]
        assert np.allclose(local, m) or np.allclose(local, m.T)  # the patch's own matrix, as the buffer packs it
        corners.add((float(m[3, 0]), float(m[3, 2])))
        assert np.allclose(np.frombuffer(raw[144:156], np.float32), (32.0, 20.0, 32.0))
    assert len(corners) == len(draws)  # each slot its own patch
    # the frame's rows made at once are each patch's buffer as written alone (section 16)
    from annotoolbox.render import gameshader as gs

    cells = np.array([[d.m_cell[0], d.m_cell[1], d.m_cell[2]] for d in draws])
    for d, row in zip(draws, ocean1800.patch_rows(layout, cells, draws[0].m_eye), strict=True):
        alone = gs.CConstants(layout)
        gs.set_values([alone], d.patch)
        assert bytes(row) == bytes(alone.m_data)


def test_every_patchs_edges_are_found_at_once_as_one_by_one():
    rng = np.random.default_rng(3)
    for _ in range(20):
        eye = (*rng.uniform(0, 320, 1), rng.uniform(5, 200), *rng.uniform(0, 320, 1))
        found = ocean1800.sea2205.chunks(eye, 320.0, ocean1800.NEAR_PATCH, ocean1800.SPLIT)
        keys = {(round(float(s), 3), round(float(x) / s), round(float(z) / s)) for x, z, s in found[:, :3]}
        alone = []
        for x, z, s in found[:, :3].astype(float):
            big, eps, mask = 2 * s, s * 1e-3, 0
            probes = ((x - eps, z + s / 2), (x + s + eps, z + s / 2), (x + s / 2, z - eps), (x + s / 2, z + s + eps))
            for bit, (px, pz) in enumerate(probes):
                mask |= ((round(big, 3), int(np.floor(px / big)), int(np.floor(pz / big))) in keys) << bit
            alone.append(mask)
        assert ocean1800.edge_masks(found).tolist() == alone


def test_the_look_draws_no_sea_while_it_compiles_and_puts_the_settings_water_into_the_frame():
    from annotoolbox.editor.desktop import desktopgame1800

    look = object.__new__(desktopgame1800.CGameIsland1800)
    look.m_sea, look.m_sea_error, look.m_sea_waiting = None, False, False
    look.m_renderer = mock.MagicMock()
    look.m_renderer.m_loader.get.return_value = None
    look.m_renderer.m_session = SimpleNamespace(m_island={}, m_size_tiles=64.0)
    f, opts = SimpleNamespace(lt={}), SimpleNamespace(layers={})
    assert look._water(f, opts, (0, 0, 0), {}) == [] and look.m_sea_waiting
    look.m_sea = mock.MagicMock()
    look.m_sea.frame_values.return_value = {"cAbsorption": 3.5}
    values: dict = {}
    look._water(f, opts, (0, 0, 0), values)
    assert values == {"cAbsorption": 3.5} and look.m_renderer.m_game_sea and look.m_sea.run.called


def test_the_oceans_surface_compiles_with_its_small_waves_and_the_maps_read():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from annotoolbox.editor.formats.a7m.reader import CGameFiles
    from annotoolbox.infra.fxc import CFxc
    from annotoolbox.islands.islandview import CFiles
    from annotoolbox.othergames.installs import GAMES
    from annotoolbox.render import terrain1800

    files = CFiles(CGameFiles(GAMES["anno1800"].index(Path(root), None)))
    compiler = CFxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    got = ocean1800.compile_stages(terrain1800.CTerrainSource(files, compiler, "anno1800"))
    stages, state = got["water"]
    names = {cb.name: {v.name for v in cb.variables} for cb in stages[0].reflection.cbuffers}
    assert {"cMtxOceanLocal", "cLocalEye"} <= names["cbOceanFFTPerCall"] and set(ocean1800.OCEAN) <= names["cbOceanFFT"]
    assert not state.blend and got["gradients"] is not None
    assert set(ocean1800.read_maps(files)) == set(ocean1800.MAPS)
