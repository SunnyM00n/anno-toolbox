"""How a part of a model moves: a chain of steps from the model's origin to the part - fixed placements, an
``ANIMATION_TRANSFORM``'s spin, position motion (``Bob``) and alignment (``Align``), and a joint it hangs on (a
``BONELINK_TRANSFORM``) - turned into its matrix at a time.

``at_time(steps, seconds, joint, env)`` is the entry (``Env``: the object's state, the light, the wind, the camera);
``spin_of`` and ``spin2070_of`` read a spin, ``rest`` gives the matrix at rest (joints at the origin). The sense of a
spin, the position motion, the alignments and the order of the steps: docs/engine-animation.md "Spinning parts" and
"Bone links".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..models.configtree import SpinSpec
from ..models.meshxform import euler_quat


@dataclass(frozen=True)
class Spin:
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
class Linked:
    """The joint a part hangs on: the part ``host`` (its node) and the joint by its name's hash or its number."""

    host: str
    hash: int | None = None
    index: int | None = None


def turn(axis, angle: float) -> np.ndarray:
    """The 4x4 turn by ``angle`` radians about the unit ``axis`` (right-handed, column vectors)."""
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    m = np.identity(4)
    m[:3, :3] = [
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ]
    return m


def _spin(rotation, speed: float, oscillation: float = 0.0) -> Spin | None:
    """The engine's turn: about the rotation's axis at its angle (wrapped into -pi..pi) x speed a second, the other way
    round from the quaternion's sense."""
    x, y, z, w = euler_quat(*rotation)
    angle = 2 * math.atan2(math.sqrt(x * x + y * y + z * z), w)
    angle = (angle + math.pi) % (2 * math.pi) - math.pi
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9 or abs(angle) < 1e-9:
        return None
    return Spin((x / length, y / length, z / length), -angle * speed, math.radians(oscillation / 2))


def spin_of(spec: SpinSpec) -> Spin | None:
    """A newer game's ANIMATION_TRANSFORM as a spin; None for one that does not turn."""
    return _spin(spec.rotation, spec.speed, spec.oscillation)


def spin2070_of(spin) -> Spin | None:
    """A 2070 ``cfg2070.Spin``: the same fields, read in the same sense (not checked against 2070 itself)."""
    return _spin(spin.rotation, spin.speed)


@dataclass(frozen=True)
class Bob:
    """The position motion of an ANIMATION_TRANSFORM: ``offset`` x sin(phase), y sin(phase + pi/2), z sin(phase + pi),
    the phase ``speed`` radians a second, turned with the phase about all three axes with ``rotate``, added to the
    node's place in its parent's space (``local``: the node's own placement)."""

    offset: tuple[float, float, float]
    speed: float
    rotate: bool
    local: np.ndarray
    when: int = 0

    def matrix(self, seconds: float) -> np.ndarray:
        phase = (self.speed * seconds) % (2 * math.pi)
        x, y, z = self.offset
        v = np.array([x * math.sin(phase), y * math.sin(phase + math.pi / 2), z * math.sin(phase + math.pi)])
        if self.rotate:  # the engine's quaternion of the phase on every axis, its vector part negated
            qx, qy, qz, qw = euler_quat(phase, phase, phase)
            v = _qrotate((-qx, -qy, -qz, qw), v)
        t = np.identity(4)
        t[:3, 3] = v
        return np.linalg.inv(self.local) @ t @ self.local


CLOCK_HANDS = (5, 6, 7)  # the alignments of the hour, minute and second hand


@dataclass(frozen=True)
class Align:
    """An ANIMATION_TRANSFORM's ``Alignment``: the node's +x turned to the light (1) or the wind (2), its tilt kept, to
    the camera (3 whole, 4 its heading only), or turned about its own +z as a clock's hour, minute or second hand (5
    to 7); ``local`` the node's own placement."""

    kind: int
    local: np.ndarray
    when: int = 0


@dataclass
class Env:
    """What the steps read besides the time: the object's condition bits (``viewer.states.States``), the directions
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
    return any(isinstance(s, Align) and s.kind in CLOCK_HANDS for s in steps)


def _qrotate(q, v: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    u = np.array([x, y, z])
    return v + 2 * np.cross(u, np.cross(u, v) + w * v)


def _heading(direction) -> np.ndarray:
    """The turn about +y that brings +x to the horizontal ``direction`` (x, z)."""
    dx, dz = direction
    return turn((0.0, 1.0, 0.0), math.atan2(-dz, dx))


def _aligned(step: Align, env: Env) -> np.ndarray:
    """The node's placement with its rotation aligned (``step.local`` changed in its rotation only)."""
    local = np.array(step.local, float)
    rot = local[:3, :3]
    if step.kind in (1, 2):
        light = env.light
        target = (light[0], light[2]) if step.kind == 1 else env.wind
        if math.hypot(*target) < 1e-9:
            return local
        own = math.atan2(-rot[2, 0], rot[0, 0])  # the heading of the node's +x
        local[:3, :3] = _heading(target)[:3, :3] @ turn((0.0, 1.0, 0.0), -own)[:3, :3] @ rot
    elif step.kind in (3, 4) and env.camera is not None:
        cam = np.array(env.camera, float)
        if step.kind == 4:  # upright: the camera's heading only
            back = cam[:, 2] * (1, 0, 1)
            if np.linalg.norm(back) < 1e-9:
                return local
            back /= np.linalg.norm(back)
            cam = np.stack([np.cross((0.0, 1.0, 0.0), back), (0.0, 1.0, 0.0), back], 1)
        scale = np.linalg.norm(rot, axis=0)
        local[:3, :3] = cam * scale  # reset first: the part's own turn is dropped
    elif step.kind in CLOCK_HANDS and env.clock is not None:  # after its own turn, by -angle about its own +z
        scale = np.linalg.norm(rot, axis=0)
        hand = turn((0.0, 0.0, 1.0), -math.radians(hand_angle(step.kind, env.clock)))[:3, :3]
        local[:3, :3] = (rot / scale) @ hand * scale
    return local


Step = np.ndarray | Spin | Linked | Bob | Align


def at_time(
    steps, seconds: float, joint: Callable[[Linked], np.ndarray | None], env: Env | None = None
) -> np.ndarray | None:
    """The part's matrix at ``seconds``: the steps applied in order; a joint step stands for the host's matrix and
    the joint's place at that time (``joint``), None while the joint is not known. A conditioned spin or position
    motion runs for the time its bits were on (``env``; without one, all along)."""
    env = env or Env()
    m = np.identity(4)
    for step in steps:
        if isinstance(step, Linked):
            found = joint(step)
            if found is None:
                return None
            m = found
        elif isinstance(step, Spin | Bob):
            m = m @ step.matrix(env.seconds(step.when, seconds))
        elif isinstance(step, Align):
            if env.states is None or not step.when or env.states.bits & step.when:
                m = m @ np.linalg.inv(step.local) @ _aligned(step, env)
        elif hasattr(step, "at_time"):  # a path of its own (viewer.figures.Walk)
            m = m @ step.at_time(seconds)
        else:
            m = m @ step
    return m


def rest(steps) -> np.ndarray:
    """The matrix with every spin at its start and every joint at the host's origin."""
    return at_time(steps, 0.0, lambda _link: np.identity(4))


def moves(steps) -> bool:
    return any(not isinstance(s, np.ndarray) for s in steps)
