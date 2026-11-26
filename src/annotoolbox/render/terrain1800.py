"""Anno 1800's terrain drawn by its own terrain shader (``terrain.fx``, ``TerrainDefault``) on Direct3D 11, lit in
its own pixel shader (forward): 117's square nodes of the 16-byte terrain vertex (``terraindraw``), each a ``CFxcDraw``
with its constants, the three-channel normal page, its tint page and the baked arrays; an unedited stored node from
its stored mesh. Also what 2205's terrain shares with it (the compile, the bake's gamma YCC, the node as a forward
model, the arrays an edit remakes in part; ``terrain2205``). ``compile_terrain`` compiles (in the background);
``CTerrain1800(ctx, program, state, size, step, width).nodes(eye)`` gives the forward models.
docs/rendering/island-1800-2205-view.md.
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np
from PySide6.QtGui import (
    QRhiTexture,
    QRhiViewport,
)

from ..models import shaderforward
from . import frameforward, gamedraw, gameshader, gameshadow, pages1800, terraindraw, terrainpages
from .fxcdraw import CDummies, CFxcDraw
from .objects1800 import CObjectDraw, SBinding, cascade_pairs, group
from .terrainnodes import SNode

EFFECT = "data/shaders/fx/terrain.fx"
TECHNIQUE = "TerrainDefault"
SHADOW = "TerrainShadow"  # a node into the sun's shadow map (phase 4)
DETAILED, SHADOW_DETAILED = "TerrainDefaultDetailed", "TerrainShadowDetailed"  # a node tessellated from its page
# the normals-and-depth pre-pass 1800's ambient occlusion reads (its face normals: PREZ_NORMAL_RT_ENABLED)
PREPASS, PREPASS_DETAILED = "TerrainNormalsDepth", "TerrainNormalsDepthDetailed"
PREZ_NORMALS = ("PREZ_NORMAL_RT_ENABLED", "1")
TESSELLATED = True  # the near nodes tessellated from their pages where the device and the quality allow
PATCH_CORNERS = terraindraw.PATCH_CORNERS  # one rule for both games (island-1800-2205-view.md 10)
# a quarter of the shadow map a cascade: the shader's bias reads the reciprocal of its side (constants_shadow.fxh)
PARTITION_REC = 2.0
# a node's own buffers; the rest are the frame's, shared with every node and object (``objects1800.SharedBuffers``)
NODE_OWN = ("cbTerrainNode",)
CASTER_OWN = (*NODE_OWN, "cbShadowPartition")  # a cascade's draw: its cascade too (``CCaster``)
# the shipped caches' third switch set of each game's terrain shader: diffuse, normal and cavity (1800: grit, its
# baked size and the tint pages too); parallax and self-shadowing read the engine's render values
# (island-1800-2205-view.md 1) [ours]
DEFINES = {
    "anno1800": (("TERRAIN_BAKED_TEX_SIZE", "512"), ("DIFFUSE_ENABLED", ""), ("NORMAL_ENABLED", ""),
                 ("GRIT_ENABLED", ""), ("CAVITY_MAP_ENBLED", ""), ("TINT_MAP_ENABLED", "")),
    "anno2205": (("DIFFUSE_ENABLED", ""), ("NORMAL_ENABLED", ""), ("CAVITY_MAP_ENBLED", "")),
}  # fmt: skip
RENDER_PARAMS = [1.0, 0.0, 0.0, 0.0]  # cTrnRenderParams: normal map 1, no parallax, no self-shadow [E]
# the session's old tint map where a node has no tint page: a neutral tint as 1800's YCC stores it (Y and the chroma
# at the middle: x 1 after ``UncompressTintColor``); the frame's stand-ins beside (no cloud noise among them)
FALLBACKS = {**frameforward.FALLBACKS, "cTerrainTintTex": (128, 128, 128, 255),
             "cNodeGranularityMap": pages1800.GRANULARITY}  # fmt: skip
_SPACE_RE = re.compile(rb":\s*register\s*\(\s*\w+\s*,\s*space\d+\s*\)")


def encode_fs(linear: bool) -> str:
    """An albedo as both older games' bake stores it (functions_terrain.fxh BAKE_YCC_COMPRESSED, BAKE_POW2; the
    compress pass's ``convertToYCCpow2``): gamma 2.2 (``linear``: the view's linear albedo turned into it first; the
    games' own bake draws gamma already), JPEG's YCbCr, the chroma's square root about 0.5, kept as (Cb, Y, Cr)."""
    read = "texelFetch(albedo, ivec2(gl_FragCoord.xy), 0).rgb"
    gamma = f"pow(max({read}, vec3(0.0)), vec3(1.0 / 2.2))" if linear else f"clamp({read}, 0.0, 1.0)"
    return f"""
#version 330
uniform sampler2D albedo;
in vec2 vNdc; out vec4 color;
float root(float c) {{ float s = 2.0 * c - 1.0; return 0.5 + 0.5 * sign(s) * sqrt(abs(s)); }}
void main() {{
    vec3 g = {gamma};
    float y = 0.299 * g.r + 0.587 * g.g + 0.114 * g.b;
    float cb = -0.169 * g.r - 0.331 * g.g + 0.5 * g.b + 0.5;
    float cr = 0.5 * g.r - 0.419 * g.g - 0.081 * g.b + 0.5;
    color = vec4(root(cb), y, root(cr), 1.0);
}}
"""


ENCODE_FS = encode_fs(True)  # the view's own bake
Format = QRhiTexture.Format


class CTerrainSource(shaderforward.CForwardSource):
    """The game's forward compile with a Direct3D 12 register hint dropped: FXC rejects a register space below shader
    model 5.1, and the engine writes a 5.0 shader's registers itself (island-1800-2205-view.md 1)."""

    def _read(self, path: str) -> bytes | None:
        raw = super()._read(path)
        return _SPACE_RE.sub(b"", raw) if raw is not None else None


def compile_terrain(files, compiler, game: str, technique: str = TECHNIQUE, patches: bool = False) -> tuple:
    """The stages of ``technique`` (``TerrainDefault``; ``SHADOW``; ``PREPASS``; with ``patches`` the tessellated
    ``DETAILED`` / ``SHADOW_DETAILED`` / ``PREPASS_DETAILED``: hull and domain too) by key (``vs``, ``ps``, ``hs``,
    ``ds``) and its states, the colour pass as the view draws it without the depth pre-pass (``EQUAL`` against it
    becomes ``LESS_EQUAL``, the depth written)."""
    source = CTerrainSource(files, compiler, game)
    defines = (*DEFINES[game], PREZ_NORMALS) if technique in (PREPASS, PREPASS_DETAILED) else DEFINES[game]
    vs, ps = source.effect_pass(EFFECT, defines, technique)[:2]
    stages = {"vs": vs, "ps": ps}
    if patches:
        stages.update(
            {stage_key: source.call_stage(EFFECT, defines, technique, stage_key) for stage_key in ("hs", "ds")}
        )
    state = source.technique_state(EFFECT, defines, technique)
    if state.depth_func == "equal" or not state.depth_write:
        state = replace(state, depth_func="less_equal", depth_write=True)
    return stages, state


def program(stages: dict) -> gamedraw.CProgram:
    """A ``compile_terrain`` stage set as one program (tessellated where it has a hull and domain shader)."""
    return gamedraw.CProgram(stages["vs"], stages["ps"], hull_stage=stages.get("hs"), domain_stage=stages.get("ds"))


def ycc_decode(stored: np.ndarray) -> np.ndarray:
    """The linear RGB the games' pixel shader reads from stored (Cb', Y, Cr') bytes as floats 0..1 (the shader's
    formulas: the check of ``ENCODE_FS``)."""
    channels = np.asarray(stored, float)
    chroma = channels[..., [0, 2]] * 2 - 1
    chroma = chroma * chroma * np.sign(chroma) * 0.5 + 0.5
    y, cb, cr = channels[..., 1], chroma[..., 0] - 0.5, chroma[..., 1] - 0.5
    rgb = np.stack([y + 1.402 * cr, y - 0.344 * cb - 0.714 * cr, y + 1.772 * cb], -1)
    return np.abs(np.maximum(rgb, 0.0)) ** 2.2


def ycc_encode(linear: np.ndarray) -> np.ndarray:
    """``ENCODE_FS`` on the CPU: linear RGB 0..1 -> stored (Cb', Y, Cr') 0..1."""
    gamma = np.maximum(np.asarray(linear, float), 0.0) ** (1 / 2.2)
    red, green, blue = gamma[..., 0], gamma[..., 1], gamma[..., 2]
    y = 0.299 * red + 0.587 * green + 0.114 * blue
    cb = -0.169 * red - 0.331 * green + 0.5 * blue + 0.5
    cr = 0.5 * red - 0.419 * green - 0.081 * blue + 0.5
    root = lambda value: 0.5 + 0.5 * np.sign(2 * value - 1) * np.sqrt(np.abs(2 * value - 1))  # noqa: E731
    return np.stack([root(cb), y, root(cr)], -1)


def normal_page_xzy(heights: np.ndarray, step_tiles: float) -> np.ndarray:
    """117's normal page (``terraindraw.normal_page``: a texel per cell of the heights) as 1800 reads it: the
    normal's x, z and y as three unorm bytes and a fourth (``UnpackTerrainNodeNormalMap``: ``normalize(sample.xzy *
    2 - 1)``) [rows, cols, 4]."""
    xz_bytes = terraindraw.normal_page(heights, step_tiles)
    xz = xz_bytes.astype(np.float64) / 255 * 2 - 1
    y = np.sqrt(np.clip(1 - (xz**2).sum(-1), 0.0, 1.0))
    out = np.full(xz.shape[:2] + (4,), 255, np.uint8)
    out[..., :2] = xz_bytes
    out[..., 2] = np.clip(np.rint((y * 0.5 + 0.5) * 255), 0, 255)
    return out


def node_values(level: int, layer: int, tint=None, page=(0.0, 0.0, 1.0)) -> dict:
    """1800's ``cbTerrainNode``: 117's names (``terraindraw.node_values``; ``tint`` the tint page's parameters, None:
    none; ``page`` its height and normal page's) with the level in ``cTrnChunkLevelParams`` (the new normal format:
    bit 4 clear)."""
    return {**terraindraw.node_values(level, layer, page, tint=tint), "cTrnChunkLevelParams": level}


class _CInPartition:
    """A command buffer whose viewport is one cascade's part of the shadow map (``gameshadow.DIVISIONS``: its
    offset and size in the map's texture space, top down; QRhi's viewport counts from the bottom)."""

    def __init__(self, command_buffer, division: tuple, size_px: tuple[int, int]) -> None:
        self._m_cb, self._m_rect = command_buffer, gamedraw.share_rect(size_px, division)

    def setViewport(self, _whole) -> None:  # QRhiCommandBuffer's name
        self._m_cb.setViewport(QRhiViewport(*self._m_rect))

    def __getattr__(self, name: str):
        return getattr(self._m_cb, name)


class CCaster:
    """A node drawn into the sun's shadow map by ``SHADOW`` (the frame's ``shadow`` of a model): 1800's shader takes
    its cascade from ``cCurrentCascade`` and the engine draws each into its part of the map (its models place
    themselves there by the instance), so a draw per cascade read (``instances``, set by the frame) in that part;
    2205's single map once. ``make()`` makes a draw, ``partition()`` is the side of one cascade's part of the map
    as a share of the map's (the shader's bias)."""

    def __init__(self, make, cascaded: bool, partition) -> None:
        self.m_make, self.m_cascaded, self.m_partition = make, cascaded, partition
        self.m_draws: list[CFxcDraw] = []
        self.m_instances = 1
        self.m_reach: set[int] | None = None  # the cascades its box reaches into (``cull``); None: every one read
        self.m_box: np.ndarray | None = None  # its node's eight corners in the view's axes
        self._m_shown: list[int] = []

    def cull(self, matrices: list) -> None:
        """Drawn only into the cascades its node's box reaches into (``objects1800.cascade_pairs``)."""
        if self.m_box is None:
            return
        min_corner, max_corner = self.m_box.min(0), self.m_box.max(0)
        found = cascade_pairs(
            ((min_corner + max_corner) / 2)[None], np.array([np.linalg.norm(max_corner - min_corner) / 2]), matrices
        )
        self.m_reach = {int(i) for i in found[:, 1]}

    def prepare(self, render_pass, signature, batch, textures: dict, fill) -> None:
        cascade_count = max(1, self.m_instances) if self.m_cascaded else 1
        self._m_shown = [
            i for i in range(cascade_count) if not self.m_cascaded or self.m_reach is None or i in self.m_reach
        ]
        while len(self.m_draws) < cascade_count:
            self.m_draws.append(self.m_make())
        reciprocal_side = self.m_partition()
        for i in self._m_shown:
            draw = self.m_draws[i]

            def own(constants: dict, i=i) -> None:
                fill(constants)
                gameshader.set_values(
                    constants.values(),
                    {"cCurrentCascade": i, "cShadowPartitionRecSize": [reciprocal_side, reciprocal_side]},
                )

            draw.prepare(render_pass, signature, batch, textures, own)

    def draw(self, command_buffer, size_px) -> None:
        if not self.m_cascaded:
            self.m_draws[0].draw(command_buffer, size_px)
            return
        for i in self._m_shown:
            self.m_draws[i].draw(_CInPartition(command_buffer, gameshadow.DIVISIONS[i], size_px), size_px)

    def release(self) -> None:
        for draw in self.m_draws:
            draw.release()
        self.m_draws = []


class CTerrainNode:
    """One node as the forward frame draws it (``frameforward.CForwardModel``'s shape): its ``CFxcDraw``, its
    constants (``values``), the terrain's textures by name (``shared``) and its own (``own``), its ``CCaster``."""

    def __init__(self, draw: CFxcDraw, values: dict, shared: dict, own: dict | None = None) -> None:
        self.m_draw_call, self.m_values, self.m_shared, self.m_own = draw, values, shared, own or {}
        self.m_box: np.ndarray | None = None  # its eight corners in the view's axes (``corners``)
        self.m_world = np.identity(4)
        self.m_shadow: CCaster | None = None
        self.m_prepass: CFxcDraw | None = None  # its normals and depth for 1800's occlusion (the frame's pre-pass)
        self.m_blended = False
        self.m_later: list = []
        self.m_node: SNode | None = None
        self.m_kind: tuple = ("grid", None, 0)

    def fresh(self) -> bool:
        """Whether its draw's resources are not made yet (``nativepass.prepared`` spreads the first prepares)."""
        return not self.m_draw_call.m_keep

    def prepare(self, render_pass, signature, batch, camera: gameshader.SCamera, light: dict, maps: dict | None = None,
                draw: CFxcDraw | None = None) -> None:  # fmt: skip
        """This frame's constants for the colour pass, or ``draw`` (the frame's pre-pass)."""

        def fill(constants: dict) -> None:  # one group of buffers at a time (``objects1800.CObjectDraw``)
            if group(next(iter(constants)), NODE_OWN) == "frame":
                frameforward.fill_frame(constants, camera, light)
            self.fill_model(constants)

        (draw or self.m_draw_call).prepare(render_pass, signature, batch, {**(maps or {}), **self.textures()}, fill)

    def textures(self) -> dict:
        return {**self.m_shared, **self.m_own}

    def fill_model(self, constants: dict) -> None:
        """The node's constants (its shadow pass's too)."""
        gameshader.set_values(constants.values(), self.m_values)

    fill_caster = fill_model  # 1800's cascades: the cascade is the caster's (``CCaster``)

    def draw(self, command_buffer, size_px) -> None:
        self.m_draw_call.draw(command_buffer, size_px)

    def corners(self) -> np.ndarray:
        """The node's box in the view's axes (the shadow's fit): the vertex holds packed nuggets, no position the
        mesh's own box could read."""
        return self.m_box

    def release(self) -> None:
        self.m_draw_call.release()
        for part in (self.m_shadow, self.m_prepass):
            if part is not None:
                part.release()


class CTerrain1800(terraindraw.CTerrain):
    """117's node set (``terraindraw.CTerrain``: the nodes, the slices, the stored meshes' choice, the tint pages, the
    heights' changes, the normals and the page made again only where an edit reached) with each node a forward model
    of 1800's shader, its normal page in three channels."""

    PAGE_FORMAT = Format.RGBA8  # ``normal_page_xzy``'s four bytes
    CASCADED = True  # the sun's shadow in cascades (2205: one map)

    def __init__(self, ctx, program: gamedraw.CProgram, state, size_tiles: float, step_tiles: float, width_samples: int,
                 detailed: gamedraw.CProgram | None = None) -> None:  # fmt: skip
        super().__init__(ctx.m_rhi, program, size_tiles, step_tiles, width_samples, detailed)
        self.m_ctx, self.m_state = ctx, state
        self.m_casting: tuple | None = None  # (program, state) of ``SHADOW``: the nodes cast (phase 4); None: not
        self.m_casting_detailed: tuple | None = None  # the same of ``SHADOW_DETAILED`` for the tessellated nodes
        self.m_prez: tuple | None = None  # (program, state) of ``PREPASS``: the nodes in the pre-pass; None: not
        self.m_prez_detailed: tuple | None = None  # the same of ``PREPASS_DETAILED``
        self.m_partition = PARTITION_REC  # the reciprocal of a cascade's side as a share of the map's (``CCaster``)
        self.m_dummies = CDummies(ctx.m_rhi)
        self.m_shared: tuple | None = None  # the frame's buffers of the colour pass and the casters' (the owner's)
        self.m_island = {**terraindraw.island_values(size_tiles), "cTrnRenderParams": RENDER_PARAMS,
                       "cTerrainSize": [size_tiles, size_tiles, 1 / size_tiles, 1 / size_tiles]}  # fmt: skip
        self._m_page = terraindraw.CRegional(normal_page_xzy, cells=True)

    def _values(self, node: SNode, layer: int, kind: tuple, tint) -> dict:
        page = kind[1]
        params = terrainpages.page_params(page, self.m_size_tiles) if page is not None else (0.0, 0.0, 1.0)
        return {**self.m_island, **node_values(node.level, layer, tint, params)}

    def _page_textures(self, kind: tuple) -> dict:
        """A node's rebuilt page (``pages1800``): its heights, its normals, its granularity, the session's heights;
        none without."""
        if kind[1] is None or self.m_pages is None or kind[1] not in self.m_pages.m_ready:
            return {}
        height, normal = self.m_pages.m_ready[kind[1]]
        own = {"cNodeHeightMap": height, "cNodeNormalMap": normal, "cTerrainHeightTex": self.m_pages.m_coarse}
        granularity = self.m_pages.m_granularity.get(kind[1])
        return {**own, "cNodeGranularityMap": granularity} if granularity is not None else own

    def _own(self, node: SNode, layer: int, kind: tuple) -> dict:
        """A node's own textures beyond the terrain's and its tint page (none: 1800's bake is the arrays' slice)."""
        return {}

    def remake_nodes(self) -> None:
        """Every node made again with its slice and bake kept (the island's files came in: the tint pages)."""
        for model in self.m_models.values():
            model.release()
        self.m_models = {}

    def _make(self, node: SNode, layer: int, kind: tuple) -> CTerrainNode:
        tint = self._tint(node)
        own = {"cNodeTintMap": tint[0]} if tint is not None else {}
        detailed = kind[0] == "detailed"
        draw = self._draw(self.m_detailed if detailed else self.m_program, self._mesh(node, kind), self.m_state, 0)
        draw.m_is_mirrored = True  # the view's axes turn x over (1800's island matrix, 2205's mirrored patch)
        draw.m_fallbacks = dict(FALLBACKS)
        values = self._values(node, layer, kind, tint[1] if tint is not None else None)
        own = {**own, **self._page_textures(kind), **self._own(node, layer, kind)}
        model = CTerrainNode(draw, values, self.m_textures, own)
        model.m_node, model.m_kind, model.m_box = node, kind, self.node_box(node)
        prez = self.m_prez_detailed if detailed else self.m_prez
        if prez is not None:  # the frame hands it its own shared buffers (``CForwardFrame._occlusion``)
            model.m_prepass = self._draw(prez[0], draw.m_mesh, prez[1], 0)
            model.m_prepass.m_is_mirrored, model.m_prepass.m_fallbacks = True, dict(FALLBACKS)
        casting = self.m_casting_detailed if detailed else self.m_casting
        if casting is not None:
            make = lambda: self._caster_draw(draw.m_mesh, casting)  # noqa: E731
            model.m_shadow = CCaster(make, self.CASCADED, lambda: self.m_partition)
            model.m_shadow.m_box = model.m_box
        return model

    def node_box(self, node: SNode) -> np.ndarray:
        """A node's eight corners in the view's axes (x turned over): its heights' range, the skirt below."""
        col, row, cols, rows = node.samples(self.m_step_tiles, self.m_width_samples)
        node_heights = self.m_heights[row : row + rows, col : col + cols]
        min_height, max_height = float(node_heights.min()) - terraindraw.GRID_SKIRT, float(node_heights.max())
        min_x, max_x = self.m_size_tiles - node.x - node.tiles, self.m_size_tiles - node.x
        return np.array(
            [[x, y, z] for x in (min_x, max_x) for y in (min_height, max_height) for z in (node.z, node.z + node.tiles)]
        )

    def _caster_draw(self, mesh: gamedraw.CMesh, casting: tuple) -> CFxcDraw:
        """A node's shadow draw: 1800's cascades look along the light with x right and y up, which turns the
        picture over once more, so the view's mirror and theirs cancel (the viewer's rule for 1800's casters; drawn
        mirrored the terrain cast its back faces, the skirts' band: island-1800-2205-view.md 8); 2205's map keeps it."""
        program, state = casting
        draw = self._draw(program, mesh, state, 1)
        draw.m_is_mirrored, draw.m_fallbacks = not self.CASCADED, dict(FALLBACKS)
        return draw

    def _draw(self, program, mesh: gamedraw.CMesh, state, pass_index: int) -> CFxcDraw:
        """A node's draw (``which``: 0 the colour pass, 1 its cascades), binding the frame's buffers of that pass
        shared (``shared``) where the owner hands them."""
        if self.m_shared is None:
            return CFxcDraw(self.m_ctx, program, mesh, state, self.m_dummies)
        own = (NODE_OWN, CASTER_OWN)[pass_index]
        return CObjectDraw(self.m_ctx, program, mesh, state, self.m_dummies, SBinding(self.m_shared[pass_index], own))

    def casters(self, models: list) -> list:
        return []

    def release(self) -> None:
        super().release()
        self.m_dummies.release(self.m_ctx)
