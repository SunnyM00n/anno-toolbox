"""A cloth moved by Anno 117's rules: pinned vertices from the mesh's colours, welded vertices, the triangles' edges as
distance constraints, tethers to the nearest pinned vertex, one Verlet step a frame with the wind pressing along the
normals, then the pins, the collisions and the normals; finer levels of the mesh (a vertex in the middle of every
edge) by the camera's distance, within the frame's budget.

``prepare(positions, indices, colours)`` makes a ``SClothMesh``; ``CClothSim(mesh, params).step(delta_s, forces)`` moves
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
MAX_FRAME_S = 0.066  # the game simulates a slower frame as 66 ms
TETHER_SLACK = 1.2  # a vertex may stray this much farther than its path to its pinned vertex
LEVEL_PASSES = (1.3, 3.0)  # passes = max(4, int(3 x 1.3^level - 1))
MIN_PASSES = 4
LOCAL_WIND = 0.2  # a local wind's strength before WindStrength
WORLD_WIND = 0.05  # the world wind's share of the session strength
# the level by the camera's distance d: int(7 (1 - clamp((d - 20) F / 60, 0, 1))), F = tan(fov / 2) / tan(0.375)
LEVEL_NEAR, LEVEL_SPAN, LEVEL_TOP, LEVEL_FOV_RAD = 20.0, 60.0, 7, 0.75
MAX_LEVEL = 10
LEVEL_EVERY_FRAMES = 8  # frames between two steps of a level toward its target
BUDGET = 320_000  # a frame's work (passes x edges of every cloth); above it every level drops by one


@dataclass(frozen=True)
class SClothParams:
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


def unit_bytes(vectors: np.ndarray) -> np.ndarray:
    """Unit vectors [n, 3] as bytes (-1..1 onto 0..255), the quantiser every cloth vertex layout packs with."""
    return np.clip(np.rint((np.asarray(vectors, float) + 1.0) * 127.5), 0, 255).astype(np.uint8)


def params_of(values: Mapping[str, str], collisions: tuple[int, ...] = ()) -> SClothParams:
    """The parameters of a newer game's CLOTH fields (name -> text); ``collisions`` its ``CollisionIndex`` list."""

    def number(name: str, default: float) -> float:
        return field_number(values, name, default)

    def switch(name: str, default: bool) -> bool:
        return str(values.get(name, "1" if default else "0")).strip() == "1"

    return SClothParams(
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
    colour_bytes = np.asarray(colours, np.uint8).reshape(-1, 4)
    holds = (colour_bytes[:, 0] & PIN_BIT) != 0
    white = (colour_bytes[:, 1] == 255) & (colour_bytes[:, 2] == 255) & (colour_bytes[:, 3] == 255)
    return holds | white, holds


@dataclass
class SClothMesh:
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
    vertices = np.asarray(positions, np.float64).reshape(-1, 3)
    unique, index = np.unique(vertices, axis=0, return_inverse=True)
    return unique, index.reshape(-1)


def edges_of(triangles: np.ndarray) -> np.ndarray:
    """The distinct edges of the triangles [t, 3] as sorted pairs."""
    triangle_indices = np.asarray(triangles, np.int64).reshape(-1, 3)
    pairs = np.concatenate([triangle_indices[:, [0, 1]], triangle_indices[:, [1, 2]], triangle_indices[:, [2, 0]]])
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
    distance_to_pin, _pred, sources = dijkstra(
        graph, directed=False, indices=pins, min_only=True, return_predecessors=True
    )
    anchor = np.where(sources >= 0, sources, np.arange(count))
    return anchor.astype(np.int64), np.where(np.isfinite(distance_to_pin), distance_to_pin, np.inf)


def _tethers_plain(
    count: int, edges: np.ndarray, lengths: np.ndarray, pinned: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    near = [[] for _ in range(count)]
    for (first, second), edge_length in zip(edges.tolist(), lengths.tolist(), strict=True):
        near[first].append((second, edge_length))
        near[second].append((first, edge_length))
    distance_to_pin = np.full(count, np.inf)
    anchor = np.arange(count)
    done = np.zeros(count, bool)
    queue = [(0.0, i, i) for i in np.flatnonzero(pinned).tolist()]
    heapq.heapify(queue)
    while queue:
        path_length, i, first = heapq.heappop(queue)
        if done[i]:
            continue
        done[i], distance_to_pin[i], anchor[i] = True, path_length, first
        for j, edge_length in near[i]:
            if not done[j] and path_length + edge_length < distance_to_pin[j]:
                distance_to_pin[j] = path_length + edge_length
                heapq.heappush(queue, (path_length + edge_length, j, first))
    return anchor, distance_to_pin


def prepare(positions, indices, colours=None) -> SClothMesh:
    """The solver's mesh of a cloth file's vertices [n, 3], triangle indices and colour bytes [n, 4] (None: none)."""
    rest, index = weld(positions)
    stored_pins, stored_holds = vertex_pins(colours, len(index))
    pinned, holds = np.zeros(len(rest), bool), np.zeros(len(rest), bool)
    np.logical_or.at(pinned, index, stored_pins)  # a welded vertex is pinned when any of its copies is
    np.logical_or.at(holds, index, stored_holds)
    triangles = index[np.asarray(indices, np.int64).reshape(-1, 3)]
    triangles = triangles[
        (triangles[:, 0] != triangles[:, 1])
        & (triangles[:, 1] != triangles[:, 2])
        & (triangles[:, 2] != triangles[:, 0])
    ]
    return _finish(rest, index, triangles, pinned, holds)


def _finish(rest, index, triangles, pinned, holds, weight=None, parents=None) -> SClothMesh:
    """The mesh's edges, rest lengths and tethers over welded vertices and triangles."""
    edges = edges_of(triangles)
    rest2 = np.sum((rest[edges[:, 1]] - rest[edges[:, 0]]) ** 2, axis=1) if len(edges) else np.zeros(0)
    anchor, tether = tethers(len(rest), edges, np.sqrt(rest2), pinned)
    longest = float(np.sqrt(rest2.max())) if len(rest2) else 0.0
    return SClothMesh(rest, index, triangles, edges, rest2, pinned, holds, anchor, tether, longest, weight, parents)


def refine(mesh: SClothMesh) -> SClothMesh:
    """The next finer level: a vertex in the middle of every edge, each triangle cut into four. The coarser
    vertices keep their places in front; a middle starts with the mean of its ends' weights (the game's rule) and
    is pinned, for its tether, when both ends are (chosen)."""
    vertex_count, edges = len(mesh.rest), mesh.edges
    middle = {(int(first), int(second)): vertex_count + k for k, (first, second) in enumerate(edges.tolist())}
    triangles = mesh.triangles

    def mid(first: np.ndarray, second: np.ndarray) -> np.ndarray:
        lower, upper = np.minimum(first, second), np.maximum(first, second)
        return np.array(
            [
                middle[(int(lower_vertex), int(upper_vertex))]
                for lower_vertex, upper_vertex in zip(lower.tolist(), upper.tolist(), strict=True)
            ],
            np.int64,
        )

    first, second, third = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    middle_ab, middle_bc, middle_ca = mid(first, second), mid(second, third), mid(third, first)
    triangles = np.concatenate(
        [
            np.stack(corners, axis=1)
            for corners in (
                (first, middle_ab, middle_ca),
                (middle_ab, second, middle_bc),
                (middle_ca, middle_bc, third),
                (middle_ab, middle_bc, middle_ca),
            )
        ]
    )
    rest = np.concatenate([mesh.rest, 0.5 * (mesh.rest[edges[:, 0]] + mesh.rest[edges[:, 1]])])
    pinned = np.concatenate([mesh.pinned, mesh.pinned[edges[:, 0]] & mesh.pinned[edges[:, 1]]])
    holds = np.concatenate([mesh.holds, mesh.holds[edges[:, 0]] & mesh.holds[edges[:, 1]]])
    weight = np.concatenate([mesh.weight, 0.5 * (mesh.weight[edges[:, 0]] + mesh.weight[edges[:, 1]])])
    return _finish(rest, mesh.index, triangles, pinned, holds, weight, edges.copy())


def target_level(distance_m: float, field_of_view_rad: float) -> int:
    """The level a cloth aims at from the camera's ``distance`` with a field of view ``fov`` (radians)."""
    scale = math.tan(0.5 * field_of_view_rad) / math.tan(0.5 * LEVEL_FOV_RAD)
    blend = min(max((distance_m - LEVEL_NEAR) * scale / LEVEL_SPAN, 0.0), 1.0)
    return int(LEVEL_TOP * (1.0 - blend))


def work(mesh: SClothMesh, level: int) -> int:
    """What a step of the mesh at ``level`` costs the frame's budget: passes x edges."""
    return passes(level) * len(mesh.edges)


@dataclass(frozen=True)
class SForces:
    """One frame's wind and acceleration (gravity or a push), in the world's axes, before the step's length."""

    wind: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel: tuple[float, float, float] = (0.0, 0.0, 0.0)


def frame_factor(delta_s: float) -> float:
    """The game's damping of both forces on a slow frame: 1 up to 33 ms, 0.2 from 100 ms."""
    blend = min(max((delta_s - 0.1) / (-1.0 / 15.0), 0.0), 1.0)
    return blend + 0.2 * (1.0 - blend)


def gust(seconds: float, along: float, strength: float) -> float:
    """``1 + strength x |sin(along + 0.8 t)|^16 x |sin(5 t)|^2``: the game's gusts."""
    return 1.0 + strength * abs(math.sin(along + 0.8 * seconds)) ** 16 * abs(math.sin(5.0 * seconds)) ** 2


def world_forces(cloth_params: SClothParams, place, wind: tuple[float, float], strength: float, seconds: float,
                 delta_s: float) -> SForces:  # fmt: skip
    """The world's wind on a cloth at ``place`` (x, y, z): the session's direction (x, z) and strength, varied over
    the map and in time, and gravity (docs/engine-cloth.md "The forces")."""
    x, _y, z = (float(component) for component in place)
    wind_length = math.hypot(*wind)
    direction_x, direction_z = (wind[0] / wind_length, wind[1] / wind_length) if wind_length > 0 else (0.0, 0.0)
    gust_factor = gust(seconds, direction_x * x + direction_z * z, 0.4)
    wind_x = abs(math.sin(x / 3.0 + 0.2 * seconds)) * direction_x * strength * WORLD_WIND * gust_factor
    wind_z = abs(math.cos(z / 11.0 + 0.2 * seconds)) * direction_z * strength * WORLD_WIND * gust_factor
    damping = frame_factor(delta_s)
    wind_scale = cloth_params.wind_strength * damping
    return SForces((wind_x * wind_scale, 0.0, wind_z * wind_scale), (0.0, -cloth_params.gravity * damping, 0.0))


def local_forces(cloth_params: SClothParams, axes: np.ndarray, seconds: float, delta_s: float) -> SForces:
    """A moving unit's own wind along ``LocalWindDirection`` in its axes (rows x, y, z), and no gravity."""
    gust_factor = 1.0 + 0.2 * math.sin(0.8 * seconds) ** 16 * math.sin(5.0 * seconds) ** 2
    direction_rad = cloth_params.local_wind_direction
    own = np.array([math.cos(direction_rad), 0.0, math.sin(direction_rad)]) * LOCAL_WIND * gust_factor
    wind = own @ np.asarray(axes, float)[:3, :3] * cloth_params.wind_strength * frame_factor(delta_s)
    return SForces(tuple(float(component) for component in wind), (0.0, 0.0, 0.0))


def passes(level: int) -> int:
    """The constraint passes a step makes at a subdivision level (4 at the mesh as stored)."""
    base, times = LEVEL_PASSES
    return max(MIN_PASSES, int(times * base**level - 1.0))


def thickness(mesh: SClothMesh, level: int = 0) -> float:
    """The collision thickness: half the longest edge, at most ``0.25 / 1.3^(level - 1)``."""
    return min(0.5 * mesh.longest, 0.25 / LEVEL_PASSES[0] ** (level - 1))


def _turned(axes: np.ndarray, vector) -> np.ndarray:
    """A world vector in the cloth's axes, at its own length (the game keeps it whatever the scale)."""
    vector = np.asarray(vector, float)
    vector_length = float(np.linalg.norm(vector))
    turned = np.asarray(axes, float) @ vector
    turned_length = float(np.linalg.norm(turned))
    return turned * (vector_length / turned_length) if turned_length > 1e-12 else np.zeros(3)


@dataclass
class CClothSim:
    """One cloth's simulation: positions and previous positions per welded vertex of its level's mesh, the weights
    (1 pinned), the normals. ``step`` moves it a frame, ``set_level`` makes it finer or coarser; ``drawn()`` gives the
    stored vertices' positions and normals. ``mesh`` is the mesh as stored (level 0), ``levels`` the finer ones."""

    m_mesh: SClothMesh
    m_cloth_params: SClothParams = field(default_factory=SClothParams)
    m_level: int = 0

    def __post_init__(self) -> None:
        self.m_levels = [self.m_mesh]
        wanted, self.m_level = self.m_level, 0
        self.reset()
        self.set_level(wanted)

    @property
    def fine(self) -> SClothMesh:
        """The mesh of the level it simulates."""
        return self.m_levels[self.m_level]

    def reset(self) -> None:
        """Back to the rest pose, still."""
        level_mesh = self.fine
        self.m_positions = level_mesh.rest.copy()
        self.m_previous_positions = self.m_positions.copy()
        self.m_weights = level_mesh.weight.copy()
        self.m_normals = vertex_normals(self.m_positions, level_mesh.triangles)

    def set_level(self, level: int) -> None:
        """Simulate at ``level`` (0 to ``MAX_LEVEL``) from now on, the finer meshes made as needed; a vertex keeps its
        place, a new middle starts between its ends."""
        level = min(max(int(level), 0), MAX_LEVEL)
        while len(self.m_levels) <= level:
            self.m_levels.append(refine(self.m_levels[-1]))
        while self.m_level < level:
            ends = self.m_levels[self.m_level + 1].parents
            self.m_positions, self.m_previous_positions, self.m_weights = (np.concatenate([array, 0.5 * (array[ends[:, 0]] + array[ends[:, 1]])])
                                         for array in (self.m_positions, self.m_previous_positions, self.m_weights))  # fmt: skip
            self.m_level += 1
        if level < self.m_level:
            vertex_count = len(self.m_levels[level].rest)
            self.m_positions, self.m_previous_positions, self.m_weights, self.m_level = (
                self.m_positions[:vertex_count],
                self.m_previous_positions[:vertex_count],
                self.m_weights[:vertex_count],
                level,
            )
        self.m_normals = vertex_normals(self.m_positions, self.fine.triangles)

    def step(self, delta_s: float, forces: SForces, axes=None, colliders=(), is_torn: bool = False) -> None:
        """One frame of ``delta_s`` seconds (capped at 66 ms); ``axes`` turns world vectors into the cloth's (rows). A
        cloth whose positions are not finite (a vertex inside a box) starts again at rest, as the game's does."""
        delta_s = min(float(delta_s), MAX_FRAME_S)
        if delta_s <= 0.0 or not len(self.m_positions):
            return
        if not np.all(np.isfinite(self.m_positions)):
            self.reset()
        level_mesh = self.fine
        axes = np.identity(3) if axes is None else np.asarray(axes, float)[:3, :3]
        if self.m_cloth_params.tearing:
            self._tear(is_torn)
        self._integrate(delta_s, _turned(axes, forces.accel) * delta_s, _turned(axes, forces.wind) * delta_s)
        self._tethers()
        relax = 0.5 ** (30.0 * delta_s)
        for _ in range(passes(self.m_level)):
            self.m_positions += relax * self._corrections()
        self.m_positions += (level_mesh.rest - self.m_positions) * self.m_weights[:, None]
        reach = thickness(level_mesh, self.m_level)
        with np.errstate(divide="ignore", invalid="ignore"):
            for collider in colliders:
                collider.push(
                    self.m_positions, self.m_previous_positions, 0.0 if getattr(collider, "thin", False) else reach
                )
        self.m_normals = vertex_normals(self.m_positions, level_mesh.triangles)

    def _tear(self, is_torn: bool) -> None:
        level_mesh = self.fine
        loose = level_mesh.pinned & is_torn & ~level_mesh.holds
        self.m_weights[loose] = 0.0
        back = level_mesh.pinned & ~loose
        self.m_weights[back] = np.minimum(level_mesh.weight[back], 1.01 * self.m_weights[back] + 0.001)

    def _integrate(self, delta_s: float, accel: np.ndarray, wind: np.ndarray) -> None:
        free = (1.0 - self.m_weights)[:, None]
        push = self.m_normals @ wind
        moved = self.m_positions - self.m_previous_positions
        self.m_previous_positions = self.m_positions.copy()
        self.m_positions = self.m_positions + moved + free * accel + free * self.m_normals * push[:, None]

    def _tethers(self) -> None:
        level_mesh = self.fine
        held = np.isfinite(level_mesh.tether) & ~level_mesh.pinned
        if not held.any():
            return
        to_anchor = self.m_positions[level_mesh.anchor] - self.m_positions
        distance_to_anchor = np.linalg.norm(to_anchor, axis=1)
        over = distance_to_anchor - TETHER_SLACK * np.where(held, level_mesh.tether, 0.0)
        pull = held & (over > 0) & (distance_to_anchor > 1e-12)
        self.m_positions[pull] += to_anchor[pull] * (over[pull] / distance_to_anchor[pull])[:, None]

    def _corrections(self) -> np.ndarray:
        """The summed edge corrections of one pass: ``0.5 (0.5 - L^2 / (d^2 + L^2))`` of each edge's vector."""
        level_mesh = self.fine
        edges = level_mesh.edges
        delta = self.m_positions[edges[:, 1]] - self.m_positions[edges[:, 0]]
        squared_distance = np.sum(delta * delta, axis=1)
        strength = 0.5 * (0.5 - level_mesh.rest2 / np.maximum(squared_distance + level_mesh.rest2, 1e-20))
        corr = strength[:, None] * delta
        vertex_count = len(self.m_positions)
        return np.stack([np.bincount(edges[:, 0], corr[:, k], vertex_count) - np.bincount(edges[:, 1], corr[:, k], vertex_count) for k in range(3)],
                        axis=1)  # fmt: skip

    def drawn(self) -> tuple[np.ndarray, np.ndarray]:
        """(positions, normals) of the stored vertices, in their order (a finer level's first vertices are theirs)."""
        return self.m_positions[self.m_mesh.index], self.m_normals[self.m_mesh.index]


def on_surface(tangents: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Directions [k, 3] turned perpendicular to the unit normals ``n`` and made unit (one along n stays): a stored
    tangent frame kept on a moved cloth's surface."""
    out = tangents - np.sum(tangents * normals, axis=1, keepdims=True) * normals
    vector_lengths = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(vector_lengths > 1e-6, out / np.maximum(vector_lengths, 1e-6), tangents)


def vertex_normals(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Each vertex's normal: the normalised sum of its triangles' unit normals (a vertex on none: +y)."""
    out = np.zeros_like(positions)
    if len(triangles):
        first, second, third = (positions[triangles[:, k]] for k in range(3))
        face = np.cross(second - first, third - first)
        vector_lengths = np.linalg.norm(face, axis=1, keepdims=True)
        face = np.where(vector_lengths > 1e-20, face / np.maximum(vector_lengths, 1e-20), 0.0)
        for k in range(3):
            np.add.at(out, triangles[:, k], face)
    vector_lengths = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(vector_lengths > 1e-9, out / np.maximum(vector_lengths, 1e-9), np.array([0.0, 1.0, 0.0]))
