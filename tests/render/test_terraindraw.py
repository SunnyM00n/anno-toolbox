"""117's terrain by the game's terrain shader (``render.terraindraw``, ``terrainnodes``, the island view's
``desktopgame``): the engine's vertex format read back by the shader's own formulas, the normal page, the island's
place, the nodes a camera picks and the slices they keep; with an installed Anno 117 (``ANNO117_DIR``) and
Direct3D 12 the island view draws its terrain by the game's shaders (in a child process)."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.render import terrainnodes
from annotoolbox.render.terrainnodes import CSliceCache, SNode


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


def _walked(size, eye, budget: int) -> set:
    """The nodes the quadtree's walk one node at a time picked (``select`` before its array operations)."""
    import heapq

    def need(node) -> float:
        return node.tiles / max(node.distance(eye), 1e-6)

    top = terrainnodes.root(size)
    heap, done, count, serial = [(-need(top), 0, top)], [], 1, 1
    while heap:
        wanted, _serial, node = heapq.heappop(heap)
        kids = node.children(size) if node.level > terrainnodes.MIN_LEVEL and -wanted > 1.0 / terrainnodes.SPLIT else []
        if not kids or count + len(kids) - 1 > budget:
            done.append(node)
            continue
        count += len(kids) - 1
        for kid in kids:
            heapq.heappush(heap, (-need(kid), serial, kid))
            serial += 1
    return sorted(done, key=lambda node: node.distance(eye))


def test_the_nodes_picked_in_array_operations_are_those_of_the_walk_one_node_at_a_time():
    """Within the budget and past it (the nearest split first), eyes near, far, high and off the island, in the
    walk's order (equal distances too: the bake follows it), worked out in the eye's precision (the view's float32)."""
    rng = np.random.default_rng(7)
    for size in (96.0, 256.0, 960.0):
        eyes = [(size / 3, 25.0, size / 3), (size / 2, size, size / 2), (-size, 5.0, 2 * size)]
        eyes += [tuple(rng.uniform(-size, 2 * size, 3)) for _ in range(40)]
        eyes += [tuple(np.float32(v) for v in np.round(rng.uniform(-size, 2 * size, 3) / 16) * 16) for _ in range(40)]
        for eye in eyes:
            for budget in (128, 40, 7):
                got = terrainnodes.select(size, eye, budget)
                assert got == _walked(size, eye, budget) and len(got) <= max(budget, 1)


def test_the_nodes_boxes_are_the_height_range_of_the_blocks_they_cover():
    """``CNodeBoxes.bounds`` of many nodes at once: the lowest and highest block under each, with the box's play."""
    rng = np.random.default_rng(3)
    low = rng.uniform(-5, 5, (13, 11))  # an odd count of blocks: the pyramid fills out its edge
    high = low + rng.uniform(0, 4, low.shape)
    boxes = terrainnodes.CNodeBoxes(low, high, 44.0)
    nodes = []
    for level in range(terrainnodes.MIN_LEVEL, 7):
        side = 2**level
        nodes += [(level, x, z) for x in range(0, 44, side) for z in range(0, 52, side)]
    got = boxes.bounds(np.array(nodes, float))
    for (level, x, z), (bottom, top) in zip(nodes, got.tolist(), strict=True):
        rows, columns = slice(z // 4, (z + 2**level) // 4), slice(x // 4, (x + 2**level) // 4)
        assert bottom == pytest.approx(low[rows, columns].min() - boxes.BELOW)
        assert top == pytest.approx(high[rows, columns].max() + boxes.ABOVE)


def test_a_node_spans_its_samples_with_the_far_edge():
    n = SNode(3, 8.0, 0.0)  # 8 tiles from x 8
    assert n.samples(0.5, 33) == (16, 0, 17, 17)
    assert SNode(5, 0.0, 0.0).samples(0.5, 33) == (0, 0, 33, 33)  # clipped to the grid


def test_slices_are_reused_longest_unused_first_and_spoilt_by_a_box():
    cache = CSliceCache(2)
    a, b, c = SNode(2, 0.0, 0.0), SNode(2, 4.0, 0.0), SNode(2, 8.0, 0.0)
    assert (cache.get(a), cache.get(b)) == (0, 1)
    assert cache.m_fresh == {a, b}
    cache.baked(a)
    cache.baked(b)
    cache.get(a)  # a used again: b is the longest unused
    assert cache.get(c) == 1 and cache.m_dropped == [b] and cache.m_fresh == {c}
    assert cache.spoil((0.0, 0.0, 1.0, 1.0)) == [a] and a in cache.m_fresh


def test_a_split_node_draws_until_all_its_quarters_are_baked():
    """Zooming in: the larger node drawn before stands in for its quarters until every one is baked (an unbaked slice
    drew black or another node's ground for a few frames)."""
    parent, other = SNode(3, 0.0, 0.0), SNode(3, 8.0, 0.0)
    kids = parent.children(16.0)
    baked = {parent, other, kids[0]}
    wanted = [*kids, other]
    assert terrainnodes.stand_ins(wanted, baked.__contains__, {parent, other}, 16.0) == [parent, other]
    baked.update(kids)
    assert terrainnodes.stand_ins(wanted, baked.__contains__, {parent, other}, 16.0) == wanted


def test_a_merged_node_draws_as_its_parts_until_it_is_baked():
    """Zooming out: the smaller nodes drawn before stand in for the larger one where they cover its part in view;
    where they do not (nothing drawn before, a part in view not covered) it draws itself."""
    parent = SNode(3, 0.0, 0.0)
    kids = parent.children(16.0)
    baked = set(kids)
    assert set(terrainnodes.stand_ins([parent], baked.__contains__, set(kids), 16.0)) == set(kids)
    assert terrainnodes.stand_ins([parent], baked.__contains__, set(), 16.0) == [parent]
    assert terrainnodes.stand_ins([parent], baked.__contains__, set(kids[:3]), 16.0) == [parent]
    hidden = kids[3]  # out of view: not needed
    shown = terrainnodes.stand_ins([parent], baked.__contains__, set(kids[:3]), 16.0, lambda n: n != hidden)
    assert set(shown) == set(kids[:3])


def test_the_terrain_shows_the_nodes_drawn_before_while_the_new_ones_bake():
    """``CTerrain.shown``: a node is ready once its slice is baked and its page is still the one on the device; the
    nodes standing in are kept in the slice cache."""
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    from annotoolbox.render.terraindraw import CTerrain

    parent = SNode(3, 0.0, 0.0)
    kids = parent.children(16.0)
    page = object()
    t = object.__new__(CTerrain)
    t.m_size_tiles, t._m_visible, t.m_cache = 16.0, None, CSliceCache(8)
    t.m_pages = SimpleNamespace(m_ready={(0, 0, 0): page})
    t.m_models = {n: SimpleNamespace(m_node=n, m_kind=("grid", None, 0)) for n in (parent, *kids)}
    t.m_models[parent].m_kind = ("detailed", (0, 0, 0), page)  # the entry itself: its id could be given again
    for n in (parent, *kids):
        t.m_cache.get(n)
    t.m_cache.baked(parent)
    t._m_drawn = {parent}
    wanted = [t.m_models[k] for k in kids]
    assert t.shown(wanted) == [t.m_models[parent]] and next(reversed(t.m_cache.m_slices)) == parent
    t.m_pages.m_ready = {(0, 0, 0): object()}  # its page was made again: it no longer stands in
    assert t.shown(wanted) == wanted


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
    node = SNode(3, 0.0, 0.0)
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
    part = SNode(2, 4.0, 4.0)  # tiles 4..8 of a node of 16 tiles at the origin: what such a mesh holds
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
    """A node's values were also ``CGameModel.values``, which ``prepare`` writes every frame: 100 names a node, a
    fifth of a moving frame on a continental island."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gameshader, terraindraw

    written = []

    class FakeCounting(gameshader.CConstants):
        def set(self, path, value):
            written.append(path)
            return super().set(path, value)

    alone = terraindraw.SOwner(None)
    model = terraindraw.CTerrainModel(SimpleNamespace(), SimpleNamespace(version=0), {"cX": 2.0}, alone)
    model.m_constants = {"cb": FakeCounting(SConstantBuffer("cb", 16, [SVariable("cX", 0, 4, "float", 1, 1)]))}
    model.m_buffers, model.m_keep, model.m_pipeline, model._m_uploaded = {"cb": object()}, [object()], object(), None
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: None)
    camera = gameshader.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    for _ in range(3):
        model.prepare(None, None, batch, camera)
    assert written.count("cX") == 1 and model.m_values == {}


def test_nodes_share_the_buffers_that_hold_none_of_their_values(monkeypatch):
    """A node made while the camera moves made a buffer for each of its program's constant buffers (about a
    millisecond each); those without node values are one per program for all nodes (``CCommon``)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    monkeypatch.setattr(gamedraw.CGameModel, "_uniform", lambda self, name, size: SimpleNamespace(own=name))
    common = gameinstances.CCommon(None, {"cTrnIslandSize"})  # the island's values: every node's alike
    program = SimpleNamespace()
    models = []
    for level in (1, 2):
        values = {"cTrnPackedLevels": level, "cTrnIslandSize": 64.0}
        model = terraindraw.CTerrainModel(program, None, values, terraindraw.SOwner(None, common=common))
        model.m_constants = {
            "cbNode": gameshader.CConstants(SConstantBuffer("cbNode", 16, [SVariable("cTrnPackedLevels", 0, 4, "uint", 1, 1)])),
            "cbFrame": gameshader.CConstants(SConstantBuffer("cbFrame", 16, [SVariable("cTrnIslandSize", 0, 4, "float", 1, 1)])),
        }  # fmt: skip
        models.append({name: model._uniform(name, 16) for name in model.m_constants})
    assert models[0]["cbFrame"] is models[1]["cbFrame"] and models[0]["cbNode"] is not models[1]["cbNode"]


def test_a_camera_back_where_it_was_rewrites_the_nodes_shared_buffer(monkeypatch):
    """Each node compared a shared buffer with what it had sent itself: node A drawn at camera 1, node B alone at
    camera 2, then A alone at camera 1 again sent nothing, and drew with camera 2's values."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    held = {}  # what each buffer holds on the device
    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: held.__setitem__(id(buf), data))
    common = gameinstances.CCommon(None, set())
    program = SimpleNamespace()
    nodes = []
    for _ in range(2):
        node = terraindraw.CTerrainModel(program, None, {}, terraindraw.SOwner(None, common=common))
        node.m_constants = {"cbFrame": gameshader.CConstants(SConstantBuffer("cbFrame", 16, [
            SVariable("cCameraPosition", 0, 12, "float3", 1, 3)]))}  # fmt: skip
        node.m_buffers = {"cbFrame": node._uniform("cbFrame", 16)}
        node.m_keep, node.m_pipeline, node._m_uploaded = [object()], object(), None
        nodes.append(node)

    def camera(x):
        return gameshader.SCamera(np.array([x, 0.0, 0.0]), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))

    monkeypatch.setattr(gameshader, "fill_frame", lambda c, camera: c.set("cCameraPosition", camera.eye))
    for node, eye in ((nodes[0], 1.0), (nodes[1], 2.0), (nodes[0], 1.0)):
        node.prepare(None, None, batch, camera(eye))
    shared = nodes[0].m_buffers["cbFrame"]
    assert np.frombuffer(held[id(shared)], np.float32)[0] == 1.0


def test_the_nodes_shared_buffers_are_filled_once_a_pass_and_their_own_values_sit_in_one_slotted_buffer(monkeypatch):
    """Every node filled and compared each shared buffer every frame, and made a uniform buffer of its own for
    ``cbTerrainNode``: the first node of a pass fills a shared one for its camera, the node values go to the node's
    slot of one buffer (island-117-view.md "Speed", part 11)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gamedraw, gameinstances, gameshader, terraindraw

    sent = []  # (buffer, offset) of every write
    monkeypatch.setattr(gamedraw, "uniform_buffer", lambda rhi, size: SimpleNamespace(size=size))
    batch = SimpleNamespace(updateDynamicBuffer=lambda buf, offset, data: sent.append((buf, offset)))
    filled = []
    monkeypatch.setattr(gameshader, "fill_frame", lambda c, camera: filled.append(c.m_reflection.name))
    common, slots = gameinstances.CCommon(None, set()), terraindraw.CNodeSlots(None, 4)
    slots.m_buffer = SimpleNamespace(name="slots")  # its binding needs a device
    program = SimpleNamespace(cbuffers={"cbTerrainNode": SConstantBuffer("cbTerrainNode", 16, [
        SVariable("cTrnPackedLevels", 0, 4, "uint", 1, 1)])})  # fmt: skip
    nodes = []
    for layer in (1, 2):
        owner = terraindraw.SOwner(None, {}, common, slots)
        node = terraindraw.CTerrainModel(program, None, {"cTrnPackedLevels": layer}, owner, slot=layer)
        node.m_constants = {"cbFrame": gameshader.CConstants(SConstantBuffer("cbFrame", 16, [
            SVariable("cCameraPosition", 0, 12, "float3", 1, 3)])),
            "cbTerrainNode": gameshader.CConstants(program.cbuffers["cbTerrainNode"])}  # fmt: skip
        node.m_buffers = {"cbFrame": node._uniform("cbFrame", 16)}
        node.m_slot_binding = 5
        node.m_keep, node.m_pipeline, node._m_uploaded = [object()], object(), None
        nodes.append(node)
    camera = gameshader.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    for _ in range(2):  # a still camera's second frame: nothing new
        for node in nodes:
            node.prepare(None, None, batch, camera)
    assert filled.count("cbFrame") == 1
    assert sorted(off for buf, off in sent if buf is slots.m_buffer) == [256, 512]  # each node's slot, once
    draws = []
    nodes[1].m_mesh = SimpleNamespace(draw=lambda *a: draws.append(a[-1]))
    nodes[1].m_srb = nodes[1].m_vbufs = nodes[1].m_ibuf = None
    nodes[1].draw(None, (4, 4))
    assert draws == [(5, 512)]


def test_a_tessellated_nodes_patches_take_their_corners_with_u_along_z():
    """The hull's clockwise triangles of a patch in (x0, z0), (x0, z1), (x1, z1), (x1, z0) faced down (found on
    1800's view, which culls; 117's view draws without culling, so it never showed): the corners go with u along z,
    the skirts' quads alike (island-1800-2205-view.md 8)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import terraindraw
    from annotoolbox.render.terrainnodes import SNode

    detailed = SimpleNamespace()
    terrain = terraindraw.CTerrain(None, SimpleNamespace(), 16.0, 1.0, 17, detailed=detailed)
    terrain.set_heights(np.zeros((17, 17), np.float32))
    model = terrain._make(SNode(2, 4.0, 8.0), 0, ("detailed", None, 0))
    assert model.m_program is detailed
    xyz = np.frombuffer(model.m_mesh.m_data, np.uint8).reshape(-1, terraindraw.STRIDE)[:, 0:8].copy().view("<u2")
    xz = xyz[:, [0, 2]].astype(float) * terraindraw.UNIT
    first = model.m_mesh.m_indices.reshape(-1, 4)[0]
    assert np.allclose(xz[first], [[4, 8], [5, 8], [5, 9], [4, 9]])  # (x0, z0), (x1, z0), (x1, z1), (x0, z1)
    _data, plain = terraindraw.with_skirts(b"\0" * 16 * 25, 5, 5)
    assert np.array_equal(model.m_mesh.m_indices.reshape(-1, 4), plain.reshape(-1, 4)[:, terraindraw.PATCH_CORNERS])


def test_a_height_edit_remakes_117s_normals_and_page_only_where_it_reached():
    """Each frame of a stroke made the whole island's normal page and vertex normals again and uploaded the whole page
    (24 to 58 ms a frame, performance.md 6, item 4): the part the edit reached is made, the same as a whole remake."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import terraindraw

    rng = np.random.default_rng(5)
    terrain = terraindraw.CTerrain(None, SimpleNamespace(), 16.0, 0.5, 33)
    heights = rng.random((33, 33))
    terrain.set_heights(heights)
    terrain.normals()
    terrain.page_region()
    heights = heights.copy()
    heights[8:13, 4:9] += 1.0  # tiles x 2 to 4, z 4 to 6
    terrain.set_heights(heights, (2.0, 4.0, 4.0, 6.0))
    assert np.array_equal(terrain.normals(), terraindraw.vertex_normals(heights, 0.5))
    texels, (x, y) = terrain.page_region()
    whole = terraindraw.normal_page(heights, 0.5)
    assert texels.shape[0] < whole.shape[0] and texels.shape[1] < whole.shape[1]
    assert np.array_equal(texels, whole[y : y + texels.shape[0], x : x + texels.shape[1]])
    assert np.array_equal(terrain._m_page.m_value, whole)


@pytest.mark.parametrize(("make_name", "cells"), [("vertex_normals", False), ("normal_page", True)])
def test_an_edit_remakes_the_normals_and_the_page_only_where_it_reached(make_name, cells):
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    make = getattr(terraindraw, make_name)
    rng = np.random.default_rng(3)
    h = rng.random((41, 41))
    part = terraindraw.CRegional(make, cells)
    part.get(h, 0.5)
    for box in ((3.0, 4.0, 6.5, 9.0), (0.0, 0.0, 1.0, 2.0), (17.0, 18.0, 20.0, 20.0)):  # tiles, inner and edges
        rows = terraindraw.sample_span(box[1], box[3], 0.5, 41)
        cols = terraindraw.sample_span(box[0], box[2], 0.5, 41)
        h = h.copy()
        h[rows[0] : rows[1], cols[0] : cols[1]] += rng.random((rows[1] - rows[0], cols[1] - cols[0]))
        part.spoil((*rows, *cols))
        assert np.array_equal(part.get(h, 0.5), make(h, 0.5)), box
    part.spoil(None)  # another grid: whole
    h = rng.random((21, 21))
    assert np.array_equal(part.get(h, 0.5), make(h, 0.5)) and part.m_region is None


def test_two_edits_before_a_get_remake_one_box_over_both():
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    rng = np.random.default_rng(8)
    h = rng.random((33, 33))
    part = terraindraw.CRegional(terraindraw.normal_page, cells=True)
    part.get(h, 0.5)
    h = h.copy()
    h[4:7, 4:7] += 1.0
    h[20:23, 10:12] += 1.0
    part.spoil((4, 7, 4, 7))
    part.spoil((20, 23, 10, 12))
    assert np.array_equal(part.get(h, 0.5), terraindraw.normal_page(h, 0.5))
    assert part.m_region == ((3, 23), (3, 12))  # the cells that read a changed sample, one region over both


@pytest.mark.parametrize("cells", [False, True])
def test_an_edit_box_past_the_heights_changes_nothing(cells):
    """A box that reaches no height sample (past the island's edge, or empty) left a remake of nothing pending: the
    vertex normals' gradient of no samples raised, and so did the page's copy of a slice of negative size."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    make = terraindraw.normal_page if cells else terraindraw.vertex_normals
    h = np.random.default_rng(4).random((33, 33))
    part = terraindraw.CRegional(make, cells)
    part.get(h, 0.5)
    for box in ((17.0, 17.0, 16.0, 16.0), (-3.0, -3.0, -1.0, -1.0), (2.0, 20.0, 4.0, 18.0)):
        rows = terraindraw.sample_span(box[1], box[3], 0.5, 33)
        cols = terraindraw.sample_span(box[0], box[2], 0.5, 33)
        part.spoil((*rows, *cols))
        assert np.array_equal(part.get(h, 0.5), make(h, 0.5)), box


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

    game = object.__new__(desktopgame.CGameIsland)
    game.m_renderer = SimpleNamespace(m_loader=SimpleNamespace(get=lambda key, job: ("compiled",)), m_game_sea=False)
    game.why_not = lambda: ""
    game._m_bake_stages, game.m_pages, game._m_unbaked = None, None, 0
    game.m_sea_waiting = game.m_inland_waiting = game._m_coast_edited = False
    game.m_objects = SimpleNamespace(m_waiting=0, m_ahead_left=3)
    game.m_decals = SimpleNamespace(m_waiting=0)
    game.m_native_shading = SimpleNamespace(m_sky=SimpleNamespace(is_running=False))
    assert game.busy() == 1
    game.m_objects.m_ahead_left = 0
    assert game.busy() == 0
    game.m_native_shading.m_sky.is_running = True  # the sky's chain, run over frames (performance.md 6, item 2)
    assert game.busy() == 1


def test_the_look_stays_busy_for_the_frame_that_bakes_the_last_nodes():
    """A new camera's few nodes baked in one frame left the view idle in that frame: a picture taken then showed
    their unbaked ground (island-1800-2205-view.md 13)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from unittest import mock

    from annotoolbox.editor.desktop import desktopgame

    game = object.__new__(desktopgame.CGameIsland)
    cache = SimpleNamespace(m_fresh={1, 2}, m_slices={1: 0, 2: 1}, baked=lambda node: cache.m_fresh.discard(node))
    game.m_terrain = SimpleNamespace(m_cache=cache)
    game.m_pages, game._m_pending, game._m_baked_with = None, SimpleNamespace(weights=None), {}
    game.m_bake = mock.MagicMock()
    game.m_renderer = SimpleNamespace(m_ctx=None)
    game._bake_model = lambda node, page: node
    models = [SimpleNamespace(m_node=n, m_kind=("grid", None, None)) for n in (1, 2)]
    game._bake_game(models)
    assert game._m_unbaked == 2 and game.m_bake.run.call_args.args[1] == [(1, 0), (2, 1)]
    game._bake_game(models)  # nothing left: idle from the next frame on
    assert game._m_unbaked == 0


def test_cascades_fitted_for_another_camera_keep_the_view_drawing():
    """The casters are culled by cascades read back a frame or more late: after a zoom the view stopped on a frame
    culled by the old camera's (shadows wrong until the next rotation). It is late until a read for this camera."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtCore import QByteArray

    from annotoolbox.editor.desktop import desktopgame
    from annotoolbox.render import gameshadow

    game = object.__new__(desktopgame.CGameIsland)
    game.m_fit = SimpleNamespace(m_error="", m_readbacks=[], m_read_back=False)
    game.m_native_shading = None  # the current path's fit
    game._m_cascades = game._m_cascades_for = game._m_asked_for = None
    near, far = SimpleNamespace(view_proj=np.eye(4)), SimpleNamespace(view_proj=np.eye(4) * 2)
    game._seen_cascades(near, (8, 8))
    assert game._m_late and game.m_fit.m_read_back  # nothing read yet: asked for this camera
    raw = bytes(gameshadow.INFO_BYTES * gameshadow.COUNT)
    game.m_fit.m_readbacks = [None, SimpleNamespace(data=QByteArray(raw))]  # the read arrives after that frame
    game._seen_cascades(near, (8, 8))
    assert not game._m_late and game._m_cascades is not None
    game._seen_cascades(far, (8, 8))  # zoomed: fitted for the camera before
    assert game._m_late


INSTALL_ISLAND = """
import json, sys, time
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.editor.desktop.desktoprender import CRenderer, SViewOptions
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.render.camera import COrbitCamera
from annotoolbox.render.rhiview import CRhiView
from annotoolbox.sources.anno117 import CAnno117Files
from tests.editor.editorfakes import edit_island
app = QApplication([])

class Editor:
    m_files = CAnno117Files(Path(sys.argv[1]))
    def __getattr__(self, name):  # what a view asks an editor for: not here (loaded in the background, said)
        def missing(*args, **kw):
            raise FileNotFoundError(name)
        return missing

s = CEditSession(edit_island(objects=[], props=[], forest={"tiles": [], "models": []}))
s.finish_loading()

class View(CRhiView):
    def ready(self, ctx):
        self.r = CRenderer(ctx, s, Editor())
    def draw(self, ctx, screen, size_px):
        self.r.render(screen, size_px, COrbitCamera((8.0, 0.5, 8.0), 14.0), self.opts)

view = View("d3d12")
out = {}
for native in (False, True):
    view.opts = SViewOptions(native=native, game_light=True, layers=dict.fromkeys(("objects", "markers", "props",
                            "grass", "forest", "water"), False))
    start = time.monotonic()
    img = view.picture(160, 120)
    while native and (view.r.m_game is None or view.r.m_game.busy()) and time.monotonic() - start < 240:
        time.sleep(0.2)
        img = view.picture(160, 120)
    for _ in range(3):  # the bake and the uploads land over a frame or two
        img = view.picture(160, 120)
    out[native] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(160)] for y in range(120)], float)
game = view.r.m_game
print(json.dumps({"diff": float(np.abs(out[True] - out[False]).mean()), "std": float(out[True].std()),
                  "nodes": view.r.m_stats.get("terrain_nodes", 0), "why": game.why_not() if game else "none",
                  "errors": view.r.m_ctx.m_errors[-3:]}))
"""


def test_the_island_views_terrain_is_drawn_by_the_games_shaders():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_ISLAND.replace("sys.argv[1]", repr(root)))
    assert got["why"] == "" and not got["errors"], got
    assert got["nodes"] > 0 and got["std"] > 3 and got["diff"] > 0.5, got
