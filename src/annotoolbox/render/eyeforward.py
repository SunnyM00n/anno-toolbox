"""Anno 1800's eye adaptation by its own compute shaders (``eye_adaptation.fx`` compiled as ``cs_5_0``) on the forward
path: 117's three passes (``render.eyeadapt``) with 1800's values - the histogram of the picture's middle third, its
filter and exposure range - and the exposure read back each frame for the post chain's constants.

``compile_stages(shaders)`` compiles in the background (``shaders``: the game's ``CForwardSource``);
``CEyeAdaptation1800(rhi, stages)``'s ``run(ctx, image, size_px, seconds)`` records the passes, ``value`` is the last
exposure read back. docs/rendering/shader-compile.md section 11, "1800's eye adaptation".
"""

from __future__ import annotations

import struct

from . import eyeadapt
from . import gameshader as gs
from .gamecompute import groups, record

EFFECT = "data/shaders/fx/eye_adaptation.fx"
TECHNIQUES = {"tiles": "ComputePerTileHistograms", "merge": "MergeHistograms", "exposure": "CalculateExposure"}
MERGE_GROUP = 1024  # NUM_TILES_PER_GROUP: the merge's threads, one a tile
CROP = 0.33  # the histogram reads the middle of the picture, this share of its size
# the engine's values (taken from how the game behaves; the filter's and the range's order taken from 117's [I])
CONSTANTS = {"cHistogramMin": -12.0, "cHistogramRange": 16.0, "cHistogramFilterMin": 0.5, "cHistogramFilterMax": 0.9,
             "cMinExposure": 1.0, "cMaxExposure": 2.1, "cAdaptationSpeedUp": 1.0, "cAdaptationSpeedDown": 0.8}  # fmt: skip


def compile_stages(shaders) -> dict:
    """In the background: the three passes' compute stages by ``TECHNIQUES``' keys."""
    return {key: shaders.call_stage(EFFECT, (), technique) for key, technique in TECHNIQUES.items()}


def crop(size_px: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """(offset, size) of the part of a picture of ``size_px`` the histogram reads: its middle, ``CROP`` of each side."""
    width_px, height_px = max(1, int(size_px[0] * CROP)), max(1, int(size_px[1] * CROP))
    return ((size_px[0] - width_px) // 2, (size_px[1] - height_px) // 2), (width_px, height_px)


def fill(constants: gs.CConstants, size_px: tuple[int, int], seconds: float | None) -> None:
    """``cbHistogram`` for a picture of ``size_px`` and the time since the last frame (None: no easing, the exposure
    goes to its target)."""
    offset, part = crop(size_px)
    for key, value in CONSTANTS.items():
        constants.set(key, value)
    eased = seconds is not None and 0.0 < seconds <= eyeadapt.SETTLE
    for key, value in (("cImageOffset", list(offset)), ("cImageSize", list(part)),
                       ("cNumTiles", [groups(part[0], eyeadapt.TILE), groups(part[1], eyeadapt.TILE)]),
                       ("cProgressive", int(eased)), ("cDeltaTime", seconds if eased else 0.0)):  # fmt: skip
        constants.set(key, value)


class CEyeAdaptation1800(eyeadapt.CEyeAdaptation):
    """117's chain with 1800's passes and values (module docstring); ``value`` the exposure of the last frame whose
    read-back arrived (1 before the first)."""

    def __init__(self, rhi, stages: dict) -> None:
        super().__init__(rhi, stages)
        self.m_value = 1.0

    def take(self) -> bool:
        """Take the exposure a frame read back; whether the view must draw once more: the value changed, or a
        read-back is still on its way (Direct3D 12 and Vulkan hand it over frames later, at a later frame's start)."""
        if self.m_readback is None:
            return False
        raw = bytes(self.m_readback.data)
        if len(raw) < 4:
            return True
        value, self.m_readback = struct.unpack_from("<f", raw)[0], None
        changed, self.m_value = value != self.m_value, value
        return changed

    def _after(self):
        """The batch the exposure pass ends with: a read-back only while none is on its way - QRhi writes into the
        result when the frame is done, so a pending one must stay alive."""
        return self._readback() if self.m_readback is None else None

    def run(self, ctx, image, size_px: tuple[int, int], seconds: float | None) -> None:
        """The histograms of ``image``'s middle (a device texture of ``size_px``), their merge and the exposure, read
        back when the frame is done; ``seconds`` as ``fill`` takes it."""
        _offset, part = crop(size_px)
        grid = (groups(part[0], eyeadapt.TILE), groups(part[1], eyeadapt.TILE))
        tiles = self._tile_buffer(grid[0] * grid[1])
        inputs = {"cImageTex": image.native, "cPerTileHistogramsOut": tiles, "cPerTileHistogramsIn": tiles,
                  "cMergedHistogramOut": self.m_merged, "cMergedHistogramIn": self.m_merged, "cExposureOut": self.m_exposure}  # fmt: skip
        first = not self._m_started
        frame_seconds = None if first else seconds

        def start(batch) -> None:  # the engine's first exposure: 1, then zeros
            if first:
                batch.uploadStaticBuffer(
                    self.m_exposure, struct.pack("<f", 1.0) + bytes(4 * (eyeadapt.EXPOSURE_FLOATS - 1))
                )

        def setup(constants: gs.CConstants) -> None:
            fill(constants, size_px, frame_seconds)

        record(ctx, self.m_stages["tiles"], inputs, setup, (*grid, 1), before=start)
        record(ctx, self.m_stages["merge"], inputs, setup, (groups(grid[0] * grid[1], MERGE_GROUP), 1, 1))
        record(ctx, self.m_stages["exposure"], inputs, setup, (1, 1, 1), self._after)
        self._m_started = True
