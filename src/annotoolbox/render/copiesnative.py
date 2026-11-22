"""The water's copy of the scene's depth as view distance by the game's shipped ``copy.fx`` (``csDepthCopyToViewZ``) on
QRhi's own list, where the current path's compute pass ran: the copy QRhi makes of the lit colour stays QRhi's (a
plain texture copy, no shader).

``CNativeCopies(device, natives)`` is the entry; ``run(ctx, copies, lit, depth, size_px, camera)`` records the pass for a
``gamewater.CSceneCopies``, whose targets, maps and constants stay its own. docs/rendering/native-backend.md 16.
"""

from __future__ import annotations

import os

from . import gameshader as gs
from . import gamewater, nativecompute, nativeforeign, nativegpu, nativepass
from .gamecompute import copy_into, groups
from .terrainnative import CBridge

ENV = "ANNO_NATIVE_COPIES"  # "0": the water's depth copy by the current path (the rest of the water stays native)
STEM, FUNCTION = "copy", gamewater.STAGES["depth"][2]
CAMERAS = "cCameraBuffer"


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on() and hasattr(nativegpu.load(), "record_dispatch")


def shaders(natives) -> dict:
    """The copy's shader of the first shipped variant, reflected (in the background)."""
    shader = natives.call(natives.variant(STEM), FUNCTION)
    natives.reflection(shader)
    return {"depth": shader}


class CNativeCopies(nativecompute.CTold):
    """The depth copy's kernel (module docstring)."""

    def __init__(self, device, natives) -> None:
        self.m_device = device
        self.m_messages, self.m_foreign = [], []
        self.m_error = ""
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list)
        shader = shaders(natives)["depth"]
        reflection = natives.reflection(shader)
        self.m_kernel = nativeforeign.CForeignKernel(device, shader, reflection, self.m_stand_ins)
        # the camera records of the shipped shaders (a structured buffer; the rewrite's constants)
        self.m_element = next(
            (constant_buffer for constant_buffer in reflection.cbuffers if constant_buffer.name == CAMERAS), None
        )
        self.m_cameras, self._m_sent = None, b""

    def run(self, ctx, copies, lit, depth, size_px: tuple[int, int], camera: gs.SCamera) -> None:
        """``SceneCopies.run``'s pass: the depth as view distance into its target natively, then QRhi's copy of
        the lit colour as the pass's last batch (and the maps' upload with its first)."""
        copies._targets(ctx, size_px)
        buffers = self.m_kernel.write(self.m_list, 0, lambda constants: gs.fill_frame(constants, camera))
        cameras = self._cameras(camera)
        self.m_list.execute()
        grid = (groups(size_px[0], gamewater.GROUP), groups(size_px[1], gamewater.GROUP), 1)

        def record(command_buffer) -> None:
            nativecompute.guarded(self, self._record, command_buffer, depth, copies.m_viewz, buffers, grid, cameras)

        def after():
            return copy_into(ctx.m_rhi, copies.m_color.native, lit.native, size_px)

        nativepass.compute_pass(ctx, record, copies._upload, after)

    def _cameras(self, camera):
        """``cCameraBuffer``: the three camera records (all this one), written when they changed; None when the
        shader reads none."""
        if self.m_element is None:
            return None
        constants = gs.CConstants(nativecompute.records(self.m_element, gs.CAMERAS))
        gs.fill_frame(constants, camera)
        data = bytes(constants.m_data)
        if self.m_cameras is None:
            self.m_cameras = self.m_device.buffer(len(data), "structured", stride=self.m_element.size, name=CAMERAS)
        if data != self._m_sent:
            self.m_list.write_buffer(self.m_cameras, data)
            self._m_sent = data
        return self.m_cameras

    def _record(self, command_buffer, depth, viewz, buffers: dict, grid: tuple, cameras) -> None:
        self._told_before()
        with nativeforeign.CRecording(self.m_device, command_buffer, self.m_bridge) as recording:
            named = {
                "cSourceDepth": recording.qrhi(depth.native),
                "cDstViewZ": recording.qrhi(viewz.native),
                CAMERAS: cameras,
            }
            self.m_kernel.record(recording, 0, named, buffers, grid)
        self._told_after()

    def end_frame(self) -> None:
        self.m_bridge.end_frame()

    def release(self) -> None:
        self.m_kernel.release()
        if self.m_cameras is not None:
            self.m_cameras.release()
        self.m_stand_ins.release()
        self.m_bridge.release()
        self.m_list.close()
