"""The view's ground for Anno 117's frame: a flat square drawn into the G-buffer by the game's own model shader
(``model_pbr``'s G-buffer pass with a plain grey material), so that the deferred shading lights it, the sun's
cascades fall on it and the water and the sky read it in the scene's copies and depth, as they read the terrain.

``compile_ground(source)`` is the entry (in the background: the shaders and the material from a
``models.shaderview.ShaderSource``); ``mesh()`` the square. A stand-in of ours for the terrain:
docs/rendering/frame-117-viewer.md section 7.
"""

from __future__ import annotations

import numpy as np

from .gamedraw import Mesh

EFFECT = "model_pbr"
COLOR = (0.55, 0.55, 0.52)  # the view's own ground (modelrender) [ours]
# a plain material: no maps of its own, the colour, rough (the fxm defaults for the rest)
VALUES = {"ShaderID": "8", "DIFFUSE_ENABLED": "0", "NORMAL_ENABLED": "0", "METALLIC_TEX_ENABLED": "0",
          "cDiffuseColor.r": str(COLOR[0]), "cDiffuseColor.g": str(COLOR[1]), "cDiffuseColor.b": str(COLOR[2]),
          "cGlossinessFactor": "0.1"}  # fmt: skip
# the stored vertex: position (half4), normal, tangent, binormal (biased bytes), texture coordinate (half2)
ELEMENTS = ((0, 6, 4, 0), (1, 5, 1, 8), (2, 5, 1, 12), (3, 5, 1, 16), (4, 6, 2, 20))
STRIDE = 24


def mesh() -> Mesh:
    """The square from -1 to 1 in x and z at y = 0, facing up (its place scales and moves it)."""
    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    rows = []
    for x, z in corners:
        position = np.array([x, 0, z, 1], "<f2").tobytes()
        axes = bytes((128, 255, 128, 255, 255, 128, 128, 255, 128, 128, 255, 255))  # up, x, z: UnBias = 2 b - 1
        uv = np.array([(x + 1) / 2, (z + 1) / 2], "<f2").tobytes()
        rows.append(position + axes + uv)
    return Mesh(b"".join(rows), STRIDE, ELEMENTS, np.array([0, 2, 1, 0, 3, 2], np.uint32), 4)


def compile_ground(source) -> tuple:
    """The vertex and pixel shader of the plain material's G-buffer pass and its material (in the background)."""
    vs, ps, refs = source.stages(EFFECT, VALUES)
    return vs, ps, source.material(refs, VALUES)
