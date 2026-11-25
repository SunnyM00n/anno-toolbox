"""The objects' shadow casters culled into the cascades on the GPU as Anno 117 culls them: the shipped
``model_culler.fx`` writes each caster's word once per cascade it needs and raises its draw's instance count in the
indirect arguments the shadow pass draws by (the module's ``record_indirect``).

``shaders(natives)`` finds the two compute shaders (in the background); ``CShadowCull(device, natives)`` is the device
side, one per view: ``run`` records the culling in a compute pass of QRhi's after the cascades' fit, ``record`` the
casters' indirect draws. docs/rendering/performance.md section 4, item 4.
"""

from __future__ import annotations

from . import gameshader as gs
from . import gameshadow, nativecompute, nativeforeign, nativegpu
from .gamecompute import groups
from .terrainnative import CBridge, camera_buffer

EFFECT = "model_culler"
INIT, CULL = "csModelCullInit", "csModelCull"
THREADS = 512  # MODEL_CULLER_THREAD_GROUPS_X, the engine's define (``shaderdefines.ENGINE_DEFINES``)
CULL_SIZE = 4.0  # cShadowCullSize: our texel rule's size for a round caster [E] (quality.md, the engine's is not read)
OCCUPATION_BYTES = 32 * 32 * 4  # cOccupationMap: a word a cell of the shadow map's 32 x 32
SHADER_READ = nativeforeign.SHADER_READ
INDIRECT = 0x200  # D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT
WORD_BYTES = 16  # a culled word (``cModelGPUCulledInstanceData``'s uint4)
CULLED_NAME = "cModelGPUCulledInstanceData"


def shaders(natives) -> dict:
    """The culling's two shaders from the shipped variant (else compiled from the source with the engine's defines),
    reflected; a missing one raises (the casters are then culled on the CPU)."""
    variant = natives.variant(EFFECT)
    out = {key: natives.call(variant, function_name) for key, function_name in (("init", INIT), ("cull", CULL))}
    for shader in out.values():
        natives.reflection(shader)
    return out


class CShadowCull(nativecompute.CTold):
    """The casters' culling on the GPU (module docstring): its kernels, the draws' arguments and the culled words it
    writes (grown to the frame's), the occupation map (every cell occupied: the shader's path without the occupation
    marks, as on Intel [ours]) and the camera records it reads."""

    def __init__(self, device, natives) -> None:
        self.m_device, self.m_module = device, nativegpu.load()
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_stand_ins = nativeforeign.CStandIns(device, self.m_list)
        found = shaders(natives)
        self.m_kernels = {key: nativeforeign.CForeignKernel(device, shader, natives.reflection(shader), self.m_stand_ins)
                          for key, shader in found.items()}  # fmt: skip
        reflection = natives.reflection(found["cull"])
        self.m_element = next(buffer for buffer in reflection.cbuffers if buffer.name == "cCameraBuffer")
        self.m_occupation = device.buffer(OCCUPATION_BYTES, "raw,uav", name="occupation")
        self.m_list.write_buffer(self.m_occupation, bytes(OCCUPATION_BYTES))
        self.m_bridge = CBridge(device)  # its own: the shadow pass's moves of QRhi's resources stay the pass's
        self.m_args = self.m_output = self.m_camera = None
        self.m_let_go: list = []  # the frame grew out of these since take_let_go; the casters' sets go too
        self._m_camera_sent = b""
        self._m_frame: tuple | None = None  # (what the dispatches bind, their constants, the counts): ``prepare``
        self.m_error = ""
        self.m_messages, self.m_foreign = [], []

    def _fit(self, held, size_bytes: int, usage: str, stride: int, name: str):
        """``held`` when it holds ``size_bytes``, else a buffer of the next power of two (the old one let go late)."""
        if held is not None and held.size >= size_bytes:
            return held
        if held is not None:
            held.release()
            self.m_let_go.append(held)
        return self.m_device.buffer(max(1 << 12, 1 << max(0, size_bytes - 1).bit_length()), usage, stride=stride,
                                    name=name)  # fmt: skip

    def named(self) -> dict:
        """What the shadow pass's casters read of the culling: the culled words."""
        return {CULLED_NAME: self.m_output} if self.m_output is not None else {}

    def take_let_go(self) -> list:
        """Return the buffers let go since the last call: a set kept with one is never asked for again."""
        gone, self.m_let_go = self.m_let_go, []
        return gone

    def _camera(self, camera):
        """``cCameraBuffer``: the three camera records (all this one), written when they changed."""
        constants = gs.CConstants(camera_buffer(self.m_element))
        gs.fill_frame(constants, camera)
        data = bytes(constants.m_data)
        if self.m_camera is None:
            self.m_camera = self.m_device.buffer(
                len(data), "structured", stride=self.m_element.size, name="cull camera"
            )
        if data != self._m_camera_sent:
            self.m_list.write_buffer(self.m_camera, data)
            self._m_camera_sent = data
        return self.m_camera

    def prepare(self, objects, camera, cascades, information) -> bool:
        """This frame's culling made ready before the shadow pass records it (``objects``: ``objectsnative
        .CNativeObjects``, its frame's cull objects sent; ``information``: the cascades' buffer as the pass binds
        it): its buffers fitted, its constants and camera written and sent; False when nothing is culled or it failed
        (``error``: the casters are culled on the CPU from the next frame)."""
        self._m_frame = None
        if self.m_error:
            return False
        try:
            return self._prepare(objects, camera, cascades, information)
        except Exception as exc:  # pattern: ok (a boundary: the culling's failure becomes its ``error``)
            self.m_error = f"{type(exc).__name__}: {exc}"
            return False

    def _prepare(self, objects, camera, cascades, information) -> bool:
        object_count, draw_count, output_count = counts = objects.m_instances.m_cull_counts
        if not draw_count:
            return False
        stride = self.m_module.INDIRECT_STRIDE
        self.m_args = self._fit(self.m_args, draw_count * stride, "indirect,raw,uav", 0, "cull arguments")
        self.m_output = self._fit(self.m_output, max(1, output_count) * WORD_BYTES, "structured,uav", WORD_BYTES,
                                  "culled words")  # fmt: skip
        light = [*cascades.light, 0.0]

        def fill(constants: gs.CConstants) -> None:
            gs.fill_frame(constants, camera)
            gameshadow.fill(constants, cascades)
            for name, value in (("cShadowCullSize", CULL_SIZE), ("cLgtDirection", light), ("cVendorIsIntel", 1),
                                ("cModelCullNumObjects", object_count), ("cModelCullNumDrawArgs", draw_count),
                                ("cModelCullingEnabled", 1)):  # fmt: skip
                constants.set(name, value)

        memo: dict = {}
        buffers = {key: kernel.write(self.m_list, 0, fill, memo) for key, kernel in self.m_kernels.items()}
        named = {**objects.m_instances.cull_named(), "cCameraBuffer": self._camera(camera), "cDrawArgs": self.m_args,
                 "cOutputObjects": self.m_output, "cOccupationMap": self.m_occupation,
                 "cShadowInformation": information}  # fmt: skip
        self.m_list.execute()
        self._m_frame = (named, buffers, counts)
        return True

    def _cull(self, list_ptr) -> None:
        """The culling's two dispatches on QRhi's list, ahead of the casters' draws in the shadow pass (a compute
        pass of its own cost QRhi more than the dispatches: performance.md 4, item 4)."""
        named, buffers, (object_count, draw_count, _outputs) = self._m_frame
        self._told_before()
        with nativeforeign.CRecording(self.m_device, list_ptr, self.m_bridge) as recording:
            self.m_kernels["init"].record(recording, 0, named, buffers["init"], (groups(draw_count, THREADS), 1, 1))
            self.m_kernels["cull"].record(recording, 0, named, buffers["cull"], (groups(object_count, THREADS), 1, 1))
        self._told_after()

    def record(self, list_ptr, items: list, args: list[int], offsets: list[int], viewport: tuple) -> None:
        """The culling (``prepare``) and the casters' draws on QRhi's list (inside the shadow pass's
        ``beginExternal``), each by its arguments the culling wrote (``args``: their indices), with the arguments
        moved into the indirect state and back; a culling that fails draws nothing (its ``error``)."""
        if self._m_frame is None:
            raise RuntimeError("the casters' culling was not prepared this frame")
        try:
            self._cull(list_ptr)
        except Exception as exc:  # pattern: ok (a boundary: its arguments are not this frame's, nothing is drawn)
            self.m_error, self._m_frame = f"{type(exc).__name__}: {exc}", None
            return
        stride, arguments = self.m_module.INDIRECT_STRIDE, self.m_args.native
        self.m_module.transitions(list_ptr, [(arguments, SHADER_READ, INDIRECT)])
        try:
            self.m_module.record_indirect(self.m_device, list_ptr, items, self.m_args, [arg * stride for arg in args],
                                          offsets, viewport)  # fmt: skip
        finally:
            self.m_module.transitions(list_ptr, [(arguments, INDIRECT, SHADER_READ)])
            self._m_frame = None

    def release(self) -> None:
        for kernel in self.m_kernels.values():
            kernel.release()
        for resource in (self.m_args, self.m_output, self.m_camera, self.m_occupation):
            if resource is not None:
                resource.release()
        self.m_stand_ins.release()
        self.m_bridge.release()
        self.m_list.close()
        self.m_args = self.m_output = self.m_camera = self._m_frame = None
