"""A cloth moved by Anno 117's rules: pinned vertices from the mesh's colours, welded vertices, the triangles' edges as
distance constraints, tethers to the nearest pinned vertex, one Verlet step a frame with the wind pressing along the
normals, then the pins, the collisions and the normals; finer levels of the mesh (a vertex in the middle of every
edge) by the camera's distance, within the frame's budget.

``prepare(positions, indices, colours)`` makes a ``ClothMesh``; ``ClothSim(mesh, params).step(dt, forces)`` moves
it, ``set_level`` subdivides it (``refine``, ``target_level``); ``world_forces`` and ``local_forces`` are the game's
wind and gravity, ``params_of`` reads a CLOTH config's fields. Rules and chosen values: docs/engine-cloth.md.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

PIN_BIT = 0x80  # the first colour byte's top bit pins a vertex and keeps it pinned when the cloth tears
MAX_FRAME = 0.066  # the game simulates a slower frame as 66 ms
TETHER_SLACK = 1.2  # a vertex may stray this much farther than its path to its pinned vertex
LEVEL_PASSES = (1.3, 3.0)  # passes = max(4, int(3 x 1.3^level - 1))
MIN_PASSES = 4
LOCAL_WIND = 0.2  # a local wind's strength before WindStrength
WORLD_WIND = 0.05  # the world wind's share of the session strength
# the level by the camera's distance d: int(7 (1 - clamp((d - 20) F / 60, 0, 1))), F = tan(fov / 2) / tan(0.375)
LEVEL_NEAR, LEVEL_SPAN, LEVEL_TOP, LEVEL_FOV = 20.0, 60.0, 7, 0.75
MAX_LEVEL = 10
LEVEL_EVERY = 8  # frames between two steps of a level toward its target
BUDGET = 320_000  # a frame's work (passes x edges of every cloth); above it every level drops by one


@dataclass(frozen=True)
class ClothParams:
    """A CLOTH config's simulation fields; 2070's lack a wind strength, 0.1 is the commonest of 117's."""

    wind_strength: float = 0.1
    gravity: float = 0.1
    allow_local_wind: bool = True
    local_wind_direction: float = 0.0  # radians, in the object's axes
    unique: bool = False
    tearing: bool = False
    collisions: tuple[int, ...] = ()


def field_number(values: Mapping[str, str], name: str, default: float) -> float:
    """A config field as a number, the default for a missing or unreadable one."""
    try:
        return float(values.get(name, default))
    except (TypeError, ValueError):
        return default


def inverse(matrix) -> np.ndarray | None:
    """The inverse of a matrix, None for a singular one (a part scaled to nothing, as a hidden figure's)."""
    try:
        return np.linalg.inv(np.asarray(matrix, float))
    except np.linalg.LinAlgError:
        return None


def unit_bytes(v: np.ndarray) -> np.ndarray:
    """Unit vectors [n, 3] as bytes (-1..1 onto 0..255), the quantiser every cloth vertex layout packs with."""
    return np.clip(np.rint((np.asarray(v, float) + 1.0) * 127.5), 0, 255).astype(np.uint8)


def params_of(values: Mapping[str, str], collisions: tuple[int, ...] = ()) -> ClothParams:
    """The parameters of a newer game's CLOTH fields (name -> text); ``collisions`` its ``CollisionIndex`` list."""

    def number(name: str, default: float) -> float:
        return field_number(values, name, default)

    def switch(name: str, default: bool) -> bool:
        return str(values.get(name, "1" if default else "0")).strip() == "1"

    return ClothParams(
        number("WindStrength", 0.1), number("Gravity", 0.1), switch("AllowLocalWind", True),
        number("LocalWindDirection", 0.0), switch("UniqueSimulation", False), switch("DamageTearingEnabled", False),
        collisions,
    )  # fmt: skip


def vertex_pins(colours: np.ndarray | None, count: int) -> tuple[np.ndarray, np.ndarray]:
    """(pinned, holds) per vertex from the colour bytes [n, 4] as stored: pinned when the first byte's top bit is set
    or the other three are 255; ``holds`` the top bit alone (such a pin stays when the cloth tears). A mesh without
    colours is pinned whole, as the game reads it."""
    if colours is None:
        return np.ones(count, bool), np.ones(count, bool)
    c = np.asarray(colours, np.uint8).reshape(-1, 4)
    holds = (c[:, 0] & PIN_BIT) != 0
    white = (c[:, 1] == 255) & (c[:, 2] == 255) & (c[:, 3] == 255)
    return holds | white, holds


@dataclass
class ClothMesh:
    """A cloth as the solver takes it: ``rest`` the welded vertices, ``index`` each stored vertex's welded one,
    ``triangles`` and ``edges`` over the welded vertices, ``rest2`` each edge's squared rest length, ``anchor`` and
    ``tether`` each vertex's nearest pinned vertex and the path length to it (inf: none)."""

    rest: np.ndarray
    index: np.ndarray
    triangles: np.ndarray
    edges: np.ndarray
    rest2: np.ndarray
    pinned: np.ndarray
    holds: np.ndarray
    anchor: np.ndarray
    tether: np.ndarray
    longest: float = 0.0
    weight: np.ndarray | None = None  # the weights it starts with (1 pinned; a finer level's middles the mean)
    parents: np.ndarray | None = None  # a finer level's new vertices: the coarser edge [m, 2] each is the middle of

    def __post_init__(self) -> None:
        if self.weight is None:
            self.weight = self.pinned.astype(np.float64)
        if self.parents is None:
            self.parents = np.zeros((0, 2), np.int64)


def weld(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(the distinct positions, each vertex's index among them): vertices at the same place are one."""
    pos = np.asarray(positions, np.float64).reshape(-1, 3)
    unique, index = np.unique(pos, axis=0, return_inverse=True)
    return unique, index.reshape(-1)


def edges_of(triangles: np.ndarray) -> np.ndarray:
    """The distinct edges of the triangles [t, 3] as sorted pairs."""
    t = np.asarray(triangles, np.int64).reshape(-1, 3)
    pairs = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    pairs = np.sort(pairs, axis=1)
    pairs = pairs[pairs[:, 0] != pairs[:, 1]]
    return np.unique(pairs, axis=0) if len(pairs) else np.zeros((0, 2), np.int64)


def tethers(count: int, edges: np.ndarray, lengths: np.ndarray, pinned: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per vertex the nearest pinned vertex along the edges and the path length to it (Dijkstra from every pin;
    SciPy's when it is installed, a finer level has thousands of vertices)."""
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import dijkstra
    except ImportError:
        return _tethers_plain(count, edges, lengths, pinned)
    pins = np.flatnonzero(pinned)
    if not len(pins) or not len(edges):
        return _tethers_plain(count, edges, lengths, pinned)
    graph = coo_matrix((np.maximum(lengths, 1e-12), (edges[:, 0], edges[:, 1])), shape=(count, count)).tocsr()
    dist, _pred, sources = dijkstra(graph, directed=False, indices=pins, min_only=True, return_predecessors=True)
    anchor = np.where(sources >= 0, sources, np.arange(count))
    return anchor.astype(np.int64), np.where(np.isfinite(dist), dist, np.inf)


def _tethers_plain(
    count: int, edges: np.ndarray, lengths: np.ndarray, pinned: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    near = [[] for _ in range(count)]
    for (a, b), length in zip(edges.tolist(), lengths.tolist(), strict=True):
        near[a].append((b, length))
        near[b].append((a, length))
    dist = np.full(count, np.inf)
    anchor = np.arange(count)
    done = np.zeros(count, bool)
    queue = [(0.0, i, i) for i in np.flatnonzero(pinned).tolist()]
    heapq.heapify(queue)
    while queue:
        d, i, a = heapq.heappop(queue)
        if done[i]:
            continue
        done[i], dist[i], anchor[i] = True, d, a
        for j, length in near[i]:
            if not done[j] and d + length < dist[j]:
                dist[j] = d + length
                heapq.heappush(queue, (d + length, j, a))
    return anchor, dist


def prepare(positions, indices, colours=None) -> ClothMesh:
    """The solver's mesh of a cloth file's vertices [n, 3], triangle indices and colour bytes [n, 4] (None: none)."""
    rest, index = weld(positions)
    stored_pins, stored_holds = vertex_pins(colours, len(index))
    pinned, holds = np.zeros(len(rest), bool), np.zeros(len(rest), bool)
    np.logical_or.at(pinned, index, stored_pins)  # a welded vertex is pinned when any of its copies is
    np.logical_or.at(holds, index, stored_holds)
    tris = index[np.asarray(indices, np.int64).reshape(-1, 3)]
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 2] != tris[:, 0])]
    return _finish(rest, index, tris, pinned, holds)


def _finish(rest, index, tris, pinned, holds, weight=None, parents=None) -> ClothMesh:
    """The mesh's edges, rest lengths and tethers over welded vertices and triangles."""
    edges = edges_of(tris)
    rest2 = np.sum((rest[edges[:, 1]] - rest[edges[:, 0]]) ** 2, axis=1) if len(edges) else np.zeros(0)
    anchor, tether = tethers(len(rest), edges, np.sqrt(rest2), pinned)
    longest = float(np.sqrt(rest2.max())) if len(rest2) else 0.0
    return ClothMesh(rest, index, tris, edges, rest2, pinned, holds, anchor, tether, longest, weight, parents)


def refine(mesh: ClothMesh) -> ClothMesh:
    """The next finer level: a vertex in the middle of every edge, each triangle cut into four. The coarser
    vertices keep their places in front; a middle starts with the mean of its ends' weights (the game's rule) and
    is pinned, for its tether, when both ends are (chosen)."""
    n, e = len(mesh.rest), mesh.edges
    middle = {(int(a), int(b)): n + k for k, (a, b) in enumerate(e.tolist())}
    t = mesh.triangles

    def mid(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        return np.array([middle[(int(x), int(y))] for x, y in zip(lo.tolist(), hi.tolist(), strict=True)], np.int64)

    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
    tris = np.concatenate([np.stack(s, axis=1) for s in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    rest = np.concatenate([mesh.rest, 0.5 * (mesh.rest[e[:, 0]] + mesh.rest[e[:, 1]])])
    pinned = np.concatenate([mesh.pinned, mesh.pinned[e[:, 0]] & mesh.pinned[e[:, 1]]])
    holds = np.concatenate([mesh.holds, mesh.holds[e[:, 0]] & mesh.holds[e[:, 1]]])
    weight = np.concatenate([mesh.weight, 0.5 * (mesh.weight[e[:, 0]] + mesh.weight[e[:, 1]])])
    return _finish(rest, mesh.index, tris, pinned, holds, weight, e.copy())


def target_level(distance: float, fov: float) -> int:
    """The level a cloth aims at from the camera's ``distance`` with a field of view ``fov`` (radians)."""
    f = math.tan(0.5 * fov) / math.tan(0.5 * LEVEL_FOV)
    t = min(max((distance - LEVEL_NEAR) * f / LEVEL_SPAN, 0.0), 1.0)
    return int(LEVEL_TOP * (1.0 - t))


def work(mesh: ClothMesh, level: int) -> int:
    """What a step of the mesh at ``level`` costs the frame's budget: passes x edges."""
    return passes(level) * len(mesh.edges)


@dataclass(frozen=True)
class Forces:
    """One frame's wind and acceleration (gravity or a push), in the world's axes, before the step's dt."""

    wind: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel: tuple[float, float, float] = (0.0, 0.0, 0.0)


def frame_factor(dt: float) -> float:
    """The game's damping of both forces on a slow frame: 1 up to 33 ms, 0.2 from 100 ms."""
    k = min(max((dt - 0.1) / (-1.0 / 15.0), 0.0), 1.0)
    return k + 0.2 * (1.0 - k)


def gust(seconds: float, along: float, strength: float) -> float:
    """``1 + strength x |sin(along + 0.8 t)|^16 x |sin(5 t)|^2``: the game's gusts."""
    return 1.0 + strength * abs(math.sin(along + 0.8 * seconds)) ** 16 * abs(math.sin(5.0 * seconds)) ** 2


def world_forces(params: ClothParams, place, wind: tuple[float, float], strength: float, seconds: float,
                 dt: float) -> Forces:  # fmt: skip
    """The world's wind on a cloth at ``place`` (x, y, z): the session's direction (x, z) and strength, varied over
    the map and in time, and gravity (docs/engine-cloth.md "The forces")."""
    x, _y, z = (float(v) for v in place)
    n = math.hypot(*wind)
    ux, uz = (wind[0] / n, wind[1] / n) if n > 0 else (0.0, 0.0)
    g = gust(seconds, ux * x + uz * z, 0.4)
    wx = abs(math.sin(x / 3.0 + 0.2 * seconds)) * ux * strength * WORLD_WIND * g
    wz = abs(math.cos(z / 11.0 + 0.2 * seconds)) * uz * strength * WORLD_WIND * g
    k = frame_factor(dt)
    w = params.wind_strength * k
    return Forces((wx * w, 0.0, wz * w), (0.0, -params.gravity * k, 0.0))


def local_forces(params: ClothParams, axes: np.ndarray, seconds: float, dt: float) -> Forces:
    """A moving unit's own wind along ``LocalWindDirection`` in its axes (rows x, y, z), and no gravity."""
    g = 1.0 + 0.2 * math.sin(0.8 * seconds) ** 16 * math.sin(5.0 * seconds) ** 2
    a = params.local_wind_direction
    own = np.array([math.cos(a), 0.0, math.sin(a)]) * LOCAL_WIND * g
    w = own @ np.asarray(axes, float)[:3, :3] * params.wind_strength * frame_factor(dt)
    return Forces(tuple(float(v) for v in w), (0.0, 0.0, 0.0))


def passes(level: int) -> int:
    """The constraint passes a step makes at a subdivision level (4 at the mesh as stored)."""
    base, times = LEVEL_PASSES
    return max(MIN_PASSES, int(times * base**level - 1.0))


def thickness(mesh: ClothMesh, level: int = 0) -> float:
    """The collision thickness: half the longest edge, at most ``0.25 / 1.3^(level - 1)``."""
    return min(0.5 * mesh.longest, 0.25 / LEVEL_PASSES[0] ** (level - 1))


def _turned(axes: np.ndarray, v) -> np.ndarray:
    """A world vector in the cloth's axes, at its own length (the game keeps it whatever the scale)."""
    v = np.asarray(v, float)
    length = float(np.linalg.norm(v))
    turned = np.asarray(axes, float) @ v
    size = float(np.linalg.norm(turned))
    return turned * (length / size) if size > 1e-12 else np.zeros(3)


@dataclass
class ClothSim:
    """One cloth's simulation: positions and previous positions per welded vertex of its level's mesh, the weights
    (1 pinned), the normals. ``step`` moves it a frame, ``set_level`` makes it finer or coarser; ``drawn()`` gives the
    stored vertices' positions and normals. ``mesh`` is the mesh as stored (level 0), ``levels`` the finer ones."""

    mesh: ClothMesh
    params: ClothParams = field(default_factory=ClothParams)
    level: int = 0

    def __post_init__(self) -> None:
        self.levels = [self.mesh]
        wanted, self.level = self.level, 0
        self.reset()
        self.set_level(wanted)

    @property
    def fine(self) -> ClothMesh:
        """The mesh of the level it simulates."""
        return self.levels[self.level]

    def reset(self) -> None:
        """Back to the rest pose, still."""
        m = self.fine
        self.p = m.rest.copy()
        self.prev = self.p.copy()
        self.w = m.weight.copy()
        self.normals = vertex_normals(self.p, m.triangles)

    def set_level(self, level: int) -> None:
        """Simulate at ``level`` (0 to ``MAX_LEVEL``) from now on, the finer meshes made as needed; a vertex keeps its
        place, a new middle starts between its ends."""
        level = min(max(int(level), 0), MAX_LEVEL)
        while len(self.levels) <= level:
            self.levels.append(refine(self.levels[-1]))
        while self.level < level:
            ends = self.levels[self.level + 1].parents
            self.p, self.prev, self.w = (np.concatenate([a, 0.5 * (a[ends[:, 0]] + a[ends[:, 1]])])
                                         for a in (self.p, self.prev, self.w))  # fmt: skip
            self.level += 1
        if level < self.level:
            n = len(self.levels[level].rest)
            self.p, self.prev, self.w, self.level = self.p[:n], self.prev[:n], self.w[:n], level
        self.normals = vertex_normals(self.p, self.fine.triangles)

    def step(self, dt: float, forces: Forces, axes=None, colliders=(), torn: bool = False) -> None:
        """One frame of ``dt`` seconds (capped at 66 ms); ``axes`` turns world vectors into the cloth's (rows). A
        cloth whose positions are not finite (a vertex inside a box) starts again at rest, as the game's does."""
        dt = min(float(dt), MAX_FRAME)
        if dt <= 0.0 or not len(self.p):
            return
        if not np.all(np.isfinite(self.p)):
            self.reset()
        m = self.fine
        axes = np.identity(3) if axes is None else np.asarray(axes, float)[:3, :3]
        if self.params.tearing:
            self._tear(torn)
        self._integrate(dt, _turned(axes, forces.accel) * dt, _turned(axes, forces.wind) * dt)
        self._tethers()
        relax = 0.5 ** (30.0 * dt)
        for _ in range(passes(self.level)):
            self.p += relax * self._corrections()
        self.p += (m.rest - self.p) * self.w[:, None]
        reach = thickness(m, self.level)
        with np.errstate(divide="ignore", invalid="ignore"):
            for c in colliders:
                c.push(self.p, self.prev, 0.0 if getattr(c, "thin", False) else reach)
        self.normals = vertex_normals(self.p, m.triangles)

    def _tear(self, torn: bool) -> None:
        m = self.fine
        loose = m.pinned & torn & ~m.holds
        self.w[loose] = 0.0
        back = m.pinned & ~loose
        self.w[back] = np.minimum(m.weight[back], 1.01 * self.w[back] + 0.001)

    def _integrate(self, dt: float, accel: np.ndarray, wind: np.ndarray) -> None:
        free = (1.0 - self.w)[:, None]
        push = self.normals @ wind
        moved = self.p - self.prev
        self.prev = self.p.copy()
        self.p = self.p + moved + free * accel + free * self.normals * push[:, None]

    def _tethers(self) -> None:
        m = self.fine
        held = np.isfinite(m.tether) & ~m.pinned
        if not held.any():
            return
        d = self.p[m.anchor] - self.p
        dist = np.linalg.norm(d, axis=1)
        over = dist - TETHER_SLACK * np.where(held, m.tether, 0.0)
        pull = held & (over > 0) & (dist > 1e-12)
        self.p[pull] += d[pull] * (over[pull] / dist[pull])[:, None]

    def _corrections(self) -> np.ndarray:
        """The summed edge corrections of one pass: ``0.5 (0.5 - L^2 / (d^2 + L^2))`` of each edge's vector."""
        m = self.fine
        e = m.edges
        delta = self.p[e[:, 1]] - self.p[e[:, 0]]
        d2 = np.sum(delta * delta, axis=1)
        c = 0.5 * (0.5 - m.rest2 / np.maximum(d2 + m.rest2, 1e-20))
        corr = c[:, None] * delta
        n = len(self.p)
        return np.stack([np.bincount(e[:, 0], corr[:, k], n) - np.bincount(e[:, 1], corr[:, k], n) for k in range(3)],
                        axis=1)  # fmt: skip

    def drawn(self) -> tuple[np.ndarray, np.ndarray]:
        """(positions, normals) of the stored vertices, in their order (a finer level's first vertices are theirs)."""
        return self.p[self.mesh.index], self.normals[self.mesh.index]


def on_surface(t: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Directions [k, 3] turned perpendicular to the unit normals ``n`` and made unit (one along n stays): a stored
    tangent frame kept on a moved cloth's surface."""
    out = t - np.sum(t * n, axis=1, keepdims=True) * n
    length = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(length > 1e-6, out / np.maximum(length, 1e-6), t)


def vertex_normals(p: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Each vertex's normal: the normalised sum of its triangles' unit normals (a vertex on none: +y)."""
    out = np.zeros_like(p)
    if len(triangles):
        a, b, c = (p[triangles[:, k]] for k in range(3))
        face = np.cross(b - a, c - a)
        length = np.linalg.norm(face, axis=1, keepdims=True)
        face = np.where(length > 1e-20, face / np.maximum(length, 1e-20), 0.0)
        for k in range(3):
            np.add.at(out, triangles[:, k], face)
    length = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(length > 1e-9, out / np.maximum(length, 1e-9), np.array([0.0, 1.0, 0.0]))
