"""Anno 2070's point and spot lights as its light renderer draws them over the G-buffer (``deferred_lights.fx``,
added to the lit scene): a point light as a hemisphere turned to the camera or, small or near the camera, a sprite;
a spot as a cone, its base too when the camera looks at its end. The engine feeds one instance per light; the viewer
writes each light's values into every vertex of its copy of the mesh, which the shaders read the same way.

``split`` sorts the lights by the engine's rule, ``point_rows`` / ``spot_rows`` / ``sprite_rows`` are their values,
``LightVolumes(ctx, stages, dummies)`` draws them. docs/rendering/shader-compile.md section 10, "Point and spot lights".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .fxcdraw import FxcDraw
from .gamedraw import Mesh, Program
from .lightsforward import Lamp

FX = "data/system/shaders/fx/deferred_lights.fx"
TECHNIQUES = {"sprite": "DeferredPointLight", "point": "DeferredPointLightMesh", "spot": "DeferredSpotLightMesh"}
SPRITE_ROWS = 3  # float4s per sprite in cSpriteData (position and range; colour and falloff; volume)
MAX_SPRITES = 256 // SPRITE_ROWS  # cSpriteData holds 256 float4s
SPOT_VOLUME = 0.5  # the engine halves a spot's volume intensity before the shader reads it
# the hemisphere's rings (pi / 8 apart) and segments (pi / 3), pushed out so the polygons enclose the unit sphere
RINGS, SEGMENTS = 5, 7
RING_SCALE, SEGMENT_SCALE = 1 / math.cos(math.pi / 16), 1 / math.cos(math.pi / 6)
CONE_RIM = 10  # the cone's rim vertices, pushed out by 1 / cos(pi / 10); the apex is the eleventh


def hemisphere() -> tuple[np.ndarray, np.ndarray]:
    """The point light's volume in view space, apex towards the camera (+z in the viewer's view space; the engine's
    is left-handed, its apex at -z), as vertices [35, 3] and the engine's triangle strip turned into a list."""
    verts = []
    for i in range(RINGS):
        ring, z = math.sin(i * math.pi / 8), math.cos(i * math.pi / 8) * RING_SCALE
        for j in range(SEGMENTS):
            a = j * math.pi / 3
            verts.append((math.cos(a) * ring * SEGMENT_SCALE, math.sin(a) * ring * SEGMENT_SCALE, z))
    strip = [k for i in range(RINGS - 1) for j in range(SEGMENTS) for k in (i * SEGMENTS + j, i * SEGMENTS + j + 7)]
    return np.array(verts, "<f4"), strip_to_list(strip)


def strip_to_list(strip: list[int]) -> np.ndarray:
    """A triangle strip as a list of its triangles, every other one turned so all keep the strip's winding;
    degenerate ones (two equal corners) left out."""
    out = []
    for k in range(len(strip) - 2):
        a, b, c = strip[k : k + 3]
        if len({a, b, c}) == 3:
            out += [a, b, c] if k % 2 == 0 else [b, a, c]
    return np.array(out, np.uint32)


def cone() -> tuple[np.ndarray, np.ndarray, int]:
    """The spot light's unit cone (apex at 0, its base at y = 1 with radius 1): vertices [11, 3], the indices of its
    sides then of its base, and how many of them are the sides."""
    scale = 1 / math.cos(math.pi / CONE_RIM)
    rim = [(math.sin(k * 2 * math.pi / CONE_RIM) * scale, 1.0, math.cos(k * 2 * math.pi / CONE_RIM) * scale)
           for k in range(CONE_RIM)]  # fmt: skip
    verts = np.array([*rim, (0.0, 0.0, 0.0)], "<f4")
    sides = [i for k in range(CONE_RIM) for i in (CONE_RIM, k, (k + 1) % CONE_RIM)]
    base = [i for k in range(1, CONE_RIM - 1) for i in (0, k + 1, k)]
    return verts, np.array(sides + base, np.uint32), len(sides)


@dataclass(frozen=True)
class Split:
    """The lights by how the engine draws them: sprites, point meshes, spot cones, and the spots whose base shows."""

    sprites: list
    points: list
    spots: list
    bases: list


def split(lamps: list[Lamp], eye, near: float) -> Split:
    """The engine's rule: a point light of range 1 or less, or one the camera is within range + near + 1 of, is a
    sprite, the others meshes; a spot whose far end the camera looks at from beyond (the view ray to the end against
    the spot's axis) gets its base drawn too."""
    eye = np.asarray(eye, float)
    sprites, points, spots, bases = [], [], [], []
    for lamp in lamps:
        at = np.asarray(lamp.position, float)
        if lamp.axis is None:
            near_it = float(np.sum((eye - at) ** 2)) <= (near + lamp.range + 1.0) ** 2
            (sprites if lamp.range <= 1.0 or near_it else points).append(lamp)
            continue
        spots.append(lamp)
        axis = np.asarray(lamp.axis, float)
        end = at + axis * lamp.range - eye
        if float(np.dot(end / max(1e-9, float(np.linalg.norm(end))), axis)) < -1e-6:
            bases.append(lamp)
    return Split(sprites, points, spots, bases)


def point_rows(points: list[Lamp]) -> np.ndarray:
    """[n, 9] per point light: centre, range, colour, falloff, volume (``P3f,S3f_S1f_S3f_S1f_S1f``)."""
    return np.array([[*p.position, p.range, *p.color, p.falloff, p.volume] for p in points], "<f4").reshape(-1, 9)


def _frame(axis) -> np.ndarray:
    """Three rows: two unit vectors across the spot's axis and the axis between them (the engine's cross products)."""
    y = np.asarray(axis, float) / max(1e-9, float(np.linalg.norm(axis)))
    helper = np.array([0.0, 0.0, 1.0]) if abs(y[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
    x = np.cross(y, helper)
    x /= np.linalg.norm(x)
    return np.array([x, y, np.cross(x, y)])


def spot_rows(spots: list[Lamp]) -> np.ndarray:
    """[n, 21] per spot light: the three rows of its frame (the axis second), centre, (range, base radius, falloff,
    half the volume), (cos outer, 1 / (cos inner - cos outer)), colour (``P3f,S3f_S3f_S3f_S3f_S4f_S2f_S3f``)."""
    out = []
    for s in spots:
        tan = math.sqrt(max(0.0, 1.0 - s.cos_outer**2)) / max(1e-4, s.cos_outer)
        delta = 1.0 / max(1e-4, s.cos_inner - s.cos_outer)
        settings = [s.range, s.range * tan, s.falloff, s.volume * SPOT_VOLUME]
        out.append([*_frame(s.axis).reshape(-1), *s.position, *settings, s.cos_outer, delta, *s.color])
    return np.array(out, "<f4").reshape(-1, 21)


def sprite_rows(sprites: list[Lamp]) -> list[list[float]]:
    """``cSpriteData``: three float4s per sprite (position, range; colour, falloff; volume)."""
    rows = []
    for p in sprites[:MAX_SPRITES]:
        rows += [[*p.position, p.range], [*p.color, p.falloff], [p.volume, 0.0, 0.0, 0.0]]
    return rows


def instanced(verts: np.ndarray, indices: np.ndarray, rows: np.ndarray, widths: tuple[int, ...]) -> Mesh:
    """One copy of the mesh per row, each vertex carrying its row as texture coordinates of ``widths`` floats."""
    n, count = len(rows), len(verts)
    data = np.concatenate([np.tile(verts, (n, 1)), np.repeat(rows, count, axis=0)], axis=1).astype("<f4")
    elements, at = [(0, 7, 3, 0)], 12
    for w in widths:
        elements.append((4, 7, w, at))
        at += 4 * w
    index = np.concatenate([indices + k * count for k in range(n)]).astype(np.uint32)
    return Mesh(np.ascontiguousarray(data).tobytes(), at, tuple(elements), index, n * count)


def sprite_mesh(n: int) -> Mesh:
    """``n`` quads of (corner x, corner y, sprite index): the vertex shader spreads each over its light's range."""
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    verts = np.array([(x, y, k) for k in range(n) for x, y in corners], "<f4")
    index = np.array([i + 4 * k for k in range(n) for i in (0, 1, 2, 0, 2, 3)], np.uint32)
    return Mesh(verts.tobytes(), 12, ((0, 7, 3, 0),), index, 4 * n)


class LightVolumes:
    """The light draws of one frame (module docstring): ``stages`` holds the three techniques' ``PassStages``."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.ctx, self.stages, self.dummies = ctx, stages, dummies
        self.draws: dict[str, FxcDraw] = {}
        self.keys: dict[str, bytes] = {}
        self.sprites: list = []

    def _draw(self, kind: str, mesh: Mesh, key: bytes) -> FxcDraw:
        """The draw of ``kind`` for ``mesh``, made anew when what it draws changed."""
        if self.keys.get(kind) != key or kind not in self.draws:
            if kind in self.draws:
                self.draws[kind].release()
            st = self.stages[TECHNIQUES["sprite" if kind == "sprite" else "spot" if kind == "base" else kind]]
            self.draws[kind], self.keys[kind] = (
                FxcDraw(self.ctx, Program(st.vs, st.ps), mesh, st.state, self.dummies),
                key,
            )
        return self.draws[kind]

    def frame(self, lamps: list[Lamp], eye, near: float) -> list[FxcDraw]:
        """The draws for this frame's lights (none for none)."""
        found, out = split(lamps, eye, near), []
        if found.points:
            rows = point_rows(found.points)
            verts, index = hemisphere()
            out.append(self._draw("point", instanced(verts, index, rows, (3, 1, 3, 1, 1)), rows.tobytes()))
        verts, index, sides = cone()
        for kind, chosen, part in (("spot", found.spots, index[:sides]), ("base", found.bases, index[sides:])):
            if chosen:
                rows = spot_rows(chosen)
                out.append(self._draw(kind, instanced(verts, part, rows, (3, 3, 3, 3, 4, 2, 3)), rows.tobytes()))
        self.sprites = sprite_rows(found.sprites)
        if self.sprites:
            n = len(self.sprites) // SPRITE_ROWS
            out.append(self._draw("sprite", sprite_mesh(n), bytes([n])))
        return out

    def release(self) -> None:
        for d in self.draws.values():
            d.release()
        self.draws, self.keys = {}, {}
