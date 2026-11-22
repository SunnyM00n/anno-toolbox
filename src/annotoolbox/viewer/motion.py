"""How a part of a model moves: a chain of steps from the model's origin to the part - fixed placements, an
``ANIMATION_TRANSFORM``'s spin, position motion (``SBob``) and alignment (``SAlign``), and a joint it hangs on (a
``BONELINK_TRANSFORM``) - turned into its matrix at a time.

``at_time(steps, seconds, joint, env)`` is the entry (``SEnv``: the object's state, the light, the wind, the camera);
``spin_of`` and ``spin2070_of`` read a spin, ``rest`` gives the matrix at rest (joints at the origin). The sense of a
spin, the position motion, the alignments and the order of the steps: docs/engine-animation.md "Spinning parts" and
"Bone links".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..models.configtree import SSpinSpec
from ..models.meshxform import euler_quat


@dataclass(frozen=True)
class SSpin:
    """A constant turn of ``rate`` radians a second about ``axis``; with ``sway`` (radians) a back and forth of that
    reach instead, at the same pace."""

    axis: tuple[float, float, float]
    rate: float
    sway: float = 0.0
    when: int = 0  # the condition bits it runs under (0: always); stopped, it keeps its angle

    def matrix(self, seconds: float) -> np.ndarray:
        phase = self.rate * seconds
        angle = math.copysign(self.sway, self.rate) * math.sin(abs(phase)) if self.sway else phase
        return turn(self.axis, angle)


@dataclass(frozen=True)
class SLinked:
    """The joint a part hangs on: the part ``host`` (its node) and the joint by its name's hash or its number."""

    host: str
    hash: int | None = None
    index: int | None = None


def turn(axis, angle_rad: float) -> np.ndarray:
    """The 4x4 turn by ``angle`` radians about the unit ``axis`` (right-handed, column vectors)."""
    x, y, z = axis
    cos_angle, sin_angle = math.cos(angle_rad), math.sin(angle_rad)
    one_minus_cos = 1 - cos_angle
    matrix = np.identity(4)
    matrix[:3, :3] = [
        [
            one_minus_cos * x * x + cos_angle,
            one_minus_cos * x * y - sin_angle * z,
            one_minus_cos * x * z + sin_angle * y,
        ],
        [
            one_minus_cos * x * y + sin_angle * z,
            one_minus_cos * y * y + cos_angle,
            one_minus_cos * y * z - sin_angle * x,
        ],
        [
            one_minus_cos * x * z - sin_angle * y,
            one_minus_cos * y * z + sin_angle * x,
            one_minus_cos * z * z + cos_angle,
        ],
    ]
    return matrix


def _spin(rotation, speed: float, oscillation: float = 0.0) -> SSpin | None:
    """The engine's turn: about the rotation's axis at its angle (wrapped into -pi..pi) x speed a second, the other way
    round from the quaternion's sense."""
    x, y, z, w = euler_quat(*rotation)
    angle = 2 * math.atan2(math.sqrt(x * x + y * y + z * z), w)
    angle = (angle + math.pi) % (2 * math.pi) - math.pi
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9 or abs(angle) < 1e-9:
        return None
    return SSpin((x / length, y / length, z / length), -angle * speed, math.radians(oscillation / 2))


def spin_of(spec: SSpinSpec) -> SSpin | None:
    """A newer game's ANIMATION_TRANSFORM as a spin; None for one that does not turn."""
    return _spin(spec.rotation, spec.speed, spec.oscillation)


def spin2070_of(spin) -> SSpin | None:
    """A 2070 ``cfg2070.SSpin``: the same fields, read in the same sense (not checked against 2070 itself)."""
    return _spin(spin.rotation, spin.speed)


@dataclass(frozen=True)
class SBob:
    """The position motion of an ANIMATION_TRANSFORM: ``offset`` x sin(phase), y sin(phase + pi/2), z sin(phase + pi),
    the phase ``speed`` radians a second, turned with the phase about all three axes with ``rotate``, added to the
    node's place in its parent's space (``local``: the node's own placement)."""

    offset: tuple[float, float, float]
    speed_rad_s: float
    rotate: bool
    local: np.ndarray
    when: int = 0

    def matrix(self, seconds: float) -> np.ndarray:
        phase = (self.speed_rad_s * seconds) % (2 * math.pi)
        x, y, z = self.offset
        v = np.array([x * math.sin(phase), y * math.sin(phase + math.pi / 2), z * math.sin(phase + math.pi)])
        if self.rotate:  # the engine's quaternion of the phase on every axis, its vector part negated
            qx, qy, qz, qw = euler_quat(phase, phase, phase)
            v = _qrotate((-qx, -qy, -qz, qw), v)
        t = np.identity(4)
        t[:3, 3] = v
        back = _unplaced(self.local)
        return back @ t @ self.local if back is not None else np.identity(4)


CLOCK_HANDS = (5, 6, 7)  # the alignments of the hour, minute and second hand


@dataclass(frozen=True)
class SAlign:
    """An ANIMATION_TRANSFORM's ``Alignment``: the node's +x turned to the light (1) or the wind (2), its tilt kept, to
    the camera (3 whole, 4 its heading only), or turned about its own +z as a clock's hour, minute or second hand (5
    to 7); ``local`` the node's own placement."""

    kind: int
    local: np.ndarray
    when: int = 0


@dataclass
class SEnv:
    """What the steps read besides the time: the object's condition bits (``viewer.states.SStates``), the directions
    of the light and the wind (x, z) in the model's space, the camera's axes there (columns right, up, back) and the
    local clock (hour, minute, second; None: 12 o'clock)."""

    states: object = None
    light: tuple[float, float, float] = (0.0, 1.0, 0.0)
    wind: tuple[float, float] = (1.0, 0.0)
    camera: np.ndarray | None = None
    clock: tuple[int, int, int] | None = None

    def seconds(self, when: int, seconds: float) -> float:
        return seconds if not when or self.states is None else self.states.on_seconds(when)


def hand_angle(kind: int, clock: tuple[int, int, int]) -> float:
    """A clock hand's angle in degrees (alignment 5 the hour, 6 the minute, 7 the second hand) at ``clock`` (hour,
    minute, second): whole minutes and seconds, so the hands tick (engine-animation "Spinning parts")."""
    hour, minute, second = clock
    hour = hour - 12 if hour > 12 else hour
    return {5: hour * 30 + minute * 0.5, 6: minute * 6.0, 7: second * 6.0}[kind]


def on_the_clock(steps) -> bool:
    """Whether a part's steps hold a clock hand (the view draws again when the clock ticks)."""
    return any(isinstance(step, SAlign) and step.kind in CLOCK_HANDS for step in steps)


def _unplaced(local: np.ndarray) -> np.ndarray | None:
    """The inverse of a node's own placement; None for a node scaled to nothing (it draws nothing: its step is left
    out)."""
    try:
        return np.linalg.inv(local)
    except np.linalg.LinAlgError:
        return None


def _qrotate(quaternion, vector: np.ndarray) -> np.ndarray:
    x, y, z, w = quaternion
    u = np.array([x, y, z])
    return vector + 2 * np.cross(u, np.cross(u, vector) + w * vector)


def _heading(direction) -> np.ndarray:
    """The turn about +y that brings +x to the horizontal ``direction`` (x, z)."""
    direction_x, direction_z = direction
    return turn((0.0, 1.0, 0.0), math.atan2(-direction_z, direction_x))


def _aligned(step: SAlign, environment: SEnv) -> np.ndarray:
    """The node's placement with its rotation aligned (``step.local`` changed in its rotation only)."""
    local = np.array(step.local, float)
    rotation = local[:3, :3]
    if step.kind in (1, 2):
        light = environment.light
        target = (light[0], light[2]) if step.kind == 1 else environment.wind
        if math.hypot(*target) < 1e-9:
            return local
        own = math.atan2(-rotation[2, 0], rotation[0, 0])  # the heading of the node's +x
        local[:3, :3] = _heading(target)[:3, :3] @ turn((0.0, 1.0, 0.0), -own)[:3, :3] @ rotation
    elif step.kind in (3, 4) and environment.camera is not None:
        cam = np.array(environment.camera, float)
        if step.kind == 4:  # upright: the camera's heading only
            back = cam[:, 2] * (1, 0, 1)
            if np.linalg.norm(back) < 1e-9:
                return local
            back /= np.linalg.norm(back)
            cam = np.stack([np.cross((0.0, 1.0, 0.0), back), (0.0, 1.0, 0.0), back], 1)
        scale = np.linalg.norm(rotation, axis=0)
        local[:3, :3] = cam * scale  # reset first: the part's own turn is dropped
    elif step.kind in CLOCK_HANDS and environment.clock is not None:  # after its own turn, by -angle about its own +z
        scale = np.linalg.norm(rotation, axis=0)
        hand = turn((0.0, 0.0, 1.0), -math.radians(hand_angle(step.kind, environment.clock)))[:3, :3]
        local[:3, :3] = (rotation / scale) @ hand * scale
    return local


Step = np.ndarray | SSpin | SLinked | SBob | SAlign


def at_time(
    steps, seconds: float, joint: Callable[[SLinked], np.ndarray | None], environment: SEnv | None = None
) -> np.ndarray | None:
    """The part's matrix at ``seconds``: the steps applied in order; a joint step stands for the host's matrix and
    the joint's place at that time (``joint``), None while the joint is not known. A conditioned spin or position
    motion runs for the time its bits were on (``env``; without one, all along)."""
    environment = environment or SEnv()
    matrix = np.identity(4)
    for step in steps:
        if isinstance(step, SLinked):
            found = joint(step)
            if found is None:
                return None
            matrix = found
        elif isinstance(step, SSpin | SBob):
            matrix = matrix @ step.matrix(environment.seconds(step.when, seconds))
        elif isinstance(step, SAlign):
            back = _unplaced(step.local)
            if back is not None and (
                environment.states is None or not step.when or environment.states.bits & step.when
            ):
                matrix = matrix @ back @ _aligned(step, environment)
        elif hasattr(step, "at_time"):  # a path of its own (viewer.figures.CWalk)
            matrix = matrix @ step.at_time(seconds)
        else:
            matrix = matrix @ step
    return matrix


def rest(steps) -> np.ndarray:
    """The matrix with every spin at its start and every joint at the host's origin."""
    return at_time(steps, 0.0, lambda _link: np.identity(4))


def moves(steps) -> bool:
    return any(not isinstance(step, np.ndarray) for step in steps)
