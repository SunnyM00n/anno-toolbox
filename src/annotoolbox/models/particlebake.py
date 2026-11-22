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

from ..formats.rdp117 import FRAME_TIME_MS, MAX_FRAMES, SBakedParticle, SRdpMaterial
from .particles2070 import BILLBOARD, FLAME, KIND_NAMES, LINE, PARTICLE, SCurve, SEffect

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


class CBakeError(ValueError):
    """The emitter cannot be expressed in 117; the message says why."""


@dataclass
class SBaked:
    particles: list[SBakedParticle]
    frame_count: int
    looped: bool
    time_scale: float  # the cfg's TimeScale: how much slower than 30 frames a second the file plays
    material: SRdpMaterial
    duration_ms: float  # one play (a loop's period, a one-shot's whole run)
    emitted: int  # particles the simulation made, before the cap dropped any
    lit: bool


# Rotations (right-handed column matrices; the engine's angle convention is applied by euler_angles)


def _mul(left: Matrix, right: Matrix) -> Matrix:
    return tuple(tuple(sum(left[i][k] * right[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rot_x(angle_rad: float) -> Matrix:
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    return ((1, 0, 0), (0, cosine, -sine), (0, sine, cosine))


def rot_y(angle_rad: float) -> Matrix:
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    return ((cosine, 0, sine), (0, 1, 0), (-sine, 0, cosine))


def rot_z(angle_rad: float) -> Matrix:
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    return ((cosine, -sine, 0), (sine, cosine, 0), (0, 0, 1))


def yaw_pitch_roll(yaw_rad: float, pitch_rad: float, roll_rad: float) -> Matrix:
    """DirectX's order: roll about z first, then pitch about x, then yaw about y."""
    return _mul(rot_y(yaw_rad), _mul(rot_x(pitch_rad), rot_z(roll_rad)))


def apply(matrix: Matrix, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[i][k] * vector[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def euler_angles(matrix: Matrix) -> tuple[float, float, float]:
    """The rotation bytes' angles (about x, y, z, radians) for a matrix: the engine builds
    ``Ry(-B) * Rz(-C) * Rx(-A)`` from them, so solve ``R = Ry(b) * Rz(c) * Rx(a)`` and negate."""
    z_angle = math.asin(max(-1.0, min(1.0, matrix[1][0])))
    if abs(math.cos(z_angle)) < 1e-6:  # z turned a quarter: only a + b is fixed, give it all to b
        x_angle, y_angle = 0.0, math.atan2(matrix[0][2], -matrix[0][1] if z_angle > 0 else matrix[0][1])
    else:
        x_angle = math.atan2(-matrix[1][2], matrix[1][1])
        y_angle = math.atan2(-matrix[2][0], matrix[0][0])
    return (-x_angle, -y_angle, -z_angle)


# 2070 draws its quads in the x-y plane, 117 in x-z: this turns one onto the other
_QUAD_PLANE = rot_x(-math.pi / 2)


# Curves


def _at(effect: SEffect, name: str, progress: float, default: float = 0.0) -> float:
    curve = effect.curve(name)
    return default if curve is None else curve.at(progress)


def _factor(curve: SCurve | None, random_value: float) -> float:
    """The per-particle variation: ``(1 - var) + (r - 0.5) * 2 var`` when the curve varies, else 1."""
    if curve is None or curve.variance == 0:
        return 1.0
    return (1 - curve.variance) + (random_value - 0.5) * 2 * curve.variance


def _is_zero(curve: SCurve | None) -> bool:
    return curve is None or all(abs(curve.at(i / 10)) < 1e-9 for i in range(11))


def spins_upright(effect: SEffect) -> bool:
    """A fixed sprite that only turns about the vertical over its life (2070's holograms): drawn as 117's upright
    card, which faces the camera about the vertical, as the turning quad shows itself in 2070 from every side."""
    if int(effect.number("Alignment")) != ALIGN_FIXED or not _is_zero(effect.curve("RotationX")):
        return False
    yaw = effect.curve("RotationY")
    return yaw is not None and max(yaw.at(i / 10) for i in range(11)) - min(yaw.at(i / 10) for i in range(11)) > 1e-6


@functools.lru_cache(maxsize=8192)
def _fixed_orientation(yaw_rad: float, pitch_rad: float, spin: float) -> tuple[float, float, float]:
    return euler_angles(_mul(yaw_pitch_roll(yaw_rad, pitch_rad, spin), _QUAD_PLANE))


def orientation(effect: SEffect, spin: float, age: float = 0.0) -> tuple[float, float, float]:
    """The rotation bytes' angles of a particle of ``effect`` that has spun by ``spin`` radians at ``age`` (0..1 of its
    life): a fixed sprite turns by its RotationY and RotationX curves at that age."""
    if int(effect.number("Alignment")) == ALIGN_FIXED and not spins_upright(effect):
        return _fixed_orientation(
            round(_at(effect, "RotationY", age), 9), round(_at(effect, "RotationX", age), 9), round(spin, 3)
        )
    return (0.0, spin, 0.0)  # faces the camera: the spin is about the quad's normal


def _emitter_matrix(effect: SEffect, time_ms: float) -> Matrix:
    """The emitter's own turn ``ms`` into its emission window (the EmitterRot curves, over the duration)."""
    progress = min(max(time_ms / effect.duration_ms, 0.0), 1.0) if effect.duration_ms > 0 else 0.0
    return yaw_pitch_roll(
        _at(effect, "EmitterRotY", progress), _at(effect, "EmitterRotX", progress), _at(effect, "EmitterRotZ", progress)
    )


# Timeline


def _intervals(effect: SEffect) -> list[int]:
    """The milliseconds between two particles for every millisecond of the emission window."""
    pep = effect.curve("PEP")
    duration = max(effect.duration_ms, 1.0)
    return [
        max(1, int(pep.at(min(time_ms / duration, 1.0)))) if pep is not None else DEFAULT_PEP_MS
        for time_ms in range(int(effect.duration_ms))
    ]


def _births(effect: SEffect, cycle_starts: list[float]) -> list[tuple[float, float]]:
    """``(birth ms, progress through the emission window)`` of every particle over the given cycles."""
    if effect.kind != PARTICLE:  # a billboard or flame is one quad that lives its whole life
        return [(start + effect.delay_ms, 0.0) for start in cycle_starts]
    intervals = _intervals(effect)
    duration = max(effect.duration_ms, 1.0)
    out = []
    acc = 0
    for start in cycle_starts:
        for time_ms, interval in enumerate(intervals):
            acc += 1
            if acc >= interval:
                acc -= interval
                out.append((start + effect.delay_ms + time_ms, min(time_ms / duration, 1.0)))
    return out


def _timing(effect: SEffect) -> tuple[float, list[float], float, int, float]:
    """``(period ms, cycle start times, ms a frame, frame count, cycle ms)``. A loop is baked as whole
    cycles long enough to hold a particle's life, so its steady state repeats exactly."""
    life = effect.life_ms
    frame_time_ms = FRAME_TIME_MS
    run = 0.0 if effect.kind != PARTICLE else effect.duration_ms
    cycle = effect.delay_ms + (life if effect.kind != PARTICLE else effect.duration_ms)
    if effect.loop:
        if cycle <= 0:
            raise CBakeError("a looped effect without a duration")
        cycle_count = max(1, math.ceil((life + PADDING_FRAMES * frame_time_ms) / cycle))
        period, starts = cycle_count * cycle, [j * cycle for j in range(cycle_count)]
    else:
        period, starts = effect.delay_ms + run + life + PADDING_FRAMES * frame_time_ms, [0.0]
    frames = min(MAX_FRAMES, max(BLANK_TICKS + 2, round(period / frame_time_ms)))
    return period, starts, period / frames, frames, cycle


def _crop(effect: SEffect) -> tuple[float, float, float, float]:
    """The atlas tile as the texture rectangle (u, v, width, height); a tile read backwards is not mirrored."""
    u0, v0 = effect.number("AtlasOffsetU"), effect.number("AtlasOffsetV")
    u1, v1 = effect.number("AtlasTilingU", 1.0), effect.number("AtlasTilingV", 1.0)
    width, height = abs(u1 - u0), abs(v1 - v0)
    if width < 1e-6 or height < 1e-6:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(u0, u1), min(v0, v1), width, height)


def material_of(effect: SEffect) -> SRdpMaterial:
    """How the emitter's particles are drawn: blending, alignment and the tile of the atlas they show."""
    if effect.kind == FLAME or spins_upright(effect):
        view = 2
    else:
        view = {ALIGN_CAMERA: 1, ALIGN_UPRIGHT: 2, ALIGN_FIXED: 0}.get(int(effect.number("Alignment")), 1)
    additive = effect.technique == ADDITIVE
    return SRdpMaterial(
        view_aligned=view,
        additive=additive,
        crop=_crop(effect),
        color_boost=ADDITIVE_BOOST if additive else 0,
        refractive=int(effect.technique == REFRACT),
        wave=int(effect.technique == DYNAMIC_WAVE),
        opaque_with_depth=int(effect.technique == Z_WRITE),
    )


# Bake


def bake(effect: SEffect, seed: str) -> SBaked:
    """Simulate the emitter; ``seed`` makes the random choices repeat from run to run."""
    if effect.kind not in SUPPORTED:
        raise CBakeError(f"{KIND_NAMES.get(effect.kind, effect.kind)} effects are not converted")
    if effect.life_ms <= 0:
        raise CBakeError("no life time")
    if effect.kind == LINE:
        return bake_line(effect)
    period, cycle_starts, frame_ms, frames, cycle = _timing(effect)
    births = _births(effect, cycle_starts)
    if not births:
        raise CBakeError("emits nothing")
    emitted = len(births)
    ticks_each = effect.life_ms / frame_ms + BLANK_TICKS + 1
    births = births[:: max(math.ceil(emitted / MAX_PARTICLES), math.ceil(emitted * ticks_each / MAX_TICKS))]
    rng = random.Random(zlib.crc32(seed.encode()))
    turns = any(not _is_zero(effect.curve(f"EmitterRot{axis}")) for axis in "XYZ")
    matrix = functools.lru_cache(maxsize=None)(lambda time_ms: _emitter_matrix(effect, time_ms)) if turns else None
    particles = []
    for birth, progress in births:
        part = _particle(effect, rng, birth, progress, frame_ms, frames, cycle, matrix)
        if part is not None:
            particles.append(part)
    if not particles:
        raise CBakeError("no particle lives a frame")
    return SBaked(
        particles, frames, effect.loop, frame_ms / FRAME_TIME_MS, material_of(effect), period, emitted,
        effect.technique != ADDITIVE,
    )  # fmt: skip


def bake_line(effect: SEffect) -> SBaked:
    """A line emitter (2070's trail: a strip through the points it drops on its way, each point's width, colour and
    alpha by its age) as 117's static line. The ribbon spans 16 frames, so a frame is a sixteenth of 2070's life
    and the trail is as long in time; the one particle's ticks are the look from the head (age 0) to the tail."""
    frame_ms = effect.life_ms / LINE_SEGMENTS
    color = effect.curve("Color")
    positions, size, rot, rgba = [], [], [], []
    for k in range(LINE_FRAMES):
        progress = k / (LINE_FRAMES - 1)
        half = _at(effect, "Scale", progress, 1.0)
        positions.append(effect.offset)
        size.append((2 * half, 2 * half, 2 * half))
        rot.append((0.0, 0.0, 0.0))
        rgb = tuple(min(max(color.at(progress, ch), 0.0), 1.0) for ch in range(3)) if color is not None else (1.0,) * 3
        rgba.append((*rgb, min(max(_at(effect, "Alpha", progress, 1.0), 0.0), 1.0)))
    material = material_of(effect)
    material.view_aligned, material.line_type = 1, STATIC_LINE  # the ribbon turns its face to the camera
    particle = SBakedParticle(0, LINE_FRAMES - 1, 0, positions, size, rot, rgba)
    return SBaked(
        [particle], LINE_FRAMES, True, frame_ms / FRAME_TIME_MS, material, LINE_FRAMES * frame_ms, 1,
        effect.technique != ADDITIVE,
    )  # fmt: skip


def _particle(
    effect: SEffect,
    rng: random.Random,
    birth: float,
    progress: float,
    frame_ms: float,
    frames: int,
    cycle: float,
    emitter_matrix: Callable[[int], Matrix] | None,
) -> SBakedParticle | None:
    life = effect.life_ms
    random_value = rng.random()
    dx, dz = rng.random() * 2 - 1, rng.random() * 2 - 1
    norm = math.hypot(dx, dz) or 1.0
    ring = _at(effect, "EmitArea", progress) * _factor(effect.curve("EmitArea"), random_value)
    ox, oy, oz = effect.offset
    spawn = (ring * dx / norm + ox, oy, ring * dz / norm + oz)
    first = math.floor(birth / frame_ms)
    ticks = max(math.floor((birth + life) / frame_ms) - first, 1) + BLANK_TICKS  # lead-in, life, blank
    ticks = min(ticks, frames) if effect.loop else min(ticks, frames - first)
    if ticks < BLANK_TICKS + 1:
        return None
    vel = [
        (effect.curve(f"Velocity{curve}"), _factor(effect.curve(f"Velocity{curve}"), random_value)) for curve in "XYZ"
    ]
    scale_f = _factor(effect.curve("Scale"), random_value)
    spin_name = "Rotation" if effect.curve("Rotation") is not None else "RotationZ"
    spin_f = _factor(effect.curve(spin_name), random_value)
    color = effect.curve("Color")
    positions, size, rot, rgba = [], [], [], []
    for k in range(ticks):
        time_ms = (first + k) * frame_ms
        age = 0.0 if k == 0 else 1.0 if k == ticks - 1 else min(max((time_ms - birth) / life, 0.0), 1.0)
        position = tuple(
            spawn[i] + (curve.at(age) * factor if curve is not None else 0.0) for i, (curve, factor) in enumerate(vel)
        )
        half = _at(effect, "Scale", age, 1.0) * scale_f
        if effect.kind == FLAME:
            width, height = 1.5 * half, 2.0 * half
            size.append((width, width, height))
            position = (ox, oy + height / 2, oz)
        else:
            size.append((2 * half, 2 * half, 2 * half))
        if emitter_matrix is not None:
            at_ms = (time_ms if effect.bound else birth) % cycle - effect.delay_ms
            position = apply(emitter_matrix(round(at_ms)), position)  # type: ignore[arg-type]
        positions.append(position)
        rot.append(orientation(effect, _at(effect, spin_name, age) * spin_f, age))
        rgb = tuple(min(max(color.at(age, ch), 0.0), 1.0) for ch in range(3)) if color is not None else (1.0,) * 3
        rgba.append((*rgb, min(max(_at(effect, "Alpha", age, 1.0), 0.0), 1.0)))
    return SBakedParticle(first % frames, (first + ticks - 1) % frames, 0, positions, size, rot, rgba)
