"""Anno 2070's point and spot lights as its light renderer draws them over the G-buffer (``deferred_lights.fx``,
added to the lit scene): a point light as a hemisphere turned to the camera or, small or near the camera, a sprite;
a spot as a cone, its base too when the camera looks at its end. The engine feeds one instance per light; the viewer
writes each light's values into every vertex of its copy of the mesh, which the shaders read the same way.

``split`` sorts the lights by the engine's rule, ``point_rows`` / ``spot_rows`` / ``sprite_rows`` are their values,
``CLightVolumes(ctx, stages, dummies)`` draws them. docs/rendering/shader-compile.md section 10, "Point and spot lights".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .fxcdraw import CFxcDraw
from .gamedraw import CMesh, CProgram
from .lightsforward import SLamp

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
    vertices = []
    for i in range(RINGS):
        ring, z = math.sin(i * math.pi / 8), math.cos(i * math.pi / 8) * RING_SCALE
        for j in range(SEGMENTS):
            angle = j * math.pi / 3
            vertices.append((math.cos(angle) * ring * SEGMENT_SCALE, math.sin(angle) * ring * SEGMENT_SCALE, z))
    strip = [k for i in range(RINGS - 1) for j in range(SEGMENTS) for k in (i * SEGMENTS + j, i * SEGMENTS + j + 7)]
    return np.array(vertices, "<f4"), strip_to_list(strip)


def strip_to_list(strip: list[int]) -> np.ndarray:
    """A triangle strip as a list of its triangles, every other one turned so all keep the strip's winding;
    degenerate ones (two equal corners) left out."""
    out = []
    for k in range(len(strip) - 2):
        corner_a, corner_b, corner_c = strip[k : k + 3]
        if len({corner_a, corner_b, corner_c}) == 3:
            out += [corner_a, corner_b, corner_c] if k % 2 == 0 else [corner_b, corner_a, corner_c]
    return np.array(out, np.uint32)


def cone() -> tuple[np.ndarray, np.ndarray, int]:
    """The spot light's unit cone (apex at 0, its base at y = 1 with radius 1): vertices [11, 3], the indices of its
    sides then of its base, and how many of them are the sides."""
    scale = 1 / math.cos(math.pi / CONE_RIM)
    rim = [(math.sin(k * 2 * math.pi / CONE_RIM) * scale, 1.0, math.cos(k * 2 * math.pi / CONE_RIM) * scale)
           for k in range(CONE_RIM)]  # fmt: skip
    vertices = np.array([*rim, (0.0, 0.0, 0.0)], "<f4")
    sides = [i for k in range(CONE_RIM) for i in (CONE_RIM, k, (k + 1) % CONE_RIM)]
    base = [i for k in range(1, CONE_RIM - 1) for i in (0, k + 1, k)]
    return vertices, np.array(sides + base, np.uint32), len(sides)


@dataclass(frozen=True)
class SSplit:
    """The lights by how the engine draws them: sprites, point meshes, spot cones, and the spots whose base shows."""

    sprites: list
    points: list
    spots: list
    bases: list


def split(lamps: list[SLamp], eye, near: float) -> SSplit:
    """The engine's rule: a point light of range 1 or less, or one the camera is within range + near + 1 of, is a
    sprite, the others meshes; a spot whose far end the camera looks at from beyond (the view ray to the end against
    the spot's axis) gets its base drawn too."""
    eye = np.asarray(eye, float)
    sprites, points, spots, bases = [], [], [], []
    for lamp in lamps:
        position = np.asarray(lamp.position, float)
        if lamp.axis is None:
            near_it = float(np.sum((eye - position) ** 2)) <= (near + lamp.range + 1.0) ** 2
            (sprites if lamp.range <= 1.0 or near_it else points).append(lamp)
            continue
        spots.append(lamp)
        axis = np.asarray(lamp.axis, float)
        end = position + axis * lamp.range - eye
        if float(np.dot(end / max(1e-9, float(np.linalg.norm(end))), axis)) < -1e-6:
            bases.append(lamp)
    return SSplit(sprites, points, spots, bases)


def point_rows(points: list[SLamp]) -> np.ndarray:
    """[n, 9] per point light: centre, range, colour, falloff, volume (``P3f,S3f_S1f_S3f_S1f_S1f``)."""
    return np.array(
        [[*light.position, light.range, *light.color, light.falloff, light.volume] for light in points], "<f4"
    ).reshape(-1, 9)


def _frame(axis) -> np.ndarray:
    """Three rows: two unit vectors across the spot's axis and the axis between them (the engine's cross products)."""
    y = np.asarray(axis, float) / max(1e-9, float(np.linalg.norm(axis)))
    helper = np.array([0.0, 0.0, 1.0]) if abs(y[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
    x = np.cross(y, helper)
    x /= np.linalg.norm(x)
    return np.array([x, y, np.cross(x, y)])


def spot_rows(spots: list[SLamp]) -> np.ndarray:
    """[n, 21] per spot light: the three rows of its frame (the axis second), centre, (range, base radius, falloff,
    half the volume), (cos outer, 1 / (cos inner - cos outer)), colour (``P3f,S3f_S3f_S3f_S3f_S4f_S2f_S3f``)."""
    out = []
    for spot in spots:
        tan = math.sqrt(max(0.0, 1.0 - spot.cos_outer**2)) / max(1e-4, spot.cos_outer)
        delta = 1.0 / max(1e-4, spot.cos_inner - spot.cos_outer)
        settings = [spot.range, spot.range * tan, spot.falloff, spot.volume * SPOT_VOLUME]
        out.append([*_frame(spot.axis).reshape(-1), *spot.position, *settings, spot.cos_outer, delta, *spot.color])
    return np.array(out, "<f4").reshape(-1, 21)


def sprite_rows(sprites: list[SLamp]) -> list[list[float]]:
    """``cSpriteData``: three float4s per sprite (position, range; colour, falloff; volume)."""
    rows = []
    for sprite in sprites[:MAX_SPRITES]:
        rows += [[*sprite.position, sprite.range], [*sprite.color, sprite.falloff], [sprite.volume, 0.0, 0.0, 0.0]]
    return rows


def instanced(vertices: np.ndarray, indices: np.ndarray, rows: np.ndarray, widths: tuple[int, ...]) -> CMesh:
    """One copy of the mesh per row, each vertex carrying its row as texture coordinates of ``widths`` floats."""
    row_count, count = len(rows), len(vertices)
    data = np.concatenate([np.tile(vertices, (row_count, 1)), np.repeat(rows, count, axis=0)], axis=1).astype("<f4")
    elements, byte_offset = [(0, 7, 3, 0)], 12
    for width in widths:
        elements.append((4, 7, width, byte_offset))
        byte_offset += 4 * width
    index = np.concatenate([indices + k * count for k in range(row_count)]).astype(np.uint32)
    return CMesh(np.ascontiguousarray(data).tobytes(), byte_offset, tuple(elements), index, row_count * count)


def sprite_mesh(sprite_count: int) -> CMesh:
    """``n`` quads of (corner x, corner y, sprite index): the vertex shader spreads each over its light's range."""
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    vertices = np.array([(x, y, k) for k in range(sprite_count) for x, y in corners], "<f4")
    index = np.array([i + 4 * k for k in range(sprite_count) for i in (0, 1, 2, 0, 2, 3)], np.uint32)
    return CMesh(vertices.tobytes(), 12, ((0, 7, 3, 0),), index, 4 * sprite_count)


class CLightVolumes:
    """The light draws of one frame (module docstring): ``stages`` holds the three techniques' ``SPassStages``."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.m_ctx, self.m_stages, self.m_dummies = ctx, stages, dummies
        self.m_draws: dict[str, CFxcDraw] = {}
        self.m_keys: dict[str, bytes] = {}
        self.m_sprites: list = []

    def _draw(self, kind: str, mesh: CMesh, key: bytes) -> CFxcDraw:
        """The draw of ``kind`` for ``mesh``, made anew when what it draws changed."""
        if self.m_keys.get(kind) != key or kind not in self.m_draws:
            if kind in self.m_draws:
                self.m_draws[kind].release()
            stage = self.m_stages[TECHNIQUES["sprite" if kind == "sprite" else "spot" if kind == "base" else kind]]
            self.m_draws[kind], self.m_keys[kind] = (
                CFxcDraw(
                    self.m_ctx, CProgram(stage.vertex_stage, stage.pixel_stage), mesh, stage.state, self.m_dummies
                ),
                key,
            )
        return self.m_draws[kind]

    def frame(self, lamps: list[SLamp], eye, near: float) -> list[CFxcDraw]:
        """The draws for this frame's lights (none for none)."""
        found, out = split(lamps, eye, near), []
        if found.points:
            rows = point_rows(found.points)
            vertices, index = hemisphere()
            out.append(self._draw("point", instanced(vertices, index, rows, (3, 1, 3, 1, 1)), rows.tobytes()))
        vertices, index, sides = cone()
        for kind, chosen, part in (("spot", found.spots, index[:sides]), ("base", found.bases, index[sides:])):
            if chosen:
                rows = spot_rows(chosen)
                out.append(self._draw(kind, instanced(vertices, part, rows, (3, 3, 3, 3, 4, 2, 3)), rows.tobytes()))
        self.m_sprites = sprite_rows(found.sprites)
        if self.m_sprites:
            sprite_count = len(self.m_sprites) // SPRITE_ROWS
            out.append(self._draw("sprite", sprite_mesh(sprite_count), bytes([sprite_count])))
        return out

    def release(self) -> None:
        for draw in self.m_draws.values():
            draw.release()
        self.m_draws, self.m_keys = {}, {}
