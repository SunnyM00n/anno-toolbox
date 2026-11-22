"""The sky's light and the clouds' maps by the game's shipped shaders on the native backend's own command list, ahead of
QRhi's frame: ``sky.fx``'s tables, the sun's light, the sky cube with its clouds, its spherical harmonics and the
prefiltered cube (``gamesky``'s chain), and ``environment.fx``'s self-shadow cache with the engine's cloud volumes and
the ambient's weather map (``gameclouds``').

``CNativeSky(device, natives, handover, atmosphere)`` is the entry, one per view; ``run(sky, clouds, camera, shared)``
records what changed (``clouds``: the view's ``gameclouds.CClouds``); ``light``, ``cube``, ``clouds`` ... hold the
results, ``qrhi_cube`` and ``qrhi_clouds`` QRhi's wrappers of them. docs/rendering/native-backend.md section 12.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameclouds, gamesky, mipchain, nativecompute, nativeforeign
from . import gameshader as gs
from .bakenative import CStored
from .gamecompute import groups

SKY = "sky"
ENVIRONMENT = "environment"
CALLS = {**{key: (SKY, fn) for key, (_e, _t, fn) in gamesky.STAGES.items()}, "clouds": (SKY, "csAddReflectedClouds"),
         "cache": (ENVIRONMENT, "csUpdateSelfShadowCache")}  # fmt: skip
BINDLESS = "cBindlessTextures"  # csAddReflectedClouds' regional weather maps: none (cNumRegionalWeatherMaps 0)
TextureFormat = QRhiTexture.Format
QRHI_FORMATS = {"R8_UNORM": TextureFormat.R8, "RG8_UNORM": TextureFormat.RG8, "R16_UNORM": TextureFormat.R16, "RGBA16_FLOAT": TextureFormat.RGBA16F,
                "RGBA8_UNORM": TextureFormat.RGBA8, "BC1_UNORM": TextureFormat.BC1, "BC2_UNORM": TextureFormat.BC2, "BC3_UNORM": TextureFormat.BC3,
                "BC4_UNORM": TextureFormat.BC4, "BC5_UNORM": TextureFormat.BC5, "BC6H_UFLOAT": TextureFormat.BC6H, "BC7_UNORM": TextureFormat.BC7}  # fmt: skip


def shaders(natives) -> dict:
    """The chain's shaders of the shipped variants by ``CALLS``' keys, each reflected (in the background)."""
    out = {}
    for key, (stem, function) in CALLS.items():
        out[key] = natives.call(natives.variant(stem), function)
        natives.reflection(out[key])
    return out


def give(handover, texture, texture_format: str, size_px: tuple, flags=None):
    """A QRhi texture over the module's ``texture`` (a volume too: ``size_px`` of three), tracked from shader reads."""
    q_fmt = QRHI_FORMATS[texture_format.removesuffix("_SRGB")]
    flags = flags if flags is not None else QRhiTexture.Flag(0)
    if len(size_px) == 2:
        return handover.give(texture, q_fmt, size_px, flags=flags)
    qrhi_texture = handover.m_rhi.newTexture(q_fmt, *size_px, 1, flags | QRhiTexture.Flag.ThreeDimensional)
    if not handover.m_module.qrhi_create_from(
        nativecompute.pointer(qrhi_texture), texture.native, nativecompute.SHADER_READ
    ):
        raise RuntimeError(f"QRhi did not take the module's volume over ({texture_format}, {size_px})")
    handover.m_given[id(qrhi_texture)] = (qrhi_texture, texture)
    return qrhi_texture


class CNativeSky:
    """The sky's light and the clouds' maps (module docstring)."""

    def __init__(self, device, natives, handover, atmosphere: dict) -> None:
        self.m_device, self.m_handover, self.m_atmosphere = device, handover, atmosphere
        self.m_description = None  # the view's gameclouds.CClouds this frame (None: no clouds)
        self.m_list = device.command_list()
        self.m_list.open()
        found = shaders(natives)
        self.m_kernels = {
            key: nativecompute.CKernel.compute(device, source, natives.reflection(source))
            for key, source in found.items()
        }
        self.m_reflections = {key: natives.reflection(source) for key, source in found.items()}
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list)
        gpu_device = device
        uav = "uav"
        self.m_transmittance = gpu_device.texture(
            *gamesky.TRANSMITTANCE, "RGBA16_FLOAT", usage=uav, name="transmittance"
        )
        self.m_multiscat = gpu_device.texture(*gamesky.MULTISCAT, "RGBA16_FLOAT", usage=uav, name="multiscat")
        self.m_targets = [gpu_device.texture(gamesky.CUBE >> k, gamesky.CUBE >> k, "RGBA16_FLOAT", usage=uav, name="sky level")
                        for k in range(gamesky.LEVELS)]  # fmt: skip
        cube = {"depth": 6, "mips": gamesky.LEVELS, "kind": "cube"}
        self.m_sky = gpu_device.texture(gamesky.CUBE, gamesky.CUBE, "RGBA16_FLOAT", **cube, name="sky cube")
        self.m_cube = gpu_device.texture(gamesky.CUBE, gamesky.CUBE, "RGBA16_FLOAT", **cube, name="prefiltered cube")
        self.m_light = gpu_device.buffer(
            gamesky.LIGHT_BYTES, "structured,uav", stride=gamesky.LIGHT_BYTES, name="light data"
        )
        flags = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.MipMapped
        self.m_qrhi_cube = give(handover, self.m_cube, "RGBA16_FLOAT", (gamesky.CUBE, gamesky.CUBE), flags)
        self.m_clouds: dict = {}  # the clouds' textures by the shaders' names (``_upload_maps``)
        self.m_qrhi_clouds: dict = {}  # QRhi's wrappers of them, for the passes still on QRhi
        self.m_table = None  # csAddReflectedClouds' bindless table of stand-ins
        self._m_done: tuple = ()

    def _upload_maps(self, command_list) -> None:
        """The engine's volumes and profiles, the ambient's weather map and the self-shadow cache, once; QRhi's
        wrappers of them for its passes (the transparent pass's cloud shadow)."""
        maps, gpu_device = self.m_description.m_maps, self.m_device
        for name, stored in maps.volumes.items():
            levels = mipchain.full_array_levels(stored, volume=True) if len(stored) > 1 else stored
            depth, height, width = levels[0].shape[:3]
            texture_format = "RGBA16_FLOAT" if levels[0].ndim == 4 else "R8_UNORM"
            texel = 8 if texture_format == "RGBA16_FLOAT" else 1
            texture = gpu_device.texture(
                width, height, texture_format, depth=depth, mips=len(levels), kind="3d", name=name
            )
            for mip, level in enumerate(levels):
                raw = np.ascontiguousarray(level).tobytes()
                command_list.write_texture(
                    texture, raw, level.shape[2] * texel, mip=mip, depth_pitch=level.shape[1] * level.shape[2] * texel
                )
            self._keep(name, texture, texture_format, (width, height, depth))
        types = np.ascontiguousarray(maps.types, np.uint8)
        texture = gpu_device.texture(types.shape[1], types.shape[0], "R8_UNORM", name="cloud types")
        command_list.write_texture(texture, types.tobytes(), types.shape[1])
        self._keep("cCloudTypes", texture, "R8_UNORM", types.shape[::-1])
        if maps.weather is not None:
            stored = CStored(maps.weather)
            texture = stored.upload(gpu_device, command_list, "weather map")
            qrhi_texture = give(self.m_handover, texture, stored.m_format, (stored.m_width, stored.m_height))
            for name in ("cCloudWeatherTexture", "cCloudWeatherTextureBlend", "cShadowWeatherMap"):
                self.m_clouds[name], self.m_qrhi_clouds[name] = texture, qrhi_texture
        cache = gpu_device.texture(*gameclouds.CACHE[:2], "R16_UNORM", depth=gameclouds.CACHE[2], kind="3d", usage="uav",
                          name="self-shadow cache")  # fmt: skip
        self._keep("cSelfShadowCacheRO", cache, "R16_UNORM", gameclouds.CACHE)
        shadow = (gameclouds.SHADOW_SIZE, gameclouds.SHADOW_SIZE)
        transmittance = gpu_device.texture(*shadow, "RG8_UNORM", usage="uav", name="cloud transmittance")
        self._keep("cTransmittanceMap", transmittance, "RG8_UNORM", shadow)

    def _keep(self, name: str, texture, texture_format: str, size_px: tuple) -> None:
        self.m_clouds[name] = texture
        self.m_qrhi_clouds[name] = give(self.m_handover, texture, texture_format, tuple(size_px))

    def ready(self) -> bool:
        return bool(self.m_clouds) or self.m_description is None

    def fill_clouds(self, constants: gs.CConstants, camera) -> None:
        """The clouds' constants and ``GCF_CLOUDS`` by name, the view's (``camera`` None: the sky cube's, from the
        ground at the world's origin)."""
        self.m_description.fill(constants, camera)

    def _fill(self, constants: gs.CConstants, sky, slot: gamesky.SSlot) -> None:
        if slot.key == "clouds":  # their constants first: the sky's own win where both name one (as CSkyPasses)
            self.fill_clouds(constants, None)
        for key, value in {**self.m_atmosphere, **gamesky.SKY_GEN}.items():
            constants.set(key, value)
        gs.fill_sun(constants, sky.sun, sky.night)
        direction, right, up = gamesky.FACES[slot.face]
        for key, value in (("cSkyFaceDir", direction), ("cSkyFaceX", right), ("cSkyFaceY", up),
                           ("cSkyFaceID", slot.face), ("cSkyRoughness", slot.level / (gamesky.LEVELS - 1)),
                           ("cSkyConvolveClearSky", int(slot.clear))):  # fmt: skip
            constants.set(key, value)

    def _named(self, key: str, named: dict) -> dict:
        """``named`` with stand-ins for what kernel ``key`` reads that nothing hands (zeros of the declared kind)."""
        kernel, refl = self.m_kernels[key], self.m_reflections[key]
        out = dict(named)
        declared = {binding.name: binding for binding in refl.bindings}
        strides = {constant_buffer.name: constant_buffer.size for constant_buffer in refl.cbuffers}
        for spec in kernel.m_plan.layouts:
            if spec.bindless:
                out.setdefault(spec.items[0].name, self._table(kernel, spec))
                continue
            for item in spec.items:
                if item.name not in out and item.kind not in ("sampler", "push", "volatile", "cbuffer"):
                    out[item.name] = self.m_stand_ins.of(item, declared[item.name], strides.get(item.name, 16))
        return out

    def _table(self, kernel, spec):
        if self.m_table is None:
            item = spec.items[0]
            self.m_table = self.m_device.descriptor_table(kernel.m_layouts[kernel.m_plan.layouts.index(spec)], 1)
            self.m_device.write_table(self.m_table, (item.kind, 0, self.m_stand_ins.white_texture()))
        return self.m_table

    def _pass(self, key: str, slot: gamesky.SSlot, named: dict, sky, grid) -> None:
        # the harmonics add face by face into the light buffer, the clouds into the sky's target: NVRHI places no
        # barrier between dispatches of one unchanged set (the current path's passes each end with QRhi's)
        self.m_list.uav_barrier()
        self.m_kernels[key].dispatch(
            self.m_list, self._named(key, named), lambda constants: self._fill(constants, sky, slot), grid
        )

    def run(self, sky, clouds, camera, shared: dict) -> bool:
        """The clouds' maps once, the self-shadow cache around ``camera`` every frame (``shared``: the frame's camera
        buffer and blue noise by name), and the sky's chain when the light or (in ``CLOUD_STEP``s) the clouds moved,
        on the module's list, sent to the queue (ahead of QRhi's frame, which reads them); True when the chain ran."""
        self.m_description = clouds
        if clouds is not None and not self.m_clouds:
            self._upload_maps(self.m_list)
        if clouds is not None:
            self._cache(camera, sky, shared)
        ran = self._sky(sky)
        self.m_list.execute()
        self.m_handover.handed_back()
        return ran

    def _cache(self, camera, sky, shared: dict) -> None:
        """``csUpdateSelfShadowCache``: the cache made anew around the camera (``cSSCUpdateFactor`` 1)."""
        cache = self.m_clouds["cSelfShadowCacheRO"]
        self.m_handover.take_back(self.m_list, [self.m_qrhi_clouds["cSelfShadowCacheRO"]])  # QRhi's passes read it
        named = {**shared, **{k: value for k, value in self.m_clouds.items() if k != "cSelfShadowCacheRO"},
                 "cSelfShadowCacheRW": cache}  # fmt: skip
        grid = tuple(
            groups(count, group_size)
            for count, group_size in zip(gameclouds.CACHE, gameclouds.CACHE_GROUP, strict=True)
        )

        def fill(constants: gs.CConstants) -> None:
            gs.fill_frame(constants, camera)
            gs.fill_sun(constants, sky.sun, sky.night)
            self.fill_clouds(constants, camera)

        self.m_kernels["cache"].dispatch(self.m_list, self._named("cache", named), fill, grid)

    def _sky(self, sky) -> bool:
        """``gamesky.CSkyPasses.run``'s chain on the module's list (its order, sizes and slots)."""
        clouds = self.m_description is not None and bool(self.m_clouds)
        description = self.m_description
        moment = (int(description.m_seconds // gamesky.CLOUD_STEP), description.m_wind) if clouds else None
        key = (tuple(np.round(np.asarray(sky.sun, float), 4)), sky.night, moment)
        if key == self._m_done:
            return False
        clouds_only = clouds and self._m_done[:2] == key[:2]
        self._m_done = key
        self.m_handover.take_back(self.m_list, [self.m_qrhi_cube])  # QRhi's passes read it since the last run
        tables = {"cAtmosphereTransmittanceTarget": self.m_transmittance, "cAtmosphereMultiscatTarget": self.m_multiscat,
                  "cAtmosphereTransmittanceLUT": self.m_transmittance, "cAtmosphereMultiscatLUT": self.m_multiscat,
                  "cLightDataUAV": self.m_light}  # fmt: skip
        transmittance_size, multiscat_size = gamesky.TRANSMITTANCE, gamesky.MULTISCAT
        SSlot = gamesky.SSlot
        if not clouds_only:
            self._pass(
                "transmittance",
                SSlot("transmittance"),
                tables,
                sky,
                (groups(transmittance_size[0]), groups(transmittance_size[1]), 1),
            )
            self._pass("multiscat", SSlot("multiscat"), tables, sky, (*multiscat_size, 1))
            self._pass("light", SSlot("light"), tables, sky, (1, 1, 1))
            self.m_list.execute()
        for clear in (False,) if clouds_only else (True, False):  # the clear sky's coefficients, then the cloudy
            if clear or clouds:
                self._sky_cube(tables, sky, clouds and not clear)
            for face in range(6):
                self._pass("sh", SSlot("sh", face, 0, clear), {**tables, "cSkyTexture": self.m_sky}, sky, (1, 1, 1))
            self.m_list.execute()
        for level in range(gamesky.LEVELS):
            for face in range(6):
                self._face(SSlot("filter", face, level), {**tables, "cSkyTexture": self.m_sky}, sky, self.m_cube)
        return True

    def _sky_cube(self, tables: dict, sky, clouds: bool) -> None:
        """The sky cube, every level integrated at its own size; with ``clouds`` each face's clouds added before it
        goes into the cube."""
        for level in range(gamesky.LEVELS):
            for face in range(6):
                self._face(gamesky.SSlot("sky", face, level), tables, sky, self.m_sky, clouds)
            self.m_list.execute()  # a volatile buffer's versions per list are bounded (NVRHI)

    def _face(self, slot: gamesky.SSlot, named: dict, sky, cube, clouds: bool = False) -> None:
        size = gamesky.CUBE >> slot.level
        target = self.m_targets[slot.level]
        grid = (groups(size), groups(size), 1)
        self._pass(slot.key, slot, {**named, "cSkyTarget": target}, sky, grid)
        if clouds:
            # as the current path's: no light buffer and no camera (zeros), the clouds' maps and the tables
            self._pass(
                "clouds", slot._replace(key="clouds"), {**named, **self.m_clouds, "cSkyTarget": target}, sky, grid
            )
        self.m_list.copy_region(cube, slot.face, slot.level, 0, 0, target, 0, 0, size, size)

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        for qrhi_texture in [
            self.m_qrhi_cube,
            *{id(qrhi_texture): qrhi_texture for qrhi_texture in self.m_qrhi_clouds.values()}.values(),
        ]:
            self.m_handover.drop(qrhi_texture)
        for resource in (
            self.m_transmittance,
            self.m_multiscat,
            *self.m_targets,
            self.m_sky,
            self.m_light,
            self.m_table,
        ):
            if resource is not None:
                resource.release()
        self.m_stand_ins.release()
        self.m_list.close()
        self.m_clouds, self.m_qrhi_clouds, self.m_kernels = {}, {}, {}
