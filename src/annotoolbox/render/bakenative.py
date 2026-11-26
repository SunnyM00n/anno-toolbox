"""Anno 117's ground baked by the game's own ``terrain_baking.fx`` on the native backend: the splats by the unmodified
``psBakeSplatsBulk`` (``cTrnBakeSplatTextures[96]`` a bindless range: one descriptor table), the normal, granularity
and BC4 height passes, on the module's own list into the module's arrays, which QRhi reads as its own (the decals).

``CNativeBake(device, natives, handover, ground, flip)`` is the entry, with ``terrainbake.CBake``'s interface
(``arrays``, ``model``, ``run(ctx, jobs)``, ``paint``, ``error``); the formulas and what is ours: ``terrainbake``.
docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gamemaps, gametextures, nativegpu, terraindraw
from . import gameshader as gs
from .gamecompute import groups
from .nativecompute import CKernel, CTold, records
from .terrainbake import GRANULARITY, PER_FRAME, SIZE, SPLATS, CGroundPaint, CMissingMap, SGroundSource

ENV = "ANNO_NATIVE_BAKE"  # "0": the bake by the current path (the native pages stay)
EFFECT = "terrain_baking"
# the bake's shaders by the entry and the literal arguments their techniques pass (TerrainBakeSplatsBulk,
# TerrainNormalFromHeight, TerrainGranularityFromHeight, TerrainCompressHeight)
CALLS = {"vs": ("vsBaking", ""), "ps": ("psBakeSplatsBulk", "false"),
         "normal": ("csGenerateNormalFromHeight", ""), "granularity": ("csGenerateGranularityFromHeight", ""),
         "height": ("csCompressBC4", "float3(1,0,0)true")}  # fmt: skip
BINDLESS = "cTrnBakeSplatTextures"
SLOTS = SPLATS * 3  # the range's maps: diffuse, height and gloss of each material id
TARGETS = ("RGBA16_FLOAT", "RGBA16_FLOAT", "RGBA8_UNORM")  # albedo, height, gloss (``terrainbake._CSlot``'s)
TextureFormat = QRhiTexture.Format
# the arrays ``terrain.fx`` reads: the module's format, QRhi's, and the target or working texture copied in
ARRAYS = {"cBakedDiffTexArray": ("RGBA16_FLOAT", TextureFormat.RGBA16F, "albedo"),
          "cBakedNormalTexArray": ("RGBA8_UNORM", TextureFormat.RGBA8, "normal"),
          "cBakedSpecTexArray": ("RGBA8_UNORM", TextureFormat.RGBA8, "gloss"),
          "cBakedHeightTexArray": ("BC4_UNORM", TextureFormat.BC4, "blocks"),
          "cBakedGranularityTexArray": ("R8_UNORM", TextureFormat.R8, "granularity")}  # fmt: skip
STATE = {"cull": "none", "depth_test": False, "depth_write": False, "colors": list(TARGETS), "depth": ""}
# a DDS file's stored format -> the module's (as stored: the shader turns sRGB to linear itself, as the current
# path's arrays keep a file's own sRGB flag)
BLOCKS = {"DXT1": ("BC1_UNORM", 8), "DXT3": ("BC2_UNORM", 16), "DXT5": ("BC3_UNORM", 16), "ATI1": ("BC4_UNORM", 8),
          "ATI2": ("BC5_UNORM", 16), "BC6H": ("BC6H_UFLOAT", 16), "BC7": ("BC7_UNORM", 16)}  # fmt: skip


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """The bake's shaders (``CALLS``) of the install's variant, each reflected (in the background)."""
    variant = natives.variant(EFFECT)
    out = {key: natives.call(variant, *call) for key, call in CALLS.items()}
    for shader in out.values():
        natives.reflection(shader)
    return out


@dataclass
class SBakeJob:
    """A node's draw into the bake: its mesh, its ``terrainbake.bake_values``, its page's QRhi textures by name."""

    mesh: object
    values: dict
    own: dict


class CStored:
    """A map as ``gametextures.CTextureTable`` holds it, from the file: the stored levels of a single-face BC map (with the
    chain's missing small levels), else its first level as RGBA8."""

    def __init__(self, dds) -> None:
        dds_info = dds.m_info
        found = BLOCKS.get(dds_info.format) if dds_info.faces == 1 else None
        self.m_width, self.m_height = dds_info.width_px, dds_info.height_px
        if found is None:
            self.m_format, self.m_block = "RGBA8_UNORM", 0
            self.m_levels = [np.ascontiguousarray(dds.level(0), np.uint8).tobytes()]
        else:
            self.m_format, self.m_block = found
            if dds_info.srgb and self.m_format.startswith(("BC1", "BC2", "BC3", "BC7")):
                self.m_format += "_SRGB"
            self.m_levels = gametextures.stored_levels(dds)

    def pitch(self, mip: int) -> int:
        mip_width = max(1, self.m_width >> mip)
        return (mip_width + 3) // 4 * self.m_block if self.m_block else mip_width * 4

    def upload(self, device, command_list, name: str):
        texture = device.texture(self.m_width, self.m_height, self.m_format, mips=len(self.m_levels), name=name)
        for mip, raw in enumerate(self.m_levels):
            command_list.write_texture(texture, raw, self.pitch(mip), mip=mip)
        return texture


class CNativeBake(CGroundPaint, CTold):
    """The game's bake on the native backend (module docstring). ``flip``: our own pass's compiled stage (``blob``,
    ``reflection``: ``terrainbake.FLIP_HLSL``), which turns the baked normal's v over for the mirrored view."""

    m_native = True  # its owner tells the ways apart by it

    def __init__(self, device, natives, handover, ground: SGroundSource, flip, slices: int = terraindraw.SLICES,
                 size_texels: int = SIZE, max_texture: int = 0) -> None:  # fmt: skip
        self.m_device, self.m_handover, self.m_ground = device, handover, ground
        self.m_size_texels, self.m_max_texture = size_texels, max_texture
        self.m_program = object()  # no model of the current path is this bake's (``CGameIsland._take_failures``)
        made = shaders(natives)
        self.m_draw = CKernel.graphics(device, {k: (made[k], natives.reflection(made[k])) for k in ("vs", "ps")}, STATE,
                                    (BINDLESS,))  # fmt: skip
        self.m_kernels = {k: CKernel.compute(device, made[k], natives.reflection(made[k]))
                        for k in ("normal", "granularity", "height")}  # fmt: skip
        self.m_kernels["flip"] = CKernel.compute(device, SFlipShader(flip), flip.reflection)
        element = next(constant_buffer for stage in ("ps", "vs") for constant_buffer in natives.reflection(made[stage]).cbuffers
                       if constant_buffer.name == "cSplatsBuffer")  # fmt: skip
        self.m_splat_records = records(element, SPLATS)
        self.m_list = device.command_list()
        self.m_pool = nativegpu.CBufferPool(device, "vertex,index", "bake meshes")  # a node's mesh for its one bake
        self.m_list.open()
        self.m_targets = {name: device.texture(size_texels, size_texels, texture_format, usage="target", name=f"bake {name}")
                        for name, texture_format in zip(("albedo", "height", "gloss"), TARGETS, strict=True)}  # fmt: skip
        self.m_framebuffer = device.framebuffer(list(self.m_targets.values()))
        self.m_work = {"normal": device.texture(size_texels, size_texels, "RGBA8_UNORM", usage="uav", name="bake normal"),
                     "flipped": device.texture(size_texels, size_texels, "RGBA8_UNORM", usage="uav", name="bake flipped"),
                     "granularity": device.texture(GRANULARITY, GRANULARITY, "R8_UNORM", usage="uav", name="bake grain"),
                     "blocks": device.texture(size_texels // 4, size_texels // 4, "RG32_UINT", usage="uav", name="bake blocks")}  # fmt: skip
        self.m_native_arrays = {}
        self.m_arrays = {}  # by the shader's name: QRhi's textures over the module's
        for name, (texture_format, qformat, _src) in ARRAYS.items():
            side = GRANULARITY if name == "cBakedGranularityTexArray" else size_texels
            texture = device.texture(side, side, texture_format, depth=slices, kind="2darray", name=name)
            self.m_native_arrays[name] = texture
            self.m_arrays[name] = handover.give(texture, qformat, (side, side), slices)
        self.m_white = device.texture(4, 4, "RGBA8_UNORM", name="white")
        self.m_list.write_texture(self.m_white, bytes([255]) * 64, 16)
        self.m_splats = device.buffer(
            self.m_splat_records.size, "structured", stride=element.size, name="cSplatsBuffer"
        )
        self.m_maps: dict = {}  # the material maps by the shader's names, made with the first run
        self.m_table = None  # the splat maps' descriptor table (``BINDLESS``) and the maps in it
        self.m_splat_maps: list = []
        self._m_painted: list = []
        self.m_camera = gs.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 100.0, (size_texels, size_texels))
        self._m_recorded = True  # the white map's upload
        self.m_error = ""
        self.m_messages, self.m_foreign = [], []

    def model(self, mesh, values: dict, own: dict) -> SBakeJob:
        return SBakeJob(mesh, values, own)

    def _ground_textures(self) -> None:
        """The material maps (R16G16_UINT, as the engine's: a u32 word each) and the splat maps in the range (a
        material of the set without a map: its kind's stand-in; an id outside the set: white, the current path's
        null map); the maps' files let go once on the device (a new bake reads them again)."""
        device, command_list, ground = self.m_device, self.m_list, self.m_ground
        for name, words in (("cMaterialIDTex", ground.ids), ("cMaterialAlphaTex", ground.alphas)):
            texture = self.m_maps[name] = device.texture(words.shape[1], words.shape[0], "RG16_UINT", name=name)
            command_list.write_texture(texture, np.ascontiguousarray(words, "<u4").tobytes(), words.shape[1] * 4)
        ground.release_maps()
        layout = self.m_draw.m_layouts[self.m_draw.m_plan.index(1, 0)]
        self.m_table = device.descriptor_table(layout, SLOTS)
        missing = [CStored(CMissingMap(k)).upload(device, command_list, f"missing {k}") for k in range(3)]
        by_slot = dict.fromkeys(range(SLOTS), self.m_white)
        for splat in ground.splats:
            for k in range(3):
                splat_map = gamemaps.smaller(splat.maps[k], self.m_max_texture)
                texture = (
                    CStored(splat_map).upload(device, command_list, f"splat {splat.mat_id}.{k}")
                    if splat_map is not None
                    else missing[k]
                )
                if splat.mat_id * 3 + k < SLOTS:
                    by_slot[splat.mat_id * 3 + k] = texture
        for slot, texture in by_slot.items():
            device.write_table(self.m_table, ("texture", slot, texture))
        self.m_splat_maps = [
            *missing,
            *{id(texture): texture for texture in by_slot.values() if texture is not self.m_white}.values(),
        ]
        for texture in self.m_splat_maps:  # a descriptor table's maps are tracked by no set: read in this list
            command_list.set_state(texture, "shader_resource")
        ground.splats = [replace(splat, maps=(None, None, None)) for splat in ground.splats]

    def _write_paint(self) -> None:
        """The painted words (``paint``) into the material maps, through a texture of each rectangle."""
        block, command_list = self.m_ground.block, self.m_list
        for x0, z0, ids, alphas in self._m_painted:
            for name, words, (x, z) in (("cMaterialIDTex", ids, (x0 // block, z0 // block)),
                                        ("cMaterialAlphaTex", alphas, (x0, z0))):  # fmt: skip
                if words is None or not words.size:
                    continue
                height_px, width_px = words.shape
                part = self.m_device.texture(width_px, height_px, "RG16_UINT", name="painted words")
                command_list.write_texture(part, np.ascontiguousarray(words, "<u4").tobytes(), width_px * 4)
                command_list.copy_region(self.m_maps[name], 0, 0, x, z, part, 0, 0, width_px, height_px)
                part.release()  # late: the list still reads it
            self._m_recorded = True
        self._m_painted.clear()

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (``SBakeJob``, layer)) on the module's list, sent to the queue at
        once (ahead of QRhi's frame, which reads the arrays); a failure is kept in ``error``."""
        if self.m_error:
            return
        try:
            self._told_before()
            if self.m_table is None:
                self._ground_textures()
                self._m_recorded = True
            self._write_paint()
            jobs = jobs[:PER_FRAME]
            if jobs:  # what QRhi read since: its states taken back
                pages = [page_texture for job, _layer in jobs for page_texture in job.own.values()]
                self.m_handover.take_back(self.m_list, [*self.m_arrays.values(), *pages])
            for job, layer in jobs:
                self._bake(job, layer)
            if self._m_recorded or jobs:
                self.m_list.execute()
                self.m_handover.handed_back()
                self._m_recorded = False
            self._told_after()
        except Exception as exc:  # said once by its owner, which bakes by the current path from then on
            self.m_error = f"{type(exc).__name__}: {exc}"

    def _bake(self, job: SBakeJob, layer: int) -> None:
        """One node: its splats into the targets, the normal, granularity and height's blocks made from the baked
        height, then each copied into the arrays at ``layer``."""
        command_list, values, mesh, pool = self.m_list, job.values, job.mesh, self.m_pool
        indices = np.ascontiguousarray(mesh.m_indices, "<u4")
        pieces = [pool.place(command_list, mesh.m_data), pool.place(command_list, indices.tobytes())]
        pipeline, fill = self.m_draw.pipeline_for(mesh.m_elements, mesh.m_stride)
        if fill:
            pieces.append(pool.place(command_list, fill))
        streams = [piece[:2] for piece in (pieces[0], *pieces[2:])]
        splats = gs.CConstants(self.m_splat_records)
        for name, value in values.items():
            if name.startswith("cSplatsBuffer["):
                splats.set(name, value)
        command_list.write_buffer(self.m_splats, bytes(splats.m_data))

        def own(constants) -> None:
            for name, value in values.items():
                constants.set(name, value)

        named = {**self.m_draw.write(command_list, own, self.m_camera), **self.m_maps, "cSplatsBuffer": self.m_splats,
                 BINDLESS: self.m_table}  # fmt: skip
        for name in ("cHeightPageTex", "cNormalPageTex", "cTerrainHeightTex", "cTerrainIslandBorderTex"):
            named[name] = self.m_handover.native(job.own.get(name)) or self.m_white
        for target in self.m_targets.values():
            command_list.clear_color(target, 0.0, 0.0, 0.0, 0.0)
        command_list.draw(self.m_framebuffer, pipeline, self.m_draw.sets(named), len(indices), vertex_buffers=streams,
                          index_buffer=pieces[1].buffer, index_offset=pieces[1].offset,
                          viewport=(0.0, 0.0, float(self.m_size_texels), float(self.m_size_texels)))  # fmt: skip
        for piece in pieces:
            pool.free(piece)  # reused a few frames later: the list still reads them
        self._derive(values["cTrnBakedTexelSizeWS"], layer)

    def _derive(self, texel: float, layer: int) -> None:
        """The compute passes over the baked height, then the copies into the arrays."""
        command_list, kernels, work, height_texture = self.m_list, self.m_kernels, self.m_work, self.m_targets["height"]
        size_px = self.m_size_texels

        def fill(constants) -> None:
            constants.set("cTrnBakedTexelSizeWS", texel)
            constants.set("cTrnCompressTexelSize", [1.0 / size_px, 1.0 / size_px])

        steps = (("normal", {"cHeightTexture": height_texture, "cNormalFromHeightDstTex": work["normal"]}, groups(size_px, 16)),
                 ("granularity", {"cHeightTexture": height_texture, "cGranularityFromHeightDstTex": work["granularity"]},
                  groups(GRANULARITY, 16)),
                 ("height", {"cCompressSrcTex": height_texture, "cCompressDstTex": work["blocks"]}, groups(size_px // 4, 16)),
                 ("flip", {"cSrc": work["normal"], "cDst": work["flipped"]}, groups(size_px, 8)))  # fmt: skip
        for name, named, group_count in steps:
            kernels[name].dispatch(command_list, named, fill, (group_count, group_count, 1))
        sources = {
            **self.m_targets,
            "normal": work["flipped"],
            "granularity": work["granularity"],
            "blocks": work["blocks"],
        }
        for name, (_fmt, _q, source_name) in ARRAYS.items():
            texture = sources[source_name]
            side = {"blocks": size_px // 4, "granularity": GRANULARITY}.get(source_name, size_px)
            command_list.copy_region(self.m_native_arrays[name], layer, 0, 0, 0, texture, 0, 0, side, side)

    def release(self) -> None:
        for kernel in (self.m_draw, *self.m_kernels.values()):
            kernel.release()
        for array_texture in self.m_arrays.values():
            self.m_handover.drop(array_texture)
        for resource in (*self.m_targets.values(), *self.m_work.values(), *self.m_maps.values(), *self.m_splat_maps, self.m_white,
                  self.m_splats, self.m_framebuffer):  # fmt: skip
            resource.release()
        if self.m_table is not None:
            self.m_table.release()
        self.m_pool.release()
        self.m_list.close()
        self.m_arrays, self.m_maps, self.m_splat_maps, self.m_table = {}, {}, [], None


@dataclass(frozen=True)
class SFlipShader:
    """Our own pass's compiled stage as ``CKernel.compute`` takes a shader (``bytecode``, ``name``)."""

    stage: object

    @property
    def bytecode(self) -> bytes:
        return self.stage.blob

    @property
    def name(self) -> str:
        return self.stage.entry
