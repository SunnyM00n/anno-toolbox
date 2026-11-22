"""Anno 1800's height pages on the GPU as its engine makes them, by its own ``terrain_compress.fx``: 117's chain
(``terrainpages.CPages``: the parts uploaded, three syntheses, the compaction against the session's heights, the
normals) with 1800's steps - the session's height texture uploaded from the CPU (R16 unorm, no upload kernel), the
compacted heights kept uncompressed as the page's height texture, the normal page block-compressed as BC1.

``CPages1800(rhi, stages, size, island)`` is the entry (``STAGES``: its compute shaders by key, compiled by
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
from .gamecompute import CComputeStage, copy_into, groups
from .gamedraw import upload_level
from .terrainpages import GROUP, INNER, PAGE_TEXELS, PER_FRAME, Key, SPageParts, _texture

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
TextureFormat = QRhiTexture.Format


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
    """The chain's compute shaders by key (``STAGES``) from a ``terrain1800.CTerrainSource`` (in the background), with
    the defines the engine compiles them with (117's set: ``shaderdefines``), and ``pack`` (``PACK_HLSL``)."""
    from ..models import fxstage
    from . import gameshader as gs

    out = {key: source.call_stage(EFFECT, DEFINES, technique) for key, technique in STAGES.items()}
    done = source.m_compiler.compile(PACK_HLSL, "csPackBlocks", "cs_5_0", name="pack")
    if not done.ok:
        raise RuntimeError(f"csPackBlocks: {done.messages[:2000]}")
    reflection = source.m_compiler.reflect(done.object)
    out["pack"] = fxstage.SStage(done.object, "csPackBlocks", reflection, [], gs.declared_types(PACK_HLSL), {})
    return out


def coarse_texels(heights: np.ndarray, step_tiles: float) -> tuple[bytes, int]:
    """The session's height texture as 1800's shaders read it (``UnpackTerrainHeightSample``: ``r * 256 - 128``): R16
    unorm, 2 texels a tile in the view's axes (117's ``upload_heights`` grid), with its width."""
    data, width = terrainpages.upload_heights(heights, step_tiles)
    height_values = np.frombuffer(data, "<i2").astype(np.float64) / 256
    return np.clip(np.rint((height_values + 128) / 256 * 65535), 0, 65535).astype("<u2").tobytes(), width


class _CSlot:
    """One page's working textures and its stages (117's ``_CSlot`` with 1800's steps)."""

    def __init__(self, rhi, stages: dict, first: _CSlot | None) -> None:
        sizes = [PAGE_TEXELS // 4, PAGE_TEXELS // 2, PAGE_TEXELS]
        self.m_down = _texture(rhi, TextureFormat.R16, 66)
        self.m_deltas = [_texture(rhi, TextureFormat.R8, texture_size) for texture_size in sizes]
        self.m_floats = [
            _texture(rhi, TextureFormat.R32F, texture_size, storage=True, source=True) for texture_size in sizes
        ]
        self.m_normal = _texture(rhi, TextureFormat.RGBA8, INNER, storage=True)
        self.m_words = _texture(rhi, TextureFormat.RGBA32UI, INNER // 4, storage=True)
        self.m_blocks = _texture(rhi, TextureFormat.RG32UI, INNER // 4, storage=True, source=True)
        self.m_levels = rhi.newTexture(TextureFormat.R8, QSize(PAGE_TEXELS, PAGE_TEXELS), 1, QRhiTexture.Flag.MipMapped)
        self.m_levels.create()  # the deltas as the granularity kernel reads them: one texture, level 0 the finest
        self.m_textures = [
            self.m_down,
            *self.m_deltas,
            *self.m_floats,
            self.m_normal,
            self.m_words,
            self.m_blocks,
            self.m_levels,
        ]
        self.m_stages = [CComputeStage(rhi, stages[name], first.m_stages[i] if first is not None else None)
                       for i, name in enumerate(STEPS)]  # fmt: skip

    def release(self) -> None:
        for stage in sorted(self.m_stages, key=lambda stage: stage.m_share is None):
            stage.release()
        for texture in self.m_textures:
            texture.destroy()


class CPages1800(terrainpages.CPages):
    """The session's height texture and the pages (module docstring); ``ready`` holds (height, normal) by key as
    117's: the height R16 (``cNodeHeightMap``), the normal BC1 of x, z, y (``cNodeNormalMap``)."""

    def __init__(self, rhi, stages: dict, size_tiles: float, island: dict) -> None:
        terrainpages.CPageQueue.__init__(self, size_tiles, island)  # not 117's slots: 1800's own (``_CSlot``)
        self.m_rhi = rhi
        self.m_slots = []
        for _ in range(PER_FRAME):
            self.m_slots.append(_CSlot(rhi, stages, self.m_slots[0] if self.m_slots else None))
        self._m_coarse_blocks = self._m_coarse_buffer = None
        self.m_granularity: dict = {}  # each ready page's granularity map (``cNodeGranularityMap``) by key
        self.m_ctx, self.m_read_back, self.m_readbacks = None, None, {}

    @property
    def m_stages(self) -> dict:
        return {(k, i): stage for k, slot in enumerate(self.m_slots) for i, stage in enumerate(slot.m_stages)}

    def set_coarse(self, heights: np.ndarray, step_tiles: float) -> None:
        self._m_coarse_data = coarse_texels(heights, step_tiles)

    def _run_upload(self, command_buffer, batch, data: bytes, width_px: int) -> None:
        """The session's heights from the CPU into their texture (made again when the width changed), handed to the
        command buffer: no pass takes this batch, and a batch no pass takes is dropped (the texture stayed zero, the
        tessellated nodes 128 units down: island-1800-2205-view.md 10)."""
        if self.m_coarse is None or self.m_coarse.pixelSize().width() != width_px:
            if self.m_coarse is not None:  # this frame's nodes were made with it: let go after the frame
                self.m_ctx.later(self.m_coarse)
            self.m_coarse = _texture(self.m_rhi, TextureFormat.R16, width_px)
        upload_level(batch, self.m_coarse, data)
        command_buffer.resourceUpdate(batch)
        for key in list(self.m_ready):  # compacted against the old heights: again, keeping the old until then
            self._m_made[key] = -1

    def run(self, ctx) -> None:
        """117's ``run``; a page let go takes its granularity map along."""
        super().run(ctx)
        for key in [k for k in self.m_granularity if k not in self.m_ready]:
            ctx.later(self.m_granularity.pop(key))

    def _granularity_values(self, constants, key: Key, parts: SPageParts) -> None:
        """The granularity kernel's constants: the deltas' scales and bits in its levels' order, finest first."""
        self._page_values(constants, key, parts, None)
        finest_first = parts.deltas[::-1]
        constants.set_rows(
            "cScaleFactor", [[float(scale_factor), 0.0, 0.0, 0.0] for _codes, scale_factor, _bits in finest_first]
        )
        constants.set_rows("cQuantBits", [[int(quant_bits), 0, 0, 0] for _codes, _s, quant_bits in finest_first])

    def _run_page(self, command_buffer, batch, slot: _CSlot, key: Key, parts: SPageParts) -> None:
        """One page through 1800's chain: the parts uploaded, three syntheses, the compaction into the page's own
        height texture, the normals and their BC1 blocks copied into the page's normal texture, the granularity map
        from the deltas (island-1800-2205-view.md 10)."""
        rhi = self.m_rhi
        old = self.m_ready.pop(key, None)
        height = _texture(rhi, TextureFormat.R16, PAGE_TEXELS, storage=True)
        normal = _texture(rhi, TextureFormat.BC1, INNER)
        granularity = _texture(rhi, TextureFormat.R16, GRANULARITY_TEXELS, storage=True)
        upload_level(batch, slot.m_down, np.ascontiguousarray(parts.downsampled, "<u2").tobytes())
        for texture, (codes, _s, _b) in zip(slot.m_deltas, parts.deltas, strict=True):
            upload_level(batch, texture, np.ascontiguousarray(codes, np.uint8).tobytes())
        batch.uploadTexture(slot.m_levels, levels_upload(parts))
        sources = [slot.m_down, *slot.m_floats[:2]]

        def page(k: int | None):
            return lambda constants: self._page_values(constants, key, parts, k)

        steps = [({"cSrc1": slot.m_deltas[k], "cSrc2": sources[k], "cDstFloat": slot.m_floats[k]}, page(k),
                  slot.m_deltas[k].pixelSize().width(), None) for k in range(3)]  # fmt: skip
        blocks = copy_into(rhi, normal, slot.m_blocks, (INNER // 4, INNER // 4))
        steps += [({"cSrc1": slot.m_floats[2], "cSessionTerrainHeightTex": self.m_coarse, "cDstUnorm": height}, page(None),
                   PAGE_TEXELS, None),
                  ({"cSrc1": slot.m_floats[2], "cSrc2": slot.m_floats[2], "cSrc3": slot.m_floats[2],
                    "cDstNormal": slot.m_normal}, page(None), INNER, None),
                  ({"cSrc1": slot.m_normal, "cDstCompressedNormal": slot.m_words}, page(None), INNER // 4, None),
                  ({"cWords": slot.m_words, "cBlocks": slot.m_blocks}, page(None), INNER // 4, blocks),
                  ({"cSrc1": slot.m_levels, "cDstUnorm": granularity},
                   lambda constants: self._granularity_values(constants, key, parts), GRANULARITY_TEXELS, None)]  # fmt: skip
        for i, (stage, (inputs, fill, texels, after)) in enumerate(zip(slot.m_stages, steps, strict=True)):
            if i:
                batch = rhi.nextResourceUpdateBatch()
            stage.prepare(batch, inputs, fill)
            command_buffer.beginComputePass(batch)
            group_count = groups(texels, GROUP)
            stage.dispatch(command_buffer, (group_count, group_count, 1))
            command_buffer.endComputePass(after)
        self.m_ready[key] = (height, normal)
        old_granularity, self.m_granularity[key] = self.m_granularity.get(key), granularity
        self._m_made[key] = self.m_version
        for texture in (*(old or ()), old_granularity):  # the frame's nodes may still read them
            if texture is not None:
                self.m_ctx.later(texture)

    def release(self) -> None:
        for slot in self.m_slots:
            slot.release()
        for height, normal in self.m_ready.values():
            height.destroy()
            normal.destroy()
        for texture in self.m_granularity.values():
            texture.destroy()
        if self.m_coarse is not None:
            self.m_coarse.destroy()
        self.m_ready.clear()
        self.m_queue.clear()
        self.m_granularity.clear()
        self.m_coarse = None


def levels_upload(parts: SPageParts) -> QRhiTextureUploadDescription:
    """The page's three deltas as one texture's levels, the finest at level 0 (the parts hold them coarsest first)."""
    entries = [QRhiTextureUploadEntry(0, level, QRhiTextureSubresourceUploadDescription(
               np.ascontiguousarray(codes, np.uint8).tobytes())) for level, (codes, _s, _b) in
               enumerate(parts.deltas[::-1])]  # fmt: skip
    description = QRhiTextureUploadDescription()
    description.setEntries(entries)
    return description
