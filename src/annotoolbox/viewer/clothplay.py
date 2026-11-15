"""The view's cloth simulation: a cloth part's mesh handed to ``render.clothsim`` and stepped once a frame of the
view's clock at the level its distance from the camera asks for, its vertices written into the part's buffers (our
looks) and handed to the games' own shaders (``render.clothmesh``).

``cloth_part`` (a newer game's CLOTH node) and ``cloth_part2070`` give a part its ``ClothPart``; the renderer owns
one ``ClothPlayer`` and calls ``pose`` for each cloth group it draws. docs/engine-cloth.md "The cloth in the view".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..render import clothcollide, clothsim
from ..render.gameclouds import CALM

COLLISIONS = "Collisions"
VIEW_BUDGET = (
    60_000  # a frame's work (clothsim.work) numpy solves in about 9 ms; the game's 320,000 is for its own solver
)
# a cloth not stepped this frame keeps its last pose; near and long-waiting cloths go first (docs/engine-cloth.md
# "Frame skipping")


@dataclass(frozen=True)
class ClothPart:
    """What a cloth part simulates with: its parameters and its collision parts (kind, place in the model, fields)."""

    params: clothsim.ClothParams
    collisions: tuple[tuple[str, np.ndarray, dict], ...] = ()


def cloth_part(node, tree, place_of: Callable) -> ClothPart:
    """A newer game's CLOTH node: its fields and the ``Collisions`` parts of its config (``tree``) it names, each
    placed in the model by ``place_of(node)``."""
    indices = tuple(int(v) for k, v in node.fields if k == "CollisionIndex" and v.strip().lstrip("-").isdigit())
    params = clothsim.params_of(dict(node.fields), indices)
    parts = tree.section_of(COLLISIONS) if tree is not None else []
    found = []
    for k in indices:
        if 0 <= k < len(parts):
            c = parts[k]
            found.append((c.get("CollisionType"), np.asarray(place_of(c), float), dict(c.fields)))
    return ClothPart(params, tuple(found))


def cloth_part2070(cloth) -> ClothPart:
    """A 2070 ``cfg2070.Cloth``: its gravity, local wind and sharing; the wind strength is the default."""
    return ClothPart(
        clothsim.ClothParams(
            gravity=cloth.gravity, local_wind_direction=cloth.local_wind_direction, unique=cloth.unique_simulation
        )
    )


def _colours(stored) -> np.ndarray | None:
    """A stored mesh's first colour element as bytes [n, 4], None without one."""
    k = next((i for i, e in enumerate(stored.elements) if e.usage == 5 and e.type == 5), None)
    return None if k is None else np.asarray(stored.element(k), np.uint8).reshape(-1, 4)


def _indices(data) -> np.ndarray:
    stored = data.stored
    if stored is not None and stored.indices:
        return np.asarray(stored.indices, np.int64)
    return np.concatenate([np.asarray(v, np.int64) for v in data.groups.values()]) if data.groups else np.zeros(0)


class _Running:
    """One mesh's simulation, the view time it was last stepped at, its colliders in the cloth's space and the level
    it aims at (by the camera's distance)."""

    def __init__(self, data, spec: ClothPart, matrix: np.ndarray) -> None:
        mesh = clothsim.prepare(data.vertices[:, :3], _indices(data), _colours(data.stored) if data.stored else None)
        self.sim = clothsim.ClothSim(mesh, spec.params)
        to_cloth = clothsim.inverse(matrix)
        shapes = (clothcollide.collider(kind, to_cloth @ m, values) for kind, m, values in spec.collisions)
        self.colliders = [s for s in shapes if s is not None] if to_cloth is not None else []
        self.time: float | None = None
        self.frame = -1
        self.target = 0
        self.distance = 0.0  # from the camera, as of the last frame it was posed
        self.idle = 0  # frames since it was last stepped
        self.poses = 0  # how often its vertices changed: a group holding the latest has nothing to write

    def advance(self, seconds: float, place: np.ndarray, wind, torn: bool) -> None:
        if self.time is None or seconds < self.time:
            self.sim.reset()
            self.time = seconds
            self.poses += 1
            return
        dt, self.time = seconds - self.time, seconds
        if dt <= 0:
            return
        self.poses += 1
        forces = clothsim.world_forces(self.sim.params, place[:3, 3], wind, CALM.strength, seconds, dt)
        axes = clothsim.inverse(place[:3, :3])  # world vectors into the cloth's axes
        if axes is None:
            return  # a part scaled to nothing (a hidden figure): nothing to move
        self.sim.step(dt, forces, axes, self.colliders, torn)

    def aim(self, place: np.ndarray, camera) -> None:
        """The level this cloth aims at from the camera (eye in model space, field of view); none: level 0."""
        if camera is None:
            self.target, self.distance = 0, 0.0
            return
        eye, fov = camera
        self.distance = float(np.linalg.norm(np.asarray(place, float)[:3, 3] - eye))
        self.target = clothsim.target_level(self.distance, fov)


class ClothPlayer:
    """The renderer's cloths: a simulation per part and mesh, stepped once per frame however many groups it has;
    every ``LEVEL_EVERY``-th frame each level steps toward its target (one finer mesh made a frame at most), and a
    frame whose work passes the view's budget lowers every level by one (docs/engine-cloth.md "Levels"); when the
    cloths at their levels still pass it, only some are stepped (``schedule``, "Frame skipping")."""

    def __init__(self) -> None:
        self.running: dict[tuple[str, int], _Running] = {}
        self.frame = 0
        self.allowed: set[int] | None = None  # ids of the runs stepped this frame (None: all)
        self.camera: tuple[np.ndarray, float] | None = None

    def clear(self) -> None:
        self.running.clear()

    def next_frame(self, eye=None, fov: float = clothsim.LEVEL_FOV) -> None:
        """A new frame of the view, its camera's eye in model space (None: no levels this frame)."""
        self.frame += 1
        self.camera = (np.asarray(eye, float), float(fov)) if eye is not None else None
        runs = list(self.running.values())
        if self.frame % clothsim.LEVEL_EVERY == 0:
            built = False
            for run in runs:
                step = (run.target > run.sim.level) - (run.target < run.sim.level)
                finer = run.sim.level + step >= len(run.sim.levels)
                if step and not (finer and built):  # a finer mesh is made one at a time
                    built = built or finer
                    run.sim.set_level(run.sim.level + step)
        work = sum(clothsim.work(r.sim.fine, r.sim.level) for r in runs)
        if work > min(clothsim.BUDGET, VIEW_BUDGET):
            for run in runs:
                run.sim.set_level(run.sim.level - 1)
        self.allowed = self.schedule(runs)

    @staticmethod
    def schedule(runs: list[_Running]) -> set[int] | None:
        """The runs (by ``id``) stepped this frame: all of them while their work fits the view's budget, else in the
        order of (1 + distance) over (1 + frames waited) until it is used up, one at least; the others keep their last pose
        and wait a frame longer."""
        budget = min(clothsim.BUDGET, VIEW_BUDGET)
        cost = {id(r): clothsim.work(r.sim.fine, r.sim.level) for r in runs}
        if sum(cost.values()) <= budget:
            for r in runs:
                r.idle = 0
            return None
        chosen: set[int] = set()
        spent = 0
        for r in sorted(runs, key=lambda r: (1.0 + r.distance) / (1 + r.idle)):
            if chosen and spent + cost[id(r)] > budget:
                r.idle += 1
            else:
                chosen.add(id(r))
                spent += cost[id(r)]
                r.idle = 0
        return chosen

    def pose(self, part, item: dict, state, place: np.ndarray, env) -> None:
        """Write the cloth group's vertices of this frame (its rest pose with the simulation off); ``posed`` and its
        ``cloth_version`` are what the own-shader passes follow (``render.clothmesh``)."""
        if not getattr(state, "cloth", False):
            if item.get("cloth"):
                item["vbo"].write(np.ascontiguousarray(item["verts"], "f4").tobytes())
                item["cloth"], item["posed"], item["cloth_pose"] = False, None, None
                item["cloth_version"] = item.get("cloth_version", 0) + 1
            return
        data = item.get("data")
        if data is None or item.get("verts") is None:
            return
        key = (part.node, int(item.get("mesh", 0)))
        run = self.running.get(key)
        if run is None:
            run = self.running[key] = _Running(data, part.cloth, part.matrix)
        if run.frame != self.frame:
            run.frame = self.frame
            run.aim(place, self.camera)
            if self.allowed is None or id(run) in self.allowed or run.time is None:
                wind = env.wind if env is not None else (math.cos(CALM.angle), math.sin(CALM.angle))
                run.advance(float(state.time), np.asarray(place, float), wind, bool(getattr(state, "torn", False)))
        stamp = (id(run), run.poses, run.sim.level)
        if item.get("cloth") and item.get("cloth_pose") == stamp:
            return  # a cloth not stepped keeps the vertices the group holds
        pos, normals = run.sim.drawn()
        verts = np.array(item["verts"], np.float32)
        verts[:, :3], verts[:, 3:6] = pos, normals
        item["vbo"].write(np.ascontiguousarray(verts, "f4").tobytes())
        item["cloth"], item["posed"], item["cloth_pose"] = True, verts, stamp
        item["cloth_version"] = item.get("cloth_version", 0) + 1
