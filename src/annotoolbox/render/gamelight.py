"""Anno 117's deferred shading on QRhi: the compute pass of ``deferred_shading.fx`` (``csDeferredShading``, the uber
technique) over ``gameframe``'s G-buffer, writing the lit colour the frame shows.

``CDeferredShading(rhi, stage, tables)`` is the entry (a compiled compute ``gamedraw.SStage``, the engine's ``STables``
from the install); ``prepare(batch, inputs, camera, sky)`` before the pass, ``dispatch(command_buffer, size_px)`` inside it. The
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
from .gamebind import RAW, keyed
from .gamecompute import CComputeStage, groups, storage_buffer
from .gamedraw import stand_in

Binding, TextureFormat = QRhiShaderResourceBinding, QRhiTexture.Format
TILE = 16  # DEFERRED_SHADING_TILE_SIZE_X / _Y of the shipped variant
CUBE_SIZE = 32  # the stand-in sky cube's faces
CLUSTER_BYTES = 32 * 16 * 16 * 8  # cLightClusterBuffer: a uint2 per cell
POINT_BYTES = 2048 * 48  # cPointLights: three float4 per light
WHITE = ("cSSSTex", "cShadowWeatherMap", "cTransmittanceMap")  # what reads neutral as 1 (no shadow, clear air)
# a cube face's direction for texel (u, v) in -1..1, v down: +X, -X, +Y, -Y, +Z, -Z (Direct3D's order)
FACES = (lambda u, v: (1, -v, -u), lambda u, v: (-1, -v, u), lambda u, v: (u, 1, v), lambda u, v: (u, -1, -v),
         lambda u, v: (u, -v, 1), lambda u, v: (-u, -v, -1))  # fmt: skip


@dataclass
class SExtras:
    """What a frame adds to the shading: the sun's cascades (``gameshadow.SCascades``, with the ``cShadowMap`` among
    the inputs), the clustered lights (``lightclusters.SClusters``) and the clouds' shadow (``gameclouds.CClouds``,
    its maps among the inputs)."""

    cascades: object = None
    clusters: object = None
    clouds: object = None


@dataclass
class STables:
    """The engine's tables from the install, as float RGBA arrays: the split-sum BRDF table (its levels), the area
    lights' two LTC tables, the blue noise."""

    brdf: list = field(default_factory=list)
    ltc: list = field(default_factory=list)
    noise: np.ndarray | None = None


@dataclass(frozen=True)
class SSky:
    """The light of a frame in the scene's axes: the sun's direction (towards it) and ``LgtDiffuse`` (radiance over
    pi), the sky's nine spherical-harmonic coefficients (raw radiance, rgb), the ambient factor."""

    sun: tuple
    sun_diffuse: tuple
    sh: tuple
    ambient: float = 1.0
    night: bool = False  # the game's sky passes light the night by the moon (cDayNight)


def sh_radiance(harmonics, dirs: np.ndarray) -> np.ndarray:
    """The sky's radiance along unit directions ``dirs`` [n, 3] from raw SH coefficients (``EvalSH9``'s basis)."""
    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    basis = np.stack([np.full_like(x, 0.282095), 0.488603 * y, 0.488603 * z, 0.488603 * x, 1.092548 * x * y,
                      1.092548 * y * z, 0.315392 * (3 * z * z - 1), 1.092548 * x * z, 0.546274 * (x * x - y * y)], 1)  # fmt: skip
    return np.maximum(basis @ np.asarray(harmonics, np.float64)[:, :3], 0.0)


def sky_cube(harmonics, size_px: int = CUBE_SIZE) -> list[list[np.ndarray]]:
    """The prefiltered specular cube's stand-in: the SH sky as six faces of float RGBA per level (every level the
    same light: the SH holds no detail a sharper level could show)."""
    levels = []
    for level in range(int(math.log2(size_px)) + 1):
        side = max(1, size_px >> level)
        axis_values = (np.arange(side) + 0.5) / side * 2 - 1
        coord_u, coord_v = np.meshgrid(axis_values, axis_values)
        faces = []
        for face in FACES:
            directions = np.stack(
                [np.broadcast_to(np.asarray(component, float), coord_u.shape) for component in face(coord_u, coord_v)],
                -1,
            ).reshape(-1, 3)
            directions /= np.linalg.norm(directions, axis=1, keepdims=True)
            rgb = sh_radiance(harmonics, directions).reshape(side, side, 3)
            faces.append(np.concatenate([rgb, np.ones((side, side, 1))], -1).astype(np.float16))
        levels.append(faces)
    return levels


def _upload(batch, texture, levels: list[list[np.ndarray]]) -> None:
    """Float16 RGBA layers per level into ``texture`` (a layer is a cube face or the one image)."""
    entries = [QRhiTextureUploadEntry(layer, level, QRhiTextureSubresourceUploadDescription(image.tobytes()))
               for level, layers in enumerate(levels) for layer, image in enumerate(layers)]  # fmt: skip
    description = QRhiTextureUploadDescription()
    description.setEntries(entries)
    batch.uploadTexture(texture, description)


class CDeferredShading(CComputeStage):
    """The compute pass and what it binds (module docstring); ``color`` and ``alpha`` are written by name."""

    def __init__(self, rhi, stage, tables: STables) -> None:
        self.m_tables = tables
        self.m_fixed: dict[str, object] = {}  # the tables, the sky cube, the lights' buffers, the stand-in textures
        self._m_sh: tuple = ()
        self.m_cube_log2 = math.log2(CUBE_SIZE)
        self._m_clusters = None
        self._m_emptied = False  # the cells hold no light (``_lights``): not sent again every frame
        self._m_replaced: list = []  # the texture ``_replace`` last took out, destroyed with the next
        super().__init__(rhi, stage, stand_in=self._stand, raw=Binding.bufferLoad, address="CLAMP")

    def _texture(self, batch, size_px, texture_format, levels=None, flags=None, layers: int = 1):
        flags = QRhiTexture.Flag(0) if flags is None else flags
        if layers > 1:
            texture = self.m_rhi.newTextureArray(texture_format, layers, QSize(*size_px), 1, flags)
        else:
            texture = self.m_rhi.newTexture(texture_format, QSize(*size_px), 1, flags)
        texture.create()
        if levels is not None:
            _upload(batch, texture, levels)
        self.m_res.m_keep.append(texture)
        return texture

    def _stand(self, batch, name: str, kind: str):
        """What the shading binds itself: its tables, the sky cube, the light buffers and a texture it fills
        (``WHITE`` reads 1); None for the rest (``gamebind``'s defaults)."""
        if kind == "texture":
            if name == "cPrefilteredSpecularCube" and "cube" in self.m_fixed:
                return self.m_fixed["cube"]
            if name not in self.m_fixed:
                self.m_fixed[name] = self._table(batch, name)
            return self.m_fixed[name]
        return self._raw(name) if kind in RAW else None

    def _table(self, batch, name: str):
        """A table of the install, or a 1 x 1 stand-in of the declared kind (``WHITE``: 1, else 0)."""
        tables = self.m_tables
        if name == "cPrefilteredBRDF" and tables.brdf:
            flags = QRhiTexture.Flag.MipMapped
            levels = [[lv] for lv in mipchain.full_array_levels(tables.brdf)]
            return self._texture(batch, tables.brdf[0].shape[1::-1], TextureFormat.RGBA16F, levels, flags)
        if name in ("cLTC0Tex", "cLTC1Tex") and len(tables.ltc) == 2:
            image = tables.ltc[name == "cLTC1Tex"]
            return self._texture(batch, image.shape[1::-1], TextureFormat.RGBA16F, [[image]])
        if name == "cBlueNoiseTex" and tables.noise is not None:
            return self._texture(batch, tables.noise.shape[1::-1], TextureFormat.RGBA16F, [[tables.noise]])
        declared = self.m_stage.types.get(name, "Texture2D")
        texture = stand_in(self.m_rhi, "texture", declared, batch, 1.0 if name in WHITE else 0.0)
        self.m_res.m_keep.append(texture)
        return texture

    def _cube(self, batch, sky: SSky) -> None:
        """The sky cube and the light buffer of the look's light (the fallback of the game's sky passes), made
        again when the light changes."""
        sh = tuple(map(tuple, sky.sh))
        if sh == self._m_sh and "cube" in self.m_fixed:
            return
        flags = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.MipMapped
        self._replace("cube", self._texture(batch, (CUBE_SIZE, CUBE_SIZE), TextureFormat.RGBA16F, sky_cube(sh), flags))
        batch.uploadStaticBuffer(self._raw("cLightBuffer"), gamesky.light_bytes(sky.sh, sky.sun_diffuse))
        self._m_sh = sh
        self.m_res.invalidate()

    def _replace(self, name: str, texture):
        """``texture`` as the shading's own ``name`` from now on. The one it replaces goes with the next replacement (a
        binding set of this frame may still hold it): kept for good, every change of the light left a cube behind."""
        for old in self._m_replaced:
            old.destroy()
        old = self.m_fixed.get(name)
        self._m_replaced = [old] if old is not None else []
        if old is not None:
            self.m_res.m_keep = [resource for resource in self.m_res.m_keep if resource is not old]
        self.m_fixed[name] = texture
        return texture

    def _raw(self, name: str):
        """A raw buffer the shading fills itself, at its full size (the cells; 2048 point lights; a zeroed one for
        another name), made once."""
        if name not in self.m_fixed:
            size = {"cLightClusterBuffer": CLUSTER_BYTES, "cPointLights": POINT_BYTES,
                    "cLightBuffer": gamesky.LIGHT_BYTES}.get(name, 4096)  # fmt: skip
            self.m_fixed[name] = storage_buffer(self.m_rhi, size)
            self.m_res.m_keep.append(self.m_fixed[name])
        return self.m_fixed[name]

    def _lights(self, batch, clusters) -> None:
        """The clustered lights uploaded when they change (none: every cell empty, sent once)."""
        if clusters is self._m_clusters and (clusters is not None or self._m_emptied):
            return
        cells = clusters.cells if clusters is not None else np.zeros(CLUSTER_BYTES // 4, np.uint32)
        batch.uploadStaticBuffer(self._raw("cLightClusterBuffer"), cells.tobytes().ljust(CLUSTER_BYTES, bytes(1)))
        if clusters is not None:
            points = clusters.points.tobytes()[:POINT_BYTES].ljust(POINT_BYTES, bytes(1))
            batch.uploadStaticBuffer(self._raw("cPointLights"), points)
            index = self.m_fixed.get("cLightIndexTex")
            if index is None or index.pixelSize().width() != clusters.indices.shape[1]:
                index = self._replace(
                    "cLightIndexTex", self._texture(batch, clusters.indices.shape[::-1], TextureFormat.R32UI)
                )
                self.m_res.invalidate()  # rebinds with the new table
            batch.uploadTexture(index, QRhiTextureUploadDescription(QRhiTextureUploadEntry(
                0, 0, QRhiTextureSubresourceUploadDescription(clusters.indices.tobytes()))))  # fmt: skip
        self._m_clusters, self._m_emptied = clusters, clusters is None

    def prepare(self, batch, inputs: dict, camera: gs.SCamera, sky: SSky, extras: SExtras | None = None) -> None:
        """Bind ``inputs`` (name -> ``QRhiTexture``: the G-buffer, ``cDepth``, ``cDstColor``, ``cDstAlpha``, the
        ``cShadowMap`` of the cascades; the ``cShadowInformation`` buffer) and write this frame's constants and
        ``extras`` into ``batch``."""
        extras = extras or SExtras()
        self._cube(batch, sky)
        self.m_cube_log2 = math.log2(inputs["cPrefilteredSpecularCube"].pixelSize().width()) if (
            "cPrefilteredSpecularCube" in inputs) else math.log2(CUBE_SIZE)  # fmt: skip
        self._lights(batch, extras.clusters)
        fill = keyed(lambda constants: self.fill(constants, camera, sky, extras), self.fill_key(camera, sky, extras))
        super().prepare(batch, inputs, fill)

    def fill_key(self, camera: gs.SCamera, sky: SSky, extras: SExtras) -> tuple:
        """What ``fill`` writes, comparable by content (a keyed fill's: ``gamebind.keyed``)."""
        clouds, clusters = extras.clouds, extras.clusters
        lights = None if clusters is None else (gs.value_key(clusters.spots), gs.value_key(clusters.areas))
        return ("shading", gs.camera_key(camera), gs.value_key((sky.sun, sky.night, sky.ambient)), self.m_cube_log2,
                gameshadow.fill_key(extras.cascades), clouds.moment() if clouds is not None else None, lights)  # fmt: skip

    def fill(self, constants: gs.CConstants, camera: gs.SCamera, sky: SSky, extras: SExtras) -> None:
        """This frame's light into a constant buffer by name: the camera, the sun and the sky light, the cascades,
        the spot and area lights (the shading's own buffers and a transparent draw's alike)."""
        self._fill(constants, camera, sky)
        gameshadow.fill(constants, extras.cascades)
        constants.set("cGlobalFlags", 0)
        if extras.clouds is not None:  # GCF_CLOUDS: the clouds' shadow on what is lit
            extras.clouds.fill(constants, camera)
        if extras.clusters is not None:  # the cells' counts say how much of each list is read
            constants.set("cSpotLights", extras.clusters.spots)
            constants.set("cAreaLights", extras.clusters.areas)

    def shared(self, inputs: dict) -> dict:
        """What a forward-lit draw reads of this shading, by name: its tables, sky cube and stand-ins, the light
        buffers and index texture, the shadow map and the cascades' information (not the G-buffer: the
        transparent pass draws into the lit colour over that depth)."""
        out = {("cPrefilteredSpecularCube" if k == "cube" else k): value for k, value in self.m_fixed.items()}
        kept = ("cShadowMap", "cShadowInformation", "cLightBuffer", "cPrefilteredSpecularCube")
        out.update({k: inputs[k] for k in kept if k in inputs})
        return out

    def _fill(self, constants: gs.CConstants, camera: gs.SCamera, sky: SSky) -> None:
        gs.fill_frame(constants, camera)
        width, height = camera.size_px
        gs.fill_sun(constants, sky.sun, sky.night)
        for key, value in (("cAmbientLightFactor", sky.ambient), ("cAmbientLightFactorOcean", sky.ambient),
                           ("cNoRTAmbientFactor", 0.7), ("cAOIntensity", 0.8), ("cSOIntensity", 0.75),
                           ("cPrefilteredSpecCubeSizeLog2", self.m_cube_log2), ("cGlowBrightness", 1.5),
                           ("cViewport", [0, 0, width, height]), ("cGBufferSize", [width, height]),
                           ("cNumTiles", [groups(width, TILE), groups(height, TILE)])):  # fmt: skip
            constants.set(key, value)  # the light itself is the raw cLightBuffer (gamesky; the look's on the CPU)

    def tiles(self, size_px: tuple[int, int]) -> tuple[int, int, int]:
        """The groups of tiles that cover a view of ``size_px`` pixels."""
        return groups(size_px[0], TILE), groups(size_px[1], TILE), 1

    def release(self) -> None:
        super().release()
        for old in self._m_replaced:
            old.destroy()
        self.m_fixed, self._m_replaced = {}, []
        self._m_sh, self._m_clusters, self._m_emptied = (), None, False
