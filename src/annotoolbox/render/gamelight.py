"""Anno 117's deferred shading on QRhi: the compute pass of ``deferred_shading.fx`` (``csDeferredShading``, the uber
technique) over ``gameframe``'s G-buffer, writing the lit colour the frame shows.

``DeferredShading(rhi, stage, tables)`` is the entry (a compiled compute ``gamedraw.Stage``, the engine's ``Tables``
from the install); ``prepare(batch, inputs, camera, sky)`` before the pass, ``dispatch(cb, size)`` inside it. The
sun and the sky light (spherical harmonics, ``sky_cube``), the cascades and the clustered lights are filled by name;
what this frame lacks (reflections, ray-traced light) reads neutral. docs/rendering/frame-117-viewer.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiShaderResourceBinding,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from . import gameshader as gs
from . import gameshadow, gamesky, mipchain
from .gamebind import RAW
from .gamecompute import ComputeStage, groups, storage_buffer
from .gamedraw import stand_in

B, F = QRhiShaderResourceBinding, QRhiTexture.Format
TILE = 16  # DEFERRED_SHADING_TILE_SIZE_X / _Y of the shipped variant
CUBE_SIZE = 32  # the stand-in sky cube's faces
CLUSTER_BYTES = 32 * 16 * 16 * 8  # cLightClusterBuffer: a uint2 per cell
POINT_BYTES = 2048 * 48  # cPointLights: three float4 per light
WHITE = ("cSSSTex", "cShadowWeatherMap", "cTransmittanceMap")  # what reads neutral as 1 (no shadow, clear air)
# a cube face's direction for texel (u, v) in -1..1, v down: +X, -X, +Y, -Y, +Z, -Z (Direct3D's order)
FACES = (lambda u, v: (1, -v, -u), lambda u, v: (-1, -v, u), lambda u, v: (u, 1, v), lambda u, v: (u, -1, -v),
         lambda u, v: (u, -v, 1), lambda u, v: (-u, -v, -1))  # fmt: skip


@dataclass
class Extras:
    """What a frame adds to the shading: the sun's cascades (``gameshadow.Cascades``, with the ``cShadowMap`` among
    the inputs), the clustered lights (``lightclusters.Clusters``) and the clouds' shadow (``gameclouds.Clouds``,
    its maps among the inputs)."""

    cascades: object = None
    clusters: object = None
    clouds: object = None


@dataclass
class Tables:
    """The engine's tables from the install, as float RGBA arrays: the split-sum BRDF table (its levels), the area
    lights' two LTC tables, the blue noise."""

    brdf: list = field(default_factory=list)
    ltc: list = field(default_factory=list)
    noise: np.ndarray | None = None


@dataclass(frozen=True)
class Sky:
    """The light of a frame in the scene's axes: the sun's direction (towards it) and ``LgtDiffuse`` (radiance over
    pi), the sky's nine spherical-harmonic coefficients (raw radiance, rgb), the ambient factor."""

    sun: tuple
    sun_diffuse: tuple
    sh: tuple
    ambient: float = 1.0
    night: bool = False  # the game's sky passes light the night by the moon (cDayNight)


def sh_radiance(sh, dirs: np.ndarray) -> np.ndarray:
    """The sky's radiance along unit directions ``dirs`` [n, 3] from raw SH coefficients (``EvalSH9``'s basis)."""
    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    basis = np.stack([np.full_like(x, 0.282095), 0.488603 * y, 0.488603 * z, 0.488603 * x, 1.092548 * x * y,
                      1.092548 * y * z, 0.315392 * (3 * z * z - 1), 1.092548 * x * z, 0.546274 * (x * x - y * y)], 1)  # fmt: skip
    return np.maximum(basis @ np.asarray(sh, np.float64)[:, :3], 0.0)


def sky_cube(sh, size: int = CUBE_SIZE) -> list[list[np.ndarray]]:
    """The prefiltered specular cube's stand-in: the SH sky as six faces of float RGBA per level (every level the
    same light: the SH holds no detail a sharper level could show)."""
    levels = []
    for level in range(int(math.log2(size)) + 1):
        n = max(1, size >> level)
        c = (np.arange(n) + 0.5) / n * 2 - 1
        u, v = np.meshgrid(c, c)
        faces = []
        for face in FACES:
            d = np.stack([np.broadcast_to(np.asarray(a, float), u.shape) for a in face(u, v)], -1).reshape(-1, 3)
            d /= np.linalg.norm(d, axis=1, keepdims=True)
            rgb = sh_radiance(sh, d).reshape(n, n, 3)
            faces.append(np.concatenate([rgb, np.ones((n, n, 1))], -1).astype(np.float16))
        levels.append(faces)
    return levels


def _upload(batch, tex, levels: list[list[np.ndarray]]) -> None:
    """Float16 RGBA layers per level into ``tex`` (a layer is a cube face or the one image)."""
    entries = [QRhiTextureUploadEntry(layer, level, QRhiTextureSubresourceUploadDescription(img.tobytes()))
               for level, layers in enumerate(levels) for layer, img in enumerate(layers)]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    batch.uploadTexture(tex, desc)


class DeferredShading(ComputeStage):
    """The compute pass and what it binds (module docstring); ``color`` and ``alpha`` are written by name."""

    def __init__(self, rhi, stage, tables: Tables) -> None:
        self.tables = tables
        self.fixed: dict[str, object] = {}  # the tables, the sky cube, the lights' buffers, the stand-in textures
        self._sh: tuple = ()
        self.cube_log2 = math.log2(CUBE_SIZE)
        self._clusters = None
        super().__init__(rhi, stage, stand_in=self._stand, raw=B.bufferLoad, address="CLAMP")

    def _texture(self, batch, size, fmt, levels=None, flags=None, layers: int = 1):
        flags = QRhiTexture.Flag(0) if flags is None else flags
        if layers > 1:
            tex = self.rhi.newTextureArray(fmt, layers, QSize(*size), 1, flags)
        else:
            tex = self.rhi.newTexture(fmt, QSize(*size), 1, flags)
        tex.create()
        if levels is not None:
            _upload(batch, tex, levels)
        self.res.keep.append(tex)
        return tex

    def _stand(self, batch, name: str, kind: str):
        """What the shading binds itself: its tables, the sky cube, the light buffers and a texture it fills
        (``WHITE`` reads 1); None for the rest (``gamebind``'s defaults)."""
        if kind == "texture":
            if name == "cPrefilteredSpecularCube" and "cube" in self.fixed:
                return self.fixed["cube"]
            if name not in self.fixed:
                self.fixed[name] = self._table(batch, name)
            return self.fixed[name]
        return self._raw(name) if kind in RAW else None

    def _table(self, batch, name: str):
        """A table of the install, or a 1 x 1 stand-in of the declared kind (``WHITE``: 1, else 0)."""
        t = self.tables
        if name == "cPrefilteredBRDF" and t.brdf:
            flags = QRhiTexture.Flag.MipMapped
            levels = [[lv] for lv in mipchain.full_array_levels(t.brdf)]
            return self._texture(batch, t.brdf[0].shape[1::-1], F.RGBA16F, levels, flags)
        if name in ("cLTC0Tex", "cLTC1Tex") and len(t.ltc) == 2:
            img = t.ltc[name == "cLTC1Tex"]
            return self._texture(batch, img.shape[1::-1], F.RGBA16F, [[img]])
        if name == "cBlueNoiseTex" and t.noise is not None:
            return self._texture(batch, t.noise.shape[1::-1], F.RGBA16F, [[t.noise]])
        declared = self.stage.types.get(name, "Texture2D")
        tex = stand_in(self.rhi, "texture", declared, batch, 1.0 if name in WHITE else 0.0)
        self.res.keep.append(tex)
        return tex

    def _cube(self, batch, sky: Sky) -> None:
        """The sky cube and the light buffer of the look's light (the fallback of the game's sky passes), made
        again when the light changes."""
        sh = tuple(map(tuple, sky.sh))
        if sh == self._sh and "cube" in self.fixed:
            return
        flags = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.MipMapped
        self.fixed["cube"] = self._texture(batch, (CUBE_SIZE, CUBE_SIZE), F.RGBA16F, sky_cube(sh), flags)
        batch.uploadStaticBuffer(self._raw("cLightBuffer"), gamesky.light_bytes(sky.sh, sky.sun_diffuse))
        self._sh = sh
        self.res.invalidate()

    def _raw(self, name: str):
        """A raw buffer the shading fills itself, at its full size (the cells; 2048 point lights; a zeroed one for
        another name), made once."""
        if name not in self.fixed:
            size = {"cLightClusterBuffer": CLUSTER_BYTES, "cPointLights": POINT_BYTES,
                    "cLightBuffer": gamesky.LIGHT_BYTES}.get(name, 4096)  # fmt: skip
            self.fixed[name] = storage_buffer(self.rhi, size)
            self.res.keep.append(self.fixed[name])
        return self.fixed[name]

    def _lights(self, batch, clusters) -> None:
        """The clustered lights uploaded when they change (none: every cell empty)."""
        if clusters is self._clusters and clusters is not None:
            return
        cells = clusters.cells if clusters is not None else np.zeros(CLUSTER_BYTES // 4, np.uint32)
        batch.uploadStaticBuffer(self._raw("cLightClusterBuffer"), cells.tobytes().ljust(CLUSTER_BYTES, bytes(1)))
        if clusters is not None:
            points = clusters.points.tobytes()[:POINT_BYTES].ljust(POINT_BYTES, bytes(1))
            batch.uploadStaticBuffer(self._raw("cPointLights"), points)
            index = self.fixed.get("cLightIndexTex")
            if index is None or index.pixelSize().width() != clusters.indices.shape[1]:
                self.fixed["cLightIndexTex"] = index = self._texture(batch, clusters.indices.shape[::-1], F.R32UI)
                self.res.invalidate()  # rebinds with the new table
            batch.uploadTexture(index, QRhiTextureUploadDescription(QRhiTextureUploadEntry(
                0, 0, QRhiTextureSubresourceUploadDescription(clusters.indices.tobytes()))))  # fmt: skip
        self._clusters = clusters

    def prepare(self, batch, inputs: dict, camera: gs.Camera, sky: Sky, extras: Extras | None = None) -> None:
        """Bind ``inputs`` (name -> ``QRhiTexture``: the G-buffer, ``cDepth``, ``cDstColor``, ``cDstAlpha``, the
        ``cShadowMap`` of the cascades; the ``cShadowInformation`` buffer) and write this frame's constants and
        ``extras`` into ``batch``."""
        extras = extras or Extras()
        self._cube(batch, sky)
        self.cube_log2 = math.log2(inputs["cPrefilteredSpecularCube"].pixelSize().width()) if (
            "cPrefilteredSpecularCube" in inputs) else math.log2(CUBE_SIZE)  # fmt: skip
        self._lights(batch, extras.clusters)
        super().prepare(batch, inputs, lambda c: self.fill(c, camera, sky, extras))

    def fill(self, c: gs.Constants, camera: gs.Camera, sky: Sky, extras: Extras) -> None:
        """This frame's light into a constant buffer by name: the camera, the sun and the sky light, the cascades,
        the spot and area lights (the shading's own buffers and a transparent draw's alike)."""
        self._fill(c, camera, sky)
        gameshadow.fill(c, extras.cascades)
        c.set("cGlobalFlags", 0)
        if extras.clouds is not None:  # GCF_CLOUDS: the clouds' shadow on what is lit
            extras.clouds.fill(c, camera)
        if extras.clusters is not None:  # the cells' counts say how much of each list is read
            c.set("cSpotLights", extras.clusters.spots)
            c.set("cAreaLights", extras.clusters.areas)

    def shared(self, inputs: dict) -> dict:
        """What a forward-lit draw reads of this shading, by name: its tables, sky cube and stand-ins, the light
        buffers and index texture, the shadow map and the cascades' information (not the G-buffer: the
        transparent pass draws into the lit colour over that depth)."""
        out = {("cPrefilteredSpecularCube" if k == "cube" else k): v for k, v in self.fixed.items()}
        kept = ("cShadowMap", "cShadowInformation", "cLightBuffer", "cPrefilteredSpecularCube")
        out.update({k: inputs[k] for k in kept if k in inputs})
        return out

    def _fill(self, c: gs.Constants, camera: gs.Camera, sky: Sky) -> None:
        gs.fill_frame(c, camera)
        w, h = camera.size
        gs.fill_sun(c, sky.sun, sky.night)
        for key, value in (("cAmbientLightFactor", sky.ambient), ("cAmbientLightFactorOcean", sky.ambient),
                           ("cNoRTAmbientFactor", 0.7), ("cAOIntensity", 0.8), ("cSOIntensity", 0.75),
                           ("cPrefilteredSpecCubeSizeLog2", self.cube_log2), ("cGlowBrightness", 1.5),
                           ("cViewport", [0, 0, w, h]), ("cGBufferSize", [w, h]),
                           ("cNumTiles", [groups(w, TILE), groups(h, TILE)])):  # fmt: skip
            c.set(key, value)  # the light itself is the raw cLightBuffer (gamesky; the look's on the CPU)

    def tiles(self, size: tuple[int, int]) -> tuple[int, int, int]:
        """The groups of tiles that cover a view of ``size`` pixels."""
        return groups(size[0], TILE), groups(size[1], TILE), 1

    def release(self) -> None:
        super().release()
        self.fixed = {}
        self._sh, self._clusters = (), None
