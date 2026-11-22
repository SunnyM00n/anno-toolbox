"""Anno 117's coarse heights and height pages made by the game's shipped ``terrain_compress.fx`` (bound as it ships, no
rewrite) on the native backend's own command list, into the module's textures, which go to QRhi as its own
(``nativecompute.CHandover``): the phases still on QRhi (the ground's maps, the haze, the rivers, the current path's
nodes) read them as before, the native nodes and bake read the module's.

``CNativePages(device, natives, handover, size, island)`` is the entry, with ``terrainpages.CPages``' interface
(``set_coarse``, ``want``, ``run(ctx)``, ``ready``, ``coarse``, ``error``); the chain and its formats are
``terrainpages``' (terrain-geometry.md 1.3, 1.4, 5). docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import nativegpu
from .gamecompute import groups
from .nativecompute import CKernel, CTold
from .terrainpages import ACTIVE_WORDS, GROUP, INNER, PAGE_TEXELS, PER_FRAME, STAGES, CPageQueue, Key, SPageParts

ENV = "ANNO_NATIVE_PAGES"  # "0": the pages (and the bake with them) by the current path
EFFECT = "terrain_compress"
TextureFormat = QRhiTexture.Format


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """The chain's shaders of the shipped variant by ``terrainpages.STAGES``' keys, each reflected (in the
    background: the island's loader); a missing one raises (the pages then go by the current path)."""
    variant = natives.variant(EFFECT)
    out = {key: natives.call(variant, entry) for key, (_effect, _tech, entry) in STAGES.items()}
    for shader in out.values():
        natives.reflection(shader)
    return out


class CNativePages(CPageQueue, CTold):
    """The coarse texture and the pages (module docstring); ``ready`` and ``coarse`` hold QRhi textures over the
    module's (``handover.native`` gives the module's)."""

    m_native = True  # its owner tells the ways apart by it

    def __init__(self, device, natives, handover, size_tiles: float, island: dict) -> None:
        super().__init__(size_tiles, island)
        self.m_device, self.m_handover = device, handover
        self.m_kernels = {
            stage: CKernel.compute(device, shader, natives.reflection(shader))
            for stage, shader in shaders(natives).items()
        }
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_work: dict[tuple, object] = {}  # working textures by (name, side): one page at a time, in list order
        self.m_error = ""
        self.m_messages, self.m_foreign = [], []

    def _work(self, name: str, side: int, texture_format: str, usage: str = ""):
        key = (name, side)
        if key not in self.m_work:
            self.m_work[key] = self.m_device.texture(side, side, texture_format, usage=usage, name=f"pages {name}")
        return self.m_work[key]

    def run(self, ctx) -> None:
        """The coarse upload when due, then up to ``PER_FRAME`` pages, on the module's list, sent to the queue at
        once (ahead of QRhi's frame: its passes read them); a failure is kept in ``error`` (its owner turns the
        pages over to the current path)."""
        if self.m_error:
            return
        try:
            due = self._m_coarse_data is not None or (self.m_queue and self.m_coarse is not None)
            if due:
                self._told_before()
                self.m_handover.take_back(self.m_list, [self.m_coarse])  # the compaction reads it: QRhi read it before
                if self._m_coarse_data is not None:
                    self._upload(ctx, *self._m_coarse_data)
                for _ in range(PER_FRAME):
                    if not self.m_queue or self.m_coarse is None:
                        break
                    self._page(ctx, *self.m_queue.popitem(last=False))
                self.m_list.execute()
                self.m_handover.handed_back()
                self._told_after()
            self._evict(ctx)
        except Exception as exc:  # said once by its owner, which draws the pages by the current path from then on
            self.m_error = f"{type(exc).__name__}: {exc}"

    def _upload(self, ctx, data: bytes, width_px: int) -> None:
        """``csUploadHeight``: the coarse heights into BC5 blocks, copied into a new coarse texture (the old one
        let go after the frames that read it)."""
        self._m_coarse_data = None
        device, command_list = self.m_device, self.m_list
        source = device.buffer(len(data), "structured", stride=8, name="coarse heights")
        active = device.buffer(4 * ACTIVE_WORDS, "structured", stride=4, name="active map")
        blocks = device.texture(width_px // 4, width_px // 4, "RGBA32_UINT", usage="uav", name="coarse blocks")
        coarse = device.texture(width_px, width_px, "BC5_UNORM", name="coarse heights")
        command_list.write_buffer(source, data)
        command_list.write_buffer(active, np.full(ACTIVE_WORDS, 0xFFFFFFFF, "<u4").tobytes())

        def fill(constants) -> None:
            constants.set("cIslandSize", [width_px, width_px])
            constants.set("cActiveMapWidth", ACTIVE_WORDS)
            constants.set("cIslandRotation", 0)

        group_count = groups(width_px // 4, GROUP)
        named = {"cUploadDataSrc8": source, "cActiveMap": active, "cDstBlockCompressedRGBA32": blocks}
        self.m_kernels["upload"].dispatch(command_list, named, fill, (group_count, group_count, 1))
        command_list.copy_region(coarse, 0, 0, 0, 0, blocks, 0, 0, width_px // 4, width_px // 4)
        for resource in (source, active, blocks):
            resource.release()  # late: the list still reads them
        if self.m_coarse is not None:
            self.m_handover.let_go(ctx, self.m_coarse)
        self.m_coarse = self.m_handover.give(coarse, TextureFormat.BC5, (width_px, width_px))
        self.m_version += 1
        for key in list(self.m_ready):  # compacted against the old heights: again, keeping the old until then
            self._m_made[key] = -1

    def _page(self, ctx, key: Key, parts: SPageParts) -> None:
        """One page through the chain: the file's parts uploaded, three syntheses, the compaction against the coarse
        heights and its BC4 blocks copied into the page's height texture, the normals into its own."""
        device, command_list, kernels = self.m_device, self.m_list, self.m_kernels
        down = parts.downsampled
        source = self._work("down", down.shape[0], "R16_UNORM")
        command_list.write_texture(source, np.ascontiguousarray(down, "<u2").tobytes(), down.shape[1] * 2)
        floats = []
        for i, (codes, _s, _b) in enumerate(parts.deltas):
            side = codes.shape[0]
            delta = self._work(f"delta{i}", side, "R8_UNORM")
            command_list.write_texture(delta, np.ascontiguousarray(codes, np.uint8).tobytes(), codes.shape[1])
            floats.append(self._work(f"float{i}", side, "R32_FLOAT", "uav"))
            coarser = floats[i - 1] if i else source
            kernels["synth"].dispatch(command_list, {"cSrc1": delta, "cSrc2": coarser, "cDstFloat": floats[i]},
                                lambda constants, i=i: self._page_values(constants, key, parts, i), (groups(side, GROUP),) * 2 + (1,))  # fmt: skip

        def last(constants) -> None:  # the steps after the syntheses
            self._page_values(constants, key, parts, None)

        compact = self._work("compact", PAGE_TEXELS, "R16_UNORM", "uav")
        blocks = self._work("blocks", PAGE_TEXELS // 4, "RG32_UINT", "uav")
        height = device.texture(PAGE_TEXELS, PAGE_TEXELS, "BC4_UNORM", name="height page")
        normal = device.texture(INNER, INNER, "RGBA8_UNORM", usage="uav", name="normal page")
        coarse = self.m_handover.native(self.m_coarse)
        group_count = groups(PAGE_TEXELS, GROUP)
        kernels["compact"].dispatch(command_list, {"cSrc1": floats[-1], "cSessionTerrainHeightTex": coarse, "cDstUnorm16": compact},
                              last, (group_count, group_count, 1))  # fmt: skip
        group_count = groups(PAGE_TEXELS // 4, GROUP)
        kernels["bc4"].dispatch(
            command_list, {"cSrc1": compact, "cDstBlockCompressedRG32": blocks}, last, (group_count, group_count, 1)
        )
        command_list.copy_region(height, 0, 0, 0, 0, blocks, 0, 0, PAGE_TEXELS // 4, PAGE_TEXELS // 4)
        group_count = groups(INNER, GROUP)
        top = floats[-1]
        kernels["normal"].dispatch(
            command_list,
            {"cSrc1": top, "cSrc2": top, "cSrc3": top, "cDstNormal": normal},
            last,
            (group_count, group_count, 1),
        )
        old = self.m_ready.pop(key, None)
        self.m_ready[key] = (self.m_handover.give(height, TextureFormat.BC4, (PAGE_TEXELS, PAGE_TEXELS)),
                           self.m_handover.give(normal, TextureFormat.RGBA8, (INNER, INNER)))  # fmt: skip
        self._m_made[key] = self.m_version
        if old is not None:  # the frame's nodes may still read it
            self._let_go(ctx, old)

    def _let_go(self, ctx, pair: tuple) -> None:
        for native in pair:
            self.m_handover.let_go(ctx, native)

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        for texture in self.m_work.values():
            texture.release()
        for native in (*(native for pair in self.m_ready.values() for native in pair), self.m_coarse):
            self.m_handover.drop(native)
        self.m_list.close()
        self.m_ready.clear()
        self.m_queue.clear()
        self.m_work, self.m_coarse = {}, None
