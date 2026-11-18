"""Particle effects for the 3D view: an effect file as sprites that can be asked for at any time.

``load_rdp`` reads a baked 117 effect; ``load_efc`` runs each emitter of a 2070 effect through the simulation that
bakes it for 117 (``models.particlebake``), so both games' effects play from the same kind of record: every
particle is alive for a range of ticks of a looping strip. ``Effect.at`` gives the particles alive at a time as
arrays in the effect's own space; the view turns them into quads (``render.particledraw``).
docs/island-editor-view.md "Particle effects".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..formats.rdp117 import (
    ANGLE_STEPS,
    FRAME_TIME_MS,
    POSITION_RANGE,
    BakedParticle,
    RdpMaterial,
    parse_rdp,
    texture_file,
)
from ..models import particlebake
from ..models.particles2070 import parse_efc

FIXED, CAMERA, UPRIGHT = 0, 1, 2  # an .rdp material's ViewAligned: through the matrix, facing the camera, upright


@dataclass
class Sprites:
    """The particles alive at one moment, in the effect's space: ``size`` is (width, depth, height) in metres,
    ``rotation`` the angles about x, y, z in radians, ``color`` rgba 0..1, ``material`` the index in
    ``Effect.materials`` and ``life`` how far through its life each particle is (0..1)."""

    position: np.ndarray
    size: np.ndarray
    rotation: np.ndarray
    color: np.ndarray
    material: np.ndarray
    life: np.ndarray

    def __len__(self) -> int:
        return len(self.position)


@dataclass
class Effect:
    """One baked emitter (or an ``.rdp``'s whole set of particles): ``start``, ``count`` and ``first`` give for
    every particle the tick it is born on, how many ticks it lives and where its ticks begin in the track arrays.
    ``texture`` is the file all materials show unless one names its own (``material_textures``)."""

    name: str
    texture: str
    materials: list[RdpMaterial]
    frame_count: int
    frame_ms: float
    looped: bool
    start: np.ndarray
    count: np.ndarray
    first: np.ndarray
    particle_material: np.ndarray
    position: np.ndarray
    size: np.ndarray
    rotation: np.ndarray
    color: np.ndarray
    material_textures: list[str] = field(default_factory=list)
    lighting: bool = False  # an .rdp's header: lit by the sun, shaded from the effect's centre, drawn in order
    self_occlusion: bool = False
    sort: bool = False

    def texture_of(self, material: int) -> str:
        """The texture file the ``material``-th material shows."""
        own = self.material_textures[material] if material < len(self.material_textures) else ""
        return own or self.texture

    @property
    def seconds(self) -> float:
        """How long one pass of the strip lasts."""
        return self.frame_count * self.frame_ms / 1000.0

    @classmethod
    def from_particles(
        cls,
        name: str,
        particles: list[BakedParticle],
        materials: list[RdpMaterial],
        texture: str,
        frame_count: int,
        frame_ms: float,
        looped: bool,
    ) -> Effect:
        """An effect from particles in real units (what the 2070 simulation gives, or a dequantised file)."""
        particles = [p for p in particles if p.pos]
        counts = np.array([len(p.pos) for p in particles], np.int64)
        flat = lambda attr, width: np.array([v for p in particles for v in getattr(p, attr)], "f4").reshape(-1, width)  # noqa: E731
        lives = (np.array([p.start for p in particles], np.int64), counts,
                 np.array([p.material for p in particles], np.int64))  # fmt: skip
        tracks = (flat("pos", 3), flat("size", 3), flat("rot", 3), flat("color", 4))
        return cls._with_tracks(name, materials, (texture, frame_count, frame_ms, looped), lives, tracks)

    @classmethod
    def _with_tracks(cls, name, materials, strip: tuple, lives: tuple, tracks: tuple) -> Effect:
        """An effect of its ``strip`` (texture, frame count, a frame's milliseconds, looped), its particles'
        ``lives`` (start tick, tick count, material) and their tick ``tracks`` (position, size, rotation, colour)."""
        texture, frame_count, frame_ms, looped = strip
        start, count, which = lives
        pos, size, rot, color = tracks
        first = np.concatenate([[0], np.cumsum(count)[:-1]]).astype(np.int64) if len(count) else count
        color = color.copy()
        if len(first):  # a particle fades in from and out to nothing: the first and last tick are blank
            color[first, 3] = 0.0
            color[first + count - 1, 3] = 0.0
        return cls(
            name, texture, materials, frame_count, frame_ms, looped, start, count, first, which, pos, size, rot, color
        )

    def at(self, seconds: float, speed: float = 1.0) -> Sprites:
        """The particles alive ``seconds`` into the strip (it repeats), played at ``speed`` times its own pace;
        positions, sizes and colours are blended between ticks. A strip without a frame time stands at its first
        tick; a time before the strip's start is the same moment of the pass before."""
        if not len(self.start) or self.frame_count <= 0:
            return _no_sprites()
        ticks = seconds * 1000.0 * speed / self.frame_ms if self.frame_ms > 0 else 0.0
        if not math.isfinite(ticks):
            ticks = 0.0
        frame, blend = math.floor(ticks) % self.frame_count, ticks % 1.0
        age = (frame - self.start) % self.frame_count
        alive = age < self.count
        if not alive.any():
            return _no_sprites()
        now = self.first[alive] + age[alive]
        nxt = np.minimum(age[alive] + 1, self.count[alive] - 1) + self.first[alive]

        def mix(track: np.ndarray) -> np.ndarray:
            return track[now] + (track[nxt] - track[now]) * blend

        life = age[alive] / np.maximum(self.count[alive] - 1, 1)
        return Sprites(
            mix(self.position), mix(self.size), self.rotation[now], mix(self.color), self.particle_material[alive], life
        )


def _no_sprites() -> Sprites:
    return Sprites(
        np.zeros((0, 3), "f4"),
        np.zeros((0, 3), "f4"),
        np.zeros((0, 3), "f4"),
        np.zeros((0, 4), "f4"),
        np.zeros(0, np.int64),
        np.zeros(0, "f4"),
    )


def load_rdp(data: bytes, name: str = "") -> Effect:
    """A baked 117 effect file. Its texture is the file the game loads for the reference it names."""
    r = parse_rdp(data)
    n = len(r.starts)
    count = np.array([r.ticks_of(i) for i in range(n)], np.int64)
    angle = 2 * math.pi / ANGLE_STEPS
    pos = np.array(r.positions, "f4").reshape(-1, 3) / POSITION_RANGE * np.array(r.extents, "f4") + np.array(
        r.center, "f4"
    )
    size = np.array(r.scales, "f4").reshape(-1, 3) / 255.0 * r.max_scale
    rot = np.array(r.rotations, "f4").reshape(-1, 3) * angle
    color = np.array(r.colors, "f4").reshape(-1, 4)[:, [2, 1, 0, 3]] / 255.0  # stored blue, green, red, alpha
    if len(pos) != int(count.sum()):
        raise ValueError(f"{name or 'effect'}: the tick tracks do not match the particles' frame ranges")
    materials = r.materials or [RdpMaterial()]
    strip = (_file(r.texture), r.frame_count, r.frame_time, r.looped)
    lives = (np.array(r.starts, np.int64), count, np.array(r.particle_materials, np.int64))
    effect = Effect._with_tracks(name, materials, strip, lives, (pos, size, rot, color))
    effect.material_textures = [_file(m.movie_texture) for m in materials]
    effect.lighting, effect.self_occlusion, effect.sort = r.lighting, r.self_occlusion, r.do_sort
    return effect


def _file(reference: str) -> str:
    """The file the game loads for a texture reference of a file ("" for none), with forward slashes."""
    return texture_file(reference).replace("\\", "/") if reference else ""


def load_efc(text: str, name: str, texture: Callable[[str], str]) -> tuple[list[Effect], list[str]]:
    """The emitters of a 2070 effect file as effects, and why the others are left out. ``texture`` turns an
    emitter's texture name into the file the game loads. Models, lines and arrows are not sprites."""
    out: list[Effect] = []
    left: list[str] = []
    for emitter in parse_efc(text):
        label = f"{name} #{emitter.index}"
        try:
            baked = particlebake.bake(emitter, label)
        except particlebake.BakeError as exc:
            left.append(f"{label}: {exc}")
            continue
        out.append(
            Effect.from_particles(
                label,
                baked.particles,
                [baked.material],
                texture(emitter.texture),
                baked.frame_count,
                FRAME_TIME_MS * baked.time_scale,
                baked.looped,
            )  # fmt: skip
        )
    return out, left


# Quads


def turns(rotation: np.ndarray) -> np.ndarray:
    """The turn ([n, 3, 3], column vectors) of each of ``rotation`` (the file's angles about x, y, z): the game builds
    ``Ry(-b) Rz(-c) Rx(-a)``."""
    a, b, c = -rotation[:, 0], -rotation[:, 1], -rotation[:, 2]
    ca, sa, cb, sb, cc, sc = np.cos(a), np.sin(a), np.cos(b), np.sin(b), np.cos(c), np.sin(c)
    zero, one = np.zeros_like(a), np.ones_like(a)
    rx = np.stack([one, zero, zero, zero, ca, -sa, zero, sa, ca], -1).reshape(-1, 3, 3)
    ry = np.stack([cb, zero, sb, zero, one, zero, -sb, zero, cb], -1).reshape(-1, 3, 3)
    rz = np.stack([cc, -sc, zero, sc, cc, zero, zero, zero, one], -1).reshape(-1, 3, 3)
    return ry @ rz @ rx


def quad_axes(rotation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The width and height directions ([n, 3] each) of fixed quads turned by ``rotation`` (``turns``); a quad lies
    in the x-z plane: width along x, height along z."""
    turn = turns(rotation)
    return turn[:, :, 0], turn[:, :, 2]
