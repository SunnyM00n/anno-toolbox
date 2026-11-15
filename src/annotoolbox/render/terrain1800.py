"""Anno 1800's terrain drawn by its own terrain shader (``terrain.fx``, ``TerrainDefault``) on Direct3D 11, lit in
its own pixel shader (forward): 117's square nodes of the 16-byte terrain vertex (``terraindraw``), each a ``FxcDraw``
with its constants, the three-channel normal page, its tint page and the baked arrays; an unedited stored node from
its stored mesh. Also what 2205's terrain shares with it (the compile, the bake's gamma YCC, the node as a forward
model, the arrays an edit remakes in part; ``terrain2205``). ``compile_terrain`` compiles (in the background);
``Terrain1800(ctx, program, state, size, step, width).nodes(eye)`` gives the forward models.
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
from . import frameforward, gamedraw, gameshadow, pages1800, terraindraw, terrainpages
from . import gameshader as gs
from .fxcdraw import Dummies, FxcDraw
from .objects1800 import Binding, ObjectDraw, cascade_pairs, group
from .terrainnodes import Node

EFFECT = "data/shaders/fx/terrain.fx"
TECHNIQUE = "TerrainDefault"
SHADOW = "TerrainShadow"  # a node into the sun's shadow map (phase 4)
DETAILED, SHADOW_DETAILED = "TerrainDefaultDetailed", "TerrainShadowDetailed"  # a node tessellated from its page
# the normals-and-depth pre-pass 1800's ambient occlusion reads (its face normals: PREZ_NORMAL_RT_ENABLED)
PREPASS, PREPASS_DETAILED = "TerrainNormalsDepth", "TerrainNormalsDepthDetailed"
PREZ_NORMALS = ("PREZ_NORMAL_RT_ENABLED", "1")
TESSELLATED = True  # the near nodes tessellated from their pages where the device and the quality allow
# a patch's corners with u along z: the tessellator's clockwise triangles then face up under the engine's back-face
# culling, as the stored meshes' do (in 117's order they faced down: culled, island-1800-2205-view.md 10) [ours]
PATCH_CORNERS = [0, 3, 2, 1]
# a quarter of the shadow map a cascade: the shader's bias reads the reciprocal of its side (constants_shadow.fxh)
PARTITION_REC = 2.0
# a node's own buffers; the rest are the frame's, shared with every node and object (``objects1800.SharedBuffers``)
NODE_OWN = ("cbTerrainNode",)
CASTER_OWN = (*NODE_OWN, "cbShadowPartition")  # a cascade's draw: its cascade too (``Caster``)
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
F = QRhiTexture.Format


class TerrainSource(shaderforward.ForwardSource):
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
    source = TerrainSource(files, compiler, game)
    defines = (*DEFINES[game], PREZ_NORMALS) if technique in (PREPASS, PREPASS_DETAILED) else DEFINES[game]
    vs, ps = source.effect_pass(EFFECT, defines, technique)[:2]
    stages = {"vs": vs, "ps": ps}
    if patches:
        stages.update({s: source.call_stage(EFFECT, defines, technique, s) for s in ("hs", "ds")})
    state = source.technique_state(EFFECT, defines, technique)
    if state.depth_func == "equal" or not state.depth_write:
        state = replace(state, depth_func="less_equal", depth_write=True)
    return stages, state


def program(stages: dict) -> gamedraw.Program:
    """A ``compile_terrain`` stage set as one program (tessellated where it has a hull and domain shader)."""
    return gamedraw.Program(stages["vs"], stages["ps"], hs=stages.get("hs"), ds=stages.get("ds"))


def ycc_decode(stored: np.ndarray) -> np.ndarray:
    """The linear RGB the games' pixel shader reads from stored (Cb', Y, Cr') bytes as floats 0..1 (the shader's
    formulas: the check of ``ENCODE_FS``)."""
    s = np.asarray(stored, float)
    c = s[..., [0, 2]] * 2 - 1
    c = c * c * np.sign(c) * 0.5 + 0.5
    y, cb, cr = s[..., 1], c[..., 0] - 0.5, c[..., 1] - 0.5
    rgb = np.stack([y + 1.402 * cr, y - 0.344 * cb - 0.714 * cr, y + 1.772 * cb], -1)
    return np.abs(np.maximum(rgb, 0.0)) ** 2.2


def ycc_encode(linear: np.ndarray) -> np.ndarray:
    """``ENCODE_FS`` on the CPU: linear RGB 0..1 -> stored (Cb', Y, Cr') 0..1."""
    g = np.maximum(np.asarray(linear, float), 0.0) ** (1 / 2.2)
    r, gg, b = g[..., 0], g[..., 1], g[..., 2]
    y = 0.299 * r + 0.587 * gg + 0.114 * b
    cb = -0.169 * r - 0.331 * gg + 0.5 * b + 0.5
    cr = 0.5 * r - 0.419 * gg - 0.081 * b + 0.5
    root = lambda c: 0.5 + 0.5 * np.sign(2 * c - 1) * np.sqrt(np.abs(2 * c - 1))  # noqa: E731
    return np.stack([root(cb), y, root(cr)], -1)


def normal_page_xzy(heights: np.ndarray, step: float) -> np.ndarray:
    """117's normal page (``terraindraw.normal_page``: a texel per cell of the heights) as 1800 reads it: the
    normal's x, z and y as three unorm bytes and a fourth (``UnpackTerrainNodeNormalMap``: ``normalize(sample.xzy *
    2 - 1)``) [rows, cols, 4]."""
    xz_bytes = terraindraw.normal_page(heights, step)
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


def sample_span(lo: float, hi: float, step: float, count: int) -> tuple[int, int]:
    """The height samples (first, past the last) a box's side of tiles ``lo`` .. ``hi`` reaches."""
    return max(0, int(np.floor(lo / step))), min(count, int(np.ceil(hi / step)) + 1)


class Regional:
    """An array made from the heights by ``make(heights, step)`` - per sample (``cells`` False: a value reads its
    neighbours, as central differences) or per cell (True: a cell reads its four corners) - kept between edits and
    made again only over the samples an edit reached: a continental island's whole page took half a second, on the
    window's thread at every stroke. ``region`` is what the last ``get`` changed ((rows, cols) of values; None: all)."""

    def __init__(self, make, cells: bool) -> None:
        self.make, self.cells = make, cells
        self.value: np.ndarray | None = None
        self.pending: list | None = None  # sample boxes (r0, r1, c0, c1) to remake; None: all
        self.region: tuple | None = None

    def spoil(self, samples: tuple[int, int, int, int] | None) -> None:
        """The samples (r0, r1, c0, c1) changed; None: all of them (new heights, another grid)."""
        if samples is None or self.pending is None:  # a whole remake asked for, or already due
            self.pending = None
            return
        box = self.pending[0] if self.pending else samples
        self.pending = [tuple(f(a, b) for f, a, b in zip((min, max, min, max), box, samples, strict=True))]

    def _span(self, a: int, b: int, count: int) -> tuple[int, int, int, int]:
        """(slice of samples to make from, values it changes) along one axis for changed samples a .. b."""
        if self.cells:  # cell j reads samples j and j + 1: the slice's cells are exactly those changed
            lo, hi = max(0, a - 1), min(count, b + 1)
            return lo, hi, lo, hi - 1
        lo, hi = max(0, a - 2), min(count, b + 2)  # a value reads one beyond: those beside the change change too
        return lo, hi, max(0, a - 1), min(count, b + 1)

    def get(self, heights: np.ndarray, step: float) -> np.ndarray:
        """The array for ``heights``: made whole the first time and after ``spoil(None)``, else in the spoilt part."""
        if self.value is None or self.pending is None:
            self.value, self.region, self.pending = self.make(heights, step), None, []
            return self.value
        for r0, r1, c0, c1 in self.pending:
            sr0, sr1, vr0, vr1 = self._span(r0, r1, heights.shape[0])
            sc0, sc1, vc0, vc1 = self._span(c0, c1, heights.shape[1])
            part = self.make(heights[sr0:sr1, sc0:sc1], step)
            self.value[vr0:vr1, vc0:vc1] = part[vr0 - sr0 : vr1 - sr0, vc0 - sc0 : vc1 - sc0]
            self.region = ((vr0, vr1), (vc0, vc1))
        self.pending = []
        return self.value


class _InPartition:
    """A command buffer whose viewport is one cascade's part of the shadow map (``gameshadow.DIVISIONS``: its
    offset and size in the map's texture space, top down; QRhi's viewport counts from the bottom)."""

    def __init__(self, cb, division: tuple, size: tuple[int, int]) -> None:
        self._cb, self._rect = cb, gamedraw.share_rect(size, division)

    def setViewport(self, _whole) -> None:  # QRhiCommandBuffer's name
        self._cb.setViewport(QRhiViewport(*self._rect))

    def __getattr__(self, name: str):
        return getattr(self._cb, name)


class Caster:
    """A node drawn into the sun's shadow map by ``SHADOW`` (the frame's ``shadow`` of a model): 1800's shader takes
    its cascade from ``cCurrentCascade`` and the engine draws each into its part of the map (its models place
    themselves there by the instance), so a draw per cascade read (``instances``, set by the frame) in that part;
    2205's single map once. ``make()`` makes a draw, ``partition()`` is the side of one cascade's part of the map
    as a share of the map's (the shader's bias)."""

    def __init__(self, make, cascaded: bool, partition) -> None:
        self.make, self.cascaded, self.partition = make, cascaded, partition
        self.draws: list[FxcDraw] = []
        self.instances = 1
        self.reach: set[int] | None = None  # the cascades its box reaches into (``cull``); None: every one read
        self.box: np.ndarray | None = None  # its node's eight corners in the view's axes
        self._shown: list[int] = []

    def cull(self, matrices: list) -> None:
        """Drawn only into the cascades its node's box reaches into (``objects1800.cascade_pairs``)."""
        if self.box is None:
            return
        lo, hi = self.box.min(0), self.box.max(0)
        found = cascade_pairs(((lo + hi) / 2)[None], np.array([np.linalg.norm(hi - lo) / 2]), matrices)
        self.reach = {int(i) for i in found[:, 1]}

    def prepare(self, rp, signature, batch, textures: dict, fill) -> None:
        n = max(1, self.instances) if self.cascaded else 1
        self._shown = [i for i in range(n) if not self.cascaded or self.reach is None or i in self.reach]
        while len(self.draws) < n:
            self.draws.append(self.make())
        rec = self.partition()
        for i in self._shown:
            draw = self.draws[i]

            def own(c: dict, i=i) -> None:
                fill(c)
                gs.set_values(c.values(), {"cCurrentCascade": i, "cShadowPartitionRecSize": [rec, rec]})

            draw.prepare(rp, signature, batch, textures, own)

    def draw(self, cb, size) -> None:
        if not self.cascaded:
            self.draws[0].draw(cb, size)
            return
        for i in self._shown:
            self.draws[i].draw(_InPartition(cb, gameshadow.DIVISIONS[i], size), size)

    def release(self) -> None:
        for draw in self.draws:
            draw.release()
        self.draws = []


class TerrainNode:
    """One node as the forward frame draws it (``frameforward.ForwardModel``'s shape): its ``FxcDraw``, its
    constants (``values``), the terrain's textures by name (``shared``) and its own (``own``), its ``Caster``."""

    def __init__(self, draw: FxcDraw, values: dict, shared: dict, own: dict | None = None) -> None:
        self.draw_call, self.values, self.shared, self.own = draw, values, shared, own or {}
        self.box: np.ndarray | None = None  # its eight corners in the view's axes (``corners``)
        self.world = np.identity(4)
        self.shadow: Caster | None = None
        self.prepass: FxcDraw | None = None  # its normals and depth for 1800's occlusion (the frame's pre-pass)
        self.blended = False
        self.later: list = []
        self.node: Node | None = None
        self.kind: tuple = ("grid", None, 0)

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, maps: dict | None = None,
                draw: FxcDraw | None = None) -> None:  # fmt: skip
        """This frame's constants for the colour pass, or ``draw`` (the frame's pre-pass)."""

        def fill(c: dict) -> None:  # one group of buffers at a time (``objects1800.ObjectDraw``)
            if group(next(iter(c)), NODE_OWN) == "frame":
                frameforward.fill_frame(c, cam, light)
            self.fill_model(c)

        (draw or self.draw_call).prepare(rp, signature, batch, {**(maps or {}), **self.textures()}, fill)

    def textures(self) -> dict:
        return {**self.shared, **self.own}

    def fill_model(self, c: dict) -> None:
        """The node's constants (its shadow pass's too)."""
        gs.set_values(c.values(), self.values)

    fill_caster = fill_model  # 1800's cascades: the cascade is the caster's (``Caster``)

    def draw(self, cb, size) -> None:
        self.draw_call.draw(cb, size)

    def corners(self) -> np.ndarray:
        """The node's box in the view's axes (the shadow's fit): the vertex holds packed nuggets, no position the
        mesh's own box could read."""
        return self.box

    def release(self) -> None:
        self.draw_call.release()
        for part in (self.shadow, self.prepass):
            if part is not None:
                part.release()


class Terrain1800(terraindraw.Terrain):
    """117's node set (``terraindraw.Terrain``: the nodes, the slices, the stored meshes' choice, the tint pages, the
    heights' changes) with each node a forward model of 1800's shader, its normal page in three channels; the vertex
    normals and the page made again only where an edit reached (``Regional``)."""

    PAGE = "cNodeNormalMap"  # the texture the normal page goes into
    ELEMENTS = terraindraw.ELEMENTS  # the vertex's three inputs as the shader names them (COLOR0..2)
    CASCADED = True  # the sun's shadow in cascades (2205: one map)

    def __init__(self, ctx, program: gamedraw.Program, state, size: float, step: float, width: int,
                 detailed: gamedraw.Program | None = None) -> None:  # fmt: skip
        super().__init__(ctx.rhi, program, size, step, width, detailed)
        self.ctx, self.state = ctx, state
        self.casting: tuple | None = None  # (program, state) of ``SHADOW``: the nodes cast (phase 4); None: not
        self.casting_detailed: tuple | None = None  # the same of ``SHADOW_DETAILED`` for the tessellated nodes
        self.prez: tuple | None = None  # (program, state) of ``PREPASS``: the nodes in the pre-pass; None: not
        self.prez_detailed: tuple | None = None  # the same of ``PREPASS_DETAILED``
        self.partition = PARTITION_REC  # the reciprocal of a cascade's side as a share of the map's (``Caster``)
        self.dummies = Dummies(ctx.rhi)
        self.shared: tuple | None = None  # the frame's buffers of the colour pass and the casters' (the owner's)
        self.island = {**terraindraw.island_values(size), "cTrnRenderParams": RENDER_PARAMS,
                       "cTerrainSize": [size, size, 1 / size, 1 / size]}  # fmt: skip
        self._vertex_normals = Regional(terraindraw.vertex_normals, cells=False)
        self._page = Regional(normal_page_xzy, cells=True)

    def set_heights(self, heights: np.ndarray, box=None) -> None:
        """117's rule (the nodes ``box`` touches made again), the normals and the page remade where it reaches."""
        same = self.heights is not None and box is not None and heights.shape == self.heights.shape
        samples = None
        if same:
            rows = sample_span(box[1], box[3], self.step, heights.shape[0])
            cols = sample_span(box[0], box[2], self.step, heights.shape[1])
            samples = (*rows, *cols)
        for part in (self._vertex_normals, self._page):
            part.spoil(samples)
        super().set_heights(heights, box)

    def normals(self) -> np.ndarray:
        return self._vertex_normals.get(self.heights, self.step)

    def _values(self, node: Node, layer: int, kind: tuple, tint) -> dict:
        page = kind[1]
        params = terrainpages.page_params(page, self.size) if page is not None else (0.0, 0.0, 1.0)
        return {**self.island, **node_values(node.level, layer, tint, params)}

    def _page_textures(self, kind: tuple) -> dict:
        """A node's rebuilt page (``pages1800``): its heights, its normals, its granularity, the session's heights;
        none without."""
        if kind[1] is None or self.pages is None or kind[1] not in self.pages.ready:
            return {}
        height, normal = self.pages.ready[kind[1]]
        own = {"cNodeHeightMap": height, "cNodeNormalMap": normal, "cTerrainHeightTex": self.pages.coarse}
        granularity = self.pages.granularity.get(kind[1])
        return {**own, "cNodeGranularityMap": granularity} if granularity is not None else own

    def _own(self, node: Node, layer: int, kind: tuple) -> dict:
        """A node's own textures beyond the terrain's and its tint page (none: 1800's bake is the arrays' slice)."""
        return {}

    def remake_nodes(self) -> None:
        """Every node made again with its slice and bake kept (the island's files came in: the tint pages)."""
        for model in self.models.values():
            model.release()
        self.models = {}

    def _mesh(self, node: Node, kind: tuple) -> gamedraw.Mesh:
        """A node's vertices: as the island stores them ("stored"), else from the heights with a skirt that covers
        the steps to the stored nodes beside it (117's rule)."""
        if kind[0] == "stored":
            tiles = 2**node.level
            vertices, indices = self.source.meshes[node.level][(int(node.x // tiles), int(node.z // tiles))]
            return gamedraw.Mesh(vertices, terraindraw.STRIDE, self.ELEMENTS, indices,
                                 len(vertices) // terraindraw.STRIDE)  # fmt: skip
        _col, _row, cols, rows = node.samples(self.step, self.width)
        data = terraindraw.encode_vertices(self.heights, self.step, node, self.width, self.normals())
        if kind[0] == "detailed":  # quad patches, the skirt hung by the domain shader below the page's surface
            data, patches = terraindraw.with_skirts(data, cols, rows)
            patches = patches.reshape(-1, 4)[:, PATCH_CORNERS].ravel()
            return gamedraw.Mesh(data, terraindraw.STRIDE, self.ELEMENTS, patches, len(data) // terraindraw.STRIDE)
        data, quads = terraindraw.with_skirts(data, cols, rows, terraindraw.GRID_SKIRT)
        return gamedraw.Mesh(data, terraindraw.STRIDE, self.ELEMENTS, terraindraw.quad_triangles(quads),
                             len(data) // terraindraw.STRIDE)  # fmt: skip

    def _make(self, node: Node, layer: int, kind: tuple) -> TerrainNode:
        tint = self._tint(node)
        own = {"cNodeTintMap": tint[0]} if tint is not None else {}
        detailed = kind[0] == "detailed"
        draw = self._draw(self.detailed if detailed else self.program, self._mesh(node, kind), self.state, 0)
        draw.mirrored = True  # the view's axes turn x over (1800's island matrix, 2205's mirrored patch)
        draw.fallbacks = dict(FALLBACKS)
        values = self._values(node, layer, kind, tint[1] if tint is not None else None)
        own = {**own, **self._page_textures(kind), **self._own(node, layer, kind)}
        model = TerrainNode(draw, values, self.textures, own)
        model.node, model.kind, model.box = node, kind, self.node_box(node)
        prez = self.prez_detailed if detailed else self.prez
        if prez is not None:  # the frame hands it its own shared buffers (``ForwardFrame._occlusion``)
            model.prepass = self._draw(prez[0], draw.mesh, prez[1], 0)
            model.prepass.mirrored, model.prepass.fallbacks = True, dict(FALLBACKS)
        casting = self.casting_detailed if detailed else self.casting
        if casting is not None:
            make = lambda: self._caster_draw(draw.mesh, casting)  # noqa: E731
            model.shadow = Caster(make, self.CASCADED, lambda: self.partition)
            model.shadow.box = model.box
        return model

    def node_box(self, node: Node) -> np.ndarray:
        """A node's eight corners in the view's axes (x turned over): its heights' range, the skirt below."""
        col, row, cols, rows = node.samples(self.step, self.width)
        h = self.heights[row : row + rows, col : col + cols]
        lo, hi = float(h.min()) - terraindraw.GRID_SKIRT, float(h.max())
        x0, x1 = self.size - node.x - node.tiles, self.size - node.x
        return np.array([[x, y, z] for x in (x0, x1) for y in (lo, hi) for z in (node.z, node.z + node.tiles)])

    def _caster_draw(self, mesh: gamedraw.Mesh, casting: tuple) -> FxcDraw:
        """A node's shadow draw: 1800's cascades look along the light with x right and y up, which turns the
        picture over once more, so the view's mirror and theirs cancel (the viewer's rule for 1800's casters; drawn
        mirrored the terrain cast its back faces, the skirts' band: island-1800-2205-view.md 8); 2205's map keeps it."""
        program, state = casting
        draw = self._draw(program, mesh, state, 1)
        draw.mirrored, draw.fallbacks = not self.CASCADED, dict(FALLBACKS)
        return draw

    def _draw(self, program, mesh: gamedraw.Mesh, state, which: int) -> FxcDraw:
        """A node's draw (``which``: 0 the colour pass, 1 its cascades), binding the frame's buffers of that pass
        shared (``shared``) where the owner hands them."""
        if self.shared is None:
            return FxcDraw(self.ctx, program, mesh, state, self.dummies)
        own = (NODE_OWN, CASTER_OWN)[which]
        return ObjectDraw(self.ctx, program, mesh, state, self.dummies, Binding(self.shared[which], own))

    def page_region(self) -> tuple[np.ndarray, tuple[int, int]] | None:
        """The normal page's texels to upload and where they go (column, row): the whole page, or the part the last
        edit changed; None when nothing changed."""
        page = self._page.get(self.heights, self.step)
        if self._page.region is None:
            return page, (0, 0)
        (r0, r1), (c0, c1) = self._page.region
        return page[r0:r1, c0:c1], (c0, r0)

    def uploads(self, batch) -> None:
        """117's uploads (the stand-ins of what no bake hands, the tint pages) with this game's normal page in place
        of 117's, written into the same texture while its size stays (only the part an edit changed)."""
        dirty, self._page_dirty = self._page_dirty, False
        super().uploads(batch)
        if not dirty or self.heights is None:
            return
        texels, (x, y) = self.page_region()
        raw = np.ascontiguousarray(texels).tobytes()
        if self.page is None:
            self.page = terraindraw.upload(self.rhi, batch, F.RGBA8, (texels.shape[1], texels.shape[0]), [raw], False)
            self.textures[self.PAGE] = self.page
            return
        part = QRhiTextureSubresourceUploadDescription(raw)
        part.setDestinationTopLeft(QPoint(x, y))
        part.setSourceSize(QSize(texels.shape[1], texels.shape[0]))
        batch.uploadTexture(self.page, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, part)))

    def casters(self, models: list) -> list:
        return []

    def release(self) -> None:
        super().release()
        self.dummies.release(self.ctx)
        for part in (self._vertex_normals, self._page):
            part.spoil(None)
