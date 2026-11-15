"""The cloth solver (``render.clothsim``) and its collisions (``render.clothcollide``) on synthetic strips, and with
an install (``ANNO117_DIR``, ``ANNO2070_DIR``, ``ANNO1800_DIR``, ``ANNO2205_DIR``) on the game's own cloth meshes."""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.render import clothcollide, clothsim
from annotoolbox.sources.gametree import open_game

PIN = (0x80, 0, 0, 0)
FREE = (0, 0, 0, 0)


def strip(columns: int = 4, rows: int = 6, size: float = 0.1):
    """A vertical banner: ``columns`` x ``rows`` vertices in the x-y plane, the top row pinned, two triangles a cell."""
    pos, colours, tris = [], [], []
    for r in range(rows):
        for c in range(columns):
            pos.append((c * size, -r * size, 0.0))
            colours.append(PIN if r == 0 else FREE)
    for r in range(rows - 1):
        for c in range(columns - 1):
            a = r * columns + c
            tris += [a, a + 1, a + columns, a + 1, a + columns + 1, a + columns]
    return np.array(pos), np.array(tris), np.array(colours, np.uint8)


def test_pins_from_the_colour_bytes():
    colours = np.array([PIN, FREE, (0, 255, 255, 255), (0x7F, 255, 255, 0)], np.uint8)
    pinned, holds = clothsim.vertex_pins(colours, 4)
    assert pinned.tolist() == [True, False, True, False]
    assert holds.tolist() == [True, False, False, False]
    pinned, holds = clothsim.vertex_pins(None, 3)  # no colour stream: the game pins the whole cloth
    assert pinned.all() and holds.all()


def test_welding_edges_and_tethers():
    pos, tris, colours = strip(3, 3)
    doubled = np.concatenate([pos, pos[:1]])  # a copy of the first vertex, as a seam has
    mesh = clothsim.prepare(doubled, np.concatenate([tris, [9, 1, 3]]), np.concatenate([colours, [FREE]]))
    assert len(mesh.rest) == 9 and mesh.index[9] == mesh.index[0]
    assert mesh.pinned.sum() == 3
    assert len(mesh.edges) == 16  # 12 sides and 4 diagonals of the four cells
    far = np.argmin(mesh.rest[:, 1])
    assert mesh.tether[far] == pytest.approx(0.2)  # two edges down to the pinned row
    assert mesh.pinned[mesh.anchor[far]]
    assert mesh.longest == pytest.approx(0.1 * math.sqrt(2))


def test_the_passes_and_the_thickness():
    assert clothsim.passes(0) == 4
    assert clothsim.passes(5) == int(3 * 1.3**5 - 1)
    mesh = clothsim.prepare(*strip())
    assert clothsim.thickness(mesh) == pytest.approx(min(0.5 * mesh.longest, 0.25 * 1.3))


def test_frame_factor():
    assert clothsim.frame_factor(1 / 60) == pytest.approx(1.0)
    assert clothsim.frame_factor(0.1) == pytest.approx(0.2)
    assert clothsim.frame_factor(0.066) == pytest.approx(0.51 + 0.2 * 0.49, abs=0.01)


def test_a_pinned_strip_hangs_and_holds():
    sim = clothsim.ClothSim(clothsim.prepare(*strip()), clothsim.ClothParams(gravity=0.5, wind_strength=0.0))
    top = sim.mesh.pinned
    rest = sim.mesh.rest.copy()
    forces = clothsim.Forces(accel=(0.0, -0.5, 0.0))
    for _ in range(120):
        sim.step(1 / 30, forces)
    assert np.allclose(sim.p[top], rest[top])  # the pins stay
    assert np.all(np.isfinite(sim.p))
    lowest = sim.mesh.tether.argmax()
    reach = np.linalg.norm(sim.p[lowest] - sim.p[sim.mesh.anchor[lowest]])
    assert reach <= clothsim.TETHER_SLACK * sim.mesh.tether[lowest] + 1e-6  # held by its tether
    pos, normals = sim.drawn()
    assert pos.shape == (24, 3) and np.allclose(np.linalg.norm(normals, axis=1), 1.0)


def test_the_wind_presses_along_the_normal():
    sim = clothsim.ClothSim(clothsim.prepare(*strip()), clothsim.ClothParams(gravity=0.0, wind_strength=1.0))
    side = clothsim.Forces(wind=(0.0, 0.0, 2.0))  # into the banner's face (it lies in the x-y plane)
    along = clothsim.Forces(wind=(2.0, 0.0, 0.0))  # in its plane: no push
    for _ in range(10):
        sim.step(1 / 30, along)
    assert np.allclose(sim.p, sim.mesh.rest, atol=1e-9)
    for _ in range(10):
        sim.step(1 / 30, side)
    assert np.abs(sim.p[~sim.mesh.pinned, 2]).max() > 1e-3


def test_the_world_wind():
    params = clothsim.ClothParams(wind_strength=2.0, gravity=0.05)
    f = clothsim.world_forces(params, (1.0, 0.0, 2.0), (1.0, 0.0), 1.0, 3.0, 1 / 30)
    g = 1 + 0.4 * abs(math.sin(1.0 + 2.4)) ** 16 * abs(math.sin(15.0)) ** 2
    assert f.wind[0] == pytest.approx(abs(math.sin(1 / 3 + 0.6)) * 0.05 * g * 2.0)
    assert f.wind[1] == 0.0 and f.wind[2] == pytest.approx(0.0)
    assert f.accel == (0.0, -0.05, 0.0)
    local = clothsim.local_forces(clothsim.ClothParams(local_wind_direction=math.pi / 2), np.identity(3), 0.0, 1 / 30)
    assert local.wind[2] == pytest.approx(0.2 * 0.1) and local.accel == (0.0, 0.0, 0.0)


def test_tearing_lets_the_white_pins_go():
    pos, tris, colours = strip(2, 3)
    colours[0] = (0, 255, 255, 255)  # pinned white: lets go when torn
    sim = clothsim.ClothSim(clothsim.prepare(pos, tris, colours), clothsim.ClothParams(tearing=True))
    sim.step(1 / 30, clothsim.Forces(), torn=True)
    white = sim.mesh.index[0]
    assert sim.w[white] == 0.0 and sim.w[sim.mesh.index[1]] == 1.0
    sim.step(1 / 30, clothsim.Forces(), torn=False)
    assert sim.w[white] == pytest.approx(0.001)


def test_collisions_put_a_vertex_on_the_surface():
    p = np.array([[0.02, 0.05, 0.0], [0.0, 2.0, 0.0]])
    prev = p - 0.01
    capsule = clothcollide.collider("Cylinder", np.identity(4), {"Length": "1", "Radius": "0.1"})  # y from -0.5 to 0.5
    capsule.push(p, prev, 0.0)
    assert p[0] == pytest.approx([0.02 * (0.1 / 0.02 + 0.001), 0.05, 0.0])
    assert np.allclose(p[1], [0.0, 2.0, 0.0]) and np.allclose(prev[0], p[0])  # outside: untouched; inside: no velocity
    q = np.array([[0.05, 0.0, 0.0]])
    sphere = clothcollide.collider("2", np.diag([0.5, 0.5, 0.5, 1.0]), {"Radius": "9"})  # by index; Radius unread
    sphere.push(q, q.copy(), 0.0)
    assert np.linalg.norm(q[0]) == pytest.approx(0.5, abs=1e-3)
    r = np.array([[0.0, -0.2, 0.0], [0.0, 0.3, 0.0]])
    plane = clothcollide.collider("Plane", np.identity(4), {})
    assert plane.thin  # the game gives a plane no thickness
    plane.push(r, r.copy())
    assert r[0, 1] == pytest.approx(-0.001) and r[1, 1] == pytest.approx(0.3)  # to just behind the plane; +y free
    box = clothcollide.collider("Box", np.diag([2.0, 2.0, 2.0, 1.0]), {"Extents.x": "0.25", "Extents.y": "0.5"})
    b = np.array([[0.52, 0.1, 0.0], [0.7, 0.1, 0.0]])
    box.push(b, b.copy(), 0.05)  # half sizes s x Extents: 0.5 along x
    assert b[0, 0] == pytest.approx(0.5 + 0.02 * (0.05 / 0.02 + 0.001)) and b[1, 0] == pytest.approx(0.7)
    inside = np.array([[0.1, 0.1, 0.0]])
    with np.errstate(invalid="ignore", divide="ignore"):
        box.push(inside, inside.copy(), 0.05)
    assert not np.isfinite(inside).all()  # the game's box has no inside rule: the cloth starts again


def test_a_cloth_that_is_not_finite_starts_again():
    sim = clothsim.ClothSim(clothsim.prepare(*strip()), clothsim.ClothParams(gravity=0.5))
    sim.p[3] = np.nan
    sim.step(1 / 30, clothsim.Forces())
    assert np.all(np.isfinite(sim.p))


def test_a_finer_level_puts_a_vertex_in_the_middle_of_every_edge():
    mesh = clothsim.prepare(*strip(3, 3))
    fine = clothsim.refine(mesh)
    n = len(mesh.rest)
    assert len(fine.rest) == n + len(mesh.edges) and len(fine.triangles) == 4 * len(mesh.triangles)
    assert np.allclose(fine.rest[:n], mesh.rest)  # the coarse vertices keep their places in front
    a, b = fine.parents[0]
    assert np.allclose(fine.rest[n], (mesh.rest[a] + mesh.rest[b]) / 2)
    top = [k for k, (a, b) in enumerate(fine.parents) if mesh.pinned[a] and mesh.pinned[b]]
    half = [k for k, (a, b) in enumerate(fine.parents) if mesh.pinned[a] != mesh.pinned[b]]
    assert fine.weight[n + top[0]] == 1.0 and fine.weight[n + half[0]] == 0.5
    assert fine.longest == pytest.approx(mesh.longest / 2)
    sim = clothsim.ClothSim(mesh, clothsim.ClothParams(gravity=0.5, wind_strength=0.0))
    for _ in range(10):
        sim.step(1 / 30, clothsim.Forces(accel=(0.0, -0.5, 0.0)))
    before = sim.p.copy()
    sim.set_level(2)
    assert sim.level == 2 and len(sim.levels) == 3 and np.allclose(sim.p[: len(before)], before)
    for _ in range(10):
        sim.step(1 / 30, clothsim.Forces(accel=(0.0, -0.5, 0.0)))
    pos, normals = sim.drawn()
    assert pos.shape == (9, 3) and np.all(np.isfinite(pos)) and np.allclose(np.linalg.norm(normals, axis=1), 1.0)
    sim.set_level(0)
    assert len(sim.p) == n


def test_the_level_by_the_cameras_distance_and_the_budget():
    assert clothsim.target_level(10.0, 0.75) == 7 and clothsim.target_level(80.0, 0.75) == 0
    assert clothsim.target_level(50.0, 0.75) == int(7 * 0.5)
    assert clothsim.target_level(50.0, 1.2) < clothsim.target_level(50.0, 0.75)  # a wider view counts as farther
    mesh = clothsim.prepare(*strip())
    assert clothsim.work(mesh, 0) == 4 * len(mesh.edges)


def _cloth_meshes(game: str, limit: int = 40):
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {INSTALL_VARS[game]}")
    tree = open_game(game, Path(folder))
    found = [p for p in tree.paths if p.endswith(".rdm") and "cloth" in p.rsplit("/", 1)[-1]][:limit]
    return tree, found


@pytest.mark.parametrize("game", ["anno117", "anno2070", "anno1800", "anno2205"])
def test_the_games_cloth_meshes_hang(game):
    tree, paths = _cloth_meshes(game)
    stepped = 0
    for path in paths:
        try:
            rdm = read_rdm(tree.read(path))
        except Exception:
            continue
        for m in rdm.meshes:
            colour = next((i for i, e in enumerate(m.elements) if e.usage == 5 and e.type == 5), None)
            pos = next((i for i, e in enumerate(m.elements) if e.usage == 0 and e.type == 7), None)
            if colour is None or pos is None or not m.indices:
                continue
            mesh = clothsim.prepare(np.array(m.element(pos))[:, :3], m.indices, np.array(m.element(colour), np.uint8))
            assert mesh.pinned.any(), path  # every cloth of the games pins some vertices by the rule
            sim = clothsim.ClothSim(mesh, clothsim.ClothParams(gravity=0.05))
            for _ in range(30):
                sim.step(1 / 30, clothsim.Forces(wind=(0.1, 0.0, 0.05), accel=(0.0, -0.05, 0.0)))
            assert np.all(np.isfinite(sim.p)), path
            assert np.allclose(sim.p[mesh.pinned], mesh.rest[mesh.pinned]), path
            stepped += 1
    assert stepped > 0


class _Buffer:
    def __init__(self) -> None:
        self.written = []

    def write(self, data: bytes) -> None:
        self.written.append(np.frombuffer(data, np.float32).reshape(-1, 8))


def test_the_view_steps_a_cloth_once_a_frame():
    from types import SimpleNamespace

    from annotoolbox.viewer.clothplay import ClothPart, ClothPlayer

    pos, tris, colours = strip()
    verts = np.concatenate([pos, np.zeros((len(pos), 3)), np.zeros((len(pos), 2))], axis=1).astype(np.float32)
    colour = SimpleNamespace(usage=5, type=5)
    stored = SimpleNamespace(indices=tris.tolist(), elements=[colour], element=lambda k: [tuple(c) for c in colours])
    data = SimpleNamespace(vertices=verts, stored=stored, groups={0: tris})
    part = SimpleNamespace(node="Clothes/0", matrix=np.identity(4),
                           cloth=ClothPart(clothsim.ClothParams(gravity=0.5, wind_strength=0.0)))  # fmt: skip
    item = {"data": data, "mesh": 0, "verts": verts, "vbo": _Buffer()}
    player, state = ClothPlayer(), SimpleNamespace(cloth=True, time=0.0, torn=False)
    for k in range(10):
        state.time = k / 30
        player.next_frame()
        player.pose(part, item, state, np.identity(4), None)
        player.pose(part, item, state, np.identity(4), None)  # a second group of the same mesh: no second step
    run = player.running[("Clothes/0", 0)]
    assert run.time == pytest.approx(9 / 30)
    assert item["vbo"].written[-1][:, 1].min() < pos[:, 1].min() - 1e-4  # it sags under gravity
    state.cloth = False
    player.pose(part, item, state, np.identity(4), None)
    assert np.allclose(item["vbo"].written[-1], verts) and item["posed"] is None


def _player_item(columns: int = 4, rows: int = 6):
    from types import SimpleNamespace

    from annotoolbox.viewer.clothplay import ClothPart

    pos, tris, colours = strip(columns, rows)
    verts = np.concatenate([pos, np.zeros((len(pos), 3)), np.zeros((len(pos), 2))], axis=1).astype(np.float32)
    colour = SimpleNamespace(usage=5, type=5)
    stored = SimpleNamespace(indices=tris.tolist(), elements=[colour], element=lambda k: [tuple(c) for c in colours])
    data = SimpleNamespace(vertices=verts, stored=stored, groups={0: tris})
    part = SimpleNamespace(node="Clothes/0", matrix=np.identity(4),
                           cloth=ClothPart(clothsim.ClothParams(gravity=0.5, wind_strength=0.0)))  # fmt: skip
    return part, {"data": data, "mesh": 0, "verts": verts, "vbo": _Buffer()}


def test_the_view_raises_a_near_cloths_level_every_eighth_frame_within_the_budget(monkeypatch):
    from types import SimpleNamespace

    from annotoolbox.viewer import clothplay
    from annotoolbox.viewer.clothplay import ClothPlayer

    monkeypatch.setattr(clothplay, "VIEW_BUDGET", clothsim.BUDGET)  # the game's own number here
    part, item = _player_item(3, 3)  # level 5 fits the budget, 6 does not
    player, state = ClothPlayer(), SimpleNamespace(cloth=True, time=0.0, torn=False)
    for k in range(40):
        state.time = k / 30
        player.next_frame((0.0, 0.0, 5.0), 0.75)  # near: the target is 7
        player.pose(part, item, state, np.identity(4), None)
    run = player.running[("Clothes/0", 0)]
    assert run.target == 7 and run.sim.level == 40 // clothsim.LEVEL_EVERY  # one step every eighth frame
    assert item["cloth_version"] == 40
    monkeypatch.setattr(clothsim, "BUDGET", clothsim.work(run.sim.levels[2], 2))
    player.next_frame((0.0, 0.0, 5.0), 0.75)
    assert run.sim.level == 4  # over the budget: every level one lower, once a frame
    for _ in range(2):
        player.next_frame((0.0, 0.0, 5.0), 0.75)
    assert run.sim.level == 2
    player.next_frame((0.0, 0.0, 500.0), 0.75)
    player.pose(part, item, state, np.identity(4), None)
    assert run.target == 0


def _mesh(elements, rows: np.ndarray, stride: int):
    from annotoolbox.render.gamedraw import Mesh

    return Mesh(rows.tobytes(), stride, elements, np.arange(3, dtype=np.uint32), len(rows))


def _forward_model(mesh):
    from types import SimpleNamespace

    return SimpleNamespace(draw_call=SimpleNamespace(mesh=mesh))


def test_the_moved_vertices_go_into_the_stored_layout_in_their_own_types():
    pytest.importorskip("PySide6")
    from annotoolbox.render import clothmesh

    floats = ((0, 7, 3, 0), (1, 7, 3, 12), (2, 7, 3, 24), (4, 7, 2, 36), (5, 5, 1, 44))
    rows = np.zeros((3, 48), np.uint8)
    rows[:, 0:36] = np.array([[0, 0, 0, 0, 0, 1, 1, 0, 0]] * 3, "<f4").view(np.uint8)
    rows[:, 44:48] = 200
    mesh = _mesh(floats, rows, 48)
    pos = np.array([[1.0, 2.0, 3.0]] * 3)
    normal = np.array([[0.0, 1.0, 0.0]] * 3)
    out = np.frombuffer(clothmesh.reposed(mesh, pos, normal), np.uint8).reshape(3, 48)
    values = np.ascontiguousarray(out[:, :36]).view("<f4")
    assert values[0, :3] == pytest.approx([1, 2, 3]) and values[0, 3:6] == pytest.approx([0, 1, 0])
    assert values[0, 6:9] == pytest.approx([1, 0, 0])  # the tangent stays on the new surface
    assert (out[:, 44:] == 200).all()  # the pins' colour is kept
    halves = ((0, 6, 4, 0), (1, 5, 1, 8), (2, 5, 1, 12))
    rows = np.zeros((3, 16), np.uint8)
    rows[:, 6:8] = np.array([1.0], "<f2").view(np.uint8)  # w 1
    rows[:, 8:12] = (127, 255, 127, 9)
    rows[:, 12:16] = (127, 127, 255, 9)
    out = np.frombuffer(clothmesh.reposed(_mesh(halves, rows, 16), pos, np.array([[0.0, 0.0, 1.0]] * 3)), np.uint8)
    out = out.reshape(3, 16)
    assert np.ascontiguousarray(out[:, :8]).view("<f2")[0] == pytest.approx([1, 2, 3, 1], abs=1e-3)
    assert tuple(out[0, 8:12]) == (128, 128, 255, 9)  # the packed normal; its fourth byte kept
    item = {"posed": None, "cloth_version": 0}
    mesh = _mesh(floats, np.zeros((3, 48), np.uint8), 48)
    clothmesh.follow(item, _forward_model(mesh))  # version 0: the rest, nothing to do
    assert mesh.version == 0 and not mesh.rest
    item.update(posed=np.concatenate([pos, normal, np.zeros((3, 2))], axis=1), cloth_version=3)
    clothmesh.follow(item, _forward_model(mesh))
    assert mesh.version == 3 and mesh.rest and mesh.data != mesh.rest
    item.update(posed=None, cloth_version=4)
    clothmesh.follow(item, _forward_model(mesh))
    assert mesh.version == 4 and mesh.data == mesh.rest  # back to rest when the simulation is off


def test_2070s_cloth_mesh_moves_with_its_tangent_frame():
    pytest.importorskip("PySide6")
    from annotoolbox.render import cloth2070, clothmesh

    pos, tris, _colours = strip(3, 3)
    verts = np.concatenate([pos, np.tile([0.0, 0.0, 1.0], (9, 1)), pos[:, :2]], axis=1).astype(np.float32)
    mesh = cloth2070.cloth_mesh(verts, tris)
    normal = cloth2070.unpacked(np.frombuffer(mesh.data, np.uint8).reshape(9, cloth2070.STRIDE)[:, 12:16])
    same = clothmesh.reposed(mesh, pos, normal)
    raw, back = (np.frombuffer(b, np.uint8).reshape(9, cloth2070.STRIDE) for b in (mesh.data, same))
    assert np.abs(raw.astype(int) - back.astype(int)).max() <= 1  # at rest: the bytes it was made with
    moved = np.frombuffer(clothmesh.reposed(mesh, pos + 1.0, np.tile([1.0, 0.0, 0.0], (9, 1))), np.uint8)
    moved = moved.reshape(9, cloth2070.STRIDE)
    assert np.ascontiguousarray(moved[:, :12]).view("<f4")[0] == pytest.approx(pos[0] + 1.0)
    n = cloth2070.unpacked(moved[:, 12:16])
    t = cloth2070.unpacked(np.ascontiguousarray(moved[:, 24:40]).view("<u4"))
    assert n[0] == pytest.approx([1, 0, 0], abs=0.01) and abs(float(t[0] @ n[0])) < 0.02


def _shift(x: float) -> np.ndarray:
    m = np.zeros((4, 4))
    m[0, 3] = x
    return m


def test_a_part_scaled_to_nothing_holds_no_box_and_stops_the_cloth():
    from types import SimpleNamespace

    from annotoolbox.viewer.clothplay import ClothPart, ClothPlayer

    assert clothcollide.collider("Box", np.diag([0.0, 1.0, 1.0, 1.0]), {}) is None
    assert clothsim.inverse(np.zeros((4, 4))) is None
    part, item = _player_item()
    part.cloth = ClothPart(part.cloth.params, (("Box", np.eye(4) + _shift(10.0), {}),))
    player, state = ClothPlayer(), SimpleNamespace(cloth=True, time=0.0, torn=False)
    for k in range(3):  # the second frame hides the part (a figure's zero scale), the third shows it again
        state.time = k / 30
        player.next_frame()
        player.pose(part, item, state, np.zeros((4, 4)) if k == 1 else np.identity(4), None)
    assert np.isfinite(item["posed"]).all() and item["cloth_version"] == 3


def test_prepare_builds_the_edges_once_and_the_unit_bytes_round_trip(monkeypatch):
    calls = []
    real = clothsim.tethers
    monkeypatch.setattr(clothsim, "tethers", lambda *a: calls.append(1) or real(*a))
    clothsim.prepare(*strip(3, 3))
    assert len(calls) == 1
    v = np.array([[-1.0, 0.0, 1.0]])
    assert clothsim.unit_bytes(v).tolist() == [[0, 128, 255]]


def test_the_cloth_constant_packs_what_the_shader_unpacks():
    from annotoolbox.render import clothmesh

    def colour(bits: int) -> tuple[float, ...]:  # cloth.fx: 4 bits a channel, then FastGammaToLinear (squared)
        return tuple((((bits >> s) & 0xF) / 15.0) ** 2 for s in (8, 4, 0))

    x, y, z, w = clothmesh.cloth_constant(dye=(0.13, 0.32, 0.72))
    assert (y, w) == (0, 0)  # no atlas index, no damage, vertex data at 0
    assert colour((x >> 1) & 0xFFF) == (1.0, 1.0, 1.0) and (x >> 13) & 0x7 == 7  # opaque white object colour
    assert colour((z >> 2) & 0xFFF) == (1.0, 1.0, 1.0)  # a zero second colour would paint the cloth black
    assert z & 3 == 0 and (z >> 14) & 0xFF == 0  # not inverted, not metallic, no logo
    assert colour((x >> 16) & 0xFFF) == pytest.approx((0.13, 0.32, 0.72), abs=0.04)


def test_the_views_budget_is_below_the_games_and_holds_a_busy_frame_down():
    from types import SimpleNamespace

    from annotoolbox.viewer import clothplay
    from annotoolbox.viewer.clothplay import ClothPlayer

    assert clothplay.VIEW_BUDGET < clothsim.BUDGET
    part, item = _player_item(3, 3)
    player, state = ClothPlayer(), SimpleNamespace(cloth=True, time=0.0, torn=False)
    for k in range(clothsim.LEVEL_EVERY * 8):
        state.time = k / 30
        player.next_frame((0.0, 0.0, 5.0), 0.75)  # near: the target is 7
        player.pose(part, item, state, np.identity(4), None)
    run = player.running[("Clothes/0", 0)]
    assert 0 < run.sim.level < 7  # held down: the level whose work passes the view's budget is not kept
    assert clothsim.work(run.sim.fine, run.sim.level) <= clothplay.VIEW_BUDGET * 2.5
