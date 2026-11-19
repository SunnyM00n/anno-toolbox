"""Anno 117's ground baked by the game's own ``terrain_baking.fx``: per terrain node the island's materials laid down
from its material maps into the node's slice of the baked arrays (``psBakeSplatsBulk``), the normal and granularity
made from the baked height, the height stored as the engine stores it (``csCompressBC4``).

``Bake(ctx, stages, ground)`` is the entry: ``arrays`` are the five arrays ``terrain.fx`` reads by name, ``run(ctx,
jobs)`` bakes up to ``PER_FRAME`` nodes, ``paint`` writes changed material-map words; ``GroundSource`` is what the
island gives it. Formulas: docs/rendering/terrain-materials.md 1 and 6; ours: docs/rendering/island-117-view.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureCopyDescription,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from . import gamedraw, gamemaps, terraindraw
from . import gameshader as gs
from .gamecompute import ComputeChain, ComputeStage, carry, groups
from .gameinstances import Common
from .nativepass import draw_models
from .renderstates import PassState
from .terrainnodes import Node

EFFECT = "data/shaders/fx/terrain_baking.fx"
# a function's first technique holds its call (the effect's later techniques reuse the shaders: fxeffect.shader_calls)
STAGES = {"vs": (EFFECT, "TerrainBakeSplat", "vsBaking"), "ps": (EFFECT, "TerrainBakeSplatsBulk", "psBakeSplatsBulk"),
          "normal": (EFFECT, "TerrainNormalFromHeight", "csGenerateNormalFromHeight"),
          "granularity": (EFFECT, "TerrainGranularityFromHeight", "csGenerateGranularityFromHeight"),
          "height": (EFFECT, "TerrainCompressHeight", "csCompressBC4")}  # fmt: skip
# ours: the baked normal's v component turned over, as the view's mirrored island turns the shader's bitangent
FLIP_ENTRY = "csFlipNormalV"
FLIP_HLSL = """Texture2D<float4> cSrc;
RWTexture2D<float4> cDst;
[numthreads(8, 8, 1)]
void csFlipNormalV(uint3 id : SV_DispatchThreadID)
{
    float4 n = cSrc.Load(int3(id.xy, 0));
    cDst[id.xy] = float4(n.r, 1.0 - n.g, n.b, n.a);
}
"""
SIZE = terraindraw.SLICE_SIZE  # a node's bake, texels a side (the engine's TERRAIN_BAKED_TEX_SIZE: 512) [ours]
GRANULARITY = 64  # a node's granularity map, texels a side [ours]
PER_FRAME = 4  # nodes baked a frame [ours]
SPLATS = 32  # cSplatsBuffer's elements: MAX_NUGGETS_TEXTURE_MATERIALS
DISPLACEMENT_ADD = 1  # TerrainBakeSplatFlags: TSF_DISPLACEMENT_ADD
BAKE_STATE = PassState(depth_test=False, depth_write=False)  # sbTerrainSplatsBulkBaking
F = QRhiTexture.Format
# the three maps of a material in cTrnBakeSplatTextures (id * 3 + k) and what stands in for a missing one: grey with
# a full blend mask, the middle height, no gloss
MAP_KINDS = ("Diffuse", "Height", "Spec")
MISSING = ((128, 128, 128, 255), (128, 128, 128, 255), (0, 0, 0, 255))


@dataclass(frozen=True)
class Splat:
    """One material of the set as the bake reads it (``TerrainBakeSplat``) and its three maps (DDS files or None)."""

    mat_id: int
    repetition: float = 4.0
    sharpness: float = 1.0
    floor: float = 0.0
    ceil: float = 0.0
    add: bool = False
    maps: tuple = (None, None, None)


@dataclass
class GroundSource:
    """The island's ground: its material set's materials in the set's list order (the order the bake lays them
    down), its material maps (``ids``: a u32 word per id block, ``alphas``: per alpha texel; six 5-bit fields each),
    its size in tiles; ``bits`` the materials with an alpha per tile (the engine's ``UsedMaterialBits``); ``reread``
    reads the maps again (from the island's file; None: they cannot be, and are kept)."""

    splats: list
    ids: np.ndarray
    alphas: np.ndarray
    size: float
    bits: np.ndarray = field(default=None)
    reread: Callable | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.bits is None:
            self.bits = used_bits(self.ids, self.alphas, max(1, round(self.alphas.shape[1] / self.size)))

    def release_maps(self) -> None:
        """The maps let go once they are on the device, where they can be read again: their shapes stay (``block``,
        ``texel``), their words come back at the first paint stroke (``desktopgamebake.PaintedMaps``)."""
        if self.reread is not None:
            self.ids, self.alphas = (np.broadcast_to(np.uint32(0), a.shape) for a in (self.ids, self.alphas))

    def has_maps(self) -> bool:
        """Whether ``ids`` and ``alphas`` hold the maps' words (not let go by ``release_maps``)."""
        return self.ids.strides != (0, 0)

    @property
    def block(self) -> int:
        """Alpha texels per id word a side (``MaterialIDBlockSize``)."""
        return max(1, self.alphas.shape[1] // max(1, self.ids.shape[1]))

    @property
    def texel(self) -> float:
        """An alpha texel's width in tiles (world units: ``cTrnBakeMatTexelSizeWS``) [I]."""
        return self.size / self.alphas.shape[1]


def used_bits(ids: np.ndarray, alphas: np.ndarray, per: int) -> np.ndarray:
    """Per cell of ``per`` x ``per`` alpha texels the materials that have an alpha there, a bit each (1 << id)."""
    block = max(1, alphas.shape[1] // max(1, ids.shape[1]))
    up = np.repeat(np.repeat(ids, block, 0), block, 1)[: alphas.shape[0], : alphas.shape[1]]
    bits = np.zeros(alphas.shape, np.uint32)
    for s in range(6):
        here = ((alphas >> np.uint32(5 * s)) & np.uint32(31)) > 0
        bits |= np.where(here, np.uint32(1) << ((up >> np.uint32(5 * s)) & np.uint32(31)), np.uint32(0))
    n = -(-alphas.shape[0] // per)
    bits = np.pad(bits, ((0, n * per - bits.shape[0]), (0, n * per - bits.shape[1])))
    return np.bitwise_or.reduce(np.bitwise_or.reduce(bits.reshape(n, per, n, per), axis=3), axis=1)


def node_splats(ground: GroundSource, node: Node) -> list[Splat]:
    """The splats a node needs, in the set's order: the materials with an alpha over its square and one tile round
    it (the bicubic coverage reaches a texel beyond) [ours: the engine culls by its used-material bits]."""
    cells = ground.bits
    per = ground.size / cells.shape[1]
    i0, j0 = max(0, int(node.x / per) - 1), max(0, int(node.z / per) - 1)
    i1, j1 = int((node.x + node.tiles) / per) + 2, int((node.z + node.tiles) / per) + 2
    mask = int(np.bitwise_or.reduce(cells[j0:j1, i0:i1], axis=None)) if cells[j0:j1, i0:i1].size else 0
    return [s for s in ground.splats if mask >> s.mat_id & 1][:SPLATS]


def splat_values(splats: list[Splat]) -> dict:
    """``cSplatsBuffer`` and ``cTrnBakeNumSplats`` by member path."""
    out = {"cTrnBakeNumSplats": len(splats)}
    for k, s in enumerate(splats):
        row = {"MatID": s.mat_id, "Repetition": s.repetition, "AlphaSharpness": s.sharpness,
               "DisplacementFloor": s.floor, "DisplacementCeil": s.ceil, "Flags": DISPLACEMENT_ADD if s.add else 0}  # fmt: skip
        out.update({f"cSplatsBuffer[{k}].{name}": value for name, value in row.items()})
    return out


def bake_values(node: Node, ground: GroundSource, island: dict, page=None, size: int = SIZE,
                side: float | None = None) -> dict:  # fmt: skip
    """``cbTerrainBaking`` (and the island's ``cbPerFrame`` part) for a node: its bake's texel, the material maps'
    texel and block, the island's place (``island``: ``terraindraw.island_values``) and its inverse, the node's page
    (``page``: ``cTrnHeightPageParams``; None: no page, the vertices' own heights and normals); ``side``: the tiles
    its slice spans (None: its square; a trimmed stored mesh's longer side, the shader's texel is one number [I])."""
    m0 = np.array(island["cTrnIslandMatrix0"], float)
    m1 = np.array(island["cTrnIslandMatrix1"], float)
    rot = np.linalg.inv(np.array([m0[:2], m1[:2]]))
    move = -rot @ np.array([m0[2], m1[2]])
    return {**island, "cTrnIslandSize": [ground.size, ground.size], "cTrnBakeMatTexelSizeWS": ground.texel,
            "cTrnMatIDBlockSize": ground.block, "cTrnBakedTexelSizeWS": (side or node.tiles) / size,
            "cTrnIslandMatrixInv0": [rot[0, 0], rot[0, 1], move[0], 0.0],
            "cTrnIslandMatrixInv1": [rot[1, 0], rot[1, 1], move[1], 0.0],
            "cTrnCompressTexelSize": [1.0 / size, 1.0 / size], "cTrnBakeIslandBorderFixEnabled": 0,
            "cTrnBakeUsedMatBitsTexelSizeWS": 0.0, "cTrnBakeTessellationEnabled": 1 if page is not None else 0,
            "cTrnHeightPageParams": list(page) if page is not None else [0.0, 0.0, 1.0], "cTrnNodeL": node.level,
            "cTrnNodeX": int(node.x), "cTrnNodeZ": int(node.z), **splat_values(node_splats(ground, node))}  # fmt: skip


def bake_mesh(heights: np.ndarray, step: float, node: Node, width: int, normals: np.ndarray):
    """A node's height samples as the bake draws them: the terrain's vertices, two triangles a cell (the rasterizer
    lays each at its uv: ``vsBaking``)."""
    _col, _row, cols, rows = node.samples(step, width)
    data = terraindraw.encode_vertices(heights, step, node, width, normals)
    indices = terraindraw.grid_indices(cols, rows)
    return gamedraw.Mesh(data, terraindraw.STRIDE, terraindraw.ELEMENTS, indices, cols * rows)


def _texture(rhi, fmt, size: tuple[int, int], layers: int = 0, storage: bool = False):
    """A texture (an array of ``layers`` when given); ``storage``: written by a compute pass and copied from."""
    flags = (
        QRhiTexture.Flag.UsedWithLoadStore | QRhiTexture.Flag.UsedAsTransferSource if storage else QRhiTexture.Flag(0)
    )
    tex = (
        rhi.newTextureArray(fmt, layers, QSize(*size), 1, flags)
        if layers
        else rhi.newTexture(fmt, QSize(*size), 1, flags)
    )
    tex.create()
    return tex


def material_map_textures(rhi, batch, ground: GroundSource) -> dict:
    """The ground's material maps on the device by the bake shaders' names (R32_UINT: the shader's ``R16G16_UINT``
    read reads them alike); the maps themselves let go where the island's file gives them back (0.3 GB on Egypt's
    quadrant: ``GroundSource.release_maps``)."""
    out = {}
    for name, words in (("cMaterialIDTex", ground.ids), ("cMaterialAlphaTex", ground.alphas)):
        out[name] = _texture(rhi, F.R32UI, (words.shape[1], words.shape[0]))
        gamedraw.upload_level(batch, out[name], np.ascontiguousarray(words, "<u4").tobytes())
    ground.release_maps()
    return out


class MissingMap:
    """A map a material lacks, as ``gamedraw.TextureTable`` reads a map: 4 x 4 of its kind's stand-in."""

    def __init__(self, kind: int) -> None:
        self.info = SimpleNamespace(format="RGBA8", faces=1, width=4, height=4, mips=1, srgb=False)
        self.pixels = np.tile(np.array(MISSING[kind], np.uint8), (4, 4, 1))

    def level(self, mip: int = 0) -> np.ndarray:
        return self.pixels


def splat_table(splats: list[Splat]) -> tuple[gamedraw.TextureTable, np.ndarray]:
    """The splats' maps as the bindless stand-in holds them (``cTrnBakeSplatTextures`` read through it: the maps
    stored as they are, BC blocks with their mips; the shader turns sRGB to linear itself) and the slot table that
    puts each at its index ``id * 3 + k``."""
    maps = {f"{s.mat_id * 3 + k}": s.maps[k] if s.maps[k] is not None else MissingMap(k)
            for s in splats for k in range(3)}  # fmt: skip
    table = gamedraw.TextureTable(maps)
    words = np.zeros(gs.SLOTS, np.uint32)
    for name, slot in table.slots.items():
        words[int(name)] = table.words[slot]
    return table, words


class _Slot:
    """One node's bake a frame: the three targets, the working textures of the compute steps and their stages (a
    stage's constants are what was written last in a frame)."""

    def __init__(self, ctx, stages: dict, first: _Slot | None, size: int = SIZE) -> None:
        rhi = ctx.rhi
        self.targets = [ctx.native_texture((size, size), f) for f in (F.RGBA16F, F.RGBA16F, F.RGBA8)]
        self.fbo = ctx.framebuffer(self.targets)
        self.normal = _texture(rhi, F.RGBA8, (size, size), storage=True)
        self.flipped = _texture(rhi, F.RGBA8, (size, size), storage=True)
        self.granularity = _texture(rhi, F.R8, (GRANULARITY, GRANULARITY), storage=True)
        self.blocks = _texture(rhi, F.RG32UI, (size // 4, size // 4), storage=True)
        names = ("normal", "granularity", "height", "flip")
        share = first.stages if first is not None else dict.fromkeys(names)
        self.stages = {n: ComputeStage(rhi, stages[n], share[n]) for n in names if n in stages}

    def release(self) -> None:
        for s in sorted(self.stages.values(), key=lambda s: s.share is None):
            s.release()
        for t in (self.normal, self.flipped, self.granularity, self.blocks):
            t.destroy()
        self.fbo.release()
        for t in self.targets:
            t.release()


class GroundPaint:
    """What every way of baking does with a paint stroke (``Bake``, ``bakenative.NativeBake``): its material-map
    words into ``ground``'s maps and onto the list (``_painted``) the next run writes to the device."""

    ground: GroundSource
    _painted: list

    def paint(self, x0: int, z0: int, ids: np.ndarray | None, alphas: np.ndarray | None) -> None:
        """New material-map words from (``x0``, ``z0``) on (alpha texels; the ids at that over the block), written
        with the next run (the nodes over them bake again: the owner's)."""
        b = self.ground.block
        if ids is not None:
            self.ground.ids[z0 // b : z0 // b + ids.shape[0], x0 // b : x0 // b + ids.shape[1]] = ids
        if alphas is not None:
            self.ground.alphas[z0 : z0 + alphas.shape[0], x0 : x0 + alphas.shape[1]] = alphas
        self._painted.append((x0, z0, ids, alphas))


class Bake(GroundPaint, ComputeChain):
    """The game's bake of the island's ground (module docstring). ``stages``: the compiled shaders by the keys of
    ``STAGES`` and ``"flip"``; ``ground``: a ``GroundSource``; ``max_texture``: the quality's largest side of a
    splat's maps (0: as stored), as 1800's bake takes it."""

    max_texture = 0

    def __init__(self, ctx, stages: dict, ground: GroundSource, slices: int = terraindraw.SLICES,
                 size: int = SIZE, alike=(), max_texture: int = 0) -> None:  # fmt: skip
        rhi = self.rhi = ctx.rhi
        self.max_texture = max_texture
        self.program = gamedraw.Program(stages["vs"], stages["ps"])
        self.ground = ground
        self.slots: list[_Slot] = []
        for _ in range(PER_FRAME):
            self.slots.append(_Slot(ctx, stages, self.slots[0] if self.slots else None, size))
        self.arrays = {"cBakedDiffTexArray": _texture(rhi, F.RGBA16F, (size, size), slices),
                       "cBakedNormalTexArray": _texture(rhi, F.RGBA8, (size, size), slices),
                       "cBakedSpecTexArray": _texture(rhi, F.RGBA8, (size, size), slices),
                       "cBakedHeightTexArray": _texture(rhi, F.BC4, (size, size), slices),
                       "cBakedGranularityTexArray": _texture(rhi, F.R8, (GRANULARITY, GRANULARITY), slices)}  # fmt: skip
        self.textures: dict = {}  # the material maps, made with the first run
        self.bindless: list = []  # the splat maps in the bindless stand-in's arrays, and its slot table
        self.words = np.zeros(gs.SLOTS, np.uint32)
        self.keep: list = []
        self._painted: list = []  # material-map words to write: (x0, z0, ids or None, alphas or None)
        self._done: list = []  # the last frame's bake models: released once that frame is through
        self.size = size  # a node's bake, texels a side (``render.quality``)
        self.common = Common(rhi, alike)  # the bake draws' buffers without node values (``alike``: the island's)
        self.camera = gs.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 100.0, (size, size))

    @property
    def stages(self) -> dict:
        return {(k, n): s for k, slot in enumerate(self.slots) for n, s in slot.stages.items()}

    def _ground_textures(self, batch) -> None:
        """The material maps (``material_map_textures``) and the splat maps."""
        rhi, g = self.rhi, self.ground
        self.textures = material_map_textures(rhi, batch, g)
        capped = [replace(sp, maps=tuple(gamemaps.smaller(m, self.max_texture) for m in sp.maps)) for sp in g.splats]
        table, self.words = splat_table(capped)
        self.bindless = table.create(rhi, batch)
        # on the device now: the maps' files let go (300 MB on a continental island; a new bake reads them again)
        g.splats = [replace(sp, maps=(None, None, None)) for sp in g.splats]
        self.keep += [*self.textures.values(), *self.bindless]

    def model(self, mesh, values: dict, own: dict) -> terraindraw.TerrainModel:
        """A node's draw into the bake (``bake_model``): its mesh, its ``bake_values``, its page's textures."""
        return bake_model(self.rhi, self.program, mesh, values, own, self.common)

    def _write_paint(self, batch) -> None:
        b = self.ground.block
        for x0, z0, ids, alphas in self._painted:
            for tex, words, at in ((self.textures["cMaterialIDTex"], ids, (x0 // b, z0 // b)),
                                   (self.textures["cMaterialAlphaTex"], alphas, (x0, z0))):  # fmt: skip
                if words is None:
                    continue
                sub = QRhiTextureSubresourceUploadDescription(np.ascontiguousarray(words, "<u4").tobytes())
                sub.setDestinationTopLeft(QPoint(*at))
                sub.setSourceSize(QSize(words.shape[1], words.shape[0]))
                batch.uploadTexture(tex, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, sub)))
        self._painted.clear()

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (model, layer), a ``terraindraw.TerrainModel`` of the bake per node),
        each its draw into a slot's targets, then a compute pass of the normal, the granularity and the height's
        blocks ending in the copies into the arrays at its layer."""
        for model in self._done:  # the frame that drew them is through: released after the frame in flight
            for r in model.keep:
                ctx.later(r)
        self._done = [m for m, _layer in jobs]
        first = not self.textures
        for slot, (model, layer) in zip(self.slots, jobs[:PER_FRAME], strict=False):

            def before(batch, first=first, model=model) -> None:
                if first:
                    self._ground_textures(batch)
                self._write_paint(batch)
                model.textures.update(self.textures)
                model.given_arrays = self.bindless
                model.node_values["cBindlessSlots"] = self.words.reshape(-1, 4)

            draw_models(ctx, slot.fbo, [model], lambda m, rp, sig, batch: m.prepare(rp, sig, batch, self.camera),
                        lambda m, cb: m.draw(cb, (self.size, self.size)), before=before, targets=3, depth=0.0)  # fmt: skip
            ctx.native_pass(lambda cb, batch, s=slot, m=model, k=layer: self._derive(cb, batch, s, m, k))
            first = False

    def _derive(self, cb, batch, slot: _Slot, model, layer: int) -> None:
        """The normal and granularity from the baked height, the height's BC4 blocks, then the copies."""
        albedo, height, gloss = (t.native for t in slot.targets)
        texel = model.node_values["cTrnBakedTexelSizeWS"]

        def fill(c) -> None:
            c.set("cTrnBakedTexelSizeWS", texel)
            c.set("cTrnCompressTexelSize", [1.0 / self.size, 1.0 / self.size])

        steps = [("normal", {"cHeightTexture": height, "cNormalFromHeightDstTex": slot.normal}, groups(self.size, 16)),
                 ("granularity", {"cHeightTexture": height, "cGranularityFromHeightDstTex": slot.granularity},
                  groups(GRANULARITY, 16)),
                 ("height", {"cCompressSrcTex": height, "cCompressDstTex": slot.blocks}, groups(self.size // 4, 16)),
                 ("flip", {"cSrc": slot.normal, "cDst": slot.flipped}, groups(self.size, 8))]  # fmt: skip
        normal = slot.flipped if "flip" in slot.stages else slot.normal
        for name, inputs, n in steps:
            stage = slot.stages.get(name)
            if stage is None:
                continue
            stage.prepare(batch, inputs, fill)
            cb.beginComputePass(batch)
            stage.dispatch(cb, (n, n, 1))
            cb.endComputePass()
            batch = self.rhi.nextResourceUpdateBatch()
        a = self.arrays
        copies = [(a["cBakedDiffTexArray"], albedo, self.size), (a["cBakedSpecTexArray"], gloss, self.size),
                  (a["cBakedNormalTexArray"], normal, self.size), (a["cBakedHeightTexArray"], slot.blocks, self.size // 4),
                  (a["cBakedGranularityTexArray"], slot.granularity, GRANULARITY)]  # fmt: skip
        for dst, src, n in copies:
            desc = QRhiTextureCopyDescription()
            desc.setDestinationLayer(layer)
            desc.setPixelSize(QSize(n, n))
            batch.copyTexture(dst, src, desc)
        carry(cb, batch)  # hands the copies on

    def release(self) -> None:
        for slot in self.slots:
            slot.release()
        for model in self._done:
            model.release()
        self.program.release()  # the pipelines of the models let go a frame after their bake (``run``)
        for t in (*self.keep, *self.arrays.values()):
            t.destroy()
        self.common.release()
        self.slots, self.keep, self._done, self.textures = [], [], [], {}


def bake_model(rhi, program, mesh, values: dict, page_textures: dict, common=None) -> terraindraw.TerrainModel:
    """A node's draw into the bake's targets: its vertices, its constants, the page's textures it reads when the bake
    takes its heights and normals from the page (``cHeightPageTex``, ``cNormalPageTex``, ``cTerrainHeightTex``)."""
    owner = terraindraw.Owner(rhi, common=common)
    return terraindraw.TerrainModel(program, mesh, values, owner, page_textures, state=BAKE_STATE)
