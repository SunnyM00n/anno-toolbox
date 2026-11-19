"""Anno 1800's height pages on the GPU as its engine makes them, by its own ``terrain_compress.fx``: 117's chain
(``terrainpages.Pages``: the parts uploaded, three syntheses, the compaction against the session's heights, the
normals) with 1800's steps - the session's height texture uploaded from the CPU (R16 unorm, no upload kernel), the
compacted heights kept uncompressed as the page's height texture, the normal page block-compressed as BC1.

``Pages1800(rhi, stages, size, island)`` is the entry (``STAGES``: its compute shaders by key, compiled by
``compile_stages``); ``set_coarse`` / ``want`` / ``run`` / ``ready`` as 117's. docs/rendering/island-1800-2205-view.md
section 9.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from . import terrainpages
from .gamecompute import ComputeStage, copy_into, groups
from .gamedraw import upload_level
from .terrainpages import GROUP, INNER, PAGE_TEXELS, PER_FRAME, Key, PageParts, _texture

EFFECT = terrainpages.EFFECT
STAGES = {"synth": "Synthesize", "compact": "FinalHeightMapCompaction", "normal": "ComputeNormalMap",
          "bc1": "BlockCompressNormalMap", "granularity": "ComputeGranularityMap"}  # fmt: skip
STEPS = ("synth", "synth", "synth", "compact", "normal", "bc1", "pack", "granularity")  # a slot's, one per dispatch
DEFINES = (("TERRAIN_COMPRESS_THREAD_GROUPS_X", "8"), ("TERRAIN_COMPRESS_THREAD_GROUPS_Y", "8"),
           ("HEIGHT_COMPRESS_DELTA_COUNT", "3"))  # fmt: skip
# the hull's factors are scaled by the page's granularity after a base three times as large (USE_GRANULARITY_MAP):
# a third, the factors as without the map, where a node has no map yet [ours]
GRANULARITY = (85, 85, 85, 255)
GRANULARITY_TEXELS = 66  # a page's granularity map, 8 page texels a texel (island-1800-2205-view.md 10) [ours]
F = QRhiTexture.Format


# ours: the BC1 kernel writes a block as four 16-bit words into a uint4 texel, a format QRhi has no storage of; two
# words a 32-bit lane make the RG32UI texel a BC1 block copies from
PACK_HLSL = """
Texture2D<uint4> cWords;
RWTexture2D<uint2> cBlocks;
[numthreads(8, 8, 1)]
void csPackBlocks(uint3 id : SV_DispatchThreadID)
{
    uint4 w = cWords[id.xy];
    cBlocks[id.xy] = uint2((w.x & 0xFFFF) | (w.y << 16), (w.z & 0xFFFF) | (w.w << 16));
}
"""


def compile_stages(source) -> dict:
    """The chain's compute shaders by key (``STAGES``) from a ``terrain1800.TerrainSource`` (in the background), with
    the defines the engine compiles them with (117's set: ``shaderdefines``), and ``pack`` (``PACK_HLSL``)."""
    from ..models import fxstage
    from . import gameshader as gs

    out = {key: source.call_stage(EFFECT, DEFINES, technique) for key, technique in STAGES.items()}
    done = source.compiler.compile(PACK_HLSL, "csPackBlocks", "cs_5_0", name="pack")
    if not done.ok:
        raise RuntimeError(f"csPackBlocks: {done.messages[:2000]}")
    reflection = source.compiler.reflect(done.object)
    out["pack"] = fxstage.Stage(done.object, "csPackBlocks", reflection, [], gs.declared_types(PACK_HLSL), {})
    return out


def coarse_texels(heights: np.ndarray, step: float) -> tuple[bytes, int]:
    """The session's height texture as 1800's shaders read it (``UnpackTerrainHeightSample``: ``r * 256 - 128``): R16
    unorm, 2 texels a tile in the view's axes (117's ``upload_heights`` grid), with its width."""
    data, width = terrainpages.upload_heights(heights, step)
    h = np.frombuffer(data, "<i2").astype(np.float64) / 256
    return np.clip(np.rint((h + 128) / 256 * 65535), 0, 65535).astype("<u2").tobytes(), width


class _Slot:
    """One page's working textures and its stages (117's ``_Slot`` with 1800's steps)."""

    def __init__(self, rhi, stages: dict, first: _Slot | None) -> None:
        sizes = [PAGE_TEXELS // 4, PAGE_TEXELS // 2, PAGE_TEXELS]
        self.down = _texture(rhi, F.R16, 66)
        self.deltas = [_texture(rhi, F.R8, n) for n in sizes]
        self.floats = [_texture(rhi, F.R32F, n, storage=True, source=True) for n in sizes]
        self.normal = _texture(rhi, F.RGBA8, INNER, storage=True)
        self.words = _texture(rhi, F.RGBA32UI, INNER // 4, storage=True)
        self.blocks = _texture(rhi, F.RG32UI, INNER // 4, storage=True, source=True)
        self.levels = rhi.newTexture(F.R8, QSize(PAGE_TEXELS, PAGE_TEXELS), 1, QRhiTexture.Flag.MipMapped)
        self.levels.create()  # the deltas as the granularity kernel reads them: one texture, level 0 the finest
        self.textures = [self.down, *self.deltas, *self.floats, self.normal, self.words, self.blocks, self.levels]
        self.stages = [ComputeStage(rhi, stages[name], first.stages[i] if first is not None else None)
                       for i, name in enumerate(STEPS)]  # fmt: skip

    def release(self) -> None:
        for s in sorted(self.stages, key=lambda s: s.share is None):
            s.release()
        for t in self.textures:
            t.destroy()


class Pages1800(terrainpages.Pages):
    """The session's height texture and the pages (module docstring); ``ready`` holds (height, normal) by key as
    117's: the height R16 (``cNodeHeightMap``), the normal BC1 of x, z, y (``cNodeNormalMap``)."""

    def __init__(self, rhi, stages: dict, size: float, island: dict) -> None:
        terrainpages.PageQueue.__init__(self, size, island)  # not 117's slots: 1800's own (``_Slot``)
        self.rhi = rhi
        self.slots = []
        for _ in range(PER_FRAME):
            self.slots.append(_Slot(rhi, stages, self.slots[0] if self.slots else None))
        self._coarse_blocks = self._coarse_buffer = None
        self.granularity: dict = {}  # each ready page's granularity map (``cNodeGranularityMap``) by key
        self.ctx, self.read_back, self.readbacks = None, None, {}

    @property
    def stages(self) -> dict:
        return {(k, i): s for k, slot in enumerate(self.slots) for i, s in enumerate(slot.stages)}

    def set_coarse(self, heights: np.ndarray, step: float) -> None:
        self._coarse_data = coarse_texels(heights, step)

    def _run_upload(self, cb, batch, data: bytes, width: int) -> None:
        """The session's heights from the CPU into their texture (made again when the width changed), handed to the
        command buffer: no pass takes this batch, and a batch no pass takes is dropped (the texture stayed zero, the
        tessellated nodes 128 units down: island-1800-2205-view.md 10)."""
        if self.coarse is None or self.coarse.pixelSize().width() != width:
            if self.coarse is not None:  # this frame's nodes were made with it: let go after the frame
                self.ctx.later(self.coarse)
            self.coarse = _texture(self.rhi, F.R16, width)
        upload_level(batch, self.coarse, data)
        cb.resourceUpdate(batch)
        for key in list(self.ready):  # compacted against the old heights: again, keeping the old until then
            self._made[key] = -1

    def run(self, ctx) -> None:
        """117's ``run``; a page let go takes its granularity map along."""
        super().run(ctx)
        for key in [k for k in self.granularity if k not in self.ready]:
            ctx.later(self.granularity.pop(key))

    def _granularity_values(self, c, key: Key, parts: PageParts) -> None:
        """The granularity kernel's constants: the deltas' scales and bits in its levels' order, finest first."""
        self._page_values(c, key, parts, None)
        finest_first = parts.deltas[::-1]
        c.set_rows("cScaleFactor", [[float(s), 0.0, 0.0, 0.0] for _codes, s, _bits in finest_first])
        c.set_rows("cQuantBits", [[int(b), 0, 0, 0] for _codes, _s, b in finest_first])

    def _run_page(self, cb, batch, slot: _Slot, key: Key, parts: PageParts) -> None:
        """One page through 1800's chain: the parts uploaded, three syntheses, the compaction into the page's own
        height texture, the normals and their BC1 blocks copied into the page's normal texture, the granularity map
        from the deltas (island-1800-2205-view.md 10)."""
        rhi = self.rhi
        old = self.ready.pop(key, None)
        height = _texture(rhi, F.R16, PAGE_TEXELS, storage=True)
        normal = _texture(rhi, F.BC1, INNER)
        granularity = _texture(rhi, F.R16, GRANULARITY_TEXELS, storage=True)
        upload_level(batch, slot.down, np.ascontiguousarray(parts.downsampled, "<u2").tobytes())
        for tex, (codes, _s, _b) in zip(slot.deltas, parts.deltas, strict=True):
            upload_level(batch, tex, np.ascontiguousarray(codes, np.uint8).tobytes())
        batch.uploadTexture(slot.levels, levels_upload(parts))
        sources = [slot.down, *slot.floats[:2]]

        def page(k: int | None):
            return lambda c: self._page_values(c, key, parts, k)

        steps = [({"cSrc1": slot.deltas[k], "cSrc2": sources[k], "cDstFloat": slot.floats[k]}, page(k),
                  slot.deltas[k].pixelSize().width(), None) for k in range(3)]  # fmt: skip
        blocks = copy_into(rhi, normal, slot.blocks, (INNER // 4, INNER // 4))
        steps += [({"cSrc1": slot.floats[2], "cSessionTerrainHeightTex": self.coarse, "cDstUnorm": height}, page(None),
                   PAGE_TEXELS, None),
                  ({"cSrc1": slot.floats[2], "cSrc2": slot.floats[2], "cSrc3": slot.floats[2],
                    "cDstNormal": slot.normal}, page(None), INNER, None),
                  ({"cSrc1": slot.normal, "cDstCompressedNormal": slot.words}, page(None), INNER // 4, None),
                  ({"cWords": slot.words, "cBlocks": slot.blocks}, page(None), INNER // 4, blocks),
                  ({"cSrc1": slot.levels, "cDstUnorm": granularity},
                   lambda c: self._granularity_values(c, key, parts), GRANULARITY_TEXELS, None)]  # fmt: skip
        for i, (stage, (inputs, fill, texels, after)) in enumerate(zip(slot.stages, steps, strict=True)):
            if i:
                batch = rhi.nextResourceUpdateBatch()
            stage.prepare(batch, inputs, fill)
            cb.beginComputePass(batch)
            n = groups(texels, GROUP)
            stage.dispatch(cb, (n, n, 1))
            cb.endComputePass(after)
        self.ready[key] = (height, normal)
        old_granularity, self.granularity[key] = self.granularity.get(key), granularity
        self._made[key] = self.version
        for tex in (*(old or ()), old_granularity):  # the frame's nodes may still read them
            if tex is not None:
                self.ctx.later(tex)

    def release(self) -> None:
        for slot in self.slots:
            slot.release()
        for height, normal in self.ready.values():
            height.destroy()
            normal.destroy()
        for tex in self.granularity.values():
            tex.destroy()
        if self.coarse is not None:
            self.coarse.destroy()
        self.ready.clear()
        self.queue.clear()
        self.granularity.clear()
        self.coarse = None


def levels_upload(parts: PageParts) -> QRhiTextureUploadDescription:
    """The page's three deltas as one texture's levels, the finest at level 0 (the parts hold them coarsest first)."""
    entries = [QRhiTextureUploadEntry(0, level, QRhiTextureSubresourceUploadDescription(
               np.ascontiguousarray(codes, np.uint8).tobytes())) for level, (codes, _s, _b) in
               enumerate(parts.deltas[::-1])]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    return desc
