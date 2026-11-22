"""Anno 1701's effect emitters (``formats.efc1701``) run into particles for the viewer: born at the emitter's rate,
moved, sized and coloured over their life, ticked 30 times a second into the tracks every effect of the viewer plays
(``rdp117.SBakedParticle``). How 1701 plays the fields is inferred (docs/formats/anno1701-effects.md).

``bake(emitter, seed)`` is the entry; it raises ``particlebake.CBakeError`` for a kind it does not draw. A seed fixes
the random spread, so a view of an effect looks the same each time.
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from ..formats.efc1701 import SEmitter
from ..formats.rdp117 import FRAME_TIME_MS, SBakedParticle, SRdpMaterial
from .meshxform import qaxis, qmul, qrotate
from .particlebake import CBakeError, euler_angles

MAX_PARTICLES = 400  # one emitter's particles over its loop, thinned evenly beyond
FIXED, CAMERA, UPRIGHT = 0, 1, 2  # SRdpMaterial.view_aligned
ALIGNMENT = {0: CAMERA, 1: UPRIGHT, 2: FIXED}  # an emitter's Alignment (inferred: 2 lies as its Rotation turns it)
FLAT = qaxis((1.0, 0.0, 0.0), -math.pi / 2)  # lays a card (it stands in x-y) on the ground, its normal up
FIREWORK_SPARK = 12.0  # world units a spark is wide per unit of StartWidth (a guess: the file gives no size)
NOT_DRAWN = {
    "GrannyEffect": "a model the effect shows: the preview plays sprites, a model is drawn by the viewer's mesh view",
    "VolumeChainsEffect": "the file names its chain's numbers (Stretch, Spread, Move) but not how the links move",
}
Vec3 = tuple[float, float, float]
State = tuple[Vec3, tuple[float, float], float, tuple[float, float, float, float]]  # place, (w, h), spin, rgba


@dataclass(frozen=True)
class SSpawn:
    """One particle: born ``birth_ms`` into the loop, living ``life_ms``; ``state(age_ms)`` its place (in the
    emitter), width and height, spin (radians) and colour."""

    birth_ms: float
    life_ms: float
    state: Callable[[float], State]


@dataclass
class SBaked:
    """An emitter's particles over ``frame_count`` ticks (repeating when ``looped``), drawn with ``material``;
    ``time_scale`` its pace (1 its own)."""

    particles: list[SBakedParticle]
    material: SRdpMaterial
    frame_count: int
    looped: bool
    time_scale: float = 1.0


def bake(emitter: SEmitter, seed: str) -> SBaked:
    """The particles of one drawn emitter (``KINDS``)."""
    make = KINDS.get(emitter.kind)
    if make is None:
        why = NOT_DRAWN.get(emitter.kind)
        raise CBakeError(f"{emitter.kind} is not drawn" + (f": {why}" if why else ""))
    rng = random.Random(zlib.crc32(seed.encode("utf-8")))
    spawns, pass_ms = make(emitter, rng)
    if not spawns:
        raise CBakeError(f"{emitter.kind} {emitter.name!r} gives no particle")
    looped = emitter.loop
    longest = max(spawn.life_ms for spawn in spawns)
    if looped:
        frames = max(1, round(pass_ms / FRAME_TIME_MS)) * max(1, math.ceil(longest / max(pass_ms, 1.0)))
    else:
        frames = math.ceil((max(spawn.birth_ms for spawn in spawns) + longest) / FRAME_TIME_MS) + 2
    material = material_of(emitter)
    turn = _card_rotation(emitter)
    particles = [_ticks(spawn, frames, emitter, material.view_aligned == FIXED, turn) for spawn in _thinned(spawns)]
    time_scale = emitter.number("TimeScale", 1.0) if emitter.kind == "ParticleEffect" else 1.0
    return SBaked(particles, material, frames, looped, time_scale if time_scale > 1e-3 else 1.0)


def _thinned(spawns: list[SSpawn]) -> list[SSpawn]:
    if len(spawns) <= MAX_PARTICLES:
        return spawns
    step = len(spawns) / MAX_PARTICLES
    return [spawns[int(k * step)] for k in range(MAX_PARTICLES)]


def _card_rotation(emitter: SEmitter) -> tuple[float, float, float, float]:
    """The turn that lays a fixed card: the emitter's, and for a flat ring (``Flat``) the card lying on the ground."""
    flat = emitter.kind == "SuperNovaEffect" and emitter.number("Flat") != 0
    return qmul(emitter.rotation, FLAT) if flat else emitter.rotation


def _ticks(spawn: SSpawn, frames: int, emitter: SEmitter, fixed: bool, turn) -> SBakedParticle:
    """A particle's tracks, tick by tick, in the effect's space (the emitter's move, turn and scale applied)."""
    count = max(2, min(frames, round(spawn.life_ms / FRAME_TIME_MS) + 1))
    start = round(spawn.birth_ms / FRAME_TIME_MS) % frames
    positions, sizes, rotations, color = [], [], [], []
    for k in range(count):
        place, (card_width, card_height), spin, rgba = spawn.state(spawn.life_ms * k / (count - 1))
        positions.append(_placed(emitter, place))
        sizes.append((card_width * emitter.scale[0], card_width * emitter.scale[2], card_height * emitter.scale[1]))
        rotations.append(_fixed_turn(turn, spin) if fixed else (0.0, spin, 0.0))
        color.append(rgba)
    return SBakedParticle(start, (start + count - 1) % frames, 0, positions, sizes, rotations, color)


def _placed(emitter: SEmitter, point: Vec3) -> Vec3:
    scaled = (point[0] * emitter.scale[0], point[1] * emitter.scale[1], point[2] * emitter.scale[2])
    turned = qrotate(emitter.rotation, scaled)
    return (turned[0] + emitter.position[0], turned[1] + emitter.position[1], turned[2] + emitter.position[2])


def _fixed_turn(rotation: tuple[float, float, float, float], spin: float) -> Vec3:
    """The angles of a card lying as the emitter's turn lays it, spun about its normal: 1701's card lies in x-y
    (inferred), the viewer's in x-z, so its width stays x, its height y becomes z and its normal -z becomes y."""
    cosine, sine = math.cos(spin), math.sin(spin)
    columns = [qrotate(rotation, v) for v in ((cosine, sine, 0.0), (0.0, 0.0, -1.0), (-sine, cosine, 0.0))]
    return euler_angles(tuple(tuple(columns[j][i] for j in range(3)) for i in range(3)))


def material_of(emitter: SEmitter) -> SRdpMaterial:
    """How the emitter's particles draw: facing the camera (or as ``ALIGNMENT`` says), the ``Rect`` of its
    texture, added when a blend is ``ONE`` or its state block is fire."""
    u0, v1, u1, start_velocity = emitter.rect
    card_width, card_height = abs(u1 - u0), abs(v1 - start_velocity)
    crop = (
        (min(u0, u1), min(start_velocity, v1), card_width, card_height)
        if card_width > 1e-6 and card_height > 1e-6
        else (0.0, 0.0, 1.0, 1.0)
    )
    additive = emitter.additive or emitter.block.text("StateBlock") == "FX_FIRE"
    aligned = ALIGNMENT.get(int(emitter.number("Alignment")), CAMERA)
    if emitter.kind == "SuperNovaEffect" and emitter.number("Flat") != 0:
        aligned = FIXED
    return SRdpMaterial(view_aligned=aligned, additive=additive, crop=crop)


def _births(emitter: SEmitter, rate: Callable[[float], float], first_ms: float = 0.0) -> list[tuple[float, float]]:
    """(birth ms, x 0..1 along the pass) of the particles a rate (particles a second at x) gives over one pass."""
    duration_ms = max(emitter.duration_ms, FRAME_TIME_MS)
    out, owed, t = [], 0.5, 0.0
    while t < duration_ms:
        owed += max(rate(t / duration_ms), 0.0) * FRAME_TIME_MS / 1000.0
        while owed >= 1.0 and len(out) < 4 * MAX_PARTICLES:
            out.append((emitter.start_ms + first_ms + t, t / duration_ms))
            owed -= 1.0
        t += FRAME_TIME_MS
    return out


def _unit(rng: random.Random) -> Vec3:
    """A random direction, even over the sphere."""
    z, a = rng.uniform(-1.0, 1.0), rng.uniform(0.0, 2 * math.pi)
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return (r * math.cos(a), z, r * math.sin(a))


def _mix(first: tuple, second: tuple, fraction: float) -> tuple:
    return tuple(x + (y - x) * fraction for x, y in zip(first, second, strict=True))


# The drawn kinds


def _particles(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``ParticleEffect``: born at ``Emit`` a second, thrown along ``Pitch`` / ``Yaw`` at ``Speed``, pulled by
    ``Gravity`` and ``Acceleration``, sized, spun and coloured by the life graphs."""
    graph = emitter.graph
    out = []
    for birth, x in _births(emitter, graph("Emit").at):
        life = max(graph("LifeTime", 1000.0).at(x), FRAME_TIME_MS)
        pitch = graph("Pitch").at(x) + rng.uniform(-1, 1) * graph("PitchVar").at(x)
        yaw = graph("Yaw").at(x) + rng.uniform(-1, 1) * graph("YawVar").at(x)
        direction = (
            math.cos(pitch) * math.sin(yaw),
            math.sin(pitch),
            math.cos(pitch) * math.cos(yaw),
        )  # pitch: elevation
        spread, speed = _unit(rng), graph("Speed").at(x)
        start_velocity = tuple(c * speed + s * graph("Spread").at(x) for c, s in zip(direction, spread, strict=True))
        start_position = tuple(c * rng.random() ** (1 / 3) * graph("EmitterRadius").at(x) for c in _unit(rng))
        spin0 = rng.uniform(0, 2 * math.pi) if emitter.number("RandomSpin") else 0.0
        out.append(
            SSpawn(birth, life, _particle_state(emitter, direction, start_velocity, start_position, spin0, life))
        )
    return out, emitter.duration_ms


def _particle_state(
    emitter: SEmitter, direction: Vec3, start_velocity: tuple, start_position: tuple, spin0: float, life: float
):
    graph, colors = emitter.graph, emitter.colors.get("Color")

    def state(age: float) -> State:
        u, t = age / life, age / 1000.0
        acc = graph("Acceleration").at(u)
        pull = (
            graph("GravityX").at(u) + direction[0] * acc,
            graph("GravityY").at(u) + direction[1] * acc,
            graph("GravityZ").at(u) + direction[2] * acc,
        )
        pos = tuple(p + v * t + 0.5 * a * t * t for p, v, a in zip(start_position, start_velocity, pull, strict=True))
        rgba = colors.at(u) if colors is not None else (1.0, 1.0, 1.0, 1.0)
        alpha = rgba[3] * (graph("Alpha", 1.0).at(u))
        return (
            pos,
            (graph("SizeX", 1.0).at(u), graph("SizeY", 1.0).at(u)),
            spin0 + graph("Spin").at(u) * t,
            (*rgba[:3], alpha),
        )

    return state


def _billboard(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``BillboardEffect``: one card through the pass, sized, spun and coloured by its graphs along it."""
    graph, colors, life = emitter.graph, emitter.colors.get("Color"), max(emitter.duration_ms, FRAME_TIME_MS)

    def state(age: float) -> State:
        u = age / life
        rgba = colors.at(u) if colors is not None else (1.0, 1.0, 1.0, 1.0)
        size = (graph("SizeX", 1.0).at(u), graph("SizeY", 1.0).at(u))
        return (
            (0.0, 0.0, 0.0),
            size,
            graph("Spin").at(u) * age / 1000.0,
            (*rgba[:3], rgba[3] * graph("Alpha", 1.0).at(u)),
        )

    return [SSpawn(emitter.start_ms, life, state)], emitter.duration_ms


def _puffs(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``InstancingEffect`` and ``InstancingSlowEffect``: ``PPS`` puffs a second rising at ``Velocity`` from a
    circle of ``EmitRadius``, growing from ``MinSize`` to ``MaxSize`` and fading from ``Color`` to ``Target``."""
    life = max(emitter.number("LifeTime", 1000.0), FRAME_TIME_MS)
    out = []
    for birth, _x in _births(emitter, lambda _x: emitter.number("PPS"), emitter.number("DelayTime")):
        a, r = rng.uniform(0, 2 * math.pi), math.sqrt(rng.random()) * emitter.number("EmitRadius")
        jitter = tuple(c * emitter.number("Varianz") for c in _unit(rng))
        start_position = (r * math.cos(a) + jitter[0], jitter[1], r * math.sin(a) + jitter[2])
        out.append(
            SSpawn(birth, life, _puff_state(emitter, start_position, (0.0, emitter.number("Velocity"), 0.0), life, rng))
        )
    return out, emitter.duration_ms


def _spray(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``SprayEffect``: ``Particles`` puffs from each of ``Emitters`` points thrown up and out at ``Velocity``,
    pulled down by ``Gravitation``, once a pass."""
    life = max(emitter.number("LifeTime", 1000.0), FRAME_TIME_MS)
    out = []
    for _point in range(max(1, int(emitter.number("Emitters", 1)))):
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(emitter.number("MinDistance"), emitter.number("MaxDistance"))
        base = (r * math.cos(a), 0.0, r * math.sin(a))
        for _k in range(max(1, int(emitter.number("Particles", 1)))):
            direction = _unit(rng)
            start_velocity = (
                direction[0] * 0.5 * emitter.number("Velocity"),
                abs(direction[1]) * emitter.number("Velocity"),
                direction[2] * 0.5 * emitter.number("Velocity"),
            )
            birth = emitter.start_ms + emitter.number("DelayTime") + rng.uniform(0, 100)
            out.append(
                SSpawn(
                    birth, life, _puff_state(emitter, base, start_velocity, life, rng, emitter.number("Gravitation"))
                )
            )
    return out, emitter.duration_ms


def _puff_state(
    emitter: SEmitter, start_position: Vec3, start_velocity: Vec3, life: float, rng: random.Random, gravity: float = 0.0
):
    start = tuple(emitter.number(f"Color{c}", 255.0) / 255.0 for c in ("Red", "Green", "Blue"))
    end = tuple(emitter.number(f"Target{c}", 255.0) / 255.0 for c in ("Red", "Green", "Blue"))
    sizes, turns, spin0 = (
        (emitter.number("MinSize"), emitter.number("MaxSize")),
        emitter.number("RPS"),
        rng.uniform(0, 2 * math.pi),
    )

    def state(age: float) -> State:
        u, t = age / life, age / 1000.0
        pos = (
            start_position[0] + start_velocity[0] * t,
            start_position[1] + start_velocity[1] * t - 0.5 * gravity * t * t,
            start_position[2] + start_velocity[2] * t,
        )
        size = sizes[0] + (sizes[1] - sizes[0]) * u
        alpha = min(u / 0.1, 1.0) * min((1.0 - u) / 0.4, 1.0)  # in fast, out slowly (inferred)
        return pos, (size, size), spin0 + turns * 2 * math.pi * t, (*_mix(start, end, u), alpha)

    return state


def _fade(age: float, life: float, fade: float) -> float:
    """1 until the last ``fade`` ms of the life, then down to 0."""
    return 1.0 if fade <= 0 else min(max((life - age) / fade, 0.0), 1.0)


def _firework(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``FireWorxEffect``: at ``DelayTime`` a burst of ``SegCount`` sparks on each of ``CountX2`` rings of a ball (``Size``
    across, ``Height`` up), flying out over ``Move`` ms, fading over the last ``FadeTime``; ``RandomColorFlag``: one
    colour of the list for the whole burst. ``Gravity`` and ``Stretch`` are not played (inferred)."""
    rings, per_ring = max(1, int(emitter.number("CountX2", 1))), max(1, int(emitter.number("SegCount", 1)))
    life, move = max(emitter.number("LifeTime", 1000.0), FRAME_TIME_MS), max(emitter.number("Move", 1.0), 1.0)
    colors = emitter.colors.get("Color")
    burst = colors.at(rng.random())[:3] if colors is not None and emitter.number("RandomColorFlag") else None
    size = max(emitter.number("StartWidth", 1.0), 0.1) * FIREWORK_SPARK
    out = []
    for ring in range(rings):
        polar = math.pi * (ring + 0.5) / rings
        for k in range(per_ring):
            azimuth = 2 * math.pi * (k + 0.5 * (ring % 2)) / per_ring
            direction = (math.sin(polar) * math.cos(azimuth), math.cos(polar), math.sin(polar) * math.sin(azimuth))
            out.append(
                SSpawn(
                    emitter.start_ms + emitter.number("DelayTime"),
                    life,
                    _spark_state(emitter, direction, (move, life, size), burst),
                )
            )
    return out, emitter.duration_ms


def _spark_state(emitter: SEmitter, direction: Vec3, timing: tuple[float, float, float], burst):
    move, life, size = timing
    colors, reach, fade = (
        emitter.colors.get("Color"),
        (emitter.number("Size"), emitter.number("Height")),
        emitter.number("FadeTime"),
    )

    def state(age: float) -> State:
        s = min(age / move, 1.0)
        out = 1.0 - (1.0 - s) * (1.0 - s)  # fast at first, slowing as the burst opens
        rgba = (*burst, 1.0) if burst is not None else colors.at(age / life) if colors is not None else (1.0,) * 4
        alpha = rgba[3] * emitter.number("Alpha", 1.0) * _fade(age, life, fade)
        pos = (direction[0] * reach[0] * out, direction[1] * reach[1] * out, direction[2] * reach[0] * out)
        return pos, (size, size), 0.0, (*rgba[:3], alpha)

    return state


def _ring(emitter: SEmitter, rng: random.Random) -> tuple[list[SSpawn], float]:
    """``SuperNovaEffect``: ``SegCount`` cards in a ring that widens to ``Size`` over ``Move`` ms, a band ``Spread`` deep
    at the end, coloured by the list, fading over the last ``FadeTime``; ``Flat`` lays the cards on the ground
    (inferred; ``TexRepeat``, ``TexSlide`` and ``StartWidth`` are not played)."""
    segments = max(3, int(emitter.number("SegCount", 3)))
    life, move = max(emitter.number("LifeTime", 1000.0), FRAME_TIME_MS), max(emitter.number("Move", 1.0), 1.0)
    flat, colors, fade = emitter.number("Flat") != 0, emitter.colors.get("Color"), emitter.number("FadeTime")
    out = []
    for k in range(segments):
        angle_rad = 2 * math.pi * k / segments

        def state(age: float, angle_rad: float = angle_rad) -> State:
            s = min(age / move, 1.0)
            r = emitter.number("Size") * s
            rgba = colors.at(age / life) if colors is not None else (1.0, 1.0, 1.0, 1.0)
            spin = -angle_rad - math.pi / 2 if flat else 0.0  # the card's width runs along the ring
            alpha = rgba[3] * emitter.number("Alpha", 1.0) * _fade(age, life, fade)
            width = max(2 * math.pi * r / segments * 1.1, 1.0)
            return (
                (r * math.cos(angle_rad), 0.0, r * math.sin(angle_rad)),
                (width, max(emitter.number("Spread") * s, 1.0)),
                spin,
                (*rgba[:3], alpha),
            )

        out.append(SSpawn(emitter.start_ms + emitter.number("DelayTime"), life, state))
    return out, emitter.duration_ms


KINDS: dict[str, Callable[[SEmitter, random.Random], tuple[list[SSpawn], float]]] = {
    "ParticleEffect": _particles,
    "BillboardEffect": _billboard,
    "InstancingEffect": _puffs,
    "InstancingSlowEffect": _puffs,
    "SprayEffect": _spray,
    "FireWorxEffect": _firework,
    "SuperNovaEffect": _ring,
}
