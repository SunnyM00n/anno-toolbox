"""The native backend's Direct3D 12 layer from Python: the compiled module (``_nativegpu``, scripts/build_native.py),
QRhi's device, queue and command list taken over from a view, a pass's layouts, sets and samplers from its
``nativelayout.SBindingPlan``, the pipeline library on disk (``open_pipelines``, ``keep_pipelines``) and pooled
static buffers (``CBufferPool``).

``load()`` is the module or None (``reason()`` says why); ``attach(rhi)`` is NVRHI on a QRhi's device and queue
(route A), ``own_device()`` one of our own (route B); ``command_list(command_buffer)`` is QRhi's list inside
``beginExternal``. docs/rendering/native-backend.md.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple

from ..infra import buildcache
from ..sources.overlay import cache_base
from . import nativelayout

MODULE = "_nativegpu"
ENV = "ANNO_NATIVE_GPU"  # "0": never use the module (the current path draws everything)
DEBUG_ENV = "ANNO_D3D12_DEBUG"  # "1": the debug layer on an own device, where Windows' Graphics Tools are installed
# QRhiD3D12NativeHandles: dev (0), minimumFeatureLevel, adapterLuidLow, adapterLuidHigh, commandQueue (24)
DEVICE_AT, QUEUE_AT = 0, 24
PIPELINES_DIR = "pipelines"  # the cache's folder of the pipeline libraries, one file per adapter
SAVE_AFTER_S = 10.0  # a changed library is written once no worker made a pipeline for this long [I: ours]
POOL_BLOCK_BYTES = 4 << 20  # one buffer of a ``CBufferPool`` [I: ours, about 300 of an island's meshes]
POOL_ALIGN = 256  # a piece's start: a constant buffer view's alignment, so every usage can share a pool
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
        open_pipelines(found)
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
        save_pipelines(found)
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


def mesh_pipeline(device, command_list, shaders: tuple, bytecode: bytes, layouts: list, state: dict, mesh,
                  is_background: bool = False) -> tuple:  # fmt: skip
    """(pipeline, the constant vertex buffer of the inputs the mesh lacks) of a vertex and a pixel shader for a
    mesh's stored layout (``bytecode``: the vertex shader's, whose inputs it reads); ``is_background``: made by the
    module's workers, its ``ready`` says when."""
    laid, fill = nativelayout.input_elements(mesh.m_elements, nativelayout.signature(bytecode), mesh.m_stride)
    pipe = device.graphics_pipeline(shaders[0], shaders[1], device.input_layout(laid), layouts, state,
                                    background=is_background)  # fmt: skip
    return pipe, static_buffer(device, command_list, fill or bytes(16), "vertex", "constant vertex")


def library_path(device) -> Path:
    """The file of a device's pipeline library: one per adapter, made again when its driver changes."""
    return cache_base() / PIPELINES_DIR / f"{device.adapter_id}.bin"


def open_pipelines(device) -> bool:
    """The device's pipeline library read from its file (``library_path``): False when there is none or it was made
    for another driver or module (the library then starts empty and is written anew)."""
    if not buildcache.enabled():
        return False
    try:
        data = library_path(device).read_bytes()
    except OSError:
        return False
    return device.open_pipeline_library(data)


def save_pipelines(device, is_background: bool = False) -> bool:
    """The device's pipeline library written to its file when pipelines were stored into it since: whole or not at
    all (``buildcache.write_atomic``); ``is_background``: the file written on a thread of its own."""
    if not buildcache.enabled() or not device.pipeline_stats["changed"]:
        return False
    data, path = device.pipeline_library(), library_path(device)
    if not data:
        return False
    if is_background:
        threading.Thread(target=_write_library, args=(path, data), name="pipeline-library", daemon=True).start()
        return True
    return _write_library(path, data)


def _write_library(path: Path, data: bytes) -> bool:
    try:
        buildcache.write_atomic(path, data)
    except OSError:  # a full disk or a folder that cannot be written: made again at the next start
        return False
    return True


def keep_pipelines(device, now_s: float) -> None:
    """Once a frame: the pipeline library written when it changed and no worker has been busy for ``SAVE_AFTER_S``,
    so a later start loads what this one made (the view's own frames: performance.md section 4, item 8)."""
    due = _g_state.setdefault("saved_at", {})
    if now_s - due.get(device.adapter_id, 0.0) < SAVE_AFTER_S:
        return
    stats = device.pipeline_stats
    if stats["pending"]:
        return
    due[device.adapter_id] = now_s
    if stats["changed"]:
        save_pipelines(device, is_background=True)


class SPlaced(NamedTuple):
    """A piece of a ``CBufferPool``: its buffer, where it starts there, its block (negative: a buffer of its own) and
    its size."""

    buffer: object
    offset: int
    block: int
    size_bytes: int


class CBufferPool:
    """Small static buffers of one usage placed in a few large ones, as the engine places its meshes: a buffer made
    per mesh cost about 0.3 ms on the window's thread, one per node or part a frame (performance.md section 4, item
    8). ``place`` writes bytes and gives (buffer, offset); ``free`` gives a range back, reused ``device.keep_frames``
    frames later (a frame in flight may still read it); a piece over a quarter of a block gets a buffer of its own."""

    def __init__(self, device, usage: str, name: str, block_bytes: int = POOL_BLOCK_BYTES) -> None:
        self.m_device, self.m_usage, self.m_name, self.m_block_bytes = device, usage, name, block_bytes
        self.m_blocks: list = []  # [buffer, sorted free ranges [(offset, size)]]
        self.m_freed: list[tuple[int, int, int, int]] = []  # (device frame, block, offset, size) not reusable yet
        self.m_own: dict[int, object] = {}  # a large piece's buffer by its number
        self._m_next_own = 0

    def place(self, command_list, data: bytes) -> SPlaced:
        """The piece holding ``data``, written on the module's list ``command_list``; ``free`` takes it back."""
        size = -(-max(len(data), 16) // POOL_ALIGN) * POOL_ALIGN
        if size > self.m_block_bytes // 4:
            buffer = self.m_device.buffer(size, self.m_usage, name=self.m_name)
            command_list.write_buffer(buffer, data)
            self._m_next_own += 1
            self.m_own[self._m_next_own] = buffer
            return SPlaced(buffer, 0, -self._m_next_own, size)
        self._recycle()
        found = self._take(size)
        if found is None:
            self.m_blocks.append([self.m_device.buffer(self.m_block_bytes, self.m_usage, name=self.m_name),
                                  [(0, self.m_block_bytes)]])  # fmt: skip
            found = self._take(size)
        block, offset = found
        command_list.write_buffer(self.m_blocks[block][0], data, offset)
        return SPlaced(self.m_blocks[block][0], offset, block, size)

    def free(self, placed: SPlaced) -> None:
        """Give back a piece that ``place`` handed out. A piece from before a ``release`` is ignored: its range in a
        block made since would be handed out twice."""
        if placed.block < 0:
            if self.m_own.get(-placed.block) is placed.buffer:
                self.m_own.pop(-placed.block).release()  # late: a frame in flight may still read it
            return
        if placed.block >= len(self.m_blocks) or self.m_blocks[placed.block][0] is not placed.buffer:
            return
        self.m_freed.append((self.m_device.frame, placed.block, placed.offset, placed.size_bytes))

    def _take(self, size: int) -> tuple[int, int] | None:
        for block, (_buffer, ranges) in enumerate(self.m_blocks):
            for k, (offset, length) in enumerate(ranges):
                if length >= size:
                    ranges[k : k + 1] = [(offset + size, length - size)] if length > size else []
                    return block, offset
        return None

    def _recycle(self) -> None:
        """The ranges freed ``keep_frames`` frames ago back into their blocks' free lists, joined to their neighbours."""
        if not self.m_freed:
            return
        frame, keep = self.m_device.frame, self.m_device.keep_frames
        ready = [entry for entry in self.m_freed if entry[0] + keep < frame]
        if not ready:
            return
        self.m_freed = [entry for entry in self.m_freed if entry[0] + keep >= frame]
        for _frame, block, offset, size in ready:
            ranges = sorted([*self.m_blocks[block][1], (offset, size)])
            joined = [ranges[0]]
            for start, length in ranges[1:]:
                if joined[-1][0] + joined[-1][1] == start:
                    joined[-1] = (joined[-1][0], joined[-1][1] + length)
                else:
                    joined.append((start, length))
            self.m_blocks[block][1] = joined

    def release(self) -> None:
        for buffer, _ranges in self.m_blocks:
            buffer.release()
        for buffer in self.m_own.values():
            buffer.release()
        self.m_blocks, self.m_freed, self.m_own = [], [], {}


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

    def let_go(self, before: int) -> list:
        """The buffers last used before frame ``before`` let go (late); returned, so the sets made with them go too."""
        gone = [self.m_held.pop(key)[0] for key in [k for k, held in self.m_held.items() if held[2] < before]]
        for buffer in gone:
            buffer.release()
        return gone

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

    def touch(self, keys: list) -> bool:
        """Whether every set of ``keys`` (``get``'s) is still kept, each marked as used now."""
        for key in keys:
            if key not in self.m_sets:
                return False
            self.m_sets.move_to_end(key)
        return True

    def get(self, layout, held: tuple, make, keys: list | None = None):
        """The set of ``layout`` for the resources ``held``, ``make()`` when there is none yet (``keys``: its key
        appended, for ``touch``)."""
        key = (id(layout), *map(id, held))
        if keys is not None:
            keys.append(key)
        found = self.m_sets.get(key)
        if found is None:
            found = self.m_sets[key] = ((layout, held), make())
            while len(self.m_sets) > self.m_size:
                self.m_sets.popitem(last=False)[1][1].release()
        else:
            self.m_sets.move_to_end(key)
        return found[1]

    def let_go(self, resources: list) -> None:
        """The sets made with any of ``resources`` let go (late) with them: one kept past its resource is never asked
        for again (its successor has another id) and holds its sampler descriptors, of which the device has 2048."""
        if not resources:
            return

        def holds_one(held: tuple) -> bool:
            return any(resource is gone for resource in held if resource is not None for gone in resources)

        for key in [key for key, ((_layout, held), _made) in self.m_sets.items() if holds_one(held)]:
            self.m_sets.pop(key)[1].release()

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
        elif isinstance(found := named.get(item.name), SPlaced):  # a constant buffer's piece of a pool
            items.append((item.kind, item.slot, found.buffer, {"offset": found.offset, "range": found.size_bytes}))
        else:
            items.append((item.kind, item.slot, found))
    return device.binding_set(layout, items)
