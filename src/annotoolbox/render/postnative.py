"""117's post chain by the game's shipped shaders on QRhi's list (step 7, docs/rendering/native-backend.md 14): the
eye adaptation (``eye_adaptation.fx``) over the view's resolved colour, the grade (``posteffect_colorgrade.fx``) and
tone map (``posteffect_tonemap.fx``) into two half-float targets of QRhi's, the composite (``composite.fx``) into the
view's own target, which the editor's overlays draw over (``nativeforeign`` records them).

``CNativePost(device, natives)`` is the entry, one per view; ``run(ctx, image, size_px, fbo, view, maps)`` does
``CFrameExtras.finish``'s work and the view's final pass (``desktopframe._finish``); ``steady``, ``read_exposure`` and
``exposure`` are ``CFrameExtras``' for a bench (``scripts/native_compare.py``).
"""

from __future__ import annotations

import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QRhiReadbackResult, QRhiTexture

from . import eyeadapt, gamepost, nativecompute, nativeforeign, nativegpu, nativelayout
from . import gameshader as gs
from .atmospherenative import blends
from .gamecompute import groups, storage_buffer
from .nativepass import compute_pass, draw_pass
from .terrainnative import FORMATS, CBridge

ENV = "ANNO_NATIVE_POST"  # "0": the eye adaptation, grade, tone map and the view's final pass by the current path
EYE = "eye_adaptation"
# key -> the eye adaptation's entry (eyeadapt.STAGES' functions)
KERNELS = {"tiles": "csPerTileHistogram", "merge": "csMergeHistograms", "exposure": "csCalculateExposure"}
# key -> (effect, vertex entry, pixel entry, technique): the post effects in the order default.pfx runs them
DRAWS = {"grade": ("posteffect_colorgrade", "vsColorGrade", "psColorGrade", "ColorGrade"),
         "tonemap": ("posteffect_tonemap", "vsTonemap", "psTonemap", "Tonemap"),
         "composite": ("composite", "vsComposite", "psComposite", "RenderComposite")}  # fmt: skip
HDR = "RGBA16_FLOAT"  # the grade's and the tone map's targets, as the current path's (gamepost)
# the composite's frame values: the options' gamma at its default and SDR output (GCF_USE_HDR clear) [E]
COMPOSITE = {"cGammaAdjust": 1.0, "cGlobalFlags": 0}
READ_BYTES = 32  # the exposure buffer's head a bench reads back, as ``eyeadapt.CEyeAdaptation._readback``


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """Every shader of the step from the shipped variants, reflected, keyed ``eye.<key>`` and ``<draw>.vs`` /
    ``<draw>.ps``; a missing one raises (the current path then does it all)."""
    eye = natives.variant(EYE)
    out = {f"eye.{key}": natives.call(eye, function_name) for key, function_name in KERNELS.items()}
    for key, (stem, vs, ps, _technique) in DRAWS.items():
        variant = natives.variant(stem)
        out[f"{key}.vs"], out[f"{key}.ps"] = natives.call(variant, vs), natives.call(variant, ps)
    for shader in out.values():
        natives.reflection(shader)
    return out


@dataclass
class SMaps:
    """What the frame hands the chain: the ambient's grade cube (None: ungraded), the blue noise (either QRhi's or the
    module's)."""

    lut: object = None
    noise: object = None


def target_format(framebuffer) -> tuple[str, str]:
    """(colour format, depth format or "") of a framebuffer of the view, as the module's pipelines name them; raises
    ``CNativeError`` for a format the module has no name for."""
    texture = framebuffer.color_attachments[0].native
    texture_format = FORMATS.get(texture.format())
    if texture_format is None:
        raise nativeforeign.CNativeError(f"no native format for the view's {texture.format()}")
    if texture_format == "RGBA8_UNORM" and texture.flags() & QRhiTexture.Flag.sRGB:
        texture_format = "SRGBA8_UNORM"
    depth = framebuffer.depth_attachment
    return texture_format, (FORMATS.get(depth.native.format(), "D32") if depth is not None else "")


def _module_texture(texture) -> bool:
    """A texture of the module's (its ``native`` is the resource's address), not one of QRhi's or the view's."""
    return isinstance(getattr(texture, "native", None), int)


class CNativePost(nativecompute.CTold):
    """The step's passes on the native backend (module docstring)."""

    m_native = True

    def __init__(self, device, natives) -> None:
        self.m_device, self.m_natives = device, natives
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list)
        self.m_found = found = shaders(natives)
        reflect = natives.reflection
        self.m_kernels = {key: nativeforeign.CForeignKernel(device, found[f"eye.{key}"], reflect(found[f"eye.{key}"]),
                                                       self.m_stand_ins) for key in KERNELS}  # fmt: skip
        elements, stride = nativelayout.float_elements(nativelayout.signature(found["grade.vs"].bytecode))
        self.m_inputs = device.input_layout(elements)
        triangle = gamepost.TRIANGLE
        vertices = np.concatenate(
            [np.asarray(triangle["float3"], np.float32), np.asarray(triangle["float2"], np.float32)], 1
        )
        if vertices.shape[1] * 4 != stride:
            raise nativeforeign.CNativeError(f"the post effects' vertex is {stride} bytes, not the triangle's")
        self.m_triangle = device.buffer(vertices.nbytes, "vertex", name="full-screen triangle")
        self.m_list.write_buffer(self.m_triangle, vertices.tobytes())
        # the view's offscreen pictures stand upside down against its target where the backend's y differ (rhipass
        # ``flip_offscreen``): the grade, the one pass that samples by texture coordinate, reads them turned over
        vertices[:, 4] = 1.0 - vertices[:, 4]
        self.m_turned = device.buffer(vertices.nbytes, "vertex", name="full-screen triangle, v turned")
        self.m_list.write_buffer(self.m_turned, vertices.tobytes())
        self.m_draws: dict[tuple, nativeforeign.CForeignKernel] = {}  # (draw, colour, depth) -> its kernel
        self.m_merged = device.buffer(eyeadapt.BINS * 4, "structured,uav", stride=4, name="merged histogram")
        size = eyeadapt.EXPOSURE_FLOATS * 4
        self.m_exposure_buffer = device.buffer(size, "structured,uav", stride=4, name="exposure")
        self.m_read_back = False  # the next frame copies the exposure out (``read_exposure``)
        self.m_readback: QRhiReadbackResult | None = None
        self.m_copy = None  # QRhi's buffer the exposure is copied into for its read-back, made on first use
        self.m_tiles, self.m_tile_count = None, 0
        self.m_targets: list = []  # the grade's and the tone map's (QRhi's, as gamepost's)
        self.m_fbos: list = []
        self.m_size_px = None
        self.m_ui, self.m_ui_size = None, None  # the composite's UI: transparent at the view's size (it sizes the uv)
        self._m_started = False
        self._m_last_frame: float | None = None
        self.m_clock: Callable[[], float] = time.monotonic  # the eye adaptation's time (``steady``: fixed steps)
        self.m_error = ""
        self.m_messages, self.m_foreign = [], []

    def _draw(self, key: str, colour: str, depth: str) -> nativeforeign.CForeignKernel:
        """``DRAWS[key]``'s kernel into a target of ``colour`` (and ``depth``), made once per target kind."""
        found = self.m_draws.get((key, colour, depth))
        if found is None:
            stem, _vs, _ps, technique = DRAWS[key]
            state = self.m_natives.techniques(stem)[technique].state
            fixed = {"cull": "none", "depth_test": False, "depth_write": False, "colors": [colour], "depth": depth,
                     "blends": blends(state, 1)}  # fmt: skip
            reflect = self.m_natives.reflection
            stages = {
                stage: (self.m_found[f"{key}.{stage}"], reflect(self.m_found[f"{key}.{stage}"]))
                for stage in ("vs", "ps")
            }
            found = self.m_draws[(key, colour, depth)] = nativeforeign.CForeignKernel.draw(
                self.m_device, stages, fixed, self.m_stand_ins, self.m_inputs
            )
        return found

    def _targets(self, ctx, size_px) -> None:
        if self.m_size_px == size_px:
            return
        for resource in (*self.m_fbos, *self.m_targets):
            resource.release()
        self.m_targets = [ctx.native_texture(size_px, QRhiTexture.Format.RGBA16F) for _ in range(2)]
        self.m_fbos = [ctx.framebuffer([texture]) for texture in self.m_targets]
        self.m_size_px = size_px

    def _ui(self, size_px) -> object:
        """The composite's UI texture: transparent black at the view's size (its size is the composite's uv)."""
        if self.m_ui_size != size_px:
            if self.m_ui is not None:
                self.m_ui.release()
            self.m_ui = self.m_device.texture(*size_px, "RGBA8_UNORM", usage="target", name="no UI")
            self.m_list.clear_color(self.m_ui, 0.0, 0.0, 0.0, 0.0)
            self.m_ui_size = size_px
        return self.m_ui

    def _tile_buffer(self, count: int):
        if self.m_tiles is None or count > self.m_tile_count:
            if self.m_tiles is not None:
                self.m_tiles.release()
            self.m_tiles = self.m_device.buffer(count * eyeadapt.BINS * 4, "structured,uav", stride=4, name="tiles")
            self.m_tile_count = count
        return self.m_tiles

    def _step(self) -> float | None:
        """The eye adaptation's time since the last frame (None: the exposure settles at once: the first frame, a
        view drawn after a pause), as ``CFrameExtras._exposure`` takes it."""
        now = self.m_clock()
        step = None if self._m_last_frame is None else now - self._m_last_frame
        self._m_last_frame = now
        if step is None or step > eyeadapt.SETTLE or not self._m_started:
            return None
        return step

    def steady(self, step: float) -> None:
        """``CFrameExtras.steady``: from the next frame on the exposure starts at its target and eases by ``step``
        seconds a frame, whatever the frames cost."""
        frames = iter(range(1 << 62))
        self.m_clock = lambda: next(frames) * step
        self._m_last_frame = None

    def read_exposure(self) -> None:
        """The next frame reads its exposure back (``exposure``)."""
        self.m_read_back = True

    def exposure(self) -> float | None:
        """The exposure read back by the frame ``read_exposure`` asked (None: not asked or not in yet)."""
        data = bytes(self.m_readback.data) if self.m_readback is not None else b""
        return struct.unpack_from("<f", data)[0] if len(data) >= 4 else None

    def run(self, ctx, image, size_px: tuple[int, int], framebuffer, view: tuple[int, int], maps: SMaps) -> None:
        """The eye adaptation over ``image`` (the view's resolved HDR colour, ``size_px``), its grade and tone map, and
        the composite into ``framebuffer`` (the view's target, ``view`` pixels), each a pass of QRhi's frame."""
        self._targets(ctx, size_px)
        colour, depth = target_format(framebuffer)
        step = self._step()
        lut, first = maps.lut is not None, not self._m_started  # the passes run when the frame is recorded: now
        read, self.m_read_back = self.m_read_back, False
        if read and self.m_copy is None:
            self.m_copy = storage_buffer(ctx.m_rhi, READ_BYTES)

        def eye(command_buffer) -> None:
            nativecompute.guarded(self, self._eye, command_buffer, image, size_px, step, first, read)

        def before(batch) -> None:  # QRhi's copy found as a copy's destination (``CPins``)
            nativeforeign.CPins.copy_dest(batch, self.m_copy, bytes(READ_BYTES))

        def after():
            batch = ctx.m_rhi.nextResourceUpdateBatch()
            self.m_readback = QRhiReadbackResult()
            batch.readBackBuffer(self.m_copy, 0, READ_BYTES, self.m_readback)
            return batch

        compute_pass(ctx, eye, before if read else None, after if read else None)

        def nothing(*_args) -> None:
            return None

        turned = self.m_turned if ctx.m_flip_offscreen else self.m_triangle
        chain = (("grade", image, self.m_fbos[0], size_px, HDR, "", turned),
                 ("tonemap", self.m_targets[0], self.m_fbos[1], size_px, HDR, "", self.m_triangle),
                 ("composite", self.m_targets[1], framebuffer, view, colour, depth, self.m_triangle))  # fmt: skip
        for key, source, destination, target_size, colour_format, depth_format, triangle in chain:

            def record(
                command_buffer, arguments=(key, source, target_size, colour_format, depth_format, maps, lut, triangle)
            ) -> None:
                nativecompute.guarded(self, self._post, command_buffer, *arguments)

            draw_pass(ctx, destination, nothing, nothing, clear=key != "composite", external=record)
        self._m_started = True

    def _eye(self, command_buffer, image, size_px, step, first: bool, read: bool) -> None:
        """The per-tile histograms, their merge and the exposure (``eyeadapt.CEyeAdaptation.run``'s passes)."""
        grid = (groups(size_px[0], eyeadapt.TILE), groups(size_px[1], eyeadapt.TILE))
        tiles = self._tile_buffer(grid[0] * grid[1])
        if first:  # the engine's first exposure: 1, then zeros
            self.m_list.write_buffer(
                self.m_exposure_buffer, struct.pack("<f", 1.0) + bytes(4 * (eyeadapt.EXPOSURE_FLOATS - 1))
            )
        self.m_list.write_buffer(self.m_merged, bytes(eyeadapt.BINS * 4))  # the merge adds into it

        def fill(buffer: gs.CConstants) -> None:
            eyeadapt.fill(buffer, size_px, step)

        memo: dict = {}
        buffers = {
            kernel_key: kernel.write(self.m_list, 0, fill, memo) for kernel_key, kernel in self.m_kernels.items()
        }
        self.m_list.execute()
        self._told_before()
        with nativeforeign.CRecording(self.m_device, command_buffer, self.m_bridge) as recording:
            named = {"cImageTex": recording.qrhi(image), "cPerTileHistogramsOut": tiles, "cPerTileHistogramsIn": tiles,
                     "cMergedHistogramOut": self.m_merged, "cMergedHistogramIn": self.m_merged, "cExposureOut": self.m_exposure_buffer}  # fmt: skip
            where = {"tiles": (*grid, 1), "merge": (groups(grid[0] * grid[1], eyeadapt.MERGE_GROUP), 1, 1)}
            for key, kernel in self.m_kernels.items():
                kernel.record(recording, 0, named, buffers[key], where.get(key, (1, 1, 1)))
            if read:
                copy = recording.qrhi_buffer(self.m_copy, 4, nativeforeign.COPY_DEST)
                recording.copy_buffer(copy, self.m_exposure_buffer, READ_BYTES)
        self._told_after()

    def _post(self, command_buffer, key, source, size_px, colour, depth, maps: SMaps, lut: bool, triangle) -> None:
        """One of ``DRAWS`` reading ``src`` (QRhi's), into the target QRhi has bound, by ``triangle``."""
        kernel = self._draw(key, colour, depth)

        def fill(buffer: gs.CConstants) -> None:
            for name, value in gamepost.CONSTANTS.items():
                buffer.set(name, value)
            if not lut:  # no cube read yet: ungraded, as the view's own finish
                buffer.set("cUsedLUTTextures", 0)
                buffer.set("cLUTWeights", (0.0, 0.0, 0.0))
            buffer.set("cGlobalFlags", eyeadapt.GCF_EYE_ADAPT)
            if key == "composite":
                for name, value in COMPOSITE.items():
                    buffer.set(name, value)

        buffers = kernel.write(self.m_list, 0, fill)
        named = {"cExposureBuffer": self.m_exposure_buffer}
        if key == "composite":
            named["cUITexture"] = self._ui(size_px)  # its clear goes on the list sent next
        self.m_list.execute()
        self._told_before()
        with nativeforeign.CRecording(self.m_device, command_buffer, self.m_bridge) as recording:
            named["cSceneTexture" if key == "composite" else "cTexture0"] = recording.qrhi(source)
            if lut and key == "grade":
                named["cLUTTexture0"] = recording.qrhi(maps.lut)
            noise = maps.noise
            if noise is not None:
                named["cBlueNoiseTex"] = noise if _module_texture(noise) else recording.qrhi(noise)
            kernel.record_draw(recording, 0, named, buffers, size_px, [triangle])
        self._told_after()

    def restart(self) -> None:
        """The next frame starts at the target at once (a new island, a view drawn after a pause)."""
        self._m_started = False

    def end_frame(self) -> None:
        self.m_bridge.end_frame()

    def release(self) -> None:
        for kernel in (*self.m_kernels.values(), *self.m_draws.values()):
            kernel.release()
        for resource in (
            self.m_triangle,
            self.m_turned,
            self.m_merged,
            self.m_exposure_buffer,
            self.m_tiles,
            self.m_ui,
        ):
            if resource is not None:
                resource.release()
        for resource in (*self.m_fbos, *self.m_targets):
            resource.release()
        if self.m_copy is not None:
            self.m_copy.destroy()
        self.m_inputs.release()
        self.m_stand_ins.release()
        self.m_bridge.release()
        self.m_list.close()
        self.m_draws, self.m_targets, self.m_fbos, self.m_tiles, self.m_ui = {}, [], [], None, None
