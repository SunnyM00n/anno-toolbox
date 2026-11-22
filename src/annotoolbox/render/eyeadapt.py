"""Anno 117's eye adaptation by the game's own compute shaders (``eye_adaptation.fx``): a histogram of the view's HDR
colour per 32 x 32 tile, the tiles merged, the exposure worked out and eased into the buffer the tone map reads.

``CEyeAdaptation(rhi, stages)`` is the entry (the compiled shaders by pass, ``STAGES``); ``run(ctx, image, size_px,
seconds)`` records the three passes and leaves the exposure in ``exposure`` (``cExposureBuffer``). The engine's values
and the viewer's stand-ins: docs/rendering/frame-117-viewer.md section 6.
"""

from __future__ import annotations

import struct

from PySide6.QtGui import QRhiReadbackResult

from . import gameshader as gs
from .gamecompute import CComputeChain, CComputeStage, groups, record, storage_buffer

EFFECT = "data/shaders/fx/eye_adaptation.fx"
# pass -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles
STAGES = {"tiles": (EFFECT, "ComputePerTileHistograms", "csPerTileHistogram"),
          "merge": (EFFECT, "MergeHistograms", "csMergeHistograms"),
          "exposure": (EFFECT, "CalculateExposure", "csCalculateExposure")}  # fmt: skip
TILE, BINS, MERGE_GROUP = 32, 64, 64  # HISTOGRAM_TILE_SIZE, HISTOGRAM_NUM_BINS, csMergeHistograms' threads
EXPOSURE_FLOATS = 96  # the engine's exposure buffer: 0x180 bytes, 1 then zeros
GCF_EYE_ADAPT = 1 << 1  # shared_constants.fxh
MAX_EXPOSURE = 12.0  # globals.xml EyeAdaptionMaxExposure (the engine starts at 40) [I]
# the engine's start values (frame-117-viewer.md 6); the target is 0.18 with the environment's two weights at 0
CONSTANTS = {"cHistogramMin": -12.0, "cHistogramRange": 16.0, "cHistogramFilterMin": 0.2, "cHistogramFilterMax": 0.9,
             "cMinExposure": 1.0, "cMaxExposure": MAX_EXPOSURE, "cAdaptationSpeedUp": 1.0,
             "cAdaptationSpeedDown": 0.8, "cExposureTarget": 0.18, "cExposureTargetRange": 0.0}  # fmt: skip
SETTLE = 1.0  # seconds: a frame later than this after the last one starts at the target (no easing)


def fill(constants: gs.CConstants, size_px: tuple[int, int], seconds: float | None) -> None:
    """``cbHistogram`` for an image of ``size_px`` and the frame's time (``seconds`` since the last frame; None: no
    easing, the exposure jumps to its target)."""
    for key, value in CONSTANTS.items():
        constants.set(key, value)
    eased = seconds is not None and 0.0 < seconds <= SETTLE
    for key, value in (("cImageOffset", [0, 0]), ("cImageSize", list(size_px)),
                       ("cNumTiles", [groups(size_px[0], TILE), groups(size_px[1], TILE)]),
                       ("cProgressive", int(eased)), ("cRealTimer", [0.0, seconds if eased else 0.0, 0.0, 0.0])):  # fmt: skip
        constants.set(key, value)


class CEyeAdaptation(CComputeChain):
    """The three passes and their buffers (module docstring); ``read_back`` asks for the exposure once (the tests)."""

    def __init__(self, rhi, stages: dict) -> None:
        self.m_rhi = rhi
        self.m_stages = {key: CComputeStage(rhi, stages[key]) for key in STAGES}
        self.m_exposure = storage_buffer(rhi, EXPOSURE_FLOATS * 4)
        self.m_merged = storage_buffer(rhi, BINS * 4)
        self.m_tiles = None
        self.m_tile_count = 0
        self._m_started = False
        self.m_read_back = False
        self.m_readback: QRhiReadbackResult | None = None

    def _tile_buffer(self, count: int):
        if self.m_tiles is None or count > self.m_tile_count:
            if self.m_tiles is not None:
                self.m_tiles.destroy()
            self.m_tiles, self.m_tile_count = storage_buffer(self.m_rhi, count * BINS * 4), count
            for stage in self.m_stages.values():
                stage.m_res.invalidate()
        return self.m_tiles

    def run(self, ctx, image, size_px: tuple[int, int], seconds: float | None) -> None:
        """The histograms of ``image`` (a device texture of ``size_px``), their merge and the exposure, each a compute
        pass of its own; ``seconds`` as ``fill`` takes it."""
        grid = (groups(size_px[0], TILE), groups(size_px[1], TILE))
        tiles = self._tile_buffer(grid[0] * grid[1])
        inputs = {"cImageTex": image.native, "cPerTileHistogramsOut": tiles, "cPerTileHistogramsIn": tiles,
                  "cMergedHistogramOut": self.m_merged, "cMergedHistogramIn": self.m_merged, "cExposureOut": self.m_exposure}  # fmt: skip

        first = not self._m_started  # the passes run when the frame is recorded: decide now
        frame_seconds = None if first else seconds

        def setup(constants: gs.CConstants) -> None:
            fill(constants, size_px, frame_seconds)

        def start(batch) -> None:  # the engine's first exposure: 1, then zeros
            if first:
                batch.uploadStaticBuffer(self.m_exposure, struct.pack("<f", 1.0) + bytes(4 * (EXPOSURE_FLOATS - 1)))

        record(ctx, self.m_stages["tiles"], inputs, setup, (*grid, 1), before=start)
        record(ctx, self.m_stages["merge"], inputs, setup, (groups(grid[0] * grid[1], MERGE_GROUP), 1, 1),
               before=lambda batch: batch.uploadStaticBuffer(self.m_merged, bytes(BINS * 4)))  # fmt: skip
        after = self._readback if self.m_read_back else None
        record(ctx, self.m_stages["exposure"], inputs, setup, (1, 1, 1), after)
        self._m_started = True

    def restart(self) -> None:
        """The next run starts at the target at once (a new model, a view drawn after a pause)."""
        self._m_started = False

    def _readback(self):
        self.m_read_back = False
        batch = self.m_rhi.nextResourceUpdateBatch()
        self.m_readback = QRhiReadbackResult()
        batch.readBackBuffer(self.m_exposure, 0, 8 * 4, self.m_readback)
        return batch

    def release(self) -> None:
        super().release()
        for buffer in (self.m_exposure, self.m_merged, self.m_tiles):
            if buffer is not None:
                buffer.destroy()
        self.m_tiles = None
