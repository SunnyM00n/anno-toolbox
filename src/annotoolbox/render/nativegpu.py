"""The native backend's Direct3D 12 layer from Python: the compiled module (``_nativegpu``, built by
scripts/build_native.py from native/d3d12/, shipped in the installer), QRhi's device, queue and command list taken
over from a view, and the layouts, sets and samplers of a pass made from its ``nativelayout.BindingPlan``.

``load()`` is the module or None (``reason()`` says why: not built, not Windows, no Direct3D 12); without it the
view draws its own look, as before the backend. ``attach(rhi)`` is NVRHI on a QRhi's device and queue (route A of
native-backend.md, step 0), ``own_device()`` one of our own (route B); ``command_list(cb)`` is QRhi's list inside
``beginExternal``. docs/rendering/native-backend.md.
"""

from __future__ import annotations

import ctypes
import os
import sys

from . import nativelayout

MODULE = "_nativegpu"
ENV = "ANNO_NATIVE_GPU"  # "0": never use the module (the current path draws everything)
DEBUG_ENV = "ANNO_D3D12_DEBUG"  # "1": the debug layer on an own device, where Windows' Graphics Tools are installed
# QRhiD3D12NativeHandles: dev (0), minimumFeatureLevel, adapterLuidLow, adapterLuidHigh, commandQueue (24)
DEVICE_AT, QUEUE_AT = 0, 24
_state: dict = {}


def load():
    """The module, or None where it cannot run here (``reason``)."""
    if "module" in _state:
        return _state["module"]
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
    _state["module"], _state["reason"] = module, why
    return module


def available() -> bool:
    return load() is not None


def reason() -> str:
    load()
    return _state.get("reason", "")


def _pointer(obj) -> int:
    import shiboken6

    return int(shiboken6.getCppPointer(obj)[0])


def _read(address: int, offset: int) -> int:
    return ctypes.c_void_p.from_address(address + offset).value or 0


def qrhi_handles(rhi) -> tuple[int, int]:
    """The ``ID3D12Device*`` and ``ID3D12CommandQueue*`` of a QRhi on Direct3D 12 (PySide6 gives the handles'
    struct without its fields: read from its memory, design section 3)."""
    handles = rhi.nativeHandles()
    if handles is None:
        raise RuntimeError("the QRhi has no native handles (not Direct3D 12?)")
    at = _pointer(handles)
    return _read(at, DEVICE_AT), _read(at, QUEUE_AT)


def command_list(cb) -> int:
    """The ``ID3D12GraphicsCommandList*`` QRhi records into: valid between ``beginExternal`` and ``endExternal``."""
    handles = cb.nativeHandles()
    if handles is None:
        raise RuntimeError("the command buffer has no native handles")
    return _read(_pointer(handles), 0)


def attach(rhi):
    """NVRHI on a QRhi's device and queue, made once per device (a view's device changes when it moves window)."""
    module = load()
    if module is None:
        raise RuntimeError(reason())
    device, queue = qrhi_handles(rhi)
    held = _state.setdefault("attached", {})
    found = held.get(device)
    if found is None:  # each view's QRhi has a device of its own: one NVRHI each, until ``detach``
        found = held[device] = module.Device.attach(device, queue)
    return found


def detach(rhi) -> None:
    """Forget the NVRHI over a QRhi's device, with its samplers (each holds its device alive); before QRhi releases
    the device (``RhiView.released``)."""
    held = _state.get("attached", {})
    try:
        device, _queue = qrhi_handles(rhi)
    except RuntimeError:
        return
    found = held.pop(device, None)
    if found is not None:
        made = _state.get("samplers", {})
        for key in [k for k in made if k[0] == id(found)]:
            del made[key]
        found.wait_idle()


def own_device(adapter: int = -1):
    """A device of our own (route B; ``adapter`` -2: Windows' software rasteriser, for the tests)."""
    module = load()
    if module is None:
        raise RuntimeError(reason())
    return module.Device.create(debug=os.environ.get(DEBUG_ENV, "") == "1", adapter=adapter)


def layouts(device, plan: nativelayout.BindingPlan) -> list:
    """The module's binding layouts for a plan, in its order."""
    out = []
    for spec in plan.layouts:
        if spec.bindless:
            item = spec.items[0]
            out.append(device.bindless_layout([(item.kind, spec.space)], first=item.slot, capacity=1 << 16))
        else:
            out.append(device.layout(spec.tuples(), space=spec.space))
    return out


def samplers(device, plan: nativelayout.BindingPlan) -> dict:
    """The engine's fixed samplers a plan reads, by (space, register), each made once per device and name."""
    made = _state.setdefault("samplers", {})
    out = {}
    for where, name in plan.samplers.items():
        key = (id(device), name)
        if key not in made:
            made[key] = device.sampler(**nativelayout.sampler_state(name))
        out[where] = made[key]
    return out


def binding_set(device, layout, spec: nativelayout.LayoutSpec, named: dict, sampler_of: dict):
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
