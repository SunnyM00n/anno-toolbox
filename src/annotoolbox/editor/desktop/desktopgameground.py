"""The session's terrain maps the island's models, decals and the terrain itself read in the look "The game's own
shaders", beyond the heights: the coarse displacement the bent models and the decals add, the grit and cliff
coverage (rd3d ``GritMap2``) with the material set's grit and cliff textures and their constants.

``GroundMaps(island)`` is the entry (one per ``desktopgame.GameIsland``): ``ground(coarse)`` is the frame's
``gameframe.Ground`` once the maps are on the device. docs/rendering/island-117-view.md "Phase 5", "Grit and cliff".
"""

from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ...render import gamedraw
from ...render.gamecompute import carry
from ...render.gameframe import Ground

JOB = "game-island-ground-maps"
NEUTRAL_DISPLACEMENT = 128  # a coarse displacement byte of no displacement (terrain-geometry.md 4.2)
# the material set's grit and cliff fields -> the frame's constants (``cbPerFrame``), and their textures
SET_FIELDS = {"GritTiling": "cGritTilingRec", "GritAlphaSharpness": "cGritAlphaSharpness", "GritSlope": "cGritSlope",
              "CliffTiling": "cCliffTilingRec", "CliffAlphaSharpness": "cCliffAlphaSharpness",
              "CliffSlope": "cCliffSlope"}  # fmt: skip
SET_TEXTURES = {"GritDiffuseTexture": "cGritDiffuseTex", "CliffDiffuseTexture": "cCliffDiffuseTex"}
F = QRhiTexture.Format


def coarse_displacement(view) -> np.ndarray:
    """The island's coarse displacement (rd3d ``Terrain/CoarseDisplacementMap``: a byte a texel) as [z, x] bytes, x
    turned over into the view's mirrored axes."""
    raw = view.rd.path("Terrain/CoarseDisplacementMap").find("map").data
    n = round(len(raw) ** 0.5)
    return np.ascontiguousarray(np.frombuffer(raw[: n * n], np.uint8).reshape(n, n)[:, ::-1])


def grit_map(view) -> tuple[bytes, int]:
    """The island's grit and cliff coverage (rd3d ``Terrain/GritMap2``: BC5 blocks, r grit, g cliff, two texels a
    world unit) as stored, x turned over into the view's mirrored axes; with its side in texels."""
    from ...render.fxcdraw import mirrored_x

    g = view.rd.path("Terrain/GritMap2")
    side = int(np.frombuffer(g.find("width").data, "<u4")[0]) * 4
    return mirrored_x(g.find("map").data, "ATI2", side, side), side


def set_grit(text: str) -> tuple[dict, dict]:
    """A material set's grit and cliff constants (the tilings as their reciprocals) and texture paths."""

    def field(name: str) -> str:
        m = re.search(rf"<{name}>([^<]*)</", text)
        return m.group(1).strip() if m else ""

    values = {}
    for name, key in SET_FIELDS.items():
        try:
            v = float(field(name))
        except ValueError:
            continue
        values[key] = 1.0 / v if name.endswith("Tiling") and v else v
    return values, {key: field(name).replace("\\", "/") for name, key in SET_TEXTURES.items() if field(name)}


def read_ground(editor) -> dict:
    """In the background: the displacement, the grit map, the set's grit constants and textures (DDS files); a part
    the island does not have is left out."""
    from .desktopgamebake import map_file

    view = editor.view
    out: dict = {"displacement": coarse_displacement(view)}
    try:
        out["grit"] = grit_map(view)
    except (AttributeError, ValueError):  # an island without a grit map: no grit
        pass
    values, textures = set_grit(view.files.read(view.material_set).decode("utf-8", "replace"))
    out["values"] = values
    out["textures"] = {key: dds for key, path in textures.items() if (dds := map_file(view.files, path)) is not None}
    return out


class GroundMaps:
    """The island's ground maps on the device (module docstring), made once they are read."""

    def __init__(self, island) -> None:
        self.island, self.r = island, island.r
        self.maps: dict = {}  # shader name -> texture
        self.values: dict = {}
        self._made: list = []

    def ground(self, coarse) -> Ground | None:
        """The frame's ground over the coarse heights ``coarse`` (None: none yet, no ground)."""
        if coarse is None:
            return None
        if not self._made:
            got = self.r.loader.get(JOB, lambda: read_ground(self.r.editor))
            if got is None:
                return None
            self._make(got if isinstance(got, dict) else {})
        return Ground(float(self.r.session.size), {"cTerrainHeightTex": coarse, **self.maps}, self.values)

    def _make(self, got: dict) -> None:
        rhi, uploads = self.r.ctx.rhi, []
        displacement = got.get("displacement")
        if displacement is None:
            displacement = np.full((1, 1), NEUTRAL_DISPLACEMENT, np.uint8)
        rows, columns = displacement.shape
        tex = rhi.newTexture(F.R8, QSize(columns, rows))
        tex.create()
        pixels = np.ascontiguousarray(displacement, np.uint8).tobytes()
        uploads.append(lambda b, t=tex, d=pixels: gamedraw.upload_level(b, t, d))
        self.maps["cTerrainCoarseDisplTex"] = tex
        self._made.append(tex)
        if "grit" in got:  # else the terrain's black stand-in: no grit
            raw, side = got["grit"]
            tex = rhi.newTexture(F.BC5, QSize(side, side))
            tex.create()
            uploads.append(lambda b, t=tex, d=raw: gamedraw.upload_level(b, t, d))
            self.maps["cTerrainGritTex"] = tex
            self._made.append(tex)
        for name, dds in got.get("textures", {}).items():
            tex, desc = gamedraw.dds_texture(rhi, dds)
            uploads.append(lambda b, t=tex, d=desc: b.uploadTexture(t, d))
            self.maps[name] = tex
            self._made.append(tex)
        self.values = dict(got.get("values", {}))
        self.r.loader.forget(JOB)  # its pixels go once on the device

        def upload(batch) -> None:
            for step in uploads:
                step(batch)

        self.r.ctx.native_pass(lambda cb, batch: carry(cb, batch, upload))

    def release(self) -> None:
        for tex in self._made:
            self.r.ctx.later(tex)
        self.maps, self.values, self._made = {}, {}, []
