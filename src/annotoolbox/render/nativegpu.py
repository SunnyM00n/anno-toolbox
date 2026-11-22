"""The native backend's Direct3D 12 layer from Python: the compiled module (``_nativegpu``, built by
scripts/build_native.py from native/d3d12/, shipped in the installer), QRhi's device, queue and command list taken
over from a view, and the layouts, sets and samplers of a pass made from its ``nativelayout.SBindingPlan``.

``load()`` is the module or None (``reason()`` says why: not built, not Windows, no Direct3D 12); without it the
view draws its own look, as before the backend. ``attach(rhi)`` is NVRHI on a QRhi's device and queue (route A of
native-backend.md, step 0), ``own_device()`` one of our own (route B); ``command_list(command_buffer)`` is QRhi's list inside
``beginExternal``. docs/rendering/native-backend.md.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections import OrderedDict

from . import nativelayout

MODULE = "_nativegpu"
ENV = "ANNO_NATIVE_GPU"  # "0": never use the module (the current path draws everything)
DEBUG_ENV = "ANNO_D3D12_DEBUG"  # "1": the debug layer on an own device, where Windows' Graphics Tools are installed
# QRhiD3D12NativeHandles: dev (0), minimumFeatureLevel, adapterLuidLow, adapterLuidHigh, commandQueue (24)
DEVICE_AT, QUEUE_AT = 0, 24
_g_state: dict = {}


def load():
    """The module, or None where it cannot run here (``reason``)."""
    if "module" in _g_state:
        return _g_state["module"]
    module, why = None, ""
    if os.environ.get(ENV, "") == "0":
        why = f"switched off ({ENV}=0)"
    elif sys.platform != "win32":
        why = "Direct3D 12 is Windows only"
    else:
        try:
            from . import _nativegpu as module  # type: ignore[attr-defined]  # built, not tracked
        except ImportError as exc:  # not built (a source checkout) or a broken install
            why = f"the module is not there ({exc}): scripts/build_native.py builds it"
    _g_state["module"], _g_state["reason"] = module, why
    return module


def available() -> bool:
    return load() is not None


def reason() -> str:
    load()
    return _g_state.get("reason", "")


def want(is_enabled: bool) -> None:
    """The whole native look on or off (the setting ``Settings.native_shaders``, View > Rendering's switch); off:
    every phase the current path's. A failure said by ``failed`` is forgotten with each change."""
    _g_state["wanted"] = bool(is_enabled)
    _g_state.pop("failed", None)


def wanted() -> bool:
    return _g_state.get("wanted", True)


def on() -> bool:
    """Whether the native phases draw: the look is wanted, the module loads and no device refused it (each phase
    then asks its own ``ANNO_NATIVE_*`` switch, kept for debugging)."""
    return wanted() and not _g_state.get("failed") and available()


def failed(why: str) -> None:
    """The native look cannot draw on this device (``check`` refused it, or its making failed): off until ``want``
    is asked again; ``blocker`` says why."""
    _g_state["failed"] = why


def blocker(backend: str = "d3d12") -> str:
    """Why the native look cannot be on for a view on ``backend`` ("" when it can): no module, not Direct3D 12, the
    device refused it (``failed``). Not translated: the caller wraps it."""
    if not available():
        return reason()
    if backend != "d3d12":
        return f"the view draws on {backend}, the game's shipped shaders need Direct3D 12"
    return _g_state.get("failed", "")


SHADER_MODEL, BINDING_TIER = 0x62, 2  # what the game's shaders need (native-backend.md section 6)


def check(device) -> None:
    """Raise when ``device`` cannot bind the game's shipped shaders: shader model 6.2 and binding tier 2 at least."""
    if device.shader_model < SHADER_MODEL or device.binding_tier < BINDING_TIER:
        shader_model_text = f"{device.shader_model >> 4}.{device.shader_model & 15}"
        raise RuntimeError(f"the device offers shader model {shader_model_text} and binding tier {device.binding_tier}; the game's "
                           f"shaders need 6.2 and tier 2")  # fmt: skip


def _pointer(qt_object) -> int:
    import shiboken6

    return int(shiboken6.getCppPointer(qt_object)[0])


def _read(address: int, offset_bytes: int) -> int:
    return ctypes.c_void_p.from_address(address + offset_bytes).value or 0


def qrhi_handles(rhi) -> tuple[int, int]:
    """The ``ID3D12Device*`` and ``ID3D12CommandQueue*`` of a QRhi on Direct3D 12 (PySide6 gives the handles'
    struct without its fields: read from its memory, design section 3)."""
    handles = rhi.nativeHandles()
    if handles is None:
        raise RuntimeError("the QRhi has no native handles (not Direct3D 12?)")
    address = _pointer(handles)
    return _read(address, DEVICE_AT), _read(address, QUEUE_AT)


def command_list(command_buffer) -> int:
    """The ``ID3D12GraphicsCommandList*`` QRhi records into: valid between ``beginExternal`` and ``endExternal``."""
    handles = command_buffer.nativeHandles()
    if handles is None:
        raise RuntimeError("the command buffer has no native handles")
    return _read(_pointer(handles), 0)


def attach(rhi):
    """NVRHI on a QRhi's device and queue, made once per device (a view's device changes when it moves window)."""
    module = load()
    if module is None:
        raise RuntimeError(reason())
    device, queue = qrhi_handles(rhi)
    held = _g_state.setdefault("attached", {})
    found = held.get(device)
    if found is None:  # each view's QRhi has a device of its own: one NVRHI each, until ``detach``
        found = held[device] = module.Device.attach(device, queue)
    return found


def detach(rhi) -> None:
    """Forget the NVRHI over a QRhi's device, with its samplers (each holds its device alive); before QRhi releases
    the device (``CRhiView.released``)."""
    held = _g_state.get("attached", {})
    if not held or rhi.backendName() != "D3D12":  # another backend's handles are laid out otherwise
        return
    try:
        device, _queue = qrhi_handles(rhi)
    except RuntimeError:
        return
    found = held.pop(device, None)
    if found is not None:
        made = _g_state.get("samplers", {})
        for key in [k for k in made if k[0] == id(found)]:
            del made[key]
        found.wait_idle()


def own_device(adapter: int = -1):
    """A device of our own (route B; ``adapter`` -2: Windows' software rasteriser, for the tests)."""
    module = load()
    if module is None:
        raise RuntimeError(reason())
    return module.Device.create(debug=os.environ.get(DEBUG_ENV, "") == "1", adapter=adapter)


def layout(device, spec: nativelayout.SLayoutSpec):
    """The module's binding layout of one of a plan's layouts (a bindless one: its unbounded array)."""
    if spec.bindless:
        item = spec.items[0]
        return device.bindless_layout([(item.kind, spec.space)], first=item.slot, capacity=1 << 16)
    return device.layout(spec.tuples(), space=spec.space)


def layouts(device, plan: nativelayout.SBindingPlan) -> list:
    """The module's binding layouts for a plan, in its order."""
    return [layout(device, spec) for spec in plan.layouts]


def samplers(device, plan: nativelayout.SBindingPlan) -> dict:
    """The engine's fixed samplers a plan reads, by (space, register), each made once per device and name."""
    made = _g_state.setdefault("samplers", {})
    out = {}
    for where, name in plan.samplers.items():
        key = (id(device), name)
        if key not in made:
            made[key] = device.sampler(**nativelayout.sampler_state(name))
        out[where] = made[key]
    return out


def static_buffer(device, command_list, data: bytes, usage: str, name: str):
    """A buffer holding ``data`` (written on the module's list ``command_list``), at least 16 bytes."""
    buffer = device.buffer(max(len(data), 16), usage, name=name)
    command_list.write_buffer(buffer, data)
    return buffer


def mesh_pipeline(device, command_list, shaders: tuple, bytecode: bytes, layouts: list, state: dict, mesh) -> tuple:
    """(pipeline, the constant vertex buffer of the inputs the mesh lacks) of a vertex and a pixel shader for a
    mesh's stored layout (``bytecode``: the vertex shader's, whose inputs it reads)."""
    laid, fill = nativelayout.input_elements(mesh.m_elements, nativelayout.signature(bytecode), mesh.m_stride)
    pipe = device.graphics_pipeline(shaders[0], shaders[1], device.input_layout(laid), layouts, state)
    return pipe, static_buffer(device, command_list, fill or bytes(16), "vertex", "constant vertex")


class CKeptBuffers:
    """Buffers by key, each holding the bytes last written into it (on the module's list, only when they changed),
    made again in whole ``slot``s when they outgrow it; ``frame`` marks a use, ``let_go`` the ones not used since."""

    def __init__(self, device, slot: int = 256) -> None:
        self.m_device, self.m_slot = device, slot
        self.m_held: dict = {}  # key -> [buffer, its bytes, last frame used]

    def get(
        self, command_list, key, data: bytes, usage: str = "constant", stride: int = 0, name: str = "", frame: int = 0
    ):
        size_bytes = -(-max(len(data), 16) // self.m_slot) * self.m_slot
        held = self.m_held.get(key)
        if held is None or held[0].size < size_bytes:
            if held is not None:
                held[0].release()  # late: a frame in flight may still read it
            held = self.m_held[key] = [self.m_device.buffer(size_bytes, usage, stride=stride, name=name), None, frame]
        if held[1] != data:
            command_list.write_buffer(held[0], data)
            held[1] = data
        held[2] = frame
        return held[0]

    def let_go(self, before: int) -> None:
        """The buffers last used before frame ``before``."""
        for key in [k for k, held in self.m_held.items() if held[2] < before]:
            self.m_held.pop(key)[0].release()

    def release(self) -> None:
        for held in self.m_held.values():
            held[0].release()
        self.m_held = {}


class CSetCache:
    """Binding sets kept by the resources they were made with (held with them: their ids stay theirs), the least
    recently used let go past ``max_sets`` (late: a frame in flight may still read one)."""

    def __init__(self, max_sets: int) -> None:
        self.m_size = max_sets
        self.m_sets: OrderedDict = OrderedDict()  # (layout, resources' ids) -> ((layout, resources), set)

    def get(self, layout, held: tuple, make):
        """The set of ``layout`` for the resources ``held``, ``make()`` when there is none yet."""
        key = (id(layout), *map(id, held))
        found = self.m_sets.get(key)
        if found is None:
            found = self.m_sets[key] = ((layout, held), make())
            while len(self.m_sets) > self.m_size:
                self.m_sets.popitem(last=False)[1][1].release()
        else:
            self.m_sets.move_to_end(key)
        return found[1]

    def __len__(self) -> int:
        return len(self.m_sets)

    def release(self) -> None:
        for _held, made in self.m_sets.values():
            made.release()
        self.m_sets = OrderedDict()


def binding_set(device, layout, spec: nativelayout.SLayoutSpec, named: dict, sampler_of: dict):
    """A layout's set: each item's resource by its shader name from ``named`` (a missing one is a null view, which
    ``-all-resources-bound`` allows: native-backend.md section 1; NVRHI's validation, on with the debug layer,
    refuses a set that leaves one out), samplers from ``sampler_of``."""
    items = []
    for item in spec.items:
        if item.kind == "sampler":
            items.append(("sampler", item.slot, sampler_of[(spec.space, item.slot)]))
        elif item.kind == "push":
            items.append(("push", item.slot, None, {"size": item.count}))
        else:
            items.append((item.kind, item.slot, named.get(item.name)))
    return device.binding_set(layout, items)
