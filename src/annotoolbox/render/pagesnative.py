"""Anno 117's coarse heights and height pages made by the game's shipped ``terrain_compress.fx`` (bound as it ships, no
rewrite) on the native backend's own command list, into the module's textures, which go to QRhi as its own
(``nativecompute.Handover``): the phases still on QRhi (the ground's maps, the haze, the rivers, the current path's
nodes) read them as before, the native nodes and bake read the module's.

``NativePages(device, natives, handover, size, island)`` is the entry, with ``terrainpages.Pages``' interface
(``set_coarse``, ``want``, ``run(ctx)``, ``ready``, ``coarse``, ``error``); the chain and its formats are
``terrainpages``' (terrain-geometry.md 1.3, 1.4, 5). docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import nativegpu
from .gamecompute import groups
from .nativecompute import Kernel, Told
from .terrainpages import ACTIVE_WORDS, GROUP, INNER, PAGE_TEXELS, PER_FRAME, STAGES, Key, PageParts, PageQueue

ENV = "ANNO_NATIVE_PAGES"  # "0": the pages (and the bake with them) by the current path
EFFECT = "terrain_compress"
F = QRhiTexture.Format


def enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.available()


def shaders(natives) -> dict:
    """The chain's shaders of the shipped variant by ``terrainpages.STAGES``' keys, each reflected (in the
    background: the island's loader); a missing one raises (the pages then go by the current path)."""
    v = natives.variant(EFFECT)
    out = {key: natives.call(v, entry) for key, (_effect, _tech, entry) in STAGES.items()}
    for s in out.values():
        natives.reflection(s)
    return out


class NativePages(PageQueue, Told):
    """The coarse texture and the pages (module docstring); ``ready`` and ``coarse`` hold QRhi textures over the
    module's (``handover.native`` gives the module's)."""

    native = True  # its owner tells the ways apart by it

    def __init__(self, device, natives, handover, size: float, island: dict) -> None:
        super().__init__(size, island)
        self.device, self.handover = device, handover
        self.kernels = {k: Kernel.compute(device, s, natives.reflection(s)) for k, s in shaders(natives).items()}
        self.list = device.command_list()
        self.list.open()
        self.work: dict[tuple, object] = {}  # working textures by (name, side): one page at a time, in list order
        self.error = ""
        self.messages, self.foreign = [], []

    def _work(self, name: str, side: int, fmt: str, usage: str = ""):
        key = (name, side)
        if key not in self.work:
            self.work[key] = self.device.texture(side, side, fmt, usage=usage, name=f"pages {name}")
        return self.work[key]

    def run(self, ctx) -> None:
        """The coarse upload when due, then up to ``PER_FRAME`` pages, on the module's list, sent to the queue at
        once (ahead of QRhi's frame: its passes read them); a failure is kept in ``error`` (its owner turns the
        pages over to the current path)."""
        if self.error:
            return
        try:
            due = self._coarse_data is not None or (self.queue and self.coarse is not None)
            if due:
                self._told_before()
                self.handover.take_back(self.list, [self.coarse])  # the compaction reads it: QRhi read it before
                if self._coarse_data is not None:
                    self._upload(ctx, *self._coarse_data)
                for _ in range(PER_FRAME):
                    if not self.queue or self.coarse is None:
                        break
                    self._page(ctx, *self.queue.popitem(last=False))
                self.list.execute()
                self.handover.handed_back()
                self._told_after()
            self._evict(ctx)
        except Exception as exc:  # said once by its owner, which draws the pages by the current path from then on
            self.error = f"{type(exc).__name__}: {exc}"

    def _upload(self, ctx, data: bytes, width: int) -> None:
        """``csUploadHeight``: the coarse heights into BC5 blocks, copied into a new coarse texture (the old one
        let go after the frames that read it)."""
        self._coarse_data = None
        d, lst = self.device, self.list
        source = d.buffer(len(data), "structured", stride=8, name="coarse heights")
        active = d.buffer(4 * ACTIVE_WORDS, "structured", stride=4, name="active map")
        blocks = d.texture(width // 4, width // 4, "RGBA32_UINT", usage="uav", name="coarse blocks")
        coarse = d.texture(width, width, "BC5_UNORM", name="coarse heights")
        lst.write_buffer(source, data)
        lst.write_buffer(active, np.full(ACTIVE_WORDS, 0xFFFFFFFF, "<u4").tobytes())

        def fill(c) -> None:
            c.set("cIslandSize", [width, width])
            c.set("cActiveMapWidth", ACTIVE_WORDS)
            c.set("cIslandRotation", 0)

        n = groups(width // 4, GROUP)
        named = {"cUploadDataSrc8": source, "cActiveMap": active, "cDstBlockCompressedRGBA32": blocks}
        self.kernels["upload"].dispatch(lst, named, fill, (n, n, 1))
        lst.copy_region(coarse, 0, 0, 0, 0, blocks, 0, 0, width // 4, width // 4)
        for r in (source, active, blocks):
            r.release()  # late: the list still reads them
        if self.coarse is not None:
            self.handover.let_go(ctx, self.coarse)
        self.coarse = self.handover.give(coarse, F.BC5, (width, width))
        self.version += 1
        for key in list(self.ready):  # compacted against the old heights: again, keeping the old until then
            self._made[key] = -1

    def _page(self, ctx, key: Key, parts: PageParts) -> None:
        """One page through the chain: the file's parts uploaded, three syntheses, the compaction against the coarse
        heights and its BC4 blocks copied into the page's height texture, the normals into its own."""
        d, lst, k = self.device, self.list, self.kernels
        down = parts.downsampled
        source = self._work("down", down.shape[0], "R16_UNORM")
        lst.write_texture(source, np.ascontiguousarray(down, "<u2").tobytes(), down.shape[1] * 2)
        floats = []
        for i, (codes, _s, _b) in enumerate(parts.deltas):
            side = codes.shape[0]
            delta = self._work(f"delta{i}", side, "R8_UNORM")
            lst.write_texture(delta, np.ascontiguousarray(codes, np.uint8).tobytes(), codes.shape[1])
            floats.append(self._work(f"float{i}", side, "R32_FLOAT", "uav"))
            coarser = floats[i - 1] if i else source
            k["synth"].dispatch(lst, {"cSrc1": delta, "cSrc2": coarser, "cDstFloat": floats[i]},
                                lambda c, i=i: self._page_values(c, key, parts, i), (groups(side, GROUP),) * 2 + (1,))  # fmt: skip

        def last(c) -> None:  # the steps after the syntheses
            self._page_values(c, key, parts, None)

        compact = self._work("compact", PAGE_TEXELS, "R16_UNORM", "uav")
        blocks = self._work("blocks", PAGE_TEXELS // 4, "RG32_UINT", "uav")
        height = d.texture(PAGE_TEXELS, PAGE_TEXELS, "BC4_UNORM", name="height page")
        normal = d.texture(INNER, INNER, "RGBA8_UNORM", usage="uav", name="normal page")
        coarse = self.handover.native(self.coarse)
        n = groups(PAGE_TEXELS, GROUP)
        k["compact"].dispatch(lst, {"cSrc1": floats[-1], "cSessionTerrainHeightTex": coarse, "cDstUnorm16": compact},
                              last, (n, n, 1))  # fmt: skip
        n = groups(PAGE_TEXELS // 4, GROUP)
        k["bc4"].dispatch(lst, {"cSrc1": compact, "cDstBlockCompressedRG32": blocks}, last, (n, n, 1))
        lst.copy_region(height, 0, 0, 0, 0, blocks, 0, 0, PAGE_TEXELS // 4, PAGE_TEXELS // 4)
        n = groups(INNER, GROUP)
        top = floats[-1]
        k["normal"].dispatch(lst, {"cSrc1": top, "cSrc2": top, "cSrc3": top, "cDstNormal": normal}, last, (n, n, 1))
        old = self.ready.pop(key, None)
        self.ready[key] = (self.handover.give(height, F.BC4, (PAGE_TEXELS, PAGE_TEXELS)),
                           self.handover.give(normal, F.RGBA8, (INNER, INNER)))  # fmt: skip
        self._made[key] = self.version
        if old is not None:  # the frame's nodes may still read it
            self._let_go(ctx, old)

    def _let_go(self, ctx, pair: tuple) -> None:
        for q in pair:
            self.handover.let_go(ctx, q)

    def release(self) -> None:
        for kernel in self.kernels.values():
            kernel.release()
        for tex in self.work.values():
            tex.release()
        for q in (*(q for pair in self.ready.values() for q in pair), self.coarse):
            self.handover.drop(q)
        self.list.close()
        self.ready.clear()
        self.queue.clear()
        self.work, self.coarse = {}, None
