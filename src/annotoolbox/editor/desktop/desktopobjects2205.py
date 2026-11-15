"""The island's objects in an Anno 2205 session's look "The game's own shaders": 1800's object set
(``desktopobjects1800.Objects1800``: the parts within reach compiled in the background, many instances a draw, a part
that fails the view's own) with each part drawn by 2205's model and prop shaders (``render.objects2205``).

``Objects2205(island)`` is the ``objects`` of ``desktopgame2205.GameIsland2205``; docs/rendering/island-1800-2205-view.md
section 18.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ...render import cull, gamedraw, objects2205
from .desktopobjects1800 import Objects1800, Part1800


class Part2205(Part1800):
    """A compiled part (``Part1800``) whose runs are 2205's: words over the frame's matrix textures, props' rows."""

    RUN, PROP_RUN, PROP_CHUNK = objects2205.Run2205, objects2205.PropRun2205, objects2205.PROP_CHUNK

    terrain: dict = {}  # the session's maps the grass and pebbles stand on (``Objects2205.terrain``)

    @staticmethod
    def chunk(bones: int) -> int:
        return objects2205.CHUNK  # a word an instance; the matrices are the frame's textures

    def textures(self, batch) -> dict:
        return {**super().textures(batch), **self.terrain}


class Objects2205(Objects1800):
    """2205's objects by its own shaders (module docstring)."""

    GAME, PART = "anno2205", Part2205

    def __init__(self, island) -> None:
        super().__init__(island)
        self.matrices: objects2205.FrameMatrices | None = None
        self.terrain: dict = {}  # ``objects2205.HEIGHT_MAP``, made from the terrain's heights (``_heights``)
        self._height_key: tuple | None = None

    def _part(self, part: dict, source):
        got = super()._part(part, source)
        if isinstance(got, Part2205):
            got.terrain = self.terrain
        return got

    def _heights(self) -> None:
        """The session's height map the grass and pebbles stand on, made again when the terrain's heights changed
        (their sum compared: an edit writes into the same array)."""
        t = getattr(self.island, "terrain", None)
        heights = getattr(t, "heights", None)
        if heights is None:
            return
        key = (id(heights), heights.shape, float(heights.sum()))
        if key == self._height_key:
            return
        self._height_key = key
        grid = objects2205.terrain_heights(heights, t.step)
        rhi, tex = self.r.ctx.rhi, self.terrain.get(objects2205.HEIGHT_MAP)
        if tex is None or tex.pixelSize().width() != grid.shape[1]:
            if tex is not None:
                self.r.ctx.later(tex)
            tex = rhi.newTexture(QRhiTexture.Format.R32F, QSize(grid.shape[1], grid.shape[0]))
            tex.create()
            self.terrain[objects2205.HEIGHT_MAP] = tex
        self.r.ctx.native_pass(lambda cb, batch: (gamedraw.upload_level(batch, tex, grid.tobytes()),
                                                  cb.resourceUpdate(batch)))  # fmt: skip

    def _rules(self) -> cull.LodRules:
        """2205 ships no engine settings with level-of-detail distances: every part at its first level, the smallest
        size drawn 117's rule [ours]."""
        if self.rules is None:
            self.rules = cull.LodRules(bias=1.0)
        return self.rules

    @staticmethod
    def colour_rows(stored: np.ndarray) -> np.ndarray:
        """2205's prop rows take the stored colour as floats (``objects2205.prop_rows``)."""
        return stored

    def place(self, drawn, used: int, cascades: tuple) -> None:
        """The frame's words, props' rows and matrices of ``drawn``'s runs at once (``objects2205.place_runs``);
        nothing at a still camera."""
        if self.device is None:
            return
        self._heights()
        rows = self.device.rows
        if rows.key is not None and rows.key[0] is drawn:
            return
        self.matrices = self.matrices or objects2205.FrameMatrices(self.r.ctx.rhi)
        objects2205.place_runs(self.r.ctx, rows, self.matrices, [*drawn.gbuffer, *drawn.transparent])
        rows.key = (drawn, used, cascades)

    def release(self) -> None:
        super().release()
        if self.matrices is not None:
            self.matrices.release()
            self.matrices = None
        for tex in self.terrain.values():
            self.r.ctx.later(tex)
        self.terrain.clear()
        self._height_key = None
