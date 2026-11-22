"""Anno 2205's decals drawn by its own ``decal.fx`` (a DECAL material of ShaderID 0: ``cDecal...`` maps and
``cbMaterialCommon``) or ``decal_pbr.fx`` (ShaderID 8: a model_pbr material) in the forward frame: the decal's
rectangle as the engine's low-poly quad, its place and colour in ``cbDecal``, alpha-blended over what is below.

``quad(extents)`` is the mesh, ``instance_rows(world)`` the four rows of ``cDecalInstanceData``; ``CDecalModel`` is
``frameforward.CForwardModel`` with those rows. docs/rendering/shader-compile.md section 11 "Decals".
"""

from __future__ import annotations

import numpy as np

from .frameforward import CForwardModel
from .gamedraw import CMesh

FILES = {0: "data/shaders/fx/decal.fx", 8: "data/shaders/fx/decal_pbr.fx"}  # a decal material's ShaderID -> effect
TECHNIQUE = "DecalLowPoly"  # the viewer's ground is flat: the high-poly decal (a mesh over the terrain) is this quad
# a decal material's maps (its fields name them so for both shaders) -> the texture each shader reads them as; for
# decal_pbr.fx (a model_pbr material) the engine's binding was not read: by their meaning (ours)
DECAL_MAPS = ("cDecalDiffuseTex", "cDecalNormalTex", "cDecalReflectionTex")
SLOTS = {0: DECAL_MAPS, 8: ("cModelDiffTex", "cModelNormalTex", "cModelMetallicTex")}
PRECISION = 128.0  # UnpackColor: each channel in steps of 1/128, packed in base 129


def pack_color(rgb) -> float:
    """A colour as the shaders' ``UnpackColor`` reads it from one float."""
    red, green, blue = (round(min(max(float(channel), 0.0), 1.0) * PRECISION) for channel in rgb)
    base = PRECISION + 1
    return red + green * base + blue * base * base


def quad(extents: tuple[float, float]) -> CMesh:
    """The decal's rectangle of half sizes ``extents`` (x, z) at its place's height: position, normal (up), tangent
    (x), coordinate, age 0 (no fading), as ``VSInputDecal`` takes them."""
    x, z = extents
    rows = [(-x, 0, -z, 0, 0), (x, 0, -z, 1, 0), (x, 0, z, 1, 1), (-x, 0, z, 0, 1)]
    verts = np.array(
        [
            [position_x, position_y, position_z, 0, 1, 0, 1, 0, 0, coord_u, coord_v, 0]
            for position_x, position_y, position_z, coord_u, coord_v in rows
        ],
        "<f4",
    )
    elements = ((0, 7, 3, 0), (1, 7, 3, 12), (2, 7, 3, 24), (4, 7, 2, 36), (4, 7, 1, 44))
    return CMesh(verts.tobytes(), 48, elements, np.array([0, 1, 2, 0, 2, 3], np.uint32), 4)


def instance_rows(world: np.ndarray, color=(1.0, 1.0, 1.0), alpha: float = 1.0, clamp: bool = True) -> list:
    """``cDecalInstanceData`` of one decal: (packed colour, alpha, clamp the coordinates, 0), then the first three
    rows of its place (column vectors; the shader transposes them into its row-vector matrix)."""
    matrix = np.asarray(world, float)
    return [[pack_color(color), alpha, 1.0 if clamp else 0.0, 0.0], *(matrix[row].tolist() for row in range(3))]


class CDecalModel(CForwardModel):
    """A decal in the forward frame: a ``CForwardModel`` whose instance is in ``cbDecal`` (drawn in the blended pass)."""

    def fill_model(self, constants: dict) -> None:
        super().fill_model(constants)
        if "cbDecal" in constants:
            constants["cbDecal"].set_rows("cDecalInstanceData", instance_rows(self.m_world))
