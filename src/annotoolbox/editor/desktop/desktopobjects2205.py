"""The island's objects in an Anno 2205 session's look "The game's own shaders": 1800's object set
(``desktopobjects1800.CObjects1800``: the parts within reach compiled in the background, many instances a draw, a part
that fails the view's own) with each part drawn by 2205's model and prop shaders (``render.objects2205``).

``CObjects2205(island)`` is the ``objects`` of ``desktopgame2205.CGameIsland2205``; docs/rendering/island-1800-2205-view.md
section 18.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ...render import cull, gamedraw, objects2205
from .desktopobjects1800 import CObjects1800, CPart1800


class CPart2205(CPart1800):
    """A compiled part (``CPart1800``) whose runs are 2205's: words over the frame's matrix textures, props' rows."""

    RUN, PROP_RUN, PROP_CHUNK = objects2205.CRun2205, objects2205.CPropRun2205, objects2205.PROP_CHUNK

    m_terrain: dict = {}  # the session's maps the grass and pebbles stand on (``CObjects2205.terrain``)

    @staticmethod
    def chunk(bones: int) -> int:
        return objects2205.CHUNK  # a word an instance; the matrices are the frame's textures

    def textures(self, batch) -> dict:
        return {**super().textures(batch), **self.m_terrain}


class CObjects2205(CObjects1800):
    """2205's objects by its own shaders (module docstring)."""

    GAME, PART = "anno2205", CPart2205

    def __init__(self, island) -> None:
        super().__init__(island)
        self.m_matrices: objects2205.CFrameMatrices | None = None
        self.m_terrain: dict = {}  # ``objects2205.HEIGHT_MAP``, made from the terrain's heights (``_heights``)
        self._m_height_key: tuple | None = None

    def _part(self, part: dict, source):
        got = super()._part(part, source)
        if isinstance(got, CPart2205):
            got.m_terrain = self.m_terrain
        return got

    def _heights(self) -> None:
        """The session's height map the grass and pebbles stand on, made again when the terrain's heights changed
        (their sum compared: an edit writes into the same array)."""
        terrain = getattr(self.m_island, "m_terrain", None)
        heights = getattr(terrain, "m_heights", None)
        if heights is None:
            return
        key = (id(heights), heights.shape, float(heights.sum()))
        if key == self._m_height_key:
            return
        self._m_height_key = key
        grid = objects2205.terrain_heights(heights, terrain.m_step_tiles)
        rhi, texture = self.m_renderer.m_ctx.m_rhi, self.m_terrain.get(objects2205.HEIGHT_MAP)
        if texture is None or texture.pixelSize().width() != grid.shape[1]:
            if texture is not None:
                self.m_renderer.m_ctx.later(texture)
            texture = rhi.newTexture(QRhiTexture.Format.R32F, QSize(grid.shape[1], grid.shape[0]))
            texture.create()
            self.m_terrain[objects2205.HEIGHT_MAP] = texture
        self.m_renderer.m_ctx.native_pass(lambda command_buffer, update_batch: (gamedraw.upload_level(update_batch, texture, grid.tobytes()),
                                                  command_buffer.resourceUpdate(update_batch)))  # fmt: skip

    def _rules(self) -> cull.SLodRules:
        """2205 ships no engine settings with level-of-detail distances: every part at its first level, the smallest
        size drawn 117's rule [ours]."""
        if self.m_rules is None:
            self.m_rules = cull.SLodRules(bias=1.0)
        return self.m_rules

    @staticmethod
    def colour_rows(stored: np.ndarray) -> np.ndarray:
        """2205's prop rows take the stored colour as floats (``objects2205.prop_rows``)."""
        return stored

    def place(self, drawn, used: int, cascades: tuple) -> None:
        """The frame's words, props' rows and matrices of ``drawn``'s runs at once (``objects2205.place_runs``);
        nothing at a still camera."""
        if self.m_device is None:
            return
        self._heights()
        rows = self.m_device.rows
        if rows.m_key is not None and rows.m_key[0] is drawn:
            return
        self.m_matrices = self.m_matrices or objects2205.CFrameMatrices(self.m_renderer.m_ctx.m_rhi)
        objects2205.place_runs(self.m_renderer.m_ctx, rows, self.m_matrices, [*drawn.gbuffer, *drawn.transparent])
        rows.m_key = (drawn, used, cascades)

    def release(self) -> None:
        super().release()
        if self.m_matrices is not None:
            self.m_matrices.release()
            self.m_matrices = None
        for texture in self.m_terrain.values():
            self.m_renderer.m_ctx.later(texture)
        self.m_terrain.clear()
        self._m_height_key = None
