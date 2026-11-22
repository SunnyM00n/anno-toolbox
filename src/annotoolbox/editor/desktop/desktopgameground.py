"""The session's terrain maps the island's models, decals and the terrain itself read in the look "The game's own
shaders", beyond the heights: the coarse displacement the bent models and the decals add, the grit and cliff
coverage (rd3d ``GritMap2``) with the material set's grit and cliff textures and their constants.

``CGroundMaps(island)`` is the entry (one per ``desktopgame.CGameIsland``): ``ground(coarse)`` is the frame's
``gameframe.SGround`` once the maps are on the device. docs/rendering/island-117-view.md "Phase 5", "Grit and cliff".
"""

from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ...render import gamedraw
from ...render.gamecompute import carry
from ...render.gameframe import SGround

JOB = "game-island-ground-maps"
NEUTRAL_DISPLACEMENT = 128  # a coarse displacement byte of no displacement (terrain-geometry.md 4.2)
# the material set's grit and cliff fields -> the frame's constants (``cbPerFrame``), and their textures
SET_FIELDS = {"GritTiling": "cGritTilingRec", "GritAlphaSharpness": "cGritAlphaSharpness", "GritSlope": "cGritSlope",
              "CliffTiling": "cCliffTilingRec", "CliffAlphaSharpness": "cCliffAlphaSharpness",
              "CliffSlope": "cCliffSlope"}  # fmt: skip
SET_TEXTURES = {"GritDiffuseTexture": "cGritDiffuseTex", "CliffDiffuseTexture": "cCliffDiffuseTex"}
TextureFormat = QRhiTexture.Format


def coarse_displacement(view) -> np.ndarray:
    """The island's coarse displacement (rd3d ``Terrain/CoarseDisplacementMap``: a byte a texel) as [z, x] bytes, x
    turned over into the view's mirrored axes."""
    raw = view.rd3d.path("Terrain/CoarseDisplacementMap").find("map").data
    side = round(len(raw) ** 0.5)
    return np.ascontiguousarray(np.frombuffer(raw[: side * side], np.uint8).reshape(side, side)[:, ::-1])


def grit_map(view) -> tuple[bytes, int]:
    """The island's grit and cliff coverage (rd3d ``Terrain/GritMap2``: BC5 blocks, r grit, g cliff, two texels a
    world unit) as stored, x turned over into the view's mirrored axes; with its side in texels."""
    from ...render.fxcdraw import mirrored_x

    grit_path = view.rd3d.path("Terrain/GritMap2")
    side = int(np.frombuffer(grit_path.find("width").data, "<u4")[0]) * 4
    return mirrored_x(grit_path.find("map").data, "ATI2", side, side), side


def set_grit(text: str) -> tuple[dict, dict]:
    """A material set's grit and cliff constants (the tilings as their reciprocals) and texture paths."""

    def field(name: str) -> str:
        match = re.search(rf"<{name}>([^<]*)</", text)
        return match.group(1).strip() if match else ""

    values = {}
    for name, key in SET_FIELDS.items():
        try:
            value = float(field(name))
        except ValueError:
            continue
        values[key] = 1.0 / value if name.endswith("Tiling") and value else value
    return values, {key: field(name).replace("\\", "/") for name, key in SET_TEXTURES.items() if field(name)}


def read_ground(editor) -> dict:
    """In the background: the displacement, the grit map, the set's grit constants and textures (DDS files); a part
    the island does not have is left out."""
    from .desktopgamebake import map_file

    view = editor.m_view
    out: dict = {}
    for name, read in (("displacement", coarse_displacement), ("grit", grit_map)):
        try:
            out[name] = read(view)
        except (AttributeError, ValueError):  # an island without the map: none (``CGroundMaps._make``)
            pass
    try:
        text = view.files.read(view.material_set).decode("utf-8", "replace")
    except (OSError, KeyError):  # a set that does not ship: no grit materials, the island's maps all the same
        text = ""
    values, textures = set_grit(text)
    out["values"] = values
    out["textures"] = {key: dds for key, path in textures.items() if (dds := map_file(view.files, path)) is not None}
    return out


class CGroundMaps:
    """The island's ground maps on the device (module docstring), made once they are read."""

    def __init__(self, island) -> None:
        self.m_island, self.m_renderer = island, island.m_renderer
        self.m_maps: dict = {}  # shader name -> texture
        self.m_values: dict = {}
        self._m_made: list = []

    def ground(self, coarse) -> SGround | None:
        """The frame's ground over the coarse heights ``coarse`` (None: none yet, no ground)."""
        if coarse is None:
            return None
        if not self._m_made:
            got = self.m_renderer.m_loader.get(JOB, lambda: read_ground(self.m_renderer.m_editor))
            if got is None:
                return None
            self._make(got if isinstance(got, dict) else {})
        return SGround(
            float(self.m_renderer.m_session.m_size_tiles), {"cTerrainHeightTex": coarse, **self.m_maps}, self.m_values
        )

    def _make(self, got: dict) -> None:
        rhi, uploads = self.m_renderer.m_ctx.m_rhi, []
        displacement = got.get("displacement")
        if displacement is None:
            displacement = np.full((1, 1), NEUTRAL_DISPLACEMENT, np.uint8)
        rows, columns = displacement.shape
        texture = rhi.newTexture(TextureFormat.R8, QSize(columns, rows))
        texture.create()
        pixels = np.ascontiguousarray(displacement, np.uint8).tobytes()
        uploads.append(
            lambda upload_batch, target=texture, data=pixels: gamedraw.upload_level(upload_batch, target, data)
        )
        self.m_maps["cTerrainCoarseDisplTex"] = texture
        self._m_made.append(texture)
        if "grit" in got:  # else the terrain's black stand-in: no grit
            raw, side = got["grit"]
            texture = rhi.newTexture(TextureFormat.BC5, QSize(side, side))
            texture.create()
            uploads.append(
                lambda upload_batch, target=texture, data=raw: gamedraw.upload_level(upload_batch, target, data)
            )
            self.m_maps["cTerrainGritTex"] = texture
            self._m_made.append(texture)
        for name, dds in got.get("textures", {}).items():
            texture, description = gamedraw.dds_texture(rhi, dds)
            uploads.append(
                lambda upload_batch, target=texture, data=description: upload_batch.uploadTexture(target, data)
            )
            self.m_maps[name] = texture
            self._m_made.append(texture)
        self.m_values = dict(got.get("values", {}))
        self.m_renderer.m_loader.forget(JOB)  # its pixels go once on the device

        def upload(batch) -> None:
            for step in uploads:
                step(batch)

        self.m_renderer.m_ctx.native_pass(lambda command_buffer, batch: carry(command_buffer, batch, upload))

    def release(self) -> None:
        for texture in self._m_made:
            self.m_renderer.m_ctx.later(texture)
        self.m_maps, self.m_values, self._m_made = {}, {}, []
