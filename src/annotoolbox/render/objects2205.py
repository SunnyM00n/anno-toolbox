"""Anno 2205's objects by its own model and prop shaders, many instances a draw as its engine draws a batch: a
model draw's ``cbModel`` words (colour, effects, time, the index of its matrix) over the frame's three matrix
textures, a prop draw's four float4 an instance in ``cbProp``; each draw a model of ``frameforward.ForwardFrame``.

``ObjectSource2205`` compiles; ``FrameMatrices`` holds the frame's matrices; ``Run2205`` / ``PropRun2205`` are a
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


class ObjectSource2205(objects1800.ObjectSource):
    """2205's model and prop effects as the viewer compiles them (``shaderforward``)."""

    GAME, PROPS = "anno2205", PROPS


def instance_words(count: int, first: int, bones: int = 1) -> np.ndarray:
    """``cModelInstanceData`` of ``count`` instances (int32 [n, 4]): white, no effects or damage, time 0, the index
    of each one's (first) matrix from ``first`` on, ``bones`` matrices each."""
    out = np.zeros((count, 4), np.int32)
    out[:, 0] = WHITE
    out[:, 3] = first + np.arange(count, dtype=np.int32) * max(1, bones)
    return out


def terrain_heights(heights: np.ndarray, step: float) -> np.ndarray:
    """``HEIGHT_MAP``'s texels ([z, x] float32) from the terrain's heights (rows z, the island's x, ``step`` apart):
    a texel a world unit in the view's axes, each the height at its centre (``PlaceOnTerrain`` reads the map at
    ``positionWS.xz / its size``, linearly) [ours: the engine's map is not read]."""
    size = (heights.shape[1] - 1) * step
    at = decalmesh.heights_sampler(heights, step, size)
    centres = np.arange(int(round(size))) + 0.5
    xs, zs = np.meshgrid(centres, centres)
    return np.ascontiguousarray(at(xs, zs), np.float32)


def prop_rows(worlds: np.ndarray, colours: np.ndarray | None = None) -> np.ndarray:
    """The props' ``cPropInstanceData`` ([n, 4, 4] float32): the place and fade (shown), the turn as a quaternion, the
    scale (the view's mirror a negative x, as 1800's: ``objects1800.prop_rows``), the colour (rgba floats; white)."""
    out = objects1800.prop_rows(worlds)
    out[:, 2, 3] = 0.0
    out[:, 3] = 1.0 if colours is None else np.asarray(colours, np.float32).reshape(-1, 4)
    return out


class FrameMatrices:
    """The frame's matrices of every model draw in the three textures 2205's model shaders read (row r of each
    matrix at texel ``index & 4095, index >> 12``): ``begin`` a frame, ``add`` a draw's worlds, ``upload`` once."""

    def __init__(self, rhi) -> None:
        self.rhi, self.textures, self.rows = rhi, [], 0
        self.begin()

    def begin(self) -> None:
        self._parts: list[np.ndarray] = []
        self.count, self._sent = 0, False

    def add(self, worlds: np.ndarray, bones: int = 1) -> int:
        """A draw's matrices (each world ``bones`` times: a skin at rest): the index of its first."""
        first = self.count
        rows = frameforward.matrix_rows(list(np.repeat(np.asarray(worlds, float), max(1, bones), axis=0)))
        self._parts.append(rows)
        self.count += rows.shape[1]
        return first

    def named(self) -> dict:
        """The textures by the shaders' names (made with the frame's first ``add``s: room for its matrices)."""
        need = max(1, -(-self.count // WIDTH))
        if need > self.rows:
            for tex in self.textures:
                tex.destroy()
            self.textures = []
            for _ in MATRIX_NAMES:
                tex = self.rhi.newTexture(QRhiTexture.Format.RGBA32F, QSize(WIDTH, need))
                tex.create()
                self.textures.append(tex)
            self.rows, self._sent = need, False
        return dict(zip(MATRIX_NAMES, self.textures, strict=True))

    def upload(self, batch) -> None:
        """The frame's matrices into the textures (once a frame)."""
        if self._sent or not self._parts:
            return
        self._sent = True
        self.named()
        every = np.concatenate(self._parts, axis=1)
        for r, tex in enumerate(self.textures):
            data = np.zeros((self.rows * WIDTH, 4), np.float32)
            data[: every.shape[1]] = every[r]
            gamedraw.upload_level(batch, tex, data.tobytes())

    def release(self) -> None:
        for tex in self.textures:
            tex.destroy()
        self.textures, self.rows = [], 0
        self.begin()


class Run2205(objects1800.Run):
    """Up to ``CHUNK`` instances of a mesh (``objects1800.Run``'s shape): its words in its ``cbModel`` slot of the
    frame's rows, its matrices in the frame's ``FrameMatrices``; its caster (2205's one map) shares both."""

    matrices: FrameMatrices | None = None

    def _matrices(self) -> dict:
        return self.matrices.named() if self.matrices is not None else {}

    def textures(self) -> dict:
        return {**self.part.textures(None), **self._matrices()}

    def prepare(self, rp, signature, batch, cam, light: dict, extra: dict | None = None, draw=None) -> None:
        super().prepare(rp, signature, batch, cam, light, {**self._matrices(), **(extra or {})}, draw)

    def _hand(self, rows: objects1800.FrameRows, name: str, path: str, data: np.ndarray, fill=None) -> None:
        """``data`` into a slot of ``name`` its colour and caster draws share."""
        slot = objects1800.place_slot(rows, self.draw_call.program, name, path, data, fill)
        objects1800.hand_slots([self.draw_call, self.shadow.draw_call if self.shadow else None], slot, {})

    def place(self, rows: objects1800.FrameRows, matrices: FrameMatrices) -> None:
        """Its matrices into ``matrices``, its words into a ``cbModel`` slot its colour and caster draws share."""
        self.matrices = matrices
        first = matrices.add(self.worlds, self.bones)
        self._hand(rows, "cbModel", "cModelInstanceData", instance_words(len(self.worlds), first, self.bones))


class PropRun2205(Run2205):
    """A run of props (``PROP_CHUNK`` at most): their rows (``prop_rows``) in a ``cbProp`` slot with the fade (shown)
    its colour and caster draws share, no matrices; ``colours`` the island's stored colours (rgba floats; None:
    white)."""

    colours: np.ndarray | None = None

    def place(self, rows: objects1800.FrameRows, matrices: FrameMatrices | None = None) -> None:
        data = prop_rows(self.worlds, self.colours).reshape(-1, 4)
        self._hand(rows, "cbProp", "cPropInstanceData", data, objects1800.shown_fade)


def place_runs(ctx, rows: objects1800.FrameRows, matrices: FrameMatrices, runs: list) -> None:
    """The frame's instance data of every run at once: each run's words or props into the rows, the models'
    matrices into ``matrices``, sent ahead of the frame's passes (the shadow pass reads them first)."""
    rows.begin()
    matrices.begin()
    for r in runs:
        r.place(rows, matrices)
    matrices.named()  # made at their size before the draws bind them
    ctx.native_pass(lambda cb, batch: (matrices.upload(batch), cb.resourceUpdate(batch)))
