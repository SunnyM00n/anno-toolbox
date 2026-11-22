"""Anno 2070's cloth (a config's CLOTH: flags, banners) drawn into the G-buffer by its own ``cloth.fx`` and casting its
shadow by its ``Obj2Shadow``: the cloth file's vertices (at rest, or as the view's simulation moves them) with the
normal and the tangent frame the manager feeds packed into bytes, its place in ``cMtxWorld``, its object colour in
``cObjectColor``.

``cloth_mesh(vertices, indices)`` is the mesh as the shader takes it, ``reposed`` its vertices moved, ``defines(values)``
the variant, ``techniques`` the pair of a cloth's draws, ``CClothModel2070`` the ``frame2070.CModel2070`` of a cloth.
docs/rendering/shader-compile.md section 10, "Cloth".
"""

from __future__ import annotations

import numpy as np

from ..models.tangents import frames
from . import gameshader as gs
from .clothsim import on_surface, unit_bytes
from .frame2070 import CModel2070
from .gamedraw import CMesh

FX = "data/system/shaders/fx/cloth.fx"
EFFECTS = {0: FX}  # the CLOTH owner's table index -> effect; every shipped cloth material is 0 (materials-2070 3)
SWITCHES = ("DIFFUSE", "NORMAL", "PARALLAX", "RIPPLES")  # what clothbase.fxh reads (ripples need a normal or parallax)
INTACT = [0.0, 0.0, 0.0, 0.0]  # cObjectDamage: w 0 keeps the colour and the alpha (AddDamageRGB / AddDamageAlpha)
# the vertex as the cloth manager feeds it (p3c4,t2,i4i4): position, the normal packed into a colour, the uv, the
# tangent and bitangent as four unsigned integers each; (usage, type, count, offset), rdm.md's type codes
ELEMENTS = ((0, 7, 3, 0), (5, 5, 1, 12), (4, 7, 2, 16), (7, 4, 4, 24), (7, 4, 4, 40))
STRIDE = 56


def techniques(values: dict) -> tuple[str, str]:
    """The cloth's draw and shadow techniques: a double-sided cloth (``m_IsDoubleSided``) has its own pair."""
    two = int(values.get("DoubleSided", 0))
    return ("ClothDoubleSided", "Obj2ShadowDoubleSided") if two else ("Cloth", "Obj2Shadow")


def defines(values: dict) -> tuple:
    """The variant a cloth material compiles: its switches that ``clothbase.fxh`` reads (the effect sets its own
    vertex format)."""
    return tuple((f"{name}_ENABLED", "1") for name in SWITCHES if int(values.get(f"Switch.{name.lower()}", 0)))


def effect(values: dict) -> str | None:
    """The effect of a cloth material, None for an index the table lacks."""
    return EFFECTS.get(int(values.get("ShaderIndex", 0)))


def packed(vectors: np.ndarray) -> np.ndarray:
    """Unit vectors [n, 3] as the shader unpacks them (``GetColor`` reads ``.bgra``, then ``* 2 - 1``): bytes z, y,
    x, 255."""
    encoded = unit_bytes(vectors)
    return np.stack([encoded[:, 2], encoded[:, 1], encoded[:, 0], np.full(len(encoded), 255, np.uint8)], axis=1)


def cloth_mesh(vertices: np.ndarray, indices) -> CMesh:
    """A cloth file's vertices (position, normal, uv per row, as ``models.rdmmesh`` reads them) with one group's
    triangles in the layout ``cloth.fx`` takes: the normal and the tangent frame made from the triangles and uvs
    (``models.tangents``: the tangents stored negated, as the shader reads them)."""
    rows = np.asarray(vertices, np.float32)
    index = np.asarray(indices, np.uint32)
    positions, uv = (
        [tuple(position) for position in rows[:, :3].tolist()],
        [tuple(texcoord) for texcoord in rows[:, 6:8].tolist()],
    )
    normals, tangents, bitangents = frames(positions, uv, index.tolist())
    count = len(rows)
    data = np.zeros((count, STRIDE), np.uint8)
    data[:, 0:12] = np.ascontiguousarray(rows[:, :3], "<f4").view(np.uint8)
    data[:, 12:16] = packed(normals)
    data[:, 16:24] = np.ascontiguousarray(rows[:, 6:8], "<f4").view(np.uint8)
    data[:, 24:40] = packed(-np.asarray(tangents)).astype("<u4").view(np.uint8)
    data[:, 40:56] = packed(-np.asarray(bitangents)).astype("<u4").view(np.uint8)
    return CMesh(data.tobytes(), STRIDE, ELEMENTS, index, count)


def unpacked(encoded: np.ndarray) -> np.ndarray:
    """``packed``'s bytes (or its four integers) [n, 4] back as vectors [n, 3]."""
    encoded = np.asarray(encoded, float)
    return np.stack([encoded[:, 2], encoded[:, 1], encoded[:, 0]], axis=1) / 127.5 - 1.0


def reposed(mesh: CMesh, positions: np.ndarray, normals: np.ndarray) -> bytes:
    """``cloth_mesh``'s vertices moved by the simulation (``render.clothmesh``): the positions and the packed normals
    written, the stored tangent and bitangent turned onto the new normal (their rest direction kept on the surface)."""
    raw = np.frombuffer(mesh.m_rest or mesh.m_data, np.uint8).reshape(mesh.m_count, STRIDE).copy()
    normals_array = np.asarray(normals, float)
    raw[:, 0:12] = np.ascontiguousarray(np.asarray(positions, "<f4")).view(np.uint8)
    raw[:, 12:16] = packed(normals_array)
    for offset_bytes in (24, 40):
        tangent = unpacked(np.ascontiguousarray(raw[:, offset_bytes : offset_bytes + 16]).view("<u4"))
        raw[:, offset_bytes : offset_bytes + 16] = (
            packed(on_surface(tangent, normals_array)).astype("<u4").view(np.uint8)
        )
    return raw.tobytes()


class CClothModel2070(CModel2070):
    """A cloth in 2070's frame: its place in ``cMtxWorld`` (and times the camera in ``cMtxWorldViewProj``), its
    colour (rgba) in ``cObjectColor``, ``ripple`` its ``cClothRipple`` (size, normal and parallax strength)."""

    def __init__(self, draw, shadow=None) -> None:
        super().__init__(draw, shadow)
        self.m_ripple = (1.0, 0.0, 0.0)
        self.m_view_proj = np.identity(4)

    def fill_model(self, constants: dict) -> None:
        world_matrix = np.asarray(self.m_world, float)
        gs.set_values(constants.values(), {"cMtxWorld": gs.as_columns(world_matrix), "cMtxWorldViewProj": gs.as_columns(world_matrix @ self.m_view_proj),
                                   "cObjectColor": [*self.m_color, 1.0][:4], "cObjectDamage": INTACT,
                                   "cClothRipple": [*self.m_ripple, 0.0]})  # fmt: skip

    def prepare(self, render_pass, signature, batch, camera: gs.SCamera, sun, frame_maps=None) -> None:
        self.m_view_proj = camera.view @ camera.projection
        super().prepare(render_pass, signature, batch, camera, sun, frame_maps)
