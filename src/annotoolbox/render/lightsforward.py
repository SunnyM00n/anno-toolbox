"""Anno 2205's and 1800's local lights as their model shaders read them: the point and spot lights in
``cbPointLights`` / ``cbSpotLights`` (two and three float4 each, the same rows in both games), the light cells (2205: a
3D texture, 30 x 20 x 16; 1800: the structured buffer ``cLightClusterBuffer``, 32 x 16 x 16, here a constant buffer),
each the start of its run in the index texture and its counts, and the index texture (512 wide). A model viewer puts
every light in every cell: a scene of one model holds a few lights, and the shader skips those out of range itself.

``SLamp`` is one light; ``rows``, ``cells`` (2205) and ``buffer_cells`` (1800) the data, ``CClusterTextures(ctx)`` it on
the device. docs/rendering/shader-compile.md section 11, "Local lights".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CELLS = (30, 20, 16)  # the shader's cell grid: across, down, depth slices
INDEX_WIDTH = 512  # the index texture's width (the shader reads index i at (i & 511, i >> 9))
MAX_LIGHTS = 256
# 1800's cell buffer read as a constant buffer holds 4,096 cells of its 8,192 (64 KB): its first 8 depth slices, out to
# a quarter of the far plane; a model viewer's lights are nearer (shader-compile section 11 "Local lights")
BUFFER_CELLS = 4096


@dataclass(frozen=True)
class SLamp:
    """One light where it stands (the view's axes): ``color`` its radiance, ``falloff`` the exponent of its range;
    a spot's ``axis`` (unit, where it shines) and the cosines of its cone's inner and outer half-angles."""

    position: tuple
    range: float
    color: tuple
    falloff: float = 1.0
    axis: tuple | None = None
    cos_inner: float = 1.0
    cos_outer: float = 0.0
    volume: float = 0.0  # 2070's glow of the air round the light (lights2070)


def rows(points: list[SLamp], spots: list[SLamp]) -> tuple[list, list]:
    """The rows of ``cPointLights`` (position, range; colour, falloff) and ``cSpotLights`` (position, falloff;
    colour, cos outer; the direction back towards the light times the range, 1 / (cos inner - cos outer)): the
    shader takes the spot's cone factor as (dot(to light, direction) - cos outer) x that last value."""
    point_rows = []
    for lamp in points[:MAX_LIGHTS]:
        point_rows += [[*lamp.position, lamp.range], [*lamp.color, lamp.falloff]]
    spot_rows = []
    for lamp in spots[:MAX_LIGHTS]:
        back = -np.asarray(lamp.axis or (0, -1, 0), float) * lamp.range
        delta = 1.0 / max(1e-4, lamp.cos_inner - lamp.cos_outer)
        spot_rows += [[*lamp.position, lamp.falloff], [*lamp.color, lamp.cos_outer], [*back, delta]]
    return point_rows, spot_rows


def cells(n_points: int, n_spots: int) -> tuple[np.ndarray, np.ndarray]:
    """The cell texture (depth, height, width, 2) as uint32 - every cell starts at index 0 with all the points and
    spots - and the index texture (rows, 512): the points' indices, then the spots'."""
    n_points, n_spots = min(n_points, MAX_LIGHTS), min(n_spots, MAX_LIGHTS)
    grid = np.zeros((CELLS[2], CELLS[1], CELLS[0], 2), np.uint32)
    grid[..., 1] = n_points | (n_spots << 16)
    count = max(1, n_points + n_spots)
    index = np.zeros((max(1, math.ceil(count / INDEX_WIDTH)), INDEX_WIDTH), np.uint32)
    flat = index.reshape(-1)
    flat[:n_points] = np.arange(n_points)
    flat[n_points : n_points + n_spots] = np.arange(n_spots)
    return grid, index


def buffer_cells(n_points: int, n_spots: int) -> list[list[int]]:
    """1800's ``cLightClusterBuffer``: every cell starts at index 0 with all the points and spots, the counts packed as
    the shader unpacks them (points in bits 0-10, spots 11-20, decals 21-31)."""
    n_points, n_spots = min(n_points, MAX_LIGHTS), min(n_spots, MAX_LIGHTS)
    return [[0, n_points | (n_spots << 11)]] * BUFFER_CELLS


class CClusterTextures:
    """The two textures on the device, made again when the lights' counts change."""

    def __init__(self, ctx) -> None:
        self.m_ctx, self.m_key, self.m_made = ctx, None, []

    def update(self, points: list[SLamp], spots: list[SLamp], game: str = "anno2205") -> tuple[dict, dict]:
        """The textures by the shader's names and the buffers' rows by name (1800: its cells as buffer rows)."""
        key = (min(len(points), MAX_LIGHTS), min(len(spots), MAX_LIGHTS))
        if key != self.m_key:
            self.release()
            grid, index = cells(*key)
            self.m_made = [self.m_ctx.texture3d(CELLS, 2, grid.tobytes(), dtype="u4"),
                         self.m_ctx.texture((INDEX_WIDTH, index.shape[0]), 1, index.tobytes(), dtype="u4")]  # fmt: skip
            self.m_key = key
        point_rows, spot_rows = rows(points, spots)
        if game == "anno1800":
            return {"cLightIndexTex": self.m_made[1].native}, {
                "cPointLights": point_rows, "cSpotLights": spot_rows, "cLightClusterBuffer": buffer_cells(*key)
            }  # fmt: skip
        return {"cLightClusterTex": self.m_made[0].native, "cLightIndexTex": self.m_made[1].native}, {
            "cPointLights": point_rows,
            "cSpotLights": spot_rows,
        }

    def release(self) -> None:
        for texture in self.m_made:
            texture.release()
        self.m_made, self.m_key = [], None
