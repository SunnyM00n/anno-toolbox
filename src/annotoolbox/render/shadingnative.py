"""117's deferred shading, its sky light, the clouds' shadow, the visible sky and haze and the cascades' fit by the
game's shipped shaders through the native backend (step 5 of docs/rendering/native-backend.md): the sky's chain and
the clouds' maps on the module's own list (``skynative``), the rest on QRhi's list between its passes
(``nativeforeign``, ``atmospherenative``), reading and writing QRhi's G-buffer, depth, lit colour and shadow map.

``CNativeShading(device, natives, handover, rhi, tables, atmosphere)`` is the entry, one per view; ``shade`` replaces
``gameframe.CGameFrame._shading``, ``atmosphere`` the visible sky's ``Atmosphere.run``, ``fit`` the cascades' analysis
(``atmospherenative.CNativeFit``). What the passes still on QRhi read of them goes to QRhi (``inputs``, ``SCloudsSeen``).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np

from . import gameclouds, gameshadow, gamesky, mipchain, nativecompute, nativeforeign, nativegpu, skynative
from . import gameshader as gs
from .gamecompute import groups, storage_buffer
from .gamelight import CLUSTER_BYTES, POINT_BYTES, TILE, WHITE, SExtras
from .nativepass import compute_pass
from .terrainnative import CBridge

ENV = "ANNO_NATIVE_SHADING"  # "0": the shading, sky, haze, clouds and fit by the current path
# key -> (effect, entry, the literal arguments its technique passes): DeferredShading_Uber's, the clouds' shadow
CALLS = {"shading": ("deferred_shading", "csDeferredShading", "0xfffffffffalse"),
         "cloud_shadow": ("environment", "csVolumetricShadow", "")}  # fmt: skip
CLUSTER_STRIDE, POINT_STRIDE = 8, 16  # cLightClusterBuffer's uint2, cPointLights' float4


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """Every shader of step 5 read from the shipped variants and reflected (in the background: the island's
    loader); a missing one raises (the current path then does it all)."""
    from . import atmospherenative

    out = {
        key: natives.call(natives.variant(stem), function_name, args)
        for key, (stem, function_name, args) in CALLS.items()
    }
    out.update(atmospherenative.shaders(natives))
    out.update({f"sky.{k}": shader for k, shader in skynative.shaders(natives).items()})
    for shader in out.values():
        natives.reflection(shader)
    return out


@dataclass
class SCloudsSeen:
    """The clouds as the passes still on QRhi read them (``gameclouds.CClouds``' interface): QRhi's wrappers of the
    module's maps, cache and (``shadowed``) the cascades' transmittance; the constants of the view's clouds."""

    clouds: object  # gameclouds.CClouds: the maps, the time and the wind
    textures: dict = field(default_factory=dict)
    shadowed: bool = False

    def inputs(self) -> dict:
        return {key: texture for key, texture in self.textures.items() if self.shadowed or key != "cTransmittanceMap"}

    def fill(self, buffer: gs.CConstants, camera) -> None:
        self.clouds.fill(buffer, camera)

    def moment(self) -> tuple:
        return self.clouds.moment()


class CCommon:
    """What the native parts of step 5 share: the module's list for their constants and uploads, the bridge to
    QRhi's resources, the stand-ins, the read-back pins and the frame's camera records."""

    def __init__(self, device, natives) -> None:
        self.m_device, self.m_natives = device, natives
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list, WHITE)
        self.m_pins = nativeforeign.CPins()
        self.m_cameras = None  # cCameraBuffer
        self._m_sent = b""

    def kernel(self, shader) -> nativeforeign.CForeignKernel:
        return nativeforeign.CForeignKernel(self.m_device, shader, self.m_natives.reflection(shader), self.m_stand_ins)

    def camera(self, element, camera):
        """``cCameraBuffer``: the three camera records (all this one), written when they changed."""
        frame_constants = gs.CConstants(nativecompute.records(element, gs.CAMERAS))
        gs.fill_frame(frame_constants, camera)
        data = bytes(frame_constants.m_data)
        if self.m_cameras is None:
            self.m_cameras = self.m_device.buffer(len(data), "structured", stride=element.size, name="cCameraBuffer")
        if data != self._m_sent:
            self.m_list.write_buffer(self.m_cameras, data)
            self._m_sent = data
        return self.m_cameras

    def release(self) -> None:
        self.m_stand_ins.release()
        self.m_bridge.release()
        if self.m_cameras is not None:
            self.m_cameras.release()
        self.m_list.close()


class CNativeShading(nativecompute.CTold):
    """Step 5 on the native backend (module docstring)."""

    m_native = True

    def __init__(self, device, natives, handover, rhi, tables, atmosphere: dict) -> None:
        from . import atmospherenative

        self.m_device, self.m_handover, self.m_rhi = device, handover, rhi
        self.m_common = CCommon(device, natives)
        found = shaders(natives)
        self.m_kernels = {key: self.m_common.kernel(found[key]) for key in CALLS}
        self.m_element = next(
            constant_buffer
            for constant_buffer in natives.reflection(found["shading"]).cbuffers
            if constant_buffer.name == "cCameraBuffer"
        )
        self.m_sky = skynative.CNativeSky(device, natives, handover, atmosphere)
        self.m_fit = atmospherenative.CNativeFit(self, found, rhi)
        self.m_atmosphere_part = atmospherenative.CNativeAtmosphere(self, found)
        self.m_tables = self._tables(tables)
        gpu = device
        self.m_clusters = {
            "cLightClusterBuffer": gpu.buffer(CLUSTER_BYTES, "structured", stride=CLUSTER_STRIDE, name="light cells"),
            "cPointLights": gpu.buffer(POINT_BYTES, "structured", stride=POINT_STRIDE, name="point lights")}  # fmt: skip
        self._m_clusters = False  # the clusters last written (None: none; False: nothing written yet)
        self.m_shadowed = False  # this frame's clouds' shadow is made (the haze reads it then)
        self.m_qrhi_light = storage_buffer(rhi, gamesky.LIGHT_BYTES)  # the sky's light as QRhi's passes read it
        self.m_info = None  # the cascades' information when the CPU fits them (the module's copy)
        whole = gameshadow.INFO_BYTES * gameshadow.COUNT  # the analysis's, copied for the native transparent draws
        self.m_info_copy = device.buffer(whole, "structured", stride=gameshadow.INFO_BYTES, name="info copy")
        self.m_error = ""
        self.m_messages, self.m_foreign = [], []

    def _tables(self, engine_tables) -> dict:
        """The engine's tables (``gamelight.STables``) as the module's textures, once."""
        out, gpu, command_list = {}, self.m_device, self.m_common.m_list

        def upload(name: str, levels: list) -> None:
            height_px, width_px = levels[0].shape[:2]
            texture = gpu.texture(width_px, height_px, "RGBA16_FLOAT", mips=len(levels), name=name)
            for mip, level in enumerate(levels):
                command_list.write_texture(
                    texture, np.ascontiguousarray(level, np.float16).tobytes(), level.shape[1] * 8, mip=mip
                )
            out[name] = texture

        if engine_tables.brdf:
            upload("cPrefilteredBRDF", mipchain.full_array_levels(engine_tables.brdf))
        for name, image in zip(
            ("cLTC0Tex", "cLTC1Tex"), engine_tables.ltc if len(engine_tables.ltc) == 2 else (), strict=False
        ):
            upload(name, [image])
        if engine_tables.noise is not None:
            upload("cBlueNoiseTex", [engine_tables.noise])
        return out

    def _lights(self, clusters) -> None:
        """The clustered lights in the module's buffers, written when they change (none: every cell empty)."""
        if clusters is self._m_clusters:
            return
        held, command_list = self.m_clusters, self.m_common.m_list
        cells = clusters.cells if clusters is not None else np.zeros(CLUSTER_BYTES // 4, np.uint32)
        command_list.write_buffer(
            held["cLightClusterBuffer"], cells.tobytes()[:CLUSTER_BYTES].ljust(CLUSTER_BYTES, bytes(1))
        )
        if clusters is not None:
            command_list.write_buffer(
                held["cPointLights"], clusters.points.tobytes()[:POINT_BYTES].ljust(POINT_BYTES, bytes(1))
            )
            height_px, width_px = clusters.indices.shape
            index = held.get("cLightIndexTex")
            if index is None or index.width != width_px:
                if index is not None:
                    index.release()
                index = held["cLightIndexTex"] = self.m_device.texture(
                    width_px, height_px, "R32_UINT", name="light index"
                )
            command_list.write_texture(index, np.ascontiguousarray(clusters.indices, "<u4").tobytes(), width_px * 4)
        self._m_clusters = clusters

    def shared(self, recording: nativeforeign.CRecording, frame, cascades) -> dict:
        """What the shading, the clouds and the haze read by name (as the module's, for ``rec``): the tables, the
        lights, the camera records, the sky's light, cube and tables, the shadow map and the cascades' information
        (QRhi's, made known by a pin: a copy's source; the module's copy when the CPU fitted them)."""
        sky = self.m_sky
        out = {**self.m_tables, **self.m_clusters, "cCameraBuffer": self.m_common.m_cameras, "cLightBuffer": sky.m_light,
               "cPrefilteredSpecularCube": recording.handed(sky.m_qrhi_cube, sky.m_cube),
               "cAtmosphereTransmittanceLUT": sky.m_transmittance, "cAtmosphereMultiscatLUT": sky.m_multiscat}  # fmt: skip
        if cascades is not None:
            out["cShadowMap"] = recording.qrhi(frame.m_shadow.m_texture)
            if cascades.gpu:
                out["cShadowInformation"] = recording.qrhi_buffer(
                    frame.m_info, gameshadow.INFO_BYTES, nativeforeign.COPY_SOURCE
                )
            else:
                out["cShadowInformation"] = self.m_info
        return out

    def overlay(self, frame) -> dict:
        """What the native transparent draws (``waternative.SFrameWater``) bind in place of QRhi's buffers of the
        same names: the module's lights, light buffer and cascades' information (QRhi's are left in copy states
        here, where the native draws assume QRhi's storage state)."""
        cascades = frame.m_cascades
        info = None
        if cascades is not None:
            info = self.m_info_copy if cascades.gpu else self.m_info
        out = {**self.m_clusters, "cLightBuffer": self.m_sky.m_light}
        return {**out, "cShadowInformation": info} if info is not None else out

    def clouds_named(self, recording: nativeforeign.CRecording) -> dict:
        """The clouds' textures as the module's, in the states QRhi tracks for its wrappers."""
        return {
            name: recording.handed(self.m_sky.m_qrhi_clouds[name], texture)
            for name, texture in self.m_sky.m_clouds.items()
        }

    def pin_info(self, batch, frame, cascades) -> None:
        """The cascades' information found as a copy's source (QRhi's, by a read-back in ``batch``), or the
        module's own copy of the CPU's fit."""
        if cascades is None:
            return
        if cascades.gpu:
            self.m_common.m_pins.copy_source(batch, frame.m_info, gameshadow.INFO_BYTES * gameshadow.COUNT)
            return
        data = gameshadow.info_bytes(cascades)
        if self.m_info is None:
            self.m_info = self.m_device.buffer(len(data), "structured", stride=gameshadow.INFO_BYTES, name="info")
        self.m_common.m_list.write_buffer(self.m_info, data)

    def shade(self, ctx, frame, size_px, camera, lighting, sky, clusters, clouds, held: bool = False):
        """The sky's chain, the clouds' shadow in the cascades' parts and the deferred shading into ``frame``'s lit
        colour (``gameframe.CGameFrame._shading``'s work); returns (the inputs, the extras) as the passes still on
        QRhi read them (the transparent pass, the post chain's noise): QRhi's resources, the module's handed over,
        and ``lighting``'s (the current path's shading) tables and lights, bound without a dispatch. ``held``: the lit
        colour is the kept frame's (``gamestill``), nothing is dispatched."""
        cascades = frame.m_cascades
        seen = SCloudsSeen(clouds, dict(self.m_sky.m_qrhi_clouds)) if clouds is not None else None
        extras = SExtras(cascades, clusters, seen)
        inputs = {**{name: target.native for name, target in zip(("cGBuffer0", "cGBuffer1", "cGBuffer2"), frame.m_targets, strict=False)},
                  "cDepth": frame.m_depth.native, "cDstColor": frame.m_lit[0].native, "cDstAlpha": frame.m_lit[1].native,
                  "cLightBuffer": self.m_qrhi_light, "cPrefilteredSpecularCube": self.m_sky.m_qrhi_cube}  # fmt: skip
        if cascades is not None:
            inputs["cShadowMap"], inputs["cShadowInformation"] = frame.m_shadow.m_texture.native, frame.m_info
        shadowed = self.m_shadowed = clouds is not None and cascades is not None
        if seen is not None:
            seen.shadowed = shadowed

        def before(batch) -> None:
            self.pin_info(batch, frame, cascades)
            nativeforeign.CPins.copy_dest(batch, self.m_qrhi_light, bytes(gamesky.LIGHT_BYTES))
            lighting.prepare(batch, inputs, camera, sky, extras)

        def record(command_buffer) -> None:
            nativecompute.guarded(
                self,
                self._shade,
                command_buffer,
                frame,
                size_px,
                camera,
                lighting,
                sky,
                clusters,
                clouds,
                extras,
                shadowed,
            )

        if not held:
            compute_pass(ctx, record, before)
        return inputs, extras

    def _shade(self, command_buffer, frame, size_px, camera, lighting, sky, clusters, clouds, extras, shadowed) -> None:
        common, cascades = self.m_common, frame.m_cascades
        noise = {"cBlueNoiseTex": self.m_tables["cBlueNoiseTex"]} if "cBlueNoiseTex" in self.m_tables else {}
        self.m_sky.run(sky, clouds, camera,
                     {"cCameraBuffer": common.camera(self.m_element, camera), **noise})  # fmt: skip
        self._lights(clusters)
        lighting.m_cube_log2 = math.log2(gamesky.CUBE)
        shadow, shading = self.m_kernels["cloud_shadow"], self.m_kernels["shading"]
        used = min(gameshadow.COUNT, cascades.used) if shadowed else 0
        buffers = []
        for k in range(used):

            def fill(buffer: gs.CConstants, k=k) -> None:
                gs.fill_frame(buffer, camera)
                gs.fill_sun(buffer, sky.sun, sky.night)
                clouds.fill(buffer, camera)
                gameshadow.fill(buffer, cascades)
                buffer.set("cCurrentCascade", k)

            buffers.append(shadow.write(common.m_list, k, fill))
        main = shading.write(common.m_list, 0, lambda buffer: lighting.fill(buffer, camera, sky, extras))
        common.m_list.execute()
        self._told_before()
        with nativeforeign.CRecording(self.m_device, command_buffer, common.m_bridge) as recording:
            named = {**self.shared(recording, frame, cascades), **noise}
            if clouds is not None:
                named.update(self.clouds_named(recording))
            part = groups(gameclouds.SHADOW_SIZE // 2, gameclouds.SHADOW_GROUP)  # a cascade a quarter of the map
            for k in range(used):
                shadow.record(recording, k, {**named, "cVolumetricShadowTarget": named["cTransmittanceMap"]}, buffers[k],
                              (part, part, 1))  # fmt: skip
            if not shadowed:
                named.pop("cTransmittanceMap", None)  # clear air until the clouds' shadow is made (``WHITE``)
            names = ("cGBuffer0", "cGBuffer1", "cGBuffer2")
            named.update({name: recording.qrhi(target) for name, target in zip(names, frame.m_targets, strict=False)})
            named.update({"cDepth": recording.qrhi(frame.m_depth), "cDstColor": recording.qrhi(frame.m_lit[0]),
                          "cDstAlpha": recording.qrhi(frame.m_lit[1])})  # fmt: skip
            shading.record(recording, 0, named, main, (groups(size_px[0], TILE), groups(size_px[1], TILE), 1))
            light = recording.qrhi_buffer(self.m_qrhi_light, gamesky.LIGHT_BYTES, nativeforeign.COPY_DEST)
            recording.copy_buffer(light, self.m_sky.m_light, gamesky.LIGHT_BYTES)
            if cascades is not None and cascades.gpu:
                recording.copy_buffer(self.m_info_copy, named["cShadowInformation"], self.m_info_copy.size)
        self._told_after()

    def atmosphere(self, ctx, frame, size_px, camera, sky, passes, clouds) -> bool:
        """The visible sky and haze over the lit colour by the shipped environment passes (``Atmosphere.run``'s
        work, its targets and fog map); False when the view has none (``passes.atmosphere``)."""
        atmosphere = passes.atmosphere
        if atmosphere is None or atmosphere.m_error:
            return False
        self.m_atmosphere_part.run(ctx, frame, size_px, camera, sky, atmosphere, passes.look, clouds)
        return True

    def settling(self) -> bool:
        """Whether the clouds and fog, accumulated over frames, still fill in after the camera moved (the view draws
        on until they are whole), or the sky's chain still runs over frames (``skynative.CNativeSky.is_running``)."""
        return self.m_atmosphere_part.settling() or self.m_sky.is_running

    def end_frame(self) -> None:
        self.m_common.m_bridge.end_frame()

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        self.m_fit.release()
        self.m_atmosphere_part.release()
        self.m_sky.release()
        for resource in (*self.m_tables.values(), *self.m_clusters.values(), self.m_info, self.m_info_copy):
            if resource is not None:
                resource.release()
        self.m_qrhi_light.destroy()
        self.m_common.release()
