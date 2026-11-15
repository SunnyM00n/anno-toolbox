"""Snapping a place or a turn to the grids the game builds on: tiles, half and quarter tiles, the diagonal lattice
of a turned building, the 8-tile cells of the active map, eighth and 15-degree turns, and the ground.

``Snap`` is the setting the tools keep (``mode``, ``turn``, ``ground``); ``Snap.place`` and ``Snap.yaw`` apply it.
The lattice and the eighths are 117's placement rules (island-editor-tools "Game rules"); every function
works in Anno's x and z, a tile being one unit (island-editor "Full spec").
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

CELL = 8  # tiles per cell of the active map
MODES = ("off", "tile", "corner", "half", "quarter", "diagonal", "cell", "building")  # building: a footprint's own grid
TURNS = {"off": 0.0, "eighth": math.pi / 4, "15": math.radians(15), "quarter": math.pi / 2}


def to_step(v: float, step: float, offset: float = 0.0) -> float:
    """``v`` on the nearest point of ``offset + k * step``."""
    return offset + round((v - offset) / step) * step


def tile_centre(x: float, z: float) -> tuple[float, float]:
    return to_step(x, 1.0, 0.5), to_step(z, 1.0, 0.5)


def tile_corner(x: float, z: float) -> tuple[float, float]:
    return to_step(x, 1.0), to_step(z, 1.0)


def diagonal(x: float, z: float) -> tuple[float, float]:
    """The nearest point where ``x + z`` and ``z - x`` are whole: a tile corner or a tile centre, where the game
    puts the origin of an object turned by an odd eighth."""
    u, v = round(x + z), round(z - x)  # the lattice is square in u and v, so rounding each finds the nearest
    return (u - v) / 2, (u + v) / 2


def cell_corner(x: float, z: float) -> tuple[float, float]:
    return to_step(x, CELL), to_step(z, CELL)


def snap_xz(x: float, z: float, mode: str) -> tuple[float, float]:
    """``x``, ``z`` snapped by ``mode`` (``MODES``)."""
    if mode in ("tile", "building"):  # a building with a footprint snaps by it (``footprints.snap_place``)
        return tile_centre(x, z)
    if mode == "corner":
        return tile_corner(x, z)
    if mode == "half":
        return to_step(x, 0.5), to_step(z, 0.5)
    if mode == "quarter":
        return to_step(x, 0.25), to_step(z, 0.25)
    if mode == "diagonal":
        return diagonal(x, z)
    if mode == "cell":
        return cell_corner(x, z)
    if mode != "off":
        raise ValueError(f"unknown snap mode {mode!r}: one of {', '.join(MODES)}")
    return x, z


def eighth(yaw: float) -> float:
    """A turn rounded to the eighth the game turns a slot's building by: the nearest of eight, a negative turn
    within 22.5 degrees reading as none."""
    step = math.pi / 4
    return (round(yaw / step) % 8) * step


def snap_yaw(yaw: float, turn: str) -> float:
    """``yaw`` (radians) on the steps of ``turn`` (``TURNS``), in 0 .. 2 pi."""
    step = TURNS[turn]
    if not step:
        return yaw
    if turn == "eighth":
        return eighth(yaw)
    return (round(yaw / step) * step) % (2 * math.pi)


def snap_rotation(q, turn: str) -> list[float]:
    """A turn about the vertical (quaternion x, y, z, w) rounded to the steps of ``turn``; a tilted one is kept."""
    step = TURNS[turn]
    if not step or abs(q[0]) > 1e-4 or abs(q[2]) > 1e-4:
        return [float(v) for v in q]
    yaw = round(2 * math.atan2(q[1], q[3]) / step) * step
    return [0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2)]


@dataclass
class Snap:
    """The snap setting of the tools: where a place lands (``mode``), what a turn steps by (``turn``), and whether
    a placed object sits on the ground."""

    mode: str = "off"
    turn: str = "off"
    ground: bool = False

    def place(self, position, height_at: Callable[[float, float], float] | None = None) -> list[float]:
        x, z = snap_xz(float(position[0]), float(position[2]), self.mode)
        y = float(position[1])
        if self.ground and height_at is not None:
            y = float(height_at(x, z))
        return [x, y, z]

    def yaw(self, yaw: float) -> float:
        return snap_yaw(yaw, self.turn)
