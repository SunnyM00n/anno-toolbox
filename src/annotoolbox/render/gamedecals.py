"""Anno 117's decals by its own decal shader: a config's DECAL drawn by ``decal_pbr.fx`` ``TerrainDecalDeferred`` into
the G-buffer after the terrain, laid on the session's heights by the shader itself, many alike in one draw (the
engine's ``cDecalInstanceData``: five rows a decal).

``decal_looks(placed)`` groups an island's decals by material and shape; ``decal_grid`` is the mesh of a shape and
``decal_rows`` the rows of its decals; a ``CDecalDraw`` is one draw. The island view compiles a look and makes the
draws (``editor.desktop.desktopgamedecals``). docs/rendering/island-117-view.md "Phase 5", "Config decals".
"""

from __future__ import annotations

import math

import numpy as np

from . import gamedraw, renderstates

SHADER_ID = 1  # a MATERIAL's ShaderID that draws by decal_pbr (shadercache.SHADER_FILES)
EFFECT = "decal_pbr"
ROWS = 5  # NUM_CONSTANTS_PER_DECAL (constants_decal.fxh)
PER_DRAW = 1024 // ROWS  # MAX_NUM_DECAL_CONSTANTS: the decals one draw holds
CELL, MAX_CELLS = 0.5, 64  # the grid's cell in world units and its most cells a side [ours]
NEUTRAL = 0xFF808080  # a decal's own colour, ARGB, its RGB doubled by the shader: white, opaque [ours]
NO_MIN_HEIGHT = -1000.0  # the height a decal is held at least (a building socket's): none [ours]
FULL_FADE = 0xFF  # the displacement's fade (row 0's alpha, low byte): full
# the engine's DecalVertex as the vertex shader reads it: position, normal, tangent, coordinate (age, coverage and
# aspect read 0 from the inputs the mesh lacks)
VERTEX = np.dtype([("pos", "<f4", 3), ("n", "<f4", 3), ("t", "<f4", 3), ("uv", "<f4", 2)])
ELEMENTS = ((0, 7, 3, 0), (1, 7, 3, 12), (2, 7, 3, 24), (4, 7, 2, 36))


def decal_looks(placed) -> dict:
    """The decals of ``placed`` ((instance matrices [n, 4, 4] in the view's axes, the config's decals) per model)
    that draw by this shader: {material values: {(half sizes, coordinate rectangle): [world matrices]}}; a hidden
    instance (scale 0) left out."""
    out: dict = {}
    for materials, decals in placed:
        for decal in decals:
            values = decal.get("values") or {}
            if (
                str(values.get("ShaderID", "")).strip() != str(SHADER_ID)
                or decal.get("kind", "").strip().lower() == "vehicle"
            ):
                continue
            shape = (
                tuple(float(extent) for extent in decal["extents"]),
                tuple(decal.get("texcoords") or (0.0, 0.0, 1.0, 1.0)),
            )
            worlds = out.setdefault(tuple(sorted(values.items())), {}).setdefault(shape, [])
            worlds += [matrix @ decal["matrix"] for matrix in materials if np.abs(matrix[:3, :3]).sum() > 0]
    return out


def decal_grid(extents: tuple[float, float], texcoords: tuple = (0.0, 0.0, 1.0, 1.0)) -> gamedraw.CMesh:
    """A decal's flat grid in its own axes (x across ``extents[0]``, z across ``extents[1]``: half sizes) of about
    ``CELL`` a cell, its coordinate over the rectangle ``texcoords`` (u0, v0, u1, v1); the shader lays it on the
    terrain."""
    ex, ez = (max(float(extent), 1e-6) for extent in extents)
    nx, nz = (int(min(MAX_CELLS, max(1, math.ceil(2 * extent / CELL)))) for extent in (ex, ez))
    u, v = np.meshgrid(np.linspace(0, 1, nx + 1), np.linspace(0, 1, nz + 1))
    vert = np.zeros(u.size, VERTEX)
    vert["pos"] = np.stack([(u * 2 - 1) * ex, np.zeros_like(u), (v * 2 - 1) * ez], -1).reshape(-1, 3)
    vert["n"], vert["t"] = (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)
    u0, v0, u1, v1 = (float(c) for c in texcoords)
    vert["uv"] = np.stack([u0 + u * (u1 - u0), v0 + v * (v1 - v0)], -1).reshape(-1, 2)
    i = np.arange(nx)[None, :] + np.arange(nz)[:, None] * (nx + 1)
    quads = np.stack([i, i + 1, i + nx + 2, i, i + nx + 2, i + nx + 1], -1).reshape(-1).astype(np.uint32)
    return gamedraw.CMesh(vert.tobytes(), VERTEX.itemsize, ELEMENTS, quads, len(vert))


def decal_rows(worlds: np.ndarray, extents: tuple[float, float]) -> bytes:
    """The decals' ``cDecalInstanceData`` (``ROWS`` float4 each): the colour, the half sizes as 8.8 fixed point, the
    lowest height and the fade; then the top three rows of its place (column vectors); no damage, no flag."""
    worlds = np.asarray(worlds, np.float32).reshape(-1, 4, 4)
    rows = np.zeros((len(worlds), ROWS, 4), np.uint32)
    as_float = rows.view(np.float32)
    ex, ez = (min(0xFFFF, round(float(extent) * 256)) for extent in extents)
    rows[:, 0, 0], rows[:, 0, 1], rows[:, 0, 3] = NEUTRAL, ex | (ez << 16), FULL_FADE
    as_float[:, 0, 2] = NO_MIN_HEIGHT
    as_float[:, 1:4, :] = worlds[:, :3, :]
    return rows.tobytes()


def decal_state(states: dict) -> renderstates.SPassState:
    """The technique's states into the G-buffer's four targets (``sbDecalDeferred``: blended, depth tested in front,
    not written, the alphas and the velocity kept by its write masks)."""
    return renderstates.pass_state(states, 4)


class CDecalDraw(gamedraw.CGameModel):
    """Up to ``PER_DRAW`` decals of one look and shape in one draw (``placed``: their places and the shape's
    extents): their rows the start of ``cbDecal``; ``maps`` the look's texture arrays, made by its first draw and
    kept by the owner (``release_maps``); ``textures`` the maps the frame does not hand that the owner stands in for
    by name (the terrain's session maps)."""

    def __init__(self, rhi, program, mesh, material, state, placed: tuple, maps: list, textures=None) -> None:
        super().__init__(rhi, program, mesh, material, None, state)
        self.m_maps, self.m_textures = maps, textures if textures is not None else {}
        self.m_count = len(placed[0])
        self.m_raw = {"cbDecal": decal_rows(*placed)}
        self.m_values = {"cDepthBiasFactor": 1.0}  # outside the first-person mode [I]

    def prepare(self, render_pass, signature, batch, camera, fill=None, shared: dict | None = None) -> None:
        if not self.m_keep and self.m_maps:  # the look's arrays, made by another of its draws
            self.m_given_arrays = self.m_maps
        super().prepare(render_pass, signature, batch, camera, fill, shared)
        if not self.m_maps and self.m_arrays:  # the first draw's arrays become the look's
            self.m_maps.extend(self.m_arrays)
            self.m_keep = [kept for kept in self.m_keep if all(kept is not array for array in self.m_arrays)]

    def _texture(self, name: str, shared: dict, white):
        if name not in shared and name in self.m_textures:
            return self.m_textures[name]
        return super()._texture(name, shared, white)

    def draw(self, command_buffer, size_px: tuple[int, int], instances: int = 1) -> None:
        super().draw(command_buffer, size_px, self.m_count)


def decal_draws(rhi, program, material, state, shapes: dict, maps: list, textures=None) -> list[CDecalDraw]:
    """The draws of one look (``maps``: its arrays, filled by the first that draws; ``textures``: ``CDecalDraw``'s): per
    shape its grid and its decals ``PER_DRAW`` at a time."""
    out = []
    for (extents, texcoords), worlds in shapes.items():
        mesh = decal_grid(extents, texcoords)
        for k in range(0, len(worlds), PER_DRAW):
            placed = (worlds[k : k + PER_DRAW], extents)
            out.append(CDecalDraw(rhi, program, mesh, material, state, placed, maps, textures))
    return out


def release_maps(maps: list) -> None:
    """A look's arrays (``decal_draws``), once its draws are released."""
    for texture in maps:
        texture.destroy()
    maps.clear()
