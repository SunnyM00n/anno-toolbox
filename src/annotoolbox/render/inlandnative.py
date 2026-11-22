"""The rivers' passes by the game's shipped shaders on QRhi's own list, where the current path's ran: the wave
gradients every frame (``water_inland_water.fx``, ``csComputeRiverGradients``) and, when the rivers change, the flow
map: the river ribbons drawn from above (``water_cluster_flow.fx``, ``ClusterFlow``), expanded and dilated.

``CNativeInland(device, natives)`` is the entry; ``gradients(ctx, own, seconds)``, ``raster(ctx, fbo, models, named,
camera, side)`` and ``flow(ctx, raster, expanded, flow, side)`` record for a ``gameinland.CInlandWater``, whose
textures and draws stay its own. docs/rendering/native-backend.md section 16.
"""

from __future__ import annotations

import os

from . import gameinland, nativecompute, nativeforeign, nativegpu, nativepass
from .gamecompute import copy_into, groups
from .terrainnative import CBridge
from .waternative import CNativeWater, CWaterRun

ENV = "ANNO_NATIVE_INLAND"  # "0": the rivers' compute passes by the current path (their draws stay native)
KERNELS = {"gradients": (gameinland.GRADIENTS[0], gameinland.GRADIENTS[2]),
           "expand": (gameinland.FLOW["expand"][0], gameinland.FLOW["expand"][2]),
           "dilate": (gameinland.FLOW["dilate"][0], gameinland.FLOW["dilate"][2])}  # fmt: skip
GROUP = 16  # the flow passes' groups, as the current path dispatches them
GRADIENTS_OUT = "cRiverGradientsRW"  # the shipped shader's name of what the rewrite calls cRiverGradientsRO
RASTER = ["RGBA8_UNORM"]  # the flow map's raster target (``CInlandWater._maps``), no depth


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on() and hasattr(nativegpu.load(), "record_dispatch")


def shaders(natives) -> dict:
    """The three compute shaders of the first shipped variants, reflected (in the background)."""
    out = {}
    for key, (path, entry_function) in KERNELS.items():
        out[key] = natives.call(natives.variant(path.rsplit("/", 1)[-1][:-3]), entry_function)
        natives.reflection(out[key])
    return out


class CNativeInland(nativecompute.CTold):
    """The rivers' compute kernels (module docstring)."""

    def __init__(self, device, natives) -> None:
        self.m_device = device
        self.m_messages, self.m_foreign = [], []
        self.m_error = ""
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list)
        found = shaders(natives)
        self.m_kernels = {key: nativeforeign.CForeignKernel(device, shader, natives.reflection(shader), self.m_stand_ins)
                        for key, shader in found.items()}  # fmt: skip
        self.m_ribbons = CNativeWater(
            device, natives, has_stencil=False
        )  # the raster's draws, as the water's are drawn
        self.m_ribbons.m_formats, self.m_ribbons.m_depth = RASTER, ""
        # the flow map's own variant, the one the current path compiles: the engine's MAX_RIVER_FLOW_SPEED
        self.m_flow_switches = tuple(natives.variant("water_cluster_flow").material)

    def gradients(self, ctx, own: dict, seconds: float) -> None:
        """``CInlandWater.run``'s wave gradients at ``seconds`` from ``own``'s volume into its gradients' target."""
        timer = [seconds, 0.0, seconds, seconds]
        buffers = self.m_kernels["gradients"].write(self.m_list, 0, lambda constants: constants.set("cTimer", timer))
        self.m_list.execute()
        target = own["cRiverGradientsRO"]
        size = target.pixelSize()
        grid = (groups(size.width()), groups(size.height()), 1)

        def record(command_buffer) -> None:
            def work(recording) -> None:
                named = {
                    "cWavesElevation": recording.qrhi(own["cWavesElevation"]),
                    GRADIENTS_OUT: recording.qrhi(target),
                }
                self.m_kernels["gradients"].record(recording, 0, named, buffers, grid)

            self._guarded(command_buffer, work)

        nativepass.compute_pass(ctx, record)

    def raster(self, ctx, framebuffer, models: list, named: dict, camera, side: int) -> None:
        """``CInlandWater._make_flow``'s draw of the river ribbons (``models``: its ``CInlandModel`` s of the flow map's
        program, their values set) into the raster target ``framebuffer`` by the shipped ``ClusterFlow``, from above
        (``camera``); ``named``: the session's heights. A failure is this part's error: the current path makes the
        flow map again from the next frame."""

        for model in models:
            model.m_switches = self.m_flow_switches

        def record(command_buffer, run: list) -> None:
            nativecompute.guarded(self, self.m_ribbons.record, command_buffer, [part.m_model for part in run], camera, [], named,
                                  (side, side))  # fmt: skip

        nothing = lambda *_a: None  # noqa: E731 - never called: every model is recorded natively
        nativepass.draw_models(ctx, framebuffer, [CWaterRun(model) for model in models], nothing, nothing, targets=1, depth=0.0,
                               defer=False, native=record)  # fmt: skip

    def flow(self, ctx, raster, expanded, flow, side: int) -> None:
        """``CInlandWater._make_flow``'s two passes after its raster draw: expanded, copied into the flow map (QRhi's
        copy, the first pass's last batch) and dilated there."""
        group_count = groups(side, GROUP)

        def pass_of(key: str, source, target):
            def record(command_buffer) -> None:
                def work(recording) -> None:
                    named = {
                        "cExpandFlowSrc": recording.qrhi(source.native),
                        "cExpandFlowDst": recording.qrhi(target.native),
                    }
                    self.m_kernels[key].record(recording, 0, named, {}, (group_count, group_count, 1))

                self._guarded(command_buffer, work)

            return record

        nativepass.compute_pass(
            ctx,
            pass_of("expand", raster, expanded),
            after=lambda: copy_into(ctx.m_rhi, flow.native, expanded.native, (side, side)),
        )
        nativepass.compute_pass(ctx, pass_of("dilate", expanded, flow))

    def _guarded(self, command_buffer, work) -> None:
        """``work(rec)`` on QRhi's list (``nativecompute.guarded``: a raise is this part's error, the current path's
        passes from the next frame), every state moved back also after a raise."""

        def recorded() -> None:
            self._told_before()
            with nativeforeign.CRecording(self.m_device, command_buffer, self.m_bridge) as recording:
                work(recording)
            self._told_after()

        nativecompute.guarded(self, recorded)

    def end_frame(self) -> None:
        self.m_bridge.end_frame()
        self.m_ribbons.end_frame()

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        self.m_ribbons.release()
        self.m_stand_ins.release()
        self.m_bridge.release()
        self.m_list.close()
        self.m_kernels = {}
