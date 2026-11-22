"""Anno 117's ground baked by the game's own ``terrain_baking.fx``: per terrain node the island's materials laid down
from its material maps into the node's slice of the baked arrays (``psBakeSplatsBulk``), the normal and granularity
made from the baked height, the height stored as the engine stores it (``csCompressBC4``).

``CBake(ctx, stages, ground)`` is the entry: ``arrays`` are the five arrays ``terrain.fx`` reads by name, ``run(ctx,
jobs)`` bakes up to ``PER_FRAME`` nodes, ``paint`` writes changed material-map words; ``SGroundSource`` is what the
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
from .gamecompute import CComputeChain, CComputeStage, carry, groups
from .gameinstances import CCommon
from .nativepass import draw_models
from .renderstates import SPassState
from .terrainnodes import SNode

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
BAKE_STATE = SPassState(depth_test=False, depth_write=False)  # sbTerrainSplatsBulkBaking
TextureFormat = QRhiTexture.Format
# the three maps of a material in cTrnBakeSplatTextures (id * 3 + k) and what stands in for a missing one: grey with
# a full blend mask, the middle height, no gloss
MAP_KINDS = ("Diffuse", "Height", "Spec")
MISSING = ((128, 128, 128, 255), (128, 128, 128, 255), (0, 0, 0, 255))


@dataclass(frozen=True)
class SSplat:
    """One material of the set as the bake reads it (``TerrainBakeSplat``) and its three maps (DDS files or None)."""

    mat_id: int
    repetition: float = 4.0
    sharpness: float = 1.0
    floor: float = 0.0
    ceil: float = 0.0
    add: bool = False
    maps: tuple = (None, None, None)


@dataclass
class SGroundSource:
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
        ``texel``), their words come back at the first paint stroke (``desktopgamebake.CPaintedMaps``)."""
        if self.reread is not None:
            self.ids, self.alphas = (np.broadcast_to(np.uint32(0), array.shape) for array in (self.ids, self.alphas))

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
    expanded = np.repeat(np.repeat(ids, block, 0), block, 1)[: alphas.shape[0], : alphas.shape[1]]
    bits = np.zeros(alphas.shape, np.uint32)
    for splat_index in range(6):
        here = ((alphas >> np.uint32(5 * splat_index)) & np.uint32(31)) > 0
        bits |= np.where(here, np.uint32(1) << ((expanded >> np.uint32(5 * splat_index)) & np.uint32(31)), np.uint32(0))
    cells = -(-alphas.shape[0] // per)
    bits = np.pad(bits, ((0, cells * per - bits.shape[0]), (0, cells * per - bits.shape[1])))
    return np.bitwise_or.reduce(np.bitwise_or.reduce(bits.reshape(cells, per, cells, per), axis=3), axis=1)


def node_splats(ground: SGroundSource, node: SNode) -> list[SSplat]:
    """The splats a node needs, in the set's order: the materials with an alpha over its square and one tile round
    it (the bicubic coverage reaches a texel beyond) [ours: the engine culls by its used-material bits]."""
    cells = ground.bits
    per = ground.size / cells.shape[1]
    min_i, min_j = max(0, int(node.x / per) - 1), max(0, int(node.z / per) - 1)
    max_i, max_j = int((node.x + node.tiles) / per) + 2, int((node.z + node.tiles) / per) + 2
    mask = (
        int(np.bitwise_or.reduce(cells[min_j:max_j, min_i:max_i], axis=None))
        if cells[min_j:max_j, min_i:max_i].size
        else 0
    )
    return [splat for splat in ground.splats if mask >> splat.mat_id & 1][:SPLATS]


def splat_values(splats: list[SSplat]) -> dict:
    """``cSplatsBuffer`` and ``cTrnBakeNumSplats`` by member path."""
    out = {"cTrnBakeNumSplats": len(splats)}
    for k, splat in enumerate(splats):
        row = {"MatID": splat.mat_id, "Repetition": splat.repetition, "AlphaSharpness": splat.sharpness,
               "DisplacementFloor": splat.floor, "DisplacementCeil": splat.ceil, "Flags": DISPLACEMENT_ADD if splat.add else 0}  # fmt: skip
        out.update({f"cSplatsBuffer[{k}].{name}": value for name, value in row.items()})
    return out


def bake_values(node: SNode, ground: SGroundSource, island: dict, page=None, size_texels: int = SIZE,
                side: float | None = None) -> dict:  # fmt: skip
    """``cbTerrainBaking`` (and the island's ``cbPerFrame`` part) for a node: its bake's texel, the material maps'
    texel and block, the island's place (``island``: ``terraindraw.island_values``) and its inverse, the node's page
    (``page``: ``cTrnHeightPageParams``; None: no page, the vertices' own heights and normals); ``side``: the tiles
    its slice spans (None: its square; a trimmed stored mesh's longer side, the shader's texel is one number [I])."""
    matrix_0 = np.array(island["cTrnIslandMatrix0"], float)
    matrix_1 = np.array(island["cTrnIslandMatrix1"], float)
    rotation = np.linalg.inv(np.array([matrix_0[:2], matrix_1[:2]]))
    move = -rotation @ np.array([matrix_0[2], matrix_1[2]])
    return {**island, "cTrnIslandSize": [ground.size, ground.size], "cTrnBakeMatTexelSizeWS": ground.texel,
            "cTrnMatIDBlockSize": ground.block, "cTrnBakedTexelSizeWS": (side or node.tiles) / size_texels,
            "cTrnIslandMatrixInv0": [rotation[0, 0], rotation[0, 1], move[0], 0.0],
            "cTrnIslandMatrixInv1": [rotation[1, 0], rotation[1, 1], move[1], 0.0],
            "cTrnCompressTexelSize": [1.0 / size_texels, 1.0 / size_texels], "cTrnBakeIslandBorderFixEnabled": 0,
            "cTrnBakeUsedMatBitsTexelSizeWS": 0.0, "cTrnBakeTessellationEnabled": 1 if page is not None else 0,
            "cTrnHeightPageParams": list(page) if page is not None else [0.0, 0.0, 1.0], "cTrnNodeL": node.level,
            "cTrnNodeX": int(node.x), "cTrnNodeZ": int(node.z), **splat_values(node_splats(ground, node))}  # fmt: skip


def bake_mesh(heights: np.ndarray, step_tiles: float, node: SNode, width_samples: int, normals: np.ndarray):
    """A node's height samples as the bake draws them: the terrain's vertices, two triangles a cell (the rasterizer
    lays each at its uv: ``vsBaking``)."""
    _col, _row, cols, rows = node.samples(step_tiles, width_samples)
    data = terraindraw.encode_vertices(heights, step_tiles, node, width_samples, normals)
    indices = terraindraw.grid_indices(cols, rows)
    return gamedraw.CMesh(data, terraindraw.STRIDE, terraindraw.ELEMENTS, indices, cols * rows)


def _texture(rhi, texture_format, size_px: tuple[int, int], layers: int = 0, use_storage: bool = False):
    """A texture (an array of ``layers`` when given); ``use_storage``: written by a compute pass and copied from."""
    flags = (
        QRhiTexture.Flag.UsedWithLoadStore | QRhiTexture.Flag.UsedAsTransferSource
        if use_storage
        else QRhiTexture.Flag(0)
    )
    texture = (
        rhi.newTextureArray(texture_format, layers, QSize(*size_px), 1, flags)
        if layers
        else rhi.newTexture(texture_format, QSize(*size_px), 1, flags)
    )
    texture.create()
    return texture


def material_map_textures(rhi, batch, ground: SGroundSource) -> dict:
    """The ground's material maps on the device by the bake shaders' names (R32_UINT: the shader's ``R16G16_UINT``
    read reads them alike); the maps themselves let go where the island's file gives them back (0.3 GB on Egypt's
    quadrant: ``SGroundSource.release_maps``)."""
    out = {}
    for name, words in (("cMaterialIDTex", ground.ids), ("cMaterialAlphaTex", ground.alphas)):
        out[name] = _texture(rhi, TextureFormat.R32UI, (words.shape[1], words.shape[0]))
        gamedraw.upload_level(batch, out[name], np.ascontiguousarray(words, "<u4").tobytes())
    ground.release_maps()
    return out


class CMissingMap:
    """A map a material lacks, as ``gamedraw.CTextureTable`` reads a map: 4 x 4 of its kind's stand-in."""

    def __init__(self, kind: int) -> None:
        self.m_info = SimpleNamespace(format="RGBA8", faces=1, width_px=4, height_px=4, mips=1, srgb=False)
        self.m_pixels = np.tile(np.array(MISSING[kind], np.uint8), (4, 4, 1))

    def level(self, mip: int = 0) -> np.ndarray:
        return self.m_pixels


def splat_table(splats: list[SSplat]) -> tuple[gamedraw.CTextureTable, np.ndarray]:
    """The splats' maps as the bindless stand-in holds them (``cTrnBakeSplatTextures`` read through it: the maps
    stored as they are, BC blocks with their mips; the shader turns sRGB to linear itself) and the slot table that
    puts each at its index ``id * 3 + k``."""
    maps = {f"{splat.mat_id * 3 + k}": splat.maps[k] if splat.maps[k] is not None else CMissingMap(k)
            for splat in splats for k in range(3)}  # fmt: skip
    table = gamedraw.CTextureTable(maps)
    words = np.zeros(gs.SLOTS, np.uint32)
    for name, slot in table.m_slots.items():
        words[int(name)] = table.m_words[slot]
    return table, words


class _CSlot:
    """One node's bake a frame: the three targets, the working textures of the compute steps and their stages (a
    stage's constants are what was written last in a frame)."""

    def __init__(self, ctx, stages: dict, first: _CSlot | None, size_texels: int = SIZE) -> None:
        rhi = ctx.m_rhi
        self.m_targets = [
            ctx.native_texture((size_texels, size_texels), texture_format)
            for texture_format in (TextureFormat.RGBA16F, TextureFormat.RGBA16F, TextureFormat.RGBA8)
        ]
        self.m_fbo = ctx.framebuffer(self.m_targets)
        self.m_normal = _texture(rhi, TextureFormat.RGBA8, (size_texels, size_texels), use_storage=True)
        self.m_flipped = _texture(rhi, TextureFormat.RGBA8, (size_texels, size_texels), use_storage=True)
        self.m_granularity = _texture(rhi, TextureFormat.R8, (GRANULARITY, GRANULARITY), use_storage=True)
        self.m_blocks = _texture(rhi, TextureFormat.RG32UI, (size_texels // 4, size_texels // 4), use_storage=True)
        names = ("normal", "granularity", "height", "flip")
        share = first.m_stages if first is not None else dict.fromkeys(names)
        self.m_stages = {name: CComputeStage(rhi, stages[name], share[name]) for name in names if name in stages}

    def release(self) -> None:
        for stage in sorted(self.m_stages.values(), key=lambda stage: stage.m_share is None):
            stage.release()
        for texture in (self.m_normal, self.m_flipped, self.m_granularity, self.m_blocks):
            texture.destroy()
        self.m_fbo.release()
        for texture in self.m_targets:
            texture.release()


class CGroundPaint:
    """What every way of baking does with a paint stroke (``CBake``, ``bakenative.CNativeBake``): its material-map
    words into ``ground``'s maps and onto the list (``_painted``) the next run writes to the device."""

    m_ground: SGroundSource
    _m_painted: list

    def paint(self, min_x: int, min_z: int, ids: np.ndarray | None, alphas: np.ndarray | None) -> None:
        """New material-map words from (``x0``, ``z0``) on (alpha texels; the ids at that over the block), written
        with the next run (the nodes over them bake again: the owner's)."""
        block = self.m_ground.block
        if ids is not None:
            self.m_ground.ids[
                min_z // block : min_z // block + ids.shape[0], min_x // block : min_x // block + ids.shape[1]
            ] = ids
        if alphas is not None:
            self.m_ground.alphas[min_z : min_z + alphas.shape[0], min_x : min_x + alphas.shape[1]] = alphas
        self._m_painted.append((min_x, min_z, ids, alphas))


class CBake(CGroundPaint, CComputeChain):
    """The game's bake of the island's ground (module docstring). ``stages``: the compiled shaders by the keys of
    ``STAGES`` and ``"flip"``; ``ground``: a ``SGroundSource``; ``max_texture``: the quality's largest side of a
    splat's maps (0: as stored), as 1800's bake takes it."""

    m_max_texture = 0

    def __init__(self, ctx, stages: dict, ground: SGroundSource, slices: int = terraindraw.SLICES,
                 size_texels: int = SIZE, alike=(), max_texture: int = 0) -> None:  # fmt: skip
        rhi = self.m_rhi = ctx.m_rhi
        self.m_max_texture = max_texture
        self.m_program = gamedraw.CProgram(stages["vs"], stages["ps"])
        self.m_ground = ground
        self.m_slots: list[_CSlot] = []
        for _ in range(PER_FRAME):
            self.m_slots.append(_CSlot(ctx, stages, self.m_slots[0] if self.m_slots else None, size_texels))
        self.m_arrays = {"cBakedDiffTexArray": _texture(rhi, TextureFormat.RGBA16F, (size_texels, size_texels), slices),
                       "cBakedNormalTexArray": _texture(rhi, TextureFormat.RGBA8, (size_texels, size_texels), slices),
                       "cBakedSpecTexArray": _texture(rhi, TextureFormat.RGBA8, (size_texels, size_texels), slices),
                       "cBakedHeightTexArray": _texture(rhi, TextureFormat.BC4, (size_texels, size_texels), slices),
                       "cBakedGranularityTexArray": _texture(rhi, TextureFormat.R8, (GRANULARITY, GRANULARITY), slices)}  # fmt: skip
        self.m_textures: dict = {}  # the material maps, made with the first run
        self.m_bindless: list = []  # the splat maps in the bindless stand-in's arrays, and its slot table
        self.m_words = np.zeros(gs.SLOTS, np.uint32)
        self.m_keep: list = []
        self._m_painted: list = []  # material-map words to write: (x0, z0, ids or None, alphas or None)
        self._m_done: list = []  # the last frame's bake models: released once that frame is through
        self.m_size_texels = size_texels  # a node's bake, texels a side (``render.quality``)
        self.m_common = CCommon(rhi, alike)  # the bake draws' buffers without node values (``alike``: the island's)
        self.m_camera = gs.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 100.0, (size_texels, size_texels))

    @property
    def m_stages(self) -> dict:
        return {(k, name): stage for k, slot in enumerate(self.m_slots) for name, stage in slot.m_stages.items()}

    def _ground_textures(self, batch) -> None:
        """The material maps (``material_map_textures``) and the splat maps."""
        rhi, ground = self.m_rhi, self.m_ground
        self.m_textures = material_map_textures(rhi, batch, ground)
        capped = [
            replace(splat, maps=tuple(gamemaps.smaller(map_texture, self.m_max_texture) for map_texture in splat.maps))
            for splat in ground.splats
        ]
        table, self.m_words = splat_table(capped)
        self.m_bindless = table.create(rhi, batch)
        # on the device now: the maps' files let go (300 MB on a continental island; a new bake reads them again)
        ground.splats = [replace(splat, maps=(None, None, None)) for splat in ground.splats]
        self.m_keep += [*self.m_textures.values(), *self.m_bindless]

    def model(self, mesh, values: dict, own: dict) -> terraindraw.CTerrainModel:
        """A node's draw into the bake (``bake_model``): its mesh, its ``bake_values``, its page's textures."""
        return bake_model(self.m_rhi, self.m_program, mesh, values, own, self.m_common)

    def _write_paint(self, batch) -> None:
        block = self.m_ground.block
        for min_x, min_z, ids, alphas in self._m_painted:
            for texture, words, block_origin in ((self.m_textures["cMaterialIDTex"], ids, (min_x // block, min_z // block)),
                                   (self.m_textures["cMaterialAlphaTex"], alphas, (min_x, min_z))):  # fmt: skip
                if words is None:
                    continue
                sub = QRhiTextureSubresourceUploadDescription(np.ascontiguousarray(words, "<u4").tobytes())
                sub.setDestinationTopLeft(QPoint(*block_origin))
                sub.setSourceSize(QSize(words.shape[1], words.shape[0]))
                batch.uploadTexture(texture, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, sub)))
        self._m_painted.clear()

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (model, layer), a ``terraindraw.CTerrainModel`` of the bake per node),
        each its draw into a slot's targets, then a compute pass of the normal, the granularity and the height's
        blocks ending in the copies into the arrays at its layer."""
        for model in self._m_done:  # the frame that drew them is through: released after the frame in flight
            for kept in model.m_keep:
                ctx.later(kept)
        self._m_done = [baked for baked, _layer in jobs]
        first = not self.m_textures
        for slot, (model, layer) in zip(self.m_slots, jobs[:PER_FRAME], strict=False):

            def before(batch, first=first, model=model) -> None:
                if first:
                    self._ground_textures(batch)
                self._write_paint(batch)
                model.m_textures.update(self.m_textures)
                model.m_given_arrays = self.m_bindless
                model.m_node_values["cBindlessSlots"] = self.m_words.reshape(-1, 4)

            draw_models(ctx, slot.m_fbo, [model], lambda baked, render_pass, signature, batch: baked.prepare(render_pass, signature, batch, self.m_camera),
                        lambda baked, command_buffer: baked.draw(command_buffer, (self.m_size_texels, self.m_size_texels)), before=before, targets=3, depth=0.0,
                        defer=False)  # fmt: skip
            ctx.native_pass(
                lambda command_buffer, batch, s=slot, baked=model, k=layer: self._derive(
                    command_buffer, batch, s, baked, k
                )
            )
            first = False

    def _derive(self, command_buffer, batch, slot: _CSlot, model, layer: int) -> None:
        """The normal and granularity from the baked height, the height's BC4 blocks, then the copies."""
        albedo, height_texture, gloss = (target.native for target in slot.m_targets)
        texel = model.m_node_values["cTrnBakedTexelSizeWS"]

        def fill(constants) -> None:
            constants.set("cTrnBakedTexelSizeWS", texel)
            constants.set("cTrnCompressTexelSize", [1.0 / self.m_size_texels, 1.0 / self.m_size_texels])

        steps = [("normal", {"cHeightTexture": height_texture, "cNormalFromHeightDstTex": slot.m_normal}, groups(self.m_size_texels, 16)),
                 ("granularity", {"cHeightTexture": height_texture, "cGranularityFromHeightDstTex": slot.m_granularity},
                  groups(GRANULARITY, 16)),
                 ("height", {"cCompressSrcTex": height_texture, "cCompressDstTex": slot.m_blocks}, groups(self.m_size_texels // 4, 16)),
                 ("flip", {"cSrc": slot.m_normal, "cDst": slot.m_flipped}, groups(self.m_size_texels, 8))]  # fmt: skip
        normal = slot.m_flipped if "flip" in slot.m_stages else slot.m_normal
        for name, inputs, n in steps:
            stage = slot.m_stages.get(name)
            if stage is None:
                continue
            stage.prepare(batch, inputs, fill)
            command_buffer.beginComputePass(batch)
            stage.dispatch(command_buffer, (n, n, 1))
            command_buffer.endComputePass()
            batch = self.m_rhi.nextResourceUpdateBatch()
        arrays = self.m_arrays
        copies = [(arrays["cBakedDiffTexArray"], albedo, self.m_size_texels), (arrays["cBakedSpecTexArray"], gloss, self.m_size_texels),
                  (arrays["cBakedNormalTexArray"], normal, self.m_size_texels), (arrays["cBakedHeightTexArray"], slot.m_blocks, self.m_size_texels // 4),
                  (arrays["cBakedGranularityTexArray"], slot.m_granularity, GRANULARITY)]  # fmt: skip
        for dst, src, n in copies:
            desc = QRhiTextureCopyDescription()
            desc.setDestinationLayer(layer)
            desc.setPixelSize(QSize(n, n))
            batch.copyTexture(dst, src, desc)
        carry(command_buffer, batch)  # hands the copies on

    def release(self) -> None:
        for slot in self.m_slots:
            slot.release()
        for model in self._m_done:
            model.release()
        self.m_program.release()  # the pipelines of the models let go a frame after their bake (``run``)
        for texture in (*self.m_keep, *self.m_arrays.values()):
            texture.destroy()
        self.m_common.release()
        self.m_slots, self.m_keep, self._m_done, self.m_textures = [], [], [], {}


def bake_model(rhi, program, mesh, values: dict, page_textures: dict, common=None) -> terraindraw.CTerrainModel:
    """A node's draw into the bake's targets: its vertices, its constants, the page's textures it reads when the bake
    takes its heights and normals from the page (``cHeightPageTex``, ``cNormalPageTex``, ``cTerrainHeightTex``)."""
    owner = terraindraw.SOwner(rhi, common=common)
    return terraindraw.CTerrainModel(program, mesh, values, owner, page_textures, state=BAKE_STATE)
