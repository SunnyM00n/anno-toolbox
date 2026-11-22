"""The view's cloth simulation: a cloth part's mesh handed to ``render.clothsim`` and stepped once a frame of the
view's clock at the level its distance from the camera asks for, its vertices written into the part's buffers (our
looks) and handed to the games' own shaders (``render.clothmesh``).

``cloth_part`` (a newer game's CLOTH node) and ``cloth_part2070`` give a part its ``SClothPart``; the renderer owns
one ``CClothPlayer`` and calls ``pose`` for each cloth group it draws. docs/engine-cloth.md "The cloth in the view".
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
class SClothPart:
    """What a cloth part simulates with: its parameters and its collision parts (kind, place in the model, fields)."""

    params: clothsim.SClothParams
    collisions: tuple[tuple[str, np.ndarray, dict], ...] = ()


def cloth_part(node, tree, place_of: Callable) -> SClothPart:
    """A newer game's CLOTH node: its fields and the ``Collisions`` parts of its config (``tree``) it names, each
    placed in the model by ``place_of(node)``."""
    indices = tuple(
        int(value) for k, value in node.fields if k == "CollisionIndex" and value.strip().lstrip("-").isdigit()
    )
    params = clothsim.params_of(dict(node.fields), indices)
    parts = tree.section_of(COLLISIONS) if tree is not None else []
    found = []
    for k in indices:
        if 0 <= k < len(parts):
            cloth_node = parts[k]
            found.append(
                (cloth_node.get("CollisionType"), np.asarray(place_of(cloth_node), float), dict(cloth_node.fields))
            )
    return SClothPart(params, tuple(found))


def cloth_part2070(cloth) -> SClothPart:
    """A 2070 ``cfg2070.SCloth``: its gravity, local wind and sharing; the wind strength is the default."""
    return SClothPart(
        clothsim.SClothParams(
            gravity=cloth.gravity, local_wind_direction=cloth.local_wind_direction, unique=cloth.unique_simulation
        )
    )


def _colours(stored) -> np.ndarray | None:
    """A stored mesh's first colour element as bytes [n, 4], None without one."""
    k = next((i for i, element in enumerate(stored.elements) if element.usage == 5 and element.type == 5), None)
    return None if k is None else np.asarray(stored.element(k), np.uint8).reshape(-1, 4)


def _indices(data) -> np.ndarray:
    stored = data.stored
    if stored is not None and stored.indices:
        return np.asarray(stored.indices, np.int64)
    return (
        np.concatenate([np.asarray(group, np.int64) for group in data.groups.values()])
        if data.groups
        else np.zeros(0, np.int64)
    )


class _CRunning:
    """One mesh's simulation, the view time it was last stepped at, its colliders in the cloth's space and the level
    it aims at (by the camera's distance)."""

    def __init__(self, data, spec: SClothPart, matrix: np.ndarray) -> None:
        mesh = clothsim.prepare(data.vertices[:, :3], _indices(data), _colours(data.stored) if data.stored else None)
        self.m_sim = clothsim.CClothSim(mesh, spec.params)
        to_cloth = clothsim.inverse(matrix)
        shapes = (
            clothcollide.collider(kind, to_cloth @ collider_matrix, values)
            for kind, collider_matrix, values in spec.collisions
        )
        self.m_colliders = [collider for collider in shapes if collider is not None] if to_cloth is not None else []
        self.m_time: float | None = None
        self.m_frame = -1
        self.m_target = 0
        self.m_distance_m = 0.0  # from the camera, as of the last frame it was posed
        self.m_idle = 0  # frames since it was last stepped
        self.m_poses = 0  # how often its vertices changed: a group holding the latest has nothing to write

    def advance(self, seconds: float, place: np.ndarray, wind, torn: bool) -> None:
        if self.m_time is None or seconds < self.m_time:
            self.m_sim.reset()
            self.m_time = seconds
            self.m_poses += 1
            return
        dt, self.m_time = seconds - self.m_time, seconds
        if dt <= 0:
            return
        self.m_poses += 1
        forces = clothsim.world_forces(self.m_sim.m_cloth_params, place[:3, 3], wind, CALM.strength, seconds, dt)
        axes = clothsim.inverse(place[:3, :3])  # world vectors into the cloth's axes
        if axes is None:
            return  # a part scaled to nothing (a hidden figure): nothing to move
        self.m_sim.step(dt, forces, axes, self.m_colliders, torn)

    def aim(self, place: np.ndarray, camera) -> None:
        """The level this cloth aims at from the camera (eye in model space, field of view); none: level 0."""
        if camera is None:
            self.m_target, self.m_distance_m = 0, 0.0
            return
        eye, fov = camera
        self.m_distance_m = float(np.linalg.norm(np.asarray(place, float)[:3, 3] - eye))
        self.m_target = clothsim.target_level(self.m_distance_m, fov)


class CClothPlayer:
    """The renderer's cloths: a simulation per part and mesh, stepped once per frame however many groups it has;
    every ``LEVEL_EVERY_FRAMES``-th frame each level steps toward its target (one finer mesh made a frame at most), and a
    frame whose work passes the view's budget lowers every level by one (docs/engine-cloth.md "Levels"); when the
    cloths at their levels still pass it, only some are stepped (``schedule``, "Frame skipping")."""

    def __init__(self) -> None:
        self.m_running: dict[tuple[str, int], _CRunning] = {}
        self.m_frame = 0
        self.m_allowed: set[int] | None = None  # ids of the runs stepped this frame (None: all)
        self.m_camera: tuple[np.ndarray, float] | None = None

    def clear(self) -> None:
        self.m_running.clear()

    def next_frame(self, eye=None, fov_rad: float = clothsim.LEVEL_FOV_RAD) -> None:
        """A new frame of the view, its camera's eye in model space (None: no levels this frame)."""
        self.m_frame += 1
        self.m_camera = (np.asarray(eye, float), float(fov_rad)) if eye is not None else None
        runs = list(self.m_running.values())
        if self.m_frame % clothsim.LEVEL_EVERY_FRAMES == 0:
            built = False
            for run in runs:
                step = (run.m_target > run.m_sim.m_level) - (run.m_target < run.m_sim.m_level)
                finer = run.m_sim.m_level + step >= len(run.m_sim.m_levels)
                if step and not (finer and built):  # a finer mesh is made one at a time
                    built = built or finer
                    run.m_sim.set_level(run.m_sim.m_level + step)
        work = sum(clothsim.work(running.m_sim.fine, running.m_sim.m_level) for running in runs)
        if work > min(clothsim.BUDGET, VIEW_BUDGET):
            for run in runs:
                run.m_sim.set_level(run.m_sim.m_level - 1)
        self.m_allowed = self.schedule(runs)

    @staticmethod
    def schedule(runs: list[_CRunning]) -> set[int] | None:
        """The runs (by ``id``) stepped this frame: all of them while their work fits the view's budget, else in the
        order of (1 + distance) over (1 + frames waited) until it is used up, one at least; the others keep their last pose
        and wait a frame longer."""
        budget = min(clothsim.BUDGET, VIEW_BUDGET)
        cost = {id(running): clothsim.work(running.m_sim.fine, running.m_sim.m_level) for running in runs}
        if sum(cost.values()) <= budget:
            for running in runs:
                running.m_idle = 0
            return None
        chosen: set[int] = set()
        spent = 0
        for running in sorted(runs, key=lambda running: (1.0 + running.m_distance_m) / (1 + running.m_idle)):
            if chosen and spent + cost[id(running)] > budget:
                running.m_idle += 1
            else:
                chosen.add(id(running))
                spent += cost[id(running)]
                running.m_idle = 0
        return chosen

    def pose(self, part, item: dict, state, place: np.ndarray, environment) -> None:
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
        run = self.m_running.get(key)
        if run is None:
            run = self.m_running[key] = _CRunning(data, part.cloth, part.matrix)
        if run.m_frame != self.m_frame:
            run.m_frame = self.m_frame
            run.aim(place, self.m_camera)
            if self.m_allowed is None or id(run) in self.m_allowed or run.m_time is None:
                wind = (
                    environment.wind
                    if environment is not None
                    else (math.cos(CALM.angle_rad), math.sin(CALM.angle_rad))
                )
                run.advance(float(state.time), np.asarray(place, float), wind, bool(getattr(state, "torn", False)))
        stamp = (id(run), run.m_poses, run.m_sim.m_level)
        if item.get("cloth") and item.get("cloth_pose") == stamp:
            return  # a cloth not stepped keeps the vertices the group holds
        positions, normals = run.m_sim.drawn()
        verts = np.array(item["verts"], np.float32)
        verts[:, :3], verts[:, 3:6] = positions, normals
        item["vbo"].write(np.ascontiguousarray(verts, "f4").tobytes())
        item["cloth"], item["posed"], item["cloth_pose"] = True, verts, stamp
        item["cloth_version"] = item.get("cloth_version", 0) + 1
