"""The game's compute shaders and the bake's draw on the native backend's own command lists, and the module's textures
handed to QRhi so that the phases still on QRhi read them.

``CKernel.compute(device, shader, reflection)`` is one of the game's compute shaders as it ships (``CKernel.graphics``
the stages of a draw): its binding plan, layouts, samplers and pipeline, its constant buffers written per dispatch
(NVRHI's volatile ones), ``dispatch(list, named, fill, groups)`` records it. ``CHandover(device, rhi)`` gives a module
texture to QRhi (``QRhiTexture::createFrom``) and hands its state over both ways (``take_back``, ``handed_back``).
Users: ``pagesnative``, ``bakenative``; docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

from collections import OrderedDict

import shiboken6
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ..infra.dxc import SConstantBuffer, SVariable
from . import gameshader as gs
from . import nativegpu, nativelayout

# NVRHI's permanent state of the module's textures between its lists (ShaderResource:
# D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE | PIXEL_SHADER_RESOURCE): QRhi takes them over in it
SHADER_READ = 0x40 | 0x80
CONSTANT_SLOT = 256  # a constant buffer's bytes are rounded up to this (Direct3D 12's alignment)
SETS = 64  # binding sets a kernel keeps for the resources it was handed last [ours]
COMPUTE_STAGE = "cs"


def records(element: SConstantBuffer, count: int) -> SConstantBuffer:
    """A structured buffer of ``count`` records (the reflection's element type, ``$Element``) as a buffer of one
    array named as the buffer, so its records are written by path (``cSplatsBuffer[3].MatID``); one for each element
    and count, kept: a new one each frame made the constants look every path up again (performance.md 4, item 2)."""
    key = (id(element), count)  # the entry holds the element: its id stays its own
    held = _g_records.get(key)
    if held is not None and held[0] is element:
        return held[1]
    one = element.variables[0]
    array = SVariable(element.name, 0, element.size * count, one.type, elements=count, members=one.members)
    made = SConstantBuffer(element.name, element.size * count, [array])
    if len(_g_records) >= KEPT_RECORDS:
        del _g_records[next(iter(_g_records))]
    _g_records[key] = (element, made)
    return made


KEPT_RECORDS = 256  # the record buffers ``records`` keeps (the oldest goes first) [ours]
_g_records: dict[tuple[int, int], tuple] = {}


def reset_records() -> None:
    """``records``' kept buffers let go (a test's, or a closed island's reflections)."""
    _g_records.clear()


def pointer(wrapper) -> int:
    """The address of the C++ object behind a Qt wrapper."""
    return int(shiboken6.getCppPointer(wrapper)[0])


class CKernel:
    """A compute shader or a draw's stages on the module (module docstring). ``named`` (shader name -> the module's
    texture, buffer or descriptor table) binds them by name; the kernel's own constant buffers join by their names."""

    def __init__(self, device, plan: nativelayout.SBindingPlan, cbuffers: dict, name: str) -> None:
        self.m_device, self.m_plan, self.m_name = device, plan, name
        self.m_layouts = nativegpu.layouts(device, plan)
        self.m_samplers = nativegpu.samplers(device, plan)
        volatile = {i.name for spec in plan.layouts for i in spec.items if i.kind == "volatile"}
        self.m_cbuffers = {
            buffer_name: constant_buffer for buffer_name, constant_buffer in cbuffers.items() if buffer_name in volatile
        }
        self.m_buffers = {buffer_name: device.buffer(-(-max(constant_buffer.size, 16) // CONSTANT_SLOT) * CONSTANT_SLOT, "volatile", name=buffer_name)
                        for buffer_name, constant_buffer in self.m_cbuffers.items()}  # fmt: skip
        self.m_pipeline = None  # a compute kernel's; a draw's go by its vertex layout (``pipelines``)
        self.m_pipelines: dict = {}
        self.m_stages: dict = {}  # a draw's shaders by stage, its vertex shader's bytecode and its fixed state
        self.m_vs_bytecode = b""
        self.m_state: dict = {}
        self._m_sets: OrderedDict[tuple, tuple] = OrderedDict()

    @classmethod
    def compute(cls, device, shader, reflection) -> CKernel:
        """One compute shader (``shadercache.SCachedShader``-like: ``bytecode``, ``name``) and its reflection."""
        plan = nativelayout.SBindingPlan.of([reflection], volatile=True)
        kernel = cls(
            device,
            plan,
            {constant_buffer.name: constant_buffer for constant_buffer in reflection.cbuffers},
            shader.name,
        )
        made = device.shader(COMPUTE_STAGE, shader.bytecode, shader.name)
        kernel.m_pipeline = device.compute_pipeline(made, kernel.m_layouts)
        return kernel

    @classmethod
    def graphics(cls, device, stages: dict, state: dict, bindless: tuple = ()) -> CKernel:
        """A draw's stages (``{"vs": (shader, reflection), "ps": ...}``) with the pipeline's fixed ``state``; its
        pipelines are made per vertex layout (``pipeline_for``). ``bindless``: the arrays bound as descriptor
        tables."""
        reflections = [reflection for _s, reflection in stages.values()]
        plan = nativelayout.SBindingPlan.of(reflections, bindless=bindless, volatile=True)
        kernel = cls(
            device,
            plan,
            {
                constant_buffer.name: constant_buffer
                for reflection in reflections
                for constant_buffer in reflection.cbuffers
            },
            stages["vs"][0].name,
        )
        kernel.m_stages = {k: device.shader(k, stage.bytecode, stage.name) for k, (stage, _r) in stages.items()}
        kernel.m_vs_bytecode = stages["vs"][0].bytecode
        kernel.m_state = state
        return kernel

    def pipeline_for(self, elements: tuple, stride: int) -> tuple:
        """(pipeline, the constant vertex of the inputs the mesh lacks) for a mesh's stored ``elements``."""
        key = (tuple(map(tuple, elements)), stride)
        found = self.m_pipelines.get(key)
        if found is None:
            inputs = nativelayout.signature(self.m_vs_bytecode)
            laid, fill = nativelayout.input_elements(elements, inputs, stride)
            stages = self.m_stages
            pipe = self.m_device.graphics_pipeline(stages["vs"], stages.get("ps"), self.m_device.input_layout(laid), self.m_layouts,
                                                 self.m_state)  # fmt: skip
            found = self.m_pipelines[key] = (pipe, fill)
        return found

    def write(self, command_list, fill, camera=None) -> dict:
        """Every constant buffer of the kernel written for the next dispatch or draw: the frame's values for
        ``camera`` (None: none), then ``fill(constants)``; by name, for ``named``."""
        for name, constant_buffer in self.m_cbuffers.items():
            constants = gs.CConstants(constant_buffer)
            if camera is not None:
                gs.fill_frame(constants, camera)
            if fill is not None:
                fill(constants)
            command_list.write_buffer(self.m_buffers[name], bytes(constants.m_data))
        return self.m_buffers

    def sets(self, named: dict) -> list:
        """The sets and tables of the kernel's layouts in its pipeline's order (a set made when what it binds
        changed; the last ``SETS`` kept)."""
        out = []
        for index, spec in enumerate(self.m_plan.layouts):
            if spec.bindless:
                out.append(named[spec.items[0].name])
                continue
            held = tuple(named.get(i.name) for i in spec.items)
            key = (index, *map(id, held))
            found = self._m_sets.get(key)
            if found is None:
                made = nativegpu.binding_set(self.m_device, self.m_layouts[index], spec, named, self.m_samplers)
                found = self._m_sets[key] = (held, made)  # the resources held: their ids stay theirs
                while len(self._m_sets) > SETS:
                    self._m_sets.popitem(last=False)[1][1].release()
            else:
                self._m_sets.move_to_end(key)
            out.append(found[1])
        return out

    def dispatch(self, command_list, named: dict, fill, groups: tuple[int, int, int]) -> None:
        """One dispatch of a compute kernel: its constants (``fill(constants)``) written, its resources bound by name."""
        buffers = self.write(command_list, fill)
        command_list.dispatch(self.m_pipeline, self.sets({**named, **buffers}), *groups)

    def release(self) -> None:
        for _held, made in self._m_sets.values():
            made.release()
        for buffer in self.m_buffers.values():
            buffer.release()
        for pipe, _fill in self.m_pipelines.values():
            pipe.release()
        if self.m_pipeline is not None:
            self.m_pipeline.release()
        for layout in self.m_layouts:
            layout.release()
        self._m_sets, self.m_buffers, self.m_pipelines, self.m_pipeline = OrderedDict(), {}, {}, None


def guarded(owner, work, *args) -> None:
    """``work(*args)`` of a native part ``owner`` that has not failed (None: nothing); a raise becomes its ``error``,
    said once by the view's look, and the current path draws that part from the next frame."""
    if owner is None or owner.m_error:
        return
    try:
        work(*args)
    except Exception as exc:  # pattern: ok (a boundary: a native part's failure becomes its ``error``)
        owner.m_error = f"{type(exc).__name__}: {exc}"


class CTold:
    """With the debug layer: its and NVRHI's messages while a part records and sends its list (``messages``) and
    those before (``foreign``: the other parts' on the same device), for ``scripts/native_compare.py``."""

    m_device: object
    m_messages: list
    m_foreign: list

    def _told_before(self) -> None:
        if self.m_device.debug:
            self.m_foreign += self.m_device.messages()

    def _told_after(self) -> None:
        if self.m_device.debug:
            self.m_messages += self.m_device.messages()


class CHandover:
    """The module's textures handed to QRhi: a QRhi texture over the module's resource (``give``), QRhi's states
    of them taken back before a list of the module's reads or writes them (``take_back``, recorded ahead of
    NVRHI's own barriers: NVRHI assumes its permanent state) and told to QRhi after (``handed_back``); let go with
    QRhi's wrapper first (``let_go``). Their work goes to the queue before QRhi's frame, so the states QRhi tracks
    then are those its last frame left (docs/rendering/native-backend.md 10.2)."""

    def __init__(self, device, rhi) -> None:
        self.m_device, self.m_rhi, self.m_module = device, rhi, nativegpu.load()
        self.m_given: dict[int, tuple] = {}  # id(QRhi texture) -> (QRhi texture, module texture)
        self._m_moved: list = []

    def give(self, texture, texture_format, size_px: tuple[int, int], layers: int = 0, flags=None):
        """A QRhi texture of ``texture_format`` (QRhi's) over the module texture ``texture``, tracked from ``SHADER_READ``."""
        rhi, flags = self.m_rhi, flags if flags is not None else QRhiTexture.Flag(0)
        rhi_texture = rhi.newTextureArray(texture_format, layers, QSize(*size_px), 1, flags) if layers else rhi.newTexture(texture_format, QSize(*size_px),
                                                                                                    1, flags)  # fmt: skip
        if not self.m_module.qrhi_create_from(pointer(rhi_texture), texture.native, SHADER_READ):
            raise RuntimeError(f"QRhi did not take the module's texture over ({texture_format}, {size_px})")
        self.m_given[id(rhi_texture)] = (rhi_texture, texture)
        return rhi_texture

    def native(self, rhi_texture):
        """The module texture behind a QRhi texture ``give`` made, None for any other."""
        found = self.m_given.get(id(rhi_texture)) if rhi_texture is not None else None
        return found[1] if found is not None else None

    def take_back(self, command_list, textures) -> None:
        """Before ``command_list`` (the module's, open) uses ``textures`` (QRhi's, given here): each from the state QRhi
        left it in into ``SHADER_READ``."""
        items = []
        for rhi_texture in textures:
            if (
                rhi_texture is None
                or id(rhi_texture) not in self.m_given
                or any(moved is rhi_texture for moved in self._m_moved)
            ):
                continue
            resource, state = self.m_module.qrhi_texture(pointer(rhi_texture))
            if state != SHADER_READ:
                items.append((resource, state, SHADER_READ))
            self._m_moved.append(rhi_texture)
        if items:
            command_list.transitions(items)

    def handed_back(self) -> None:
        """After the list went to the queue: QRhi tracks what it took back in ``SHADER_READ`` again (NVRHI leaves
        its textures there at a list's end)."""
        for rhi_texture in self._m_moved:
            rhi_texture.setNativeLayout(SHADER_READ)
        self._m_moved = []

    def let_go(self, ctx, rhi_texture) -> None:
        """QRhi's wrapper after the frame in flight, the module's texture some frames late (``Device.keep_frames``)."""
        found = self.m_given.pop(id(rhi_texture), None)
        if found is None:
            return
        ctx.later(rhi_texture)
        found[1].release()

    def drop(self, rhi_texture) -> None:
        """QRhi's wrapper destroyed now (its owner goes, no frame of it follows), the module's texture late."""
        found = self.m_given.pop(id(rhi_texture), None) if rhi_texture is not None else None
        if found is not None:
            rhi_texture.destroy()
            found[1].release()

    def release(self) -> None:
        for q, _tex in list(self.m_given.values()):
            self.drop(q)
        self._m_moved = []
