"""The view's culling and cell tables (``render.cull``, ``editor.desktop.desktopbatches``)."""

import numpy as np
import pytest

from annotoolbox.render import camera, cull


def _planes(target=(10.0, 0.0, 10.0), distance=20.0):
    cam = camera.COrbitCamera(target, distance)
    return cull.frustum_planes(cam.proj(1.5) @ cam.view())


def test_boxes_behind_or_far_to_the_side_of_the_camera_are_outside_the_frustum():
    planes = _planes()
    here = np.array([[9.0, -1.0, 9.0]]), np.array([[11.0, 1.0, 11.0]])  # round the pivot the camera looks at
    far = np.array([[900.0, 0.0, 900.0]]), np.array([[901.0, 1.0, 901.0]])
    assert cull.boxes_in_view(planes, *here).all()
    assert not cull.boxes_in_view(planes, *far).any()
    big = np.array([[-5000.0, -50.0, -5000.0]]), np.array([[5000.0, 50.0, 5000.0]])  # one box holding the camera
    assert cull.boxes_in_view(planes, *big).all()


def test_the_terrain_chunks_cover_every_quad_once_and_follow_the_camera():
    w, step = 65, 1.0
    chunks = cull.CTerrainChunks(w, step)
    quads = (w - 1) ** 2
    assert len(chunks.m_indices) == quads * 6 and chunks.m_counts.sum() == quads * 6
    assert len(np.unique(chunks.m_indices.reshape(-1, 6)[:, 0])) == quads  # each quad's first corner, once
    chunks.set_heights(np.zeros((w, w), "f4"))
    everything = _planes((32.0, 0.0, 32.0), 400.0)
    assert chunks.runs(everything) == [(0, quads * 6)]  # all in view: neighbours join into one run
    close = chunks.runs(_planes((8.0, 0.0, 8.0), 6.0))
    drawn = sum(n for _, n in close)
    assert 0 < drawn < quads * 6 / 2
    assert chunks.runs(_planes((900.0, 0.0, 900.0), 6.0)) == []


def test_instance_cells_gather_what_is_in_view_and_keep_the_rest_out():
    rng = np.random.default_rng(1)
    n = 500
    mats = np.tile(np.identity(4, "f4"), (n, 1, 1))
    mats[:, :3, 3] = rng.uniform(0, 200, (n, 3)) * [1, 0, 1]
    cells = cull.CInstanceCells(mats)
    assert cells.m_counts.sum() == n and len(cells.m_counts) > 4
    assert cells.gather(np.ones(len(cells.m_counts), bool)).shape == (n, 16)
    planes = _planes((20.0, 0.0, 20.0), 30.0)
    seen = cells.visible(planes, 1.0)
    rows = cells.gather(seen)
    assert 0 < len(rows) < n
    # every instance inside the frustum is among the gathered ones (the cells only ever over-include)
    inside = cull.boxes_in_view(planes, mats[:, :3, 3], mats[:, :3, 3])
    gathered = {tuple(r[12:15]) for r in rows}  # the translation, column-major
    assert all(tuple(m[:3, 3]) in gathered for m in mats[inside])
    assert not np.isfinite(cull.reach(None)) and cells.visible(planes, float("inf")).all()


def _row(n: int, spacing: float) -> np.ndarray:
    """Instances in a row going away from an eye at the origin that looks down -z."""
    mats = np.tile(np.identity(4, "f4"), (n, 1, 1))
    mats[:, 2, 3] = -5.0 - np.arange(n) * spacing
    return mats


def test_the_cell_table_codes_every_batch_by_view_and_level_of_detail():
    """117's rule per cell over all batches at once: near cells fine, far ones coarse or too small, out of view -2,
    a batch's level at most its last, a batch of unknown size always at its finest."""
    near_row = cull.CInstanceCells(_row(200, 10.0))  # 5..2000 units ahead, a cell each 32
    unknown = cull.CInstanceCells(_row(3, 1.0))
    table = cull.CCellTable([(near_row, 2.0, 5), (near_row, 2.0, 2), (unknown, float("inf"), 5)])
    proj = camera.perspective(np.radians(45), 1.0, 0.1, 5000.0)
    lod = cull.SLodCamera(np.zeros(3), np.identity(4), proj, (1000, 1000))
    everything = cull.frustum_planes(np.diag([1e-4, 1e-4, 1e-4, 1.0]))  # a box that holds the whole row
    codes = table.codes(everything, lod)
    fine, capped, always = table.of(codes, 0), table.of(codes, 1), table.of(codes, 2)
    nearest_first = np.argsort(-near_row.m_max_box[:, 2])
    fine_by_distance = fine[nearest_first]  # near, coarser further out, too small at 2 km
    assert fine_by_distance[0] == 0 and fine_by_distance.max() >= 2 and fine_by_distance[-1] == -1
    assert (np.diff(np.where(fine_by_distance < 0, 9, fine_by_distance)) >= 0).all()
    assert capped.max() <= 1 and (capped[fine == -1] == -1).all()
    assert (always == 0).all()
    assert (table.codes(everything, None)[: len(fine)] == 0).all()  # no camera: every cell in view, finest


def test_the_frames_cell_table_follows_new_cells_even_where_their_id_comes_back(monkeypatch):
    """A batch's cells made again (its objects moved) make the frame's table again, even when the new cells get the
    old ones' id (Python hands a freed object's address on: the table kept the old places, culling moved objects
    by where they were)."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.editor.desktop import desktoprender

    monkeypatch.setattr(desktoprender, "id", lambda _obj: 1, raising=False)  # every object the same id
    batch = SimpleNamespace(m_cells=cull.CInstanceCells(_row(3, 1.0)), m_reach=1.0, m_levels=1, m_sets={"model": None})
    stub = SimpleNamespace(_all_batches=lambda: [batch], _m_table=None, _m_frame_codes={})
    ahead = cull.frustum_planes(camera.perspective(np.radians(45), 1.0, 0.1, 100.0))
    _table, _index, codes = desktoprender.CRenderer._pass_codes(stub, "model", ahead, None)
    assert (codes == 0).all()
    behind = _row(3, 1.0)
    behind[:, 2, 3] *= -1  # the objects moved behind the eye
    batch.m_cells, stub._m_frame_codes = cull.CInstanceCells(behind), {}
    _table, _index, codes = desktoprender.CRenderer._pass_codes(stub, "model", ahead, None)
    assert (codes == -2).all()


def test_a_batch_made_again_lets_its_old_vaos_go():
    """The VAOs of a batch whose model, levels or count changed are released with its instance buffers (they
    stayed on the device for good: each move of an object that changed a count left a set behind)."""
    from annotoolbox.editor.desktop.desktopbatches import CBatch

    released = []

    class FakeThing:
        def release(self):
            released.append(self)

    batch = CBatch(("props", "a.cfg"), [])
    vaos = {"pick": FakeThing(), "model": [[FakeThing()], [FakeThing(), FakeThing()]], "depth": [[FakeThing()], []]}
    batch.m_vaos, batch.m_sets = [({}, vaos)], {"model": FakeThing()}
    batch.release_draws()
    assert len(released) == 6 and batch.m_vaos == [] and batch.m_sets == {}


def test_instance_sets_write_each_level_once_its_codes_change():
    from annotoolbox.editor.desktop.desktopbatches import CInstanceSets, level_meshes

    written = []

    class FakeBuffer:
        def write(self, data):
            written.append(len(data))

        def release(self):
            pass

    ctx = type("Ctx", (), {"buffer": lambda self, reserve=0, dynamic=False: FakeBuffer()})()
    cells = cull.CInstanceCells(_row(200, 10.0))
    sets = CInstanceSets(ctx, 200, 3)
    codes = np.array([0, 1, 2, 7, -1, -2] + [0] * (len(cells.m_counts) - 6))
    total = sets.update(cells, codes)
    assert sets.m_counts[2] == int(cells.m_counts[2] + cells.m_counts[3])  # past the last level: drawn at the last
    assert total == sum(sets.m_counts) == cells.m_counts.sum() - cells.m_counts[4] - cells.m_counts[5]
    before = len(written)
    assert sets.update(cells, codes) == total and len(written) == before  # the same codes: nothing written
    # a part with coarser levels draws them from its first mesh; a part without draws its own at every level
    a, b = {"id": "a"}, {"id": "b"}
    parts = [(a, "a0"), (a, "a1"), (b, "b0")]
    per = level_meshes(parts, {id(a): [["a-l1"], ["a-l2x", "a-l2y"]]}, 4)
    assert per[0] == [["a0"], ["a-l1"], ["a-l2x", "a-l2y"], ["a-l2x", "a-l2y"]]
    assert per[1] == [["a1"], [], [], []] and per[2] == [["b0"]] * 4


def test_the_level_of_detail_follows_the_games_screen_size_rule():
    rules = cull.SLodRules(bias=1.0, min_pixels=4.0)
    proj = np.diag([1.0, 1.0, -1.0, 0.0])  # a 90-degree view: the screen area is 4 r^2 / d^2
    distances = np.array([0.5, 3.0, 30.0, 60.0, 120.0, 400.0, 4000.0])
    centres = np.c_[np.zeros((7, 2)), -distances]
    levels = cull.lod_levels(centres, np.ones(7), np.identity(4), proj, (1000, 1000), rules, 5)
    # inside the sphere: the whole screen; then areas 0.44, 4.4e-3, 1.1e-3, 2.8e-4 against 0.25 .. 0.0045
    assert levels.tolist() == [0, 0, 4, 4, 4, -1, -1]  # 6 and 0.06 pixels: too small
    near = cull.lod_levels(centres[:1] * 0 + [0, 0, -4.0], np.array([0.1]), np.identity(4), proj,
                                    (1000, 1000), rules, 5)  # fmt: skip
    assert near[0] == 0  # small on screen, but within LODMinDistances' 5: no coarser level
    assert cull.lod_levels(centres, np.ones(7), np.identity(4), proj, (1000, 1000), rules, 2).max() == 1


def test_a_sphere_is_drawn_up_to_the_distance_it_can_be_drawn_at_in_every_direction():
    """``drawn_within`` (what the island view compiles and makes ahead by, island-117-view.md "Speed", part 9) is
    never nearer than where ``lod_levels`` still draws a sphere: in the middle of the picture and at its corner."""
    rules = cull.SLodRules(min_pixels=12.0)
    proj = camera.perspective(np.radians(45), 16 / 9, 0.1, 1e5)
    size, radius = (1600, 900), np.array([1.5])
    reach = float(cull.drawn_within(radius, proj, size, rules)[0])
    t = np.array([1 / proj[0, 0], 1 / proj[1, 1], -1.0])  # the picture's corner, a depth of 1 ahead
    corner, middle = t / np.linalg.norm(t), np.array([0.0, 0.0, -1.0])

    def drawn(way, distance) -> bool:
        return cull.lod_levels(way[None] * distance, radius, np.identity(4), proj, size, rules, 5)[0] >= 0

    assert drawn(corner * 0.999, reach * 0.99) and not drawn(corner * 0.999, reach * 1.01)
    assert not drawn(middle, reach * 1.01) and drawn(middle, reach / np.linalg.norm(t) * 0.99)
    assert cull.drawn_within(radius, proj, size, cull.SLodRules(far=50.0))[0] == 51.5  # the quality's far plane


def test_the_lod_rules_are_read_from_the_engine_settings():
    text = ("<LODAreas><LOD0>0.5</LOD0><LOD1>0.1</LOD1><LOD2>0.01</LOD2><LOD3>0.001</LOD3><LOD4>0</LOD4></LODAreas>"
            "<LODMinDistances><LOD0>0</LOD0><LOD1>1</LOD1><LOD2>2</LOD2><LOD3>3</LOD3><LOD4>4</LOD4></LODMinDistances>"
            "<LODBias>0.5</LODBias><MinimumObjectSizeInPixels>9</MinimumObjectSizeInPixels>"
            "<LODBias>2.0</LODBias><MinimumObjectSizeInPixels>7</MinimumObjectSizeInPixels>")  # fmt: skip
    rules = cull.read_lod_rules(text)
    assert rules == cull.SLodRules((0.5, 0.1, 0.01, 0.001), (1.0, 2.0, 3.0, 4.0), 2.0, 7.0)
    assert cull.read_lod_rules(text, 0).bias == 0.5
    assert cull.read_lod_rules("") == cull.SLodRules()


def test_a_coarser_level_draws_the_same_config_material():
    lods = [[0, 1, 2], [2, 0], [1]]  # MaterialLODInfos: per level the config's material of each mesh number
    assert cull.level_numbers(lods, 2, 1) == [0] and cull.level_numbers(lods, 0, 1) == [1]
    assert cull.level_numbers(lods, 0, 2) == []  # the coarsest level drops it
    assert cull.level_numbers([], 3, 2) == [3] and cull.level_numbers(lods, None, 1) is None


def test_culling_changes_what_is_drawn_but_not_the_picture():
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from tests.editor.editorfakes import edit_island as island

    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.editor.editsession import CEditSession

    s = CEditSession(island())
    s.finish_loading()
    r = desktoprender.CRenderer(ctx, s, None)
    w, h = 320, 200
    fbo = ctx.framebuffer([ctx.renderbuffer((w, h), 4)], ctx.depth_renderbuffer((w, h)))
    cam = camera.COrbitCamera((s.m_size_tiles / 2, 0.0, s.m_size_tiles / 2), 14)
    shots = {}
    for culled in (True, False):
        r.render(fbo, (w, h), cam, desktoprender.SViewOptions(cull=culled))
        shots[culled] = (np.frombuffer(fbo.read(components=3), np.uint8).copy(), dict(r.m_stats))
    assert (shots[True][0] == shots[False][0]).all()
    assert shots[False][1]["terrain"] == shots[False][1]["terrain_of"]
    assert shots[True][1]["terrain"] <= shots[True][1]["terrain_of"]


def test_a_table_of_no_batches_answers_with_no_cells():
    """With nothing to draw the table held float arrays, and ``seen | always`` raised on its first use."""
    table = cull.CCellTable([])
    assert len(table.codes(_planes(), None)) == 0
    assert len(table.codes(_planes(), cull.SLodCamera(np.zeros(3), np.identity(4), np.identity(4), (8, 8)))) == 0


def test_a_closed_loader_answers_none_instead_of_raising_in_the_last_frame():
    """A view's last frame after its renderer was released asked the closed loader, whose pool refused the job."""
    from annotoolbox.render.loader import CLoader

    loader = CLoader(1)
    loader.close()
    assert loader.get("ground", lambda: 1) is None and loader.pending() == 0
