"""Anno 117's terrain heights on the GPU as the engine makes them, by the game's own ``terrain_compress.fx``: the
island's coarse heights into the session's height texture (``csUploadHeight``, BC5), and each height page rebuilt from
its file's parts (``csSynthesize`` per delta, ``csFinalHeightMapCompaction`` and its BC4 compression,
``csComputeNormalMap``) into the pair of textures a detailed node reads (``cNodeHeightMap``, ``cNodeNormalMap``).

``CPages(rhi, stages, size, island)`` is the entry: ``set_coarse`` and ``want`` ask, ``run(ctx)`` records what is due
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

from .gamecompute import CComputeChain, CComputeStage, copy_into, groups, storage_buffer
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
TEXTURE_FORMAT = QRhiTexture.Format
Key = tuple[int, int, int]  # (level, x, z)
MAX_PAGE_LEVEL = 12  # 16 x 4096 tiles: past any island


def page_tiles(level: int) -> float:
    return float(PAGE_TILES * 2**level)


def page_params(key: Key, size_tiles: float) -> list[float]:
    """``cTrnNormalPageParams``: the page's index (x, z) and how many pages of its level span the island, so that
    ``position / size * z - xy`` is the position on the page (terrain-geometry.md 1.2)."""
    level, nx, nz = key
    return [float(nx), float(nz), size_tiles / page_tiles(level)]


def finest_page(keys, level: int, x: float, z: float) -> Key | None:
    """The finest of ``keys`` whose square holds a node's (``level``: 2^level tiles, corner x, z) whole, None when
    none does: the page a detailed node reads [ours: terrain-geometry.md open question 3]."""
    tiles = 2.0**level
    for page_level in range(MAX_PAGE_LEVEL + 1):  # finest first, each level's page looked up, not searched for
        page_size_tiles = page_tiles(page_level)
        key = (page_level, int(x // page_size_tiles), int(z // page_size_tiles))
        if (
            page_size_tiles >= tiles
            and key in keys
            and x + tiles <= (key[1] + 1) * page_size_tiles
            and z + tiles <= (key[2] + 1) * page_size_tiles
        ):
            return key
    return None


def upload_heights(heights: np.ndarray, step_tiles: float, mirror: bool = True) -> tuple[bytes, int]:
    """The coarse heights (world units, [row][column] every ``step`` tiles) as ``csUploadHeight`` reads them: int16
    steps of 1/256 world unit, 2 a tile, rows of ``2 * size`` (the last sample left out), turned over in x into the
    view's mirrored axes when ``mirror`` (texel i is then the island's x ``size - i / 2``); with its width."""
    samples = np.asarray(heights, np.float64)
    if step_tiles != 0.5:  # a large island's grid: onto the half-tile grid of the texture, linearly
        sample_count = int(round((samples.shape[0] - 1) * step_tiles * 2)) + 1
        at = np.arange(sample_count) * 0.5 / step_tiles
        samples = _resampled(_resampled(samples, at, 1), at, 0)
    width = (samples.shape[0] - 1 + 3) // 4 * 4
    samples = np.pad(samples, ((0, width + 1 - samples.shape[0]), (0, width + 1 - samples.shape[1])), mode="edge")
    samples = samples[:width, width:0:-1] if mirror else samples[:width, :width]
    return np.clip(np.rint(samples * 256), -32768, 32767).astype("<i2").tobytes(), width


def _resampled(samples: np.ndarray, at: np.ndarray, axis: int) -> np.ndarray:
    """``samples`` read at the places ``at`` along ``axis`` (samples 1 apart) linearly, as ``np.interp`` does it for
    each row in turn (a continental island's rows one by one took half a second on the window's thread)."""
    last = samples.shape[axis] - 1
    at = np.clip(at, 0, last)
    first = np.minimum(np.floor(at).astype(np.int64), last)
    after = np.minimum(first + 1, last)
    shape = [1, 1]
    shape[axis] = len(at)
    lower, upper = np.take(samples, first, axis), np.take(samples, after, axis)
    return (upper - lower) * (at - first).reshape(shape) + lower


@dataclass
class SPageParts:
    """A page file's parts (``islands.gameterrain.read_page``): ``Downsampled`` (u16) and the deltas coarsest
    first, (codes u8, scale, bits) each."""

    downsampled: np.ndarray
    deltas: list


def page_values(
    constants, key: Key, parts: SPageParts, delta_index: int | None, island: dict, size_tiles: float
) -> None:
    """``cbTerrainCompress`` for one dispatch of a page's chain: the synthesis of delta ``k`` (None: the steps after
    the last one), the page's place on the island (``island``: ``cTrnIslandMatrix0/1``, ``size_tiles`` its tiles)."""
    level, nx, nz = key
    tiles = page_tiles(level)
    constants.set_rows("cScaleFactor", [[float(scale), 0.0, 0.0, 0.0] for _codes, scale, _bits in parts.deltas])
    constants.set_rows("cQuantBits", [[int(bits), 0, 0, 0] for _codes, _s, bits in parts.deltas])
    if delta_index is not None:
        fine = parts.deltas[delta_index][0].shape[0]
        constants.set("cSrc1Size", [fine, fine])
        coarse = parts.deltas[delta_index - 1][0] if delta_index else parts.downsampled
        constants.set("cSrc2Size", [coarse.shape[1], coarse.shape[0]])
        constants.set("cCurrentMip", delta_index)
    else:
        constants.set("cSrc1Size", [PAGE_TEXELS, PAGE_TEXELS])
    constants.set("cHardThreshold", 0.0)
    constants.set("cTrnIslandMatrix0", island["cTrnIslandMatrix0"])
    constants.set("cTrnIslandMatrix1", island["cTrnIslandMatrix1"])
    constants.set("cNormalScale", 256.0 * INNER / tiles)  # unorm height steps into world units per world unit
    constants.set("cNumBorderTexels", BORDER)
    constants.set("cRecSessionTerrainSize", [1.0 / size_tiles, 1.0 / size_tiles])
    constants.set("cPagePosIS", [nx * tiles, nz * tiles])
    constants.set("cPageSizeIS", tiles)


class CPageQueue:
    """What every way of making the pages keeps (``CPages`` on QRhi, ``pagesnative.CNativePages`` on the native
    backend): the pages asked for (``queue``), those made (``ready``: by key, the longest unused first; made again
    after a coarse upload, ``version``), those this frame's nodes read (``wanted``: never let go for ``KEEP``) and
    the coarse heights due. ``size_tiles``: the island's tiles; ``island``: ``cTrnIslandMatrix0/1``."""

    def __init__(self, size_tiles: float, island: dict) -> None:
        self.m_size_tiles, self.m_island = size_tiles, island
        self.m_coarse = None
        self._m_coarse_data: tuple[bytes, int] | None = None
        self.m_ready: OrderedDict[Key, tuple] = OrderedDict()
        self.m_queue: OrderedDict[Key, SPageParts] = OrderedDict()
        self.m_version = 0  # counts the coarse uploads: the pages compacted against an older one are made again
        self._m_made: dict[Key, int] = {}
        self.m_wanted: set[Key] = set()

    def set_coarse(self, heights: np.ndarray, step_tiles: float) -> None:
        """New coarse heights (world units): uploaded with the next ``run``, every page compacted again after."""
        self._m_coarse_data = upload_heights(heights, step_tiles)

    def want(self, key: Key, parts: SPageParts) -> None:
        """A page to rebuild (once; again after a coarse upload), its file's parts."""
        self.m_wanted.add(key)
        if key in self.m_ready and self._m_made.get(key) == self.m_version:
            self.m_ready.move_to_end(key)
        elif key not in self.m_queue:
            self.m_queue[key] = parts

    def busy(self) -> bool:
        return bool(self.m_queue) or self._m_coarse_data is not None

    def _evict(self, ctx) -> None:
        """The longest unused go first, never one this frame's nodes read: a view of more pages than ``KEEP`` (a
        continental island whole) let them go and made them again every frame, its nodes baked again for good."""
        for key in [k for k in self.m_ready if k not in self.m_wanted][: max(0, len(self.m_ready) - KEEP)]:
            self._let_go(ctx, self.m_ready.pop(key))
            self._m_made.pop(key, None)  # a page asked again later is made anew: its mark would only pile up
        self.m_wanted = set()

    def _let_go(self, ctx, pair: tuple) -> None:
        """A page's (height, normal) let go after the frame in flight (the frame's nodes may still read it)."""
        ctx.later(pair[0])
        ctx.later(pair[1])

    def _page_values(self, constants, key: Key, parts: SPageParts, delta_index: int | None) -> None:
        page_values(constants, key, parts, delta_index, self.m_island, self.m_size_tiles)


def _texture(rhi, texture_format, size_px: int, storage: bool = False, source: bool = False):
    flags = QRhiTexture.Flag.UsedWithLoadStore if storage else QRhiTexture.Flag(0)
    if source:
        flags |= QRhiTexture.Flag.UsedAsTransferSource
    texture = rhi.newTexture(texture_format, QSize(size_px, size_px), 1, flags)
    texture.create()
    return texture


class _CSlot:
    """One page's worth of working textures and the chain's stages over them (a slot rebuilds one page a frame:
    a stage's constants are what was written last in a frame)."""

    def __init__(self, rhi, stages: dict, first: _CSlot | None) -> None:
        sizes = [PAGE_TEXELS // 4, PAGE_TEXELS // 2, PAGE_TEXELS]
        self.m_down = _texture(rhi, TEXTURE_FORMAT.R16, 66)
        self.m_deltas = [_texture(rhi, TEXTURE_FORMAT.R8, side) for side in sizes]
        self.m_floats = [_texture(rhi, TEXTURE_FORMAT.R32F, side, storage=True, source=True) for side in sizes]
        self.m_compact = _texture(rhi, TEXTURE_FORMAT.R16, PAGE_TEXELS, storage=True, source=True)
        self.m_blocks = _texture(rhi, TEXTURE_FORMAT.RG32UI, PAGE_TEXELS // 4, storage=True, source=True)
        self.m_textures = [self.m_down, *self.m_deltas, *self.m_floats, self.m_compact, self.m_blocks]

        names = ("synth", "synth", "synth", "compact", "bc4", "normal")  # one pipeline per shader, the first slot's
        self.m_stages = [CComputeStage(rhi, stages[name], first.m_stages[i] if first is not None else None)
                       for i, name in enumerate(names)]  # fmt: skip
        self.m_synth = self.m_stages[:3]
        self.m_compact_stage, self.m_bc4, self.m_normal = self.m_stages[3:]

    def release(self) -> None:
        for stage in sorted(self.m_stages, key=lambda stage: stage.m_share is None):
            stage.release()
        for texture in self.m_textures:
            texture.destroy()


class CPages(CPageQueue, CComputeChain):
    """The coarse texture and the pages (module docstring). ``island`` holds ``cTrnIslandMatrix0/1`` (the island's
    place, the same the terrain draws with); ``coarse`` the session's height texture once uploaded."""

    def __init__(self, rhi, stages: dict, size_tiles: float, island: dict) -> None:
        super().__init__(size_tiles, island)
        self.m_rhi = rhi
        self.m_upload = CComputeStage(rhi, stages["upload"])
        self.m_slots: list[_CSlot] = []
        for _ in range(PER_FRAME):
            self.m_slots.append(_CSlot(rhi, stages, self.m_slots[0] if self.m_slots else None))
        self._m_coarse_blocks = None
        self._m_coarse_buffer = None
        self.m_ctx = None
        self.m_read_back: Key | None = None  # a page whose synthesized heights and normals to read (the tests)
        self.m_readbacks: dict[str, QRhiReadbackResult] = {}

    @property
    def m_stages(self) -> dict:
        out = {"upload": self.m_upload}
        for k, slot in enumerate(self.m_slots):
            out.update({(k, i): s for i, s in enumerate(slot.m_stages)})
        return out

    def run(self, ctx) -> None:
        """The coarse upload when due, then up to ``PER_FRAME`` pages, each dispatch a compute pass of its own."""
        self.m_ctx = ctx
        if self._m_coarse_data is not None:
            data, width = self._m_coarse_data
            self._m_coarse_data = None
            ctx.native_pass(lambda command_buffer, batch: self._run_upload(command_buffer, batch, data, width))
            self.m_version += 1
        for slot in self.m_slots:
            if not self.m_queue or self.m_coarse is None:
                break
            key, parts = self.m_queue.popitem(last=False)
            ctx.native_pass(
                lambda command_buffer, batch, page_slot=slot, page_key=key, page_parts=parts: self._run_page(
                    command_buffer, batch, page_slot, page_key, page_parts
                )
            )
        self._evict(ctx)

    def _run_upload(self, command_buffer, batch, data: bytes, width_px: int) -> None:
        rhi = self.m_rhi
        if self.m_coarse is None or self.m_coarse.pixelSize().width() != width_px:
            for old in (self.m_coarse, self._m_coarse_blocks, self._m_coarse_buffer):
                if old is not None:
                    old.destroy()
            self.m_coarse = _texture(rhi, TEXTURE_FORMAT.BC5, width_px)
            self._m_coarse_blocks = _texture(rhi, TEXTURE_FORMAT.RGBA32UI, width_px // 4, storage=True, source=True)
            self._m_coarse_buffer = storage_buffer(rhi, len(data))
        batch.uploadStaticBuffer(self._m_coarse_buffer, data)

        def fill(buffer) -> None:
            buffer.set("cIslandSize", [width_px, width_px])
            buffer.set("cActiveMapWidth", ACTIVE_WORDS)
            buffer.set("cIslandRotation", 0)
            buffer.set_rows("cActiveMap", [0xFFFFFFFF] * ACTIVE_WORDS)

        inputs = {"cUploadDataSrc8": self._m_coarse_buffer, "cDstBlockCompressedRGBA32": self._m_coarse_blocks}
        self.m_upload.prepare(batch, inputs, fill)
        command_buffer.beginComputePass(batch)
        group_count = groups(width_px // 4, GROUP)
        self.m_upload.dispatch(command_buffer, (group_count, group_count, 1))
        command_buffer.endComputePass(
            copy_into(rhi, self.m_coarse, self._m_coarse_blocks, (width_px // 4, width_px // 4))
        )
        for key in list(self.m_ready):  # compacted against the old heights: again, keeping the old until then
            self._m_made[key] = -1

    def _run_page(self, command_buffer, batch, slot: _CSlot, key: Key, parts: SPageParts) -> None:
        """One page through the chain: the file's parts uploaded, three syntheses, the compaction against the
        coarse texture and its BC4 blocks copied into the page's height texture, the normals into its own."""
        rhi = self.m_rhi
        old = self.m_ready.pop(key, None)
        height = _texture(rhi, TEXTURE_FORMAT.BC4, PAGE_TEXELS)
        normal = _texture(rhi, TEXTURE_FORMAT.RGBA8, INNER, storage=True)
        upload_level(batch, slot.m_down, np.ascontiguousarray(parts.downsampled, "<u2").tobytes())
        for texture, (codes, _s, _b) in zip(slot.m_deltas, parts.deltas, strict=True):
            upload_level(batch, texture, np.ascontiguousarray(codes, np.uint8).tobytes())
        sources = [slot.m_down, *slot.m_floats[:2]]
        steps = [(synth_stage, {"cSrc1": slot.m_deltas[k], "cSrc2": sources[k], "cDstFloat": slot.m_floats[k]}, k,
                  slot.m_deltas[k].pixelSize().width(), None) for k, synth_stage in enumerate(slot.m_synth)]  # fmt: skip
        blocks = copy_into(rhi, height, slot.m_blocks, (PAGE_TEXELS // 4, PAGE_TEXELS // 4))
        steps += [(slot.m_compact_stage, {"cSrc1": slot.m_floats[2], "cSessionTerrainHeightTex": self.m_coarse,
                                        "cDstUnorm16": slot.m_compact}, None, PAGE_TEXELS, None),
                  (slot.m_bc4, {"cSrc1": slot.m_compact, "cDstBlockCompressedRG32": slot.m_blocks}, None,
                   PAGE_TEXELS // 4, blocks),
                  (slot.m_normal, {"cSrc1": slot.m_floats[2], "cSrc2": slot.m_floats[2], "cSrc3": slot.m_floats[2],
                                 "cDstNormal": normal}, None, INNER, None)]  # fmt: skip
        for i, (stage, inputs, k, texels, after) in enumerate(steps):
            if i:
                batch = rhi.nextResourceUpdateBatch()
            stage.prepare(
                batch, inputs, lambda buffer, delta_index=k: self._page_values(buffer, key, parts, delta_index)
            )
            command_buffer.beginComputePass(batch)
            group_count = groups(texels, GROUP)
            stage.dispatch(command_buffer, (group_count, group_count, 1))
            if i == len(steps) - 1 and self.m_read_back == key:
                after = self._read(slot, normal)
            command_buffer.endComputePass(after)
        self.m_ready[key] = (height, normal)
        self._m_made[key] = self.m_version
        if old is not None:  # the frame's nodes may still read it
            self.m_ctx.later(old[0])
            self.m_ctx.later(old[1])

    def _read(self, slot: _CSlot, normal):
        """The page's synthesized heights, their compaction against the coarse heights and its normals, read once
        (they arrive after the frame)."""
        self.m_read_back = None
        batch = self.m_rhi.nextResourceUpdateBatch()
        self.m_readbacks = {}
        for name, texture in (
            ("heights", slot.m_floats[2]),
            ("compact", slot.m_compact),
            ("blocks", slot.m_blocks),
            ("normals", normal),
        ):
            self.m_readbacks[name] = QRhiReadbackResult()
            batch.readBackTexture(QRhiReadbackDescription(texture), self.m_readbacks[name])
        return batch

    def release(self) -> None:
        self.m_upload.release()
        for slot in self.m_slots:
            slot.release()
        for height, normal in self.m_ready.values():
            height.destroy()
            normal.destroy()
        for texture in (self.m_coarse, self._m_coarse_blocks, self._m_coarse_buffer):
            if texture is not None:
                texture.destroy()
        self.m_ready.clear()
        self.m_queue.clear()
        self.m_coarse = self._m_coarse_blocks = self._m_coarse_buffer = None
