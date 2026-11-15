"""117's terrain by the game's terrain shader (``render.terraindraw``, ``terrainnodes``, the island view's
``desktopgame``): the engine's vertex format read back by the shader's own formulas, the normal page, the island's
place, the nodes a camera picks and the slices they keep; with an installed Anno 117 (``ANNO117_DIR``) and
Direct3D 12 the island view draws its terrain by the game's shaders (in a child process)."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.render import terrainnodes
from annotoolbox.render.terrainnodes import Node, SliceCache


def _area(nodes, size) -> float:
    return sum(min(n.tiles, size - n.x) * min(n.tiles, size - n.z) for n in nodes)


def test_the_nodes_cover_the_island_once_finer_near_the_eye():
    size = 96.0
    nodes = terrainnodes.select(size, (10.0, 5.0, 10.0))
    assert _area(nodes, size) == pytest.approx(size * size)
    for a in nodes:  # no two overlap
        for b in nodes:
            if a is not b:
                assert a.x + a.tiles <= b.x or b.x + b.tiles <= a.x or a.z + a.tiles <= b.z or b.z + b.tiles <= a.z
    near = min(nodes, key=lambda n: n.distance((10.0, 5.0, 10.0)))
    far = max(nodes, key=lambda n: n.distance((10.0, 5.0, 10.0)))
    assert near.level == terrainnodes.MIN_LEVEL and far.level > near.level
    assert nodes[0] is near  # nearest first


def test_the_budget_and_the_view_bound_the_nodes():
    size = 256.0
    eye = (128.0, 2.0, 128.0)
    assert len(terrainnodes.select(size, eye, budget=10)) <= 10
    left = terrainnodes.select(size, eye, visible=lambda n: n.x < 128)
    assert left and all(n.x < 128 for n in left)


def test_a_node_spans_its_samples_with_the_far_edge():
    n = Node(3, 8.0, 0.0)  # 8 tiles from x 8
    assert n.samples(0.5, 33) == (16, 0, 17, 17)
    assert Node(5, 0.0, 0.0).samples(0.5, 33) == (0, 0, 33, 33)  # clipped to the grid


def test_slices_are_reused_longest_unused_first_and_spoilt_by_a_box():
    cache = SliceCache(2)
    a, b, c = Node(2, 0.0, 0.0), Node(2, 4.0, 0.0), Node(2, 8.0, 0.0)
    assert (cache.get(a), cache.get(b)) == (0, 1)
    assert cache.fresh == {a, b}
    cache.baked(a)
    cache.baked(b)
    cache.get(a)  # a used again: b is the longest unused
    assert cache.get(c) == 1 and cache.dropped == [b] and cache.fresh == {c}
    assert cache.spoil((0.0, 0.0, 1.0, 1.0)) == [a] and a in cache.fresh


def _unpack(raw: bytes, n: int):
    """The vertices read back as ``vsTerrainInternal`` reads them (terrain-geometry.md 1.1)."""
    v = np.frombuffer(raw, np.uint8).reshape(n, 16)
    pos = v[:, :8].copy().view("<u2").astype(float)
    x, y, z = pos[:, 0] / 32, pos[:, 1] / 256 - 128, pos[:, 2] / 32
    u = pos[:, 3] / 65535
    vv = (v[:, 11] / 255 + v[:, 15] / 255 * 256) * 255 / 65535
    normal = v[:, [10, 9, 8]] / 255 * 2 - 1  # .bgra
    return x, y, z, u, vv, normal


def test_the_vertices_read_back_by_the_shaders_formulas():
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    step, w = 0.5, 17
    j, i = np.mgrid[0:w, 0:w]
    heights = (0.25 * i * step + 1.0).astype(np.float32)  # a slope up along x: 0.25 a tile
    node = Node(3, 0.0, 0.0)
    normals = terraindraw.vertex_normals(heights, step)
    raw = terraindraw.encode_vertices(heights, step, node, w, normals)
    x, y, z, u, v, normal = _unpack(raw, w * w)
    assert np.allclose(x, (i * step).ravel()) and np.allclose(z, (j * step).ravel())
    assert np.allclose(y, heights.ravel(), atol=1 / 256)
    assert np.allclose(u, x / 8, atol=1e-4) and np.allclose(v, z / 8, atol=1e-4)
    expected = np.array([-0.25, 1.0, 0.0]) / np.linalg.norm([-0.25, 1.0, 0.0])
    assert np.allclose(normal, expected, atol=0.01)
    assert len(terraindraw.grid_indices(w, w)) == (w - 1) ** 2 * 6


def test_the_normal_page_is_cell_centred_and_holds_x_and_z():
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    step = 0.5
    j, i = np.mgrid[0:5, 0:5]
    page = terraindraw.normal_page((0.5 * j * step).astype(np.float32), step)  # up along z by 0.5 a tile
    assert page.shape == (4, 4, 2)
    n = page / 255 * 2 - 1
    expected = np.array([0.0, -0.5]) / np.linalg.norm([0.0, 1.0, -0.5])
    assert np.allclose(n, expected, atol=0.01)


def test_the_island_matrix_turns_x_over_into_the_views_axes():
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    v = terraindraw.island_values(64.0)
    m0, m1 = np.array(v["cTrnIslandMatrix0"]), np.array(v["cTrnIslandMatrix1"])
    p = np.array([10.0, 3.0])  # IslandPositionToWS: dot(p.xz, m.xy) + m.z
    assert p @ m0[:2] + m0[2] == 54.0 and p @ m1[:2] + m1[2] == 3.0
    node = terraindraw.node_values(4, 7)
    assert node["cTrnBakedTexArrayIndex"] == 7 and node["cTrnPackedLevels"] == 4


def test_the_bake_sees_a_node_from_above_its_first_row_at_the_bottom():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgame, desktopgamebake

    m = desktopgamebake.bake_matrix(64.0, 8.0, 16.0, 4.0)
    corner = m @ np.array([64.0 - 8.0, 5.0, 16.0, 1.0])  # the node's corner (x 8, z 16) in the mirrored view
    far = m @ np.array([64.0 - 12.0, 5.0, 20.0, 1.0])
    assert np.allclose(corner[:2], [-1, -1]) and np.allclose(far[:2], [1, 1])
    lo, hi = desktopgame.block_ranges(np.arange(81, dtype=float).reshape(9, 9), 0.5, 2.0)
    assert lo.shape == (2, 2) and lo[0, 0] == 0 and hi[1, 1] == 80


def test_a_trimmed_stored_mesh_is_baked_over_its_own_rectangle():
    """A stored mesh at the island's edge holds only its part of the node, its u and v over that part (vanilla's
    edge nodes): its slice is baked over the same rectangle, else its far part read unbaked texels (round 12)."""
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgamebake
    from annotoolbox.render import terraindraw

    step, w = 0.5, 17
    heights = np.zeros((w, w), np.float32)
    part = Node(2, 4.0, 4.0)  # tiles 4..8 of a node of 16 tiles at the origin: what such a mesh holds
    raw = terraindraw.encode_vertices(heights, step, part, w, terraindraw.vertex_normals(heights, step))
    assert terraindraw.mesh_rect(raw) == (4.0, 4.0, 4.0, 4.0)
    m = desktopgamebake.bake_matrix(64.0, 2.0, 16.0, 8.0, 4.0)  # a rectangle of 8 x 4 tiles at (2, 16)
    corner, far = m @ np.array([62.0, 0.0, 16.0, 1.0]), m @ np.array([54.0, 0.0, 20.0, 1.0])
    assert np.allclose(corner[:2], [-1, -1]) and np.allclose(far[:2], [1, 1])


def test_the_blocks_height_ranges_are_those_of_their_samples_far_edges_included():
    """``block_ranges`` ran a Python loop over every block on the window's thread per height change (0.14 s a
    stroke on a continental island); the array form gives each block's samples as the loop took them."""
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgame

    rng = np.random.default_rng(3)
    for shape, tiles in (((33, 33), 4.0), ((30, 30), 4.0), ((65, 70), 16.0), ((2, 2), 4.0)):
        heights = rng.random(shape)
        lo, hi = desktopgame.block_ranges(heights, 0.5, tiles)
        per = round(tiles / 0.5)
        for j, i in np.ndindex(lo.shape):
            block = heights[j * per : j * per + per + 1, i * per : i * per + per + 1]
            assert (lo[j, i], hi[j, i]) == (block.min(), block.max())


def test_a_nodes_own_constants_are_written_once_not_every_frame():
    """A node's values were also ``GameModel.values``, which ``prepare`` writes every frame: 100 names a node, a
    fifth of a moving frame on a continental island."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import ConstantBuffer, Variable
    from annotoolbox.render import gameshader, terraindraw

    written = []

    class Counting(gameshader.Constants):
        def set(self, path, value):
            written.append(path)
            return super().set(path, value)

    alone = terraindraw.Owner(None)
    model = terraindraw.TerrainModel(SimpleNamespace(), SimpleNamespace(version=0), {"cX": 2.0}, alone)
    model.constants = {"cb": Counting(ConstantBuffer("cb", 16, [Variable("cX", 0, 4, "float", 1, 1)]))}
    model.buffers, model.keep, model.pipeline, model._uploaded = {"cb": object()}, [object()], object(), None
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: None)
    camera = gameshader.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    for _ in range(3):
        model.prepare(None, None, batch, camera)
    assert written.count("cX") == 1 and model.values == {}


def test_nodes_share_the_buffers_that_hold_none_of_their_values(monkeypatch):
    """A node made while the camera moves made a buffer for each of its program's constant buffers (about a
    millisecond each); those without node values are one per program for all nodes (``Common``)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import ConstantBuffer, Variable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    monkeypatch.setattr(gamedraw.GameModel, "_uniform", lambda self, name, size: SimpleNamespace(own=name))
    common = gameinstances.Common(None, {"cTrnIslandSize"})  # the island's values: every node's alike
    program = SimpleNamespace()
    models = []
    for level in (1, 2):
        values = {"cTrnPackedLevels": level, "cTrnIslandSize": 64.0}
        model = terraindraw.TerrainModel(program, None, values, terraindraw.Owner(None, common=common))
        model.constants = {
            "cbNode": gameshader.Constants(ConstantBuffer("cbNode", 16, [Variable("cTrnPackedLevels", 0, 4, "uint", 1, 1)])),
            "cbFrame": gameshader.Constants(ConstantBuffer("cbFrame", 16, [Variable("cTrnIslandSize", 0, 4, "float", 1, 1)])),
        }  # fmt: skip
        models.append({name: model._uniform(name, 16) for name in model.constants})
    assert models[0]["cbFrame"] is models[1]["cbFrame"] and models[0]["cbNode"] is not models[1]["cbNode"]


def test_a_camera_back_where_it_was_rewrites_the_nodes_shared_buffer(monkeypatch):
    """Each node compared a shared buffer with what it had sent itself: node A drawn at camera 1, node B alone at
    camera 2, then A alone at camera 1 again sent nothing, and drew with camera 2's values."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import ConstantBuffer, Variable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    held = {}  # what each buffer holds on the device
    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: held.__setitem__(id(buf), data))
    common = gameinstances.Common(None, set())
    program = SimpleNamespace()
    nodes = []
    for _ in range(2):
        node = terraindraw.TerrainModel(program, None, {}, terraindraw.Owner(None, common=common))
        node.constants = {"cbFrame": gameshader.Constants(ConstantBuffer("cbFrame", 16, [
            Variable("cCameraPosition", 0, 12, "float3", 1, 3)]))}  # fmt: skip
        node.buffers = {"cbFrame": node._uniform("cbFrame", 16)}
        node.keep, node.pipeline, node._uploaded = [object()], object(), None
        nodes.append(node)

    def camera(x):
        return gameshader.Camera(np.array([x, 0.0, 0.0]), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))

    monkeypatch.setattr(gameshader, "fill_frame", lambda c, cam: c.set("cCameraPosition", cam.eye))
    for node, eye in ((nodes[0], 1.0), (nodes[1], 2.0), (nodes[0], 1.0)):
        node.prepare(None, None, batch, camera(eye))
    shared = nodes[0].buffers["cbFrame"]
    assert np.frombuffer(held[id(shared)], np.float32)[0] == 1.0


def test_the_nodes_shared_buffers_are_filled_once_a_pass_and_their_own_values_sit_in_one_slotted_buffer(monkeypatch):
    """Every node filled and compared each shared buffer every frame, and made a uniform buffer of its own for
    ``cbTerrainNode``: the first node of a pass fills a shared one for its camera, the node values go to the node's
    slot of one buffer (island-117-view.md "Speed", part 11)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import ConstantBuffer, Variable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    sent = []  # (buffer, offset) of every write
    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: sent.append((buf, offset)))
    filled = []
    monkeypatch.setattr(gameshader, "fill_frame", lambda c, cam: filled.append(c.reflection.name))
    common, slots = gameinstances.Common(None, set()), terraindraw.NodeSlots(None, 4)
    slots.buffer = SimpleNamespace(name="slots")  # its binding needs a device
    program = SimpleNamespace(cbuffers={"cbTerrainNode": ConstantBuffer("cbTerrainNode", 16, [
        Variable("cTrnPackedLevels", 0, 4, "uint", 1, 1)])})  # fmt: skip
    nodes = []
    for layer in (1, 2):
        owner = terraindraw.Owner(None, {}, common, slots)
        node = terraindraw.TerrainModel(program, None, {"cTrnPackedLevels": layer}, owner, slot=layer)
        node.constants = {"cbFrame": gameshader.Constants(ConstantBuffer("cbFrame", 16, [
            Variable("cCameraPosition", 0, 12, "float3", 1, 3)])),
            "cbTerrainNode": gameshader.Constants(program.cbuffers["cbTerrainNode"])}  # fmt: skip
        node.buffers = {"cbFrame": node._uniform("cbFrame", 16)}
        node.slot_binding = 5
        node.keep, node.pipeline, node._uploaded = [object()], object(), None
        nodes.append(node)
    camera = gameshader.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    for _ in range(2):  # a still camera's second frame: nothing new
        for node in nodes:
            node.prepare(None, None, batch, camera)
    assert filled.count("cbFrame") == 1
    assert sorted(off for buf, off in sent if buf is slots.buffer) == [256, 512]  # each node's slot, once
    draws = []
    nodes[1].mesh = SimpleNamespace(draw=lambda *a: draws.append(a[-1]))
    nodes[1].srb = nodes[1].vbufs = nodes[1].ibuf = None
    nodes[1].draw(None, (4, 4))
    assert draws == [(5, 512)]


def test_a_tessellated_nodes_patches_take_their_corners_with_u_along_z():
    """The hull's clockwise triangles of a patch in (x0, z0), (x0, z1), (x1, z1), (x1, z0) faced down (found on
    1800's view, which culls; 117's view draws without culling, so it never showed): the corners go with u along z,
    the skirts' quads alike (island-1800-2205-view.md 8)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import terraindraw
    from annotoolbox.render.terrainnodes import Node

    detailed = SimpleNamespace()
    terrain = terraindraw.Terrain(None, SimpleNamespace(), 16.0, 1.0, 17, detailed=detailed)
    terrain.set_heights(np.zeros((17, 17), np.float32))
    model = terrain._make(Node(2, 4.0, 8.0), 0, ("detailed", None, 0))
    assert model.program is detailed
    xyz = np.frombuffer(model.mesh.data, np.uint8).reshape(-1, terraindraw.STRIDE)[:, 0:8].copy().view("<u2")
    xz = xyz[:, [0, 2]].astype(float) * terraindraw.UNIT
    first = model.mesh.indices.reshape(-1, 4)[0]
    assert np.allclose(xz[first], [[4, 8], [5, 8], [5, 9], [4, 9]])  # (x0, z0), (x1, z0), (x1, z1), (x0, z1)
    _data, plain = terraindraw.with_skirts(b"\0" * 16 * 25, 5, 5)
    assert np.array_equal(model.mesh.indices.reshape(-1, 4), plain.reshape(-1, 4)[:, terraindraw.PATCH_CORNERS])


def test_a_height_edit_marks_the_blocks_it_touches():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgame

    base = np.zeros((33, 33), np.float32)  # 16 tiles, 4 blocks of 4 tiles a side
    heights = base.copy()
    heights[20, 3] += 0.5  # z 10, x 1.5: the third block row, the first column
    edited = desktopgame.edited_blocks(heights, base, 0.5)
    assert edited.shape == (4, 4) and edited.sum() == 1 and edited[2, 0]


def test_the_look_is_busy_while_parts_within_reach_are_still_to_be_made_ahead():
    """The view said it was idle with parts within reach compiled but not made yet (``ahead_left``): a bench and
    the pictures took a frame that was still making them (island-117-view.md "Speed", part 11)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.editor.desktop import desktopgame

    game = object.__new__(desktopgame.GameIsland)
    game.r = SimpleNamespace(loader=SimpleNamespace(get=lambda key, job: ("compiled",)), game_sea=False)
    game.why_not = lambda: ""
    game._bake_stages, game.pages, game._unbaked = None, None, 0
    game.sea_waiting = game.inland_waiting = game._coast_edited = False
    game.objects = SimpleNamespace(waiting=0, ahead_left=3)
    game.decals = SimpleNamespace(waiting=0)
    assert game.busy() == 1
    game.objects.ahead_left = 0
    assert game.busy() == 0


def test_the_look_stays_busy_for_the_frame_that_bakes_the_last_nodes():
    """A new camera's few nodes baked in one frame left the view idle in that frame: a picture taken then showed
    their unbaked ground (island-1800-2205-view.md 13)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from unittest import mock

    from annotoolbox.editor.desktop import desktopgame

    game = object.__new__(desktopgame.GameIsland)
    cache = SimpleNamespace(fresh={1, 2}, slices={1: 0, 2: 1}, baked=lambda node: cache.fresh.discard(node))
    game.terrain = SimpleNamespace(cache=cache)
    game.pages, game._pending, game._baked_with = None, SimpleNamespace(weights=None), {}
    game.bake = mock.MagicMock()
    game.r = SimpleNamespace(ctx=None)
    game._bake_model = lambda node, page: node
    models = [SimpleNamespace(node=n, kind=("grid", None, None)) for n in (1, 2)]
    game._bake_game(models)
    assert game._unbaked == 2 and game.bake.run.call_args.args[1] == [(1, 0), (2, 1)]
    game._bake_game(models)  # nothing left: idle from the next frame on
    assert game._unbaked == 0


INSTALL_ISLAND = """
import json, sys, time
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.editor.desktop.desktoprender import Renderer, ViewOptions
from annotoolbox.editor.editsession import EditSession
from annotoolbox.render.camera import OrbitCamera
from annotoolbox.render.rhiview import RhiView
from annotoolbox.sources.anno117 import Anno117Files
from fakes import edit_island
app = QApplication([])

class Editor:
    files = Anno117Files(Path(sys.argv[1]))
    def __getattr__(self, name):  # what a view asks an editor for: not here (loaded in the background, said)
        def missing(*args, **kw):
            raise FileNotFoundError(name)
        return missing

s = EditSession(edit_island(objects=[], props=[], forest={"tiles": [], "models": []}))
s.finish_loading()

class View(RhiView):
    def ready(self, ctx):
        self.r = Renderer(ctx, s, Editor())
    def draw(self, ctx, screen, size):
        self.r.render(screen, size, OrbitCamera((8.0, 0.5, 8.0), 14.0), self.opts)

view = View("d3d12")
out = {}
for native in (False, True):
    view.opts = ViewOptions(native=native, game_light=True, layers=dict.fromkeys(("objects", "markers", "props",
                            "grass", "forest", "water"), False))
    start = time.monotonic()
    img = view.picture(160, 120)
    while native and (view.r.game is None or view.r.game.busy()) and time.monotonic() - start < 240:
        time.sleep(0.2)
        img = view.picture(160, 120)
    for _ in range(3):  # the bake and the uploads land over a frame or two
        img = view.picture(160, 120)
    out[native] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(160)] for y in range(120)], float)
game = view.r.game
print(json.dumps({"diff": float(np.abs(out[True] - out[False]).mean()), "std": float(out[True].std()),
                  "nodes": view.r.stats.get("terrain_nodes", 0), "why": game.why_not() if game else "none",
                  "errors": view.r.ctx.errors[-3:]}))
"""


def test_the_island_views_terrain_is_drawn_by_the_games_shaders():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_ISLAND.replace("sys.argv[1]", repr(root)))
    assert got["why"] == "" and not got["errors"], got
    assert got["nodes"] > 0 and got["std"] > 3 and got["diff"] > 0.5, got
