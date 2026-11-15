"""Bake one emitter of a 2070 ``.efc`` into the particles of a 117 ``.rdp`` (engine-particles "Particles").

117 replays a stored simulation, so the bake runs 2070's own: particles are born every PEP
milliseconds, live ``LifeTime`` ms and move by the velocity curves as displacements from their spawn
ring. A looped effect is baked as the steady state of whole cycles, so the file loops without a seam.
"""

from __future__ import annotations

import functools
import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from ..formats.rdp117 import FRAME_TIME_MS, MAX_FRAMES, BakedParticle, RdpMaterial
from .particles2070 import BILLBOARD, FLAME, KIND_NAMES, LINE, PARTICLE, Curve, Effect

SUPPORTED = (PARTICLE, BILLBOARD, FLAME, LINE)
# 117's static line (LineType 2): a ribbon through the emitter's last 16 positions, one a frame, its look along the
# ribbon read from one particle's ticks by the age along it (engine-particles "Line emitters")
LINE_SEGMENTS = 16  # the particle shader's PARTICLE_LINE_SEGMENT_COUNT
LINE_FRAMES = 32  # the frames of vanilla's trails: the emitter keeps a position per frame
STATIC_LINE = 2
# 2070's techniques; 117 has a list of its own for three of them (engine-particles "Particle lists")
ADDITIVE, REFRACT, Z_WRITE, DYNAMIC_WAVE = 1, 2, 3, 5
# ColorBoost of an additive material: 0 = the plain colour (archived 13, engine-particles "Archived tuning")
ADDITIVE_BOOST = 0
# a file holds no more than the largest that 117 itself plays (engine-particles "Particles": the noria's stream and
# the hippodrome's confetti, read from the 3.0 install)
MAX_PARTICLES = 6939
MAX_TICKS = 419_204
BLANK_TICKS = 2  # a particle also has a blank tick before and after its life
PADDING_FRAMES = 3  # slack a one-shot keeps at the end, a loop between a particle's death and the seam
ALIGN_CAMERA, ALIGN_UPRIGHT, ALIGN_FIXED = 0, 1, 2  # 2070's Alignment
DEFAULT_PEP_MS = 100

Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


class BakeError(ValueError):
    """The emitter cannot be expressed in 117; the message says why."""


@dataclass
class Baked:
    particles: list[BakedParticle]
    frame_count: int
    looped: bool
    time_scale: float  # the cfg's TimeScale: how much slower than 30 frames a second the file plays
    material: RdpMaterial
    duration_ms: float  # one play (a loop's period, a one-shot's whole run)
    emitted: int  # particles the simulation made, before the cap dropped any
    lit: bool


# Rotations (right-handed column matrices; the engine's angle convention is applied by euler_angles)


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rot_x(t: float) -> Matrix:
    c, s = math.cos(t), math.sin(t)
    return ((1, 0, 0), (0, c, -s), (0, s, c))


def rot_y(t: float) -> Matrix:
    c, s = math.cos(t), math.sin(t)
    return ((c, 0, s), (0, 1, 0), (-s, 0, c))


def rot_z(t: float) -> Matrix:
    c, s = math.cos(t), math.sin(t)
    return ((c, -s, 0), (s, c, 0), (0, 0, 1))


def yaw_pitch_roll(yaw: float, pitch: float, roll: float) -> Matrix:
    """DirectX's order: roll about z first, then pitch about x, then yaw about y."""
    return _mul(rot_y(yaw), _mul(rot_x(pitch), rot_z(roll)))


def apply(m: Matrix, v: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def euler_angles(r: Matrix) -> tuple[float, float, float]:
    """The rotation bytes' angles (about x, y, z, radians) for a matrix: the engine builds
    ``Ry(-B) * Rz(-C) * Rx(-A)`` from them, so solve ``R = Ry(b) * Rz(c) * Rx(a)`` and negate."""
    c = math.asin(max(-1.0, min(1.0, r[1][0])))
    if abs(math.cos(c)) < 1e-6:  # z turned a quarter: only a + b is fixed, give it all to b
        a, b = 0.0, math.atan2(r[0][2], -r[0][1] if c > 0 else r[0][1])
    else:
        a = math.atan2(-r[1][2], r[1][1])
        b = math.atan2(-r[2][0], r[0][0])
    return (-a, -b, -c)


# 2070 draws its quads in the x-y plane, 117 in x-z: this turns one onto the other
_QUAD_PLANE = rot_x(-math.pi / 2)


# Curves


def _at(effect: Effect, name: str, t: float, default: float = 0.0) -> float:
    curve = effect.curve(name)
    return default if curve is None else curve.at(t)


def _factor(curve: Curve | None, r: float) -> float:
    """The per-particle variation: ``(1 - var) + (r - 0.5) * 2 var`` when the curve varies, else 1."""
    if curve is None or curve.variance == 0:
        return 1.0
    return (1 - curve.variance) + (r - 0.5) * 2 * curve.variance


def _is_zero(curve: Curve | None) -> bool:
    return curve is None or all(abs(curve.at(i / 10)) < 1e-9 for i in range(11))


def spins_upright(effect: Effect) -> bool:
    """A fixed sprite that only turns about the vertical over its life (2070's holograms): drawn as 117's upright
    card, which faces the camera about the vertical, as the turning quad shows itself in 2070 from every side."""
    if int(effect.number("Alignment")) != ALIGN_FIXED or not _is_zero(effect.curve("RotationX")):
        return False
    yaw = effect.curve("RotationY")
    return yaw is not None and max(yaw.at(i / 10) for i in range(11)) - min(yaw.at(i / 10) for i in range(11)) > 1e-6


@functools.lru_cache(maxsize=8192)
def _fixed_orientation(yaw: float, pitch: float, spin: float) -> tuple[float, float, float]:
    return euler_angles(_mul(yaw_pitch_roll(yaw, pitch, spin), _QUAD_PLANE))


def orientation(effect: Effect, spin: float, age: float = 0.0) -> tuple[float, float, float]:
    """The rotation bytes' angles of a particle of ``effect`` that has spun by ``spin`` radians at ``age`` (0..1 of its
    life): a fixed sprite turns by its RotationY and RotationX curves at that age."""
    if int(effect.number("Alignment")) == ALIGN_FIXED and not spins_upright(effect):
        return _fixed_orientation(
            round(_at(effect, "RotationY", age), 9), round(_at(effect, "RotationX", age), 9), round(spin, 3)
        )
    return (0.0, spin, 0.0)  # faces the camera: the spin is about the quad's normal


def _emitter_matrix(effect: Effect, ms: float) -> Matrix:
    """The emitter's own turn ``ms`` into its emission window (the EmitterRot curves, over the duration)."""
    u = min(max(ms / effect.duration_ms, 0.0), 1.0) if effect.duration_ms > 0 else 0.0
    return yaw_pitch_roll(_at(effect, "EmitterRotY", u), _at(effect, "EmitterRotX", u), _at(effect, "EmitterRotZ", u))


# Timeline


def _intervals(effect: Effect) -> list[int]:
    """The milliseconds between two particles for every millisecond of the emission window."""
    pep = effect.curve("PEP")
    duration = max(effect.duration_ms, 1.0)
    return [
        max(1, int(pep.at(min(ms / duration, 1.0)))) if pep is not None else DEFAULT_PEP_MS
        for ms in range(int(effect.duration_ms))
    ]


def _births(effect: Effect, cycle_starts: list[float]) -> list[tuple[float, float]]:
    """``(birth ms, progress through the emission window)`` of every particle over the given cycles."""
    if effect.kind != PARTICLE:  # a billboard or flame is one quad that lives its whole life
        return [(start + effect.delay_ms, 0.0) for start in cycle_starts]
    intervals = _intervals(effect)
    duration = max(effect.duration_ms, 1.0)
    out = []
    acc = 0
    for start in cycle_starts:
        for ms, interval in enumerate(intervals):
            acc += 1
            if acc >= interval:
                acc -= interval
                out.append((start + effect.delay_ms + ms, min(ms / duration, 1.0)))
    return out


def _timing(effect: Effect) -> tuple[float, list[float], float, int, float]:
    """``(period ms, cycle start times, ms a frame, frame count, cycle ms)``. A loop is baked as whole
    cycles long enough to hold a particle's life, so its steady state repeats exactly."""
    life = effect.life_ms
    ft = FRAME_TIME_MS
    run = 0.0 if effect.kind != PARTICLE else effect.duration_ms
    cycle = effect.delay_ms + (life if effect.kind != PARTICLE else effect.duration_ms)
    if effect.loop:
        if cycle <= 0:
            raise BakeError("a looped effect without a duration")
        m = max(1, math.ceil((life + PADDING_FRAMES * ft) / cycle))
        period, starts = m * cycle, [j * cycle for j in range(m)]
    else:
        period, starts = effect.delay_ms + run + life + PADDING_FRAMES * ft, [0.0]
    frames = min(MAX_FRAMES, max(BLANK_TICKS + 2, round(period / ft)))
    return period, starts, period / frames, frames, cycle


def _crop(effect: Effect) -> tuple[float, float, float, float]:
    """The atlas tile as the texture rectangle (u, v, width, height); a tile read backwards is not mirrored."""
    u0, v0 = effect.number("AtlasOffsetU"), effect.number("AtlasOffsetV")
    u1, v1 = effect.number("AtlasTilingU", 1.0), effect.number("AtlasTilingV", 1.0)
    w, h = abs(u1 - u0), abs(v1 - v0)
    if w < 1e-6 or h < 1e-6:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(u0, u1), min(v0, v1), w, h)


def material_of(effect: Effect) -> RdpMaterial:
    """How the emitter's particles are drawn: blending, alignment and the tile of the atlas they show."""
    if effect.kind == FLAME or spins_upright(effect):
        view = 2
    else:
        view = {ALIGN_CAMERA: 1, ALIGN_UPRIGHT: 2, ALIGN_FIXED: 0}.get(int(effect.number("Alignment")), 1)
    additive = effect.technique == ADDITIVE
    return RdpMaterial(
        view_aligned=view,
        additive=additive,
        crop=_crop(effect),
        color_boost=ADDITIVE_BOOST if additive else 0,
        refractive=int(effect.technique == REFRACT),
        wave=int(effect.technique == DYNAMIC_WAVE),
        opaque_with_depth=int(effect.technique == Z_WRITE),
    )


# Bake


def bake(effect: Effect, seed: str) -> Baked:
    """Simulate the emitter; ``seed`` makes the random choices repeat from run to run."""
    if effect.kind not in SUPPORTED:
        raise BakeError(f"{KIND_NAMES.get(effect.kind, effect.kind)} effects are not converted")
    if effect.life_ms <= 0:
        raise BakeError("no life time")
    if effect.kind == LINE:
        return bake_line(effect)
    period, cycle_starts, frame_ms, frames, cycle = _timing(effect)
    births = _births(effect, cycle_starts)
    if not births:
        raise BakeError("emits nothing")
    emitted = len(births)
    ticks_each = effect.life_ms / frame_ms + BLANK_TICKS + 1
    births = births[:: max(math.ceil(emitted / MAX_PARTICLES), math.ceil(emitted * ticks_each / MAX_TICKS))]
    rng = random.Random(zlib.crc32(seed.encode()))
    turns = any(not _is_zero(effect.curve(f"EmitterRot{c}")) for c in "XYZ")
    matrix = functools.lru_cache(maxsize=None)(lambda ms: _emitter_matrix(effect, ms)) if turns else None
    particles = []
    for birth, u in births:
        part = _particle(effect, rng, birth, u, frame_ms, frames, cycle, matrix)
        if part is not None:
            particles.append(part)
    if not particles:
        raise BakeError("no particle lives a frame")
    return Baked(
        particles, frames, effect.loop, frame_ms / FRAME_TIME_MS, material_of(effect), period, emitted,
        effect.technique != ADDITIVE,
    )  # fmt: skip


def bake_line(effect: Effect) -> Baked:
    """A line emitter (2070's trail: a strip through the points it drops on its way, each point's width, colour and
    alpha by its age) as 117's static line. The ribbon spans 16 frames, so a frame is a sixteenth of 2070's life
    and the trail is as long in time; the one particle's ticks are the look from the head (age 0) to the tail."""
    frame_ms = effect.life_ms / LINE_SEGMENTS
    color = effect.curve("Color")
    pos, size, rot, rgba = [], [], [], []
    for k in range(LINE_FRAMES):
        a = k / (LINE_FRAMES - 1)
        half = _at(effect, "Scale", a, 1.0)
        pos.append(effect.offset)
        size.append((2 * half, 2 * half, 2 * half))
        rot.append((0.0, 0.0, 0.0))
        rgb = tuple(min(max(color.at(a, ch), 0.0), 1.0) for ch in range(3)) if color is not None else (1.0,) * 3
        rgba.append((*rgb, min(max(_at(effect, "Alpha", a, 1.0), 0.0), 1.0)))
    material = material_of(effect)
    material.view_aligned, material.line_type = 1, STATIC_LINE  # the ribbon turns its face to the camera
    particle = BakedParticle(0, LINE_FRAMES - 1, 0, pos, size, rot, rgba)
    return Baked(
        [particle], LINE_FRAMES, True, frame_ms / FRAME_TIME_MS, material, LINE_FRAMES * frame_ms, 1,
        effect.technique != ADDITIVE,
    )  # fmt: skip


def _particle(
    effect: Effect,
    rng: random.Random,
    birth: float,
    u: float,
    frame_ms: float,
    frames: int,
    cycle: float,
    emitter_matrix: Callable[[int], Matrix] | None,
) -> BakedParticle | None:
    life = effect.life_ms
    r = rng.random()
    dx, dz = rng.random() * 2 - 1, rng.random() * 2 - 1
    norm = math.hypot(dx, dz) or 1.0
    ring = _at(effect, "EmitArea", u) * _factor(effect.curve("EmitArea"), r)
    ox, oy, oz = effect.offset
    spawn = (ring * dx / norm + ox, oy, ring * dz / norm + oz)
    first = math.floor(birth / frame_ms)
    ticks = max(math.floor((birth + life) / frame_ms) - first, 1) + BLANK_TICKS  # lead-in, life, blank
    ticks = min(ticks, frames) if effect.loop else min(ticks, frames - first)
    if ticks < BLANK_TICKS + 1:
        return None
    vel = [(effect.curve(f"Velocity{c}"), _factor(effect.curve(f"Velocity{c}"), r)) for c in "XYZ"]
    scale_f = _factor(effect.curve("Scale"), r)
    spin_name = "Rotation" if effect.curve("Rotation") is not None else "RotationZ"
    spin_f = _factor(effect.curve(spin_name), r)
    color = effect.curve("Color")
    pos, size, rot, rgba = [], [], [], []
    for k in range(ticks):
        t = (first + k) * frame_ms
        a = 0.0 if k == 0 else 1.0 if k == ticks - 1 else min(max((t - birth) / life, 0.0), 1.0)
        p = tuple(spawn[i] + (c.at(a) * f if c is not None else 0.0) for i, (c, f) in enumerate(vel))
        half = _at(effect, "Scale", a, 1.0) * scale_f
        if effect.kind == FLAME:
            width, height = 1.5 * half, 2.0 * half
            size.append((width, width, height))
            p = (ox, oy + height / 2, oz)
        else:
            size.append((2 * half, 2 * half, 2 * half))
        if emitter_matrix is not None:
            at_ms = (t if effect.bound else birth) % cycle - effect.delay_ms
            p = apply(emitter_matrix(round(at_ms)), p)  # type: ignore[arg-type]
        pos.append(p)
        rot.append(orientation(effect, _at(effect, spin_name, a) * spin_f, a))
        rgb = tuple(min(max(color.at(a, ch), 0.0), 1.0) for ch in range(3)) if color is not None else (1.0,) * 3
        rgba.append((*rgb, min(max(_at(effect, "Alpha", a, 1.0), 0.0), 1.0)))
    return BakedParticle(first % frames, (first + ticks - 1) % frames, 0, pos, size, rot, rgba)
