"""Anno 117's ground baked by the game's own ``terrain_baking.fx`` on the native backend: the splats by the unmodified
``psBakeSplatsBulk`` (``cTrnBakeSplatTextures[96]`` a bindless range: one descriptor table), the normal, granularity
and BC4 height passes, on the module's own list into the module's arrays, which QRhi reads as its own (the decals).

``NativeBake(device, natives, handover, ground, flip)`` is the entry, with ``terrainbake.Bake``'s interface
(``arrays``, ``model``, ``run(ctx, jobs)``, ``paint``, ``error``); the formulas and what is ours: ``terrainbake``.
docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gamedraw, gamemaps, nativegpu, terraindraw
from . import gameshader as gs
from .gamecompute import groups
from .nativecompute import Kernel, Told, records
from .terrainbake import GRANULARITY, PER_FRAME, SIZE, SPLATS, GroundPaint, GroundSource, MissingMap

ENV = "ANNO_NATIVE_BAKE"  # "0": the bake by the current path (the native pages stay)
EFFECT = "terrain_baking"
# the bake's shaders by the entry and the literal arguments their techniques pass (TerrainBakeSplatsBulk,
# TerrainNormalFromHeight, TerrainGranularityFromHeight, TerrainCompressHeight)
CALLS = {"vs": ("vsBaking", ""), "ps": ("psBakeSplatsBulk", "false"),
         "normal": ("csGenerateNormalFromHeight", ""), "granularity": ("csGenerateGranularityFromHeight", ""),
         "height": ("csCompressBC4", "float3(1,0,0)true")}  # fmt: skip
BINDLESS = "cTrnBakeSplatTextures"
SLOTS = SPLATS * 3  # the range's maps: diffuse, height and gloss of each material id
TARGETS = ("RGBA16_FLOAT", "RGBA16_FLOAT", "RGBA8_UNORM")  # albedo, height, gloss (``terrainbake._Slot``'s)
F = QRhiTexture.Format
# the arrays ``terrain.fx`` reads: the module's format, QRhi's, and the target or working texture copied in
ARRAYS = {"cBakedDiffTexArray": ("RGBA16_FLOAT", F.RGBA16F, "albedo"),
          "cBakedNormalTexArray": ("RGBA8_UNORM", F.RGBA8, "normal"),
          "cBakedSpecTexArray": ("RGBA8_UNORM", F.RGBA8, "gloss"),
          "cBakedHeightTexArray": ("BC4_UNORM", F.BC4, "blocks"),
          "cBakedGranularityTexArray": ("R8_UNORM", F.R8, "granularity")}  # fmt: skip
STATE = {"cull": "none", "depth_test": False, "depth_write": False, "colors": list(TARGETS), "depth": ""}
# a DDS file's stored format -> the module's (as stored: the shader turns sRGB to linear itself, as the current
# path's arrays keep a file's own sRGB flag)
BLOCKS = {"DXT1": ("BC1_UNORM", 8), "DXT3": ("BC2_UNORM", 16), "DXT5": ("BC3_UNORM", 16), "ATI1": ("BC4_UNORM", 8),
          "ATI2": ("BC5_UNORM", 16), "BC6H": ("BC6H_UFLOAT", 16), "BC7": ("BC7_UNORM", 16)}  # fmt: skip


def enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.available()


def shaders(natives) -> dict:
    """The bake's shaders (``CALLS``) of the install's variant, each reflected (in the background)."""
    v = natives.variant(EFFECT)
    out = {key: natives.call(v, *call) for key, call in CALLS.items()}
    for s in out.values():
        natives.reflection(s)
    return out


@dataclass
class BakeJob:
    """A node's draw into the bake: its mesh, its ``terrainbake.bake_values``, its page's QRhi textures by name."""

    mesh: object
    values: dict
    own: dict


class Stored:
    """A map as ``gamedraw.TextureTable`` holds it, from the file: the stored levels of a single-face BC map (with the
    chain's missing small levels), else its first level as RGBA8."""

    def __init__(self, dds) -> None:
        info = dds.info
        found = BLOCKS.get(info.format) if info.faces == 1 else None
        self.width, self.height = info.width, info.height
        if found is None:
            self.format, self.block = "RGBA8_UNORM", 0
            self.levels = [np.ascontiguousarray(dds.level(0), np.uint8).tobytes()]
        else:
            self.format, self.block = found
            if info.srgb and self.format.startswith(("BC1", "BC2", "BC3", "BC7")):
                self.format += "_SRGB"
            self.levels = gamedraw.stored_levels(dds)

    def pitch(self, mip: int) -> int:
        w = max(1, self.width >> mip)
        return (w + 3) // 4 * self.block if self.block else w * 4

    def upload(self, device, lst, name: str):
        tex = device.texture(self.width, self.height, self.format, mips=len(self.levels), name=name)
        for mip, raw in enumerate(self.levels):
            lst.write_texture(tex, raw, self.pitch(mip), mip=mip)
        return tex


class NativeBake(GroundPaint, Told):
    """The game's bake on the native backend (module docstring). ``flip``: our own pass's compiled stage (``blob``,
    ``reflection``: ``terrainbake.FLIP_HLSL``), which turns the baked normal's v over for the mirrored view."""

    native = True  # its owner tells the ways apart by it

    def __init__(self, device, natives, handover, ground: GroundSource, flip, slices: int = terraindraw.SLICES,
                 size: int = SIZE, max_texture: int = 0) -> None:  # fmt: skip
        self.device, self.handover, self.ground = device, handover, ground
        self.size, self.max_texture = size, max_texture
        self.program = object()  # no model of the current path is this bake's (``GameIsland._take_failures``)
        made = shaders(natives)
        self.draw = Kernel.graphics(device, {k: (made[k], natives.reflection(made[k])) for k in ("vs", "ps")}, STATE,
                                    (BINDLESS,))  # fmt: skip
        self.kernels = {k: Kernel.compute(device, made[k], natives.reflection(made[k]))
                        for k in ("normal", "granularity", "height")}  # fmt: skip
        self.kernels["flip"] = Kernel.compute(device, FlipShader(flip), flip.reflection)
        element = next(cb for s in ("ps", "vs") for cb in natives.reflection(made[s]).cbuffers
                       if cb.name == "cSplatsBuffer")  # fmt: skip
        self.splat_records = records(element, SPLATS)
        self.list = device.command_list()
        self.list.open()
        d = device
        self.targets = {name: d.texture(size, size, fmt, usage="target", name=f"bake {name}")
                        for name, fmt in zip(("albedo", "height", "gloss"), TARGETS, strict=True)}  # fmt: skip
        self.framebuffer = d.framebuffer(list(self.targets.values()))
        self.work = {"normal": d.texture(size, size, "RGBA8_UNORM", usage="uav", name="bake normal"),
                     "flipped": d.texture(size, size, "RGBA8_UNORM", usage="uav", name="bake flipped"),
                     "granularity": d.texture(GRANULARITY, GRANULARITY, "R8_UNORM", usage="uav", name="bake grain"),
                     "blocks": d.texture(size // 4, size // 4, "RG32_UINT", usage="uav", name="bake blocks")}  # fmt: skip
        self.native_arrays = {}
        self.arrays = {}  # by the shader's name: QRhi's textures over the module's
        for name, (fmt, qformat, _src) in ARRAYS.items():
            side = GRANULARITY if name == "cBakedGranularityTexArray" else size
            tex = d.texture(side, side, fmt, depth=slices, kind="2darray", name=name)
            self.native_arrays[name] = tex
            self.arrays[name] = handover.give(tex, qformat, (side, side), slices)
        self.white = d.texture(4, 4, "RGBA8_UNORM", name="white")
        self.list.write_texture(self.white, bytes([255]) * 64, 16)
        self.splats = d.buffer(self.splat_records.size, "structured", stride=element.size, name="cSplatsBuffer")
        self.maps: dict = {}  # the material maps by the shader's names, made with the first run
        self.table = None  # the splat maps' descriptor table (``BINDLESS``) and the maps in it
        self.splat_maps: list = []
        self._painted: list = []
        self.camera = gs.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 100.0, (size, size))
        self._recorded = True  # the white map's upload
        self.error = ""
        self.messages, self.foreign = [], []

    def model(self, mesh, values: dict, own: dict) -> BakeJob:
        return BakeJob(mesh, values, own)

    def _ground_textures(self) -> None:
        """The material maps (R16G16_UINT, as the engine's: a u32 word each) and the splat maps in the range (a
        material of the set without a map: its kind's stand-in; an id outside the set: white, the current path's
        null map); the maps' files let go once on the device (a new bake reads them again)."""
        d, lst, g = self.device, self.list, self.ground
        for name, words in (("cMaterialIDTex", g.ids), ("cMaterialAlphaTex", g.alphas)):
            tex = self.maps[name] = d.texture(words.shape[1], words.shape[0], "RG16_UINT", name=name)
            lst.write_texture(tex, np.ascontiguousarray(words, "<u4").tobytes(), words.shape[1] * 4)
        g.release_maps()
        layout = self.draw.layouts[self.draw.plan.index(1, 0)]
        self.table = d.descriptor_table(layout, SLOTS)
        missing = [Stored(MissingMap(k)).upload(d, lst, f"missing {k}") for k in range(3)]
        by_slot = dict.fromkeys(range(SLOTS), self.white)
        for sp in g.splats:
            for k in range(3):
                m = gamemaps.smaller(sp.maps[k], self.max_texture)
                tex = Stored(m).upload(d, lst, f"splat {sp.mat_id}.{k}") if m is not None else missing[k]
                if sp.mat_id * 3 + k < SLOTS:
                    by_slot[sp.mat_id * 3 + k] = tex
        for slot, tex in by_slot.items():
            d.write_table(self.table, ("texture", slot, tex))
        self.splat_maps = [*missing, *{id(t): t for t in by_slot.values() if t is not self.white}.values()]
        for tex in self.splat_maps:  # a descriptor table's maps are tracked by no set: read in this list
            lst.set_state(tex, "shader_resource")
        g.splats = [replace(sp, maps=(None, None, None)) for sp in g.splats]

    def _write_paint(self) -> None:
        """The painted words (``paint``) into the material maps, through a texture of each rectangle."""
        b, lst = self.ground.block, self.list
        for x0, z0, ids, alphas in self._painted:
            for name, words, (x, z) in (("cMaterialIDTex", ids, (x0 // b, z0 // b)),
                                        ("cMaterialAlphaTex", alphas, (x0, z0))):  # fmt: skip
                if words is None or not words.size:
                    continue
                h, w = words.shape
                part = self.device.texture(w, h, "RG16_UINT", name="painted words")
                lst.write_texture(part, np.ascontiguousarray(words, "<u4").tobytes(), w * 4)
                lst.copy_region(self.maps[name], 0, 0, x, z, part, 0, 0, w, h)
                part.release()  # late: the list still reads it
            self._recorded = True
        self._painted.clear()

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (``BakeJob``, layer)) on the module's list, sent to the queue at
        once (ahead of QRhi's frame, which reads the arrays); a failure is kept in ``error``."""
        if self.error:
            return
        try:
            self._told_before()
            if self.table is None:
                self._ground_textures()
                self._recorded = True
            self._write_paint()
            jobs = jobs[:PER_FRAME]
            if jobs:  # what QRhi read since: its states taken back
                pages = [q for job, _layer in jobs for q in job.own.values()]
                self.handover.take_back(self.list, [*self.arrays.values(), *pages])
            for job, layer in jobs:
                self._bake(job, layer)
            if self._recorded or jobs:
                self.list.execute()
                self.handover.handed_back()
                self._recorded = False
            self._told_after()
        except Exception as exc:  # said once by its owner, which bakes by the current path from then on
            self.error = f"{type(exc).__name__}: {exc}"

    def _bake(self, job: BakeJob, layer: int) -> None:
        """One node: its splats into the targets, the normal, granularity and height's blocks made from the baked
        height, then each copied into the arrays at ``layer``."""
        d, lst, values = self.device, self.list, job.values
        mesh = job.mesh
        vertices = d.buffer(max(16, len(mesh.data)), "vertex", name="bake vertices")
        indices = np.ascontiguousarray(mesh.indices, "<u4")
        index = d.buffer(max(16, indices.nbytes), "index", name="bake indices")
        lst.write_buffer(vertices, mesh.data)
        lst.write_buffer(index, indices.tobytes())
        pipeline, fill = self.draw.pipeline_for(mesh.elements, mesh.stride)
        streams = [vertices]
        if fill:
            stream = d.buffer(max(16, len(fill)), "vertex", name="bake fill")
            lst.write_buffer(stream, fill)
            streams.append(stream)
        splats = gs.Constants(self.splat_records)
        for name, value in values.items():
            if name.startswith("cSplatsBuffer["):
                splats.set(name, value)
        lst.write_buffer(self.splats, bytes(splats.data))

        def own(c) -> None:
            for name, value in values.items():
                c.set(name, value)

        named = {**self.draw.write(lst, own, self.camera), **self.maps, "cSplatsBuffer": self.splats,
                 BINDLESS: self.table}  # fmt: skip
        for name in ("cHeightPageTex", "cNormalPageTex", "cTerrainHeightTex", "cTerrainIslandBorderTex"):
            named[name] = self.handover.native(job.own.get(name)) or self.white
        for t in self.targets.values():
            lst.clear_color(t, 0.0, 0.0, 0.0, 0.0)
        lst.draw(self.framebuffer, pipeline, self.draw.sets(named), len(indices), vertex_buffers=streams,
                 index_buffer=index, viewport=(0.0, 0.0, float(self.size), float(self.size)))  # fmt: skip
        for r in (vertices, index, *streams[1:]):
            r.release()  # late: the list still reads them
        self._derive(values["cTrnBakedTexelSizeWS"], layer)

    def _derive(self, texel: float, layer: int) -> None:
        """The compute passes over the baked height, then the copies into the arrays."""
        lst, k, w, height = self.list, self.kernels, self.work, self.targets["height"]
        size = self.size

        def fill(c) -> None:
            c.set("cTrnBakedTexelSizeWS", texel)
            c.set("cTrnCompressTexelSize", [1.0 / size, 1.0 / size])

        steps = (("normal", {"cHeightTexture": height, "cNormalFromHeightDstTex": w["normal"]}, groups(size, 16)),
                 ("granularity", {"cHeightTexture": height, "cGranularityFromHeightDstTex": w["granularity"]},
                  groups(GRANULARITY, 16)),
                 ("height", {"cCompressSrcTex": height, "cCompressDstTex": w["blocks"]}, groups(size // 4, 16)),
                 ("flip", {"cSrc": w["normal"], "cDst": w["flipped"]}, groups(size, 8)))  # fmt: skip
        for name, named, n in steps:
            k[name].dispatch(lst, named, fill, (n, n, 1))
        sources = {**self.targets, "normal": w["flipped"], "granularity": w["granularity"], "blocks": w["blocks"]}
        for name, (_fmt, _q, src) in ARRAYS.items():
            tex = sources[src]
            side = {"blocks": size // 4, "granularity": GRANULARITY}.get(src, size)
            lst.copy_region(self.native_arrays[name], layer, 0, 0, 0, tex, 0, 0, side, side)

    def release(self) -> None:
        for kernel in (self.draw, *self.kernels.values()):
            kernel.release()
        for q in self.arrays.values():
            self.handover.drop(q)
        for t in (*self.targets.values(), *self.work.values(), *self.maps.values(), *self.splat_maps, self.white,
                  self.splats, self.framebuffer):  # fmt: skip
            t.release()
        if self.table is not None:
            self.table.release()
        self.list.close()
        self.arrays, self.maps, self.splat_maps, self.table = {}, {}, [], None


@dataclass(frozen=True)
class FlipShader:
    """Our own pass's compiled stage as ``Kernel.compute`` takes a shader (``bytecode``, ``name``)."""

    stage: object

    @property
    def bytecode(self) -> bytes:
        return self.stage.blob

    @property
    def name(self) -> str:
        return self.stage.entry
