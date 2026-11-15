"""Anno 117's volumetric clouds by the game's own shaders (``environment.fx``): the engine's noise volumes, the
ambient's weather map, the layer's constants as the engine sets them, the self-shadow cache and the clouds'
transmittance in the sun's cascades (the ground's cloud shadow).

``Clouds(rhi, stages, read_maps(files, ambient))`` is the entry; ``update`` records the cache before the clouds'
pass, ``shadow`` the cascades' transmittance before the shading, ``inputs()`` / ``fill(c, camera)`` hand textures and
constants by name to the passes. docs/rendering/frame-117-viewer.md section 12.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from ..models import ddsdecode, fileresolve
from . import gameshader as gs
from . import gameshadow, mipchain
from .gamecompute import ComputeChain, ComputeStage, groups, record
from .gamedraw import dds_texture, stand_in
from .gamesky import block, block_number, block_vector

F = QRhiTexture.Format
EFFECT = "data/shaders/fx/environment.fx"
STAGES = {"cache": (EFFECT, "UpdateSelfShadowCache", "csUpdateSelfShadowCache"),
          "shadow": (EFFECT, "VolumeShadowCompute", "csVolumetricShadow"),
          "reflected": ("data/shaders/fx/sky.fx", "AddReflectedClouds", "csAddReflectedClouds")}  # fmt: skip
ENGINE = "data/base/graphics/engine/clouds/"
VOLUMES = {"cCloudBaseNoise": f"{ENGINE}base_noise.dds", "cCloudErosionNoise": f"{ENGINE}erosion_noise.dds",
           "cCloudCurlNoise": f"{ENGINE}curl_noise.dds"}  # fmt: skip
TYPES = f"{ENGINE}cloud_types.dds"  # the height profile of each of the six cloud types
SETTINGS = "data/base/config/engine/enginesettings/default.xml"
GCF_CLOUDS = 1 << 5  # shared_constants.fxh
# the engine's values, read off update 3.0 (frame-117-viewer.md 12): written by its environment, no file holds them
DOME_RADIUS = 200000.0  # cCloudDomeRadius
WEATHERMAP_SCALE = 1250.0  # cWeathermapScale: the world units one weather map covers
LAYER_HEIGHT = 208.0  # cCloudLayerHeight: where the weather map's shadow is projected from
CURL_INTENSITY = 7.0  # cCloudCurlIntensity
CACHE = (128, 128, 16)  # the self-shadow cache: SSC_TILEDIMS x SSC_TILEDIMS x SSC_NUMSLICES
CACHE_GROUP = (2, 2, 8)  # csUpdateSelfShadowCache's numthreads
SHADOW_GROUP = 8  # csVolumetricShadow's numthreads (x, y)
SHADOW_SIZE = 1024  # the cascades' transmittance map: ours, the engine's size is a setting [E]
FAR_PLANE = 750.0  # the camera's far plane while clouds are drawn: ViewDistance of the top quality level [I]
# the environment's wind (frame-117-viewer.md 12 "The wind and the scroll"): the gust's amplitude and pace, its
# constant factor, the strength's cap, the noise's and the curl's own pace
GUST, GUST_PACE, GUST_FACTOR = 0.2, 0.4, 0.5 * np.sin(0.37) * np.sin(0.079) + 0.5
MAX_STRENGTH, NOISE_PACE, CURL_PACE = 4.0, 0.75, 0.1
STEP = 1 / 30  # the scroll integrated in frames of this length (the engine adds one per frame)


@dataclass(frozen=True)
class Wind:
    """The session's wind: its angle (radians) and strength. The strength is the ambient's ``WindStrength``
    (``session_wind``); the angle is the weather's at run time, which a model view does not have: 0 [E]."""

    angle: float = 0.0
    strength: float = 1.0


CALM = Wind()  # the viewer's stand-in for a session's wind


def session_wind(ambient: dict) -> Wind:
    """The wind of an ambient's values (``read_ambient``): its ``WindStrength``, which the environment multiplies the
    gust by (the blended ambient's field, frame-117-viewer.md 12 "The wind and the scroll"), the angle 0."""
    return Wind(CALM.angle, float(ambient.get("wind_strength", CALM.strength)))


def wind_drift(seconds: np.ndarray, wind: Wind) -> tuple[np.ndarray, np.ndarray]:
    """(the wind's direction (x, z), the clouds' drift in world units a second at each of ``seconds``)."""
    u = 100.0 * np.fmod(np.asarray(seconds, float) * 1000.0 / 50000.0, 40 * np.pi)
    v = GUST_PACE * u
    gust = GUST * np.sin(v - 0.5 * np.sin(v)) * GUST_FACTOR
    strength = np.clip((gust + 1.0) * wind.strength, 0.0, MAX_STRENGTH)
    direction = np.array([np.cos(wind.angle), np.sin(wind.angle)])
    return direction, 2.0 * strength[..., None] * direction


@lru_cache(maxsize=8)  # every pass of a frame asks for the same moment
def scroll(seconds: float, wind: Wind = CALM) -> dict:
    """``cWeatherScroll``, ``cCloudNoiseScroll`` and ``cCloudCurlScroll`` ``seconds`` into a view, as the engine
    adds them up frame by frame (read only: the result is shared)."""
    steps = np.arange(0.0, max(0.0, seconds), STEP)
    dt = np.diff(np.append(steps, max(0.0, seconds)))
    direction, drift = wind_drift(steps, wind)
    moved = (drift * dt[:, None]).sum(0) if len(dt) else np.zeros(2)
    time = float(dt.sum())
    out = {"cWeatherScroll": list(np.fmod(moved, WEATHERMAP_SCALE) / WEATHERMAP_SCALE)}
    for name, pace in (("cCloudNoiseScroll", NOISE_PACE), ("cCloudCurlScroll", CURL_PACE)):
        out[name] = [pace * direction[0] * time + moved[0], pace * time, pace * direction[1] * time + moved[1]]
    return out


@dataclass
class CloudMaps:
    """What the clouds read from the install: the volumes (per level a ``(depth, height, width[, 4])`` array), the
    types' profiles, the weather map (a ``ddsdecode.DdsFile``, uploaded as stored), the ambient's values and the far
    plane."""

    volumes: dict = field(default_factory=dict)
    types: np.ndarray | None = None
    weather: object = None
    ambient: dict = field(default_factory=dict)
    far: float = FAR_PLANE  # the engine's far plane, where a ray that meets nothing ends


def _volume(dds: ddsdecode.DdsFile, floats: bool) -> list[np.ndarray]:
    """Every level of a volume, slice by slice: its first channel as bytes, or RGBA as half floats."""
    out = []
    for mip in range(dds.info.mips):
        if floats:
            out.append(np.stack([dds.level_float(mip, 0, z) for z in range(dds.slices(mip))]).astype("f2"))
        else:
            out.append(np.stack([dds.level(mip, 0, z)[..., 0] for z in range(dds.slices(mip))]))
    return out


def read_ambient(text: str) -> dict:
    """The clouds' values of an ambient file: the shadow's strength by day and night, the albedo, the planar clouds'
    altitude, the shadow's cut-off height, the weather map's name and the wind's strength."""
    return {"cCloudShadowIntensityDay": block_number(text, "CloudShadowIntensity", 0.0),
            "cCloudShadowIntensityNgt": block_number(text, "CloudShadowIntensityNgt", 0.0),
            "cCloudAlbedo": block_vector(text, "CloudAlbedo", (1.0, 1.0, 1.0)),
            "cPlanarCloudAltitude": block_number(text, "PlanarCloudAltitude", 0.0),
            "cShadowCutoffHeight": block_number(text, "ShadowCutoffHeight", 0.0),
            "weather": block(text, "WeatherMap").strip().replace("\\", "/"),
            "wind_strength": block_number(text, "WindStrength", CALM.strength)}  # fmt: skip


def far_plane(text: str) -> float:
    """The top quality level's ``ViewDistance`` of the engine settings, ``FAR_PLANE`` without one."""
    found = [float(v) for v in re.findall(r"<QualityLevel\d>.*?<ViewDistance>([\d.]+)<", text, re.S)]
    return found[-1] if found else FAR_PLANE


def read_maps(files, ambient_text: str) -> CloudMaps:
    """The engine's volumes and profiles, the ambient's weather map and the settings' far plane from ``files``
    (``read`` and ``exists``)."""
    ambient = read_ambient(ambient_text)
    volumes = {name: _volume(ddsdecode.DdsFile(files.read(path)), name == "cCloudCurlNoise")
               for name, path in VOLUMES.items()}  # fmt: skip
    types = ddsdecode.DdsFile(files.read(TYPES)).level(0)[..., 0]
    path = fileresolve.texture_file(ambient["weather"], files.exists) if ambient["weather"] else ""
    weather = ddsdecode.DdsFile(files.read(path)) if path else None
    far = far_plane(files.read(SETTINGS).decode("utf-8", "replace")) if files.exists(SETTINGS) else FAR_PLANE
    return CloudMaps(volumes, types, weather, ambient, far)


def constants(camera: gs.Camera, ambient: dict, seconds: float = 0.0, wind: Wind = CALM) -> dict:
    """The clouds' constants by name for a view from ``camera`` ``seconds`` into it: the engine's layer, the
    ambient's light, the cache centred under the camera, the weather and the noise moved by ``wind``."""
    values = {key: value for key, value in ambient.items() if key.startswith("c")}
    return {**values, "cWeathermapScale": WEATHERMAP_SCALE, "cCloudLayerHeight": LAYER_HEIGHT,
            "cCloudDomeRadius": DOME_RADIUS, "cCloudCurlIntensity": CURL_INTENSITY, "cCloudBlendFactor": 0.0,
            "cNumRegionalWeatherMaps": 0, "cFrustumPos": [camera.eye[0], camera.eye[2]], **scroll(seconds, wind),
            "cSSCUpdateFactor": 1.0}  # fmt: skip


def _upload(batch, tex, levels: list[np.ndarray]) -> None:
    """Each level of a volume (``(depth, height, width[, 4])``; a flat texture has one slice) into ``tex``."""
    entries = []
    for mip, level in enumerate(levels):
        for z, img in enumerate(level):
            sub = QRhiTextureSubresourceUploadDescription(np.ascontiguousarray(img).tobytes())
            entries.append(QRhiTextureUploadEntry(z, mip, sub))
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    batch.uploadTexture(tex, desc)


class Clouds(ComputeChain):
    """The clouds' maps, the self-shadow cache and the cascades' transmittance (module docstring)."""

    def __init__(self, rhi, stages: dict, maps: CloudMaps) -> None:
        self.rhi, self.maps = rhi, maps
        self.ambient = maps.ambient
        self.reflected = stages.get("reflected")  # the clouds in the sky cube: run by the sky passes (gamesky)
        self.stages = {"cache": ComputeStage(rhi, stages["cache"], stand_in=self._stand)}
        for k in range(gameshadow.COUNT):  # a uniform buffer per cascade: one dispatch each
            self.stages[f"shadow{k}"] = ComputeStage(rhi, stages["shadow"], self.stages.get("shadow0"),
                                                     stand_in=self._stand)  # fmt: skip
        self.textures: dict = {}
        self._pending = True  # the maps go up with the first pass
        self.cache = self._texture(F.R16, CACHE, storage=True)
        self.transmittance = self._texture(F.RG8, (SHADOW_SIZE, SHADOW_SIZE), storage=True)
        self.shadowed = False  # whether the transmittance holds this frame's cascades
        self.seconds, self.wind = 0.0, session_wind(maps.ambient)  # the view's time and the wind (the scroll)
        self._white = None

    def _texture(self, fmt, size: tuple, levels: int = 1, storage: bool = False):
        flags = QRhiTexture.Flag.UsedWithLoadStore if storage else QRhiTexture.Flag(0)
        if levels > 1:
            flags |= QRhiTexture.Flag.MipMapped
        if len(size) == 3:
            tex = self.rhi.newTexture(fmt, *size, 1, flags | QRhiTexture.Flag.ThreeDimensional)
        else:
            tex = self.rhi.newTexture(fmt, QSize(*size), 1, flags)
        tex.create()
        return tex

    def _stand(self, batch, name: str, kind: str):
        """The cascades' transmittance reads 1 (clear air) until the shadow pass wrote it; None for the rest."""
        if kind != "texture" or name != "cTransmittanceMap":
            return None
        if self._white is None:
            self._white = stand_in(self.rhi, "texture", "Texture2D", batch, 1.0)
        return self._white

    def _upload_maps(self, batch) -> None:
        """The volumes, the profiles and the weather map, once."""
        m = self.maps
        for name, stored in m.volumes.items():
            levels = mipchain.full_array_levels(stored, volume=True) if len(stored) > 1 else stored
            depth, height, width = levels[0].shape[:3]
            fmt = F.RGBA16F if levels[0].ndim == 4 else F.R8
            self.textures[name] = tex = self._texture(fmt, (width, height, depth), len(levels))
            _upload(batch, tex, levels)
        self.textures["cCloudTypes"] = tex = self._texture(F.R8, m.types.shape[::-1])
        _upload(batch, tex, [m.types[None]])
        if m.weather is not None:
            tex, desc = dds_texture(self.rhi, m.weather)
            batch.uploadTexture(tex, desc)
            for name in ("cCloudWeatherTexture", "cCloudWeatherTextureBlend", "cShadowWeatherMap"):
                self.textures[name] = tex
        self._pending = False

    def inputs(self) -> dict:
        """The clouds' textures by the names the passes read (the cascades' transmittance once it was made)."""
        out = {**self.textures, "cSelfShadowCacheRO": self.cache}
        if self.shadowed:
            out["cTransmittanceMap"] = self.transmittance
        return out

    def fill(self, c: gs.Constants, camera: gs.Camera | None) -> None:
        """The clouds' constants and ``GCF_CLOUDS`` into a constant buffer by name (``camera`` None: the sky cube's,
        which looks from the ground at the world's origin)."""
        c.set("cGlobalFlags", GCF_CLOUDS)
        camera = camera or gs.Camera(np.zeros(3), np.identity(4), np.identity(4), 1.0, 1.0, (1, 1))
        for key, value in constants(camera, self.ambient, self.seconds, self.wind).items():
            c.set(key, value)

    def maps_ready(self) -> bool:
        """Whether the volumes and the weather map are up (the sky passes read them after a frame's clouds)."""
        return not self._pending

    def _fill(self, c: gs.Constants, camera: gs.Camera, sky) -> None:
        gs.fill_frame(c, camera)
        gs.fill_sun(c, sky.sun, sky.night)
        self.fill(c, camera)

    def update(self, ctx, camera: gs.Camera, sky, inputs: dict) -> None:
        """The self-shadow cache around the camera, made anew (``cSSCUpdateFactor`` 1: no history to blend)."""

        def before(batch) -> None:
            if self._pending:
                self._upload_maps(batch)

        named = {**inputs, **self.inputs(), "cSelfShadowCacheRW": self.cache}
        grid = tuple(groups(n, g) for n, g in zip(CACHE, CACHE_GROUP, strict=True))
        record(ctx, self.stages["cache"], named, lambda c: self._fill(c, camera, sky), grid, before=before)

    def shadow(self, ctx, camera: gs.Camera, sky, cascades, inputs: dict) -> None:
        """The clouds' transmittance along the sun into each used cascade's part of the map (``inputs``: the shadow
        map and the cascades' information, the blue noise); a cascade the quality leaves out is read by nothing."""
        named = {**inputs, **self.inputs(), "cVolumetricShadowTarget": self.transmittance}
        part = groups(SHADOW_SIZE // 2, SHADOW_GROUP)  # each cascade a quarter of the map (gameshadow.DIVISIONS)

        def before(batch) -> None:
            if self._pending:
                self._upload_maps(batch)

        for k in range(min(gameshadow.COUNT, cascades.used)):

            def fill(c: gs.Constants, k=k) -> None:
                self._fill(c, camera, sky)
                gameshadow.fill(c, cascades)
                c.set("cCurrentCascade", k)

            record(ctx, self.stages[f"shadow{k}"], named, fill, (part, part, 1), before=before if k == 0 else None)
        self.shadowed = True

    def release(self) -> None:
        super().release()
        for tex in {id(t): t for t in (*self.textures.values(), self.cache, self.transmittance, self._white)
                    if t is not None}.values():  # fmt: skip
            tex.destroy()
        self.textures, self._white, self._pending = {}, None, True
