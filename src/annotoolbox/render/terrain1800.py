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
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
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


def sample_span(min_tile: float, max_tile: float, step_tiles: float, count: int) -> tuple[int, int]:
    """The height samples (first, past the last) a box's side of tiles ``lo`` .. ``hi`` reaches."""
    return max(0, int(np.floor(min_tile / step_tiles))), min(count, int(np.ceil(max_tile / step_tiles)) + 1)


class CRegional:
    """An array made from the heights by ``make(heights, step)`` - per sample (``cells`` False: a value reads its
    neighbours, as central differences) or per cell (True: a cell reads its four corners) - kept between edits and
    made again only over the samples an edit reached: a continental island's whole page took half a second, on the
    window's thread at every stroke. ``region`` is what the last ``get`` changed ((rows, cols) of values; None: all)."""

    def __init__(self, make, cells: bool) -> None:
        self.m_make, self.m_cells = make, cells
        self.m_value: np.ndarray | None = None
        self.m_pending: list | None = None  # sample boxes (r0, r1, c0, c1) to remake; None: all
        self.m_region: tuple | None = None

    def spoil(self, samples: tuple[int, int, int, int] | None) -> None:
        """The samples (r0, r1, c0, c1) changed; None: all of them (new heights, another grid)."""
        if samples is None or self.m_pending is None:  # a whole remake asked for, or already due
            self.m_pending = None
            return
        box = self.m_pending[0] if self.m_pending else samples
        self.m_pending = [
            tuple(pick(old, new) for pick, old, new in zip((min, max, min, max), box, samples, strict=True))
        ]

    def _span(self, first: int, last: int, count: int) -> tuple[int, int, int, int]:
        """(slice of samples to make from, values it changes) along one axis for changed samples a .. b."""
        if self.m_cells:  # cell j reads samples j and j + 1: the slice's cells are exactly those changed
            start, stop = max(0, first - 1), min(count, last + 1)
            return start, stop, start, stop - 1
        start, stop = (
            max(0, first - 2),
            min(count, last + 2),
        )  # a value reads one beyond: those beside the change change too
        return start, stop, max(0, first - 1), min(count, last + 1)

    def get(self, heights: np.ndarray, step_tiles: float) -> np.ndarray:
        """The array for ``heights``: made whole the first time and after ``spoil(None)``, else in the spoilt part."""
        if self.m_value is None or self.m_pending is None:
            self.m_value, self.m_region, self.m_pending = self.m_make(heights, step_tiles), None, []
            return self.m_value
        for row_start, row_end, col_start, col_end in self.m_pending:
            source_row_start, source_row_end, value_row_start, value_row_end = self._span(
                row_start, row_end, heights.shape[0]
            )
            source_col_start, source_col_end, value_col_start, value_col_end = self._span(
                col_start, col_end, heights.shape[1]
            )
            part = self.m_make(heights[source_row_start:source_row_end, source_col_start:source_col_end], step_tiles)
            self.m_value[value_row_start:value_row_end, value_col_start:value_col_end] = part[
                value_row_start - source_row_start : value_row_end - source_row_start,
                value_col_start - source_col_start : value_col_end - source_col_start,
            ]
            self.m_region = ((value_row_start, value_row_end), (value_col_start, value_col_end))
        self.m_pending = []
        return self.m_value


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
    heights' changes) with each node a forward model of 1800's shader, its normal page in three channels; the vertex
    normals and the page made again only where an edit reached (``CRegional``)."""

    PAGE = "cNodeNormalMap"  # the texture the normal page goes into
    ELEMENTS = terraindraw.ELEMENTS  # the vertex's three inputs as the shader names them (COLOR0..2)
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
        self._m_vertex_normals = CRegional(terraindraw.vertex_normals, cells=False)
        self._m_page = CRegional(normal_page_xzy, cells=True)

    def set_heights(self, heights: np.ndarray, box=None) -> None:
        """117's rule (the nodes ``box`` touches made again), the normals and the page remade where it reaches."""
        same = self.m_heights is not None and box is not None and heights.shape == self.m_heights.shape
        samples = None
        if same:
            rows = sample_span(box[1], box[3], self.m_step_tiles, heights.shape[0])
            cols = sample_span(box[0], box[2], self.m_step_tiles, heights.shape[1])
            samples = (*rows, *cols)
        for part in (self._m_vertex_normals, self._m_page):
            part.spoil(samples)
        super().set_heights(heights, box)

    def normals(self) -> np.ndarray:
        return self._m_vertex_normals.get(self.m_heights, self.m_step_tiles)

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

    def _mesh(self, node: SNode, kind: tuple) -> gamedraw.CMesh:
        """A node's vertices: as the island stores them ("stored"), else from the heights with a skirt that covers
        the steps to the stored nodes beside it (117's rule)."""
        if kind[0] == "stored":
            tiles = 2**node.level
            vertices, indices = self.m_source.meshes[node.level][(int(node.x // tiles), int(node.z // tiles))]
            return gamedraw.CMesh(vertices, terraindraw.STRIDE, self.ELEMENTS, indices,
                                 len(vertices) // terraindraw.STRIDE)  # fmt: skip
        _col, _row, cols, rows = node.samples(self.m_step_tiles, self.m_width_samples)
        data = terraindraw.encode_vertices(
            self.m_heights, self.m_step_tiles, node, self.m_width_samples, self.normals()
        )
        if kind[0] == "detailed":  # quad patches, the skirt hung by the domain shader below the page's surface
            data, patches = terraindraw.with_skirts(data, cols, rows)
            patches = patches.reshape(-1, 4)[:, PATCH_CORNERS].ravel()
            return gamedraw.CMesh(data, terraindraw.STRIDE, self.ELEMENTS, patches, len(data) // terraindraw.STRIDE)
        data, quads = terraindraw.with_skirts(data, cols, rows, terraindraw.GRID_SKIRT)
        return gamedraw.CMesh(data, terraindraw.STRIDE, self.ELEMENTS, terraindraw.quad_triangles(quads),
                             len(data) // terraindraw.STRIDE)  # fmt: skip

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

    def page_region(self) -> tuple[np.ndarray, tuple[int, int]] | None:
        """The normal page's texels to upload and where they go (column, row): the whole page, or the part the last
        edit changed; None when nothing changed."""
        page = self._m_page.get(self.m_heights, self.m_step_tiles)
        if self._m_page.m_region is None:
            return page, (0, 0)
        (row_start, row_end), (col_start, col_end) = self._m_page.m_region
        return page[row_start:row_end, col_start:col_end], (col_start, row_start)

    def uploads(self, batch) -> None:
        """117's uploads (the stand-ins of what no bake hands, the tint pages) with this game's normal page in place
        of 117's, written into the same texture while its size stays (only the part an edit changed)."""
        dirty, self._m_page_dirty = self._m_page_dirty, False
        super().uploads(batch)
        if not dirty or self.m_heights is None:
            return
        texels, (x, y) = self.page_region()
        raw = np.ascontiguousarray(texels).tobytes()
        if self.m_page is None:
            self.m_page = terraindraw.upload(
                self.m_rhi, batch, Format.RGBA8, (texels.shape[1], texels.shape[0]), [raw], False
            )
            self.m_textures[self.PAGE] = self.m_page
            return
        part = QRhiTextureSubresourceUploadDescription(raw)
        part.setDestinationTopLeft(QPoint(x, y))
        part.setSourceSize(QSize(texels.shape[1], texels.shape[0]))
        batch.uploadTexture(self.m_page, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, part)))

    def casters(self, models: list) -> list:
        return []

    def release(self) -> None:
        super().release()
        self.m_dummies.release(self.m_ctx)
        for part in (self._m_vertex_normals, self._m_page):
            part.spoil(None)
