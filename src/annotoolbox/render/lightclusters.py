"""Anno 117's clustered lights, filled on the CPU: the point, spot and area lights in the engine's layouts
(``cPointLights``: three float4 each, ``cSpotLights``: eight, ``cAreaLights``: five), the grid of 32 x 16 x 16 cells
over the view (``cLightClusterBuffer``: a cell's first index and its three counts) and the index texture the cells
point into (``cLightIndexTex``, 512 x 128).

``build(lights, view, proj, far)`` is the entry (no GPU), ``from_config`` makes a light of a config's LIGHT. The
engine fills the grid on the GPU in its tiled-lighting setup; this is the stand-in, cell for cell the same rule (a
light is in every cell its sphere's bounds touch). docs/rendering/frame-117-viewer.md section 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np

CELLS = (32, 16, 16)  # NUM_LIGHT_CLUSTERS_X / _Y / _Z
NEAR_SLICE = 20.0  # NEAR_Z_CLUSTER_RANGE: the first slice's depth
INDEX_SIZE = (512, 128)  # LIGHT_INDEX_TEX_SIZE
FLOATS_PER = {"point": 12, "spot": 32, "area": 20}  # FLOAT4_PER_POINT_LIGHT / _SPOT_LIGHT / _AREA_LIGHT x 4
LIGHT_ARRAY = 4096 * 4  # the floats of cSpotLights and cAreaLights (LIGHT_ARRAY_SIZE float4)
# a cell's counts: point lights in bits 0-10, spot lights in 11-20, area lights in 21-31
COUNT_BITS = {"point": (0, 0x7FF), "spot": (11, 0x3FF), "area": (21, 0x7FF)}
# the config's Type -> the kind, and for an area light the shader's shape (AREA_LIGHT_TYPE_*)
CONFIG_KINDS = {0: ("point", 0), 2: ("spot", 0), 5: ("area", 1), 6: ("area", 2), 8: ("area", 5)}
AREA_DOUBLE_SIDED, AREA_NO_FOG = 2, 16  # AREA_LIGHT_FLAG_*


@dataclass(frozen=True)
class SPointLight:
    """A light as the shading reads it: its place, reach and colour (radiance: the config's colour times its
    strength)."""

    position: tuple
    range: float
    color: tuple
    kind = "point"

    def floats(self) -> list[float]:
        return [*self.position, self.range, *self.color, 0.0, 0, 0, 0, 0]  # the third float4: no shadow slot


@dataclass(frozen=True)
class SSpotLight:
    """A spot light: ``direction`` where it shines (unit), ``cos_inner`` / ``cos_outer`` its cone's half-angle
    cosines, ``falloff`` the config's exponent (written, not read by the shading)."""

    position: tuple
    range: float
    color: tuple
    direction: tuple
    cos_inner: float
    cos_outer: float
    falloff: float = 2.0
    kind = "spot"

    def floats(self) -> list[float]:
        towards = -np.asarray(self.direction, float) * self.range  # towards the light, its length the range
        delta = 1.0 / max(self.cos_inner - self.cos_outer, 1e-4)  # 1 / (cos theta - cos phi)
        return [*self.position, self.falloff, *self.color, self.cos_outer, *towards, delta, *[0.0] * 20]


@dataclass(frozen=True)
class SAreaLight:
    """An area light: ``shape`` the shader's type (1 quad, 2 ellipse, 3 sphere, 4 star, 5 line), ``axis1`` and
    ``axis2`` its data vectors (half axes; a line's half length and its thickness in ``axis2[0]``), ``flags``."""

    position: tuple
    range: float
    color: tuple
    shape: int
    axis1: tuple
    axis2: tuple
    flags: int = 0
    kind = "area"

    def floats(self) -> list[float]:
        return [*self.position, float(self.shape), *self.axis1, float(self.flags), *self.axis2, self.range,
                *self.color, 0.0, 0, 0, 0, 0]  # fmt: skip


@dataclass
class SClusters:
    points: np.ndarray  # float32 [n * 12]
    cells: np.ndarray  # uint32 [cells * 2]: (first index, counts)
    indices: np.ndarray  # uint32 [128, 512]
    count: int
    spots: np.ndarray | None = None  # float32 [4096 * 4]: cSpotLights
    areas: np.ndarray | None = None  # float32 [4096 * 4]: cAreaLights


class ILamp(Protocol):
    """A config's LIGHT as ``from_config`` reads it (the viewer's scene record has these fields)."""

    kind: int  # the config's Type
    range: float
    cone: tuple[float, float]  # Theta and Phi, whole angles
    size: tuple[float, float]  # Width and Height
    falloff: float
    double_sided: bool
    no_fog: bool


def from_config(lamp: ILamp, place, color):
    """The engine's light for a config's LIGHT ``lamp`` at ``place`` (4 x 4, column vectors, in the scene's axes) in
    ``color``: a spot shines along local -y; an area light spans local x and z and faces -y (frame-117-viewer.md 1).
    None for a type the engine's layout has no reading of."""
    found = CONFIG_KINDS.get(int(lamp.kind))
    if found is None:
        return None
    matrix = np.asarray(place, float)
    position, color = tuple(matrix[:3, 3]), tuple(float(component) for component in color)
    if found[0] == "point":
        return SPointLight(position, float(lamp.range), color)
    if found[0] == "spot":
        axis = -matrix[:3, 1] / max(np.linalg.norm(matrix[:3, 1]), 1e-9)
        inner, outer = (math.cos(float(angle) / 2) for angle in lamp.cone)
        return SSpotLight(position, float(lamp.range), color, tuple(axis), inner, outer, float(lamp.falloff))
    width, height = lamp.size
    flags = (AREA_DOUBLE_SIDED if lamp.double_sided else 0) | (AREA_NO_FOG if lamp.no_fog else 0)
    x = matrix[:3, 0] * width / 2
    # the second axis is -z: the shader lights the side away from cross(axis1, axis2), so the light faces -y as a spot
    axis2 = (float(height), 0.0, 0.0) if found[1] == 5 else tuple(-matrix[:3, 2] * height / 2)
    return SAreaLight(position, float(lamp.range), color, found[1], tuple(x), axis2, flags)


def slice_of(z: float, far: float) -> int:
    """``GetClusterSlice``: the first slice to 20 ahead, the other 15 spread to the far plane on a square root."""
    if z < NEAR_SLICE:
        return 0
    fraction = min(max((z - NEAR_SLICE) / max(far - NEAR_SLICE, 1e-6), 0.0), 1.0)
    return min(int(math.sqrt(fraction) * CELLS[2] + 1), CELLS[2] - 1)


def _cell_range(light, view: np.ndarray, view_proj: np.ndarray, far: float):
    """The cells (x, y, z ranges) a light's sphere touches, or None when it lies behind the camera."""
    position = np.asarray(light.position, float)
    reach = float(light.range)
    view_z = float(np.r_[position, 1.0] @ view[:, 2])
    if view_z + reach <= 0:
        return None
    offsets = np.array([[x, y, z] for x in (-reach, reach) for y in (-reach, reach) for z in (-reach, reach)])
    clip = np.c_[position + offsets, np.ones(8)] @ view_proj
    if np.any(clip[:, 3] <= 1e-4):  # the camera inside the bounds: every cell across
        min_x, max_x, min_y, max_y = 0, CELLS[0] - 1, 0, CELLS[1] - 1
    else:
        ndc = clip[:, :2] / clip[:, 3:4]
        low = np.clip((ndc.min(0) * 0.5 + 0.5) * CELLS[:2], 0, np.array(CELLS[:2]) - 1).astype(int)
        high = np.clip((ndc.max(0) * 0.5 + 0.5) * CELLS[:2], 0, np.array(CELLS[:2]) - 1).astype(int)
        min_x, min_y, max_x, max_y = low[0], low[1], high[0], high[1]
    return (min_x, max_x), (min_y, max_y), (slice_of(max(view_z - reach, 0.0), far), slice_of(view_z + reach, far))


def _lists(lights: list) -> dict[str, list]:
    """The lights per kind, each list cut to what its buffer and its count bits hold."""
    out: dict[str, list] = {"point": [], "spot": [], "area": []}
    for light in lights:
        out[light.kind].append(light)
    limits = {"point": COUNT_BITS["point"][1], "spot": LIGHT_ARRAY // FLOATS_PER["spot"],
              "area": LIGHT_ARRAY // FLOATS_PER["area"]}  # fmt: skip
    return {k: values[: limits[k]] for k, values in out.items()}


def _packed(lights: list, kind: str, floats: int) -> np.ndarray:
    data = np.zeros(floats, np.float32)
    per = FLOATS_PER[kind]
    for k, light in enumerate(lights):
        data[k * per : (k + 1) * per] = light.floats()
    return data


def build(lights: list, view: np.ndarray, projection: np.ndarray, far: float) -> SClusters:
    """The buffers for ``lights`` seen by a camera (``view``, ``projection``: the engine's, row vectors)."""
    kinds = _lists(lights)
    per_cell: dict[int, dict[str, list[int]]] = {}
    view_proj = view @ projection
    for kind, found_lights in kinds.items():
        for k, light in enumerate(found_lights):
            found = _cell_range(light, view, view_proj, far)
            if found is None:
                continue
            (min_x, max_x), (min_y, max_y), (min_z, max_z) = found
            for z in range(min_z, max_z + 1):
                for y in range(min_y, max_y + 1):
                    for x in range(min_x, max_x + 1):
                        cell = per_cell.setdefault(x + y * CELLS[0] + z * CELLS[0] * CELLS[1], {})
                        cell.setdefault(kind, []).append(k)
    cells = np.zeros((CELLS[0] * CELLS[1] * CELLS[2], 2), np.uint32)
    flat: list[int] = []
    for cell in sorted(per_cell):
        counts, entries = 0, []
        for kind in ("point", "spot", "area"):  # the order GetPointLight / GetSpotLight / GetAreaLight read
            shift, mask = COUNT_BITS[kind]
            found = per_cell[cell].get(kind, [])[:mask]
            counts |= len(found) << shift
            entries += found
        if len(flat) + len(entries) > INDEX_SIZE[0] * INDEX_SIZE[1]:
            break
        cells[cell] = (len(flat), counts)
        flat += entries
    indices = np.zeros(INDEX_SIZE[0] * INDEX_SIZE[1], np.uint32)
    indices[: len(flat)] = flat
    points = _packed(kinds["point"], "point", max(1, len(kinds["point"])) * FLOATS_PER["point"])
    return SClusters(points, cells.ravel(), indices.reshape(INDEX_SIZE[1], INDEX_SIZE[0]), len(kinds["point"]),
                    _packed(kinds["spot"], "spot", LIGHT_ARRAY), _packed(kinds["area"], "area", LIGHT_ARRAY))  # fmt: skip
