"""117's ground baked by the game's bake (``render.terrainbake``, the island view's ``desktopgame``): the materials a
node needs, the splat list and the bake's constants, the splat maps in the bindless stand-in, the shader rewrite that
reads them, the terrain vertex's tangent, the tint page, the paint strokes in the material maps; with an installed
Anno 117 (``ANNO117_DIR``) and Direct3D 12 the island view's ground baked by the game's shaders, its terrain casting
into the sun's cascades and a paint stroke baked again (in a child process)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.render import gameshader, terrainbake, terraindraw
from annotoolbox.render.terrainnodes import Node


def _words(slots: list[tuple[int, int]]) -> int:
    """A material-map word of (id or alpha) per slot, 5 bits each."""
    return sum(v << (5 * s) for s, v in enumerate(v for v, _ in slots))


def _ground(size: float = 8.0) -> terrainbake.GroundSource:
    """An island of ``size`` tiles, 4 alpha texels a tile, ids per 2 x 2: material 3 everywhere in slot 0, material 5
    in slot 1 with an alpha in the first tile only."""
    n = int(size * 4)
    ids = np.full((n // 2, n // 2), _words([(3, 0), (5, 0)]), np.uint32)
    alphas = np.full((n, n), _words([(31, 0)]), np.uint32)
    alphas[:4, :4] = _words([(31, 0), (16, 0)])
    splats = [terrainbake.Splat(5, 2.0, 0.5, -0.1, 0.2, True), terrainbake.Splat(3, 4.0, 1.0)]
    return terrainbake.GroundSource(splats, ids, alphas, size)


def test_a_node_bakes_the_materials_over_its_square_in_the_sets_order():
    g = _ground()
    assert g.block == 2 and g.texel == pytest.approx(0.25)
    assert g.bits.shape == (8, 8) and g.bits[0, 0] == (1 << 3 | 1 << 5) and g.bits[5, 5] == 1 << 3
    near = terrainbake.node_splats(g, Node(2, 0.0, 0.0))
    far = terrainbake.node_splats(g, Node(2, 4.0, 4.0))
    assert [s.mat_id for s in near] == [5, 3]  # the set's order, not the ids'
    assert [s.mat_id for s in far] == [3]
    v = terrainbake.splat_values(near)
    assert v["cTrnBakeNumSplats"] == 2 and v["cSplatsBuffer[0].MatID"] == 5 and v["cSplatsBuffer[0].Flags"] == 1
    assert v["cSplatsBuffer[1].Repetition"] == 4.0 and v["cSplatsBuffer[1].Flags"] == 0


def test_the_bake_constants_invert_the_islands_place_and_name_the_page():
    g = _ground(64.0)
    island = terraindraw.island_values(64.0)
    v = terrainbake.bake_values(Node(3, 8.0, 16.0), g, island, [1.0, 2.0, 4.0])
    m0, m1 = np.array(island["cTrnIslandMatrix0"]), np.array(island["cTrnIslandMatrix1"])
    i0, i1 = np.array(v["cTrnIslandMatrixInv0"]), np.array(v["cTrnIslandMatrixInv1"])
    p = np.array([10.0, 3.0])
    ws = np.array([p @ m0[:2] + m0[2], p @ m1[:2] + m1[2]])
    assert np.allclose([ws @ i0[:2] + i0[2], ws @ i1[:2] + i1[2]], p)
    assert v["cTrnBakeTessellationEnabled"] == 1 and v["cTrnHeightPageParams"] == [1.0, 2.0, 4.0]
    assert v["cTrnBakedTexelSizeWS"] == pytest.approx(8.0 / terrainbake.SIZE) and v["cTrnMatIDBlockSize"] == 2
    flat = terrainbake.bake_values(Node(3, 8.0, 16.0), g, island)
    assert flat["cTrnBakeTessellationEnabled"] == 0
    low = terrainbake.bake_values(Node(3, 8.0, 16.0), g, island, size=128)  # the quality's bake resolution
    assert low["cTrnBakedTexelSizeWS"] == pytest.approx(8.0 / 128) and low["cTrnCompressTexelSize"] == [1 / 128] * 2


def test_the_splat_maps_sit_at_id_times_three_in_the_bindless_stand_in():
    table, words = terrainbake.splat_table(_ground().splats)
    for mid in (3, 5):
        for k in range(3):
            assert words[mid * 3 + k] != 0 or table.slots[str(mid * 3 + k)] == 0
    assert words[0] == 0 and len(table.slots) == 6  # every missing map stands in, slot 0 stays the null texture
    hlsl = """Texture2D cTrnBakeSplatTextures[96];
struct SplatTextures
{
    Texture2D DiffuseTex;
};
float4 f(uint i, SamplerState s)
{
    SplatTextures t;
    t.DiffuseTex = cTrnBakeSplatTextures[i * 3];
    return t.DiffuseTex.Sample(s, float2(0, 0));
}
"""
    out = gameshader.adapt(hlsl, "f").hlsl
    assert "cTrnBakeSplatTextures" not in out and "BindlessAt(i * 3)" in out and "BindlessTex DiffuseTex;" in out


def test_the_bake_lets_its_splat_maps_go_once_they_are_on_the_device(monkeypatch):
    """The bake kept every material's map files after uploading them (300 MB on a continental island); they go, the
    materials' values stay (a new bake reads the ground again: ``desktopgame.GameIsland._game_bake``)."""
    from types import SimpleNamespace

    from annotoolbox.render import gamedraw

    monkeypatch.setattr(terrainbake, "_texture", lambda *a, **k: object())
    monkeypatch.setattr(gamedraw, "upload_level", lambda *a: None)
    monkeypatch.setattr(gamedraw, "class_array", lambda rhi, batch, maps: object())
    info = SimpleNamespace(width=4, height=4, mips=1, format="DXT1", srgb=False, faces=1)
    dds = SimpleNamespace(info=info, stored=lambda level=0, face=0: bytes(8))
    ground = _ground()
    ground.splats = [terrainbake.Splat(3, 4.0, 1.0, maps=(dds, dds, None))]
    bake = terrainbake.Bake.__new__(terrainbake.Bake)
    bake.rhi, bake.ground, bake.keep = None, ground, []
    bake._ground_textures(None)
    assert ground.splats == [terrainbake.Splat(3, 4.0, 1.0)] and len(bake.bindless) == gameshader.CLASSES


def test_the_terrain_vertex_carries_the_engines_tangent_along_minus_x():
    step, w = 0.5, 9
    j, i = np.mgrid[0:w, 0:w]
    heights = (0.5 * i * step).astype(np.float32)  # up along x
    normals = terraindraw.vertex_normals(heights, step)
    raw = terraindraw.encode_vertices(heights, step, Node(2, 0.0, 0.0), w, normals)
    v = np.frombuffer(raw, np.uint8).reshape(-1, 16)
    tangent = v[:, [14, 13, 12]] / 255 * 2 - 1  # read .bgra
    normal = v[:, [10, 9, 8]] / 255 * 2 - 1
    assert (tangent[:, 0] < -0.8).all() and np.abs((tangent * normal).sum(axis=1)).max() < 0.03
    tinted = terraindraw.node_values(2, 1, tint=[3.0, 1.0, 20.0])
    assert tinted["cTrnTintPageParams"] == [3.0, 1.0, 20.0, 1.0]
    assert terraindraw.node_values(2, 1)["cTrnTintPageParams"][3] == 0.0


def test_each_node_casts_by_the_shadow_technique_of_its_kind():
    plain, patches, detailed = object(), object(), object()
    terrain = terraindraw.Terrain(None, None, 64.0, 0.5, 129, detailed=detailed)
    assert terrain.casters([]) == [] and terrain.shadow is None
    terrain.shadow = (plain, patches)
    alone = terraindraw.Owner(None)
    near = terraindraw.TerrainModel(detailed, None, {"v": 1}, alone, {"page": 2})
    far = terraindraw.TerrainModel(object(), None, {}, alone)
    near.node, far.node = Node(2, 0.0, 0.0), Node(4, 16.0, 0.0)
    casters = terrain.casters([near, far])
    assert [c.program for c in casters] == [patches, plain]
    assert casters[0].share is near and casters[0].node_values is near.node_values and casters[0].own == {"page": 2}
    assert terrain.casters([near, far])[0] is casters[0]  # kept while its node's model stays
    terrain.shadow = (plain, None)  # no tessellated shadow: a tessellated node casts none
    again = terraindraw.TerrainModel(detailed, None, {}, alone)
    again.node = near.node
    assert [c.program for c in terrain.casters([again, far])] == [plain]


def test_paint_strokes_go_into_the_maps_and_an_undo_gives_the_files_back():
    from annotoolbox.editor.desktop.desktopgamebake import PaintedMaps

    g = _ground(16.0)
    base = g.alphas.copy(), g.ids.copy()
    maps = PaintedMaps(g, {3: 0, 5: 1})
    assert maps.update([], None) == [] and maps.committed is None  # no copies before the first stroke
    stroke = {"material": 5, "radius": 2.0, "strength": 1.0, "points": [[8.0, 8.0], [9.0, 8.0]]}
    boxes = maps.update([], stroke)  # in progress
    assert boxes and not np.array_equal(g.alphas, base[0])
    x0, z0, x1, z1 = boxes[-1]
    assert x0 <= 32 < x1 and z0 <= 32 < z1  # tile 8 at 4 texels a tile
    outside = np.ones(g.alphas.shape, bool)
    outside[z0:z1, x0:x1] = False
    assert np.array_equal(g.alphas[outside], base[0][outside])
    maps.update([stroke], None)  # committed: the same words
    painted = g.alphas.copy()
    assert np.array_equal(painted, maps.committed[1])
    maps.update([], None)  # undone
    assert np.array_equal(g.alphas, base[0]) and np.array_equal(g.ids, base[1])


def test_the_maps_let_go_on_the_device_come_back_from_the_file_at_the_first_stroke():
    from annotoolbox.editor.desktop.desktopgamebake import PaintedMaps

    g = _ground(16.0)
    base = g.alphas.copy(), g.ids.copy()
    kept = _ground(16.0)
    kept.release_maps()  # nothing to read them again from: kept
    assert kept.has_maps()
    g.reread = lambda: (base[1], base[0])  # the island file's maps
    g.release_maps()
    assert not g.has_maps() and g.alphas.shape == base[0].shape and g.block == kept.block
    again = PaintedMaps(g, {3: 0, 5: 1})
    stroke = {"material": 5, "radius": 2.0, "strength": 1.0, "points": [[8.0, 8.0], [9.0, 8.0]]}
    again.update([stroke], None)
    assert g.has_maps() and not np.array_equal(g.alphas, base[0])
    again.update([], None)  # undone: the file's maps read again
    assert again._kept is None and np.array_equal(g.alphas, base[0]) and np.array_equal(g.ids, base[1])


def test_a_stroke_after_the_maps_went_with_strokes_in_them_paints_over_the_committed_maps():
    """A session opened with paint strokes has them in the maps before the bake's first run lets the maps go: the
    next stroke wrote into the let-go maps and failed."""
    from annotoolbox.editor.desktop.desktopgamebake import PaintedMaps

    g = _ground(16.0)
    base = g.alphas.copy(), g.ids.copy()
    g.reread = lambda: (base[1], base[0])
    maps = PaintedMaps(g, {3: 0, 5: 1})
    stroke = {"material": 5, "radius": 2.0, "strength": 1.0, "points": [[8.0, 8.0], [9.0, 8.0]]}
    maps.update([stroke], None)
    painted = g.alphas.copy()
    g.release_maps()  # the bake's first run
    more = {"material": 5, "radius": 1.0, "strength": 1.0, "points": [[13.0, 13.0]]}
    x0, z0, x1, z1 = maps.update([stroke, more], None)[0]
    assert g.has_maps() and not np.array_equal(g.alphas, painted)
    outside = np.ones(g.alphas.shape, bool)
    outside[z0:z1, x0:x1] = False
    assert np.array_equal(g.alphas[outside], painted[outside])  # the first stroke is still in them


INSTALL_BAKE = """
import json, time
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop.desktoprender import Renderer, ViewOptions
from annotoolbox.editor.editsession import EditSession
from annotoolbox.render import gameextras
from annotoolbox.render.camera import OrbitCamera
from annotoolbox.render.rhiview import RhiView
app = QApplication([])
eapp = editorapp.App()
eapp.open("anno117", "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m")
s = EditSession(json.loads(eapp.editor.island_json()), eapp.editor.edits())
s.finish_loading()
CAM = (92.0, 4.0, 110.0)

class View(RhiView):
    def ready(self, ctx):
        self.r = Renderer(ctx, s, eapp.editor)
    def draw(self, ctx, screen, size):
        self.r.render(screen, size, OrbitCamera(CAM, 30.0), self.opts)

def settle(view):
    start = time.monotonic()
    view.picture(160, 100)
    while (view.r.game is None or view.r.busy()) and time.monotonic() - start < 300:
        time.sleep(0.2)
        view.picture(160, 100)
    for _ in range(3):
        img = view.picture(160, 100)
    return np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(160)] for y in range(100)], float)

# the picture held still: the clouds at one moment, no eye adaptation (a painted patch would move the exposure)
gameextras.FrameExtras._exposure = lambda self, *args: None
view = View("d3d12")
view.opts = ViewOptions(native=True, game_terrain=True, game_light=True, particle_time=0.0,
                        layers=dict.fromkeys(("objects", "markers", "props", "grass", "forest", "water"), False))
before = settle(view)
mats = eapp.editor.view.materials()
top = max(mats, key=lambda m: mats[m]["order"])
x, z = s.size - CAM[0], CAM[2]
s.begin_paint(top, 4.0, 1.0, x - 6, z)
for k in range(1, 13):
    s.paint_to(x - 6 + k, z)
s.end_paint()
after = settle(view)
d = np.abs(before - after).mean(axis=2)
game = view.r.game
print(json.dumps({"bake": game.bake is not None, "casters": view.r.stats.get("terrain_casters", 0), "std": float(before.std()), "centre": float(d[35:65, 55:105].mean()),
                  "corner": float(d[:20, :30].mean()), "why": game.why_not(), "messages": view.r.messages,
                  "failed": view.failed}))
"""


def test_the_island_views_ground_is_baked_by_the_games_bake_and_painted():
    import os
    import sys

    if not os.environ.get("ANNO117_DIR") or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_BAKE)
    assert got["bake"] and got["why"] == "" and not got["failed"] and not got["messages"], got
    assert got["std"] > 3 and got["centre"] > 0.5 and got["corner"] < 0.1, got
    assert got["casters"] > 0, got  # the terrain's shadow technique into the sun's cascades
