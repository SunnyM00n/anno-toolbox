"""The sun's cascades fitted and the visible sky and haze drawn by the game's shipped ``shadow.fx`` and
``environment.fx`` on QRhi's command list (``nativeforeign``): the analysis over the G-buffer's depth into QRhi's
``cShadowInformation``, then after the transparent pass the depth at half size, the clouds', the atmosphere's and the
fog's scattering, its blur and the upsample over the lit colour (``shadowfit`` and ``gameatmosphere``'s chains).

``CNativeFit(owner, shaders, rhi)`` has ``shadowfit.CShadowFit``'s interface; ``CNativeAtmosphere(owner, shaders)``'s
``run`` does ``Atmosphere.run``'s work with that object's targets, fog map and constants. ``owner``:
``shadingnative.CNativeShading``. docs/rendering/native-backend.md section 12.
"""

from __future__ import annotations

from PySide6.QtGui import QRhiReadbackResult

from . import gameatmosphere, gameshadow, nativeforeign, shadowfit
from .gamecompute import groups
from .nativecompute import guarded
from .nativepass import compute_pass, draw_pass
from .terrainnative import LIT_FORMATS

ENVIRONMENT, SHADOW = "environment", "shadow"
# key -> (effect, entry, literal arguments) of the atmosphere's passes (gameatmosphere.STAGES' functions)
ATMOSPHERE = {"quad": "vsCommonFullScreenQuad", "down": "psDownsampleDepth", "clouds": "csVolumetricClouds",
              "end": "csComputeAtmosphereEnd", "fog": "csVolumetricFog", "up": "psUpsampleScatteringVolumes"}  # fmt: skip
BLURS = {"blur.h": "int2(1,0)", "blur.v": "int2(0,1)"}  # csBlurScattering across, then down
DOWN_STATE = {
    "topology": "strip",
    "cull": "none",
    "depth_test": True,
    "depth_write": True,
    "depth_func": "always",
    "colors": [],
    "depth": "D32",
}  # DownsampleDepth's: ZFUNC ALWAYS into the half-size depth (QRhi's D32F)
FACTORS = {"one": "one", "zero": "zero", "srcalpha": "src_alpha", "invsrcalpha": "inv_src_alpha",
           "srccolor": "src_color", "invsrccolor": "inv_src_color", "destalpha": "dst_alpha",
           "invdestalpha": "inv_dst_alpha", "destcolor": "dst_color", "invdestcolor": "inv_dst_color"}  # fmt: skip


def shaders(natives) -> dict:
    """The fit's and the atmosphere's shaders of the shipped variants, keyed ``fit.<entry>`` and ``atm.<key>``."""
    shadow, environment = natives.variant(SHADOW), natives.variant(ENVIRONMENT)
    out = {f"fit.{function_name}": natives.call(shadow, function_name) for function_name in shadowfit.FUNCTIONS}
    out.update({f"atm.{key}": natives.call(environment, function_name) for key, function_name in ATMOSPHERE.items()})
    out.update({f"atm.{key}": natives.call(environment, "csBlurScattering", args) for key, args in BLURS.items()})
    return out


def blends(states: dict, targets: int) -> list:
    """A technique's blend states per target as the module's pipeline takes them (renderstates.pass_state's rules:
    a target without its own takes the block's, an unnamed mask writes all)."""
    state_map = {k.lower(): value.upper() for k, value in states.items()}

    def factor(key: str, default: str) -> str:
        return FACTORS[state_map.get(key, default).lower().replace("_", "").replace("blend", "")]

    out = []
    for i in range(targets):
        on = state_map.get(f"alphablendenable[{i}]", state_map.get("alphablendenable", "FALSE")) == "TRUE"
        source_factor, destination_factor = factor(f"srcblend[{i}]", "ONE"), factor(f"dstblend[{i}]", "ZERO")
        srca = factor(f"srcblendalpha[{i}]", state_map.get(f"srcblend[{i}]", "ONE"))
        dsta = factor(f"dstblendalpha[{i}]", state_map.get(f"dstblend[{i}]", "ZERO"))
        mask = int(state_map.get(f"colorwriteenable[{i}]", "0xF"), 0)
        out.append((on, source_factor, destination_factor, "add", srca, dsta, "add", mask))
    return out


class CNativeFit:
    """The cascades' analysis (``shadowfit.CShadowFit``'s interface): its passes on QRhi's list, QRhi's information
    buffer found and left a copy's source (``pinned``: a read-back in the pass's opening batch)."""

    m_pinned = True

    def __init__(self, owner, found: dict, rhi) -> None:
        self.m_owner, self.m_rhi = owner, rhi
        common, device = owner.m_common, owner.m_device
        self.m_kernels = {
            function_name: common.kernel(found[f"fit.{function_name}"]) for function_name in shadowfit.FUNCTIONS
        }
        self.m_bounds = device.buffer(
            shadowfit.BOUNDS_BYTES, "structured,uav", stride=shadowfit.BOUNDS_BYTES, name="bounds"
        )
        size = gameshadow.INFO_BYTES * gameshadow.COUNT
        self.m_last = device.buffer(size, "structured", stride=gameshadow.INFO_BYTES, name="last splits")  # no blend
        common.m_list.write_buffer(self.m_last, bytes(size))
        self.m_half = None
        self.m_read_back = False
        self.m_readbacks: list = []

    @property
    def m_error(self) -> str:
        return self.m_owner.m_error

    def run(self, ctx, depth, size_px: tuple[int, int], camera, sun, shadow_information) -> None:
        """The copy of the depth to half size, then the analysis into ``shadow_information`` (QRhi's), recorded on QRhi's list."""
        half = (groups(size_px[0], 2), groups(size_px[1], 2))
        if self.m_half is None or (self.m_half.width, self.m_half.height) != half:
            if self.m_half is not None:
                self.m_half.release()
            self.m_half = self.m_owner.m_device.texture(*half, "R32_FLOAT", usage="uav", name="half depth")
        read, self.m_read_back = self.m_read_back, False
        whole = gameshadow.INFO_BYTES * gameshadow.COUNT

        def before(batch) -> None:
            self.m_owner.m_common.m_pins.copy_source(batch, shadow_information, whole)

        def after():
            batch = self.m_rhi.nextResourceUpdateBatch()
            self.m_readbacks = [QRhiReadbackResult(), QRhiReadbackResult()]  # the bounds stay the module's
            batch.readBackBuffer(shadow_information, 0, whole, self.m_readbacks[1])
            return batch

        def record(command_buffer) -> None:
            guarded(self.m_owner, self._record, command_buffer, depth, size_px, camera, sun, shadow_information)

        compute_pass(ctx, record, before, after if read else None)

    def _record(self, command_buffer, depth, size_px, camera, sun, shadow_information) -> None:
        common = self.m_owner.m_common
        memo: dict = {}  # one fill for every pass: each buffer worked out once
        buffers = {function_name: k.write(common.m_list, 0, lambda constants: shadowfit.fill(constants, camera, sun, size_px), memo)
                   for function_name, k in self.m_kernels.items()}  # fmt: skip
        cameras = common.camera(self.m_owner.m_element, camera)
        common.m_list.execute()
        with nativeforeign.CRecording(self.m_owner.m_device, command_buffer, common.m_bridge) as rec:
            depth_texture = rec.qrhi(depth)
            stored = rec.qrhi_buffer(shadow_information, gameshadow.INFO_BYTES, nativeforeign.COPY_SOURCE)
            named = {"cDepthCopySrc": depth_texture, "cDepthCopyDst": self.m_half, "cDepthTexture": depth_texture,
                     "cDepthTexAfterTransparent": self.m_half, "cZBoundsOut": self.m_bounds, "cShadowInformationUAV": stored,
                     "cShadowInfoLastFrame": self.m_last, "cCameraBuffer": cameras}  # fmt: skip
            tiles = (groups(size_px[0], shadowfit.TILE), groups(size_px[1], shadowfit.TILE), 1)
            copy = (
                groups(self.m_half.width, shadowfit.COPY_GROUP),
                groups(self.m_half.height, shadowfit.COPY_GROUP),
                1,
            )
            where = {shadowfit.COPY: copy, "csReduceZ": tiles, "csReduceLightBounds": tiles}
            for function_name, kernel in self.m_kernels.items():
                kernel.record(rec, 0, named, buffers[function_name], where.get(function_name, (1, 1, 1)))

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        for resource in (self.m_bounds, self.m_last, self.m_half):
            if resource is not None:
                resource.release()


class CNativeAtmosphere:
    """The visible sky and haze (module docstring): three passes of QRhi's (the half-size depth, the scattering, the
    upsample over the lit colour) with the shipped shaders recorded in them."""

    def __init__(self, owner, found: dict) -> None:
        self.m_owner = owner
        common = owner.m_common
        self.m_kernels = {key: common.kernel(found[f"atm.{key}"]) for key in ("clouds", "end", "fog", *BLURS)}
        refl = common.m_natives.reflection
        quad = (found["atm.quad"], refl(found["atm.quad"]))
        up_state = common.m_natives.techniques(ENVIRONMENT)["UpsampleScatteringVolumes"].state
        up = {"topology": "strip", "cull": "none", "depth_test": False, "depth_write": False, "colors": LIT_FORMATS,
              "depth": "", "blends": blends(up_state, len(LIT_FORMATS))}  # fmt: skip
        draw = nativeforeign.CForeignKernel.draw
        self.m_down = draw(owner.m_device, {"vs": quad, "ps": (found["atm.down"], refl(found["atm.down"]))}, DOWN_STATE,
                         common.m_stand_ins)  # fmt: skip
        self.m_up = draw(
            owner.m_device, {"vs": quad, "ps": (found["atm.up"], refl(found["atm.up"]))}, up, common.m_stand_ins
        )
        self.m_lit_fbo = None  # the lit colour and alpha without the depth: the upsample reads that depth
        self._m_lit_of: tuple = ()

    def _lit(self, ctx, frame):
        key = tuple(id(target) for target in frame.m_lit)
        if self.m_lit_fbo is None or key != self._m_lit_of:
            if self.m_lit_fbo is not None:
                self.m_lit_fbo.release()
            self.m_lit_fbo, self._m_lit_of = ctx.framebuffer(frame.m_lit, None), key
        return self.m_lit_fbo

    def run(self, ctx, frame, size_px, camera, sky, atmosphere, look, clouds) -> None:
        """``gameatmosphere.CAtmosphere.run``'s passes over ``frame``'s depth into its lit colour."""
        owner, look = self.m_owner, look or gameatmosphere.SLook()
        atmosphere.targets(ctx, size_px)
        half = tuple(atmosphere.m_target.size[:2])
        fog = atmosphere.fog_map(ctx)
        heights = atmosphere.m_heights if fog is not None else None

        def fill(constants) -> None:
            atmosphere.fill(constants, camera, sky, look, clouds)

        def nothing(*_args) -> None:
            return None

        fill.memo = {}  # the passes share one fill: each buffer worked out once a frame
        down = lambda command_buffer: guarded(  # noqa: E731
            owner, self._draw, command_buffer, self.m_down, {"cDepthBuffer": frame.m_depth}, fill, camera, half
        )
        draw_pass(ctx, atmosphere.m_half_fbo, nothing, nothing, targets=0, clear=False, external=down)
        scatter = (frame, half, fill, camera, atmosphere, look, clouds, fog, heights)
        compute_pass(ctx, lambda command_buffer: guarded(owner, self._scatter, command_buffer, *scatter),
                                   lambda batch: owner.pin_info(batch, frame, frame.m_cascades))  # fmt: skip
        upsample = {"cDepthBuffer": frame.m_depth, "cDownsampledDepthBuffer": atmosphere.m_half,
                    "cScatteringTexture": atmosphere.m_target}  # fmt: skip

        def up(command_buffer):
            return guarded(owner, self._draw, command_buffer, self.m_up, upsample, fill, camera, size_px)

        draw_pass(ctx, self._lit(ctx, frame), nothing, nothing, targets=len(LIT_FORMATS), clear=False, external=up)

    def _draw(self, command_buffer, kernel, qrhi: dict, fill, camera, size_px) -> None:
        """One full-screen strip of ``kernel`` reading ``qrhi`` (QRhi's textures by name)."""
        common = self.m_owner.m_common
        buffers = kernel.write(common.m_list, 0, fill, fill.memo)
        cameras = common.camera(self.m_owner.m_element, camera)
        common.m_list.execute()
        with nativeforeign.CRecording(self.m_owner.m_device, command_buffer, common.m_bridge) as rec:
            named = {name: rec.qrhi(texture) for name, texture in qrhi.items()}
            kernel.record_draw(rec, 0, {**named, "cCameraBuffer": cameras}, buffers, size_px)

    def _scatter(self, command_buffer, frame, half, fill, camera, atmosphere, look, clouds, fog, heights) -> None:
        """The clouds' march, the atmosphere beyond them, the fog and the atmosphere before them, the blur."""
        owner, common = self.m_owner, self.m_owner.m_common
        buffers = {key: k.write(common.m_list, 0, fill, fill.memo) for key, k in self.m_kernels.items()}
        common.camera(owner.m_element, camera)
        common.m_list.execute()
        with nativeforeign.CRecording(owner.m_device, command_buffer, common.m_bridge) as rec:
            named = {**owner.shared(rec, frame, frame.m_cascades), "cDepthBuffer": rec.qrhi(frame.m_depth.native),
                     "cDownsampledDepthBuffer": rec.qrhi(atmosphere.m_half.native),
                     "cScatteringTarget": rec.qrhi(atmosphere.m_target.native)}  # fmt: skip
            if fog is not None:
                named["cFogMapTexture"] = rec.qrhi(fog)
                if heights is not None:
                    named["cTerrainHeightTex"] = rec.qrhi(heights)
            if clouds is not None:
                seen = owner.clouds_named(rec)
                if not owner.m_shadowed:
                    seen.pop("cTransmittanceMap", None)
                named.update(seen)
            grid = (groups(half[0], gameatmosphere.GROUP), groups(half[1], gameatmosphere.GROUP), 1)
            small = (groups(half[0], gameatmosphere.BLUR_GROUP), groups(half[1], gameatmosphere.BLUR_GROUP), 1)
            k = self.m_kernels
            k["clouds"].record(rec, 0, named, buffers["clouds"], grid)
            k["end"].record(rec, 0, named, buffers["end"], small)
            k["fog"].record(rec, 0, named, buffers["fog"], grid)
            if look.blur:  # across into the second target and back (SCATTER_BLUR_ENABLE)
                target, blurred = named["cScatteringTarget"], rec.qrhi(atmosphere.m_blurred.native)
                for key, source, destination in (("blur.h", target, blurred), ("blur.v", blurred, target)):
                    k[key].record(rec, 0, {**named, "cScatteringBlurSrc": source, "cScatteringBlurDst": destination}, buffers[key],
                                  small)  # fmt: skip

    def release(self) -> None:
        for kernel in (*self.m_kernels.values(), self.m_down, self.m_up):
            kernel.release()
        if self.m_lit_fbo is not None:
            self.m_lit_fbo.release()
            self.m_lit_fbo = None
