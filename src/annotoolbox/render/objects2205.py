"""Anno 2205's objects by its own model and prop shaders, many instances a draw as its engine draws a batch: a
model draw's ``cbModel`` words (colour, effects, time, the index of its matrix) over the frame's three matrix
textures, a prop draw's four float4 an instance in ``cbProp``; each draw a model of ``frameforward.CForwardFrame``.

``CObjectSource2205`` compiles; ``CFrameMatrices`` holds the frame's matrices; ``CRun2205`` / ``CPropRun2205`` are a
draw of instances; ``place_runs`` places a frame's instance data at once. The draws, the frame's rows and the maps are
1800's (``objects1800``). docs/rendering/island-1800-2205-view.md section 18.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import decalmesh, frameforward, gamedraw, objects1800

# 2205's prop shaders by the .prp's Type: the effect, its colour technique, the state block it draws with without the
# depth pre-pass, its caster, no normals-and-depth pass (2205 has no ambient occlusion pre-pass)
PROPS = {
    "Plant": ("prop_plant", "PlantDefault", 1, "PlantShadow", ""),
    "Simple": ("prop_simple", "PropDefault", 1, "PropShadow", ""),
    "Arctic": ("prop_arctic", "PropDefault", 1, "PropShadow", ""),
    # grass and pebbles stand on the session's height map (prop_terrain_object.fxh PlaceOnTerrain: ``HEIGHT_MAP``);
    # grass casts nothing, its shadow technique has no pass; the decals blend, premultiplied, casting nothing
    "Grass": ("prop_grass", "GrassDefault", 1, "GrassShadow", ""),
    "Pebble": ("prop_pebble", "GrassDefault", 1, "GrassShadow", ""),
    "Decal": ("prop_decal", "PropDefault", 1, "PropShadow", ""),
}
HEIGHT_MAP = "cTerrainHeightTex"  # the session's heights, a texel a world unit (``terrain_heights``)
CHUNK = 280  # cbModel's words a draw (constants_model.fxh NUM_MODEL_CONSTANTS)
PROP_CHUNK = 256  # cbProp holds 1024 float4: four an instance
WIDTH = frameforward.MATRIX_WIDTH  # the matrix textures' texels a row (model_base.fxh MODEL_CONSTANT_TEX_WIDTH)
WHITE = -1  # a word's x: the object's colour ARGB, white and opaque
MATRIX_NAMES = tuple(f"cModelConstantsMatrix{r}" for r in range(3))


class CObjectSource2205(objects1800.CObjectSource):
    """2205's model and prop effects as the viewer compiles them (``shaderforward``)."""

    GAME, PROPS = "anno2205", PROPS


def instance_words(count: int, first: int, bones: int = 1) -> np.ndarray:
    """``cModelInstanceData`` of ``count`` instances (int32 [n, 4]): white, no effects or damage, time 0, the index
    of each one's (first) matrix from ``first`` on, ``bones`` matrices each."""
    out = np.zeros((count, 4), np.int32)
    out[:, 0] = WHITE
    out[:, 3] = first + np.arange(count, dtype=np.int32) * max(1, bones)
    return out


def terrain_heights(heights: np.ndarray, step_tiles: float) -> np.ndarray:
    """``HEIGHT_MAP``'s texels ([z, x] float32) from the terrain's heights (rows z, the island's x, ``step`` apart):
    a texel a world unit in the view's axes, each the height at its centre (``PlaceOnTerrain`` reads the map at
    ``positionWS.xz / its size``, linearly) [ours: the engine's map is not read]."""
    size = (heights.shape[1] - 1) * step_tiles
    sample_height = decalmesh.heights_sampler(heights, step_tiles, size)
    centres = np.arange(int(round(size))) + 0.5
    grid_x, grid_z = np.meshgrid(centres, centres)
    return np.ascontiguousarray(sample_height(grid_x, grid_z), np.float32)


def prop_rows(worlds: np.ndarray, colours: np.ndarray | None = None) -> np.ndarray:
    """The props' ``cPropInstanceData`` ([n, 4, 4] float32): the place and fade (shown), the turn as a quaternion, the
    scale (the view's mirror a negative x, as 1800's: ``objects1800.prop_rows``), the colour (rgba floats; white)."""
    out = objects1800.prop_rows(worlds)
    out[:, 2, 3] = 0.0
    out[:, 3] = 1.0 if colours is None else np.asarray(colours, np.float32).reshape(-1, 4)
    return out


class CFrameMatrices:
    """The frame's matrices of every model draw in the three textures 2205's model shaders read (row r of each
    matrix at texel ``index & 4095, index >> 12``): ``begin`` a frame, ``add`` a draw's worlds, ``upload`` once."""

    def __init__(self, rhi) -> None:
        self.m_rhi, self.m_textures, self.m_rows = rhi, [], 0
        self.begin()

    def begin(self) -> None:
        self._m_parts: list[np.ndarray] = []
        self.m_count, self._m_sent = 0, False

    def add(self, worlds: np.ndarray, bones: int = 1) -> int:
        """A draw's matrices (each world ``bones`` times: a skin at rest): the index of its first."""
        first = self.m_count
        rows = frameforward.matrix_rows(list(np.repeat(np.asarray(worlds, float), max(1, bones), axis=0)))
        self._m_parts.append(rows)
        self.m_count += rows.shape[1]
        return first

    def named(self) -> dict:
        """The textures by the shaders' names (made with the frame's first ``add``s: room for its matrices)."""
        need = max(1, -(-self.m_count // WIDTH))
        if need > self.m_rows:
            for texture in self.m_textures:
                texture.destroy()
            self.m_textures = []
            for _ in MATRIX_NAMES:
                texture = self.m_rhi.newTexture(QRhiTexture.Format.RGBA32F, QSize(WIDTH, need))
                texture.create()
                self.m_textures.append(texture)
            self.m_rows, self._m_sent = need, False
        return dict(zip(MATRIX_NAMES, self.m_textures, strict=True))

    def upload(self, batch) -> None:
        """The frame's matrices into the textures (once a frame)."""
        if self._m_sent or not self._m_parts:
            return
        self._m_sent = True
        self.named()
        every = np.concatenate(self._m_parts, axis=1)
        for row, texture in enumerate(self.m_textures):
            data = np.zeros((self.m_rows * WIDTH, 4), np.float32)
            data[: every.shape[1]] = every[row]
            gamedraw.upload_level(batch, texture, data.tobytes())

    def release(self) -> None:
        for texture in self.m_textures:
            texture.destroy()
        self.m_textures, self.m_rows = [], 0
        self.begin()


class CRun2205(objects1800.CRun):
    """Up to ``CHUNK`` instances of a mesh (``objects1800.CRun``'s shape): its words in its ``cbModel`` slot of the
    frame's rows, its matrices in the frame's ``CFrameMatrices``; its caster (2205's one map) shares both."""

    m_matrices: CFrameMatrices | None = None

    def _matrices(self) -> dict:
        return self.m_matrices.named() if self.m_matrices is not None else {}

    def textures(self) -> dict:
        return {**self.m_part.textures(None), **self._matrices()}

    def prepare(self, render_pass, signature, batch, camera, light: dict, extra: dict | None = None, draw=None) -> None:
        super().prepare(render_pass, signature, batch, camera, light, {**self._matrices(), **(extra or {})}, draw)

    def _hand(self, rows: objects1800.CFrameRows, name: str, path: str, data: np.ndarray, fill=None) -> None:
        """``data`` into a slot of ``name`` its colour and caster draws share."""
        slot = objects1800.place_slot(rows, self.m_draw_call.m_program, name, path, data, fill)
        objects1800.hand_slots([self.m_draw_call, self.m_shadow.m_draw_call if self.m_shadow else None], slot, {})

    def place(self, rows: objects1800.CFrameRows, matrices: CFrameMatrices) -> None:
        """Its matrices into ``matrices``, its words into a ``cbModel`` slot its colour and caster draws share."""
        self.m_matrices = matrices
        first = matrices.add(self.m_worlds, self.m_bones)
        self._hand(rows, "cbModel", "cModelInstanceData", instance_words(len(self.m_worlds), first, self.m_bones))


class CPropRun2205(CRun2205):
    """A run of props (``PROP_CHUNK`` at most): their rows (``prop_rows``) in a ``cbProp`` slot with the fade (shown)
    its colour and caster draws share, no matrices; ``colours`` the island's stored colours (rgba floats; None:
    white)."""

    m_colours: np.ndarray | None = None

    def place(self, rows: objects1800.CFrameRows, matrices: CFrameMatrices | None = None) -> None:
        data = prop_rows(self.m_worlds, self.m_colours).reshape(-1, 4)
        self._hand(rows, "cbProp", "cPropInstanceData", data, objects1800.shown_fade)


def place_runs(ctx, rows: objects1800.CFrameRows, matrices: CFrameMatrices, runs: list) -> None:
    """The frame's instance data of every run at once: each run's words or props into the rows, the models'
    matrices into ``matrices``, sent ahead of the frame's passes (the shadow pass reads them first)."""
    rows.begin()
    matrices.begin()
    for run in runs:
        run.place(rows, matrices)
    matrices.named()  # made at their size before the draws bind them
    ctx.native_pass(lambda command_buffer, batch: (matrices.upload(batch), command_buffer.resourceUpdate(batch)))
