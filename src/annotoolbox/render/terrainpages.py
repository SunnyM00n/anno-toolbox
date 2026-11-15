"""Anno 117's terrain heights on the GPU as the engine makes them, by the game's own ``terrain_compress.fx``: the
island's coarse heights into the session's height texture (``csUploadHeight``, BC5), and each height page rebuilt from
its file's parts (``csSynthesize`` per delta, ``csFinalHeightMapCompaction`` and its BC4 compression,
``csComputeNormalMap``) into the pair of textures a detailed node reads (``cNodeHeightMap``, ``cNodeNormalMap``).

``Pages(rhi, stages, size, island)`` is the entry: ``set_coarse`` and ``want`` ask, ``run(ctx)`` records what is due
(the coarse texture, then a few pages a frame), ``ready`` holds the pairs by (level, x, z). The formats:
docs/rendering/terrain-geometry.md 1.3, 1.4 and 5; what is ours: docs/rendering/island-117-view.md "Phase 2".
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiReadbackDescription,
    QRhiReadbackResult,
    QRhiTexture,
)

from .gamecompute import ComputeChain, ComputeStage, copy_into, groups, storage_buffer
from .gamedraw import upload_level

EFFECT = "data/shaders/fx/terrain_compress.fx"
STAGES = {"upload": (EFFECT, "TerrainUploadHeight", "csUploadHeight"), "synth": (EFFECT, "Synthesize", "csSynthesize"),
          "compact": (EFFECT, "FinalHeightMapCompaction", "csFinalHeightMapCompaction"),
          "bc4": (EFFECT, "BlockCompressHeightMap", "csCompressBC4"),
          "normal": (EFFECT, "ComputeNormalMap", "csComputeNormalMap")}  # fmt: skip
PAGE_TILES = 16  # tiles a level-0 page covers; doubles per level
PAGE_TEXELS = 528  # a rebuilt page, its border included (rd3d PageSize 512 + 2 x HeightPageBorderTexels)
BORDER = 8
INNER = PAGE_TEXELS - 2 * BORDER
GROUP = 8  # TERRAIN_COMPRESS_THREAD_GROUPS_X/Y and the upload's work group
PER_FRAME = 4  # pages rebuilt a frame [ours]
KEEP = 160  # pages kept on the device, the longest unused let go first [ours]
ACTIVE_WORDS = 32  # the upload's active map: one row of 32 chunks, all set (an island view has no inactive chunk)
F = QRhiTexture.Format
Key = tuple[int, int, int]  # (level, x, z)
MAX_PAGE_LEVEL = 12  # 16 x 4096 tiles: past any island


def page_tiles(level: int) -> float:
    return float(PAGE_TILES * 2**level)


def page_params(key: Key, size: float) -> list[float]:
    """``cTrnNormalPageParams``: the page's index (x, z) and how many pages of its level span the island, so that
    ``position / size * z - xy`` is the position on the page (terrain-geometry.md 1.2)."""
    level, nx, nz = key
    return [float(nx), float(nz), size / page_tiles(level)]


def finest_page(keys, level: int, x: float, z: float) -> Key | None:
    """The finest of ``keys`` whose square holds a node's (``level``: 2^level tiles, corner x, z) whole, None when
    none does: the page a detailed node reads [ours: terrain-geometry.md open question 3]."""
    tiles = 2.0**level
    for page_level in range(MAX_PAGE_LEVEL + 1):  # finest first, each level's page looked up, not searched for
        t = page_tiles(page_level)
        key = (page_level, int(x // t), int(z // t))
        if t >= tiles and key in keys and x + tiles <= (key[1] + 1) * t and z + tiles <= (key[2] + 1) * t:
            return key
    return None


def upload_heights(heights: np.ndarray, step: float, mirror: bool = True) -> tuple[bytes, int]:
    """The coarse heights (world units, [row][column] every ``step`` tiles) as ``csUploadHeight`` reads them: int16
    steps of 1/256 world unit, 2 a tile, rows of ``2 * size`` (the last sample left out), turned over in x into the
    view's mirrored axes when ``mirror`` (texel i is then the island's x ``size - i / 2``); with its width."""
    h = np.asarray(heights, np.float64)
    if step != 0.5:  # a large island's grid: onto the half-tile grid of the texture, linearly
        n = int(round((h.shape[0] - 1) * step * 2)) + 1
        at = np.arange(n) * 0.5 / step
        h = np.array([np.interp(at, np.arange(h.shape[1]), row) for row in h])
        h = np.array([np.interp(at, np.arange(h.shape[0]), col) for col in h.T]).T
    width = (h.shape[0] - 1 + 3) // 4 * 4
    h = np.pad(h, ((0, width + 1 - h.shape[0]), (0, width + 1 - h.shape[1])), mode="edge")
    h = h[:width, width:0:-1] if mirror else h[:width, :width]
    return np.clip(np.rint(h * 256), -32768, 32767).astype("<i2").tobytes(), width


@dataclass
class PageParts:
    """A page file's parts (``islands.gameterrain.read_page``): ``Downsampled`` (u16) and the deltas coarsest
    first, (codes u8, scale, bits) each."""

    downsampled: np.ndarray
    deltas: list


def _texture(rhi, fmt, size: int, storage: bool = False, source: bool = False):
    flags = QRhiTexture.Flag.UsedWithLoadStore if storage else QRhiTexture.Flag(0)
    if source:
        flags |= QRhiTexture.Flag.UsedAsTransferSource
    tex = rhi.newTexture(fmt, QSize(size, size), 1, flags)
    tex.create()
    return tex


def _upload(batch, tex, data: bytes) -> None:
    upload_level(batch, tex, data)


class _Slot:
    """One page's worth of working textures and the chain's stages over them (a slot rebuilds one page a frame:
    a stage's constants are what was written last in a frame)."""

    def __init__(self, rhi, stages: dict, first: _Slot | None) -> None:
        sizes = [PAGE_TEXELS // 4, PAGE_TEXELS // 2, PAGE_TEXELS]
        self.down = _texture(rhi, F.R16, 66)
        self.deltas = [_texture(rhi, F.R8, n) for n in sizes]
        self.floats = [_texture(rhi, F.R32F, n, storage=True, source=True) for n in sizes]
        self.compact = _texture(rhi, F.R16, PAGE_TEXELS, storage=True, source=True)
        self.blocks = _texture(rhi, F.RG32UI, PAGE_TEXELS // 4, storage=True, source=True)
        self.textures = [self.down, *self.deltas, *self.floats, self.compact, self.blocks]

        names = ("synth", "synth", "synth", "compact", "bc4", "normal")  # one pipeline per shader, the first slot's
        self.stages = [ComputeStage(rhi, stages[name], first.stages[i] if first is not None else None)
                       for i, name in enumerate(names)]  # fmt: skip
        self.synth = self.stages[:3]
        self.compact_stage, self.bc4, self.normal = self.stages[3:]

    def release(self) -> None:
        for s in sorted(self.stages, key=lambda s: s.share is None):
            s.release()
        for t in self.textures:
            t.destroy()


class Pages(ComputeChain):
    """The coarse texture and the pages (module docstring). ``island`` holds ``cTrnIslandMatrix0/1`` (the island's
    place, the same the terrain draws with); ``coarse`` the session's height texture once uploaded."""

    def __init__(self, rhi, stages: dict, size: float, island: dict) -> None:
        self.rhi, self.size, self.island = rhi, size, island
        self.upload = ComputeStage(rhi, stages["upload"])
        self.slots: list[_Slot] = []
        for _ in range(PER_FRAME):
            self.slots.append(_Slot(rhi, stages, self.slots[0] if self.slots else None))
        self.coarse = None
        self._coarse_blocks = None
        self._coarse_data: tuple[bytes, int] | None = None
        self._coarse_buffer = None
        self.ready: OrderedDict[Key, tuple] = OrderedDict()
        self.queue: OrderedDict[Key, PageParts] = OrderedDict()
        self.version = 0  # counts the coarse uploads: the pages compacted against an older one are made again
        self._made: dict[Key, int] = {}
        self.wanted: set[Key] = set()  # the pages this frame's nodes asked for: never let go for ``KEEP``
        self.ctx = None
        self.read_back: Key | None = None  # a page whose synthesized heights and normals to read (the tests)
        self.readbacks: dict[str, QRhiReadbackResult] = {}

    @property
    def stages(self) -> dict:
        out = {"upload": self.upload}
        for k, slot in enumerate(self.slots):
            out.update({(k, i): s for i, s in enumerate(slot.stages)})
        return out

    def set_coarse(self, heights: np.ndarray, step: float) -> None:
        """New coarse heights (world units): uploaded with the next ``run``, every page compacted again after."""
        self._coarse_data = upload_heights(heights, step)

    def want(self, key: Key, parts: PageParts) -> None:
        """A page to rebuild (once; again after a coarse upload), its file's parts."""
        self.wanted.add(key)
        if key in self.ready and self._made.get(key) == self.version:
            self.ready.move_to_end(key)
        elif key not in self.queue:
            self.queue[key] = parts

    def busy(self) -> bool:
        return bool(self.queue) or self._coarse_data is not None

    def run(self, ctx) -> None:
        """The coarse upload when due, then up to ``PER_FRAME`` pages, each dispatch a compute pass of its own."""
        self.ctx = ctx
        if self._coarse_data is not None:
            data, width = self._coarse_data
            self._coarse_data = None
            ctx.native_pass(lambda cb, batch: self._run_upload(cb, batch, data, width))
            self.version += 1
        for slot in self.slots:
            if not self.queue or self.coarse is None:
                break
            key, parts = self.queue.popitem(last=False)
            ctx.native_pass(lambda cb, batch, s=slot, k=key, p=parts: self._run_page(cb, batch, s, k, p))
        # the longest unused go first, never one this frame's nodes read: a view of more pages than ``KEEP`` (a
        # continental island whole) let them go and made them again every frame, its nodes baked again for good
        for key in [k for k in self.ready if k not in self.wanted][: max(0, len(self.ready) - KEEP)]:
            height, normal = self.ready.pop(key)
            ctx.later(height)
            ctx.later(normal)
        self.wanted = set()

    def _run_upload(self, cb, batch, data: bytes, width: int) -> None:
        rhi = self.rhi
        if self.coarse is None or self.coarse.pixelSize().width() != width:
            for old in (self.coarse, self._coarse_blocks, self._coarse_buffer):
                if old is not None:
                    old.destroy()
            self.coarse = _texture(rhi, F.BC5, width)
            self._coarse_blocks = _texture(rhi, F.RGBA32UI, width // 4, storage=True, source=True)
            self._coarse_buffer = storage_buffer(rhi, len(data))
        batch.uploadStaticBuffer(self._coarse_buffer, data)

        def fill(c) -> None:
            c.set("cIslandSize", [width, width])
            c.set("cActiveMapWidth", ACTIVE_WORDS)
            c.set("cIslandRotation", 0)
            c.set_rows("cActiveMap", [0xFFFFFFFF] * ACTIVE_WORDS)

        inputs = {"cUploadDataSrc8": self._coarse_buffer, "cDstBlockCompressedRGBA32": self._coarse_blocks}
        self.upload.prepare(batch, inputs, fill)
        cb.beginComputePass(batch)
        n = groups(width // 4, GROUP)
        self.upload.dispatch(cb, (n, n, 1))
        cb.endComputePass(copy_into(rhi, self.coarse, self._coarse_blocks, (width // 4, width // 4)))
        for key in list(self.ready):  # compacted against the old heights: again, keeping the old until then
            self._made[key] = -1

    def _page_values(self, c, key: Key, parts: PageParts, k: int | None) -> None:
        level, nx, nz = key
        tiles = page_tiles(level)
        c.set_rows("cScaleFactor", [[float(s), 0.0, 0.0, 0.0] for _codes, s, _bits in parts.deltas])
        c.set_rows("cQuantBits", [[int(b), 0, 0, 0] for _codes, _s, b in parts.deltas])
        if k is not None:
            fine = parts.deltas[k][0].shape[0]
            c.set("cSrc1Size", [fine, fine])
            coarse = parts.deltas[k - 1][0] if k else parts.downsampled
            c.set("cSrc2Size", [coarse.shape[1], coarse.shape[0]])
            c.set("cCurrentMip", k)
        else:
            c.set("cSrc1Size", [PAGE_TEXELS, PAGE_TEXELS])
        c.set("cHardThreshold", 0.0)
        c.set("cTrnIslandMatrix0", self.island["cTrnIslandMatrix0"])
        c.set("cTrnIslandMatrix1", self.island["cTrnIslandMatrix1"])
        c.set("cNormalScale", 256.0 * INNER / tiles)  # unorm height steps into world units per world unit
        c.set("cNumBorderTexels", BORDER)
        c.set("cRecSessionTerrainSize", [1.0 / self.size, 1.0 / self.size])
        c.set("cPagePosIS", [nx * tiles, nz * tiles])
        c.set("cPageSizeIS", tiles)

    def _run_page(self, cb, batch, slot: _Slot, key: Key, parts: PageParts) -> None:
        """One page through the chain: the file's parts uploaded, three syntheses, the compaction against the
        coarse texture and its BC4 blocks copied into the page's height texture, the normals into its own."""
        rhi = self.rhi
        old = self.ready.pop(key, None)
        height = _texture(rhi, F.BC4, PAGE_TEXELS)
        normal = _texture(rhi, F.RGBA8, INNER, storage=True)
        _upload(batch, slot.down, np.ascontiguousarray(parts.downsampled, "<u2").tobytes())
        for tex, (codes, _s, _b) in zip(slot.deltas, parts.deltas, strict=True):
            _upload(batch, tex, np.ascontiguousarray(codes, np.uint8).tobytes())
        sources = [slot.down, *slot.floats[:2]]
        steps = [(s, {"cSrc1": slot.deltas[k], "cSrc2": sources[k], "cDstFloat": slot.floats[k]}, k,
                  slot.deltas[k].pixelSize().width(), None) for k, s in enumerate(slot.synth)]  # fmt: skip
        blocks = copy_into(rhi, height, slot.blocks, (PAGE_TEXELS // 4, PAGE_TEXELS // 4))
        steps += [(slot.compact_stage, {"cSrc1": slot.floats[2], "cSessionTerrainHeightTex": self.coarse,
                                        "cDstUnorm16": slot.compact}, None, PAGE_TEXELS, None),
                  (slot.bc4, {"cSrc1": slot.compact, "cDstBlockCompressedRG32": slot.blocks}, None,
                   PAGE_TEXELS // 4, blocks),
                  (slot.normal, {"cSrc1": slot.floats[2], "cSrc2": slot.floats[2], "cSrc3": slot.floats[2],
                                 "cDstNormal": normal}, None, INNER, None)]  # fmt: skip
        for i, (stage, inputs, k, texels, after) in enumerate(steps):
            if i:
                batch = rhi.nextResourceUpdateBatch()
            stage.prepare(batch, inputs, lambda c, k=k: self._page_values(c, key, parts, k))
            cb.beginComputePass(batch)
            n = groups(texels, GROUP)
            stage.dispatch(cb, (n, n, 1))
            if i == len(steps) - 1 and self.read_back == key:
                after = self._read(slot, normal)
            cb.endComputePass(after)
        self.ready[key] = (height, normal)
        self._made[key] = self.version
        if old is not None:  # the frame's nodes may still read it
            self.ctx.later(old[0])
            self.ctx.later(old[1])

    def _read(self, slot: _Slot, normal):
        """The page's synthesized heights, their compaction against the coarse heights and its normals, read once
        (they arrive after the frame)."""
        self.read_back = None
        batch = self.rhi.nextResourceUpdateBatch()
        self.readbacks = {}
        for name, tex in (
            ("heights", slot.floats[2]),
            ("compact", slot.compact),
            ("blocks", slot.blocks),
            ("normals", normal),
        ):
            self.readbacks[name] = QRhiReadbackResult()
            batch.readBackTexture(QRhiReadbackDescription(tex), self.readbacks[name])
        return batch

    def release(self) -> None:
        self.upload.release()
        for slot in self.slots:
            slot.release()
        for height, normal in self.ready.values():
            height.destroy()
            normal.destroy()
        for t in (self.coarse, self._coarse_blocks, self._coarse_buffer):
            if t is not None:
                t.destroy()
        self.ready.clear()
        self.queue.clear()
        self.coarse = self._coarse_blocks = self._coarse_buffer = None
